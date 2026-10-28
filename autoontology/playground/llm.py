"""The active chat provider for playground calls: the service's own ``/v1`` router
(Ollama or OpenAI-compatible passthrough), called over HTTP with the model name upstream
would send (``Settings.upstream_chat_model``), so the playground goes through exactly the
path, limits and deadlines the engine uses.

Streaming: token deltas are relayed as they arrive. A stream that ends without
``data: [DONE]`` raises ``LLMError(502, "stream_truncated")``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from autoontology.config import CHAT_PROVIDERS, Settings
from autoontology.web.providers.base import parse_json_answer, schema_problem
from autoontology.web.providers.ollama import proxy_timeout_s
from autoontology.web.providers.openai_compat import ProviderError

ROUTED = CHAT_PROVIDERS


class LLMError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class PlaygroundLLM(Protocol):
    provider: str
    model: str

    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...

    async def structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], name: str
    ) -> Any: ...


def _error_message(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = str(err.get("message") or err)
        return f"{msg} ({err['remedy']})" if err.get("remedy") else msg
    return str(data)[:300]


class RouterLLM:
    def __init__(
        self,
        base_url: str,
        model_name: str,
        *,
        provider: str,
        model: str,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.provider = provider
        self.model = model
        # the router enforces the provider deadline; leave slack
        self.timeout = timeout or proxy_timeout_s() + 30
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=5.0), transport=self.transport
        )

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        body = {"model": self.model_name, "messages": messages, "stream": True, "temperature": 0}
        async with self._client() as http:
            try:
                async with http.stream(
                    "POST", f"{self.base_url}/v1/chat/completions", json=body
                ) as resp:
                    if resp.status_code >= 400:
                        raise LLMError(
                            resp.status_code, "provider_error", _error_message(await resp.aread())
                        )
                    if "text/event-stream" not in resp.headers.get("content-type", ""):
                        data = json.loads(await resp.aread())
                        yield data["choices"][0]["message"].get("content") or ""
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return  # the only normal end of an SSE answer
                        try:
                            chunk = json.loads(payload)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(502, "provider_error", _error_message(payload.encode()))
                        for choice in chunk.get("choices") or []:
                            text = (choice.get("delta") or {}).get("content") or (
                                choice.get("message") or {}
                            ).get("content")
                            if text:
                                yield text
                    # EOF without [DONE]: a cut connection or proxy, never a full answer
                    raise LLMError(
                        502,
                        "stream_truncated",
                        f"the {self.provider} answer stream ended without [DONE] "
                        "(the partial answer is stored as incomplete)",
                    )
            except httpx.TimeoutException as exc:
                raise LLMError(
                    504,
                    "provider_timeout",
                    f"{self.provider} did not answer within {self.timeout:.0f}s",
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(
                    502, "provider_unreachable", f"chat router unreachable: {type(exc).__name__}"
                ) from exc

    async def structured(
        self, messages: list[dict[str, str]], schema: dict[str, Any], name: str
    ) -> Any:
        body = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        async with self._client() as http:
            try:
                resp = await http.post(f"{self.base_url}/v1/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                raise LLMError(
                    504,
                    "provider_timeout",
                    f"{self.provider} did not answer within {self.timeout:.0f}s",
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(
                    502, "provider_unreachable", f"chat router unreachable: {type(exc).__name__}"
                ) from exc
        if resp.status_code >= 400:
            raise LLMError(resp.status_code, "provider_error", _error_message(resp.content))
        content = resp.json()["choices"][0]["message"].get("content") or ""
        try:
            value = parse_json_answer(content)
        except ValueError as exc:
            raise LLMError(422, "invalid_answer", f"answer is not JSON: {content[:200]}") from exc
        try:  # CPU-bound validation: off the event loop
            problem = await asyncio.to_thread(schema_problem, value, schema)
        except ProviderError as exc:
            raise LLMError(exc.status, exc.type, exc.message) from exc
        if problem:
            raise LLMError(422, "invalid_answer", f"answer does not match {name}: {problem}")
        return value


def llm_for(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> RouterLLM:
    """The selected chat provider through the service's router."""
    if settings.chat_provider not in ROUTED:
        raise LLMError(
            400,
            "provider_unsupported",
            f"the playground uses the service's chat router (ollama, openai);"
            f" chat provider {settings.chat_provider!r} is not routed there",
        )
    return RouterLLM(
        settings.service_url,
        settings.upstream_chat_model,
        provider=settings.chat_provider,
        model=settings.effective_chat_model,
        transport=transport,
    )
