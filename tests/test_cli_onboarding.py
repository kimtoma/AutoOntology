# SPDX-License-Identifier: MIT
"""`init`, `demo` and `provider` through the CLI, with fake detection/setup/engine."""

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from autoontology import cli, onboarding
from autoontology.cli import app
from autoontology.engine import pg
from autoontology.onboarding import Detection, ProviderInfo
from autoontology.store.db import Store

runner = CliRunner()
#: The real init step 6 (the ``fakes`` fixture replaces it).
REAL_INIT_SETUP = cli._init_setup


def _det(env_path, *, claude=False, codex=False, ollama=True, node=True) -> Detection:
    infos = {
        "ollama": ProviderInfo("ollama", ollama, ollama, "local", ""),
        "openai": ProviderInfo("openai", False, False, "base URL not set", "set it"),
        "claude": ProviderInfo(
            "claude", True, claude, "logged in" if claude else "not logged in", "claude auth login"
        ),
        "codex": ProviderInfo("codex", codex, codex, "-", "codex login"),
    }
    return Detection(
        postgres_ok=True,
        postgres_detail="ok",
        docker=True,
        providers=infos,
        ollama_models=ollama,
        node_ok=node,
        node_detail="v22" if node else "node not found",
        kordoc=False,
        ocr_ready=False,
        env_exists=Path(env_path).is_file(),
        env_path=Path(env_path),
    )


@pytest.fixture
def fakes(isolated_env, monkeypatch):
    calls: dict = {"setup": [], "pg": 0}
    state = {"det": {}, "pg_ok": True}

    def setup_database(settings):
        calls["pg"] += 1
        if not state["pg_ok"]:
            raise pg.SetupError("cannot prepare database autoontology on localhost:5432")
        return "database autoontology exists; extensions vector, pg_trgm"

    monkeypatch.setattr(pg, "setup_database", setup_database)
    monkeypatch.setattr(
        onboarding,
        "detect_environment",
        lambda settings, env_path=None: _det(env_path, **state["det"]),
    )
    monkeypatch.setattr(cli, "_init_setup", lambda docs: calls["setup"].append(docs))
    env = Path(isolated_env) / "conf" / ".env"
    return calls, state, env


def test_init_yes_happy_path_writes_env_from_the_template(fakes):
    calls, state, env = fakes
    state["det"] = {"claude": True}
    res = runner.invoke(app, ["init", "--yes", "--env", str(env)])
    assert res.exit_code == 0, res.output
    text = env.read_text(encoding="utf-8")
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude\n" in text
    assert "AUTOONTOLOGY_CLAUDE_MODEL=claude-sonnet-5-5\n" in text
    assert "AUTOONTOLOGY_EMBED_PROVIDER=ollama\n" in text
    assert "# --- Postgres 17 + pgvector" in text  # the template's comments are kept
    for n in range(1, 8):
        assert f"[{n}/7]" in res.output
    assert calls["setup"] == [True]  # node >= 20 -> documents by default
    assert "created" in res.output and "next: uv run autoontology demo --set hanbit" in res.output
    assert "LOGGED IN" in res.output


def test_init_yes_defaults_to_ollama_and_no_docs_without_node(fakes):
    calls, state, env = fakes
    state["det"] = {"node": False}
    res = runner.invoke(app, ["init", "--yes", "--env", str(env)])
    assert res.exit_code == 0, res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=ollama" in env.read_text(encoding="utf-8")
    assert calls["setup"] == [False] and "install Node.js 20+" in res.output
    assert res.output.rstrip().endswith("next: uv run autoontology demo")


def test_init_postgres_down_with_yes_exits_2_with_the_remedy(fakes):
    calls, state, env = fakes
    state["pg_ok"] = False
    res = runner.invoke(app, ["init", "--yes", "--env", str(env)])
    assert res.exit_code == 2
    assert "docker compose up -d" in res.output and "POSTGRES_HOST" in res.output
    assert calls["setup"] == [] and calls["pg"] == 1 and not env.exists()


def test_init_postgres_retry_interactively(fakes):
    calls, state, env = fakes
    state["pg_ok"] = False
    res = runner.invoke(app, ["init", "--env", str(env)], input="y\ny\ny\n")
    assert res.exit_code == 2 and calls["pg"] == 4  # first try + 3 retries
    assert res.output.count("press Enter to retry, or n to stop [Y/n]") == 3
    assert "Postgres is not reachable at localhost:5432" in res.output


