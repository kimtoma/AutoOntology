"""The Apache Ossie and OKF exporters (``export/ossie.py``, ``export/okf.py``) over a
fixture snapshot: Korean names, a folded ``same``, a ``narrower``, an attached attribute, an
unattached metric, a compound key, declared and semantic FKs."""

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from autoontology.config import REPO_ROOT
from autoontology.export import okf, ossie
from autoontology.ontology import model as merged
from autoontology.store.alignments import AlignmentRecord
from autoontology.store.documents import ConceptRow, DocumentRow, RelationRow
from autoontology.structured.semantic import SemanticFK, StructuredSnapshot
from autoontology.unstructured.align import TermInfo

CREATED = "2026-10-28T11:11:30.616+00:00"


def _attr(aid, name, table, datatype="text", description=""):
    return {
        "id": aid,
        "name": name,
        "datatype": datatype,
        "column": name,
        "schema": "public",
        "table": table,
        "description": description,
    }


def _term(tid, name, table, attrs, synonyms=()):
    return TermInfo(
        tid,
        name,
        f"{table} 테이블",
        list(synonyms),
        attributes=attrs,
        tables=[{"id": f"tb-{table}", "name": table, "schema": "public"}],
    )


def structured():
    contracts = _term(
        "t-c",
        "Contracts",
        "contracts",
        [
            _attr("a1", "계약id", "contracts", description="계약 식별자"),
            _attr("a2", "계약금액", "contracts", "bigint"),
            _attr("a3", "시작일", "contracts", "date"),
        ],
        ["Contract"],
    )
    customers = _term(
        "t-k",
        "Customers",
        "customers",
        [_attr("a4", "고객id", "customers"), _attr("a5", "지역", "customers")],
    )
    invoices = _term(
        "t-i",
        "Invoices",
        "invoices",
        [
            _attr("a6", "청구id", "invoices"),
            _attr("a7", "순번", "invoices", "integer"),
            _attr("a8", "청구금액", "invoices", "numeric"),
        ],
    )
    return StructuredSnapshot(
        "hanbit",
        "db1",
        "autoontology_src_hanbit",
        [contracts, customers, invoices],
        [SemanticFK("t-c", "t-k", "a4", "고객id", "public.contracts.고객id")],
    )


def _concept(cid, name, kind, definition="", name_en="", synonyms=()):
    return ConceptRow(cid, "src", name, name_en, kind, definition, list(synonyms), [], [], 1, 1)


def _align(cid, name, relation, term_id, term, confidence, accepted=False):
    return AlignmentRecord(
        "src",
        cid,
        name,
        relation,
        "run",
        "hanbit",
        term_id,
        term,
        0.8,
        confidence,
        "근거",
        accepted=accepted,
    )


