# SPDX-License-Identifier: MIT
"""The align step of ``autoontology build``: link a documents source's concepts to the
upstream Terms of every structured source and enrich the Terms.

Steps ``terms -> candidates -> judge -> apply -> report``, persisted like ``build`` and
``extract`` (:func:`autoontology.engine.jobs._execute`). The judge writes one
``concept:<concept id>`` step row per concept, so ``--resume`` continues where it stopped;
a verdict is also cached on its alignment row by a key over the judge's inputs (concept,
candidates, model, prompt version), so a re-run with unchanged inputs makes no LLM call.

``apply`` (skipped with ``--dry-run``, which only plans) writes Term synonyms in-process
(:mod:`autoontology.engine.upstream_terms`: the public PATCH has no synonyms field) and a
description through ``PATCH /api/terms/{id}`` only when the Term has none or upstream's
fallback. It plans from the Terms' current state, so re-running changes nothing.
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import Settings
from autoontology.engine.client import BackendClient
from autoontology.engine.jobs import (
    JobError,
    RunResult,
    _execute,
    hold_sources,
    reap_stale_runs,
    require_llm_proxy,
    setup_job_logging,
    verify_sources,
)
from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import Run, Source, Store, now_iso
from autoontology.store.documents import DocumentStore
from autoontology.structured.semantic import StructuredSnapshot, combine, fetch_snapshot
from autoontology.unstructured import align as al
from autoontology.unstructured.extractor import LLMBackend, _is_connection_error, upstream_backend

logger = logging.getLogger("autoontology.jobs")

ALIGN_KIND = "align"
ALIGN_STEPS: tuple[str, ...] = ("terms", "candidates", "judge", "apply", "report")
CONCEPT_STEP_PREFIX = "concept:"

Embedder = Callable[[list[str], str], list[list[float] | None]]


def embed_model_name(settings: Settings) -> str:
    from autoontology.config import expand_model_profile, resolve_triplet_field

    return (
        resolve_triplet_field(expand_model_profile(settings), "EMBED", "MODEL")
        or os.environ.get("EMBED_MODEL", "")
        or "default"
    )


def make_typed_embedder(settings: Settings) -> Embedder:
    from autoontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    from auto_ontology.utils.embedding import embed_texts, get_embed_params

    params = get_embed_params()

    def embed(texts: list[str], input_type: str) -> list[list[float] | None]:
        return embed_texts(texts, params, input_type)  # type: ignore[arg-type]

    return embed


@dataclass
class AlignBackends:
    settings: Settings
    judge_llm: LLMBackend | None = None
    embedder: Embedder | None = None
    model: str | None = None

    def judge(self) -> LLMBackend:
        if self.judge_llm is None:
            self.judge_llm = upstream_backend(self.settings, role="non_reasoning", max_tokens=1024)
        return self.judge_llm

    def embed(self) -> Embedder:
        if self.embedder is None:
            self.embedder = make_typed_embedder(self.settings)
        return self.embedder

    def embed_model(self) -> str:
        return self.model or embed_model_name(self.settings)

    def judge_model(self) -> str:
        from autoontology.config import expand_model_profile, resolve_triplet_field

        return (
            resolve_triplet_field(expand_model_profile(self.settings), "NON_REASONING", "MODEL")
            or "default"
        )


class UpstreamTermWriter:
    """Synonyms in-process (no API field for them), descriptions through the PATCH."""

    def __init__(self, settings: Settings, client: BackendClient) -> None:
        self.settings = settings
        self.client = client

    def add_synonyms(self, term_id: str, additions: list[str]) -> dict[str, Any]:
        from autoontology.engine.upstream_terms import add_term_synonyms

        return add_term_synonyms(self.settings, term_id, additions)

    def patch_description(self, term_id: str, description: str) -> dict[str, Any]:
        return self.client.patch_term(term_id, {"description": description})

    def refresh_embeddings(self, term_id: str) -> bool:
        from autoontology.engine.upstream_terms import refresh_term

        return refresh_term(self.settings, term_id)

    def remove_synonyms(self, term_id: str, removals: list[str]) -> dict[str, Any]:
        from autoontology.engine.upstream_terms import remove_term_synonyms

        return remove_term_synonyms(self.settings, term_id, removals)


@dataclass
class _AlignCtx:
    settings: Settings
    store: Store
    docs: DocumentStore
    aligns: AlignmentStore
    source: Source
    #: every structured source whose Terms are candidates (their union)
    structured: list[Source]
    run: Run
    client: BackendClient
    backends: AlignBackends
    writer: al.TermWriter
    dry_run: bool
    min_sim: float
    min_conf: float
    top_k: int
    _snapshot: StructuredSnapshot | None = None
    #: sub-step details of this pass (the unified build runs align as one step)
    details: dict[str, Any] = field(default_factory=dict)
    #: the report folder (None: no align-report.md)
    report_dir: Path | None = None

    @property
    def export_dir(self) -> Path:
        return self.report_dir or self.settings.exports_dir / self.source.name / self.run.id

    @property
    def structured_label(self) -> str:
        return "+".join(s.name for s in self.structured)

    def snapshot(self, refresh: bool = False) -> StructuredSnapshot:
        if self._snapshot is None or refresh:
            self._snapshot = combine([fetch_snapshot(self.client, s) for s in self.structured])
        return self._snapshot


def _concepts(ctx: _AlignCtx) -> list[al.ConceptInfo]:
    quotes: dict[str, tuple[int, str]] = {}
    for row in ctx.docs.evidence_rows(ctx.source.id):
        cid = row["concept_id"]
        if not cid:
            continue
        rank = 0 if row["verified"] else 1
        if cid not in quotes or rank < quotes[cid][0]:
            quotes[cid] = (rank, row["quote"])
    return [
        al.ConceptInfo(
            id=c.id,
            name=c.name,
            kind=c.kind,
            definition=c.definition,
            name_en=c.name_en,
            synonyms=c.synonyms,
            attributes=c.attributes,
            evidence=quotes.get(c.id, (0, ""))[1],
        )
        for c in sorted(ctx.docs.list_concepts(ctx.source.id), key=lambda c: c.name)
    ]


def _step_terms(ctx: _AlignCtx) -> dict[str, Any]:
    snap = ctx.snapshot()
    if not snap.terms:
        raise RuntimeError(
            f"structured source(s) {ctx.structured_label} have no Terms in the upstream "
            "semantic layer; run `uv run autoontology build` first"
        )
    for t in snap.terms:
        logger.info(
            "  Term %s: %d synonym(s), %d column attribute(s)%s",
            t.name,
            len(t.synonyms),
            len(t.attributes),
            " [fallback]" if t.is_fallback else "",
        )
    return {
        "structured_source": ctx.structured_label,
        "structured_sources": [s.name for s in ctx.structured],
        "database_id": snap.database_id,
        "terms": len(snap.terms),
        "term_names": [t.name for t in snap.terms],
        "fallback_terms": [t.name for t in snap.terms if t.is_fallback],
        "semantic_fks": len(snap.fks),
    }


def _step_candidates(ctx: _AlignCtx) -> dict[str, Any]:
    snap = ctx.snapshot()
    model = ctx.backends.embed_model()
    term_vectors: dict[str, list[float] | None] = {}
    missing: list[tuple[str, str, str]] = []
    for t in snap.terms:
        text = al.term_text(t)
        sha = al.text_sha256(text)
        cached = ctx.aligns.get_term_embedding(t.id, sha, model)
        if cached is not None:
            term_vectors[t.id] = cached
        else:
            missing.append((t.id, sha, text))
    if missing:
        vectors = ctx.backends.embed()([m[2] for m in missing], "passage")
        for (tid, sha, _), vec in zip(missing, vectors):
            term_vectors[tid] = vec
            if vec:
                ctx.aligns.put_term_embedding(tid, sha, model, vec)
    concepts = [c for c in _concepts(ctx) if c.kind in al.ALIGNABLE_KINDS]
    vectors = (
        ctx.backends.embed()([al.concept_text(c) for c in concepts], "query") if concepts else []
    )
    candidates: dict[str, list[list[Any]]] = {}
    for concept, vec in zip(concepts, vectors):
        found = al.top_candidates(vec, term_vectors, k=ctx.top_k, floor=ctx.min_sim)
        candidates[concept.id] = [[c.term_id, c.similarity] for c in found]
    with_candidates = sum(bool(v) for v in candidates.values())
    logger.info(
        "  %d Term embedding(s) cached, %d computed (%s); %d of %d concept(s) have "
        "a candidate with similarity >= %.2f",
        len(snap.terms) - len(missing),
        len(missing),
        model,
        with_candidates,
        len(concepts),
        ctx.min_sim,
    )
    return {
        "model": model,
        "term_embeddings": {"cached": len(snap.terms) - len(missing), "computed": len(missing)},
        "alignable": len(concepts),
        "with_candidates": with_candidates,
        "min_sim": ctx.min_sim,
        "top_k": ctx.top_k,
        "candidates": candidates,
    }


def _record(ctx: _AlignCtx, concept: al.ConceptInfo, relation: str, **kw: Any) -> AlignmentRecord:
    kw.setdefault("dry_run", ctx.dry_run)
    term_id = kw.get("term_id") or ""
    return AlignmentRecord(
        source_id=ctx.source.id,
        concept_id=concept.id,
        concept_name=concept.name,
        relation=relation,
        run_id=ctx.run.id,
        # the structured source that owns the verdict's Term (none: every candidate source)
        structured_source=ctx.snapshot().source_of(term_id) if term_id else ctx.structured_label,
        **kw,
    )


def _eligible(ctx: _AlignCtx, relation: str, confidence: float | None) -> bool:
    """A verdict the apply policy acts on: ``same`` at or above the identity bar
    (``max(--min-conf, 0.90)``); the identity policy already turned every other ``same``
    into ``related`` in the judge step, this is the last line of defence."""
    return relation == "same" and (confidence or 0.0) >= al.identity_bar(ctx.min_conf)


def _accepts(ctx: _AlignCtx, relation: str, confidence: float | None) -> bool:
    """Persisted ``accepted`` (what export folds into a Term): never for a dry run, so a dry
    run cannot change the next export."""
    return not ctx.dry_run and _eligible(ctx, relation, confidence)


def _step_judge(ctx: _AlignCtx) -> dict[str, Any]:
    snap = ctx.snapshot()
    terms = snap.term_map()
    steps = {s.name: s for s in ctx.store.get_steps(ctx.run.id)}
    if "candidates" in ctx.details:
        candidates = ctx.details["candidates"].get("candidates", {})
    else:
        candidates = (
            steps["candidates"].detail.get("candidates", {}) if "candidates" in steps else {}
        )
    judge = al.make_alignment_judge(ctx.backends.judge())
    model = ctx.backends.judge_model()
    concepts = _concepts(ctx)
    counts: Counter[str] = Counter()
    stats = {"judged": 0, "cached": 0, "resumed": 0, "failed": 0, "proposals": 0}
    failed: list[dict[str, str]] = []
    auto_added = ctx.aligns.alias_additions()  # synonyms AutoOntology wrote: never evidence
    auto_descriptions = ctx.aligns.auto_descriptions()  # left out of the judge key too
    # Terms with a legacy description record (no text): every concept judged against one
    # is judged again (no resume, no cache), and only that successful judge call confirms
    # the description it saw as the baseline (never a permanent wildcard)
    legacy = {tid for tid, texts in auto_descriptions.items() if al.ANY_DESCRIPTION in texts}

    def guard(
        raw: al.ValidatedVerdict, concept: al.ConceptInfo
    ) -> tuple[al.ValidatedVerdict, dict[str, Any]]:
        verdict, proposal = al.guard_verdict(
            raw,
            concept,
            terms.get(raw.term_id),
            min_conf=ctx.min_conf,
            auto_added=auto_added.get(raw.term_id, []),
        )
        stats["proposals"] += bool(proposal)
        return verdict, proposal

    for index, concept in enumerate(concepts, start=1):
        name = CONCEPT_STEP_PREFIX + concept.id
        # the judge's input now: concept content, candidate Term ids + content, model and
        # prompt version (judge_key); none for a concept that is not judged
        alignable = concept.kind in al.ALIGNABLE_KINDS
        found = (
            [(tid, sim) for tid, sim in candidates.get(concept.id, []) if tid in terms]
            if alignable
            else []
        )
        cand_terms = [terms[tid] for tid, _ in found]
        sims = dict(found)
        cand_json = [
            {"term_id": t.id, "term_name": t.name, "similarity": sims[t.id]} for t in cand_terms
        ]
        key = (
            al.judge_key(
                al.concept_payload(concept),
                [al.candidate_payload(t) for t in cand_terms],
                model,
                auto_added=auto_added,
                auto_descriptions=auto_descriptions,
            )
            if found
            else None
        )
        unconfirmed = [t for t in cand_terms if t.id in legacy]
        prior = steps.get(name)
        if prior is not None and prior.status == "succeeded" and not unconfirmed:
            # the verdict this run already wrote -- in either table: the resumed run may
            # differ in --dry-run/--min-conf (or the identity policy) from the one that
            # judged, so relation and acceptance are recomputed from the judge's verdict
            # (a refused proposal included), never taken from the stored ones
            mine = [
                r
                for r in (
                    ctx.aligns.get(concept.id, preview=ctx.dry_run),
                    ctx.aligns.get(concept.id, preview=not ctx.dry_run),
                )
                if r is not None
                and r.run_id == ctx.run.id
                # only while the judge's input is unchanged (the candidates may have moved
                # since: a re-aligned structured source) and its Term still exists
                and (r.judge_key or None) == key
                and (not r.term_id or r.term_id in terms)
            ]
            if mine:
                existing = mine[0]
                stats["resumed"] += 1
                verdict, proposal = guard(_raw_verdict(existing), concept)
                counts[verdict.relation] += 1
                accepted = _accepts(ctx, verdict.relation, verdict.confidence)
                if (accepted, ctx.dry_run, verdict.relation, proposal) != (
                    existing.accepted,
                    existing.dry_run,
                    existing.relation,
                    existing.proposal,
                ):
                    keep = (
                        existing.applied
                        if not existing.dry_run
                        and not ctx.dry_run
                        and _eligible(ctx, verdict.relation, verdict.confidence)
                        else {}
                    )
                    ctx.aligns.put(
                        AlignmentRecord(
                            **{
                                **existing.__dict__,
                                "id": None,
                                "relation": verdict.relation,
                                "accepted": accepted,
                                "dry_run": ctx.dry_run,
                                "applied": keep,
                                "proposal": proposal,
                            }
                        )
                    )
                continue
        ctx.store.start_step(ctx.run.id, name)
        started = time.monotonic()
        if not alignable:
            ctx.aligns.put(
                _record(ctx, concept, "skipped", rationale=f"kind {concept.kind} is not aligned")
            )
            counts["skipped"] += 1
            ctx.store.finish_step(
                ctx.run.id,
                name,
                status="succeeded",
                detail={"relation": "skipped", "kind": concept.kind},
            )
            continue
        if not found:
            ctx.aligns.put(
                _record(
                    ctx,
                    concept,
                    "none",
                    rationale=(f"no Term with similarity >= {ctx.min_sim:.2f}"),
                )
            )
            counts["none"] += 1
            ctx.store.finish_step(
                ctx.run.id, name, status="succeeded", detail={"relation": "none", "candidates": 0}
            )
            continue
        # committed verdict (for `applied`); the judge cache also reuses a preview's verdict
        existing = ctx.aligns.get(concept.id)
        hit = next(
            (
                r
                for r in (existing, ctx.aligns.get(concept.id, preview=True))
                # the key covers the candidate Term ids, so it never matches across sources
                if r is not None and r.judge_key == key
            ),
            None,
        )
        if unconfirmed:
            hit = None  # judged once against the current description (legacy record)
        path = "cached"
        if hit is not None:
            verdict = _raw_verdict(hit)
            stats["cached"] += 1
        else:
            try:
                verdict = al.judge_concept(judge, concept, cand_terms)
            except Exception as exc:  # noqa: BLE001 - per concept; resume retries it
                if _is_connection_error(exc):
                    raise
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                stats["failed"] += 1
                failed.append({"concept_id": concept.id, "concept": concept.name, "error": error})
                ctx.store.finish_step(ctx.run.id, name, status="failed", detail={"error": error})
                logger.warning(
                    "  [%d/%d] %s: judge FAILED: %s",
                    index,
                    len(concepts),
                    concept.name,
                    error[:200],
                )
                continue
            path = "judged"
            stats["judged"] += 1
        # also on cached verdicts, so a rule change reaches them without an LLM call
        verdict, proposal = guard(verdict, concept)
        accepted = _accepts(ctx, verdict.relation, verdict.confidence)
        keep_applied = (
            existing.applied
            if _eligible(ctx, verdict.relation, verdict.confidence)
            and existing is not None
            and existing.term_id == verdict.term_id
            else {}
        )
        ctx.aligns.put(
            _record(
                ctx,
                concept,
                verdict.relation,
                term_id=verdict.term_id,
                term_name=terms[verdict.term_id].name if verdict.term_id else "",
                similarity=sims.get(verdict.term_id, found[0][1]),
                confidence=verdict.confidence,
                rationale=verdict.rationale,
                accepted=accepted,
                dry_run=ctx.dry_run,
                judge_key=key,
                candidates=cand_json,
                applied=keep_applied,
                proposal=proposal,
            )
        )
        if unconfirmed and path == "judged":  # after the put, which may carry the old record
            ctx.aligns.settle_legacy_descriptions({t.id: t.description for t in unconfirmed})
        counts[verdict.relation] += 1
        seconds = round(time.monotonic() - started, 1)
        ctx.store.finish_step(
            ctx.run.id,
            name,
            status="succeeded",
            detail={
                "relation": verdict.relation,
                "term_id": verdict.term_id,
                "confidence": verdict.confidence,
                "path": path,
                "notes": verdict.notes,
                "candidates": len(cand_terms),
                "seconds": seconds,
                "proposal": proposal,
            },
        )
        logger.info(
            "  [%d/%d] %s (%s) -> %s %s (conf %.2f, sim %.3f)%s%s",
            index,
            len(concepts),
            concept.name,
            concept.kind,
            verdict.relation,
            terms[verdict.term_id].name if verdict.term_id else "-",
            verdict.confidence,
            sims.get(verdict.term_id, found[0][1]),
            " [accepted]" if accepted else "",
            " (cached)" if path == "cached" else f" in {seconds:.1f}s",
        )
    return {
        "concepts": len(concepts),
        "relations": dict(counts),
        **stats,
        "failed_concepts": failed,
        "partial": bool(failed),
        "min_conf": ctx.min_conf,
        "identity_policy": al.IDENTITY_POLICY_VERSION,
        "identity_min_conf": al.identity_bar(ctx.min_conf),
    }


def _raw_verdict(record: AlignmentRecord) -> al.ValidatedVerdict:
    """The judge's verdict as stored: a refused ``same`` lives in ``proposal``."""
    p = record.proposal or {}
    if p.get("relation") == "same" and p.get("term_id"):
        return al.ValidatedVerdict(
            str(p["term_id"]), "same", float(p.get("confidence") or 0), record.rationale
        )
    return al.ValidatedVerdict(
        record.term_id, record.relation, record.confidence or 0.0, record.rationale
    )


