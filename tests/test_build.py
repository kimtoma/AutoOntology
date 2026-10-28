# SPDX-License-Identifier: MIT
"""The unified build (``engine/build.py``), the snapshot store and the export from a
snapshot, with fake upstream/LLM backends."""

import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

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
        if fmt == "ossie":
            return ossie_zip()
        return f"{fmt}:{','.join(ids)}:{include_ontology}".encode()


def _dataset(name, key, columns):
    return {
        "name": name,
        "source": f"autoontology_src_hanbit.public.{name}",
        "unique_keys": [[key]],
        "fields": [
            {
                "name": c,
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": c}]},
                "datatype": "String",
            }
            for c in columns
        ],
    }


def ossie_zip() -> bytes:
    """An upstream Ossie export of the fake catalog (the shape the backend returns)."""
    model = {
        "version": "0.2.0.dev0",
        "name": "autoontology_src_hanbit",
        "datasets": [
            _dataset("contracts", "계약id", ["계약id", "고객id"]),
            _dataset("customers", "고객id", ["고객id"]),
        ],
    }
    buf = io.BytesIO()
    stamp = (2026, 1, 1, 0, 0, 0)  # fixed: writestr(name) would stamp the current time
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in (
            ("semantic_model.yaml", yaml.safe_dump(model, allow_unicode=True)),
            ("ontology.yaml", "version: 0.2.0.dev0\n"),
            ("mapping.yaml", "version: 0.2.0.dev0\n"),
        ):
            zf.writestr(zipfile.ZipInfo(name, stamp), text)
    return buf.getvalue()


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


