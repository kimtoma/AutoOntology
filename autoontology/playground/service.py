# SPDX-License-Identifier: MIT
"""Playground orchestration: scope, element index and RAG chat.

One :class:`Playground` per service process (``web/playground_api.py``). The scope is the
workspace's latest ontology snapshot; the element index is keyed by the workspace and
rebuilt when the snapshot revision changes. Every LLM call holds the workspace's slot, so
one runs at a time (409 ``playground_busy`` otherwise).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio

from autoontology.playground import prompts
from autoontology.playground.elements import Element, elements_from_model
from autoontology.playground.index import DEFAULT_TOP_K, ElementIndex, Embedder, Hit, build_index
from autoontology.playground.llm import LLMError, PlaygroundLLM
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import Source, Store

logger = logging.getLogger("autoontology.playground")

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
    #: the workspace name (the key of the index, conversations and slot)
    key: str
    #: the snapshot revision the scope was taken from
    revision: int
    #: the snapshot's model (:class:`~autoontology.ontology.model.MergedOntology`)
    model: Any
    #: every source of the model: structured first, then documents
    names: list[str]
    structured: list[Source] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    from autoontology.config import expand_model_profile

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
            from autoontology.engine.jobs import prepare_upstream_env

            # upstream reads its env at import: load it once (any selection) before importing
            prepare_upstream_env(self.boot_settings)
            from auto_ontology.utils.embedding import EmbedParams, embed_texts

            params = EmbedParams(model_name=model, embed_invoke_url=endpoint, api_key=key)

            def embed(texts: list[str], input_type: str) -> list[list[float] | None]:
                return embed_texts(texts, params, input_type)  # type: ignore[arg-type]

            self._key, self._value = (endpoint, model), (embed, model)
            logger.info("playground embedder: %s", model)
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
        self.pg = PlaygroundStore(store)
        self.model_for = model_for
        self.llm_factory = llm_factory
        self.embedder_factory = embedder_factory
        self._indexes: dict[str, ElementIndex] = {}
        self._index_lock = threading.Lock()
        self._busy: set[str] = set()
        self._busy_lock = threading.Lock()

    # --- scope / concurrency ------------------------------------------------------------

    def scope(self) -> Scope:
        """The latest snapshot (``model_for`` raises when there is none)."""
        revision, model = self.model_for()
        structured, warnings = [], []
        for name in model.structured_sources:
            s = self.store.get_source(name)
            if s is not None and s.kind in STRUCTURED_KINDS and s.catalog_database_id:
                structured.append(s)
            else:
                warnings.append(f"structured source {name!r} is no longer registered")
        return Scope(
            key=model.name,
            revision=revision,
            model=model,
            names=[*model.structured_sources, *model.documents_sources],
            structured=structured,
            warnings=warnings,
        )

    @contextlib.contextmanager
    def slot(self, key: str) -> Iterator[None]:
        with self._busy_lock:
            if key in self._busy:
                raise PlaygroundError(
                    409,
                    "playground_busy",
                    f"another playground call for {key!r} is running; wait for it to finish",
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
        stack.enter_context(self.slot(scope.key))
        return stack

    # --- index --------------------------------------------------------------------------

    def embedder(self) -> tuple[Embedder, str]:
        """Asked on every use: the factory (``EmbedderCache``) follows the current selection."""
        return self.embedder_factory()

    def elements(self, scope: Scope) -> list[Element]:
        return elements_from_model(scope.model)

    def index(self, scope: Scope) -> ElementIndex:
        from autoontology.store.publications import PublicationError, Publications, index_receipt

        embed, model_name = self.embedder()
        revision = scope.revision
        try:
            publication = Publications(self.store).check_model(revision)
            if publication:
                receipt = publication["manifest"]["index"]
                if isinstance(self.embedder_factory, EmbedderCache):
                    from autoontology.store.operations import digest

                    endpoint = embed_triplet(self.embedder_factory.settings_fn())[0]
                    if digest(endpoint) != publication["manifest"]["profile"]["endpoint_sha256"]:
                        raise PublicationError(
                            "embedding endpoint changed; validate and publish a new revision"
                        )
                if model_name != receipt["model"]:
                    raise PublicationError(
                        "embedding selection changed; validate and publish a new revision before using it"
                    )
                if index_receipt(self.store, receipt["scope"], scope.model) != receipt:
                    raise PublicationError("publication index integrity check failed")
                rows = self.pg.index_rows(receipt["scope"])
                elements = self.elements(scope)
                return ElementIndex(
                    elements,
                    [rows[e.id].embedding for e in elements],
                    model=model_name,
                    revision=revision,
                    reused=len(elements),
                )
        except PublicationError as exc:
            raise PlaygroundError(409, "publication_invalid", str(exc)) from exc
        sid = f"{scope.key}@r{revision}"
        with self._index_lock:
            cached = self._indexes.get(sid)
            if cached and cached.revision == revision and cached.model == model_name:
                return cached
        elements = self.elements(scope)
        if not self.pg.index_rows(sid):
            legacy = self.pg.index_rows(scope.key)
            if legacy:
                self.pg.replace_index(
                    sid,
                    [r for r in legacy.values() if r.model == model_name],
                    revision=0,
                    model=model_name,
                )
        try:
            idx = build_index(
                self.pg, sid, elements, revision=revision, model=model_name, embed=embed
            )
        except Exception as exc:  # noqa: BLE001 - the embed endpoint's own errors
            raise PlaygroundError(
                502,
                "embed_failed",
                f"embedding the ontology failed: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc
        with self._index_lock:
            if len(self._indexes) >= 4:
                self._indexes.pop(next(iter(self._indexes)))
            self._indexes[sid] = idx
        return idx

    def index_status(self, scope: Scope) -> dict[str, Any]:
        from autoontology.store.publications import Publications

        publication = Publications(self.store).check_model(scope.revision)
        sid = (
            publication["manifest"]["index"]["scope"]
            if publication
            else f"{scope.key}@r{scope.revision}"
        )
        meta = self.pg.index_meta(sid)
        revision = scope.revision
        return {
            "workspace": scope.key,
            "scope": scope.names,
            "warnings": scope.warnings,
            "revision": revision,
            "index": meta,
            "publication_id": publication["id"] if publication else None,
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
            conv = self.pg.get_conversation(conversation_id)
            if conv is None or conv["scope"] != scope.key:
                raise PlaygroundError(
                    404,
                    "unknown_conversation",
                    f"no conversation {conversation_id!r} for {scope.key!r}",
                )
            # an incomplete (truncated) answer is not context for the next turn
            return conversation_id, [
                {"role": m["role"], "content": m["content"]}
                for m in self.pg.messages(conversation_id)
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
                    cid = conversation_id or self.pg.create_conversation(scope.key, question[:80])
                    self.pg.add_message(cid, "user", question)
                    mid = self.pg.add_message(
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
                cid = conversation_id or self.pg.create_conversation(scope.key, question[:80])
                self.pg.add_message(cid, "user", question)
                mid = self.pg.add_message(
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
