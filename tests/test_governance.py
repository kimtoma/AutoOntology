# SPDX-License-Identifier: MIT
"""Governance regressions from the security review of the actionable ontology:

* H1 -- an editor changing a function must not change what an owner activated: function
  pins on actions / workflows, ``function_changed`` at execution and approval, restore to
  draft (not active), and ``logic.activate`` for functions an active definition uses;
* H2 -- four-eyes also covers the person behind an agent token (``requested_for``);
* M1 -- archived projects are not writable through the agent API or workflow triggers;
* M2 -- manual workflow runs (editors: ``trigger.manual`` only, one running at a time) and
  notification recipient caps;
* L1 -- line breaks in a subject and a failing recipient do not stop the mail;
* L2 -- an object type's key cannot change while live edits exist;
* L3 -- read-only POST routes work on archived projects.
"""

from __future__ import annotations

import sqlite3

import pytest

from autoontology.logic import actions, registry, workflows
from autoontology.logic.actions import Caller
from autoontology.objects import service
from autoontology.objects.reader import ObjectError
from autoontology.store import db
from autoontology.store.db import Store
from tests.logic_fixtures import INV
from tests.test_actions import (  # noqa: F401 - the setup fixture
    ACTION,
    EDITOR,
    OWNER,
    REVIEWER,
    FakeMailer,
    sample_specs,
    setup,
)
from tests.test_actions_api import (  # noqa: F401 - fixtures
    API,
    _rpc,
    aenv,
    backend,
)
from tests.test_actions_api import client as actions_client  # noqa: F401
from tests.test_workflows import WORKFLOW, _activate, _run, _wf, sample_workflow
from tests.test_workflows import env as wf_env  # noqa: F401


def _archive(store, on: bool = True) -> None:
    """Archive the default project (the store refuses that; the guards do not care which
    project it is)."""
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE projects SET archived_at = ? WHERE id = ?",
            ("2026-11-25T00:00:00" if on else None, store.default_project_id),
        )


def _function(name: str) -> dict:
    return next(d for d in sample_specs("functions.yaml") if d["name"] == name)


def _gate_on_overdue(view) -> None:
    """The owner's action: owner approval whenever the invoice is overdue (a function)."""
    a = sample_specs("actions.yaml")[0]
    registry.save(view, {**a, "approval": {"role": "owner", "when": "연체여부()"}})
    registry.set_status(view, "action", ACTION, "active", actor="owner@x")


def _editor_rewrites(view, name: str = "연체여부", expr: str = "false") -> None:
    registry.save(view, {**_function(name), "expr": expr}, actor="ed@x")
    registry.set_status(view, "function", name, "active", actor="ed@x")


# --- H1: function pins ----------------------------------------------------------------------


def test_activation_pins_function_versions_transitively(setup):  # noqa: F811
    _, view, _ = setup
    _gate_on_overdue(view)
    d = view.logic_defs().get("action", ACTION)
    # 연체여부 calls 연체일수; the effects and the notification call 미수금 and 연체일수
    assert d.function_pins == {"미수금": 1, "연체여부": 1, "연체일수": 1}
    assert d.to_dict()["function_pins"] == d.function_pins


def test_editor_function_edit_cannot_bypass_owner_approval(setup):  # noqa: F811
    settings, view, _ = setup
    _gate_on_overdue(view)
    p = actions.preview(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert p["approval"]["required"] is True
    _editor_rewrites(view)
    # the action does not come back active with the edited function: an owner must look
    d = view.logic_defs().get("action", ACTION)
    assert d.status == "draft" and d.to_dict()["needs_reactivation"] is True
    assert "연체여부 changed (v1 -> v2)" in d.broken_reason
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, EDITOR)
    assert e.value.code == "action_not_active"
    # the owner activates it again: it is pinned to the new version and runs
    registry.set_status(view, "action", ACTION, "active", actor="owner@x")
    d = view.logic_defs().get("action", ACTION)
    assert d.function_pins["연체여부"] == 2 and not d.to_dict()["needs_reactivation"]
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert run.status == "applied"


