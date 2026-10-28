# SPDX-License-Identifier: MIT
import json
import os

import pytest

from autoontology.config import Settings
from autoontology.engine import jobs
from autoontology.engine.client import BackendUnavailable
from autoontology.store.db import Store
from autoontology.structured import pipeline
from autoontology.structured.loaders import LoadReport, TableReport


class FakeClient:
    def __init__(self):
        self.connections: list[str] = []
        self.created: list[dict] = []
        self.databases: dict[str, str] = {}
        self.updated: list[tuple[str, dict]] = []
        self.down = False
        self.nodes = [
            {"id": "t1", "name": "Customer", "description": "A buyer of goods"},
            {"id": "t2", "name": "Order", "description": "A purchase"},
        ]

    def health(self):
        if self.down:
            raise BackendUnavailable("upstream backend not reachable; `uv run autoontology up`")
        return {"status": "ok"}

    def connection_exists(self, name):
        return name in self.connections

    def create_connection(self, conn):
        self.created.append(conn)
        self.connections.append(conn["database"])
        return conn

    def update_connection(self, name, conn):
        self.updated.append((name, conn))
        return conn

    def database_id_by_name(self, name):
        return self.databases.get(name)

    def semantic_graph(self, database_id=None):
        return {"nodes": self.nodes, "links": [{"type": "SEMANTIC_FK"}, {"type": "HAS_ATTRIBUTE"}]}

    def schemas(self, database_id):
        return [{"id": "s1", "schema_name": "public"}]

    def get_term(self, term_id):
        node = next(n for n in self.nodes if n["id"] == term_id)
        return {
            **node,
            "tables": [{"id": f"tb-{term_id}", "name": node["name"].lower(), "schema_id": "s1"}],
        }

    def term_column_attributes(self, term_id):
        if term_id in getattr(self, "bare_terms", ()):
            return []
        return [
            {
                "id": f"a-{term_id}",
                "primary_column": {"table_id": f"tb-{term_id}", "column_name": "id"},
            }
        ]

    def list_terms(self, **params):
        return {"data": [], "count": 0, "total": 4}

    def export_model(self, ids, fmt="auto_ontology", include_ontology=False):
        return f"{fmt}:{','.join(ids)}:{include_ontology}".encode()


@pytest.fixture(autouse=True)
def _service_reachable(monkeypatch):
    """Job sequencing must not depend on a live service: the default chat provider (ollama)
    goes through the service's /v1 router, so ``require_llm_proxy`` asks ``service_alive``.
    Pretend it answers; the gate itself is covered by
    ``test_build_requires_service_when_chat_goes_through_proxy`` (which overrides this)."""
    monkeypatch.setattr(jobs, "service_alive", lambda settings: True)


@pytest.fixture
def env(isolated_env, monkeypatch, tmp_path):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    data = tmp_path / "files"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n")
    pipeline.add_source(store, str(data), name="demo")
    client = FakeClient()
    calls: list[str] = []

    def fake_load(files, pg_database, settings, **kw):
        calls.append(f"load:{pg_database}:{len(files)}")
        return LoadReport(
            pg_database=pg_database,
            tables=[TableReport("a", str(files[0]), None, 1, [{"name": "x", "type": "bigint"}])],
        )

    def fake_ingest(db, settings):
        calls.append(f"ingest:{db}")
        client.databases[db] = "db-1"
        return {"tables": 1}

    def fake_compile(db, settings):
        calls.append(f"compile:{db}")
        return {"tables_compiled": 1}

    monkeypatch.setattr("autoontology.structured.loaders.load_files_to_postgres", fake_load)
    monkeypatch.setattr(jobs, "upstream_ingest", fake_ingest)
    monkeypatch.setattr(jobs, "upstream_compile", fake_compile)
    monkeypatch.setattr(
        jobs, "ensure_vector_tables", lambda settings: {"embedding_dim": 1024, "vector_tables": {}}
    )
    monkeypatch.setattr(jobs, "catalog_snapshot", lambda db, settings: {})  # no upstream DAL
    return settings, store, client, calls


def build(env, **kw):
    settings, store, client, _ = env
    kw.setdefault("resume", False)
    return jobs.run_structured("demo", settings=settings, store=store, client=client, **kw)


