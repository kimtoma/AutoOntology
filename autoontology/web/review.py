# SPDX-License-Identifier: MIT
"""The review inbox (``/api/review``): what a person should look at, and their decisions.

Categories, in this order:

* ``proposals`` (``al:<concept id>``): a ``same`` verdict the identity policy refused
  (stored as ``related`` with the refused verdict in ``proposal``);
* ``axioms`` (``ax:<concept id>``): a ``related``/``narrower``/``broader`` verdict the
  judge gave with confidence >= :data:`AXIOM_MIN_CONFIDENCE` -- never accepted by the
  judge alone;
* ``unaligned`` (``un:<concept id>``): document classes of the latest snapshot joined to
  no Term (concepts new since the previous revision first);
* ``aliases`` (``alias:<term id>|<alias>``): names of folded concepts the alias policy
  kept off a Term (instance-like names, column names);
* ``stale_sources`` (``src:<source name>``): sources whose inputs changed since the last
  build (``web/freshness.py``); the action is a build, not a decision.

Item ids are stable across calls. A decided item keeps its id; once the decision is
applied and the item is no longer current (an accepted alias left ``rejected_aliases``),
it is rebuilt from the copy the decision stored (``payload.item``).
"""

from __future__ import annotations

from typing import Any

from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import Store
from autoontology.store.decisions import ACCEPT_RELATIONS, Decision, DecisionStore, alias_key
from autoontology.store.documents import DocumentStore

CATEGORIES: tuple[str, ...] = ("proposals", "axioms", "unaligned", "aliases", "stale_sources")
PREFIX_OF: dict[str, str] = {
    "proposals": "al",
    "axioms": "ax",
    "unaligned": "un",
    "aliases": "alias",
    "stale_sources": "src",
}
CATEGORY_OF: dict[str, str] = {v: k for k, v in PREFIX_OF.items()}
#: decision kind of each category (stale sources take no decision)
KIND_OF: dict[str, str] = {
    "proposals": "alignment",
    "axioms": "alignment",
    "unaligned": "concept",
    "aliases": "alias",
}
AXIOM_RELATIONS: tuple[str, ...] = ("related", "narrower", "broader")
AXIOM_MIN_CONFIDENCE = 0.6
EVIDENCE_PER_ITEM = 3
STATUSES: tuple[str, ...] = ("pending", "decided", "all")