def test_execution_refuses_a_function_that_differs_from_the_pin(setup):  # noqa: F811
    settings, view, _ = setup
    _gate_on_overdue(view)
    view.logic_defs().set_pins("action", ACTION, {"미수금": 1, "연체여부": 7, "연체일수": 1})
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert e.value.status == 409 and e.value.code == "function_changed"
    assert "연체여부 changed (v7 -> v1)" in e.value.message


def test_pending_approval_fails_when_a_function_changed_since_submit(setup):  # noqa: F811
    settings, view, _ = setup
    _gate_on_overdue(view)
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert run.status == "pending_approval" and run.preview["functions"]["연체여부"] == 1
    _editor_rewrites(view, expr='수납상태 != "완납"')
    registry.set_status(view, "action", ACTION, "active", actor="owner@x")
    done = actions.approve(settings, view, run.id, OWNER)
    assert done.status == "failed" and done.result == {"failure": "function_changed"}
    assert "연체여부 changed (v1 -> v2)" in done.error


def test_unpinned_legacy_action_is_pinned_on_first_use(setup):  # noqa: F811
    settings, view, _ = setup
    with sqlite3.connect(view.store.path) as conn:
        conn.execute("UPDATE logic_defs SET function_pins_json = NULL WHERE kind = 'action'")
    assert view.logic_defs().get("action", ACTION).function_pins is None
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert run.status == "applied"
    assert view.logic_defs().get("action", ACTION).function_pins == {"미수금": 1, "연체일수": 1}


def test_workflow_is_pinned_and_restored_to_draft_after_a_function_edit(wf_env):  # noqa: F811
    settings, view, _, _ = wf_env
    _activate(view, {**sample_workflow(), "trigger": {"manual": True}}, actor="owner@x")
    assert view.logic_defs().get("workflow", WORKFLOW).function_pins == {
        "연체여부": 1,
        "연체일수": 1,
    }
    view.logic_defs().set_pins("workflow", WORKFLOW, {"연체여부": 9, "연체일수": 1})
    with pytest.raises(ObjectError) as e:
        _run(settings, view)
    assert e.value.code == "function_changed"
    registry.set_status(view, "workflow", WORKFLOW, "active", actor="owner@x")
    _editor_rewrites(view, name="연체일수", expr="0")
    # the action it runs uses the function too: a draft now, so the workflow is broken
    assert view.logic_defs().get("action", ACTION).status == "draft"
    assert view.logic_defs().get("workflow", WORKFLOW).status == "broken"
    # the owner activates the action again: the workflow still needs its own activation
    registry.set_status(view, "action", ACTION, "active", actor="owner@x")
    d = view.logic_defs().get("workflow", WORKFLOW)
    assert d.status == "draft" and d.to_dict()["needs_reactivation"]
    assert "연체일수 changed (v1 -> v2)" in d.broken_reason


def test_used_by_active_lists_actions_and_broken_ones(setup):  # noqa: F811
    _, view, _ = setup
    assert registry.used_by_active(view, "연체일수") == [f"action:{ACTION}"]
    assert registry.used_by_active(view, "남은일수") == []
    registry.save(view, {**_function("연체일수"), "expr": "1"}, actor="ed@x")  # now a draft
    assert view.logic_defs().get("action", ACTION).status == "broken"
    assert registry.used_by_active(view, "연체일수") == [f"action:{ACTION}"]


def test_editor_needs_owner_to_activate_a_function_an_active_action_uses(aenv):  # noqa: F811
    editor = aenv.client("e@x.io", "editor")
    owner = aenv.client("o@x.io", "owner")
    url = f"{API}/logic/function/연체일수/status"
    refused = editor.post(url, json={"status": "retired"})
    assert refused.status_code == 403 and refused.json()["permission"] == "logic.activate"
    assert refused.json()["used_by"] == [f"action:{ACTION}"]
    # an unused function stays an editor's
    free = editor.post(f"{API}/logic/function/남은일수/status", json={"status": "draft"})
    assert free.status_code == 200
    # the editor's edit breaks the action; re-activating the function needs the owner
    saved = editor.post(f"{API}/logic", json={"spec": {**_function("연체일수"), "expr": "1"}})
    assert saved.status_code == 200
    again = editor.post(url, json={"status": "active"})
    assert again.status_code == 403
    assert owner.post(url, json={"status": "active"}).status_code == 200
    action = owner.get(f"{API}/logic/action/{ACTION}").json()["definition"]
    assert action["status"] == "draft" and action["needs_reactivation"] is True


