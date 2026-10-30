# SPDX-License-Identifier: MIT
"""Playground orchestration: scope, element index and RAG chat.

One :class:`Playground` per service process (``web/playground_api.py``). The scope is the
workspace's latest ontology snapshot; the element index is keyed by the workspace and
rebuilt when the snapshot revision changes. Every LLM call holds the workspace's slot, so
one runs at a time (409 ``playground_busy`` otherwise).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import numpy as np

from autoontology.playground import prompts
from autoontology.playground.elements import Element, elements_from_model
from autoontology.playground.index import DEFAULT_TOP_K, ElementIndex, Embedder, Hit, build_index
from autoontology.playground.llm import LLMError, PlaygroundLLM
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import Source, Store
from autoontology.store.traces import UsageTraces, error_code

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
        self._key: tuple | None = None
        self._value: tuple[Embedder, str] | None = None
        self._lock = threading.Lock()

    def __call__(self) -> tuple[Embedder, str]:
        from autoontology.engine.model_artifacts import GuardedEmbedder, embedding_identity
        from autoontology.store.operations import digest

        settings = self.settings_fn()
        endpoint, key, model = embed_triplet(settings)
        identity = embedding_identity(settings, endpoint=endpoint, model=model)
        cache_key = (endpoint, key, model, digest(identity))
        with self._lock:
            if self._value is not None and self._key == cache_key:
                return self._value
            from autoontology.engine.jobs import prepare_upstream_env

            # upstream reads its env at import: load it once (any selection) before importing
            prepare_upstream_env(self.boot_settings)
            from auto_ontology.utils.embedding import EmbedParams, embed_texts

            params = EmbedParams(model_name=model, embed_invoke_url=endpoint, api_key=key)

            def embed(texts: list[str], input_type: str) -> list[list[float] | None]:
                return embed_texts(texts, params, input_type)  # type: ignore[arg-type]

            guarded = GuardedEmbedder(
                embed,
                identity,
                lambda: embedding_identity(settings, endpoint=endpoint, model=model),
            )
            self._key, self._value = cache_key, (guarded, model)
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
        self.traces = UsageTraces(store)
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
        from autoontology.engine.model_artifacts import ModelArtifactError

        try:
            return self.embedder_factory()
        except ModelArtifactError as exc:
            raise PlaygroundError(exc.status, exc.code, str(exc)) from exc

    def elements(self, scope: Scope) -> list[Element]:
        return elements_from_model(scope.model)

    def index(self, scope: Scope) -> ElementIndex:
        from autoontology.engine.model_artifacts import ModelArtifactError
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
                from autoontology.engine.model_artifacts import GuardedEmbedder, embedding_identity

                expected = publication["manifest"]["profile"].get("embedding_artifact")
                if (
                    isinstance(embed, GuardedEmbedder)
                    or (expected or {}).get("status") == "verified"
                ) and expected != embedding_identity(None, (embed, model_name)):
                    raise PublicationError(
                        "embedding model artifact changed or was not recorded; validate and publish a new revision"
                    )
                if index_receipt(self.store, receipt["scope"], scope.model) != receipt:
                    raise PublicationError("publication index integrity check failed")
                rows = self.pg.index_rows(receipt["scope"])
                elements = self.elements(scope)
                index = ElementIndex(
                    elements,
                    [rows[e.id].embedding for e in elements],
                    model=model_name,
                    revision=revision,
                    reused=len(elements),
                )
                index.embedding_artifact = expected
                return index
        except PublicationError as exc:
            raise PlaygroundError(409, "publication_invalid", str(exc)) from exc
        except ModelArtifactError as exc:
            raise PlaygroundError(exc.status, exc.code, str(exc)) from exc
        from autoontology.engine.model_artifacts import GuardedEmbedder
        from autoontology.store.operations import digest

        # Preserve legacy rows, but never assume they came from today's mutable tag.
        suffix = f"@{digest(embed.artifact)}" if isinstance(embed, GuardedEmbedder) else ""
        sid = f"{scope.key}@r{revision}{suffix}"
        with self._index_lock:
            cached = self._indexes.get(sid)
            if cached and cached.revision == revision and cached.model == model_name:
                return cached
        elements = self.elements(scope)
        if not suffix and not self.pg.index_rows(sid):
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
        except ModelArtifactError as exc:
            raise PlaygroundError(exc.status, exc.code, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - the embed endpoint's own errors
            raise PlaygroundError(
                502,
                "embed_failed",
                f"embedding the ontology failed: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc
        idx.embedding_artifact = dict(getattr(embed, "artifact", {"status": "unverified"}))
        with self._index_lock:
            if len(self._indexes) >= 4:
                self._indexes.pop(next(iter(self._indexes)))
            self._indexes[sid] = idx
        return idx

    def index_status(self, scope: Scope) -> dict[str, Any]:
        from autoontology.store.operations import digest
        from autoontology.store.publications import Publications

        publication = Publications(self.store).check_model(scope.revision)
        sid = (
            publication["manifest"]["index"]["scope"]
            if publication
            else f"{scope.key}@r{scope.revision}"
        )
        identity = None
        if isinstance(self.embedder_factory, EmbedderCache):
            try:
                embed, _ = self.embedder()
                identity = dict(embed.artifact)
                if not publication:
                    sid += f"@{digest(identity)}"
            except PlaygroundError as exc:
                identity = {"status": "unavailable", "error": exc.code}
        meta = (
            None
            if identity and identity.get("status") == "unavailable"
            else self.pg.index_meta(sid)
        )
        compatible = (
            identity == publication["manifest"]["profile"].get("embedding_artifact")
            if publication and identity
            else None
        )
        revision = scope.revision
        return {
            "workspace": scope.key,
            "scope": scope.names,
            "warnings": scope.warnings,
            "revision": revision,
            "index": meta,
            "embedding_artifact": identity,
            "identity_matches_publication": compatible,
            "publication_id": publication["id"] if publication else None,
            "stale": meta is None or meta["revision"] != revision or compatible is False,
        }

    def retrieve(self, scope: Scope, question: str, top_k: int) -> tuple[ElementIndex, list[Hit]]:
        idx, hits, _ = self.retrieve_with_vector(scope, question, top_k)
        return idx, hits

    def retrieve_with_vector(self, scope: Scope, question: str, top_k: int):
        from autoontology.engine.model_artifacts import ModelArtifactError

        embed, name = self.embedder()
        idx = self.index(scope)
        if name != idx.model or (
            isinstance(self.embedder_factory, EmbedderCache)
            and self.embedder_factory._value[0] is not embed
        ):
            raise PlaygroundError(
                409,
                "embedding_selection_changed",
                "embedding selection changed during retrieval; retry the request",
            )
        try:
            vector = embed([question], "query")[0]
            if vector and (not np.isfinite(vector).all() or not any(vector)):
                raise ValueError("invalid query vector")
        except ModelArtifactError as exc:
            raise PlaygroundError(exc.status, exc.code, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise PlaygroundError(
                502,
                "embed_failed",
                f"embedding the question failed: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc
        return idx, idx.retrieve(vector or [], top_k=top_k), vector

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

    def retrieval_context(self, scope: Scope, idx: ElementIndex, hits: list[Hit], vector) -> dict:
        from autoontology.store.operations import digest
        from autoontology.store.publications import Publications

        publication = Publications(self.store).get(scope.revision)
        if not np.isfinite(idx._matrix).all():
            raise PlaygroundError(
                502, "invalid_embedding", "the retrieval index contains invalid vectors"
            )
        fingerprint = digest(
            {
                "texts": [(e.id, e.text_sha) for e in idx.elements],
                "rows": idx._rows,
                "vectors": idx._matrix.tolist(),
                "model": idx.model,
            }
        )
        return {
            "query_vector": vector,
            "index": {
                "sha256": fingerprint,
                "algorithm": "normalized-float32-v1",
                "model": idx.model,
                "embedding_artifact": getattr(idx, "embedding_artifact", {"status": "unverified"}),
                "elements": len(idx.elements),
                "publication_index": publication["manifest"]["index"] if publication else None,
            },
            "hits": [
                {
                    "ref": f"E{i}",
                    **h.element.chip(),
                    "text": h.element.text,
                    "text_sha256": h.element.text_sha,
                    "score": h.score,
                    "via": h.via,
                }
                for i, h in enumerate(hits, 1)
            ],
        }

    def search(self, scope: Scope, question: str, top_k: int = DEFAULT_TOP_K) -> dict:
        top_k = max(1, min(int(top_k), MAX_TOP_K))
        trace = self.traces.begin(
            kind="retrieval",
            workspace=scope.key,
            revision=scope.revision,
            model=scope.model,
            request={"question": question, "top_k": top_k},
        )
        try:
            idx, hits, vector = self.retrieve_with_vector(scope, question, top_k)
            context = self.retrieval_context(scope, idx, hits, vector)
            self.traces.context(trace, context)
            self.traces.finish(trace, "succeeded", {"hit_count": len(hits)})
            return {"trace_id": trace, "revision": scope.revision, **context}
        except BaseException as exc:
            self.traces.finish(trace, "failed", {"error": error_code(exc)})
            raise

    async def ask(
        self, scope: Scope, question: str, *, trace_id: str | None = None, **options
    ) -> AsyncIterator[dict[str, Any]]:
        trace_id = trace_id or self.traces.begin(
            kind="chat",
            workspace=scope.key,
            revision=scope.revision,
            model=scope.model,
            request={"question": question, "top_k": options.get("top_k", DEFAULT_TOP_K)},
            conversation_id=options.get("conversation_id"),
        )
        parts, cid = [], options.get("conversation_id")
        try:
            async for event in self._ask(scope, question, trace_id=trace_id, **options):
                name, data = event["event"], event["data"]
                if name == "delta":
                    parts.append(data["text"])
                if name == "stream_truncated":
                    cid = data.get("conversation_id") or cid
                if name == "done":
                    cid = data.get("conversation_id") or cid
                    answer = event["answer"]
                    self.traces.finish(
                        trace_id,
                        "succeeded",
                        {
                            "answer": answer.text,
                            "citations": answer.citations,
                            "unknown": answer.unknown,
                            "provider": answer.provider,
                            "model": answer.model,
                            "partial": False,
                        },
                        conversation_id=cid,
                    )
                yield {**event, "data": {**data, "trace_id": trace_id}}
        except Exception as exc:
            self.traces.finish(
                trace_id,
                "failed",
                {"answer": "".join(parts).strip(), "partial": True, "error": error_code(exc)},
                conversation_id=cid,
            )
            raise
        finally:
            self.traces.finish(
                trace_id,
                "cancelled",
                {"answer": "".join(parts).strip(), "partial": True, "error": "request_cancelled"},
                conversation_id=cid,
            )

    async def _ask(
        self,
        scope: Scope,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        top_k: int = DEFAULT_TOP_K,
        conversation_id: str | None = None,
        persist: bool = True,
        llm: PlaygroundLLM | None = None,
        trace_id: str | None = None,
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
        idx, hits, vector = await anyio.to_thread.run_sync(
            self.retrieve_with_vector, scope, question, top_k
        )
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
        self.traces.context(
            trace_id,
            {
                **self.retrieval_context(scope, idx, hits, vector),
                "messages": messages,
                "prompt_sha256": hashlib.sha256(prompts.CHAT_SYSTEM.encode()).hexdigest(),
                "provider": llm.provider,
                "model": llm.model,
            },
        )
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
                        trace_id=trace_id,
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
                    trace_id=trace_id,
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