def _step_apply(ctx: _AlignCtx) -> dict[str, Any]:
    # eligible rather than `accepted`: a dry run persists accepted=False but still plans
    # the verdicts of this mode's table: a dry run plans from its previews
    known = ctx.snapshot().term_map()
    eligible = [
        a
        for a in ctx.aligns.list(ctx.source.id, preview=ctx.dry_run)
        if _eligible(ctx, a.relation, a.confidence) and a.term_id in known
    ]
    concepts = {c.id: c for c in ctx.docs.list_concepts(ctx.source.id)}
    matches: dict[str, list[al.SameMatch]] = {}
    for a in eligible:
        c = concepts.get(a.concept_id)
        if c is None:
            continue
        matches.setdefault(a.term_id, []).append(
            al.SameMatch(
                alignment_id=a.id,
                concept_name=c.name,
                synonyms=c.synonyms,
                definition=c.definition,
                confidence=a.confidence or 0.0,
            )
        )
    # plan from the Terms' current state (an earlier run or a person may have edited them);
    # the writer merges again inside its transaction, so a concurrent edit is never lost
    snap = ctx.snapshot(refresh=True)
    pending = (
        {} if ctx.dry_run else _retry_pending_refreshes(ctx.aligns, ctx.writer, snap.term_map())
    )
    patches = al.plan_patches(matches, snap.term_map())
    by_term: dict[str, list[Any]] = {}
    for a in eligible:
        by_term.setdefault(a.term_id, []).append(a)
    out: list[dict[str, Any]] = []
    changed = 0
    refresh_failed: list[str] = []
    for patch in patches:
        entry = patch.to_dict()
        if not patch.changed:
            entry["action"] = "unchanged"
            logger.info(
                "  %s: unchanged (already has %s)",
                patch.term_name,
                ", ".join(patch.synonyms_before) or "-",
            )
            stale = [
                a
                for a in by_term.get(patch.term_id, [])
                if a.applied.get("embeddings_refreshed") is False
            ]
            if stale and ctx.dry_run:
                entry["embeddings_refreshed"] = False
                refresh_failed.append(patch.term_name)
            elif stale:
                # an earlier write's embedding refresh failed: retry it (no other write)
                ok = ctx.writer.refresh_embeddings(patch.term_id)
                entry.update(action="refreshed" if ok else "unchanged", embeddings_refreshed=ok)
                for a in stale:
                    ctx.aligns.set_applied(
                        a.id,
                        {**a.applied, "embeddings_refreshed": ok, "refresh_retried_at": now_iso()},
                    )
                logger.log(
                    logging.INFO if ok else logging.WARNING,
                    "  %s: embedding refresh retried: %s",
                    patch.term_name,
                    "ok" if ok else "FAILED again",
                )
                if not ok:
                    refresh_failed.append(patch.term_name)
        elif ctx.dry_run:
            entry["action"] = "planned"
            logger.info(
                "  %s: would add synonyms %s%s (dry run)",
                patch.term_name,
                patch.synonyms_added,
                "; would set the description" if patch.description else "",
            )
        else:
            result = al.apply_patch(patch, ctx.writer)
            changed += 1
            entry.update(action="applied", result=result, synonyms_added=result["synonyms_added"])
            if result.get("embeddings_refreshed") is False:
                entry["embeddings_refreshed"] = False
                refresh_failed.append(patch.term_name)
            # never identity evidence from now on (alias registry, review #4)
            ctx.aligns.record_alias_additions(
                patch.term_id,
                result["synonyms_added"],
                run_id=ctx.run.id,
                concepts=patch.concepts,
                source_id=ctx.source.id,  # the documents source (a build run has none)
            )
            applied = {"run_id": ctx.run.id, "at": now_iso(), **result}
            for alignment_id in patch.alignment_ids:
                ctx.aligns.set_applied(alignment_id, applied)
            logger.info(
                "  %s: added synonyms %s%s%s",
                patch.term_name,
                result["synonyms_added"],
                "; set the description" if patch.description else "",
                "; embedding refresh FAILED (retried next run)"
                if result.get("embeddings_refreshed") is False
                else "",
            )
        if patch.kept_definitions:
            logger.info(
                "  %s: has its own description; %d document definition(s) kept for the export only",
                patch.term_name,
                len(patch.kept_definitions),
            )
        out.append(entry)
    return {
        "dry_run": ctx.dry_run,
        "accepted": len(eligible),
        "patches": out,
        "applied": changed,
        "planned": sum(e["action"] == "planned" for e in out),
        "unchanged": sum(e["action"] == "unchanged" for e in out),
        "refreshed": sum(e["action"] == "refreshed" for e in out),
        "refresh_failed": refresh_failed,
        "pending_refreshes": pending,
    }


