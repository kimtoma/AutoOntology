"""REST API behind the web UI, under ``/api/`` (upstream owns ``/api/*`` on the
backend port; the UI never calls it directly).

Errors are ``{"error": <code>, "detail": <message>}`` with a matching status. Source names
follow the CLI rules (structured: ``[a-z0-9_]+``; documents: ``[a-z0-9][a-z0-9_-]*``).
Secrets never leave: database URLs are shown with the password masked, run params and
errors pass through :func:`mask`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import UploadFile

from autoontology import __version__
from autoontology.config import Settings
from autoontology.store.db import ACTIVE_RUN_STATUSES, Run, Source, Store
from autoontology.web.runs import RUN_ID_RE, TOP_STEPS, RunManager, RunStartError, run_log_path

PREFIX = "/api"
#: Host header names the API answers to (DNS-rebinding guard); "testserver" = TestClient.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "testserver"})
STRUCTURED_KINDS = ("files", "database")
#: Upload cap per request (all files together), ``AUTOONTOLOGY_UPLOAD_MAX_MB``.
DEFAULT_UPLOAD_MAX_MB = 200
#: Slack for multipart boundaries/headers when comparing Content-Length with the cap.
MULTIPART_OVERHEAD = 1024 * 1024
FILE_NAME_RE = re.compile(r"^[^/\\\x00]{1,200}$")
EXPORT_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
# Masking is the only barrier between log records (ours and upstream's) and the SSE stream.
_URL_PASSWORD_RE = re.compile(r"(://[^:/@\s]*:)[^@\s]+@")  # user may be empty
#: KEY=value / KEY: value / "key": "value" / 'key': 'value' for any name ending in a secret
#: word (POSTGRES_PASSWORD, PGPASSWORD, OPENAI_API_KEY, client_secret, ...), not token_count.
_KV_SECRET_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY))"
    r"([\"']?\s*[=:]\s*)(?:([\"'])(?!\*\*\*)[^\"'\n]*\3|(?!\*\*\*)[^\s,;&'\"}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)(?!\*\*\*)[^\s,;'\"]+")
_BARE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(nvapi-|sk-)[A-Za-z0-9_-]{6,}")
_SECRET_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "authorization",
)
#: Seconds between SSE polls of the run log and step rows.
SSE_POLL_S = 0.5
#: How often the SSE stream checks that an unmanaged active run's owner is alive.
SSE_STALE_CHECK_S = 5.0
GRAPH_CACHE_SIZE = 8
STATUS_CACHE_S = 15.0


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail


def mask(value: Any) -> Any:
    """Copy of *value* with URL passwords and secret-looking keys masked."""
    if isinstance(value, str):
        value = _URL_PASSWORD_RE.sub(r"\1***@", value)
        value = _KV_SECRET_RE.sub(lambda m: f"{m[1]}{m[2]}{m[3] or ''}***{m[3] or ''}", value)
        value = _BEARER_RE.sub(r"\1***", value)
        return _BARE_TOKEN_RE.sub(r"\1***", value)
    if isinstance(value, dict):
        return {
            k: "***" if any(m in str(k).lower() for m in _SECRET_KEYS) else mask(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


# --- request models ------------------------------------------------------------------


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["files", "database", "documents"]
    locator: str = Field(min_length=1, max_length=4096)
    recursive: bool = False


class BuildOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume: bool = False
    skip_compile: bool = False
    skip_export: bool = False


class ExtractOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume: bool = False
    workers: int | None = Field(default=None, ge=1, le=16)
    max_chunks: int | None = Field(default=None, ge=0)


class AlignOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    structured_source: str | None = None
    dry_run: bool = False
    resume: bool = False
    threshold: float | None = Field(default=None, ge=0, le=1)
    min_conf: float | None = Field(default=None, ge=0, le=1)


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str] | None = None


OPTIONS: dict[str, type[BaseModel]] = {
    "build": BuildOptions,
    "extract": ExtractOptions,
    "align": AlignOptions,
    "export": ExportOptions,
}


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["build", "extract", "align", "export"]
    source: str
    options: dict[str, Any] = Field(default_factory=dict)


# --- helpers -----------------------------------------------------------------------------


def validate_name(name: str, kind: str) -> str:
    from autoontology.engine.extract import validate_documents_source_name
    from autoontology.structured.pipeline import validate_source_name

    try:
        if kind == "documents":
            return validate_documents_source_name(name)
        return validate_source_name(name)
    except ValueError as exc:
        raise ApiError(400, "invalid_name", str(exc)) from exc


def _any_name(name: str) -> str:
    """A name valid under either rule (lookups)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*|[a-z0-9_]+", name or ""):
        raise ApiError(400, "invalid_name", f"invalid source name {name!r}")
    return name