def documents():
    docs = [
        DocumentRow(
            "d1",
            "src",
            "samples/계약 규정.md",
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
        _concept("c2", "계약 상태", "attribute", "진행 단계"),
        _concept("c3", "연체율", "metric", "연체 금액의 비율"),
        _concept("c4", "거래처", "entity", "거래 상대방"),
        _concept("c5", "신규 계약", "entity", "새로 맺는 계약"),
        _concept("c6", "승인", "process", "승인 절차"),
        _concept("c7", "승인 기준", "rule", "3억 이상은 대표이사 승인"),
        _concept("c8", "String", "entity", "이름이 내장 타입과 같은 개념"),
        _concept("c9", "담당자", "entity", "업무 담당자"),
        _concept("c10", "index", "entity", "예약 파일명과 같은 개념"),
    ]
    relations = [
        RelationRow("r1", "src", "계약", "has_attribute", "계약 상태", "계약의 상태", 1),
        RelationRow("r2", "src", "신규 계약", "is_a", "계약", "", 1),
        RelationRow("r3", "src", "승인", "governed_by", "승인 기준", "", 1),
        RelationRow("r4", "src", "담당자", "owns", "계약", "담당자가 계약을 관리", 1),
        RelationRow("r5", "src", "담당자", "owns", "승인", "담당자가 승인을 맡는다", 1),
    ]
    evidence = [
        {
            "id": i,
            "concept_id": cid,
            "relation_id": None,
            "quote": f"근거 [{cid}] | 인용",
            "verified": 1,
            "chunk_id": "k",
            "page": 2,
            "section_path": "",
            "doc_id": "d1",
        }
        for i, cid in enumerate(["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c9"], start=1)
    ]
    aligns = [
        _align("c1", "계약", "same", "t-c", "Contracts", 0.95, accepted=True),
        _align("c4", "거래처", "narrower", "t-k", "Customers", 0.9, accepted=True),
        _align("c3", "연체율", "related", "t-i", "Invoices", 0.7),
        _align("c9", "담당자", "same", "t-k", "Customers", 0.5),  # below threshold: draft
    ]
    return merged.DocumentsInput("hanbit-docs", docs, concepts, relations, evidence, aligns)


def _dataset(name, keys, columns, *, primary=None):
    item = {
        "name": name,
        "source": f"autoontology_src_hanbit.public.{name}",
        "fields": [
            {
                "name": c,
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": c}]},
                "datatype": "String",
            }
            for c in columns
        ],
    }
    if keys:
        item["unique_keys"] = keys
    if primary:
        item["primary_key"] = primary
    return item


def semantic_model(name="autoontology_src_hanbit", extra=()):
    return {
        "version": "0.2.0.dev0",
        "name": name,
        "datasets": [
            _dataset("contracts", [["계약id"]], ["계약id", "계약금액", "시작일", "고객id"]),
            _dataset("customers", [["고객id"]], ["고객id", "지역"]),
            _dataset("invoices", [["청구id", "순번"]], ["청구id", "순번", "청구금액", "계약id"]),
            *extra,
        ],
        "relationships": [
            {
                "name": "invoices_to_contracts",
                "from": "invoices",
                "to": "contracts",
                "from_columns": ["계약id"],
                "to_columns": ["계약id"],
            }
        ],
        "custom_extensions": [{"vendor_name": "NVIDIA_AUTO_ONTOLOGY", "data": "{}"}],
    }


def write_zip(path: Path, doc: dict) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("semantic_model.yaml", yaml.safe_dump(doc, allow_unicode=True))
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def model():
    return merged.build("hanbit", structured=[structured()], documents=[documents()])


@pytest.fixture
def bundle(model, tmp_path):
    zipped = write_zip(tmp_path / "hanbit.ossie.zip", semantic_model())
    out = tmp_path / "r0001"
    result = ossie.write(model, out, revision=1, created_at=CREATED, upstream=[("hanbit", zipped)])
    okf_result = okf.write(model, out, revision=1, created_at=CREATED)
    return out, result, okf_result


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _concepts(onto):
    return {c["concept"]: c for c in onto["ontology"]}


# --- vendored schemas -------------------------------------------------------------------


def test_vendored_schemas_match_the_licence_folder():
    for name in ("ontology.json", "mapping.json", "ossie-schema.json", "LICENSE", "NOTICE"):
        packaged = (ossie.SCHEMA_DIR / name).read_bytes()
        assert packaged == (REPO_ROOT / "third_party" / "ossie" / name).read_bytes(), name
    readme = (REPO_ROOT / "third_party/ossie/README.md").read_text(encoding="utf-8")
    assert "89e2e17f8249e68f30df7b29ac8852fa9dd24501" in readme and "unmodified" in readme


def test_schema_errors_name_the_json_pointer():
    errors = ossie.schema_errors(
        "ontology",
        {
            "version": "0.2.0.dev0",
            "name": "x",
            "ontology": [{"concept": "A", "type": "Thing"}],
        },
    )
    assert errors and errors[0].startswith("/ontology/0/type:")
    sm = ossie.schema_errors("semantic_model", {"version": "0.2.0.dev0", "name": "x"})
    assert sm == ["/: 'datasets' is a required property"]


# --- ossie --------------------------------------------------------------------------------


def test_ossie_documents_validate_and_round_trip(bundle):
    out, result, _ = bundle
    folder = out / "ossie"
    record = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
    assert record == result["validation"]
    for key in ("semantic_model", "ontology", "mapping", "roundtrip"):
        assert record[key] == "ok", (key, record[key])
    for kind, name in (
        ("semantic_model", "semantic_model.yaml"),
        ("ontology", "ontology.yaml"),
        ("mapping", "mapping.yaml"),
    ):
        assert ossie.schema_errors(kind, _load(folder / name)) == []
    from ossie_nvidia_auto_ontology import convert_ossie_to_auto_ontology

    native = yaml.safe_load(
        convert_ossie_to_auto_ontology((folder / "semantic_model.yaml").read_text("utf-8"))
    )
    assert isinstance(native, dict) and "data_layer" in native


def test_semantic_model_merges_the_upstream_datasets(bundle):
    out, _, _ = bundle
    sm = _load(out / "ossie/semantic_model.yaml")
    assert sm["version"] == "0.2.0.dev0" and sm["name"] == "hanbit"
    assert [d["name"] for d in sm["datasets"]] == ["contracts", "customers", "invoices"]
    assert sm["relationships"][0]["from"] == "invoices"
    # one source: its model-level extension is kept
    assert sm["custom_extensions"] == [{"vendor_name": "NVIDIA_AUTO_ONTOLOGY", "data": "{}"}]


def test_dataset_name_collisions_are_prefixed_with_the_source():
    erp = {
        "version": "0.2.0.dev0",
        "name": "erp",
        "datasets": [_dataset("customers", [["id"]], ["id"]), _dataset("orders", [], ["id"])],
        "relationships": [
            {
                "name": "invoices_to_contracts",
                "from": "orders",
                "to": "customers",
                "from_columns": ["id"],
                "to_columns": ["id"],
            }
        ],
        "metrics": [
            {
                "name": "n",
                "expression": {
                    "dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(customers.id)"}]
                },
            }
        ],
        "custom_extensions": [{"vendor_name": "NVIDIA_AUTO_ONTOLOGY", "data": "{}"}],
    }
    warnings: list[str] = []
    doc, datasets = ossie.merge_semantic_models(
        "ws", [("hanbit", semantic_model()), ("erp-x", erp)], warnings
    )
    names = [d["name"] for d in doc["datasets"]]
    assert names == [
        "contracts",
        "hanbit_customers",
        "invoices",
        "erp_x_customers",
        "orders",
    ]
    rels = {r["name"]: r for r in doc["relationships"]}
    assert set(rels) == {"hanbit_invoices_to_contracts", "erp_x_invoices_to_contracts"}
    assert rels["erp_x_invoices_to_contracts"]["to"] == "erp_x_customers"
    expr = doc["metrics"][0]["expression"]["dialects"][0]["expression"]
    assert expr == "COUNT(erp_x_customers.id)"
    assert "custom_extensions" not in doc  # per-source native documents are dropped
    assert any("dropped" in w for w in warnings) and any("renamed" in w for w in warnings)
    assert {d.name: d.table for d in datasets}["erp_x_customers"] == "public.customers"
    assert ossie.schema_errors("semantic_model", doc) == []


