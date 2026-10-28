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
    assert res.output.count("Retry?") == 3


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
    assert text.startswith(
        "# my settings\nAUTOONTOLOGY_PORT=7810\nAUTOONTOLOGY_CHAT_PROVIDER=codex\nPOSTGRES_USER=me\n"
    )
    assert "# --- Set by autoontology init ---\n" in text and "AUTOONTOLOGY_CODEX_MODEL=" in text
    backups = list(env.parent.glob(".env.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    assert "AUTOONTOLOGY_CHAT_PROVIDER: ollama -> codex" in res.output
    # running it again changes nothing and makes no new backup
    again = runner.invoke(app, ["init", "--yes", "--no-docs", "--env", str(env)])
    assert again.exit_code == 0 and "no changes" in again.output
    assert len(list(env.parent.glob(".env.bak-*"))) == 1


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
    return calls


def test_demo_runs_the_steps_in_order(demo_env):
    res = runner.invoke(app, ["demo", "--no-open"])
    assert res.exit_code == 0, res.output
    assert demo_env == [
        "up",
        ("build", ("quick", "quick-docs"), False),
        "export",
        ("ui", True),
    ]
    assert "quick: created" in res.output and "≈ estimate" in res.output
    assert "What to look at next: Ontology → Graph → Playground" in res.output
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
