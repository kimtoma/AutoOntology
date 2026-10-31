# SPDX-License-Identifier: MIT
"""Bounded local/PG observations and a credential-owned persistent preparation loop."""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from autoontology.security.context import authorize
from autoontology.security.host_files import HostFiles
from autoontology.security.policy import AccessError
from autoontology.store.collection import Collection, fail
from autoontology.store.db import Store
from autoontology.store.operations import digest


def observe_source(store, source):
    authorize(store, "source.read", source_ids=[source.id])
    if source.kind == "database":
        result = postgres_observation(source)
    else:
        guard = HostFiles(store)
        if source.kind == "documents":
            from autoontology.engine.extract import documents_target
            from autoontology.unstructured.readers import discover_documents

            target, recursive = documents_target(source)
            guard.check(target)
            files = discover_documents(target, recursive=recursive)
        else:
            from autoontology.structured.pipeline import files_locator

            locator = files_locator(source)
            target = Path(locator.path)
            files = locator.files(store=store, source_id=source.id)
        if len(files) > 2000:
            fail(
                "Limit collection to 2,000 input files per source.",
                "collection_source_too_large",
                400,
            )
        root = target if target.is_dir() else target.parent
        items = []
        total = 0
        excluded = 0
        for path in sorted(files, key=str):
            try:
                guard.check(path)
            except AccessError as error:
                if error.code == "host_secret_path":
                    excluded += 1
                    continue
                raise
            size = path.stat().st_size
            total += size
            if total > 256 * 1024 * 1024:
                fail(
                    "Limit an observation to 256 MiB; split larger sources.",
                    "collection_source_too_large",
                    400,
                )
            items.append(
                [str(path.relative_to(root)), guard.sha256(path, source_id=source.id), size]
            )
        result = {
            "connector": "folder",
            "files": len(items),
            "bytes": total,
            "entries": items,
            "protected_excluded": excluded,
            "duplicate_contents": len(items) - len({v[1] for v in items}),
        }
        result["sha256"] = digest(result)
    authorize(store, "source.read", source_ids=[source.id])
    return result


