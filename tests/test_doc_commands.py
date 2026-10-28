"""Every `autoontology ...` command the docs show must exist with the options it uses.

Parses fenced code blocks and inline code spans of README.md, walks
the typer/click command tree for each `autoontology <cmd> [<sub>] [--opt ...]` and checks
the command path and every `--option`.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import typer.main

from autoontology.cli import app

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md"]
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
INLINE = re.compile(r"`([^`\n]+)`")
INVOCATION = re.compile(r"(?:^\s*|\buv run\s+)autoontology\s+([a-z][^\n#;&|)`]*)")


def invocations(text: str) -> list[str]:
    found = []
    for block in FENCE.findall(text):
        for line in block.splitlines():
            found += [m.group(1).strip() for m in INVOCATION.finditer(line)]
    for span in INLINE.findall(FENCE.sub("", text)):
        found += [m.group(1).strip() for m in INVOCATION.finditer(span)]
    return found


def is_group(command) -> bool:
    return hasattr(command, "commands")


def resolve(root, args: list[str]):
    command = root
    rest = list(args)
    while is_group(command) and rest:
        name = rest.pop(0)
        sub = command.commands.get(name)
        if sub is None:
            raise AssertionError(f"unknown command {name!r} in `autoontology {' '.join(args)}`")
        command = sub
    return command, rest


def option_names(command) -> set[str]:
    return {opt for p in command.params for opt in (*p.opts, *p.secondary_opts)}


def test_invocation_parser():
    text = (
        "```bash\nuv run autoontology build hanbit --resume   # comment\n"
        "uv run autoontology up && uv run autoontology status\n```\n"
        "text `uv run autoontology source rm x --drop-db` and `autoontology.config`, "
        "`autoontology_src_<name>`, `pg_restore -d autoontology x.dump`, `autoontology doctor`.\n"
    )
    assert invocations(text) == [
        "build hanbit --resume",
        "up",
        "status",
        "source rm x --drop-db",
        "doctor",
    ]


def test_resolve_rejects_unknown():
    root = typer.main.get_command(app)
    with pytest.raises(AssertionError):
        resolve(root, ["sources", "list"])
    command, rest = resolve(root, ["tools", "install-kordoc", "--force"])
    assert command.name == "install-kordoc" and rest == ["--force"]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_documented_commands_exist(doc: Path):
    root = typer.main.get_command(app)
    calls = invocations(doc.read_text(encoding="utf-8"))
    assert len(calls) >= 5, f"suspiciously few autoontology invocations in {doc.name}"
    problems = []
    for call in calls:
        try:
            tokens = shlex.split(call)
        except ValueError:
            tokens = call.split()
        tokens = [t for t in tokens if not t.startswith("<") and t not in ("[", "]", "|")]
        try:
            command, rest = resolve(root, tokens)
        except AssertionError as exc:
            problems.append(str(exc))
            continue
        if is_group(command):
            problems.append(f"`autoontology {call}` names a group, not a command")
            continue
        known = option_names(command)
        for token in rest:
            flag = token.strip("[]").split("=", 1)[0]
            if flag.startswith("--") and flag not in known:
                problems.append(f"`autoontology {call}`: {command.name} has no option {flag}")
    assert not problems, "\n".join(problems)
