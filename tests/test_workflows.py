# SPDX-License-Identifier: MIT
"""The workflow runner (``autoontology/logic/workflows.py``): the quickstart 연체독촉
workflow end to end over faked sources, for_each with where and truncation, if/then/else,
notify, pause on approval and resume (approve, reject, restart reconciliation, nested
steps), duplicate slots, on_action triggers and their depth limit, the activator's role,
cancel and changed workflows."""

from __future__ import annotations

import sqlite3

import pytest

from autoontology.config import Settings
from autoontology.logic import actions, functions, registry, workflows
from autoontology.logic.actions import Caller
from autoontology.objects import service
from autoontology.objects.reader import ObjectError
from autoontology.store.db import Store
from tests import logic_fixtures as fx
from tests.logic_fixtures import INV
from tests.test_actions import ACTION, FakeMailer, activate_samples, sample_specs
from tests.test_logic import TODAY

WORKFLOW = "연체독촉"
OWNER = Caller("owner@x", role="owner", four_eyes=True)

#: extra invoices (contract K2024-003): overdue 16, 86 and 147 days on TODAY (2026-11-25;
#: due = 청구일 + 30 days) -> stages 1, 3 and 4; INV-2 is 55 days overdue -> stage 2
EXTRA = [
    ("INV-4", "2026-10-10"),
    ("INV-5", "2026-08-01"),
    ("INV-6", "2026-06-01"),
]


def _add_invoices(backend, rows=EXTRA) -> None:
    for key, day in rows:
        backend.tables["public.invoices"]["rows"].append(
            {
                "청구ID": key,
                "계약ID": "K2024-003",
                "청구일": day,
                "청구금액": 1000000,
                "수납금액": None,
                "수납상태": "미납",
            }
        )


def _activate(view, spec: dict, actor: str = "local") -> None:
    registry.save(view, spec, actor=actor)
    registry.set_status(view, "workflow", spec["name"], "active", actor=actor)


def sample_workflow() -> dict:
    return sample_specs("workflows.yaml")[0]


@pytest.fixture
def env(isolated_env, monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    backend = fx.install_backend(monkeypatch)
    _add_invoices(backend)
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    view = fx.seed(store)
    activate_samples(view)
    mailer = FakeMailer()
    workflows.install(settings, mailer=mailer)
    yield settings, view, backend, mailer
    workflows.wait()
    actions.wait_notifications()


def _stage(settings, view, key):
    return service.get_object(settings, view, INV, key)["values"].get("독촉단계")


def _run(settings, view, name=WORKFLOW, slot="manual:1", **kw):
    return workflows.run(
        settings,
        view,
        name,
        trigger="manual",
        fire_slot=slot,
        started_by="tester",
        when=TODAY,
        **kw,
    )


def _audit(view, prefix="workflow.") -> list[str]:
    with sqlite3.connect(view.store.path) as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE action LIKE ? ORDER BY id", (prefix + "%",)
        ).fetchall()
    return [r[0] for r in rows]


# --- the sample end to end ---------------------------------------------------------------


