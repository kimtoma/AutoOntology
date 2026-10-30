# SPDX-License-Identifier: MIT
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
import unicodedata
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
from autoontology.config import LOCAL_HOSTS, Settings, host_name
from autoontology.store.db import ACTIVE_RUN_STATUSES, Run, Source, Store
from autoontology.web.runs import (
    RUN_ID_RE,
    TOP_STEPS,
    WORKSPACE,
    RunManager,
    RunStartError,
    run_log_path,
)

PREFIX = "/api"
#: Host header names the API answers to (DNS-rebinding guard); "testserver" = TestClient.
STRUCTURED_KINDS = ("files", "database")
#: Upload cap per request (all files together), ``AUTOONTOLOGY_UPLOAD_MAX_MB``.
DEFAULT_UPLOAD_MAX_MB = 200
#: Slack for multipart boundaries/headers when comparing Content-Length with the cap.
MULTIPART_OVERHEAD = 1024 * 1024
FILE_NAME_RE = re.compile(r"^[^/\\\x00]{1,200}$")
#: one segment of an export file path: letters (Hangul too, e.g. ``okf/concepts/계약.md``),
#: digits, ``._-``; never empty, never starting with ``.`` or ``_``
EXPORT_FILE_RE = re.compile(r"^[^\W_][\w.-]{0,200}$")
#: deepest export file: ``okf/concepts/<slug>.md``
EXPORT_PATH_DEPTH = 3
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
MODEL_CACHE_SIZE = 4
EXPORT_DIR_RE = re.compile(r"^r\d{4,}$")
#: ``/api/ontology/concepts/{id}``: a Term class or a document concept node of the model
CONCEPT_ID_RE = re.compile(r"^(term|concept)-[a-z0-9_-]{1,100}$")
ORIGINS: tuple[str, ...] = ("structured", "document", "aligned")
STATUS_CACHE_S = 15.0
#: ``GET /api/sources/freshness`` cache (``?refresh=1`` bypasses it)
FRESHNESS_CACHE_S = 60.0
#: how long a caller waits for another caller's freshness computation
FRESHNESS_WAIT_S = 120.0


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail
        #: Additional top-level fields of the error body (e.g. ``conflicts``).
        self.extra = extra


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


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject", "acknowledge"]
    #: alignment accept: same | related | narrower | broader (default: the item's)
    #: (``broader``: the concept is narrower than the Term, concept ⊑ Term; ``narrower``:
    #: Term ⊑ concept; ``related``: skos:related)
    relation: Literal["same", "related", "narrower", "broader"] | None = None
    #: alignment accept: the item's Term (default) or one of its candidates
    term_id: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=2000)


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: apply: the review decisions -> a new snapshot, no model call (takes only `force`)
    kind: Literal["build", "export", "apply"]
    #: apply: write a new revision even with no pending decision
    force: bool = False
    #: build: rebuild only these sources (align/merge/snapshot still cover everything)
    sources: list[str] | None = Field(default=None, max_length=100)
    #: build: continue the latest build run
    resume: bool = False
    #: export: the snapshot revision (default: the latest)
    revision: int | None = Field(default=None, ge=1)
    #: export: all | rdf | ossie | okf
    format: Literal["all", "rdf", "ossie", "okf"] | None = None


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


#: Header ``tailscale serve`` adds for tailnet clients (absent on public Funnel traffic).
TAILSCALE_LOGIN_HEADER = "tailscale-user-login"
#: Header tailscaled sets on public Funnel traffic.
TAILSCALE_FUNNEL_HEADER = "tailscale-funnel-request"


