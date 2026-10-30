# SPDX-License-Identifier: MIT
"""``autoontology apply``: reflect the review decisions in a new snapshot, without a model
call.

One ``apply`` run, three steps:

1. ``decisions``: every alignment decision is written onto its concept's alignment row
   (:mod:`autoontology.engine.overlay`; a removed decision gives the row its judged
   verdict back), and the synonyms of a person's accepted ``same`` are added to the
   upstream Term (the align ``apply`` step's code path, so they are recorded as
   AutoOntology's own and never become identity evidence);
2. ``merge``: the unified model over every source (as in ``build``);
3. ``snapshot``: the next revision; the decisions it reflects get ``applied_revision``.

With nothing pending (no unapplied decision, no removed one) merge and snapshot are
recorded ``skipped`` ("no pending decisions") and no revision is written -- so a scheduled
``build --resume && apply`` adds no empty revisions -- unless ``force``.

It needs the upstream backend (the structured Terms) but never the LLM proxy, and holds
the workspace lease like build and export: it refuses to start while one of them runs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from autoontology.config import Settings
from autoontology.engine import align as align_job
from autoontology.engine import jobs, overlay
from autoontology.engine.build import (
    APPLY_KIND,
    BUILD_KIND,
    GLOBAL,
    STRUCTURED_KINDS,
    WORKSPACE_LEASE,
    BuildResult,
    _Build,
    _execute,
    _Step,
    refuse_if_active,
    registration_order,
)
from autoontology.engine.client import BackendClient
from autoontology.engine.jobs import JobError, hold_sources
from autoontology.store.alignments import AlignmentStore
from autoontology.store.db import Source, Store
from autoontology.store.decisions import DecisionStore
from autoontology.store.documents import DocumentStore
from autoontology.structured.semantic import combine, fetch_snapshot

logger = logging.getLogger("autoontology.jobs")

APPLY_STEPS: tuple[str, ...] = ("decisions", "merge", "snapshot")


NOTHING_PENDING = "no pending decisions"


def pending_work(store: Store, model: Any | None = None) -> dict[str, Any]:
    """What the next apply would change: ``{decisions, reverted, removed, total}`` --
    decisions no snapshot reflects yet, alignment rows whose decision was removed (they get
    their judged verdict back) or whose synonyms are still to be taken off a Term
    (:func:`overlay.unfold_pending`), and decisions the latest snapshot reflects that are gone
    (an undone alias or acknowledgement). *model*: the latest snapshot's, when loaded."""
    from autoontology.ontology.model import MergedOntology

    current = DecisionStore(store).list()
    keys = {(d.kind, d.subject_key) for d in current}
    pending = {(d.kind, d.subject_key) for d in current if d.applied_revision is None}
    reverted = {
        ("alignment", row.concept_id)
        for row in AlignmentStore(store).review_rows()
        if (row.judged and ("alignment", row.concept_id) not in keys) or overlay.unfold_pending(row)
    }
    if model is None:
        snap = store.latest_snapshot()
        model = MergedOntology.from_dict(snap.model) if snap and snap.model else None
    removed = {
        (d.get("kind"), d.get("subject"))
        for d in (model.decisions if model is not None else [])
        if d.get("kind") in ("alignment", "alias", "concept")
        and (d.get("kind"), d.get("subject")) not in keys
    }
    return {
        "decisions": len(pending),
        "reverted": len(reverted),
        "removed": len(removed - reverted),
        "total": len(pending | reverted | removed),
    }


def run_apply(
    store: Store,
    settings: Settings,
    *,
    log: Callable[[str], None] | None = None,
    client: BackendClient | None = None,
    writer: Any = None,
    force: bool = False,
) -> BuildResult:
    """Apply the review decisions and save a new snapshot (module docstring). With nothing
    pending (:func:`pending_work`) merge and snapshot are skipped and no revision is written,
    unless *force*."""
    log_path = jobs.setup_job_logging(settings)
    registered = registration_order(store)
    if not registered:
        raise JobError("no sources: nothing to apply", log_path=log_path)
    if store.latest_revision() is None:
        raise JobError(
            "no ontology snapshot yet: run `uv run autoontology build` first", log_path=log_path
        )
    names = [WORKSPACE_LEASE, *(s.name for s in registered)]
    with hold_sources(store, names, APPLY_KIND, log_path=log_path):
        registered = jobs.verify_sources(store, registered, log_path=log_path)
        refuse_if_active(store, log_path=log_path)
        client = client or BackendClient(settings.backend_url)
        if any(s.kind in STRUCTURED_KINDS for s in registered):
            client.health()  # BackendUnavailable -> "run autoontology up"
        work = pending_work(store)
        run = store.create_run(
            kind=APPLY_KIND,
            source_id=None,
            params={
                "pid": os.getpid(),
                "pending_decisions": work["decisions"],
                "pending": work,
                "force": force,
            },
        )
        logger.info(
            "apply run %s: %d pending decision(s), %d removed",
            run.id,
            work["decisions"],
            work["reverted"] + work["removed"],
        )
        idle = not work["total"] and not force
        skip = {"merge": NOTHING_PENDING, "snapshot": NOTHING_PENDING} if idle else {}
        build = _Build(
            settings=settings,
            store=store,
            run=run,
            sources=registered,
            client=client,
            resume=False,
            extract_backends=None,
            align_backends=None,
            writer=writer or align_job.UpstreamTermWriter(settings, client),
        )
        plan = [
            _Step("decisions", GLOBAL, lambda: _step_decisions(build)),
            _Step("merge", GLOBAL, build.merge),
            _Step("snapshot", GLOBAL, build.snapshot),
        ]
        result = _execute(
            build, plan, already=set(), skip=skip, log=log or (lambda _l: None), log_path=log_path
        )
        snap = next(
            (s for s in store.get_steps(run.id) if s.name == "snapshot"),
            None,
        )
        if snap is not None and snap.status == "succeeded":
            result.revision = snap.detail.get("revision")
            result.counts = snap.detail.get("counts", {})
        elif idle:  # nothing to apply: the latest revision already reflects everything
            result.revision = store.latest_revision()
            logger.info("apply run %s: %s (revision %s)", run.id, NOTHING_PENDING, result.revision)
        store.update_run(
            run.id,
            status="succeeded",
            params={
                **run.params,
                "applied_revision": result.revision,
                "decisions_applied": (snap.detail.get("decisions_applied", 0) if snap else 0),
                "skipped": NOTHING_PENDING if idle else None,
            },
        )
        result.status = "succeeded"
        return result