def test_ontology_mapping_rules(bundle):
    out, result, _ = bundle
    onto = _load(out / "ossie/ontology.yaml")
    assert onto["name"] == "hanbit_ontology"
    assert onto["prefixes"] == {"ao": "http://example.com/autoontology#"}
    ctx = onto["ai_context"]
    assert ctx["revision"] == 1 and ctx["created_at"] == CREATED
    assert ctx["sources"] == ["hanbit", "hanbit-docs"] and ctx["counts"]["evidence"] == 8
    comps = _concepts(onto)
    assert ctx["counts"]["concepts"] == len(comps)
    # names: unique identifiers, Hangul kept, the built-in name avoided
    assert len(comps) == len(onto["ontology"]) and all(n.isidentifier() for n in comps)
    assert "String_2" in comps and "신규계약" in comps and "승인기준" in comps
    assert all(c["type"] == "EntityType" for c in comps.values())
    # same: 계약 folded into Contracts (no own concept, synonyms in the description)
    assert "계약" not in comps
    contracts = comps["Contracts"]
    assert contracts["iri"] == "ao:term-t-c"
    assert contracts["description"].startswith("contracts 테이블. 고객과 맺는 약정.")
    assert contracts["description"].endswith("Also known as: Contract, 계약, 계약서.")
    rels = {r["name"]: r for r in contracts["relationships"]}
    assert rels["계약금액"]["roles"] == [{"concept": "Integer"}]
    assert rels["계약금액"]["verbalizes"] == ["{Contracts} has 계약금액 {Integer}"]
    assert rels["시작일"]["roles"] == [{"concept": "Date"}]
    assert rels["계약id"]["multiplicity"] == "ManyToOne"
    # semantic FK
    assert rels["customers"]["roles"] == [{"concept": "Customers"}]
    assert rels["customers"]["verbalizes"] == ["{Contracts} refers to {Customers}"]
    # attached attribute -> value relationship, and no concept of its own
    assert rels["계약_상태"]["roles"] == [{"concept": "String"}]
    assert rels["계약_상태"]["verbalizes"] == ["{Contracts} has 계약 상태 {String}"]
    assert "계약상태" not in comps
    assert any(
        "계약 상태 is modelled only as a value" in w for w in result["validation"]["warnings"]
    )
    # unattached metric -> its own concept; related alignment -> related_to
    assert "연체율" in comps and comps["연체율"]["description"].startswith("연체 금액의 비율.")
    related = comps["연체율"]["relationships"][0]
    assert related["name"] == "related_to" and related["roles"] == [{"concept": "Invoices"}]
    assert related["verbalizes"] == ["{연체율} is related to {Invoices}"]
    # narrower: the Term is narrower than the concept -> Customers extends 거래처
    assert comps["Customers"]["extends"] == ["거래처"]
    # is_a -> extends
    assert comps["신규계약"]["extends"] == ["Contracts"]
    # declared FK of the semantic model
    inv = {r["name"]: r for r in comps["Invoices"]["relationships"]}
    assert inv["contracts"]["verbalizes"] == ["{Invoices} refers to {Contracts}"]
    # document relations: predicate names, unique per concept
    owner = {r["name"]: r for r in comps["담당자"]["relationships"]}
    assert set(owner) == {"owns_contracts", "owns_승인"}
    assert owner["owns_contracts"]["verbalizes"] == ["{담당자} owns {Contracts}"]
    assert owner["owns_contracts"]["description"] == "담당자가 계약을 관리"
    governed = comps["승인"]["relationships"][0]
    assert governed["name"] == "governed_by" and "description" not in governed
    assert governed["verbalizes"] == ["{승인} governed by {승인기준}"]
    # every relationship name is unique in its concept, every role concept exists
    known = set(comps) | ossie.BUILTIN_CONCEPTS
    for comp in comps.values():
        names = [r["name"] for r in comp.get("relationships", [])]
        assert len(names) == len(set(names)), comp["concept"]
        for r in comp.get("relationships", []):
            assert all(role["concept"] in known for role in r["roles"])
        assert all(e in known for e in comp.get("extends", []))
    assert ossie.ontology_errors(onto) == []


