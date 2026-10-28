# SPDX-License-Identifier: MIT
"""OpenAI models via the ``codex`` CLI and the user's ChatGPT login (no API key).

codex is an agent, and the prompt carries untrusted document text, so a call is boxed in:

    CODEX_HOME=<home>/run/codex-home   (only auth.json, a symlink to the user's login)
    codex exec -m <model> --ephemeral --skip-git-repo-check --ignore-user-config
               --ignore-rules -s read-only -C <empty temp dir> --json --color never
               -c web_search="disabled" --disable shell_tool --disable unified_exec ...
               -o <last.txt> [--output-schema <schema.json>] -     (prompt on stdin)

* the dedicated ``CODEX_HOME`` keeps the user's ``~/.codex/AGENTS.md`` (personal
  instructions, measured: injected even with ``--ignore-user-config``), skills, memories and
  hooks out; prompt tokens per call drop from ~17.7k to ~11.3k;
* ``--disable`` turns off every tool feature (shell, unified exec, apps, plugins, browser,
  computer use, image tools, memories, ...): asked to run ``ls``, the model answers it
  cannot;
* the ``--json`` event stream is checked and the call fails closed (502) if any item other
  than a message/reasoning/notice shows up (a command, tool or file change).

``-m`` is mandatory (the user's config default may be a model ChatGPT accounts reject).
codex has no system-prompt flag, so system messages become a leading "System:" block. ``--output-schema`` takes only OpenAI strict schemas (every object closed, every
property required); any other schema is described in the prompt and validated here.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoontology.config import CLI_KNOWN_MODELS, CODEX_REASONING_EFFORTS, codex_reasoning_effort
from autoontology.web.providers.base import (
    CLIProvider,
    ProviderStatus,
    remove_workdir,
    scrub,
)
from autoontology.web.providers.openai_compat import ChatRequest, ChatResponse, ProviderError

MODELS = list(CLI_KNOWN_MODELS["codex"])
LOGIN_REMEDY = "run `codex login` as this user (ChatGPT account)"
#: Every tool-like codex feature (``codex features list``, 0.153), all off.
DISABLED_FEATURES: tuple[str, ...] = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "remote_plugin",
    "memories",
    "skill_search",
    "tool_suggest",
    "view_image",
    "image_generation",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "goals",
    "sleep_tool",
    "hooks",
    "code_mode_host",
    "chronicle",
    "multi_agent",
    "multi_agent_v2",
)
#: Event-stream item types a pure chat answer may contain; anything else fails closed.
#: codex 0.153 exec items: agent_message, reasoning, error, todo_list (plan updates: no side
#: effect) vs command_execution, file_change, mcp_tool_call, web_search, collab_tool_call
#: (strings in the 0.153.4 binary).
ALLOWED_ITEMS: frozenset[str] = frozenset({"agent_message", "reasoning", "error", "todo_list"})
#: Lock file next to the user's ``auth.json``: every AutoOntology home (several checkouts on
#: one machine) takes it while writing that file back.
USER_SYNC_LOCK = ".autoontology-sync.lock"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_STATUS_401_RE = re.compile(r"\b401\b")
_STATUS_429_RE = re.compile(r"\b429\b")

logger = logging.getLogger("autoontology.providers")


def user_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def codex_home_dir(home: Path) -> Path:
    return Path(home).resolve() / "run" / "codex-home"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Exclusive lock on *path*: threads of this process (one lock per path), then other
    processes (``flock``; the CLI doctor vs the service, other AutoOntology homes)."""
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(path), threading.Lock())
    with thread_lock, open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def ensure_codex_home(home: Path) -> Path:
    """``<home>/run/codex-home`` holding only ``auth.json`` -> the user's login.

    A refreshed private copy (see :func:`sync_codex_home`) is written back first; if that
    fails the private copy stays in place (it holds the only valid refresh token) and codex
    keeps using it until a later write-back succeeds.
    """
    target = codex_home_dir(home)
    target.mkdir(parents=True, exist_ok=True)
    with _locked(target / ".lock"):
        return _ensure_codex_home(target)


def _ensure_codex_home(target: Path) -> Path:
    _sync_back(target)
    link = target / "auth.json"
    if not link.is_symlink() and not link.exists():
        link.symlink_to(user_codex_home() / "auth.json")
    for stray in ("AGENTS.md", "config.toml", "hooks.json"):
        (target / stray).unlink(missing_ok=True)
    return target