def test_sample_workflow_records_stages_and_pauses_for_stage_four(env):
    settings, view, backend, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    assert run.status == "paused" and run.trigger == "manual" and run.workflow_version == 1
    s = run.summary
    # INV-1 is paid, INV-3 not yet due: the where leaves them out
    assert (s["matched"], s["completed"], s["actions_applied"], s["pending_approval"]) == (
        4,
        3,
        3,
        1,
    )
    assert s["scanned"] == 6 and s["truncated"] is False and s["failed"] == 0
    assert {k: _stage(settings, view, k) for k in ("INV-2", "INV-4", "INV-5", "INV-6")} == {
        "INV-2": 2,
        "INV-4": 1,
        "INV-5": 3,
        "INV-6": None,
    }
    by_key = {o["key"]: o for o in run.to_dict()["objects"]}
    assert by_key["INV-6"]["status"] == "paused"
    assert by_key["INV-2"]["steps"][0] == {"path": "steps[0]", "kind": "if", "value": False}
    paused = run.to_dict()["paused"]
    assert [p["key"] for p in paused] == ["INV-6"]
    assert paused[0]["path"] == [["steps", 0], ["then", 0], ["then", 0]]
    # the action runs: requested by the workflow, linked to the run
    linked = view.action_runs().list(workflow_run_id=run.id)
    assert len(linked) == 4 and {a.via for a in linked} == {"workflow"}
    assert {a.requested_by for a in linked} == {f"workflow:{WORKFLOW}"}
    pending = [a for a in linked if a.status == "pending_approval"]
    assert [(a.object_key, a.approval_role, a.params) for a in pending] == [
        ("INV-6", "owner", {"단계": 4})
    ]
    # the owner approves: the object resumes (no steps left) and the run succeeds
    actions.approve(settings, view, pending[0].id, OWNER, when=TODAY)
    workflows.wait()
    done = view.workflow_runs().get(run.id)
    assert done.status == "succeeded" and done.finished_at
    assert done.summary["actions_applied"] == 4 and done.summary["completed"] == 4
    assert done.summary["pending_approval"] == 0
    assert _stage(settings, view, "INV-6") == 4
    obj = {o["key"]: o for o in done.to_dict()["objects"]}["INV-6"]
    assert obj["status"] == "done" and obj["steps"][-1]["result"] == "applied"
    assert _audit(view) == ["workflow.run", "workflow.finish"]
    # the sources were never written
    assert all("독촉단계" not in r for r in backend.tables["public.invoices"]["rows"])
    # the next day: every stage is recorded already, so nothing new is submitted
    again = _run(settings, view, slot="manual:2")
    assert again.status == "succeeded"
    assert (again.summary["matched"], again.summary["completed"]) == (4, 4)
    assert again.summary["actions_applied"] == 0 and again.summary["pending_approval"] == 0
    assert view.action_runs().list(workflow_run_id=again.id) == []