def _step_report(ctx: _AlignCtx) -> dict[str, Any]:
    from autoontology.export import align_report

    steps = {s.name: s.detail for s in ctx.store.get_steps(ctx.run.id)}
    steps.update(ctx.details)
    path = align_report.write(
        ctx.export_dir,
        source=ctx.source.name,
        structured=ctx.structured_label,
        run_id=ctx.run.id,
        alignments=ctx.aligns.list(ctx.source.id, preview=ctx.dry_run),
        concepts=ctx.docs.list_concepts(ctx.source.id),
        steps=steps,
        min_sim=ctx.min_sim,
        min_conf=ctx.min_conf,
    )
    return {"report": str(path)}


ALIGN_STEP_FUNCS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "terms": _step_terms,
    "candidates": _step_candidates,
    "judge": _step_judge,
    "apply": _step_apply,
    "report": _step_report,
}


def _structured_source(store: Store, name: str | None) -> Source:
    if name:
        source = store.get_source(name)
        if source is None:
            raise JobError(f"unknown source {name!r} (see `autoontology source list`)")
        if source.kind not in ("files", "database"):
            raise JobError(
                f"--structured-source {name!r} is a {source.kind} source; pick a "
                "files or database source"
            )
        return source
    built = [
        s for s in store.list_sources() if s.kind in ("files", "database") and s.catalog_database_id
    ]
    if len(built) != 1:
        names = ", ".join(s.name for s in built) or "none"
        raise JobError(f"pick the structured source with --structured-source (built: {names})")
    return built[0]


