# SPDX-License-Identifier: MIT
"""The review decisions about one concept of the concept API (the UI's concept sheet shows
the decision state: accepted or rejected by a person, who and when)."""

from __future__ import annotations

from typing import Any

from autoontology.store.db import Store
from autoontology.store.decisions import DecisionStore
from autoontology.web.review import PREFIX_OF

#: the review item prefix of a decision kind when its payload names no category
_DEFAULT_PREFIX = {"alignment": "al", "alias": "alias", "concept": "un"}


def concept_decisions(store: Store, concept_id: str) -> list[dict[str, Any]]:
    """Decisions about the model local *concept_id*: a document concept's alignment and
    acknowledge decisions (``concept-<id>``), a Term's alias decisions (``term-<id>``)."""
    kind, _, key = concept_id.partition("-")
    decisions = DecisionStore(store)
    if kind == "concept":
        found = [d for k in ("alignment", "concept") if (d := decisions.get(k, key))]
    elif kind == "term":
        found = [d for d in decisions.list("alias") if d.subject_key.startswith(f"{key}|")]
    else:
        found = []
    out = []
    for d in found:
        payload = d.payload or {}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        prefix = PREFIX_OF.get(str(payload.get("category")), _DEFAULT_PREFIX[d.kind])
        term = item.get("term") if isinstance(item.get("term"), dict) else {}
        term_id = payload.get("term_id") or term.get("id")
        term_name = payload.get("term_name") or term.get("name") or ""
        out.append(
            {
                "kind": d.kind,
                "decision": d.decision,
                "relation": d.relation or None,
                "alias": d.subject_key.partition("|")[2] if d.kind == "alias" else None,
                "term": {"id": term_id, "name": term_name} if term_id else None,
                "note": d.note,
                "decided_by": d.decided_by,
                "decided_at": d.decided_at,
                "applied_revision": d.applied_revision,
                "item_id": item.get("id") or f"{prefix}:{d.subject_key}",
            }
        )
    return out