def run(env, llm=None, judge=None, **kw):
    llm = llm or FakeLLM()
    return bj.run_build(
        env["store"],
        env["settings"],
        client=env["client"],
        extract_backends=ex.Backends(
            env["settings"], llm=llm, judge_llm=llm, embedder=lambda t: [[1.0, 0.0]] * len(t)
        ),
        align_backends=AlignBackends(
            env["settings"],
            judge_llm=judge or FakeJudge(),
            embedder=align_embed,
            model="fake-embed",
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
    assert {m.documents_source for m in model.merges} == {"hanbit-docs"}
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
    with pytest.raises(ValueError, match="format 2 is newer"):
        MergedOntology.from_dict({**snap.model, "format": 2})
    legacy = {k: v for k, v in snap.model.items() if k != "format"}
    assert MergedOntology.from_dict(legacy) == model  # no format key: format 1


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


def test_resume_after_a_source_was_removed_merges_and_snapshots_again(env):
    """build --resume used to keep merge/snapshot when a source was removed and the
    remaining inputs were unchanged: the snapshot still held the removed source."""
    add_structured(env)
    add_documents(env)
    store = env["store"]
    first = run(env)
    assert first.status == "succeeded" and first.revision == 1
    store.delete_source(store.get_source("hanbit-docs").id)
    again = run(env, resume=True)
    assert again.run_id == first.run_id and again.status == "succeeded"
    assert "merge" in again.details and "snapshot" in again.details  # ran, not kept
    assert again.revision == 2
    snap = store.latest_snapshot()
    assert [s["name"] for s in snap.sources] == ["hanbit"]
    assert MergedOntology.from_dict(snap.model).documents_sources == []
    # the same set again: nothing global runs
    kept = run(env, resume=True)
    assert "merge" not in kept.details and kept.revision == 2


def test_resume_drops_the_problems_and_warnings_of_a_removed_source(env, monkeypatch):
    """Recheck #6: after the failing documents source is removed, the resumed build only
    reports the steps of its current plan: no stale chunk failure, no kordoc warning."""
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    (env["docs"] / "규정.hwp").write_bytes(b"x")
    add_structured(env)
    add_documents(env)

    class Flaky(FakeLLM):
        def structured(self, system, human, schema):
            if schema is not MergeDecision and "청구서" in human:
                raise ValueError("flaky chunk")
            return super().structured(system, human, schema)

    first = run(env, llm=Flaky())
    assert first.status == "failed" and first.problems and first.warnings
    store = env["store"]
    store.delete_source(store.get_source("hanbit-docs").id)
    again = run(env, resume=True)
    assert again.run_id == first.run_id
    assert again.problems == [] and again.warnings == []
    assert again.status == "succeeded"


def test_resumed_align_rejudges_when_the_candidate_term_changed(env):
    """Codex #7: the structured source changes and its Term old -> new id; the resumed
    align must judge the concept against the new candidate (judged=1), not keep the
    verdict for the Term that no longer exists."""
    add_structured(env)
    add_documents(env)
    store, client = env["store"], env["client"]
    first = run(env)
    assert first.status == "succeeded"
    docs_id = store.get_source("hanbit-docs").id
    from autoontology.store.alignments import AlignmentStore

    def accepted():
        return {
            a.concept_name: a.term_id for a in AlignmentStore(store).list(docs_id) if a.accepted
        }

    assert accepted()["계약"] == "t-contract"
    term = client.terms.pop("t-contract")
    client.terms["t-contract-new"] = {**term, "id": "t-contract-new"}
    client.attrs["t-contract-new"] = client.attrs.pop("t-contract")
    (env["data"] / "contracts.csv").write_text("x\n1\n2\n")  # the structured input changed

    class Renamed(FakeJudge):
        def structured(self, system, human, schema):
            verdict = super().structured(system, human, schema)
            if verdict.term_id == "t-contract":
                verdict.term_id = "t-contract-new"
            return verdict

    again = run(env, resume=True, judge=Renamed())
    assert again.run_id == first.run_id and again.status == "succeeded", again.problems
    judge = again.details["align:hanbit-docs"]
    assert judge["judged"] == 1 and judge["resumed"] >= 1
    assert accepted()["계약"] == "t-contract-new"
    assert "t-contract" not in accepted().values()


def test_align_rejudges_when_the_candidate_description_changed(env):
    """Recheck #2: the judge sees the candidate's description and synonyms, so a manual
    change of either re-judges the concept (judged=1) instead of reusing the cached
    verdict; what apply itself wrote (its synonyms) does not."""
    add_structured(env)
    add_documents(env)
    client = env["client"]
    assert run(env).status == "succeeded"

    def judged():
        result = run(env)
        assert result.status == "succeeded", result.problems
        return result.details["align:hanbit-docs"]["judged"]

    assert judged() == 0  # only apply's own synonyms changed since: the cache holds
    client.terms["t-contract"]["description"] = "판매 계약을 제외한 고용 계약"
    assert judged() == 1
    assert judged() == 0
    client.terms["t-contract"]["synonyms"] = [
        *client.terms["t-contract"].get("synonyms", []),
        "고용 약정",
    ]
    assert judged() == 1


def _forget_description_texts(store):
    """Turn the recorded description writes into pre-fix records (no ``description``)."""
    import json as _json

    with store._conn() as conn:
        rows = conn.execute(
            "SELECT id, applied_json FROM alignments WHERE applied_json LIKE '%description_set%'"
        ).fetchall()
        for row_id, raw in rows:
            applied = _json.loads(raw)
            applied.pop("description", None)
            conn.execute(
                "UPDATE alignments SET applied_json = ? WHERE id = ?",
                (_json.dumps(applied), row_id),
            )
    return len(rows)


def test_legacy_description_record_is_not_a_permanent_wildcard(env):
    """Round 3 #2: a pre-fix ``description_set`` record without the text used to hide every
    later description change. The first re-judge records the description it saw as the
    baseline, so the next change re-judges (judged=1, not resumed)."""
    add_structured(env)
    add_documents(env)
    client, store = env["client"], env["store"]
    assert run(env).status == "succeeded"
    assert _forget_description_texts(store) >= 1

    def judged():
        result = run(env)
        assert result.status == "succeeded", result.problems
        return result.details["align:hanbit-docs"]["judged"]

    term = client.terms["t-customer"]
    term["synonyms"] = [*term.get("synonyms", []), "거래처"]
    assert judged() == 1  # the first re-judge after the upgrade
    term["description"] = "판매 고객이 아닌 고용 상대방"
    assert judged() == 1
    assert judged() == 0
    # the align-step Terms fingerprint uses the same baseline: --resume runs align again
    first = run(env)
    term["description"] = "고객사의 담당자"
    again = run(env, resume=True)
    assert again.run_id == first.run_id and again.status == "succeeded", again.problems
    assert again.details["align:hanbit-docs"]["judged"] == 1


def test_resume_right_after_the_upgrade_rejudges_a_legacy_description(env):
    """Round 4 #1: with an unconfirmed legacy ``description_set`` record (no text) and no
    other change, ``--resume`` must not reuse the align step and the concept must be
    judged against the current description before it becomes the baseline; a later
    description change re-judges again."""
    add_structured(env)
    add_documents(env)
    client, store = env["client"], env["store"]
    first = run(env)
    assert first.status == "succeeded"
    assert _forget_description_texts(store) >= 1
    upgraded = run(env, resume=True)
    assert upgraded.run_id == first.run_id and upgraded.status == "succeeded"
    assert upgraded.details["align:hanbit-docs"]["judged"] >= 1
    assert "align:hanbit-docs" not in run(env, resume=True).details  # settled now
    client.terms["t-customer"]["description"] = "판매 고객이 아닌 고용 상대방"
    again = run(env, resume=True)
    assert again.details["align:hanbit-docs"]["judged"] >= 1


def test_upgrade_rejudges_every_documents_source_of_a_legacy_term(env, tmp_path):
    """Round 5: two documents sources share a Term with an unconfirmed legacy description
    record. The first source's judge call confirms the baseline, but the second source's
    stored verdict was never re-validated: after the upgrade `--resume` both are judged,
    and a further resume with no change reuses both."""
    add_structured(env)
    add_documents(env)
    other = tmp_path / "docs-b"
    shutil.copytree(env["docs"], other)
    ex.ensure_documents_source(env["store"], "b-docs", other)
    store = env["store"]
    first = run(env)
    assert first.status == "succeeded", first.problems
    assert _forget_description_texts(store) >= 1
    upgraded = run(env, resume=True)
    assert upgraded.run_id == first.run_id and upgraded.status == "succeeded"
    for name in ("align:hanbit-docs", "align:b-docs"):
        assert upgraded.details[name]["judged"] >= 1, name
    kept = run(env, resume=True)
    assert "align:hanbit-docs" not in kept.details and "align:b-docs" not in kept.details


def test_upgrade_rejudges_the_second_source_after_the_first_rewrites_the_description(env, tmp_path):
    """Round 6: the first documents source's judge call confirms the legacy baseline and
    its apply rewrites the (fallback again) Term description, replacing that record's
    ``applied``; the invalidation must survive it, so the second source is still judged,
    and a further resume with no change reuses both."""
    add_structured(env)
    add_documents(env)
    other = tmp_path / "docs-b"
    shutil.copytree(env["docs"], other)
    ex.ensure_documents_source(env["store"], "b-docs", other)
    store, client = env["store"], env["client"]
    first = run(env)
    assert first.status == "succeeded", first.problems
    assert _forget_description_texts(store) >= 1
    client.terms["t-customer"]["description"] = ""  # a fallback again: apply rewrites it
    upgraded = run(env, resume=True)
    assert upgraded.status == "succeeded", upgraded.problems
    assert client.terms["t-customer"]["description"]  # the first source's apply wrote it
    for name in ("align:hanbit-docs", "align:b-docs"):
        assert upgraded.details[name]["judged"] >= 1, name
    kept = run(env, resume=True)
    assert "align:hanbit-docs" not in kept.details and "align:b-docs" not in kept.details


def test_resume_runs_align_again_when_an_upstream_term_changed(env):
    """Follow-up of recheck #2: with unchanged inputs `--resume` used to keep the whole
    align step, so an edited upstream Term was never re-judged. The step records a
    fingerprint of the Terms (apply's own writes left out) and runs again when it moved."""
    add_structured(env)
    add_documents(env)
    client = env["client"]
    first = run(env)
    assert first.status == "succeeded"
    kept = run(env, resume=True)
    assert "align:hanbit-docs" not in kept.details  # nothing changed, apply's writes included
    client.terms["t-contract"]["description"] = "판매 계약을 제외한 고용 계약"
    again = run(env, resume=True)
    assert again.run_id == first.run_id and again.status == "succeeded", again.problems
    assert again.details["align:hanbit-docs"]["judged"] == 1
    assert "merge" in again.details and "snapshot" in again.details
    assert "align:hanbit-docs" not in run(env, resume=True).details


def test_build_records_the_documents_source_on_the_aliases_it_adds(env):
    add_structured(env)
    add_documents(env)
    store = env["store"]
    assert run(env).status == "succeeded"
    from autoontology.store.alignments import AlignmentStore

    prov = AlignmentStore(store).alias_provenance()
    sources = {r["source_id"] for records in prov.values() for r in records}
    assert prov and sources == {store.get_source("hanbit-docs").id}


def test_documents_that_need_kordoc_are_skipped_not_failed(env, monkeypatch):
    """Without kordoc the canonical quickstart must still exit 0: the HWP/PDF files are
    skipped with a warning naming them and the install command, and once kordoc is
    available a --resume reads them (kordoc is part of the input fingerprint)."""
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    (env["docs"] / "규정.hwp").write_bytes(b"x")
    add_documents(env)
    result = run(env)
    assert result.status == "succeeded", result.problems
    assert result.problems == []
    read = result.details["documents:hanbit-docs:read"]
    assert read["failed"] == [] and [f["code"] for f in read["needs_kordoc"]] == ["ENGINE_MISSING"]
    assert len(result.warnings) == 1
    assert "규정.hwp" in result.warnings[0] and "tools install-kordoc" in result.warnings[0]
    assert env["store"].get_run(result.run_id).status == "succeeded"
    # nothing changed: a resume keeps the read
    again = run(env, resume=True)
    assert "documents:hanbit-docs:read" not in again.details
    # kordoc becomes available: the documents source is read again
    monkeypatch.setattr(bj, "kordoc_state", lambda home: "/opt/kordoc/cli.js:1:node")
    after = run(env, resume=True)
    assert "documents:hanbit-docs:read" in after.details


def test_documents_source_that_only_needs_kordoc_reads_zero_documents(env, monkeypatch):
    """When every document of a source needs kordoc and kordoc is absent, the read step
    succeeds with 0 documents and the same warning (files + install command)."""
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    for md in list(env["docs"].iterdir()):
        md.unlink()
    (env["docs"] / "규정.hwp").write_bytes(b"x")
    (env["docs"] / "지침.pdf").write_bytes(b"%PDF-1.4")
    add_documents(env)
    result = run(env)
    assert result.status == "succeeded", result.problems
    assert result.problems == []
    read = result.details["documents:hanbit-docs:read"]
    assert read["documents"] == [] and read["failed"] == []
    assert sorted(f["code"] for f in read["needs_kordoc"]) == ["ENGINE_MISSING"] * 2
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "2 document(s) need kordoc" in warning
    assert "규정.hwp" in warning and "지침.pdf" in warning
    assert "tools install-kordoc" in warning


def test_cli_build_prints_the_kordoc_warning_and_exits_0(env, monkeypatch):
    from typer.testing import CliRunner

    from autoontology.cli import app

    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    (env["docs"] / "규정.hwp").write_bytes(b"x")
    add_documents(env)

    real = bj.run_build
    result = run(env)  # with the fake backends, then reported by the CLI
    monkeypatch.setattr(bj, "run_build", lambda *a, **k: result)
    assert real is not bj.run_build
    res = CliRunner().invoke(app, ["build"])
    assert res.exit_code == 0, res.output
    assert "need kordoc" in res.output and "tools install-kordoc" in res.output


def test_database_source_fingerprint_follows_the_live_schema(env, monkeypatch, tmp_path):
    """A DB-URL source's input fingerprint includes its schema (here read from a sqlite
    file standing in for the external database); unreachable means never reused."""
    import sqlite3

    db = tmp_path / "ext.sqlite"
    sqlite3.connect(db).executescript("CREATE TABLE customers (id INTEGER, name TEXT);")

    def sqlite_columns(conn):
        with sqlite3.connect(db) as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return [
                ("main", t, col[1], col[2])
                for t in tables
                for col in c.execute(f"PRAGMA table_info({t})")
            ]

    monkeypatch.setitem(pipeline.SCHEMA_INTROSPECTORS, "postgres", sqlite_columns)
    src = pipeline.add_source(env["store"], "postgres://u:p@db.example/crm", name="crm")
    first = bj.input_fingerprint(src)
    assert first and bj.input_fingerprint(src) == first
    with sqlite3.connect(db) as c:
        c.execute("ALTER TABLE customers ADD COLUMN tier TEXT")
    changed = bj.input_fingerprint(src)
    assert changed and changed != first
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE orders (id INTEGER)")
    assert bj.input_fingerprint(src) not in ("", changed)

    def unreachable(conn):
        raise OSError("connection refused")

    monkeypatch.setitem(pipeline.SCHEMA_INTROSPECTORS, "postgres", unreachable)
    assert bj.input_fingerprint(src) == ""  # never equal to a recorded fingerprint
    mysql = pipeline.add_source(env["store"], "mysql://u:p@db.example/shop", name="shop")
    assert bj.input_fingerprint(mysql) == ""  # no introspector: not reused either


def test_database_fingerprint_follows_foreign_keys(env, monkeypatch, tmp_path):
    """Recheck #3: the semantic step reads declared FKs (and PKs), so adding a foreign key
    changes the fingerprint; the sqlite introspector stands in for the external DB."""
    import sqlite3

    db = tmp_path / "ext.sqlite"
    sqlite3.connect(db).executescript(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER);"
    )

    def sqlite_schema(conn):
        with sqlite3.connect(db) as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            rows = []
            for t in tables:
                for col in c.execute(f"PRAGMA table_info({t})"):
                    rows.append(("column", "main", t, col[1], col[2]))
                    if col[5]:
                        rows.append(("pk", "main", t, col[1], str(col[5])))
                for fk in c.execute(f"PRAGMA foreign_key_list({t})"):
                    rows.append(("fk", f"fk{fk[0]}", "main", t, fk[3], "main", fk[2], fk[4]))
            return rows

    monkeypatch.setitem(pipeline.SCHEMA_INTROSPECTORS, "postgres", sqlite_schema)
    src = pipeline.add_source(env["store"], "postgres://u:p@db.example/crm", name="crm")
    first = bj.input_fingerprint(src)
    assert first
    with sqlite3.connect(db) as c:
        c.executescript(
            "DROP TABLE orders; CREATE TABLE orders (id INTEGER, customer_id INTEGER "
            "REFERENCES customers(id));"
        )
    assert bj.input_fingerprint(src) not in ("", first)


def test_postgres_introspector_reads_keys_under_a_statement_timeout(monkeypatch):
    """Recheck #3: the real Postgres introspector reads PKs and FKs (constraint name,
    columns, referenced table/columns) besides columns, and bounds every metadata query
    with a statement timeout, not only the connect."""
    import sys
    import types

    seen: dict = {"sql": []}

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, args=()):
            seen["sql"].append(sql)
            if "pg_attribute" in sql:
                return Result([(10, 2, "customer_id"), (20, 1, "id")])
            if "pg_constraint" in sql:
                return Result(
                    [
                        (10, 20, "public", "fk_orders_customer", "public", "orders")
                        + ("public", "customers", [2], [1])
                    ]
                )
            if "PRIMARY KEY" in sql:
                return Result([("public", "customers", "id", 1)])
            return Result([("public", "orders", "customer_id", "integer")])

    def connect(**kwargs):
        seen["kwargs"] = kwargs
        return Conn()

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    rows = pipeline.SCHEMA_INTROSPECTORS["postgres"]({"host": "db", "user": "u", "database": "crm"})
    assert "statement_timeout" in seen["kwargs"].get("options", "")
    assert seen["kwargs"]["connect_timeout"] == pipeline.SCHEMA_CONNECT_TIMEOUT_S
    kinds = {r[0] for r in rows}
    assert kinds == {"column", "pk", "fk"}
    fk = next(r for r in rows if r[0] == "fk")
    assert fk == (
        "fk",
        "public",
        "fk_orders_customer",
        "public",
        "orders",
        "customer_id",
        "public",
        "customers",
        "id",
        "1",
    )