def run_align(
    source_name: str,
    *,
    settings: Settings,
    store: Store,
    structured_source: str | None = None,
    dry_run: bool = False,
    resume: bool = False,
    min_sim: float | None = None,
    min_conf: float | None = None,
    top_k: int = al.DEFAULT_TOP_K,
    client: BackendClient | None = None,
    backends: AlignBackends | None = None,
    writer: al.TermWriter | None = None,
    check_service: bool = True,
) -> RunResult:
    log_path = setup_job_logging(settings)
    source = store.get_source(source_name)
    if source is None:
        raise JobError(f"unknown source {source_name!r} (see `autoontology source list`)")
    if source.kind != "documents":
        raise JobError(
            f"source {source_name!r} is a {source.kind} source; `align` needs a "
            "documents source (`autoontology source add <path> --kind documents`)"
        )
    docs = DocumentStore(store)
    if not docs.list_concepts(source.id):
        raise JobError(
            f"source {source_name!r} has no concepts; run `uv run autoontology build "
            f"--source {source_name}` first"
        )
    structured = _structured_source(store, structured_source)
    try:
        min_sim = al.min_sim_from_env() if min_sim is None else min_sim
        min_conf = al.min_conf_from_env() if min_conf is None else min_conf
    except ValueError as exc:
        raise JobError(str(exc)) from exc
    for label, value in (("--threshold", min_sim), ("--min-conf", min_conf)):
        if not 0.0 <= value <= 1.0:
            raise JobError(f"{label} must be between 0 and 1, got {value}")
    # the structured source too: align writes its upstream Terms, which a build replaces
    with hold_sources(store, [source.name, structured.name], ALIGN_KIND, log_path=log_path):
        return _run_align_held(
            source,
            structured,
            settings=settings,
            store=store,
            docs=docs,
            dry_run=dry_run,
            resume=resume,
            min_sim=min_sim,
            min_conf=min_conf,
            top_k=top_k,
            client=client,
            backends=backends,
            writer=writer,
            check_service=check_service,
            log_path=log_path,
        )


