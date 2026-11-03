# SPDX-License-Identifier: MIT
"""UI/CLI parity commands (``autoontology/cli_ops.py``): the offline ones over a fake build's
store, the HTTP ones against the real app (TestClient) or an ``httpx.MockTransport``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology import cli_ops
from autoontology.cli import app
from autoontology.config import Settings
from autoontology.engine.apply import run_apply
from autoontology.i18n_catalog import en, ko
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web.runs import run_log_path
from tests.test_align import ClientWriter
from tests.test_build import env  # noqa: F401 - the build fixture
from tests.test_decisions import OpsJudge, _no_models, _rows, ops  # noqa: F401
from tests.test_providers_cli import fakes  # noqa: F401 - fixture
from tests.test_settings_api import KEY, envfile, stubs  # noqa: F401 - fixtures

runner = CliRunner()


def _down(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    return httpx.Client(base_url=settings.service_url, transport=httpx.MockTransport(refuse))


@pytest.fixture(autouse=True)
def _no_real_service(monkeypatch):
    """No test reaches a real port: the service is down unless a test plugs one in."""
    monkeypatch.setattr(cli_ops, "http_client", _down)


def _serve(monkeypatch, settings=None, runs=None):
    """Plug the real app in as 'the running service'."""
    settings = settings or Settings.from_env()
    client = TestClient(web.create_app(settings, runs=runs), base_url=settings.service_url)
    monkeypatch.setattr(cli_ops, "http_client", lambda s: client)
    return client


def _mock(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        cli_ops,
        "http_client",
        lambda s: httpx.Client(base_url=s.service_url, transport=httpx.MockTransport(record)),
    )
    return seen


def _ok(args, **kw):
    res = runner.invoke(app, args, **kw)
    assert res.exit_code == 0, res.output
    return res


@pytest.fixture
def two_revisions(ops):  # noqa: F811
    """The fake build (r1) plus an apply that folds 수납 into Customer (r2)."""
    from tests.test_decisions import _accept

    _accept(ops, "수납", "same", "t-customer", note="수납은 고객 계정")
    result = run_apply(
        ops["store"], ops["settings"], client=ops["client"], writer=ClientWriter(ops["client"])
    )
    assert result.revision == 2
    return ops


# --- offline ontology commands --------------------------------------------------------


def test_ontology_revisions_and_diff_read_the_store(two_revisions):
    out = _ok(["ontology", "revisions"]).output
    assert re.search(r"^r2\s+\S+\s+\S+\s+apply\b", out, re.M)
    assert re.search(r"^r1\s+\S+\s+\S+\s+build\b", out, re.M)
    body = json.loads(_ok(["ontology", "revisions", "--json", "--limit", "1"]).output)
    assert [r["revision"] for r in body["revisions"]] == [2]

    out = _ok(["ontology", "diff"]).output
    assert out.startswith("r1 -> r2")
    assert "+ 수납 -> Customer" in out and "merged_concepts+1" in out
    diff = json.loads(_ok(["ontology", "diff", "--from", "1", "--to", "2", "--json"]).output)
    assert diff["from"] == 1 and diff["to"] == 2
    assert [m["concept_name"] for m in diff["merges"]["added"]] == ["수납"]
    bad = runner.invoke(app, ["ontology", "diff", "--to", "9"])
    assert bad.exit_code == 1 and "unknown_revision" in bad.output


def test_ontology_summary_takes_a_revision(two_revisions):
    assert "revision 2" in _ok(["ontology"]).output
    assert _ok(["ontology", "--revision", "1"]).output.startswith("revision 1 ")
    assert json.loads(_ok(["ontology", "--json"]).output)["revision"] == 2
    assert runner.invoke(app, ["ontology", "--revision", "7"]).exit_code == 1


def test_ontology_concepts_filter_and_page(two_revisions):
    body = json.loads(_ok(["ontology", "concepts", "--json", "--size", "2"]).output)
    assert body["size"] == 2 and body["pages"] == -(-body["total"] // 2)
    page2 = json.loads(_ok(["ontology", "concepts", "--json", "--size", "2", "--page", "2"]).output)
    assert {i["id"] for i in page2["items"]}.isdisjoint(i["id"] for i in body["items"])
    terms = json.loads(_ok(["ontology", "concepts", "--json", "--kind", "term"]).output)
    assert terms["items"] and {i["kind"] for i in terms["items"]} == {"term"}
    out = _ok(["ontology", "concepts", "-q", "customer"]).output
    assert "term-t-customer" in out and "page 1/1" in out
    old = json.loads(_ok(["ontology", "concepts", "--json", "--revision", "1"]).output)
    assert old["revision"] == 1
    bad = runner.invoke(app, ["ontology", "concepts", "--origin", "nope"])
    assert bad.exit_code == 1 and "invalid_origin" in bad.output


def test_ontology_concept_shows_alignments_evidence_and_decisions(two_revisions):
    out = _ok(["ontology", "concept", "term-t-customer"]).output
    assert out.startswith("Customer  (term-t-customer, r2)")
    assert "merged concepts: 고객, 수납" in out
    assert re.search(r"same\s+수납 .*accepted", out)
    body = json.loads(_ok(["ontology", "concept", "term-t-customer", "--json"]).output)
    assert body["revision"] == 2 and "decisions" in body
    concept = next(
        i["id"]
        for i in json.loads(_ok(["ontology", "concepts", "--json", "-q", "수납"]).output)["items"]
        if i["name"] == "수납"
    )
    out = _ok(["ontology", "concept", concept]).output
    assert "folded into: Customer" in out
    assert re.search(r"decisions \(1\):\n  alignment: accept same by local", out)
    assert "note: 수납은 고객 계정" in out
    missing = runner.invoke(app, ["ontology", "concept", "term-nope"])
    assert missing.exit_code == 1 and "unknown_concept" in missing.output
    assert runner.invoke(app, ["ontology", "concept", "../x"]).exit_code == 2


def test_ontology_graph_writes_the_page_json(two_revisions, tmp_path):
    target = tmp_path / "out" / "graph.json"
    out = _ok(["ontology", "graph", "--layer", "structured", "--out", str(target)]).output
    assert "layer structured" in out and f"written: {target}" in out
    body = json.loads(target.read_text("utf-8"))
    assert body["layer"] == "structured" and body["revision"] == 2 and body["nodes"]
    assert all(e.get("layer") in (None, "structured") for e in body["edges"])
    printed = json.loads(_ok(["ontology", "graph", "--json", "--evidence"]).output)
    assert printed["layer"] == "all"
    bad = runner.invoke(app, ["ontology", "graph", "--layer", "nope"])
    assert bad.exit_code == 1 and "invalid_layer" in bad.output


def test_offline_commands_before_the_first_build(isolated_env):
    res = runner.invoke(app, ["ontology", "concepts"])
    assert res.exit_code == 1 and "no_snapshot" in res.output
    assert "no ontology snapshot yet" in _ok(["ontology", "revisions"]).output


# --- runs -------------------------------------------------------------------------------


def test_runs_list_stays_the_default(two_revisions):
    out = _ok(["runs", "--limit", "5"]).output
    assert out.startswith("RUN") and " apply " in out and " build " in out
    body = json.loads(_ok(["runs", "--json"]).output)
    assert [r["kind"] for r in body["runs"]] == ["apply", "build"]


def test_runs_show_and_cancel_through_the_service(two_revisions, monkeypatch):
    run_id = two_revisions["store"].list_runs(None, limit=1)[0].id
    _serve(monkeypatch)
    out = _ok(["runs", "show", run_id]).output
    assert out.startswith(f"run {run_id}  apply  succeeded")
    assert re.search(r"^decisions\s+succeeded", out, re.M)
    assert json.loads(_ok(["runs", "show", run_id, "--json"]).output)["id"] == run_id
    gone = runner.invoke(app, ["runs", "show", "20990101T000000Z-abcdef"])
    assert gone.exit_code == 1 and "unknown_run" in gone.output
    done = runner.invoke(app, ["runs", "cancel", run_id])
    assert done.exit_code == 1 and "not_cancellable" in done.output
    done_json = runner.invoke(app, ["runs", "cancel", run_id, "--json"])
    assert done_json.exit_code == 1
    assert json.loads(done_json.stdout)["status"] == 409
    unknown = runner.invoke(app, ["runs", "cancel", "20990101T000000Z-abcdef"])
    assert unknown.exit_code == 1 and "unknown_run" in unknown.output


def test_runs_cancel_accepted(monkeypatch, isolated_env):
    seen = _mock(
        monkeypatch,
        lambda r: httpx.Response(202, json={"run_id": "r1", "cancel_requested": True}),
    )
    out = _ok(["runs", "cancel", "20261101T000000Z-abcdef"]).output
    assert "cancel requested for 20261101T000000Z-abcdef" in out
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/runs/20261101T000000Z-abcdef/cancel"


@pytest.mark.parametrize(
    "args",
    [
        ["runs", "show", "20261101T000000Z-abcdef"],
        ["runs", "cancel", "20261101T000000Z-abcdef"],
        ["runs", "logs", "20261101T000000Z-abcdef", "--follow"],
        ["playground", "sparql", "ASK {}"],
        ["playground", "chat", "hi"],
        ["playground", "index"],
        ["settings", "restart"],
    ],
)
def test_http_commands_exit_2_when_the_service_is_down(isolated_env, args):
    res = runner.invoke(app, args)
    assert res.exit_code == 2, res.output
    assert "start it with `uv run autoontology up`" in res.output
    assert "Traceback" not in res.output


def test_runs_logs_prints_the_stored_log_masked(two_revisions):
    settings = two_revisions["settings"]
    run_id = two_revisions["store"].list_runs(None, limit=1)[0].id
    path = run_log_path(settings, run_id)
    missing = runner.invoke(app, ["runs", "logs", run_id])
    assert missing.exit_code == 1 and "jobs.log" in missing.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("12:00 INFO start\n12:01 INFO POSTGRES_PASSWORD=hunter22 used\n")
    out = _ok(["runs", "logs", run_id]).output
    assert "12:00 INFO start" in out and "hunter22" not in out and "POSTGRES_PASSWORD=***" in out
    lines = _ok(["runs", "logs", run_id, "--json"]).output.splitlines()
    assert json.loads(lines[0]) == {"event": "log", "data": "12:00 INFO start"}
    assert runner.invoke(app, ["runs", "logs", "20990101T000000Z-abcdef"]).exit_code == 1
    assert runner.invoke(app, ["runs", "logs", "../etc"]).exit_code == 2


def test_runs_logs_follow_reads_the_events_until_end(two_revisions, monkeypatch):
    settings = two_revisions["settings"]
    run_id = two_revisions["store"].list_runs(None, limit=1)[0].id
    path = run_log_path(settings, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("one\nPGPASSWORD=s3cretvalue two\n")
    _serve(monkeypatch, settings)
    res = _ok(["runs", "logs", run_id, "--follow"])
    assert res.stdout.splitlines() == ["one", "PGPASSWORD=*** two"]
    assert "[status] succeeded" in res.output
    events = [
        json.loads(x) for x in _ok(["runs", "logs", run_id, "-f", "--json"]).stdout.split("\n") if x
    ]
    assert events[-1]["event"] == "end" and {"log", "step", "status"} <= {
        e["event"] for e in events
    }


# --- sources freshness, samples ---------------------------------------------------------


def test_sources_freshness_table(two_revisions):
    out = _ok(["source", "freshness"]).output
    assert re.search(r"^hanbit\s+files\s+fresh\s+no", out, re.M)
    assert "0 of 2 source(s) stale" in out
    (two_revisions["data"] / "new.csv").write_text("y\n2\n")
    body = json.loads(_ok(["sources", "freshness", "--json", "--refresh"]).output)
    row = next(r for r in body["sources"] if r["name"] == "hanbit")
    assert row["stale"] is True and body["stale"] == 1
    out = _ok(["sources", "freshness"]).output
    assert re.search(
        r"^hanbit\s+files\s+stale\s+yes\s+\S+\s+files_changed\(1\): new.csv", out, re.M
    )


def test_samples_list_and_load_with_conflict_and_replace(isolated_env, tmp_path, monkeypatch):
    from autoontology.structured import remove

    removed = []

    def fake_remove(settings, store, src, **kw):  # no backend in unit tests
        removed.append(src.name)
        store.delete_source(src.id)

    monkeypatch.setattr(remove, "remove_source", fake_remove)
    out = _ok(["samples", "list"]).output
    assert re.search(r"^quickstart\s+quick \(structured, 3 files\)", out, re.M)
    listing = json.loads(_ok(["samples", "list", "--json"]).output)
    assert [s["set"] for s in listing] == ["quickstart", "hanbit"]
    assert listing[0]["registered"] is False
    # a source of the same name pointing elsewhere: refused unless --replace
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.csv").write_text("a\n1\n")
    from autoontology.structured import pipeline

    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    pipeline.add_source(store, str(other), name="quick")
    res = runner.invoke(app, ["samples", "load", "quickstart"])
    assert res.exit_code == 2, res.output
    assert "sample_conflict" in res.output and "add --replace" in res.output
    assert 'send "replace"' not in res.output
    assert "quick: registered for" in res.output
    assert removed == []
    out = _ok(["samples", "load", "quickstart", "--replace"]).output
    assert removed == ["quick"]
    assert re.search(r"^quick\s+files\s+replaced", out, re.M)
    assert re.search(r"^quick-docs\s+documents\s+created", out, re.M)
    assert "next: `uv run autoontology build`" in out
    again = json.loads(_ok(["samples", "load", "quickstart", "--json"]).output)
    assert {s["action"] for s in again["sources"]} == {"kept"}
    unknown = runner.invoke(app, ["samples", "load", "nope"])
    assert unknown.exit_code == 2 and "unknown_sample_set" in unknown.output


# --- exports ----------------------------------------------------------------------------


@pytest.fixture
def exports(isolated_env):
    settings = Settings.from_env()
    root = Path(settings.exports_dir) / "r0002"
    (root / "okf").mkdir(parents=True)
    (root / "ossie").mkdir()
    (root / "report.md").write_text("# 보고서\n", "utf-8")
    (root / "okf" / "validation.json").write_text('{"ok": true}')
    (root / "ossie" / "validation.json").write_text('{"ontology": "ok"}')
    (root / ".hidden").write_text("x")
    return root


def test_exports_list_show_get(exports, tmp_path):
    out = _ok(["exports", "list"]).output
    assert re.search(r"^r2\s+r0002\s+\S+\s+3\s+", out, re.M) and "okf/ (1)" in out
    assert json.loads(_ok(["exports", "list", "--json"]).output)["exports"][0]["dir"] == "r0002"
    out = _ok(["exports", "show", "2"]).output
    assert "ossie/validation.json" in out and ".hidden" not in out
    assert _ok(["exports", "show", "r0002", "okf/validation.json"]).output == '{"ok": true}\n'
    shown = json.loads(_ok(["exports", "show", "r2", "report.md", "--json"]).output)
    assert shown == {"dir": "r0002", "path": "report.md", "text": "# 보고서\n"}
    target = tmp_path / "dl"
    target.mkdir()
    assert (
        "saved"
        in _ok(["exports", "get", "2", "ossie/validation.json", "--out", str(target)]).output
    )
    assert (target / "validation.json").read_text() == '{"ontology": "ok"}'
    assert runner.invoke(app, ["exports", "show", "2", "../../etc/passwd"]).exit_code == 2
    assert runner.invoke(app, ["exports", "show", "2", ".hidden"]).exit_code == 1
    assert runner.invoke(app, ["exports", "show", "x2"]).exit_code == 2
    assert runner.invoke(app, ["exports", "show", "9"]).exit_code == 1


# --- playground (HTTP, the UI's payloads) ---------------------------------------------


def _sse(*events):
    body = "".join(
        f"event: {name}\r\ndata: {json.dumps(data, ensure_ascii=False)}\r\n\r\n"
        for name, data in events
    )
    return httpx.Response(
        200, text=": ping\r\n\r\n" + body, headers={"content-type": "text/event-stream"}
    )


def test_playground_chat_streams_the_answer_and_citations(isolated_env, monkeypatch):
    conv = "a" * 32
    seen = _mock(
        monkeypatch,
        lambda r: _sse(
            ("status", {"phase": "index", "provider": "claude", "model": "m"}),
            (
                "retrieved",
                {"elements": [{"ref": "E1", "label": "고객", "type": "class", "id": "term-x"}]},
            ),
            ("delta", {"text": "고객은 "}),
            ("delta", {"text": "계약을 맺는다 [E1]."}),
            ("citations", {"citations": ["E1"], "unknown": []}),
            ("done", {"conversation_id": conv, "message_id": 3}),
        ),
    )
    res = _ok(["playground", "chat", "고객이란?", "--conversation", "b" * 32, "--top-k", "5"])
    assert json.loads(seen[0].content) == {
        "question": "고객이란?",
        "conversation_id": "b" * 32,
        "top_k": 5,
    }
    assert seen[0].url.path == "/api/playground/chat"
    assert "고객은 계약을 맺는다 [E1]." in res.stdout
    assert "[E1] 고객  (class term-x)" in res.stdout
    assert f"--conversation {conv}" in res.stdout
    lines = _ok(["playground", "chat", "q", "--json"]).stdout.splitlines()
    assert json.loads(lines[-1]) == {
        "event": "done",
        "data": {"conversation_id": conv, "message_id": 3},
    }


def test_playground_chat_error_event_exits_1(isolated_env, monkeypatch):
    _mock(
        monkeypatch,
        lambda r: _sse(("error", {"status": 502, "error": "embed_failed", "detail": "x"})),
    )
    res = runner.invoke(app, ["playground", "chat", "q"])
    assert res.exit_code == 1 and "embed_failed: x" in res.output
    _mock(
        monkeypatch,
        lambda r: httpx.Response(409, json={"error": "playground_busy", "detail": "busy"}),
    )
    res = runner.invoke(app, ["playground", "chat", "q"])
    assert res.exit_code == 1 and "playground_busy: busy" in res.output


def test_playground_sql_relays_steps_and_result(isolated_env, monkeypatch):
    seen = _mock(
        monkeypatch,
        lambda r: _sse(
            ("meta", {"notices": [], "target_db": "db1"}),
            ("step", {"node": "plan", "label": "planning"}),
            (
                "result",
                {
                    "answer": {
                        "sql_code": "select 1",
                        "sql_columns": ["n"],
                        "sql_response_from_db": [[1]],
                        "response": "하나입니다",
                    }
                },
            ),
            ("done", {}),
        ),
    )
    res = _ok(["playground", "sql", "몇 개?", "--source", "hanbit"])
    assert json.loads(seen[0].content) == {"question": "몇 개?", "source": "hanbit"}
    assert (
        "SQL: select 1" in res.stdout and "하나입니다" in res.stdout and "[planning]" in res.output
    )


def test_playground_sparql_from_a_file(isolated_env, monkeypatch, tmp_path):
    result = {
        "type": "SELECT",
        "columns": ["c", "label"],
        "rows": [["http://x#A", "고객"]],
        "total": 1,
        "truncated": False,
        "triples": 9,
        "revision": 4,
        "ttl": "ontology.ttl",
    }
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json=result))
    query = tmp_path / "q.rq"
    query.write_text("SELECT ?c ?label WHERE { ?c rdfs:label ?label }", "utf-8")
    res = _ok(["playground", "sparql", "--file", str(query), "--limit", "10"])
    assert json.loads(seen[0].content) == {"query": query.read_text("utf-8"), "limit": 10}
    assert re.search(r"^http://x#A\s+고객$", res.stdout, re.M)
    assert "SELECT: 1 result(s); r4 ontology.ttl" in res.output
    assert json.loads(_ok(["playground", "sparql", "ASK {}", "--json"]).stdout) == result
    both = runner.invoke(app, ["playground", "sparql", "ASK {}", "--file", str(query)])
    assert both.exit_code == 2
    assert runner.invoke(app, ["playground", "sparql"]).exit_code == 2
    _mock(
        monkeypatch, lambda r: httpx.Response(400, json={"error": "sparql_syntax", "detail": "bad"})
    )
    bad = runner.invoke(app, ["playground", "sparql", "SELEC"])
    assert bad.exit_code == 1 and "sparql_syntax: bad" in bad.output


def test_playground_index_status_and_rebuild(isolated_env, monkeypatch):
    status = {"scope": ["hanbit"], "revision": 3, "index": None, "stale": True, "warnings": []}
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json=status))
    assert "no index yet" in _ok(["playground", "index"]).output
    _ok(["playground", "index", "--rebuild"])
    assert [r.method for r in seen] == ["GET", "POST"]


# --- settings ---------------------------------------------------------------------------


def test_settings_set_reads_the_key_from_stdin_only(envfile, stubs, caplog):  # noqa: F811
    caplog.set_level("DEBUG")
    args = [
        "settings",
        "set",
        "--chat-provider",
        "openai",
        "--chat-model",
        "gpt-test",
        "--base-url",
        "http://127.0.0.1:9/v1",
        "--api-key-stdin",
    ]
    res = runner.invoke(app, args, input=KEY + "\n")
    assert res.exit_code == 0, res.output
    assert KEY not in res.output and KEY not in " ".join(args) and KEY not in caplog.text
    text = envfile.read_text()
    assert f"AUTOONTOLOGY_OPENAI_API_KEY={KEY}" in text
    assert "AUTOONTOLOGY_CHAT_PROVIDER=openai" in text and "# keep me" in text
    assert "AUTOONTOLOGY_OPENAI_API_KEY" in res.output and "settings restart --wait" in res.output
    shown = _ok(["settings", "show", "--json"]).output
    assert KEY not in shown
    body = json.loads(shown)
    assert body["openai"]["api_key_set"] is True and body["service_running"] is False
    assert "API key set" in _ok(["settings", "show"]).output
    # the saved key is never sent to another endpoint: a new base URL needs a new key
    moved = runner.invoke(app, ["settings", "set", "--base-url", "https://other.test/v1"])
    assert moved.exit_code == 1 and "key_required" in moved.output
    cleared = _ok(["settings", "set", "--chat-provider", "claude", "--clear-api-key"])
    assert KEY not in envfile.read_text() and KEY not in cleared.output


def test_settings_set_refusals(envfile, stubs, monkeypatch):  # noqa: F811
    assert runner.invoke(app, ["settings", "set"]).exit_code == 2
    empty = runner.invoke(app, ["settings", "set", "--api-key-stdin"], input="\n")
    assert empty.exit_code == 2 and "empty line" in empty.output
    both = runner.invoke(app, ["settings", "set", "--api-key-stdin", "--clear-api-key"])
    assert both.exit_code == 2
    bad = runner.invoke(app, ["settings", "set", "--chat-provider", "gemini"])
    assert bad.exit_code == 2 and "invalid_settings" in bad.output
    monkeypatch.setenv("AUTOONTOLOGY_CODEX_MODEL", "gpt-5.5")
    shell = runner.invoke(
        app, ["settings", "set", "--chat-provider", "codex", "--chat-model", "x-1"]
    )
    assert shell.exit_code == 1 and "shell_override" in shell.output
    # the dimension guard of the Settings page
    stubs["vectors"] = {"rows": 42, "dimension": 1024}
    stubs["dims"] = 768
    dim = runner.invoke(app, ["settings", "set", "--embed-model", "other-embed"])
    assert dim.exit_code == 1 and "embedding_dimension_changed" in dim.output
    _ok(["settings", "set", "--embed-model", "other-embed", "--confirm-reingest"])
    assert "AUTOONTOLOGY_OLLAMA_EMBED_MODEL=other-embed" in envfile.read_text()


def test_settings_test_pongs_without_saving(envfile, stubs, fakes):  # noqa: F811
    res = _ok(["settings", "test", "--chat-provider", "codex"])
    assert re.search(r"^chat\s+OK\s+codex gpt-6-astra\s+'pong'", res.output, re.M)
    assert "embedding  OK   ollama bge-m3  dimension 1024" in res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in envfile.read_text()
    stubs["embed_error"] = "refused"
    failed = runner.invoke(app, ["settings", "test", "--json"])
    assert failed.exit_code == 1 and json.loads(failed.stdout)["embed"]["ok"] is False


def test_settings_show_prefers_the_running_service(envfile, stubs, monkeypatch):  # noqa: F811
    _serve(monkeypatch)
    body = json.loads(_ok(["settings", "show", "--json"]).output)
    assert body["service_running"] is True and body["chat"]["provider"] == "claude"


def test_settings_restart_waits_for_the_record(isolated_env, monkeypatch):
    states = iter(["running", "done"])

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"restart_id": "0123456789ab", "previous_pid": 1})
        return httpx.Response(
            200, json={"id": "0123456789ab", "state": next(states), "new_pid": 77}
        )

    seen = _mock(monkeypatch, handler)
    monkeypatch.setattr(cli_ops, "RESTART_POLL_S", 0.0)
    assert "restart 0123456789ab started" in _ok(["settings", "restart"]).output
    out = _ok(["settings", "restart", "--wait"]).output
    assert "restart 0123456789ab done" in out and "pid 77" in out
    assert seen[-1].url.path == "/api/settings/restart/0123456789ab"
    _mock(
        monkeypatch, lambda r: httpx.Response(409, json={"error": "not_supervised", "detail": "no"})
    )
    res = runner.invoke(app, ["settings", "restart", "--wait"])
    assert res.exit_code == 1 and "not_supervised" in res.output


# --- review paging, SSE parser, i18n ------------------------------------------------------


def test_review_list_pages(ops):  # noqa: F811
    full = json.loads(_ok(["review", "list", "--json", "--size", "100"]).output)
    assert full["total"] >= 2
    first = json.loads(_ok(["review", "list", "--json", "--size", "1"]).output)
    second = json.loads(_ok(["review", "list", "--json", "--size", "1", "--page", "2"]).output)
    assert [first["items"][0]["id"], second["items"][0]["id"]] == [
        i["id"] for i in full["items"][:2]
    ]
    assert f"page 2/{full['total']}" in _ok(["review", "list", "--size", "1", "--page", "2"]).output


def test_parse_sse():
    lines = [": ping", "", "event: log", "id: 7", "data: plain", "", 'data: {"a": 1}', "data: "]
    assert list(cli_ops.parse_sse(iter(lines))) == [
        ("log", "plain", "7"),
        ("message", {"a": 1}, None),
    ]


def test_every_ops_message_key_is_in_both_catalogues():
    source = Path(cli_ops.__file__).read_text("utf-8")
    keys = set(re.findall(r"msg\(\s*[\"']([a-z_.]+)[\"']", source))
    assert len(keys) > 60
    assert keys <= set(en.MESSAGES) and keys <= set(ko.MESSAGES)


def test_korean_help(monkeypatch):
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "autoontology", "settings", "set", "--help"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "AUTOONTOLOGY_LANG": "ko", "COLUMNS": "200"},
        check=True,
    ).stdout
    assert "표준 입력" in out and "--api-key-stdin" in out


# --- fix wave: path segments, Korean help, service-first writes, --json errors ---------


@pytest.mark.parametrize(
    "args",
    [
        ["ontology", "concept", "../../ontology"],
        ["ontology", "concept", "term-x?revision=1"],
        ["runs", "show", "../settings"],
        ["runs", "cancel", "../settings/restart?x="],
        ["runs", "logs", "../settings", "--follow"],
        ["exports", "show", "1", "../../samples"],
        ["exports", "show", "1", "okf/../../samples"],
        ["exports", "show", "1", "okf/./validation.json"],
        ["exports", "show", "1", "report.md?inline=0"],
        ["exports", "show", "1", "report.md#x"],
        ["exports", "show", "1", "/etc/passwd"],
        ["exports", "show", "1", "okf\\validation.json"],
        ["exports", "get", "1", "../settings", "--out", "x.json"],
    ],
)
def test_path_segments_never_reach_another_endpoint(isolated_env, monkeypatch, args):
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json={"id": "x"}))
    res = runner.invoke(app, args)
    assert res.exit_code == 2, res.output
    assert (
        "Traceback" not in res.output
        and res.exception is None
        or isinstance(res.exception, SystemExit)
    )
    assert seen == []


def test_export_paths_are_quoted(exports):
    (exports / "okf" / "계약-갱신.md").write_text("본문", "utf-8")
    assert _ok(["exports", "show", "2", "okf/계약-갱신.md"]).output == "본문\n"


def test_restart_id_from_the_service_is_quoted(isolated_env, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"restart_id": "../../ontology", "previous_pid": 1})
        return httpx.Response(200, json={"id": "x", "state": "done", "new_pid": 2})

    seen = _mock(monkeypatch, handler)
    monkeypatch.setattr(cli_ops, "RESTART_POLL_S", 0.0)
    _ok(["settings", "restart", "--wait"])
    assert seen[-1].url.raw_path == b"/api/settings/restart/..%2F..%2Fontology"


NEW_COMMANDS = [
    ["ontology"],
    ["ontology", "revisions"],
    ["ontology", "diff"],
    ["ontology", "concepts"],
    ["ontology", "concept"],
    ["ontology", "graph"],
    ["runs"],
    ["runs", "show"],
    ["runs", "logs"],
    ["runs", "cancel"],
    ["source"],
    ["source", "freshness"],
    ["samples"],
    ["samples", "list"],
    ["samples", "load"],
    ["exports"],
    ["exports", "list"],
    ["exports", "show"],
    ["exports", "get"],
    ["playground"],
    ["playground", "chat"],
    ["playground", "sql"],
    ["playground", "sparql"],
    ["playground", "index"],
    ["settings"],
    ["settings", "show"],
    ["settings", "test"],
    ["settings", "set"],
    ["settings", "restart"],
    ["review", "list"],
    [],
]

_HELP_SCRIPT = """
import json, re, sys
TOP = r"\\W+(ontology|runs|samples|exports|playground|settings) "
from typer.testing import CliRunner
from autoontology.cli import app
bad = {}
for args in json.loads(sys.argv[1]):
    out = CliRunner().invoke(app, [*args, "--help"], env={"COLUMNS": "80"}).output
    lines = [line for line in out.splitlines() if "\\u2026" in line]
    if not args:  # the top level: only the rows of the groups this module adds
        lines = [x for x in lines if re.match(TOP, x)]
    if lines or not out.strip():
        bad[" ".join(args)] = lines
