# SPDX-License-Identifier: MIT
import sqlite3

import pytest

from autoontology.store.db import MIGRATIONS, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "autoontology.db")


def test_schema_created_versioned_and_wal(store, tmp_path):
    assert store.schema_version() == MIGRATIONS[-1][0]
    assert store.migrate() == MIGRATIONS[-1][0]  # idempotent
    conn = sqlite3.connect(tmp_path / "autoontology.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"schema_version", "sources", "runs", "steps"} <= tables
    assert (tmp_path / "autoontology.db").stat().st_mode & 0o077 == 0


def test_source_crud(store):
    src = store.add_source(
        name="hanbit", kind="files", locator="{}", pg_database="autoontology_src_hanbit"
    )
    assert store.get_source("hanbit") == src
    assert store.get_source_by_id(src.id) == src
    with pytest.raises(ValueError, match="already exists"):
        store.add_source(name="hanbit", kind="files", locator="{}")
    store.set_catalog_database_id(src.id, "db-1")
    assert store.get_source("hanbit").catalog_database_id == "db-1"
    assert [s.name for s in store.list_sources()] == ["hanbit"]
    run = store.create_run(kind="build", source_id=src.id)
    store.start_step(run.id, "load")
    store.delete_source(src.id)
    assert store.get_source("hanbit") is None
    assert store.get_run(run.id) is None and store.get_steps(run.id) == []


def test_kind_check_constraint(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.add_source(name="x", kind="bogus", locator="{}")  # type: ignore[arg-type]


def test_runs_and_steps(store):
    src = store.add_source(name="a", kind="files", locator="{}")
    r1 = store.create_run(kind="build", source_id=src.id, params={"resume": False})
    assert r1.status == "running" and r1.params == {"resume": False}
    store.start_step(r1.id, "load")
    store.finish_step(r1.id, "load", status="succeeded", detail={"tables": 3})
    store.start_step(r1.id, "ingest")
    store.finish_step(r1.id, "ingest", status="failed", detail={"error": "boom"})
    store.update_run(r1.id, status="failed", error="boom")
    assert store.succeeded_steps(r1.id) == {"load"}
    steps = store.get_steps(r1.id)
    assert [(s.name, s.status) for s in steps] == [("load", "succeeded"), ("ingest", "failed")]
    assert steps[0].detail == {"tables": 3}
    # restarting a step resets it to running (resume)
    store.start_step(r1.id, "ingest")
    assert store.get_steps(r1.id)[1].status == "running"
    run = store.get_run(r1.id)
    assert run.status == "failed" and run.error == "boom" and run.finished_at
    assert store.active_runs() == []
    r2 = store.create_run(kind="ingest", source_id=src.id)
    assert store.latest_run(src.id).id == r2.id
    assert store.latest_run(src.id, "build").id == r1.id
    assert [r.id for r in store.active_runs(src.id)] == [r2.id]
    assert [r.id for r in store.list_runs()] == [r2.id, r1.id]
    assert store.list_runs(source_id="nope") == []


def test_interrupted_migration_leaves_no_partial_schema(tmp_path, monkeypatch):
    """Finding #15: a failure after a migration's DDL but before its version row must roll
    the DDL back, so the retry applies the migration cleanly."""
    import sqlite3

    from autoontology.store import db

    path = tmp_path / "s.db"
    Store(path)  # all shipped migrations
    base = db.MIGRATIONS[-1][0]
    broken = (base + 1, "ALTER TABLE runs ADD COLUMN extra TEXT; INSERT INTO no_such VALUES (1)")
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, broken])
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
        version = conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
    assert "extra" not in cols and version == base
    fixed = (base + 1, "ALTER TABLE runs ADD COLUMN extra TEXT")
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS[:-1], fixed])
    assert Store(path).schema_version() == base + 1


