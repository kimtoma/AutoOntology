# SPDX-License-Identifier: MIT
"""The logic routes (``/api/logic``), computed values on ``/api/objects``, the
``ao logic`` commands and the agent tools ``list_functions`` / ``call_function``
(sources faked; roles in ``test_permissions.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.logic import functions
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web.runs import RunManager
from tests import logic_fixtures as fx
from tests.logic_fixtures import CON, INV
from tests.test_logic import FUNCS, MIUSU, TODAY

API = "/api"
SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "quickstart" / "logic"


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    return fx.install_backend(monkeypatch)


@pytest.fixture
def client(isolated_env, backend):
    settings = Settings.from_env()
    with TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict))) as c:
        store = Store.for_home(settings.home)
        fx.seed(store)
        yield c, store


def _audit(store: Store) -> list[tuple[str, str]]:
    import sqlite3

    with sqlite3.connect(store.path) as conn:
        return conn.execute("SELECT action, target FROM audit_log ORDER BY id").fetchall()


def test_logic_routes_save_validate_status_history(client):
    c, store = client
    assert c.get(f"{API}/logic").json() == {"definitions": []}
    bad = c.post(
        f"{API}/logic/validate", json={"spec": {**MIUSU, "expr": "청구금액 + 없음"}}
    ).json()
    assert bad["valid"] is False and bad["errors"][0]["pos"] == 7 and bad["name"] == "미수금"
    text = "kind: function\nname: 미수금\nobject_type: 청구\nreturns: number\nexpr: 청구금액\n"
    assert c.post(f"{API}/logic/validate", json={"spec": text}).json()["valid"] is True
    schema_bad = c.post(f"{API}/logic/validate", json={"spec": "kind: macro"}).json()
    assert schema_bad["valid"] is False and schema_bad["kind"] is None
    resp = c.post(f"{API}/logic", json={"spec": {**MIUSU, "expr": "청구금액 +"}})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_spec"
    assert resp.json()["errors"][0]["code"] == "syntax_error"
    saved = c.post(f"{API}/logic", json={"spec": MIUSU}).json()["definition"]
    assert (saved["status"], saved["version"], saved["object_type"]) == ("draft", 1, "청구")
    assert c.post(f"{API}/logic", json={"spec": text}).json()["definition"]["version"] == 2
    active = c.post(f"{API}/logic/function/미수금/status", json={"status": "active"}).json()
    assert active["definition"]["status"] == "active"
    one = c.get(f"{API}/logic/function/미수금").json()
    assert one["definition"]["spec"]["expr"] == "청구금액"
    assert [h["status"] for h in one["history"]] == ["active", "draft", "draft"]
    assert (
        c.get(f"{API}/logic?kind=function&status=active").json()["definitions"][0]["name"]
        == "미수금"
    )
    assert c.get(f"{API}/logic?kind=action").json()["definitions"] == []
    for path, code in (
        (f"{API}/logic?kind=macro", "invalid_kind"),
        (f"{API}/logic?status=gone", "invalid_status"),
        (f"{API}/logic/macro/x", "invalid_kind"),
    ):
        assert c.get(path).json()["error"] == code
    assert c.get(f"{API}/logic/function/없음").status_code == 404
    assert (
        c.post(f"{API}/logic/function/미수금/status", json={"status": "broken"}).status_code == 400
    )
    assert c.post(f"{API}/logic", json={"spec": "x" * 70_000}).status_code == 413
    assert c.post(f"{API}/logic", json={"bogus": 1}).status_code == 400
    # an action referencing a draft function cannot be activated
    action = {
        "kind": "action",
        "name": "완납처리",
        "label": "완납 처리",
        "object_type": "청구",
        "preconditions": [{"expr": "미수금() > 0", "message": "남은 금액이 없습니다"}],
        "effects": [{"set": "수납상태", "value": "'완납'"}],
    }
    c.post(f"{API}/logic", json={"spec": text.replace("청구금액\n", "청구금액 * 1\n")})
    c.post(f"{API}/logic", json={"spec": action})
    refused = c.post(f"{API}/logic/action/완납처리/status", json={"status": "active"})
    assert refused.status_code == 409 and refused.json()["error"] == "reference_not_active"
    c.post(f"{API}/logic/function/미수금/status", json={"status": "active"})
    ok = c.post(f"{API}/logic/action/완납처리/status", json={"status": "active"})
    assert ok.json()["definition"]["status"] == "active"
    actions = [a for a in _audit(store) if a[0].startswith("logic.")]
    assert ("logic.save", "function:미수금") in actions
    assert ("logic.status", "action:완납처리") in actions


def test_object_detail_and_list_carry_computed_values(client):
    c, store = client
    for f in FUNCS:
        assert c.post(f"{API}/logic", json={"spec": f}).status_code == 200
        c.post(f"{API}/logic/function/{f['name']}/status", json={"status": "active"})
    one = c.get(f"{API}/objects/{INV}/INV-2").json()
    assert one["computed"]["미수금"] == {"value": 6000000, "type": "number"}
    assert one["computed"]["연체일수"]["value"] == 55
    assert one["computed"]["망가짐"]["error"]["code"] == "division_by_zero"
    con = c.get(f"{API}/objects/{CON}/K2024-003").json()
    assert (
        con["computed"]["미수청구건수"]["value"] == 2
        and con["computed"]["담당자"]["value"] == "최하은"
    )
    plain = c.get(f"{API}/objects/{INV}").json()
    assert "computed" not in plain["items"][0]
    listed = c.get(f"{API}/objects/{INV}?computed=1").json()
    assert {i["key"]: i["computed"]["연체여부"]["value"] for i in listed["items"]} == {
        "INV-1": False,
        "INV-2": True,
        "INV-3": False,
    }
    # retired: no longer computed
    c.post(f"{API}/logic/function/망가짐/status", json={"status": "retired"})
    assert "망가짐" not in c.get(f"{API}/objects/{INV}/INV-1").json()["computed"]


def test_function_test_and_call_routes(client):
    c, store = client
    c.post(f"{API}/logic", json={"spec": MIUSU})
    tested = c.post(f"{API}/logic/functions/미수금/test", json={"keys": ["INV-2", "INV-3"]}).json()
    assert [r["value"] for r in tested["results"]] == [6000000, 2000000]
    unsaved = c.post(
        f"{API}/logic/test",
        json={"spec": {**MIUSU, "name": "x", "expr": "청구금액 * 2"}, "keys": ["INV-1"]},
    ).json()
    assert unsaved["results"][0]["value"] == 9000000
    bad = c.post(
        f"{API}/logic/test", json={"spec": {**MIUSU, "expr": "1 +"}, "keys": ["INV-1"]}
    ).json()
    assert bad["errors"] and bad["results"] == []
    assert c.post(f"{API}/logic/test", json={"keys": ["INV-1"]}).status_code == 400
    assert c.post(f"{API}/logic/functions/미수금/test", json={"keys": []}).status_code == 400
    assert c.post(f"{API}/logic/functions/없음/test", json={"keys": ["a"]}).status_code == 404
    assert c.post(f"{API}/logic/functions/미수금/test", json={"keys": ["NOPE"]}).status_code == 404
    draft = c.post(f"{API}/logic/functions/미수금/call", json={"key": "INV-2"})
    assert draft.status_code == 409 and draft.json()["error"] == "function_not_active"
    c.post(f"{API}/logic/function/미수금/status", json={"status": "active"})
    called = c.post(f"{API}/logic/functions/미수금/call", json={"key": "INV-2"}).json()
    assert called["value"] == 6000000 and called["object_type"] == INV


def test_off_mode_unchanged_without_logic(client):
    c, _ = client
    # no definitions: objects read as before, with an empty computed map
    one = c.get(f"{API}/objects/{INV}/INV-1").json()
    assert one["computed"] == {} and one["values"]["청구금액"] == 4500000


# --- agent tools ----------------------------------------------------------------------------


def _rpc(c, token, name, args):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    res = c.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"}).json()["result"]
    return res["isError"], res["structuredContent"] if not res["isError"] else json.loads(
        res["content"][0]["text"]
    )


def test_agent_function_tools(client):
    c, store = client
    token = c.post(f"{API}/agent/tokens", json={"name": "t"}).json()["token"]
    for f in FUNCS[:3]:
        c.post(f"{API}/logic", json={"spec": f})
        c.post(f"{API}/logic/function/{f['name']}/status", json={"status": "active"})
    err, listed = _rpc(c, token, "list_functions", {})
    assert not err and [f["name"] for f in listed["functions"]] == [
        "미수금",
        "연체여부",
        "연체일수",
    ]
    err, only = _rpc(c, token, "list_functions", {"type": "계약"})
    assert not err and only["functions"] == []
    err, value = _rpc(
        c, token, "call_function", {"name": "연체일수", "key": "INV-2", "type": "청구"}
    )
    assert not err and value["value"] == 55 and value["type"] == "number"
    err, missing = _rpc(c, token, "call_function", {"name": "없음", "key": "INV-2"})
    assert err and missing["error"] == "unknown_function"
    err, obj = _rpc(c, token, "get_object", {"type": INV, "key": "INV-2"})
    assert not err and obj["computed"]["미수금"]["value"] == 6000000
    rest = c.post(
        "/agent/v1/functions/미수금/call",
        json={"key": "INV-3"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert rest["value"] == 2000000
    listed = c.get(
        "/agent/v1/functions?type=청구", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(listed["functions"]) == 3
    # read-only: nothing changed
    assert [d.status for d in store.project().logic_defs().list()] == ["active"] * 3
    assert store.project().object_edits().current(INV) == {}


# --- CLI ------------------------------------------------------------------------------------


def test_cli_logic_commands(client, tmp_path):
    from typer.testing import CliRunner

    from autoontology.cli import app

    runner = CliRunner()

    def ok(args, code=0):
        out = runner.invoke(app, args)
        assert out.exit_code == code, out.output
        return out.output

    assert "no definitions yet" in ok(["logic", "list"])
    # the sample specs, in dependency order
    out = ok(["logic", "validate", "-f", str(SAMPLES / "functions.yaml")])
    assert out.count(": valid") == 5
    applied = ok(["logic", "apply", "-f", str(SAMPLES / "functions.yaml")])
    assert "function 연체일수: version 1, draft" in applied
    for name in ("미수금", "연체일수", "연체여부", "남은일수", "미수청구건수"):
        assert "active" in ok(["logic", "activate", "function", name])
    ok(["logic", "apply", "-f", str(SAMPLES / "actions.yaml")])
    ok(["logic", "apply", "-f", str(SAMPLES / "workflows.yaml")])
    listed = json.loads(ok(["logic", "list", "--json"]))
    assert {(d["kind"], d["name"], d["status"]) for d in listed["definitions"]} >= {
        ("action", "독촉단계기록", "draft"),
        ("workflow", "연체독촉", "draft"),
    }
    table = ok(["logic", "list", "--kind", "function", "--status", "active"])
    assert "연체여부" in table and "독촉" not in table
    shown = ok(["logic", "show", "function", "연체일수"])
    assert "청구일 + 30" in shown and "v1 active" in shown
    tested = ok(["logic", "test", "연체일수", "--key", "INV-2", "--key", "INV-1"])
    assert "55" in tested and "INV-1" in tested
    data = json.loads(ok(["logic", "test", "미수금", "--key", "INV-2", "--json"]))
    assert data["results"][0]["value"] == 6000000
    spec = tmp_path / "f.yaml"
    spec.write_text(
        "kind: function\nname: 두배\nobject_type: 청구\nreturns: number\nexpr: 청구금액 * 2\n"
    )
    assert "9000000" in ok(["logic", "test", "-f", str(spec), "--key", "INV-1"])
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "kind: function\nname: 틀림\nobject_type: 청구\nreturns: number\nexpr: 청구금액 + 없음\n"
    )
    out = runner.invoke(app, ["logic", "validate", "-f", str(bad)])
    assert out.exit_code == 1 and "1 error" in out.output and "at 7" in out.output
    out = runner.invoke(app, ["logic", "apply", "-f", str(bad), "--json"])
    assert out.exit_code == 1 and '"error": "invalid_spec"' in out.output
    assert runner.invoke(app, ["logic", "test", "--key", "x"]).exit_code == 2
    assert runner.invoke(app, ["logic", "show", "macro", "x"]).exit_code == 2
    assert (
        runner.invoke(app, ["logic", "apply", "-f", str(tmp_path / "missing.yaml")]).exit_code == 2
    )
    assert "retired" in ok(["logic", "retire", "function", "남은일수"])
    # the workflow references the action: a draft action keeps it from activation
    out = runner.invoke(app, ["logic", "activate", "workflow", "연체독촉"])
    assert out.exit_code == 1 and "reference_not_active" in out.output