def host_name(header: str) -> str:
    """The host part of a Host header: ``[::1]:7800`` / ``[::1]`` -> ``[::1]``,
    ``localhost:7800`` -> ``localhost``."""
    header = header.strip().lower()
    if header.startswith("["):
        end = header.find("]")
        return header[: end + 1] if end > 0 else header
    return header.rsplit(":", 1)[0] if ":" in header else header


#: Paths the Host/Origin guard leaves open (liveness/readiness probes).
UNGUARDED_PREFIXES = ("/health",)


def guarded_path(path: str) -> bool:
    """True for every route the local-only middleware checks (all but ``/health*``)."""
    return not any(path == p or path.startswith(p + "/") for p in UNGUARDED_PREFIXES)


def upload_limit_bytes() -> int:
    raw = os.environ.get("AUTOONTOLOGY_UPLOAD_MAX_MB", "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_UPLOAD_MAX_MB
    except ValueError:
        mb = DEFAULT_UPLOAD_MAX_MB
    return int(mb * 1024 * 1024)


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Api:
    """State shared by the handlers: settings, store, run manager, caches."""

    def __init__(self, settings: Settings, runs: RunManager | None = None) -> None:
        self.settings = settings
        self.runs = runs or RunManager(settings)
        self._store: Store | None = None
        self._graph_cache: dict[tuple, Any] = {}
        self._graph_lock = threading.Lock()
        self._status: tuple[float, dict[str, Any]] | None = None

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store.for_home(self.settings.home)
        return self._store

    @property
    def uploads_dir(self) -> Path:
        return Path(self.settings.home) / "uploads"

    def source(self, name: str) -> Source:
        source = self.store.get_source(_any_name(name))
        if source is None:
            raise ApiError(404, "unknown_source", f"unknown source {name!r}")
        return source

    # --- serialisation ---------------------------------------------------------------

    def run_dict(
        self, run: Run, *, names: dict[str, str] | None = None, steps: bool = True
    ) -> dict[str, Any]:
        names = names if names is not None else {s.id: s.name for s in self.store.list_sources()}
        out: dict[str, Any] = {
            "id": run.id,
            "kind": run.kind,
            "source": names.get(run.source_id or ""),
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": mask(run.error),
            "params": mask({k: v for k, v in run.params.items() if k != "pid"}),
            "managed": self.runs.is_managed(run.id),
        }
        if steps:
            out.update(self.step_state(run))
        return out

    def step_state(self, run: Run) -> dict[str, Any]:
        top = TOP_STEPS.get(run.kind, ())
        rows = self.store.get_steps(run.id)
        by_name = {s.name: s for s in rows}
        steps = []
        for name in list(top) + [
            s.name
            for s in rows
            if s.name not in top and not s.name.startswith(("chunk:", "concept:"))
        ]:
            s = by_name.get(name)
            steps.append(
                {
                    "name": name,
                    "status": s.status if s else "pending",
                    "started_at": s.started_at if s else None,
                    "finished_at": s.finished_at if s else None,
                    "detail": mask(_short_detail(s.detail)) if s else {},
                }
            )
        progress: dict[str, dict[str, int]] = {}
        for s in rows:
            for prefix, label in (("chunk:", "chunks"), ("concept:", "concepts")):
                if s.name.startswith(prefix):
                    bucket = progress.setdefault(label, {})
                    bucket[s.status] = bucket.get(s.status, 0) + 1
        return {"steps": steps, "progress": progress}

    def source_dict(self, source: Source) -> dict[str, Any]:
        from autoontology.store.documents import DocumentStore
        from autoontology.structured import pipeline

        last = self.store.latest_run(source.id)
        active = self.runs.active_source(source.name)
        out: dict[str, Any] = {
            "name": source.name,
            "kind": source.kind,
            "locator": mask(pipeline.display_locator(source)),
            "database": (
                pipeline.catalog_database_name(source) if source.kind in STRUCTURED_KINDS else None
            ),
            "built": bool(source.catalog_database_id),
            "uploaded": self._is_upload(source),
            "created_at": source.created_at,
            "last_run": (
                {
                    "id": last.id,
                    "kind": last.kind,
                    "status": last.status,
                    "finished_at": last.finished_at,
                }
                if last
                else None
            ),
            "active_run": ({"id": active.run_id, "kind": active.kind} if active else None),
        }
        if source.kind == "documents":
            docs = DocumentStore(self.store)
            out["documents"] = len(docs.list_documents(source.id))
            out["concepts"] = len(docs.list_concepts(source.id))
        return out

    def _is_upload(self, source: Source) -> bool:
        if source.kind == "database":
            return False
        try:
            path = Path(json.loads(source.locator)["path"]).resolve()
        except (ValueError, KeyError, TypeError):
            return False
        return path == (self.uploads_dir / source.name).resolve()

    # --- graph -------------------------------------------------------------------------

    def graph_model(self, names: list[str]) -> tuple[Any, list[str]]:
        from autoontology.engine.client import BackendClient, BackendError
        from autoontology.engine.export_job import collect_model, documents_inputs
        from autoontology.export import merged

        sources = [self.source(n) for n in names]
        structured = [s for s in sources if s.kind in STRUCTURED_KINDS]
        documents = [s for s in sources if s.kind == "documents"]
        # any run finishing anywhere (e.g. an align of another documents source against a
        # requested structured source changes its Terms), also an older one finishing after
        # a newer one, bumps the store's ontology revision and so invalidates the cache
        fingerprint = (tuple(names), self.store.ontology_revision())
        with self._graph_lock:
            cached = self._graph_cache.get(fingerprint)
        if cached is not None:
            return cached
        name = (documents[0].name if documents else structured[0].name) if sources else "graph"
        warnings: list[str] = []
        try:
            with BackendClient(self.settings.backend_url) as client:
                model, _, _ = collect_model(name, self.store, client, structured, documents)
        except (BackendError, LookupError, httpx.HTTPError) as exc:
            if not documents:
                raise ApiError(
                    503, "backend_unavailable", f"structured graph unavailable: {mask(str(exc))}"
                ) from exc
            warnings.append(
                f"could not load the structured sources (backend): {mask(str(exc))[:200]}"
            )
            model = merged.build(name, documents=documents_inputs(self.store, documents))
            return model, warnings  # not cached: retry once the backend is back
        with self._graph_lock:
            if len(self._graph_cache) >= GRAPH_CACHE_SIZE:
                self._graph_cache.pop(next(iter(self._graph_cache)))
            self._graph_cache[fingerprint] = (model, warnings)
        return model, warnings

    # --- status ------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Read-only service status (cached ``STATUS_CACHE_S``): backend health, Postgres
        reachability, Ollama models present, kordoc availability and the provider summary
        (names and models only, never keys)."""
        now = time.monotonic()
        if self._status and now - self._status[0] < STATUS_CACHE_S:
            return self._status[1]
        s = self.settings
        checks: dict[str, dict[str, str]] = {}
        try:
            code = httpx.get(f"{s.backend_url}/api/health/live", timeout=2.0).status_code
            checks["backend"] = (
                {"status": "ok", "detail": s.backend_url}
                if code == 200
                else {"status": "error", "detail": f"HTTP {code}"}
            )
        except httpx.HTTPError as exc:
            checks["backend"] = {"status": "error", "detail": type(exc).__name__}
        try:
            from autoontology.engine.pg import connect

            with connect(s) as conn:
                conn.execute("select 1")
            checks["postgres"] = {
                "status": "ok",
                "detail": f"{s.postgres_host}:{s.postgres_port}/{s.postgres_database}",
            }
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            checks["postgres"] = {
                "status": "error",
                "detail": type(exc).__name__,
                "remedy": "start Postgres (e.g. `docker compose up -d`), "
                "then `uv run autoontology setup`",
            }
        if "ollama" in (s.chat_provider, s.embed_provider):
            from autoontology.engine.ollama import has_model, list_models

            try:
                names = list_models(s.ollama_endpoint)
                wanted = [
                    *([s.ollama_chat_model] if s.chat_provider == "ollama" else []),
                    *([s.ollama_embed_model] if s.embed_provider == "ollama" else []),
                ]
                missing = [m for m in wanted if not has_model(names, m)]
                checks["ollama"] = (
                    {
                        "status": "error",
                        "detail": f"missing models: {', '.join(missing)}",
                        "remedy": "uv run autoontology setup",
                    }
                    if missing
                    else {"status": "ok", "detail": ", ".join(wanted)}
                )
            except (httpx.HTTPError, ValueError) as exc:
                checks["ollama"] = {
                    "status": "error",
                    "detail": type(exc).__name__,
                    "remedy": "start Ollama (`ollama serve`)",
                }
        from autoontology.unstructured import kordoc

        try:
            kordoc.resolve_engine(s.home)
            kordoc_ok = True
        except Exception:  # noqa: BLE001 - optional component (KordocUnavailable, OSError)
            kordoc_ok = False
        overall = "error" if any(c["status"] == "error" for c in checks.values()) else "ok"
        result = {
            "status": overall,
            "version": __version__,
            "providers": {
                "chat_provider": s.chat_provider,
                "chat_model": s.effective_chat_model,
                "embed_provider": s.embed_provider,
                "embed_model": s.effective_embed_model,
                "openai_base_url": s.openai_base_url
                if "openai" in (s.chat_provider, s.embed_provider)
                else "",
            },
            "kordoc_available": kordoc_ok,
            "service_url": s.service_url,
            "checks": checks,
            "doctor": "uv run autoontology doctor",
            "upload_max_mb": round(upload_limit_bytes() / (1024 * 1024), 1),
        }
        result = mask(result)
        self._status = (now, result)
        return result


def _short_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Step detail for the API: scalars and small values only (no per-chunk lists)."""
    out: dict[str, Any] = {}
    for k, v in detail.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v[:300] if isinstance(v, str) else v
        elif isinstance(v, dict) and len(json.dumps(v, default=str)) < 2000:
            out[k] = v
        elif isinstance(v, list):
            out[k] = v[:5] if len(json.dumps(v[:5], default=str)) < 600 else len(v)
    return out


# --- router --------------------------------------------------------------------------------


def build_router(api: Api) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    # sources ------------------------------------------------------------------------

    @router.get("/sources")
    def list_sources() -> dict[str, Any]:
        return {"sources": [api.source_dict(s) for s in api.store.list_sources()]}

    @router.post("/sources", status_code=201)
    async def add_source(request: Request) -> dict[str, Any]:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            limit = upload_limit_bytes()
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > limit + MULTIPART_OVERHEAD:  # refuse before reading the body
                raise ApiError(
                    413,
                    "too_large",
                    f"upload exceeds {limit // (1024 * 1024)} MB (AUTOONTOLOGY_UPLOAD_MAX_MB)",
                )
            form = await request.form()
            try:
                return await _add_upload(api, form)
            finally:
                await form.close()
        try:
            body = SourceIn.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        return await anyio.to_thread.run_sync(_add_locator, api, body)

    @router.delete("/sources/{name}")
    def delete_source(name: str, drop_db: bool = False, force: bool = False) -> dict[str, Any]:
        from autoontology.structured.remove import RemoveError, remove_source

        source = api.source(name)
        if api.runs.active_source(source.name):
            raise ApiError(409, "run_active", f"source {name!r} has an active run")
        messages: list[str] = []
        try:
            remove_source(
                api.settings,
                api.store,
                source,
                drop_db=drop_db,
                force=force,
                notify=lambda m, _w: messages.append(mask(m)),
            )
        except RemoveError as exc:
            raise ApiError(409, "remove_failed", mask(str(exc))) from exc
        upload = api.uploads_dir / source.name
        if api._is_upload(source) and upload.is_dir():
            shutil.rmtree(upload, ignore_errors=True)
            messages.append("deleted the uploaded files")
        return {"removed": source.name, "messages": messages}

    # runs ---------------------------------------------------------------------------

    @router.post("/runs", status_code=202)
    def start_run(body: dict[str, Any]) -> dict[str, Any]:
        try:
            req = RunIn.model_validate(body)
            options = OPTIONS[req.kind].model_validate(req.options)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        source = api.source(req.source)
        call = _job_call(api, req.kind, source, options)
        try:
            run_id = api.runs.start(req.kind, source.name, call)
        except RunStartError as exc:
            code = {409: "run_active", 503: "backend_unavailable"}.get(
                exc.status, "run_not_started"
            )
            raise ApiError(exc.status, code, mask(str(exc))) from exc
        return {"run_id": run_id}

    @router.get("/runs")
    def list_runs(
        source: str | None = None, limit: int = Query(30, ge=1, le=200)
    ) -> dict[str, Any]:
        source_id = api.source(source).id if source else None
        names = {s.id: s.name for s in api.store.list_sources()}
        runs = api.store.list_runs(source_id, limit=limit)
        return {"runs": [api.run_dict(r, names=names) for r in runs]}

    def _run(run_id: str) -> Run:
        if not RUN_ID_RE.fullmatch(run_id):
            raise ApiError(400, "invalid_run_id", f"invalid run id {run_id!r}")
        run = api.store.get_run(run_id)
        if run is None:
            raise ApiError(404, "unknown_run", f"unknown run {run_id!r}")
        return run

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return api.run_dict(_run(run_id))

    @router.post("/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> dict[str, Any]:
        run = _run(run_id)
        if run.status not in ACTIVE_RUN_STATUSES or not api.runs.cancel(run_id):
            raise ApiError(
                409,
                "not_cancellable",
                f"run {run_id} is not running in this service ({run.status})",
            )
        return {"run_id": run_id, "cancel_requested": True}

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> EventSourceResponse:
        _run(run_id)
        return EventSourceResponse(_events(api, run_id, request), ping=15)

    # concepts / relations -----------------------------------------------------------

    @router.get("/concepts")
    def list_concepts(
        source: str | None = None,
        q: str = Query("", max_length=200),
        kind: str = Query("", max_length=40),
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        from autoontology.store.alignments import AlignmentStore
        from autoontology.store.documents import DocumentStore

        if source:
            sources = [api.source(source)]
            if sources[0].kind != "documents":
                raise ApiError(
                    400,
                    "not_documents",
                    f"source {source!r} is a "
                    f"{sources[0].kind} source; concepts come from documents",
                )
        else:
            sources = [s for s in api.store.list_sources() if s.kind == "documents"]
        names = {s.id: s.name for s in sources}
        rows, total = DocumentStore(api.store).search_concepts(
            list(names), q=q, kind=kind, limit=size, offset=(page - 1) * size
        )
        aligns = AlignmentStore(api.store)
        best: dict[str, Any] = {}
        for sid in names:
            for a in aligns.list(sid):
                if a.relation in ("none", "skipped"):
                    continue
                cur = best.get(a.concept_id)
                if cur is None or (a.confidence or 0) > (cur.confidence or 0):
                    best[a.concept_id] = a
        items = []
        for c in rows:
            a = best.get(c.id)
            items.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "name_en": c.name_en,
                    "kind": c.kind,
                    "source": names.get(c.source_id),
                    "definition": c.definition[:300],
                    "synonyms": c.synonyms,
                    "evidence_count": c.evidence_count,
                    "verified_count": c.verified_count,
                    "alignment": (
                        {
                            "relation": a.relation,
                            "term": a.term_name,
                            "accepted": a.accepted,
                            "dry_run": a.dry_run,
                        }
                        if a
                        else None
                    ),
                }
            )
        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, -(-total // size)),
        }

    @router.get("/concepts/{concept_id}")
    def get_concept(concept_id: str) -> dict[str, Any]:
        from autoontology.store.alignments import AlignmentStore
        from autoontology.store.documents import DocumentStore
        from autoontology.unstructured.report import display_path

        if not re.fullmatch(r"[0-9a-f]{32}", concept_id):
            raise ApiError(400, "invalid_concept_id", f"invalid concept id {concept_id!r}")
        docs = DocumentStore(api.store)
        c = docs.get_concept(concept_id)
        if c is None:
            raise ApiError(404, "unknown_concept", f"unknown concept {concept_id!r}")
        source = api.store.get_source_by_id(c.source_id)
        evidence = [
            {
                "quote": e["quote"],
                "verified": bool(e["verified"]),
                "page": e["page"],
                "section": e["section_path"],
                "document": e["title"],
                "path": display_path(e["path"]),
            }
            for e in docs.concept_evidence(c.id)
        ]
        alignments = [
            {
                "relation": a.relation,
                "term": a.term_name,
                "term_id": a.term_id,
                "structured_source": a.structured_source,
                "confidence": a.confidence,
                "similarity": a.similarity,
                "accepted": a.accepted,
                "dry_run": a.dry_run,
                "rationale": a.rationale,
            }
            for a in AlignmentStore(api.store).for_concept(c.id)
        ]
        return {
            "id": c.id,
            "name": c.name,
            "name_en": c.name_en,
            "kind": c.kind,
            "source": source.name if source else None,
            "definition": c.definition,
            "synonyms": c.synonyms,
            "attributes": c.attributes,
            "merged_from": c.merged_from,
            "evidence_count": c.evidence_count,
            "verified_count": c.verified_count,
            "evidence": evidence,
            "relations": docs.concept_relations(c.id),
            "alignments": alignments,
        }

    @router.get("/relations")
    def list_relations(source: str) -> dict[str, Any]:
        from autoontology.store.documents import DocumentStore

        src = api.source(source)
        rows = DocumentStore(api.store).list_relations(src.id)
        return {"relations": [r.__dict__ for r in rows]}

    # graph ------------------------------------------------------------------------------

    @router.get("/graph")
    def graph(
        sources: str | None = None, layer: str = "all", evidence: bool = False
    ) -> dict[str, Any]:
        from autoontology.web.graph import LAYERS, max_nodes_from_env, to_view

        if layer not in LAYERS:
            raise ApiError(400, "invalid_layer", f"layer must be one of {', '.join(LAYERS)}")
        if sources:
            names = list(dict.fromkeys(n.strip() for n in sources.split(",") if n.strip()))
        else:
            names = [
                s.name
                for s in api.store.list_sources()
                if (s.kind in STRUCTURED_KINDS and s.catalog_database_id) or s.kind == "documents"
            ]
        if not names:
            return {
                "nodes": [],
                "edges": [],
                "layer": layer,
                "warnings": [],
                "counts": {"nodes": 0, "edges": 0},
                "structured_sources": [],
                "documents_sources": [],
            }
        model, warnings = api.graph_model(names)
        cap = max_nodes_from_env()
        return {
            **to_view(model, layer, evidence=evidence, max_nodes=cap),
            "max_nodes": cap,
            "warnings": warnings,
        }

    # exports ----------------------------------------------------------------------------

    @router.get("/exports")
    def list_exports(source: str | None = None) -> dict[str, Any]:
        root = Path(api.settings.exports_dir)
        folders = (
            [root / _any_name(source)]
            if source
            else (sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else [])
        )
        out = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for run_dir in folder.iterdir():
                if not (run_dir.is_dir() and RUN_ID_RE.fullmatch(run_dir.name)):
                    continue
                run = api.store.get_run(run_dir.name)
                files = sorted(
                    (
                        {"name": f.name, "bytes": f.stat().st_size}
                        for f in run_dir.iterdir()
                        if f.is_file() and EXPORT_FILE_RE.fullmatch(f.name)
                    ),
                    key=lambda f: f["name"],
                )
                out.append(
                    {
                        "source": folder.name,
                        "run_id": run_dir.name,
                        "kind": run.kind if run else None,
                        "status": run.status if run else None,
                        "created_at": (run.finished_at or run.started_at)
                        if run
                        else _iso(run_dir.stat().st_mtime),
                        "files": files,
                    }
                )
        out.sort(key=lambda e: e["run_id"], reverse=True)
        return {"exports": out}

    @router.get("/exports/{run_id}/{file_name}")
    def download_export(run_id: str, file_name: str, inline: bool = False) -> Response:
        if not RUN_ID_RE.fullmatch(run_id) or not EXPORT_FILE_RE.fullmatch(file_name):
            raise ApiError(400, "invalid_path", "invalid export path")
        root = Path(api.settings.exports_dir).resolve()
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            candidate = (folder / run_id / file_name).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                if inline:
                    if candidate.stat().st_size > 5 * 1024 * 1024:
                        raise ApiError(413, "too_large", "file too large to preview")
                    return Response(
                        candidate.read_text(encoding="utf-8", errors="replace"),
                        media_type="text/plain; charset=utf-8",
                    )
                return FileResponse(candidate, filename=file_name)
        raise ApiError(404, "unknown_export", f"no export file {run_id}/{file_name}")

    # status -----------------------------------------------------------------------------

    @router.get("/status")
    def status() -> dict[str, Any]:
        return api.status()

    return router


def _validation_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()
        )[:600]
    return str(exc)[:600]


