# SPDX-License-Identifier: MIT
"""Read-only connection setup, bounded output and cooperative SQL worker cleanup."""

import asyncio
import json
import time

import pytest

from autoontology.playground import sql_executor as executor
from autoontology.playground.sql_guard import SqlError
from tests import test_sql_publication as guard_tests

catalog = guard_tests.catalog


class Cursor:
    description = [("count",)]

    def __init__(self, rows=None):
        self.rows = list(rows if rows is not None else [(40,)])
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)

    def fetchmany(self, n):
        rows, self.rows = self.rows[:n], self.rows[n:]
        return rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Connection:
    def __init__(self):
        self.cur = Cursor()
        self.calls = []
        self.closed = False

    def execute(self, sql):
        self.calls.append(sql)

    def cursor(self, **kwargs):
        return self.cur

    def start_transaction(self, **kwargs):
        self.calls.append(kwargs)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def target(catalog):
    return {
        "type": catalog["dialect"],
        "host": "db.test",
        "user": "reader",
        "password": "do-not-log-credential",
        "database": catalog["database"],
        "account": "account",
        "warehouse": "warehouse",
    }


def test_postgres_transaction_and_session_limits_are_set(catalog, monkeypatch):
    import psycopg

    conn = Connection()
    seen = []
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: seen.append(kwargs) or conn)
    result = executor.execute_read(catalog, target(catalog), "SELECT count(*) FROM customers")
    assert result["rows"] == [[40]] and conn.closed
    assert conn.calls == ["SET TRANSACTION READ ONLY"]
    assert (
        "default_transaction_read_only=on" in seen[0]["options"]
        and "search_path=pg_catalog" in seen[0]["options"]
    )
    assert "statement_timeout=10000" in seen[0]["options"] and seen[0]["autocommit"] is False
    assert "LIMIT 501" in conn.cur.calls[0]


def test_rejected_statement_never_opens_a_database(catalog, monkeypatch):
    import psycopg

    monkeypatch.setattr(
        psycopg, "connect", lambda **kwargs: pytest.fail("no connection for unsafe SQL")
    )
    with pytest.raises(SqlError):
        executor.execute_read(catalog, target(catalog), "DELETE FROM customers")


@pytest.mark.parametrize("dialect", ["mysql", "snowflake"])
def test_external_adapters_keep_scope_and_deadline(catalog, monkeypatch, dialect):
    import importlib

    driver = importlib.import_module(dialect + ".connector")
    conn = Connection()
    seen = []
    monkeypatch.setattr(driver, "connect", lambda **kwargs: seen.append(kwargs) or conn)
    catalog["dialect"] = dialect
    if dialect == "snowflake":
        catalog["database"] = catalog["database"].upper()
        for table in catalog["tables"]:
            table["schema"] = table["schema"].upper()
            table["name"] = table["name"].upper()
            table["columns"] = {k.upper(): v for k, v in table["columns"].items()}
    result = executor.execute_read(catalog, target(catalog), "SELECT count(*) FROM customers")
    assert result["rows"] == [[40]] and conn.closed
    if dialect == "mysql":
        assert {"readonly": True} in conn.calls and "max_execution_time" in conn.cur.calls[0]
    else:
        assert seen[0]["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 10


def test_result_rows_and_bytes_are_bounded(monkeypatch):
    rows = executor.collect(Cursor([(i,) for i in range(800)]))
    assert len(rows["rows"]) == 500 and rows["truncated"]
    monkeypatch.setattr(executor, "MAX_RESULT_BYTES", 70000)
    rows = executor.collect(Cursor([("x" * 1000,) for _ in range(100)]))
    assert (
        len(rows["rows"]) < 10 and rows["truncated"] and rows["truncation_reason"] == "byte_limit"
    )


def finished_worker(pipe, *args):
    pipe.send_bytes(
        json.dumps({"result": {"rows": [[40]], "columns": ["count"], "truncated": False}}).encode()
    )
    pipe.close()


def hanging_worker(pipe, *args):
    time.sleep(30)


@pytest.mark.asyncio
async def test_query_worker_returns_a_bounded_result_and_releases_slots(catalog):
    runner = executor.SqlExecutor(worker_target=finished_worker)
    assert (await runner.run(catalog, target(catalog), "SELECT count(*) FROM customers"))[
        "rows"
    ] == [[40]]
    assert runner.slots.acquire(blocking=False) and runner.slots.acquire(blocking=False)
    runner.slots.release()
    runner.slots.release()


@pytest.mark.asyncio
async def test_timeout_and_cancel_stop_the_worker_and_allow_another_query(catalog):
    runner = executor.SqlExecutor(timeout=0.3, worker_target=hanging_worker)
    with pytest.raises(SqlError) as caught:
        await runner.run(catalog, target(catalog), "SELECT count(*) FROM customers")
    assert caught.value.code == "sql_timeout"
    runner.timeout = 20
    task = asyncio.create_task(
        runner.run(catalog, target(catalog), "SELECT count(*) FROM customers")
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runner.worker_target = finished_worker
    assert (await runner.run(catalog, target(catalog), "SELECT count(*) FROM customers"))[
        "rows"
    ] == [[40]]


def test_driver_failure_does_not_copy_secrets_to_receipts(catalog, monkeypatch):
    class Pipe:
        def send_bytes(self, value):
            self.value = value

        def close(self):
            pass

    def broken(*args):
        raise RuntimeError("do-not-log-credential")

    monkeypatch.setattr(executor, "execute_read", broken)
    pipe = Pipe()
    executor.worker(pipe, catalog, target(catalog), "SELECT count(*) FROM customers")
    assert "do-not-log-credential" not in pipe.value.decode()
    assert json.loads(pipe.value)["error"] == "sql_execution_failed"


@pytest.mark.asyncio
async def test_worker_setup_failure_does_not_leak_capacity(catalog, monkeypatch):
    def broken(*args):
        raise OSError("unavailable resource")

    monkeypatch.setattr(executor.multiprocessing, "get_context", broken)
    runner = executor.SqlExecutor()
    with pytest.raises(SqlError):
        await runner.run(catalog, target(catalog), "SELECT count(*) FROM customers")
    assert runner.slots.acquire(blocking=False) and runner.slots.acquire(blocking=False)
    runner.slots.release()
    runner.slots.release()