def postgres_observation(source):
    import psycopg
    from psycopg import sql

    from autoontology.structured.pipeline import parse_database_url

    connection = parse_database_url(source.locator)
    if connection["type"] != "postgres":
        fail(
            "Scheduled database observation currently supports PostgreSQL.",
            "collection_connector_unsupported",
            400,
        )
    hashes = {}
    counts = {}
    with psycopg.connect(
        host=connection.get("host"),
        port=connection.get("port", 5432),
        user=connection.get("user"),
        password=connection.get("password"),
        dbname=connection["database"],
        connect_timeout=3,
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        columns = conn.execute(
            "SELECT table_schema,table_name,column_name,data_type,is_nullable,ordinal_position FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name,ordinal_position"
        ).fetchall()
        tables = conn.execute(
            "SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema,table_name LIMIT 101"
        ).fetchall()
        if len(tables) > 100:
            fail(
                "Limit scheduled PostgreSQL sources to 100 tables.",
                "collection_source_too_large",
                400,
            )
        for schema, table in tables:
            query = sql.SQL(
                'SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text COLLATE "C" LIMIT 10001'
            ).format(sql.Identifier(schema), sql.Identifier(table))
            rows = [r[0] for r in conn.execute(query)]
            if len(rows) > 10000:
                fail(
                    "A scheduled table exceeds 10,000 rows; use a bounded source.",
                    "collection_source_too_large",
                    400,
                )
            key = schema + "." + table
            hashes[key] = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
            counts[key] = len(rows)
    result = {
        "connector": "postgres",
        "schema_sha256": digest(columns),
        "data_sha256": digest(hashes),
        "table_hashes": hashes,
        "row_counts": counts,
        "tables": len(tables),
        "rows": sum(counts.values()),
    }
    result["sha256"] = digest(result)
    return result


class JobStore(Store):
    """Every participating SQLite write is fenced to the current job generation."""

    def __init__(self, store, collection, job):
        self.path = store.path
        self.collection, self.job = collection, job

    class Connection:
        def __init__(self, connection, check):
            self.connection, self.check = connection, check

        def __getattr__(self, key):
            if key in {"cursor", "executescript"}:
                raise AccessError(
                    "Use fenced collection SQL operations.", "collection_unfenced_write", 409
                )
            return getattr(self.connection, key)

        def fence(self, sql):
            command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
            if command not in {"SELECT", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK", "EXPLAIN"}:
                if not self.connection.in_transaction:
                    self.connection.execute("BEGIN IMMEDIATE")
                self.check(self.connection)

        def execute(self, sql, parameters=()):
            self.fence(sql)
            return self.connection.execute(sql, parameters)

        def executemany(self, sql, parameters):
            self.fence(sql)
            return self.connection.executemany(sql, parameters)

    @contextmanager
    def _conn(self):
        with super()._conn() as conn:
            yield self.Connection(
                conn,
                lambda current: self.collection.check(
                    self.job["id"], self.job["lease_id"], conn=current
                ),
            )

    @contextmanager
    def _immediate(self):
        with super()._immediate() as conn:
            self.collection.check(self.job["id"], self.job["lease_id"], conn=conn)
            yield self.Connection(
                conn,
                lambda current: self.collection.check(
                    self.job["id"], self.job["lease_id"], conn=current
                ),
            )

    def create_run(self, **kwargs):
        kwargs["params"] = {
            **kwargs.get("params", {}),
            "collection_job": self.job["id"],
            "collection_generation": self.job["generation"],
        }
        result = super().create_run(**kwargs)
        self.collection.attach_run(self.job["id"], self.job["lease_id"], result.id)
        return result


def completed_run(store, job):
    for run in store.list_runs(None, limit=1000):
        if (
            run.params.get("collection_job") == job["id"]
            and run.params.get("collection_generation") == job["generation"]
            and run.status == "succeeded"
        ):
            revision = next(
                (s.revision for s in store.list_snapshots(1000) if s.run_id == run.id), None
            )
            if revision is not None:
                return {
                    "mode": "prepare",
                    "run_id": run.id,
                    "candidate_revision": revision,
                    "recovered_completed_run": True,
                    "publication_performed": False,
                }
    return None


def cycle(store, settings, *, collection=None, builder=None, observer=observe_source):
    queue = collection or Collection(store)
    queue.pulse()
    scanned = []
    for schedule in queue.due():
        try:
            source = store.get_source(schedule["source_name"])
            if source is None:
                raise AccessError("Source is missing.", "collection_source_missing", 404)
            observation = observer(store, source)
        except Exception as error:  # errors are bounded codes, never source values or credentials
            code = getattr(error, "code", None) or "collection_input_unavailable"
            observation = {"connector_error": code}
        try:
            event = queue.observe(schedule["id"], observation)
        except AccessError:
            # A policy/source change cannot authorize a new observation write.
            continue
        if event:
            scanned.append(event["id"])
    job = queue.claim()
    if job is None:
        return {"scanned": scanned, "job": None}
    schedule = queue.get(job["schedule_id"])
    source = store.get_source(schedule["source_name"])
    stop = threading.Event()
    context = contextvars.copy_context()

    def heartbeat():
        while not stop.wait(20):
            try:
                queue.heartbeat(job["id"], job["lease_id"])
            except AccessError:
                break

    thread = threading.Thread(target=lambda: context.run(heartbeat), daemon=True)
    thread.start()
    try:
        fresh = observer(store, source)
        if digest(fresh) != digest(job["input"]):
            replacement = queue.observe(schedule["id"], fresh)
            return {
                "scanned": scanned,
                "job": {
                    "id": job["id"],
                    "status": "superseded",
                    "replacement_id": replacement["id"],
                },
            }
        if schedule["mode"] == "observe":
            result = {
                "mode": "observe",
                "input_sha256": digest(fresh),
                "connector": fresh["connector"],
                "files": fresh.get("files"),
                "rows": fresh.get("rows"),
                "publication_performed": False,
            }
        else:
            result = completed_run(store, job)
            if result is None:
                if builder is None:
                    from autoontology.engine.build import run_build

                    builder = run_build
                guarded = JobStore(store, queue, job)
                built = builder(
                    guarded, settings, sources=[source.name], resume=source.kind != "database"
                )
                if built.status != "succeeded":
                    raise AccessError(
                        "Candidate preparation did not complete.",
                        "collection_preparation_failed",
                        409,
                    )
                revision = next(
                    (s.revision for s in store.list_snapshots(1000) if s.run_id == built.run_id),
                    None,
                )
                if revision is None:
                    raise AccessError(
                        "Candidate receipt is missing.", "collection_preparation_failed", 409
                    )
                result = {
                    "mode": "prepare",
                    "run_id": built.run_id,
                    "candidate_revision": revision,
                    "recovered_completed_run": False,
                    "publication_performed": False,
                }
        if digest(observer(store, source)) != digest(fresh):
            raise AccessError(
                "Input changed during processing. Inspect the candidate before retry.",
                "collection_input_changed",
                409,
            )
        done = queue.finish(job["id"], job["lease_id"], result)
        return {"scanned": scanned, "job": done}
    except Exception as error:
        try:
            failed = queue.fail(
                job["id"],
                job["lease_id"],
                getattr(error, "code", None) or "collection_preparation_failed",
            )
        except AccessError:
            failed = {
                "id": job["id"],
                "status": "interrupted",
                "error_code": "collection_authority_or_lease_lost",
            }
        return {"scanned": scanned, "job": failed}
    finally:
        stop.set()
        thread.join(2)