def test_postgres_fk_fingerprint_follows_cross_schema_and_composite_pairs(env, monkeypatch):
    """Round 3 #3: the FK rows come from ``pg_constraint`` (referenced table resolved by
    ``confrelid``, so ``sales.orders -> crm.customers`` is seen) and ``conkey``/``confkey``
    are paired position by position through ``pg_attribute``, so pointing the FK at
    another schema or swapping which column references which changes the fingerprint."""
    import sys
    import types

    rels = {10: ("sales", "orders"), 20: ("crm", "customers"), 30: ("sales", "customers")}
    attrs = {
        10: {1: "id", 2: "customer_id", 3: "region"},
        20: {1: "id", 2: "region"},
        30: {1: "id", 2: "region"},
    }
    fk = {"confrelid": 30, "conkey": [2, 3], "confkey": [1, 2]}

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, args=()):
            if "pg_attribute" in sql:
                return Result(
                    [(rel, n, name) for rel, cols in attrs.items() for n, name in cols.items()]
                )
            if "pg_constraint" in sql:
                assert "contype = 'f'" in sql
                ref = rels[fk["confrelid"]]
                return Result(
                    [
                        (10, fk["confrelid"], "sales", "fk_orders_customer", "sales", "orders")
                        + ref
                        + (list(fk["conkey"]), list(fk["confkey"]))
                    ]
                )
            if "PRIMARY KEY" in sql:
                return Result([])
            return Result([("sales", "orders", "id", "integer")])

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda **kw: Conn()))
    src = pipeline.add_source(env["store"], "postgres://u:p@db.example/crm", name="crm")
    rows = pipeline.SCHEMA_INTROSPECTORS["postgres"]({"host": "db", "user": "u", "database": "crm"})
    fks = sorted(r for r in rows if r[0] == "fk")
    assert [(r[5], r[8]) for r in fks] == [("customer_id", "id"), ("region", "region")]
    first = bj.input_fingerprint(src)
    assert first
    fk["confrelid"] = 20  # sales.orders -> crm.customers: another schema, same names
    cross = bj.input_fingerprint(src)
    assert cross not in ("", first)
    fk["confkey"] = [2, 1]  # customer_id -> region, region -> id: the pairing changed
    assert bj.input_fingerprint(src) not in ("", first, cross)


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
    assert [n for n in names if not n.startswith("okf/")] == [
        "export.json",
        "ontology.graphml",
        "ontology.jsonld",
        "ontology.ttl",
        "ossie/mapping.yaml",
        "ossie/ontology.yaml",
        "ossie/semantic_model.yaml",
        "ossie/validation.json",
        "report.md",
        "upstream/hanbit.model.yaml",
        "upstream/hanbit.ossie.zip",
    ]
    assert {"okf/index.md", "okf/log.md", "okf/validation.json"} <= set(names)
    assert any(n.startswith("okf/concepts/") for n in names)
    assert any(n.startswith("okf/tables/hanbit.") for n in names)
    checks = json.loads((folder / "ossie/validation.json").read_text(encoding="utf-8"))
    assert [checks[k] for k in ("semantic_model", "ontology", "mapping", "roundtrip")] == ["ok"] * 4
    assert json.loads((folder / "okf/validation.json").read_text("utf-8"))["ok"] is True
    marker = export_job.read_export_metadata(folder)
    assert marker["revision"] == 1 and marker["workspace"] == settings.workspace
    assert marker["format"] == "all"
    assert (folder / "upstream/hanbit.ossie.zip").read_bytes() == ossie_zip()
    assert "ossie/ontology.yaml" in (folder / "report.md").read_text(encoding="utf-8")
    ttl = (folder / "ontology.ttl").read_text(encoding="utf-8")
    assert "@prefix ao: <http://example.com/autoontology#>" in ttl
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "Revision: 1" in report
    assert "upstream/ files reflect the backend state at export time, not at build time" in report
    assert marker["upstream_fetched_at"] and marker["upstream_fetched_at"] in report
    again = export_job.run_export(store, settings, revision=1, client=env["client"])
    assert again.export_dir == folder and again.status == "succeeded"
    assert sorted(marker["files"]) == sorted(
        p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
    )
    assert not [p for p in folder.parent.iterdir() if ".prev-" in p.name]  # backup removed
    # even under the exports root, a file the exporter did not write is never deleted
    (folder / "stale.txt").write_text("old")
    with pytest.raises(JobError, match="refusing to replace .*stale.txt"):
        export_job.run_export(store, settings, revision=1, client=env["client"])
    assert (folder / "stale.txt").read_text() == "old"
    (folder / "stale.txt").unlink()
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
    # exporter names alone are not enough: without export.json the folder is not ours
    own = tmp_path / "work"
    (own / "r0001" / "okf").mkdir(parents=True)
    (own / "r0001" / "okf" / "sentinel.md").write_text("my notes")
    (own / "r0001" / "report.md").write_text("my report")
    with pytest.raises(JobError, match="has no export.json"):
        export_job.run_export(store, settings, out=own, client=env["client"])
    assert (own / "r0001" / "okf" / "sentinel.md").read_text() == "my notes"
    assert (own / "r0001" / "report.md").read_text() == "my report"
    (own / "empty" / "r0001").mkdir(parents=True)  # an empty folder is fine
    assert export_job.run_export(store, settings, out=own / "empty", client=env["client"]).status
    for bad in (Path.home(), REPO_ROOT, Path("/")):
        with pytest.raises(JobError, match="refusing to export into"):
            export_job.run_export(store, settings, out=bad, client=env["client"])
    assert (out / "sentinel.txt").read_text() == "keep me"


