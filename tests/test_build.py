"""The unified build (``engine/build.py``), the snapshot store and the export from a
snapshot, with fake upstream/LLM backends."""

import json
import os
from pathlib import Path

import pytest

from autoontology.config import REPO_ROOT, Settings
from autoontology.engine import build as bj
from autoontology.engine import export_job, jobs
from autoontology.engine import extract as ex
from autoontology.engine.align import AlignBackends
from autoontology.engine.jobs import JobError
from autoontology.ontology.model import MergedOntology
from autoontology.store.db import Store
from autoontology.structured import pipeline
from autoontology.structured.loaders import LoadReport, TableReport
from autoontology.unstructured.schemas import MergeDecision
from tests.test_align import ClientWriter, FakeJudge
from tests.test_align import FakeClient as TermsClient
from tests.test_extract_job import FakeLLM as VocabLLM

ENGLISH = {"계약": "Contract", "고객": "Customer"}


class FakeLLM(VocabLLM):
    """The extraction fake of the extract tests, plus English names (identity evidence)."""

    def structured(self, system, human, schema):
        out = super().structured(system, human, schema)
        for concept in getattr(out, "concepts", []):
            concept.name_en = ENGLISH.get(concept.name, "")
        return out


STRUCTURED_STEPS = [
    "structured:hanbit:load",
    "structured:hanbit:register",
    "structured:hanbit:ingest",
    "structured:hanbit:compile",
]
DOCUMENT_STEPS = [
    "documents:hanbit-docs:read",
    "documents:hanbit-docs:extract",
    "documents:hanbit-docs:consolidate",
]


class Client(TermsClient):
    """Terms (from the align tests) plus the connection/export calls of the build."""

    def __init__(self):
        super().__init__()
        self.connections: list[str] = []
        self.exports: list[tuple] = []

    def semantic_graph(self, database_id=None):
        graph = super().semantic_graph(database_id)
        for node in graph["nodes"]:  # what compile's quality gate reads
            node["description"] = self.terms[node["id"]]["description"]
        return graph

    def connection_exists(self, name):
        return name in self.connections

    def create_connection(self, conn):
        self.connections.append(conn["database"])
        return conn

    def update_connection(self, name, conn):
        return conn

    def export_model(self, ids, fmt="auto_ontology", include_ontology=False):
        self.exports.append((tuple(ids), fmt, include_ontology))
        return f"{fmt}:{','.join(ids)}:{include_ontology}".encode()


VECTORS = {"Contract": [1.0, 0.0, 0.0], "Customer": [0.0, 1.0, 0.0]}
VECTORS |= {"계약": [0.95, 0.05, 0.0], "고객": [0.1, 0.9, 0.0]}


def align_embed(texts, input_type):
    return [VECTORS.get(t.split(":")[0].split(" (")[0], [0.0, 0.0, 1.0]) for t in texts]


@pytest.fixture
def env(isolated_env, monkeypatch, tmp_path):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    data = tmp_path / "files"
    data.mkdir()
    (data / "contracts.csv").write_text("x\n1\n")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "계약.md").write_text("# 계약규정\n\n계약은 고객과 맺는다.\n", encoding="utf-8")
    (docs_dir / "청구.md").write_text("# 청구규정\n\n청구서를 발행하고 수납을 확인한다.", "utf-8")
    client = Client()
    calls: list[str] = []

    def fake_load(files, pg_database, settings, **kw):
        calls.append(f"load:{pg_database}")
        return LoadReport(
            pg_database=pg_database,
            tables=[TableReport("contracts", str(files[0]), None, 1, [{"name": "x"}])],
        )

    def fake_ingest(db, settings):
        calls.append(f"ingest:{db}")
        return {"tables": 1}

    def fake_compile(db, settings):
        calls.append(f"compile:{db}")
        return {"tables_compiled": 1}

    monkeypatch.setattr("autoontology.structured.loaders.load_files_to_postgres", fake_load)
    monkeypatch.setattr(jobs, "upstream_ingest", fake_ingest)
    monkeypatch.setattr(jobs, "upstream_compile", fake_compile)
    monkeypatch.setattr(jobs, "ensure_vector_tables", lambda s: {"vector_tables": {}})
    monkeypatch.setattr(jobs, "catalog_snapshot", lambda db, s: {})
    monkeypatch.setattr(jobs, "service_alive", lambda s: True)
    return {
        "settings": settings,
        "store": store,
        "client": client,
        "calls": calls,
        "data": data,
        "docs": docs_dir,
    }


