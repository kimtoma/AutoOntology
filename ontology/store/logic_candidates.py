# SPDX-License-Identifier: MIT
"""Logic candidates (schema v10 table ``logic_candidates``, v11 columns), per project:
function / action / workflow specs a ``logic`` run extracted from documents, waiting for
a person in the review inbox (category ``logic``, item ``lg:<id>``).

A candidate keeps its spec as the model proposed it (``spec``), what validation found
(``errors``: empty means ready to accept), the verified evidence quotes it rests on, the
model's confidence and rationale, and ``spec_hash`` -- the hash of the normalized spec a
later run dedupes against. Decisions on candidates live in ``review_decisions`` (kind
``logic``, subject the candidate id); this table never records them.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ontology.store.db import Store, now_iso


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class LogicCandidate:
    kind: str
    name: str
    spec: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    rationale: str = ""
    spec_hash: str = ""
    evidence_verified: bool = False
    document: str = ""
    source_id: str = ""
    run_id: str = ""
    created_at: str = ""
    id: str = ""
    project_id: str = ""

    @property
    def ready(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        spec = self.spec if isinstance(self.spec, dict) else {}
        for_each = spec.get("for_each")
        object_type = spec.get("object_type") or (
            for_each.get("object_type") if isinstance(for_each, dict) else ""
        )
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "label": str(spec.get("label") or ""),
            "object_type": str(object_type or ""),
            "spec": spec,
            "errors": self.errors,
            "ready": self.ready,
            "evidence": self.evidence,
            "evidence_verified": self.evidence_verified,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "document": self.document,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "spec_hash": self.spec_hash,
            "created_at": self.created_at,
        }


def _candidate(row: sqlite3.Row) -> LogicCandidate:
    return LogicCandidate(
        id=row["id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        name=row["name"],
        spec=_loads(row["spec_json"], {}),
        errors=_loads(row["errors_json"], []),
        evidence=_loads(row["evidence_json"], []),
        confidence=row["confidence"],
        source_id=row["source_id"],
        created_at=row["created_at"],
        rationale=row["rationale"],
        spec_hash=row["spec_hash"],
        evidence_verified=bool(row["evidence_verified"]),
        document=row["document"],
    )


class LogicCandidates:
    """The logic candidates of one project (``store.project(...).logic_candidates()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def get(self, candidate_id: str) -> LogicCandidate | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logic_candidates WHERE project_id = ? AND id = ?",
                (self.project_id, candidate_id),
            ).fetchone()
        return _candidate(row) if row else None

    def list(self, kind: str | None = None, name: str | None = None) -> list[LogicCandidate]:
        sql, args = "SELECT * FROM logic_candidates WHERE project_id = ?", [self.project_id]
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        if name is not None:
            sql += " AND name = ?"
            args.append(name)
        with self.store._conn() as conn:
            rows = conn.execute(sql + " ORDER BY created_at, id", args).fetchall()
        return [_candidate(r) for r in rows]

    def add(self, c: LogicCandidate) -> LogicCandidate:
        c.id = c.id or "lc_" + secrets.token_hex(6)
        c.created_at = c.created_at or now_iso()
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO logic_candidates (project_id, id, run_id, kind, name, spec_json, "
                "errors_json, evidence_json, confidence, source_id, created_at, rationale, "
                "spec_hash, evidence_verified, document) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    c.id,
                    c.run_id,
                    c.kind,
                    c.name,
                    _dumps(c.spec),
                    _dumps(c.errors),
                    _dumps(c.evidence),
                    c.confidence,
                    c.source_id,
                    c.created_at,
                    c.rationale,
                    c.spec_hash,
                    int(c.evidence_verified),
                    c.document,
                ),
            )
        c.project_id = self.project_id
        return c

    def set_errors(self, candidate_id: str, errors: list[dict[str, Any]]) -> None:
        """Record what the latest validation found (a later run re-validated it)."""
        with self.store._conn() as conn:
            conn.execute(
                "UPDATE logic_candidates SET errors_json = ? WHERE project_id = ? AND id = ?",
                (_dumps(errors), self.project_id, candidate_id),
            )

    def delete(self, candidate_ids: list[str]) -> int:
        if not candidate_ids:
            return 0
        with self.store._conn() as conn:
            cur = conn.execute(
                f"DELETE FROM logic_candidates WHERE project_id = ? AND id IN "
                f"({', '.join('?' * len(candidate_ids))})",
                (self.project_id, *candidate_ids),
            )
        return cur.rowcount
