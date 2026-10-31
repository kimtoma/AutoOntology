# SPDX-License-Identifier: MIT
"""One approved query context and durable result serve UI, CLI and agents."""

import json

import anyio
import pytest

from autoontology.playground.service import Scope
from autoontology.playground.sql_guard import SqlError
from autoontology.playground.sql_service import SqlService, result_csv
from tests import test_sql_catalog as catalog_tests
from tests.test_playground import client as playground_client  # noqa: F401

frozen = catalog_tests.frozen


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Planner:
    provider, model = "fake", "sql-test"

    def __init__(self):
        self.query = "SELECT count(*) AS total FROM customers"
        self.calls = []
        self.on_call = None

    async def structured(self, messages, schema, name):
        self.calls.append(messages)
        if self.on_call:
            self.on_call()
        return {"sql": self.query, "clarification": None}


class Executor:
    def __init__(self):
        self.calls = []
        self.error = None

    async def run(self, catalog, connection, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return {"columns": ["total"], "rows": [[40]], "truncated": False, "limit": 500}


def attach(client):
    """A controlled SQL executor; real frozen-export checks live in catalog tests."""
    from autoontology.playground.sql_catalog import source_binding

    c, settings, app, _, _ = client
    source = app.state.api.store.get_source("hanbit")
    catalog = {
        "source": "hanbit",
        "database": "autoontology_src_hanbit",
        "dialect": "postgres",
        "binding": source_binding(source, settings),
        "tables": [{"schema": "public", "name": "customers", "columns": {"id": {"type": "text"}}}],
    }
    planner, executor = Planner(), Executor()
    sql = SqlService(
        app.state.api.store,
        lambda: settings,
        lambda: planner,
        executor=executor,
        catalog_loader=lambda *args: catalog,
    )
    app.state.sql_service = sql
    return sql, planner, executor


@pytest.fixture
def client(playground_client):  # noqa: F811
    return playground_client


def test_api_download_is_receipt_bound_and_prebody_disconnect_cancels(client):
    from fastapi import FastAPI

    from autoontology.web import playground_api
    from tests.test_playground import API, _asgi_post_then_leave, _sse

    c, _, app, _, _ = client
    sql, planner, executor = attach(client)
    events = dict(_sse(c.post(f"{API}/sql", json={"question": "고객 수"}).text))
    trace_id = events["done"]["trace_id"]
    response = c.get(f"{API}/traces/{trace_id}/result.csv")
    assert response.status_code == 200 and response.content == result_csv(sql.traces.get(trace_id))
    assert response.headers["cache-control"] == "private, no-store"
    assert len(planner.calls) == len(executor.calls) == 1
    bare = FastAPI()
    bare.include_router(playground_api.build_router(app.state.api, app))
    assert _asgi_post_then_leave(bare, f"{API}/sql", {"question": "다시"}) == [200]
    assert app.state.playground._busy == set()
    latest = sql.traces.listing(workspace=app.state.playground.scope().key)[0]
    assert latest["status"] == "cancelled"
    assert len(planner.calls) == 1
    assert c.get(f"{API}/traces/{latest['id']}/result.csv").status_code == 409


def test_cli_sql_output_matches_api_and_never_overwrites_or_repeats_failed_query(
    client, monkeypatch, tmp_path
):
    import httpx
    from typer.testing import CliRunner

    from autoontology import cli

    c, settings, _, _, _ = client
    sql, planner, executor = attach(client)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(
        httpx, "request", lambda method, url, **kw: c.request(method, url, json=kw.get("json"))
    )
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kw: c.stream(method, url, json=kw.get("json"))
    )
    path = tmp_path / "result.csv"
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ops", "sql", "고객 수", "--output", str(path)])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert path.read_bytes() == result_csv(sql.traces.get(record["receipt"]["id"]))
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(planner.calls) == len(executor.calls) == 1
    existing = runner.invoke(cli.app, ["ops", "sql", "다시", "--output", str(path)])
    assert existing.exit_code == 1 and json.loads(existing.output)["error"] == "output_exists"
    assert len(planner.calls) == 1
    planner.query = "DELETE FROM customers"
    failed = tmp_path / "failed.csv"
    result = runner.invoke(cli.app, ["ops", "sql", "삭제", "--output", str(failed), "--watch"])
    assert result.exit_code == 1 and not failed.exists()
    records = [json.loads(line) for line in result.output.splitlines()]
    assert records[-1]["data"]["status"] == "failed"
    assert len(executor.calls) == 1
    saved = tmp_path / "saved-again.csv"
    result = runner.invoke(
        cli.app, ["ops", "sql-result", record["receipt"]["id"], "--output", str(saved)]
    )
    assert result.exit_code == 0 and saved.read_bytes() == path.read_bytes()
    assert len(executor.calls) == 1 and len(planner.calls) == 2


@pytest.fixture
def service(frozen):
    store, settings, model, rev, source, _ = frozen
    planner, executor = Planner(), Executor()
    sql = SqlService(store, lambda: settings, lambda: planner, executor=executor)
    scope = Scope(model.name, rev, model, [source.name], [source])
    return sql, scope, planner, executor


