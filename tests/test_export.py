import json

import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, PROV, RDF, RDFS, SKOS

from autoontology.export import graphml, rdf
from autoontology.export import report as export_report
from autoontology.ontology import model as merged
from autoontology.store.alignments import AlignmentRecord
from autoontology.store.documents import ConceptRow, DocumentRow, RelationRow
from autoontology.structured.semantic import SemanticFK, StructuredSnapshot
from autoontology.unstructured.align import TermInfo

ATTR = {"schema": "public", "table": "contracts"}


def _snapshot():
    contracts = TermInfo(
        "t-c",
        "Contracts",
        "계약 관련 정보",
        ["Contract"],
        attributes=[
            {
                "id": "a1",
                "name": "계약금액",
                "datatype": "bigint",
                "column": "계약금액",
                **ATTR,
                "description": "총 금액",
            },
            {"id": "a2", "name": "고객id", "datatype": "text", "column": "고객id", **ATTR},
        ],
        tables=[{"id": "tb-c", "name": "contracts", "schema": "public"}],
    )
    customers = TermInfo(
        "t-k",
        "Customers",
        "Business entity represented by table customers",
        attributes=[
            {
                "id": "a3",
                "name": "고객id",
                "datatype": "text",
                "column": "고객id",
                "schema": "public",
                "table": "customers",
            }
        ],
        tables=[{"id": "tb-k", "name": "customers", "schema": "public"}],
    )
    return StructuredSnapshot(
        "hanbit",
        "db1",
        "autoontology_src_hanbit",
        [contracts, customers],
        [SemanticFK("t-c", "t-k", "a3", "고객id", "public.contracts.고객id")],
    )


def _concept(cid, name, kind, definition="정의", name_en="", synonyms=()):
    return ConceptRow(cid, "src", name, name_en, kind, definition, list(synonyms), [], [], 1, 1)


def _documents():
    docs = [
        DocumentRow(
            "d1",
            "src",
            "samples/계약.md",
            "계약관리규정",
            "text/markdown",
            "native",
            "ab" * 32,
            10,
            None,
            "",
            "",
        )
    ]
    concepts = [
        _concept("c1", "계약", "entity", "고객과 맺는 약정", "Contract", ["계약서"]),
        _concept("c2", "계약금액", "metric", "계약의 총 금액"),
        _concept("c3", "신규 계약", "entity", "새로 맺는 계약"),
        _concept("c4", "계약상태", "attribute", "진행 단계"),
        _concept("c5", "승인", "process", "승인 절차"),
        _concept("c6", "승인 기준", "rule", "3억 이상은 대표이사 승인"),
        _concept("c7", "담당자", "entity", "업무 담당자"),
    ]
    relations = [
        RelationRow("r1", "src", "계약", "has_attribute", "계약상태", "계약의 상태", 1),
        RelationRow("r2", "src", "신규 계약", "is_a", "계약", "", 1),
        RelationRow("r3", "src", "승인", "governed_by", "승인 기준", "", 1),
        RelationRow("r4", "src", "담당자", "owns", "계약", "담당자가 계약을 관리", 1),
    ]
    evidence = [
        {
            "id": i,
            "concept_id": cid,
            "relation_id": None,
            "quote": f"근거 {cid}",
            "verified": 1,
            "chunk_id": "k",
            "page": 2,
            "section_path": "",
            "doc_id": "d1",
        }
        for i, cid in enumerate(["c1", "c2", "c3", "c4", "c5", "c6", "c7"], start=1)
    ]
    evidence += [
        {
            "id": 10 + i,
            "concept_id": None,
            "relation_id": rid,
            "quote": "관계 근거",
            "verified": 1,
            "chunk_id": "k",
            "page": None,
            "section_path": "",
            "doc_id": "d1",
        }
        for i, rid in enumerate(["r1", "r2", "r3", "r4"])
    ]
    aligns = [
        AlignmentRecord(
            "src",
            "c1",
            "계약",
            "same",
            "run",
            "hanbit",
            "t-c",
            "Contracts",
            0.8,
            0.92,
            "같음",
            accepted=True,
        ),
        AlignmentRecord(
            "src",
            "c2",
            "계약금액",
            "related",
            "run",
            "hanbit",
            "t-c",
            "Contracts",
            0.7,
            0.8,
            "속성",
        ),
        AlignmentRecord(
            "src",
            "c3",
            "신규 계약",
            "broader",
            "run",
            "hanbit",
            "t-c",
            "Contracts",
            0.6,
            0.9,
            "하위",
        ),
        AlignmentRecord(
            "src", "c7", "담당자", "same", "run", "hanbit", "t-k", "Customers", 0.56, 0.5, "낮음"
        ),
        AlignmentRecord("src", "c5", "승인", "skipped", "run"),
    ]
    return merged.DocumentsInput("hanbit-docs", docs, concepts, relations, evidence, aligns)


