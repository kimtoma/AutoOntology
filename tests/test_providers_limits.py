# SPDX-License-Identifier: MIT
"""CLI provider deadlines, cancellation, input limits and housekeeping."""

import asyncio
import json
import os
import tempfile
import threading
import time

import pytest

from autoontology.config import Settings, expand_model_profile
from autoontology.web import app as web
from autoontology.web.providers import base
from autoontology.web.providers.openai_compat import ProviderError, parse_request
from autoontology.web.providers.router import ClientGone, until_disconnected
from tests.test_providers_cli import FMT, _chat, _client, fakes  # noqa: F401 - fixture


def test_one_deadline_covers_the_wait_for_a_slot(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("AUTOONTOLOGY_PROVIDER_TIMEOUT_S", "1.5")
    monkeypatch.setenv("AUTOONTOLOGY_PROVIDER_MAX_INFLIGHT", "1")
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider

    provider = ClaudeCLIProvider(Settings.from_env().home, ["sonnet"])
    req = parse_request({"messages": [{"role": "user", "content": "x"}]}, "sonnet")

    async def both():
        return await asyncio.gather(
            provider.complete(req), provider.complete(req), return_exceptions=True
        )

    start = time.monotonic()
    first, second = asyncio.run(both())
    assert time.monotonic() - start < 4  # not 2 x the deadline
    assert isinstance(first, ProviderError) and first.status == 504
    assert isinstance(second, ProviderError) and second.status == 504
    assert "no free claude slot" in second.message


def test_reask_needs_enough_time_left(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"wrong": 1}, {"answer": "pong"}]))
    monkeypatch.setattr(base, "MIN_REASK_S", 10_000)
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 422 and "too little for a re-ask" in res.json()["error"]["message"]
    assert len(fakes()) == 1


def test_llm_invoke_timeout_sits_above_the_router_deadline(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "claude")
    assert expand_model_profile(Settings.from_env())["LLM_INVOKE_TIMEOUT_S"] == "270"
    monkeypatch.setenv("AUTOONTOLOGY_PROVIDER_TIMEOUT_S", "60")
    assert expand_model_profile(Settings.from_env())["LLM_INVOKE_TIMEOUT_S"] == "150"
    assert base.DEFAULT_TIMEOUT_S == 120


def test_cancel_kills_and_reaps_the_cli(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "sleep")
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider

    provider = ClaudeCLIProvider(Settings.from_env().home, ["sonnet"])
    req = parse_request({"messages": [{"role": "user", "content": "x"}]}, "sonnet")

    async def go():
        task = asyncio.ensure_future(provider.complete(req))
        while not fakes():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # the slot is free again
        assert provider._semaphore()._value == base.provider_max_inflight()

    asyncio.run(go())
    pid = fakes()[0]["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_until_disconnected_cancels_the_call():
    cancelled = threading.Event()

    class Req:
        def __init__(self):
            self.polls = 0

        async def is_disconnected(self):
            self.polls += 1
            return self.polls >= 2

    async def slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def go():
        with pytest.raises(ClientGone):
            await until_disconnected(Req(), slow())

    import autoontology.web.providers.router as router

    old = router.DISCONNECT_POLL_S
    router.DISCONNECT_POLL_S = 0.01
    try:
        asyncio.run(go())
    finally:
        router.DISCONNECT_POLL_S = old
    assert cancelled.is_set()


@pytest.mark.parametrize("model", ["-p", "--json-schema", "a" * 200, "x y"])
def test_bad_model_names_are_400(fakes, model):  # noqa: F811
    res = _chat(_client(), f"openai/claude:{model}")
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_model"
    assert fakes() == []


def test_oversized_schema_is_413_not_503(fakes, monkeypatch):  # noqa: F811
    from autoontology.web.providers import claude_cli

    monkeypatch.setattr(claude_cli, "MAX_SCHEMA_ARG_BYTES", 50)
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 413 and res.json()["error"]["type"] == "request_too_large"


def test_models_route_checks_status_off_the_event_loop(fakes, monkeypatch):  # noqa: F811
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider

    threads = []
    real = ClaudeCLIProvider.status

    def spy(self, *a, **kw):
        threads.append(threading.current_thread().name)
        return real(self, *a, **kw)

    monkeypatch.setattr(ClaudeCLIProvider, "status", spy)
    client = _client("claude")
    client.get("/v1/models")
    assert threads and all("AnyIO worker" in t for t in threads), threads


def test_service_start_sweeps_stale_workdirs(isolated_env):
    stale = tempfile.mkdtemp(prefix=f"{base.WORKDIR_PREFIX}claude-")
    fresh = tempfile.mkdtemp(prefix=f"{base.WORKDIR_PREFIX}claude-")
    old = time.time() - base.STALE_WORKDIR_S - 60
    os.utime(stale, (old, old))
    settings = Settings.from_env()
    legacy = settings.home / "run" / "providers" / "claude-abc"
    legacy.mkdir(parents=True)
    web.create_app(settings)
    assert not os.path.exists(stale) and not legacy.exists()
    assert os.path.exists(fresh)  # may belong to a call in flight
    os.rmdir(fresh)


def test_cli_profile_points_every_chat_role_at_the_router(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "codex")
    settings = Settings.from_env()
    env = expand_model_profile(settings)
    for prefix in ("DEFAULT_MODELS", "REASONING", "NON_REASONING", "RERANK"):
        assert env[f"{prefix}_ENDPOINT"] == settings.llm_proxy_endpoint
        assert env[f"{prefix}_MODEL"] == "openai/chatgpt:gpt-6-astra"
    assert env["EMBED_ENDPOINT"] == settings.ollama_endpoint and env["EMBED_MODEL"] == "bge-m3"
    assert (env["LLM_MAX_INFLIGHT"], env["SEMANTIC_LLM_MAX_INFLIGHT"]) == ("2", "2")
    # the user's own limits win
    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_S", "999")
    assert "LLM_INVOKE_TIMEOUT_S" not in expand_model_profile(Settings.from_env())
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "claude")
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_MODEL", "claude-haiku-5-5")
    settings = Settings.from_env()
    assert settings.upstream_chat_model == "openai/claude:claude-haiku-5-5"
    assert settings.effective_chat_model == "claude-haiku-5-5"