def test_full_build_sequence_and_exports(env):
    settings, store, client, calls = env
    result = build(env)
    assert calls == [
        "load:autoontology_src_demo:1",
        "ingest:autoontology_src_demo",
        "compile:autoontology_src_demo",
    ]
    assert result.status == "succeeded" and list(result.steps) == list(jobs.STEP_ORDER)
    conn = client.created[0]
    assert conn["type"] == "postgres" and conn["database"] == "autoontology_src_demo"
    assert conn["password"]  # upstream requires a non-empty password
    run = store.get_run(result.run_id)
    assert run.status == "succeeded" and run.finished_at
    assert [(s.name, s.status) for s in store.get_steps(run.id)] == [
        (n, "succeeded") for n in jobs.STEP_ORDER
    ]
    assert store.get_source("demo").catalog_database_id == "db-1"
    out = settings.exports_dir / "demo" / run.id
    assert result.export_dir == out
    assert (out / "model.yaml").read_bytes() == b"auto_ontology:db-1:False"
    assert (out / "ossie.zip").read_bytes() == b"ossie:db-1:True"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["semantic"]["semantic_fk_links"] == 1
    assert summary["semantic"]["terms_total"] == 2  # this database's Terms, not catalog-wide
    assert summary["steps"]["load"]["rows"] == 1
    assert jobs.jobs_log_path(settings).is_file()


def test_failure_persists_error_and_resume_skips_succeeded(env, monkeypatch):
    settings, store, client, calls = env

    def boom(db, settings):
        raise RuntimeError("llm exploded")

    monkeypatch.setattr(jobs, "upstream_compile", boom)
    with pytest.raises(jobs.JobError) as info:
        build(env)
    err = info.value
    assert err.step == "compile" and "llm exploded" in str(err)
    assert err.log_path == jobs.jobs_log_path(settings)
    run = store.get_run(err.run_id)
    assert run.status == "failed" and "compile: RuntimeError: llm exploded" in run.error
    steps = {s.name: s for s in store.get_steps(run.id)}
    assert (
        steps["compile"].status == "failed" and "llm exploded" in steps["compile"].detail["error"]
    )
    assert "export" not in steps

    calls.clear()
    monkeypatch.setattr(jobs, "upstream_compile", lambda db, s: {"tables_compiled": 2})
    result = build(env, resume=True)
    assert result.run_id == err.run_id and result.status == "succeeded"
    assert calls == []  # load / ingest not re-run
    assert set(result.details) == {"compile", "export"}
    assert store.get_run(err.run_id).status == "succeeded"
    assert len(store.list_runs()) == 1


def test_resume_without_previous_run_starts_new(env):
    assert build(env, resume=True).status == "succeeded"


def test_new_build_without_resume_reruns_everything(env):
    first = build(env)
    _, store, client, calls = env
    calls.clear()
    second = build(env)
    assert second.run_id != first.run_id
    assert calls[0].startswith("load:") and len(client.created) == 1  # connection reused
    assert store.get_steps(second.run_id)[1].detail["action"] == "updated"
    assert client.updated == [("autoontology_src_demo", client.created[0])]  # always PUT


def test_skip_flags_record_skipped_steps(env):
    _, store, _, calls = env
    result = build(env, skip_compile=True, skip_export=True)
    assert result.steps["compile"] == "skipped" and result.steps["export"] == "skipped"
    assert not any(c.startswith("compile") for c in calls)
    assert result.export_dir is None
    steps = {s.name: s for s in store.get_steps(result.run_id)}
    assert steps["compile"].detail == {"reason": "--skip-compile"}
    # resume later runs the steps that were skipped
    calls.clear()
    resumed = build(env, resume=True)
    assert calls == ["compile:autoontology_src_demo"] and resumed.export_dir


def test_backend_down_is_clear_error(env):
    _, store, client, _ = env
    client.down = True
    with pytest.raises(BackendUnavailable, match="autoontology up"):
        build(env)
    assert store.list_runs() == []


def test_active_run_blocks_and_stale_run_is_reaped(env):
    _, store, _, _ = env
    src = store.get_source("demo")
    live = store.create_run(kind="build", source_id=src.id, params={"pid": os.getppid()})
    with pytest.raises(jobs.JobError, match="active"):
        build(env)
    store.update_run(live.id, status="failed")
    dead = store.create_run(kind="build", source_id=src.id, params={"pid": 2**22 + 12345})
    store.start_step(dead.id, "ingest")
    assert build(env).status == "succeeded"
    reaped = store.get_run(dead.id)
    assert reaped.status == "failed" and "interrupted" in reaped.error
    assert store.get_steps(dead.id)[0].status == "failed"


def test_unknown_source(env):
    settings, store, client, _ = env
    with pytest.raises(jobs.JobError, match="unknown source"):
        jobs.run_structured("nope", resume=False, settings=settings, store=store, client=client)


def test_ingest_job_for_source_and_skip_when_build_active(env):
    settings, store, client, calls = env
    result = jobs.run_ingest_job(
        "AUTOONTOLOGY_SRC_DEMO", settings=settings, store=store, client=client
    )
    assert result.status == "succeeded" and calls == ["ingest:AUTOONTOLOGY_SRC_DEMO"]
    run = store.get_run(result.run_id)
    assert run.kind == "ingest" and run.source_id == store.get_source("demo").id
    src = store.get_source("demo")
    store.create_run(kind="build", source_id=src.id, params={"pid": os.getppid()})
    assert (
        jobs.run_ingest_job("autoontology_src_demo", settings=settings, store=store, client=client)
        is None
    )


