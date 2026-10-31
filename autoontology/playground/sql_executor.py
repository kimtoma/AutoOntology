# SPDX-License-Identifier: MIT
"""Bounded per-query DB workers; cancellation closes the query's own connection."""

from __future__ import annotations

import json
import math
import multiprocessing
import threading
import time
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal

import anyio

from autoontology.playground.sql_guard import (
    MAX_RESULT_BYTES,
    MAX_ROWS,
    STATEMENT_SECONDS,
    WORKER_SECONDS,
    SqlError,
    validate_sql,
)


def cell(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, datetime, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        import base64

        return "base64:" + base64.b64encode(value).decode()
    if isinstance(value, (dict, list, tuple)):
        return (
            {str(k): cell(v) for k, v in value.items()}
            if isinstance(value, dict)
            else [cell(v) for v in value]
        )
    return str(value)


def collect(cursor):
    columns = [str(c[0]) for c in cursor.description or []]
    rows = []
    size = len(json.dumps(columns).encode())
    truncated = False
    reason = None
    while True:
        batch = cursor.fetchmany(min(25, MAX_ROWS + 1 - len(rows)))
        if not batch:
            break
        for row in batch:
            if len(rows) == MAX_ROWS:
                truncated = True
                reason = "row_limit"
                break
            converted = [cell(v) for v in row]
            size += len(json.dumps(converted, ensure_ascii=False).encode())
            if size > MAX_RESULT_BYTES - 65536:
                if not rows:
                    raise SqlError(
                        "A result cell exceeds the download limit. Refine the query.",
                        "sql_result_too_large",
                        413,
                    )
                truncated = True
                reason = "byte_limit"
                break
            rows.append(converted)
        if truncated:
            break
    return {
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "limit": MAX_ROWS,
        "truncation_reason": reason,
    }


def execute_read(catalog, connection, query):
    if (
        connection.get("database") != catalog["database"]
        or connection.get("type") != catalog["dialect"]
    ):
        raise SqlError(
            "The execution connection does not match the frozen catalog.", "sql_source_changed"
        )
    query = validate_sql(query, catalog)
    # The outer limit bounds driver fetches without altering aggregate semantics.
    alias = "`ao_result`" if catalog["dialect"] == "mysql" else '"ao_result"'
    bounded = f"SELECT * FROM ({query}) AS {alias} LIMIT {MAX_ROWS + 1}"
    dialect = catalog["dialect"]
    if dialect == "postgres":
        import psycopg

        with psycopg.connect(
            host=connection["host"],
            port=int(connection.get("port") or 5432),
            user=connection["user"],
            password=connection.get("password") or None,
            dbname=connection["database"],
            connect_timeout=5,
            autocommit=False,
            options=f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_SECONDS * 1000} -c lock_timeout=2000 -c search_path=pg_catalog",
        ) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor(name="autoontology_read") as cursor:
                cursor.execute(bounded)
                return collect(cursor)
    if dialect == "mysql":
        import mysql.connector

        conn = mysql.connector.connect(
            host=connection["host"],
            port=int(connection.get("port") or 3306),
            user=connection["user"],
            password=connection.get("password") or None,
            database=connection["database"],
            connection_timeout=5,
            read_timeout=STATEMENT_SECONDS + 2,
            write_timeout=5,
            autocommit=False,
        )
        cursor = conn.cursor()
        try:
            try:
                cursor.execute(f"SET SESSION max_execution_time = {STATEMENT_SECONDS * 1000}")
            except mysql.connector.Error:
                try:
                    cursor.execute(f"SET SESSION max_statement_time = {STATEMENT_SECONDS}")
                except mysql.connector.Error:
                    raise SqlError(
                        "This MySQL server cannot enforce the required query deadline.",
                        "sql_limits_unavailable",
                        503,
                    ) from None
            conn.start_transaction(readonly=True)
            cursor.execute(bounded)
            return collect(cursor)
        finally:
            conn.close()
            try:
                cursor.close()
            except mysql.connector.Error:
                pass
    if dialect == "snowflake":
        import snowflake.connector

        with snowflake.connector.connect(
            account=connection["account"],
            user=connection["user"],
            password=connection["password"],
            database=connection["database"],
            warehouse=connection["warehouse"],
            login_timeout=5,
            network_timeout=STATEMENT_SECONDS + 2,
            session_parameters={
                "STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_SECONDS,
                "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": STATEMENT_SECONDS,
                "QUERY_TAG": "autoontology_publication_read",
            },
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(bounded)
                return collect(cursor)
    raise SqlError("The source has no managed SQL adapter.", "sql_dialect_unavailable")


def worker(pipe, catalog, connection, query):
    try:
        result = {"result": execute_read(catalog, connection, query)}
    except SqlError as exc:
        result = {"error": exc.code, "detail": str(exc), "status": exc.status}
    except BaseException as exc:
        state = str(getattr(exc, "sqlstate", "") or "")
        code = (
            "sql_source_changed"
            if state in ("42703", "42P01", "42S02", "42S22", "42804")
            or getattr(exc, "errno", None) in (1054, 1146)
            else "sql_execution_failed"
        )
        result = {
            "error": code,
            "detail": "The source query could not complete. Check source availability, schema and read permissions; no provider error body is returned.",
            "status": 503,
        }
    try:
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_RESULT_BYTES:
            payload = json.dumps(
                {
                    "error": "sql_result_too_large",
                    "detail": "Refine the query to reduce its output.",
                    "status": 413,
                }
            ).encode()
        pipe.send_bytes(payload)
    finally:
        pipe.close()


class SqlExecutor:
    def __init__(self, *, timeout=WORKER_SECONDS, worker_target=worker):
        self.timeout = timeout
        self.worker_target = worker_target
        self.slots = threading.BoundedSemaphore(2)

    async def run(self, catalog, connection, query):
        if not self.slots.acquire(blocking=False):
            raise SqlError(
                "Other data queries are still running. Retry after they finish.", "sql_busy", 429
            )
        parent = child = process = None
        try:
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(
                target=self.worker_target, args=(child, catalog, connection, query), daemon=True
            )
            process.start()
            child.close()
            deadline = time.monotonic() + self.timeout
            while not parent.poll():
                if not process.is_alive():
                    raise SqlError(
                        "The data query worker stopped. Retry or check the source.",
                        "sql_worker_failed",
                        503,
                    )
                if time.monotonic() > deadline:
                    raise SqlError(
                        "The data query exceeded its deadline. Refine it before retrying.",
                        "sql_timeout",
                        504,
                    )
                await anyio.sleep(0.025)
            try:
                result = json.loads(parent.recv_bytes(MAX_RESULT_BYTES))
            except (EOFError, OSError, ValueError):
                raise SqlError(
                    "The query worker returned an invalid result.", "sql_worker_failed", 503
                ) from None
            if "error" in result:
                raise SqlError(result["detail"], result["error"], result["status"])
            return result["result"]
        except SqlError:
            raise
        except Exception:
            raise SqlError(
                "The data query worker could not start or return a result. Retry after checking local resources.",
                "sql_worker_failed",
                503,
            ) from None
        finally:
            with anyio.CancelScope(shield=True):
                if process is not None and process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    await anyio.to_thread.run_sync(process.join, 1.0)
                    if process.is_alive():
                        process.kill()
                        await anyio.to_thread.run_sync(process.join, 1.0)
                    process.close()
                if parent is not None:
                    parent.close()
                if child is not None:
                    child.close()
                self.slots.release()
