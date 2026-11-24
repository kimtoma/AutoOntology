# SPDX-License-Identifier: MIT
"""The workflow routes (``/api/workflows``) and the ``ao workflow`` commands: list with
the next slot, manual runs (background and ``wait``), runs and their action runs, cancel,
approvals resuming through the API, and accounts mode roles (sources faked; the runner in
``test_workflows.py``)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.logic import actions, functions, registry, workflows
from autoontology.store import accounts as acc
from autoontology.store.accounts import Accounts
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web.runs import RunManager
from tests import logic_fixtures as fx
from tests.test_actions import activate_samples
from tests.test_logic import TODAY
from tests.test_workflows import WORKFLOW, _add_invoices, sample_workflow

API = "/api"


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    fake = fx.install_backend(monkeypatch)
    _add_invoices(fake)
    return fake


def _seed(store: Store, actor: str = "local") -> None:
    view = fx.seed(store)
    activate_samples(view)
    registry.save(view, {**sample_workflow(), "trigger": {"schedule": "daily 02:00"}})
    registry.set_status(view, "workflow", WORKFLOW, "active", actor=actor)


@pytest.fixture
def client(isolated_env, backend, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_TZ", "Asia/Seoul")
    settings = Settings.from_env()
    with TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict))) as c:
        store = Store.for_home(settings.home)
        _seed(store)
        yield c, store
    workflows.wait()
    actions.wait_notifications()


def test_workflow_routes_off_mode(client):
    c, store = client
    body = c.get(f"{API}/workflows").json()
    assert body["triggers"] == "on"
    (w,) = body["workflows"]
    assert w["name"] == WORKFLOW and w["trigger"] == {"schedule": "daily 02:00"}
    assert w["next_slot"].endswith("T02:00") and w["last_run"] is None
    assert w["object_type"] == fx.INV and w["activated_by"] == "local"
    # a background run: 202 at once, the objects on a worker
    started = c.post(f"{API}/workflows/{WORKFLOW}/run")
    assert started.status_code == 202, started.text
    run_id = started.json()["run"]["id"]
    assert started.json()["run"]["status"] == "running"
    workflows.wait()
    detail = c.get(f"{API}/workflows/runs/{run_id}").json()["run"]
    assert detail["status"] == "paused" and detail["summary"]["matched"] == 4
    assert len(detail["action_runs"]) == 4 and len(detail["objects"]) == 4
    assert detail["trigger"] == "manual" and detail["started_by"] == "local"
    pending = [a for a in detail["action_runs"] if a["status"] == "pending_approval"]
    # approving through the API resumes the object
    resp = c.post(f"{API}/actions/runs/{pending[0]['id']}/approve")
    assert resp.status_code == 200 and resp.json()["run"]["status"] == "applied"
    workflows.wait()
    assert c.get(f"{API}/workflows/runs/{run_id}").json()["run"]["status"] == "succeeded"
    # wait: true answers with the run as it ended (every stage is recorded already: the
    # guards of the sample submit nothing new)
    waited = c.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True})
    assert waited.status_code == 200 and waited.json()["run"]["status"] == "succeeded"
    assert waited.json()["run"]["summary"]["actions_applied"] == 0
    assert len(waited.json()["run"]["action_runs"]) == 0
    listed = c.get(f"{API}/workflows/runs?workflow={WORKFLOW}").json()["runs"]
    assert [r["id"] for r in listed] == [waited.json()["run"]["id"], run_id]
    assert "objects" not in listed[0]
    assert c.get(f"{API}/workflows").json()["workflows"][0]["last_run"]["id"] == listed[0]["id"]
    # errors
    for path, code, status in (
        (f"{API}/workflows/runs/wfr_none", "unknown_run", 404),
        (f"{API}/workflows/runs?status=done", "invalid_status", 400),
    ):
        resp = c.get(path)
        assert (resp.status_code, resp.json()["error"]) == (status, code)
    resp = c.post(f"{API}/workflows/없음/run")
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_workflow"
    resp = c.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True, "x": 1})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"
    resp = c.post(f"{API}/workflows/runs/{run_id}/cancel")
    assert resp.status_code == 409 and resp.json()["error"] == "not_cancellable"


def test_cancel_route_and_audit(client):
    c, store = client
    run = c.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True}).json()["run"]
    assert run["status"] == "paused"  # INV-6 (stage 4) waits for an owner
    resp = c.post(f"{API}/workflows/runs/{run['id']}/cancel")
    assert resp.status_code == 200 and resp.json()["run"]["status"] == "cancelled"
    with sqlite3.connect(store.path) as conn:
        audited = [
            r[0]
            for r in conn.execute(
                "SELECT action FROM audit_log WHERE action LIKE 'workflow.%' ORDER BY id"
            )
        ]
    assert audited == ["workflow.run", "workflow.cancel"]


def test_cli_workflow_commands(client, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli_ops
    from autoontology.cli import app

    def down(*a, **kw):
        raise cli_ops.ServiceDown("http://127.0.0.1:0")

    monkeypatch.setattr(cli_ops, "_service", lambda *a, **kw: _Down(down))
    runner = CliRunner()

    def ok(args, code=0, **kw):
        out = runner.invoke(app, args, **kw)
        assert out.exit_code == code, out.output
        return out.output

    listed = ok(["workflow", "list"])
    assert WORKFLOW in listed and "daily 02:00" in listed
    assert ok(["workflow", "runs"]).strip().endswith("no workflow runs")
    out = ok(["workflow", "run", WORKFLOW, "--wait"])
    assert "paused" in out and "matched=4" in out and "pending_approval=1" in out
    data = json.loads(ok(["workflow", "run", WORKFLOW, "--json"]))  # service down: here
    run_id = data["run"]["id"]
    assert data["run"]["status"] == "paused"
    shown = ok(["workflow", "show", run_id])
    assert "INV-6: paused" in shown and "pending_approval (owner)" in shown
    assert run_id in ok(["workflow", "runs", "--status", "paused"])
    pending = json.loads(ok(["workflow", "show", run_id, "--json"]))["run"]["action_runs"]
    act = next(a["id"] for a in pending if a["status"] == "pending_approval")
    # approving from the CLI resumes the object in this process
    assert "applied" in ok(["action", "approve", act])
    assert "succeeded" in ok(["workflow", "show", run_id])
    first = json.loads(ok(["workflow", "runs", "--json"]))["runs"][-1]["id"]
    assert "cancelled" in ok(["workflow", "cancel", first])
    out = runner.invoke(app, ["workflow", "cancel", first])
    assert out.exit_code == 1 and "not_cancellable" in out.output
    assert runner.invoke(app, ["workflow", "show", "../x"]).exit_code == 2
    assert runner.invoke(app, ["workflow", "run", "없음", "--wait"]).exit_code == 1


class _Down:
    """A :class:`ServiceClient` stand-in for a service that is not running."""

    def __init__(self, fail):
        self.call = fail
        self.raw = fail


# --- accounts mode -----------------------------------------------------------------------


@pytest.fixture
def accounts_env(isolated_env, backend, monkeypatch):
    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    settings = Settings.from_env()
    app = web.create_app(settings, runs=RunManager(settings, jobs=dict))
    store = Store.for_home(settings.home)
    accounts = Accounts(store)
    view = store.project()

    def client(email: str, role: str) -> TestClient:
        user = accounts.by_email(email) or accounts.create_user(email)
        accounts.add_member(view.id, user.id, role)
        raw, _ = accounts.create_session(user.id)
        c = TestClient(app)
        c.cookies.set("ao_session", raw)
        return c

    owner = client("boss@example.com", "owner")
    _seed(store, actor="boss@example.com")
    yield client, owner, store, accounts
    workflows.wait()
    actions.wait_notifications()


def test_accounts_roles_on_workflow_routes(accounts_env):
    client, owner, store, accounts = accounts_env
    viewer = client("v@example.com", "viewer")
    editor = client("e@example.com", "editor")
    assert viewer.get(f"{API}/workflows").status_code == 200
    resp = viewer.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True})
    assert resp.status_code == 403 and resp.json()["permission"] == "workflow.run"
    # an editor runs it; the run acts as the owner who activated it
    run = editor.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True}).json()["run"]
    assert run["status"] == "paused" and run["started_by"] == "e@example.com"
    pending = next(a for a in run["action_runs"] if a["status"] == "pending_approval")
    assert pending["requested_by"] == f"workflow:{WORKFLOW}"
    # the owner may approve the workflow's request (it is not their own)
    assert owner.post(f"{API}/actions/runs/{pending['id']}/approve").status_code == 200
    workflows.wait()
    assert viewer.get(f"{API}/workflows/runs/{run['id']}").json()["run"]["status"] == "succeeded"
    # the activator is demoted: the next run fails with the reason
    boss = accounts.by_email("boss@example.com")
    client("o2@example.com", "owner")  # the project keeps an owner
    accounts.set_role(store.project().id, boss.id, "editor")
    failed = editor.post(f"{API}/workflows/{WORKFLOW}/run", json={"wait": True}).json()["run"]
    assert failed["status"] == "failed" and "no longer an owner" in failed["error"]
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT action, user_id FROM audit_log WHERE action = 'workflow.run'"
        ).fetchall()
    assert rows and all(r[1] == accounts.by_email("e@example.com").id for r in rows)
