# SPDX-License-Identifier: MIT
"""Usage receipts preserve the exact request context without rerunning providers."""

import json

import httpx
import pytest

from autoontology.playground.llm import LLMError
from autoontology.store.traces import TraceError, UsageTraces
from tests.test_playground import API, _sse
from tests.test_playground import client as playground_client  # noqa: F401


@pytest.fixture
def client(playground_client):  # noqa: F811
    return playground_client


def test_chat_trace_preserves_sent_prompt_hits_answer_revision_and_timing(client):
    c, _, app, llm, _ = client
    events = _sse(c.post(f"{API}/chat", json={"question": "승인 기준은?"}).text)
    done = dict(events)["done"]
    trace = c.get(f"{API}/traces/{done['trace_id']}").json()
    assert trace["status"] == "succeeded" and trace["kind"] == "chat"
    assert trace["data"]["binding"]["revision"] == 1
    assert trace["data"]["binding"]["legacy"] is True
    assert trace["data"]["context"]["messages"] == llm.messages[-1]
    assert trace["data"]["context"]["hits"][0]["text"]
    assert trace["data"]["context"]["index"]["sha256"]
    assert trace["data"]["outcome"]["answer"] == llm.answer
    assert trace["data"]["duration_ms"] >= 0
    assert trace["data"]["actor"]["verified"] is False
    assert (
        c.get(f"{API}/conversations/{done['conversation_id']}").json()["messages"][-1]["trace_id"]
        == done["trace_id"]
    )
    old = trace
    model = app.state.api.latest_model()[1]
    app.state.api.store.save_snapshot("new-run", model)
    assert c.get(f"{API}/traces/{done['trace_id']}").json() == old
    assert UsageTraces(app.state.api.store).verify(done["trace_id"])["ok"]


def test_failed_and_truncated_answers_are_never_successful_traces(client):
    c, _, app, llm, _ = client

    async def broken(messages):
        yield "partial"
        raise LLMError(502, "stream_truncated", "provider body must not enter the receipt")

    llm.stream = broken
    events = _sse(c.post(f"{API}/chat", json={"question": "계약?"}).text)
    trace_id = dict(events)["status"]["trace_id"]
    trace = c.get(f"{API}/traces/{trace_id}").json()
    assert trace["status"] == "failed" and trace["data"]["outcome"]["partial"] is True
    assert trace["data"]["outcome"]["answer"] == "partial"
    assert trace["data"]["outcome"]["error"] == "stream_truncated"
    assert "provider body" not in json.dumps(trace)
    assert "done" not in dict(events)
    cid = dict(events)["stream_truncated"]["conversation_id"]
    c.delete(f"{API}/conversations/{cid}")
    assert c.get(f"{API}/traces/{trace_id}").status_code == 404


def test_retrieval_has_a_trace_and_never_calls_the_chat_model(client):
    c, _, _, llm, _ = client
    result = c.post(f"{API}/retrieve", json={"question": "승인 기준", "top_k": 3}).json()
    assert result["hits"] and not llm.messages
    trace = c.get(f"{API}/traces/{result['trace_id']}").json()
    assert trace["kind"] == "retrieval" and trace["status"] == "succeeded"
    assert trace["data"]["context"]["hits"] == result["hits"]


def test_trace_terminal_state_is_immutable_and_tampering_is_detected(client):
    c, _, app, _, _ = client
    result = c.post(f"{API}/retrieve", json={"question": "계약"}).json()
    trace_id = result["trace_id"]
    traces = UsageTraces(app.state.api.store)
    assert traces.finish(trace_id, "failed", {"error": "late_cleanup"}) is False
    assert traces.get(trace_id)["status"] == "succeeded"
    with app.state.api.store._conn() as conn:
        conn.execute("UPDATE usage_traces SET data_json='{}' WHERE id=?", (trace_id,))
    with pytest.raises(TraceError, match="integrity"):
        traces.get(trace_id)
    assert c.get(f"{API}/traces/{trace_id}").status_code == 409


def test_sql_trace_records_actual_result_and_execution_failure_is_not_done(client):
    from autoontology.playground.sql_guard import SqlError
    from tests.test_sql_service import attach

    c, _, _, _, _ = client
    _, _, executor = attach(client)
    for failure in (None, SqlError("Query deadline exceeded", "sql_timeout", 504)):
        executor.error = failure
        events = dict(_sse(c.post(f"{API}/sql", json={"question": "고객 수"}).text))
        trace = c.get(f"{API}/traces/{events['meta']['trace_id']}").json()
        assert trace["status"] == ("failed" if failure else "succeeded")
        assert trace["data"]["context"]["data_time"] == "live_at_execution"
        if failure:
            assert "done" not in events and events["error"]["error"] == "sql_timeout"
        else:
            assert trace["data"]["outcome"]["events"][0]["answer"]["sql_response_from_db"] == [[40]]


