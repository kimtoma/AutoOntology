# SPDX-License-Identifier: MIT
"""The action routes (``/api/actions``), ``actions`` on object details, the
``approvals`` count, the ``onto action`` commands, token scopes and the agent action tools
-- off mode and accounts mode (sources faked; the runner in ``test_actions.py``)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ontology.config import Settings
from ontology.logic import actions, functions
from ontology.store import accounts as acc
from ontology.store.accounts import Accounts
from ontology.store.db import Store
from ontology.web import app as web
from ontology.web.runs import RunManager
from tests import logic_fixtures as fx
from tests.logic_fixtures import INV
from tests.test_actions import ACTION, activate_samples, sample_specs
from tests.test_logic import TODAY

API = "/api"


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    return fx.install_backend(monkeypatch)


@pytest.fixture
def client(isolated_env, backend):
    settings = Settings.from_env()
    with TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict))) as c:
        store = Store.for_home(settings.home)
        activate_samples(fx.seed(store))
        yield c, store
    actions.wait_notifications()


def _audit(store: Store) -> list[tuple[str, str]]:
    with sqlite3.connect(store.path) as conn:
        return conn.execute(
            "SELECT action, target FROM audit_log WHERE action LIKE 'action.%' ORDER BY id"
        ).fetchall()


def _rpc(c, token, name, args):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    res = c.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"}).json()["result"]
    if res["isError"]:
        return True, json.loads(res["content"][0]["text"])
    return False, res["structuredContent"]


# --- off mode -------------------------------------------------------------------------------


def test_action_routes_off_mode(client):
    c, store = client
    listed = c.get(f"{API}/actions").json()["actions"]
    assert [a["name"] for a in listed] == [ACTION]
    assert c.get(f"{API}/actions?type=계약").json()["actions"] == []
    p = c.post(f"{API}/actions/{ACTION}/preview", json={"key": "INV-2", "params": {"단계": 2}})
    assert p.status_code == 200 and p.json()["preview"]["can_submit"] is True
    bad = c.post(f"{API}/actions/{ACTION}/preview", json={"key": "INV-2", "extra": 1})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_request"
    resp = c.post(
        f"{API}/actions/{ACTION}/submit",
        json={"key": "INV-2", "params": {"단계": 2}, "idempotency_key": "ui-1"},
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()["run"]
    assert run["status"] == "applied" and run["via"] == "ui" and run["requested_by"] == "local"
    again = c.post(
        f"{API}/actions/{ACTION}/submit",
        json={"key": "INV-2", "params": {"단계": 2}, "idempotency_key": "ui-1"},
    )
    assert again.status_code == 200 and again.json() == {
        "run": {**run, "result": again.json()["run"]["result"]},
        "created": False,
    }
    refused = c.post(f"{API}/actions/{ACTION}/submit", json={"key": "INV-1"})
    assert refused.status_code == 409 and refused.json()["error"] == "precondition_failed"
    assert refused.json()["failures"][0]["message"] == "이미 완납된 청구서입니다."
    # the object shows the edit and its actions
    obj = c.get(f"{API}/objects/{INV}/INV-2").json()
    assert obj["values"]["독촉단계"] == 2 and obj["edits"]["독촉단계"]["action_run_id"] == run["id"]
    assert obj["actions"] == [{"name": ACTION, "label": "독촉 단계 기록", "available": True}]
    paid = c.get(f"{API}/objects/{INV}/INV-1").json()["actions"][0]
    assert paid["available"] is False and paid["reason"] == "이미 완납된 청구서입니다."
    # approval: stage 4 needs an owner; off mode lets the local user approve
    pending = c.post(
        f"{API}/actions/{ACTION}/submit", json={"key": "INV-3", "params": {"단계": 4}}
    ).json()["run"]
    assert pending["status"] == "pending_approval" and pending["can_approve"] is True
    counts = c.get(f"{API}/review/pending-count").json()
    assert counts["approvals"] == 1 and "pending" in counts and "unapplied" in counts
    assert [
        r["id"] for r in c.get(f"{API}/actions/runs?status=pending_approval").json()["runs"]
    ] == [pending["id"]]
    assert len(c.get(f"{API}/actions/runs?key=INV-2").json()["runs"]) == 1
    assert c.get(f"{API}/actions/runs?status=bogus").json()["error"] == "invalid_status"
    done = c.post(f"{API}/actions/runs/{pending['id']}/approve").json()["run"]
    assert done["status"] == "applied" and done["approver"] == "local"
    assert c.get(f"{API}/review/pending-count").json()["approvals"] == 0
    detail = c.get(f"{API}/actions/runs/{pending['id']}").json()
    assert detail["run"]["can_revert"] and [e["property"] for e in detail["edits"]] == [
        "독촉단계",
        "독촉메모",
    ]
    reverted = c.post(f"{API}/actions/runs/{pending['id']}/revert").json()["run"]
    assert reverted["status"] == "reverted"
    assert c.get(f"{API}/objects/{INV}/INV-3").json()["values"].get("독촉단계") is None
    # reject
    p2 = c.post(f"{API}/actions/{ACTION}/submit", json={"key": "INV-3", "params": {"단계": 4}})
    rid = p2.json()["run"]["id"]
    rejected = c.post(f"{API}/actions/runs/{rid}/reject", json={"note": "아직"}).json()["run"]
    assert rejected["status"] == "rejected" and rejected["result"] == {"note": "아직"}
    again = c.post(f"{API}/actions/runs/{rid}/approve")
    assert again.status_code == 409 and again.json()["error"] == "not_pending"
    assert c.get(f"{API}/actions/runs/act_none").json()["error"] == "unknown_run"
    assert c.post(f"{API}/actions/없음/preview", json={"key": "x"}).json()["error"] == (
        "unknown_action"
    )
    # project-scoped: another project sees nothing
    store.create_project("beta", name="Beta")
    assert c.get(f"{API}/projects/beta/actions/runs").json()["runs"] == []
    assert c.get(f"{API}/projects/beta/actions/runs/{rid}").status_code == 404
    actions_audited = [a for a, _ in _audit(store)]
    assert actions_audited.count("action.submit") == 3 and "action.revert" in actions_audited


def test_cli_action_commands(client):
    from typer.testing import CliRunner

    from ontology.cli import app

    runner = CliRunner()

    def ok(args, code=0, **kw):
        out = runner.invoke(app, args, **kw)
        assert out.exit_code == code, out.output
        return out.output

    assert ACTION in ok(["action", "list"])
    assert json.loads(ok(["action", "list", "--type", "계약", "--json"]))["actions"] == []
    shown = ok(["action", "preview", ACTION, "INV-2", "--param", "단계=3"])
    assert "독촉단계: - -> 3 (operational)" in shown and "[ok]" in shown
    assert "approval: owner" in ok(["action", "preview", ACTION, "INV-2", "--param", "단계=4"])
    # asks first; "n" stops it
    out = runner.invoke(app, ["action", "run", ACTION, "INV-2"], input="n\n")
    assert out.exit_code == 1 and "Run 독촉단계기록 on INV-2?" in out.output
    assert ok(["action", "runs"]).strip().endswith("no action runs")
    assert "applied" in ok(["action", "run", ACTION, "INV-2", "--param", "단계=2"], input="y\n")
    data = json.loads(
        ok(["action", "run", ACTION, "INV-3", "--param", "단계=4", "--yes", "--json"])
    )
    run_id = data["run"]["id"]
    assert data["run"]["status"] == "pending_approval" and data["run"]["via"] == "cli"
    assert runner.invoke(app, ["action", "run", ACTION, "INV-3", "--json"]).exit_code == 2
    out = runner.invoke(app, ["action", "run", ACTION, "INV-1"], input="y\n")
    assert out.exit_code == 1 and "FAILED" in out.output
    table = ok(["action", "runs", "--status", "pending_approval"])
    assert run_id in table and "pending_approval (owner)" in table
    assert "applied" in ok(["action", "approve", run_id])
    assert "reverted" in ok(["action", "revert", run_id])
    other = json.loads(ok(["action", "run", ACTION, "INV-3", "--param", "단계=4", "-y", "--json"]))
    assert "rejected" in ok(["action", "reject", other["run"]["id"], "--note", "no"])
    out = runner.invoke(app, ["action", "approve", other["run"]["id"]])
    assert out.exit_code == 1 and "not_pending" in out.output
    assert runner.invoke(app, ["action", "approve", "../x"]).exit_code == 2


def test_token_scopes_and_agent_action_tools(client):
    c, store = client
    made = c.post(f"{API}/agent/tokens", json={"name": "reader"}).json()
    assert made["record"]["scopes"] == ["read"]
    reader = made["token"]
    proposer = c.post(
        f"{API}/agent/tokens",
        json={"name": "p", "scopes": ["propose"], "actions": [ACTION]},
    ).json()
    assert proposer["record"]["scopes"] == ["read", "propose", f"action:{ACTION}"]
    bad = c.post(f"{API}/agent/tokens", json={"name": "x", "scopes": ["admin"]})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_scope"
    executor = c.post(f"{API}/agent/tokens", json={"name": "e", "scopes": ["execute"]}).json()
    tools = c.get(f"{API}/agent/tools").json()
    assert tools["write_tools"] == ["submit_action"]
    names = [t["name"] for t in tools["tools"]]
    assert names[-4:] == ["list_actions", "preview_action", "submit_action", "get_action_run"]
    # read token: lists and previews, may not submit
    err, listed = _rpc(c, reader, "list_actions", {"type": "청구"})
    assert not err and listed["actions"][0]["agent"] == "propose"
    err, prev = _rpc(c, reader, "preview_action", {"name": ACTION, "key": "INV-2"})
    assert not err and prev["preview"]["permission"] == {
        "min_role": "editor",
        "allowed": False,
        "refused": "scope_required",
    }
    err, refused = _rpc(c, reader, "submit_action", {"name": ACTION, "key": "INV-2"})
    assert err and refused["error"] == "scope_required"
    # propose: a pending run, through MCP and REST
    err, sub = _rpc(
        c,
        proposer["token"],
        "submit_action",
        {"name": ACTION, "key": "INV-2", "params": {"단계": 1}, "idempotency_key": "a1"},
    )
    assert not err and sub["created"] and sub["run"]["status"] == "pending_approval"
    assert sub["run"]["approval_role"] == "owner" and sub["run"]["via"] == "agent"
    assert sub["run"]["token_id"] == proposer["record"]["id"]
    rest = c.post(
        f"/agent/v1/actions/{ACTION}/submit",
        json={"key": "INV-2", "params": {"단계": 1}, "idempotency_key": "a1"},
        headers={"Authorization": f"Bearer {proposer['token']}"},
    )
    assert rest.status_code == 200 and rest.json()["created"] is False
    got = c.get(
        f"/agent/v1/actions/runs/{sub['run']['id']}",
        headers={"Authorization": f"Bearer {proposer['token']}"},
    ).json()
    assert got["run"]["status"] == "pending_approval" and got["run"]["can_approve"] is False
    # the execute token is capped by the spec's agent policy (propose)
    err, sub2 = _rpc(c, executor["token"], "submit_action", {"name": ACTION, "key": "INV-3"})
    assert not err and sub2["run"]["status"] == "pending_approval"
    # a person approves the agent's proposal
    done = c.post(f"{API}/actions/runs/{sub['run']['id']}/approve").json()["run"]
    assert done["status"] == "applied"
    # audited as the agent
    entries = Accounts(store).list_audit(action="action.submit")
    assert {e["actor"] for e in entries} == {
        f"agent:{proposer['record']['id']}",
        f"agent:{executor['record']['id']}",
    }
    assert all(e["detail"]["via"] == "agent" and e["detail"]["token_id"] for e in entries)
    # instructions and the OpenAPI description name the write tool
    init = c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": f"Bearer {proposer['token']}"},
    ).json()["result"]["instructions"]
    assert "submit_action, which writes" in init and "propose scope" in init
    assert ACTION in init
    spec = c.get("/agent/v1/openapi.json", headers={"Authorization": f"Bearer {reader}"}).json()
    assert "/agent/v1/actions/{name}/submit" in spec["paths"]
    assert (
        "read-only"
        in c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Authorization": f"Bearer {reader}"},
        ).json()["result"]["instructions"]
    )
    # spec agent: none refuses every token
    from ontology.logic import registry

    view = store.project()
    registry.save(view, {**sample_specs("actions.yaml")[0], "agent": "none"})
    registry.set_status(view, "action", ACTION, "active")
    err, nope = _rpc(c, executor["token"], "submit_action", {"name": ACTION, "key": "INV-3"})
    assert err and nope["error"] == "agent_not_allowed"


# --- accounts mode --------------------------------------------------------------------------


class AccountsEnv:
    def __init__(self, monkeypatch):
        from ontology.engine import jobs

        monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
        monkeypatch.setattr(acc, "_DUMMY_HASH", None)
        monkeypatch.setattr(jobs, "_APPLIED_ENV", jobs._APPLIED_ENV)
        monkeypatch.setenv("ONTOLOGY_AUTH", "accounts")
        self.settings = Settings.from_env()
        self.app = web.create_app(self.settings, runs=RunManager(self.settings, jobs=dict))
        self.store = Store.for_home(self.settings.home)
        self.accounts = Accounts(self.store)
        self.view = fx.seed(self.store)
        activate_samples(self.view)

    def client(self, email: str, role: str | None = None, *, admin: bool = False) -> TestClient:
        c = TestClient(self.app)
        user = self.accounts.by_email(email) or self.accounts.create_user(email, is_admin=admin)
        if role:
            self.accounts.add_member(self.view.id, user.id, role)
        raw, _ = self.accounts.create_session(user.id)
        c.cookies.set("onto_session", raw)
        return c


@pytest.fixture
def aenv(isolated_env, backend, monkeypatch):
    env = AccountsEnv(monkeypatch)
    yield env
    actions.wait_notifications()


def test_accounts_permissions_and_four_eyes(aenv):
    viewer = aenv.client("v@x.io", "viewer")
    reviewer = aenv.client("r@x.io", "reviewer")
    editor = aenv.client("e@x.io", "editor")
    owner = aenv.client("o@x.io", "owner")
    owner2 = aenv.client("o2@x.io", "owner")
    url = f"{API}/actions/{ACTION}/submit"
    # viewers read and preview, but may not run
    assert viewer.get(f"{API}/actions").status_code == 200
    seen = viewer.post(f"{API}/actions/{ACTION}/preview", json={"key": "INV-2"}).json()
    assert seen["preview"]["permission"]["allowed"] is False
    resp = viewer.post(url, json={"key": "INV-2"})
    assert resp.status_code == 403 and resp.json() == {
        "error": "forbidden",
        "detail": "this action needs the editor role",
        "permission": "action.run",
        "min_role": "editor",
    }
    assert viewer.get(f"{API}/objects/{INV}/INV-2").json()["actions"][0]["reason"] == "permission"
    # an editor's stage-4 request waits for an owner
    run = editor.post(url, json={"key": "INV-2", "params": {"단계": 4}}).json()["run"]
    assert run["status"] == "pending_approval" and run["requested_by"] == "e@x.io"
    assert run["can_approve"] is False
    assert editor.get(f"{API}/review/pending-count").json()["approvals"] == 0
    assert reviewer.get(f"{API}/review/pending-count").json()["approvals"] == 0
    assert owner.get(f"{API}/review/pending-count").json()["approvals"] == 1
    no = reviewer.post(f"{API}/actions/runs/{run['id']}/approve")
    assert no.status_code == 403 and no.json()["permission"] == "action.approve"
    # an owner's own request needs another owner
    own = owner.post(url, json={"key": "INV-3", "params": {"단계": 4}}).json()["run"]
    self_ok = owner.post(f"{API}/actions/runs/{own['id']}/approve")
    assert self_ok.status_code == 403 and self_ok.json()["error"] == "self_approval"
    assert owner.get(f"{API}/review/pending-count").json()["approvals"] == 1
    assert owner2.post(f"{API}/actions/runs/{own['id']}/approve").json()["run"]["status"] == (
        "applied"
    )
    done = owner.post(f"{API}/actions/runs/{run['id']}/approve").json()["run"]
    assert done["status"] == "applied" and done["approver"] == "o@x.io"
    # revert: editor+; a viewer may not
    assert viewer.post(f"{API}/actions/runs/{run['id']}/revert").status_code == 403
    assert editor.post(f"{API}/actions/runs/{run['id']}/revert").json()["run"]["status"] == (
        "reverted"
    )
    # the audit log names the users
    entries = aenv.accounts.list_audit(action="action.approve")
    assert {e["actor"] for e in entries} == {"o@x.io", "o2@x.io"}


def test_accounts_execute_tokens_and_creator_role(aenv):
    from ontology.logic import registry

    editor = aenv.client("e@x.io", "editor")
    owner = aenv.client("o@x.io", "owner")
    resp = editor.post(f"{API}/agent/tokens", json={"name": "x", "scopes": ["execute"]})
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden"
    proposer = editor.post(f"{API}/agent/tokens", json={"name": "p", "scopes": ["propose"]}).json()
    executor = owner.post(f"{API}/agent/tokens", json={"name": "x", "scopes": ["execute"]}).json()
    # an editor cannot rotate an owner's execute token
    rot = editor.post(f"{API}/agent/tokens/{executor['record']['id']}/rotate")
    assert rot.status_code == 403
    registry.save(
        aenv.view,
        {**sample_specs("actions.yaml")[0], "agent": "execute", "approval": {"role": "none"}},
    )
    registry.set_status(aenv.view, "action", ACTION, "active")
    anon = TestClient(aenv.app)
    err, applied = _rpc(anon, executor["token"], "submit_action", {"name": ACTION, "key": "INV-2"})
    assert not err and applied["run"]["status"] == "applied"
    err, proposed = _rpc(anon, proposer["token"], "submit_action", {"name": ACTION, "key": "INV-2"})
    assert not err and proposed["run"]["status"] == "pending_approval"
    assert proposed["run"]["approval_role"] == "reviewer"
    # the token's creator is the person behind the proposal: four-eyes refuses them
    assert proposed["run"]["requested_for"] == "e@x.io"
    listed = editor.get(f"{API}/actions/runs/{proposed['run']['id']}").json()["run"]
    assert listed["can_approve"] is False
    mine = editor.post(f"{API}/actions/runs/{proposed['run']['id']}/approve")
    assert mine.status_code == 403 and mine.json()["error"] == "self_approval"
    # another reviewer (here the owner) approves it
    ok = owner.post(f"{API}/actions/runs/{proposed['run']['id']}/approve")
    assert ok.json()["run"]["status"] == "applied"
    # the creator demoted below the action's role: the token is refused
    user = aenv.accounts.by_email("e@x.io")
    aenv.accounts.set_role(aenv.view.id, user.id, "viewer")
    err, refused = _rpc(anon, proposer["token"], "submit_action", {"name": ACTION, "key": "INV-2"})
    assert err and refused["error"] == "forbidden"
    err, still = _rpc(anon, proposer["token"], "list_actions", {})
    assert not err and still["actions"]
