# SPDX-License-Identifier: MIT
from pathlib import Path

from typer.testing import CliRunner

from ontology.cli import app
from ontology.engine import build as build_job
from ontology.engine import jobs
from ontology.structured import remove

runner = CliRunner()


def test_source_add_list_and_runs(isolated_env):
    data = Path(isolated_env) / "d"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n")
    res = runner.invoke(app, ["source", "add", str(data), "--name", "demo"])
    assert res.exit_code == 0, res.output
    assert "ontology_src_demo" in res.output and "a.csv" in res.output
    res = runner.invoke(app, ["source", "add", str(data), "--name", "Bad-Name"])
    assert res.exit_code == 1 and "invalid source name" in res.output
    res = runner.invoke(app, ["source", "list"])
    assert "demo" in res.output and "files" in res.output
    assert "no runs" in runner.invoke(app, ["runs"]).output
    assert runner.invoke(app, ["runs", "--source", "nope"]).exit_code == 2  # no such option


def _demo_source(isolated_env) -> None:
    data = Path(isolated_env) / "d"
    data.mkdir(exist_ok=True)
    (data / "a.csv").write_text("x\n1\n")
    res = runner.invoke(app, ["source", "add", str(data), "--name", "demo"])
    assert res.exit_code == 0, res.output


def test_source_add_documents_kind(isolated_env):
    docs = Path(isolated_env) / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\ntext\n")
    res = runner.invoke(
        app, ["source", "add", str(docs), "--name", "demo-docs", "--kind", "documents"]
    )
    assert res.exit_code == 0, res.output
    assert "demo-docs (documents)" in res.output
    assert "documents" in runner.invoke(app, ["source", "list"]).output
    again = runner.invoke(
        app, ["source", "add", str(docs), "--name", "demo-docs", "--kind", "documents"]
    )
    assert again.exit_code == 1 and "already exists" in again.output
    missing = runner.invoke(
        app, ["source", "add", str(docs / "nope"), "--name", "x", "--kind", "documents"]
    )
    assert missing.exit_code == 1 and "no such file" in missing.output
    bad = runner.invoke(app, ["source", "add", str(docs), "--name", "x", "--kind", "files"])
    assert bad.exit_code == 2 and "structured | documents" in bad.output


def test_build_without_sources_fails_cleanly(isolated_env):
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 1 and "no sources" in res.output
    _demo_source(isolated_env)
    res = runner.invoke(app, ["build", "--source", "nope"])
    assert res.exit_code == 1 and "unknown source 'nope'" in res.output


def test_build_failure_exits_1_with_log_path(isolated_env, monkeypatch):
    _demo_source(isolated_env)

    def fail(*a, **k):
        raise jobs.JobError(
            "step compile failed: boom", run_id="r1", step="compile", log_path=Path("/x/jobs.log")
        )

    monkeypatch.setattr(build_job, "run_build", fail)
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 1
    assert "build failed: step compile failed: boom" in res.output
    assert "log: /x/jobs.log" in res.output and "--resume" in res.output


def test_build_passes_sources_and_resume(isolated_env, monkeypatch):
    _demo_source(isolated_env)
    calls = []

    def fake(store, settings, **kw):
        calls.append({k: v for k, v in kw.items() if k != "project"})
        return build_job.BuildResult(run_id="b1", status="succeeded", revision=3, counts={})

    monkeypatch.setattr(build_job, "run_build", fake)
    res = runner.invoke(app, ["build", "--resume", "--source", "demo"])
    assert res.exit_code == 0, res.output
    assert calls == [{"sources": ["demo"], "resume": True}]
    assert "revision 3:" in res.output and "build b1: succeeded" in res.output


def test_build_with_backend_down_points_to_up(isolated_env, monkeypatch):
    _demo_source(isolated_env)
    monkeypatch.setenv("BACKEND_PORT", "1")  # nothing listens there
    res = runner.invoke(app, ["build", "--source", "demo"])
    assert res.exit_code == 1 and "onto up" in res.output


