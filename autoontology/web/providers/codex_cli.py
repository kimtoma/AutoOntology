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

import fcntl
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from autoontology.config import CLI_KNOWN_MODELS
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
_HOME_LOCK = threading.Lock()


def user_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def ensure_codex_home(home: Path) -> Path:
    """``<home>/run/codex-home`` holding only ``auth.json`` -> the user's login.

    If codex replaced the symlink with a file (token refresh written via rename), the newer
    tokens are copied back to the user's ``auth.json`` first so the user's own codex keeps a
    valid refresh token, then the symlink is restored.
    """
    target = Path(home).resolve() / "run" / "codex-home"
    target.mkdir(parents=True, exist_ok=True)
    # threads of this process, then other AutoOntology processes (CLI doctor vs the service)
    with _HOME_LOCK, open(target / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _ensure_codex_home(target)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _ensure_codex_home(target: Path) -> Path:
    source = user_codex_home() / "auth.json"
    link = target / "auth.json"
    if link.exists() and not link.is_symlink():
        try:
            if not source.exists() or link.stat().st_mtime > source.stat().st_mtime:
                tmp = source.with_name(".auth.json.autoontology-tmp")
                shutil.copy2(link, tmp)
                os.chmod(tmp, 0o600)
                os.replace(tmp, source)
        finally:
            link.unlink(missing_ok=True)
    if not link.is_symlink():
        link.symlink_to(source)
    for stray in ("AGENTS.md", "config.toml", "hooks.json"):
        (target / stray).unlink(missing_ok=True)
    return target


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
        effort = os.environ.get("AUTOONTOLOGY_CODEX_REASONING_EFFORT", "").strip()
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        if schema_file is not None:
            argv += ["--output-schema", str(schema_file)]
        return [*argv, "-"]

    def child_env(self) -> dict[str, str]:
        env = super().child_env()
        env["CODEX_HOME"] = str(ensure_codex_home(self.home))
        return env

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
    if "401" in lowered or "unauthorized" in lowered or "not logged in" in lowered:
        return ProviderError(401, "not_logged_in", "codex CLI is not logged in", LOGIN_REMEDY)
    if "model_not_found" in lowered or "model is not supported" in lowered:
        return ProviderError(
            400, "model_not_found", f"codex cannot use model {model!r} with this account"
        )
    if "429" in lowered or "usage limit" in lowered or "rate limit" in lowered:
        return ProviderError(
            429,
            "usage_limit",
            f"codex usage limit: {scrub(message, 300)}",
            "wait for the ChatGPT plan limit to reset or switch provider",
        )
    return ProviderError(502, "provider_error", f"codex exited {returncode}: {scrub(message)}")