def sync_codex_home(home: Path) -> bool:
    """Write a refreshed private ``auth.json`` back to the user's login.

    codex refreshes its token by renaming a new file over ``auth.json``, which replaces the
    private symlink with a file holding a new, rotated refresh token; the user's own codex
    needs it, or its next refresh fails ("refresh token already used"). Runs right after
    every codex call, at ``status()`` and at service shutdown. True when nothing is left to
    write back."""
    target = codex_home_dir(home)
    if not target.is_dir():
        return True
    with _locked(target / ".lock"):
        return _sync_back(target)


def _sync_back(target: Path) -> bool:
    link = target / "auth.json"
    if link.is_symlink() or not link.exists():
        return True
    source = user_codex_home() / "auth.json"
    try:
        seen = _identity(link)
        with _locked(source.parent / USER_SYNC_LOCK):
            newer = not source.exists() or link.stat().st_mtime > source.stat().st_mtime
            if newer:
                _replace_with_copy(link, source)
    except OSError as exc:
        # keep the private copy: it holds the only valid refresh token (no content logged)
        logger.warning(
            "codex refreshed its login, but writing it back to the user's codex auth.json "
            "failed (%s, errno %s); the private copy is kept and the write-back is retried "
            "before the next codex call",
            type(exc).__name__,
            exc.errno,
        )
        return False
    # a concurrent codex call (one private home, several in flight) may have renamed a
    # newer token over the file since it was read: never delete that one, sync it next time
    try:
        changed = _identity(link) != seen
    except OSError:
        changed = True
    if changed:
        return False
    # written back, or older than the user's own (their codex refreshed later): relink
    link.unlink(missing_ok=True)
    link.symlink_to(source)
    return True


def _identity(path: Path) -> tuple[int, int, int, int]:
    """What changes when codex renames a new file over *path* (no symlink following)."""
    st = path.lstat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _replace_with_copy(src: Path, dest: Path) -> None:
    """Atomically replace *dest* by a copy of *src*: a 0600 temp file from creation
    (``mkstemp``) next to *dest*, fsynced, with *src*'s mtime, then ``os.replace``."""
    data = src.read_bytes()
    stat = src.stat()
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".auth.json.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.utime(tmp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def strict_compatible(schema: Any) -> bool:
    """True if *schema* fits OpenAI's strict structured-output subset (roughly)."""
    if isinstance(schema, list):
        return all(strict_compatible(s) for s in schema)
    if not isinstance(schema, dict):
        return True
    props = schema.get("properties")
    if schema.get("type") == "object" or props is not None:
        if schema.get("additionalProperties") is not False:
            return False
        if set(schema.get("required") or []) != set(props or {}):
            return False
    for key in (
        "default",
        "format",
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
    ):
        if key in schema:
            return False
    return all(
        strict_compatible(v)
        for k, v in schema.items()
        if k in ("properties", "items", "anyOf", "$defs", "definitions")
        for v in ([v] if k == "items" else v.values() if isinstance(v, dict) else v)
    )


