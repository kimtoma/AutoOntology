# SPDX-License-Identifier: MIT
"""Playground orchestration: scope, element index and RAG chat.

One :class:`Playground` per service process (``web/playground_api.py``), serving every
project. A scope is one project's latest ontology snapshot; the element index, the
conversations and the slot are keyed by the project (its id; the scope key inside it is the
project id too, so a project rename never hides its conversations)
and the index is rebuilt when the project's snapshot revision changes. Every LLM call holds
its project's slot, so one runs at a time per project (409 ``playground_busy`` otherwise);
projects never wait for each other here (the SQL relay's slots stay installation-wide,
``web/playground_api.py`` ``SqlSlots``: the upstream agent is one backend).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio

from ontology.playground import prompts
from ontology.playground.elements import Element, elements_from_model
from ontology.playground.index import DEFAULT_TOP_K, ElementIndex, Embedder, Hit, build_index
from ontology.playground.llm import LLMError, PlaygroundLLM
from ontology.playground.store import PlaygroundStore
from ontology.store.db import DEFAULT_PROJECT_ID, ProjectStore, Source, Store, scoped

logger = logging.getLogger("ontology.playground")

STRUCTURED_KINDS = ("files", "database")
MAX_TOP_K = 40


class PlaygroundError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Scope:
    #: the scope key inside the project: the project's id (stable across renames; rows of
    #: an earlier key, the ontology's name, are adopted, ``PlaygroundStore.adopt_scope``)
    key: str
    #: the snapshot revision the scope was taken from
    revision: int
    #: the snapshot's model (:class:`~ontology.ontology.model.MergedOntology`)
    model: Any
    #: every source of the model: structured first, then documents
    names: list[str]
    structured: list[Source] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: the project the scope belongs to (the outer key of index, conversations and slot)
    project_id: str = DEFAULT_PROJECT_ID
    #: the project's playground tables (:class:`PlaygroundStore` of ``project_id``)
    pg: Any = None
    #: the ontology's name (the project's display name when the snapshot was built)
    name: str = ""

    @property
    def slot_key(self) -> tuple[str, str]:
        return (self.project_id, self.key)


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    tokens: dict[str, Element]
    citations: dict[str, dict[str, str]]
    unknown: list[str]
    provider: str
    model: str
    conversation_id: str = ""
    message_id: int = 0


def embed_triplet(settings: Any) -> tuple[str, str, str]:
    """(endpoint, api key, model) of the selected embed provider, from *settings* (not from
    upstream's import-time globals, so a provider/model change applies without a restart)."""
    from ontology.config import expand_model_profile

    env = expand_model_profile(settings)
    models = settings.upstream_models

    def field_(name: str) -> str:
        return str(env.get(f"EMBED_{name}") or models.get(f"EMBED_{name}") or "")

    return field_("ENDPOINT"), field_("API_KEY"), field_("MODEL")


class EmbedderCache:
    """The embed function for the current selection, rebuilt when the embed endpoint or
    model changes (``settings_fn`` is re-read on every call)."""

    def __init__(self, settings_fn: Callable[[], Any], boot_settings: Any) -> None:
        self.settings_fn = settings_fn
        self.boot_settings = boot_settings
        self._key: tuple[str, str] | None = None
        self._value: tuple[Embedder, str] | None = None
        self._lock = threading.Lock()

    def __call__(self) -> tuple[Embedder, str]:
        endpoint, key, model = embed_triplet(self.settings_fn())
        with self._lock:
            if self._value is not None and self._key == (endpoint, model):
                return self._value
            from ontology.engine.jobs import prepare_upstream_env

            # upstream reads its env at import: load it once (any selection) before importing
            prepare_upstream_env(self.boot_settings)
            from auto_ontology.utils.embedding import EmbedParams, embed_texts

            params = EmbedParams(model_name=model, embed_invoke_url=endpoint, api_key=key)

            def embed(texts: list[str], input_type: str) -> list[list[float] | None]:
                return embed_texts(texts, params, input_type)  # type: ignore[arg-type]

            self._key, self._value = (endpoint, model), (embed, model)
            logger.info("playground embedder: %s at %s", model, endpoint)
            return self._value


class Playground:
    def __init__(
        self,
        store: Store,
        *,
        model_for: Callable[[], tuple[int, Any]],
        llm_factory: Callable[[], PlaygroundLLM],
        embedder_factory: Callable[[], tuple[Embedder, str]],
    ) -> None:
        self.store = store
        #: the default project's playground tables (callers with a scope use ``scope.pg``)
        self.pg = PlaygroundStore(scoped(store))
        #: ``model_for(project view)`` -> ``(revision, model)`` of its latest snapshot
        self.model_for = model_for
        self.llm_factory = llm_factory
        self.embedder_factory = embedder_factory
        self._indexes: dict[tuple[str, str], ElementIndex] = {}
        self._index_lock = threading.Lock()
        self._busy: set[tuple[str, str]] = set()
        self._busy_lock = threading.Lock()
        #: projects whose earlier scope rows were adopted by this process
        self._adopted: set[str] = set()

    def view(self, project: ProjectStore | str | None = None) -> ProjectStore:
        """The project view of *project* (a view, an id or slug; None = the default)."""
        if isinstance(project, ProjectStore):
            return project
        return scoped(self.store, project)

    def pg_for(self, project: ProjectStore | str | None = None) -> PlaygroundStore:
        """The playground tables of *project*."""
        return PlaygroundStore(self.view(project))

    # --- scope / concurrency ------------------------------------------------------------

    def scope(self, project: ProjectStore | str | None = None) -> Scope:
        """The project's latest snapshot (*project* None = the default project;
        ``model_for`` raises when there is none). Its structured sources are the
        project's (the SQL relay's targets)."""
        view = self.view(project)
        revision, model = self.model_for(view)
        structured, warnings = [], []
        for name in model.structured_sources:
            s = view.get_source(name)
            if s is not None and s.kind in STRUCTURED_KINDS and s.catalog_database_id:
                structured.append(s)
            else:
                warnings.append(f"structured source {name!r} is no longer registered")
        if view.id not in self._adopted:
            moved = PlaygroundStore(view).adopt_scope(view.id)
            if moved:
                logger.info("playground: %d conversation(s) of %s re-keyed", moved, view.slug)
            self._adopted.add(view.id)
        return Scope(
            key=view.id,
            name=model.name,
            revision=revision,
            model=model,
            names=[*model.structured_sources, *model.documents_sources],
            structured=structured,
            warnings=warnings,
            project_id=view.id,
            pg=PlaygroundStore(view),
        )

    @contextlib.contextmanager
    def slot(self, key: tuple[str, str] | str) -> Iterator[None]:
        with self._busy_lock:
            if key in self._busy:
                raise PlaygroundError(
                    409,
                    "playground_busy",
                    "another playground call of this project is running; wait for it to finish",
                )
            self._busy.add(key)
        try:
            yield
        finally:
            with self._busy_lock:
                self._busy.discard(key)

    def acquire(self, scope: Scope) -> contextlib.ExitStack:
        """The slot entered now (409 raised here, before a response starts); close the
        returned stack to release it."""
        stack = contextlib.ExitStack()
        stack.enter_context(self.slot(scope.slot_key))
        return stack

    # --- index --------------------------------------------------------------------------

    def embedder(self) -> tuple[Embedder, str]:
        """Asked on every use: the factory (``EmbedderCache``) follows the current selection."""
        return self.embedder_factory()

    def elements(self, scope: Scope) -> list[Element]:
        return elements_from_model(scope.model)

    def index(self, scope: Scope) -> ElementIndex:
        embed, model_name = self.embedder()
        revision = scope.revision
        sid = scope.key
        cache_key = scope.slot_key
        with self._index_lock:
            cached = self._indexes.get(cache_key)
            if cached and cached.revision == revision and cached.model == model_name:
                return cached
        elements = self.elements(scope)
        try:
            idx = build_index(
                self._pg(scope), sid, elements, revision=revision, model=model_name, embed=embed
            )
        except Exception as exc:  # noqa: BLE001 - the embed endpoint's own errors
            raise PlaygroundError(
                502,
                "embed_failed",
                f"embedding the ontology failed: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc
        with self._index_lock:
            self._indexes[cache_key] = idx
        return idx

    def _pg(self, scope: Scope) -> PlaygroundStore:
        return scope.pg if scope.pg is not None else self.pg_for(scope.project_id)

    def index_status(self, scope: Scope) -> dict[str, Any]:
        meta = self._pg(scope).index_meta(scope.key)
        revision = scope.revision
        return {
            "workspace": scope.name or scope.key,
            "project": scope.project_id,
            "scope": scope.names,
            "warnings": scope.warnings,
            "revision": revision,
            "index": meta,
            "stale": meta is None or meta["revision"] != revision,
        }

    def retrieve(self, scope: Scope, question: str, top_k: int) -> tuple[ElementIndex, list[Hit]]:
        idx = self.index(scope)
        embed, _ = self.embedder()
        try:
            vector = embed([question], "query")[0]
        except Exception as exc:  # noqa: BLE001
            raise PlaygroundError(
                502,
                "embed_failed",
                f"embedding the question failed: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc
        return idx, idx.retrieve(vector or [], top_k=top_k)

    # --- chat ---------------------------------------------------------------------------

    def _history(
        self, scope: Scope, conversation_id: str | None, history: list[dict[str, str]] | None
    ) -> tuple[str | None, list[dict]]:
        if conversation_id:
            conv = self._pg(scope).get_conversation(conversation_id)
            if conv is None or conv["scope"] != scope.key:
                raise PlaygroundError(
                    404,
                    "unknown_conversation",
                    f"no conversation {conversation_id!r} in this project",
                )
            # an incomplete (truncated) answer is not context for the next turn
            return conversation_id, [
                {"role": m["role"], "content": m["content"]}
                for m in self._pg(scope).messages(conversation_id)
                if not m["incomplete"]
            ]
        return None, list(history or [])

    async def ask(
        self,
        scope: Scope,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        top_k: int = DEFAULT_TOP_K,
        conversation_id: str | None = None,
        persist: bool = True,
        llm: PlaygroundLLM | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Events ``status`` / ``retrieved`` / ``delta`` / ``citations`` / ``done``; the last
        event's ``answer`` is the :class:`Answer`. The caller holds the source's slot. A
        truncated provider stream yields ``stream_truncated`` (the partial answer is stored
        ``incomplete``) and re-raises: no ``citations``/``done``, so it is never graded."""
        top_k = max(1, min(int(top_k or DEFAULT_TOP_K), MAX_TOP_K))
        conversation_id, past = self._history(scope, conversation_id, history)
        llm = llm or self.llm_factory()
        yield {
            "event": "status",
            "data": {
                "phase": "index",
                "provider": llm.provider,
                "model": llm.model,
                "scope": scope.names,
            },
        }
        idx, hits = await anyio.to_thread.run_sync(self.retrieve, scope, question, top_k)
        tokens = prompts.context_tokens(hits)
        yield {
            "event": "retrieved",
            "data": {
                "elements": [
                    {"ref": t, **e.chip(), "score": h.score, "via": h.via}
                    for (t, e), h in zip(tokens.items(), hits, strict=True)
                ],
                "index": {
                    "elements": len(idx),
                    "embedded": idx.embedded,
                    "reused": idx.reused,
                    "model": idx.model,
                },
                "warnings": scope.warnings,
            },
        }
        messages = prompts.chat_messages(question, hits, past)
        parts: list[str] = []
        try:
            async for text in llm.stream(messages):
                parts.append(text)
                yield {"event": "delta", "data": {"text": text}}
        except LLMError as exc:
            if exc.code != "stream_truncated":
                raise
            # keep what arrived, marked incomplete; no citations, no `done`, never graded
            partial = "".join(parts).strip()
            info: dict[str, Any] = {
                "conversation_id": conversation_id,
                "message_id": None,
                "partial_chars": len(partial),
            }
            if persist:

                def save_partial() -> tuple[str, int]:
                    pg = self._pg(scope)
                    cid = conversation_id or pg.create_conversation(scope.key, question[:80])
                    pg.add_message(cid, "user", question)
                    mid = pg.add_message(
                        cid,
                        "assistant",
                        partial,
                        retrieved=[h.element.id for h in hits],
                        provider=llm.provider,
                        model=llm.model,
                        incomplete=True,
                    )
                    return cid, mid

                info["conversation_id"], info["message_id"] = await anyio.to_thread.run_sync(
                    save_partial
                )
            yield {"event": "stream_truncated", "data": info}
            raise
        answer = "".join(parts).strip()
        cited, unknown = prompts.parse_citations(answer, tokens)
        result = Answer(answer, hits, tokens, cited, unknown, llm.provider, llm.model)
        if persist:

            def save() -> tuple[str, int]:
                pg = self._pg(scope)
                cid = conversation_id or pg.create_conversation(scope.key, question[:80])
                pg.add_message(cid, "user", question)
                mid = pg.add_message(
                    cid,
                    "assistant",
                    answer,
                    retrieved=[h.element.id for h in hits],
                    citations=cited,
                    provider=llm.provider,
                    model=llm.model,
                )
                return cid, mid

            result.conversation_id, result.message_id = await anyio.to_thread.run_sync(save)
        yield {"event": "citations", "data": {"citations": cited, "unknown": unknown}}
        yield {
            "event": "done",
            "data": {
                "conversation_id": result.conversation_id,
                "message_id": result.message_id,
                "provider": llm.provider,
                "model": llm.model,
            },
            "answer": result,
        }