def tailscale_refusal(
    settings: Settings, login: str | None, *, funnel: bool = False
) -> tuple[str, str] | None:
    """Why a request on a configured tailnet hostname is refused, or None to let it through.
    The login itself is never part of the answer."""
    login = (login or "").strip().lower()
    if funnel or not login:
        return ("tailscale_identity_required", "this hostname is served to tailnet clients only")
    if settings.tailscale_logins and login not in settings.tailscale_logins:
        return ("access_denied", "this tailnet login is not allowed")
    return None


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
        #: parsed models by (revision, snapshot run): a recreated store reuses revisions
        self._models: dict[tuple[int, str], Any] = {}
        #: the concept routes' :class:`~autoontology.web.concepts.ConceptIndex`, same keys
        self._concepts: dict[tuple[int, str], Any] = {}
        self._model_lock = threading.Lock()
        self._status: tuple[float, dict[str, Any]] | None = None
        #: One probe round at a time: concurrent cache misses wait for it and share it.
        self._status_lock = threading.Lock()
        #: The service's chat provider registry (set by ``create_app``).
        self.providers: Any = None
        #: One provider test (``POST /api/providers/test``) at a time.
        self._provider_test_lock = threading.Lock()
        #: ``GET /api/sources/freshness``: (monotonic time, rows), one computation at a time
        self._freshness: tuple[float, list[dict[str, Any]]] | None = None
        self._freshness_lock = threading.Lock()
        #: the computation in progress (concurrent callers wait for it), or None
        self._freshness_flight: threading.Event | None = None

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
        from autoontology.engine.build import is_top_step, registration_order, step_names

        rows = self.store.get_steps(run.id)
        by_name = {s.name: s for s in rows}
        top = list(TOP_STEPS.get(run.kind, ()))
        if run.kind == "build" and run.status in ACTIVE_RUN_STATUSES:
            top = step_names(registration_order(self.store))  # the plan, before it runs
        names = list(dict.fromkeys([*(s.name for s in rows if is_top_step(s.name)), *top]))
        if top:  # plan order first, then rows the plan does not know
            names = [n for n in top if n in names] + [n for n in names if n not in top]
        steps = []
        for name in names:
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

        last = self.store.latest_run(source.id) or next(
            (r for r in self.store.list_runs(None, limit=50) if r.kind == "build"), None
        )
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

    # --- ontology snapshot ---------------------------------------------------------------

    def snapshot(self, revision: int | None = None) -> tuple[Any, Any]:
        """``(Snapshot without model, MergedOntology)`` of *revision* (default: published),
        parsed once per revision; 404 ``no_snapshot`` before the first build."""
        from autoontology.ontology.model import MergedOntology
        from autoontology.store.operations import published_revision

        live = published_revision(self.store) if revision is None else None
        head = (
            (
                self.store.get_snapshot(live, with_model=False)
                if live
                else self.store.latest_snapshot(with_model=False)
            )
            if revision is None
            else self.store.get_snapshot(revision, with_model=False)
        )
        if head is None:
            if revision is None:
                raise ApiError(
                    404, "no_snapshot", "no ontology yet: run a build (`autoontology build`)"
                )
            raise ApiError(404, "unknown_revision", f"unknown revision {revision}")
        key = (head.revision, head.run_id)
        with self._model_lock:
            model = self._models.get(key)
        if model is None:
            full = self.store.get_snapshot(head.revision)
            assert full is not None and full.model is not None
            model = MergedOntology.from_dict(full.model)
            with self._model_lock:
                if len(self._models) >= MODEL_CACHE_SIZE:
                    self._models.pop(next(iter(self._models)))
                self._models[key] = model
        return head, model

    def concepts(self, revision: int | None = None) -> tuple[Any, Any, Any]:
        """``(head, model, ConceptIndex)`` of *revision* (default: the latest); the index
        is computed once per snapshot."""
        from autoontology.web.concepts import concept_index

        head, model = self.snapshot(revision)
        key = (head.revision, head.run_id)
        with self._model_lock:
            index = self._concepts.get(key)
        if index is None:
            index = concept_index(model)
            with self._model_lock:
                if len(self._concepts) >= MODEL_CACHE_SIZE:
                    self._concepts.pop(next(iter(self._concepts)))
                self._concepts[key] = index
        return head, model, index

    def freshness(self, refresh: bool = False) -> tuple[list[dict[str, Any]], float]:
        """``(rows, age in seconds)`` of the per-source freshness, computed at most every
        :data:`FRESHNESS_CACHE_S` (``refresh``: now). One computation at a time (file hashes,
        database connections), outside the lock: a caller arriving meanwhile waits for it
        and shares its result; only the cache swap holds the lock."""
        from autoontology.web.freshness import freshness

        with self._freshness_lock:
            cached = self._freshness
            fresh = cached and time.monotonic() - cached[0] < FRESHNESS_CACHE_S
            if cached and fresh and not refresh:
                return cached[1], time.monotonic() - cached[0]
            flight = self._freshness_flight
            leader = flight is None
            if leader:
                flight = self._freshness_flight = threading.Event()
        assert flight is not None
        if not leader:
            flight.wait(FRESHNESS_WAIT_S)
            done = self.cached_freshness()
            if done is not None:
                return done
        try:
            rows = freshness(self.store, self.settings)
            with self._freshness_lock:
                self._freshness = (time.monotonic(), rows)
            return rows, 0.0
        finally:
            if leader:
                with self._freshness_lock:
                    self._freshness_flight = None
                flight.set()

    def cached_freshness(self) -> tuple[list[dict[str, Any]], float] | None:
        """The last computed freshness and its age, never computing it (None: never)."""
        with self._freshness_lock:
            cached = self._freshness
        return (cached[1], time.monotonic() - cached[0]) if cached else None

    def inbox(self, *, sources: bool = False) -> Any:
        """The review :class:`~autoontology.web.review.Inbox` over the latest snapshot.
        Stale sources come from the freshness cache; *sources*: computed when it expired
        (the badge and the other categories never pay for file hashing)."""
        from autoontology.web.review import Inbox

        model = previous = None
        head = self.store.latest_snapshot(with_model=False)
        if head is not None:
            _, model = self.snapshot(head.revision)
            older = self.store.previous_revision(head.revision)
            if older is not None:
                _, previous = self.snapshot(older)
        if sources:
            rows = self.freshness()[0]
        else:
            cached = self.cached_freshness()
            rows = cached[0] if cached else []
        return Inbox(self.store, model, previous, rows)

    def decided_by(self, request: Request) -> str:
        """The tailnet login of a request that came through a configured tailnet hostname
        (``tailscale serve`` sets it), else ``local``."""
        host = host_name(request.headers.get("host") or "")
        login = (request.headers.get(TAILSCALE_LOGIN_HEADER) or "").strip().lower()
        if host in self.settings.tailscale_hosts and login:
            return login[:200]
        return "local"

    def latest_model(self) -> tuple[int, Any]:
        """``(revision, model)`` of the latest snapshot (the playground's scope)."""
        head, model = self.snapshot()
        return head.revision, model

    # --- status ------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Read-only service status (cached ``STATUS_CACHE_S``): backend health, Postgres
        reachability, Ollama models present, kordoc availability and the provider summary
        (names and models only, never keys)."""
        cached = self._status
        if cached and time.monotonic() - cached[0] < STATUS_CACHE_S:
            return cached[1]
        with self._status_lock:
            cached = self._status  # another request may have probed while we waited
            if cached and time.monotonic() - cached[0] < STATUS_CACHE_S:
                return cached[1]
            result = self._probe_status()
            self._status = (time.monotonic(), result)
            return result

    def _probe_status(self) -> dict[str, Any]:
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
        ocr_ready = False
        if kordoc_ok:
            try:
                ocr_ready = bool(kordoc.ocr_models_status(s.home)["ready"])
            except Exception:  # noqa: BLE001 - optional component
                ocr_ready = False
        from autoontology import formats
        from autoontology.config import CLI_PROVIDERS

        chat = self._chat_provider_status()
        if s.chat_provider in CLI_PROVIDERS:
            checks[s.chat_provider] = {
                "status": "ok" if chat["available"] and chat["logged_in"] else "error",
                "detail": chat["detail"],
                **({"remedy": chat["remedy"]} if chat["remedy"] else {}),
            }
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
                "chat": chat,
                "embed": {"name": s.embed_provider, "model": s.effective_embed_model},
            },
            "kordoc_available": kordoc_ok,
            "ocr_ready": ocr_ready,
            "vision_model": s.vision_model or None,
            "formats": formats.describe(),
            "service_url": s.service_url,
            "checks": checks,
            "doctor": "uv run autoontology doctor",
            "upload_max_mb": round(upload_limit_bytes() / (1024 * 1024), 1),
            "remote": {
                "tailscale_hosts": list(s.tailscale_hosts),
                "logins_restricted": bool(s.tailscale_logins),
            },
        }
        return mask(result)

    def _chat_provider_status(self) -> dict[str, Any]:
        """The active chat provider's status (CLI login, endpoint) and its last test."""
        from autoontology.web.providers import probe
        from autoontology.web.providers.router import Registry

        s = self.settings
        out: dict[str, Any] = {"name": s.chat_provider, "model": s.effective_chat_model}
        try:
            st = (self.providers or Registry(s)).status()
            out.update(
                available=st.available, logged_in=st.logged_in, detail=st.detail, remedy=st.remedy
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            out.update(
                available=False,
                logged_in=False,
                detail=f"status failed: {type(exc).__name__}",
                remedy="uv run autoontology doctor",
            )
        from autoontology.web.providers.base import scrub

        out["detail"] = scrub(out["detail"], 300)
        last = probe.last_test(s)
        # entries written before home paths were scrubbed may still carry one
        out["last_test"] = (
            {k: scrub(v, 300) if isinstance(v, str) else v for k, v in last.items()}
            if last
            else last
        )
        return out

    async def test_provider(self, app: Any) -> dict[str, Any]:
        """One pong through this service's own /v1 router, in-process (409 while another
        test runs)."""
        from autoontology.web.providers import probe

        if not self._provider_test_lock.acquire(blocking=False):
            raise ApiError(409, "test_in_progress", "a provider test is already running")
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                result = await probe.run_test_async(self.settings, client=c, via="service")
        finally:
            self._provider_test_lock.release()
        self._status = None  # the next /api/status shows the new last_test
        # error/answer strings are scrubbed by the probe; mask() would also blank the
        # usage counters (their keys contain "token")
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
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        call = _job_call(api, req)
        try:
            run_id = api.runs.start(req.kind, WORKSPACE, call)
        except RunStartError as exc:
            code = {409: "run_active", 503: "backend_unavailable"}.get(
                exc.status, "run_not_started"
            )
            raise ApiError(exc.status, code, mask(str(exc))) from exc
        return {"run_id": run_id}

    @router.get("/runs")
    def list_runs(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
        names = {s.id: s.name for s in api.store.list_sources()}
        runs = api.store.list_runs(None, limit=limit)
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

    # review inbox, revisions, freshness (living ontology operations) --------------------

    def _review(call: Any) -> Any:
        from autoontology.web.review import ReviewError

        try:
            return call()
        except ReviewError as exc:
            raise ApiError(exc.status, exc.error, exc.detail) from exc

    @router.get("/review")
    def review(
        category: str | None = Query(None, max_length=40),
        status: str = Query("pending", max_length=20),
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        sources: bool = False,
    ) -> dict[str, Any]:
        from autoontology.web.review import CATEGORIES, STATUSES

        if category and category not in CATEGORIES:
            raise ApiError(
                400, "invalid_category", f"category must be one of {', '.join(CATEGORIES)}"
            )
        if status not in STATUSES:
            raise ApiError(400, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        inbox = api.inbox(sources=sources or category == "stale_sources")
        head = api.store.latest_snapshot(with_model=False)
        return {
            "revision": head.revision if head else None,
            **inbox.listing(category=category, status=status, page=page, size=size),
        }

    @router.get("/review/pending-count")
    def review_pending_count() -> dict[str, Any]:
        listing = api.inbox().listing(category=None, status="pending", page=1, size=1)
        return {
            "pending": listing["counts"]["total"],
            "counts": listing["counts"],
            "unapplied": listing["unapplied"],
        }

    @router.post("/review/{item_id:path}/decision")
    def review_decide(item_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            req = DecisionIn.model_validate(body)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        inbox = api.inbox()
        decision = _review(
            lambda: inbox.decide(
                item_id,
                decision=req.decision,
                relation=req.relation,
                term_id=req.term_id,
                note=req.note.strip(),
                decided_by=api.decided_by(request),
            )
        )
        item = {**inbox.items[item_id], "decision": decision.to_dict()}
        return {"item": item, "decision": decision.to_dict()}

    @router.delete("/review/{item_id:path}/decision")
    def review_undo(item_id: str) -> dict[str, Any]:
        inbox = api.inbox()
        if not _review(lambda: inbox.undo(item_id)):
            raise ApiError(404, "no_decision", f"no decision on {item_id[:80]!r}")
        return {"removed": item_id}

    @router.get("/ontology/revisions")
    def ontology_revisions(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        from autoontology.web.revisions import revision_list

        return {"revisions": revision_list(api.store, limit)}

    @router.get("/ontology/diff")
    def ontology_diff(
        from_: int | None = Query(None, alias="from", ge=1),
        to: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        from autoontology.web.revisions import diff

        new_head, new = api.snapshot(to)
        if from_ is None:
            from_ = api.store.previous_revision(new_head.revision)
        old_head = old = None
        if from_ is not None:
            old_head, old = api.snapshot(from_)
        return {
            "from": from_,
            "to": new_head.revision,
            **diff(
                old,
                new,
                old_sources=old_head.sources if old_head else [],
                new_sources=new_head.sources,
            ),
        }

    @router.get("/sources/freshness")
    def sources_freshness(refresh: bool = False) -> dict[str, Any]:
        rows, age = api.freshness(refresh)
        return {
            "sources": rows,
            "stale": sum(bool(r.get("stale")) for r in rows),
            "age_s": round(age, 1),
            "cache_s": FRESHNESS_CACHE_S,
        }

    # ontology (the latest snapshot) -----------------------------------------------------

    @router.get("/ontology")
    def ontology(revision: int | None = Query(None, ge=1)) -> dict[str, Any]:
        head, model = api.snapshot(revision)
        return {
            "revision": head.revision,
            "created_at": head.created_at,
            "run_id": head.run_id,
            "workspace": model.name,
            "base_iri": model.base,
            "sources": head.sources,
            "counts": head.counts,
            "alignment": model.alignment_counts,
        }

    @router.get("/ontology/graph")
    def ontology_graph(
        layer: str = "all", evidence: bool = False, revision: int | None = Query(None, ge=1)
    ) -> dict[str, Any]:
        from autoontology.web.graph import LAYERS, max_nodes_from_env, to_view

        if layer not in LAYERS:
            raise ApiError(400, "invalid_layer", f"layer must be one of {', '.join(LAYERS)}")
        head, model = api.snapshot(revision)
        cap = max_nodes_from_env()
        return {
            **to_view(model, layer, evidence=evidence, max_nodes=cap),
            "revision": head.revision,
            "max_nodes": cap,
            "warnings": [],
        }

    @router.get("/ontology/concepts")
    def ontology_concepts(
        q: str = Query("", max_length=200),
        kind: str = Query("", max_length=40),
        origin: str = Query("", max_length=20),
        source: str = Query("", max_length=80),
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=200),
        revision: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        if origin and origin not in ORIGINS:
            raise ApiError(400, "invalid_origin", f"origin must be one of {', '.join(ORIGINS)}")
        head, _, index = api.concepts(revision)
        items = index.items
        needle = q.strip().casefold()
        if needle:
            items = [i for i in items if needle in i["_text"]]
        if kind:
            items = [i for i in items if i["kind"] == kind]
        if origin:
            items = [i for i in items if i["origin"] == origin]
        if source:
            items = [i for i in items if i["source"] == source]
        total = len(items)
        chunk = items[(page - 1) * size : page * size]
        return {
            "revision": head.revision,
            "items": [{k: v for k, v in i.items() if not k.startswith("_")} for i in chunk],
            "total": total,
            "page": page,
            "size": size,
            "pages": max(1, -(-total // size)),
        }

    @router.get("/ontology/concepts/{concept_id}")
    def ontology_concept(
        concept_id: str, revision: int | None = Query(None, ge=1)
    ) -> dict[str, Any]:
        from autoontology.web.concepts import concept_detail

        if not CONCEPT_ID_RE.fullmatch(concept_id):
            raise ApiError(400, "invalid_concept_id", f"invalid concept id {concept_id!r}")
        head, model, index = api.concepts(revision)
        detail = concept_detail(model, concept_id, index)
        if detail is None:
            raise ApiError(404, "unknown_concept", f"unknown concept {concept_id!r}")
        return {"revision": head.revision, **detail}

    # exports ----------------------------------------------------------------------------

    @router.get("/exports")
    def list_exports() -> dict[str, Any]:
        root = Path(api.settings.exports_dir)
        folders = (
            sorted(
                (p for p in root.iterdir() if p.is_dir() and EXPORT_DIR_RE.fullmatch(p.name)),
                key=lambda p: int(p.name[1:]),
                reverse=True,
            )
            if root.is_dir()
            else []
        )
        out = []
        from autoontology.store.operations import visible_revision

        for folder in folders:
            if not visible_revision(api.store, int(folder.name[1:])):
                continue
            files = sorted(
                (
                    {"name": f.relative_to(folder).as_posix(), "bytes": f.stat().st_size}
                    for f in folder.rglob("*")
                    if f.is_file() and _export_path_ok(f.relative_to(folder).as_posix())
                ),
                key=lambda f: f["name"],
            )
            dirs: dict[str, dict[str, Any]] = {}
            for f in files:
                head, sep, rest = f["name"].partition("/")
                if sep:
                    entry = dirs.setdefault(head, {"name": head, "files": [], "bytes": 0})
                    entry["files"].append(rest)
                    entry["bytes"] += f["bytes"]
            out.append(
                {
                    "revision": int(folder.name[1:]),
                    "dir": folder.name,
                    "created_at": _iso(folder.stat().st_mtime),
                    "files": files,
                    #: the sub-folders (upstream/, ossie/, okf/) with their files
                    "dirs": [dirs[k] for k in sorted(dirs)],
                }
            )
        return {"exports": out}

    @router.get("/exports/{rev}/{file_path:path}")
    def download_export(rev: str, file_path: str, inline: bool = False) -> Response:
        if rev.isdigit():
            from autoontology.engine.export_job import revision_dir_name

            rev = revision_dir_name(int(rev))
        if not EXPORT_DIR_RE.fullmatch(rev) or not _export_path_ok(file_path):
            raise ApiError(400, "invalid_path", "invalid export path")
        from autoontology.store.operations import visible_revision

        if not visible_revision(api.store, int(rev[1:])):
            raise ApiError(404, "unpublished_export", "this revision has not been published")
        root = Path(api.settings.exports_dir).resolve()
        candidate = (root / rev / unicodedata.normalize("NFC", file_path)).resolve()
        if candidate.is_file() and candidate.is_relative_to(root / rev):
            if inline:
                if candidate.stat().st_size > 5 * 1024 * 1024:
                    raise ApiError(413, "too_large", "file too large to preview")
                return Response(
                    candidate.read_text(encoding="utf-8", errors="replace"),
                    media_type="text/plain; charset=utf-8",
                )
            return FileResponse(candidate, filename=candidate.name)
        raise ApiError(404, "unknown_export", f"no export file {rev}/{file_path}")

    # status -----------------------------------------------------------------------------

    @router.get("/status")
    def status() -> dict[str, Any]:
        return api.status()

    @router.post("/providers/test")
    async def test_provider(request: Request) -> dict[str, Any]:
        return await api.test_provider(request.app)

    # sample sets ----------------------------------------------------------------------

    @router.get("/samples")
    def list_samples() -> list[dict[str, Any]]:
        from autoontology import onboarding

        return onboarding.list_sample_sets(api.settings, api.store)

    @router.post("/samples/load")
    async def load_samples(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError as exc:
            raise ApiError(400, "invalid_body", "the body must be a JSON object") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_body", "the body must be a JSON object")
        return await anyio.to_thread.run_sync(_load_samples, api, body)

    return router


def _load_samples(api: Api, body: dict[str, Any]) -> dict[str, Any]:
    """Register a sample set's sources exactly like ``autoontology demo``."""
    from autoontology import onboarding
    from autoontology.engine.jobs import JobError
    from autoontology.structured.loaders import LoadError
    from autoontology.structured.remove import RemoveError

    name = body.get("set") if isinstance(body, dict) else None
    if not isinstance(name, str) or name not in onboarding.SAMPLE_SETS:
        raise ApiError(
            400,
            "unknown_sample_set",
            f"unknown sample set {name!r} (choose one of: {', '.join(onboarding.SAMPLE_SETS)})",
        )
    replace = body.get("replace", False)
    if not isinstance(replace, bool):
        raise ApiError(400, "invalid_body", "'replace' must be true or false")
    busy_text = "a build or export is running: load the samples when it has finished"
    try:  # held while registering: no run may start in between
        api.runs.claim_idle(WORKSPACE, "samples")
    except RunStartError as exc:
        raise ApiError(409, "run_active", busy_text) from exc
    try:
        if api.store.active_runs():  # a CLI run
            raise ApiError(409, "run_active", busy_text)
        messages: list[str] = []
        try:
            registered = onboarding.register_sample_set(
                api.settings,
                api.store,
                name,
                notify=lambda m: messages.append(mask(m)),
                replace=replace,
            )
        except onboarding.SampleConflict as exc:
            raise ApiError(
                409,
                "sample_conflict",
                mask(f'{exc}; send "replace": true to replace them'),
                conflicts=[{k: mask(str(v)) for k, v in c.items()} for c in exc.conflicts],
            ) from exc
        except onboarding.SampleSetError as exc:
            raise ApiError(400, "unknown_sample_set", mask(str(exc))) from exc
        except (RemoveError, LoadError, JobError, ValueError) as exc:
            raise ApiError(409, "samples_not_loaded", mask(str(exc))) from exc
    finally:
        api.runs.release(WORKSPACE)
    return {
        "set": name,
        "sources": [{**api.source_dict(src), "action": action} for src, action in registered],
        "messages": messages,
    }


def _validation_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()
        )[:600]
    return str(exc)[:600]


def _add_locator(api: Api, body: SourceIn) -> dict[str, Any]:
    validate_name(body.name, body.kind)
    try:  # a sample load holds the workspace: it may remove a source of this name
        api.runs.claim(body.name, "register")
    except RunStartError as exc:
        raise ApiError(409, "busy", f"{exc}; try again when it has finished") from exc
    try:
        source = _add_locator_claimed(api, body)
    finally:
        api.runs.release(body.name)
    return api.source_dict(source)


def _add_locator_claimed(api: Api, body: SourceIn) -> Any:
    from autoontology.engine.extract import ensure_documents_source
    from autoontology.structured import pipeline
    from autoontology.structured.loaders import LoadError

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
    return source


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


def _export_path_ok(path: str) -> bool:
    """A file path inside an export folder: at most :data:`EXPORT_PATH_DEPTH` plain
    segments (``upstream/...``, ``ossie/...``, ``okf/concepts/...``)."""
    parts = unicodedata.normalize("NFC", path).split("/")
    return 1 <= len(parts) <= EXPORT_PATH_DEPTH and all(EXPORT_FILE_RE.fullmatch(p) for p in parts)


def _job_call(api: Api, req: RunIn):
    """``call(job_fn, store)`` for the run manager, after validating the request."""
    settings = api.settings
    if req.force and req.kind != "apply":
        raise ApiError(400, "invalid_request", "`force` is for apply runs only")
    if req.kind == "build":
        sources = [api.source(n).name for n in req.sources] if req.sources else None
        if not api.store.list_sources():
            raise ApiError(400, "no_sources", "no sources: add one first")
        return lambda fn, store: fn(store, settings, sources=sources, resume=req.resume)
    if req.kind == "apply":
        if req.sources or req.resume or req.revision or req.format:
            raise ApiError(400, "invalid_request", "apply takes only `force`")
        api.snapshot()  # 404 no_snapshot before a thread starts
        return lambda fn, store: fn(store, settings, force=req.force)
    if req.sources or req.resume:
        raise ApiError(400, "invalid_request", "export takes only `revision` and `format`")
    head, _ = api.snapshot(req.revision)  # 404 before a thread starts
    from autoontology.store.operations import visible_revision

    if not visible_revision(api.store, head.revision):
        raise ApiError(409, "unpublished_revision", "publish the reviewed changes before exporting")
    fmt = req.format or "all"
    from autoontology.store.operations import published_revision

    revision = head.revision if published_revision(api.store) else req.revision
    return lambda fn, store: fn(store, settings, revision=revision, format=fmt)


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
    from autoontology.web import settings_api

    settings_api.install(app, api)
    from autoontology.web import operations_api

    operations_api.install(app, api)

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
            if host in settings.tailscale_hosts:
                # Only tailscaled (`tailscale serve`) delivers this Host to 127.0.0.1, and it
                # adds the identity header for tailnet clients only -- never for Funnel.
                refusal = tailscale_refusal(
                    settings,
                    request.headers.get(TAILSCALE_LOGIN_HEADER),
                    funnel=TAILSCALE_FUNNEL_HEADER in request.headers,
                )
                if refusal:
                    return JSONResponse(
                        {"error": refusal[0], "detail": refusal[1]}, status_code=403
                    )
            elif host not in LOCAL_HOSTS:
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
        return JSONResponse(
            {"error": exc.error, "detail": exc.detail, **exc.extra}, status_code=exc.status
        )

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