def add_structured(env):
    pipeline.add_source(env["store"], str(env["data"]), name="hanbit")


def add_documents(env):
    ex.ensure_documents_source(env["store"], "hanbit-docs", env["docs"])


def run(env, llm=None, **kw):
    llm = llm or FakeLLM()
    return bj.run_build(
        env["store"],
        env["settings"],
        client=env["client"],
        extract_backends=ex.Backends(
            env["settings"], llm=llm, judge_llm=llm, embedder=lambda t: [[1.0, 0.0]] * len(t)
        ),
        align_backends=AlignBackends(
            env["settings"], judge_llm=FakeJudge(), embedder=align_embed, model="fake-embed"
        ),
        writer=ClientWriter(env["client"]),
        **kw,
    )


def top_steps(store, run_id):
    return [s for s in store.get_steps(run_id) if bj.is_top_step(s.name)]


def test_build_runs_every_source_in_one_run_and_saves_a_snapshot(env):
    add_structured(env)
    add_documents(env)
    store = env["store"]
    result = run(env)
    assert result.status == "succeeded", result.problems
    steps = top_steps(store, result.run_id)
    assert [s.name for s in steps] == [
        *STRUCTURED_STEPS,
        *DOCUMENT_STEPS,
        "align:hanbit-docs",
        "merge",
        "snapshot",
    ]
    assert {s.status for s in steps} == {"succeeded"}
    assert len([r for r in store.list_runs(None) if r.kind == "build"]) == 1
    assert env["calls"] == [
        "load:autoontology_src_hanbit",
        "ingest:autoontology_src_hanbit",
        "compile:autoontology_src_hanbit",
    ]
    align = next(s for s in steps if s.name == "align:hanbit-docs").detail
    assert align["structured_sources"] == ["hanbit"]
    assert "계약 -> Contract" in align["accepted_same"]
    assert result.revision == 1
    snap = store.latest_snapshot()
    assert snap.revision == 1 and snap.run_id == result.run_id
    assert [(s["name"], s["kind"], s["included"]) for s in snap.sources] == [
        ("hanbit", "structured", True),
        ("hanbit-docs", "documents", True),
    ]
    model = MergedOntology.from_dict(snap.model)
    assert model.name == "default" and model.base == "http://example.com/autoontology#"
    assert model.counts()["classes_structured"] == 2
    folded = {m.concept_name: model.classes[m.term_local].label for m in model.merges}
    assert folded == {"계약": "Contract", "고객": "Customer"}
    assert model.aligned_terms() == {"term-t-contract", "term-t-customer"}
    assert snap.counts["merged_concepts"] == 2


def test_snapshot_round_trip_is_json_safe(env):
    add_structured(env)
    add_documents(env)
    run(env)
    snap = env["store"].latest_snapshot()
    model = MergedOntology.from_dict(snap.model)
    again = MergedOntology.from_dict(json.loads(json.dumps(model.to_dict())))
    assert again == model
    assert again.to_dict() == snap.model
    assert env["store"].get_snapshot(1).revision == 1
    assert env["store"].get_snapshot(2) is None
    assert env["store"].latest_snapshot(with_model=False).model is None