# --- H2: four-eyes and agent tokens ---------------------------------------------------------


def test_token_creator_cannot_approve_their_agents_proposal(setup):  # noqa: F811
    settings, view, _ = setup
    a = sample_specs("actions.yaml")[0]
    registry.save(view, {**a, "approval": {"role": "reviewer"}, "agent": "propose"})
    registry.set_status(view, "action", ACTION, "active")
    agent = Caller(
        "agent:tok_1",
        role="editor",
        four_eyes=True,
        via="agent",
        token_id="tok_1",
        agent_mode="propose",
        requested_for="ed@x",
    )
    run, _ = actions.submit(settings, view, ACTION, "INV-3", {"단계": 1}, agent)
    assert run.requested_by == "agent:tok_1" and run.requested_for == "ed@x"
    assert actions.may_decide(run, EDITOR) is False
    assert actions.run_dict(run, EDITOR)["can_approve"] is False
    with pytest.raises(ObjectError) as e:
        actions.approve(settings, view, run.id, EDITOR)
    assert e.value.code == "self_approval"
    # another reviewer approves
    assert actions.approve(settings, view, run.id, REVIEWER).status == "applied"
    # the creator may withdraw their agent's request
    other, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, agent)
    assert actions.reject(view, other.id, EDITOR).status == "rejected"
    # a direct run records its requester
    mine, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    assert mine.requested_for == "ed@x"