def _add_locator(api: Api, body: SourceIn) -> dict[str, Any]:
    from autoontology.engine.extract import ensure_documents_source
    from autoontology.structured import pipeline
    from autoontology.structured.loaders import LoadError

    validate_name(body.name, body.kind)
    if api.store.get_source(body.name):
        raise ApiError(409, "source_exists", f"source {body.name!r} already exists")
    try:
        if body.kind == "documents":
            path = Path(body.locator).expanduser()
            if not path.is_absolute():
                raise ApiError(400, "invalid_locator", "use an absolute path")
            if not path.exists():
                raise ApiError(400, "invalid_locator", f"no such file or directory: {path}")
            source = ensure_documents_source(api.store, body.name, path, body.recursive)
        else:
            if body.kind == "files" and not Path(body.locator).expanduser().is_absolute():
                raise ApiError(400, "invalid_locator", "use an absolute path")
            source = pipeline.add_source(
                api.store, body.locator, name=body.name, kind=body.kind, recursive=body.recursive
            )
    except (LoadError, ValueError) as exc:
        raise ApiError(400, "invalid_source", mask(str(exc))) from exc
    return api.source_dict(source)


async def _add_upload(api: Api, form: Any) -> dict[str, Any]:
    name = str(form.get("name") or "")
    kind = str(form.get("kind") or "")
    if kind not in ("files", "documents"):
        raise ApiError(400, "invalid_kind", "uploads need kind files or documents")
    validate_name(name, kind)
    from autoontology.store.db import SourceBusy

    try:
        api.runs.claim(name, "upload")  # concurrent uploads of the same name: 409
    except RunStartError as exc:
        raise ApiError(409, "source_busy", str(exc)) from exc
    try:
        # the store lease also excludes CLI runs / removals of the same name
        token = await anyio.to_thread.run_sync(
            lambda: api.store.acquire_leases([name], "upload", ttl=3600.0)
        )
    except SourceBusy as exc:
        api.runs.release(name)
        raise ApiError(409, "source_busy", str(exc)) from exc
    try:
        return await _add_upload_claimed(api, form, name, kind)
    finally:
        await anyio.to_thread.run_sync(api.store.release_lease, token)
        api.runs.release(name)


