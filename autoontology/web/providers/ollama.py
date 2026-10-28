# SPDX-License-Identifier: MIT
"""Ollama: raw OpenAI-compatible passthrough (the default chat provider).

Ollama 0.40.11 enforces a ``response_format`` json_schema on thinking-capable models only
when the request sets ``reasoning_effort`` or ``think`` explicitly; otherwise the schema
grammar waits for the end of thinking and qwen3 answers in ```json fences, which
upstream's ChatOpenAI parsing rejects. Upstream (LangChain, vendored) cannot send that
field, so the passthrough adds ``reasoning_effort: "none"`` when neither field is present.
The body is otherwise forwarded verbatim (model name included: the Ollama alias is
literally ``openai/qwen3:8b``), streaming responses are relayed as they arrive.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autoontology.config import Settings
from autoontology.web.providers.base import ProviderStatus
from autoontology.web.providers.passthrough import PassthroughProvider

TIMEOUT_MARGIN_S = 30.0
DEFAULT_INVOKE_TIMEOUT_S = 120.0  # upstream's own default for LLM_INVOKE_TIMEOUT_S


def prepare_chat_payload(body: dict[str, Any], *, no_think: bool) -> dict[str, Any]:
    """The body forwarded to Ollama: a copy, with ``reasoning_effort: "none"`` added when
    *no_think* and the caller set neither ``reasoning_effort`` nor ``think``."""
    out = dict(body)
    if no_think and "reasoning_effort" not in out and "think" not in out:
        out["reasoning_effort"] = "none"
    return out


def proxy_timeout_s() -> float:
    raw = os.environ.get("LLM_INVOKE_TIMEOUT_S", "").strip()
    try:
        base = float(raw) if raw else DEFAULT_INVOKE_TIMEOUT_S
    except ValueError:
        base = DEFAULT_INVOKE_TIMEOUT_S
    return base + TIMEOUT_MARGIN_S


class OllamaProvider(PassthroughProvider):
    name = "ollama"
    label = "Ollama"
    forward_headers = ("authorization", "content-type")

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        super().__init__(f"{settings.ollama_root_url}/v1", transport)
        self.settings = settings
        self.no_think = settings.ollama_no_think

    def timeout_s(self) -> float:
        return proxy_timeout_s()

    def payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return prepare_chat_payload(body, no_think=self.no_think)

    def models(self) -> list[str]:
        from autoontology.engine.ollama import list_models

        try:
            return sorted(list_models(self.settings.ollama_endpoint))
        except (httpx.HTTPError, ValueError):
            return []

    def status(self, *, refresh: bool = False) -> ProviderStatus:
        from autoontology.engine.ollama import has_model, list_models

        try:
            names = list_models(self.settings.ollama_endpoint)
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderStatus(
                self.name,
                False,
                False,
                f"{self.settings.ollama_root_url}: {type(exc).__name__}",
                "start Ollama (`ollama serve`)",
            )
        wanted = [self.settings.ollama_chat_model, self.settings.ollama_embed_model]
        missing = [m for m in wanted if not has_model(names, m)]
        detail = (
            f"models missing: {', '.join(missing)}"
            if missing
            else f"{self.settings.ollama_root_url} has {', '.join(wanted)}"
        )
        # "logged in" = no login needed; a missing model is reported in detail/remedy.
        return ProviderStatus(
            self.name,
            True,
            True,
            detail,
            "uv run autoontology setup" if missing else "",
            sorted(names),
        )