def _run_align_held(
    source: Source,
    structured: Source,
    *,
    settings: Settings,
    store: Store,
    docs: DocumentStore,
    dry_run: bool,
    resume: bool,
    min_sim: float,
    min_conf: float,
    top_k: int,
    client: BackendClient | None,
    backends: AlignBackends | None,
    writer: al.TermWriter | None,
    check_service: bool,
    log_path: Path,
) -> RunResult:
    source, structured = verify_sources(store, [source, structured], log_path=log_path)
    source_name = source.name
    active = reap_stale_runs(store, source.id)
    if active:
        raise JobError(
            f"source {source_name!r} already has an active {active[0].kind} run "
            f"{active[0].id}; wait for it to finish",
            log_path=log_path,
        )
    client = client or BackendClient(settings.backend_url)
    client.health()  # BackendUnavailable -> "run autoontology up"
    if check_service:
        require_llm_proxy(settings, log_path=log_path)
    params = {
        "structured_source": structured.name,
        "dry_run": dry_run,
        "resume": resume,
        "min_sim": min_sim,
        "min_conf": min_conf,
        "top_k": top_k,
        "pid": os.getpid(),
        "identity_policy": al.IDENTITY_POLICY_VERSION,
    }
    previous = store.latest_run(source.id, ALIGN_KIND) if resume else None
    changed = [
        k
        for k in ("structured_source", "min_sim", "top_k")
        if previous is not None and previous.params.get(k) != params[k]
    ]
    if changed:
        # the candidates (and so every per-concept verdict of that run) depend on these
        logger.info(
            "--resume: %s changed since run %s; starting a new run", ", ".join(changed), previous.id
        )
        previous = None
    if previous is not None:
        store.update_run(
            previous.id,
            status="running",
            params={**previous.params, **params, "resumed_at": now_iso()},
        )
        run = store.get_run(previous.id)
        assert run is not None
        already = store.succeeded_steps(run.id) & set(ALIGN_STEPS)
        judge = next((s.detail for s in store.get_steps(run.id) if s.name == "judge"), {})
        if previous.status != "succeeded" or judge.get("failed"):
            # the judge resumes per concept; everything after it is recomputed
            already -= {"judge", "apply", "report"}
        if any(
            previous.params.get(k) != v
            for k, v in (
                ("dry_run", dry_run),
                ("min_conf", min_conf),
                ("identity_policy", al.IDENTITY_POLICY_VERSION),
            )
        ):
            # acceptance depends on these: the judge recomputes it per concept from the
            # stored verdicts (no LLM call), then apply/report run again
            already -= {"judge", "apply", "report"}
        logger.info(
            "resuming align run %s (succeeded: %s)", run.id, ", ".join(sorted(already)) or "-"
        )
    else:
        run = store.create_run(kind=ALIGN_KIND, source_id=source.id, params=params)
        already = set()
        logger.info(
            "align run %s: %s -> %s%s",
            run.id,
            source_name,
            structured.name,
            " (dry run)" if dry_run else "",
        )
    ctx = _AlignCtx(
        settings=settings,
        store=store,
        docs=docs,
        aligns=AlignmentStore(store),
        source=source,
        structured=[structured],
        run=run,
        client=client,
        backends=backends or AlignBackends(settings),
        writer=writer or UpstreamTermWriter(settings, client),
        dry_run=dry_run,
        min_sim=min_sim,
        min_conf=min_conf,
        top_k=top_k,
    )
    result = _execute(ctx, list(ALIGN_STEPS), skip={}, already=already, steps=ALIGN_STEP_FUNCS)
    result.export_dir = ctx.export_dir
    judge_detail = result.details.get("judge", {})
    if judge_detail.get("partial"):
        failed = judge_detail["failed_concepts"]
        error = f"partial: {len(failed)} concept(s) failed: " + "; ".join(
            f"{f['concept']}: {f['error'][:120]}" for f in failed[:10]
        )
        store.update_run(
            run.id,
            status="failed",
            error=error,
            params={**(store.get_run(run.id) or run).params, "partial": True},
        )
        result.status = "failed"
        logger.warning("align run %s: %s", run.id, error)
    return result