def test_interrupted_initial_migration_leaves_no_tables(tmp_path, monkeypatch):
    import sqlite3

    from autoontology.store import db

    path = tmp_path / "fresh.db"
    first_sql = db.MIGRATIONS[0][1] + "; INSERT INTO no_such VALUES (1)"
    monkeypatch.setattr(db, "MIGRATIONS", [(1, first_sql), *db.MIGRATIONS[1:]])
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sources" not in tables and "runs" not in tables


def test_concurrent_migrators_apply_each_migration_once(tmp_path):
    import threading

    from autoontology.store.db import MIGRATIONS

    path = tmp_path / "c.db"
    errors = []

    def go():
        try:
            Store(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert Store(path).schema_version() == MIGRATIONS[-1][0]


def test_schema_has_every_table_and_no_playground_tests(store):
    with store._conn() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(alignments)")}
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(playground_messages)")}
        conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(playground_conversations)")}
    assert {
        "sources",
        "runs",
        "steps",
        "source_leases",
        "file_manifests",
        "semantic_baselines",
        "ontology_snapshots",
        "documents",
        "chunks",
        "concepts",
        "relations",
        "evidence",
        "term_embeddings",
        "alignments",
        "alignment_previews",
        "term_alias_additions",
        "term_refresh_pending",
        "term_legacy_descriptions",
        "playground_index",
        "playground_index_meta",
        "playground_conversations",
        "playground_messages",
        "schema_version",
    } <= tables
    assert not {"playground_tests", "playground_test_runs", "ontology_revision"} & tables
    assert {"dry_run", "proposal_json"} <= cols and "incomplete" in msg_cols
    assert "scope" in conv_cols and "source_id" not in conv_cols  # v2: workspace-scoped
    assert store.schema_version() == 4


def _hold_write_lock(path):
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def test_lock_wait_is_bounded_by_the_store_deadline(tmp_path, monkeypatch):
    """A lock that never clears fails after AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S with a clear
    error, not after every retry's full busy timeout (~30 min)."""
    import time

    from autoontology.store.db import StoreLocked

    store = Store(tmp_path / "s.db")
    monkeypatch.setenv("AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S", "0.5")
    holder = _hold_write_lock(store.path)
    started = time.monotonic()
    try:
        with pytest.raises(StoreLocked, match="store is locked .*write transaction"):
            store.acquire_leases(["demo"], "build")
    finally:
        holder.rollback()
        holder.close()
    assert time.monotonic() - started < 5
    assert store.acquire_leases(["demo"], "build")  # fine once released


def test_lock_wait_succeeds_after_release(tmp_path, monkeypatch):
    import threading

    store = Store(tmp_path / "s.db")
    monkeypatch.setenv("AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S", "10")
    holder = _hold_write_lock(store.path)

    def release():
        holder.rollback()
        holder.close()

    threading.Timer(0.3, release).start()
    assert store.acquire_leases(["demo"], "build")


def test_migration_v4_notes_the_legacy_description_records(tmp_path):
    """Round 6: the Terms whose description an older apply set without the text are noted
    at upgrade in ``term_legacy_descriptions`` (which apply never writes)."""
    import json

    path = tmp_path / "autoontology.db"
    Store(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE term_legacy_descriptions")
    conn.execute("DELETE FROM schema_version WHERE version = 4")
    for i, applied in enumerate(
        [
            {"term_id": "t-old", "description_set": True},
            {"term_id": "t-new", "description_set": True, "description": "정의"},
            {"term_id": "t-none", "description_set": False},
        ]
    ):
        conn.execute(
            "INSERT INTO alignments (source_id, concept_id, concept_name, term_id, relation, "
            "applied_json, run_id, created_at) VALUES ('s', ?, 'c', ?, 'same', ?, 'r', 'x')",
            (f"c{i}", applied["term_id"], json.dumps(applied)),
        )
    conn.commit()
    conn.close()
    assert Store(path).schema_version() == 4
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT term_id, confirmed_at FROM term_legacy_descriptions").fetchall()
    assert rows == [("t-old", None)]
