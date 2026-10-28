import pytest

from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import Store
from autoontology.store.documents import (
    ConceptRecord,
    DocumentStore,
    EvidenceRecord,
    RelationRecord,
    concept_id,
)
from tests.test_documents_store import _add


@pytest.fixture
def stores(tmp_path):
    store = Store(tmp_path / "autoontology.db")
    src = store.add_source(name="docs", kind="documents", locator="{}")
    return store, DocumentStore(store), AlignmentStore(store), src


def _ontology(chunk_id, definition="d"):
    ev = [EvidenceRecord(chunk_id, "계약은 고객과 체결한다", True)]
    return ([ConceptRecord("계약", "entity", definition, evidence=ev),
             ConceptRecord("고객", "entity", "d", evidence=ev)],
            [RelationRecord("계약", "belongs_to", "고객", evidence=ev)])


def test_concept_ids_are_deterministic_and_alignments_survive_reconsolidation(stores):
    store, docs, aligns, src = stores
    doc, _ = _add(docs, src)
    chunk = docs.chunks_for_document(doc.id)[0]
    docs.replace_ontology(src.id, *_ontology(chunk.id))
    first = {c.name: c.id for c in docs.list_concepts(src.id)}
    assert first["계약"] == concept_id(src.id, "entity", "계약")
    aligns.put(AlignmentRecord(src.id, first["계약"], "계약", "same", "run1", term_id="t1",
                               term_name="Contracts", confidence=0.9, accepted=True))
    # same concepts again (new definition): ids kept, alignment row kept, evidence replaced
    counts = docs.replace_ontology(src.id, *_ontology(chunk.id, definition="new"))
    assert counts == {"concepts": 2, "relations": 1, "evidence": 3}
    again = {c.name: c for c in docs.list_concepts(src.id)}
    assert {k: v.id for k, v in again.items()} == first
    assert again["계약"].definition == "new" and again["계약"].evidence_count == 1
    [row] = aligns.list(src.id)
    assert (row.concept_id, row.term_id, row.relation, row.accepted) == (
        first["계약"], "t1", "same", True)
    # 계약 renamed away: its id changes, so the alignment goes by cascade
    docs.replace_ontology(src.id, [ConceptRecord("계약서", "entity"),
                                   ConceptRecord("고객", "entity")], [])
    assert aligns.list(src.id) == []
    assert {c.name: c.id for c in docs.list_concepts(src.id)}["고객"] == first["고객"]


def test_case_variants_get_distinct_ids(stores):
    _, docs, _, src = stores
    docs.replace_ontology(src.id, [ConceptRecord("PM", "entity"),
                                   ConceptRecord("pm", "entity")], [])
    assert len({c.id for c in docs.list_concepts(src.id)}) == 2


def test_alignment_put_replaces_and_embedding_cache(stores):
    store, docs, aligns, src = stores
    docs.replace_ontology(src.id, [ConceptRecord("계약", "entity")], [])
    cid = docs.list_concepts(src.id)[0].id
    aligns.put(AlignmentRecord(src.id, cid, "계약", "none", "r1"))
    row = aligns.put(AlignmentRecord(src.id, cid, "계약", "same", "r2", term_id="t",
                                     candidates=[{"term_id": "t", "similarity": 0.8}]))
    assert [r.relation for r in aligns.list(src.id)] == ["same"]
    aligns.set_applied(row.id, {"synonyms_added": ["계약"]})
    assert aligns.get(cid).applied == {"synonyms_added": ["계약"]}
    assert aligns.get(cid).candidates == [{"term_id": "t", "similarity": 0.8}]
    with pytest.raises(ValueError):
        aligns.put(AlignmentRecord(src.id, cid, "계약", "maybe", "r3"))
    assert aligns.get_term_embedding("t", "sha", "m") is None
    aligns.put_term_embedding("t", "sha", "m", [0.1, 0.2])
    aligns.put_term_embedding("t", "sha", "m", [0.3, 0.4])
    assert aligns.get_term_embedding("t", "sha", "m") == [0.3, 0.4]
    assert aligns.count_term_embeddings() == 1
    store.delete_source(src.id)
    assert aligns.list(src.id) == []


def test_evidence_rows_carry_document_and_page(stores):
    _, docs, _, src = stores
    doc, _ = _add(docs, src)
    chunk = docs.chunks_for_document(doc.id)[0]
    docs.replace_ontology(src.id, *_ontology(chunk.id))
    rows = docs.evidence_rows(src.id)
    assert len(rows) == 3 and {r["doc_id"] for r in rows} == {doc.id}
    assert sum(r["relation_id"] is not None for r in rows) == 1