def test_init_claude_not_logged_in_retries_after_login(fakes, monkeypatch):
    calls, state, env = fakes
    answers = iter([False, True])

    def info(settings, name):
        ok = next(answers)
        return ProviderInfo(name, True, ok, "logged in (claude.ai)" if ok else "not logged in")

    monkeypatch.setattr(onboarding, "provider_info", info)
    # retry (not yet) -> retry (ok) -> chat model default -> no documents
    res = runner.invoke(
        app, ["init", "--provider", "claude", "--env", str(env)], input="y\ny\n\nn\n"
    )
    assert res.exit_code == 0, res.output
    assert "claude auth login" in res.output and "still not ready" in res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in env.read_text(encoding="utf-8")
    assert calls["setup"] == [False]


def test_init_claude_not_logged_in_with_yes_exits_2(fakes):
    calls, _state, env = fakes
    res = runner.invoke(app, ["init", "--yes", "--provider", "claude", "--env", str(env)])
    assert res.exit_code == 2 and "claude auth login" in res.output and calls["setup"] == []


def test_init_openai_never_asks_for_the_key(fakes):
    calls, _state, env = fakes
    res = runner.invoke(
        app,
        ["init", "--provider", "openai", "--env", str(env)],
        input="https://api.example.com/v1/\nmodel-x\n\nn\n",
    )
    assert res.exit_code == 0, res.output
    text = env.read_text(encoding="utf-8")
    assert "AUTOONTOLOGY_OPENAI_BASE_URL=https://api.example.com/v1\n" in text
    assert "AUTOONTOLOGY_OPENAI_CHAT_MODEL=model-x\n" in text
    assert "AUTOONTOLOGY_EMBED_PROVIDER=ollama\n" in text
    assert "\nAUTOONTOLOGY_OPENAI_API_KEY=" not in text  # still the commented template line
    prompts = [line for line in res.output.splitlines() if line.rstrip().endswith(":")]
    assert not any("key" in p.lower() for p in prompts)
    assert "Add AUTOONTOLOGY_OPENAI_API_KEY=… to .env yourself" in res.output