@pytest.fixture
def model():
    return merged.build("hanbit-docs", structured=[_snapshot()], documents=[_documents()])


def test_merged_model_structure(model):
    c = model.counts()
    # 2 Terms + 계약금액, 신규 계약, 계약상태, 담당자 (계약 is folded into Contracts)
    assert c["classes"] == 6 and c["classes_structured"] == 2 and c["merged_concepts"] == 1
    assert (
        c["relation_properties"],
        c["relation_datatype_properties"],
        c["column_properties"],
        c["semantic_fk_properties"],
    ) == (2, 1, 3, 1)
    assert c["by_source"]["hanbit"] == {
        "class:structured": 2,
        "datatype_property:structured": 3,
        "object_property:semantic_fk": 1,
    }
    assert c["by_source"]["hanbit-docs"]["object_property:document"] == 2
    contracts = model.classes["term-t-c"]
    assert contracts.alt_labels == ["Contract", "계약", "계약서"]
    assert contracts.definitions == ["고객과 맺는 약정"] and contracts.tables == [
        "public.contracts"
    ]
    assert model.fallback_terms == ["Customers"]
    assert "Metric" in model.classes["concept-c2"].extra_types
    assert {i.type for i in model.individuals.values()} == {"Process", "BusinessRule"}
    kinds = {(a.subject, a.kind, a.object, a.source) for a in model.axioms}
    assert ("concept-c3", "subclass_of", "term-t-c", "alignment") in kinds  # broader
    assert ("concept-c2", "related", "term-t-c", "alignment") in kinds
    assert ("concept-c3", "subclass_of", "term-t-c", "document") in kinds  # is_a
    has_attr = model.properties["rel-r1"]
    assert (has_attr.kind, has_attr.domain, has_attr.value_concept) == (
        "datatype",
        "term-t-c",
        "concept-c4",
    )
    gov = model.properties["rel-r3"]
    assert (gov.kind, gov.domain, gov.range) == ("object", "Process", "BusinessRule")
    fk = next(p for p in model.properties.values() if p.source == "semantic_fk")
    assert (fk.domain, fk.range, fk.label) == ("term-t-c", "term-t-k", "고객id refers to Customers")
    assert model.properties["attr-a1"].range == "xsd:integer"
    assert [e["name"] for e in model.low_confidence] == ["담당자"]
    assert {e["name"] for e in model.unaligned} == {"계약상태", "담당자"}


@pytest.mark.filterwarnings("ignore:ConjunctiveGraph is deprecated:DeprecationWarning")
def test_turtle_and_jsonld_roundtrip_with_korean_labels(model, tmp_path):
    paths = rdf.write(model, tmp_path)
    expected = merged.expected_class_count([_snapshot()], [_documents()])
    assert expected == 2 + 4
    check = rdf.roundtrip(paths["ttl"], model, expected_classes=expected)
    assert check["ok"], check
    assert check["classes"] == 2 + 4 and not check["properties_without_domain_or_range"]
    assert not rdf.roundtrip(paths["ttl"], model, expected_classes=7)["ok"]
    g = Graph().parse(str(paths["ttl"]), format="turtle")
    os_ = Namespace(model.base)
    assert model.base == "http://example.com/autoontology#"  # AUTOONTOLOGY_BASE_IRI default
    contracts = os_["term-t-c"]
    assert (contracts, SKOS.altLabel, Literal("계약", lang="ko")) in g
    assert (contracts, RDFS.label, Literal("Contracts", lang="en")) in g
    assert (contracts, SKOS.definition, Literal("고객과 맺는 약정", lang="ko")) in g
    assert (contracts, Namespace(model.base)["source"], Literal("structured")) in g
    assert set(g.objects(contracts, PROV.wasDerivedFrom)) == {os_["ev-1"]}
    assert (os_["concept-c3"], RDFS.subClassOf, contracts) in g
    assert (os_["concept-c2"], RDF.type, os_["Metric"]) in g
    assert (os_["concept-c6"], RDF.type, os_["BusinessRule"]) in g
    assert (os_["ev-1"], RDF.type, PROV.Entity) in g
    assert (
        os_["doc-d1"],
        Namespace("http://purl.org/dc/terms/")["identifier"],
        Literal("ab" * 32),
    ) in g
    assert (os_["rel-r4"], RDFS.subPropertyOf, os_["owns"]) in g
    assert (os_["rel-r4"], RDF.type, OWL.ObjectProperty) in g
    text = paths["ttl"].read_text(encoding="utf-8")
    assert "@prefix ao: <http://example.com/autoontology#>" in text
    assert "계약관리규정" in text  # not \u-escaped
    data = json.loads(paths["jsonld"].read_text(encoding="utf-8"))
    assert data["@context"]["ao"] == model.base
    assert len(Graph().parse(str(paths["jsonld"]), format="json-ld")) == len(g)


