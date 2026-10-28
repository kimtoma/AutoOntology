# SPDX-License-Identifier: MIT
"""Structured-source steps and the shared job machinery (logging, leases, stale runs,
the step executor).

``autoontology build`` (:mod:`autoontology.engine.build`) runs the step functions here
(``load`` -> ``register`` -> ``ingest`` -> ``compile``) for every structured source inside
its one run. :func:`run_structured` is the single-source runner of the same steps (plus
the per-source upstream ``export``), kept for library use and the step tests; every step
writes its ``steps`` row when it starts and when it ends.

Ingest and compile run upstream code **in-process and synchronously** so completion and
exceptions are known. ``auto_ontology`` freezes model/DB settings at import time, so all
upstream imports happen inside functions, after :func:`prepare_upstream_env`.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import Settings, postgres_env
from autoontology.engine.client import BackendClient
from autoontology.store.db import Lease, LeaseLost, Run, Source, SourceBusy, Store, now_iso
from autoontology.structured import pipeline

logger = logging.getLogger("autoontology.jobs")

STEP_ORDER: tuple[str, ...] = ("load", "register_connection", "ingest", "compile", "export")
BUILD_KIND = "build"
INGEST_KIND = "ingest"
SEMANTIC_FK = "SEMANTIC_FK"


class JobError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        step: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.step = step
        self.log_path = log_path


@dataclass
class RunResult:
    run_id: str
    status: str
    steps: dict[str, str] = field(default_factory=dict)
    export_dir: Path | None = None
    details: dict[str, dict[str, Any]] = field(default_factory=dict)


# --- environment and logging ---------------------------------------------------------


_ENV_LOCK = threading.Lock()
_APPLIED_ENV: dict[str, str] | None = None


def upstream_env(settings: Settings) -> dict[str, str]:
    """What :func:`prepare_upstream_env` puts into ``os.environ`` (beyond ``.env``)."""
    from autoontology.config import expand_model_profile

    return {
        **expand_model_profile(settings),
        **postgres_env(settings),
        "INGESTION_SERVICE_URL": settings.service_url,
    }


def apply_model_env(settings: Settings) -> None:
    """Make os.environ what upstream will read at import time (call before importing it)."""
    import warnings

    from autoontology.config import expand_model_profile, load_env

    load_env()
    os.environ.update(expand_model_profile(settings))
    # ChatNVIDIA (only for model names without the openai/ prefix) warns for every model
    # outside NVIDIA's catalog; AutoOntology's openai/ names use ChatOpenAI instead.
    warnings.filterwarnings("ignore", message=".*not known to support structured output.*")


def prepare_upstream_env(settings: Settings) -> None:
    """Make ``os.environ`` what the upstream backend child gets (``supervisor.child_env``).

    Serialised by a process-wide lock (shim threads and the build share the process) and
    idempotent: the env is written once per process. Upstream freezes it at import time,
    so a later call with *different* settings re-applies it but logs a warning.
    """
    global _APPLIED_ENV
    wanted = upstream_env(settings)
    with _ENV_LOCK:
        if _APPLIED_ENV == wanted:
            return
        if _APPLIED_ENV is not None:
            logger.warning(
                "upstream env re-applied with different settings; modules already "
                "imported keep the values they read at import time"
            )
        from autoontology.config import DERIVED_ENV_VAR, derived_env_keys, expand_model_profile

        # mark the expansion as derived: a later Settings.from_env() in this process must
        # not take it for user settings
        models = expand_model_profile(settings)
        derived = derived_env_keys() | {k for k, v in models.items() if os.environ.get(k) != v}
        apply_model_env(settings)
        os.environ.update(wanted)
        os.environ[DERIVED_ENV_VAR] = ",".join(sorted(derived))
        _APPLIED_ENV = wanted


def jobs_log_path(settings: Settings) -> Path:
    return settings.logs_dir / "jobs.log"


_FILE_HANDLER_ATTR = "_autoontology_jobs_log"


def setup_job_logging(settings: Settings, *, console: bool = False) -> Path:
    """Root FileHandler on ``<home>/logs/jobs.log`` (upstream logs included), once per path.

    With *console*, AutoOntology's own job messages also go to stderr.
    """
    path = jobs_log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not any(getattr(h, _FILE_HANDLER_ATTR, None) == str(path) for h in root.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
        )
        setattr(handler, _FILE_HANDLER_ATTR, str(path))
        root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    if console and not any(getattr(h, "_autoontology_console", False) for h in logger.handlers):
        stream = _StderrHandler()
        stream.setFormatter(_ConsoleFormatter("%(message)s"))
        stream._autoontology_console = True  # type: ignore[attr-defined]
        logger.addHandler(stream)
    return path


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is at emit time, not the stream current when the
    handler was made (a CLI runner swaps stderr per invocation; a stale, closed stream
    turns every later message into a logging error on the new one)."""

    @property  # type: ignore[override]
    def stream(self):
        import sys

        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


