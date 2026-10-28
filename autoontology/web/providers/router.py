# SPDX-License-Identifier: MIT
"""``/v1/chat/completions`` + ``/v1/models`` on the service port.

Upstream sends the model name verbatim and uses ChatOpenAI only for names starting with
``openai/``, so the expanded triplets name:

* ``openai/qwen3:8b`` (an Ollama alias, forwarded unchanged) or ``openai/<model>`` for the
  ``openai`` provider (the prefix is stripped and the bearer key added): the raw
  passthroughs;
* ``openai/claude:<model>`` -> :class:`ClaudeCLIProvider` (the user's ``claude`` login);
* ``openai/chatgpt:<model>`` (or ``codex:<model>``) -> :class:`CodexCLIProvider` (the
  user's ``codex`` login; upstream gets ``chatgpt``: langchain sends names containing
  "codex" to /v1/responses).

The router serves the one chat provider selected at service start: a CLI prefix for a
provider that is not active is refused with 400 ``provider_not_active``. CLI answers to
``stream: true`` come back as one SSE chunk + ``[DONE]`` (no incremental streaming).
Errors are OpenAI-style ``{"error": {message, type, code, remedy?}}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from typing import Any

import anyio
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from autoontology.config import CHAT_MODEL_RE, CLI_MODEL_PREFIXES, CLI_PROVIDERS, Settings
from autoontology.web.providers.base import ProviderStatus
from autoontology.web.providers.ollama import OllamaProvider
from autoontology.web.providers.openai import OpenAIProvider
from autoontology.web.providers.openai_compat import (
    ChatRequest,
    ChatResponse,
    ProviderError,
    parse_request,
    to_openai,
    to_sse,
)
from autoontology.web.providers.passthrough import PassthroughProvider

logger = logging.getLogger("autoontology.providers")

#: model-name prefix -> CLI provider
ROUTED_PREFIXES: dict[str, str] = {"claude": "claude", "codex": "codex", "chatgpt": "codex"}
#: split_model's provider for names without a CLI prefix (the raw passthrough).
PASSTHROUGH = ""


def split_model(name: str) -> tuple[str, str]:
    """(provider, model) for a request's model name: ``claude``/``codex`` with the bare
    CLI model, or :data:`PASSTHROUGH` with the name unchanged."""
    bare = name.removeprefix("openai/")
    head, sep, rest = bare.partition(":")
    if sep and head in ROUTED_PREFIXES and rest:
        return ROUTED_PREFIXES[head], rest
    return PASSTHROUGH, name


def cli_provider(settings: Settings, name: str) -> Any:
    """The ``claude`` or ``codex`` provider, listing the configured model first."""
    if name == "claude":
        from autoontology.web.providers.claude_cli import MODELS, ClaudeCLIProvider

        return ClaudeCLIProvider(settings.home, [settings.claude_model, *MODELS])
    from autoontology.web.providers.codex_cli import MODELS, CodexCLIProvider

    return CodexCLIProvider(settings.home, [settings.codex_model, *MODELS])


class Registry:
    """The chat provider of one service process."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        providers: dict[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        if providers is not None:
            self.providers = dict(providers)
        elif settings.chat_provider == "openai":
            self.providers = {"openai": OpenAIProvider(settings, transport)}
        elif settings.chat_provider in CLI_PROVIDERS:
            self.providers = {
                settings.chat_provider: cli_provider(settings, settings.chat_provider)
            }
        else:
            self.providers = {"ollama": OllamaProvider(settings, transport)}

    @property
    def active(self) -> Any:
        name = self.settings.chat_provider
        return self.get(name if name in self.providers else next(iter(self.providers)))

    def get(self, name: str) -> Any:
        try:
            return self.providers[name]
        except KeyError:
            raise ProviderError(400, "unknown_provider", f"no provider {name!r}") from None

    def status(self, name: str | None = None, *, refresh: bool = False) -> ProviderStatus:
        provider = self.active if name is None else self.get(name)
        params = inspect.signature(provider.status).parameters.values()
        if any(p.name == "refresh" or p.kind is p.VAR_KEYWORD for p in params):
            return provider.status(refresh=refresh)
        return provider.status()  # providers without a status cache (Ollama, OpenAI)

    def resolve(self, model_name: str) -> tuple[Any, str]:
        """(provider, model) serving *model_name*; 400 ``provider_not_active`` when the name
        belongs to a provider this service does not run."""
        provider, model = split_model(model_name)
        active = self.active
        if provider == PASSTHROUGH:
            if isinstance(active, PassthroughProvider):
                return active, model
            raise ProviderError(
                400,
                "provider_not_active",
                f"model {model_name[:80]!r} is not served here: the active chat provider is "
                f"{active.name} (model names openai/{CLI_MODEL_PREFIXES.get(active.name, '?')}"
                ":<model>)",
            )
        if provider not in self.providers:
            raise ProviderError(
                400,
                "provider_not_active",
                f"model {model_name[:80]!r} needs the {provider} chat provider, but this "
                f"service runs {self.settings.chat_provider}",
                f"set AUTOONTOLOGY_CHAT_PROVIDER={provider} in .env and restart "
                "(`uv run autoontology down && uv run autoontology up`)",
            )
        return self.providers[provider], model

    async def complete(self, model_name: str, request: ChatRequest) -> ChatResponse:
        """One call by upstream model name (``doctor``), without HTTP."""
        provider, model = self.resolve(model_name)
        request.model = model
        return await provider.complete(request)


