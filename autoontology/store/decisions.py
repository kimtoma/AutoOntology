# SPDX-License-Identifier: MIT
"""Review decisions (schema v6, table ``review_decisions``): what a person decided about a
review item. One active decision per ``(kind, subject_key)``; a new one replaces it and
deleting it lets the align judge decide again.

Subjects:

* ``alignment`` -- ``subject_key`` = the document concept id. ``accept`` with a relation
  (``same`` folds the concept into the Term; ``related``/``narrower``/``broader`` become an
  accepted axiom) or ``reject`` (the concept stays unaligned). The payload carries the
  Term: ``{relation, term_id, term_name, structured_source, confidence, source_id,
  concept_name}``.
* ``alias`` -- ``subject_key`` = ``<term_id>|<alias>``: ``accept`` moves a rejected
  alias onto the Term's labels in the next snapshot; ``reject`` keeps it off.
* ``concept`` -- ``subject_key`` = the concept id: ``acknowledge`` an unaligned concept
  (it stays its own class; the review inbox stops listing it).

``applied_revision`` is the snapshot that first reflected the decision (an ``apply`` run,
or a build whose align pass applied it); NULL while pending.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

KINDS: tuple[str, ...] = ("alignment", "alias", "concept")
DECISIONS: tuple[str, ...] = ("accept", "reject", "acknowledge")
#: decisions each kind takes
ALLOWED: dict[str, tuple[str, ...]] = {
    "alignment": ("accept", "reject"),
    "alias": ("accept", "reject"),
    "concept": ("acknowledge",),
}
#: relations an accepted alignment may take
ACCEPT_RELATIONS: tuple[str, ...] = ("same", "related", "narrower", "broader")
#: rationale prefix of an alignment row a decision wrote
HUMAN_RATIONALE = "human decision"


@dataclass
class Decision:
    kind: str
    subject_key: str
    decision: str
    payload: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    decided_by: str = "local"
    decided_at: str = ""
    applied_revision: int | None = None
    id: int | None = None

    @property
    def relation(self) -> str:
        """The alignment relation the decision sets (``none`` for a rejection)."""
        if self.kind != "alignment":
            return ""
        return str(self.payload.get("relation") or "same") if self.decision == "accept" else "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject_key": self.subject_key,
            "decision": self.decision,
            "relation": self.relation or None,
            "payload": self.payload,
            "note": self.note,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "applied_revision": self.applied_revision,
        }


def alias_key(term_id: str, alias: str) -> str:
    return f"{term_id}|{alias}"


def _decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"],
        kind=row["kind"],
        subject_key=row["subject_key"],
        decision=row["decision"],
        payload=json.loads(row["payload_json"] or "{}"),
        note=row["note"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        applied_revision=row["applied_revision"],
    )


class DecisionStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _conn(self):
        return self.store._conn()

    def put(self, decision: Decision) -> Decision:
        """Record *decision*, replacing the subject's earlier one (pending again)."""
        if decision.kind not in KINDS:
            raise ValueError(f"unknown decision kind {decision.kind!r}")
        if decision.decision not in ALLOWED[decision.kind]:
            raise ValueError(f"a {decision.kind} decision cannot be {decision.decision!r}")
        if decision.kind == "alignment" and decision.decision == "accept":
            if decision.payload.get("relation") not in ACCEPT_RELATIONS:
                raise ValueError(f"accept needs a relation in {', '.join(ACCEPT_RELATIONS)}")
            if not decision.payload.get("term_id"):
                raise ValueError("accept needs a Term")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO review_decisions (kind, subject_key, decision, payload_json, note, "
                "decided_by, decided_at, applied_revision) VALUES (?,?,?,?,?,?,?,NULL) "
                "ON CONFLICT (kind, subject_key) DO UPDATE SET decision = excluded.decision, "
                "payload_json = excluded.payload_json, note = excluded.note, "
                "decided_by = excluded.decided_by, decided_at = excluded.decided_at, "
                "applied_revision = NULL",
                (
                    decision.kind,
                    decision.subject_key,
                    decision.decision,
                    json.dumps(decision.payload, ensure_ascii=False, default=str),
                    decision.note,
                    decision.decided_by or "local",
                    decision.decided_at or now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM review_decisions WHERE kind = ? AND subject_key = ?",
                (decision.kind, decision.subject_key),
            ).fetchone()
        return _decision(row)

    def get(self, kind: str, subject_key: str) -> Decision | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM review_decisions WHERE kind = ? AND subject_key = ?",
                (kind, subject_key),
            ).fetchone()
        return _decision(row) if row else None

    def delete(self, kind: str, subject_key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM review_decisions WHERE kind = ? AND subject_key = ?",
                (kind, subject_key),
            )
        return cur.rowcount > 0

    def list(self, kind: str | None = None, *, pending: bool | None = None) -> list[Decision]:
        """Every decision (of *kind*); *pending* True/False: only unapplied/applied ones."""
        sql = "SELECT * FROM review_decisions WHERE 1 = 1"
        args: list[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        if pending is True:
            sql += " AND applied_revision IS NULL"
        elif pending is False:
            sql += " AND applied_revision IS NOT NULL"
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY id", args).fetchall()
        return [_decision(r) for r in rows]

    def by_subject(self, kind: str) -> dict[str, Decision]:
        return {d.subject_key: d for d in self.list(kind)}

    def mark_applied(self, decisions: list[Decision], revision: int) -> int:
        """Record *revision* on *decisions* still pending as they were read (a decision
        replaced meanwhile stays pending); returns how many were marked."""
        with self._conn() as conn:
            marked = 0
            for d in decisions:
                cur = conn.execute(
                    "UPDATE review_decisions SET applied_revision = ? "
                    "WHERE id = ? AND decided_at = ? AND applied_revision IS NULL",
                    (revision, d.id, d.decided_at),
                )
                marked += cur.rowcount
        return marked