class _ConsoleFormatter(logging.Formatter):
    """Message only: tracebacks go to jobs.log, not the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        saved = record.exc_info, record.exc_text, record.stack_info
        record.exc_info = record.exc_text = record.stack_info = None
        try:
            return super().format(record)
        finally:
            record.exc_info, record.exc_text, record.stack_info = saved


def short_error(exc: BaseException, limit: int = 600) -> str:
    """``Type: first line`` of an exception, capped (driver errors carry whole SQL dumps)."""
    first = (str(exc).strip().splitlines() or [""])[0]
    text = f"{type(exc).__name__}: {first}" if first else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 3] + "..."


def uses_llm_proxy(settings: Settings) -> bool:
    """True when upstream's chat endpoint is the service's own /v1 passthrough."""
    from autoontology.config import expand_model_profile, resolve_triplet_field

    models = expand_model_profile(settings)
    return any(
        resolve_triplet_field(models, p, "ENDPOINT").rstrip("/") == settings.llm_proxy_endpoint
        for p in ("REASONING", "NON_REASONING")
    )


def service_alive(settings: Settings) -> bool:
    import httpx

    try:
        return httpx.get(f"{settings.service_url}/health/live", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def require_llm_proxy(settings: Settings, *, log_path: Path | None = None) -> None:
    """In-process compile sends every chat call to the service's /v1: without it upstream
    silently falls back (placeholder Terms, no FKs) while compile still reports ok."""
    if uses_llm_proxy(settings) and not service_alive(settings):
        raise JobError(
            f"AutoOntology service not reachable at {settings.service_url}: the "
            f"{settings.chat_provider} chat provider goes through its /v1 router. "
            "Start it with `uv run autoontology up`",
            log_path=log_path,
        )


# --- upstream calls (module-level so tests can monkeypatch them) -----------------------

#: Upstream creates its pgvector tables lazily with a hard-coded ``vector_size`` of 2048
#: (``vdb/postgres.py:174``, ``vdb/__init__.py:get_vdb`` never passes one), which only
#: fits 2048-dim models. Other models (bge-m3: 1024) fail on insert, so AutoOntology creates
#: the tables first, through upstream's own DDL, sized to what the embedder emits.
VDB_COLLECTIONS: tuple[str, ...] = ("data_objects_layer", "semantic_layer")
#: pg_advisory_lock key for the vdb.* table check/create ("OntoVecT" as int64).
VECTOR_TABLES_LOCK_KEY = 0x4F6E746F56656354


def embedding_dimension(settings: Settings) -> int:
    prepare_upstream_env(settings)
    from auto_ontology.utils.embedding import embed_texts, get_embed_params

    vectors = embed_texts(["dimension probe"], get_embed_params(), "query")
    if not vectors or not vectors[0]:
        raise RuntimeError("embedding endpoint returned no vector (check EMBED_* / profile)")
    return len(vectors[0])


def require_vdb_store_api(vdb_cls: type) -> None:
    """Fail clearly if upstream's private ``PostgresVDB._get_store`` (our DDL hook) is gone."""
    if not callable(getattr(vdb_cls, "_get_store", None)):
        raise RuntimeError(
            f"{vdb_cls.__module__}.{vdb_cls.__qualname__} has no _get_store(): the vendored "
            "auto-ontology changed. AutoOntology uses that private method to create the vdb.* "
            "tables at the embedder's dimension (autoontology/engine/jobs.py "
            "ensure_vector_tables); port it to the new upstream API before ingesting."
        )


def vector_table_state(settings: Settings) -> dict[str, tuple[str, int] | None]:
    """``{collection: (embedding column type, row count) | None if absent}``."""
    from autoontology.engine import pg

    prepare_upstream_env(settings)
    from auto_ontology.vdb import VDB_SCHEMA

    state: dict[str, tuple[str, int] | None] = {}
    with pg.connect(settings) as conn:
        for collection in VDB_COLLECTIONS:
            row = conn.execute(
                "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = "
                "c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
                "AND a.attname = 'embedding'",
                (VDB_SCHEMA, collection),
            ).fetchone()
            if row is None:
                state[collection] = None
                continue
            count = conn.execute(f'SELECT count(*) FROM "{VDB_SCHEMA}"."{collection}"').fetchone()[
                0
            ]
            state[collection] = (row[0], count)
    return state


def ensure_vector_tables(settings: Settings) -> dict[str, Any]:
    """Make ``vdb.<collection>`` exist with the embedder's dimension (see VDB_COLLECTIONS)."""
    from autoontology.engine import pg

    prepare_upstream_env(settings)
    from auto_ontology.vdb.postgres import PostgresVDB

    require_vdb_store_api(PostgresVDB)  # before touching any table
    dim = embedding_dimension(settings)
    # The build process and the service's shim threads may both get here. A session-level
    # advisory lock (held on its own connection, since the check, the DROP and upstream's
    # DDL each use their own connection) serialises check -> drop -> create.
    with pg.connect(settings) as lock_conn:
        acquire_advisory_lock(lock_conn, VECTOR_TABLES_LOCK_KEY, "vdb vector tables")
        try:
            return _ensure_vector_tables_locked(settings, dim)
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (VECTOR_TABLES_LOCK_KEY,))


LOCK_POLL_S = 0.5


