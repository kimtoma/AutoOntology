# SPDX-License-Identifier: MIT
"""The documents steps of ``autoontology build``: documents -> chunks -> LLM
concepts/relations -> consolidated concepts in the store.

Steps ``read -> extract -> consolidate -> report``, persisted like ``build``
(:func:`autoontology.engine.jobs._execute`). Every chunk extraction also gets its own step
row (``chunk:<chunk id>``) and its validated result is stored on the chunk the moment it
returns, so ``--resume`` (and any later run) only extracts chunks that have none.

Re-running on unchanged files is a no-op: documents are keyed by (source, sha256), so
nothing is re-read or re-extracted, and consolidation is skipped when the set of chunk
extractions has the same fingerprint as the last successful consolidation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import Settings
from autoontology.engine.jobs import (
    JobError,
    RunResult,
    _execute,
    hold_sources,
    reap_stale_runs,
    require_llm_proxy,
    setup_job_logging,
)
from autoontology.store.db import Run, Source, Store, now_iso
from autoontology.store.documents import DocumentStore
from autoontology.unstructured import chunker, consolidate, readers, report
from autoontology.unstructured.extractor import (
    ChunkJob,
    ChunkOutcome,
    Extractor,
    LLMBackend,
    make_embedder,
    make_judge,
    upstream_backend,
)
from autoontology.unstructured.report import display_path
from autoontology.unstructured.schemas import ValidatedExtraction, reverify

logger = logging.getLogger("autoontology.jobs")

EXTRACT_KIND = "extract"
EXTRACT_STEPS: tuple[str, ...] = ("read", "extract", "consolidate", "report")
CHUNK_STEP_PREFIX = "chunk:"


@dataclass
class Backends:
    """LLM/embedding access, built lazily (a no-op re-run never touches a model)."""

    settings: Settings
    llm: LLMBackend | None = None
    judge_llm: LLMBackend | None = None
    embedder: Callable[[list[str]], list[list[float] | None]] | None = None

    def extraction(self) -> LLMBackend:
        if self.llm is None:
            self.llm = upstream_backend(self.settings, role="reasoning")
        return self.llm

    def judge(self) -> LLMBackend:
        if self.judge_llm is None:
            self.judge_llm = upstream_backend(self.settings, role="non_reasoning", max_tokens=1024)
        return self.judge_llm

    def embed(self) -> Callable[[list[str]], list[list[float] | None]]:
        if self.embedder is None:
            self.embedder = make_embedder(self.settings)
        return self.embedder


@dataclass
class _ExtractCtx:
    settings: Settings
    store: Store
    docs: DocumentStore
    source: Source
    run: Run
    target: Path
    recursive: bool
    workers: int
    max_chunks: int | None
    backends: Backends
    #: every document under the target was looked at (not --max-chunks, not a resume):
    #: only then are documents that disappeared pruned
    full_scan: bool = True
    details: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def export_dir(self) -> Path:
        return self.settings.exports_dir / self.source.name / self.run.id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


#: Read error codes meaning "the kordoc engine (or Node) is not installed".
KORDOC_UNAVAILABLE_CODES = frozenset({"ENGINE_MISSING", "NODE_MISSING"})


def _step_read(ctx: _ExtractCtx) -> dict[str, Any]:
    files = readers.discover_documents(ctx.target, recursive=ctx.recursive)
    if not files:
        raise RuntimeError(
            f"no supported documents in {ctx.target} "
            f"({', '.join(sorted(readers.SUPPORTED_EXTENSIONS))})"
        )
    max_tokens, overlap, pack = chunker.env_sizes()
    documents: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    #: documents that need the kordoc engine, which is not installed: skipped (not a
    #: failure); kordoc's availability is part of the source's input fingerprint, so the
    #: next build after `tools install-kordoc` reads them
    needs_kordoc: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    superseded: list[dict[str, str]] = []
    # Ownership comes from the *current* contents of the whole input set, decided before
    # anything is deleted or read: a stored document whose content is still somewhere in
    # the input keeps it (at its own path when that still holds it, else at the first path
    # that does: a move or a swap); identical content at other paths is a duplicate. Stored
    # rows are never trusted for a path whose file changed (e.g. A=X,B=Y -> A=Y,B=Z keeps
    # Y under A and reads Z for B).
    current = {str(p.resolve()): sha256_file(p) for p in files}  # stored absolute
    seen = set(current)
    paths_of: dict[str, list[str]] = {}
    for shown, sha in current.items():
        paths_of.setdefault(sha, []).append(shown)
    stored = {d.sha256: d for d in ctx.docs.list_documents(ctx.source.id)}
    owner: dict[str, str] = {}
    for sha, paths in paths_of.items():
        doc = stored.get(sha)
        owner[sha] = doc.path if doc is not None and doc.path in paths else paths[0]
        if doc is not None and doc.path != owner[sha]:
            ctx.docs.touch_document(doc.id, owner[sha])  # moved (or swapped) content
    stale = [d for d in stored.values() if d.sha256 not in paths_of and d.path in seen]
    if stale:
        # the file at that path now holds other content: the old document is gone from
        # the input (chunks/evidence go by cascade, consolidate runs again)
        ctx.docs.delete_documents([d.id for d in stale])
        for d in stale:
            superseded.append({"path": d.path, "old_sha256": d.sha256})
            logger.info("  %s: content changed; its old content was removed", display_path(d.path))
    replaced_paths = {d.path for d in stale}
    engine = None
    for path in files:
        shown = str(path.resolve())  # displayed relative to the cwd
        sha = current[shown]
        if owner[sha] != shown:
            duplicates.append({"path": shown, "of": owner[sha]})
            logger.info(
                "  %s: duplicate of %s, skipped", display_path(shown), display_path(owner[sha])
            )
            continue
        existing = stored.get(sha)
        if existing is not None:
            documents.append(
                {
                    "path": shown,
                    "action": "unchanged",
                    "engine": existing.engine,
                    "chunks": len(ctx.docs.chunks_for_document(existing.id)),
                }
            )
            logger.info("  %s: unchanged", display_path(shown))
            continue
        try:
            if path.suffix.lower() in readers.kordoc.EXTENSIONS and engine is None:
                engine = readers.kordoc.resolve_engine(ctx.settings.home)
            doc = readers.read_document(path, home=ctx.settings.home, engine=engine)
            chunks = chunker.chunk(
                doc, max_tokens=max_tokens, overlap_tokens=overlap, pack_tokens=pack
            )
            if not chunks:
                raise readers.ReadError(f"{path.name}: no text extracted", code="EMPTY")
        except (readers.ReadError, readers.kordoc.KordocError) as exc:
            code = getattr(exc, "code", "READ_FAILED")
            message = getattr(exc, "message", None) or str(exc)
            if code in KORDOC_UNAVAILABLE_CODES:
                needs_kordoc.append({"path": shown, "code": code, "error": message})
                continue
            failed.append({"path": shown, "code": code, "error": message})
            logger.warning("  %s: FAILED (%s) %s", display_path(shown), code, message)
            continue
        row, replaced = ctx.docs.replace_document(
            source_id=ctx.source.id,
            path=shown,
            title=doc.title,
            mime=doc.mime,
            engine=doc.engine,
            sha256=sha,
            size=path.stat().st_size,
            pages=doc.pages,
            chunks=chunks,
        )
        action = "replaced" if replaced or shown in replaced_paths else "new"
        documents.append(
            {
                "path": shown,
                "action": action,
                "engine": doc.engine,
                "chunks": len(chunks),
                "pages": doc.pages,
            }
        )
        logger.info(
            "  %s: %s, %s, %d section(s) -> %d chunk(s)",
            display_path(shown),
            action,
            doc.engine,
            len(doc.sections),
            len(chunks),
        )
    if not documents:
        raise RuntimeError(
            "every document failed to read: "
            + "; ".join(f"{f['path']}: {f['error']}" for f in [*failed, *needs_kordoc])
        )
    if needs_kordoc:
        logger.warning(
            "  %d document(s) need kordoc, skipped: %s; run `%s`, then build again",
            len(needs_kordoc),
            ", ".join(display_path(f["path"]) for f in needs_kordoc),
            readers.kordoc.INSTALL_COMMAND,
        )
    pruned: list[str] = []
    if ctx.full_scan:
        # documents of this source that are no longer at the target (deleted, moved out,
        # or the locator changed) leave the ontology; chunks/evidence go by cascade and the
        # changed chunk set makes consolidate run again
        gone = [d for d in ctx.docs.list_documents(ctx.source.id) if d.path not in seen]
        ctx.docs.delete_documents([d.id for d in gone])
        pruned = [d.path for d in gone]
        for path in pruned:
            logger.info("  %s: no longer present, removed from the source", display_path(path))
    if failed:
        # counted against the run (failed, documents listed); `--resume` reads them again
        logger.warning(
            "  %d document(s) failed to read: %s",
            len(failed),
            ", ".join(display_path(f["path"]) for f in failed),
        )
    return {
        "documents": documents,
        "failed": failed,
        "needs_kordoc": needs_kordoc,
        "duplicates": duplicates,
        "pruned": pruned,
        "superseded": superseded,
        "files": len(files),
        "partial": bool(failed),
        "new_or_changed": sum(d["action"] != "unchanged" for d in documents)
        + len(pruned)
        + len(stale),
    }


def _step_extract(ctx: _ExtractCtx) -> dict[str, Any]:
    titles = {d.id: d.title for d in ctx.docs.list_documents(ctx.source.id)}
    all_chunks = ctx.docs.chunks_for_source(ctx.source.id)
    pending = [c for c in all_chunks if c.extraction is None]
    limited = pending[: ctx.max_chunks] if ctx.max_chunks is not None else pending
    detail: dict[str, Any] = {
        "chunks": len(all_chunks),
        "pending": len(pending),
        "attempted": len(limited),
        "remaining": len(pending) - len(limited),
    }
    if not limited:
        logger.info("  nothing to extract (%d chunk(s) already extracted)", len(all_chunks))
        return {**detail, "succeeded": 0, "failed": 0, "paths": {}}
    totals = {c.doc_id: 0 for c in all_chunks}
    for c in all_chunks:
        totals[c.doc_id] += 1
    jobs = [
        ChunkJob(
            key=c.id,
            doc_title=titles.get(c.doc_id, ""),
            section_path=c.section_path,
            index=c.idx,
            total=totals[c.doc_id],
            text=c.text,
        )
        for c in limited
    ]
    run_id = ctx.run.id
    done = {"n": 0}
    paths: dict[str, int] = {}
    errors: list[str] = []
    failed_chunks: list[dict[str, Any]] = []
    retried = {"chunks": 0, "retries": 0}

    def on_start(job: ChunkJob) -> None:
        ctx.store.start_step(run_id, CHUNK_STEP_PREFIX + job.key)

    def on_done(outcome: ChunkOutcome) -> None:
        done["n"] += 1
        name = CHUNK_STEP_PREFIX + outcome.job.key
        if outcome.detail.get("upstream_retries"):
            retried["chunks"] += 1
            retried["retries"] += outcome.detail["upstream_retries"]
        if outcome.extraction is None:
            errors.append(outcome.error or "unknown error")
            failed_chunks.append(
                {
                    "chunk_id": outcome.job.key,
                    "document": outcome.job.doc_title,
                    "index": outcome.job.index,
                    "error": (outcome.error or "")[:300],
                }
            )
            ctx.docs.set_chunk_failure(outcome.job.key, {**outcome.detail, "error": outcome.error})
            ctx.store.finish_step(
                run_id, name, status="failed", detail={**outcome.detail, "error": outcome.error}
            )
            logger.warning(
                "  [%d/%d] chunk %s (%s #%d) FAILED: %s",
                done["n"],
                len(jobs),
                outcome.job.key[:8],
                outcome.job.doc_title,
                outcome.job.index,
                (outcome.error or "")[:200],
            )
            return
        ctx.docs.set_chunk_extraction(outcome.job.key, outcome.extraction.to_dict(), outcome.detail)
        ctx.store.finish_step(run_id, name, status="succeeded", detail=outcome.detail)
        path = outcome.detail.get("path", "?")
        paths[path] = paths.get(path, 0) + 1
        retries = outcome.detail.get("upstream_retries", 0)
        logger.info(
            "  [%d/%d] %s #%d: %d concept(s), %d relation(s) via %s in %.1fs%s",
            done["n"],
            len(jobs),
            outcome.job.doc_title,
            outcome.job.index,
            outcome.detail.get("concepts", 0),
            outcome.detail.get("relations", 0),
            path,
            outcome.detail.get("seconds", 0.0),
            f" ({retries} upstream retry/retries after a timeout or error)" if retries else "",
        )

    extractor = Extractor(ctx.backends.extraction())
    outcomes = extractor.run(jobs, workers=ctx.workers, on_start=on_start, on_done=on_done)
    succeeded = sum(o.extraction is not None for o in outcomes)
    detail.update(
        succeeded=succeeded,
        failed=len(outcomes) - succeeded,
        paths=paths,
        mode=extractor.mode,
        workers=ctx.workers,
        failed_chunks=failed_chunks,
        partial=bool(failed_chunks),
        retried_chunks=retried["chunks"],
        upstream_retries=retried["retries"],
        max_chunk_seconds=max((o.detail.get("seconds", 0.0) for o in outcomes), default=0.0),
    )
    if succeeded == 0:
        raise RuntimeError(
            f"all {len(outcomes)} chunk extraction(s) failed; first error: "
            f"{errors[0] if errors else '?'}"
        )
    return detail


#: Bump when consolidation logic changes so the next run consolidates again.
CONSOLIDATION_VERSION = "3"


def extraction_fingerprint(pairs: list[tuple[str, dict[str, Any]]]) -> str:
    digest = hashlib.sha256(CONSOLIDATION_VERSION.encode())
    for chunk_id, extraction in sorted(pairs, key=lambda p: p[0]):
        digest.update(chunk_id.encode())
        digest.update(json.dumps(extraction, sort_keys=True, ensure_ascii=False).encode())
    return digest.hexdigest()


def consolidation_config(settings: Settings, threshold: float, max_judge: int) -> dict[str, Any]:
    """What else, besides the extractions, decides a consolidation's result: part of its
    cache identity (a changed model, threshold or merge prompt consolidates again)."""
    from autoontology.config import expand_model_profile, resolve_triplet_field
    from autoontology.unstructured import prompts

    models = expand_model_profile(settings)
    return {
        "threshold": threshold,
        "max_judge": max_judge,
        "judge_model": resolve_triplet_field(models, "NON_REASONING", "MODEL") or "",
        "embed_model": resolve_triplet_field(models, "EMBED", "MODEL") or "",
        "merge_prompt": hashlib.sha256(prompts.MERGE_SYSTEM_PROMPT.encode()).hexdigest()[:16],
    }


def _consolidation_incomplete(detail: dict[str, Any]) -> bool:
    stats = detail.get("stats") or {}
    return bool(
        detail.get("partial")
        or stats.get("embedding_error")
        or stats.get("judge_failures")
        or stats.get("missing_vectors")
    )


def consolidate_step_name(run: Run, source: Source) -> str | None:
    """The step of *run* that consolidates *source*: ``consolidate`` in a single-source
    extract run, ``documents:<name>:consolidate`` in a unified build run."""
    if run.kind == EXTRACT_KIND and run.source_id == source.id:
        return "consolidate"
    if run.kind == "build":
        return f"documents:{source.name}:consolidate"
    return None


def _last_consolidation(ctx: _ExtractCtx) -> dict[str, Any] | None:
    for run in ctx.store.list_runs(None, limit=100):
        name = consolidate_step_name(run, ctx.source)
        if name is None or run.id == ctx.run.id:
            continue
        for step in ctx.store.get_steps(run.id):
            if step.name == name and step.status == "succeeded":
                return step.detail
    return None


def _materialised(ctx: _ExtractCtx, previous: dict[str, Any], current: str) -> bool:
    """Is the stored ontology the result *previous* describes? Only by the identity recorded
    with it: a record without one (from before identities existed) cannot be verified --
    equal counts can hide different concepts -- so it is never reused."""
    return bool(previous.get("ontology_sha")) and previous["ontology_sha"] == current


def _step_consolidate(ctx: _ExtractCtx) -> dict[str, Any]:
    chunks = [c for c in ctx.docs.chunks_for_source(ctx.source.id) if c.extraction is not None]
    if not chunks:
        raise RuntimeError("no extracted chunks to consolidate")
    # stored flags were computed by the rules of the run that extracted the chunk: apply the
    # current evidence rules again (no LLM) so rule changes reach earlier chunks
    pairs = [
        (c.id, reverify(ValidatedExtraction.from_dict(c.extraction), c.text).to_dict())
        for c in chunks
    ]  # type: ignore[arg-type]
    fingerprint = extraction_fingerprint(pairs)
    threshold = consolidate.threshold_from_env()
    max_judge = consolidate.max_judge_from_env()
    config = consolidation_config(ctx.settings, threshold, max_judge)
    previous = _last_consolidation(ctx)
    current = ctx.docs.ontology_identity(ctx.source.id)
    # cache identity = extractions + config (a consolidation recorded without a config
    # never matches); a partial one (swallowed embedding/judge failures) is never reused;
    # and the stored ontology must be that consolidation's result (its identity), or a
    # resume/other run that consolidated since would be reported as the older result
    if (
        previous
        and previous.get("fingerprint") == fingerprint
        and previous.get("config") == config
        and not _consolidation_incomplete(previous)
        and ctx.docs.list_concepts(ctx.source.id)
        and _materialised(ctx, previous, current)
    ):
        logger.info("  chunk extractions unchanged since the last consolidation: kept")
        return {
            **{k: v for k, v in previous.items() if k != "seconds"},
            "config": config,
            "ontology_sha": current,
            "action": "unchanged",
        }
    extractions = [(cid, ValidatedExtraction.from_dict(ext)) for cid, ext in pairs]
    result = consolidate.consolidate(
        extractions,
        embedder=ctx.backends.embed(),
        judge=make_judge(ctx.backends.judge()),
        threshold=threshold,
        max_judge=max_judge,
    )
    counts = ctx.docs.replace_ontology(ctx.source.id, result.concepts, result.relations)
    stats = result.stats.to_dict()
    logger.info(
        "  %d candidate(s) -> %d concept(s), %d relation(s); merges: %d exact, %d "
        "judged (%d judge call(s))",
        stats["candidates"],
        counts["concepts"],
        counts["relations"],
        stats["exact_merges"],
        stats["judge_merges"],
        stats["judge_calls"],
    )
    partial = result.stats.incomplete
    if partial:
        logger.warning(
            "  consolidation incomplete (embedding error: %s; %d judge failure(s); "
            "%d group(s) without a vector): stored, but the run is partial and the "
            "next run consolidates again",
            stats["embedding_error"] or "-",
            stats["judge_failures"],
            stats["missing_vectors"],
        )
    return {
        "action": "consolidated",
        "fingerprint": None if partial else fingerprint,
        "config": config,
        "chunks": len(chunks),
        "threshold": threshold,
        "stats": stats,
        "partial": partial,
        "ontology_sha": ctx.docs.ontology_identity(ctx.source.id),
        **counts,
    }


def _llm_timeout(settings: Settings) -> float | None:
    from autoontology.config import expand_model_profile

    raw = os.environ.get("LLM_INVOKE_TIMEOUT_S") or expand_model_profile(settings).get(
        "LLM_INVOKE_TIMEOUT_S", ""
    )
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _step_report(ctx: _ExtractCtx) -> dict[str, Any]:
    steps = {s.name: s.detail for s in ctx.store.get_steps(ctx.run.id)}
    data = report.ReportData(
        source=ctx.source.name,
        run_id=ctx.run.id,
        generated_at=now_iso(),
        documents=ctx.docs.list_documents(ctx.source.id),
        chunks=ctx.docs.chunks_for_source(ctx.source.id),
        concepts=ctx.docs.list_concepts(ctx.source.id),
        relations=ctx.docs.list_relations(ctx.source.id),
        concept_docs=ctx.docs.concept_documents(ctx.source.id),
        evidence=ctx.docs.evidence_stats(ctx.source.id),
        read_detail=steps.get("read", {}),
        extract_detail=steps.get("extract", {}),
        consolidate_detail=steps.get("consolidate", {}),
        llm_timeout_s=_llm_timeout(ctx.settings),
    )
    path = report.write(data, ctx.export_dir)
    counts = report.summary_counts(data)
    return {
        "report": str(path),
        **{k: v for k, v in counts.items() if k != "paths"},
        "paths": counts["paths"],
    }


EXTRACT_STEP_FUNCS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "read": _step_read,
    "extract": _step_extract,
    "consolidate": _step_consolidate,
    "report": _step_report,
}


#: Documents sources never own a Postgres database, so their names may also use "-".
DOCUMENTS_SOURCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_documents_source_name(name: str) -> str:
    if not DOCUMENTS_SOURCE_NAME_RE.fullmatch(name or ""):
        raise ValueError(
            f"invalid source name {name!r}: use lowercase letters, digits, _ "
            "and - (starting with a letter or digit)"
        )
    return name


def ensure_documents_source(
    store: Store, name: str, target: Path, recursive: bool = False
) -> Source:
    """The documents source *name* (created, or its locator updated, for *target*)."""
    return _documents_source(store, name, target, recursive)


def documents_target(source: Source) -> tuple[Path, bool]:
    """``(path, recursive)`` recorded on a documents source by the last extract/add."""
    data = json.loads(source.locator)
    return Path(data["path"]), bool(data.get("recursive"))


def _documents_source(store: Store, name: str, target: Path, recursive: bool) -> Source:
    validate_documents_source_name(name)
    locator = json.dumps(
        {"path": str(target.resolve()), "recursive": recursive}, ensure_ascii=False
    )
    source = store.get_source(name)
    if source is None:
        return store.add_source(name=name, kind="documents", locator=locator)
    if source.kind != "documents":
        raise JobError(
            f"source {name!r} is a {source.kind} source; `extract` needs a "
            "documents source (pick another name)"
        )
    if source.locator != locator:
        store.set_locator(source.id, locator)
        source = store.get_source(name) or source
    return source


def run_extract(
    source_name: str,
    target: Path,
    *,
    settings: Settings,
    store: Store,
    recursive: bool = False,
    resume: bool = False,
    workers: int | None = None,
    max_chunks: int | None = None,
    backends: Backends | None = None,
    check_service: bool = True,
) -> RunResult:
    target = Path(target)
    if not target.exists():
        raise JobError(f"no such file or directory: {target}")
    if max_chunks is not None and max_chunks < 0:
        raise JobError("--max-chunks must be >= 0")
    log_path = setup_job_logging(settings)
    with hold_sources(store, [source_name], EXTRACT_KIND, log_path=log_path):
        return _run_extract_held(
            source_name,
            target,
            settings=settings,
            store=store,
            recursive=recursive,
            resume=resume,
            workers=workers,
            max_chunks=max_chunks,
            backends=backends,
            check_service=check_service,
            log_path=log_path,
        )


def _run_extract_held(
    source_name: str,
    target: Path,
    *,
    settings: Settings,
    store: Store,
    recursive: bool,
    resume: bool,
    workers: int | None,
    max_chunks: int | None,
    backends: Backends | None,
    check_service: bool,
    log_path: Path,
) -> RunResult:
    from autoontology.unstructured.extractor import workers_default

    try:
        source = _documents_source(store, source_name, target, recursive)
    except ValueError as exc:
        raise JobError(str(exc)) from exc
    active = reap_stale_runs(store, source.id)
    if active:
        raise JobError(
            f"source {source_name!r} already has an active {active[0].kind} run "
            f"{active[0].id}; wait for it to finish",
            log_path=log_path,
        )
    if check_service:
        require_llm_proxy(settings, log_path=log_path)
    workers = workers or workers_default(settings)
    params = {
        "target": str(target.resolve()),
        "recursive": recursive,
        "resume": resume,
        "workers": workers,
        "max_chunks": max_chunks,
        "pid": os.getpid(),
    }
    previous = store.latest_run(source.id, EXTRACT_KIND) if resume else None
    if previous is not None:
        store.update_run(
            previous.id,
            status="running",
            params={**previous.params, **params, "resumed_at": now_iso()},
        )
        run = store.get_run(previous.id)
        assert run is not None
        already = store.succeeded_steps(run.id) & set(EXTRACT_STEPS)
        last = {s.name: s.detail for s in store.get_steps(run.id)}
        last_extract = last.get("extract", {})
        if last.get("read", {}).get("failed"):
            # documents failed to read: read them again (unchanged files stay no-ops);
            # _execute then recomputes every step after it
            already.discard("read")
        consolidated = last.get("consolidate")
        if "consolidate" in already and consolidated is not None:
            try:
                config = consolidation_config(
                    settings, consolidate.threshold_from_env(), consolidate.max_judge_from_env()
                )
            except ValueError as exc:
                raise JobError(str(exc)) from exc
            identity = consolidated.get("ontology_sha")
            if (
                _consolidation_incomplete(consolidated)
                or consolidated.get("config") != config
                or not identity
                or identity != DocumentStore(store).ontology_identity(source.id)
            ):
                # a stored consolidation is reusable only when complete, made with the
                # current threshold/models/prompt, and the stored ontology is still its
                # result (a legacy record without config or identity never is)
                logger.info(
                    "  consolidation of run %s is incomplete, its settings changed "
                    "or the stored ontology is not its result: consolidating again",
                    run.id,
                )
                already -= {"consolidate", "report"}
        if (
            previous.status != "succeeded"
            or last_extract.get("remaining")
            or last_extract.get("failed")
        ):
            # extract stopped part-way (or left chunks pending/failed): re-enter it (it only
            # does chunks without an extraction) and recompute everything after it.
            already -= {"extract", "consolidate", "report"}
        logger.info(
            "resuming extract run %s (succeeded: %s)", run.id, ", ".join(sorted(already)) or "-"
        )
    else:
        run = store.create_run(kind=EXTRACT_KIND, source_id=source.id, params=params)
        already = set()
        logger.info("extract run %s for source %s (%s)", run.id, source_name, target)
    ctx = _ExtractCtx(
        settings=settings,
        store=store,
        docs=DocumentStore(store),
        source=source,
        run=run,
        target=target,
        recursive=recursive,
        workers=workers,
        max_chunks=max_chunks,
        backends=backends or Backends(settings),
        full_scan=max_chunks is None and previous is None,
    )
    result = _execute(ctx, list(EXTRACT_STEPS), skip={}, already=already, steps=EXTRACT_STEP_FUNCS)
    result.export_dir = ctx.export_dir
    # the run is only "succeeded" when every document was read, every chunk extracted and
    # the consolidation complete: what succeeded is stored, so `--resume` (or a re-run)
    # retries just the rest
    steps = {s.name: s.detail for s in store.get_steps(run.id)}
    extract_detail = steps.get("extract", {})
    problems: list[str] = []
    extra: dict[str, Any] = {}
    if extract_detail.get("partial"):
        failed = extract_detail["failed_chunks"]
        listed = "; ".join(
            f"{f['document']} #{f['index']} ({f['chunk_id'][:8]}): {f['error'][:120]}"
            for f in failed[:10]
        )
        problems.append(
            f"{len(failed)} of {extract_detail['attempted']} chunk(s) failed: "
            f"{listed}" + (" ..." if len(failed) > 10 else "")
        )
        extra["failed_chunks"] = [f["chunk_id"] for f in failed]
    read_failed = steps.get("read", {}).get("failed") or []
    if read_failed:
        listed = "; ".join(
            f"{display_path(f['path'])} ({f['code']}): {f['error'][:120]}" for f in read_failed[:10]
        )
        problems.append(
            f"{len(read_failed)} document(s) failed to read: {listed}"
            + (" ..." if len(read_failed) > 10 else "")
        )
        extra["failed_documents"] = [f["path"] for f in read_failed]
    if steps.get("consolidate", {}).get("partial"):
        stats = steps["consolidate"].get("stats") or {}
        problems.append(
            f"consolidation incomplete (embedding error: "
            f"{stats.get('embedding_error') or '-'}; "
            f"{stats.get('judge_failures', 0)} merge judge failure(s); "
            f"{stats.get('missing_vectors', 0)} group(s) without a vector)"
        )
        extra["consolidation_partial"] = True
    if problems and result.status == "succeeded":
        error = "partial: " + " | ".join(problems)
        store.update_run(
            run.id,
            status="failed",
            error=error,
            params={**(store.get_run(run.id) or run).params, "partial": True, **extra},
        )
        result.status = "failed"
        logger.warning("extract run %s: %s", run.id, error)
    return result


def summary_rows(store: Store, source_name: str) -> list[tuple[str, ...]]:
    """Per-document rows for the CLI table: path, engine, pages, chunks, extracted, concepts."""
    source = store.get_source(source_name)
    if source is None:
        return []
    docs = DocumentStore(store)
    per_doc = docs.concept_documents(source.id)
    concept_count: dict[str, int] = {}
    for paths in per_doc.values():
        for path in paths:
            concept_count[path] = concept_count.get(path, 0) + 1
    rows = []
    for doc in docs.list_documents(source.id):
        chunks = docs.chunks_for_document(doc.id)
        rows.append(
            (
                display_path(doc.path),
                doc.engine,
                str(doc.pages or "-"),
                str(len(chunks)),
                str(sum(c.extraction is not None for c in chunks)),
                str(concept_count.get(doc.path, 0)),
            )
        )
    return rows
