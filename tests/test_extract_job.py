# SPDX-License-Identifier: MIT
import pytest

from autoontology.config import Settings
from autoontology.engine import extract as ex
from autoontology.engine import jobs
from autoontology.store.db import Store
from autoontology.store.documents import DocumentStore
from autoontology.unstructured.schemas import ChunkExtraction, MergeDecision

VOCAB = ["계약", "고객", "청구서", "수납"]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def structured(self, system, human, schema):
        self.calls += 1
        if schema is MergeDecision:
            return MergeDecision(same=False)
        text = human.split("\n\n", 1)[1]
        found = [w for w in VOCAB if w in text]
        concepts = [
            {"name": w, "kind": "entity", "definition": f"{w}의 정의", "evidence": w} for w in found
        ]
        relations = (
            [
                {
                    "subject": found[0],
                    "predicate": "related_to",
                    "object": found[1],
                    "evidence": found[0],
                }
            ]
            if len(found) > 1
            else []
        )
        return ChunkExtraction.model_validate({"concepts": concepts, "relations": relations})

    def text(self, prompt):
        raise AssertionError("structured path expected")


class Boom:
    def structured(self, *a):
        raise AssertionError("no LLM call expected")

    text = structured


@pytest.fixture
def env(isolated_env, tmp_path):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "계약.md").write_text(
        "# 계약규정\n\n## 제1조\n계약은 고객과 맺는다.\n\n"
        "## 제2조\n" + "계약 조건은 서면으로 정한다. " * 80,
        encoding="utf-8",
    )
    (docs_dir / "청구.md").write_text(
        "# 청구규정\n\n청구서를 발행하고 수납을 확인한다.", encoding="utf-8"
    )
    return settings, store, docs_dir


def _backends(settings, llm=None, embed=None):
    llm = llm or FakeLLM()
    return ex.Backends(
        settings,
        llm=llm,
        judge_llm=llm,
        embedder=embed or (lambda texts: [[1.0, 0.0]] * len(texts)),
    )


def _cli_build_with(monkeypatch, backends):
    """`autoontology build` with fake extraction backends (no service check)."""
    from autoontology.engine import build

    real = build.run_build
    monkeypatch.setattr(
        build,
        "run_build",
        lambda store, settings, **kw: real(
            store, settings, extract_backends=backends, check_service=False, **kw
        ),
    )


def _run(settings, store, target, backends, **kw):
    return ex.run_extract(
        "hanbit_docs",
        target,
        settings=settings,
        store=store,
        backends=backends,
        check_service=False,
        workers=2,
        **kw,
    )


def test_extract_end_to_end_and_noop_rerun(env, monkeypatch):
    settings, store, docs_dir = env
    monkeypatch.setenv("AUTOONTOLOGY_CHUNK_TOKENS", "200")
    llm = FakeLLM()
    result = _run(settings, store, docs_dir, _backends(settings, llm))
    assert result.status == "succeeded"
    assert list(result.steps) == ["read", "extract", "consolidate", "report"]
    src = store.get_source("hanbit_docs")
    assert src.kind == "documents"
    docs = DocumentStore(store)
    chunks = docs.chunks_for_source(src.id)
    assert len(chunks) >= 3 and all(c.extraction for c in chunks)
    chunk_steps = [s for s in store.get_steps(result.run_id) if s.name.startswith("chunk:")]
    assert len(chunk_steps) == len(chunks) and {s.status for s in chunk_steps} == {"succeeded"}
    assert {s.detail["path"] for s in chunk_steps} == {"structured"}
    concepts = {c.name: c for c in docs.list_concepts(src.id)}
    assert set(concepts) == set(VOCAB)
    assert all(c.evidence_count >= 1 for c in concepts.values())
    assert {(r.subject, r.object) for r in docs.list_relations(src.id)} == {
        ("계약", "고객"),
        ("청구서", "수납"),
    }
    judge_calls = llm.calls - len(chunks)
    assert judge_calls == 6  # 4 concepts, every pair at cosine 1.0, judge says "not same"
    report = (result.export_dir / "report.md").read_text(encoding="utf-8")
    assert (
        "# Document extraction report — hanbit_docs" in report
        and "| Concepts (without evidence) | 4 (0) |" in report
    )
    assert result.details["report"]["concepts"] == 4

    # unchanged files: no LLM, no new chunks, consolidation kept
    again = _run(
        settings,
        store,
        docs_dir,
        ex.Backends(settings, llm=Boom(), judge_llm=Boom(), embedder=Boom().structured),
    )
    assert again.run_id != result.run_id
    assert again.details["read"]["new_or_changed"] == 0
    assert again.details["extract"]["attempted"] == 0
    assert again.details["consolidate"]["action"] == "unchanged"
    assert [c.id for c in docs.chunks_for_source(src.id)] == [c.id for c in chunks]

    # one changed file -> only its chunks are extracted again
    (docs_dir / "청구.md").write_text("# 청구규정\n\n청구서는 매월 발행한다.", encoding="utf-8")
    llm2 = FakeLLM()
    third = _run(settings, store, docs_dir, _backends(settings, llm2))
    assert [d["action"] for d in third.details["read"]["documents"]] == ["unchanged", "replaced"]
    assert third.details["extract"]["attempted"] == 1
    assert third.details["consolidate"]["action"] == "consolidated"
    assert "수납" not in {c.name for c in docs.list_concepts(src.id)}
    assert len(docs.list_documents(src.id)) == 2