def test_rejected_approval_skips_the_object(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    actions.reject(view, pending.id, OWNER, "아직")
    workflows.wait()
    done = view.workflow_runs().get(run.id)
    assert done.status == "succeeded"
    assert done.summary["skipped"] == 1 and done.summary["completed"] == 3
    obj = {o["key"]: o for o in done.to_dict()["objects"]}["INV-6"]
    assert obj["status"] == "skipped" and "rejected" in obj["reason"]
    assert _stage(settings, view, "INV-6") is None


def test_restart_reconciliation_resumes_from_polling(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    workflows.uninstall()  # another process approves: no listener here
    actions.approve(settings, view, pending.id, OWNER, when=TODAY)
    assert view.workflow_runs().get(run.id).status == "paused"
    # the service comes back: reconcile polls the paused continuations
    assert workflows.reconcile(settings, view) == 1
    done = view.workflow_runs().get(run.id)
    assert done.status == "succeeded" and done.summary["actions_applied"] == 4
    # a second reconcile (or a late listener) finds nothing to resume
    assert workflows.resume(settings, view, pending.id) is None
    assert workflows.reconcile(settings, view) == 0


def test_duplicate_slot_is_a_no_op(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    first = _run(settings, view, slot="2026-11-25T02:00")
    again = _run(settings, view, slot="2026-11-25T02:00")
    assert again.id == first.id
    assert len(view.workflow_runs().list()) == 1
    assert len(view.action_runs().list(workflow_run_id=first.id)) == 4


def test_retry_never_applies_an_action_twice(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    before = len(view.action_runs().list())
    # the same object through the same step of the same run: the same idempotency key
    key = workflows.idempotency_key(
        run.id, [["steps", 0], ["else", 0], ["then", 0], ["then", 0]], "INV-5"
    )
    found, created = actions.submit(
        settings,
        view,
        ACTION,
        "INV-5",
        {"단계": 3},
        workflows.caller_for(settings, view, WORKFLOW, run.id),
        idempotency_key=key,
        when=TODAY,
    )
    assert not created and found.workflow_run_id == run.id
    assert len(view.action_runs().list()) == before


def test_inactive_and_unknown_workflows_are_refused(env):
    settings, view, _, _ = env
    with pytest.raises(ObjectError) as e:
        _run(settings, view, name="없음")
    assert e.value.code == "unknown_workflow"
    registry.save(view, sample_workflow())
    with pytest.raises(ObjectError) as e:
        _run(settings, view)
    assert e.value.code == "workflow_not_active"
    assert view.workflow_runs().list() == []


# --- for_each, if / else, notify ---------------------------------------------------------


def _wf(steps, **extra) -> dict:
    return {
        "kind": "workflow",
        "name": extra.pop("name", "테스트"),
        "label": "테스트",
        "trigger": extra.pop("trigger", {"manual": True}),
        "for_each": extra.pop("for_each", {"object_type": "청구", "where": "연체여부()"}),
        "steps": steps,
        **extra,
    }


def test_where_limit_and_truncation(env):
    settings, view, _, _ = env
    _activate(view, _wf([{"action": ACTION, "params": {"단계": "1"}}], max_objects=2))
    run = _run(settings, view, name="테스트")
    assert run.status == "succeeded"
    assert run.summary["matched"] == 2 and run.summary["truncated"] is True
    assert run.summary["truncated_by"] == "max_objects"
    # key order: INV-2, INV-4 processed; INV-5, INV-6 not
    assert [o["key"] for o in run.to_dict()["objects"]] == ["INV-2", "INV-4"]
    assert _stage(settings, view, "INV-5") is None
    # for_each.limit below max_objects wins; exactly as many as match: not truncated
    _activate(
        view,
        _wf(
            [{"action": ACTION, "params": {"단계": "1"}}],
            name="전체",
            for_each={"object_type": "청구", "where": "연체여부()", "limit": 4},
        ),
    )
    whole = _run(settings, view, name="전체")
    assert whole.summary["matched"] == 4 and whole.summary["truncated"] is False


def test_if_else_and_notify_steps(env):
    settings, view, _, mailer = env
    steps = [
        {
            "if": "연체일수() >= 50",
            "then": [
                {
                    "notify": {
                        "to": ["email:boss@example.com", "role:owner"],
                        "subject": "[연체] {{ 청구ID }} {{ 연체일수() }}일",
                        "body": "미수금 {{ 미수금() }}원",
                    }
                }
            ],
            "else": [{"notify": {"to": "email:clerk@example.com", "subject": "{{ 청구ID }}"}}],
        }
    ]
    _activate(view, _wf(steps))
    run = _run(settings, view, name="테스트")
    assert run.status == "succeeded" and run.summary["notified"] == 4
    assert run.summary["actions_applied"] == 0
    sent = sorted((to, subject) for to, subject, _ in mailer.sent)
    assert sent == [
        ("boss@example.com", "[연체] INV-2 55일"),
        ("boss@example.com", "[연체] INV-5 86일"),
        ("boss@example.com", "[연체] INV-6 147일"),
        ("clerk@example.com", "INV-4"),
    ]
    step = {o["key"]: o for o in run.to_dict()["objects"]}["INV-2"]["steps"][1]
    assert step["kind"] == "notify" and step["status"] == "sent" and step["skipped_roles"]
    assert "boss@example.com" not in str(run.to_dict())  # counts only, never addresses


def test_failed_expression_fails_the_object_not_the_run(env):
    settings, view, _, _ = env
    _activate(
        view,
        _wf(
            [
                {"if": "청구금액 / (청구금액 - 1000000) > 0", "then": [{"action": ACTION}]},
            ]
        ),
    )
    run = _run(settings, view, name="테스트")
    # INV-4..6 have 청구금액 1000000: division by zero; INV-2 runs
    assert run.status == "failed" and run.summary["failed"] == 3
    assert run.summary["completed"] == 1 and _stage(settings, view, "INV-2") == 1
    reasons = {o["key"]: o.get("reason", "") for o in run.to_dict()["objects"]}
    assert reasons["INV-4"].startswith("steps[0]")


def test_precondition_failure_skips_the_object(env):
    settings, view, _, _ = env
    # every invoice, paid ones too: the action's precondition refuses INV-1
    _activate(
        view,
        _wf(
            [{"action": ACTION, "params": {"단계": "1"}}],
            for_each={"object_type": "청구", "where": '청구ID == "INV-1"'},
        ),
    )
    run = _run(settings, view, name="테스트")
    assert run.status == "succeeded" and run.summary["skipped"] == 1
    assert "완납" in run.to_dict()["objects"][0]["reason"]


def test_resume_continues_nested_steps_in_order(env):
    settings, view, _, mailer = env
    steps = [
        {
            "if": "연체일수() >= 90",
            "then": [
                {"action": ACTION, "params": {"단계": "4"}},
                {"notify": {"to": "email:a@example.com", "subject": "after {{ 독촉단계 }}"}},
            ],
        },
        {"notify": {"to": "email:b@example.com", "subject": "end {{ 청구ID }}"}},
    ]
    _activate(view, _wf(steps, for_each={"object_type": "청구", "where": '청구ID == "INV-6"'}))
    run = _run(settings, view, name="테스트")
    assert run.status == "paused" and mailer.sent == []
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    actions.approve(settings, view, pending.id, OWNER, when=TODAY)
    workflows.wait()
    assert [(to, subject) for to, subject, _ in mailer.sent] == [
        ("a@example.com", "after 4"),  # the object was read again after the action
        ("b@example.com", "end INV-6"),
    ]
    done = view.workflow_runs().get(run.id)
    assert done.status == "succeeded" and done.summary["notified"] == 2
    paths = [s["path"] for s in done.to_dict()["objects"][0]["steps"]]
    assert paths == [
        "steps[0]",
        "steps[0].then[0]",
        "steps[0].then[0]",
        "steps[0].then[1]",
        "steps[1]",
    ]


def test_changed_workflow_skips_resumed_objects(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    _activate(view, {**sample_workflow(), "max_objects": 100})  # version 2
    actions.approve(settings, view, pending.id, OWNER, when=TODAY)
    workflows.wait()
    done = view.workflow_runs().get(run.id)
    assert done.status == "succeeded" and done.summary["skipped"] == 1
    reason = {o["key"]: o for o in done.to_dict()["objects"]}["INV-6"]["reason"]
    assert "changed" in reason
    assert _stage(settings, view, "INV-6") == 4  # the approved action itself applied


# --- cancel ------------------------------------------------------------------------------


def test_cancel_drops_paused_objects_and_leaves_the_action_runs(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run = _run(settings, view)
    cancelled = workflows.cancel(view, run.id, actor="ed@x")
    assert cancelled.status == "cancelled" and cancelled.summary["dropped"] == 1
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")
    assert len(pending) == 1  # left as it is
    actions.approve(settings, view, pending[0].id, OWNER, when=TODAY)
    workflows.wait()
    assert view.workflow_runs().get(run.id).status == "cancelled"
    with pytest.raises(ObjectError) as e:
        workflows.cancel(view, run.id)
    assert e.value.code == "not_cancellable" and e.value.status == 409
    with pytest.raises(ObjectError) as e:
        workflows.cancel(view, "wfr_none")
    assert e.value.code == "unknown_run"
    assert _audit(view) == ["workflow.run", "workflow.cancel"]


def test_cancel_stops_between_objects(env, monkeypatch):
    settings, view, _, _ = env
    _activate(view, _wf([{"action": ACTION, "params": {"단계": "1"}}]))
    original = workflows._process

    def process_then_cancel(e, record):
        original(e, record)
        workflows.cancel(view, e.run_id)

    monkeypatch.setattr(workflows, "_process", process_then_cancel)
    run = _run(settings, view, name="테스트")
    assert run.status == "cancelled" and run.summary["matched"] == 1
    assert len(view.action_runs().list(workflow_run_id=run.id)) == 1


# --- on_action and depth ---------------------------------------------------------------------

MEMO_ACTION = {
    "kind": "action",
    "name": "메모기록",
    "label": "메모 기록",
    "object_type": "청구",
    "effects": [{"set": "독촉메모", "value": '"자동"', "operational": True}],
}


def test_on_action_trigger_runs_for_that_object_up_to_the_depth_limit(env):
    settings, view, _, _ = env
    registry.save(view, MEMO_ACTION)
    registry.set_status(view, "action", "메모기록", "active")
    # A: when 독촉단계기록 applies, record a memo; B: when a memo is recorded, stage 1:
    # a loop through two workflows that only the depth limit stops
    _activate(
        view,
        _wf(
            [{"action": "메모기록"}],
            name="A",
            trigger={"on_action": ACTION},
            for_each={"object_type": "청구"},
        ),
    )
    _activate(
        view,
        _wf(
            [{"action": ACTION, "params": {"단계": "1"}}],
            name="B",
            trigger={"on_action": "메모기록"},
            for_each={"object_type": "청구"},
        ),
    )
    first, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 2}, OWNER, when=TODAY)
    workflows.wait()
    runs = sorted(view.workflow_runs().list(), key=lambda r: r.summary["depth"])
    assert [(r.workflow_name, r.summary["depth"], r.status) for r in runs] == [
        ("A", 1, "succeeded"),
        ("B", 2, "succeeded"),
        ("A", 3, "succeeded"),
        ("B", 4, "failed"),
    ]
    assert runs[0].fire_slot == f"action:{first.id}" and runs[0].trigger == "on_action"
    assert all(r.summary["object_key"] == "INV-2" and r.summary["matched"] <= 1 for r in runs)
    assert "depth_limit" in runs[-1].error
    assert runs[0].summary["matched"] == 1  # that one object only


def test_on_action_respects_where_type_and_the_off_setting(env, monkeypatch):
    settings, view, _, _ = env
    _activate(
        view,
        _wf(
            [{"notify": {"to": "email:x@example.com", "subject": "s"}}],
            name="C",
            trigger={"on_action": ACTION},
            for_each={"object_type": "청구", "where": "연체일수() > 100"},
        ),
    )
    actions.submit(settings, view, ACTION, "INV-2", {"단계": 2}, OWNER, when=TODAY)
    workflows.wait()
    run = view.workflow_runs().list()[0]
    assert run.status == "succeeded" and run.summary["matched"] == 0  # 55 days: where false
    monkeypatch.setenv("AUTOONTOLOGY_WORKFLOWS", "off")
    off = Settings.from_env()
    assert off.workflows == "off"
    workflows.install(off)
    actions.submit(settings, view, ACTION, "INV-6", {"단계": 1}, OWNER, when=TODAY)
    workflows.wait()
    assert len(view.workflow_runs().list()) == 1


# --- the activator ---------------------------------------------------------------------------


def test_activator_role_is_checked_at_run_time(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    backend = fx.install_backend(monkeypatch)
    _add_invoices(backend)
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    view = fx.seed(store)
    activate_samples(view)
    from autoontology.store.accounts import Accounts

    accounts = Accounts(store)
    owner = accounts.create_user("boss@example.com")
    accounts.add_member(view.id, owner.id, "owner")
    other = accounts.create_user("other@example.com")
    accounts.add_member(view.id, other.id, "owner")
    _activate(view, sample_workflow(), actor="boss@example.com")
    assert workflows.activation(view, WORKFLOW)[0] == "boss@example.com"
    run = _run(settings, view, slot="s1")
    assert run.status == "paused"  # the run acts as an owner: stage 4 waits for approval
    caller = workflows.caller_for(settings, view, WORKFLOW, run.id)
    assert (caller.role, caller.via, caller.four_eyes) == ("owner", "workflow", True)
    # demoted: the next run fails with the reason, before touching any object
    accounts.set_role(view.id, owner.id, "editor")
    failed = _run(settings, view, slot="s2")
    assert failed.status == "failed" and "no longer an owner" in failed.error
    assert failed.summary["matched"] == 0
    assert view.action_runs().list(workflow_run_id=failed.id) == []
    # a paused object resumes only while the activator may still act
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    actions.approve(
        settings, view, pending.id, Caller("other@example.com", role="owner", four_eyes=True)
    )
    workflows.resume(settings, view, pending.id)
    done = view.workflow_runs().get(run.id)
    assert done.status == "failed"
    assert (
        "no longer an owner" in {o["key"]: o for o in done.to_dict()["objects"]}["INV-6"]["reason"]
    )
    # disabled users fail too; the trusted CLI (an admin) may activate
    accounts.set_role(view.id, owner.id, "owner")
    accounts.disable(owner.id)
    assert "not an active user" in _run(settings, view, slot="s3").error
    _activate(view, {**sample_workflow(), "max_objects": 10}, actor="cli")
    # (INV-6 got its stage 4 from the approval above: nothing waits any more)
    assert _run(settings, view, slot="s4").status == "succeeded"


def test_accounts_off_acts_as_owner(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    caller = workflows.caller_for(settings, view, WORKFLOW, "wfr_1")
    assert caller.role == "owner" and caller.name == f"workflow:{WORKFLOW}"
    assert caller.workflow_run_id == "wfr_1" and not caller.four_eyes


# --- interrupted runs ------------------------------------------------------------------------


def test_reconcile_fails_runs_interrupted_mid_way(env, monkeypatch):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    run, created = workflows.start(
        settings, view, WORKFLOW, trigger="manual", fire_slot="x", started_by="t"
    )
    assert created and run.status == "running"
    workflows.reconcile(settings, view)
    assert view.workflow_runs().get(run.id).status == "running"  # its heartbeat is fresh
    monkeypatch.setattr(workflows, "STALE_SECONDS", -1)
    workflows.reconcile(settings, view)
    done = view.workflow_runs().get(run.id)
    assert done.status == "failed" and done.error.startswith("interrupted")
    # execute() on an ended run does nothing
    assert workflows.execute(settings, view, run.id).status == "failed"


# --- an approval still pending from an earlier run ----------------------------------------


def test_next_day_does_not_duplicate_a_pending_approval(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    first = _run(settings, view, slot="2026-11-25T02:00")
    pending = view.action_runs().list(status="pending_approval")
    assert [(a.object_key, a.workflow_run_id) for a in pending] == [("INV-6", first.id)]
    # the next scheduled day: INV-6 still waits for the owner, so no second request
    again = _run(settings, view, slot="2026-11-26T02:00")
    assert again.status == "succeeded"
    s = again.summary
    assert s["already_pending"] == 1 and s["skipped"] == 1 and s["pending_approval"] == 0
    assert (s["matched"], s["completed"], s["actions_applied"]) == (4, 3, 0)
    assert view.action_runs().list(workflow_run_id=again.id) == []
    assert [a.id for a in view.action_runs().list(status="pending_approval")] == [pending[0].id]
    obj = {o["key"]: o for o in again.to_dict()["objects"]}["INV-6"]
    assert obj["status"] == "skipped" and "already_pending" in obj["reason"]
    assert obj["steps"][-1]["result"] == "already_pending"
    assert obj["steps"][-1]["action_run_id"] == pending[0].id
    # the owner approves the first request: the first run resumes, the next day is quiet
    actions.approve(settings, view, pending[0].id, OWNER, when=TODAY)
    workflows.wait()
    assert view.workflow_runs().get(first.id).status == "succeeded"
    third = _run(settings, view, slot="2026-11-27T02:00")
    assert third.status == "succeeded" and third.summary["already_pending"] == 0
    assert view.action_runs().list(workflow_run_id=third.id) == []


def test_pending_approval_with_other_params_does_not_block(env):
    settings, view, _, _ = env
    _activate(view, sample_workflow())
    manual, _ = actions.submit(
        settings, view, ACTION, "INV-6", {"단계": 4, "메모": "직접"}, OWNER, when=TODAY
    )
    assert manual.status == "pending_approval"
    run = _run(settings, view)
    assert run.summary["already_pending"] == 0 and run.summary["pending_approval"] == 1
    mine = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")
    assert [(a.object_key, a.params) for a in mine] == [("INV-6", {"단계": 4})]