def test_source_restriction_still_aligns_merges_and_snapshots_everything(env):
    add_structured(env)
    add_documents(env)
    run(env)
    env["calls"].clear()
    result = run(env, sources=["hanbit-docs"])
    assert result.status == "succeeded"
    by_name = {s.name: s for s in top_steps(env["store"], result.run_id)}
    for name in STRUCTURED_STEPS:
        assert by_name[name].status == "skipped"
        assert by_name[name].detail["reason"] == "--source hanbit-docs"
    for name in [*DOCUMENT_STEPS, "align:hanbit-docs", "merge", "snapshot"]:
        assert by_name[name].status == "succeeded"
    assert env["calls"] == []  # no structured work
    assert result.revision == 2
    model = MergedOntology.from_dict(env["store"].latest_snapshot().model)
    assert model.structured_sources == ["hanbit"]  # still merged in
    with pytest.raises(JobError, match="unknown source 'nope'"):
        run(env, sources=["nope"])


def test_documents_only_build_skips_align(env):
    add_documents(env)
    result = run(env)
    assert result.status == "succeeded"
    by_name = {s.name: s for s in top_steps(env["store"], result.run_id)}
    assert by_name["align:hanbit-docs"].status == "skipped"
    assert by_name["align:hanbit-docs"].detail["skipped"].startswith("no built structured")
    assert not [n for n in by_name if n.startswith("structured:")]
    model = MergedOntology.from_dict(env["store"].latest_snapshot().model)
    assert model.structured_sources == [] and model.counts()["classes_document"] == 4
    assert model.aligned_terms() == set()


class Failing(FakeLLM):
    def structured(self, system, human, schema):
        raise ValueError("model unavailable")


def test_resume_keeps_unchanged_sources_and_continues_the_run(env):
    add_structured(env)
    add_documents(env)
    with pytest.raises(JobError, match="documents:hanbit-docs:extract"):
        run(env, llm=Failing())
    first = env["store"].list_runs(None)[0]
    assert first.status == "failed"
    assert env["calls"].count("ingest:autoontology_src_hanbit") == 1
    result = run(env, resume=True)
    assert result.status == "succeeded" and result.run_id == first.id
    # the structured source and the read step were kept: no second ingest
    assert env["calls"].count("ingest:autoontology_src_hanbit") == 1
    assert result.steps["structured:hanbit:ingest"] == "succeeded"
    assert "structured:hanbit:ingest" not in result.details
    assert "documents:hanbit-docs:read" not in result.details
    assert {"documents:hanbit-docs:extract", "merge", "snapshot"} <= set(result.details)
    # resuming a finished build changes nothing and writes no new snapshot
    again = run(env, resume=True)
    assert again.run_id == first.id and again.details == {}
    assert env["store"].latest_revision() == 1


def test_resume_of_a_succeeded_build_rebuilds_sources_whose_inputs_changed(env):
    add_structured(env)
    add_documents(env)
    first = run(env)
    assert first.status == "succeeded" and first.revision == 1
    # nothing changed: everything is kept, no new snapshot
    same = run(env, resume=True)
    assert same.run_id == first.run_id and same.details == {}
    assert env["store"].latest_revision() == 1
    # a document changed: that source's steps (and align/merge/snapshot) run again
    (env["docs"] / "청구.md").write_text("# 청구규정\n\n고객에게 청구서를 보낸다.", "utf-8")
    edited = run(env, resume=True)
    assert edited.status == "succeeded" and edited.run_id == first.run_id
    assert {*DOCUMENT_STEPS, "align:hanbit-docs", "merge", "snapshot"} <= set(edited.details)
    assert not any(n.startswith("structured:") for n in edited.details)
    assert edited.revision == 2 and env["store"].latest_revision() == 2
    read = edited.details["documents:hanbit-docs:read"]
    assert read["new_or_changed"] >= 1
    assert env["calls"].count("ingest:autoontology_src_hanbit") == 1
    # a structured input file changed: its steps run again
    (env["data"] / "contracts.csv").write_text("x\n1\n2\n")
    again = run(env, resume=True)
    assert "structured:hanbit:ingest" in again.details and again.revision == 3
    assert not any(n.startswith("documents:") for n in again.details)
    assert env["calls"].count("ingest:autoontology_src_hanbit") == 2
    assert run(env, resume=True).details == {}


