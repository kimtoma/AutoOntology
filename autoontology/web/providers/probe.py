# SPDX-License-Identifier: MIT
"""Provider test: one structured ``{"answer": "pong"}`` call through the /v1 router.

The call is the raw OpenAI request upstream would send (``response_format`` json_schema,
model ``Settings.upstream_chat_model``), POSTed to ``/v1/chat/completions``: the running
service when it serves the provider under test, else a temporary in-process server on a
free 127.0.0.1 port (same app, same router). ``POST /api/providers/test`` runs it against
the service's own app in-process. The latest result per provider is kept in
``<home>/run/providers-last-test.json`` (no secrets: error strings are masked).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from autoontology.config import CHAT_PROVIDERS, Settings

PING_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
PING_PROMPT = "Return answer='pong'."
LAST_TEST_FILE = "providers-last-test.json"
DEFAULT_TEST_TIMEOUT_S = 330.0


def _mask(text: str) -> str:
    from autoontology.web.providers.base import scrub

    return scrub(text, 300)


def ping_body(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": PING_PROMPT}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "Ping", "schema": PING_SCHEMA, "strict": True},
        },
    }


async def router_call(client: httpx.AsyncClient, model: str, timeout: float) -> dict[str, Any]:
    """POST the pong request; never raises for a failed call (the result says why)."""
    start = time.monotonic()
    try:
        resp = await client.post("/v1/chat/completions", json=ping_body(model), timeout=timeout)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "seconds": round(time.monotonic() - start, 2),
            "error": f"chat router unreachable: {type(exc).__name__}",
        }
    elapsed = round(time.monotonic() - start, 2)
    try:
        data = resp.json()
    except ValueError:
        data = {"error": {"message": resp.text[:300]}}
    if resp.status_code != 200:
        err = data.get("error") if isinstance(data, dict) else None
        err = err if isinstance(err, dict) else {"message": str(data)}
        return {
            "ok": False,
            "seconds": elapsed,
            "status": resp.status_code,
            "error": _mask(str(err.get("message") or data)),
            "remedy": str(err.get("remedy") or ""),
        }
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    try:
        answer = json.loads(content).get("answer")
    except (ValueError, AttributeError):
        answer = None
    return {
        "ok": answer == "pong",
        "seconds": elapsed,
        "status": 200,
        "answer": answer if isinstance(answer, str) else _mask(str(content)[:120]),
        "usage": data.get("usage"),
    }


async def run_test_async(
    settings: Settings,
    *,
    client: httpx.AsyncClient,
    via: str,
    timeout: float = DEFAULT_TEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Pong through *client* (a router serving ``settings.chat_provider``), saved."""
    model = settings.upstream_chat_model
    result = await router_call(client, model, timeout)
    out = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": settings.chat_provider,
        "model": model,
        "via": via,
        **result,
    }
    save_result(settings, out)
    return out


def service_up(settings: Settings) -> bool:
    try:
        return httpx.get(f"{settings.service_url}/health/live", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def service_provider(settings: Settings) -> str | None:
    """The chat provider the running service serves (None if down/unknown)."""
    try:
        resp = httpx.get(f"{settings.service_url}/api/status", timeout=30.0)
        return str(resp.json()["providers"]["chat_provider"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


@contextlib.contextmanager
def temporary_service(settings: Settings) -> Iterator[str]:
    """The service app on a free 127.0.0.1 port in a thread, for as long as the block."""
    import uvicorn

    from autoontology.web.app import create_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True, name="autoontology-probe-server")
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("temporary service did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(15)


def run_test(
    settings: Settings,
    provider: str | None = None,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TEST_TIMEOUT_S,
) -> dict[str, Any]:
    """Test *provider* (default: the selected chat provider) with one pong call. Uses the
    running service when it serves that provider, else a temporary in-process one. Never
    raises for a failed call; the result says what failed."""
    name = provider or settings.chat_provider
    if name not in CHAT_PROVIDERS:
        raise ValueError(f"provider must be one of {CHAT_PROVIDERS}, got {name!r}")
    if name != settings.chat_provider:
        settings = dataclasses.replace(settings, chat_provider=name)
    if base_url is None and service_up(settings) and service_provider(settings) == name:
        base_url = settings.service_url
    via = "service" if base_url else "in-process"
    ctx = contextlib.nullcontext(base_url) if base_url else temporary_service(settings)
    with ctx as url:

        async def go() -> dict[str, Any]:
            async with httpx.AsyncClient(base_url=url) as client:
                return await run_test_async(settings, client=client, via=via, timeout=timeout)

        return asyncio.run(go())


def _path(settings: Settings) -> Path:
    return Path(settings.run_dir) / LAST_TEST_FILE


def last_tests(settings: Settings) -> dict[str, Any]:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_test(settings: Settings, provider: str | None = None) -> dict[str, Any] | None:
    entry = last_tests(settings).get(provider or settings.chat_provider)
    return entry if isinstance(entry, dict) else None


def save_result(settings: Settings, result: dict[str, Any]) -> None:
    previous = last_tests(settings)
    previous[result["provider"]] = {
        k: result.get(k)
        for k in ("at", "ok", "model", "seconds", "status", "error", "remedy")
        if result.get(k) not in (None, "")
    }
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(previous, indent=2), encoding="utf-8")
    os.replace(tmp, path)