def summary(
    store: Store, source_name: str, result: RunResult, structured_source: str | None = None
) -> dict[str, Any]:
    """Counts for the CLI table: relation counts (of the verdicts against
    *structured_source*, default: the run's) and patches applied/planned."""
    source = store.get_source(source_name)
    run = store.get_run(result.run_id)
    structured_source = structured_source or (run.params.get("structured_source") if run else None)
    dry = bool(run.params.get("dry_run")) if run else False
    rows = [
        a
        for a in (AlignmentStore(store).list(source.id, preview=dry) if source else [])
        if structured_source is None or a.structured_source == structured_source
    ]
    counts = Counter(a.relation for a in rows)
    apply = result.details.get("apply") or next(
        (s.detail for s in store.get_steps(result.run_id) if s.name == "apply"), {}
    )
    return {
        "relations": {
            r: counts.get(r, 0)
            for r in ("same", "broader", "narrower", "related", "none", "skipped")
        },
        "accepted": sum(a.accepted for a in rows),
        "applied": apply.get("applied", 0),
        "planned": apply.get("planned", 0),
        "unchanged": apply.get("unchanged", 0),
        "dry_run": apply.get("dry_run", False),
        "patches": apply.get("patches", []),
        "would_accept": sum(
            a.dry_run
            and a.relation == "same"
            and (a.confidence or 0) >= (run.params.get("min_conf", 0.7) if run else 0.7)
            for a in rows
        ),
        "refresh_failed": apply.get("refresh_failed", []),
    }