class CodexCLIProvider(CLIProvider):
    name = "codex"
    bin_env = "AUTOONTOLOGY_CODEX_BIN"
    default_bin = "codex"
    login_remedy = LOGIN_REMEDY
    strip_keys = ("OPENAI_API_KEY", "CODEX_API_KEY")

    def prompt(self, request: ChatRequest, *, schema_in_prompt: bool) -> str:
        parts = []
        system = request.system_text()
        if system:
            parts.append(f"System:\n{system}")
        transcript = request.transcript()
        if system and len(request.conversation()) == 1:
            transcript = f"User:\n{transcript}"
        parts.append(transcript)
        if request.mode == "json_object":
            parts.append("Respond with a single JSON object and nothing else.")
        elif request.structured and schema_in_prompt:
            parts.append(
                "Respond with only a JSON value (no prose, no code fences) that "
                "validates against this JSON Schema:\n"
                + json.dumps(request.schema, ensure_ascii=False)
            )
        parts.append("Answer directly: do not run commands or read files.")
        return "\n\n".join(parts)

    def argv(self, binary: str, request: ChatRequest, workdir, *, schema_file=None) -> list[str]:
        argv = [
            binary,
            "exec",
            "-m",
            request.model,
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-s",
            "read-only",
            "-C",
            str(workdir),
            "--json",
            "--color",
            "never",
            "-c",
            'web_search="disabled"',
        ]
        for feature in DISABLED_FEATURES:
            argv += ["--disable", feature]
        argv += ["-o", str(workdir / "last.txt")]
        effort = codex_reasoning_effort()
        if effort:
            if effort not in CODEX_REASONING_EFFORTS:
                raise ProviderError(
                    400,
                    "invalid_reasoning_effort",
                    f"AUTOONTOLOGY_CODEX_REASONING_EFFORT {effort[:40]!r} is not one of "
                    f"{', '.join(CODEX_REASONING_EFFORTS)}",
                    "fix or unset AUTOONTOLOGY_CODEX_REASONING_EFFORT in .env and restart",
                )
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        if schema_file is not None:
            argv += ["--output-schema", str(schema_file)]
        return [*argv, "-"]

    def child_env(self) -> dict[str, str]:
        env = super().child_env()
        env["CODEX_HOME"] = str(ensure_codex_home(self.home))
        return env

    def sync_login(self) -> None:
        """Write a token refresh of the last call back to the user's login (never raises)."""
        try:
            sync_codex_home(self.home)
        except OSError as exc:
            logger.warning("codex login write-back skipped: %s", type(exc).__name__)

    def on_shutdown(self) -> None:
        self.sync_login()

    async def call(self, request: ChatRequest, *, timeout: float) -> ChatResponse:
        binary = self.require_binary()
        workdir = self.workdir()
        try:
            schema_file = None
            native = (
                request.mode in ("json_schema", "tool")
                and request.schema
                and strict_compatible(request.schema)
            )
            if native:
                schema_file = workdir / "schema.json"
                schema_file.write_text(json.dumps(request.schema), encoding="utf-8")
            argv = self.argv(binary, request, workdir, schema_file=schema_file)
            result = await self.run(
                argv,
                stdin=self.prompt(request, schema_in_prompt=not native),
                cwd=workdir,
                timeout=timeout,
            )
            last = workdir / "last.txt"
            content = last.read_text(encoding="utf-8") if last.is_file() else ""
        finally:
            remove_workdir(workdir)
            # the child has exited (or was killed): a token it refreshed goes back now
            await asyncio.to_thread(self.sync_login)
        events = _events(result.stdout)
        used = _tool_items(events)
        if used:
            raise ProviderError(
                502,
                "tool_use_refused",
                f"codex tried to use a tool ({', '.join(used)}); answers must "
                "be plain text, so the call was discarded",
            )
        failure = _failure(events, result.stderr)
        if result.returncode != 0 or (failure and not content.strip()):
            raise _error(
                failure or result.stderr or result.stdout, request.model, result.returncode
            )
        usage = (
            next(
                (
                    e.get("usage")
                    for e in reversed(events)
                    if e.get("type") == "turn.completed" and e.get("usage")
                ),
                {},
            )
            or {}
        )
        return ChatResponse(
            content=content.strip(),
            model=request.model,
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            extra={
                "reasoning_tokens": usage.get("reasoning_output_tokens"),
                "output_schema": bool(native),
            },
        )

    def login_check(self, binary: str) -> ProviderStatus:
        proc = self.run_sync([binary, "login", "status"])
        text = f"{proc.stdout}\n{proc.stderr}".strip()
        lowered = text.lower()
        if proc.returncode == 0 and lowered.startswith("logged in"):
            how = "ChatGPT" if "chatgpt" in lowered else "API key" if "api key" in lowered else "?"
            return ProviderStatus(self.name, True, True, f"logged in ({how})", "", self.models())
        return ProviderStatus(
            self.name, True, False, scrub(text, 120) or "not logged in", LOGIN_REMEDY, self.models()
        )


def _events(stdout: str) -> list[dict[str, Any]]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _tool_items(events: list[dict[str, Any]]) -> list[str]:
    """Item types beyond a plain answer (commands, tools, file changes, web search)."""
    seen = []
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and str(event.get("type", "")).startswith("item."):
            kind = str(item.get("type") or "?")
            if kind not in ALLOWED_ITEMS and kind not in seen:
                seen.append(kind)
    return seen


def _failure(events: list[dict[str, Any]], stderr: str) -> str:
    for event in reversed(events):
        if event.get("type") == "turn.failed":
            return str((event.get("error") or {}).get("message") or "turn failed")
    errors = [str(e.get("message") or "") for e in events if e.get("type") == "error"]
    return errors[-1] if errors else ""


def _error(message: str, model: str, returncode: int) -> ProviderError:
    lowered = message.lower()
    if _STATUS_401_RE.search(lowered) or "unauthorized" in lowered or "not logged in" in lowered:
        return ProviderError(401, "not_logged_in", "codex CLI is not logged in", LOGIN_REMEDY)
    if "model_not_found" in lowered or "model is not supported" in lowered:
        return ProviderError(
            400, "model_not_found", f"codex cannot use model {model!r} with this account"
        )
    if _STATUS_429_RE.search(lowered) or "usage limit" in lowered or "rate limit" in lowered:
        return ProviderError(
            429,
            "usage_limit",
            f"codex usage limit: {scrub(message, 300)}",
            "wait for the ChatGPT plan limit to reset or switch provider",
        )
    return ProviderError(502, "provider_error", f"codex exited {returncode}: {scrub(message)}")