class ReviewError(ValueError):
    def __init__(self, status: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail


def parse_item_id(item_id: str) -> tuple[str, str]:
    """``(category, subject)`` of an item id; :class:`ReviewError` when malformed."""
    prefix, sep, subject = item_id.partition(":")
    if not sep or prefix not in CATEGORY_OF or not subject or len(item_id) > 600:
        raise ReviewError(400, "invalid_item_id", f"invalid review item id {item_id[:80]!r}")
    if prefix == "alias" and "|" not in subject:
        raise ReviewError(400, "invalid_item_id", "an alias item id is alias:<term id>|<alias>")
    return CATEGORY_OF[prefix], subject


def _node(prefix: str, value: str) -> str:
    from autoontology.ontology.model import slug

    return f"{prefix}-{slug(value)}"


def _state(row: AlignmentRecord) -> dict[str, Any]:
    """The judge's verdict of *row* (the one a decision replaced, when decided)."""
    if row.judged and "relation" in row.judged:
        j = row.judged
        return {
            "relation": j.get("relation"),
            "term_id": j.get("term_id") or "",
            "term_name": j.get("term_name") or "",
            "confidence": j.get("confidence"),
            "rationale": j.get("rationale") or "",
            "accepted": bool(j.get("accepted")),
            "proposal": j.get("proposal") or {},
            "candidates": row.candidates,
        }
    return {
        "relation": row.relation,
        "term_id": row.term_id,
        "term_name": row.term_name,
        "confidence": row.confidence,
        "rationale": row.rationale,
        "accepted": row.accepted,
        "proposal": row.proposal or {},
        "candidates": row.candidates,
    }


class Inbox:
    """Every review item of the workspace now (one instance per request)."""

    def __init__(
        self,
        store: Store,
        model: Any | None,
        previous: Any | None = None,
        freshness: list[dict[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.model = model
        self.previous = previous
        self.freshness = freshness or []
        self.decisions = DecisionStore(store)
        self.items: dict[str, dict[str, Any]] = {}
        self._decided: dict[tuple[str, str], Decision] = {
            (d.kind, d.subject_key): d for d in self.decisions.list()
        }
        self._collect()

    # --- collection ------------------------------------------------------------------

    def _collect(self) -> None:
        docs, aligns = DocumentStore(self.store), AlignmentStore(self.store)
        self.concepts: dict[str, dict[str, Any]] = {}
        self.by_name: dict[tuple[str, str], str] = {}
        self.rows: dict[str, AlignmentRecord] = {}
        evidence: dict[str, list[dict[str, Any]]] = {}
        for source in self.store.list_sources():
            if source.kind != "documents":
                continue
            titles = {d.id: (d.title, d.path) for d in docs.list_documents(source.id)}
            for c in docs.list_concepts(source.id):
                self.concepts[c.id] = {
                    "id": c.id,
                    "node": _node("concept", c.id),
                    "name": c.name,
                    "kind": c.kind,
                    "source": source.name,
                    "definition": c.definition,
                }
                self.by_name[(source.name, c.name)] = c.id
            rows = sorted(
                (r for r in docs.evidence_rows(source.id) if r.get("concept_id")),
                key=lambda r: (not r["verified"], r["id"]),
            )
            for r in rows:
                bucket = evidence.setdefault(r["concept_id"], [])
                if len(bucket) < EVIDENCE_PER_ITEM:
                    from autoontology.unstructured.report import display_path

                    title, path = titles.get(r["doc_id"], ("", ""))
                    bucket.append(
                        {
                            "quote": r["quote"],
                            "document": title or display_path(path),
                            "path": display_path(path),
                            "page": r.get("page"),
                            "verified": bool(r["verified"]),
                        }
                    )
            for row in aligns.list(source.id):
                self.rows[row.concept_id] = row
        self.evidence = evidence
        for cid, row in self.rows.items():
            if cid not in self.concepts:
                continue
            state = _state(row)
            proposal = state["proposal"]
            if proposal.get("relation") == "same" and proposal.get("term_id"):
                self._add(
                    "proposals",
                    cid,
                    term=(proposal["term_id"], proposal.get("term_name") or ""),
                    relation="same",
                    confidence=proposal.get("confidence"),
                    rationale=state["rationale"],
                    reason=proposal.get("reason") or "",
                    suggested="accept",
                )
            elif (
                state["relation"] in AXIOM_RELATIONS
                and not state["accepted"]
                and (state["confidence"] or 0.0) >= AXIOM_MIN_CONFIDENCE
                and state["term_id"]
            ):
                self._add(
                    "axioms",
                    cid,
                    term=(state["term_id"], state["term_name"]),
                    relation=state["relation"],
                    confidence=state["confidence"],
                    rationale=state["rationale"],
                    suggested="accept",
                )
        if self.model is not None:
            known = (
                set(self.previous.classes) | set(self.previous.individuals)
                if self.previous is not None
                else None
            )
            for entry in self.model.unaligned:
                cid = self.by_name.get((entry.get("source"), entry.get("name")))
                if cid is None or ("alignment", cid) in self._decided:
                    continue
                if entry.get("relation") == "skipped":  # a kind that is never aligned
                    continue
                if f"al:{cid}" in self.items or f"ax:{cid}" in self.items:
                    continue
                row = self.rows.get(cid)
                self._add(
                    "unaligned",
                    cid,
                    term=(row.term_id, row.term_name) if row and row.term_id else None,
                    relation=entry.get("relation") or "unaligned",
                    confidence=row.confidence if row else None,
                    rationale=row.rationale if row else "",
                    suggested="acknowledge",
                    new=known is not None and self.concepts[cid]["node"] not in known,
                )
            for cls in self.model.classes.values():
                for r in cls.rejected_aliases:
                    alias = r.get("alias") or ""
                    if not alias or not cls.term_id:
                        continue
                    cid = self.by_name.get((cls.documents_source, r.get("concept") or ""))
                    self._add(
                        "aliases",
                        alias_key(cls.term_id, alias),
                        concept_id=cid,
                        term=(cls.term_id, cls.label),
                        relation="same",
                        reason=r.get("reason") or "",
                        alias=alias,
                        suggested="reject",
                    )
        for f in self.freshness:
            if f.get("stale"):
                self.items[f"src:{f['name']}"] = {
                    "id": f"src:{f['name']}",
                    "category": "stale_sources",
                    "concept": None,
                    "term": None,
                    "relation": None,
                    "confidence": None,
                    "rationale": "",
                    "evidence": [],
                    "suggested_action": "build",
                    "source": f,
                    "decision": None,
                }
        # decided items no longer current: from the copy their decision stored
        for (kind, subject), d in self._decided.items():
            item = d.payload.get("item")
            if not isinstance(item, dict) or not item.get("id") or item["id"] in self.items:
                continue
            if kind == "alignment" and (
                f"al:{subject}" in self.items or f"ax:{subject}" in self.items
            ):
                continue
            self.items[item["id"]] = {**item, "decision": d.to_dict()}

    def _add(
        self,
        category: str,
        subject: str,
        *,
        concept_id: str | None = None,
        term: tuple[str, str] | None,
        relation: str | None,
        confidence: float | None = None,
        rationale: str = "",
        reason: str = "",
        suggested: str,
        alias: str = "",
        new: bool | None = None,
    ) -> None:
        cid = subject if category != "aliases" else concept_id
        item: dict[str, Any] = {
            "id": f"{PREFIX_OF[category]}:{subject}",
            "category": category,
            "concept": self.concepts.get(cid) if cid else None,
            "term": {"id": term[0], "node": _node("term", term[0]), "name": term[1]}
            if term
            else None,
            "relation": relation,
            "confidence": confidence,
            "rationale": rationale,
            "evidence": self.evidence.get(cid, []) if cid else [],
            "suggested_action": suggested,
        }
        if reason:
            item["reason"] = reason
        if alias:
            item["alias"] = alias
        if new is not None:
            item["new"] = new
        if category in ("proposals", "axioms"):
            row = self.rows.get(subject)
            item["candidates"] = [
                {
                    "id": c.get("term_id"),
                    "name": c.get("term_name"),
                    "similarity": c.get("similarity"),
                }
                for c in (row.candidates if row else [])
            ]
        kind = KIND_OF[category]
        decision = self._decided.get((kind, subject))
        item["decision"] = decision.to_dict() if decision else None
        self.items[item["id"]] = item

    # --- queries ---------------------------------------------------------------------

    def listing(self, *, category: str | None, status: str, page: int, size: int) -> dict[str, Any]:
        items = list(self.items.values())
        pending = [i for i in items if i["decision"] is None]
        counts = {c: sum(i["category"] == c for i in pending) for c in CATEGORIES}
        if status == "pending":
            chosen = pending
        elif status == "decided":
            chosen = [i for i in items if i["decision"] is not None]
        else:
            chosen = items
        if category:
            chosen = [i for i in chosen if i["category"] == category]
        order = {c: n for n, c in enumerate(CATEGORIES)}
        chosen.sort(
            key=lambda i: (
                order[i["category"]],
                not i.get("new", False),
                -(i["confidence"] or 0.0),
                (i["concept"] or {}).get("name") or i.get("alias") or i["id"],
                i["id"],
            )
        )
        total = len(chosen)
        start = (page - 1) * size
        return {
            "counts": {**counts, "total": len(pending)},
            "unapplied": self.unapplied(),
            "status": status,
            "category": category,
            "page": page,
            "size": size,
            "total": total,
            "pages": max(1, -(-total // size)),
            "items": chosen[start : start + size],
        }

    def unapplied(self) -> int:
        """Decisions no snapshot reflects yet, plus rows whose decision was removed (an
        apply gives them their judged verdict back)."""
        pending = sum(d.applied_revision is None for d in self._decided.values())
        reverted = sum(
            1
            for cid, row in self.rows.items()
            if row.judged and ("alignment", cid) not in self._decided
        )
        return pending + reverted

    def item(self, item_id: str) -> dict[str, Any]:
        parse_item_id(item_id)
        item = self.items.get(item_id)
        if item is None:
            raise ReviewError(404, "unknown_item", f"no review item {item_id[:80]!r}")
        return item

    # --- decisions -------------------------------------------------------------------

    def decide(
        self,
        item_id: str,
        *,
        decision: str,
        relation: str | None,
        term_id: str | None,
        note: str,
        decided_by: str,
    ) -> Decision:
        category, subject = parse_item_id(item_id)
        if category == "stale_sources":
            raise ReviewError(
                400,
                "not_decidable",
                "a stale source is rebuilt, not decided: POST /api/runs "
                '{"kind": "build", "resume": true, "sources": [...]}',
            )
        item = self.item(item_id)
        kind = KIND_OF[category]
        allowed = {"alignment": ("accept", "reject"), "alias": ("accept", "reject")}.get(
            kind, ("acknowledge",)
        )
        if decision not in allowed:
            raise ReviewError(
                400, "invalid_decision", f"a {category} item takes {' or '.join(allowed)}"
            )
        snapshot = {k: v for k, v in item.items() if k != "decision"}
        payload: dict[str, Any] = {"category": category, "item": snapshot}
        if kind == "alignment":
            row = self.rows.get(subject)
            payload["source_id"] = row.source_id if row else ""
            payload["concept_name"] = (item["concept"] or {}).get("name", "")
            if decision == "accept":
                rel = relation or item["relation"] or "same"
                if rel not in ACCEPT_RELATIONS:
                    raise ReviewError(
                        400,
                        "invalid_relation",
                        f"relation must be one of {', '.join(ACCEPT_RELATIONS)}",
                    )
                term = item["term"] or {}
                allowed_terms = {term.get("id"): term.get("name") or ""}
                for c in item.get("candidates") or []:
                    allowed_terms.setdefault(c.get("id"), c.get("name") or "")
                tid = term_id or term.get("id")
                if not tid or tid not in allowed_terms:
                    raise ReviewError(
                        400, "invalid_term", "term_id must be the item's Term or a candidate"
                    )
                payload.update(
                    relation=rel,
                    term_id=tid,
                    term_name=allowed_terms[tid],
                    structured_source=row.structured_source if row and row.term_id == tid else "",
                    confidence=item["confidence"] if tid == term.get("id") else None,
                )
        elif relation or term_id:
            raise ReviewError(400, "invalid_request", "relation/term_id are for alignment items")
        if kind == "alias":
            payload.update(term_id=subject.partition("|")[0], alias=item.get("alias", ""))
        if kind == "concept":
            payload["concept_name"] = (item["concept"] or {}).get("name", "")
        return self.decisions.put(
            Decision(kind, subject, decision, payload, note=note, decided_by=decided_by)
        )

    def undo(self, item_id: str) -> bool:
        category, subject = parse_item_id(item_id)
        if category == "stale_sources":
            raise ReviewError(400, "not_decidable", "a stale source takes no decision")
        return self.decisions.delete(KIND_OF[category], subject)
