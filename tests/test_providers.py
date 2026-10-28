"""The service's /v1 chat router: Ollama and generic OpenAI-compatible passthroughs, the
OpenAI wire-format helpers and the schema checks."""

import contextlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.web import app as web
from autoontology.web.providers.openai_compat import (
    ChatResponse,
    ProviderError,
    parse_request,
    to_openai,
    to_sse,
)

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
FMT = {"type": "json_schema", "json_schema": {"name": "Ping", "schema": SCHEMA, "strict": True}}
SECRET = "sk-test-secret-key-123456"


def _answer(content: str = '{"answer": "pong"}') -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


class Upstream:
    """httpx.MockTransport recording every request the router forwards."""

    def __init__(self, response=None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or (lambda request: httpx.Response(200, json=_answer()))
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response(request)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.content]


def _client(upstream: Upstream) -> TestClient:
    return TestClient(web.create_app(Settings.from_env(), llm_transport=upstream.transport))


def _chat(client, model, **extra):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "say pong"},
        ],
        **extra,
    }
    return client.post("/v1/chat/completions", json=body)


@pytest.fixture
def openai_env(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "meta/llama-4-scout")
    return isolated_env


# --- wire format ---------------------------------------------------------------------


def test_parse_request_modes_and_content_parts():
    req = parse_request(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                }
            ],
            "response_format": FMT,
        },
        "m",
    )
    assert (req.mode, req.schema, req.schema_name) == ("json_schema", SCHEMA, "Ping")
    assert req.transcript() == "a\nb"
    tool = parse_request(
        {
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "Ping", "parameters": SCHEMA}}],
        },
        "m",
    )
    assert (tool.mode, tool.schema_name) == ("tool", "Ping")
    obj = parse_request(
        {
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {"type": "json_object"},
        },
        "m",
    )
    assert obj.mode == "json_object"
    with pytest.raises(ProviderError) as exc:
        parse_request({"messages": []}, "m")
    assert exc.value.status == 400


def test_transcript_labels_multi_turn_and_system_is_separate():
    req = parse_request(
        {
            "messages": [
                {"role": "system", "content": "S1"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "system", "content": "S2"},
                {"role": "user", "content": "u2"},
            ]
        },
        "m",
    )
    assert req.system_text() == "S1\n\nS2"
    assert req.transcript() == "User:\nu1\n\nAssistant:\na1\n\nUser:\nu2"


def test_to_openai_tool_calls_and_usage():
    req = parse_request(
        {
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "Ping", "parameters": SCHEMA}}],
        },
        "m",
    )
    out = to_openai(req, ChatResponse('{"answer":"pong"}', "m", 5, 2, 0.01), "openai/m")
    msg = out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["function"] == {"name": "Ping", "arguments": '{"answer":"pong"}'}
    assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    assert out["autoontology_cost_usd"] == 0.01 and out["model"] == "openai/m"


def test_sse_shim_is_one_chunk_then_done():
    req = parse_request({"messages": [{"role": "user", "content": "x"}], "stream": True}, "m")
    raw = to_sse(req, ChatResponse("hello", "m"), "openai/m").decode()
    events = [e for e in raw.split("\n\n") if e]
    assert len(events) == 2 and events[1] == "data: [DONE]"
    chunk = json.loads(events[0].removeprefix("data: "))
    assert chunk["choices"][0]["delta"]["content"] == "hello"


# --- ollama passthrough ------------------------------------------------------------------


def test_ollama_passthrough_adds_reasoning_effort_and_keeps_the_alias(isolated_env):
    upstream = Upstream()
    res = _chat(_client(upstream), "openai/qwen3:8b", response_format=FMT)
    assert res.status_code == 200, res.text
    (request,) = upstream.requests
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    (body,) = upstream.bodies
    assert body["model"] == "openai/qwen3:8b"  # the alias name is literal
    assert body["reasoning_effort"] == "none" and body["response_format"] == FMT


def test_ollama_explicit_reasoning_effort_is_kept(isolated_env):
    upstream = Upstream()
    _chat(_client(upstream), "openai/qwen3:8b", reasoning_effort="low")
    assert upstream.bodies[0]["reasoning_effort"] == "low"