def test_max_chunks_and_resume(env):
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings), max_chunks=1)
    assert first.details["extract"]["remaining"] >= 1
    report = (first.export_dir / "report.md").read_text(encoding="utf-8")
    assert "pending chunks" in report
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.run_id == first.run_id  # same run continued
    assert resumed.details["extract"]["remaining"] == 0
    src = store.get_source("hanbit_docs")
    assert all(c.extraction for c in DocumentStore(store).chunks_for_source(src.id))


def test_failed_chunks_and_missing_engine(env, monkeypatch):
    settings, store, docs_dir = env
    monkeypatch.delenv("KORDOC_CLI", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    (docs_dir / "규정.hwp").write_bytes(b"x")

    class Flaky(FakeLLM):
        def structured(self, system, human, schema):
            if schema is ChunkExtraction and "청구" in human:
                raise RuntimeError("model said no")
            return super().structured(system, human, schema)

        def text(self, prompt):
            return "not json"

    result = _run(settings, store, docs_dir, _backends(settings, Flaky()))
    read = result.details["read"]
    assert [f["code"] for f in read["failed"]] == ["ENGINE_MISSING"]
    assert "install-kordoc" in read["failed"][0]["error"]
    assert result.details["extract"]["failed"] == 1
    failed = [
        s
        for s in store.get_steps(result.run_id)
        if s.name.startswith("chunk:") and s.status == "failed"
    ]
    assert len(failed) == 1 and "model said no" in failed[0].detail["structured_error"]
    report = (result.export_dir / "report.md").read_text(encoding="utf-8")
    assert "## Failed chunks" in report and "ENGINE_MISSING" in report
    # a partial run is not "succeeded": failed, with the failed chunks named
    run = store.get_run(result.run_id)
    assert result.status == "failed" and run.status == "failed"
    assert run.error.startswith("partial: 1 of ") and "청구규정 #0" in run.error
    assert run.params["partial"] is True and len(run.params["failed_chunks"]) == 1
    assert result.details["extract"]["partial"] is True

    assert "1 document(s) failed to read" in run.error and "규정.hwp" in run.error
    assert run.params["failed_documents"][0].endswith("규정.hwp")

    # --resume retries the failed chunk (and the unreadable document, still unreadable:
    # the run stays failed, review #9)
    llm = FakeLLM()
    resumed = _run(settings, store, docs_dir, _backends(settings, llm), resume=True)
    assert resumed.run_id == result.run_id and resumed.status == "failed"
    assert resumed.details["extract"]["attempted"] == 1
    assert [f["code"] for f in resumed.details["read"]["failed"]] == ["ENGINE_MISSING"]
    assert llm.calls - 1 == resumed.details["consolidate"]["stats"]["judge_calls"]
    run = store.get_run(result.run_id)
    assert run.status == "failed" and "of 1 chunk(s)" not in run.error
    # the document goes away: the run succeeds
    (docs_dir / "규정.hwp").unlink()
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.status == "succeeded" and store.get_run(result.run_id).status == "succeeded"


def test_all_chunks_failing_fails_the_run(env):
    settings, store, docs_dir = env

    class Down:
        def structured(self, *a):
            raise ConnectionRefusedError("refused")

        text = structured

    with pytest.raises(jobs.JobError, match="extract failed") as err:
        _run(
            settings,
            store,
            docs_dir,
            ex.Backends(settings, llm=Down(), judge_llm=Down(), embedder=lambda t: []),
        )
    run = store.get_run(err.value.run_id)
    assert run.status == "failed" and "all" in run.error


def test_source_kind_and_service_checks(env, monkeypatch):
    settings, store, docs_dir = env
    store.add_source(name="hanbit_docs", kind="files", locator='{"path": "x"}')
    with pytest.raises(jobs.JobError, match="documents source"):
        _run(settings, store, docs_dir, _backends(settings))
    ollama = Settings.from_env()
    monkeypatch.setattr(jobs, "service_alive", lambda s: False)
    with pytest.raises(jobs.JobError, match="autoontology up"):
        ex.run_extract("other", docs_dir, settings=ollama, store=store)


def test_cli_documents_source_list_and_rm(env):
    from typer.testing import CliRunner

    from autoontology.cli import app

    settings, store, docs_dir = env
    runner = CliRunner()
    res = runner.invoke(
        app, ["source", "add", str(docs_dir), "--name", "hanbit_docs", "--kind", "documents"]
    )
    assert res.exit_code == 0, res.output
    assert _run(settings, store, docs_dir, _backends(settings)).status == "succeeded"
    assert "documents" in runner.invoke(app, ["source", "list"]).output
    res = runner.invoke(app, ["source", "rm", "hanbit_docs"])
    assert res.exit_code == 0 and "removed documents source" in res.output
    assert store.get_source("hanbit_docs") is None
    res = runner.invoke(
        app, ["source", "add", str(docs_dir / "missing"), "--name", "x", "--kind", "documents"]
    )
    assert res.exit_code == 1 and "no such file" in res.output


def test_documents_source_names():
    assert ex.validate_documents_source_name("hanbit-docs") == "hanbit-docs"
    for bad in ("Hanbit", "-x", "a b", ""):
        with pytest.raises(ValueError, match="invalid source name"):
            ex.validate_documents_source_name(bad)


def test_removed_documents_are_pruned_and_duplicates_kept_stable(env):
    settings, store, docs_dir = env
    (docs_dir / "사본.md").write_text(
        (docs_dir / "청구.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    first = _run(settings, store, docs_dir, _backends(settings))
    assert [d["path"].rsplit("/", 1)[-1] for d in first.details["read"]["duplicates"]] == [
        "청구.md"
    ]  # 사본.md sorts first and owns the content
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    paths = sorted(d.path.rsplit("/", 1)[-1] for d in docs.list_documents(src.id))
    assert paths == ["계약.md", "사본.md"]
    second = _run(settings, store, docs_dir, _backends(settings))
    assert sorted(d.path.rsplit("/", 1)[-1] for d in docs.list_documents(src.id)) == paths
    assert second.details["read"]["new_or_changed"] == 0
    report = (second.export_dir / "report.md").read_text(encoding="utf-8")
    assert "### Duplicate documents" in report

    # 계약.md disappears -> its document, chunks and concepts leave the ontology
    (docs_dir / "계약.md").unlink()
    limited = _run(settings, store, docs_dir, _backends(settings), max_chunks=0)
    assert limited.details["read"]["pruned"] == []  # not a full scan: nothing pruned
    third = _run(settings, store, docs_dir, _backends(settings))
    assert [p.rsplit("/", 1)[-1] for p in third.details["read"]["pruned"]] == ["계약.md"]
    assert third.details["consolidate"]["action"] == "consolidated"
    assert [d.path.rsplit("/", 1)[-1] for d in docs.list_documents(src.id)] == ["사본.md"]
    assert {c.name for c in docs.list_concepts(src.id)} == {"청구서", "수납"}
    report = (third.export_dir / "report.md").read_text(encoding="utf-8")
    assert "### Removed documents" in report and "계약.md" in report


def test_cli_partial_exits_1(env, monkeypatch):
    from typer.testing import CliRunner

    from autoontology.cli import app

    settings, store, docs_dir = env

    class Flaky(FakeLLM):
        def structured(self, system, human, schema):
            if schema is ChunkExtraction and "청구" in human:
                raise ConnectionRefusedError("refused")
            return super().structured(system, human, schema)

    _cli_build_with(monkeypatch, _backends(settings, Flaky()))
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["source", "add", str(docs_dir), "--name", "hanbit_docs", "--kind", "documents"]
        ).exit_code
        == 0
    )
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 1
    assert "partial: documents:hanbit_docs:extract: 1 of" in res.output
    assert "chunk(s) failed" in res.output and "--resume" in res.output


def test_report_flags_chunks_over_the_llm_timeout(monkeypatch):
    from autoontology.store.documents import ChunkRow
    from autoontology.unstructured import report

    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_S", "180")

    def chunk(seconds, **detail):
        return ChunkRow(
            "c",
            "d",
            0,
            "",
            None,
            "text",
            "t",
            1,
            {"concepts": []},
            {"seconds": seconds, **detail},
            "x",
        )

    data = report.ReportData(
        "s",
        "r",
        "now",
        [],
        [chunk(327.3), chunk(90.0), chunk(10.0, upstream_retries=1)],
        [],
        [],
        {},
        {},
    )
    counts = report.summary_counts(data)
    assert counts["over_timeout_chunks"] == 1 and counts["retried_chunks"] == 1
    assert counts["max_chunk_seconds"] == 327.3


def test_moving_to_another_dir_with_the_old_copy_still_present(env, tmp_path):
    settings, store, docs_dir = env
    dir_b = tmp_path / "dirB"
    dir_b.mkdir()
    for f in docs_dir.iterdir():
        (dir_b / f.name).write_bytes(f.read_bytes())
    _run(settings, store, docs_dir, _backends(settings))
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    concepts = {c.name for c in docs.list_concepts(src.id)}
    moved = _run(
        settings,
        store,
        dir_b,
        ex.Backends(settings, llm=Boom(), judge_llm=Boom(), embedder=Boom().structured),
    )
    read = moved.details["read"]
    assert read["duplicates"] == [] and read["pruned"] == []
    assert [d["action"] for d in read["documents"]] == ["unchanged", "unchanged"]
    assert moved.details["extract"]["attempted"] == 0
    assert {d.path for d in docs.list_documents(src.id)} == {
        str((dir_b / n).resolve()) for n in ("계약.md", "청구.md")
    }
    assert {c.name for c in docs.list_concepts(src.id)} == concepts


def test_read_failures_fail_the_run_and_resume_retries_them(env, monkeypatch):
    """Review #9: a document that fails to read makes the run failed (listed), `--resume`
    reads it again and, once it reads, extract/consolidate/report run for it."""
    from autoontology.unstructured import readers

    settings, store, docs_dir = env
    real = readers.read_document
    broken = {"on": True}

    def flaky(path, **kw):
        if broken["on"] and path.name == "청구.md":
            raise readers.ReadError(f"{path.name}: reader down", code="READ_FAILED")
        return real(path, **kw)

    monkeypatch.setattr(readers, "read_document", flaky)
    first = _run(settings, store, docs_dir, _backends(settings))
    assert first.status == "failed"
    run = store.get_run(first.run_id)
    assert "1 document(s) failed to read" in run.error and "청구.md" in run.error
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    assert {c.name for c in docs.list_concepts(src.id)} == {"계약", "고객"}
    broken["on"] = False
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.run_id == first.run_id and resumed.status == "succeeded"
    assert resumed.details["read"]["failed"] == []
    assert resumed.details["extract"]["attempted"] == 1  # the recovered document's chunk
    assert resumed.details["consolidate"]["action"] == "consolidated"
    assert {"청구서", "수납"} <= {c.name for c in docs.list_concepts(src.id)}


def test_cli_extract_lists_read_failures(env, monkeypatch):
    from typer.testing import CliRunner

    from autoontology.cli import app
    from autoontology.unstructured import readers

    settings, store, docs_dir = env
    real = readers.read_document

    def flaky(path, **kw):
        if path.name == "청구.md":
            raise readers.ReadError(f"{path.name}: reader down", code="READ_FAILED")
        return real(path, **kw)

    monkeypatch.setattr(readers, "read_document", flaky)
    _cli_build_with(monkeypatch, _backends(settings))
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["source", "add", str(docs_dir), "--name", "hanbit_docs", "--kind", "documents"]
        ).exit_code
        == 0
    )
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 1
    assert "documents:hanbit_docs:read: 1 document(s) failed to read" in res.output