def test_mapping_keys_compound_keys_and_links(bundle):
    out, result, _ = bundle
    mapping = _load(out / "ossie/mapping.yaml")
    assert mapping["ontology_ref"] == {"name": "hanbit_ontology", "iri": "./ontology.yaml"}
    assert mapping["semantic_model_ref"] == {"name": "hanbit", "iri": "./semantic_model.yaml"}
    cms = {m["concept"]: m for m in mapping["concept_mappings"]}
    assert cms["Customers"]["object_mappings"] == [{"expression": "customers.고객id"}]
    assert cms["Invoices"]["object_mappings"] == [
        {
            "referent_mappings": [
                {"relationship": "청구id", "expression": "invoices.청구id"},
                {"relationship": "순번", "expression": "invoices.순번"},
            ]
        }
    ]
    assert cms["Contracts"]["link_mappings"] == [
        {
            "object_mapping": {"expression": "contracts.계약id"},
            "children": [
                {
                    "relationship": "customers",
                    "object_mapping": {"concept": "Customers", "expression": "contracts.고객id"},
                }
            ],
        }
    ]
    comps = _concepts(_load(out / "ossie/ontology.yaml"))
    assert comps["Customers"]["identify_by"] == ["고객id"]
    assert comps["Invoices"]["identify_by"] == ["청구id", "순번"]
    # Invoices -> Contracts: Invoices has a compound key, so no link mapping
    assert any("Invoices.contracts -> Contracts" in w for w in result["validation"]["warnings"])
    assert ossie.mapping_errors(mapping, _load(out / "ossie/ontology.yaml")) == []