def test_ollama_unreachable_is_502(isolated_env):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    res = _chat(_client(Upstream(refuse)), "openai/qwen3:8b")
    assert res.status_code == 502
    assert res.json()["error"]["type"] == "upstream_unreachable"
    assert "Ollama" in res.json()["error"]["message"]


def test_ollama_streaming_is_relayed(isolated_env):
    sse = b'data: {"choices":[{"delta":{"content":"po"}}]}\n\ndata: [DONE]\n\n'
    upstream = Upstream(
        lambda r: httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )
    res = _chat(_client(upstream), "openai/qwen3:8b", stream=True)
    assert res.status_code == 200 and res.content == sse


def test_bad_json_body_is_400(isolated_env):
    client = _client(Upstream())
    res = client.post(
        "/v1/chat/completions", content=b"{nope", headers={"content-type": "application/json"}
    )
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_request"
    assert client.post("/v1/chat/completions", json=[1]).status_code == 400


# --- generic OpenAI-compatible passthrough -------------------------------------------------


def test_openai_passthrough_strips_prefix_and_adds_bearer_key(openai_env):
    upstream = Upstream()
    client = _client(upstream)
    res = _chat(client, "openai/meta/llama-4-scout", response_format=FMT)
    assert res.status_code == 200, res.text
    (request,) = upstream.requests
    assert str(request.url) == "https://llm.example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = upstream.bodies[0]
    assert body["model"] == "meta/llama-4-scout"
    assert body["response_format"] == FMT
    assert "reasoning_effort" not in body  # only the Ollama passthrough adds it


def test_openai_model_id_with_its_own_openai_prefix_survives_routing(openai_env, monkeypatch):
    """NVIDIA/OpenRouter ids such as ``openai/gpt-oss-120b`` must reach the endpoint intact:
    upstream sends one extra router prefix and the router strips exactly one."""
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "openai/gpt-oss-120b")
    settings = Settings.from_env()
    assert settings.upstream_chat_model == "openai/openai/gpt-oss-120b"
    upstream = Upstream()
    res = _chat(_client(upstream), settings.upstream_chat_model)
    assert res.status_code == 200, res.text
    assert upstream.bodies[0]["model"] == "openai/gpt-oss-120b"
    # a body without a model falls back to the configured name, unstripped
    upstream2 = Upstream()
    _client(upstream2).post("/v1/chat/completions", json={"messages": []})
    assert upstream2.bodies[0]["model"] == "openai/gpt-oss-120b"


def test_openai_passthrough_replaces_the_callers_key(openai_env):
    upstream = Upstream()
    client = _client(upstream)
    client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer from-upstream"},
        json={"model": "openai/meta/llama-4-scout", "messages": [{"role": "user", "content": "x"}]},
    )
    assert upstream.requests[0].headers["authorization"] == f"Bearer {SECRET}"


def test_openai_errors_never_echo_the_key(openai_env):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    res = _chat(_client(Upstream(timeout)), "openai/meta/llama-4-scout")
    assert res.status_code == 504 and res.json()["error"]["type"] == "upstream_timeout"
    assert SECRET not in res.text


def test_openai_models_lists_the_configured_model(openai_env):
    res = _client(Upstream()).get("/v1/models")
    assert res.json()["data"] == [
        {"id": "openai/meta/llama-4-scout", "object": "model", "owned_by": "openai"}
    ]


@contextlib.contextmanager
def serve(app):
    """Run *app* on a free 127.0.0.1 port in a thread (real HTTP, like upstream's client)."""
    import socket
    import threading
    import time

    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