def test_document_changed_to_a_duplicate_drops_its_old_content(env):
    """Review #6: A.md=X, B.md=Y, then A overwritten with Y: A's old X rows must go."""
    settings, store, docs_dir = env
    a, b = docs_dir / "계약.md", docs_dir / "청구.md"
    _run(settings, store, docs_dir, _backends(settings))
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    assert {"계약", "고객"} <= {c.name for c in docs.list_concepts(src.id)}
    a.write_bytes(b.read_bytes())
    second = _run(settings, store, docs_dir, _backends(settings))
    read = second.details["read"]
    assert [d["path"] for d in read["superseded"]] == [str(a.resolve())]
    assert read["new_or_changed"] == 1
    assert [d["of"] for d in read["duplicates"]] == [str(b.resolve())]
    assert [d.path for d in docs.list_documents(src.id)] == [str(b.resolve())]
    assert second.details["consolidate"]["action"] == "consolidated"
    names = {c.name for c in docs.list_concepts(src.id)}
    assert "계약" not in names and "고객" not in names


def test_failed_consolidation_is_partial_and_retried(env):
    """Review #10: an embedding outage during consolidation must not be cached as a
    success; the next run consolidates again (and merges once the embedder is back)."""
    settings, store, docs_dir = env

    def down(texts):
        raise ConnectionError("embedder down")

    first = _run(settings, store, docs_dir, _backends(settings, embed=down))
    detail = first.details["consolidate"]
    assert detail["partial"] is True and detail["fingerprint"] is None
    assert first.status == "failed"
    assert "consolidation incomplete" in store.get_run(first.run_id).error
    second = _run(settings, store, docs_dir, _backends(settings))
    assert second.details["consolidate"]["action"] == "consolidated"
    assert second.details["consolidate"]["partial"] is False
    assert second.status == "succeeded"
    third = _run(settings, store, docs_dir, _backends(settings))
    assert third.details["consolidate"]["action"] == "unchanged"


