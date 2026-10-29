# SPDX-License-Identifier: MIT
"""scripts/bootstrap.sh: flags, dry-run plan, exit codes (no network, no real `claude`/`uv`)."""

import os
import pty
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "bootstrap.sh"
BASH = shutil.which("bash") or "/bin/bash"
REAL_GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(not Path(BASH).exists(), reason="bash not available")

LOGGED_IN = '{"loggedIn": true, "email": "someone@example.com", "orgName": "Example"}'
LOGGED_OUT = '{"loggedIn": false, "authMethod": "none"}'


def _run(args, *, env=None, cwd=None, timeout=60, tty=False):
    base = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "AUTOONTOLOGY_", "BACKEND_PORT"))
    }
    if env:
        base.update(env)
    stdin = subprocess.DEVNULL
    master = slave = None
    if tty:
        master, slave = pty.openpty()
        stdin = slave
    try:
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            capture_output=True,
            stdin=stdin,
            text=True,
            env=base,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    finally:
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)


def _stub(bindir: Path, name: str, body: str) -> Path:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_path(tmp_path: Path, claude_status: str | None = LOGGED_IN, *, docker=False, pg_env=True):
    """PATH with stubs for every tool the script calls; each call is logged.

    `git` wraps the real git (logged), `uv`/`docker`/`claude` are fakes, `curl` answers only
    the Ollama probe and never downloads anything.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    rec = f'echo "$(basename "$0") $*" >> "{log}"'
    _stub(bindir, "git", rec + f'\nexec "{REAL_GIT}" "$@"')
    _stub(bindir, "uv", rec + "\necho 'uv 0.9.0'")
    _stub(bindir, "docker", rec + ("\nexit 0" if docker else "\nexit 1"))
    _stub(bindir, "node", rec + "\necho v22.1.0")
    _stub(bindir, "ollama", rec + "\necho 'ollama version is 0.12.0'")
    _stub(bindir, "curl", rec + '\ncase "$*" in *api/tags*) exit 0;; esac\nexit 7')
    _stub(bindir, "nc", rec + "\nexit 1")
    if claude_status is not None:
        status = claude_status.replace("'", "'\\''")
        _stub(
            bindir,
            "claude",
            rec + f"\ncase \"$1 $2\" in 'auth status') printf '%s\\n' '{status}';; esac",
        )
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(tmp_path)}
    if pg_env:
        env.update({"POSTGRES_HOST": "db.invalid", "POSTGRES_PORT": "5432"})
    return env, log


def _fixture_repo(tmp_path: Path) -> Path:
    """A tiny git repo that looks like an AutoOntology checkout, to clone with --repo."""
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "autoontology"\n')
    (src / "README.md").write_text("fixture\n")
    git = ["git", "-C", str(src), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    return src


def test_help_lists_every_flag():
    proc = _run(["--help"])
    assert proc.returncode == 0, proc.stderr
    for flag in ("--dir", "--provider", "--set", "--no-demo", "--yes", "--dry-run", "--lang"):
        assert flag in proc.stdout
    assert "Exit codes" in proc.stdout
    assert "second checkout" in proc.stdout
    assert "--repo" not in proc.stdout  # hidden testing flag


def test_unknown_provider_is_a_usage_error(tmp_path):
    proc = _run(["--provider", "bogus", "--dir", str(tmp_path / "x")])
    assert proc.returncode == 2
    assert "bogus" in proc.stderr
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize(
    "args",
    [["--set", "nope"], ["--lang", "fr"], ["--frobnicate"], ["--dir"]],
)
def test_other_usage_errors(tmp_path, args):
    assert _run(args).returncode == 2


def test_no_terminal_without_yes_is_refused(tmp_path):
    env, log = _fake_path(tmp_path)
    proc = _run(["--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 2
    assert "--yes" in proc.stderr
    assert not log.exists()  # nothing was run


def test_truncated_script_runs_nothing(tmp_path):
    """A partial download (curl | bash cut short) must not execute any step."""
    env, log = _fake_path(tmp_path)
    text = SCRIPT.read_text()
    cut = text[: text.index('main "$@"') - 2000]
    proc = subprocess.run(
        [BASH, "-s", "--", "--yes", "--dir", str(tmp_path / "c")],
        input=cut,
        capture_output=True,
        text=True,
        env={**env},
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert "[1/6]" not in proc.stdout
    assert not log.exists()


def test_dry_run_prints_every_step_and_changes_nothing(tmp_path):
    env, log = _fake_path(tmp_path)
    target = tmp_path / "clone"
    before = sorted(p.name for p in tmp_path.iterdir())
    proc = _run(
        ["--dry-run", "--yes", "--dir", str(target), "--provider", "claude"], env=env, cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    for n in range(1, 7):
        assert f"[{n}/6]" in out
    assert "git clone --recurse-submodules" in out
    assert "uv sync" in out
    assert "autoontology init --yes --provider claude" in out
    assert "autoontology demo --set quickstart --no-open --replace" in out
    assert "http://127.0.0.1:7800/ui/" in out
    assert "dry-run" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*before, "calls.log"])
    assert not target.exists()
    calls = log.read_text()
    assert "uv sync" not in calls and "uv run" not in calls and "git clone" not in calls


def test_dry_run_no_demo_and_hanbit(tmp_path):
    env, _ = _fake_path(tmp_path)
    proc = _run(["--dry-run", "--no-demo", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "autoontology demo --set quickstart --no-open" not in proc.stdout
    assert "demo skipped" in proc.stdout
    proc = _run(
        ["--dry-run", "--set", "hanbit", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path
    )
    assert "autoontology demo --set hanbit" in proc.stdout


def test_dry_run_korean_next_steps(tmp_path):
    env, _ = _fake_path(tmp_path)
    proc = _run(["--dry-run", "--lang", "ko", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 0
    assert "다음 단계" in proc.stdout


def test_paths_with_spaces_are_quoted(tmp_path):
    env, _ = _fake_path(tmp_path)
    proc = _run(["--dry-run", "--no-demo", "--dir", str(tmp_path / "c d")], env=env, cwd=tmp_path)
    assert proc.returncode == 0
    assert "cd " + str(tmp_path / "c d").replace(" ", "\\ ") in proc.stdout


def test_port_override_and_password_never_printed(tmp_path):
    env, _ = _fake_path(tmp_path)
    env.update(
        {"AUTOONTOLOGY_PORT": "7890", "BACKEND_PORT": "3191", "POSTGRES_PASSWORD": "pw-Zq81-secret"}
    )
    proc = _run(["--dry-run", "--yes", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "http://127.0.0.1:7890/ui/" in proc.stdout
    assert "ports saved in .env" not in proc.stdout  # dry-run only plans it
    assert "EnvFile" in proc.stdout  # the planned .env update
    assert "pw-Zq81-secret" not in proc.stdout + proc.stderr


def test_claude_not_logged_in_stops_with_exit_3(tmp_path):
    env, log = _fake_path(tmp_path, LOGGED_OUT)
    target = tmp_path / "clone"
    proc = _run(["--yes", "--provider", "claude", "--dir", str(target)], env=env, cwd=tmp_path)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    text = proc.stdout + proc.stderr
    assert "claude auth login" in text
    assert "organization" in text  # the Enterprise hint
    calls = log.read_text()
    assert "claude auth status" in calls
    assert "uv run" not in calls and "uv sync" not in calls
    assert "git clone" not in calls
    assert not target.exists()


def test_claude_logged_in_pretty_json_and_no_account_details(tmp_path):
    pretty = '{\n  "loggedIn": true,\n  "email": "someone@example.com"\n}'
    env, _ = _fake_path(tmp_path, pretty)
    proc = _run(
        ["--dry-run", "--provider", "claude", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claude CLI logged in" in proc.stdout
    assert "someone@example.com" not in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "output",
    [
        "",
        "error: unknown command 'auth'",
        '{"authMethod": "none"}',
        '{"nested": {"loggedIn": true}}',
    ],
    ids=["empty", "old-cli", "no-key", "nested-only"],
)
def test_unreadable_claude_status_is_a_prerequisite_problem(tmp_path, output):
    env, _ = _fake_path(tmp_path, output)
    proc = _run(["--yes", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    text = proc.stdout + proc.stderr
    assert "claude update" in text
    assert "not logged in" not in text


def test_claude_status_timeout(tmp_path, monkeypatch):
    env, _ = _fake_path(tmp_path, None)
    _stub(tmp_path / "bin", "claude", "sleep 30")
    script = SCRIPT.read_text().replace("run_timeout 20 ", "run_timeout 1 ")
    fast = tmp_path / "bootstrap.sh"
    fast.write_text(script)
    proc = subprocess.run(
        [BASH, str(fast), "--yes", "--dir", str(tmp_path / "c")],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 4
    assert "did not answer" in proc.stderr


def test_claude_bin_from_environment_or_local_install(tmp_path):
    env, log = _fake_path(tmp_path, None)
    elsewhere = tmp_path / "opt"
    elsewhere.mkdir()
    _stub(elsewhere, "claude-real", f'echo "claude-real $*" >> "{log}"\necho \'{LOGGED_IN}\'')
    proc = _run(
        ["--dry-run", "--dir", str(tmp_path / "c")],
        env={**env, "AUTOONTOLOGY_CLAUDE_BIN": str(elsewhere / "claude-real")},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "claude CLI logged in" in proc.stdout
    assert "claude-real auth status" in log.read_text()
    # Not on PATH, no variable: the ~/.claude/local install is used.
    local = Path(env["HOME"]) / ".claude" / "local"
    local.mkdir(parents=True)
    _stub(local, "claude", f"echo '{LOGGED_IN}'")
    proc = _run(["--dry-run", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert "claude CLI logged in" in proc.stdout


def test_missing_claude_cli_explains_aliases(tmp_path):
    env, _ = _fake_path(tmp_path, None)
    proc = _run(["--yes", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    text = proc.stdout + proc.stderr
    assert "Claude Code" in text
    assert "aliases" in text and "AUTOONTOLOGY_CLAUDE_BIN" in text


def test_missing_uv_without_yes_prints_installer_and_stops(tmp_path):
    env, log = _fake_path(tmp_path)
    (tmp_path / "bin" / "uv").unlink()
    proc = _run(["--dir", str(tmp_path / "c")], env=env, cwd=tmp_path, tty=True)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "https://astral.sh/uv/install.sh" in proc.stdout + proc.stderr
    assert "curl -LsSf" not in log.read_text()  # did not download anything


def test_no_docker_and_no_postgres_settings(tmp_path):
    env, _ = _fake_path(tmp_path, pg_env=False)
    proc = _run(["--yes", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    assert "POSTGRES_HOST" in proc.stderr and "Docker" in proc.stderr


def test_existing_non_checkout_directory_is_refused(tmp_path):
    env, _ = _fake_path(tmp_path)
    target = tmp_path / "c"
    target.mkdir()
    (target / "something.txt").write_text("x")
    proc = _run(["--yes", "--dir", str(target)], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    assert "not an AutoOntology checkout" in proc.stdout + proc.stderr


@pytest.mark.skipif(REAL_GIT is None, reason="git not available")
def test_clone_then_rerun_updates_and_keeps_env(tmp_path):
    env, log = _fake_path(tmp_path)
    upstream = _fixture_repo(tmp_path)
    target = tmp_path / "clone"
    args = ["--yes", "--no-demo", "--repo", str(upstream), "--dir", str(target)]
    first = _run(args, env=env, cwd=tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "cloned" in first.stdout
    assert (target / "README.md").exists()
    (target / ".env").write_text("AUTOONTOLOGY_CHAT_PROVIDER=claude\nPOSTGRES_PASSWORD=x\n")
    second = _run(args, env=env, cwd=tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "updated" in second.stdout and "cloned" not in second.stdout
    assert (
        target / ".env"
    ).read_text() == "AUTOONTOLOGY_CHAT_PROVIDER=claude\nPOSTGRES_PASSWORD=x\n"
    calls = log.read_text().splitlines()
    assert sum(1 for c in calls if c.startswith("git clone")) == 1
    assert any(c.startswith("git -C") and "pull --ff-only" in c for c in calls)
    assert (
        sum(1 for c in calls if c.startswith("uv run autoontology init --yes --provider claude"))
        == 2
    )
    assert not any("autoontology demo" in c for c in calls)


@pytest.mark.skipif(REAL_GIT is None, reason="git not available")
def test_shell_ports_are_saved_into_env(tmp_path):
    env, log = _fake_path(tmp_path)
    upstream = _fixture_repo(tmp_path)
    proc = _run(
        ["--yes", "--no-demo", "--repo", str(upstream), "--dir", str(tmp_path / "c")],
        env={**env, "AUTOONTOLOGY_PORT": "7890"},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ports saved in .env" in proc.stdout
    assert "http://127.0.0.1:7890/ui/" in proc.stdout
    assert any(c.startswith("uv run python -c") for c in log.read_text().splitlines())


def test_script_is_bash_32_compatible_syntax():
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    for bashism in ("declare -A", "mapfile", "readarray", "${var,,}", "|&", "eval "):
        assert bashism not in text
    assert text.rstrip().endswith('main "$@"')
    assert subprocess.run([BASH, "-n", str(SCRIPT)], check=False).returncode == 0


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean():
    proc = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout


# --- Agent instructions, skill and docs -------------------------------------------------

PROMPT_EN = (
    "Clone https://github.com/kimtoma/AutoOntology into ~/AutoOntology and run "
    "`scripts/bootstrap.sh --yes --provider claude` there"
)
CURL = (
    "curl -fsSL https://raw.githubusercontent.com/kimtoma/AutoOntology/main/scripts/bootstrap.sh"
    " | bash -s -- --yes --provider claude"
)
SKILL = REPO / ".claude" / "skills" / "setup-autoontology" / "SKILL.md"
ONE_PROMPT_DOCS = {
    REPO / "README.md": "## Start with one prompt",
    REPO / "README.ko.md": "## 프롬프트 한 번으로 시작하기",
    REPO / "docs" / "GUIDE.md": "## Start with one prompt",
    REPO / "docs" / "GUIDE.ko.md": "## 프롬프트 한 번으로 시작하기",
}


def test_agents_md_is_a_copy_of_claude_md():
    agents = REPO / "AGENTS.md"
    assert not agents.is_symlink()
    assert agents.read_text() == (REPO / "CLAUDE.md").read_text()
    text = agents.read_text()
    for needle in (
        "scripts/bootstrap.sh --yes --provider claude",
        "vendor/",
        "SPDX",
        "autoontology_src_",
    ):
        assert needle in text


def test_skill_frontmatter_and_command():
    text = SKILL.read_text()
    assert text.startswith("---\n")
    front = text.split("---\n", 2)[1]
    assert "name: setup-autoontology\n" in front
    assert "description: " in front
    assert "scripts/bootstrap.sh --yes --provider claude" in text
    assert "AUTOONTOLOGY_LANG=ko" in text
    assert "background" in text and "AUTOONTOLOGY_CLAUDE_BIN" in text


@pytest.mark.parametrize("doc", list(ONE_PROMPT_DOCS), ids=lambda p: p.name)
def test_one_prompt_section(doc):
    text = doc.read_text()
    assert ONE_PROMPT_DOCS[doc] in text
    assert PROMPT_EN in text
    assert "~/AutoOntology 에 클론하고" in text  # the Korean prompt too
    assert CURL in text
    assert "claude auth login" in text and "claude auth status" in text
    assert "run it in the background and wait" in text  # the agent must not time out
    assert "백그라운드로 실행하고 끝날 때까지 기다려" in text
    if doc.name.endswith(".ko.md"):
        assert "`--yes`는 묻지 않고 진행합니다" in text
        assert "Cowork에서는 시험해 보지 않았고" in text
    else:
        assert "`--yes` makes it non-interactive" in text
        assert "Cowork has not been tested" in text


def test_documented_commands_exist_in_agent_files():
    from tests.test_doc_commands import test_documented_commands_exist

    for doc in (REPO / "CLAUDE.md", SKILL):
        try:
            test_documented_commands_exist(doc)
        except AssertionError as exc:
            if "suspiciously few" not in str(exc):
                raise


LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.mark.parametrize(
    "doc",
    [*ONE_PROMPT_DOCS, REPO / "CLAUDE.md", SKILL],
    ids=lambda p: p.name,
)
def test_relative_links_resolve(doc):
    missing = []
    for target in LINK.findall(doc.read_text()):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        if "/" not in target and "." not in target:  # prose like "[[X]](Ollama)", not a path
            continue
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, missing
