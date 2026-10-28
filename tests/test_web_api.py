"""REST API behind the web UI (``/api/*``), against a temp home and fake jobs."""

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.engine import build as build_job
from autoontology.engine import export_job, jobs
from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import Store
from autoontology.store.documents import (
    ConceptRecord,
    DocumentStore,
    EvidenceRecord,
    RelationRecord,
    concept_id,
)
from autoontology.structured.semantic import SemanticFK, StructuredSnapshot
from autoontology.unstructured.align import TermInfo
from autoontology.web import api as web_api
from autoontology.web import app as web
from autoontology.web.runs import RunManager

API = "/api"
log = logging.getLogger("autoontology.jobs")


@dataclass
class _Chunk:
    index: int
    text: str
    section_path: str = "1장 > 계약"
    page: int | None = 3
    kind: str = "text"
    tokens: int = 3


class FakeJobs:
    """Job functions that create a real run row and steps (through the given store)."""

    def __init__(self):
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

    def _run(self, kind, store, steps):
        run = store.create_run(kind=kind, source_id=None, params={"pid": 1})
        ctx = type("Ctx", (), {"settings": None, "store": store, "run": run})()
        ctx.settings = Settings.from_env()

        def step(name):
            def fn(_ctx):
                log.info("  doing %s with password=hunter2 at postgres://u:s3cret@h/db", name)
                self.gate.wait(5)
                return {"n": 1}

            return fn

        return jobs._execute(ctx, steps, skip={}, already=set(), steps={n: step(n) for n in steps})

    def build(self, store, settings, **kw):
        self.calls.append(("build", kw))
        if kw.get("resume"):
            raise jobs.JobError("nothing to resume (fake pre-check)")
        return self._run("build", store, build_job.step_names(build_job.registration_order(store)))

    def export(self, store, settings, **kw):
        self.calls.append(("export", kw))
        return self._run("export", store, ["collect", "serialize", "upstream", "report"])

    def table(self):
        return {"build": self.build, "export": self.export}


@pytest.fixture
def env(isolated_env, monkeypatch):
    monkeypatch.setattr(web_api, "SSE_POLL_S", 0.01)
    settings = Settings.from_env()
    fake = FakeJobs()
    manager = RunManager(settings, jobs=fake.table)
    app = web.create_app(settings, runs=manager)
    return TestClient(app), settings, fake, manager


def _store(settings):
    return Store.for_home(settings.home)


def _docs_dir(root: Path) -> Path:
    d = root / "docs"
    d.mkdir(exist_ok=True)
    (d / "계약.md").write_text("# 계약\n계약은 고객과 맺는 약정이다.\n", encoding="utf-8")
    return d


# --- sources -----------------------------------------------------------------------------


def test_add_list_delete_documents_source_by_path(env, isolated_env):
    c, settings, _, _ = env
    d = _docs_dir(isolated_env)
    resp = c.post(
        f"{API}/sources", json={"name": "hanbit-docs", "kind": "documents", "locator": str(d)}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "documents" and resp.json()["locator"] == str(d.resolve())
    again = c.post(
        f"{API}/sources", json={"name": "hanbit-docs", "kind": "documents", "locator": str(d)}
    )
    assert again.status_code == 409 and again.json()["error"] == "source_exists"
    listed = c.get(f"{API}/sources").json()["sources"]
    assert [s["name"] for s in listed] == ["hanbit-docs"] and listed[0]["concepts"] == 0
    resp = c.delete(f"{API}/sources/hanbit-docs")
    assert resp.status_code == 200 and resp.json()["removed"] == "hanbit-docs"
    assert c.get(f"{API}/sources").json()["sources"] == []
    assert c.delete(f"{API}/sources/hanbit-docs").status_code == 404


def test_source_validation_errors_are_structured(env, isolated_env):
    c, *_ = env
    resp = c.post(
        f"{API}/sources", json={"name": "Bad Name", "kind": "files", "locator": str(isolated_env)}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_name" and "invalid source name" in resp.json()["detail"]
    # structured names follow the T2 rule: no "-"
    resp = c.post(f"{API}/sources", json={"name": "a-b", "kind": "files", "locator": "/x"})
    assert resp.json()["error"] == "invalid_name"
    resp = c.post(f"{API}/sources", json={"name": "a", "kind": "nope", "locator": "/x"})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"
    resp = c.post(
        f"{API}/sources", json={"name": "a", "kind": "documents", "locator": "relative/dir"}
    )
    assert resp.json()["error"] == "invalid_locator"
    assert c.get(f"{API}/sources/../etc").status_code in (404, 405)
    resp = c.get(f"{API}/ontology/concepts", params={"page": 0})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"


def test_database_url_password_never_returned(env):
    c, settings, _, _ = env
    url = "postgresql://scott:tiger-secret@db.example:5432/sales"
    resp = c.post(f"{API}/sources", json={"name": "sales", "kind": "database", "locator": url})
    assert resp.status_code == 201, resp.text
    body = json.dumps(c.get(f"{API}/sources").json())
    assert "tiger-secret" not in body and "scott:***@db.example" in body
    assert "tiger-secret" not in resp.text


def test_upload_files_source_and_documents_source(env):
    c, settings, _, _ = env
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[
            ("files", ("orders.csv", b"id,amount\n1,10\n", "text/csv")),
            ("files", ("customers.csv", b"id,name\n1,kim\n", "text/csv")),
        ],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "files" and body["uploaded"] is True
    assert body["database"] == "autoontology_src_shop"
    target = Path(settings.home) / "uploads" / "shop"
    assert sorted(p.name for p in target.iterdir()) == ["customers.csv", "orders.csv"]

    resp = c.post(
        f"{API}/sources",
        data={"name": "rules-docs", "kind": "documents"},
        files=[("files", ("규정.md", "# 규정\n본문".encode(), "text/markdown"))],
    )
    assert resp.status_code == 201, resp.text
    assert (Path(settings.home) / "uploads" / "rules-docs" / "규정.md").is_file()
    # deleting an uploaded documents source removes its upload folder too
    assert c.delete(f"{API}/sources/rules-docs").status_code == 200
    assert not (Path(settings.home) / "uploads" / "rules-docs").exists()


@pytest.mark.parametrize(
    "filename", ["../evil.csv", "..\\evil.csv", ".hidden.csv", "/abs/evil.csv"]
)
def test_upload_rejects_unsafe_file_names(env, filename):
    c, settings, _, _ = env
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", (filename, b"id\n1\n", "text/csv"))],
    )
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_file_name"
    assert not (Path(settings.home) / "evil.csv").exists()
    assert not (Path(settings.home) / "uploads" / "shop").exists()
    assert c.get(f"{API}/sources").json()["sources"] == []


def test_upload_rejects_unsupported_type_and_size(env, monkeypatch):
    c, settings, _, _ = env
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", ("x.exe", b"MZ", "application/octet-stream"))],
    )
    assert resp.status_code == 400 and resp.json()["error"] == "unsupported_file"
    monkeypatch.setenv("AUTOONTOLOGY_UPLOAD_MAX_MB", "0.000001")
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", ("a.csv", b"id\n" + b"1\n" * 100, "text/csv"))],
    )
    assert resp.status_code == 413
    assert not (Path(settings.home) / "uploads" / "shop").exists()


