# SPDX-License-Identifier: MIT
"""REST + SSE routes of the Playground page, under ``/api/playground/``.

* ``POST /chat`` (SSE): RAG over the latest ontology snapshot (``playground.service``).
* ``POST /sql`` (SSE): upstream's NL->SQL agent (``POST /api/chat/completions`` on the
  backend), events relayed verbatim (masked).
* ``POST /sparql``, ``GET /sparql/examples``: the SPARQL console over the latest snapshot
  (its export's ``ontology.ttl`` when that export's ``export.json`` names this very
  snapshot, else a Turtle file written on demand to
  ``<home>/cache/ontology-r<rev>-<key>.ttl``; see :func:`snapshot_ttl`).

SSE streams are POST responses read with ``fetch`` (not ``EventSource``); every event's
data is JSON. The workspace's playground slot (409 ``playground_busy``) is taken before
the response starts and released when the stream ends or the client goes away.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from autoontology.playground.index import DEFAULT_TOP_K
from autoontology.playground.llm import LLMError, llm_for
from autoontology.playground.service import (
    EmbedderCache,
    Playground,
    PlaygroundError,
    Scope,
)
from autoontology.playground.sparql import (
    DEFAULT_LIMIT,
    EXAMPLES,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    SparqlError,
    SparqlRunner,
)
from autoontology.web.api import PREFIX, Api, ApiError, mask

logger = logging.getLogger("autoontology.web")

UPSTREAM_USER = "autoontology"
UPSTREAM_READ_TIMEOUT_S = 180.0
STORAGE_MISSING = "Conversation storage unavailable"
#: tests replace it with an httpx.MockTransport
UPSTREAM_TRANSPORT: httpx.AsyncBaseTransport | None = None


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(_In):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatIn(_In):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=40)
    top_k: int = Field(DEFAULT_TOP_K, ge=1, le=40)
    conversation_id: str | None = Field(None, pattern="^[0-9a-f]{32}$")


class SqlIn(_In):
    #: the structured source whose database the query targets (default: upstream picks)
    source: str | None = Field(None, max_length=80)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class SparqlIn(_In):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


def _event(name: str, data: Any) -> dict[str, str]:
    return {"event": name, "data": json.dumps(mask(data), ensure_ascii=False, default=str)}


def _err(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, (PlaygroundError, LLMError, SparqlError)):
        return exc.status, exc.code, exc.message
    if isinstance(exc, ApiError):
        return exc.status, exc.error, exc.detail
    return 500, "internal_error", f"{type(exc).__name__} (see service.log)"


def _api_error(exc: Exception) -> ApiError:
    status, code, message = _err(exc)
    return ApiError(status, code, mask(message))


async def _guarded(
    stack: ExitStack, events: AsyncIterator[dict[str, Any]]
) -> AsyncIterator[dict[str, str]]:
    """Relay *events* as SSE, an exception as a final ``error`` event; release the slot."""
    try:
        async for ev in events:
            yield _event(ev["event"], ev["data"])
    except Exception as exc:  # noqa: BLE001 - reported to the client
        status, code, message = _err(exc)
        if status >= 500 and code == "internal_error":
            logger.error("playground stream failed", exc_info=exc)
        yield _event("error", {"status": status, "error": code, "detail": message})
    finally:
        stack.close()


def build_router(api: Api, app: FastAPI) -> APIRouter:
    router = APIRouter(prefix=f"{PREFIX}/playground")
    sparql = SparqlRunner()
    app.state.sparql = sparql  # closed by the app's lifespan (web.app.create_app)

    def pg() -> Playground:
        found = getattr(app.state, "playground", None)
        if found is None:
            found = Playground(
                api.store,
                model_for=api.latest_model,
                llm_factory=lambda: llm_for(api.settings),
                embedder_factory=EmbedderCache(lambda: api.settings, api.settings),
            )
            app.state.playground = found
        return found

    def scope() -> Scope:
        try:
            return pg().scope()
        except PlaygroundError as exc:
            raise _api_error(exc) from exc

    def hold(s: Scope) -> ExitStack:
        try:
            return pg().acquire(s)
        except PlaygroundError as exc:
            raise _api_error(exc) from exc

    # --- index / chat -------------------------------------------------------------------

    @router.get("/index")
    def index_status() -> dict[str, Any]:
        return pg().index_status(scope())

    @router.post("/index")
    async def index_build() -> dict[str, Any]:
        s = scope()
        with hold(s):
            try:
                await anyio.to_thread.run_sync(pg().index, s)
            except (PlaygroundError, ApiError) as exc:
                raise _api_error(exc) from exc
        return pg().index_status(s)

    @router.post("/chat")
    async def chat(body: ChatIn) -> EventSourceResponse:
        s = scope()
        if body.conversation_id:
            conv = pg().pg.get_conversation(body.conversation_id)
            if conv is None or conv["scope"] != s.key:
                raise ApiError(
                    404,
                    "unknown_conversation",
                    f"no conversation {body.conversation_id!r} for {s.key!r}",
                )
        try:
            llm = pg().llm_factory()
        except LLMError as exc:
            raise _api_error(exc) from exc
        stack = hold(s)

        async def events() -> AsyncIterator[dict[str, Any]]:
            async for ev in pg().ask(
                s,
                body.question,
                history=[t.model_dump() for t in body.history],
                top_k=body.top_k,
                conversation_id=body.conversation_id,
                llm=llm,
            ):
                yield ev

        # the generator's finally never runs when the client leaves before the body starts:
        # the background task (after the response, whatever happened) releases the slot too
        return EventSourceResponse(
            _guarded(stack, events()), background=BackgroundTask(stack.close)
        )

    @router.get("/conversations")
    def conversations() -> dict[str, Any]:
        return {"conversations": pg().pg.list_conversations(scope().key)}

    @router.get("/conversations/{conversation_id}")
    def conversation(conversation_id: str) -> dict[str, Any]:
        conv = pg().pg.get_conversation(conversation_id)
        if conv is None:
            raise ApiError(404, "unknown_conversation", f"no conversation {conversation_id!r}")
        return mask({**conv, "messages": pg().pg.messages(conversation_id)})

    @router.delete("/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, Any]:
        if not pg().pg.delete_conversation(conversation_id):
            raise ApiError(404, "unknown_conversation", f"no conversation {conversation_id!r}")
        return {"deleted": conversation_id}

    # --- NL -> SQL (upstream) -----------------------------------------------------------

    @router.post("/sql")
    async def sql(body: SqlIn) -> EventSourceResponse:
        target_db = None
        if body.source:
            src = api.source(body.source)
            if src.kind not in ("files", "database"):
                raise ApiError(400, "not_structured", f"source {src.name!r} is a {src.kind} source")
            target_db = src.catalog_database_id
        else:  # the snapshot's first built structured source (none: upstream picks)
            try:
                structured = scope().structured
            except ApiError:
                structured = []
            target_db = next((x.catalog_database_id for x in structured), None)
        payload: dict[str, Any] = {"question": body.question}
        if target_db:
            payload["target_db"] = target_db
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=UPSTREAM_READ_TIMEOUT_S), transport=UPSTREAM_TRANSPORT
        )
        notices: list[str] = []
        url = f"{api.settings.backend_url}/api/chat/completions"
        headers = {
            "x-auto-ontology-user-id": UPSTREAM_USER,
            "x-auto-ontology-source": "autoontology-playground",
        }

        async def open_stream(with_conversation: bool) -> httpx.Response:
            data = dict(payload)
            if with_conversation and body.conversation_id:
                data["conversation_id"] = str(body.conversation_id)
            req = http.build_request("POST", url, json=data, headers=headers)
            return await http.send(req, stream=True)

        try:
            resp = await open_stream(True)
            if resp.status_code == 503 and body.conversation_id:
                raw = (await resp.aread()).decode("utf-8", "replace")
                await resp.aclose()
                if STORAGE_MISSING in raw:
                    # upstream keeps conversations in the Next.js frontend's `frontend.*`
                    # schema, which AutoOntology does not run: ask statelessly instead
                    notices.append(
                        "the upstream conversation store (frontend schema) is "
                        "missing: asking without the earlier turns"
                    )
                    resp = await open_stream(False)
            if resp.status_code >= 400:
                raw = (await resp.aread()).decode("utf-8", "replace")
                await resp.aclose()
                await http.aclose()
                try:
                    detail = str(json.loads(raw).get("detail") or raw)
                except (ValueError, AttributeError):
                    detail = raw
                if resp.status_code == 409:
                    raise ApiError(
                        409,
                        "sql_unavailable",
                        "cannot start the data query: the structured source has no "
                        "semantic layer (Terms) or a query of this conversation "
                        "is still running. "
                        f"(upstream: {mask(detail)[:300]})",
                    )
                raise ApiError(
                    502 if resp.status_code >= 500 else resp.status_code,
                    "upstream_error",
                    f"upstream {resp.status_code}: {mask(detail)[:400]}",
                )
        except httpx.HTTPError as exc:
            await http.aclose()
            raise ApiError(
                503, "backend_unavailable", f"upstream backend unreachable: {type(exc).__name__}"
            ) from exc

        async def relay() -> AsyncIterator[dict[str, str]]:
            try:
                yield _event(
                    "meta",
                    {
                        "target_db": target_db,
                        "notices": notices,
                        "stateless": bool(notices) or not body.conversation_id,
                    },
                )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # ": ping" keep-alives
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        yield _event("done", {})
                        return
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    name = str(ev.get("type") or "message") if isinstance(ev, dict) else "message"
                    yield _event(name, ev)
                yield _event(
                    "error",
                    {
                        "status": 502,
                        "error": "stream_truncated",
                        "detail": "upstream stream ended without [DONE]",
                    },
                )
            except httpx.HTTPError as exc:
                yield _event(
                    "error",
                    {
                        "status": 502,
                        "error": "upstream_error",
                        "detail": f"upstream stream broke: {type(exc).__name__}",
                    },
                )
            finally:
                await cleanup()

        async def cleanup() -> None:
            # idempotent; also the background task, for a client gone before the body started
            await resp.aclose()
            await http.aclose()

        return EventSourceResponse(relay(), background=BackgroundTask(cleanup))

    # --- SPARQL -------------------------------------------------------------------------

    @router.get("/sparql/examples")
    def sparql_examples() -> dict[str, Any]:
        return {"examples": EXAMPLES}

    @router.post("/sparql")
    async def run_sparql(body: SparqlIn) -> dict[str, Any]:
        head, model = api.snapshot()

        def work() -> dict[str, Any]:
            path = snapshot_ttl(api.settings, head, model)
            key = snapshot_key(api.settings, head)
            return {
                **sparql.run_file(key, path, body.query, limit=body.limit),
                "revision": head.revision,
                "ttl": path.name,
            }

        try:
            return mask(await anyio.to_thread.run_sync(work))
        except SparqlError as exc:
            raise _api_error(exc) from exc

    return router


def snapshot_key(settings: Any, head: Any) -> str:
    """Identity of snapshot *head* beyond its revision number -- revisions restart in a
    recreated store and ``exports/`` may be shared by several homes: workspace, revision
    and a hash of the snapshot's run id and creation time."""
    digest = hashlib.sha256(
        f"{settings.workspace}\0{head.run_id}\0{head.created_at}".encode()
    ).hexdigest()[:12]
    return f"r{head.revision}-{digest}"


