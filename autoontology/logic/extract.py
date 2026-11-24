# SPDX-License-Identifier: MIT
"""The ``logic`` run: propose function / action / workflow definitions from the rules and
procedures of a project's documents, for a person to review (inbox category ``logic``).

Steps (one ``runs`` row of kind ``logic``, steps ``collect`` / ``extract`` / ``validate``
/ ``store``):

* **collect** -- the document concepts of kind ``rule`` and ``process`` (their
  definitions and up to :data:`EVIDENCE_PER_CONCEPT` evidence quotes, verified first),
  grouped by the document most of their evidence comes from; the ``metric`` /
  ``attribute`` concepts whose names those texts mention (name + definition only); the
  active object types (labels, columns with value types, links, operational properties)
  and the existing definitions. At most :data:`MAX_CONCEPTS_PER_RUN` rule/process concepts
  per run (most evidence first; the rest are counted in ``skipped_concepts``), batched per
  document into calls of at most :data:`MAX_CONCEPTS_PER_CALL` concepts and
  :data:`MAX_CALL_CHARS` characters of payload.
* **extract** -- one chat-model call per batch (JSON mode,
  :func:`~autoontology.unstructured.extractor.invoke_json` with the validation error fed
  back, prompt :data:`~autoontology.unstructured.prompts.LOGIC_PROMPT_VERSION`). A call
  that still fails is counted (``failed_calls``) and the run goes on; the run fails only
  when every call failed. Each candidate is checked on its own and dropped, with a counted
  reason, when it is not a mapping, names an unknown kind, has no usable name, a spec that
  is not a mapping or too long, or a spec whose ``kind`` disagrees (fail-closed: one bad
  candidate never fails the run). Evidence quotes are verified against the chunk text of
  the batch's document (:func:`~autoontology.unstructured.schemas.evidence_verified`); a
  quote that does not verify is dropped. A candidate left without a verified quote is kept
  with ``evidence_verified`` false and its confidence multiplied by
  :data:`UNVERIFIED_CONFIDENCE_FACTOR`.
* **validate** -- duplicates within the run (same kind and name) keep the more confident
  one; every candidate is parsed and validated against the project's definitions plus the
  run's other candidates (:func:`autoontology.logic.registry.check_many`), so an extracted
  workflow that runs an extracted action validates together. The errors are stored:
  none means "ready", some (a column the data lacks, say) means "needs edit".
* **store** -- dedupe by (kind, name) and the hash of the normalized spec: a candidate
  equal to a non-retired definition is skipped (``already_defined``); one equal to an
  earlier candidate is skipped (``duplicate``; an undecided earlier one gets the new
  errors); otherwise undecided earlier candidates of the same kind and name are replaced.
  Decided candidates are never touched.

Nothing here writes a definition: accepting a candidate in the review inbox saves it as a
draft (:mod:`autoontology.logic.candidates`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from autoontology.config import Settings
from autoontology.logic import registry, specs
from autoontology.store.db import ProjectStore, Store, root_store, scoped
from autoontology.store.logic_candidates import LogicCandidate

logger = logging.getLogger("autoontology.logic.extract")

LOGIC_KIND = "logic"
STEPS: tuple[str, ...] = ("collect", "extract", "validate", "store")
RULE_KINDS: tuple[str, ...] = ("rule", "process")
RELATED_KINDS: tuple[str, ...] = ("metric", "attribute")
MAX_CONCEPTS_PER_RUN = 80
MAX_CONCEPTS_PER_CALL = 12
MAX_CALL_CHARS = 16_000
MAX_RELATED_PER_CALL = 20
MAX_CANDIDATES_PER_RUN = 120
MAX_DEFINITIONS_IN_PROMPT = 150
EVIDENCE_PER_CONCEPT = 3
EVIDENCE_PER_CANDIDATE = 3
MAX_DEFINITION_CHARS = 600
MAX_QUOTE_CHARS = 300
MAX_RATIONALE_CHARS = 500
DEFAULT_CONFIDENCE = 0.5
UNVERIFIED_CONFIDENCE_FACTOR = 0.5
LLM_MAX_TOKENS = 8192
#: why a candidate was dropped (run params ``dropped``)
DROP_REASONS: tuple[str, ...] = (
    "malformed",
    "unknown_kind",
    "kind_mismatch",
    "invalid_name",
    "too_large",
    "over_limit",
)


class LogicJobError(Exception):
    """Raised before the run row exists (wrapped as a :class:`JobError` by the job)."""


@dataclass
class LogicResult:
    run_id: str
    status: str = "running"
    counts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class _Concept:
    id: str
    name: str
    kind: str
    definition: str
    evidence: list[dict[str, Any]]
    weight: tuple[int, int]


@dataclass
class _Batch:
    document: str  # title (or path) of the document
    path: str
    source_id: str
    concepts: list[_Concept]
    related: list[dict[str, str]]
    texts: list[str]  # the document's chunk texts (evidence verification)


@dataclass
class _Candidate:
    kind: str
    name: str
    spec: dict[str, Any]
    evidence: list[dict[str, Any]]
    evidence_verified: bool
    confidence: float
    rationale: str
    document: str
    source_id: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    spec_hash: str = ""


def make_backend(settings: Settings) -> Any:
    """The configured chat model (the extraction's reasoning client); tests replace this."""
    from autoontology.unstructured.extractor import upstream_backend

    return upstream_backend(settings, role="reasoning", max_tokens=LLM_MAX_TOKENS)


def spec_hash(spec: Any) -> str:
    """The hash of a spec as normalized (parsed and dumped when it fits the schema)."""
    try:
        body: Any = specs.dump(specs.parse_spec(spec))
    except specs.SpecError:
        body = spec
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- collect ------------------------------------------------------------------------------


def _clip(text: Any, size: int) -> str:
    from autoontology.unstructured.schemas import collapse_ws

    value = collapse_ws(str(text or ""))
    return value if len(value) <= size else value[: size - 1] + "…"


def schema_payload(view: Any) -> list[dict[str, Any]]:
    """The active object types as the prompt shows them."""
    ctx = registry.context(view)
    out = []
    for t in ctx.schema.types.values():
        if t.status != "active":
            continue
        info = ctx.type_of(t.name) or t
        labels = {label: column for label, column in info.labels.items()}
        links = []
        for lk in info.links:
            target = ctx.schema.types.get(lk.target)
            links.append(
                {
                    "name": lk.label or lk.name,
                    "target": target.label if target else lk.target,
                    "direction": lk.direction,
                    **({"column": lk.column} if lk.direction == "out" and lk.column else {}),
                }
            )
        out.append(
            {
                "label": info.label,
                "key": info.key,
                "columns": dict(info.columns),
                **({"labels": labels} if labels else {}),
                **({"operational": dict(info.operational)} if info.operational else {}),
                "links": links,
            }
        )
    return out


def definitions_payload(view: Any) -> list[dict[str, Any]]:
    """The non-retired definitions as the prompt shows them (names, what they work on)."""
    out = []
    for d in view.logic_defs().list():
        if d.status == "retired":
            continue
        entry: dict[str, Any] = {
            "kind": d.kind,
            "name": d.name,
            "label": d.spec.get("label") or "",
            "object_type": d.to_dict()["object_type"],
            "status": d.status,
        }
        if d.kind == "function":
            entry["returns"] = d.spec.get("returns")
        if d.spec.get("params"):
            entry["params"] = [
                {"name": p.get("name"), "type": p.get("type")} for p in d.spec["params"]
            ]
        out.append(entry)
        if len(out) >= MAX_DEFINITIONS_IN_PROMPT:
            break
    return out


def collect(view: Any) -> tuple[list[_Batch], dict[str, int]]:
    """The batches of one run (module docstring) and the counts of what was left out."""
    from autoontology.store.documents import DocumentStore
    from autoontology.unstructured.report import display_path

    docs = DocumentStore(view)
    rule_concepts: list[tuple[str, _Concept, str]] = []  # (source id, concept, doc id)
    related: dict[str, list[dict[str, str]]] = {}  # source id -> metric/attribute concepts
    doc_rows: dict[str, Any] = {}
    no_evidence = 0
    for source in view.list_sources():
        if source.kind != "documents":
            continue
        for d in docs.list_documents(source.id):
            doc_rows[d.id] = d
        for c in docs.list_concepts(source.id):
            if c.kind in RELATED_KINDS:
                related.setdefault(source.id, []).append(
                    {
                        "name": c.name,
                        "kind": c.kind,
                        "definition": _clip(c.definition, 200),
                    }
                )
                continue
            if c.kind not in RULE_KINDS:
                continue
            rows = sorted(docs.concept_evidence(c.id), key=lambda r: (not r["verified"], r["id"]))
            if not rows:
                no_evidence += 1
                continue
            counts: dict[str, int] = {}
            for r in rows:
                counts[r["doc_id"]] = counts.get(r["doc_id"], 0) + 1
            doc_id = max(counts, key=lambda k: (counts[k], k == rows[0]["doc_id"]))
            evidence = [
                {"quote": _clip(r["quote"], MAX_QUOTE_CHARS), "verified": bool(r["verified"])}
                for r in rows
                if r["doc_id"] == doc_id
            ][:EVIDENCE_PER_CONCEPT]
            concept = _Concept(
                id=c.id,
                name=c.name,
                kind=c.kind,
                definition=_clip(c.definition, MAX_DEFINITION_CHARS),
                evidence=evidence,
                weight=(c.verified_count, c.evidence_count),
            )
            rule_concepts.append((source.id, concept, doc_id))
    rule_concepts.sort(key=lambda x: (-x[1].weight[0], -x[1].weight[1], x[1].name))
    kept, skipped = rule_concepts[:MAX_CONCEPTS_PER_RUN], rule_concepts[MAX_CONCEPTS_PER_RUN:]
    by_doc: dict[str, list[tuple[str, _Concept]]] = {}
    for source_id, concept, doc_id in kept:
        by_doc.setdefault(doc_id, []).append((source_id, concept))
    batches: list[_Batch] = []
    for doc_id, members in by_doc.items():
        d = doc_rows.get(doc_id)
        title = (d.title if d else "") or (display_path(d.path) if d else doc_id)
        path = display_path(d.path) if d else ""
        source_id = members[0][0]
        texts = [k.text for k in docs.chunks_for_document(doc_id)]
        current: list[_Concept] = []
        size = 0
        groups: list[list[_Concept]] = []
        for _, concept in members:
            cost = len(json.dumps(_concept_payload(concept), ensure_ascii=False))
            if current and (len(current) >= MAX_CONCEPTS_PER_CALL or size + cost > MAX_CALL_CHARS):
                groups.append(current)
                current, size = [], 0
            current.append(concept)
            size += cost
        if current:
            groups.append(current)
        for group in groups:
            text = " ".join(
                [c.definition for c in group] + [e["quote"] for c in group for e in c.evidence]
            )
            mentioned = [r for r in related.get(source_id, []) if r["name"] and r["name"] in text]
            batches.append(
                _Batch(
                    document=title,
                    path=path,
                    source_id=source_id,
                    concepts=group,
                    related=mentioned[:MAX_RELATED_PER_CALL],
                    texts=texts,
                )
            )
    return batches, {
        "concepts": len(rule_concepts),
        "skipped_concepts": len(skipped),
        "concepts_without_evidence": no_evidence,
    }


def _concept_payload(c: _Concept) -> dict[str, Any]:
    return {
        "name": c.name,
        "kind": c.kind,
        "definition": c.definition,
        "evidence": [e["quote"] for e in c.evidence],
    }


def batch_message(batch: _Batch, schema: list[dict[str, Any]], defs: list[dict[str, Any]]) -> str:
    from autoontology.unstructured import prompts

    return prompts.logic_message(
        {
            "object_types": schema,
            "definitions": defs,
            "document": {"title": batch.document, "path": batch.path},
            "concepts": [_concept_payload(c) for c in batch.concepts],
            "related": batch.related,
        }
    )


# --- parse one candidate ----------------------------------------------------------------


def parse_candidate(raw: Any, batch: _Batch) -> tuple[_Candidate | None, str]:
    """One model candidate checked on its own: ``(candidate, "")`` or ``(None, reason)``
    (a :data:`DROP_REASONS` entry). Never raises."""
    from autoontology.unstructured.schemas import collapse_ws, evidence_verified

    if not isinstance(raw, dict):
        return None, "malformed"
    kind = raw.get("kind")
    spec = raw.get("spec")
    if not isinstance(kind, str) or kind not in specs.KINDS:
        if isinstance(spec, dict) and spec.get("kind") in specs.KINDS and kind is None:
            kind = spec["kind"]
        else:
            return None, "unknown_kind"
    if not isinstance(spec, dict):
        return None, "malformed"
    spec = dict(spec)
    if spec.setdefault("kind", kind) != kind:
        return None, "kind_mismatch"
    name = spec.get("name")
    if not isinstance(name, str):
        return None, "invalid_name"
    name = "_".join(name.split())
    if not specs.NAME_RE.match(name) or name in specs.RESERVED_NAMES:
        return None, "invalid_name"
    spec["name"] = name
    try:
        text = json.dumps(spec, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None, "malformed"
    if len(text) > specs.MAX_SPEC_CHARS:
        return None, "too_large"
    try:
        confidence = float(raw.get("confidence", DEFAULT_CONFIDENCE))
    except (TypeError, ValueError):
        confidence = DEFAULT_CONFIDENCE
    if confidence != confidence:  # NaN
        confidence = DEFAULT_CONFIDENCE
    confidence = min(1.0, max(0.0, confidence))
    evidence: list[dict[str, Any]] = []
    quotes = raw.get("evidence")
    for e in quotes if isinstance(quotes, list) else []:
        if len(evidence) >= EVIDENCE_PER_CANDIDATE:
            break
        quote = collapse_ws(str(e.get("quote") or "")) if isinstance(e, dict) else ""
        if not quote or len(quote) > MAX_QUOTE_CHARS:
            continue
        if not any(evidence_verified(quote, t) for t in batch.texts):
            continue
        if any(x["quote"] == quote for x in evidence):
            continue
        evidence.append(
            {"document": batch.document, "path": batch.path, "quote": quote, "verified": True}
        )
    verified = bool(evidence)
    if not verified:
        confidence = round(confidence * UNVERIFIED_CONFIDENCE_FACTOR, 4)
    rationale = raw.get("rationale")
    return (
        _Candidate(
            kind=kind,
            name=name,
            spec=spec,
            evidence=evidence,
            evidence_verified=verified,
            confidence=confidence,
            rationale=_clip(rationale, MAX_RATIONALE_CHARS) if isinstance(rationale, str) else "",
            document=batch.document,
            source_id=batch.source_id,
        ),
        "",
    )


def unverified_quotes(raw: Any) -> int:
    quotes = raw.get("evidence") if isinstance(raw, dict) else None
    return len(quotes) if isinstance(quotes, list) else 0


# --- validate and store -----------------------------------------------------------------


def validate(view: Any, candidates: list[_Candidate]) -> None:
    """Errors (and the normalized spec, when it parses) of every candidate, checked
    together with the run's other candidates."""
    results = registry.check_many(view, [c.spec for c in candidates])
    for c, (parsed, errors) in zip(candidates, results, strict=True):
        if parsed is not None:
            c.spec = specs.dump(parsed)
        c.errors = errors
        c.spec_hash = spec_hash(c.spec)


def store_candidates(view: Any, candidates: list[_Candidate], run_id: str) -> dict[str, int]:
    """Dedupe and store (module docstring); the counts."""
    defs, table = view.logic_defs(), view.logic_candidates()
    decided = set(view.decisions().by_subject("logic"))
    counts = {"stored": 0, "ready": 0, "already_defined": 0, "duplicate": 0, "replaced": 0}
    for c in candidates:
        existing = defs.get(c.kind, c.name)
        if existing is not None and existing.status != "retired":
            if spec_hash(existing.spec) == c.spec_hash:
                counts["already_defined"] += 1
                continue
        earlier = table.list(c.kind, c.name)
        same = [p for p in earlier if p.spec_hash == c.spec_hash]
        if same:
            for p in same:
                if p.id not in decided:
                    table.set_errors(p.id, c.errors)
            counts["duplicate"] += 1
            continue
        counts["replaced"] += table.delete([p.id for p in earlier if p.id not in decided])
        table.add(
            LogicCandidate(
                kind=c.kind,
                name=c.name,
                spec=c.spec,
                errors=c.errors,
                evidence=c.evidence,
                confidence=c.confidence,
                rationale=c.rationale,
                spec_hash=c.spec_hash,
                evidence_verified=c.evidence_verified,
                document=c.document,
                source_id=c.source_id,
                run_id=run_id,
            )
        )
        counts["stored"] += 1
        counts["ready"] += not c.errors
    return counts


# --- the job ------------------------------------------------------------------------------


def _check_cancel(view: Any, step: str) -> None:
    """Between two calls: a cancel requested through the run manager stops the run."""
    cancelled = getattr(root_store(view), "_cancelled", None)
    if callable(cancelled) and cancelled():
        from autoontology.web.runs import RunCancelled

        raise RunCancelled(step)


def preflight(view: Any) -> None:
    """:class:`LogicJobError` when there is nothing to extract from or nothing to bind to."""
    ctx = registry.context(view)
    if not any(t.status == "active" for t in ctx.schema.types.values()):
        raise LogicJobError(
            "no active object types: accept object types in the review inbox (objects) first"
        )
    from autoontology.store.documents import DocumentStore

    docs = DocumentStore(view)
    for source in view.list_sources():
        if source.kind == "documents" and any(
            c.kind in RULE_KINDS for c in docs.list_concepts(source.id)
        ):
            return
    raise LogicJobError(
        "no rule or process concepts in the documents: add a documents source and build first"
    )


def run_logic(
    store: Store | ProjectStore,
    settings: Settings,
    *,
    backend: Any = None,
    project: str | None = None,
) -> LogicResult:
    """Run the ``logic`` job on the project (*store* a project view, or a plain store with
    *project*). Raises :class:`~autoontology.engine.jobs.JobError` before a run row exists
    when another run holds the project or there is nothing to extract."""
    from autoontology.engine import jobs
    from autoontology.engine.build import WORKSPACE_LEASE, refuse_if_active
    from autoontology.unstructured import prompts
    from autoontology.unstructured.extractor import invoke_json
    from autoontology.unstructured.schemas import MAX_LOGIC_CANDIDATES_PER_CALL, LogicExtraction

    log_path = jobs.setup_job_logging(settings)
    view = scoped(store, project)
    with (
        jobs.hold_engine(view, LOGIC_KIND, log_path=log_path),
        jobs.hold_sources(view, [WORKSPACE_LEASE], LOGIC_KIND, log_path=log_path),
    ):
        refuse_if_active(view, log_path=log_path)
        try:
            preflight(view)
        except LogicJobError as exc:
            raise jobs.JobError(str(exc), log_path=log_path) from exc
        run = view.create_run(
            kind=LOGIC_KIND,
            source_id=None,
            params={"pid": os.getpid(), "prompt_version": prompts.LOGIC_PROMPT_VERSION},
        )
        result = LogicResult(run.id)
        params: dict[str, Any] = dict(run.params)
        try:
            view.start_step(run.id, "collect")
            batches, collected = collect(view)
            schema, defs = schema_payload(view), definitions_payload(view)
            view.finish_step(
                run.id,
                "collect",
                status="succeeded",
                detail={
                    **collected,
                    "documents": len({b.document for b in batches}),
                    "calls": len(batches),
                    "object_types": len(schema),
                    "definitions": len(defs),
                },
            )
            logger.info(
                "logic run %s: %d rule/process concept(s) (%d skipped over the cap) in %d "
                "call(s) over %d object type(s)",
                run.id,
                collected["concepts"],
                collected["skipped_concepts"],
                len(batches),
                len(schema),
            )
            view.start_step(run.id, "extract")
            backend = backend or make_backend(settings)
            dropped = dict.fromkeys(DROP_REASONS, 0)
            candidates: list[_Candidate] = []
            calls: list[dict[str, Any]] = []
            failed_calls = dropped_evidence = raw_total = 0
            system = prompts.logic_json_system_prompt()
            for n, batch in enumerate(batches, 1):
                _check_cancel(view, "extract")
                human = batch_message(batch, schema, defs)
                entry: dict[str, Any] = {
                    "document": batch.document,
                    "concepts": len(batch.concepts),
                }
                try:
                    answer, attempts = invoke_json(backend, system, human, LogicExtraction)
                except Exception as exc:  # noqa: BLE001 - counted; the other calls go on
                    failed_calls += 1
                    entry["error"] = jobs.short_error(exc, 300)
                    calls.append(entry)
                    logger.warning(
                        "logic run %s: call %d/%d (%s) failed: %s",
                        run.id,
                        n,
                        len(batches),
                        batch.document,
                        entry["error"],
                    )
                    continue
                kept = 0
                for i, raw in enumerate(answer.candidates):
                    raw_total += 1
                    if i >= MAX_LOGIC_CANDIDATES_PER_CALL:
                        dropped["over_limit"] += 1
                        continue
                    cand, reason = parse_candidate(raw, batch)
                    if cand is None:
                        dropped[reason] += 1
                        continue
                    dropped_evidence += unverified_quotes(raw) - len(cand.evidence)
                    candidates.append(cand)
                    kept += 1
                entry.update(attempts=attempts, candidates=len(answer.candidates), kept=kept)
                calls.append(entry)
                logger.info(
                    "logic run %s: call %d/%d (%s): %d candidate(s), %d kept",
                    run.id,
                    n,
                    len(batches),
                    batch.document,
                    len(answer.candidates),
                    kept,
                )
            if batches and failed_calls == len(batches):
                raise RuntimeError(
                    f"every logic call failed ({failed_calls}); first: {calls[0].get('error')}"
                )
            extract_detail = {
                "calls": calls,
                "failed_calls": failed_calls,
                "raw_candidates": raw_total,
                "dropped": {k: v for k, v in dropped.items() if v},
                "dropped_evidence": dropped_evidence,
            }
            view.finish_step(run.id, "extract", status="succeeded", detail=extract_detail)
            if any(dropped.values()):
                logger.info(
                    "logic run %s: dropped %s",
                    run.id,
                    ", ".join(f"{v} {k}" for k, v in dropped.items() if v),
                )
            view.start_step(run.id, "validate")
            best: dict[tuple[str, str], _Candidate] = {}
            in_run_duplicates = 0
            for c in candidates:
                key = (c.kind, c.name)
                if key in best:
                    in_run_duplicates += 1
                    if c.confidence <= best[key].confidence:
                        continue
                best[key] = c
            unique = list(best.values())
            over = max(0, len(unique) - MAX_CANDIDATES_PER_RUN)
            unique = sorted(unique, key=lambda c: -c.confidence)[:MAX_CANDIDATES_PER_RUN]
            validate(view, unique)
            ready = sum(not c.errors for c in unique)
            view.finish_step(
                run.id,
                "validate",
                status="succeeded",
                detail={
                    "candidates": len(unique),
                    "ready": ready,
                    "needs_edit": len(unique) - ready,
                    "duplicates_in_run": in_run_duplicates,
                    "over_run_limit": over,
                },
            )
            view.start_step(run.id, "store")
            stored = store_candidates(view, unique, run.id)
            view.finish_step(run.id, "store", status="succeeded", detail=stored)
            logger.info(
                "logic run %s: %d candidate(s) stored (%d ready), %d already defined, "
                "%d duplicate(s), %d replaced",
                run.id,
                stored["stored"],
                stored["ready"],
                stored["already_defined"],
                stored["duplicate"],
                stored["replaced"],
            )
            result.counts = {
                **collected,
                "calls": len(batches),
                "failed_calls": failed_calls,
                "raw_candidates": raw_total,
                "dropped": {k: v for k, v in dropped.items() if v},
                "dropped_evidence": dropped_evidence,
                "candidates": len(unique),
                "needs_edit": len(unique) - ready,
                **stored,
            }
            params.update(counts=result.counts)
            view.update_run(run.id, status="succeeded", params=params)
            result.status = "succeeded"
            return result
        except Exception as exc:  # noqa: BLE001 - the run row holds the error
            for s in view.get_steps(run.id):
                if s.status == "running":
                    view.finish_step(
                        run.id, s.name, status="failed", detail={"error": jobs.short_error(exc)}
                    )
            view.update_run(run.id, status="failed", error=jobs.short_error(exc), params=params)
            logger.error("logic run %s failed: %s", run.id, jobs.short_error(exc))
            result.status, result.error = "failed", jobs.short_error(exc)
            return result