def test_roundtrip_detects_a_missing_range(model, tmp_path):
    model.properties["attr-a1"].range = "xsd:integer"
    paths = rdf.write(model, tmp_path)
    g = Graph().parse(str(paths["ttl"]), format="turtle")
    g.remove((Namespace(model.base)["attr-a1"], RDFS.range, None))
    g.serialize(destination=str(paths["ttl"]), format="turtle")
    check = rdf.roundtrip(paths["ttl"], model)
    assert not check["ok"] and check["properties_without_domain_or_range"]


def test_graphml_writer(model, tmp_path):
    path = graphml.write(model, tmp_path)
    nodes, edges = graphml.read(path)
    assert nodes["term-t-c"]["type"] == "class" and nodes["term-t-c"]["label"] == "Contracts"
    assert nodes["doc-d1"]["type"] == "document"
    assert nodes["concept-c1"]["type"] == "concept"
    types = {d["type"] for _, _, d in edges}
    assert {
        "subclass_of",
        "object_property",
        "datatype_property",
        "aligned_same",
        "related",
        "evidence",
    } <= types
    assert all({"type", "label", "source"} <= set(d) for d in nodes.values())
    # every edge endpoint is a node, and the file is plain GraphML (key/graph/node/edge)
    assert all(u in nodes and v in nodes for u, v, _ in edges)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and 'edgedefault="directed"' in text
    assert 'attr.name="type"' in text and 'for="edge"' in text


def test_graphml_parallel_edges_get_distinct_ids(tmp_path):
    g = graphml.Graph(name="x")
    g.add_node("a", type="class", label="A", source="structured")
    g.add_edge("a", "b", type="related", label="r1")
    g.add_edge("a", "b", type="related", label="r2")
    path = tmp_path / "g.graphml"
    graphml.to_xml(g).write(path, encoding="utf-8", xml_declaration=True)
    nodes, edges = graphml.read(path)
    assert set(nodes) == {"a", "b"} and nodes["b"] == {}
    assert [d["label"] for *_, d in edges] == ["r1", "r2"]
    assert path.read_text(encoding="utf-8").count('<edge source="a" target="b" id="') == 2


def test_report(model):
    text = export_report.render(
        model,
        run_id="r",
        files=[{"name": "ontology.ttl", "bytes": 10, "sha256": "f" * 64}],
        upstream=[{"name": "model.yaml", "origin": "copied"}],
    )
    assert "# Ontology report — hanbit-docs" in text
    assert "| Classes (owl:Class) | 6 |" in text and "Customers" in text
    assert "| 계약 | entity | Contracts | 0.92 |" in text
    assert "ontology.ttl" in text and "model.yaml: copied" in text
    assert "담당자→Customers (0.50)" in text


def test_report_reconciles_object_properties_with_the_turtle(model, tmp_path):
    c = model.counts()
    assert c["predicate_properties"] == 2  # owns, governed_by
    assert c["object_properties_total"] == c["object_properties"] + c["predicate_properties"]
    g = Graph().parse(str(rdf.write(model, tmp_path)["ttl"]), format="turtle")
    assert len(set(g.subjects(RDF.type, OWL.ObjectProperty))) == c["object_properties_total"]
    text = export_report.render(model, run_id="r", files=[], upstream=[])
    assert f"| owl:ObjectProperty total (Turtle) | {c['object_properties_total']} |" in text