def test_export_out_refuses_user_files_inside_exporter_folders(env, tmp_path):
    """export.json lists every file the exporter wrote; a user file anywhere under the
    revision folder (here inside okf/) makes a re-export refuse, deleting nothing."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    out = tmp_path / "desk"
    folder = export_job.run_export(store, settings, out=out, client=env["client"]).export_dir
    listed = export_job.read_export_metadata(folder)["files"]
    assert "okf/index.md" in listed and "ossie/mapping.yaml" in listed
    assert "upstream/hanbit.ossie.zip" in listed and "export.json" in listed
    for user_file in ("okf/personal-notes.md", "upstream/mine/a.txt", "ossie/x.yaml"):
        target = folder / user_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("mine")
        before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
        with pytest.raises(JobError, match=f"refusing to replace .*{user_file}"):
            export_job.run_export(store, settings, out=out, client=env["client"])
        assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == before
        assert target.read_text() == "mine"
        target.unlink()
        if target.parent.name == "mine":
            target.parent.rmdir()
    # an edited marker cannot widen what may be removed
    marker = export_job.read_export_metadata(folder)
    (folder / "export.json").write_text(
        json.dumps({**marker, "files": [*marker["files"], "../escape"]}), encoding="utf-8"
    )
    with pytest.raises(JobError, match="does not list the files it wrote"):
        export_job.run_export(store, settings, out=out, client=env["client"])
    (folder / "export.json").write_text(json.dumps(marker), encoding="utf-8")
    assert export_job.run_export(store, settings, out=out, client=env["client"]).status == (
        "succeeded"
    )


def test_export_refuses_a_pre_manifest_folder_in_the_exports_root(env):
    """A revision folder under the exports root written before export.json listed its
    files cannot be replaced; the error names the folder and says to move or delete it."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    folder = export_job.run_export(store, settings, client=env["client"]).export_dir
    assert folder.parent == settings.exports_dir
    marker = export_job.read_export_metadata(folder)
    old = {k: v for k, v in marker.items() if k != "files"}  # what an older exporter wrote
    (folder / "export.json").write_text(json.dumps(old), encoding="utf-8")
    before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
    with pytest.raises(JobError) as err:
        export_job.run_export(store, settings, client=env["client"])
    message = str(err.value)
    assert str(folder) in message
    assert "move or delete the folder" in message
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == before
    shutil.rmtree(folder)  # once moved/deleted, the export goes through
    assert export_job.run_export(store, settings, client=env["client"]).status == "succeeded"