def test_ossie_is_deterministic_and_fails_with_the_path(model, tmp_path, monkeypatch):
    zipped = write_zip(tmp_path / "hanbit.ossie.zip", semantic_model())
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        ossie.write(model, out, revision=1, created_at=CREATED, upstream=[("hanbit", zipped)])
    for name in ("semantic_model.yaml", "ontology.yaml", "mapping.yaml", "validation.json"):
        assert (a / "ossie" / name).read_bytes() == (b / "ossie" / name).read_bytes()
    bad = semantic_model()
    bad["datasets"][0]["fields"][0]["datatype"] = 7
    zipped = write_zip(tmp_path / "bad.zip", bad)
    with pytest.raises(ossie.OssieError, match=r"semantic_model\.yaml/datasets/0/fields/0"):
        ossie.write(
            model, tmp_path / "c", revision=1, created_at=CREATED, upstream=[("hanbit", zipped)]
        )
    record = json.loads((tmp_path / "c/ossie/validation.json").read_text("utf-8"))
    assert record["semantic_model"][0].startswith("/datasets/0/fields/0/datatype")
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(ossie.OssieError, match="no readable semantic_model.yaml"):
        ossie.write(
            model,
            tmp_path / "d",
            revision=1,
            created_at=CREATED,
            upstream=[("hanbit", tmp_path / "junk.zip")],
        )


def test_ossie_without_structured_sources_writes_the_ontology_only(tmp_path):
    docs = merged.build("solo", documents=[documents()])
    result = ossie.write(docs, tmp_path, revision=3, created_at=CREATED, upstream=[])
    record = result["validation"]
    assert record["ontology"] == "ok" and record["semantic_model"].startswith("skipped")
    assert sorted(p.name for p in (tmp_path / "ossie").iterdir()) == [
        "ontology.yaml",
        "validation.json",
    ]


def test_self_referencing_fk_names_its_second_role(tmp_path):
    snap = structured()
    snap.fks.append(SemanticFK("t-k", "t-k", "a4", "고객id", "public.customers.추천인id"))
    m = merged.build("ws", structured=[snap])
    result = ossie.write(m, tmp_path, revision=1, created_at=CREATED, upstream=[])
    comps = _concepts(_load(tmp_path / "ossie/ontology.yaml"))
    rel = next(r for r in comps["Customers"]["relationships"] if r["name"] == "customers")
    assert rel["roles"] == [{"concept": "Customers", "name": "target"}]
    assert rel["verbalizes"] == ["{Customers} refers to {Customers:target}"]
    assert result["validation"]["ontology"] == "ok"