def test_build_refuses_while_another_build_or_export_is_active(env):
    add_documents(env)
    store = env["store"]
    token = store.acquire_leases([bj.WORKSPACE_LEASE], "export")
    with pytest.raises(JobError, match="already has an active export"):
        run(env)
    store.release_lease(token)
    store.create_run(kind="export", source_id=None, params={"pid": os.getpid()})
    with pytest.raises(JobError, match="another export run .* is active"):
        run(env)


def test_build_reaps_a_dead_build_run(env):
    add_documents(env)
    store = env["store"]
    dead = store.create_run(kind="build", source_id=None, params={"pid": 2**22 + 12345})
    result = run(env)
    assert result.status == "succeeded"
    assert store.get_run(dead.id).status == "failed"


def test_step_names_and_partial_runs(env):
    add_structured(env)
    add_documents(env)
    assert bj.step_names(bj.registration_order(env["store"])) == [
        *STRUCTURED_STEPS,
        *DOCUMENT_STEPS,
        "align:hanbit-docs",
        "merge",
        "snapshot",
    ]

    class Flaky(FakeLLM):
        def structured(self, system, human, schema):
            if schema is not MergeDecision and "청구서" in human:
                raise ValueError("flaky chunk")
            return super().structured(system, human, schema)

    result = run(env, llm=Flaky())
    assert result.status == "failed"
    assert result.revision == 1  # the snapshot holds what succeeded
    assert any("chunk(s) failed" in p for p in result.problems)
    assert env["store"].get_run(result.run_id).error.startswith("partial:")


# --- export from a snapshot ---------------------------------------------------------------


def test_export_reads_the_snapshot_and_overwrites_its_folder(env):
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    with pytest.raises(JobError, match="no ontology snapshot"):
        export_job.run_export(store, settings, client=env["client"])
    run(env)
    env["client"].terms.clear()  # an export never reads the Terms again
    result = export_job.run_export(store, settings, client=env["client"])
    assert result.status == "succeeded"
    folder = settings.exports_dir / "r0001"
    assert result.export_dir == folder
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert names == [
        "export.json",
        "ontology.graphml",
        "ontology.jsonld",
        "ontology.ttl",
        "report.md",
        "upstream/hanbit.model.yaml",
        "upstream/hanbit.ossie.zip",
    ]
    marker = export_job.read_export_metadata(folder)
    assert marker["revision"] == 1 and marker["workspace"] == settings.workspace
    assert (folder / "upstream/hanbit.ossie.zip").read_bytes() == b"ossie:db1:True"
    ttl = (folder / "ontology.ttl").read_text(encoding="utf-8")
    assert "@prefix ao: <http://example.com/autoontology#>" in ttl
    assert "Revision: 1" in (folder / "report.md").read_text(encoding="utf-8")
    (folder / "stale.txt").write_text("old")
    again = export_job.run_export(store, settings, revision=1, client=env["client"])
    assert again.export_dir == folder and not (folder / "stale.txt").exists()
    with pytest.raises(JobError, match="unknown revision 7"):
        export_job.run_export(store, settings, revision=7, client=env["client"])
    busy = store.create_run(kind="build", source_id=None, params={"pid": os.getpid()})
    with pytest.raises(JobError, match="another build run .* is active"):
        export_job.run_export(store, settings, client=env["client"])
    store.update_run(busy.id, status="failed")
    out = settings.home / "elsewhere"
    target = out / "r0001"  # --out names the parent of the revision folder
    assert (
        export_job.run_export(store, settings, out=out, client=env["client"]).export_dir == target
    )
    assert (target / "ontology.ttl").is_file()
    runs = [r for r in store.list_runs(None) if r.kind == "export"]
    assert len(runs) == 3 and {r.params["revision"] for r in runs} == {1}
    assert env["client"].exports[:2] == [
        (("db1",), "auto_ontology", False),
        (("db1",), "ossie", True),
    ]