def test_consolidation_identity_includes_threshold(env, monkeypatch):
    settings, store, docs_dir = env
    _run(settings, store, docs_dir, _backends(settings))
    monkeypatch.setenv("AUTOONTOLOGY_MERGE_THRESHOLD", "0.9")
    again = _run(settings, store, docs_dir, _backends(settings))
    assert again.details["consolidate"]["action"] == "consolidated"
    assert again.details["consolidate"]["config"]["threshold"] == 0.9


def _paths_by_content(store, docs_dir):
    src = store.get_source("hanbit_docs")
    return {
        d.path.rsplit("/", 1)[-1]: d.sha256 for d in DocumentStore(store).list_documents(src.id)
    }


def _sha(text):
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_replace_chain_keeps_every_current_content(env):
    """Fix wave 2 #4: A=X, B=Y -> A=Y, B=Z must keep Y (now under A) and add Z."""
    settings, store, docs_dir = env
    x, y, z = (
        "# X\n\n계약은 고객과 맺는다.",
        "# Y\n\n청구서를 발행한다.",
        "# Z\n\n수납을 확인한다.",
    )
    for f in docs_dir.iterdir():
        f.unlink()
    a, b = docs_dir / "a.md", docs_dir / "b.md"
    a.write_text(x, encoding="utf-8")
    b.write_text(y, encoding="utf-8")
    _run(settings, store, docs_dir, _backends(settings))
    a.write_text(y, encoding="utf-8")
    b.write_text(z, encoding="utf-8")
    result = _run(settings, store, docs_dir, _backends(settings))
    assert result.status == "succeeded"
    assert _paths_by_content(store, docs_dir) == {"a.md": _sha(y), "b.md": _sha(z)}
    read = result.details["read"]
    assert [d["path"].rsplit("/", 1)[-1] for d in read["superseded"]] == ["a.md"]
    actions = {d["path"].rsplit("/", 1)[-1]: d["action"] for d in read["documents"]}
    assert actions == {"a.md": "unchanged", "b.md": "new"}  # Y moved to a.md
    names = {c.name for c in DocumentStore(store).list_concepts(store.get_source("hanbit_docs").id)}
    assert {"청구서", "수납"} <= names and "계약" not in names


