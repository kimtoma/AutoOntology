# SPDX-License-Identifier: MIT
"""Schema v8: projects, the project-scoped store and the v7 -> v8 migration."""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

from autoontology.playground.store import IndexRow, PlaygroundStore
from autoontology.store.agent_tokens import AgentTokens
from autoontology.store.db import (
    DEFAULT_PROJECT_ID,
    MIGRATIONS,
    ProjectNotFound,
    SourceBusy,
    Store,
    project_slug_from_workspace,
)
from autoontology.store.decisions import Decision, DecisionStore

V8 = 8


class FakeModel:
    structured_sources = ["sales"]
    documents_sources: list[str] = []

    def __init__(self, tag: str = "m") -> None:
        self.tag = tag

    def to_dict(self):
        return {"tag": self.tag}

    def counts(self):
        return {"concepts": 1}


# --- a v7 store with a row in every table v8 touches -------------------------------------


def _make_v7(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, sql in MIGRATIONS:
        if version > 7:
            break
        assert isinstance(sql, str)
        for statement in (s.strip() for s in sql.split(";")):
            if statement:
                conn.execute(statement)
        conn.execute("INSERT INTO schema_version VALUES (?, 'x')", (version,))
    ex = conn.execute
    ex(
        "INSERT INTO sources (id, name, kind, locator, pg_database, catalog_database_id, "
        "created_at, updated_at) VALUES "
        "('s1', 'sales', 'files', '{}', 'autoontology_src_sales', 'db1', 'x', 'x'), "
        "('s2', 'docs', 'documents', '{}', NULL, NULL, 'x', 'x')"
    )
    ex(
        "INSERT INTO documents (id, source_id, path, title, mime, engine, sha256, bytes, "
        "created_at, updated_at) VALUES ('d1', 's2', 'a.md', 'a', 'text/markdown', 'md', "
        "'sha', 3, 'x', 'x')"
    )
    ex(
        "INSERT INTO runs (id, kind, source_id, status, started_at, params_json) VALUES "
        "('run-src', 'load', 's1', 'succeeded', '2026-01-01T00:00:00', '{}'), "
        "('run-b1', 'build', NULL, 'succeeded', '2026-01-02T00:00:00', '{}'), "
        "('run-b2', 'build', NULL, 'failed', '2026-01-03T00:00:00', '{}')"
    )
    ex("INSERT INTO steps (run_id, name, status) VALUES ('run-b1', 'snapshot', 'succeeded')")
    for i in (1, 2, 3):
        ex(
            "INSERT INTO ontology_snapshots (created_at, run_id, sources_json, counts_json, "
            "model_json) VALUES ('x', ?, '[]', '{}', ?)",
            (f"run-b{i}", f'{{"tag": "r{i}"}}'),
        )
    ex(
        "INSERT INTO review_decisions (kind, subject_key, decision, payload_json, decided_at, "
        "applied_revision) VALUES ('concept', 'c1', 'acknowledge', '{}', 'x', 2), "
        "('concept', 'c2', 'acknowledge', '{}', 'y', NULL)"
    )
    ex(
        "INSERT INTO agent_tokens (id, name, token_hash, revision_pin, created_at) VALUES "
        "('tok_a', 'pinned', 'hash-a', 2, 'x'), ('tok_b', 'latest', 'hash-b', NULL, 'y')"
    )
    ex(
        "INSERT INTO agent_calls (ts, token_id, surface, tool, ok, ms) "
        "VALUES ('x', 'tok_a', 'mcp', 'search', 1, 5)"
    )
    ex(
        "INSERT INTO playground_index (scope, element_id, element_type, text_sha, embedding, "
        "model) VALUES ('ops-ui', 'e1', 'class', 'sha', NULL, 'm')"
    )
    ex(
        "INSERT INTO playground_index_meta (scope, revision, model, elements, built_at) "
        "VALUES ('ops-ui', 3, 'm', 1, 'x')"
    )
    ex(
        "INSERT INTO playground_conversations (id, scope, title, created_at, updated_at) "
        "VALUES ('conv1', 'ops-ui', 'q', 'x', 'x')"
    )
    ex(
        "INSERT INTO playground_messages (conversation_id, role, content, created_at) "
        "VALUES ('conv1', 'user', 'hello', 'x')"
    )
    ex(
        "INSERT INTO source_leases (source_name, token, operation, pid, acquired_at, "
        "expires_at) VALUES ('sales', 'lt', 'load', ?, 'x', 9e12)",
        (os.getpid(),),
    )
    conn.commit()
    conn.close()


COUNTED = (
    "sources",
    "documents",
    "runs",
    "steps",
    "ontology_snapshots",
    "review_decisions",
    "agent_tokens",
    "agent_calls",
    "playground_index",
    "playground_index_meta",
    "playground_conversations",
    "playground_messages",
    "source_leases",
)


def _counts(path) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNTED}