def _retry_pending_refreshes(
    aligns: AlignmentStore, writer: al.TermWriter, terms: dict[str, Any]
) -> dict[str, bool]:
    """Re-run the embedding refresh of Terms whose earlier write could not refresh."""
    out: dict[str, bool] = {}
    for term_id in aligns.pending_refreshes():
        if term_id not in terms:
            continue
        ok = bool(writer.refresh_embeddings(term_id))
        out[term_id] = ok
        if ok:
            aligns.clear_pending_refresh(term_id)
        logger.log(
            logging.INFO if ok else logging.WARNING,
            "  %s: pending embedding refresh %s",
            terms[term_id].name,
            "done" if ok else "FAILED again",
        )
    return out


def terms_fingerprint(store: Store, client: BackendClient, structured: list[Source]) -> str:
    """The upstream Terms of *structured* now (:func:`al.terms_fingerprint`), what
    ``build --resume`` compares to decide whether an ``align:<docs>`` step is reusable."""
    aligns = AlignmentStore(store)
    snap = combine([fetch_snapshot(client, s) for s in structured])
    terms = snap.term_map()
    described = aligns.auto_descriptions()
    if any(al.ANY_DESCRIPTION in described.get(tid, ()) for tid in terms):
        return ""  # a legacy description not confirmed by a judge call yet: never reusable
    return al.terms_fingerprint(list(terms.values()), aligns.alias_additions(), described)