def test_structural_checks_catch_what_the_schema_cannot():
    doc = {
        "ontology": [
            {
                "concept": "A",
                "type": "EntityType",
                "extends": ["Nope"],
                "relationships": [
                    {"name": "r", "roles": [{"concept": "B"}], "verbalizes": ["{A} r {C}"]},
                    {"name": "r", "verbalizes": ["{A} r"]},
                ],
            },
            {"concept": "A", "type": "EntityType"},
            {"concept": "String", "type": "ValueType"},
        ]
    }
    errors = ossie.ontology_errors(doc)
    assert "/ontology/0/extends/0: unknown concept 'Nope'" in errors
    assert "/ontology/0/relationships/0/roles/0/concept: unknown concept 'B'" in errors
    assert any(e.startswith("/ontology/0/relationships/0/verbalizes/0") for e in errors)
    assert "/ontology/0/relationships/1/name: 'r' is not unique in A" in errors
    assert any(e.startswith("/ontology/1/concept") for e in errors)
    assert any(e.startswith("/ontology/2/concept") for e in errors)


# --- okf ----------------------------------------------------------------------------------


def _okf(out: Path, rel: str):
    meta, body, error = okf.split_frontmatter((out / "okf" / rel).read_text(encoding="utf-8"))
    assert error is None, rel
    return meta, body


def test_okf_bundle_is_conformant_with_working_links(bundle):
    out, _, okf_result = bundle
    root = out / "okf"
    report = okf.validate(root)
    assert report.ok and report.errors == [] and report.warnings == []
    assert json.loads((root / "validation.json").read_text("utf-8")) == report.to_dict()
    assert okf_result["validation"]["ok"] is True
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))
    assert "index.md" in files and "log.md" in files
    assert "concepts/contracts.md" in files and "concepts/신규-계약.md" in files
    assert "concepts/계약.md" not in files  # folded into Contracts
    assert "concepts/index-2.md" in files  # a concept named like a reserved file
    assert "tables/hanbit.contracts.md" in files
    assert "documents/계약-규정-md.md" in files
    # every bundle link resolves
    for rel in files:
        _, body = _okf(out, rel)
        for target in okf._LINK.findall(body):
            if target.startswith("/"):
                assert (root / target.lstrip("/")).is_file(), (rel, target)


def test_okf_concept_frontmatter_and_body(bundle):
    out, _, _ = bundle
    meta, body = _okf(out, "concepts/contracts.md")
    assert meta["type"] == "Business Concept" and meta["title"] == "Contracts"
    assert meta["resource"] == "http://example.com/autoontology#term-t-c"
    assert meta["tags"] == ["origin:aligned", "kind:term", "source:hanbit", "source:hanbit-docs"]
    assert meta["generated"] == {"by": "autoontology/0.1.0", "at": CREATED}
    assert meta["status"] == "stable"
    assert meta["sources"] == [
        {
            "id": "e1",
            "resource": "autoontology://hanbit-docs/samples/계약%20규정.md",
            "title": "계약관리규정",
        }
    ]
    assert "# Schema" in body and "| `계약금액` | bigint |" in body
    assert "Backed by [hanbit.public.contracts](/tables/hanbit.contracts.md)." in body
    assert "- refers to [Customers](/concepts/customers.md) (column `고객id`)" in body
    assert "- has attribute [계약 상태](/concepts/계약-상태.md) (계약의 상태)" in body
    assert "# Synonyms" in body and "- 계약서" in body
    assert "- folded document concept 계약 (entity from hanbit-docs, confidence 0.95)" in body
    # quotes are escaped; the footnote label matches sources[].id
    assert "- “근거 \\[c1\\] \\| 인용”[^e1]" in body
    assert "[^e1]: [계약관리규정](/documents/계약-규정-md.md), p. 2" in body
    draft, _ = _okf(out, "concepts/담당자.md")
    assert draft["status"] == "draft" and draft["tags"][:2] == ["origin:document", "kind:entity"]
    metric, body = _okf(out, "concepts/연체율.md")
    assert metric["tags"][1] == "kind:metric"
    assert "is related to [Invoices](/concepts/invoices.md)" in body
    rule, _ = _okf(out, "concepts/승인-기준.md")
    assert rule["tags"][1] == "kind:rule" and rule["resource"].endswith("#concept-c7")