@pytest.fixture
def v7(tmp_path):
    path = tmp_path / "autoontology.db"
    _make_v7(path)
    return path


def test_v7_store_migrates_to_v8_without_losing_a_row(v7):
    before = _counts(v7)
    store = Store(v7, default_project_slug="ops-ui")
    assert store.schema_version() == MIGRATIONS[-1][0] >= V8
    assert _counts(v7) == before
    with sqlite3.connect(v7) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in (
            "sources",
            "runs",
            "review_decisions",
            "agent_tokens",
            "source_leases",
            "playground_index",
            "playground_index_meta",
            "playground_conversations",
            "ontology_snapshots",
        ):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert "project_id" in cols, table
            missing = conn.execute(
                f"SELECT count(*) FROM {table} WHERE project_id IS NOT ?", (DEFAULT_PROJECT_ID,)
            ).fetchone()[0]
            assert missing == 0, table


def test_migrated_default_project_keeps_revisions_and_references(v7):
    store = Store(v7, default_project_slug="ops-ui")
    default = store.default_project()
    assert default.id == DEFAULT_PROJECT_ID and default.slug == "ops-ui"
    p = store.project("ops-ui")
    assert p.id == DEFAULT_PROJECT_ID

    assert [s.revision for s in p.list_snapshots()] == [3, 2, 1]
    assert p.latest_revision() == 3 and p.previous_revision(3) == 2
    assert p.get_snapshot(2).model == {"tag": "r2"} and p.get_snapshot(2).run_id == "run-b2"
    with sqlite3.connect(v7) as conn:  # the surrogate key equals the old revision
        assert conn.execute(
            "SELECT id, revision FROM ontology_snapshots ORDER BY id"
        ).fetchall() == [(1, 1), (2, 2), (3, 3)]
        seq = dict(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        assert seq["ontology_snapshots"] == 3 and seq["review_decisions"] == 2
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%__v8'").fetchall()

    decisions = {d.subject_key: d for d in p.decisions().list()}
    assert decisions["c1"].applied_revision == 2 and decisions["c2"].applied_revision is None
    tokens = {t.id: t for t in p.agent_tokens().list_tokens()}
    assert tokens["tok_a"].revision_pin == 2 and tokens["tok_b"].revision_pin is None
    assert tokens["tok_a"].project_id == DEFAULT_PROJECT_ID
    assert p.agent_tokens().usage()["total"] == 1

    pg = p.playground()
    assert pg.index_meta("ops-ui")["revision"] == 3
    assert set(pg.index_rows("ops-ui")) == {"e1"}
    assert [c["id"] for c in pg.list_conversations("ops-ui")] == ["conv1"]
    assert pg.messages("conv1")[0]["content"] == "hello"

    assert {s.name for s in p.list_sources()} == {"sales", "docs"}
    assert p.get_source("sales").project_id == DEFAULT_PROJECT_ID
    assert p.lease_holder("sales")["operation"] == "load"
    assert p.latest_project_run("build").id == "run-b2"
    assert {r.id for r in p.list_runs()} == {"run-src", "run-b1", "run-b2"}
    assert store.get_steps("run-b1")[0].name == "snapshot"

    # documents hang off the rebuilt sources table and still cascade with it
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    store.delete_source("s2")
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0

    # the next snapshot continues the default project's numbering
    assert p.save_snapshot("run-new", FakeModel()) == 4
    assert p.latest_revision() == 4


def test_migration_rerun_is_a_noop(v7):
    Store(v7, default_project_slug="ops-ui")
    after = _counts(v7)
    with sqlite3.connect(v7) as conn:
        schema = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    store = Store(v7, default_project_slug="ops-ui")
    assert store.migrate() == MIGRATIONS[-1][0]
    assert _counts(v7) == after
    with sqlite3.connect(v7) as conn:
        assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_v8_reapplied_over_v8_shaped_tables_keeps_rows(v7):
    """A store whose schema_version lost its v8 row (a downgrade) re-runs v8 safely."""
    store = Store(v7, default_project_slug="ops-ui")
    beta = store.create_project("beta", name="Beta")
    beta.add_source(name="sales", kind="files", locator="{}")
    beta.save_snapshot("run-x", FakeModel("beta"))
    before = _counts(v7)
    with sqlite3.connect(v7) as conn:
        conn.execute("DELETE FROM schema_version WHERE version >= 8")
    store = Store(v7, default_project_slug="ops-ui")
    assert _counts(v7) == before
    assert store.project("beta").get_source("sales") is not None
    assert store.project("beta").latest_revision() == 1
    assert store.project("ops-ui").latest_revision() == 3


# --- the default project's slug ----------------------------------------------------------


@pytest.mark.parametrize(
    ("workspace", "slug"),
    [
        ("ops-ui", "ops-ui"),
        ("default", "default"),
        ("My_Space.v2", "my-space-v2"),
        ("Hanbit", "hanbit"),
        ("2026", "p-2026"),
        ("x", "default"),
        ("a" * 40, "a" * 24),
    ],
)
def test_project_slug_from_workspace(workspace, slug):
    assert project_slug_from_workspace(workspace) == slug


def test_default_slug_is_set_once_from_the_workspace(tmp_path):
    path = tmp_path / "autoontology.db"
    assert Store(path).default_project().slug == "default"  # no workspace given: pending
    assert Store(path, default_project_slug="ops-ui").default_project().slug == "ops-ui"
    # later opens (another workspace value, a user rename) never override it
    assert Store(path, default_project_slug="other").default_project().slug == "ops-ui"
    store = Store(path)
    store.rename_project(DEFAULT_PROJECT_ID, slug="renamed", name="Renamed")
    assert Store(path, default_project_slug="ops-ui").default_project().slug == "renamed"


def test_for_home_passes_the_workspace_slug(tmp_path):
    store = Store.for_home(tmp_path, default_project_slug="Team Space")
    assert store.default_project().slug == "team-space"
    assert store.default_project().name == "Team Space"


# --- project management ------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "autoontology.db", default_project_slug="ops-ui")


@pytest.mark.parametrize("slug", ["", "a", "A-b", "1ab", "ab_c", "a" * 25, "ab c", "-ab"])
def test_create_project_validates_the_slug(store, slug):
    with pytest.raises(ValueError):
        store.create_project(slug)


def test_project_slugs_are_unique(store):
    store.create_project("beta")
    with pytest.raises(ValueError):
        store.create_project("beta")
    with pytest.raises(ValueError):
        store.rename_project("beta", slug="ops-ui")


def test_rename_archive_and_list(store):
    beta = store.create_project("beta", name="Beta", description="d")
    assert beta.project.name == "Beta" and beta.project.description == "d"
    renamed = store.rename_project("beta", name="Beta 2", slug="beta-two", description="e")
    assert (renamed.slug, renamed.name, renamed.description) == ("beta-two", "Beta 2", "e")
    assert store.get_project("beta") is None and store.get_project("beta-two").id == beta.id
    assert store.get_project(beta.id).slug == "beta-two"
    assert [p.slug for p in store.list_projects()] == ["ops-ui", "beta-two"]

    archived = store.archive_project("beta-two")
    assert archived.archived_at
    assert [p.slug for p in store.list_projects()] == ["ops-ui"]
    assert [p.slug for p in store.list_projects(include_archived=True)] == ["ops-ui", "beta-two"]
    assert store.project("beta-two").project.archived_at  # still addressable by slug
    store.unarchive_project("beta-two")
    assert [p.slug for p in store.list_projects()] == ["ops-ui", "beta-two"]
    with pytest.raises(ValueError):
        store.archive_project(DEFAULT_PROJECT_ID)


def test_unknown_project_raises(store):
    with pytest.raises(ProjectNotFound):
        store.project("nope")


# --- isolation between projects ----------------------------------------------------------


@pytest.fixture
def two(store):
    return SimpleNamespace(store=store, a=store.project("ops-ui"), b=store.create_project("beta"))


def test_same_source_name_and_revision_numbers_per_project(two):
    sa = two.a.add_source(name="sales", kind="files", locator="{}")
    sb = two.b.add_source(name="sales", kind="files", locator="{}")
    assert sa.id != sb.id and sb.project_id == two.b.id
    with pytest.raises(ValueError):
        two.b.add_source(name="sales", kind="files", locator="{}")
    assert two.a.get_source("sales").id == sa.id and two.b.get_source("sales").id == sb.id
    assert [s.id for s in two.b.list_sources()] == [sb.id]
    assert two.a.get_source_by_id(sb.id) is None and two.b.get_source_by_id(sb.id).id == sb.id

    assert two.a.save_snapshot("ra", FakeModel("a")) == 1
    assert two.b.save_snapshot("rb", FakeModel("b")) == 1
    assert two.b.save_snapshot("rb2", FakeModel("b2")) == 2
    assert two.a.latest_revision() == 1 and two.b.latest_revision() == 2
    assert two.a.get_snapshot(1).model == {"tag": "a"}
    assert two.b.get_snapshot(1).model == {"tag": "b"}
    assert two.a.get_snapshot(2) is None
    assert two.b.latest_snapshot().run_id == "rb2" and two.b.previous_revision(2) == 1
    assert two.a.previous_revision(1) is None
    assert [s.revision for s in two.b.list_snapshots()] == [2, 1]
    assert two.b.get_snapshot(1).project_id == two.b.id


def test_runs_are_scoped(two):
    sb = two.b.add_source(name="sales", kind="files", locator="{}")
    run_b = two.b.create_run(kind="build", source_id=None)
    run_src = two.store.create_run(kind="load", source_id=sb.id)  # project from the source
    assert run_src.project_id == two.b.id
    run_a = two.a.create_run(kind="build", source_id=None)
    assert two.a.latest_project_run("build").id == run_a.id
    assert two.b.latest_project_run("build").id == run_b.id
    assert {r.id for r in two.b.list_runs()} == {run_b.id, run_src.id}
    assert {r.id for r in two.a.list_runs()} == {run_a.id}
    assert {r.id for r in two.a.active_runs()} == {run_a.id}
    assert two.a.get_run(run_b.id) is None and two.b.get_run(run_b.id).id == run_b.id
    assert two.store.latest_workspace_run("build").id == run_a.id  # compat: default project
    with pytest.raises(ValueError):
        two.a.create_run(kind="load", source_id=sb.id)  # another project's source


def test_decisions_are_scoped(two):
    da, db_ = two.a.decisions(), two.b.decisions()
    da.put(Decision(kind="concept", subject_key="c1", decision="acknowledge", note="a"))
    db_.put(Decision(kind="concept", subject_key="c1", decision="acknowledge", note="b"))
    assert da.get("concept", "c1").note == "a" and db_.get("concept", "c1").note == "b"
    da.put(
        Decision(
            kind="alignment",
            subject_key="c1",
            decision="reject",
        )
    )  # replaces a's concept decision only
    assert da.get("concept", "c1") is None and db_.get("concept", "c1").note == "b"
    db_.mark_applied(db_.list(), 7)
    assert db_.get("concept", "c1").applied_revision == 7
    assert da.get("alignment", "c1").applied_revision is None
    assert da.delete("alignment", "c1") and db_.get("concept", "c1") is not None
    assert DecisionStore(two.store).list() == da.list()  # compat: default project


def test_agent_tokens_are_scoped(two):
    ta, tb = two.a.agent_tokens(), two.b.agent_tokens()
    raw_b, rec_b = tb.create("b", revision_pin=1)
    _, rec_a = ta.create("a")
    assert [t.id for t in tb.list_tokens()] == [rec_b.id]
    assert ta.get(rec_b.id) is None
    with pytest.raises(Exception) as err:
        ta.revoke(rec_b.id)
    assert getattr(err.value, "status", None) == 404
    found = AgentTokens(two.store).lookup(raw_b)  # lookup is global: the token names its project
    assert found.id == rec_b.id and found.project_id == two.b.id
    tb.record_call(rec_b.id, "mcp", "search", True, 3)
    ta.record_call(rec_a.id, "rest", "search", True, 3)
    assert tb.usage()["total"] == 1 and ta.usage()["total"] == 1
    assert [t.id for t in AgentTokens(two.store).list_tokens()] == [rec_a.id]  # compat


def test_playground_is_scoped(two):
    pa, pb = two.a.playground(), two.b.playground()
    rows = [IndexRow("e1", "class", "sha", [0.5], "m")]
    pa.replace_index("ops-ui", rows, revision=1, model="m")
    assert pb.index_meta("ops-ui") is None and pb.index_rows("ops-ui") == {}
    pb.replace_index("ops-ui", [], revision=4, model="m")
    assert pa.index_meta("ops-ui")["revision"] == 1 and pb.index_meta("ops-ui")["revision"] == 4
    cid = pa.create_conversation("ops-ui", "q")
    assert pb.list_conversations("ops-ui") == [] and pb.get_conversation(cid) is None
    assert pb.delete_conversation(cid) is False and pa.get_conversation(cid) is not None
    assert PlaygroundStore(two.store).list_conversations("ops-ui")[0]["id"] == cid  # compat


def test_leases_are_scoped(two):
    with two.a.lease(["sales"], "load"):
        with two.b.lease(["sales"], "load"):  # same name, other project: no conflict
            assert two.b.lease_holder("sales")["operation"] == "load"
        with pytest.raises(SourceBusy):
            two.a.acquire_leases(["sales"], "build")
        assert two.b.lease_holder("sales") is None
    assert two.a.lease_holder("sales") is None


def test_compat_wrappers_use_the_default_project(two):
    s = two.store.add_source(name="sales", kind="files", locator="{}")
    assert s.project_id == DEFAULT_PROJECT_ID
    two.b.add_source(name="other", kind="files", locator="{}")
    assert [x.name for x in two.store.list_sources()] == ["sales"]
    assert two.store.get_source("other") is None
    assert two.store.save_snapshot("r", FakeModel()) == 1
    two.b.save_snapshot("r", FakeModel())
    two.b.save_snapshot("r", FakeModel())
    assert two.store.latest_revision() == 1
    assert two.store.latest_snapshot().revision == 1
