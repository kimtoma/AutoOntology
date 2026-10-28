"""Raw OpenAI-compatible passthrough shared by the ``ollama`` and ``openai`` providers.

The request body is forwarded as JSON (``response_format`` and tools included), streaming
responses are relayed as they arrive, and transport failures become OpenAI-style error
bodies (504 on timeout, 502 otherwise). Subclasses choose the URL, headers and payload.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from autoontology.web.providers.base import ProviderStatus
from autoontology.web.providers.openai_compat import ChatRequest, ChatResponse, ProviderError

logger = logging.getLogger("autoontology.llm_proxy")

#: Upstream error bodies longer than this are cut (and re-wrapped as an OpenAI error).
MAX_ERROR_CHARS = 2000
#: Credential-looking strings some endpoints echo back on 401 (``sk-ab***``, ``nvapi-…``,
#: ``Bearer …``); they are masked before an upstream error reaches the caller.
_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[^\s\"',]+|\b(?:sk|nvapi)-[A-Za-z0-9_\-*.]{2,}")
REDACTED = "[redacted]"


def _passthrough(resp: httpx.Response, content: bytes) -> Response:
    return Response(
        content=content, status_code=resp.status_code, media_type=resp.headers.get("content-type")
    )


class PassthroughProvider:
    """Base class: ``forward`` a chat body to ``<base>/chat/completions``."""

    name = "passthrough"
    #: Human label used in error messages ("Ollama", "the OpenAI-compatible endpoint").
    label = "the model endpoint"
    #: Request headers relayed from the caller.
    forward_headers: tuple[str, ...] = ("content-type",)

    def __init__(self, base: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base.rstrip("/")
        self.transport = transport

    # --- hooks -------------------------------------------------------------------
    def timeout_s(self) -> float:
        raise NotImplementedError

    def payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return dict(body)

    def headers(self, request: Request | None) -> dict[str, str]:
        if request is None:
            return {}
        return {k: v for k, v in request.headers.items() if k.lower() in self.forward_headers}

    def status(self) -> ProviderStatus:
        raise NotImplementedError

    def secrets(self) -> list[str]:
        """Credentials this provider sends (masked, with their prefixes, in error bodies)."""
        return []

    def redact(self, text: str) -> str:
        for secret in self.secrets():
            if len(secret) >= 8:  # also partial echoes such as "nvapi-abcd1234***"
                prefixes = (re.escape(secret[:n]) for n in range(len(secret), 7, -1))
                text = re.sub("|".join(prefixes), REDACTED, text)
            elif secret:
                text = text.replace(secret, REDACTED)
        return _TOKEN_RE.sub(REDACTED, text)

    def error_response(self, resp: httpx.Response, content: bytes) -> Response:
        """An upstream error relayed with credentials masked and the body bounded."""
        text = self.redact(content.decode("utf-8", errors="replace"))
        media_type = resp.headers.get("content-type")
        if len(text) > MAX_ERROR_CHARS:
            text = json.dumps(
                {"error": {"message": text[:MAX_ERROR_CHARS] + " ...", "type": "upstream_error"}}
            )
            media_type = "application/json"
        return Response(content=text.encode(), status_code=resp.status_code, media_type=media_type)

    def models(self) -> list[str]:
        return []

    # --- transport ---------------------------------------------------------------
    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s())

    def upstream_error(self, url: str, exc: httpx.HTTPError) -> JSONResponse:
        """504 for timeouts, 502 for everything else (connection refused, broken stream)."""
        if isinstance(exc, httpx.TimeoutException):
            return JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "message": f"{self.label} did not answer within {self.timeout_s():.0f}s at "
                        f"{url} ({type(exc).__name__}); raise the timeout or check the model",
                        "type": "upstream_timeout",
                    }
                },
            )
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"{self.label} unreachable at {url}: {type(exc).__name__}",
                    "type": "upstream_unreachable",
                }
            },
        )

    async def forward(self, body: dict[str, Any], request: Request | None) -> Response:
        """Relay *body* to ``<base>/chat/completions`` (streaming when asked)."""
        payload = self.payload(body)
        url = f"{self.base}/chat/completions"
        http = self.client()
        try:
            upstream = await http.send(
                http.build_request("POST", url, json=payload, headers=self.headers(request)),
                stream=True,
            )
        except httpx.HTTPError as exc:
            await http.aclose()
            return self.upstream_error(url, exc)

        async def close() -> None:
            await upstream.aclose()
            await http.aclose()

        if not payload.get("stream") or upstream.status_code >= 400:
            try:
                content = await upstream.aread()
            except httpx.HTTPError as exc:
                return self.upstream_error(url, exc)
            finally:
                await close()
            if upstream.status_code >= 400:
                return self.error_response(upstream, content)
            return _passthrough(upstream, content)

        async def relay():
            # finally (not only a BackgroundTask) so the upstream response and client are
            # closed on mid-stream errors and when the caller disconnects (the generator
            # is closed/cancelled then, and background tasks do not run).
            try:
                # Decoded bytes: Content-Encoding is not forwarded, so SSE goes out as-is.
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            except httpx.HTTPError as exc:
                logger.warning("LLM proxy stream from %s broke: %s", url, type(exc).__name__)
            finally:
                await close()

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """One non-streaming call (used by ``doctor``), through :meth:`forward`."""
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.mode in ("json_schema", "tool") and request.schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
            }
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        start = time.monotonic()
        resp = await self.forward(body, None)
        data = json.loads(bytes(resp.body or b"{}"))
        if resp.status_code >= 400:
            err = data.get("error") or {}
            if not isinstance(err, dict):
                err = {"message": err}
            raise ProviderError(
                resp.status_code,
                str(err.get("type") or "provider_error"),
                str(err.get("message") or data)[:300],
            )
        usage = data.get("usage") or {}
        return ChatResponse(
            content=str(
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ),
            model=request.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            duration_s=time.monotonic() - start,
        )

    async def list_models_raw(self, request: Request | None = None) -> Response:
        url = f"{self.base}/models"
        async with self.client() as http:
            try:
                resp = await http.get(url, headers=self.headers(request))
            except httpx.HTTPError as exc:
                return self.upstream_error(url, exc)
            if resp.status_code >= 400:
                return self.error_response(resp, resp.content)
            return _passthrough(resp, resp.content)