def test_sparql_trace_captures_query_result_and_exact_ttl_hash(client):
    c, _, _, _, _ = client
    result = c.post(f"{API}/sparql", json={"query": "ASK {}"}).json()
    trace = c.get(f"{API}/traces/{result['trace_id']}").json()
    assert trace["kind"] == "sparql" and trace["status"] == "succeeded"
    assert trace["data"]["context"]["ttl_sha256"]
    assert trace["data"]["request"]["query"] == "ASK {}"
    assert trace["data"]["outcome"]["result"]["boolean"] is True


def test_disconnected_stream_before_body_has_cancelled_receipt_and_no_model_call(client):
    from fastapi import FastAPI

    from autoontology.web import playground_api
    from tests.test_playground import _asgi_post_then_leave

    _, settings, app, llm, _ = client
    bare = FastAPI()
    bare.include_router(playground_api.build_router(app.state.api, app))
    assert _asgi_post_then_leave(bare, f"{API}/chat", {"question": "계약?"}) == [200]
    rows = UsageTraces(app.state.api.store).listing(workspace=settings.workspace)
    assert rows[0]["status"] == "cancelled"
    assert not llm.messages and not app.state.playground._busy


def test_provider_preflight_failure_has_a_failed_receipt_and_releases_slot(client):
    c, settings, app, _, _ = client

    def failed():
        raise LLMError(503, "provider_unavailable", "not logged in")

    app.state.playground.llm_factory = failed
    response = c.post(f"{API}/chat", json={"question": "계약?"})
    assert response.status_code == 503
    assert response.json()["trace_id"]
    trace = UsageTraces(app.state.api.store).listing(workspace=settings.workspace)[0]
    assert trace["status"] == "failed" and not app.state.playground._busy


def test_sql_rejects_unpublished_sources_without_contacting_model_or_database(client):
    from tests.test_sql_service import attach

    c, _, app, _, _ = client
    _, planner, executor = attach(client)
    source = app.state.api.store.add_source(name="unpublished", kind="files", locator="/private")
    app.state.api.store.set_catalog_database_id(source.id, "private-db")
    result = c.post(f"{API}/sql", json={"source": "unpublished", "question": "rows?"})
    assert result.status_code == 409 and result.json()["error"] == "source_not_published"
    assert planner.calls == executor.calls == []


