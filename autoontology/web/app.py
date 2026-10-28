# SPDX-License-Identifier: MIT
"""AutoOntology service (:7800): the web UI (``/ui/``) and its REST API
(``/api/*``, ``autoontology/web/api.py``), plus the upstream shims below.

``POST /ingest`` and ``POST /ingest/delete`` stand in for the upstream ingestion service
(``INGESTION_SERVICE_URL``): upstream calls them when a connection is created/deleted.
Both answer 202 and do the work in a background thread. ``/v1/*`` is the OpenAI-compatible
chat router (``autoontology/web/providers/``): Ollama or OpenAI-compatible passthrough.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from autoontology.config import Settings
from autoontology.engine.pg import connect
from autoontology.web.runs import RunManager

logger = logging.getLogger("autoontology.web")

_SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")


def redact(value: Any) -> Any:
    """Copy of *value* with secret-looking keys masked (for logs and the request log)."""
    if isinstance(value, dict):
        return {
            k: "***" if any(m in str(k).lower() for m in _SECRET_MARKERS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _run_ingest(database: str, settings: Settings) -> None:
    from autoontology.engine import jobs
    from autoontology.store.db import Store

    try:
        result = jobs.run_ingest_job(
            database, settings=settings, store=Store.for_home(settings.home)
        )
        if result is not None:
            logger.info("ingest run %s for %r: %s", result.run_id, database, result.status)
    except Exception:  # noqa: BLE001 - background thread: the run row holds the error
        logger.exception("ingest for database %r failed", database)


def enqueue_ingest(connection: dict, settings: Settings | None = None) -> threading.Thread | None:
    """Run the ``ingest`` step (only) for *connection* in a background thread.

    Skipped inside the job when a build for the same source is active (``autoontology build``
    registers the connection, which makes upstream call this, then ingests in-process).
    """
    database = str(connection.get("database") or "").strip()
    if not database:
        logger.warning("ingest request without a database name ignored")
        return None
    settings = settings or Settings.from_env()
    thread = threading.Thread(
        target=_run_ingest,
        args=(database, settings),
        daemon=True,
        name=f"autoontology-ingest-{database}",
    )
    thread.start()
    return thread


def _run_delete(database: str, settings: Settings) -> None:
    if not database.strip():
        return
    from autoontology.engine.jobs import prepare_upstream_env, setup_job_logging

    setup_job_logging(settings)
    try:
        prepare_upstream_env(settings)
        from auto_ontology.dal.reset import delete_all_data

        delete_all_data(database)
        logger.info("deleted ingested data for database %r", database)
    except Exception:  # noqa: BLE001 - best-effort, like upstream's ingestion service
        logger.exception("delete of ingested data for database %r failed", database)


def enqueue_delete(database: str, settings: Settings | None = None) -> threading.Thread:
    """Tear down a database's ingested graph and embeddings (``trigger_reset`` target)."""
    settings = settings or Settings.from_env()
    thread = threading.Thread(
        target=_run_delete,
        args=(database, settings),
        daemon=True,
        name=f"autoontology-delete-{database}",
    )
    thread.start()
    return thread


def _sweep_stale_runs(store: Any) -> None:
    """Fail the runs a killed process left 'running' (also sourceless ``/ingest`` runs,
    which no job's own pre-check ever looks at), so the UI and their SSE streams end."""
    from autoontology.engine import jobs

    try:
        before = [r.id for r in store.active_runs()]
        alive = {r.id for r in jobs.reap_stale_runs(store, None)}
    except Exception:  # noqa: BLE001 - a sweep must never keep the service from starting
        logger.exception("stale run sweep failed")
        return
    reaped = [run_id for run_id in before if run_id not in alive]
    if reaped:
        logger.info("startup sweep: reaped %d stale run(s): %s", len(reaped), ", ".join(reaped))


def create_app(
    settings: Settings | None = None,
    *,
    llm_transport: httpx.AsyncBaseTransport | None = None,
    runs: RunManager | None = None,
    providers: Any = None,
) -> FastAPI:
    from autoontology.web import api
    from autoontology.web.providers.router import Registry, build_router

    settings = settings or Settings.from_env()
    _provider_logging()
    from autoontology import __version__

    app = FastAPI(title="AutoOntology", version=__version__)
    app.state.settings = settings
    # /v1/*: OpenAI-compatible chat router (Ollama or OpenAI-compatible passthrough).
    registry = providers or Registry(settings, llm_transport)
    app.state.providers = registry
    app.include_router(build_router(settings, llm_transport, registry))
    # /api/* + the UI at /ui/ (/ redirects there)
    state = api.install(app, settings, runs)
    _sweep_stale_runs(state.store)

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health")
    def health() -> JSONResponse:
        checks: dict[str, dict[str, str]] = {}
        try:
            with connect(settings) as conn:
                conn.execute("select 1")
            checks["postgres"] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            checks["postgres"] = {"status": "error", "detail": type(exc).__name__}
        backend = f"{settings.backend_url}/api/health/live"
        try:
            code = httpx.get(backend, timeout=2.0).status_code
            checks["backend"] = (
                {"status": "ok"} if code == 200 else {"status": "error", "detail": f"HTTP {code}"}
            )
        except httpx.HTTPError as exc:
            checks["backend"] = {"status": "error", "detail": type(exc).__name__}
        healthy = all(c["status"] == "ok" for c in checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status_code=200 if healthy else 503,
        )

    @app.post("/ingest", status_code=202)
    def ingest(connection: dict = Body(...)) -> dict[str, str]:  # noqa: B008
        safe = redact(connection)
        logger.info("POST /ingest database=%r", connection.get("database"))
        settings.run_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": connection.get("database"),
            "connection": safe,
        }
        with (settings.run_dir / "ingest-requests.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        enqueue_ingest(connection, settings)
        return {"status": "accepted"}

    @app.post("/ingest/delete", status_code=202)
    def ingest_delete(database_name: str) -> dict[str, str]:
        # upstream server/ingestion/proxy.py:trigger_reset (called on connection delete)
        logger.info("POST /ingest/delete database=%r", database_name)
        if not database_name.strip():
            # delete_all_data(None) would wipe every database: never forward a blank name.
            raise HTTPException(status_code=422, detail="database_name is required")
        enqueue_delete(database_name, settings)
        return {"status": "accepted"}

    return app


def _provider_logging() -> None:
    """Provider log lines (stream breaks, failures) in service.log."""
    plog = logging.getLogger("autoontology.providers")
    if not plog.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        plog.addHandler(handler)
        plog.setLevel(logging.INFO)
        plog.propagate = False


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """``autoontology.web.app:app`` for uvicorn, built lazily so importing has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
