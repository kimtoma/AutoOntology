# SPDX-License-Identifier: MIT
"""The action runner (``autoontology/logic/actions.py``): preview, submit, idempotency,
approval (four-eyes, reject, preconditions changed meanwhile), revert and superseded
edits, permissions, notifications and agents' modes -- over the quickstart sample specs
with the sources faked."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autoontology.config import Settings
from autoontology.logic import actions, functions, registry, specs
from autoontology.logic.actions import Caller
from autoontology.objects import service
from autoontology.objects.reader import ObjectError
from autoontology.store.db import Store
from tests import logic_fixtures as fx
from tests.logic_fixtures import INV
from tests.test_logic import TODAY

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "quickstart" / "logic"
ACTION = "독촉단계기록"


class FakeMailer:
    enabled = True

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, to: str, subject: str, body: str) -> bool:
        if to in self.fail:
            return False
        self.sent.append((to, subject, body))
        return True


def sample_specs(name: str) -> list[dict]:
    return specs.load_documents((SAMPLES / name).read_text("utf-8"))


def activate_samples(view, action_overrides: dict | None = None) -> None:
    for doc in sample_specs("functions.yaml"):
        registry.save(view, doc)
    for doc in sample_specs("functions.yaml"):
        registry.set_status(view, "function", doc["name"], "active")
    for doc in sample_specs("actions.yaml"):
        registry.save(view, {**doc, **(action_overrides or {})})
        registry.set_status(view, "action", doc["name"], "active")


@pytest.fixture
def setup(isolated_env, monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    backend = fx.install_backend(monkeypatch)
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    view = fx.seed(store)
    activate_samples(view)
    yield settings, view, backend
    actions.wait_notifications()


OWNER = Caller("owner@x", role="owner", four_eyes=True)
EDITOR = Caller("ed@x", role="editor", four_eyes=True)
REVIEWER = Caller("rev@x", role="reviewer", four_eyes=True)
VIEWER = Caller("view@x", role="viewer", four_eyes=True)


def _value(settings, view, key: str, prop: str):
    return service.get_object(settings, view, INV, key)["values"].get(prop)


def _audit(view) -> list[tuple[str, str]]:
    with sqlite3.connect(view.store.path) as conn:
        return conn.execute(
            "SELECT action, target FROM audit_log WHERE action LIKE 'action.%' ORDER BY id"
        ).fetchall()


# --- preview ----------------------------------------------------------------------------


def test_preview_effects_preconditions_approval_and_params(setup):
    settings, view, _ = setup
    p = actions.preview(settings, view, ACTION, "INV-2", {"단계": "2"}, EDITOR)
    assert p["ok"] and p["can_submit"] and p["params"] == {"단계": 2}
    assert p["preconditions"] == [{"index": 0, "message": "이미 완납된 청구서입니다.", "ok": True}]
    assert [
        (e["property"], e["old_value"], e["new_value"], e["operational"]) for e in p["effects"]
    ] == [
        ("독촉단계", None, 2, True),
        ("독촉메모", None, "", True),
    ]
    assert p["approval"] == {"required": False, "role": None, "reason": ""}
    note = p["notify"][0]
    assert note["subject"] == "[독촉 2단계] INV-2"
    assert "미수금 6000000원" in note["body"] and "연체 55일" in note["body"]
    # accounts off: role recipients cannot be resolved
    assert note["recipients"] == 0 and note["skipped_roles"] and note["to"] == ["role:editor"]
    # approval with `when`
    p4 = actions.preview(settings, view, ACTION, "INV-2", {"단계": 4}, EDITOR)
    assert p4["approval"] == {"required": True, "role": "owner", "reason": "when"}
    # default parameter
    assert actions.preview(settings, view, ACTION, "INV-2", {}, EDITOR)["params"] == {"단계": 1}
    # a failing precondition
    paid = actions.preview(settings, view, ACTION, "INV-1", {}, EDITOR)
    assert not paid["ok"] and not paid["can_submit"]
    assert paid["preconditions"][0]["ok"] is False
    # the viewer sees the preview but may not submit
    seen = actions.preview(settings, view, ACTION, "INV-2", {}, VIEWER)
    assert seen["ok"] and not seen["can_submit"] and not seen["permission"]["allowed"]
    # nothing stored
    assert view.action_runs().list() == []


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"단계": 7}, "invalid_param"),
        ({"단계": "x"}, "invalid_param"),
        ({"없음": 1}, "unknown_param"),
        ({"메모": "a" * 20000}, "params_too_large"),
        ([1], "invalid_param"),
    ],
)
def test_preview_rejects_bad_params(setup, params, code):
    settings, view, _ = setup
    with pytest.raises(ObjectError) as e:
        actions.preview(settings, view, ACTION, "INV-2", params, EDITOR)
    assert e.value.code == code and e.value.status == 400


def test_preview_unknown_inactive_and_missing(setup):
    settings, view, _ = setup
    for name, key, code in (
        ("없음", "INV-2", "unknown_action"),
        (ACTION, "INV-9", "unknown_object"),
    ):
        with pytest.raises(ObjectError) as e:
            actions.preview(settings, view, name, key, {}, EDITOR)
        assert e.value.code == code
    registry.set_status(view, "action", ACTION, "retired")
    with pytest.raises(ObjectError) as e:
        actions.preview(settings, view, ACTION, "INV-2", {}, EDITOR)
    assert e.value.code == "unknown_action"  # retired definitions are out of the context
    registry.save(view, sample_specs("actions.yaml")[0])  # back as a draft
    with pytest.raises(ObjectError) as e:
        actions.preview(settings, view, ACTION, "INV-2", {}, EDITOR)
    assert e.value.code == "action_not_active"


# --- submit -----------------------------------------------------------------------------


def test_submit_applies_and_the_overlay_shows_it(setup):
    settings, view, _ = setup
    run, created = actions.submit(
        settings, view, ACTION, "INV-2", {"단계": 2, "메모": "전화"}, EDITOR
    )
    assert created and run.status == "applied" and run.requested_by == "ed@x"
    assert run.via == "ui" and run.params == {"단계": 2, "메모": "전화"}
    assert [e["property"] for e in run.result["effects"]] == ["독촉단계", "독촉메모"]
    assert _value(settings, view, "INV-2", "독촉단계") == 2
    obj = service.get_object(settings, view, INV, "INV-2")
    assert obj["edits"]["독촉메모"]["action_run_id"] == run.id
    # a second run builds on the first (old value = the edit)
    run2, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 3}, EDITOR)
    assert run2.result["effects"][0]["old_value"] == 2
    assert _value(settings, view, "INV-2", "독촉단계") == 3
    # preconditions refuse with 409 and their messages; nothing is stored
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-1", {}, EDITOR)
    assert e.value.status == 409 and e.value.code == "precondition_failed"
    assert e.value.extra["failures"][0]["message"] == "이미 완납된 청구서입니다."
    assert len(view.action_runs().list()) == 2
    assert [a for a, _ in _audit(view)] == [
        "action.submit",
        "action.apply",
        "action.submit",
        "action.apply",
    ]


def test_submit_never_writes_the_source(setup):
    settings, view, backend = setup
    before = json.dumps(backend.tables, ensure_ascii=False, sort_keys=True, default=str)
    actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    after = json.dumps(backend.tables, ensure_ascii=False, sort_keys=True, default=str)
    assert before == after


def test_column_effects_are_coerced_and_conflict_aware(setup):
    settings, view, backend = setup
    registry.save(
        view,
        {
            "kind": "action",
            "name": "수납기록",
            "label": "수납 기록",
            "object_type": "청구",
            "params": [{"name": "금액", "type": "number"}],
            "effects": [
                {"set": "수납금액", "value": "coalesce(수납금액, 0) + param.금액"},
                {
                    "set": "수납상태",
                    "value": "if(coalesce(수납금액, 0) + param.금액 >= 청구금액, '완납', '부분납')",
                },
            ],
        },
    )
    registry.set_status(view, "action", "수납기록", "active")
    run, _ = actions.submit(settings, view, "수납기록", "INV-2", {"금액": "6000000"}, EDITOR)
    assert [(e["property"], e["old_value"], e["new_value"]) for e in run.result["effects"]] == [
        ("수납금액", 3000000, 9000000),
        ("수납상태", "미납", "완납"),
    ]
    assert _value(settings, view, "INV-2", "수납상태") == "완납"
    # now paid: the reminder action's precondition refuses
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-2", {}, EDITOR)
    assert e.value.code == "precondition_failed"


def test_idempotency(setup):
    settings, view, _ = setup
    run, created = actions.submit(
        settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR, idempotency_key="k1"
    )
    again, created2 = actions.submit(
        settings, view, ACTION, "INV-2", {"단계": 3}, EDITOR, idempotency_key="k1"
    )
    assert created and not created2 and again.id == run.id
    assert len(view.action_runs().list()) == 1
    assert _value(settings, view, "INV-2", "독촉단계") == 1
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-3", {}, EDITOR, idempotency_key="k1")
    assert e.value.code == "idempotency_conflict"
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-3", {}, EDITOR, idempotency_key="x" * 300)
    assert e.value.code == "invalid_idempotency_key"


def test_object_changed_during_evaluation_is_evaluated_again(setup, monkeypatch):
    settings, view, _ = setup
    real = actions._plan
    calls = []

    def racing(*args, **kw):
        plan = real(*args, **kw)
        calls.append(1)
        if len(calls) == 1:  # another writer commits after the first evaluation
            actions.submit(settings, view, ACTION, "INV-2", {"단계": 2}, EDITOR)
        return plan

    monkeypatch.setattr(actions, "_plan", racing)
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 3}, EDITOR)
    assert len(calls) == 3  # first (stale), the inner submit, the retry
    assert run.result["effects"][0]["old_value"] == 2  # saw the other run


# --- approval ---------------------------------------------------------------------------


def test_approval_flow_four_eyes_and_apply(setup):
    settings, view, _ = setup
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, OWNER)
    assert run.status == "pending_approval" and run.approval_role == "owner"
    assert run.preview["approval"]["required"] is True
    assert _value(settings, view, "INV-2", "독촉단계") is None
    # self-approval blocked with accounts on; a reviewer lacks the role
    with pytest.raises(ObjectError) as e:
        actions.approve(settings, view, run.id, OWNER)
    assert e.value.code == "self_approval"
    with pytest.raises(ObjectError) as e:
        actions.approve(settings, view, run.id, REVIEWER)
    assert e.value.status == 403 and e.value.extra["permission"] == "action.approve"
    assert actions.pending_approvals(view, OWNER) == 0
    other = Caller("boss@x", role="owner", four_eyes=True)
    assert actions.pending_approvals(view, other) == 1
    done = actions.approve(settings, view, run.id, other)
    assert done.status == "applied" and done.approver == "boss@x" and done.applied_at
    assert _value(settings, view, "INV-2", "독촉단계") == 4
    edits = view.object_edits().by_run(run.id)
    assert {e.edited_by for e in edits} == {"owner@x"}
    with pytest.raises(ObjectError) as e:
        actions.approve(settings, view, run.id, other)
    assert e.value.code == "not_pending"
    assert [a for a, _ in _audit(view)] == [
        "action.submit",
        "action.approve",
        "action.apply",
    ]
    assert {t for _, t in _audit(view)} == {run.id}


def test_off_mode_allows_self_approval(setup):
    settings, view, _ = setup
    local = Caller("local")  # accounts off: owner, no four-eyes
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, local)
    assert run.status == "pending_approval"
    assert actions.approve(settings, view, run.id, local).status == "applied"


def test_reviewer_approval_and_reject(setup):
    settings, view, _ = setup
    registry.save(view, {**sample_specs("actions.yaml")[0], "approval": {"role": "reviewer"}})
    registry.set_status(view, "action", ACTION, "active")
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {}, EDITOR)
    assert run.approval_role == "reviewer"
    with pytest.raises(ObjectError) as e:
        actions.reject(view, run.id, VIEWER, "no")
    assert e.value.status == 403
    done = actions.reject(view, run.id, REVIEWER, "not yet")
    assert done.status == "rejected" and done.result == {"note": "not yet"}
    assert _value(settings, view, "INV-2", "독촉단계") is None
    # the requester may withdraw their own
    run2, _ = actions.submit(settings, view, ACTION, "INV-3", {}, EDITOR)
    assert actions.reject(view, run2.id, EDITOR).status == "rejected"
    run3, _ = actions.submit(settings, view, ACTION, "INV-3", {}, EDITOR)
    assert actions.approve(settings, view, run3.id, REVIEWER).status == "applied"
    assert ("action.reject", run.id) in _audit(view)


def test_precondition_changed_before_approval_fails_the_run(setup):
    settings, view, backend = setup
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, EDITOR)
    for row in backend.tables["public.invoices"]["rows"]:
        if row["청구ID"] == "INV-2":
            row["수납상태"] = "완납"
    done = actions.approve(settings, view, run.id, OWNER)
    assert done.status == "failed" and "완납" in done.error
    assert done.result == {"failure": "precondition_failed"}
    assert _value(settings, view, "INV-2", "독촉단계") is None
    assert ("action.fail", run.id) in _audit(view)


def test_action_changed_before_approval_fails_the_run(setup):
    settings, view, _ = setup
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, EDITOR)
    registry.save(view, {**sample_specs("actions.yaml")[0], "label": "독촉 v2"})
    done = actions.approve(settings, view, run.id, OWNER)
    assert done.status == "failed" and done.result["failure"] == "action_not_active"
    registry.set_status(view, "action", ACTION, "active")
    run2, _ = actions.submit(settings, view, ACTION, "INV-3", {"단계": 4}, EDITOR)
    registry.save(view, {**sample_specs("actions.yaml")[0], "label": "독촉 v3"})
    registry.set_status(view, "action", ACTION, "active")
    done2 = actions.approve(settings, view, run2.id, OWNER)
    assert done2.status == "failed" and done2.result["failure"] == "action_changed"


# --- revert -----------------------------------------------------------------------------


def test_revert_and_superseded(setup):
    settings, view, _ = setup
    first, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, EDITOR)
    second, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 2}, EDITOR)
    with pytest.raises(ObjectError) as e:
        actions.revert(settings, view, first.id, EDITOR)
    assert e.value.code == "superseded" and e.value.extra["properties"] == ["독촉단계", "독촉메모"]
    with pytest.raises(ObjectError) as e:
        actions.revert(settings, view, first.id, VIEWER)
    assert e.value.status == 403
    with pytest.raises(ObjectError) as e:
        actions.revert(settings, view, first.id, EDITOR, force=True)
    assert e.value.status == 403  # forcing needs an owner
    done = actions.revert(settings, view, second.id, EDITOR)
    assert done.status == "reverted" and done.reverted_at
    assert _value(settings, view, "INV-2", "독촉단계") == 1
    with pytest.raises(ObjectError) as e:
        actions.revert(settings, view, second.id, EDITOR)
    assert e.value.code == "not_applied"
    # the first is no longer superseded once the second is reverted
    assert actions.revert(settings, view, first.id, EDITOR).status == "reverted"
    assert _value(settings, view, "INV-2", "독촉단계") is None
    # forced revert by an owner keeps the later value
    a, _ = actions.submit(settings, view, ACTION, "INV-3", {"단계": 1}, EDITOR)
    actions.submit(settings, view, ACTION, "INV-3", {"단계": 3}, EDITOR)
    forced = actions.revert(settings, view, a.id, OWNER, force=True)
    assert forced.result["reverted"]["forced"] is True
    assert _value(settings, view, "INV-3", "독촉단계") == 3
    assert ("action.revert", a.id) in _audit(view)


# --- permissions ------------------------------------------------------------------------


def test_permission_min_role(setup):
    settings, view, _ = setup
    for caller in (VIEWER, REVIEWER):
        with pytest.raises(ObjectError) as e:
            actions.submit(settings, view, ACTION, "INV-2", {}, caller)
        assert e.value.status == 403 and e.value.extra == {
            "permission": "action.run",
            "min_role": "editor",
        }
    registry.save(view, {**sample_specs("actions.yaml")[0], "permission": {"min_role": "viewer"}})
    registry.set_status(view, "action", ACTION, "active")
    assert actions.submit(settings, view, ACTION, "INV-2", {}, VIEWER)[0].status == "applied"
    registry.save(view, {**sample_specs("actions.yaml")[0], "permission": {"min_role": "owner"}})
    registry.set_status(view, "action", ACTION, "active")
    with pytest.raises(ObjectError):
        actions.submit(settings, view, ACTION, "INV-2", {}, EDITOR)


def test_available_actions_for_an_object(setup):
    settings, view, _ = setup
    unpaid = service.get_object(settings, view, INV, "INV-2")
    paid = service.get_object(settings, view, INV, "INV-1")
    assert actions.available(settings, view, INV, unpaid, EDITOR) == [
        {"name": ACTION, "label": "독촉 단계 기록", "available": True}
    ]
    assert actions.available(settings, view, INV, paid, EDITOR)[0] == {
        "name": ACTION,
        "label": "독촉 단계 기록",
        "available": False,
        "reason": "이미 완납된 청구서입니다.",
    }
    assert actions.available(settings, view, INV, unpaid, VIEWER)[0]["reason"] == "permission"
    assert actions.available(settings, view, fx.CON, {"values": {}}, EDITOR) == []
    listed = actions.list_actions(view)
    assert [a["name"] for a in listed] == [ACTION] and listed[0]["agent"] == "propose"
    assert actions.list_actions(view, "계약") == []


# --- notifications ----------------------------------------------------------------------


def test_notifications_to_roles_emails_and_fields(setup, monkeypatch):
    settings, view, _ = setup
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    settings = Settings.from_env()
    from autoontology.store.accounts import Accounts

    acc = Accounts(view.store)
    for email, role in (
        ("v@x.io", "viewer"),
        ("e@x.io", "editor"),
        ("o@x.io", "owner"),
        ("gone@x.io", "editor"),
    ):
        user = acc.create_user(email)
        acc.add_member(view.id, user.id, role)
    acc.disable(acc.by_email("gone@x.io").id)
    registry.save(
        view,
        {
            **sample_specs("actions.yaml")[0],
            "approval": {"role": "none"},
            "notify": [
                {
                    "to": ["role:editor", "email:fixed@x.io"],
                    "subject": "S {{ 청구ID }}",
                    "body": "B",
                },
                {"to": "field:계약.고객.담당자", "subject": "bad field"},
            ],
        },
    )
    registry.set_status(view, "action", ACTION, "active")
    p = actions.preview(settings, view, ACTION, "INV-2", {}, EDITOR)
    assert [n["recipients"] for n in p["notify"]] == [3, 0]
    assert p["notify"][1]["invalid"] == 1  # 최하은 is no email address
    mailer = FakeMailer(fail=("fixed@x.io",))
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {}, EDITOR, mailer=mailer)
    actions.wait_notifications()
    assert sorted(t for t, _, _ in mailer.sent) == ["e@x.io", "o@x.io"]
    assert mailer.sent[0][1] == "S INV-2"
    stored = view.action_runs().get(run.id).result["notifications"]
    assert stored == {
        "messages": 2,
        "recipients": 3,
        "invalid": 1,
        "skipped_roles": False,
        "status": "partial",
        "sent": 2,
        "failed": 1,
    }
    # a disabled mailer is recorded, never an error
    off = FakeMailer()
    off.enabled = False
    run2, _ = actions.submit(settings, view, ACTION, "INV-3", {}, EDITOR, mailer=off)
    actions.wait_notifications()
    assert view.action_runs().get(run2.id).result["notifications"]["status"] == "mail_disabled"


def test_mailer_exception_never_fails_the_run(setup):
    settings, view, _ = setup
    registry.save(
        view,
        {
            **sample_specs("actions.yaml")[0],
            "notify": [{"to": "email:a@x.io", "subject": "s"}],
        },
    )
    registry.set_status(view, "action", ACTION, "active")

    class Boom(FakeMailer):
        def send(self, to, subject, body):
            raise RuntimeError("smtp down")

    run, _ = actions.submit(settings, view, ACTION, "INV-2", {}, EDITOR, mailer=Boom())
    actions.wait_notifications()
    assert run.status == "applied"
    assert view.action_runs().get(run.id).result["notifications"]["status"] == "failed"


# --- agents -----------------------------------------------------------------------------


def _agent(mode, role="editor", allowed=None):
    return Caller(
        "agent:tok_1",
        role=role,
        four_eyes=True,
        via="agent",
        token_id="tok_1",
        agent_mode=mode,
        allowed_actions=allowed,
    )


def test_agent_modes(setup):
    settings, view, _ = setup
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-2", {}, _agent(None))
    assert e.value.code == "scope_required"
    # spec agent: propose -> even an execute token only proposes
    run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 1}, _agent("execute"))
    assert run.status == "pending_approval" and run.approval_role == "owner"
    assert run.via == "agent" and run.token_id == "tok_1"
    assert run.preview["approval"]["reason"] == "agent_propose"
    # agents never decide; a person with the role (the token creator too) does
    with pytest.raises(ObjectError) as e:
        actions.approve(settings, view, run.id, _agent("execute", role="owner"))
    assert e.value.code == "agent_not_allowed"
    assert actions.approve(settings, view, run.id, OWNER).status == "applied"
    # spec agent: execute -> execute follows the spec's approval rules
    registry.save(
        view,
        {**sample_specs("actions.yaml")[0], "agent": "execute", "approval": {"role": "none"}},
    )
    registry.set_status(view, "action", ACTION, "active")
    applied, _ = actions.submit(settings, view, ACTION, "INV-3", {}, _agent("execute"))
    assert applied.status == "applied"
    proposed, _ = actions.submit(settings, view, ACTION, "INV-3", {}, _agent("propose"))
    assert proposed.status == "pending_approval" and proposed.approval_role == "reviewer"
    # allow-list and the creator's role
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-3", {}, _agent("execute", allowed=("다른",)))
    assert e.value.code == "action_not_allowed"
    ok, _ = actions.submit(
        settings, view, ACTION, "INV-3", {}, _agent("execute", allowed=(ACTION,))
    )
    assert ok.status == "applied"
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-3", {}, _agent("execute", role="viewer"))
    assert e.value.status == 403 and e.value.extra["permission"] == "action.run"
    # spec agent: none refuses
    registry.save(view, {**sample_specs("actions.yaml")[0], "agent": "none"})
    registry.set_status(view, "action", ACTION, "active")
    with pytest.raises(ObjectError) as e:
        actions.submit(settings, view, ACTION, "INV-3", {}, _agent("execute"))
    assert e.value.code == "agent_not_allowed"


def test_listener_sees_decisions(setup):
    settings, view, _ = setup
    seen = []

    def listener(v, run):
        seen.append(run.status)
        raise RuntimeError("ignored")

    actions.on_run_decided(listener)
    try:
        run, _ = actions.submit(settings, view, ACTION, "INV-2", {"단계": 4}, EDITOR)
        assert seen == []
        actions.approve(settings, view, run.id, OWNER)
        actions.revert(settings, view, run.id, OWNER)
    finally:
        actions.remove_listener(listener)
    assert seen == ["applied", "reverted"]