def lock_timeout_s() -> float:
    raw = os.environ.get("AUTOONTOLOGY_LOCK_TIMEOUT_S", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError as exc:
        raise JobError(f"AUTOONTOLOGY_LOCK_TIMEOUT_S must be a number, got {raw!r}") from exc


def acquire_advisory_lock(
    conn: Any, key: int, label: str, *, timeout: float | None = None, poll: float | None = None
) -> None:
    """``pg_try_advisory_lock`` polled until *timeout* (``AUTOONTOLOGY_LOCK_TIMEOUT_S``)."""
    timeout = lock_timeout_s() if timeout is None else timeout
    poll = LOCK_POLL_S if poll is None else poll
    deadline = time.monotonic() + timeout
    while not conn.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
        if time.monotonic() >= deadline:
            raise JobError(
                f"could not take the Postgres advisory lock {key} ({label}) within "
                f"{timeout:.0f}s: another build or ingest is holding it (see pg_locks "
                "where locktype = 'advisory'); retry later or raise AUTOONTOLOGY_LOCK_TIMEOUT_S"
            )
        time.sleep(poll)


def _ensure_vector_tables_locked(settings: Settings, dim: int) -> dict[str, Any]:
    from auto_ontology.infra.postgres import get_postgres_connection_string
    from auto_ontology.vdb import VDB_SCHEMA
    from auto_ontology.vdb.postgres import PostgresVDB

    from autoontology.engine import pg

    actions: dict[str, str] = {}
    for collection, found in vector_table_state(settings).items():
        if found is not None:
            current, count = found
            if current.endswith(f"({dim})"):
                actions[collection] = f"ok {current}"
                continue
            if count:
                raise RuntimeError(
                    f"{VDB_SCHEMA}.{collection} holds {count} {current} vectors but the "
                    f"embedding model emits {dim} dimensions; the catalog was embedded "
                    "with another model. Re-embedding needs an empty table."
                )
            with pg.connect(settings) as conn:
                conn.execute(f'DROP TABLE "{VDB_SCHEMA}"."{collection}"')
            actions[collection] = f"recreated {current} -> {dim}"
        else:
            actions[collection] = f"created ({dim})"
        vdb = PostgresVDB(
            connection_string=get_postgres_connection_string(),
            collection_name=collection,
            schema_name=VDB_SCHEMA,
            vector_size=dim,
        )
        try:
            vdb._get_store()  # upstream DDL: init_vectorstore_table + halfvec HNSW index
        finally:
            vdb.close()
    return {"embedding_dim": dim, "vector_tables": actions}


def upstream_ingest(database_name: str, settings: Settings) -> dict[str, Any]:
    """Synchronous equivalent of upstream ``trigger_ingest``'s worker thread.

    ``auto_ontology/ingestion_service/ingest.py:169-206`` runs, inside a daemon thread
    (a closure, not importable): invalidate the connector cache, pick the catalog
    connector whose ``database_name`` matches case-insensitively, ``run_ingest`` it
    (catalog extract -> PII -> embeddings -> pgvector), then ``replay_rules("ingest")``.
    We call the same public functions here so exceptions propagate instead of being
    logged and swallowed.
    """
    prepare_upstream_env(settings)
    from auto_ontology.connectors.registry import get_connectors, invalidate_connectors_cache
    from auto_ontology.ingestion_service.ingest import run_ingest
    from auto_ontology.ingestion_service.rules import replay_rules

    invalidate_connectors_cache()
    key = database_name.strip().casefold()
    connector = next(
        (c for c in get_connectors() if str(getattr(c, "database_name", "")).casefold() == key),
        None,
    )
    if connector is None:
        raise RuntimeError(
            f"no upstream connector for database {database_name!r}: the connection is not "
            "registered in the catalog (run the register_connection step)"
        )
    tables = len(connector.get_tables())
    run_ingest(connector)
    replay_rules("ingest")
    return {"tables": tables}


def upstream_compile(database_name: str, settings: Settings) -> dict[str, Any]:
    """``run_semantic_compilation`` plus the scheduler's post-compile passes.

    Mirrors ``ingestion_service/semantic_scheduler.py:200-245`` for one database
    (PII propagation then rule replay), minus the feature-flag gate and history rows.
    """
    prepare_upstream_env(settings)
    from auto_ontology.ingestion_service.pii import run_pii_propagation
    from auto_ontology.ingestion_service.rules import replay_rules
    from auto_ontology.semantic.compile import run_semantic_compilation

    tables = run_semantic_compilation(database_name)
    run_pii_propagation(database_name)
    replay_rules("compile")
    return {"tables_compiled": tables}


# --- step implementations -------------------------------------------------------------


@dataclass
class _Ctx:
    settings: Settings
    store: Store
    source: Source
    run: Run
    client: BackendClient
    database_name: str

    @property
    def export_dir(self) -> Path:
        return self.settings.exports_dir / self.source.name / self.run.id

    def refresh_source(self) -> None:
        self.source = self.store.get_source_by_id(self.source.id) or self.source


def upstream_reset_semantic(
    database_name: str,
    settings: Settings,
    *,
    orphan_candidates: list[str] | None = None,
    stale_table_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Make every table of one database recompile: upstream's scoped reset
    (``dal/reset.py:delete_semantic_layer``: Terms, column/SQL attributes and embeddings;
    the catalog stays), then detach the tables from Terms shared with other databases
    (which that reset keeps), so compile -- which only visits tables without a Term --
    recompiles all of them (:mod:`autoontology.engine.upstream_semantic`). Never called with a
    blank name: ``None`` would wipe every database."""
    from autoontology.engine.upstream_semantic import reset_database_semantics

    if not database_name.strip():
        raise ValueError("refusing a semantic reset without a database name")
    return reset_database_semantics(
        settings,
        database_name,
        orphan_candidates=orphan_candidates or [],
        stale_table_ids=stale_table_ids or [],
    )


def catalog_snapshot(database_name: str, settings: Settings) -> dict[str, Any]:
    """The ingested catalog of one database (tables -> column types, linked Terms)."""
    from autoontology.engine.upstream_semantic import catalog_snapshot as snap

    return snap(settings, database_name)


def _invalidate_semantic(
    ctx: _Ctx, changes: dict[str, list[str]], invalidated: dict[str, Any]
) -> None:
    """Called by the loader *before* it rewrites tables whose schema changed or drops tables
    no longer in the input. Upstream keeps a table's Terms/attributes across a re-ingest
    and compiles only tables without a Term, so they would describe the old schema while
    the build reports success. Reset the database's semantic layer instead (upstream's
    scoped ``delete_semantic_layer``: per-table invalidation has no public upstream API);
    compile then recompiles every table. Done before the write: if the reset fails the
    database is untouched and the next build retries."""
    touched = changes["changed"] + changes["removed"]
    if ctx.client.database_id_by_name(ctx.database_name) is None:
        invalidated.update(semantic_reset=False, reason="not ingested yet")
        return
    logger.warning(
        "  schema changed for %s (changed: %s; removed: %s): resetting the "
        "semantic layer of %s so compile rebuilds it; the build's align steps then "
        "realign documents sources to %s",
        ctx.source.name,
        ", ".join(changes["changed"]) or "-",
        ", ".join(changes["removed"]) or "-",
        ctx.database_name,
        ctx.source.name,
    )
    ensure_vector_tables(ctx.settings)
    result = upstream_reset_semantic(ctx.database_name, ctx.settings)
    invalidated.update(semantic_reset=True, tables=touched, **result)


def _step_load(ctx: _Ctx) -> dict[str, Any]:
    from autoontology.structured.loaders import load_files_to_postgres

    files = pipeline.files_locator(ctx.source).files()
    invalidated: dict[str, Any] = {}
    report = load_files_to_postgres(
        files,
        ctx.database_name,
        ctx.settings,
        previous=ctx.store.get_file_manifest(ctx.source.id),
        on_changes=lambda changes: _invalidate_semantic(ctx, changes, invalidated),
    )
    ctx.store.set_file_manifest(ctx.source.id, report.manifest)
    for table in report.tables:
        logger.info("  loaded %s: %d rows, %d columns", table.name, table.rows, len(table.columns))
    for table in report.dropped_tables:
        logger.info("  dropped %s: no longer produced by the input files", table)
    for warning in report.warnings:
        logger.info("  warning: %s", warning)
    return {
        "report": report.to_dict(),
        "tables": len(report.tables),
        "rows": report.total_rows,
        "schema_changes": report.schema_changes,
        "dropped_tables": report.dropped_tables,
        "invalidated": invalidated,
    }


def _step_register(ctx: _Ctx) -> dict[str, Any]:
    desired = pipeline.connection_for(ctx.source, ctx.settings)
    if ctx.client.connection_exists(ctx.database_name):
        # Always PUT: upstream only lists connections credential-free, so a changed
        # password cannot be detected by comparison. PUT re-ingests only when the schema
        # allowlist changed (server/connections/router.py update_connection).
        ctx.client.update_connection(ctx.database_name, desired)
        logger.info("  connection %s already registered; updated", ctx.database_name)
        return {"action": "updated"}
    ctx.client.create_connection(desired)
    return {"action": "created"}


def _ensure_database_id(ctx: _Ctx) -> str:
    db_id = ctx.client.database_id_by_name(ctx.database_name)
    if db_id is None:
        raise RuntimeError(
            f"database {ctx.database_name!r} is not in the upstream catalog "
            "(GET /api/datasources/dbs); did ingest succeed?"
        )
    if ctx.source.id and db_id != ctx.source.catalog_database_id:
        ctx.store.set_catalog_database_id(ctx.source.id, db_id)
        ctx.refresh_source()
    return db_id


def _step_ingest(ctx: _Ctx) -> dict[str, Any]:
    vectors = ensure_vector_tables(ctx.settings)
    # a database source's schema lives in the external database: the only place a change
    # shows is the catalog the ingest rewrites. The baseline (schema the semantic layer was
    # compiled from) is persisted *before* the ingest can change the catalog, and the
    # invalidation is persisted as pending until the reset succeeded: an ingest that fails
    # after upstream committed the new catalog, or a failed reset, is caught by the retry.
    watch = ctx.source.kind == "database" and bool(ctx.source.id)
    baseline = None
    if watch:
        baseline = ctx.store.get_semantic_baseline(ctx.source.id)
        if baseline is None:
            baseline = catalog_snapshot(ctx.database_name, ctx.settings)
            if baseline:
                ctx.store.set_semantic_baseline(ctx.source.id, baseline)
    detail = upstream_ingest(ctx.database_name, ctx.settings)
    detail.update(vectors)
    if watch:
        _invalidate_database_source(ctx, baseline or {}, detail)
    detail["catalog_database_id"] = _ensure_database_id(ctx)
    return detail


def _invalidate_database_source(
    ctx: _Ctx, baseline: dict[str, Any], detail: dict[str, Any]
) -> None:
    from autoontology.structured.loaders import diff_manifest

    after = catalog_snapshot(ctx.database_name, ctx.settings)
    changes = (
        diff_manifest(baseline, after) if baseline else {"added": [], "removed": [], "changed": []}
    )
    detail["schema_changes"] = changes
    pending = ctx.store.get_pending_invalidation(ctx.source.id) or {}
    removed = sorted({*changes["removed"], *pending.get("removed", [])})
    changed = sorted({*changes["changed"], *pending.get("changed", [])})
    if not removed and not changed:
        if after and not pending:
            ctx.store.set_semantic_baseline(ctx.source.id, after)
        return
    orphans = sorted(
        {
            *pending.get("orphan_terms", []),
            *(t for name in changes["removed"] for t in baseline.get(name, {}).get("terms", [])),
        }
    )
    stale = sorted(
        {
            *pending.get("stale_table_ids", []),
            *(
                baseline[name]["id"]
                for name in changes["removed"]
                if baseline.get(name, {}).get("id")
            ),
        }
    )
    record = {
        "removed": removed,
        "changed": changed,
        "orphan_terms": orphans,
        "stale_table_ids": stale,
        "since": pending.get("since") or now_iso(),
    }
    ctx.store.set_pending_invalidation(ctx.source.id, record)  # before the reset
    # Terms of removed tables lost their links with the catalog rows: delete them if nothing
    # else links them; detach/reset the rest so compile rebuilds
    logger.warning(
        "  schema of %s changed (changed: %s; removed: %s): resetting its semantic "
        "layer so compile rebuilds it; the build's align steps then realign documents "
        "sources to %s",
        ctx.database_name,
        ", ".join(changed) or "-",
        ", ".join(removed) or "-",
        ctx.source.name,
    )
    result = upstream_reset_semantic(
        ctx.database_name, ctx.settings, orphan_candidates=orphans, stale_table_ids=stale
    )
    ctx.store.set_pending_invalidation(ctx.source.id, None)
    ctx.store.set_semantic_baseline(ctx.source.id, after)
    detail["invalidated"] = {"semantic_reset": True, "pending": record, **result}


def _link_types(link: dict[str, Any]) -> set[str]:
    """Relationship types of a semantic-graph link. Upstream sends a
    ``relationship_types`` list (one link per node pair); single-valued keys are accepted
    too."""
    types = {str(t).upper() for t in link.get("relationship_types") or []}
    types |= {str(link.get(k, "")).upper() for k in ("type", "label", "relationship", "kind")}
    return types


def semantic_counts(
    client: BackendClient, database_id: str, graph: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Counts for one catalog database. With ``database_id`` the semantic graph returns
    every Term linked to a table of that database (``dal/exploration.py``), so its nodes
    are this database's Terms (``GET /api/terms`` has no database filter)."""
    graph = graph if graph is not None else client.semantic_graph(database_id)
    links = graph.get("links", []) or []
    fk = sum(1 for link in links if SEMANTIC_FK in _link_types(link))
    nodes = graph.get("nodes", []) or []
    return {
        "graph_nodes": len(nodes),
        "graph_links": len(links),
        "semantic_fk_links": fk,
        "terms_total": len(nodes),
        "fallback_terms": [str(n.get("name")) for n in nodes if is_fallback_term(n)],
    }


#: Upstream's deterministic fallback when the LLM term step fails
#: (``semantic/term_extractor.py:125``).
FALLBACK_TERM_PREFIX = "Business entity represented by table"


def is_fallback_term(node: dict[str, Any]) -> bool:
    description = str(node.get("description") or "").strip()
    return not description or description.startswith(FALLBACK_TERM_PREFIX)


class CompileQualityError(RuntimeError):
    pass


def check_compile_quality(
    graph: dict[str, Any], tables_compiled: int, source_name: str = "<name>"
) -> list[str]:
    """Gate on this database's Terms; returns the fallback Term names (partial fallback).

    Upstream swallows LLM errors and still reports success, so the gate looks at the
    result, not the run: it fails whenever every Term is a fallback -- also when this run
    compiled 0 tables, because compile is incremental (only tables without a Term) and a
    re-run would otherwise "succeed" on the earlier fallbacks. 0 Terms fails only when
    tables were compiled.
    """
    nodes = graph.get("nodes", []) or []
    fallback = [str(n.get("name")) for n in nodes if is_fallback_term(n)]
    if (nodes and len(fallback) == len(nodes)) or (not nodes and tables_compiled):
        raise CompileQualityError(
            f"LLM calls failed: all {len(nodes)} Term(s) are upstream fallbacks "
            f"({', '.join(fallback) or 'none'}). Recover: fix the model setup (`uv run "
            f"autoontology doctor`), then `uv run autoontology source rm {source_name} --drop-db` -> "
            f"`source add <path> --name {source_name}` -> `build --source {source_name}`; a re-run "
            "cannot recompile tables that already have Terms. Details: jobs.log"
        )
    if fallback:
        logger.warning(
            "  %d of %d Term(s) are upstream fallbacks (their LLM calls failed): %s",
            len(fallback),
            len(nodes),
            ", ".join(fallback),
        )
    return fallback


def attribute_gaps(
    client: BackendClient, graph: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """``(tables_failing, uncovered_columns)`` for this database's Term-linked tables.

    A table *fails* when it has no ColumnAttribute of its own although it has a column that
    is not a declared FK: it kept a Term but lost (or never got) its semantics. Columns no
    attribute covers (own or referenced FK column) are only *reported*: upstream
    deliberately gives declared and inferred FK columns no attribute
    (``semantic/deterministic.py``), skips self-referencing FKs and may resolve an inferred
    FK to nothing, and it does not persist those inferred-FK decisions."""
    own: set[tuple[str, str]] = set()
    covered: set[tuple[str, str]] = set()
    for node in graph.get("nodes", []) or []:
        for a in client.term_column_attributes(str(node.get("id"))):
            primary = a.get("primary_column") or {}
            own.add((str(primary.get("table_id")), str(primary.get("column_name"))))
            for col in [primary, *(a.get("referenced_columns") or [])]:
                covered.add((str(col.get("table_id")), str(col.get("column_name"))))
    owned_tables = {t for t, _ in own}
    failing: list[str] = []
    uncovered: list[str] = []
    for name, table in sorted(snapshot.items()):
        if not table.get("terms"):
            continue
        tid = str(table.get("id"))
        fks = set(table.get("fk_columns") or [])
        plain = [c for c in sorted(table.get("columns") or {}) if c not in fks]
        if tid not in owned_tables and plain:
            failing.append(name)
            continue
        uncovered += [f"{name}.{c}" for c in plain if (tid, c) not in covered]
    return failing, uncovered


def tables_without_attributes(
    client: BackendClient, database_id: str, graph: dict[str, Any]
) -> list[str]:
    """``Term/table`` pairs of this database whose table has no ColumnAttribute: a table
    that kept its Term while its attributes went (e.g. a semantic reset that could not
    detach a shared Term) and so was never recompiled."""
    schema_ids = {str(s.get("id")) for s in client.schemas(database_id)}
    missing: list[str] = []
    for node in graph.get("nodes", []) or []:
        term_id = str(node.get("id"))
        term = client.get_term(term_id)
        tables = [t for t in term.get("tables") or [] if str(t.get("schema_id")) in schema_ids]
        if not tables:
            continue
        covered = {
            str((a.get("primary_column") or {}).get("table_id"))
            for a in client.term_column_attributes(term_id)
        }
        missing += [
            f"{term.get('name') or term_id}/{t.get('name')}"
            for t in tables
            if str(t.get("id")) not in covered
        ]
    return missing


def _step_compile(ctx: _Ctx) -> dict[str, Any]:
    ensure_vector_tables(ctx.settings)
    detail = upstream_compile(ctx.database_name, ctx.settings)
    db_id = _ensure_database_id(ctx)
    graph = ctx.client.semantic_graph(db_id)
    detail.update(semantic_counts(ctx.client, db_id, graph))
    logger.info(
        "  %d table(s) compiled; %d term(s) for this database; %d semantic FK link(s)",
        detail["tables_compiled"],
        detail["terms_total"],
        detail["semantic_fk_links"],
    )
    check_compile_quality(graph, detail["tables_compiled"], ctx.source.name)
    snapshot = catalog_snapshot(ctx.database_name, ctx.settings)
    if snapshot:
        failing, uncovered = attribute_gaps(ctx.client, graph, snapshot)
    else:  # no catalog view (e.g. tests): the table-level check through the API only
        failing, uncovered = tables_without_attributes(ctx.client, db_id, graph), []
    if failing:
        raise CompileQualityError(
            f"{len(failing)} table(s) have a Term but no column attributes (they were not "
            f"recompiled): {', '.join(failing[:10])}. Recover: `uv run autoontology source rm "
            f"{ctx.source.name} --drop-db` -> `source add` -> `build --source {ctx.source.name}`"
        )
    if uncovered:
        logger.warning(
            "  %d column(s) without a semantic attribute (upstream gives FK columns "
            "none; reported only): %s",
            len(uncovered),
            ", ".join(uncovered[:20]),
        )
    detail["uncovered_columns"] = uncovered
    if ctx.source.kind == "database" and ctx.source.id and snapshot:
        ctx.store.set_semantic_baseline(ctx.source.id, snapshot)  # last compiled schema
    return detail


def _file_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _step_export(ctx: _Ctx) -> dict[str, Any]:
    db_id = _ensure_database_id(ctx)
    out = ctx.export_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.yaml").write_bytes(ctx.client.export_model([db_id], "auto_ontology"))
    (out / "ossie.zip").write_bytes(
        ctx.client.export_model([db_id], "ossie", include_ontology=True)
    )
    steps = {
        s.name: {
            "status": s.status,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            **s.detail,
        }
        for s in ctx.store.get_steps(ctx.run.id)
    }
    summary = {
        "source": ctx.source.name,
        "kind": ctx.source.kind,
        "run_id": ctx.run.id,
        "database": ctx.database_name,
        "catalog_database_id": db_id,
        "exported_at": now_iso(),
        "semantic": semantic_counts(ctx.client, db_id),
        "uncovered_columns": next(
            (
                s.detail.get("uncovered_columns", [])
                for s in ctx.store.get_steps(ctx.run.id)
                if s.name == "compile"
            ),
            [],
        ),
        "steps": steps,
        "files": [_file_entry(out / "model.yaml"), _file_entry(out / "ossie.zip")],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {"dir": str(out), "files": [f["name"] for f in summary["files"]] + ["summary.json"]}


STEPS: dict[str, Callable[[_Ctx], dict[str, Any]]] = {
    "load": _step_load,
    "register_connection": _step_register,
    "ingest": _step_ingest,
    "compile": _step_compile,
    "export": _step_export,
}


# --- run control ----------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def hold_sources(
    store: Store, names: list[str], operation: str, *, log_path: Path | None = None
) -> Iterator[None]:
    """Hold the sources' store lease (:meth:`Store.lease`) for the body: the one atomic,
    cross-process gate between build, extract, align, export, upload and ``source rm``.
    A busy source is a :class:`JobError` ("already has an active ...": HTTP 409)."""
    with ExitStack() as stack:
        try:
            lease = stack.enter_context(store.lease(names, operation))
        except SourceBusy as exc:
            raise JobError(str(exc), log_path=log_path) from exc
        reset = _HELD.set((*_HELD.get(), lease))
        try:
            yield
        finally:
            _HELD.reset(reset)


def verify_sources(
    store: Store, sources: list[Source], *, log_path: Path | None = None
) -> list[Source]:
    """Re-read *sources* under their lease: a source resolved before the lease was taken
    may have been removed (and its name reused) meanwhile; acting on the stale object could
    hit the replacement. Returns the fresh rows."""
    fresh = []
    for source in sources:
        current = store.get_source(source.name)
        if current is None or current.id != source.id:
            raise JobError(
                f"source {source.name!r} was removed or replaced while this "
                "request waited; run it again",
                log_path=log_path,
            )
        fresh.append(current)
    return fresh


#: Leases held by the current job (thread/context): `_execute` checks them before each step.
_HELD: contextvars.ContextVar[tuple[Lease, ...]] = contextvars.ContextVar(
    "autoontology_held_leases", default=()
)


def check_leases() -> None:
    """Raise :class:`LeaseLost` when a lease the current job holds is no longer its own."""
    for lease in _HELD.get():
        lease.check()


#: A pid whose process started this many seconds after the run did is a reused pid.
PID_REUSE_SLACK_S = 5.0


def _process_started_at(pid: int) -> float | None:
    """The epoch second process *pid* started (``ps -o lstart``), None when unknown."""
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        ).stdout.strip()
        return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _iso_epoch(value: Any) -> float | None:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def stale_reason(store: Store, run: Run, *, lease_grace_s: float | None = None) -> str | None:
    """Why the active *run* cannot really be running any more, or None when it may be.

    A run is stale when its owner pid is gone, when that pid now belongs to a process that
    started after the run (pid reuse), or -- for a run with a source, older than the lease
    TTL -- when its owner no longer holds the source's lease (every job holds it for the
    whole run). Runs of this very process are never judged here."""
    from autoontology.store.db import LEASE_TTL_S

    pid = run.params.get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return None
    if not _pid_alive(pid):
        return f"process {pid} is gone"
    began = _iso_epoch(run.params.get("resumed_at") or run.started_at)
    if began is None:
        return None
    started = _process_started_at(pid)
    if started is not None and started > began + PID_REUSE_SLACK_S:
        return f"process {pid} is gone (the pid now belongs to a newer process)"
    grace = LEASE_TTL_S if lease_grace_s is None else lease_grace_s
    if run.source_id and time.time() - began > grace:
        source = store.get_source_by_id(run.source_id)
        holder = store.lease_holder(source.name) if source is not None else None
        if holder is None or holder.get("pid") != pid:
            return f"process {pid} no longer holds the source lease"
    return None


def reap_run(store: Store, run: Run, reason: str) -> None:
    """Mark the stale *run* (and its running steps) failed with *reason*."""
    store.update_run(run.id, status="failed", error=f"interrupted: {reason}")
    for step in store.get_steps(run.id):
        if step.status == "running":
            store.finish_step(run.id, step.name, status="failed", detail={"error": "interrupted"})
    logger.info("reaped stale %s run %s: %s", run.kind, run.id, reason)


def reap_stale_runs(store: Store, source_id: str | None) -> list[Run]:
    """Fail runs left 'running' by a dead process (see :func:`stale_reason`); return the
    ones still really active. *source_id* None sweeps every run, sourceless ones too
    (an ``ingest`` from the ``/ingest`` shim for a connection made in upstream)."""
    alive = []
    for run in store.active_runs(source_id):
        reason = stale_reason(store, run)
        if reason is not None:
            reap_run(store, run, reason)
        else:
            alive.append(run)
    return alive


def _execute(
    ctx: Any,
    step_names: list[str],
    *,
    skip: dict[str, str],
    already: set[str],
    steps: dict[str, Callable[[Any], dict[str, Any]]] | None = None,
) -> RunResult:
    """Run *step_names* in order on *ctx* (needs ``settings``, ``store``, ``run``),
    persisting each step row; *steps* defaults to the build steps."""
    steps = STEPS if steps is None else steps
    log_path = jobs_log_path(ctx.settings)
    result = RunResult(run_id=ctx.run.id, status="running")
    total = len(step_names)
    # A resumed run reuses succeeded steps only up to the first step that runs again: what
    # comes after it was computed from the earlier state (e.g. an export written before a
    # compile that only now runs), so it is invalidated (back to pending) and re-run.
    first = next((i for i, n in enumerate(step_names) if n not in already and n not in skip), None)
    if first is not None:
        stale = [n for n in step_names[first + 1 :] if n in already]
        if stale:
            ctx.store.reset_steps(ctx.run.id, stale)
            logger.info("%s runs again: invalidated %s", step_names[first], ", ".join(stale))
            already = set(already) - set(stale)
    for index, name in enumerate(step_names, start=1):
        label = f"[{index}/{total}] {name}"
        if name in already:
            logger.info("%s: already succeeded in run %s, skipped (--resume)", label, ctx.run.id)
            result.steps[name] = "succeeded"
            continue
        if name in skip:
            ctx.store.finish_step(ctx.run.id, name, status="skipped", detail={"reason": skip[name]})
            logger.info("%s: skipped (%s)", label, skip[name])
            result.steps[name] = "skipped"
            continue
        try:
            check_leases()  # never mutate on after another operation took the source over
        except LeaseLost as exc:
            ctx.store.update_run(ctx.run.id, status="failed", error=f"{name}: {exc}")
            logger.error("%s: %s", label, exc)
            raise JobError(
                f"step {name} not started: {exc}", run_id=ctx.run.id, step=name, log_path=log_path
            ) from exc
        logger.info("%s ...", label)
        ctx.store.start_step(ctx.run.id, name)
        started = time.monotonic()
        try:
            detail = steps[name](ctx)
        except BaseException as exc:
            seconds = round(time.monotonic() - started, 1)
            interrupted = not isinstance(exc, Exception)
            message = "interrupted" if interrupted else short_error(exc)
            ctx.store.finish_step(
                ctx.run.id, name, status="failed", detail={"error": message, "seconds": seconds}
            )
            ctx.store.update_run(
                ctx.run.id,
                status="cancelled" if interrupted else "failed",
                error=f"{name}: {message}",
            )
            logger.exception("%s failed after %.1fs", label, seconds)
            if interrupted:
                raise
            raise JobError(
                f"step {name} failed: {message}", run_id=ctx.run.id, step=name, log_path=log_path
            ) from exc
        detail["seconds"] = round(time.monotonic() - started, 1)
        ctx.store.finish_step(ctx.run.id, name, status="succeeded", detail=detail)
        logger.info("%s: ok (%.1fs)", label, detail["seconds"])
        result.steps[name] = "succeeded"
        result.details[name] = detail
    ctx.store.update_run(ctx.run.id, status="succeeded")
    result.status = "succeeded"
    return result


def run_structured(
    source_name: str,
    *,
    resume: bool,
    settings: Settings,
    store: Store,
    skip_compile: bool = False,
    skip_export: bool = False,
    client: BackendClient | None = None,
) -> RunResult:
    source = store.get_source(source_name)
    if source is None:
        raise JobError(f"unknown source {source_name!r} (see `autoontology source list`)")
    if source.kind not in ("files", "database"):
        raise JobError(
            f"source {source_name!r} is a {source.kind} source; "
            "`build` handles structured sources only"
        )
    log_path = setup_job_logging(settings)
    with hold_sources(store, [source.name], BUILD_KIND, log_path=log_path):
        return _run_build_held(
            source,
            resume=resume,
            settings=settings,
            store=store,
            skip_compile=skip_compile,
            skip_export=skip_export,
            client=client,
            log_path=log_path,
        )


def _run_build_held(
    source: Source,
    *,
    resume: bool,
    settings: Settings,
    store: Store,
    skip_compile: bool,
    skip_export: bool,
    client: BackendClient | None,
    log_path: Path,
) -> RunResult:
    (source,) = verify_sources(store, [source], log_path=log_path)
    source_name = source.name
    active = reap_stale_runs(store, source.id)
    if active:
        raise JobError(
            f"source {source_name!r} already has an active {active[0].kind} run "
            f"{active[0].id}; wait for it to finish",
            log_path=log_path,
        )
    client = client or BackendClient(settings.backend_url)
    client.health()  # BackendUnavailable -> clear "run autoontology up" error
    require_llm_proxy(settings, log_path=log_path)

    params = {
        "resume": resume,
        "skip_compile": skip_compile,
        "skip_export": skip_export,
        "pid": os.getpid(),
    }
    previous = store.latest_run(source.id, BUILD_KIND) if resume else None
    if previous is not None:
        store.update_run(
            previous.id,
            status="running",
            params={**previous.params, **params, "resumed_at": now_iso()},
        )
        run = store.get_run(previous.id)
        assert run is not None
        already = store.succeeded_steps(run.id)
        logger.info("resuming run %s (succeeded: %s)", run.id, ", ".join(sorted(already)) or "-")
    else:
        if resume:
            logger.info("no earlier build run for %s; starting a new one", source_name)
        run = store.create_run(kind=BUILD_KIND, source_id=source.id, params=params)
        already = set()
        logger.info("build run %s for source %s", run.id, source_name)

    skip: dict[str, str] = {}
    if source.kind != "files":
        skip["load"] = "database source: data stays in the external database"
    if skip_compile:
        skip["compile"] = "--skip-compile"
    if skip_export:
        skip["export"] = "--skip-export"
    ctx = _Ctx(
        settings=settings,
        store=store,
        source=source,
        run=run,
        client=client,
        database_name=pipeline.catalog_database_name(source),
    )
    result = _execute(ctx, list(STEP_ORDER), skip=skip, already=already)
    if result.steps.get("export") == "succeeded":
        result.export_dir = ctx.export_dir
    return result


def run_ingest_job(
    database_name: str, *, settings: Settings, store: Store, client: BackendClient | None = None
) -> RunResult | None:
    """Run only the ``ingest`` step (the ``/ingest`` shim). None when skipped."""
    setup_job_logging(settings)
    key = database_name.casefold()
    source = next(
        (
            s
            for s in store.list_sources()
            if s.kind in ("files", "database")
            and pipeline.catalog_database_name(s).casefold() == key
        ),
        None,
    )
    if source is not None:
        try:
            with hold_sources(store, [source.name], INGEST_KIND):
                if reap_stale_runs(store, source.id):
                    logger.info(
                        "ingest for %s skipped: a run is already active for source %s",
                        database_name,
                        source.name,
                    )
                    return None
                return _run_ingest_held(
                    source, database_name, settings=settings, store=store, client=client
                )
        except JobError as exc:
            if exc.run_id is not None:
                raise
            # a build (or another operation) holds the source: it ingests in-process
            logger.info("ingest for %s skipped: %s", database_name, exc)
            return None
    # A connection made directly in upstream (not via `source add`): still ingest it,
    # as the upstream ingestion service would, but the run has no source.
    source = Source(
        id="",
        name=database_name,
        kind="database",
        locator="",
        pg_database=None,
        catalog_database_id=None,
        created_at="",
        updated_at="",
    )
    return _run_ingest_held(source, database_name, settings=settings, store=store, client=client)


def _run_ingest_held(
    source: Source,
    database_name: str,
    *,
    settings: Settings,
    store: Store,
    client: BackendClient | None,
) -> RunResult:
    run = store.create_run(
        kind=INGEST_KIND,
        source_id=source.id or None,
        params={"database": database_name, "pid": os.getpid()},
    )
    ctx = _Ctx(
        settings=settings,
        store=store,
        source=source,
        run=run,
        client=client or BackendClient(settings.backend_url),
        database_name=database_name,
    )
    return _execute(ctx, ["ingest"], skip={}, already=set())