def export_matches(settings: Any, head: Any, folder: Path) -> bool:
    """Whether the export in *folder* was written from snapshot *head* (its marker)."""
    from autoontology.engine.export_job import read_export_metadata

    meta = read_export_metadata(folder) or {}
    return (
        meta.get("workspace") == settings.workspace
        and meta.get("revision") == head.revision
        and meta.get("snapshot_run") == head.run_id
        and meta.get("snapshot_created_at") == head.created_at
    )


def snapshot_ttl(settings: Any, head: Any, model: Any) -> Path:
    """The Turtle file of snapshot *head*: its export's ``ontology.ttl`` when that export
    was written from this very snapshot (:func:`export_matches`), else
    ``<home>/cache/ontology-<snapshot_key>.ttl``, written from the model on demand."""
    from autoontology.engine.export_job import revision_dir_name
    from autoontology.export import rdf

    folder = Path(settings.exports_dir) / revision_dir_name(head.revision)
    exported = folder / "ontology.ttl"
    if exported.is_file() and export_matches(settings, head, folder):
        return exported
    cached = Path(settings.home) / "cache" / f"ontology-{snapshot_key(settings, head)}.ttl"
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(f".{cached.name}.{uuid.uuid4().hex}.tmp")
        try:
            rdf.to_graph(model).serialize(destination=str(tmp), format="turtle", encoding="utf-8")
            tmp.replace(cached)
        finally:
            tmp.unlink(missing_ok=True)
    return cached


def install(app: FastAPI, api: Api) -> None:
    app.include_router(build_router(api, app))