def test_langchain_chatopenai_structured_output_through_router(openai_env):
    """The path upstream takes: ChatOpenAI(openai/<model>).with_structured_output, through
    the router to the OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel

    class Ping(BaseModel):
        answer: str

    upstream = Upstream()
    settings = Settings.from_env()
    with serve(web.create_app(settings, llm_transport=upstream.transport)) as base_url:
        llm = ChatOpenAI(
            model=settings.upstream_chat_model,
            api_key="autoontology-router",
            base_url=f"{base_url}/v1",
            max_retries=0,
            timeout=30,
        )
        result = llm.with_structured_output(Ping, method="json_schema").invoke("pong?")
    assert result == Ping(answer="pong")
    assert upstream.bodies[0]["model"] == "meta/llama-4-scout"
    assert upstream.requests[0].headers["authorization"] == f"Bearer {SECRET}"


def test_doctor_style_complete_through_the_provider(openai_env):
    import asyncio

    from autoontology.web.providers.openai import OpenAIProvider
    from autoontology.web.providers.openai_compat import ChatMessage, ChatRequest

    upstream = Upstream()
    provider = OpenAIProvider(Settings.from_env(), upstream.transport)
    resp = asyncio.run(
        provider.complete(
            ChatRequest(
                model="openai/meta/llama-4-scout",
                messages=[ChatMessage("user", "x")],
                schema=SCHEMA,
                schema_name="Ping",
                mode="json_schema",
                max_tokens=64,
            )
        )
    )
    assert resp.content == '{"answer": "pong"}' and resp.completion_tokens == 2
    assert upstream.bodies[0]["max_tokens"] == 64
    failing = OpenAIProvider(
        Settings.from_env(),
        Upstream(
            lambda r: httpx.Response(401, json={"error": {"message": "bad key", "type": "auth"}})
        ).transport,
    )
    with pytest.raises(ProviderError) as err:
        asyncio.run(failing.complete(ChatRequest(model="m", messages=[ChatMessage("user", "x")])))
    assert err.value.status == 401 and err.value.type == "auth"


# --- schema checks (playground answers, doctor probes) ----------------------------------


@pytest.fixture
def no_fetch(monkeypatch):
    """Any attempt to fetch a schema reference fails the test (no real network/file IO)."""
    import urllib.request

    seen = []

    def boom(*args, **kwargs):
        seen.append(args)
        raise AssertionError(f"schema reference fetched: {args!r}")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return seen


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "http://127.0.0.1:9/evil.json"},
        {"$ref": "file:///etc/passwd"},
        {"type": "object", "properties": {"a": {"items": {"$ref": "http://example.invalid/n"}}}},
        {"properties": {"a": {"$ref": "sibling.json#/defs/a"}}},
        {"type": "bogus"},
        {"required": "answer"},
        {"$ref": "#"},
        {
            "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"anyOf": [{"$ref": "#/$defs/a"}]}},
            "$ref": "#/$defs/a",
        },
    ],
    ids=["http", "file", "nested", "relative", "bogus-type", "bad-required", "self", "defs-cycle"],
)
def test_check_schema_refuses_unsafe_or_invalid_schemas(no_fetch, schema):
    from autoontology.web.providers.base import check_schema

    with pytest.raises(ProviderError) as err:
        check_schema(schema)
    assert err.value.status == 400 and err.value.type == "invalid_schema"
    assert no_fetch == []


def test_schema_size_and_depth_are_bounded():
    from autoontology.web.providers import base

    deep: dict = {"type": "string"}
    for _ in range(base.MAX_SCHEMA_DEPTH + 5):
        deep = {"type": "object", "properties": {"a": deep}}
    big = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string", "description": "x" * 200} for i in range(1000)},
    }
    for schema in (deep, big):
        with pytest.raises(ProviderError):
            base.check_schema(schema)


def test_schema_problem(no_fetch):
    from autoontology.web.providers.base import schema_problem

    with pytest.raises(ProviderError) as bad:
        schema_problem({"a": 1}, {"type": "bogus"})
    assert bad.value.status == 400
    assert schema_problem({"answer": "x"}, SCHEMA) is None
    assert "answer" in (schema_problem({"answer": 1}, SCHEMA) or "")
    tree = {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#"}}}}
    assert schema_problem({"kids": [{"kids": []}]}, tree) is None
    assert no_fetch == []


def test_schema_problem_maps_recursion_to_400(monkeypatch):
    from autoontology.web.providers import base

    monkeypatch.setattr(base, "check_schema", lambda schema: None)  # bypass admission
    with pytest.raises(ProviderError) as err:
        base.schema_problem({"a": 1}, {"$ref": "#"})
    assert err.value.status == 400
