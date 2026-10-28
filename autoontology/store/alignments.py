"""Alignment tables (``autoontology align``): cached Term embeddings and one verdict row per
document concept. Shares :class:`autoontology.store.db.Store`'s connection helper.

Committed verdicts (``alignments``, what export folds) and ``align --dry-run`` previews
(``alignment_previews``, never accepted) are separate tables: a record's ``dry_run`` picks
the table, so a preview can never replace or un-accept a committed verdict."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

RELATIONS: tuple[str, ...] = ("same", "broader", "narrower", "related", "none", "skipped")


@dataclass
class AlignmentRecord:
    source_id: str
    concept_id: str
    concept_name: str
    relation: str
    run_id: str
    structured_source: str = ""
    term_id: str = ""
    term_name: str = ""
    similarity: float | None = None
    confidence: float | None = None
    rationale: str = ""
    accepted: bool = False
    dry_run: bool = False
    judge_key: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    applied: dict[str, Any] = field(default_factory=dict)
    #: a ``same`` verdict the identity policy refused, kept for review (and to recompute
    #: acceptance): {relation, term_id, term_name, confidence, policy, reason}
    proposal: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    id: int | None = None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _record(row: sqlite3.Row) -> AlignmentRecord:
    return AlignmentRecord(
        id=row["id"], source_id=row["source_id"], concept_id=row["concept_id"],
        concept_name=row["concept_name"], structured_source=row["structured_source"],
        term_id=row["term_id"], term_name=row["term_name"], relation=row["relation"],
        similarity=row["similarity"], confidence=row["confidence"], rationale=row["rationale"],
        accepted=bool(row["accepted"]), dry_run=bool(row["dry_run"]),
        judge_key=row["judge_key"],
        candidates=json.loads(row["candidates_json"] or "[]"),
        applied=json.loads(row["applied_json"] or "{}"),
        proposal=json.loads(row["proposal_json"] or "{}"), run_id=row["run_id"],
        created_at=row["created_at"])


class AlignmentStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _conn(self):
        return self.store._conn()

    # --- term embeddings ------------------------------------------------------------

    def get_term_embedding(self, term_id: str, text_sha256: str,
                           model: str) -> list[float] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT vector_json FROM term_embeddings WHERE term_id = ? AND "
                "text_sha256 = ? AND model = ?", (term_id, text_sha256, model)).fetchone()
        return json.loads(row[0]) if row else None

    def put_term_embedding(self, term_id: str, text_sha256: str, model: str,
                           vector: Sequence[float]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO term_embeddings (term_id, text_sha256, model, dim, vector_json, "
                "created_at) VALUES (?,?,?,?,?,?) ON CONFLICT (term_id, text_sha256, model) "
                "DO UPDATE SET vector_json = excluded.vector_json, dim = excluded.dim",
                (term_id, text_sha256, model, len(vector), json.dumps(list(vector)), now_iso()))

    def count_term_embeddings(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT count(*) FROM term_embeddings").fetchone()[0]

    # --- alignments -----------------------------------------------------------------

    @staticmethod
    def _table(preview: bool) -> str:
        return "alignment_previews" if preview else "alignments"

    def put(self, record: AlignmentRecord) -> AlignmentRecord:
        """Store the concept's verdict, replacing any earlier one (one row per concept) in
        the committed table, or in the previews when ``record.dry_run``."""
        if record.relation not in RELATIONS:
            raise ValueError(f"unknown alignment relation {record.relation!r}")
        if record.dry_run and record.accepted:
            raise ValueError("a dry-run verdict is never accepted")
        table = self._table(record.dry_run)
        ts = now_iso()
        with self._conn() as conn:
            conn.execute(f"DELETE FROM {table} WHERE concept_id = ?", (record.concept_id,))
            cur = conn.execute(
                f"INSERT INTO {table} (source_id, concept_id, concept_name, "
                "structured_source, term_id, term_name, relation, similarity, confidence, "
                "rationale, accepted, dry_run, judge_key, candidates_json, applied_json, "
                "proposal_json, run_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.source_id, record.concept_id, record.concept_name,
                 record.structured_source, record.term_id, record.term_name, record.relation,
                 record.similarity, record.confidence, record.rationale,
                 int(bool(record.accepted)), int(bool(record.dry_run)), record.judge_key,
                 _dumps(record.candidates),
                 _dumps({} if record.dry_run else record.applied), _dumps(record.proposal),
                 record.run_id, ts))
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?",
                               (cur.lastrowid,)).fetchone()
        return _record(row)

    def get(self, concept_id: str, *, preview: bool = False) -> AlignmentRecord | None:
        with self._conn() as conn:
            row = conn.execute(f"SELECT * FROM {self._table(preview)} WHERE concept_id = ?",
                               (concept_id,)).fetchone()
        return _record(row) if row else None

    def for_concept(self, concept_id: str) -> list[AlignmentRecord]:
        """Committed verdict first, then the dry-run preview (if any)."""
        with self._conn() as conn:
            rows = [*conn.execute("SELECT * FROM alignments WHERE concept_id = ? "
                                  "ORDER BY term_name", (concept_id,)).fetchall(),
                    *conn.execute("SELECT * FROM alignment_previews WHERE concept_id = ? "
                                  "ORDER BY term_name", (concept_id,)).fetchall()]
        return [_record(r) for r in rows]

    def list(self, source_id: str, *, preview: bool = False) -> list[AlignmentRecord]:
        """The committed verdicts of a source (``preview``: its dry-run previews)."""
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM {self._table(preview)} WHERE source_id = ? "
                                "ORDER BY concept_name", (source_id,)).fetchall()
        return [_record(r) for r in rows]

    def set_applied(self, alignment_id: int, applied: dict[str, Any]) -> None:
        """Record what apply wrote for a committed verdict."""
        with self._conn() as conn:
            conn.execute("UPDATE alignments SET applied_json = ? WHERE id = ?",
                         (_dumps(applied), alignment_id))

    # --- synonyms AutoOntology added to upstream Terms -------------------------------------

    def record_alias_additions(self, term_id: str, aliases: Sequence[str], *, run_id: str,
                               concepts: Sequence[str] = ()) -> None:
        """Remember synonyms align wrote to a Term: they never count as identity evidence
        (otherwise a wrong merge would validate itself on the next run). A re-addition (the
        alias was removed, then written again, possibly by another documents source)
        replaces the provenance with the current write's run and concepts, so `align
        repair` attributes the live alias to whoever added it last; the row itself is never
        dropped, so the alias stays untrusted."""
        with self._conn() as conn:
            for alias in aliases:
                conn.execute(
                    "INSERT INTO term_alias_additions (term_id, alias, run_id, concepts_json, "
                    "added_at) VALUES (?,?,?,?,?) ON CONFLICT (term_id, alias) DO UPDATE SET "
                    "run_id = excluded.run_id, concepts_json = excluded.concepts_json, "
                    "added_at = excluded.added_at, removed_at = NULL",
                    (term_id, alias, run_id, _dumps(list(concepts)), now_iso()))

    def mark_aliases_removed(self, term_id: str, aliases: Sequence[str]) -> None:
        """Repair: the aliases were taken off the Term (the record stays: still untrusted)."""
        with self._conn() as conn:
            for alias in aliases:
                conn.execute("UPDATE term_alias_additions SET removed_at = ? WHERE "
                             "term_id = ? AND alias = ?", (now_iso(), term_id, alias))

    def alias_additions(self, term_id: str | None = None) -> dict[str, list[str]]:
        """term id -> aliases AutoOntology added (removed ones included)."""
        sql = "SELECT term_id, alias FROM term_alias_additions"
        args: tuple[Any, ...] = ()
        if term_id is not None:
            sql += " WHERE term_id = ?"
            args = (term_id,)
        out: dict[str, list[str]] = {}
        with self._conn() as conn:
            for row in conn.execute(sql + " ORDER BY added_at, alias", args).fetchall():
                out.setdefault(row[0], []).append(row[1])
        return out

    def alias_provenance(self) -> dict[str, list[dict[str, Any]]]:
        """term id -> [{alias, source_id (the documents source whose align run added it;
        None when unknown), concepts}] from ``term_alias_additions`` + ``runs``."""
        out: dict[str, list[dict[str, Any]]] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT a.term_id, a.alias, a.concepts_json, r.source_id FROM "
                "term_alias_additions a LEFT JOIN runs r ON r.id = a.run_id "
                "ORDER BY a.added_at, a.alias").fetchall()
        for term_id, alias, concepts, source_id in rows:
            out.setdefault(term_id, []).append({"alias": alias, "source_id": source_id,
                                                "concepts": json.loads(concepts or "[]")})
        return out

    # --- pending Term embedding refreshes ---------------------------------------------

    def add_pending_refresh(self, term_id: str, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO term_refresh_pending (term_id, reason, since) VALUES "
                         "(?,?,?) ON CONFLICT (term_id) DO UPDATE SET reason = excluded.reason",
                         (term_id, reason, now_iso()))

    def clear_pending_refresh(self, term_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM term_refresh_pending WHERE term_id = ?", (term_id,))

    def pending_refreshes(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT term_id FROM term_refresh_pending ORDER BY since, term_id").fetchall()]