def test_export_swap_failure_restores_the_previous_folder(env, monkeypatch):
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    folder = export_job.run_export(store, settings, client=env["client"]).export_dir
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    real_rename = Path.rename

    def failing_rename(self, target):
        if self.name.startswith(".r0001.") and self.name.endswith(".tmp"):
            raise OSError("injected rename failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(JobError, match="injected rename failure") as info:
        export_job.run_export(store, settings, client=env["client"])
    failed = store.get_run(info.value.run_id)
    assert failed.status == "failed" and "injected rename failure" in failed.error
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    leftovers = [p.name for p in folder.parent.iterdir() if p.name != "r0001"]
    assert leftovers == []  # backup restored, work folder removed
    monkeypatch.setattr(Path, "rename", real_rename)
    ok = export_job.run_export(store, settings, client=env["client"])
    assert store.get_run(ok.run_id).status == "succeeded"


def test_export_format_keeps_the_other_formats_of_the_revision(env, tmp_path):
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    client = env["client"]
    full = export_job.run_export(store, settings, client=client)
    folder = full.export_dir
    ttl = (folder / "ontology.ttl").read_bytes()
    okf_index = (folder / "okf/index.md").read_bytes()
    first = export_job.read_export_metadata(folder)
    assert sorted(first["formats"]) == ["okf", "ossie", "rdf"]
    again = export_job.run_export(store, settings, format="ossie", client=client)
    assert again.export_dir == folder
    assert sorted(p.name for p in folder.iterdir()) == sorted(export_job.EXPORT_ENTRIES)
    assert (folder / "ontology.ttl").read_bytes() == ttl
    assert (folder / "okf/index.md").read_bytes() == okf_index
    marker = export_job.read_export_metadata(folder)
    assert marker["format"] == "ossie"
    assert marker["formats"]["ossie"]["export_run"] == again.run_id
    assert marker["formats"]["rdf"] == marker["formats"]["okf"] == first["formats"]["rdf"]
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "ontology.ttl" in report and "okf/index.md" in report
    # okf alone keeps the backend files too, and their fetch time
    export_job.run_export(store, settings, format="okf", client=client)
    assert (folder / "upstream/hanbit.ossie.zip").is_file()
    assert (folder / "ontology.ttl").read_bytes() == ttl
    assert (
        export_job.read_export_metadata(folder)["upstream_fetched_at"]
        == (marker["upstream_fetched_at"])
    )
    # a folder of another revision's export is not carried over (the current marker: an
    # older one records the hashes of files rewritten since)
    current = export_job.read_export_metadata(folder)
    (folder / "export.json").write_text(json.dumps({**current, "revision": 9}), encoding="utf-8")
    export_job.run_export(store, settings, format="okf", client=client)
    assert sorted(p.name for p in folder.iterdir()) == ["export.json", "okf", "report.md"]


def test_failed_validation_keeps_the_record_and_the_previous_folder(env, monkeypatch):
    from autoontology.export import okf

    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    folder = export_job.run_export(store, settings, client=env["client"]).export_dir
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    monkeypatch.setattr(okf, "CONCEPT_TYPE", "")  # every concept file is now non-conformant
    with pytest.raises(JobError, match=r"step okf failed: OkfError: \[full record: ") as info:
        export_job.run_export(store, settings, client=env["client"])
    failed = store.get_run(info.value.run_id)
    assert failed.status == "failed" and failed.error.startswith("okf: OkfError")
    kept = settings.logs_dir / f"{failed.id}.okf-validation.json"
    assert str(kept) in str(info.value)
    record = json.loads(kept.read_text(encoding="utf-8"))
    assert record["ok"] is False and record["errors"]
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    assert not [p for p in folder.parent.iterdir() if p.name.startswith(".")]


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
    assert "ossie: semantic_model ok, roundtrip ok, ontology ok, mapping ok" in res.output
    assert "okf: ok (" in res.output and "okf/concepts/" not in res.output  # one summary line
    res = runner.invoke(app, ["export", "--format", "rdf"])
    assert res.exit_code == 0 and "ossie/" not in res.output and "ontology.ttl" in res.output
    assert "kept from the previous export of this revision: okf" in res.output
    res = runner.invoke(app, ["runs"])
    assert "align:hanbit-docs=succeeded" in res.output and "chunks[succeeded=" in res.output
    assert "chunk:" not in res.output
    res = runner.invoke(app, ["build", "--source", "nope"])
    assert res.exit_code == 1 and "unknown source 'nope'" in res.output


def test_export_format_writes_only_the_chosen_formats(env):
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    client = env["client"]
    client.exports.clear()
    client.health = lambda: (_ for _ in ()).throw(AssertionError("okf needs no backend"))
    result = export_job.run_export(store, settings, format="okf", client=client)
    folder = result.export_dir
    top = sorted(p.name for p in folder.iterdir())
    assert top == ["export.json", "okf", "report.md"] and client.exports == []
    assert result.steps["serialize"] == result.steps["upstream"] == "skipped"
    assert result.steps["ossie"] == "skipped" and result.steps["okf"] == "succeeded"
    assert export_job.read_export_metadata(folder)["format"] == "okf"
    del client.health  # back to the class method
    result = export_job.run_export(store, settings, format="ossie", client=client)
    assert sorted(p.name for p in folder.iterdir()) == [
        "export.json",
        "okf",  # kept from the okf export of this revision
        "ossie",
        "report.md",
        "upstream",
    ]
    assert sorted(export_job.read_export_metadata(folder)["formats"]) == ["okf", "ossie"]
    with pytest.raises(JobError, match="unknown export format 'pdf'"):
        export_job.run_export(store, settings, format="pdf", client=client)


def test_export_backup_cleanup_failure_still_succeeds(env, monkeypatch, caplog):
    """Recheck #5: the new folder is installed; failing to delete the `.prev-*` backup is a
    warning naming the backup, and the run is still finalized as succeeded."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    export_job.run_export(store, settings, client=env["client"])
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **k):
        if ".prev-" in str(self):
            raise PermissionError("injected unlink failure")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with caplog.at_level("WARNING", logger="autoontology.jobs"):
        result = export_job.run_export(store, settings, client=env["client"])
    assert result.status == "succeeded"
    assert store.get_run(result.run_id).status == "succeeded"
    assert (result.export_dir / "ontology.ttl").is_file()
    [backup] = [p for p in result.export_dir.parent.iterdir() if ".prev-" in p.name]
    assert str(backup) in caplog.text


def test_export_restores_a_backup_left_between_the_two_renames(env):
    """Recheck #5: a crash after `final -> .prev-*` but before `work -> final` leaves the
    previous export only in the backup; the next export restores it before replacing it."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    folder = export_job.run_export(store, settings, client=env["client"]).export_dir
    backup = folder.with_name(f"{folder.name}.prev-20260101T000000-crashed")
    folder.rename(backup)
    folder.mkdir()  # an empty (or missing) final folder
    (folder / "user.txt").write_text("mine")
    with pytest.raises(JobError, match="refusing to replace"):  # a user's folder: kept
        export_job.run_export(store, settings, client=env["client"])
    assert backup.is_dir()
    (folder / "user.txt").unlink()
    export_job.recover_interrupted_install(folder)
    assert (folder / "export.json").is_file() and not backup.exists()
    shutil.rmtree(folder)  # the final folder missing altogether: restored by the export
    export_job.run_export(store, settings, client=env["client"])
    backup2 = folder.with_name(f"{folder.name}.prev-20260102T000000-crashed")
    folder.rename(backup2)
    ok = export_job.run_export(store, settings, client=env["client"])
    assert ok.status == "succeeded" and (folder / "ontology.ttl").is_file()
    assert not [p for p in folder.parent.iterdir() if ".prev-" in p.name]


def test_export_refuses_a_listed_file_whose_content_changed(env, tmp_path):
    """Recheck #10: export.json records a sha256 per file it wrote; a listed path whose
    content no longer matches (the user reused or edited it), or a listed path with no
    recorded hash (an edited list), is a user file: the re-export refuses, naming it."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    out = tmp_path / "desk"
    folder = export_job.run_export(store, settings, out=out, client=env["client"]).export_dir
    marker = export_job.read_export_metadata(folder)
    hashes = marker["file_sha256"]
    assert set(hashes) == set(marker["files"]) - {"export.json"}
    index = folder / "okf" / "index.md"
    original = index.read_bytes()
    index.write_text("my own notes now")
    with pytest.raises(JobError, match="refusing to replace .*okf/index.md"):
        export_job.run_export(store, settings, out=out, client=env["client"])
    assert index.read_text() == "my own notes now"
    index.write_bytes(original)
    notes = folder / "okf" / "personal-notes.md"
    notes.write_text("mine")
    (folder / "export.json").write_text(
        json.dumps({**marker, "files": sorted([*marker["files"], "okf/personal-notes.md"])}),
        encoding="utf-8",
    )
    with pytest.raises(JobError, match="refusing to replace .*okf/personal-notes.md"):
        export_job.run_export(store, settings, out=out, client=env["client"])
    assert notes.read_text() == "mine"
    notes.unlink()
    (folder / "export.json").write_text(json.dumps(marker), encoding="utf-8")
    assert export_job.run_export(store, settings, out=out, client=env["client"]).status == (
        "succeeded"
    )


def test_export_refuses_a_marker_without_file_hashes(env, tmp_path):
    """Round 3 #4: a marker with no ``file_sha256`` (an exporter before hashes, or the
    field removed) can no longer vouch for the content, so an edited ``okf/index.md``
    would be deleted: the re-export refuses, naming the folder and the migration step;
    a complete marker is accepted again."""
    add_structured(env)
    add_documents(env)
    store, settings = env["store"], env["settings"]
    run(env)
    out = tmp_path / "desk"
    folder = export_job.run_export(store, settings, out=out, client=env["client"]).export_dir
    marker = export_job.read_export_metadata(folder)
    index = folder / "okf" / "index.md"
    index.write_text("my own notes now")
    legacy = {k: v for k, v in marker.items() if k != "file_sha256"}
    for meta in (legacy, {**legacy, "file_sha256": None}):
        (folder / "export.json").write_text(json.dumps(meta), encoding="utf-8")
        with pytest.raises(JobError) as err:
            export_job.run_export(store, settings, out=out, client=env["client"])
        assert str(folder) in str(err.value) and "file_sha256" in str(err.value)
        assert "move or delete the folder" in str(err.value)
        assert index.read_text() == "my own notes now"
    shutil.rmtree(folder)  # the migration step: a fresh export writes a complete marker
    folder = export_job.run_export(store, settings, out=out, client=env["client"]).export_dir
    assert export_job.read_export_metadata(folder)["file_sha256"]
    assert export_job.run_export(store, settings, out=out, client=env["client"]).status == (
        "succeeded"
    )