async def _add_upload_claimed(api: Api, form: Any, name: str, kind: str) -> dict[str, Any]:
    from autoontology.structured import pipeline
    from autoontology.structured.loaders import SUPPORTED_SUFFIXES, LoadError
    from autoontology.unstructured.readers import SUPPORTED_EXTENSIONS

    if api.store.get_source(name):
        raise ApiError(409, "source_exists", f"source {name!r} already exists")
    uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    if not uploads:
        raise ApiError(400, "no_files", "add at least one file (field 'files')")
    allowed = SUPPORTED_SUFFIXES if kind == "files" else SUPPORTED_EXTENSIONS
    names: list[str] = []
    for f in uploads:
        fname = f.filename or ""
        # a bare file name only: no directories, no hidden files, no "..": the upload
        # folder is the only place anything is written
        if not FILE_NAME_RE.fullmatch(fname) or fname.startswith(".") or fname != fname.strip():
            raise ApiError(400, "invalid_file_name", f"invalid file name {f.filename!r}")
        if Path(fname).suffix.lower() not in allowed:
            raise ApiError(
                400,
                "unsupported_file",
                f"unsupported file type {fname!r} (supported: {', '.join(sorted(allowed))})",
            )
        if fname.casefold() in (n.casefold() for n in names):
            raise ApiError(400, "duplicate_file", f"duplicate file name {fname!r}")
        names.append(fname)
    target = api.uploads_dir / name
    if target.exists():  # left behind by an earlier failed upload (no source row)
        shutil.rmtree(target)
    target.mkdir(parents=True)
    limit, total = upload_limit_bytes(), 0
    try:
        for f, fname in zip(uploads, names, strict=True):
            with (target / fname).open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ApiError(
                            413,
                            "too_large",
                            f"upload exceeds "
                            f"{limit // (1024 * 1024)} MB (AUTOONTOLOGY_UPLOAD_MAX_MB)",
                        )
                    out.write(chunk)

        def register() -> Source:
            from autoontology.engine.extract import ensure_documents_source

            if kind == "documents":
                return ensure_documents_source(api.store, name, target, False)
            return pipeline.add_source(api.store, str(target), name=name, kind="files")

        try:
            source = await anyio.to_thread.run_sync(register)
        except (LoadError, ValueError) as exc:
            raise ApiError(400, "invalid_source", mask(str(exc))) from exc
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {**api.source_dict(source), "files": names}