def test_ingest_job_for_unmanaged_connection(env):
    settings, store, client, calls = env
    result = jobs.run_ingest_job("other_db", settings=settings, store=store, client=client)
    assert result.status == "succeeded"
    assert store.get_run(result.run_id).source_id is None


def test_short_error_keeps_first_line_and_caps():
    exc = RuntimeError("expected 2048 dimensions, not 1024\n[SQL: INSERT ...]")
    assert jobs.short_error(exc) == "RuntimeError: expected 2048 dimensions, not 1024"
    assert len(jobs.short_error(ValueError("x" * 1000))) == 600  # room for recovery hints
    assert jobs.short_error(KeyError()) == "KeyError"


def test_console_formatter_hides_traceback():
    import logging

    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    assert jobs._ConsoleFormatter("%(message)s").format(record) == "failed"
    assert "Traceback" in logging.Formatter("%(message)s").format(record)


def test_vdb_store_guard():
    class Good:
        def _get_store(self):
            return None

    class Gone:
        pass

    jobs.require_vdb_store_api(Good)
    with pytest.raises(RuntimeError, match="_get_store"):
        jobs.require_vdb_store_api(Gone)


def test_vdb_store_guard_holds_for_pinned_upstream():
    from auto_ontology.vdb.postgres import PostgresVDB

    jobs.require_vdb_store_api(PostgresVDB)


def test_semantic_counts_reads_upstream_relationship_types():
    class Client:
        def semantic_graph(self, database_id):
            # Shape returned by upstream /api/exploration/semantic-graph (T2 live run).
            return {
                "nodes": [{"name": n, "description": "d"} for n in "abc"],
                "links": [
                    {
                        "source": "a",
                        "target": "b",
                        "relationship_types": ["HAS_ATTRIBUTE", "REPRESENTS", "SEMANTIC_FK"],
                    },
                    {"source": "b", "target": "c", "relationship_types": ["HAS_ATTRIBUTE"]},
                ],
            }

        def list_terms(self, limit=1):
            return {"total": 99}  # catalog-wide: must not be used

    assert jobs.semantic_counts(Client(), "db") == {
        "graph_nodes": 3,
        "graph_links": 2,
        "semantic_fk_links": 1,
        "terms_total": 3,
        "fallback_terms": [],
    }


def test_fallback_only_terms_fail_compile(env):
    settings, store, client, _ = env
    client.nodes = [
        {
            "id": "t1",
            "name": "Contracts",
            "description": "Business entity represented by table contracts",
        },
        {"id": "t2", "name": "Customers", "description": ""},
    ]
    with pytest.raises(jobs.JobError, match="LLM calls failed"):
        build(env)
    run = store.latest_run(store.get_source("demo").id)
    steps = {s.name: s for s in store.get_steps(run.id)}
    assert steps["compile"].status == "failed" and "fallback" in steps["compile"].detail["error"]
    assert run.status == "failed"


def test_one_real_term_passes_the_gate():
    jobs.check_compile_quality(
        {
            "nodes": [
                {"name": "A", "description": "Business entity represented by table a"},
                {"name": "B", "description": "고객 정보"},
            ]
        },
        tables_compiled=2,
    )
    jobs.check_compile_quality({"nodes": []}, tables_compiled=0)  # nothing yet, nothing compiled
    with pytest.raises(jobs.CompileQualityError):
        jobs.check_compile_quality({"nodes": []}, tables_compiled=1)


def test_build_requires_service_when_chat_goes_through_proxy(env, monkeypatch):
    settings, store, client, calls = env
    settings = Settings.from_env()
    assert jobs.uses_llm_proxy(settings)
    monkeypatch.setattr(jobs, "service_alive", lambda s: False)
    with pytest.raises(jobs.JobError, match="autoontology up"):
        jobs.run_structured("demo", resume=False, settings=settings, store=store, client=client)
    assert calls == []  # nothing ran
    monkeypatch.setattr(jobs, "service_alive", lambda s: True)
    assert (
        jobs.run_structured(
            "demo", resume=False, settings=settings, store=store, client=client
        ).status
        == "succeeded"
    )


def test_both_chat_providers_go_through_the_service(isolated_env, monkeypatch):
    assert jobs.uses_llm_proxy(Settings.from_env())
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    assert jobs.uses_llm_proxy(Settings.from_env())