def test_swapped_and_moved_contents_are_kept_without_rereading(env):
    settings, store, docs_dir = env
    x, y = "# X\n\n계약은 고객과 맺는다.", "# Y\n\n청구서를 발행한다."
    for f in docs_dir.iterdir():
        f.unlink()
    a, b = docs_dir / "a.md", docs_dir / "b.md"
    a.write_text(x, encoding="utf-8")
    b.write_text(y, encoding="utf-8")
    _run(settings, store, docs_dir, _backends(settings))
    a.write_text(y, encoding="utf-8")  # swap
    b.write_text(x, encoding="utf-8")
    swapped = _run(
        settings,
        store,
        docs_dir,
        ex.Backends(settings, llm=Boom(), judge_llm=Boom(), embedder=Boom().structured),
    )
    assert swapped.status == "succeeded" and swapped.details["read"]["superseded"] == []
    assert _paths_by_content(store, docs_dir) == {"a.md": _sha(y), "b.md": _sha(x)}
    b.rename(docs_dir / "c.md")  # move
    moved = _run(
        settings,
        store,
        docs_dir,
        ex.Backends(settings, llm=Boom(), judge_llm=Boom(), embedder=Boom().structured),
    )
    assert moved.details["read"]["pruned"] == []
    assert _paths_by_content(store, docs_dir) == {"a.md": _sha(y), "c.md": _sha(x)}