def _min_conf(store: Store) -> float:
    previous = store.latest_workspace_run(BUILD_KIND)
    value = previous.params.get("min_conf") if previous is not None else None
    return float(value) if value is not None else align_job.al.min_conf_from_env()


def _step_decisions(build: _Build) -> dict[str, Any]:
    store, client = build.store, build.client
    decisions = DecisionStore(store).by_subject("alignment")
    aligns, docs = AlignmentStore(store), DocumentStore(store)
    structured = build.built_structured()
    snap = combine([fetch_snapshot(client, s) for s in structured]) if structured else None
    terms = snap.term_map() if snap is not None else {}
    out: dict[str, Any] = {
        "decisions": len(decisions),
        "written": 0,
        "restored": 0,
        "unchanged": 0,
        "term_missing": [],
        "synonyms_added": 0,
        "synonyms_removed": 0,
    }
    seen: set[str] = set()
    for source in build.documents:
        source = build.fresh(source)
        rows = {a.concept_id: a for a in aligns.list(source.id)}
        same = False
        for concept in docs.list_concepts(source.id):
            decision, row = decisions.get(concept.id), rows.get(concept.id)
            if decision is None:
                if overlay.is_decided(row):
                    assert row is not None
                    back = overlay.restore(aligns, row)
                    same = same or bool(back and back.accepted and back.relation == "same")
                    out["restored"] += 1
                continue
            seen.add(concept.id)
            term_id = str(decision.payload.get("term_id") or "")
            if decision.decision == "accept" and term_id not in terms:
                out["term_missing"].append(concept.name)
                logger.warning("  %s: the Term of the decision is gone: left as is", concept.name)
                continue
            if decision.decision == "accept" and decision.relation == "same":
                same = True
            if overlay.reflects(row, decision):
                out["unchanged"] += 1
                continue
            aligns.put(
                overlay.decided_record(
                    row,
                    decision,
                    source_id=source.id,
                    concept_id=concept.id,
                    concept_name=concept.name,
                    run_id=row.run_id if row is not None else build.run.id,
                    term_name=terms[term_id].name if decision.decision == "accept" else "",
                    structured_source=snap.source_of(term_id)
                    if snap is not None and decision.decision == "accept"
                    else "",
                )
            )
            out["written"] += 1
            logger.info(
                "  %s: %s %s",
                concept.name,
                decision.relation,
                terms[term_id].name if decision.decision == "accept" else "-",
            )
        unfold = any(overlay.unfold_pending(a) for a in aligns.list(source.id))
        if (same or unfold) and structured:
            added, removed = _apply_synonyms(build, source, structured)
            out["synonyms_added"] += added
            out["synonyms_removed"] += removed
    out["orphaned"] = len(set(decisions) - seen)
    return out


def _apply_synonyms(build: _Build, source: Source, structured: list[Source]) -> tuple[int, int]:
    """The align ``apply`` step over *source* (no model call): ``(added, removed)``
    synonyms -- a person's accepted ``same`` adds the concept's names to the Term, a
    rejected, removed or moved fold takes them off again (:func:`overlay.unfold_pending`)."""
    ctx = align_job._AlignCtx(
        settings=build.settings,
        store=build.store,
        docs=DocumentStore(build.store),
        aligns=AlignmentStore(build.store),
        source=source,
        structured=structured,
        run=build.run,
        client=build.client,
        backends=align_job.AlignBackends(build.settings),  # never called
        writer=build.writer,
        dry_run=False,
        min_sim=0.0,
        min_conf=_min_conf(build.store),
        top_k=align_job.al.DEFAULT_TOP_K,
    )
    detail = align_job._step_apply(ctx)
    added = sum(
        len(p.get("synonyms_added") or []) for p in detail["patches"] if p["action"] == "applied"
    )
    return added, int(detail.get("synonyms_removed") or 0)