def test_prepare_upstream_env_applies_once_under_lock(isolated_env, monkeypatch):
    import threading

    monkeypatch.setattr(jobs, "_APPLIED_ENV", None)
    applied = []
    monkeypatch.setattr(jobs, "apply_model_env", lambda s: applied.append(1))
    settings = Settings.from_env()
    threads = [
        threading.Thread(target=jobs.prepare_upstream_env, args=(settings,)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert applied == [1]
    assert os.environ["INGESTION_SERVICE_URL"] == settings.service_url


def test_vector_tables_hold_an_advisory_lock(isolated_env, monkeypatch):
    from autoontology.engine import pg

    events = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            if "advisory" in sql:
                events.append((sql.split("(")[0].split()[-1], params[0]))
            return self

        def fetchone(self):
            return (True,)

    monkeypatch.setattr(pg, "connect", lambda *a, **k: Conn())
    monkeypatch.setattr(jobs, "prepare_upstream_env", lambda s: None)
    monkeypatch.setattr(jobs, "embedding_dimension", lambda s: 1024)

    def locked(settings, dim):
        events.append(("work", dim))
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "_ensure_vector_tables_locked", locked)
    with pytest.raises(RuntimeError):
        jobs.ensure_vector_tables(Settings.from_env())
    key = jobs.VECTOR_TABLES_LOCK_KEY
    assert events == [("pg_try_advisory_lock", key), ("work", 1024), ("pg_advisory_unlock", key)]


def test_gate_still_fails_on_resume_with_nothing_compiled(env, monkeypatch):
    settings, store, client, _ = env
    client.nodes = [
        {
            "id": "t1",
            "name": "Contracts",
            "description": "Business entity represented by table contracts",
        }
    ]
    with pytest.raises(jobs.JobError, match="LLM calls failed"):
        build(env)
    # Upstream compile is incremental: the re-run compiles 0 tables.
    monkeypatch.setattr(jobs, "upstream_compile", lambda db, s: {"tables_compiled": 0})
    with pytest.raises(jobs.JobError, match="source rm demo --drop-db") as err:
        build(env, resume=True)
    assert "build --source demo" in str(err.value)
    run = store.latest_run(store.get_source("demo").id)
    assert run.status == "failed"


def test_partial_fallback_is_warned_and_recorded(env, caplog):
    settings, store, client, _ = env
    client.nodes = [
        {"id": "t1", "name": "Customer", "description": "A buyer"},
        {
            "id": "t2",
            "name": "Invoices",
            "description": "Business entity represented by table invoices",
        },
    ]
    with caplog.at_level("WARNING", logger="autoontology.jobs"):
        result = build(env)
    assert result.status == "succeeded"
    assert "1 of 2 Term(s) are upstream fallbacks" in caplog.text and "Invoices" in caplog.text
    assert result.details["compile"]["fallback_terms"] == ["Invoices"]
    summary = json.loads((result.export_dir / "summary.json").read_text())
    assert summary["semantic"]["fallback_terms"] == ["Invoices"]


def test_advisory_lock_times_out_with_named_error(monkeypatch):
    class Busy:
        calls = 0

        def execute(self, sql, params=None):
            assert "pg_try_advisory_lock" in sql
            Busy.calls += 1
            return self

        def fetchone(self):
            return (False,)

    with pytest.raises(jobs.JobError, match=r"advisory lock 42 \(vdb vector tables\)"):
        jobs.acquire_advisory_lock(Busy(), 42, "vdb vector tables", timeout=0.05, poll=0.01)
    assert Busy.calls >= 2


def test_advisory_lock_timeout_from_env(monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_LOCK_TIMEOUT_S", "7")
    assert jobs.lock_timeout_s() == 7.0
    monkeypatch.delenv("AUTOONTOLOGY_LOCK_TIMEOUT_S")
    assert jobs.lock_timeout_s() == 300.0


# --- source leases (review #11) ------------------------------------------------------


def test_concurrent_builds_one_fails_busy(env, monkeypatch):
    import threading

    settings, store, client, calls = env
    entered, release = threading.Event(), threading.Event()

    def slow_compile(db, settings):
        entered.set()
        assert release.wait(10)
        return {"tables_compiled": 1}

    monkeypatch.setattr(jobs, "upstream_compile", slow_compile)
    results = []
    first = threading.Thread(target=lambda: results.append(build(env)))
    first.start()
    try:
        assert entered.wait(10)
        with pytest.raises(jobs.JobError, match="already has an active build"):
            build(env)
        # source rm overlapping the build is refused too (nothing torn down)
        from autoontology.structured.remove import RemoveError, remove_source

        with pytest.raises(RemoveError, match="already has an active build"):
            remove_source(settings, store, store.get_source("demo"), drop_db=True)
        assert store.get_source("demo") is not None
    finally:
        release.set()
        first.join(10)
    assert results and results[0].status == "succeeded"
    assert len([r for r in store.list_runs() if r.kind == "build"]) == 1
    assert store.lease_holder("demo") is None  # released
    assert build(env).status == "succeeded"  # free again


def test_build_refused_while_source_is_being_removed(env):
    settings, store, client, calls = env
    with store.lease(["demo"], "remove"):
        with pytest.raises(jobs.JobError, match="active remove"):
            build(env)
    assert calls == []


def test_stale_leases_are_reclaimed(env):
    import subprocess
    import sys
    import time

    settings, store, client, calls = env
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    token = store.acquire_leases(["demo"], "build")
    with store._conn() as conn:  # holder pid is gone
        conn.execute("UPDATE source_leases SET pid = ? WHERE token = ?", (proc.pid, token))
    assert store.lease_holder("demo") is None
    assert build(env).status == "succeeded"
    token = store.acquire_leases(["demo"], "extract", ttl=0.01)  # live pid, no heartbeat
    time.sleep(0.05)
    # wave 2 #6: expiry never hands a live holder's source to someone else
    with pytest.raises(jobs.JobError, match="active extract"):
        build(env)
    store.release_lease(token)
    assert build(env).status == "succeeded"


def test_lease_heartbeat_keeps_a_long_hold_alive(env):
    import time

    settings, store, client, calls = env
    with store.lease(["demo"], "build", ttl=0.3):
        time.sleep(0.6)  # past the ttl: only the heartbeat keeps it
        holder = store.lease_holder("demo")
        assert holder is not None and holder["operation"] == "build"
        with pytest.raises(jobs.SourceBusy):
            store.acquire_leases(["demo", "other"], "align")
    assert store.lease_holder("other") is None  # all or nothing


def test_ingest_shim_skips_while_a_build_holds_the_source(env):
    settings, store, client, calls = env
    with store.lease(["demo"], "build"):
        assert (
            jobs.run_ingest_job(
                "autoontology_src_demo", settings=settings, store=store, client=client
            )
            is None
        )
    assert calls == []


def test_resume_reruns_steps_after_one_that_runs_again(env):
    """Review #12: `build --skip-compile` then `build --resume` runs compile *and* export
    again (the first export was written before compile)."""
    settings, store, client, calls = env
    first = build(env, skip_compile=True)
    steps = {s.name: s.status for s in store.get_steps(first.run_id)}
    assert steps["compile"] == "skipped" and steps["export"] == "succeeded"
    calls.clear()
    exported = []
    original = client.export_model

    def tracked(ids, fmt="auto_ontology", include_ontology=False):
        exported.append(fmt)
        return original(ids, fmt, include_ontology)

    client.export_model = tracked
    second = build(env, resume=True)
    assert second.run_id == first.run_id
    assert calls == ["compile:autoontology_src_demo"]
    assert second.details.get("export") and exported  # export executed, not reused
    steps = {s.name: s.status for s in store.get_steps(second.run_id)}
    assert steps["compile"] == "succeeded" and steps["export"] == "succeeded"


# --- schema changes on rebuild (review #4, #5) ----------------------------------------


def _manifest(**tables):
    return {
        t: {"source_file": f"{t}.csv", "sheet": None, "columns": cols} for t, cols in tables.items()
    }


def _schema_env(env, monkeypatch, manifests):
    """A fake loader that diffs like the real one and records the call order."""
    from autoontology.structured.loaders import diff_manifest

    settings, store, client, calls = env
    resets = []

    def fake_load(files, pg_database, settings, *, previous=None, on_changes=None):
        new = manifests.pop(0)
        changes = diff_manifest(previous or {}, new)
        if on_changes and (changes["removed"] or changes["changed"]):
            on_changes(changes)
        calls.append(f"write:{','.join(sorted(new))}")
        return LoadReport(
            pg_database=pg_database,
            manifest=new,
            schema_changes=changes,
            dropped_tables=changes["removed"],
        )

    def fake_reset(db, settings, **kw):
        calls.append(f"reset:{db}")
        resets.append(db)
        return {"vectors_removed": 3, "detached_terms": []}

    monkeypatch.setattr("autoontology.structured.loaders.load_files_to_postgres", fake_load)
    monkeypatch.setattr(jobs, "upstream_reset_semantic", fake_reset)
    return resets


def test_changed_schema_resets_semantic_layer_before_writing(env, monkeypatch):
    settings, store, client, calls = env
    resets = _schema_env(
        env,
        monkeypatch,
        [
            _manifest(customers={"id": "bigint"}, orders={"id": "bigint"}),
            _manifest(
                customers={"id": "bigint", "loyalty_score": "double precision"},
                orders={"id": "bigint"},
            ),
            _manifest(
                customers={"id": "bigint", "loyalty_score": "double precision"},
                orders={"id": "bigint"},
            ),
        ],
    )
    build(env)
    assert resets == []  # first build: nothing to invalidate
    source = store.get_source("demo")
    assert set(store.get_file_manifest(source.id)) == {"customers", "orders"}
    calls.clear()
    result = build(env)
    assert result.status == "succeeded"
    assert calls[:2] == ["reset:autoontology_src_demo", "write:customers,orders"]
    load = result.details["load"]
    assert load["schema_changes"]["changed"] == ["customers"]
    assert load["invalidated"]["semantic_reset"] is True
    assert "compile:autoontology_src_demo" in calls  # recompiles every table
    calls.clear()
    build(env)  # unchanged inputs: no reset
    assert resets == ["autoontology_src_demo"]


def test_removed_input_drops_table_and_resets(env, monkeypatch):
    settings, store, client, calls = env
    resets = _schema_env(
        env,
        monkeypatch,
        [
            _manifest(customers={"id": "bigint"}, orders={"id": "bigint"}),
            _manifest(customers={"id": "bigint"}),
        ],
    )
    build(env)
    result = build(env)
    load = result.details["load"]
    assert load["dropped_tables"] == ["orders"] and resets == ["autoontology_src_demo"]
    assert set(store.get_file_manifest(store.get_source("demo").id)) == {"customers"}


def test_failed_semantic_reset_fails_load_and_keeps_manifest(env, monkeypatch):
    settings, store, client, calls = env
    _schema_env(
        env,
        monkeypatch,
        [
            _manifest(customers={"id": "bigint"}),
            _manifest(customers={"id": "text"}),
        ],
    )
    build(env)

    def broken(db, settings, **kw):
        raise RuntimeError("upstream DAL down")

    monkeypatch.setattr(jobs, "upstream_reset_semantic", broken)
    with pytest.raises(jobs.JobError, match="step load failed"):
        build(env)
    manifest = store.get_file_manifest(store.get_source("demo").id)
    assert manifest["customers"]["columns"] == {"id": "bigint"}  # retried next build
    assert calls.count("write:customers") == 1  # the failed load wrote nothing


def test_schema_change_before_first_ingest_needs_no_reset(env, monkeypatch):
    settings, store, client, calls = env
    resets = _schema_env(
        env,
        monkeypatch,
        [
            _manifest(customers={"id": "bigint"}),
            _manifest(customers={"id": "text"}),
        ],
    )
    build(env, skip_compile=True)
    client.databases.clear()  # e.g. the catalog entry was removed
    result = build(env)
    assert resets == [] and result.details["load"]["invalidated"]["semantic_reset"] is False


def test_semantic_reset_refuses_blank_database(isolated_env):
    with pytest.raises(ValueError):
        jobs.upstream_reset_semantic("  ", Settings.from_env())


# --- fix wave 2 #3: shared Terms, attribute gate, database sources ---------------------


def test_compile_gate_fails_on_tables_without_attributes(env):
    settings, store, client, calls = env
    client.bare_terms = {"t1"}  # Customer kept its Term but lost its attributes
    with pytest.raises(jobs.JobError, match="no column attributes.*Customer/customer"):
        build(env)


def _db_source_env(isolated_env, monkeypatch):
    """A database source over a fake upstream catalog (`state["catalog"]`)."""
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    store.add_source(
        name="crm", kind="database", locator=json.dumps({"type": "postgres", "database": "crm"})
    )
    client = FakeClient()
    state = {
        "catalog": {
            "public.customers": {"id": "tb1", "columns": {"id": "bigint"}, "terms": ["t1"]},
            "public.orders": {"id": "tb2", "columns": {"id": "bigint"}, "terms": ["t2"]},
        },
        "next": None,
        "ingest_error": None,
        "reset_error": None,
    }
    resets = []

    def fake_ingest(db, settings):
        client.databases[db] = "db-1"
        if state["next"] is not None:  # upstream commits the new catalog ...
            state["catalog"], state["next"] = state["next"], None
        if state["ingest_error"]:  # ... and fails later
            error, state["ingest_error"] = state["ingest_error"], None
            raise RuntimeError(error)
        return {"tables": 1}

    def fake_reset(db, s, **kw):
        if state["reset_error"]:
            error, state["reset_error"] = state["reset_error"], None
            raise RuntimeError(error)
        resets.append((db, kw))
        return {"detached_terms": []}

    monkeypatch.setattr(
        jobs, "catalog_snapshot", lambda db, s: json.loads(json.dumps(state["catalog"]))
    )
    monkeypatch.setattr(jobs, "upstream_ingest", fake_ingest)
    monkeypatch.setattr(jobs, "upstream_compile", lambda db, s: {"tables_compiled": 1})
    monkeypatch.setattr(jobs, "ensure_vector_tables", lambda s: {})
    monkeypatch.setattr(jobs, "upstream_reset_semantic", fake_reset)
    monkeypatch.setattr(jobs, "attribute_gaps", lambda c, g, snap: ([], []))
    monkeypatch.setattr(pipeline, "connection_for", lambda source, s: {"database": "crm"})
    monkeypatch.setattr(pipeline, "catalog_database_name", lambda source: "crm")

    def run():
        return jobs.run_structured(
            "crm", resume=False, settings=settings, store=store, client=client
        )

    return store, state, resets, run


NEW_CATALOG = {
    "public.customers": {"id": "tb1", "columns": {"id": "bigint", "tier": "text"}, "terms": ["t1"]}
}


def test_database_source_schema_change_resets_after_ingest(isolated_env, monkeypatch):
    store, state, resets, run = _db_source_env(isolated_env, monkeypatch)
    assert run().status == "succeeded" and resets == []  # first build: baseline only
    state["next"] = NEW_CATALOG
    result = run()
    assert result.status == "succeeded"
    assert result.details["ingest"]["schema_changes"] == {
        "added": [],
        "removed": ["public.orders"],
        "changed": ["public.customers"],
    }
    assert resets == [("crm", {"orphan_candidates": ["t2"], "stale_table_ids": ["tb2"]})]
    src = store.get_source("crm")
    assert store.get_pending_invalidation(src.id) is None
    assert set(store.get_semantic_baseline(src.id)) == {"public.customers"}
    resets.clear()
    run()
    assert resets == []  # consumed


def test_ingest_failing_after_the_catalog_changed_still_resets_on_retry(isolated_env, monkeypatch):
    """Fix wave 3 #1: upstream committed the new catalog, then the ingest failed; the
    retry's own before/after snapshots are equal, but the persisted baseline is not."""
    store, state, resets, run = _db_source_env(isolated_env, monkeypatch)
    run()
    state["next"], state["ingest_error"] = NEW_CATALOG, "embedding endpoint down"
    with pytest.raises(jobs.JobError, match="step ingest failed"):
        run()
    assert resets == []
    run()
    assert [r[1]["stale_table_ids"] for r in resets] == [["tb2"]]


def test_failed_reset_is_retried_from_the_pending_record(isolated_env, monkeypatch):
    store, state, resets, run = _db_source_env(isolated_env, monkeypatch)
    run()
    state["next"], state["reset_error"] = NEW_CATALOG, "DAL down"
    with pytest.raises(jobs.JobError, match="step ingest failed"):
        run()
    src = store.get_source("crm")
    pending = store.get_pending_invalidation(src.id)
    assert pending["removed"] == ["public.orders"] and pending["orphan_terms"] == ["t2"]
    run()
    assert resets == [("crm", {"orphan_candidates": ["t2"], "stale_table_ids": ["tb2"]})]
    assert store.get_pending_invalidation(src.id) is None


def test_partial_column_coverage_warns_and_is_recorded(env, monkeypatch):
    """Fix wave 4 #1: an uncovered (non-FK) column is reported, never fatal: upstream does
    not persist its inferred-FK decisions, so it cannot be told apart from a skipped FK."""
    settings, store, client, calls = env
    monkeypatch.setattr(
        jobs,
        "catalog_snapshot",
        lambda db, s: {
            "public.customer": {
                "id": "tb-t1",
                "columns": {"id": "bigint", "tier": "text"},
                "terms": ["t1"],
                "fk_columns": [],
            }
        },
    )
    result = build(env)
    assert result.status == "succeeded"
    assert result.details["compile"]["uncovered_columns"] == ["public.customer.tier"]
    summary = json.loads((result.export_dir / "summary.json").read_text())
    assert summary["uncovered_columns"] == ["public.customer.tier"]


def test_self_referencing_fk_table_passes_the_gate(env):
    """employees(id, name, manager_id REFERENCES employees(id)): upstream makes attributes
    for id and name only and no semantic FK for the self reference."""
    settings, store, client, calls = env
    client.term_column_attributes = lambda tid: [
        {"primary_column": {"table_id": "tb-e", "column_name": c}} for c in ("id", "name")
    ]
    snap = {
        "public.employees": {
            "id": "tb-e",
            "columns": {"id": "bigint", "name": "text", "manager_id": "bigint"},
            "terms": ["t"],
            "fk_columns": ["manager_id"],
        }
    }
    assert jobs.attribute_gaps(client, {"nodes": [{"id": "t"}]}, snap) == ([], [])


def test_term_with_zero_attributes_still_fails(env, monkeypatch):
    settings, store, client, calls = env
    client.bare_terms = {"t1"}
    monkeypatch.setattr(
        jobs,
        "catalog_snapshot",
        lambda db, s: {
            "public.customer": {
                "id": "tb-t1",
                "columns": {"id": "bigint"},
                "terms": ["t1"],
                "fk_columns": [],
            },
            "public.link": {
                "id": "tb-l",
                "columns": {"a_id": "bigint"},
                "terms": ["t1"],
                "fk_columns": ["a_id"],
            },
        },
    )  # FK-only bridge table: fine
    with pytest.raises(jobs.JobError, match="no column attributes.*public.customer"):
        build(env)


def test_gate_counts_referenced_fk_columns(env):
    settings, store, client, calls = env
    client.term_column_attributes = lambda tid: [
        {
            "primary_column": {"table_id": "tb-k", "column_name": "고객id"},
            "referenced_columns": [{"table_id": "tb-c", "column_name": "고객id"}],
        },
        {"primary_column": {"table_id": "tb-c", "column_name": "계약id"}},
    ]
    snap = {
        "public.contracts": {
            "id": "tb-c",
            "columns": {"계약id": "text", "고객id": "text"},
            "terms": ["t"],
            "fk_columns": [],
        },
        "public.customers": {
            "id": "tb-k",
            "columns": {"고객id": "text"},
            "terms": ["t"],
            "fk_columns": [],
        },
    }
    assert jobs.attribute_gaps(client, {"nodes": [{"id": "t"}]}, snap) == ([], [])


# --- fix wave 2 #7: stale source identities -----------------------------------------------


def _replace_on_lease(store, name, monkeypatch):
    """Replace *name* (delete + re-add, new id) right before its lease is taken."""
    real = store.acquire_leases

    def racing(names, operation, **kw):
        old = store.get_source(name)
        if old is not None:
            store.delete_source(old.id)
            store.add_source(
                name=name, kind=old.kind, locator=old.locator, pg_database=old.pg_database
            )
        monkeypatch.setattr(store, "acquire_leases", real)
        return real(names, operation, **kw)

    monkeypatch.setattr(store, "acquire_leases", racing)


def test_stale_remove_never_touches_the_replacement(env, monkeypatch):
    from autoontology.engine import pg
    from autoontology.structured import remove

    settings, store, client, calls = env
    old = store.get_source("demo")
    store.delete_source(old.id)
    replacement = pipeline.add_source(store, json.loads(old.locator)["path"], name="demo")
    touched = []
    monkeypatch.setattr(pg, "connect", lambda *a, **k: touched.append(a) or None)
    import autoontology.engine.client as client_mod

    monkeypatch.setattr(
        client_mod, "BackendClient", lambda *a, **k: touched.append("backend") or None
    )
    with pytest.raises(remove.RemoveError, match="removed or replaced"):
        remove.remove_source(settings, store, old, drop_db=True)
    assert touched == [] and store.get_source("demo").id == replacement.id


def test_build_rereads_the_source_under_its_lease(env, monkeypatch):
    settings, store, client, calls = env
    _replace_on_lease(store, "demo", monkeypatch)
    with pytest.raises(jobs.JobError, match="removed or replaced"):
        build(env)
    assert calls == []


def test_verify_sources(env):
    settings, store, client, calls = env
    src = store.get_source("demo")
    assert jobs.verify_sources(store, [src])[0].id == src.id
    store.delete_source(src.id)
    with pytest.raises(jobs.JobError, match="removed or replaced"):
        jobs.verify_sources(store, [src])


def _set_started(store, run_id, iso):
    with store._conn() as conn:
        conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (iso, run_id))


def test_sweep_reaps_a_sourceless_ingest_run_of_a_dead_pid(env):
    _, store, _, _ = env
    stale = store.create_run(
        kind="ingest", source_id=None, params={"database": "nonexistent", "pid": 2**22 + 12345}
    )
    store.start_step(stale.id, "ingest")
    live = store.create_run(
        kind="ingest", source_id=None, params={"database": "x", "pid": os.getppid()}
    )
    alive = jobs.reap_stale_runs(store, None)
    assert [r.id for r in alive] == [live.id]
    reaped = store.get_run(stale.id)
    assert reaped.status == "failed" and "is gone" in reaped.error
    assert store.get_steps(stale.id)[0].status == "failed"


def test_a_reused_pid_does_not_keep_a_run_alive(env):
    _, store, _, _ = env
    run = store.create_run(kind="ingest", source_id=None, params={"pid": os.getppid()})
    _set_started(store, run.id, "2000-01-01T00:00:00+00:00")  # long before the parent began
    assert "newer process" in jobs.stale_reason(store, store.get_run(run.id))
    assert jobs.reap_stale_runs(store, None) == []
    assert store.get_run(run.id).status == "failed"


def test_real_process_start_time_is_read():
    import time

    started = jobs._process_started_at(os.getpid())
    assert started is not None and started <= time.time() + 1


def test_a_run_whose_owner_lost_the_lease_is_reaped(env, monkeypatch):
    _, store, _, _ = env
    src = store.get_source("demo")
    monkeypatch.setattr(jobs, "_process_started_at", lambda pid: None)
    run = store.create_run(kind="build", source_id=src.id, params={"pid": os.getppid()})
    # young runs get the lease TTL as grace (the row may predate a slow lease write)
    assert jobs.stale_reason(store, run) is None
    assert "lease" in jobs.stale_reason(store, run, lease_grace_s=-1)
    monkeypatch.setattr(store, "lease_holder", lambda name: {"pid": os.getppid()})
    assert jobs.stale_reason(store, run, lease_grace_s=-1) is None