async def query(service, scope, **body):
    prepared = await service.prepare(scope, {"question": "고객 수는?", **body})
    events = [e async for e in service.events(prepared)]
    return service.traces.get(prepared["trace_id"]), dict((e["event"], e["data"]) for e in events)


@pytest.mark.anyio
async def test_same_frozen_context_sql_rows_and_download_never_rerun(service):
    sql, scope, planner, executor = service
    receipt, events = await query(sql, scope)
    assert receipt["status"] == "succeeded" and events["done"]["status"] == "succeeded"
    assert events["meta"]["revision"] == scope.revision
    context = receipt["data"]["context"]
    assert context["messages"] == planner.calls[0]
    assert "Unapproved live label" not in json.dumps(context)
    assert "password" not in json.dumps(context)
    assert context["catalog"]["concepts"][0]["label"] == "Approved customer"
    assert context["data_time"] == "live_at_execution"
    assert receipt["data"]["outcome"]["rows"] == [[40]]
    assert result_csv(receipt).decode("utf-8-sig") == "total\r\n40\r\n"
    assert len(executor.calls) == len(planner.calls) == 1


@pytest.mark.anyio
async def test_unapproved_columns_and_source_change_after_generation_block_execution(service):
    sql, scope, planner, executor = service
    planner.query = "SELECT password FROM customers"
    receipt, events = await query(sql, scope)
    assert receipt["status"] == "failed" and events["error"]["error"] == "sql_not_allowed"
    assert receipt["data"]["context"]["plan"]["sql"] == planner.query
    assert executor.calls == [] and "done" not in events
    planner.query = "SELECT count(*) FROM customers"
    planner.on_call = lambda: sql.store.set_catalog_database_id(scope.structured[0].id, "new-db")
    receipt, events = await query(sql, scope)
    assert receipt["data"]["outcome"]["error"] == "sql_source_changed"
    assert executor.calls == []


@pytest.mark.anyio
async def test_failed_or_clarification_plan_never_has_download(service):
    sql, scope, planner, executor = service
    planner.query = None
    receipt, events = await query(sql, scope)
    assert events["error"]["error"] == "sql_clarification_required"
    assert receipt["data"]["context"]["plan"]["sql"] is None
    with pytest.raises(SqlError, match="successful"):
        result_csv(receipt)
    assert executor.calls == []


@pytest.mark.anyio
async def test_followups_only_use_successful_same_publication_catalog(service):
    sql, scope, planner, _ = service
    conv = "7f1c2b8e-0000-4000-8000-000000000001"
    first, _ = await query(sql, scope, conversation_id=conv)
    second, events = await query(sql, scope, conversation_id=conv, question="그 수는?")
    assert events["meta"]["history_turns"] == 1
    assert "고객 수는?" in planner.calls[-1][2]["content"]
    assert (
        second["data"]["request"]["conversation_id"]
        == first["data"]["request"]["conversation_id"]
        == conv
    )
    catalog = first["data"]["context"]["catalog"]
    scope.revision = sql.store.save_snapshot("changed", scope.model)
    # A new version must not consult an earlier SQL conversation, even with equal semantics.
    sql.catalog_loader = lambda *args: catalog
    _, events = await query(sql, scope, question="다시", conversation_id=conv)
    assert events["meta"]["history_turns"] == 0


@pytest.mark.anyio
async def test_cancellation_keeps_receipt_and_no_success(service):
    sql, scope, _, executor = service

    async def wait(*args):
        await anyio.sleep_forever()

    executor.run = wait
    prepared = await sql.prepare(scope, {"question": "고객 수는?"})
    with anyio.move_on_after(0.05):
        async for _ in sql.events(prepared):
            pass
    receipt = sql.traces.get(prepared["trace_id"])
    assert receipt["status"] == "cancelled"


def test_csv_quotes_cells_and_blocks_non_sql_or_running():
    receipt = {
        "kind": "sql",
        "status": "succeeded",
        "data": {
            "outcome": {
                "columns": ["이름", "메모"],
                "rows": [["한빛", 'a,b\n"c"']],
            }
        },
    }
    assert '"a,b\n""c"""' in result_csv(receipt).decode("utf-8-sig")
    receipt["status"] = "running"
    with pytest.raises(SqlError):
        result_csv(receipt)


def test_csv_download_keeps_existing_formula_protection_and_numeric_values():
    receipt = {
        "kind": "sql",
        "status": "succeeded",
        "data": {
            "outcome": {
                "columns": ["=label", "n"],
                "rows": [["=1+1", -5], ["@SUM(A1)", "-12.5"]],
            }
        },
    }
    text = result_csv(receipt).decode("utf-8-sig")
    assert text == "'=label,n\r\n'=1+1,-5\r\n'@SUM(A1),-12.5\r\n"


def test_old_upstream_receipt_download_unwraps_record_envelope_without_new_query():
    query_id = "4390611c-9cda-4d2c-875e-9fb8d61dcc22"
    receipt = {
        "kind": "sql",
        "status": "succeeded",
        "data": {
            "outcome": {
                "events": [
                    {
                        "type": "result",
                        "answer": {
                            "sql_columns": [query_id],
                            "sql_response_from_db": [{query_id: [json.dumps([{"count": 40}])]}],
                        },
                    }
                ]
            }
        },
    }
    assert result_csv(receipt).decode("utf-8-sig") == "count\r\n40\r\n"