def test_query_cli_uses_same_service_and_inspection_never_repeats_the_model(client, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli

    c, settings, _, llm, _ = client
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    real_request, real_stream = httpx.request, httpx.stream

    def call(method, url, **kwargs):
        return c.request(method, url, json=kwargs.get("json"))

    def stream(method, url, **kwargs):
        return c.stream(method, url, json=kwargs.get("json"))

    monkeypatch.setattr(httpx, "request", call)
    monkeypatch.setattr(httpx, "stream", stream)
    runner = CliRunner()
    answer = runner.invoke(cli.app, ["ops", "ask", "승인자는?"])
    assert answer.exit_code == 0, answer.output
    receipt = json.loads(answer.output)
    assert receipt["status"] == "succeeded" and receipt["data"]["outcome"]["answer"] == llm.answer
    calls = len(llm.messages)
    inspect = runner.invoke(cli.app, ["ops", "trace", receipt["id"]])
    assert json.loads(inspect.output) == receipt and len(llm.messages) == calls
    retrieve = runner.invoke(cli.app, ["ops", "retrieve", "계약"])
    assert retrieve.exit_code == 0 and len(llm.messages) == calls
    assert json.loads(retrieve.output)["hits"]
    monkeypatch.setattr(httpx, "request", real_request)
    monkeypatch.setattr(httpx, "stream", real_stream)


def test_dead_process_trace_survives_restart_as_interrupted(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from autoontology.store.db import Store

    path = tmp_path / "home"
    script = """
import os,sys
from pathlib import Path
from autoontology.store.db import Store
from autoontology.store.traces import UsageTraces
from autoontology.ontology.model import MergedOntology
store=Store.for_home(Path(sys.argv[1]))
model=MergedOntology('org','https://example.test/#')
revision=store.save_snapshot('test',model)
trace=UsageTraces(store).begin(kind='chat',workspace='org',revision=revision,model=model,request={'question':'policy?'})
UsageTraces(store).context(trace,{'messages':[{'role':'user','content':'policy?'}]})
print(trace,flush=True)
os._exit(73)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 73, child.stderr
    trace_id = child.stdout.strip()
    traces = UsageTraces(Store.for_home(path))
    assert traces.listing(workspace="org")[0]["status"] == "interrupted"
    trace = traces.get(trace_id)
    assert trace["data"]["context"]["messages"][0]["content"] == "policy?"
    assert trace["data"]["outcome"]["error"] == "owner_process_exited"
    assert traces.verify(trace_id)["ok"]


def test_trace_binds_a_new_publication_and_can_be_restored_from_backup(tmp_path):
    import asyncio
    import sqlite3
    from contextlib import closing

    from autoontology.ontology.model import MergedOntology
    from autoontology.playground.service import Playground
    from autoontology.store.db import Store
    from autoontology.store.publications import Publications
    from tests import test_publication_manifest as publication
    from tests.test_playground import FakeLLM

    prepared = publication.prepared.__wrapped__(tmp_path)
    store, _, _, _, _ = prepared
    result = publication.publish(prepared)
    model = MergedOntology.from_dict(store.get_snapshot(result.revision).model)
    llm = FakeLLM()
    service = Playground(
        store,
        model_for=lambda: (result.revision, model),
        embedder_factory=lambda: (publication.embed, "test"),
        llm_factory=lambda: llm,
    )

    async def run():
        return [event async for event in service.ask(service.scope(), "승인 기준은?")]

    events = asyncio.run(run())
    trace_id = events[-1]["data"]["trace_id"]
    receipt = service.traces.get(trace_id)
    assert (
        receipt["data"]["binding"]["publication_id"]
        == Publications(store).get(result.revision)["id"]
    )
    assert (
        receipt["data"]["context"]["index"]["publication_index"]
        == Publications(store).get(result.revision)["manifest"]["index"]
    )
    restored = tmp_path / "restored"
    restored.mkdir()
    with (
        closing(sqlite3.connect(store.path)) as source,
        closing(sqlite3.connect(restored / "autoontology.db")) as target,
    ):
        source.backup(target)
    recovered = UsageTraces(Store.for_home(restored))
    assert recovered.get(trace_id) == receipt
    assert recovered.verify(trace_id)["ok"]


@pytest.mark.parametrize("rows", [None, "SQL does not reference a known table", 3])
def test_sql_result_without_executed_rows_is_not_a_success(rows):
    from autoontology.web.playground_api import sql_result_complete

    assert not sql_result_complete(
        {
            "type": "result",
            "answer": {"sql_code": "SELECT count(*) FROM customers", "sql_response_from_db": rows},
        }
    )


def test_v9_migration_preserves_existing_conversation_and_snapshots(tmp_path, monkeypatch):
    from autoontology.ontology.model import MergedOntology
    from autoontology.playground.store import PlaygroundStore
    from autoontology.store import db

    path = tmp_path / "autoontology.db"
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS", [(v, sql) for v, sql in db.MIGRATIONS if v < 9])
        store = db.Store(path)
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO playground_conversations VALUES('c','org','title','then','then')"
            )
            conn.execute(
                "INSERT INTO playground_messages(conversation_id,role,content,retrieved_json,citations_json,provider,model,incomplete,created_at) VALUES('c','assistant','Historical answer','[]','{}','test','old',0,'then')"
            )
        revision = store.save_snapshot("old", MergedOntology("org", "https://example.test/#"))
        snapshot = store.get_snapshot(revision)
    current = db.Store(path)
    assert current.get_snapshot(revision) == snapshot
    message = PlaygroundStore(current).messages("c")[0]
    assert message["content"] == "Historical answer" and message["trace_id"] is None


@pytest.mark.parametrize("vector", [[0.0] * 96, [float("nan")] * 96])
def test_invalid_query_vectors_fail_without_an_unsupported_no_basis_answer(client, vector):
    c, settings, app, llm, _ = client
    service = app.state.playground
    service.index(service.scope())
    service.embedder_factory = lambda: (lambda texts, kind: [vector for _ in texts], "fake-embed")
    response = c.post(f"{API}/chat", json={"question": "계약?"})
    assert dict(_sse(response.text))["error"]["error"] == "embed_failed"
    trace = UsageTraces(app.state.api.store).listing(workspace=settings.workspace)[0]
    assert trace["status"] == "failed" and not llm.messages


def test_cli_watch_is_ndjson_and_failed_provider_has_nonzero_exit(client, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli

    c, settings, _, llm, _ = client
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(
        httpx, "request", lambda method, url, **kw: c.request(method, url, json=kw.get("json"))
    )
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kw: c.stream(method, url, json=kw.get("json"))
    )

    async def broken(messages):
        yield "partial"
        raise LLMError(502, "stream_truncated", "closed")

    llm.stream = broken
    result = CliRunner().invoke(cli.app, ["ops", "ask", "계약?", "--watch"])
    assert result.exit_code == 1
    events = [json.loads(line) for line in result.output.splitlines()]
    assert events[-1]["event"] == "receipt" and events[-1]["data"]["status"] == "failed"
    assert any(e["event"] == "error" for e in events)


def test_cli_preflight_failure_returns_its_receipt_id_as_ndjson(client, monkeypatch):
    from typer.testing import CliRunner

    from autoontology import cli

    c, settings, app, _, _ = client
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kw: c.stream(method, url, json=kw.get("json"))
    )

    def broken():
        raise LLMError(503, "provider_unavailable", "not logged in")

    app.state.playground.llm_factory = broken
    result = CliRunner().invoke(cli.app, ["ops", "ask", "계약?", "--watch"])
    assert result.exit_code == 1
    event = json.loads(result.output.strip())
    assert event["event"] == "error" and event["data"]["trace_id"]
    assert UsageTraces(app.state.api.store).get(event["data"]["trace_id"])["status"] == "failed"