def test_init_merges_an_existing_env_with_a_backup(fakes):
    _calls, state, env = fakes
    env.parent.mkdir(parents=True)
    original = (
        "# my settings\nAUTOONTOLOGY_PORT=7810\nAUTOONTOLOGY_CHAT_PROVIDER=ollama  \n"
        "POSTGRES_USER=me\n"
    )
    env.write_text(original, encoding="utf-8")
    state["det"] = {"codex": True}
    res = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert res.exit_code == 0, res.output
    text = env.read_text(encoding="utf-8")
    # the provider the file already selects is kept; the recommendation is only a hint
    assert text.startswith(
        "# my settings\nAUTOONTOLOGY_PORT=7810\nAUTOONTOLOGY_CHAT_PROVIDER=ollama  \n"
        "POSTGRES_USER=me\n"
    )
    assert "# --- Set by autoontology init ---\n" in text
    assert "AUTOONTOLOGY_OLLAMA_CHAT_MODEL=" in text and "AUTOONTOLOGY_CODEX_MODEL" not in text
    backups = list(env.parent.glob(".env.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    assert "AUTOONTOLOGY_CHAT_PROVIDER:" not in res.output
    assert "keeping the current provider ollama" in res.output
    assert "recommended: codex" in res.output
    # running it again changes nothing and makes no new backup
    again = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert again.exit_code == 0 and "no changes" in again.output
    assert len(list(env.parent.glob(".env.bak-*"))) == 1


def test_init_keeps_claude_chat_with_openai_embeddings(fakes):
    calls, state, env = fakes
    env.parent.mkdir(parents=True)
    original = (
        "AUTOONTOLOGY_CHAT_PROVIDER=claude\nAUTOONTOLOGY_EMBED_PROVIDER=openai\n"
        "AUTOONTOLOGY_OPENAI_BASE_URL=https://api.example.com/v1\n"
        "AUTOONTOLOGY_OPENAI_EMBED_MODEL=text-embed-x\n"
    )
    env.write_text(original, encoding="utf-8")
    state["det"] = {"claude": True, "codex": True}
    res = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert res.exit_code == 0, res.output
    text = env.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert "AUTOONTOLOGY_EMBED_PROVIDER=ollama" not in text
    assert "embeddings: openai text-embed-x (current, kept)" in res.output


def test_init_interactive_default_is_the_current_provider(fakes):
    calls, state, env = fakes
    env.parent.mkdir(parents=True)
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=ollama\n", encoding="utf-8")
    state["det"] = {"claude": True}
    # Enter at the provider prompt -> the current one; model default; no docs
    res = runner.invoke(app, ["init", "--env", str(env)], input="\n\nn\n")
    assert res.exit_code == 0, res.output
    assert "Chat provider (current: ollama; recommended: claude)" in res.output
    assert "[ollama]" in res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=ollama\n" in env.read_text(encoding="utf-8")
    # choosing the recommended (non-default) provider switches it
    res = runner.invoke(app, ["init", "--env", str(env)], input="claude\n\nn\n")
    assert res.exit_code == 0, res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude\n" in env.read_text(encoding="utf-8")


def test_init_does_not_mistake_the_old_env_value_for_a_shell_override(fakes, monkeypatch):
    """The value this process loaded from .env (now rewritten) is not a shell variable."""
    from autoontology import config

    _calls, state, env = fakes
    env.parent.mkdir(parents=True)
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=ollama\n", encoding="utf-8")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")  # as load_env copied it
    monkeypatch.setitem(config._DOTENV_LOADED, "AUTOONTOLOGY_CHAT_PROVIDER", "ollama")
    state["det"] = {"claude": True}
    res = runner.invoke(app, ["init", "--env", str(env)], input="claude\n\nn\n")
    assert res.exit_code == 0, res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude\n" in env.read_text(encoding="utf-8")
    assert "AUTOONTOLOGY_CHAT_PROVIDER: ollama -> claude" in res.output
    assert "your shell sets" not in res.output


def test_init_without_env_selection_takes_the_recommendation(fakes):
    _calls, state, env = fakes
    state["det"] = {"codex": True}
    res = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert res.exit_code == 0, res.output
    text = env.read_text(encoding="utf-8")
    assert "AUTOONTOLOGY_CHAT_PROVIDER=codex\n" in text
    assert "AUTOONTOLOGY_EMBED_PROVIDER=ollama\n" in text


def test_init_setup_prints_no_competing_next_line(fakes, monkeypatch):
    """The real setup body inside init: no `next: up` line, and the kordoc skip names
    what to do in init terms."""
    from autoontology.engine import migrate

    calls, _state, env = fakes
    monkeypatch.setattr(cli, "_init_setup", REAL_INIT_SETUP)
    monkeypatch.setattr(migrate, "run_migrations", lambda s: None)
    monkeypatch.setattr(migrate, "current_revision", lambda s: "abc")
    monkeypatch.setattr(migrate, "head_revision", lambda: "abc")
    monkeypatch.setattr(cli, "setup", _setup_skipping_models(cli.setup))
    res = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert res.exit_code == 0, res.output
    assert "next: uv run autoontology up" not in res.output
    assert "answer yes to document support or run" in res.output
    assert "uv run autoontology tools install-kordoc` later" in res.output
    assert res.output.count("next:") == 1


def _setup_skipping_models(real):
    def setup(**kw):
        return real(**{**kw, "skip_models": True})

    return setup


# --- demo -----------------------------------------------------------------------------


@pytest.fixture
def demo_env(isolated_env, monkeypatch):
    root = Path(isolated_env) / "samples"
    (root / "quickstart" / "structured").mkdir(parents=True)
    (root / "quickstart" / "structured" / "a.csv").write_text("id\n1\n", encoding="utf-8")
    (root / "quickstart" / "documents").mkdir()
    (root / "quickstart" / "documents" / "b.md").write_text("# B\n본문\n", encoding="utf-8")
    monkeypatch.setenv("AUTOONTOLOGY_SAMPLES_DIR", str(root))
    calls: list = []
    monkeypatch.setattr(cli, "up", lambda: calls.append("up"))
    monkeypatch.setattr(
        cli, "build", lambda source, resume: calls.append(("build", tuple(source), resume))
    )
    monkeypatch.setattr(cli, "export", lambda **kw: calls.append("export"))
    monkeypatch.setattr(cli, "ui", lambda no_browser: calls.append(("ui", no_browser)))
    from autoontology.structured import remove

    monkeypatch.setattr(remove, "backend_is_ours", lambda settings: False)
    return calls


def _move_quick(isolated_env):
    from autoontology.config import Settings

    store = Store.for_home(Settings.from_env().home)
    store.set_locator(store.get_source("quick").id, '{"path": "/old/place", "recursive": false}')
    return store


def test_demo_asks_before_replacing_a_source(demo_env, isolated_env, monkeypatch):
    from autoontology.structured import remove

    removed = []

    def fake_remove(settings, store, src, **kw):
        removed.append(src.name)
        store.delete_source(src.id)

    monkeypatch.setattr(remove, "remove_source", fake_remove)
    assert runner.invoke(app, ["demo", "--no-open"]).exit_code == 0
    store = _move_quick(isolated_env)
    # no stdin (scripts) and no --replace: stop with the hint, nothing deleted
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 2, res.output
    assert "Replace source quick (its database and extracted concepts will be deleted)?" in (
        res.output
    )
    assert "[y/N]" in res.output and "--replace" in res.output and removed == []
    # an explicit "n" keeps it too
    res = runner.invoke(app, ["demo", "--no-open"], input="n\n")
    assert res.exit_code == 2 and removed == []
    assert store.get_source("quick").locator.startswith('{"path": "/old/place"')
    # "y" replaces
    res = runner.invoke(app, ["demo", "--no-open"], input="y\n")
    assert res.exit_code == 0, res.output
    assert removed == ["quick"]


def test_demo_replace_flag_skips_the_question(demo_env, isolated_env, monkeypatch):
    from autoontology.structured import remove

    removed = []

    def fake_remove(settings, store, src, **kw):
        removed.append(src.name)
        store.delete_source(src.id)

    monkeypatch.setattr(remove, "remove_source", fake_remove)
    assert runner.invoke(app, ["demo", "--no-open"]).exit_code == 0
    _move_quick(isolated_env)
    res = runner.invoke(app, ["demo", "--no-open", "--replace"])
    assert res.exit_code == 0, res.output
    assert removed == ["quick"] and "Replace source" not in res.output
    assert "extracted concepts are deleted" in res.output


def test_demo_prunes_orphaned_connections_after_up(demo_env, monkeypatch):
    from autoontology.engine import client as client_mod
    from autoontology.structured import remove

    fake = FakeBackend(["autoontology_src_gone", "autoontology_src_quick", "userdb"])
    monkeypatch.setattr(client_mod, "BackendClient", lambda *a, **k: fake)
    monkeypatch.setattr(remove, "backend_is_ours", lambda settings: True)
    monkeypatch.setattr(
        pg, "database_exists", lambda settings, name: name != "autoontology_src_gone"
    )
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 0, res.output
    # only the connection whose database is gone; a live unowned one (another checkout,
    # a reset data/) is reported and kept
    assert fake.deleted == ["autoontology_src_gone"]
    assert "autoontology_src_quick" in fake.names
    assert "pruning upstream connections whose database is gone" in res.output
    assert "no source in this checkout (kept" in res.output
    assert demo_env[0] == "up"


def test_demo_survives_a_postgres_error_in_the_orphan_check(demo_env, monkeypatch):
    import psycopg

    from autoontology.engine import client as client_mod
    from autoontology.structured import remove

    fake = FakeBackend(["autoontology_src_gone"])
    monkeypatch.setattr(client_mod, "BackendClient", lambda *a, **k: fake)
    monkeypatch.setattr(remove, "backend_is_ours", lambda settings: True)

    def down(settings, name):
        raise psycopg.OperationalError("connection refused\nmore")

    monkeypatch.setattr(pg, "database_exists", down)
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 0, res.output
    assert res.exception is None and fake.deleted == []
    assert "Postgres is not reachable (connection refused)" in res.output
    assert "docker compose up -d" in res.output


class FakeBackend:
    def __init__(self, names, down=False):
        self.names = list(names)
        self.deleted: list[str] = []
        self.down = down

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def list_connections(self):
        from autoontology.engine.client import BackendUnavailable

        if self.down:
            raise BackendUnavailable("upstream backend not reachable")
        return [{"database_name": n} for n in self.names]

    def delete_connection(self, name):
        self.names.remove(name)
        self.deleted.append(name)
        return {}

    def database_id_by_name(self, name):
        return None


def test_tools_prune_connections(isolated_env, monkeypatch):
    from autoontology.config import Settings
    from autoontology.engine import client as client_mod

    store = Store.for_home(Settings.from_env().home)
    store.add_source(name="keep", kind="files", locator="{}", pg_database="autoontology_src_keep")
    names = ["autoontology_src_keep", "autoontology_src_old", "Autoontology_src_x", "crm"]
    fake = FakeBackend(names)
    monkeypatch.setattr(client_mod, "BackendClient", lambda *a, **k: fake)
    monkeypatch.setattr(pg, "database_exists", lambda settings, name: True)
    dry = runner.invoke(app, ["tools", "prune-connections", "--dry-run", "--include-unowned"])
    assert dry.exit_code == 0, dry.output
    assert "orphan: autoontology_src_old (no registered source)" in dry.output
    assert "keep" not in dry.output and "crm" not in dry.output and fake.deleted == []
    # live but unowned: only reported without --include-unowned
    res = runner.invoke(app, ["tools", "prune-connections"])
    assert res.exit_code == 0, res.output
    assert fake.deleted == [] and "unowned: autoontology_src_old" in res.output
    assert "--include-unowned" in res.output
    bad = runner.invoke(app, ["tools", "prune-connections", "--drop-db"])
    assert bad.exit_code == 2 and fake.deleted == []
    res = runner.invoke(app, ["tools", "prune-connections", "--include-unowned"])
    assert res.exit_code == 0, res.output
    assert fake.deleted == ["autoontology_src_old"]
    assert "deleted upstream connection autoontology_src_old" in res.output
    assert "no orphaned" in runner.invoke(app, ["tools", "prune-connections"]).output
    fake.down = True
    down = runner.invoke(app, ["tools", "prune-connections"])
    assert down.exit_code == 1 and "autoontology up" in down.output


def test_tools_prune_connections_deletes_dead_ones_by_default(isolated_env, monkeypatch):
    from autoontology.engine import client as client_mod

    fake = FakeBackend(["autoontology_src_dead", "autoontology_src_live", "autoontology_src_b"])
    monkeypatch.setattr(client_mod, "BackendClient", lambda *a, **k: fake)
    monkeypatch.setattr(
        pg, "database_exists", lambda settings, name: name != "autoontology_src_dead"
    )
    res = runner.invoke(app, ["tools", "prune-connections"])
    assert res.exit_code == 0, res.output
    assert fake.deleted == ["autoontology_src_dead"]
    # names limit an --include-unowned run
    res = runner.invoke(
        app, ["tools", "prune-connections", "autoontology_src_live", "--include-unowned"]
    )
    assert res.exit_code == 0, res.output
    assert fake.deleted == ["autoontology_src_dead", "autoontology_src_live"]
    assert fake.names == ["autoontology_src_b"]


def test_demo_runs_the_steps_in_order(demo_env):
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 0, res.output
    assert demo_env == [
        "up",
        ("build", ("quick", "quick-docs"), False),
        ("ui", True),
    ]
    assert "quick: created" in res.output and "≈ estimate" in res.output
    assert "The first build is a candidate" in res.output
    assert "What to look at next: Review inbox → Change history" in res.output
    from autoontology.config import Settings

    store = Store.for_home(Settings.from_env().home)
    assert [s.name for s in store.list_sources()] == ["quick", "quick-docs"]
    demo_env.clear()
    again = runner.invoke(app, ["demo", "--resume"])
    assert again.exit_code == 0, again.output
    assert "already registered" in again.output
    assert demo_env[1] == ("build", ("quick", "quick-docs"), True) and demo_env[-1] == ("ui", False)
    assert len(store.list_sources()) == 2


def test_demo_build_failure_propagates_and_skips_export(demo_env, monkeypatch):
    def fail(source, resume):
        demo_env.append("build")
        raise typer.Exit(code=1)

    monkeypatch.setattr(cli, "build", fail)
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 1 and demo_env == ["up", "build"]
    assert "demo --resume" in res.output


def test_demo_unknown_or_missing_set_exits_2(demo_env):
    res = runner.invoke(app, ["demo", "--set", "nope"])
    assert res.exit_code == 2 and "unknown sample set" in res.output
    res = runner.invoke(app, ["demo", "--set", "hanbit"])
    assert res.exit_code == 2 and "git pull" in res.output and demo_env == []


# --- provider ---------------------------------------------------------------------------


def test_provider_status_table(isolated_env, monkeypatch):
    from autoontology.web.providers import probe

    monkeypatch.setattr(
        onboarding,
        "provider_info",
        lambda s, n: ProviderInfo(
            n, n != "codex", n == "ollama", "", "" if n == "ollama" else f"fix {n}"
        ),
    )
    monkeypatch.setattr(
        probe,
        "last_test",
        lambda s, n: (
            {"ok": True, "seconds": 2.9, "at": "2026-10-29T01:02:03+00:00"}
            if n == "claude"
            else None
        ),
    )
    res = runner.invoke(app, ["provider", "status"])
    assert res.exit_code == 0, res.output
    assert "* ollama" in res.output and "openai/qwen3:8b" in res.output
    assert "ok 2.9s (2026-10-29T01:02)" in res.output and "fix codex" in res.output
    assert "claude-sonnet-5-5" in res.output


def test_provider_test_ok_and_failure(isolated_env, monkeypatch):
    from autoontology.web.providers import probe

    results = iter(
        [
            {"ok": True, "model": "m", "answer": "pong", "seconds": 1.0, "via": "in-process"},
            {"ok": False, "error": "not logged in", "remedy": "claude auth login", "seconds": 0.2},
        ]
    )
    monkeypatch.setattr(probe, "run_test", lambda settings, provider=None: next(results))
    ok = runner.invoke(app, ["provider", "test"])
    assert ok.exit_code == 0 and "answered 'pong'" in ok.output
    bad = runner.invoke(app, ["provider", "test", "--provider", "claude"])
    assert bad.exit_code == 1 and "fix: claude auth login" in bad.output


def test_provider_use_edits_env_in_place(isolated_env, monkeypatch):
    env = Path(isolated_env) / ".env"
    env.write_text("# keep me\nAUTOONTOLOGY_CHAT_PROVIDER=claude\n# AUTOONTOLOGY_CODEX_MODEL=x\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.setattr(
        onboarding,
        "provider_info",
        lambda s, n: ProviderInfo(n, True, False, "logged out", "codex login"),
    )
    res = runner.invoke(app, ["provider", "use", "codex", "--chat-model", "gpt-5.5"])
    assert res.exit_code == 0, res.output
    assert env.read_text() == (
        "# keep me\nAUTOONTOLOGY_CHAT_PROVIDER=codex\nAUTOONTOLOGY_CODEX_MODEL=gpt-5.5\n"
    )
    assert "restart: uv run autoontology down && uv run autoontology up" in res.output
    assert "codex login" in res.output
    assert len(list(Path(isolated_env).glob(".env.bak-*"))) == 1
    assert runner.invoke(app, ["provider", "use", "nope"]).exit_code == 2


def test_provider_use_warns_about_a_shell_override(isolated_env, monkeypatch):
    env = Path(isolated_env) / ".env"
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=claude\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")
    monkeypatch.setattr(onboarding, "provider_info", lambda s, n: ProviderInfo(n, True, True))
    res = runner.invoke(app, ["provider", "use", "codex"])
    assert res.exit_code == 0, res.output
    assert "your shell sets AUTOONTOLOGY_CHAT_PROVIDER=ollama" in res.output
    assert "unset AUTOONTOLOGY_CHAT_PROVIDER" in res.output


def test_provider_use_does_not_mistake_the_old_env_value_for_a_shell_override(
    isolated_env, monkeypatch
):
    """The value this process loaded from .env (now rewritten) is not a shell variable."""
    from autoontology import config

    env = Path(isolated_env) / ".env"
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=ollama\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")  # as load_env copied it
    monkeypatch.setitem(config._DOTENV_LOADED, "AUTOONTOLOGY_CHAT_PROVIDER", "ollama")
    monkeypatch.setattr(onboarding, "provider_info", lambda s, n: ProviderInfo(n, True, True))
    res = runner.invoke(app, ["provider", "use", "codex"])
    assert res.exit_code == 0, res.output
    assert "AUTOONTOLOGY_CHAT_PROVIDER: ollama -> codex" in res.output
    assert "your shell sets" not in res.output


def test_setup_names_the_vision_model_even_when_models_are_skipped(isolated_env, monkeypatch):
    from autoontology.engine import migrate

    monkeypatch.setenv("AUTOONTOLOGY_VISION_MODEL", "gemma4:e4b")
    monkeypatch.setattr(pg, "setup_database", lambda s: "database ok")
    monkeypatch.setattr(migrate, "run_migrations", lambda s: None)
    monkeypatch.setattr(migrate, "current_revision", lambda s: "abc")
    monkeypatch.setattr(migrate, "head_revision", lambda: "abc")
    res = runner.invoke(app, ["setup", "--skip-models"])
    assert res.exit_code == 0, res.output
    assert "vision model: gemma4:e4b skipped (--skip-models)" in res.output


def test_provider_use_sets_embeddings_and_the_openai_endpoint(isolated_env, monkeypatch):
    env = Path(isolated_env) / ".env"
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=claude\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    res = runner.invoke(
        app,
        [
            "provider",
            "use",
            "openai",
            "--openai-chat-model",
            "gpt-test",
            "--base-url",
            "https://api.test/v1/",
            "--embed",
            "openai",
            "--embed-model",
            "text-embedding-test",
        ],
    )
    assert res.exit_code == 0, res.output
    text = env.read_text()
    assert "AUTOONTOLOGY_CHAT_PROVIDER=openai" in text
    assert "AUTOONTOLOGY_OPENAI_CHAT_MODEL=gpt-test" in text
    assert "AUTOONTOLOGY_OPENAI_BASE_URL=https://api.test/v1\n" in text
    assert "AUTOONTOLOGY_EMBED_PROVIDER=openai" in text
    assert "AUTOONTOLOGY_OPENAI_EMBED_MODEL=text-embedding-test" in text
    assert "API key is never taken on the command line" in res.output
    assert "/ui/settings" in res.output and "uv run autoontology restart" in res.output
    for bad in (["--embed", "gemini"], ["--base-url", "ftp://x"], ["--embed-model", "a b"]):
        assert runner.invoke(app, ["provider", "use", "claude", *bad]).exit_code == 2, bad


def test_restart_runs_the_helper_in_the_foreground(isolated_env, monkeypatch):
    import subprocess

    calls = []

    def call(argv, env):
        calls.append((argv, env))
        return 0

    monkeypatch.setattr(subprocess, "call", call)
    monkeypatch.setenv("AUTOONTOLOGY_DERIVED_ENV", "REASONING_MODEL")
    monkeypatch.setenv("REASONING_MODEL", "openai/derived")
    res = runner.invoke(app, ["restart"])
    assert res.exit_code == 0, res.output
    argv, env = calls[0]
    assert argv[1:3] == ["-m", "autoontology.engine.restart"] and "--home" in argv
    assert "--only" not in argv and "REASONING_MODEL" not in env
    runner.invoke(app, ["restart", "--only", "backend"])
    assert calls[1][0][-2:] == ["--only", "backend"]
    assert runner.invoke(app, ["restart", "--only", "service"]).exit_code == 2
    monkeypatch.setattr(subprocess, "call", lambda argv, env: 1)
    res = runner.invoke(app, ["restart"])
    assert res.exit_code == 1 and "restart failed" in res.output


def test_provider_use_leaves_a_private_env(isolated_env, monkeypatch):
    import stat

    env = Path(isolated_env) / ".env"
    env.write_text("AUTOONTOLOGY_CHAT_PROVIDER=claude\nAUTOONTOLOGY_OPENAI_API_KEY=sk-x1234567\n")
    env.chmod(0o644)
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env))
    monkeypatch.setattr(onboarding, "provider_info", lambda s, n: ProviderInfo(n, True, True))
    assert runner.invoke(app, ["provider", "use", "codex"]).exit_code == 0
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    (backup,) = Path(isolated_env).glob(".env.bak-*")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600 and "sk-x1234567" not in backup.read_text()
