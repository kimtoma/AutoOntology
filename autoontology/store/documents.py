"""Documents-pipeline tables (``autoontology extract``): documents, chunks, concepts,
relations, evidence. Shares :class:`autoontology.store.db.Store`'s connection helper.

A document is keyed by ``(source_id, sha256)``: re-running ``extract`` on unchanged
files finds the row and does nothing, a changed file (same path, new hash) replaces the
old row and its chunks. A chunk keeps its validated extraction (``extraction_json``), so
consolidation can always be recomputed from the chunks without calling the LLM again.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from autoontology.store.db import Store, now_iso


@dataclass(frozen=True)
class DocumentRow:
    id: str
    source_id: str
    path: str
    title: str
    mime: str
    engine: str
    sha256: str
    bytes: int
    pages: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ChunkRow:
    id: str
    doc_id: str
    idx: int
    section_path: str
    page: int | None
    kind: str
    text: str
    tokens: int
    extraction: dict[str, Any] | None
    extract_detail: dict[str, Any]
    extracted_at: str | None


class ChunkLike(Protocol):
    index: int
    section_path: str
    page: int | None
    kind: str
    text: str
    tokens: int


@dataclass(frozen=True)
class EvidenceRecord:
    chunk_id: str
    quote: str
    verified: bool


@dataclass
class ConceptRecord:
    name: str
    kind: str
    definition: str = ""
    name_en: str = ""
    synonyms: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    #: merge chain: [{"from", "into", "via": synonym|judge|name, ...}]
    merged_from: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)


@dataclass
class RelationRecord:
    subject: str  # concept name (as in ConceptRecord.name)
    predicate: str
    object: str
    label: str = ""
    evidence: list[EvidenceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ConceptRow:
    id: str
    source_id: str
    name: str
    name_en: str
    kind: str
    definition: str
    synonyms: list[str]
    attributes: list[str]
    merged_from: list[dict[str, Any]]
    evidence_count: int
    verified_count: int


@dataclass(frozen=True)
class RelationRow:
    id: str
    source_id: str
    subject: str
    predicate: str
    object: str
    label: str
    evidence_count: int


#: Namespace of the deterministic concept ids (uuid5 of "source:kind:casefolded name").
CONCEPT_ID_NAMESPACE = uuid.UUID("5b0b6f4e-0b1d-4c55-9a39-6f6e746f7370")


def concept_id(source_id: str, kind: str, name: str) -> str:
    return uuid.uuid5(CONCEPT_ID_NAMESPACE, f"{source_id}:{kind}:{name.casefold()}").hex


def _document(row: sqlite3.Row) -> DocumentRow:
    return DocumentRow(**{k: row[k] for k in row.keys()})


def _chunk(row: sqlite3.Row) -> ChunkRow:
    raw = row["extraction_json"]
    return ChunkRow(
        id=row["id"], doc_id=row["doc_id"], idx=row["idx"], section_path=row["section_path"],
        page=row["page"], kind=row["kind"], text=row["text"], tokens=row["tokens"],
        extraction=json.loads(raw) if raw else None,
        extract_detail=json.loads(row["extract_detail_json"] or "{}"),
        extracted_at=row["extracted_at"],
    )


def _concept_row(r: sqlite3.Row) -> ConceptRow:
    return ConceptRow(
        id=r["id"], source_id=r["source_id"], name=r["name"], name_en=r["name_en"],
        kind=r["kind"], definition=r["definition"],
        synonyms=json.loads(r["synonyms_json"]), attributes=json.loads(r["attributes_json"]),
        merged_from=json.loads(r["merged_from_json"]), evidence_count=r["n"],
        verified_count=r["v"])


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class DocumentStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _conn(self):
        return self.store._conn()

    # --- documents ----------------------------------------------------------------

    def find_document(self, source_id: str, sha256: str) -> DocumentRow | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE source_id = ? AND sha256 = ?",
                               (source_id, sha256)).fetchone()
        return _document(row) if row else None

    def touch_document(self, doc_id: str, path: str) -> None:
        """Unchanged content: record the (possibly new) path, nothing else."""
        with self._conn() as conn:
            conn.execute("UPDATE documents SET path = ?, updated_at = ? WHERE id = ? "
                         "AND path <> ?", (path, now_iso(), doc_id, path))

    def replace_document(
        self, *, source_id: str, path: str, title: str, mime: str, engine: str, sha256: str,
        size: int, pages: int | None, chunks: Sequence[ChunkLike],
    ) -> tuple[DocumentRow, int]:
        """Insert a document with its chunks, deleting any earlier document at *path*
        (and, through ON DELETE CASCADE, its chunks and their evidence). Returns the new
        row and how many earlier documents were replaced."""
        doc_id = uuid.uuid4().hex
        ts = now_iso()
        with self._conn() as conn:
            replaced = conn.execute(
                "DELETE FROM documents WHERE source_id = ? AND (path = ? OR sha256 = ?)",
                (source_id, path, sha256)).rowcount
            conn.execute(
                "INSERT INTO documents (id, source_id, path, title, mime, engine, sha256, "
                "bytes, pages, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, source_id, path, title, mime, engine, sha256, size, pages, ts, ts))
            conn.executemany(
                "INSERT INTO chunks (id, doc_id, idx, section_path, page, kind, text, tokens) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(uuid.uuid4().hex, doc_id, c.index, c.section_path, c.page, c.kind, c.text,
                  c.tokens) for c in chunks])
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _document(row), replaced

    def delete_documents(self, doc_ids: Sequence[str]) -> int:
        """Delete documents (chunks and their evidence go by ON DELETE CASCADE)."""
        if not doc_ids:
            return 0
        with self._conn() as conn:
            return conn.executemany("DELETE FROM documents WHERE id = ?",
                                    [(d,) for d in doc_ids]).rowcount

    def list_documents(self, source_id: str) -> list[DocumentRow]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM documents WHERE source_id = ? ORDER BY path",
                                (source_id,)).fetchall()
        return [_document(r) for r in rows]

    # --- chunks ---------------------------------------------------------------------

    def chunks_for_source(self, source_id: str) -> list[ChunkRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.* FROM chunks c JOIN documents d ON d.id = c.doc_id "
                "WHERE d.source_id = ? ORDER BY d.path, c.idx", (source_id,)).fetchall()
        return [_chunk(r) for r in rows]

    def chunks_for_document(self, doc_id: str) -> list[ChunkRow]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM chunks WHERE doc_id = ? ORDER BY idx",
                                (doc_id,)).fetchall()
        return [_chunk(r) for r in rows]

    def set_chunk_extraction(self, chunk_id: str, extraction: dict[str, Any],
                             detail: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE chunks SET extraction_json = ?, extract_detail_json = ?, "
                "extracted_at = ? WHERE id = ?",
                (_dumps(extraction), _dumps(detail), now_iso(), chunk_id))

    def set_chunk_failure(self, chunk_id: str, detail: dict[str, Any]) -> None:
        """Record a failed extraction (the chunk stays pending)."""
        with self._conn() as conn:
            conn.execute("UPDATE chunks SET extraction_json = NULL, extract_detail_json = ?, "
                         "extracted_at = NULL WHERE id = ?", (_dumps(detail), chunk_id))

    # --- ontology -------------------------------------------------------------------

    def ontology_identity(self, source_id: str) -> str:
        """sha256 of the materialised concepts and relations of a source: what a cached
        consolidation result must match before it is reused."""
        import hashlib

        with self._conn() as conn:
            concepts = conn.execute(
                "SELECT id, name, name_en, kind, definition, synonyms_json, attributes_json "
                "FROM concepts WHERE source_id = ? ORDER BY id", (source_id,)).fetchall()
            relations = conn.execute(
                "SELECT subject_concept_id, predicate, object_concept_id, label FROM relations "
                "WHERE source_id = ? ORDER BY subject_concept_id, predicate, "
                "object_concept_id", (source_id,)).fetchall()
        digest = hashlib.sha256()
        for row in [*concepts, *relations]:
            digest.update(json.dumps(list(row), ensure_ascii=False).encode())
            digest.update(b"\n")
        return digest.hexdigest()

    def replace_ontology(self, source_id: str, concepts: Iterable[ConceptRecord],
                         relations: Iterable[RelationRecord]) -> dict[str, int]:
        """Replace the source's concepts/relations/evidence in one transaction.

        Concept ids are deterministic (:func:`concept_id`): a re-consolidation that yields
        the same concept updates its row in place, so rows keyed on it (alignments) survive.
        Concepts that are gone are deleted (their alignments go by cascade); a concept whose
        canonical name or kind changed gets a new id."""
        ts = now_iso()
        counts = {"concepts": 0, "relations": 0, "evidence": 0}
        concepts = list(concepts)
        with self._conn() as conn:
            conn.execute("DELETE FROM relations WHERE source_id = ?", (source_id,))
            ids: dict[str, str] = {}
            for concept in concepts:
                cid = concept_id(source_id, concept.kind, concept.name)
                if cid in ids.values():  # names equal up to case: keep both, exact-name id
                    cid = uuid.uuid5(CONCEPT_ID_NAMESPACE,
                                     f"{source_id}:{concept.kind}:={concept.name}").hex
                ids[concept.name] = cid
            keep = set(ids.values())
            existing = {r[0] for r in conn.execute(
                "SELECT id FROM concepts WHERE source_id = ?", (source_id,)).fetchall()}
            conn.executemany("DELETE FROM concepts WHERE id = ?",
                             [(cid,) for cid in existing - keep])
            for concept in concepts:
                cid = ids[concept.name]
                values = (concept.name, concept.name_en, concept.kind, concept.definition,
                          _dumps(concept.synonyms), _dumps(concept.attributes),
                          _dumps(concept.merged_from))
                if cid in existing:
                    conn.execute(
                        "UPDATE concepts SET name = ?, name_en = ?, kind = ?, definition = ?, "
                        "synonyms_json = ?, attributes_json = ?, merged_from_json = ? "
                        "WHERE id = ?", (*values, cid))
                    conn.execute("DELETE FROM evidence WHERE concept_id = ?", (cid,))
                else:
                    conn.execute(
                        "INSERT INTO concepts (id, source_id, name, name_en, kind, definition, "
                        "synonyms_json, attributes_json, merged_from_json, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", (cid, source_id, *values, ts))
                counts["concepts"] += 1
                counts["evidence"] += self._insert_evidence(conn, concept.evidence,
                                                            concept_id=cid)
            for rel in relations:
                if rel.subject not in ids or rel.object not in ids:
                    raise ValueError(f"relation {rel.subject} -{rel.predicate}-> {rel.object} "
                                     "references an unknown concept")
                rid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO relations (id, source_id, subject_concept_id, predicate, "
                    "object_concept_id, label) VALUES (?,?,?,?,?,?)",
                    (rid, source_id, ids[rel.subject], rel.predicate, ids[rel.object],
                     rel.label))
                counts["relations"] += 1
                counts["evidence"] += self._insert_evidence(conn, rel.evidence,
                                                            relation_id=rid)
        return counts

    @staticmethod
    def _insert_evidence(conn: sqlite3.Connection, evidence: Iterable[EvidenceRecord], *,
                         concept_id: str | None = None, relation_id: str | None = None) -> int:
        rows = [(concept_id, relation_id, e.chunk_id, e.quote, int(bool(e.verified)))
                for e in evidence if e.quote.strip()]  # an empty quote is not evidence
        conn.executemany("INSERT INTO evidence (concept_id, relation_id, chunk_id, quote, "
                         "verified) VALUES (?,?,?,?,?)", rows)
        return len(rows)

    def list_concepts(self, source_id: str) -> list[ConceptRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.*, count(e.id) AS n, coalesce(sum(e.verified), 0) AS v "
                "FROM concepts c LEFT JOIN evidence e ON e.concept_id = c.id "
                "WHERE c.source_id = ? GROUP BY c.id ORDER BY n DESC, c.name",
                (source_id,)).fetchall()
        return [_concept_row(r) for r in rows]

    def search_concepts(self, source_ids: Sequence[str], *, q: str = "", kind: str = "",
                        limit: int = 50, offset: int = 0) -> tuple[list[ConceptRow], int]:
        """Concepts of *source_ids* whose name, English name, synonyms or definition
        contain *q* (case-insensitive LIKE), optionally of one *kind*; plus the total."""
        if not source_ids:
            return [], 0
        where = [f"c.source_id IN ({', '.join('?' for _ in source_ids)})"]
        args: list[Any] = list(source_ids)
        if kind:
            where.append("c.kind = ?")
            args.append(kind)
        if q.strip():
            pattern = "%" + _like_escape(q.strip().casefold()) + "%"
            where.append("(" + " OR ".join(
                f"lower({col}) LIKE ? ESCAPE '\\'" for col in
                ("c.name", "c.name_en", "c.synonyms_json", "c.definition")) + ")")
            args += [pattern] * 4
        sql_where = " AND ".join(where)
        with self._conn() as conn:
            total = conn.execute(f"SELECT count(*) FROM concepts c WHERE {sql_where}",
                                 args).fetchone()[0]
            rows = conn.execute(
                "SELECT c.*, count(e.id) AS n, coalesce(sum(e.verified), 0) AS v "
                f"FROM concepts c LEFT JOIN evidence e ON e.concept_id = c.id "
                f"WHERE {sql_where} GROUP BY c.id ORDER BY n DESC, c.name LIMIT ? OFFSET ?",
                [*args, limit, offset]).fetchall()
        return [_concept_row(r) for r in rows], int(total)

    def get_concept(self, concept_id: str) -> ConceptRow | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT c.*, count(e.id) AS n, coalesce(sum(e.verified), 0) AS v "
                "FROM concepts c LEFT JOIN evidence e ON e.concept_id = c.id "
                "WHERE c.id = ? GROUP BY c.id", (concept_id,)).fetchone()
        return _concept_row(row) if row else None

    def concept_evidence(self, concept_id: str) -> list[dict[str, Any]]:
        """Evidence quotes of one concept with document title/path, page and section."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT e.id, e.quote, e.verified, k.page, k.section_path, d.id AS doc_id, "
                "d.title, d.path FROM evidence e JOIN chunks k ON k.id = e.chunk_id "
                "JOIN documents d ON d.id = k.doc_id WHERE e.concept_id = ? "
                "ORDER BY d.path, k.idx, e.id", (concept_id,)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def concept_relations(self, concept_id: str) -> list[dict[str, Any]]:
        """Relations with *concept_id* as subject or object (names resolved)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT r.id, s.id AS subject_id, s.name AS subject, r.predicate, "
                "o.id AS object_id, o.name AS object, r.label FROM relations r "
                "JOIN concepts s ON s.id = r.subject_concept_id "
                "JOIN concepts o ON o.id = r.object_concept_id "
                "WHERE r.subject_concept_id = ? OR r.object_concept_id = ? "
                "ORDER BY s.name, r.predicate, o.name", (concept_id, concept_id)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

    def list_relations(self, source_id: str) -> list[RelationRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT r.id, r.source_id, s.name AS subject, r.predicate, o.name AS object, "
                "r.label, count(e.id) AS n FROM relations r "
                "JOIN concepts s ON s.id = r.subject_concept_id "
                "JOIN concepts o ON o.id = r.object_concept_id "
                "LEFT JOIN evidence e ON e.relation_id = r.id "
                "WHERE r.source_id = ? GROUP BY r.id ORDER BY s.name, r.predicate, o.name",
                (source_id,)).fetchall()
        return [RelationRow(id=r["id"], source_id=r["source_id"], subject=r["subject"],
                            predicate=r["predicate"], object=r["object"], label=r["label"],
                            evidence_count=r["n"]) for r in rows]

    def evidence_stats(self, source_id: str) -> dict[str, int]:
        """Evidence rows of the source's concepts and relations: total and verified."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*), coalesce(sum(e.verified), 0) FROM evidence e "
                "LEFT JOIN concepts c ON c.id = e.concept_id "
                "LEFT JOIN relations r ON r.id = e.relation_id "
                "WHERE c.source_id = ? OR r.source_id = ?", (source_id, source_id)).fetchone()
        return {"total": row[0], "verified": row[1]}

    def concept_documents(self, source_id: str) -> dict[str, set[str]]:
        """``{concept name: {document path, ...}}`` through the evidence rows."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.name, d.path FROM concepts c JOIN evidence e ON e.concept_id = c.id "
                "JOIN chunks k ON k.id = e.chunk_id JOIN documents d ON d.id = k.doc_id "
                "WHERE c.source_id = ?", (source_id,)).fetchall()
        out: dict[str, set[str]] = {}
        for name, path in rows:
            out.setdefault(name, set()).add(path)
        return out

    def evidence_rows(self, source_id: str) -> list[dict[str, Any]]:
        """Every evidence row of the source's concepts and relations, with its chunk's
        document and page (export provenance)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT e.id, e.concept_id, e.relation_id, e.quote, e.verified, e.chunk_id, "
                "k.page, k.section_path, k.doc_id FROM evidence e "
                "JOIN chunks k ON k.id = e.chunk_id JOIN documents d ON d.id = k.doc_id "
                "WHERE d.source_id = ? ORDER BY e.id", (source_id,)).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]
