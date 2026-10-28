# SPDX-License-Identifier: MIT
"""LLM extraction of concepts/relations from chunks, through upstream's LLM helpers.

Two paths per chunk:

* ``structured`` -- upstream ``safe_invoke_with_structured_output`` with
  :class:`ChunkExtraction` (json_schema response format; 3 attempts, then raises);
* ``json_fallback`` / ``json`` -- the schema is put in the prompt, the answer parsed and
  validated with Pydantic, retried up to 3 times with the validation error fed back.

``AUTOONTOLOGY_EXTRACT_MODE``: ``auto`` (default: structured, JSON when structured fails;
after three chunks in a row needed the fallback, later chunks go straight to JSON),
``structured`` or ``json``. The path used is recorded per chunk.

Upstream freezes model settings at import time: :func:`upstream_backend` calls
``prepare_upstream_env`` before importing ``auto_ontology``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from autoontology.config import Settings
from autoontology.unstructured import prompts
from autoontology.unstructured.schemas import (
    ChunkExtraction,
    MergeDecision,
    ValidatedExtraction,
    parse_json_object,
    post_validate,
)

logger = logging.getLogger("autoontology.extract")

T = TypeVar("T", bound=BaseModel)
JSON_ATTEMPTS = 3
FALLBACK_STREAK_TO_SWITCH = 3
EXTRACT_MODES = ("auto", "structured", "json")
DEFAULT_WORKERS = 3
EXTRACT_MAX_TOKENS = 4096


class LLMBackend(Protocol):
    def structured(self, system: str, human: str, schema: type[T]) -> T: ...

    def text(self, prompt: str) -> str: ...


@dataclass
class UpstreamBackend:
    """Upstream chat client + helpers. ``judge`` uses the non-reasoning client."""

    llm: Any
    _structured: Callable[..., Any]
    _text: Callable[..., str]

    def structured(self, system: str, human: str, schema: type[T]) -> T:
        from langchain_core.messages import HumanMessage, SystemMessage

        return self._structured(
            self.llm, [SystemMessage(content=system), HumanMessage(content=human)], schema
        )

    def text(self, prompt: str) -> str:
        return self._text(self.llm, prompt)


def pool_thread_prefix() -> str:
    """Pool threads are named after the calling thread (``<caller>:extract_<n>``), so a
    per-run log handler can claim the lines of its own run's workers."""
    return f"{threading.current_thread().name}:extract"


def upstream_backend(
    settings: Settings, *, role: str = "reasoning", max_tokens: int = EXTRACT_MAX_TOKENS
) -> UpstreamBackend:
    from autoontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    from auto_ontology.utils.llm_invoke import (
        get_llm_client,
        get_non_reasoning_llm_client,
        safe_invoke_text,
        safe_invoke_with_structured_output,
    )

    factory = get_llm_client if role == "reasoning" else get_non_reasoning_llm_client
    llm = factory(temperature=0.0, max_tokens=max_tokens)
    # Upstream leaves temperature unset on its ChatOpenAI path (gpt-5.x rejects it). The
    # local Ollama alias accepts it, and extraction should be deterministic.
    if settings.chat_provider == "ollama" and getattr(llm, "temperature", 0.0) is None:
        llm.temperature = 0.0
    return UpstreamBackend(llm, safe_invoke_with_structured_output, safe_invoke_text)


def extract_mode() -> str:
    mode = os.environ.get("AUTOONTOLOGY_EXTRACT_MODE", "").strip().lower() or "auto"
    if mode not in EXTRACT_MODES:
        raise ValueError(f"AUTOONTOLOGY_EXTRACT_MODE must be one of {EXTRACT_MODES}, got {mode!r}")
    return mode


#: The ollama profile's single local runner answers one request at a time (measured: two
#: concurrent calls finish at 4.0 s and 7.7 s); parallel calls only queue there and the
#: queueing counts against LLM_INVOKE_TIMEOUT_S, so that profile defaults to 1 worker.
OLLAMA_DEFAULT_WORKERS = 1


def workers_default(settings: Settings | None = None) -> int:
    raw = os.environ.get("AUTOONTOLOGY_LLM_WORKERS", "").strip()
    fallback = (
        OLLAMA_DEFAULT_WORKERS
        if settings is not None and settings.chat_provider == "ollama"
        else DEFAULT_WORKERS
    )
    try:
        value = int(raw) if raw else fallback
    except ValueError as exc:
        raise ValueError(f"AUTOONTOLOGY_LLM_WORKERS must be an integer, got {raw!r}") from exc
    return max(1, value)


