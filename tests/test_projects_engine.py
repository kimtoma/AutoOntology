# SPDX-License-Identifier: MIT
"""Projects in the engine layer: builds, the global build gate, source databases, exports,
the playground and agent tokens each see one project only."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from autoontology import config
from autoontology.config import Settings
from autoontology.engine import build as bj
from autoontology.engine import export_job, jobs, upstream_semantic, upstream_terms
from autoontology.engine import extract as ex
from autoontology.engine.jobs import JobError
from autoontology.ontology import model as merged
from autoontology.ontology.model import MergedOntology
from autoontology.store import db
from autoontology.store.db import ProjectStore, Store, root_store, scoped
from autoontology.store.decisions import DecisionStore
from autoontology.structured import pipeline, remove
from autoontology.structured.pipeline import SourceError
from tests.test_build import FakeLLM, env, run  # noqa: F401 - the build fixture and runner

# --- the project view ----------------------------------------------------------------------


@pytest.fixture
def store(isolated_env):
    return Store.for_home(Settings.from_env().home)


def test_a_project_view_stands_in_for_the_store(store):
    beta = store.create_project("beta", name="Beta")
    assert scoped(store).id == store.default_project_id
    assert scoped(beta) is beta and root_store(beta) is store and root_store(store) is store
    with pytest.raises(ValueError, match="not 'other'"):
        scoped(beta, "other")
    # per-project tables built from the view are scoped to it
    assert DecisionStore(beta).project_id == beta.id
    assert beta.default_project_id == beta.id and beta.name == "Beta"
    # a store method with project_id gets the view's (no default-project leak)
    run = beta.create_run(kind="build", source_id=None)
    assert run.project_id == beta.id
    assert [r.id for r in beta.list_runs()] == [run.id]
    assert store.project().list_runs() == []
    assert beta.latest_workspace_run("build").id == run.id
    assert store.project().latest_workspace_run("build") is None
    # store-wide id methods pass through; deleting another project's source is refused
    src = store.project().add_source(name="a", kind="documents", locator="{}")
    with pytest.raises(ValueError, match="does not belong to this project"):
        beta.delete_source(src.id)


# --- two projects building --------------------------------------------------------------------


def _beta(ctx, *, docs_name="hanbit-docs"):
    """Project ``beta`` with one documents source (only 청구서/수납 in its text)."""
    beta = ctx["store"].create_project("beta", name="Beta")
    folder = ctx["docs"].parent / "beta-docs"
    folder.mkdir()
    (folder / "청구.md").write_text("# 청구\n\n청구서를 발행하고 수납을 확인한다.", "utf-8")
    ex.ensure_documents_source(beta, docs_name, folder)
    return beta


def test_two_projects_build_independent_revisions_and_never_mix(env):  # noqa: F811
    store = env["store"]
    pipeline.add_source(store, str(env["data"]), name="hanbit")
    ex.ensure_documents_source(store, "hanbit-docs", env["docs"])
    beta = _beta(env)  # the same source name in another project is allowed
    default = store.project()

    first = run(env)
    second = run({**env, "store": beta})
    third = run(env)
    assert (first.revision, second.revision, third.revision) == (1, 1, 2)
    assert default.latest_revision() == 2 and beta.latest_revision() == 1

    a = MergedOntology.from_dict(default.latest_snapshot().model)
    b = MergedOntology.from_dict(beta.latest_snapshot().model)
    assert a.name == "default" and b.name == "Beta"  # the ontology is named after the project
    assert a.structured_sources == ["hanbit"] and a.documents_sources == ["hanbit-docs"]
    # beta's align/merge saw only beta's sources: not the built `hanbit` of the default one
    assert b.structured_sources == [] and b.documents_sources == ["hanbit-docs"]
    # beta's own two concepts, none folded into a Term; the default one folds two
    assert b.counts()["classes_document"] == 2 and b.counts()["merged_concepts"] == 0
    assert a.counts()["merged_concepts"] == 2
    assert b.aligned_terms() == set() and a.aligned_terms()
    align = next(s for s in store.get_steps(second.run_id) if s.name == "align:hanbit-docs")
    assert align.detail["skipped"].startswith("no built structured source")
    assert [s["name"] for s in beta.latest_snapshot().sources] == ["hanbit-docs"]
    # runs belong to their project
    assert {r.id for r in beta.list_runs()} == {second.run_id}
    assert {r.id for r in default.list_runs() if r.kind == "build"} == {
        first.run_id,
        third.run_id,
    }
    # a resumed build of beta resumes beta's own run
    again = run({**env, "store": beta}, resume=True)
    assert again.run_id == second.run_id


def test_run_build_takes_a_project_by_reference(env):  # noqa: F811
    beta = _beta(env)
    result = bj.run_build(
        env["store"],
        env["settings"],
        project="beta",
        client=env["client"],
        extract_backends=ex.Backends(
            env["settings"],
            llm=FakeLLM(),
            embedder=lambda t: [[1.0, 0.0]] * len(t),
        ),
    )
    assert result.revision == 1 and beta.latest_revision() == 1
    assert env["store"].project().latest_revision() is None


# --- the global build gate --------------------------------------------------------------------


def test_a_build_waits_for_the_engine_held_by_another_project(env):  # noqa: F811
    ex.ensure_documents_source(env["store"], "hanbit-docs", env["docs"])
    beta = _beta(env)
    assert run(env).revision == 1
    with jobs.hold_engine(beta, "build"):
        with pytest.raises(JobError, match=r"engine already has an active build of project 'beta'"):
            run(env)
        with pytest.raises(JobError, match="engine already has an active"):
            export_job.run_export(env["store"], env["settings"], client=env["client"])
    assert run(env).revision == 2  # released


def test_an_active_run_of_another_project_refuses_a_build(env):  # noqa: F811
    ex.ensure_documents_source(env["store"], "hanbit-docs", env["docs"])
    beta = _beta(env)
    # a live process (the test runner's parent) holds an export of beta
    beta.create_run(kind="export", source_id=None, params={"pid": os.getppid()})
    with pytest.raises(JobError, match=r"active in project 'beta'"):
        run(env)


def test_the_run_manager_gates_builds_across_projects(isolated_env):
    from autoontology.web.runs import RunManager, RunStartError

    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    beta = store.create_project("beta")
    release = threading.Event()

    def job(tracking, project_id):
        tracking.project(project_id).create_run(kind="build", source_id=None)
        release.wait(10)

    manager = RunManager(settings, jobs=lambda: {"build": job, "export": job, "apply": job})
    run_a = manager.start("build", "*workspace*", lambda fn, s: fn(s, store.default_project_id))
    try:
        assert store.get_run(run_a).project_id == store.default_project_id
        with pytest.raises(RunStartError, match="engine already has an active build") as busy:
            manager.start("export", "*workspace*", lambda fn, s: fn(s, beta.id), project=beta.id)
        assert busy.value.status == 409
        with pytest.raises(RunStartError, match="already has an active build"):
            manager.start("build", "*workspace*", lambda fn, s: fn(s, store.default_project_id))
        # a source claim of another project is not behind the gate
        manager.claim("orders", "upload", project=beta.id)
        assert manager.active_source("orders", project=beta.id).kind == "upload"
        assert manager.active_source("orders").kind == "build"  # the default project's run
        manager.release("orders", project=beta.id)
        assert manager.busy(project=beta.id) is None
    finally:
        release.set()
        manager.join(run_a, 10)
    run_b = manager.start("build", "*workspace*", lambda fn, s: fn(s, beta.id), project=beta.id)
    manager.join(run_b, 10)
    assert store.get_run(run_b).project_id == beta.id
    # claim_idle holds every project
    manager.claim_idle("*workspace*", "restart")
    with pytest.raises(RunStartError):
        manager.claim("orders", "upload", project=beta.id)
    manager.release("*workspace*")
    assert manager.busy() is None


# --- source databases -----------------------------------------------------------------------


def test_source_database_names_per_project_and_their_length_budget():
    assert pipeline.pg_database_for("orders") == "autoontology_src_orders"
    assert pipeline.pg_database_for("orders", "sales-eu") == "autoontology_src_sales_eu__orders"
    assert config.PROJECT_SLUG_MAX_LEN == db.SLUG_MAX == 24
    assert config.PROJECT_SOURCE_NAME_MAX_LEN == 63 - 17 - 24 - 2 == 20
    assert config.source_name_max_len(None) == 46
    assert config.source_name_max_len("ab") == 46 - 2 - 2
    slug = "a" * 24
    name = "n" * 20
    assert pipeline.validate_source_name(name, slug) == name
    assert len(pipeline.pg_database_for(name, slug)) == 63
    assert config.PG_DATABASE_RE.fullmatch(pipeline.pg_database_for(name, slug))
    with pytest.raises(SourceError, match=f"at most 20 characters in project '{slug}'"):
        pipeline.validate_source_name(name + "x", slug)
    with pytest.raises(ValueError, match="63"):
        config.source_database_name(name + "x", slug)


def test_new_sources_of_another_project_get_slugged_databases(store, tmp_path):
    data = tmp_path / "files"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n")
    beta = store.create_project("sales-eu")
    mine = pipeline.add_source(store, str(data), name="orders")
    theirs = pipeline.add_source(beta, str(data), name="orders")
    assert mine.pg_database == "autoontology_src_orders"
    assert theirs.pg_database == "autoontology_src_sales_eu__orders"
    assert theirs.project_id == beta.id
    assert pipeline.catalog_database_name(theirs) == "autoontology_src_sales_eu__orders"
    # an existing row keeps the database it stores
    legacy = beta.add_source(
        name="legacy", kind="files", locator=mine.locator, pg_database="autoontology_src_legacy"
    )
    assert pipeline.catalog_database_name(legacy) == "autoontology_src_legacy"
    with pytest.raises(SourceError, match="at most 36 characters in project 'sales-eu'"):
        pipeline.add_source(beta, str(data), name="x" * 37)
    # a default-project name that spells a project database is refused there too
    store.create_project("beta")
    pipeline.add_source(store, str(data), name="beta__x")
    with pytest.raises(SourceError, match="already registered by source 'beta__x'"):
        pipeline.add_source(store.project("beta"), str(data), name="x")


def test_a_database_belongs_to_one_project(store):
    beta = store.create_project("beta")
    url = "postgresql://u:p@db.example/shop"
    shop = pipeline.add_source(store, url, name="shop")
    with pytest.raises(SourceError, match=r"already registered by source 'shop' of project"):
        pipeline.add_source(beta, url, name="shop")
    with pytest.raises(SourceError, match="already uses database 'shop'"):
        pipeline.add_source(store, url.replace("u:p", "v:q"), name="shop2")
    assert pipeline.source_for_database(beta, "SHOP").id == shop.id  # any project


def test_orphan_connections_count_every_projects_sources(store, isolated_env):
    beta = store.create_project("beta")
    data = isolated_env / "f"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n")
    pipeline.add_source(beta, str(data), name="orders")

    class Client:
        def list_connections(self):
            return [
                {"database_name": "autoontology_src_beta__orders"},
                {"database_name": "autoontology_src_gone"},
            ]

    orphans = remove.find_orphan_connections(
        Settings.from_env(), store, Client(), database_exists=lambda name: True
    )
    assert [o.name for o in orphans] == ["autoontology_src_gone"]


def test_ingest_delete_refuses_another_projects_database(store):
    from autoontology.web.app import delete_refusal

    beta = store.create_project("beta")
    pipeline.add_source(store, "postgresql://u:p@h/shop", name="shop")
    assert delete_refusal(store, "shop", None) is None  # upstream names no project
    assert delete_refusal(store, "shop", "default") is None
    assert delete_refusal(store, "shop", beta.slug) == (
        409,
        "database 'shop' belongs to another project",
    )
    assert delete_refusal(store, "shop", "nope")[0] == 404
    assert delete_refusal(store, "unknown_db", "beta") is None


def test_upstream_term_writes_are_limited_to_the_projects_databases(monkeypatch):
    monkeypatch.setattr(upstream_semantic, "term_databases_statement", lambda s, t: ("q", t))
    rows = [{"name": "autoontology_src_beta__orders"}]

    class Upstream:
        def query_write(self, statement):
            assert statement == ("q", "term-1")
            return rows

    up = {"s": None, "store": Upstream}
    upstream_terms._guard(up, "term-1", None)  # no project given: no check
    upstream_terms._guard(up, "term-1", ["AUTOONTOLOGY_SRC_BETA__ORDERS"])
    with pytest.raises(upstream_terms.TermNotInProject, match="term-1"):
        upstream_terms._guard(up, "term-1", ["autoontology_src_orders"])
    rows.clear()  # a Term no table represents: no project owns it
    with pytest.raises(upstream_terms.TermNotInProject):
        upstream_terms._guard(up, "term-1", ["autoontology_src_beta__orders"])


def test_the_term_databases_statement_joins_the_catalog():
    s = pytest.importorskip("auto_ontology.dal.schema")
    sql = str(upstream_semantic.term_databases_statement(s, "t1"))
    assert "catalog_database" in sql and "table__term" in sql


# --- exports ----------------------------------------------------------------------------------


def _marker(folder: Path, **extra) -> None:
    folder.mkdir(parents=True)
    (folder / "export.json").write_text(json.dumps({"revision": 1, **extra}))


def test_exports_go_to_the_projects_folder(env):  # noqa: F811
    ex.ensure_documents_source(env["store"], "hanbit-docs", env["docs"])
    beta = _beta(env)
    run(env)
    run({**env, "store": beta})
    mine = export_job.run_export(env["store"], env["settings"], client=env["client"])
    theirs = export_job.run_export(beta, env["settings"], client=env["client"])
    root = env["settings"].exports_dir
    assert mine.export_dir == root / "default" / "r0001"
    assert theirs.export_dir == root / "beta" / "r0001"
    marker = export_job.read_export_metadata(theirs.export_dir)
    assert marker["project"] == beta.id and marker["project_slug"] == "beta"
    assert marker["workspace"] == "Beta"
    assert export_job.marker_matches_project(marker, beta)
    assert not export_job.marker_matches_project(marker, env["store"].project())
    legacy = {k: v for k, v in marker.items() if k not in ("project", "project_slug")}
    assert export_job.marker_matches_project(legacy, env["store"].project())
    assert not export_job.marker_matches_project(legacy, beta)


def test_legacy_exports_move_into_the_default_project_once(store, isolated_env):
    settings = Settings.from_env()
    root = settings.exports_dir
    _marker(root / "r0001")
    _marker(root / "r0002.prev-20261101T000000-run")
    (root / ".r0003.run.tmp").mkdir()
    (root / "hanbit-docs").mkdir()  # a per-source job folder: not an export revision
    moved = export_job.migrate_legacy_exports(settings, store)
    assert moved == [".r0003.run.tmp", "r0001", "r0002.prev-20261101T000000-run"]
    target = root / "default"
    assert (target / "r0001" / "export.json").is_file()
    assert (target / "r0002.prev-20261101T000000-run").is_dir()
    assert (root / "hanbit-docs").is_dir() and not (root / "r0001").exists()
    record = json.loads((root / export_job.LAYOUT_MARKER).read_text())
    assert record["default_project"] == store.default_project_id and record["slug"] == "default"
    # idempotent: recorded, nothing more to do (even a new legacy-looking folder stays)
    _marker(root / "r0009")
    assert export_job.migrate_legacy_exports(settings, store) == []
    assert (root / "r0009").is_dir()


def test_legacy_export_migration_recovers_from_a_partial_run(store, isolated_env):
    settings = Settings.from_env()
    root = settings.exports_dir
    # a crash after r0001 was moved: r0002 is still in the root, no marker yet
    _marker(root / "default" / "r0001")
    _marker(root / "r0002")
    # ... and r0003 exists on both sides: never overwritten
    _marker(root / "r0003", side="root")
    _marker(root / "default" / "r0003", side="project")
    assert export_job.migrate_legacy_exports(settings, store) == ["r0002"]
    assert not (root / export_job.LAYOUT_MARKER).exists()  # not done: runs again
    assert json.loads((root / "r0003" / "export.json").read_text())["side"] == "root"
    assert json.loads((root / "default" / "r0003" / "export.json").read_text())["side"] == (
        "project"
    )
    # the user resolved the conflict: the next run completes and records it
    (root / "r0003" / "export.json").unlink()
    (root / "r0003").rmdir()
    assert export_job.migrate_legacy_exports(settings, store) == []
    assert (root / export_job.LAYOUT_MARKER).is_file()
    assert sorted(p.name for p in (root / "default").iterdir()) == ["r0001", "r0002", "r0003"]


def test_startup_recovery_migrates_then_restores_in_project_folders(store, isolated_env):
    settings = Settings.from_env()
    root = settings.exports_dir
    store.create_project("beta")
    # a legacy interrupted install: only its backup is left, in the root
    _marker(root / "r0001.prev-20261101T000000-run", side="legacy")
    # an interrupted install of beta
    _marker(root / "beta" / "r0004.prev-20261101T000000-run", side="beta")
    restored = export_job.recover_interrupted_exports(settings, store)
    assert sorted(restored) == [root / "beta" / "r0004", root / "default" / "r0001"]
    assert json.loads((root / "default" / "r0001" / "export.json").read_text())["side"] == (
        "legacy"
    )
    assert (root / export_job.LAYOUT_MARKER).is_file()


def test_project_exports_follow_a_slug_change(isolated_env):
    settings = Settings.from_env()
    _marker(settings.exports_dir / "old" / "r0001")
    assert export_job.rename_project_exports(settings, "old", "new")
    assert (settings.exports_dir / "new" / "r0001").is_dir()
    assert not export_job.rename_project_exports(settings, "old", "new")  # nothing left
    with pytest.raises(ValueError):
        export_job.project_exports_dir(settings, "../x")


# --- playground and agent tokens --------------------------------------------------------------


def _snapshot(view: ProjectStore, name: str) -> int:
    run_row = view.create_run(kind="build", source_id=None)
    return view.save_snapshot(run_row.id, merged.build(name))


def test_playground_scopes_are_per_project(store):
    from autoontology.playground.service import Playground, PlaygroundError

    beta = store.create_project("beta", name="Beta")
    _snapshot(store.project(), "default")
    _snapshot(store.project(), "default")
    _snapshot(beta, "Beta")

    def model_for(view):
        head = view.latest_snapshot()
        return head.revision, MergedOntology.from_dict(head.model)

    pg = Playground(store, model_for=model_for, llm_factory=None, embedder_factory=None)
    mine, theirs = pg.scope(), pg.scope("beta")
    assert (mine.project_id, mine.revision) == (store.default_project_id, 2)
    assert (theirs.project_id, theirs.revision, theirs.key) == (beta.id, 1, "Beta")
    # one call per project at a time; projects do not wait for each other
    with pg.acquire(mine), pg.acquire(theirs):
        with pytest.raises(PlaygroundError, match="playground call"):
            pg.acquire(mine)
    # conversations stay in their project
    cid = theirs.pg.create_conversation(theirs.key, "q")
    assert pg.pg_for(beta).get_conversation(cid) is not None
    assert pg.pg_for(None).get_conversation(cid) is None and pg.pg.get_conversation(cid) is None


def test_an_agent_token_reads_only_its_project(isolated_env):
    from autoontology.web.agent_api import AgentService, ToolError
    from autoontology.web.api import Api

    settings = Settings.from_env()
    api = Api(settings)
    store = api.store
    beta = store.create_project("beta", name="Beta")
    _snapshot(store.project(), "default")
    _snapshot(store.project(), "default")
    _snapshot(beta, "Beta")
    _marker(settings.exports_dir / "default" / "r0002")
    _marker(settings.exports_dir / "beta" / "r0001")
    service = AgentService(api, sparql=lambda: None)
    _, token_b = beta.agent_tokens().create("b")
    _, token_a = store.project().agent_tokens().create("a")
    assert token_b.project_id == beta.id

    summary = service.run_tool(token_b, "get_ontology_summary", {})
    assert summary["revision"] == 1 and summary["workspace"] == "Beta"
    assert service.run_tool(token_a, "get_ontology_summary", {})["revision"] == 2
    with pytest.raises(ToolError) as missing:
        service.run_tool(token_b, "get_ontology_summary", {"revision": 2})
    assert missing.value.status == 404  # r2 exists only in the other project
    revs = service.run_tool(token_b, "list_revisions", {})
    assert [r["revision"] for r in revs["revisions"]] == [1]
    exports = service.run_tool(token_b, "list_exports", {})
    assert [e["revision"] for e in exports["exports"]] == [1]
    assert [e["revision"] for e in service.run_tool(token_a, "list_exports", {})["exports"]] == [2]
    assert "project 'Beta'" in service.instructions(token_b)
    # a pin is a revision of the token's own project
    _, pinned = beta.agent_tokens().create("pinned", revision_pin=1)
    head = beta.get_snapshot(1, with_model=False)
    rows = service.run_tool(pinned, "list_revisions", {})["revisions"]
    assert [(r["revision"], r["run_id"]) for r in rows] == [(1, head.run_id)]