def _job_call(api: Api, kind: str, source: Source, options: BaseModel):
    """``call(job_fn, store)`` for the run manager, after validating kind vs. source."""
    from autoontology.engine.extract import documents_target

    settings = api.settings
    o = options
    if kind == "build":
        if source.kind not in STRUCTURED_KINDS:
            raise ApiError(
                400,
                "wrong_source_kind",
                f"build needs a files/database source; {source.name!r} is {source.kind}",
            )
        return lambda fn, store: fn(
            source.name,
            resume=o.resume,
            settings=settings,
            store=store,
            skip_compile=o.skip_compile,
            skip_export=o.skip_export,
        )
    if kind == "extract":
        if source.kind != "documents":
            raise ApiError(
                400,
                "wrong_source_kind",
                f"extract needs a documents source; {source.name!r} is {source.kind}",
            )
        target, recursive = documents_target(source)
        return lambda fn, store: fn(
            source.name,
            target,
            settings=settings,
            store=store,
            recursive=recursive,
            resume=o.resume,
            workers=o.workers,
            max_chunks=o.max_chunks,
        )
    if kind == "align":
        if source.kind != "documents":
            raise ApiError(
                400,
                "wrong_source_kind",
                f"align needs a documents source; {source.name!r} is {source.kind}",
            )
        if o.structured_source:
            api.source(o.structured_source)
        return lambda fn, store: fn(
            source.name,
            settings=settings,
            store=store,
            structured_source=o.structured_source,
            dry_run=o.dry_run,
            resume=o.resume,
            min_sim=o.threshold,
            min_conf=o.min_conf,
        )
    sources = None
    if o.sources:
        sources = [api.source(n).name for n in o.sources]
    return lambda fn, store: fn(source.name, settings=settings, store=store, sources=sources)


