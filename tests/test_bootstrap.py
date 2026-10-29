# SPDX-License-Identifier: MIT
"""scripts/bootstrap.sh: flags, dry-run plan, exit codes (no network, no `uv run`)."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "bootstrap.sh"
BASH = shutil.which("bash") or "/bin/bash"

pytestmark = pytest.mark.skipif(not Path(BASH).exists(), reason="bash not available")


def _run(args, *, env=None, cwd=None, timeout=60):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "AUTOONTOLOGY_"))}
    if env:
        base.update(env)
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_path(tmp_path: Path, claude_status: str) -> tuple[dict, Path]:
    """PATH with stubs for every tool the prerequisite step looks at; calls are logged."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    rec = f'echo "$(basename "$0") $*" >> "{log}"'
    _stub(bindir, "git", rec + "\necho 'git version 2.40.0'")
    _stub(bindir, "uv", rec + "\necho 'uv 0.9.0'")
    _stub(bindir, "docker", rec + "\nexit 1")  # daemon not reachable
    _stub(bindir, "node", rec + "\necho v22.1.0")
    _stub(bindir, "ollama", rec + "\necho 'ollama version is 0.12.0'")
    # Ollama answers; nothing else is reachable and nothing is ever downloaded.
    _stub(bindir, "curl", rec + '\ncase "$*" in *api/tags*) exit 0;; esac\nexit 7')
    _stub(bindir, "nc", rec + "\nexit 1")
    _stub(
        bindir, "claude", rec + f"\ncase \"$1 $2\" in 'auth status') echo '{claude_status}';; esac"
    )
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "POSTGRES_HOST": "db.invalid",
        "POSTGRES_PORT": "5432",
    }
    return env, log


def test_help_lists_every_flag():
    proc = _run(["--help"])
    assert proc.returncode == 0, proc.stderr
    for flag in ("--dir", "--provider", "--set", "--no-demo", "--yes", "--dry-run", "--lang"):
        assert flag in proc.stdout
    assert "Exit codes" in proc.stdout
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


def test_dry_run_prints_every_step_and_changes_nothing(tmp_path):
    target = tmp_path / "clone"
    before = sorted(p.name for p in tmp_path.iterdir())
    proc = _run(
        ["--dry-run", "--yes", "--dir", str(target), "--provider", "claude"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    for n in range(1, 7):
        assert f"[{n}/6]" in out
    assert "git clone --recurse-submodules" in out
    assert "uv sync" in out
    assert "autoontology init --yes --provider claude" in out
    assert "autoontology demo --set quickstart --no-open --replace" in out
    assert "/ui/" in out
    assert "dry-run" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not target.exists()


def test_dry_run_no_demo_and_hanbit(tmp_path):
    proc = _run(["--dry-run", "--no-demo", "--dir", str(tmp_path / "c")], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "autoontology demo --set quickstart --no-open" not in proc.stdout
    assert "demo skipped" in proc.stdout
    proc = _run(["--dry-run", "--set", "hanbit", "--dir", str(tmp_path / "c")], cwd=tmp_path)
    assert "autoontology demo --set hanbit" in proc.stdout


def test_dry_run_korean_next_steps(tmp_path):
    proc = _run(["--dry-run", "--lang", "ko", "--dir", str(tmp_path / "c")], cwd=tmp_path)
    assert proc.returncode == 0
    assert "다음 단계" in proc.stdout


def test_claude_not_logged_in_stops_with_exit_3(tmp_path):
    env, log = _fake_path(tmp_path, '{"loggedIn": false, "authMethod": "none"}')
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


def test_claude_logged_in_passes_the_login_check(tmp_path):
    env, log = _fake_path(tmp_path, '{"loggedIn": true, "email": "someone@example.com"}')
    proc = _run(
        ["--dry-run", "--provider", "claude", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "logged in" in proc.stdout
    assert "someone@example.com" not in proc.stdout + proc.stderr  # never echo account details


def test_missing_claude_cli_is_a_missing_prerequisite(tmp_path):
    env, _ = _fake_path(tmp_path, "{}")
    (tmp_path / "bin" / "claude").unlink()
    proc = _run(["--yes", "--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    assert "Claude Code" in proc.stdout + proc.stderr


def test_missing_uv_without_yes_prints_installer_and_stops(tmp_path):
    env, log = _fake_path(tmp_path, '{"loggedIn": true}')
    (tmp_path / "bin" / "uv").unlink()
    proc = _run(["--dir", str(tmp_path / "c")], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    assert "https://astral.sh/uv/install.sh" in proc.stdout + proc.stderr
    assert "curl" not in log.read_text()  # did not download anything


def test_existing_non_checkout_directory_is_refused(tmp_path):
    env, _ = _fake_path(tmp_path, '{"loggedIn": true}')
    target = tmp_path / "c"
    target.mkdir()
    (target / "something.txt").write_text("x")
    proc = _run(["--yes", "--dir", str(target)], env=env, cwd=tmp_path)
    assert proc.returncode == 4
    assert "not an AutoOntology checkout" in proc.stdout + proc.stderr


def test_script_is_bash_32_compatible_syntax():
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    for bashism in ("declare -A", "mapfile", "readarray", "${var,,}", "|&"):
        assert bashism not in text
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


@pytest.mark.parametrize("doc", list(ONE_PROMPT_DOCS), ids=lambda p: p.name)
def test_one_prompt_section(doc):
    text = doc.read_text()
    assert ONE_PROMPT_DOCS[doc] in text
    assert PROMPT_EN in text
    assert "~/AutoOntology 에 클론하고" in text  # the Korean prompt too
    assert CURL in text
    assert "claude auth login" in text and "claude auth status" in text


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
