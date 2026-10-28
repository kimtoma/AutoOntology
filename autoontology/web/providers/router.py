"""``/v1/chat/completions`` + ``/v1/models`` on the service port.

Upstream sends the model name verbatim and uses ChatOpenAI only for names starting with
``openai/``, so the expanded triplets name ``openai/qwen3:8b`` (an Ollama alias, forwarded
unchanged) or ``openai/<model>`` for the ``openai`` provider (the prefix is stripped and
the bearer key added). The router forwards every request to the chat provider selected at
service start. Errors are OpenAI-style ``{"error": {message, type, code}}``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from autoontology.config import Settings
from autoontology.web.providers.ollama import OllamaProvider
from autoontology.web.providers.openai import OpenAIProvider
from autoontology.web.providers.openai_compat import ProviderError
from autoontology.web.providers.passthrough import PassthroughProvider

logger = logging.getLogger("autoontology.providers")


class Registry:
    """The chat provider of one service process."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        providers: dict[str, PassthroughProvider] | None = None,
    ) -> None:
        self.settings = settings
        if providers is not None:
            self.providers = dict(providers)
        elif settings.chat_provider == "openai":
            self.providers = {"openai": OpenAIProvider(settings, transport)}
        else:
            self.providers = {"ollama": OllamaProvider(settings, transport)}

    @property
    def active(self) -> PassthroughProvider:
        name = self.settings.chat_provider
        return self.get(name if name in self.providers else next(iter(self.providers)))

    def get(self, name: str) -> PassthroughProvider:
        try:
            return self.providers[name]
        except KeyError:
            raise ProviderError(400, "unknown_provider", f"no provider {name!r}") from None


def build_router(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    registry: Registry | None = None,
) -> APIRouter:
    router = APIRouter()
    registry = registry or Registry(settings, transport)
    router.registry = registry  # type: ignore[attr-defined]

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "request body is not valid JSON",
                        "type": "invalid_request",
                    }
                },
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "request body must be a JSON object",
                        "type": "invalid_request",
                    }
                },
            )
        return await registry.active.forward(body, request)

    @router.get("/v1/models")
    async def models(request: Request) -> Response:
        provider = registry.active
        if isinstance(provider, OllamaProvider):
            return await provider.list_models_raw(request)
        data: list[dict[str, Any]] = [
            {"id": f"openai/{m}", "object": "model", "owned_by": provider.name}
            for m in provider.models()
        ]
        return JSONResponse({"object": "list", "data": data})

    return router
