# SPDX-License-Identifier: MIT
import sqlite3
from dataclasses import dataclass

import pytest

from autoontology.store.db import MIGRATIONS, Store
from autoontology.store.documents import (
    ConceptRecord,
    DocumentStore,
    EvidenceRecord,
    RelationRecord,
)


@dataclass
class C:
    index: int
    text: str
    section_path: str = ""
    page: int | None = None
    kind: str = "text"
    tokens: int = 3


@pytest.fixture
def stores(tmp_path):
    store = Store(tmp_path / "autoontology.db")
    src = store.add_source(name="docs", kind="documents", locator="{}")
    return store, DocumentStore(store), src


def _add(docs, src, path="a.md", sha="s1", texts=("one", "two")):
    return docs.replace_document(
        source_id=src.id,
        path=path,
        title="A",
        mime="text/markdown",
        engine="native",
        sha256=sha,
        size=10,
        pages=None,
        chunks=[C(i, t) for i, t in enumerate(texts)],
    )


def test_migration_2_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
    for statement in MIGRATIONS[0][1].split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO schema_version VALUES (1, 'x')")
    conn.execute("INSERT INTO sources VALUES ('s', 'kept', 'files', '{}', NULL, NULL, 'x', 'x')")
    conn.commit()
    conn.close()
    store = Store(path)
    latest = MIGRATIONS[-1][0]
    assert store.schema_version() == latest and store.migrate() == latest
    assert store.get_source("kept") is not None
    tables = {
        r[0]
        for r in sqlite3.connect(path).execute("select name from sqlite_master where type='table'")
    }
    assert {
        "documents",
        "chunks",
        "concepts",
        "relations",
        "evidence",
        "alignments",
        "term_embeddings",
    } <= tables


def test_upsert_by_sha_and_replace_by_path(stores):
    _, docs, src = stores
    doc, replaced = _add(docs, src)
    assert replaced == 0 and docs.find_document(src.id, "s1") == doc
    assert [c.text for c in docs.chunks_for_document(doc.id)] == ["one", "two"]
    docs.touch_document(doc.id, "moved.md")  # unchanged content, new path
    assert docs.find_document(src.id, "s1").path == "moved.md"
    new, replaced = _add(docs, src, path="moved.md", sha="s2", texts=("changed",))
    assert replaced == 1 and docs.find_document(src.id, "s1") is None
    assert [d.id for d in docs.list_documents(src.id)] == [new.id]
    assert [c.text for c in docs.chunks_for_source(src.id)] == ["changed"]


def test_chunk_extraction_roundtrip(stores):
    _, docs, src = stores
    doc, _ = _add(docs, src)
    chunk = docs.chunks_for_document(doc.id)[0]
    assert chunk.extraction is None
    docs.set_chunk_extraction(chunk.id, {"concepts": [{"name": "계약"}]}, {"path": "structured"})
    got = docs.chunks_for_document(doc.id)[0]
    assert got.extraction == {"concepts": [{"name": "계약"}]} and got.extracted_at
    assert got.extract_detail == {"path": "structured"}
    docs.set_chunk_failure(chunk.id, {"error": "boom"})
    got = docs.chunks_for_document(doc.id)[0]
    assert got.extraction is None and got.extract_detail == {"error": "boom"}


def test_replace_ontology_and_cascades(stores):
    store, docs, src = stores
    doc, _ = _add(docs, src)
    chunk = docs.chunks_for_document(doc.id)[0]
    ev = [EvidenceRecord(chunk.id, "one", True)]
    counts = docs.replace_ontology(
        src.id,
        [
            ConceptRecord("계약", "entity", "d", evidence=ev),
            ConceptRecord(
                "고객",
                "entity",
                "d",
                synonyms=["거래처"],
                evidence=[EvidenceRecord(chunk.id, "zz", False)],
            ),
        ],
        [RelationRecord("계약", "belongs_to", "고객", "체결", evidence=ev)],
    )
    assert counts == {"concepts": 2, "relations": 1, "evidence": 3}
    concepts = {c.name: c for c in docs.list_concepts(src.id)}
    assert concepts["고객"].synonyms == ["거래처"] and concepts["고객"].verified_count == 0
    assert concepts["계약"].evidence_count == 1 and concepts["계약"].verified_count == 1
    [rel] = docs.list_relations(src.id)
    assert (rel.subject, rel.predicate, rel.object, rel.evidence_count) == (
        "계약",
        "belongs_to",
        "고객",
        1,
    )
    assert docs.concept_documents(src.id) == {"계약": {"a.md"}, "고객": {"a.md"}}
    # second replace wipes the first
    docs.replace_ontology(src.id, [ConceptRecord("청구서", "entity")], [])
    assert [c.name for c in docs.list_concepts(src.id)] == ["청구서"]
    assert docs.list_relations(src.id) == []
    with pytest.raises(ValueError, match="unknown concept"):
        docs.replace_ontology(
            src.id, [ConceptRecord("a", "entity")], [RelationRecord("a", "is_a", "b")]
        )
    assert [c.name for c in docs.list_concepts(src.id)] == ["청구서"]  # rolled back
    store.delete_source(src.id)
    assert docs.list_documents(src.id) == [] and docs.list_concepts(src.id) == []
