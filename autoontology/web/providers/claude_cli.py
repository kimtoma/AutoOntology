# SPDX-License-Identifier: MIT
"""Claude via the ``claude`` CLI and the user's claude.ai login (no API key).

One call = ``claude -p`` with tools, MCP servers, settings files and the default system
prompt all switched off (the default Claude Code prompt costs ~100x a short call):

    claude -p --no-session-persistence --output-format json --model <m> --tools ""
           --strict-mcp-config --setting-sources "" --disable-slash-commands
           --system-prompt-file <workdir>/system.txt
           [--json-schema <schema JSON>]     (prompt on stdin)

The env sets ``INPUT_LOCKDOWN_ENV`` after scrubbing the parent's ``CLAUDE_CODE_*`` keys, so
``@/path`` mentions in the (untrusted) prompt are not expanded into file contents.

The system prompt goes through a file (not visible in ``ps``); ``--json-schema`` only takes
inline JSON (no file/@ form, checked 2026-10-28), so its size is checked against ARG_MAX.

The prompt goes on stdin: ``--tools`` is variadic and would swallow a positional prompt.
``--bare`` is never used (it skips the keychain login -> "Not logged in"). The JSON
envelope carries ``structured_output`` (parsed object), ``result`` (text), ``is_error``,
``usage``, ``total_cost_usd`` (list-price equivalent; a subscription is not billed per call
but counts against its usage limits).
"""

from __future__ import annotations

import json
import os
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

DEFAULT_SYSTEM = "You are a precise assistant. Follow the user's instructions exactly."
JSON_OBJECT_HINT = "Respond with a single JSON object and nothing else."
MODELS = list(CLI_KNOWN_MODELS["claude"])
LOGIN_REMEDY = "run `claude auth login` (or `claude` then /login) as this user"
#: Switch off the CLI's input preprocessing and customizations (checked against 2.1.311):
#: `@path` mentions, attachments (memory, reminders, IDE/file context), CLAUDE.md files and
#: auto memory. `--safe-mode` would do more but hangs `-p` (2026-10-28), so it is not used.
INPUT_LOCKDOWN_ENV = {
    "CLAUDE_CODE_DISABLE_FILE_MENTIONS": "1",
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}
#: --json-schema is inline: keep it well inside ARG_MAX (env + other args share it).
MAX_SCHEMA_ARG_BYTES = max(
    64 * 1024, (os.sysconf("SC_ARG_MAX") if hasattr(os, "sysconf") else 262144) // 4
)