def test_export_out_never_deletes_foreign_files(env, tmp_path):
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    out = tmp_path / "desk"
    out.mkdir()
    (out / "sentinel.txt").write_text("keep me")  # beside the revision folder: untouched
    first = export_job.run_export(store, settings, out=out, client=env["client"])
    assert first.export_dir == out / "r0001" and (out / "sentinel.txt").read_text() == "keep me"
    # a revision folder holding only exporter files is replaced
    again = export_job.run_export(store, settings, out=out, client=env["client"])
    assert again.status == "succeeded" and (out / "r0001" / "ontology.ttl").is_file()
    # ... but one with a foreign file is refused, and nothing is removed
    (out / "r0001" / "notes.txt").write_text("mine")
    before = sorted(p.name for p in (out / "r0001").iterdir())
    with pytest.raises(JobError, match="refusing to replace .*notes.txt"):
        export_job.run_export(store, settings, out=out, client=env["client"])
    assert sorted(p.name for p in (out / "r0001").iterdir()) == before
    assert (out / "r0001" / "notes.txt").read_text() == "mine"
    assert not [p for p in out.iterdir() if p.name.startswith(".")]  # no temp folder left
    for bad in (Path.home(), REPO_ROOT, Path("/")):
        with pytest.raises(JobError, match="refusing to export into"):
            export_job.run_export(store, settings, out=bad, client=env["client"])
    assert (out / "sentinel.txt").read_text() == "keep me"


# --- CLI ----------------------------------------------------------------------------------


def test_cli_build_ontology_export_and_runs(env, monkeypatch):
    from typer.testing import CliRunner

    from autoontology.cli import app

    real_build, real_export = bj.run_build, export_job.run_export
    llm = FakeLLM()

    def fake_build(store, settings, **kw):
        return real_build(
            store,
            settings,
            client=env["client"],
            extract_backends=ex.Backends(
                settings, llm=llm, judge_llm=llm, embedder=lambda t: [[1.0, 0.0]] * len(t)
            ),
            align_backends=AlignBackends(
                settings, judge_llm=FakeJudge(), embedder=align_embed, model="fake-embed"
            ),
            writer=ClientWriter(env["client"]),
            **kw,
        )

    monkeypatch.setattr(bj, "run_build", fake_build)
    monkeypatch.setattr(
        export_job,
        "run_export",
        lambda store, settings, **kw: real_export(store, settings, client=env["client"], **kw),
    )
    runner = CliRunner()
    res = runner.invoke(app, ["ontology"])
    assert res.exit_code == 1 and "no ontology snapshot" in res.output
    assert runner.invoke(app, ["build"]).exit_code == 1  # no sources
    for args in (
        ["source", "add", str(env["data"]), "--name", "hanbit"],
        ["source", "add", str(env["docs"]), "--name", "hanbit-docs", "--kind", "documents"],
    ):
        assert runner.invoke(app, args).exit_code == 0
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 0, res.output
    assert "revision 1:" in res.output and "succeeded" in res.output
    res = runner.invoke(app, ["ontology"])
    assert res.exit_code == 0 and "revision 1" in res.output and "hanbit-docs" in res.output
    res = runner.invoke(app, ["export"])
    assert res.exit_code == 0, res.output
    assert "upstream/hanbit.ossie.zip" in res.output and "r0001" in res.output
    res = runner.invoke(app, ["runs"])
    assert "align:hanbit-docs=succeeded" in res.output and "chunks[succeeded=" in res.output
    assert "chunk:" not in res.output
    res = runner.invoke(app, ["build", "--source", "nope"])
    assert res.exit_code == 1 and "unknown source 'nope'" in res.output