def align_pass(
    *,
    settings: Settings,
    store: Store,
    run: Run,
    source: Source,
    structured: list[Source],
    client: BackendClient,
    backends: AlignBackends | None = None,
    writer: al.TermWriter | None = None,
    report_dir: Path | None = None,
    min_sim: float | None = None,
    min_conf: float | None = None,
    top_k: int = al.DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Align documents *source* to the union of the Terms of *structured*, inside the run
    *run* (the ``align:<source>`` step of ``autoontology build``): the align steps run in
    order in memory; the judge's ``concept:<id>`` rows are written to *run*, so a resumed
    build continues per concept. Returns the step detail (no per-concept candidate lists)."""
    try:
        min_sim = al.min_sim_from_env() if min_sim is None else min_sim
        min_conf = al.min_conf_from_env() if min_conf is None else min_conf
    except ValueError as exc:
        raise JobError(str(exc)) from exc
    ctx = _AlignCtx(
        settings=settings,
        store=store,
        docs=DocumentStore(store),
        aligns=AlignmentStore(store),
        source=source,
        structured=structured,
        run=run,
        client=client,
        backends=backends or AlignBackends(settings),
        writer=writer or UpstreamTermWriter(settings, client),
        dry_run=False,
        min_sim=min_sim,
        min_conf=min_conf,
        top_k=top_k,
        report_dir=report_dir,
    )
    for name in ALIGN_STEPS:
        if name == "report" and report_dir is None:
            continue
        started = time.monotonic()
        detail = ALIGN_STEP_FUNCS[name](ctx)
        detail["seconds"] = round(time.monotonic() - started, 1)
        ctx.details[name] = detail
        logger.info("  align %s: ok (%.1fs)", name, detail["seconds"])
    terms, judge, apply = (ctx.details[k] for k in ("terms", "judge", "apply"))
    rows = [a for a in ctx.aligns.list(source.id) if not a.dry_run]
    return {
        "structured_sources": terms["structured_sources"],
        "terms": terms["terms"],
        "fallback_terms": terms["fallback_terms"],
        "alignable": ctx.details["candidates"]["alignable"],
        "with_candidates": ctx.details["candidates"]["with_candidates"],
        "relations": judge["relations"],
        "judged": judge["judged"],
        "cached": judge["cached"],
        "resumed": judge["resumed"],
        "failed": judge["failed"],
        "failed_concepts": judge["failed_concepts"],
        "partial": judge["partial"],
        "accepted": sum(a.accepted for a in rows),
        "accepted_same": sorted(
            f"{a.concept_name} -> {a.term_name}"
            for a in rows
            if a.accepted and a.relation == "same"
        ),
        "applied": apply["applied"],
        "unchanged": apply["unchanged"],
        "refresh_failed": apply["refresh_failed"],
        "min_sim": min_sim,
        "min_conf": min_conf,
        "top_k": top_k,
        "identity_policy": al.IDENTITY_POLICY_VERSION,
        **({"report": ctx.details["report"]["report"]} if "report" in ctx.details else {}),
    }