def test_v12_backfills_requested_for(tmp_path):
    from autoontology.store.accounts import Accounts

    store = Store(tmp_path / "s.db")
    store.migrate()
    view = store.project()
    user = Accounts(store).create_user("Ed@X.io")
    _, token = view.agent_tokens().create("t", created_by=user.id, scopes=["propose"])
    runs = view.action_runs()
    common = {"action_name": "a", "action_version": 1, "object_type": "t", "params": {}}
    direct = runs.create(object_key="1", status="applied", requested_by="o@x.io", **common)
    agent = runs.create(
        object_key="2",
        status="pending_approval",
        requested_by=f"agent:{token.id}",
        via="agent",
        token_id=token.id,
        **common,
    )
    orphan = runs.create(
        object_key="3", status="applied", requested_by="agent:tok_x", via="agent", **common
    )
    # back to v11 and up again
    with sqlite3.connect(store.path) as conn:
        conn.execute("ALTER TABLE action_runs DROP COLUMN requested_for")
        conn.execute("ALTER TABLE logic_defs DROP COLUMN function_pins_json")
        conn.execute("DELETE FROM schema_version WHERE version >= 12")
    assert store.migrate() == 12 == db.MIGRATIONS[-1][0]
    assert store.migrate() == 12  # idempotent
    assert runs.get(direct.id).requested_for == "o@x.io"
    assert runs.get(agent.id).requested_for == "ed@x.io"
    assert runs.get(orphan.id).requested_for == ""
    with sqlite3.connect(store.path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(logic_defs)")}
    assert "function_pins_json" in cols


# --- M1: archived projects ------------------------------------------------------------------


def test_agent_cannot_submit_on_an_archived_project(actions_client):  # noqa: F811
    c, store = actions_client
    proposer = c.post(f"{API}/agent/tokens", json={"name": "p", "scopes": ["propose"]}).json()
    _archive(store)
    err, refused = _rpc(c, proposer["token"], "submit_action", {"name": ACTION, "key": "INV-2"})
    assert err and refused["error"] == "project_archived"
    rest = c.post(
        f"/agent/v1/actions/{ACTION}/submit",
        json={"key": "INV-2"},
        headers={"Authorization": f"Bearer {proposer['token']}"},
    )
    assert rest.status_code == 409 and rest.json()["error"] == "project_archived"
    # reading and previewing still work
    err, prev = _rpc(c, proposer["token"], "preview_action", {"name": ACTION, "key": "INV-2"})
    assert not err and prev["preview"]["ok"]
    assert store.project().action_runs().list() == []


def test_workflows_do_not_start_on_an_archived_project(wf_env):  # noqa: F811
    settings, view, _, _ = wf_env
    _activate(view, {**sample_workflow(), "trigger": {"manual": True}})
    _activate(
        view,
        _wf(
            [{"notify": {"to": "email:a@x.io", "subject": "s"}}],
            name="후속",
            trigger={"on_action": ACTION},
        ),
    )
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    _archive(view.store)
    assert workflows.on_action(settings, view, run) == []
    with pytest.raises(ObjectError) as e:
        _run(settings, view)
    assert e.value.code == "project_archived"
    _archive(view.store, on=False)
    assert [r.workflow_name for r in workflows.on_action(settings, view, run)] == ["후속"]


# --- M2: manual runs and notification caps --------------------------------------------------


def test_a_manual_run_is_refused_while_another_runs(wf_env):  # noqa: F811
    settings, view, _, _ = wf_env
    _activate(view, {**sample_workflow(), "trigger": {"manual": True}})
    first, created = workflows.start(
        settings, view, WORKFLOW, trigger="manual", fire_slot="manual:a", started_by="t"
    )
    assert created and first.status == "running"
    with pytest.raises(ObjectError) as e:
        workflows.start(
            settings, view, WORKFLOW, trigger="manual", fire_slot="manual:b", started_by="t"
        )
    assert e.value.status == 409 and e.value.code == "run_active"
    assert e.value.extra["run_id"] == first.id
    # other triggers are not held back (a schedule slot is unique by itself)
    _, created = workflows.start(
        settings, view, WORKFLOW, trigger="schedule", fire_slot="2026-11-25T02:00", started_by="s"
    )
    assert created
    for open_run in view.workflow_runs().list(workflow=WORKFLOW, status="running"):
        workflows.cancel(view, open_run.id, actor="t")
    _, created = workflows.start(
        settings, view, WORKFLOW, trigger="manual", fire_slot="manual:c", started_by="t"
    )
    assert created


def test_workflow_notifications_are_capped_per_run(wf_env, monkeypatch):  # noqa: F811
    settings, view, _, mailer = wf_env
    monkeypatch.setattr(workflows, "MAX_RUN_RECIPIENTS", 5)
    to = [f"email:p{i}@x.io" for i in range(3)]
    _activate(view, _wf([{"notify": {"to": to, "subject": "{{ 청구ID }}"}}]))
    run = _run(settings, view, name="테스트")
    # 4 overdue invoices x 3 recipients = 12, capped at 5
    assert run.status == "succeeded" and len(mailer.sent) == 5
    assert run.summary["recipients_notified"] == 5 and run.summary["notifications_capped"] == 7


def test_action_notifications_are_capped_per_run(setup, monkeypatch):  # noqa: F811
    settings, view, _ = setup
    monkeypatch.setattr(actions, "MAX_RUN_RECIPIENTS", 2)
    a = sample_specs("actions.yaml")[0]
    many = [f"email:p{i}@x.io" for i in range(3)]
    registry.save(
        view, {**a, "notify": [{"to": many, "subject": "a"}, {"to": many, "subject": "b"}]}
    )
    registry.set_status(view, "action", ACTION, "active")
    mailer = FakeMailer()
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR, mailer=mailer)
    actions.wait_notifications()
    assert [to for to, _, _ in mailer.sent] == ["p0@x.io", "p1@x.io"]
    notes = view.action_runs().get(run.id).result["notifications"]
    assert notes["recipients"] == 2 and notes["notifications_capped"] == 4


def test_editors_run_only_manual_workflows(aenv):  # noqa: F811
    editor = aenv.client("e@x.io", "editor")
    owner = aenv.client("o@x.io", "owner")
    registry.save(aenv.view, {**sample_workflow(), "trigger": {"schedule": "daily 02:00"}})
    registry.set_status(aenv.view, "workflow", WORKFLOW, "active", actor="o@x.io")
    url = f"{API}/workflows/{WORKFLOW}/run"
    refused = editor.post(url, json={"wait": True})
    assert refused.status_code == 403 and refused.json()["permission"] == "logic.activate"
    assert owner.post(url, json={"wait": True}).status_code == 200
    registry.save(aenv.view, {**sample_workflow(), "trigger": {"manual": True}})
    registry.set_status(aenv.view, "workflow", WORKFLOW, "active", actor="o@x.io")
    assert editor.post(url, json={"wait": True}).status_code == 200
    workflows.wait()