print(json.dumps(bad, ensure_ascii=False))
"""


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_help_never_truncates_at_80_columns(lang):
    import os
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", _HELP_SCRIPT, json.dumps(NEW_COMMANDS)],
        capture_output=True,
        text=True,
        env={**os.environ, "AUTOONTOLOGY_LANG": lang, "COLUMNS": "80"},
        check=True,
    ).stdout
    assert json.loads(out) == {}


def test_settings_set_and_test_go_through_the_running_service(isolated_env, monkeypatch):
    def handler(request):
        if request.method == "PUT":
            return httpx.Response(
                200,
                json={
                    "written": ["AUTOONTOLOGY_OPENAI_API_KEY"],
                    "backup": None,
                    "restart_required": True,
                },
            )
        if request.url.path == "/api/settings/test":
            return httpx.Response(
                200,
                json={
                    "chat": {
                        "ok": True,
                        "provider": "claude",
                        "model": "m",
                        "answer": "pong",
                        "seconds": 1.0,
                    },
                    "embed": {
                        "ok": True,
                        "provider": "ollama",
                        "model": "bge-m3",
                        "dimension": 1024,
                    },
                },
            )
        return httpx.Response(
            400, json={"error": "shell_override", "detail": "set in the service's shell"}
        )

    seen = _mock(monkeypatch, handler)
    res = runner.invoke(
        app,
        ["settings", "set", "--base-url", "http://127.0.0.1:9/v1", "--api-key-stdin"],
        input=KEY + "\n",
    )
    assert res.exit_code == 0, res.output
    assert KEY not in res.output
    assert seen[0].method == "PUT" and seen[0].url.path == "/api/settings"
    assert json.loads(seen[0].content) == {
        "openai_base_url": "http://127.0.0.1:9/v1",
        "api_key": KEY,
    }
    out = _ok(["settings", "test", "--no-saved-key"]).output
    assert "'pong'" in out and json.loads(seen[-1].content) == {"use_saved_key": False}
    _mock(
        monkeypatch,
        lambda r: httpx.Response(
            400, json={"error": "shell_override", "detail": "set in the service's shell"}
        ),
    )
    refused = runner.invoke(app, ["settings", "set", "--chat-provider", "codex", "--json"])
    assert refused.exit_code == 1
    assert json.loads(refused.stdout)["error"] == "shell_override"


def test_samples_load_uses_the_running_service(isolated_env, monkeypatch):
    seen = _mock(
        monkeypatch,
        lambda r: httpx.Response(
            409, json={"error": "run_active", "detail": "a build or export is running"}
        ),
    )
    res = runner.invoke(app, ["samples", "load", "quickstart"])
    assert res.exit_code == 1 and "run_active" in res.output
    assert json.loads(seen[0].content) == {"set": "quickstart", "replace": False}
    assert seen[0].url.path == "/api/samples/load"


def test_exports_get_into_a_directory(exports, tmp_path):
    missing = tmp_path / "nope"
    res = runner.invoke(app, ["exports", "get", "2", "report.md", "--out", f"{missing}/"])
    assert res.exit_code == 2 and not missing.exists()
    existing = tmp_path / "dl"
    existing.mkdir()
    _ok(["exports", "get", "2", "report.md", "--out", str(existing)])
    assert (existing / "report.md").read_text("utf-8") == "# 보고서\n"
    _ok(["exports", "get", "2", "report.md", "--out", str(tmp_path / "new" / "r.md")])
    assert (tmp_path / "new" / "r.md").is_file()


@pytest.mark.parametrize(
    "args",
    [
        ["runs", "show", "20261101T000000Z-abcdef", "--json"],
        ["playground", "chat", "q", "--json"],
        ["playground", "sql", "q", "--json"],
        ["settings", "restart", "--json"],
    ],
)
def test_json_error_envelope_when_the_service_is_down(isolated_env, args):
    res = runner.invoke(app, args)
    assert res.exit_code == 2
    body = json.loads(res.stdout)
    assert body["error"] == "service_down" and "uv run autoontology up" in body["detail"]


def test_json_error_envelope_for_api_and_usage_errors(exports, monkeypatch):
    for args, code, error in (
        (["exports", "show", "2", "nope.md", "--json"], 1, "unknown_export"),
        (["exports", "show", "x", "--json"], 2, "invalid_revision"),
        (["ontology", "concept", "../x", "--json"], 2, "invalid_concept_id"),
    ):
        res = runner.invoke(app, args)
        assert res.exit_code == code, res.output
        body = json.loads(res.stdout)
        assert body["error"] == error and body["detail"]

    def broken(self, method, path, **kw):
        raise cli_ops.CallError(500, "internal_error", "boom")

    monkeypatch.setattr(cli_ops.LocalApi, "call", broken)
    for args in (["runs", "--json"], ["exports", "list", "--json"], ["samples", "list", "--json"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 1 and json.loads(res.stdout) == {
            "error": "internal_error",
            "detail": "boom",
            "status": 500,
        }, args


def test_playground_sparql_examples(isolated_env, monkeypatch):
    examples = [{"title": "Classes", "query": "SELECT ?c WHERE { ?c a owl:Class }"}]
    seen = _mock(monkeypatch, lambda r: httpx.Response(200, json={"examples": examples}))
    out = _ok(["playground", "sparql", "--examples"]).output
    assert "Classes" in out and "SELECT ?c WHERE" in out
    assert seen[0].url.path == "/api/playground/sparql/examples"
    assert json.loads(_ok(["playground", "sparql", "--examples", "--json"]).stdout) == {
        "examples": examples
    }