# --- runs ----------------------------------------------------------------------------------


def _files_source(c, isolated_env, name="shop"):
    d = isolated_env / "csv"
    d.mkdir(exist_ok=True)
    (d / "a.csv").write_text("x\n1\n")
    assert (
        c.post(
            f"{API}/sources", json={"name": name, "kind": "files", "locator": str(d)}
        ).status_code
        == 201
    )


SHOP_STEPS = [
    "structured:shop:load",
    "structured:shop:register",
    "structured:shop:ingest",
    "structured:shop:compile",
    "merge",
    "snapshot",
]


def test_run_lifecycle_lock_and_sse(env, isolated_env):
    c, settings, fake, manager = env
    _files_source(c, isolated_env)
    fake.gate.clear()
    resp = c.post(f"{API}/runs", json={"kind": "build"})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    assert fake.calls[0] == ("build", {"sources": None, "resume": False})
    busy = c.post(f"{API}/runs", json={"kind": "build"})
    assert busy.status_code == 409 and busy.json()["error"] == "run_active"
    busy = c.post(f"{API}/runs", json={"kind": "export"})
    assert busy.status_code in (404, 409)  # no snapshot yet / workspace busy
    gone = c.delete(f"{API}/sources/shop")
    assert gone.status_code == 409 and gone.json()["error"] == "run_active"
    state = c.get(f"{API}/runs/{run_id}").json()
    assert state["status"] == "running" and state["managed"] is True
    assert [s["name"] for s in state["steps"]] == SHOP_STEPS
    assert state["steps"][0]["status"] == "running" and state["steps"][1]["status"] == "pending"
    fake.gate.set()
    manager.join(run_id, 5)
    state = c.get(f"{API}/runs/{run_id}").json()
    assert state["status"] == "succeeded" and state["managed"] is False
    assert [s["name"] for s in state["steps"]] == SHOP_STEPS
    assert all(s["status"] == "succeeded" for s in state["steps"])

    with c.stream("GET", f"{API}/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    events = [line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("event: ")]
    assert events[0] == "log" and events[-1] == "end"
    assert events.count("step") == 6 and "status" in events
    assert "[1/6] structured:shop:load" in text and "doing merge" in text
    assert "hunter2" not in text and "s3cret" not in text  # masked in the log stream
    assert '"status": "succeeded"' in text

    runs = c.get(f"{API}/runs").json()["runs"]
    assert [r["id"] for r in runs] == [run_id] and runs[0]["source"] is None
    assert "pid" not in runs[0]["params"]
    # the per-run log file holds the run's lines only
    run_log = Path(settings.logs_dir) / "runs" / f"{run_id}.log"
    assert "[6/6] snapshot" in run_log.read_text()


def test_run_precheck_failure_is_returned(env, isolated_env):
    c, _, fake, manager = env
    resp = c.post(f"{API}/runs", json={"kind": "build"})
    assert resp.status_code == 400 and resp.json()["error"] == "no_sources"
    _files_source(c, isolated_env)
    resp = c.post(f"{API}/runs", json={"kind": "build", "resume": True})
    assert resp.status_code == 400 and "nothing to resume" in resp.json()["detail"]
    assert c.get(f"{API}/runs").json()["runs"] == []
    # the lock is released after a failed start
    resp = c.post(f"{API}/runs", json={"kind": "build", "sources": ["shop"]})
    assert resp.status_code == 202, resp.text
    manager.join(resp.json()["run_id"], 5)
    assert fake.calls[-1] == ("build", {"sources": ["shop"], "resume": False})


def test_run_kind_and_options_validated(env, isolated_env):
    c, _, fake, _ = env
    _files_source(c, isolated_env)
    cases = [
        ({"kind": "build", "source": "shop"}, "invalid_request"),
        ({"kind": "build", "options": {}}, "invalid_request"),
        ({"kind": "extract"}, "invalid_request"),
        ({"kind": "drop"}, "invalid_request"),
        ({"kind": "build", "sources": ["missing"]}, "unknown_source"),
        ({"kind": "export"}, "no_snapshot"),
        ({"kind": "export", "revision": 0}, "invalid_request"),
        ({"kind": "export", "resume": True}, "invalid_request"),
    ]
    for body, error in cases:
        resp = c.post(f"{API}/runs", json=body)
        assert resp.json().get("error") == error, (body, resp.text)
    assert fake.calls == []


def test_export_run_takes_a_revision(env, isolated_env):
    c, settings, fake, manager = env
    _seed_snapshot(settings)
    assert c.post(f"{API}/runs", json={"kind": "export", "revision": 9}).status_code == 404
    run_id = c.post(f"{API}/runs", json={"kind": "export", "revision": 1}).json()["run_id"]
    manager.join(run_id, 5)
    assert fake.calls == [("export", {"revision": 1})]
    state = c.get(f"{API}/runs/{run_id}").json()
    assert [s["name"] for s in state["steps"]] == ["collect", "serialize", "upstream", "report"]


def test_cancel_stops_before_the_next_step(env, isolated_env):
    c, _, fake, manager = env
    _files_source(c, isolated_env)
    fake.gate.clear()
    run_id = c.post(f"{API}/runs", json={"kind": "build"}).json()["run_id"]
    resp = c.post(f"{API}/runs/{run_id}/cancel")
    assert resp.status_code == 202
    fake.gate.set()
    manager.join(run_id, 5)
    state = c.get(f"{API}/runs/{run_id}").json()
    assert state["status"] == "cancelled" and "cancelled before step" in state["error"]
    statuses = {s["name"]: s["status"] for s in state["steps"]}
    assert statuses["structured:shop:load"] == "succeeded"
    assert "structured:shop:register" not in statuses  # never started (finished runs: rows only)
    assert c.post(f"{API}/runs/{run_id}/cancel").status_code == 409
    assert c.get(f"{API}/runs/bad id").status_code == 400
    assert c.get(f"{API}/runs/20990101T000000Z-abcdef").status_code == 404


# --- concepts, graph, exports ---------------------------------------------------------------


def _seed_documents(settings, name="hanbit-docs"):
    store = _store(settings)
    src = store.add_source(name=name, kind="documents", locator=json.dumps({"path": "/x"}))
    docs = DocumentStore(store)
    doc, _ = docs.replace_document(
        source_id=src.id,
        path="samples/계약관리규정.md",
        title="계약관리규정",
        mime="text/markdown",
        engine="native",
        sha256="ab" * 32,
        size=10,
        pages=None,
        chunks=[_Chunk(0, "계약은 고객과 맺는 약정이다.")],
    )
    chunk = docs.chunks_for_document(doc.id)[0].id
    ev = [EvidenceRecord(chunk, "계약은 고객과 맺는 약정", True)]
    docs.replace_ontology(
        src.id,
        [
            ConceptRecord("계약", "entity", "고객과 맺는 약정", "Contract", ["계약서"], [], [], ev),
            ConceptRecord("계약금액", "metric", "계약의 총 금액 (100% 기준)", evidence=ev),
            ConceptRecord("담당자", "entity", "업무 담당자", evidence=ev),
            ConceptRecord("승인 기준", "rule", "3억 이상은 대표이사 승인"),
        ],
        [RelationRecord("담당자", "owns", "계약", "담당자가 계약을 관리", ev)],
    )
    structured = store.add_source(
        name="hanbit",
        kind="files",
        locator=json.dumps({"path": "/y"}),
        pg_database="autoontology_src_hanbit",
    )
    store.set_catalog_database_id(structured.id, "db1")
    AlignmentStore(store).put(
        AlignmentRecord(
            src.id,
            concept_id(src.id, "entity", "계약"),
            "계약",
            "same",
            "r",
            "hanbit",
            "t-c",
            "Contracts",
            0.8,
            0.93,
            "같은 개념",
            accepted=True,
        )
    )
    AlignmentStore(store).put(
        AlignmentRecord(
            src.id,
            concept_id(src.id, "metric", "계약금액"),
            "계약금액",
            "related",
            "r",
            "hanbit",
            "t-c",
            "Contracts",
            0.7,
            0.8,
            "속성",
        )
    )
    return src


def _snapshot():
    contracts = TermInfo(
        "t-c",
        "Contracts",
        "계약 정보",
        ["Contract"],
        attributes=[
            {
                "id": "a1",
                "name": "계약금액",
                "datatype": "bigint",
                "column": "계약금액",
                "schema": "public",
                "table": "contracts",
            }
        ],
        tables=[{"id": "tb-c", "name": "contracts", "schema": "public"}],
    )
    customers = TermInfo(
        "t-k",
        "Customers",
        "고객",
        attributes=[],
        tables=[{"id": "tb-k", "name": "customers", "schema": "public"}],
    )
    return StructuredSnapshot(
        "hanbit",
        "db1",
        "autoontology_src_hanbit",
        [contracts, customers],
        [SemanticFK("t-c", "t-k", "a3", "고객id", "public.contracts.고객id")],
    )


def _seed_snapshot(settings):
    """The documents + structured fixture merged and saved as snapshot revision 1."""
    from autoontology.ontology import model as merged

    src = _seed_documents(settings)
    store = _store(settings)
    model = merged.build(
        settings.workspace,
        structured=[_snapshot()],
        documents=export_job.documents_inputs(store, [src]),
        base=settings.base_iri,
    )
    run = store.create_run(kind="build", source_id=None)
    store.update_run(run.id, status="succeeded")
    return store.save_snapshot(run.id, model), model


def test_ontology_summary(env):
    c, settings, _, _ = env
    resp = c.get(f"{API}/ontology")
    assert resp.status_code == 404 and resp.json()["error"] == "no_snapshot"
    assert c.get(f"{API}/ontology/graph").json()["error"] == "no_snapshot"
    revision, model = _seed_snapshot(settings)
    body = c.get(f"{API}/ontology").json()
    assert body["revision"] == revision == 1
    assert body["workspace"] == "default" and body["base_iri"] == "http://example.com/autoontology#"
    assert {(s["name"], s["kind"]) for s in body["sources"]} == {
        ("hanbit", "structured"),
        ("hanbit-docs", "documents"),
    }
    assert body["counts"]["classes"] == len(model.classes)
    assert body["counts"]["merged_concepts"] == 1
    assert c.get(f"{API}/ontology", params={"revision": 2}).json()["error"] == "unknown_revision"


def test_ontology_concepts_search_and_detail(env):
    c, settings, _, _ = env
    _seed_snapshot(settings)
    body = c.get(f"{API}/ontology/concepts").json()
    items = {i["name"]: i for i in body["items"]}
    assert body["total"] == 6 and body["revision"] == 1
    assert set(items) == {"Contracts", "Customers", "계약", "계약금액", "담당자", "승인 기준"}
    assert [i["kind"] for i in body["items"][:2]] == ["term", "term"]  # Terms first
    contracts = items["Contracts"]
    assert contracts["origin"] == "aligned" and contracts["source"] == "hanbit"
    assert contracts["merged_concepts"] == ["계약"] and "계약서" in contracts["synonyms"]
    assert contracts["alignment"]["counts"] == {"same": 1, "related": 1}
    assert items["Customers"]["origin"] == "structured"
    folded = items["계약"]
    assert folded["origin"] == "document" and folded["folded_into"]["name"] == "Contracts"
    assert items["계약금액"]["alignment"]["best"]["relation"] == "related"
    assert items["승인 기준"]["kind"] == "rule" and items["승인 기준"]["source"] == "hanbit-docs"
    by_syn = c.get(f"{API}/ontology/concepts", params={"q": "계약서"}).json()["items"]
    assert [i["name"] for i in by_syn] == ["Contracts"]
    origin = c.get(f"{API}/ontology/concepts", params={"origin": "document"}).json()
    assert {i["origin"] for i in origin["items"]} == {"document"} and origin["total"] == 4
    metrics = c.get(f"{API}/ontology/concepts", params={"kind": "metric"}).json()["items"]
    assert [i["name"] for i in metrics] == ["계약금액"]
    page2 = c.get(f"{API}/ontology/concepts", params={"size": 4, "page": 2}).json()
    assert page2["pages"] == 2 and len(page2["items"]) == 2
    bad = c.get(f"{API}/ontology/concepts", params={"origin": "x"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_origin"

    detail = c.get(f"{API}/ontology/concepts/{contracts['id']}").json()
    assert detail["description"] == "계약 정보" and detail["tables"] == ["public.contracts"]
    assert [a["label"] for a in detail["attributes"]] == ["계약금액"]
    assert detail["evidence"][0]["quote"] == "계약은 고객과 맺는 약정"
    assert detail["evidence"][0]["document"] == "계약관리규정"
    assert {r["predicate"] for r in detail["relations"]} >= {"owns", "semantic_fk"}
    assert {a["relation"] for a in detail["alignments"]} == {"same", "related"}
    folded_detail = c.get(f"{API}/ontology/concepts/{folded['id']}").json()
    assert folded_detail["folded_into"]["id"] == contracts["id"]
    assert c.get(f"{API}/ontology/concepts/concept-{'0' * 32}").status_code == 404
    assert c.get(f"{API}/ontology/concepts/xyz").status_code == 400
    for gone in ("/concepts", "/relations", "/graph"):
        assert c.get(f"{API}{gone}").status_code == 404


def test_a_proposal_below_the_accept_threshold_changes_no_origin(env):
    """A judge ``related`` at 0.3 is exported as an axiom but is not an accepted edge: the
    Term stays ``structured`` and the link says ``accepted: False``."""
    from autoontology.ontology import model as merged
    from autoontology.web.concepts import concept_items

    c, settings, _, _ = env
    src = _seed_documents(settings)
    store = _store(settings)
    AlignmentStore(store).put(
        AlignmentRecord(
            src.id,
            concept_id(src.id, "entity", "담당자"),
            "담당자",
            "related",
            "r",
            "hanbit",
            "t-k",
            "Customers",
            0.6,
            0.3,
            "weak",
        )
    )
    model = merged.build(
        settings.workspace,
        structured=[_snapshot()],
        documents=export_job.documents_inputs(store, [src]),
        base=settings.base_iri,
    )
    weak = [
        a for a in model.axioms if a.source == "alignment" and "term-t-k" in (a.subject, a.object)
    ]
    assert len(weak) == 1 and weak[0].accepted is False  # still exported
    assert "term-t-k" not in model.aligned_terms()
    assert model.origin("term-t-k") == "structured"
    assert model.origin("term-t-c") == "aligned"  # folded 계약 (accepted same)
    items = {i["name"]: i for i in concept_items(model)}
    assert items["Customers"]["origin"] == "structured"
    best = items["Customers"]["alignment"]["best"]
    assert best["relation"] == "related" and best["accepted"] is False
    again = merged.MergedOntology.from_dict(model.to_dict())
    assert again.origin("term-t-k") == "structured"


def test_ontology_graph_from_the_snapshot(env):
    c, settings, _, _ = env
    _, model = _seed_snapshot(settings)
    g = c.get(f"{API}/ontology/graph").json()
    assert g["revision"] == 1 and g["warnings"] == []
    nodes = {n["id"]: n for n in g["nodes"]}
    contracts = nodes["term-t-c"]
    assert contracts["type"] == "class" and contracts["origin"] == "aligned"
    assert nodes["term-t-k"]["origin"] == "structured"
    assert contracts["source"] == "hanbit" and contracts["enriched_by"] == "hanbit-docs"
    assert "계약" in contracts["synonyms"] and contracts["attributes"] == ["계약금액"]
    folded = [n for n in g["nodes"] if n["type"] == "concept"]
    assert [n["label"] for n in folded] == ["계약"] and folded[0]["folded_into"] == "term-t-c"
    assert {n["origin"] for n in g["nodes"]} == {"structured", "document", "aligned"}
    assert g["counts"]["origins"] == {"aligned": 1, "document": 4, "structured": 1}
    types = {e["type"] for e in g["edges"]}
    assert {"semantic_fk", "aligned_same", "related", "document"} <= types
    assert sum(n["type"] == "class" for n in g["nodes"]) == len(model.classes)

    structured = c.get(f"{API}/ontology/graph", params={"layer": "structured"}).json()
    assert {n["origin"] for n in structured["nodes"]} == {"structured", "aligned"}
    assert {e["type"] for e in structured["edges"]} == {"semantic_fk"}
    aligned = c.get(f"{API}/ontology/graph", params={"layer": "aligned"}).json()
    assert {e["type"] for e in aligned["edges"]} == {"aligned_same", "related"}
    assert {n["id"] for n in aligned["nodes"]} >= {"term-t-c"}
    docs_only = c.get(f"{API}/ontology/graph", params={"layer": "documents"}).json()
    endpoints = {x for e in docs_only["edges"] for x in (e["source"], e["target"])}
    assert all(n["origin"] == "document" or n["id"] in endpoints for n in docs_only["nodes"])
    assert {e["layer"] for e in docs_only["edges"]} == {"documents"}
    bad = c.get(f"{API}/ontology/graph", params={"layer": "bogus"})
    assert bad.json()["error"] == "invalid_layer"


def test_ontology_graph_evidence_layer_and_env_cap(env, monkeypatch):
    c, settings, _, _ = env
    _seed_snapshot(settings)
    plain = c.get(f"{API}/ontology/graph").json()
    assert not any(n["type"] == "document" for n in plain["nodes"])
    assert plain["max_nodes"] == 1500
    g = c.get(f"{API}/ontology/graph", params={"evidence": "true"}).json()
    docs = [n for n in g["nodes"] if n["type"] == "document"]
    assert [n["label"] for n in docs] == ["계약관리규정"] and docs[0]["layer"] == "evidence"
    assert docs[0]["path"] == "계약관리규정.md"  # basename only
    ev = [e for e in g["edges"] if e["type"] == "evidence"]
    assert ev and all(e["target"] == docs[0]["id"] and e["count"] >= 1 for e in ev)
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["term-t-c"]["evidence_count"] == 1  # folded 계약's evidence
    rel = {e["type"]: e.get("relation") for e in g["edges"] if e["layer"] == "aligned"}
    assert rel == {"aligned_same": "same", "related": "related"}
    monkeypatch.setenv("AUTOONTOLOGY_GRAPH_MAX_NODES", "2")
    capped = c.get(f"{API}/ontology/graph").json()
    assert capped["truncated"] is True and capped["max_nodes"] == 2
    assert len(capped["nodes"]) == 2


def test_ontology_follows_the_latest_snapshot(env):
    from autoontology.ontology import model as merged

    c, settings, _, _ = env
    _seed_snapshot(settings)
    assert c.get(f"{API}/ontology").json()["revision"] == 1
    store = _store(settings)
    store.save_snapshot("r2", merged.build(settings.workspace, structured=[_snapshot()]))
    body = c.get(f"{API}/ontology").json()
    assert body["revision"] == 2 and body["counts"]["classes"] == 2
    assert c.get(f"{API}/ontology/graph").json()["revision"] == 2
    old = c.get(f"{API}/ontology/graph", params={"revision": 1}).json()
    assert old["revision"] == 1 and old["counts"]["nodes"] > 2


def test_graph_cap_applies_before_the_evidence_layer():
    from autoontology.ontology import model as merged
    from autoontology.web.graph import to_view

    model = merged.MergedOntology(name="g", base="x")
    for i, local in enumerate(("a", "b", "c")):
        model.documents[f"doc-{i}"] = merged.ODocument(
            f"doc-{i}", f"D{i}", "0" * 64, f"/secret/dir/d{i}.md", "docs"
        )
        model.evidence[f"ev-{i}"] = merged.OEvidence(f"ev-{i}", f"doc-{i}", "q", None, True)
        model.classes[local] = merged.OClass(
            local=local,
            label=local.upper(),
            source="document",
            documents_source="docs",
            derived_from=[f"ev-{i}"],
        )
    model.axioms.append(merged.OAxiom("a", "related", "b", "document"))  # c is the least connected
    view = to_view(model, evidence=True, max_nodes=2)
    assert view["truncated"] is True and view["counts"]["total_nodes"] == 3
    kept = {n["id"] for n in view["nodes"] if n["type"] != "document"}
    docs = {n["id"] for n in view["nodes"] if n["type"] == "document"}
    assert kept == {"a", "b"}  # the cap counted classes only, not the 3 hidden documents
    assert docs == {"doc-0", "doc-1"}  # no document for the dropped node c
    ev = [e for e in view["edges"] if e["type"] == "evidence"]
    assert {(e["source"], e["target"]) for e in ev} == {("a", "doc-0"), ("b", "doc-1")}
    assert {n["path"] for n in view["nodes"] if n["type"] == "document"} == {"d0.md", "d1.md"}


def test_graph_alignment_subclass_relation_direction():
    from autoontology.ontology import model as merged
    from autoontology.web.graph import to_view

    model = merged.MergedOntology(name="g", base="x")
    model.classes["t"] = merged.OClass(local="t", label="T", source="structured")
    model.classes["c"] = merged.OClass(local="c", label="C", source="document")
    model.axioms += [
        merged.OAxiom("t", "subclass_of", "c", "alignment"),
        merged.OAxiom("c", "subclass_of", "t", "alignment"),
        merged.OAxiom("c", "subclass_of", "t", "document"),
    ]
    rels = [e.get("relation") for e in to_view(model)["edges"]]
    assert rels == ["narrower", "broader", None]


def test_exports_list_and_path_safe_download(env):
    c, settings, _, _ = env
    rev_dir = Path(settings.exports_dir) / "r0003"
    (rev_dir / "upstream").mkdir(parents=True)
    (rev_dir / "ontology.ttl").write_text("@prefix ao: <x#> .\n", encoding="utf-8")
    (rev_dir / "report.md").write_text("# 보고서\n", encoding="utf-8")
    (rev_dir / "upstream" / "hanbit.ossie.zip").write_bytes(b"PK")
    (Path(settings.exports_dir) / "r0001").mkdir()
    (Path(settings.exports_dir) / "hanbit-docs").mkdir()  # not a revision folder
    (Path(settings.exports_dir) / "secret.txt").write_text("top secret")
    listed = c.get(f"{API}/exports").json()["exports"]
    assert [e["revision"] for e in listed] == [3, 1] and listed[0]["dir"] == "r0003"
    assert [f["name"] for f in listed[0]["files"]] == [
        "ontology.ttl",
        "report.md",
        "upstream/hanbit.ossie.zip",
    ]

    resp = c.get(f"{API}/exports/r0003/ontology.ttl")
    assert resp.status_code == 200 and resp.text.startswith("@prefix")
    assert "attachment" in resp.headers["content-disposition"]
    assert c.get(f"{API}/exports/3/ontology.ttl").status_code == 200  # bare revision number
    zipped = c.get(f"{API}/exports/r0003/upstream/hanbit.ossie.zip")
    assert zipped.status_code == 200 and zipped.content == b"PK"
    inline = c.get(f"{API}/exports/r0003/ontology.ttl", params={"inline": True})
    assert inline.headers["content-type"].startswith("text/plain")
    for bad in [
        "r0003/..%2Fsecret.txt",
        "..%2F..%2Fsecret.txt/x",
        "r0003/.hidden",
        "r0003/%2Fetc%2Fpasswd",
        "r0003/upstream/../../secret.txt",
        "r0003/a/b/c.txt",
        "x/ontology.ttl",
    ]:
        resp = c.get(f"{API}/exports/{bad}")
        assert resp.status_code in (400, 404), bad
        assert "top secret" not in resp.text
    assert c.get(f"{API}/exports/r0003/missing.ttl").status_code == 404


def test_status_reports_backend_postgres_ollama_kordoc_and_providers(env, monkeypatch):
    c, settings, _, _ = env
    import httpx

    from autoontology.engine import pg

    def no_backend(*a, **k):
        raise httpx.ConnectError("refused")

    def no_postgres(*a, **k):
        raise pg.psycopg.OperationalError("refused")

    monkeypatch.setattr(web_api.httpx, "get", no_backend)
    monkeypatch.setattr(pg, "connect", no_postgres)
    body = c.get(f"{API}/status").json()
    assert body["checks"]["backend"] == {"status": "error", "detail": "ConnectError"}
    assert body["checks"]["postgres"]["status"] == "error"
    assert "docker compose up -d" in body["checks"]["postgres"]["remedy"]
    assert body["checks"]["ollama"]["status"] == "error"
    assert isinstance(body["kordoc_available"], bool)
    assert body["providers"] == {
        "chat_provider": "ollama",
        "chat_model": "openai/qwen3:8b",
        "embed_provider": "ollama",
        "embed_model": "bge-m3",
        "openai_base_url": "",
    }
    assert body["status"] == "error"
    assert body["upload_max_mb"] == web_api.upload_limit_bytes() / (1024 * 1024)
    # cached: a second call within STATUS_CACHE_S does not probe again
    monkeypatch.setattr(
        web_api.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not cached"))
    )
    assert c.get(f"{API}/status").json() == body
    root = c.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/ui/"
    assert c.get("/health/live").json() == {"status": "ok"}  # the shims are untouched


def test_status_never_shows_the_openai_key(isolated_env, monkeypatch):
    from fastapi.testclient import TestClient

    from autoontology.config import Settings
    from autoontology.web import app as web

    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_EMBED_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_API_KEY", "sk-very-secret-123456")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "m")
    import httpx

    monkeypatch.setattr(
        web_api.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused"))
    )
    from autoontology.engine import pg

    monkeypatch.setattr(pg, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    c = TestClient(web.create_app(Settings.from_env()))
    resp = c.get(f"{API}/status")
    assert resp.status_code == 200
    assert "sk-very-secret" not in resp.text
    body = resp.json()
    assert body["providers"]["chat_provider"] == "openai"
    assert body["providers"]["openai_base_url"] == "https://llm.example.test/v1"
    assert "ollama" not in body["checks"]


def test_committed_ui_is_served_or_a_clear_503(env):
    c, _, _, _ = env
    page = c.get("/ui/")
    if not (web_api.UI_DIST / "index.html").is_file():
        # T1 ships no build (ui/ is rebuilt later): the service says so
        assert page.status_code == 503 and page.json()["error"] == "ui_not_built"
        return
    assert page.status_code == 200 and "AutoOntology" in page.text
    assert '<div id="root">' in page.text and page.headers["cache-control"] == "no-cache"
    import re

    assets = re.findall(r'(?:src|href)="/ui/(assets/[^"]+)"', page.text)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for asset in assets:
        resp = c.get(f"/ui/{asset}")
        assert resp.status_code == 200 and "immutable" in resp.headers["cache-control"]


def test_ui_spa_fallback_and_redirects(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>AutoOntology</title>", encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    app = FastAPI()
    web_api.install_ui(app, dist)
    c = TestClient(app)
    assert c.get("/ui", follow_redirects=False).headers["location"] == "/ui/"
    # client-side routes (also nested, with a query) get index.html
    for path in (
        "/ui/",
        "/ui/sources",
        "/ui/settings",
        "/ui/runs?run=20261027T202922Z-34f08b",
        "/ui/concepts/deep/link",
    ):
        resp = c.get(path)
        assert resp.status_code == 200 and "<title>AutoOntology</title>" in resp.text, path
        assert resp.headers["content-type"].startswith("text/html")
    asset = c.get("/ui/assets/index-abc.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert c.get("/ui/favicon.svg").headers["cache-control"] == "no-cache"
    # a missing file is a 404, never HTML; nothing outside dist is reachable
    assert c.get("/ui/assets/missing.js").status_code == 404
    assert c.get("/ui/missing.css").status_code == 404
    for bad in ("/ui/../secret.txt", "/ui/%2e%2e/secret.txt", "/ui/..%2fsecret.txt"):
        resp = c.get(bad)
        assert "top secret" not in resp.text, bad
    # a NUL byte makes Path.resolve() raise: a 404, not a 500
    nul = c.get("/ui/assets/x%00.js")
    assert nul.status_code == 404 and nul.json()["error"] == "not_found"


def test_ui_not_built_is_a_clear_503(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    web_api.install_ui(app, tmp_path / "missing")
    resp = TestClient(app).get("/ui/sources")
    assert resp.status_code == 503
    assert resp.json()["error"] == "ui_not_built" and "tools build-ui" in resp.json()["detail"]


def test_build_ui_reports_missing_tools(tmp_path, monkeypatch):
    from autoontology.web import ui_build

    src = tmp_path / "ui"
    with pytest.raises(ui_build.BuildUiError, match="no UI sources"):
        ui_build.build_ui(src)
    src.mkdir()
    (src / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: None)
    with pytest.raises(ui_build.BuildUiError, match="Node.js not found"):
        ui_build.build_ui(src)
    monkeypatch.setattr(
        ui_build.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )
    monkeypatch.setattr(ui_build, "_node_version", lambda node: (22, 1))
    with pytest.raises(ui_build.BuildUiError, match="pnpm not found"):
        ui_build.build_ui(src)
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(ui_build, "_node_version", lambda node: (18, 2))
    with pytest.raises(ui_build.BuildUiError, match="too old"):
        ui_build.build_ui(src)
    monkeypatch.setattr(ui_build, "_node_version", lambda node: (22, 1))
    ran = []

    class Done:
        def __init__(self, code):
            self.returncode = code

    def fake_run(argv, cwd, check):
        ran.append(argv[1:])
        return Done(1 if argv[1] == "build" else 0)

    with pytest.raises(ui_build.BuildUiError, match="pnpm build.*exit 1"):
        ui_build.build_ui(src, echo=lambda m: None, run=fake_run)
    assert ran == [["install", "--frozen-lockfile"], ["build"]]


def test_cli_ui_prints_url_and_opens_browser(isolated_env, monkeypatch):
    import webbrowser

    from typer.testing import CliRunner

    from autoontology.cli import app

    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(jobs, "service_alive", lambda settings: False)
    res = CliRunner().invoke(app, ["ui"])
    assert res.exit_code == 0 and "http://127.0.0.1:7800/ui/" in res.output
    assert "autoontology up" in res.output and opened == ["http://127.0.0.1:7800/ui/"]
    opened.clear()
    assert CliRunner().invoke(app, ["ui", "--no-browser"]).exit_code == 0 and opened == []


def test_foreign_host_and_cross_origin_writes_are_refused(env, isolated_env):
    c, *_ = env
    d = _docs_dir(isolated_env)
    body = {"name": "docs", "kind": "documents", "locator": str(d)}
    resp = c.post(f"{API}/sources", json=body, headers={"origin": "http://evil.example"})
    assert resp.status_code == 403 and resp.json()["error"] == "cross_origin"
    resp = c.post(
        f"{API}/sources",
        data={"name": "x", "kind": "files"},
        files=[("files", ("a.csv", b"x\n1\n", "text/csv"))],
        headers={"sec-fetch-site": "cross-site"},
    )
    assert resp.status_code == 403
    resp = c.get(f"{API}/sources", headers={"host": "evil.example:7800"})
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden_host"
    assert c.get(f"{API}/sources").json()["sources"] == []
    # same-origin writes (the UI) and header-less clients (curl) are fine
    resp = c.post(f"{API}/sources", json=body, headers={"origin": "http://testserver"})
    assert resp.status_code == 201, resp.text
    assert c.get("/health/live", headers={"host": "evil.example"}).status_code == 200


# --- review fixes ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, secret",
    [
        ('{"password": "hunter2"}', "hunter2"),
        ("{'password': 'hunter2'}", "hunter2"),
        ("POSTGRES_PASSWORD=pw123", "pw123"),
        ("PGPASSWORD=hunter2 psql", "hunter2"),
        ("OPENAI_API_KEY=sk-abc123def456", "sk-abc123def456"),
        ("NVIDIA_API_KEY: nvapi-AbC_12-xyz789", "nvapi-AbC_12-xyz789"),
        ("client_secret=s3cr3t&x=1", "s3cr3t"),
        ("postgresql://:hunter2@h:5432/db", "hunter2"),
        ("postgresql://scott:tiger@h/db", "tiger"),
        ("Authorization: Bearer sk-live-0123456789", "sk-live-0123456789"),
        ("headers={'Authorization': 'Bearer abcdef.ghijk'}", "abcdef.ghijk"),
        ("calling with key sk-proj-Zz9_yy8-xx7 now", "sk-proj-Zz9_yy8-xx7"),
        ("token=abc.def", "abc.def"),
        ("password = 'pa ss'", "pa ss"),
        ('{"db_password":"x y"}', "x y"),
    ],
)
def test_mask_hides_secret_forms(text, secret):
    out = web_api.mask(text)
    assert secret not in out and "***" in out, out


@pytest.mark.parametrize(
    "text",
    [
        "token_count=3",
        "max_tokens: 1200",
        "1200 tokens: fine",
        "risk-assessment task-runner",
        "secretary=kim",
    ],
)
def test_mask_leaves_ordinary_text(text):
    assert web_api.mask(text) == text


def test_mask_dict_keys():
    out = web_api.mask({"Authorization": "x", "db": {"private_key": "k"}, "n": "ok"})
    assert out == {"Authorization": "***", "db": {"private_key": "***"}, "n": "ok"}


def _finished_run(settings, name="shop"):
    store = _store(settings)
    src = store.add_source(
        name=name,
        kind="files",
        locator=json.dumps({"path": "/x"}),
        pg_database=f"autoontology_src_{name}",
    )
    run = store.create_run(kind="build", source_id=src.id)
    store.update_run(run.id, status="succeeded")
    path = Path(settings.logs_dir) / "runs" / f"{run.id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return run.id, path


def _sse(text):
    out, cur = [], {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line:
            if cur:
                out.append(cur)
            cur = {}
        elif ": " in line:
            k, v = line.split(": ", 1)
            cur[k] = v
    return out


def test_sse_reads_lines_written_before_end(env, monkeypatch):
    c, settings, _, manager = env
    run_id, path = _finished_run(settings)
    path.write_text("first\n")
    calls = {"n": 0}

    def managed(rid):
        # the run thread is still writing when the stream first looks; it then finishes
        # and writes its last line (with no trailing newline) right before unregistering
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        if calls["n"] == 2:
            with path.open("a") as fh:
                fh.write("second\nlast-no-newline")
        return False

    monkeypatch.setattr(manager, "is_managed", managed)
    with c.stream("GET", f"{API}/runs/{run_id}/events") as resp:
        events = _sse("".join(resp.iter_text()))
    logs = [e["data"] for e in events if e.get("event") == "log"]
    assert logs == ["first", "second", "last-no-newline"]
    assert events[-1]["event"] == "end"


def test_sse_resumes_from_last_event_id(env):
    c, settings, _, _ = env
    run_id, path = _finished_run(settings)
    path.write_text("one\ntwo\nthree\n")
    with c.stream("GET", f"{API}/runs/{run_id}/events") as resp:
        events = [e for e in _sse("".join(resp.iter_text())) if e.get("event") == "log"]
    assert [(e["data"], e["id"]) for e in events] == [("one", "4"), ("two", "8"), ("three", "14")]
    with c.stream("GET", f"{API}/runs/{run_id}/events", headers={"Last-Event-ID": "4"}) as resp:
        again = [e["data"] for e in _sse("".join(resp.iter_text())) if e.get("event") == "log"]
    assert again == ["two", "three"]


def test_graph_view_dedupes_edge_ids_and_caps_nodes():
    from autoontology.ontology import model as merged
    from autoontology.web.graph import to_view

    model = merged.MergedOntology(name="g", base="x")
    for local, label in (("a", "A"), ("b", "B"), ("c", "C")):
        model.classes[local] = merged.OClass(
            local=local, label=label, source="document", documents_source="d"
        )
    model.axioms += [
        merged.OAxiom("a", "related", "b", "document"),
        merged.OAxiom("a", "related", "b", "document"),
    ]
    view = to_view(model)
    ids = [e["id"] for e in view["edges"]]
    assert len(ids) == 2 and len(set(ids)) == 2 and view["truncated"] is False
    capped = to_view(model, max_nodes=2)
    assert capped["truncated"] is True and capped["counts"]["total_nodes"] == 3
    assert {n["id"] for n in capped["nodes"]} == {"a", "b"}  # the connected ones
    assert all(e["source"] in {"a", "b"} and e["target"] in {"a", "b"} for e in capped["edges"])
    assert to_view(model)["edges"][0]["id"] == ids[0]  # the model is not mutated


def test_upload_checks_content_length_and_holds_the_source(env, monkeypatch):
    c, settings, _, manager = env
    monkeypatch.setenv("AUTOONTOLOGY_UPLOAD_MAX_MB", "1")
    read = []
    original = web_api._add_upload

    async def spy(*a, **k):
        read.append(1)
        return await original(*a, **k)

    monkeypatch.setattr(web_api, "_add_upload", spy)
    big = b"id\n" + b"1\n" * (1024 * 1024 + 10)
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", ("a.csv", big, "text/csv"))],
    )
    assert resp.status_code == 413 and read == []  # refused before the form was parsed
    manager.claim("shop", "upload")  # another upload of the same name in flight
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", ("a.csv", b"id\n1\n", "text/csv"))],
    )
    assert resp.status_code == 409 and resp.json()["error"] == "source_busy"
    manager.release("shop")
    resp = c.post(
        f"{API}/sources",
        data={"name": "shop", "kind": "files"},
        files=[("files", ("a.csv", b"id\n1\n", "text/csv"))],
    )
    assert resp.status_code == 201 and manager.active_source("shop") is None


@pytest.mark.parametrize(
    "header, host",
    [
        ("[::1]", "[::1]"),
        ("[::1]:7800", "[::1]"),
        ("localhost:7800", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("Evil.example:80", "evil.example"),
    ],
)
def test_host_name(header, host):
    assert web_api.host_name(header) == host


def test_bare_ipv6_host_is_local(env):
    c, *_ = env
    assert c.get(f"{API}/sources", headers={"host": "[::1]"}).status_code == 200


def test_run_log_handler_claims_only_its_pool_threads():
    from autoontology.unstructured.extractor import pool_thread_prefix
    from autoontology.web.runs import _RunLogHandler

    seen = {}
    t = threading.Thread(
        target=lambda: seen.update(prefix=pool_thread_prefix()), name="autoontology-run-7-extract"
    )
    t.start()
    t.join()
    assert seen["prefix"] == "autoontology-run-7-extract:extract"
    handler = _RunLogHandler("autoontology-run-7-extract")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
    for name, owned in (
        ("autoontology-run-7-extract", True),
        ("autoontology-run-7-extract:extract_0", True),
        ("autoontology-run-8-extract:extract_0", False),
        ("extract_0", False),
    ):
        rec.threadName = name
        assert handler.owns(rec) is owned, name


def test_sse_ends_for_a_run_whose_owner_died(env):
    c, settings, _, _ = env
    store = Store.for_home(settings.home)
    run = store.create_run(
        kind="ingest", source_id=None, params={"database": "nonexistent", "pid": 2**22 + 12345}
    )
    with c.stream("GET", f"{API}/runs/{run.id}/events") as resp:
        events = _sse("".join(resp.iter_text()))
    assert events[-1]["event"] == "end"
    assert json.loads(events[-1]["data"])["status"] == "failed"
    assert "is gone" in store.get_run(run.id).error


def test_service_start_sweeps_stale_runs(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    run = store.create_run(kind="ingest", source_id=None, params={"pid": 2**22 + 12345})
    web.create_app(settings)
    assert store.get_run(run.id).status == "failed"


def test_status_cache_misses_share_one_probe_round(isolated_env, monkeypatch):
    """Review M-7: concurrent /api/status misses wait for one probe instead of each
    paying the backend/Postgres/Ollama timeouts."""
    import time

    api = web_api.Api(Settings.from_env())
    calls = []

    def probe():
        calls.append(1)
        time.sleep(0.2)
        return {"status": "ok"}

    monkeypatch.setattr(api, "_probe_status", probe)
    results = []
    threads = [threading.Thread(target=lambda: results.append(api.status())) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and results == [{"status": "ok"}] * 5