async def _events(api: Api, run_id: str, request: Request):
    """SSE: ``log`` (one line; its id is the byte offset after it, so a reconnect with
    ``Last-Event-ID`` resumes there), ``step`` / ``progress`` / ``status`` on change, ``end``."""
    path = run_log_path(api.settings, run_id)
    try:
        offset = max(0, int(request.headers.get("last-event-id") or 0))
    except ValueError:
        offset = 0
    partial = b""
    last_steps: dict[str, str] = {}
    last_progress: Any = None
    last_status: str | None = None

    next_stale_check = 0.0

    def snapshot() -> tuple[Run | None, dict[str, Any]]:
        nonlocal next_stale_check
        run = api.store.get_run(run_id)
        if (
            run is not None
            and run.status in ACTIVE_RUN_STATUSES
            and not api.runs.is_managed(run_id)
            and time.monotonic() >= next_stale_check
        ):
            # a run whose owner died never changes status by itself: reap it, so `end` comes
            next_stale_check = time.monotonic() + SSE_STALE_CHECK_S
            from autoontology.engine import jobs

            reason = jobs.stale_reason(api.store, run)
            if reason is not None:
                jobs.reap_run(api.store, run, reason)
                run = api.store.get_run(run_id)
        return run, (api.step_state(run) if run else {"steps": [], "progress": {}})

    def read_lines(final: bool = False):
        """New complete lines (all of them, and the trailing partial one, when *final*)."""
        nonlocal offset, partial
        if not path.is_file():
            return []
        out = []
        with path.open("rb") as fh:
            fh.seek(offset)
            while True:
                data = fh.read(256 * 1024)
                if not data:
                    break
                pos = offset - len(partial)  # file offset where `partial` starts
                offset += len(data)
                lines = (partial + data).split(b"\n")
                partial = lines.pop()
                for raw in lines:  # id = file offset just after the line's newline
                    pos += len(raw) + 1
                    out.append((pos, raw))
                if not final:
                    break
        if final and partial:
            out.append((offset, partial))
            partial = b""
        return out

    def log_event(pos: int, raw: bytes) -> dict[str, str]:
        return {"event": "log", "id": str(pos), "data": mask(raw.decode("utf-8", errors="replace"))}

    while True:
        if await request.is_disconnected():
            return
        run, state = await anyio.to_thread.run_sync(snapshot)
        finished = run is None or run.status not in ACTIVE_RUN_STATUSES
        for pos, raw in read_lines():
            yield log_event(pos, raw)
        for step in state["steps"]:
            if last_steps.get(step["name"]) != step["status"]:
                last_steps[step["name"]] = step["status"]
                yield {"event": "step", "data": json.dumps(step, ensure_ascii=False, default=str)}
        if state["progress"] != last_progress:
            last_progress = state["progress"]
            yield {"event": "progress", "data": json.dumps(last_progress)}
        if run is not None and run.status != last_status:
            last_status = run.status
            yield {
                "event": "status",
                "data": json.dumps(
                    {"status": run.status, "error": mask(run.error)}, ensure_ascii=False
                ),
            }
        if finished and not api.runs.is_managed(run_id):
            # the run thread may have written its last lines after the read above
            for pos, raw in read_lines(final=True):
                yield log_event(pos, raw)
            yield {"event": "end", "data": json.dumps({"status": last_status})}
            return
        await asyncio.sleep(SSE_POLL_S)