# --- L1: mail ---------------------------------------------------------------------------------


def test_mailer_folds_line_breaks_in_the_subject(monkeypatch):
    import smtplib

    from autoontology.web.mailer import Mailer

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **kw):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    m = Mailer(host="smtp.example", port=25, sender="ao@example.com", starttls=False)
    assert m.send("a@x.io", "[독촉]\r\nBcc: evil@x.io\nmore", "body") is True
    assert sent[0]["Subject"] == "[독촉] Bcc: evil@x.io more"
    assert sent[0]["Bcc"] is None


def test_one_failing_recipient_does_not_stop_the_others(setup):  # noqa: F811
    settings, view, _ = setup
    a = sample_specs("actions.yaml")[0]
    registry.save(view, {**a, "notify": [{"to": ["email:a@x.io", "email:b@x.io"], "subject": "s"}]})
    registry.set_status(view, "action", ACTION, "active")

    class Flaky(FakeMailer):
        def send(self, to, subject, body):
            if to == "a@x.io":
                raise ValueError("bad header")
            return super().send(to, subject, body)

    mailer = Flaky()
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR, mailer=mailer)
    actions.wait_notifications()
    assert [to for to, _, _ in mailer.sent] == ["b@x.io"]
    notes = view.action_runs().get(run.id).result["notifications"]
    assert notes["status"] == "partial" and notes["sent"] == 1 and notes["failed"] == 1


def test_workflow_notify_survives_a_failing_recipient(wf_env):  # noqa: F811
    settings, view, _, _ = wf_env

    class Flaky(FakeMailer):
        def send(self, to, subject, body):
            if to == "a@x.io":
                raise ValueError("bad header")
            return super().send(to, subject, body)

    mailer = Flaky()
    _activate(view, _wf([{"notify": {"to": ["email:a@x.io", "email:b@x.io"], "subject": "s"}}]))
    run = _run(settings, view, name="테스트", mailer=mailer)
    assert run.status == "succeeded" and len(mailer.sent) == 4
    steps = [o["steps"][0] for o in run.state["objects"]]
    assert all(s["status"] == "partial" and s["sent"] == 1 for s in steps)


# --- L2: key changes ------------------------------------------------------------------------


def test_key_change_is_refused_while_live_edits_exist(setup):  # noqa: F811
    settings, view, _ = setup
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    with pytest.raises(ObjectError) as e:
        service.bind(settings, view, INV, key="청구일")
    assert e.value.status == 409 and e.value.code == "edits_exist" and e.value.extra["edits"] == 2
    # the same key (a title change) is fine
    service.bind(settings, view, INV, key="청구ID")
    # once reverted, the key may change (verified against the source as usual)
    actions.revert(settings, view, run.id, OWNER)
    with pytest.raises(ObjectError) as e:
        service.bind(settings, view, INV, key="수납상태")
    assert e.value.code != "edits_exist"


# --- L3: read-only POST routes on archived projects -----------------------------------------


def test_read_only_posts_work_on_an_archived_project(actions_client):  # noqa: F811
    c, store = actions_client
    _archive(store)
    spec = _function("미수금")
    assert c.post(f"{API}/logic/validate", json={"spec": spec}).status_code == 200
    assert c.post(f"{API}/logic/test", json={"spec": spec, "keys": ["INV-2"]}).status_code == 200
    assert c.post(f"{API}/logic/functions/미수금/test", json={"keys": ["INV-2"]}).status_code == 200
    assert c.post(f"{API}/logic/functions/미수금/call", json={"key": "INV-2"}).status_code == 200
    p = c.post(f"{API}/actions/{ACTION}/preview", json={"key": "INV-2"})
    assert p.status_code == 200, p.text
    # writes stay refused
    w = c.post(f"{API}/actions/{ACTION}/submit", json={"key": "INV-2"})
    assert w.status_code == 409 and w.json()["error"] == "project_archived"