class ClientGone(Exception):
    """The HTTP client disconnected before the answer was ready."""


async def _disconnected(request: Request) -> None:
    """Return when the client hangs up. The body has been read, so the next ASGI message
    is ``http.disconnect``. (``Request.is_disconnected()`` cannot be polled here: behind
    the ``@app.middleware("http")`` wrapper it never reports a disconnect.)"""
    while True:
        message = await request.receive()
        if message.get("type") == "http.disconnect":
            return


async def until_disconnected(request: Request, coro: Any) -> Any:
    """Await *coro*, cancelling it (which kills the CLI) if the client disconnects: upstream
    retries after its own timeout, and an abandoned call would keep a slot busy."""
    task = asyncio.ensure_future(coro)
    watcher = asyncio.ensure_future(_disconnected(request))
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise ClientGone()
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher


def _error(exc: ProviderError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


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
        name = str(body.get("model") or "")
        try:
            provider, model = registry.resolve(name)
        except ProviderError as exc:
            return _error(exc)
        if isinstance(provider, PassthroughProvider):
            return await provider.forward(body, request)
        if not CHAT_MODEL_RE.fullmatch(model):
            return _error(
                ProviderError(
                    400,
                    "invalid_model",
                    f"model name {model[:80]!r} is not allowed "
                    "(letters, digits, . _ : / @ -; must not start with -)",
                )
            )
        try:
            # schema admission (meta-validation, cycle walk): off the event loop
            chat = await asyncio.to_thread(parse_request, body, model)
            resp = await until_disconnected(request, provider.complete(chat))
        except ClientGone:
            logger.info("%s call (%s) cancelled: the client disconnected", provider.name, model)
            return Response(status_code=499)
        except ProviderError as exc:
            logger.warning(
                "%s call (%s) failed: %s %s", provider.name, model, exc.status, exc.message
            )
            return _error(exc)
        logger.info(
            "%s %s: %.1fs, tokens %d/%d%s",
            provider.name,
            model,
            resp.duration_s,
            resp.prompt_tokens,
            resp.completion_tokens,
            f", list-price equivalent ${resp.cost_usd:.4f}" if resp.cost_usd is not None else "",
        )
        if chat.stream:
            return Response(content=to_sse(chat, resp, name), media_type="text/event-stream")
        return JSONResponse(to_openai(chat, resp, name))

    @router.get("/v1/models")
    async def models(request: Request) -> Response:
        provider = registry.active
        if isinstance(provider, OllamaProvider):
            return await provider.list_models_raw(request)
        if isinstance(provider, PassthroughProvider):
            data: list[dict[str, Any]] = [
                {"id": f"openai/{m}", "object": "model", "owned_by": provider.name}
                for m in provider.models()
            ]
            return JSONResponse({"object": "list", "data": data})
        data = []
        prefix = CLI_MODEL_PREFIXES.get(provider.name)
        # status() may run the CLI (up to its timeout): never on the event loop
        status = await anyio.to_thread.run_sync(registry.status)
        if prefix and status.ok:
            data = [
                {"id": f"openai/{prefix}:{m}", "object": "model", "owned_by": provider.name}
                for m in provider.models()
            ]
        return JSONResponse({"object": "list", "data": data})

    return router