def install(app: FastAPI, settings: Settings, runs: RunManager | None = None) -> Api:
    """Mount the API, its error handlers and the static UI on *app*."""
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.responses import RedirectResponse

    api = Api(settings, runs)
    app.state.api = api
    app.include_router(build_router(api))
    from autoontology.web import playground_api

    playground_api.install(app, api)

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        # The service has no auth and listens on 127.0.0.1 only. Refuse what a web page on
        # another origin could still send: requests with a foreign Host (DNS rebinding) and
        # cross-origin state changes (a form POST needs no CORS preflight). That covers
        # every route -- the API, the /ingest shims (POST /ingest/delete tears a graph
        # down) and the /v1 passthrough -- except the health probes. Upstream (Host
        # 127.0.0.1:7800, no Origin) and curl/CLI clients pass.
        if guarded_path(request.url.path):
            host = host_name(request.headers.get("host") or "")
            if host not in LOCAL_HOSTS:
                return JSONResponse(
                    {
                        "error": "forbidden_host",
                        "detail": "the API answers on 127.0.0.1/localhost only",
                    },
                    status_code=403,
                )
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                site = request.headers.get("sec-fetch-site", "")
                same = (
                    origin is None
                    or urlsplit(origin).netloc.lower()
                    == (request.headers.get("host") or "").lower()
                )
                if not same or site == "cross-site":
                    return JSONResponse(
                        {"error": "cross_origin", "detail": "cross-origin requests are refused"},
                        status_code=403,
                    )
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.error, "detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        if request.url.path.startswith(PREFIX):
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )[:600]
            return JSONResponse({"error": "invalid_request", "detail": detail}, status_code=400)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("autoontology.web").error(
            "%s %s failed", request.method, request.url.path, exc_info=exc
        )
        return JSONResponse(
            {"error": "internal_error", "detail": f"{type(exc).__name__} (see service.log)"},
            status_code=500,
        )

    install_ui(app)

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    return api


