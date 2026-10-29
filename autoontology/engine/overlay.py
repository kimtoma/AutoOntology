# SPDX-License-Identifier: MIT
"""Review decisions laid over the stored alignment verdicts (``store/decisions.py``).

A decided concept's ``alignments`` row carries the decision (relation, Term, ``accepted``)
instead of the judge's verdict, which moves into the row's ``judged`` field; the align
judge never runs for it again (``engine/align.py`` checks :func:`decided_record` first) and
``apply`` writes the same rows without a model call. Removing the decision restores the
judged verdict (or drops the row when the concept was never judged), so the next align
pass decides again.
"""

from __future__ import annotations

from typing import Any

from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.decisions import HUMAN_RATIONALE, Decision

#: keys of a row saved in ``judged`` (and restored from it)
JUDGED_KEYS: tuple[str, ...] = (
    "relation",
    "term_id",
    "term_name",
    "structured_source",
    "similarity",
    "confidence",
    "rationale",
    "accepted",
    "proposal",
    "judge_key",
    "applied",
)


def judged_state(row: AlignmentRecord | None) -> dict[str, Any]:
    """What ``judged`` holds for *row*: its own verdict (never a decision's), with the
    ``by_decision`` marker (so a decided row is told apart even with no judged verdict)."""
    if row is not None and row.judged:
        return dict(row.judged)
    if row is None:
        return {"by_decision": True}
    return {"by_decision": True, **{k: getattr(row, k) for k in JUDGED_KEYS}}


def is_decided(row: AlignmentRecord | None) -> bool:
    return row is not None and bool(row.judged)


def rationale(decision: Decision) -> str:
    return f"{HUMAN_RATIONALE}: {decision.note}" if decision.note else HUMAN_RATIONALE


def reflects(row: AlignmentRecord | None, decision: Decision) -> bool:
    """*row* already carries *decision* (nothing to write)."""
    if not is_decided(row) or row.relation != decision.relation:
        return False
    assert row is not None
    if decision.decision == "accept":
        return bool(row.accepted) and row.term_id == decision.payload.get("term_id")
    return not row.accepted and row.rationale == rationale(decision)


def decided_record(
    existing: AlignmentRecord | None,
    decision: Decision,
    *,
    source_id: str,
    concept_id: str,
    concept_name: str,
    run_id: str,
    term_name: str = "",
    structured_source: str = "",
) -> AlignmentRecord:
    """The row of *concept_id* under *decision* (``accept``: its relation to the
    payload's Term, accepted; ``reject``: ``none``, never accepted)."""
    p = decision.payload
    accept = decision.decision == "accept"
    term_id = str(p.get("term_id") or "") if accept else (existing.term_id if existing else "")
    same_term = existing is not None and existing.term_id == term_id
    confidence = p.get("confidence") if accept else None
    if confidence is None and existing is not None:
        confidence = existing.confidence
    return AlignmentRecord(
        source_id=source_id,
        concept_id=concept_id,
        concept_name=concept_name,
        relation=decision.relation,
        run_id=run_id,
        structured_source=structured_source
        or str(p.get("structured_source") or "")
        or (existing.structured_source if existing else ""),
        term_id=term_id,
        term_name=term_name
        or (str(p.get("term_name") or "") if accept else "")
        or (existing.term_name if existing and same_term else ""),
        similarity=existing.similarity if existing and same_term else None,
        confidence=confidence,
        rationale=rationale(decision),
        accepted=accept,
        dry_run=False,
        # never a judge cache hit: the judge did not say this
        judge_key="",
        candidates=existing.candidates if existing else [],
        applied=existing.applied
        if existing and same_term and accept and decision.relation == "same"
        else {},
        proposal={},
        judged=judged_state(existing),
    )


def restore(aligns: AlignmentStore, row: AlignmentRecord) -> AlignmentRecord | None:
    """Put the judged verdict of a decided *row* back (its decision was removed); drop the
    row when the concept had no verdict before the decision. Returns the restored row."""
    judged = row.judged
    if "relation" not in judged:
        aligns.delete(row.concept_id)
        return None
    keep_applied = (
        row.applied
        if judged.get("relation") == "same" and judged.get("term_id") == row.term_id
        else judged.get("applied") or {}
    )
    fields = {k: judged[k] for k in JUDGED_KEYS if k in judged}
    return aligns.put(
        AlignmentRecord(
            **{
                **row.__dict__,
                **fields,
                "id": None,
                "applied": keep_applied,
                "judged": {},
                "accepted": bool(judged.get("accepted")),
            }
        )
    )