class ClaudeCLIProvider(CLIProvider):
    name = "claude"
    bin_env = "AUTOONTOLOGY_CLAUDE_BIN"
    default_bin = "claude"
    login_remedy = LOGIN_REMEDY
    strip_keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    native_max_tokens = True

    def child_env(self) -> dict[str, str]:
        # Set after the CLAUDE_CODE_* scrub: the prompt is untrusted data, and without these
        # `claude -p` expands `@/abs/path` mentions into file contents (and attaches other
        # context) before the model sees it, independently of `--tools ""`.
        return {**super().child_env(), **INPUT_LOCKDOWN_ENV}

    def argv(self, binary: str, request: ChatRequest, workdir: Path) -> list[str]:
        system = request.system_text() or DEFAULT_SYSTEM
        if request.mode == "json_object":
            system = f"{system}\n\n{JSON_OBJECT_HINT}"
        system_file = workdir / "system.txt"
        system_file.write_text(system, encoding="utf-8")
        argv = [
            binary,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--model",
            request.model,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--system-prompt-file",
            str(system_file),
        ]
        if request.mode in ("json_schema", "tool") and request.schema:
            schema = json.dumps(request.schema, ensure_ascii=False)
            if len(schema.encode("utf-8")) > MAX_SCHEMA_ARG_BYTES:
                raise ProviderError(
                    413,
                    "request_too_large",
                    f"the JSON schema is {len(schema.encode())} bytes; claude "
                    f"takes it on the command line (max "
                    f"{MAX_SCHEMA_ARG_BYTES} bytes)",
                )
            argv += ["--json-schema", schema]
        return argv

    async def call(self, request: ChatRequest, *, timeout: float) -> ChatResponse:
        binary = self.require_binary()
        cwd = self.workdir()
        try:
            # the caller's output budget, as the CLI's own limit (always set: a parent's
            # CLAUDE_CODE_MAX_OUTPUT_TOKENS is scrubbed with the other CLAUDE_CODE_* keys)
            extra = (
                {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(request.max_tokens)}
                if request.max_tokens
                else None
            )
            result = await self.run(
                self.argv(binary, request, cwd),
                stdin=request.transcript(),
                cwd=cwd,
                timeout=timeout,
                extra_env=extra,
            )
        finally:
            remove_workdir(cwd)
        envelope = _envelope(result.stdout)
        if envelope is None:
            text = result.stderr or result.stdout
            if "unrecognized_model" in text:
                raise ProviderError(
                    400, "model_not_found", f"claude does not know model {request.model!r}"
                )
            raise ProviderError(
                502,
                "provider_error",
                f"claude exited {result.returncode} without a result: {scrub(text)}",
                LOGIN_REMEDY,
            )
        if request.max_tokens and _over_budget(envelope):
            # the CLI stops with an error instead of a cut answer: OpenAI's "length"
            return ChatResponse(
                content="",
                model=request.model,
                completion_tokens=request.max_tokens,
                finish_reason="length",
                extra={"duration_ms": envelope.get("duration_ms")},
            )
        if envelope.get("is_error") or result.returncode != 0:
            raise _error(envelope, request.model)
        usage = envelope.get("usage") or {}
        structured = envelope.get("structured_output")
        content = (
            json.dumps(structured, ensure_ascii=False)
            if structured is not None and request.structured
            else str(envelope.get("result") or "")
        )
        prompt = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        cost = envelope.get("total_cost_usd")
        return ChatResponse(
            content=content,
            model=request.model,
            prompt_tokens=prompt,
            completion_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(cost) if cost is not None else None,
            extra={
                "duration_ms": envelope.get("duration_ms"),
                "models": list((envelope.get("modelUsage") or {})),
            },
        )

    def login_check(self, binary: str) -> ProviderStatus:
        proc = self.run_sync([binary, "auth", "status"])
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return ProviderStatus(
                self.name,
                False,
                False,
                f"`claude auth status` gave no JSON: {scrub(proc.stderr)}",
                LOGIN_REMEDY,
                self.models(),
            )
        if not data.get("loggedIn"):
            return ProviderStatus(
                self.name, True, False, "not logged in", LOGIN_REMEDY, self.models()
            )
        # Only the auth kind and plan: e-mail, org and ids never leave the service.
        detail = f"logged in ({data.get('authMethod') or '?'}"
        if data.get("subscriptionType"):
            detail += f", {data['subscriptionType']}"
        return ProviderStatus(self.name, True, True, detail + ")", "", self.models())


def _envelope(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        # stream/JSONL style: take the last result line
        for line in reversed(text.splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "result":
                return data
        return None
    return data if isinstance(data, dict) else None


def _over_budget(envelope: dict[str, Any]) -> bool:
    """The answer hit CLAUDE_CODE_MAX_OUTPUT_TOKENS (checked against 2.1.311: is_error with
    "exceeded the N output token maximum", or stop_reason max_tokens)."""
    text = str(envelope.get("result") or "").lower()
    return envelope.get("stop_reason") == "max_tokens" or (
        bool(envelope.get("is_error")) and "output token maximum" in text
    )


def _error(envelope: dict[str, Any], model: str) -> ProviderError:
    message = str(envelope.get("result") or envelope.get("subtype") or "error")
    status = envelope.get("api_error_status")
    lowered = message.lower()
    if "not logged in" in lowered or "/login" in lowered or status == 401:
        return ProviderError(401, "not_logged_in", "claude CLI is not logged in", LOGIN_REMEDY)
    if status == 404 or "issue with the selected model" in lowered:
        return ProviderError(
            400, "model_not_found", f"claude cannot use model {model!r}: {scrub(message, 200)}"
        )
    if status == 429 or "usage limit" in lowered or "rate limit" in lowered:
        return ProviderError(
            429,
            "usage_limit",
            f"claude usage limit: {scrub(message, 300)}",
            "wait for the subscription limit to reset or switch provider",
        )
    return ProviderError(502, "provider_error", f"claude failed: {scrub(message)}")