#: The built UI (``ui/`` -> ``pnpm build``), committed so the service needs no Node.
UI_DIST = Path(__file__).parent / "static" / "dist"
UI_MISSING = (
    "the web UI is not built: run `uv run autoontology tools build-ui` (needs Node.js and pnpm)"
)


def install_ui(app: FastAPI, dist: Path | None = None) -> None:
    """Serve the single-page UI at ``/ui/``: real files as they are (hashed ``assets/`` are
    cached for good), every other ``/ui/*`` path gets ``index.html`` (client-side routes)."""
    from fastapi.responses import RedirectResponse

    root = (dist or UI_DIST).resolve()

    def index() -> Response:
        page = root / "index.html"
        if not page.is_file():
            return JSONResponse({"error": "ui_not_built", "detail": UI_MISSING}, status_code=503)
        return FileResponse(
            page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/ui", include_in_schema=False)
    def _ui_root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.get("/ui/{path:path}", include_in_schema=False)
    def _ui(path: str) -> Response:
        if path:
            try:
                candidate = (root / path).resolve()
            except (ValueError, OSError):  # e.g. a NUL byte in the path
                return JSONResponse(
                    {"error": "not_found", "detail": "invalid UI path"}, status_code=404
                )
            if candidate.is_relative_to(root) and candidate.is_file():
                immutable = candidate.parent == root / "assets"
                return FileResponse(
                    candidate,
                    headers={
                        "Cache-Control": "public, max-age=31536000, immutable"
                        if immutable
                        else "no-cache"
                    },
                )
            if path.startswith("assets/") or "." in path.rsplit("/", 1)[-1]:
                # a missing file, not a client route: no HTML for a script/style URL
                return JSONResponse(
                    {"error": "not_found", "detail": f"no UI file {path!r}"}, status_code=404
                )
        return index()
