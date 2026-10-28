# SPDX-License-Identifier: MIT
"""Generic OpenAI-compatible chat provider (``AUTOONTOLOGY_CHAT_PROVIDER=openai``).

Upstream sends ``openai/<model>`` (the prefix selects its ChatOpenAI client); the
passthrough strips that prefix, replaces the caller's ``Authorization`` with
``Bearer AUTOONTOLOGY_OPENAI_API_KEY`` and forwards the JSON body (``response_format``
included) to ``AUTOONTOLOGY_OPENAI_BASE_URL/chat/completions``. Works with OpenAI, NVIDIA
build.nvidia.com (``https://integrate.api.nvidia.com/v1``), vLLM, LM Studio and others.
The API key never appears in responses, logs or errors.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Request

from autoontology.config import Settings
from autoontology.web.providers.base import ProviderStatus
from autoontology.web.providers.passthrough import PassthroughProvider


def strip_router_prefix(model: str) -> str:
    return model.removeprefix("openai/")


class OpenAIProvider(PassthroughProvider):
    name = "openai"
    label = "the OpenAI-compatible endpoint"

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        super().__init__(settings.openai_base_url, transport)
        self.settings = settings
        self._key = settings.openai_api_key

    def timeout_s(self) -> float:
        return self.settings.openai_timeout_s

    def payload(self, body: dict[str, Any]) -> dict[str, Any]:
        out = dict(body)
        model = str(out.get("model") or "")
        # Upstream always sends one router prefix; a body without a model gets the
        # configured name as-is (it was never prefixed, so nothing is stripped).
        out["model"] = strip_router_prefix(model) if model else self.settings.openai_chat_model
        return out

    def headers(self, request: Request | None) -> dict[str, str]:
        out = super().headers(request)
        if self._key:
            out["authorization"] = f"Bearer {self._key}"
        return out

    def secrets(self) -> list[str]:
        return [self._key] if self._key else []

    def models(self) -> list[str]:
        return [self.settings.openai_chat_model] if self.settings.openai_chat_model else []

    def status(self, *, refresh: bool = False) -> ProviderStatus:
        if not self.base:
            return ProviderStatus(
                self.name,
                False,
                False,
                "AUTOONTOLOGY_OPENAI_BASE_URL is not set",
                "set AUTOONTOLOGY_OPENAI_BASE_URL in .env",
            )
        detail = f"{self.base} model {self.settings.openai_chat_model or '(unset)'}"
        return ProviderStatus(
            self.name,
            True,
            bool(self._key),
            detail,
            "" if self._key else "set AUTOONTOLOGY_OPENAI_API_KEY in .env",
            self.models(),
        )