def _poison_consolidate(store, run_id, **changes):
    """Rewrite the run's stored consolidate detail (e.g. as a legacy run left it)."""
    step = next(s for s in store.get_steps(run_id) if s.name == "consolidate")
    detail = {k: v for k, v in step.detail.items() if k not in changes.get("drop", ())}
    detail.update(changes.get("set", {}))
    store.finish_step(run_id, "consolidate", status="succeeded", detail=detail)


def test_resume_reconsolidates_a_legacy_incomplete_consolidation(env):
    """Fix wave 2 #5: a legacy succeeded consolidate with stats.embedding_error and no
    partial/config fields must not be reused by --resume."""
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings))
    _poison_consolidate(
        store,
        first.run_id,
        drop=("partial", "config"),
        set={"stats": {"embedding_error": "ConnectionError: down"}},
    )
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.run_id == first.run_id
    assert resumed.details["consolidate"]["action"] == "consolidated"
    assert resumed.details["consolidate"]["config"]


def test_resume_reconsolidates_when_the_threshold_changed(env, monkeypatch):
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings))
    monkeypatch.setenv("AUTOONTOLOGY_MERGE_THRESHOLD", "0.99")
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.run_id == first.run_id
    assert resumed.details["consolidate"]["config"]["threshold"] == 0.99
    assert "report" in resumed.details


def test_legacy_consolidation_without_config_is_not_reused(env):
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings))
    _poison_consolidate(store, first.run_id, drop=("config",))
    again = _run(settings, store, docs_dir, _backends(settings))
    assert again.details["consolidate"]["action"] == "consolidated"
    third = _run(settings, store, docs_dir, _backends(settings))
    assert third.details["consolidate"]["action"] == "unchanged"