def test_okf_tables_documents_index_and_log(bundle):
    out, _, _ = bundle
    table, body = _okf(out, "tables/hanbit.contracts.md")
    assert (
        table["type"] == "Table" and table["resource"] == "autoontology://hanbit/public.contracts"
    )
    assert "| `고객id` | - | refers to [Customers](/concepts/customers.md) |" in body
    assert "- represented by [Contracts](/concepts/contracts.md)" in body
    doc, body = _okf(out, "documents/계약-규정-md.md")
    assert doc["type"] == "Document" and doc["sha256"] == "ab" * 32
    assert doc["resource"] == "autoontology://hanbit-docs/samples/계약%20규정.md"
    assert "- [Contracts](/concepts/contracts.md)" in body
    index, body = _okf(out, "index.md")
    assert index == {"okf_version": "0.2"}
    assert "# Concepts" in body and "# Tables" in body and "# Documents" in body
    assert "* [Contracts](/concepts/contracts.md) - contracts 테이블" in body
    log = (out / "okf/log.md").read_text(encoding="utf-8")
    assert log.startswith("# Update Log\n\n## 2026-10-28\n\n")
    assert (
        "- revision r0001: generated by autoontology 0.1.0 from sources hanbit, hanbit-docs" in log
    )


def test_okf_absolute_document_paths_become_file_uris():
    assert okf.document_resource("/data/계약 규정.pdf", "docs") == "file:///data/계약%20규정.pdf"
    assert okf.slug("Customer Orders (v2)") == "customer-orders-v2"
    assert okf.slug("계약 상태") == "계약-상태" and okf.slug("...") == "x"


def test_okf_validate_reports_errors_and_warnings(tmp_path):
    root = tmp_path / "b"
    (root / "sub").mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: '0.2'\ntitle: x\n---\n", "utf-8")
    (root / "log.md").write_text("# Log\n\n## yesterday\n", "utf-8")
    (root / "sub" / "index.md").write_text("---\ntype: Business Concept\n---\n", "utf-8")
    (root / "a.md").write_text("# no frontmatter\n", "utf-8")
    (root / "b.md").write_text("---\ntype: ''\n---\n", "utf-8")
    (root / "c.md").write_text("---\ntype: [unclosed\n---\n", "utf-8")
    (root / "d.md").write_text(
        "---\ntype: Note\nsources: [{id: s1, resource: x}]\n---\n"
        "See [gone](/missing.md), [ok](/a.md), [web](https://x.y) and a claim[^s2].\n",
        "utf-8",
    )
    report = okf.validate(root)
    joined = "\n".join(report.errors)
    assert "index.md: index.md frontmatter allows only okf_version (title)" in joined
    assert "log.md: log heading 'yesterday' is not YYYY-MM-DD" in joined
    assert "sub/index.md: reserved file name used for a concept" in joined
    assert "a.md: no frontmatter" in joined and "b.md: frontmatter has no non-empty type" in joined
    assert "c.md: frontmatter is not YAML" in joined
    assert report.warnings == [
        "d.md: footnote [^s2] has no sources entry",
        "d.md: broken link /missing.md",
    ]
    assert not report.ok and report.files == 7


def test_okf_write_fails_on_a_non_conformant_bundle(model, tmp_path, monkeypatch):
    monkeypatch.setattr(okf, "CONCEPT_TYPE", "")
    with pytest.raises(okf.OkfError, match="frontmatter has no non-empty type"):
        okf.write(model, tmp_path, revision=1, created_at=CREATED)
    assert json.loads((tmp_path / "okf/validation.json").read_text("utf-8"))["ok"] is False
