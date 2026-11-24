# SPDX-License-Identifier: MIT
"""Outside-agent access (``autoontology/web/agent_api.py``): bearer tokens, the MCP
endpoint (``POST /mcp``), the REST API under ``/agent/v1`` and the token management
routes under ``/api/agent``."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.ontology import model as merged
from autoontology.store.agent_tokens import AgentTokens
from autoontology.store.db import Store
from autoontology.web import agent_api
from autoontology.web import app as web
from tests.test_web_api import _seed_snapshot, _snapshot

MCP = "/mcp"
REST = "/agent/v1"


@pytest.fixture
def agent(isolated_env):
    """A service with snapshot r1 (documents + structured) and r2 (Terms only), an export
    folder for r1, and one token (no pin, no expiry)."""
    settings = Settings.from_env()
    _seed_snapshot(settings)
    Store.for_home(settings.home).save_snapshot(
        "r2", merged.build(settings.workspace, structured=[_snapshot()])
    )
    rev_dir = Path(settings.exports_dir) / "default" / "r0001"  # exports/<project slug>/
    (rev_dir / "okf").mkdir(parents=True)
    (rev_dir / "report.md").write_text("# 보고서\n", "utf-8")
    (rev_dir / "okf" / "index.md").write_text("---\nokf_version: '0.2'\n---\n", "utf-8")
    with TestClient(web.create_app(settings)) as c:
        made = c.post("/api/agent/tokens", json={"name": "claude code"}).json()
        yield c, settings, made


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _rpc(c, token, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    resp = c.post(MCP, json=msg, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tool(c, token, name, args=None):
    body = _rpc(c, token, "tools/call", {"name": name, "arguments": args or {}})
    return body["result"]


# --- tokens ------------------------------------------------------------------------------


def test_token_is_shown_once_and_only_its_hash_is_stored(agent):
    c, settings, made = agent
    token = made["token"]
    assert token.startswith("aok_") and len(token) > 40
    assert made["record"]["name"] == "claude code" and made["record"]["status"] == "active"
    assert made["record"]["revision_pin"] is None and made["record"]["expires_at"] is None
    db = sqlite3.connect(Path(settings.home) / "autoontology.db")
    rows = db.execute("SELECT token_hash FROM agent_tokens").fetchall()
    assert rows == [(hashlib.sha256(token.encode()).hexdigest(),)]
    dump = "\n".join(db.iterdump())
    assert token not in dump and token[4:] not in dump
    listed = c.get("/api/agent/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [made["record"]["id"]]
    assert all("token" not in t and "token_hash" not in t for t in listed)
    assert token not in json.dumps(listed)


def test_create_validates_like_the_other_routes(agent):
    c, _, _ = agent
    for body in ({}, {"name": ""}, {"name": "x", "expires_days": 7}, {"name": "x", "y": 1}):
        resp = c.post("/api/agent/tokens", json=body)
        assert resp.status_code == 400 and resp.json()["error"] == "invalid_request", body
    resp = c.post("/api/agent/tokens", json={"name": "x", "revision_pin": 9})
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_revision"
    made = c.post(
        "/api/agent/tokens", json={"name": "pinned", "revision_pin": 1, "expires_days": 30}
    ).json()
    assert made["record"]["revision_pin"] == 1 and made["record"]["expires_at"]
    assert c.delete("/api/agent/tokens/nope").status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer", "Bearer aok_wrong", "Basic abc", "aok_x"])
def test_unauthorized_without_a_valid_token_even_from_localhost(agent, header):
    c, _, _ = agent
    headers = {"Authorization": header} if header else {}
    for method, path in (
        ("POST", MCP),
        ("GET", f"{REST}/revisions"),
        ("GET", f"{REST}/ontology"),
        ("GET", f"{REST}/concepts?limit=abc"),  # auth comes before validation
        ("POST", f"{REST}/sparql"),
        ("GET", f"{REST}/exports/1.zip"),
        ("GET", f"{REST}/openapi.json"),
    ):
        for host in ("127.0.0.1:7800", "localhost"):
            resp = c.request(method, path, headers={**headers, "host": host}, json={})
            assert resp.status_code == 401, (method, path, header)
            assert resp.json()["error"] == "unauthorized"
            assert resp.headers["www-authenticate"].startswith("Bearer")


def test_revoked_and_expired_tokens_are_refused(agent):
    c, settings, made = agent
    token, tid = made["token"], made["record"]["id"]
    assert c.get(f"{REST}/revisions", headers=_auth(token)).status_code == 200
    revoked = c.delete(f"/api/agent/tokens/{tid}").json()
    assert revoked["status"] == "revoked" and revoked["revoked_at"]
    assert c.get(f"{REST}/revisions", headers=_auth(token)).status_code == 401
    assert c.post(f"/api/agent/tokens/{tid}/rotate").status_code == 409
    # history kept, the hash cleared
    db = sqlite3.connect(Path(settings.home) / "autoontology.db")
    assert db.execute("SELECT token_hash FROM agent_tokens").fetchall() == [(None,)]
    assert c.get("/api/agent/tokens").json()["tokens"][0]["status"] == "revoked"

    other = c.post("/api/agent/tokens", json={"name": "short", "expires_days": 30}).json()
    assert c.get(f"{REST}/revisions", headers=_auth(other["token"])).status_code == 200
    db.execute(
        "UPDATE agent_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (other["record"]["id"],),
    )
    db.commit()
    assert c.get(f"{REST}/revisions", headers=_auth(other["token"])).status_code == 401
    listed = {t["id"]: t for t in c.get("/api/agent/tokens").json()["tokens"]}
    assert listed[other["record"]["id"]]["status"] == "expired"


def test_rotate_kills_the_old_token(agent):
    c, _, made = agent
    old, tid = made["token"], made["record"]["id"]
    rotated = c.post(f"/api/agent/tokens/{tid}/rotate").json()
    new = rotated["token"]
    assert new != old and new.startswith("aok_") and rotated["record"]["id"] == tid
    assert c.get(f"{REST}/revisions", headers=_auth(old)).status_code == 401
    assert c.get(f"{REST}/revisions", headers=_auth(new)).status_code == 200


def test_migration_v7_upgrades_a_v6_store(tmp_path):
    from autoontology.store.db import MIGRATIONS

    path = tmp_path / "autoontology.db"
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE agent_calls")
        conn.execute("DROP TABLE agent_tokens")
        conn.execute("DELETE FROM schema_version WHERE version > 6")
    store = Store(path)
    assert store.schema_version() == MIGRATIONS[-1][0] >= 7
    raw, rec = AgentTokens(store).create("x")
    assert AgentTokens(store).lookup(raw).id == rec.id


def test_store_lookup_compares_hashes(isolated_env):
    store = Store.for_home(Settings.from_env().home)
    tokens = AgentTokens(store)
    raw, rec = tokens.create("x")
    assert tokens.lookup(raw).id == rec.id
    assert tokens.lookup(raw + "x") is None and tokens.lookup("") is None


# --- MCP ---------------------------------------------------------------------------------


def test_mcp_initialize_ping_and_tools_list(agent):
    c, _, made = agent
    init = _rpc(c, made["token"], "initialize", {"protocolVersion": "2025-06-18"})
    res = init["result"]
    assert init["jsonrpc"] == "2.0" and init["id"] == 1
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"] == {"tools": {"listChanged": False}}
    assert res["serverInfo"]["name"] == "autoontology"
    assert "r2" in res["instructions"] and "read-only" in res["instructions"]
    assert _rpc(c, made["token"], "ping")["result"] == {}
    tools = _rpc(c, made["token"], "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "list_revisions",
        "get_ontology_summary",
        "search_concepts",
        "get_concept",
        "sparql_query",
        "list_exports",
    ]
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)


def test_mcp_tools_call_every_tool(agent):
    c, _, made = agent
    t = made["token"]

    def data(name, args=None):
        res = _tool(c, t, name, args)
        assert res["isError"] is False, res
        assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
        assert "revision" in res["structuredContent"]
        return res["structuredContent"]

    revs = data("list_revisions")
    assert revs["revision"] == 2 and [r["revision"] for r in revs["revisions"]] == [2, 1]
    summary = data("get_ontology_summary")
    assert summary["revision"] == 2 and summary["workspace"] == "default"
    assert data("get_ontology_summary", {"revision": 1})["revision"] == 1
    found = data("search_concepts", {"query": "계약서", "revision": 1})
    assert [i["name"] for i in found["items"]] == ["Contracts"] and found["total"] == 1
    assert len(data("search_concepts", {"query": "", "limit": 1, "revision": 1})["items"]) == 1
    rules = data("search_concepts", {"query": "", "kind": "rule", "revision": 1})
    assert [i["name"] for i in rules["items"]] == ["승인 기준"]
    one = data("get_concept", {"id": "term-t-c"})
    assert one["revision"] == 2 and one["id"] == "term-t-c" and "decisions" in one
    by_name = data("get_concept", {"name": "contracts"})
    assert by_name["id"] == "term-t-c"
    by_synonym = data("get_concept", {"name": "계약서", "revision": 1})
    assert by_synonym["id"] == "term-t-c" and by_synonym["evidence"]
    ask = data("sparql_query", {"query": "ASK { ?s a owl:Class }"})
    assert ask["revision"] == 2 and ask["boolean"] is True
    exports = data("list_exports")
    assert exports["revision"] == 2 and [e["revision"] for e in exports["exports"]] == [1]


def test_mcp_domain_errors_are_tool_errors(agent):
    c, _, made = agent
    t = made["token"]
    for name, args, code in (
        ("get_concept", {"id": "term-nope"}, "unknown_concept"),
        ("get_concept", {"name": "없는 개념"}, "unknown_concept"),
        ("get_concept", {}, "invalid_arguments"),
        ("get_concept", {"id": "../x"}, "invalid_concept_id"),
        ("search_concepts", {"query": "x", "limit": 51}, "invalid_arguments"),
        ("search_concepts", {"query": "x", "bogus": 1}, "invalid_arguments"),
        ("get_ontology_summary", {"revision": 9}, "unknown_revision"),
        ("sparql_query", {"query": "SELECT * WHERE {"}, "sparql_syntax"),
        ("sparql_query", {"query": "INSERT DATA { <urn:a> <urn:b> <urn:c> }"}, "sparql_syntax"),
    ):
        res = _tool(c, t, name, args)
        assert res["isError"] is True, (name, args)
        assert json.loads(res["content"][0]["text"])["error"] == code, (name, args, res)
    unknown = _rpc(c, t, "tools/call", {"name": "drop_everything", "arguments": {}})
    assert unknown["error"]["code"] == -32602


def test_mcp_batches_notifications_and_protocol_errors(agent):
    c, _, made = agent
    h = _auth(made["token"])
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "b", "method": "tools/list"},
    ]
    out = c.post(MCP, json=batch, headers=h).json()
    assert [m["id"] for m in out] == [1, "b"] and "tools" in out[1]["result"]
    note = c.post(MCP, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
    assert note.status_code == 202 and note.content == b""
    only_notes = c.post(MCP, json=batch[1:2], headers=h)
    assert only_notes.status_code == 202 and only_notes.content == b""
    bad = c.post(MCP, content=b"{not json", headers={**h, "content-type": "application/json"})
    assert bad.status_code == 200 and bad.json()["error"]["code"] == -32700
    assert bad.json()["id"] is None
    missing = _rpc(c, made["token"], "resources/list")
    assert missing["error"]["code"] == -32601
    assert c.post(MCP, json=[], headers=h).json()["error"]["code"] == -32600
    assert c.post(MCP, json=[1], headers=h).json()[0]["error"]["code"] == -32600


def test_mcp_only_takes_post(agent):
    c, _, made = agent
    for method in ("GET", "PUT", "DELETE"):
        resp = c.request(method, MCP, headers=_auth(made["token"]))
        assert resp.status_code == 405 and resp.headers["allow"] == "POST"


# --- revision pin ------------------------------------------------------------------------


def test_revision_pin_is_enforced(agent):
    c, _, _ = agent
    pinned = c.post("/api/agent/tokens", json={"name": "pin", "revision_pin": 1}).json()
    t = pinned["token"]
    assert _tool(c, t, "get_ontology_summary")["structuredContent"]["revision"] == 1
    assert _tool(c, t, "get_ontology_summary", {"revision": 1})["isError"] is False
    refused = _tool(c, t, "get_ontology_summary", {"revision": 2})
    assert refused["isError"] is True
    assert json.loads(refused["content"][0]["text"])["error"] == "revision_pinned"
    revs = _tool(c, t, "list_revisions")["structuredContent"]
    assert revs["revision"] == 1 and [r["revision"] for r in revs["revisions"]] == [1]
    found = _tool(c, t, "search_concepts", {"query": "계약서"})["structuredContent"]
    assert found["revision"] == 1 and found["total"] == 1  # r2 has no synonyms from documents
    assert _tool(c, t, "sparql_query", {"query": "ASK {}"})["structuredContent"]["revision"] == 1
    init = _rpc(c, t, "initialize", {})["result"]
    assert "pinned to r1" in init["instructions"]
    rest = c.get(f"{REST}/ontology", params={"revision": 2}, headers=_auth(t))
    assert rest.status_code == 403 and rest.json()["error"] == "revision_pinned"
    assert c.get(f"{REST}/exports/1.zip", headers=_auth(t)).status_code == 200
    other_zip = c.get(f"{REST}/exports/2.zip", headers=_auth(t))
    assert other_zip.status_code == 403 and other_zip.json()["error"] == "revision_pinned"


# --- REST --------------------------------------------------------------------------------


def test_rest_mirrors_the_mcp_tools(agent):
    c, _, made = agent
    t, h = made["token"], _auth(made["token"])

    def same(path, tool, args=None, params=None):
        rest = c.get(f"{REST}{path}", params=params or {}, headers=h)
        assert rest.status_code == 200, rest.text
        assert rest.json() == _tool(c, t, tool, args)["structuredContent"]

    same("/revisions", "list_revisions")
    same("/ontology", "get_ontology_summary")
    same("/ontology", "get_ontology_summary", {"revision": 1}, {"revision": 1})
    same(
        "/concepts",
        "search_concepts",
        {"query": "계약", "limit": 3, "revision": 1},
        {"q": "계약", "limit": 3, "revision": 1},
    )
    same("/concepts/term-t-c", "get_concept", {"id": "term-t-c"})
    same("/exports", "list_exports")
    sparql = c.post(f"{REST}/sparql", json={"query": "ASK { ?s a owl:Class }"}, headers=h)
    assert sparql.status_code == 200
    assert (
        sparql.json()
        == _tool(c, t, "sparql_query", {"query": "ASK { ?s a owl:Class }"})["structuredContent"]
    )
    missing = c.get(f"{REST}/concepts/term-nope", headers=h)
    assert missing.status_code == 404 and missing.json()["error"] == "unknown_concept"
    bad = c.get(f"{REST}/concepts", params={"limit": "abc"}, headers=h)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_arguments"
    syntax = c.post(f"{REST}/sparql", json={"query": "SELECT * WHERE {"}, headers=h)
    assert syntax.status_code == 400 and syntax.json()["error"] == "sparql_syntax"


def test_zip_via_token(agent):
    c, _, made = agent
    resp = c.get(f"{REST}/exports/r0001.zip", headers=_auth(made["token"]))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert sorted(names) == ["r0001/okf/index.md", "r0001/report.md"]
    assert c.get(f"{REST}/exports/7.zip", headers=_auth(made["token"])).status_code == 404
    # the UI route still answers the same zip
    assert c.get("/api/exports/1.zip").content == resp.content


def test_openapi_lists_only_agent_routes(agent):
    c, _, made = agent
    spec = c.get(f"{REST}/openapi.json", headers=_auth(made["token"])).json()
    assert spec["openapi"].startswith("3.")
    assert set(spec["paths"]) == {
        f"{REST}/revisions",
        f"{REST}/ontology",
        f"{REST}/concepts",
        f"{REST}/concepts/{{concept_id}}",
        f"{REST}/sparql",
        f"{REST}/exports",
        f"{REST}/exports/{{rev}}.zip",
    }
    assert spec["components"]["securitySchemes"]["bearer"]["scheme"] == "bearer"
    # the agent routes stay out of the service's own schema
    main = c.get("/openapi.json").json()["paths"]
    assert not any(p.startswith(("/agent", "/mcp")) for p in main)


# --- limits, usage, logs -----------------------------------------------------------------


def test_rate_limit_per_token(agent, monkeypatch):
    c, _, made = agent
    monkeypatch.setenv("AUTOONTOLOGY_AGENT_RATE_PER_MIN", "3")
    other = c.post("/api/agent/tokens", json={"name": "other"}).json()["token"]
    for _ in range(3):
        assert c.get(f"{REST}/revisions", headers=_auth(made["token"])).status_code == 200
    limited = c.get(f"{REST}/revisions", headers=_auth(made["token"]))
    assert limited.status_code == 429 and limited.json()["error"] == "rate_limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert (
        c.post(
            MCP, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=_auth(made["token"])
        ).status_code
        == 429
    )
    assert c.get(f"{REST}/revisions", headers=_auth(other)).status_code == 200


def test_body_cap(agent):
    c, _, made = agent
    big = b'{"query": "' + b"x" * (agent_api.MAX_BODY_BYTES + 10) + b'"}'
    for path in (MCP, f"{REST}/sparql"):
        resp = c.post(
            path, content=big, headers={**_auth(made["token"]), "content-type": "application/json"}
        )
        assert resp.status_code == 413 and resp.json()["error"] == "too_large", path


def test_calls_are_recorded_and_summarised(agent):
    c, _, made = agent
    t = made["token"]
    _tool(c, t, "list_revisions")
    _tool(c, t, "get_concept", {"id": "term-nope"})
    c.get(f"{REST}/ontology", headers=_auth(t))
    _rpc(c, t, "tools/list")  # not a tool call
    usage = c.get("/api/agent/usage").json()
    assert usage["total"] == 3 and usage["ok"] == 2
    by_tool = {r["tool"]: r for r in usage["by_tool"]}
    assert by_tool["get_concept"] == {**by_tool["get_concept"], "calls": 1, "ok": 0}
    assert by_tool["get_ontology_summary"]["calls"] == 1
    assert sum(d["calls"] for d in usage["by_day"]) == 3
    tok = usage["by_token"][0]
    assert tok["token_id"] == made["record"]["id"] and tok["calls"] == 3
    assert tok["name"] == "claude code" and tok["by_surface"] == {"mcp": 2, "rest": 1}
    listed = c.get("/api/agent/tokens").json()["tokens"][0]
    assert listed["last_used_at"]


def test_calls_are_pruned(isolated_env, monkeypatch):
    from autoontology.store import agent_tokens

    monkeypatch.setattr(agent_tokens, "MAX_CALLS", 5)
    tokens = AgentTokens(Store.for_home(Settings.from_env().home))
    _, rec = tokens.create("x")
    for i in range(12):
        tokens.record_call(rec.id, "rest", f"t{i}", True, 1)
    assert tokens.usage()["total"] == 5
    assert {r["tool"] for r in tokens.usage()["by_tool"]} == {f"t{i}" for i in range(7, 12)}


def test_tools_route_for_the_ui(agent):
    c, _, _ = agent
    body = c.get("/api/agent/tools").json()
    assert [t["name"] for t in body["tools"]][0] == "list_revisions"
    assert body["mcp_path"] == "/mcp" and body["rest_base"] == "/agent/v1"
    assert all("inputSchema" in t for t in body["tools"])


def test_authorization_never_lands_in_logs(agent, caplog):
    c, _, made = agent
    t = made["token"]
    caplog.set_level(logging.DEBUG)
    _tool(c, t, "list_revisions")
    _tool(c, t, "get_concept", {"id": "term-nope"})
    c.get(f"{REST}/ontology", headers=_auth(t))
    c.get(f"{REST}/ontology", headers=_auth(t + "x"))
    c.post("/api/agent/tokens/" + made["record"]["id"] + "/rotate")
    text = "\n".join(f"{r.getMessage()} {r.args!r} {r.__dict__!r}" for r in caplog.records)
    assert t not in text and t[4:] not in text and "Bearer aok_" not in text


def test_the_host_guard_still_covers_the_agent_routes(agent):
    c, _, made = agent
    resp = c.get(f"{REST}/revisions", headers={**_auth(made["token"]), "host": "evil.example"})
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden_host"
    cross = c.post(
        MCP,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={**_auth(made["token"]), "origin": "https://evil.example"},
    )
    assert cross.status_code == 403


# --- review fixes: batches, nesting, pinned history, buckets ----------------------------


def _call_msg(mid, name="list_revisions"):
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


def test_batch_tool_calls_are_charged_to_the_rate_limit(agent, monkeypatch):
    c, _, made = agent
    monkeypatch.setenv("AUTOONTOLOGY_AGENT_RATE_PER_MIN", "2")
    out = c.post(MCP, json=[_call_msg(i) for i in range(5)], headers=_auth(made["token"])).json()
    assert [m["id"] for m in out] == list(range(5))
    results = [m["result"] for m in out]
    assert [r["isError"] for r in results] == [False, False, True, True, True]
    for r in results[2:]:
        assert json.loads(r["content"][0]["text"])["error"] == "rate_limited"
    # the window is spent: the next request is refused before it runs
    assert c.post(MCP, json=_call_msg(9), headers=_auth(made["token"])).status_code == 429


def test_oversized_batch_is_refused(agent):
    c, _, made = agent
    batch = [_call_msg(i) for i in range(agent_api.MAX_BATCH + 1)]
    out = c.post(MCP, json=batch, headers=_auth(made["token"])).json()
    assert out["error"]["code"] == -32600 and "batch" in out["error"]["message"]
    usage = c.get("/api/agent/usage").json()
    assert usage["total"] == 0  # nothing ran


def test_deeply_nested_json_is_a_parse_error(agent):
    c, _, made = agent
    deep = b"[" * 100_000 + b"]" * 100_000
    h = {**_auth(made["token"]), "content-type": "application/json"}
    resp = c.post(MCP, content=deep, headers=h)
    assert resp.status_code == 200 and resp.json()["error"]["code"] == -32700
    rest = c.post(f"{REST}/sparql", content=deep, headers=h)
    assert rest.status_code == 400 and rest.json()["error"] == "invalid_arguments"


def test_pinned_list_revisions_finds_an_old_revision(agent, monkeypatch):
    c, _, _ = agent
    pinned = c.post("/api/agent/tokens", json={"name": "old", "revision_pin": 1}).json()
    real = Store.list_snapshots
    # as if r1 were older than any listing limit
    monkeypatch.setattr(Store, "list_snapshots", lambda self, limit=50: real(self, 1))
    revs = _tool(c, pinned["token"], "list_revisions")["structuredContent"]
    assert [r["revision"] for r in revs["revisions"]] == [1]
    assert revs["revisions"][0]["run_id"] and "counts" in revs["revisions"][0]


def test_rate_buckets_are_dropped_on_rotate_and_revoke(agent):
    c, _, made = agent
    tid = made["record"]["id"]
    service = c.app.state.agent
    assert c.get(f"{REST}/revisions", headers=_auth(made["token"])).status_code == 200
    assert tid in service._hits
    c.post(f"/api/agent/tokens/{tid}/rotate")
    assert tid not in service._hits
    new = c.post(f"/api/agent/tokens/{tid}/rotate").json()["token"]
    assert c.get(f"{REST}/revisions", headers=_auth(new)).status_code == 200
    assert tid in service._hits
    c.delete(f"/api/agent/tokens/{tid}")
    assert tid not in service._hits