def test_threshold_cycle_never_reports_an_unmaterialised_result(env, monkeypatch):
    """Fix wave 3 #2: run 1 at 0.8 merges, fresh run 2 at 0.99 does not, resuming run 2 at
    0.8 must consolidate again (not report run 1's result over run 2's ontology)."""
    settings, store, docs_dir = env

    class Merging(FakeLLM):
        def structured(self, system, human, schema):
            if schema is MergeDecision:
                return MergeDecision(same=True)
            return super().structured(system, human, schema)

    vectors = {"계약": [1.0, 0.0], "고객": [0.9, 0.4359], "청구서": [0.0, 1.0], "수납": [-1.0, 0.0]}

    def embed(texts):
        return [vectors.get(t.split(":")[0], [0.0, -1.0]) for t in texts]

    def run(threshold, **kw):
        monkeypatch.setenv("AUTOONTOLOGY_MERGE_THRESHOLD", threshold)
        return _run(settings, store, docs_dir, _backends(settings, Merging(), embed), **kw)

    first = run("0.8")
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    merged = len(docs.list_concepts(src.id))
    assert first.details["consolidate"]["concepts"] == merged
    second = run("0.99")
    assert len(docs.list_concepts(src.id)) == merged + 1
    resumed = run("0.8", resume=True)
    assert resumed.run_id == second.run_id
    detail = resumed.details["consolidate"]
    assert detail["action"] == "consolidated"
    assert detail["concepts"] == len(docs.list_concepts(src.id)) == merged
    assert detail["ontology_sha"] == docs.ontology_identity(src.id)
    # a later run may now reuse it: identity matches the stored ontology
    again = run("0.8")
    assert again.details["consolidate"]["action"] == "unchanged"
    assert again.details["consolidate"]["concepts"] == len(docs.list_concepts(src.id))


def test_record_without_identity_is_never_reused_even_with_equal_counts(env):
    """Fix wave 4 #2: Alpha/Gamma/Delta vs Alpha/Beta/Gamma have equal counts; a record
    without an ontology identity must not be taken for the stored ontology."""
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings))
    _poison_consolidate(store, first.run_id, drop=("ontology_sha",))
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    before = len(docs.list_concepts(src.id))
    with store._conn() as conn:  # same counts, different membership
        conn.execute(
            "UPDATE concepts SET name = name || '-other' WHERE source_id = ? AND name = '계약'",
            (src.id,),
        )
    assert len(docs.list_concepts(src.id)) == before
    again = _run(settings, store, docs_dir, _backends(settings))
    assert again.details["consolidate"]["action"] == "consolidated"
    assert "계약" in {c.name for c in docs.list_concepts(src.id)}


@pytest.mark.parametrize("identity", ["absent", "mismatch", "match"])
def test_same_settings_resume_checks_the_ontology_identity(env, identity):
    """Fix wave 5 #1: a completed run resumed with unchanged settings reused consolidate
    without checking that the stored ontology is still its result."""
    settings, store, docs_dir = env
    first = _run(settings, store, docs_dir, _backends(settings))
    src = store.get_source("hanbit_docs")
    docs = DocumentStore(store)
    if identity == "absent":
        _poison_consolidate(store, first.run_id, drop=("ontology_sha",))
    elif identity == "mismatch":
        with store._conn() as conn:  # someone else's membership, same counts
            conn.execute(
                "UPDATE concepts SET name = name || '-other' WHERE source_id = ? AND name = '계약'",
                (src.id,),
            )
    resumed = _run(settings, store, docs_dir, _backends(settings), resume=True)
    assert resumed.run_id == first.run_id and resumed.status == "succeeded"
    if identity == "match":
        assert "consolidate" not in resumed.details  # reused: nothing executed
    else:
        assert resumed.details["consolidate"]["action"] == "consolidated"
        assert resumed.details["consolidate"]["ontology_sha"] == docs.ontology_identity(src.id)
        assert "계약" in {c.name for c in docs.list_concepts(src.id)}