def invoke_json(
    backend: LLMBackend, system: str, human: str, schema: type[T], attempts: int = JSON_ATTEMPTS
) -> tuple[T, int]:
    """JSON-in-prompt call with validation feedback. Returns (object, attempts used)."""
    prompt = f"{system}\n\n{human}"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        answer = backend.text(prompt)
        try:
            return schema.model_validate(parse_json_object(answer)), attempt
        except (ValueError, ValidationError) as exc:  # JSONDecodeError is a ValueError
            last = exc
            logger.info(
                "JSON-mode answer for %s invalid (attempt %d/%d): %s",
                schema.__name__,
                attempt,
                attempts,
                str(exc).splitlines()[0][:200],
            )
            prompt = (
                f"{system}\n\n{human}\n\nYour previous answer:\n{answer[:4000]}\n\n"
                + prompts.json_retry_message(str(exc))
            )
    assert last is not None
    raise last


@dataclass
class ChunkJob:
    key: str  # chunk id
    doc_title: str
    section_path: str
    index: int
    total: int
    text: str


@dataclass
class ChunkOutcome:
    job: ChunkJob
    extraction: ValidatedExtraction | None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _RetryCounter(logging.Handler):
    """Counts upstream ``llm_invoke`` retry/timeout warnings per thread. Upstream retries
    inside ``safe_invoke_with_structured_output`` and only logs them, so this is how a
    chunk's step detail learns that its wall time includes a timed-out attempt.

    It depends on upstream's log wording ("... on attempt N/M ..." plus "retry"/"timed
    out"), pinned by ``test_retry_counter_matches_the_vendored_upstream_wording``. The
    robust signal is ``over_timeout`` (chunk wall time > ``LLM_INVOKE_TIMEOUT_S``), which
    needs no log parsing; this count is the finer-grained extra."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "attempt" in message and ("retry" in message.lower() or "timed out" in message):
            with self._lock:
                self.counts[record.thread or 0] = self.counts.get(record.thread or 0, 0) + 1

    def current(self) -> int:
        with self._lock:
            return self.counts.get(threading.get_ident(), 0)


UPSTREAM_LLM_LOGGER = "auto_ontology.utils.llm_invoke"
_RETRIES = _RetryCounter()


def _install_retry_counter() -> _RetryCounter:
    log = logging.getLogger(UPSTREAM_LLM_LOGGER)
    if _RETRIES not in log.handlers:
        log.addHandler(_RETRIES)
    return _RETRIES


def llm_timeout_s() -> float | None:
    raw = os.environ.get("LLM_INVOKE_TIMEOUT_S", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


class Extractor:
    def __init__(self, backend: LLMBackend, *, mode: str | None = None) -> None:
        self.backend = backend
        self.mode = mode or extract_mode()
        self._lock = threading.Lock()
        self._fallback_streak = 0
        self._retries = _install_retry_counter()

    def _use_structured(self) -> bool:
        with self._lock:
            return self.mode == "structured" or (
                self.mode == "auto" and self._fallback_streak < FALLBACK_STREAK_TO_SWITCH
            )

    def _record(self, structured_ok: bool) -> None:
        with self._lock:
            if structured_ok:
                self._fallback_streak = 0
                return
            self._fallback_streak += 1
            if self._fallback_streak == FALLBACK_STREAK_TO_SWITCH and self.mode == "auto":
                logger.warning(
                    "structured output failed on %d chunks in a row; using JSON "
                    "mode for the remaining chunks",
                    FALLBACK_STREAK_TO_SWITCH,
                )

    def extract(self, job: ChunkJob) -> ChunkOutcome:
        human = prompts.human_message(
            doc_title=job.doc_title,
            section_path=job.section_path,
            index=job.index,
            total=job.total,
            chunk_text=job.text,
        )
        started = time.monotonic()
        retries_before = self._retries.current()
        detail: dict[str, Any] = {}
        raw: ChunkExtraction | None = None
        outcome = self._extract(job, human, started, detail, raw)
        retries = self._retries.current() - retries_before
        timeout = llm_timeout_s()
        outcome.detail.update(
            upstream_retries=retries,
            llm_timeout_s=timeout,
            over_timeout=bool(timeout and outcome.detail.get("seconds", 0) > timeout),
        )
        return outcome

    def _extract(
        self,
        job: ChunkJob,
        human: str,
        started: float,
        detail: dict[str, Any],
        raw: ChunkExtraction | None,
    ) -> ChunkOutcome:
        if self._use_structured():
            try:
                raw = self.backend.structured(prompts.SYSTEM_PROMPT, human, ChunkExtraction)
                detail["path"] = "structured"
                self._record(True)
            except Exception as exc:  # noqa: BLE001 - recorded; JSON mode may still work
                detail["structured_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                if self.mode == "structured" or _is_connection_error(exc):
                    return ChunkOutcome(
                        job,
                        None,
                        {**detail, "seconds": _since(started)},
                        detail["structured_error"],
                    )
                self._record(False)
                logger.warning(
                    "chunk %s: structured output failed (%s); trying JSON mode",
                    job.key,
                    detail["structured_error"][:160],
                )
        if raw is None:
            try:
                raw, attempts = invoke_json(
                    self.backend, prompts.json_mode_system_prompt(), human, ChunkExtraction
                )
                detail["path"] = (
                    "json"
                    if self.mode == "json" or "structured_error" not in detail
                    else "json_fallback"
                )
                detail["json_attempts"] = attempts
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                return ChunkOutcome(
                    job, None, {**detail, "json_error": error, "seconds": _since(started)}, error
                )
        validated = post_validate(raw, job.text, label=f"chunk {job.key}")
        detail.update(
            seconds=_since(started),
            raw_concepts=len(raw.concepts),
            raw_relations=len(raw.relations),
            concepts=len(validated.concepts),
            relations=len(validated.relations),
            unverified_evidence=validated.stats.unverified_evidence,
        )
        return ChunkOutcome(job, validated, detail)

    def run(
        self,
        jobs: Sequence[ChunkJob],
        *,
        workers: int,
        on_start: Callable[[ChunkJob], None] | None = None,
        on_done: Callable[[ChunkOutcome], None] | None = None,
    ) -> list[ChunkOutcome]:
        outcomes: list[ChunkOutcome] = []

        def one(job: ChunkJob) -> ChunkOutcome:
            if on_start:
                on_start(job)
            return self.extract(job)

        with ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix=pool_thread_prefix()
        ) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                outcome = future.result()
                if on_done:
                    on_done(outcome)
                outcomes.append(outcome)
        return outcomes


def _since(started: float) -> float:
    return round(time.monotonic() - started, 1)


_NO_FALLBACK_ERRORS = frozenset(
    {
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "ConnectionRefusedError",
        # a timed-out call (upstream already retried it) would only time out again in JSON mode
        "APITimeoutError",
        "ReadTimeout",
        "TimeoutException",
        "ConnectTimeout",
        "Timeout",
        "TimeoutError",
    }
)


def _is_connection_error(exc: BaseException) -> bool:
    """Endpoint down or timing out: JSON mode would fail the same way (and double the wait)."""
    return bool({type(e).__name__ for e in _chain(exc)} & _NO_FALLBACK_ERRORS)


def _chain(exc: BaseException | None):
    seen = 0
    while exc is not None and seen < 10:
        yield exc
        exc = exc.__cause__ or exc.__context__
        seen += 1


def make_judge(
    backend: LLMBackend, *, mode: str | None = None
) -> Callable[[dict[str, Any], dict[str, Any]], MergeDecision]:
    """Merge judge over two candidate summaries (structured, JSON-mode fallback)."""
    mode = mode or extract_mode()

    def judge(a: dict[str, Any], b: dict[str, Any]) -> MergeDecision:
        human = prompts.merge_message(a, b)
        if mode != "json":
            try:
                return backend.structured(prompts.MERGE_SYSTEM_PROMPT, human, MergeDecision)
            except Exception as exc:  # noqa: BLE001
                if mode == "structured" or _is_connection_error(exc):
                    raise
                logger.info("merge judge structured output failed (%s); JSON mode", str(exc)[:160])
        decision, _ = invoke_json(backend, prompts.merge_json_system_prompt(), human, MergeDecision)
        return decision

    return judge


def make_embedder(settings: Settings) -> Callable[[list[str]], list[list[float] | None]]:
    from autoontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    from auto_ontology.utils.embedding import embed_texts, get_embed_params

    params = get_embed_params()

    def embed(texts: list[str]) -> list[list[float] | None]:
        return embed_texts(texts, params, "passage")

    return embed