def test_concept_without_evidence_warns_but_does_not_fail(tmp_path):
    docs = _documents()
    docs.evidence = [e for e in docs.evidence if e["concept_id"] != "c7"]
    m = merged.build("hanbit-docs", structured=[_snapshot()], documents=[docs])
    paths = rdf.write(m, tmp_path)
    check = rdf.roundtrip(
        paths["ttl"], m, expected_classes=merged.expected_class_count([_snapshot()], [docs])
    )
    assert check["ok"] and check["concepts_without_evidence"] == [m.base + "concept-c7"]
    assert m.without_evidence == ["담당자"]
    assert "Document concepts without evidence: 담당자" in export_report.render(
        m, run_id="r", files=[], upstream=[]
    )


def test_same_reasons_source_missing_and_dry_run():
    docs = _documents()
    docs.alignments = [
        AlignmentRecord(
            "src", "c1", "계약", "same", "run", "other", "t-x", "Orders", 0.8, 0.9, accepted=True
        ),
        AlignmentRecord(
            "src",
            "c7",
            "담당자",
            "same",
            "run",
            "hanbit",
            "t-k",
            "Customers",
            0.8,
            0.9,
            dry_run=True,
        ),
    ]
    m = merged.build("hanbit-docs", structured=[_snapshot()], documents=[docs])
    assert not m.merges  # neither is folded
    assert [e["name"] for e in m.source_missing] == ["계약"]
    assert [e["name"] for e in m.dry_run_same] == ["담당자"] and not m.low_confidence
    assert merged.expected_class_count([_snapshot()], [docs]) == 2 + 5
    text = export_report.render(m, run_id="r", files=[], upstream=[])
    assert (
        "the Term source is not in the alignment targets" in text and "계약→Orders (other)" in text
    )
    assert "not applied due to dry run" in text and "below the confidence threshold" not in text


def test_alignment_counts_sum_over_documents_sources():
    """Review #17: each documents source used to replace the counts of the previous one."""
    a, b = _documents(), _documents()
    a.alignments = [
        AlignmentRecord(
            "src", "c3", "신규 계약", "broader", "run", "hanbit", "t-c", "Contracts", 0.6, 0.9
        )
    ]
    b.source = "other-docs"
    b.alignments = [
        AlignmentRecord(
            "src", "c2", "계약금액", "related", "run", "hanbit", "t-c", "Contracts", 0.7, 0.8
        )
    ]
    m = merged.build("x", structured=[_snapshot()], documents=[a, b])
    assert m.alignment_counts["broader"] == 1 and m.alignment_counts["related"] == 1
    assert m.alignment_counts["unaligned"] == 12  # 6 + 6


def test_graph_keeps_relations_between_individuals_and_classes():
    """Review #13: `승인(process) governed_by 승인 기준(rule)` and a process -> entity relation
    must be edges in the graph view (the property's domain/range are only os:Process /
    os:BusinessRule, which are not nodes)."""
    from autoontology.store.documents import RelationRow
    from autoontology.web.graph import to_view

    docs = _documents()
    docs.relations = [
        *docs.relations,
        RelationRow("r5", "src", "승인", "approves", "계약", "승인이 계약을 확정", 1),
    ]
    m = merged.build("x", structured=[_snapshot()], documents=[docs])
    view = to_view(m)
    edges = {(e["source"], e["target"]) for e in view["edges"]}
    assert ("concept-c5", "concept-c6") in edges  # process -> rule (both individuals)
    assert ("concept-c5", "term-t-c") in edges  # process -> entity folded into a Term
    prop = m.properties["rel-r5"]
    assert prop.domain == "Process" and prop.subject_node == "concept-c5"


def test_graphml_drops_xml_invalid_control_characters(tmp_path):
    """Review M-3: document text can carry C0 controls that XML 1.0 forbids."""
    g = graphml.Graph(name="x\x0c")
    g.add_node("a\x00", type="class", label="Con\x01tract\x0b", source="document")
    g.add_edge("a\x00", "b", type="related", label="tab\tkept\x1f")
    path = tmp_path / "g.graphml"
    graphml.to_xml(g).write(path, encoding="utf-8", xml_declaration=True)
    nodes, edges = graphml.read(path)  # parses: the file is well-formed
    assert nodes["a"]["label"] == "Contract"
    assert edges == [("a", "b", {"type": "related", "label": "tab\tkept"})]