def test_setup_runs_every_step_and_skips_models(isolated_env, monkeypatch):
    from ontology.engine import migrate, ollama, pg

    steps = []
    monkeypatch.setattr(pg, "setup_database", lambda s: steps.append("db") or "database ok")
    monkeypatch.setattr(migrate, "run_migrations", lambda s: steps.append("migrate"))
    monkeypatch.setattr(migrate, "current_revision", lambda s: "abc")
    monkeypatch.setattr(migrate, "head_revision", lambda: "abc")
    seen = {}

    def fake(**kw):
        seen.update(kw)
        steps.append("ollama")
        return ollama.SetupResult(kw["alias"], False, (), "pong", 2)

    monkeypatch.setattr(ollama, "setup_ollama", fake)
    res = runner.invoke(app, ["setup"])
    assert res.exit_code == 0, res.output
    assert steps == ["db", "migrate", "ollama"]
    assert "postgres: database ok" in res.output and "completion_tokens 2" in res.output
    assert "kordoc: skipped" in res.output
    assert seen["alias"] == "openai/qwen3:8b" and seen["embed"] == "bge-m3"
    assert seen["force"] is False
    steps.clear()
    res = runner.invoke(app, ["setup", "--skip-models"])
    assert res.exit_code == 0 and steps == ["db", "migrate"]
    assert "models: skipped (--skip-models)" in res.output

    def broken(**kw):
        raise ollama.OllamaSetupError("ollama not reachable")

    monkeypatch.setattr(ollama, "setup_ollama", broken)
    res = runner.invoke(app, ["setup", "--force-alias"])
    assert res.exit_code == 1 and "models: ollama not reachable" in res.output


def test_setup_with_openai_provider_skips_ollama(isolated_env, monkeypatch):
    from ontology.engine import migrate, ollama, pg

    monkeypatch.setenv("ONTOLOGY_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("ONTOLOGY_EMBED_PROVIDER", "openai")
    monkeypatch.setattr(pg, "setup_database", lambda s: "database ok")
    monkeypatch.setattr(migrate, "run_migrations", lambda s: None)
    monkeypatch.setattr(migrate, "current_revision", lambda s: "abc")
    monkeypatch.setattr(migrate, "head_revision", lambda: "abc")
    monkeypatch.setattr(
        ollama,
        "setup_ollama",
        lambda **kw: (_ for _ in ()).throw(AssertionError("ollama must not be touched")),
    )
    res = runner.invoke(app, ["setup"])
    assert res.exit_code == 0, res.output
    assert "models: skipped (chat and embeddings use the openai provider" in res.output


def test_setup_postgres_failure_points_to_compose(isolated_env, monkeypatch):
    from ontology.engine import pg

    def fail(s):
        raise pg.SetupError("cannot prepare database ontology")

    monkeypatch.setattr(pg, "setup_database", fail)
    res = runner.invoke(app, ["setup"])
    assert res.exit_code == 1 and "docker compose up -d" in res.output


class _RmClient:
    def __init__(self, *, down=False, gone_after=0, status=None):
        self.down, self.gone_after, self.status = down, gone_after, status
        self.events = []
        self.polls = 0

    def __call__(self, *_a, **_k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def delete_connection(self, name):
        from ontology.engine.client import BackendError, BackendUnavailable

        if self.down:
            raise BackendUnavailable("backend not reachable")
        if self.status:
            raise BackendError("boom", status=self.status)
        self.events.append("delete_connection")

    def database_id_by_name(self, name):
        self.polls += 1
        self.events.append("poll")
        return None if self.polls > self.gone_after else "db-1"


def _rm_setup(isolated_env, monkeypatch, fake):
    from ontology.engine import client as client_mod
    from ontology.engine import pg

    data = Path(isolated_env) / "d"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n")
    assert runner.invoke(app, ["source", "add", str(data), "--name", "demo"]).exit_code == 0
    monkeypatch.setattr(client_mod, "BackendClient", fake)
    monkeypatch.setattr(remove, "RM_POLL_S", 0.0)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *a):
            fake.events.append("drop")

    monkeypatch.setattr(pg, "connect", lambda *a, **k: Conn())


def test_source_rm_waits_for_graph_deletion_before_drop(isolated_env, monkeypatch):
    fake = _RmClient(gone_after=2)
    _rm_setup(isolated_env, monkeypatch, fake)
    res = runner.invoke(app, ["source", "rm", "demo", "--drop-db"])
    assert res.exit_code == 0, res.output
    assert fake.events == ["delete_connection", "poll", "poll", "poll", "drop"]
    assert "unknown source" in runner.invoke(app, ["source", "rm", "demo"]).output


def test_source_rm_timeout_drops_nothing(isolated_env, monkeypatch):
    fake = _RmClient(gone_after=10**6)
    _rm_setup(isolated_env, monkeypatch, fake)
    monkeypatch.setattr(remove, "RM_GRAPH_WAIT_S", 0.0)
    res = runner.invoke(app, ["source", "rm", "demo", "--drop-db"])
    assert res.exit_code == 1 and "did not finish" in res.output
    assert "drop" not in fake.events
    assert "demo" in runner.invoke(app, ["source", "list"]).output  # row kept


def test_source_rm_backend_down_needs_force(isolated_env, monkeypatch):
    fake = _RmClient(down=True)
    _rm_setup(isolated_env, monkeypatch, fake)
    res = runner.invoke(app, ["source", "rm", "demo", "--drop-db"])
    assert res.exit_code == 1 and "--force" in res.output and "drop" not in fake.events
    res = runner.invoke(app, ["source", "rm", "demo", "--drop-db", "--force"])
    assert res.exit_code == 0, res.output
    # the connection could not be deleted: dropping its database would orphan it
    assert "may remain" in res.output and fake.events == []
    assert "database ontology_src_demo kept" in res.output
    assert "--include-unowned --drop-db" in res.output
    assert "removed source demo" in res.output


def test_build_unexpected_error_is_one_line(isolated_env, monkeypatch):
    _demo_source(isolated_env)

    def explode(*a, **k):
        raise KeyError("weird\nsecond line")

    monkeypatch.setattr(build_job, "run_build", explode)
    res = runner.invoke(app, ["build"])
    assert res.exit_code == 1
    assert "build failed: KeyError" in res.output and "jobs.log" in res.output
    assert "Traceback" not in res.output
    monkeypatch.setenv("ONTOLOGY_DEBUG", "1")
    res = runner.invoke(app, ["build"])
    assert isinstance(res.exception, KeyError)


# --- audit: the entries the web routes write for the same change -------------------------


def _audit(*actions):
    from ontology.config import Settings
    from ontology.store.accounts import Accounts
    from ontology.store.db import Store

    store = Store.for_home(Settings.from_env().home)
    rows = reversed(Accounts(store).list_audit(limit=500))
    return [r for r in rows if r["action"] in actions], store.project().id


def test_cli_source_add_and_rm_are_audited(isolated_env, monkeypatch):
    docs = Path(isolated_env) / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\ntext\n")
    res = runner.invoke(app, ["source", "add", str(docs), "--name", "notes", "--kind", "documents"])
    assert res.exit_code == 0, res.output
    _rm_setup(isolated_env, monkeypatch, _RmClient())
    assert runner.invoke(app, ["source", "rm", "demo"]).exit_code == 0
    bad = runner.invoke(app, ["source", "rm", "nope"])  # refused: no entry
    assert bad.exit_code == 1
    rows, pid = _audit("source.add", "source.delete")
    assert [(r["action"], r["target"], r["detail"]) for r in rows] == [
        ("source.add", "notes", {"kind": "documents", "actor": "cli", "via": "cli"}),
        ("source.add", "demo", {"kind": "files", "actor": "cli", "via": "cli"}),
        ("source.delete", "demo", {"drop_db": False, "actor": "cli", "via": "cli"}),
    ]
    assert {r["project_id"] for r in rows} == {pid}


def test_cli_build_audits_run_start_once_the_run_exists(isolated_env, monkeypatch):
    _demo_source(isolated_env)
    seen = []

    def fake(store, settings, **kw):
        run = store.create_run(kind="build", source_id=None, project_id=kw["project"])
        seen.append(_audit("run.start")[0])  # written as soon as the run id exists
        store.update_run(run.id, status="succeeded")
        return build_job.BuildResult(run_id=run.id, status="succeeded", revision=1, counts={})

    monkeypatch.setattr(build_job, "run_build", fake)
    res = runner.invoke(app, ["build", "--resume", "--source", "demo"])
    assert res.exit_code == 0, res.output
    (row,) = seen[0]
    assert row["detail"] == {
        "kind": "build",
        "resume": True,
        "sources": ["demo"],
        "actor": "cli",
        "via": "cli",
    }
    rows, pid = _audit("run.start")
    assert rows == [row] and row["project_id"] == pid and row["target"].startswith("20")


def test_cli_export_audits_run_start(isolated_env, monkeypatch):
    from ontology.engine import export_job

    def fake(store, settings, **kw):
        run = store.create_run(kind="export", source_id=None, project_id=kw["project"])
        store.update_run(run.id, status="succeeded")
        return jobs.RunResult(run_id=run.id, status="succeeded", details={})

    monkeypatch.setattr(export_job, "run_export", fake)
    res = runner.invoke(app, ["export", "--revision", "2", "--format", "rdf"])
    assert res.exit_code == 0, res.output
    rows, pid = _audit("run.start")
    assert [(r["detail"], r["project_id"]) for r in rows] == [
        ({"kind": "export", "revision": 2, "format": "rdf", "actor": "cli", "via": "cli"}, pid)
    ]
