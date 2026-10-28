# SPDX-License-Identifier: MIT
"""The subscription-CLI chat providers behind /v1 (claude, codex), with fake CLIs."""

import contextlib
import dataclasses
import json
import os
import stat
import sys
import textwrap

import httpx
import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.web import app as web
from autoontology.web.providers.codex_cli import strict_compatible
from autoontology.web.providers.openai_compat import ProviderError
from autoontology.web.providers.router import PASSTHROUGH, split_model

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
FMT = {"type": "json_schema", "json_schema": {"name": "Ping", "schema": SCHEMA, "strict": True}}

FAKE_CLAUDE = r"""
import json, os, sys, time
log = os.environ["FAKE_LOG"]
stdin = sys.stdin.read()
argv = sys.argv[1:]
system = (open(argv[argv.index("--system-prompt-file") + 1]).read()
          if "--system-prompt-file" in argv else None)
with open(log, "a") as fh:
    fh.write(json.dumps({"argv": argv, "stdin": stdin, "system": system, "pid": os.getpid(),
                         "env": {k: os.environ.get(k) for k in
                                 ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY",
                                  "CLAUDE_CODE_DISABLE_FILE_MENTIONS",
                                  "CLAUDE_CODE_DISABLE_ATTACHMENTS",
                                  "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                                  "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                                  "CLAUDE_CODE_MAX_OUTPUT_TOKENS")},
                         "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": mode != "notlogged", "authMethod": "claude.ai",
                      "email": "someone@example.com", "subscriptionType": "max"}))
    sys.exit(0 if mode != "notlogged" else 1)
if mode == "sleep":
    time.sleep(30)
if mode == "overbudget" and os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"):
    print(json.dumps({"type": "result", "is_error": True, "stop_reason": "stop_sequence",
                      "result": "API Error: Claude's response exceeded the "
                      + os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] + " output token maximum.",
                      "usage": {"output_tokens": 9}}))
    sys.exit(1)
if mode == "notlogged":
    print(json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login",
                      "api_error_status": None, "total_cost_usd": 0}))
    sys.exit(1)
answers = json.loads(os.environ["FAKE_ANSWERS"])
n = sum(1 for _ in open(log)) - 1
ans = answers[min(n, len(answers) - 1)]
if ans == "__OVERBUDGET__" and os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"):
    print(json.dumps({"type": "result", "is_error": True, "stop_reason": "stop_sequence",
                      "result": "API Error: Claude's response exceeded the "
                      + os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] + " output token maximum.",
                      "usage": {"output_tokens": 9}}))
    sys.exit(1)
env = {"type": "result", "is_error": False, "result": json.dumps(ans) if not isinstance(ans, str) else ans,
       "usage": {"input_tokens": 3, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5,
                 "output_tokens": 7}, "total_cost_usd": 0.0012, "duration_ms": 900,
       "modelUsage": {"claude-sonnet-5-5": {}}}
if "--json-schema" in sys.argv and not isinstance(ans, str):
    env["structured_output"] = ans
print(json.dumps(env))
"""

FAKE_CODEX = r"""
import json, os, sys
log = os.environ["FAKE_LOG"]
args = sys.argv[1:]
stdin = sys.stdin.read() if "-" in args else ""
with open(log, "a") as fh:
    fh.write(json.dumps({"argv": args, "stdin": stdin, "cwd": os.getcwd(),
                         "codex_home": sorted(os.listdir(os.environ["CODEX_HOME"])),
                         "codex_home_path": os.environ["CODEX_HOME"],
                         "env": {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "CLAUDECODE")}}) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if args[:2] == ["login", "status"]:
    print("Not logged in" if mode == "notlogged" else "Logged in using ChatGPT")
    sys.exit(1 if mode == "notlogged" else 0)
print(json.dumps({"type": "thread.started", "thread_id": "x"}))
if mode == "notlogged":
    print(json.dumps({"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401 Unauthorized: Missing bearer)"}))
    print(json.dumps({"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}}))
    sys.exit(1)
if mode == "todo":
    print(json.dumps({"type": "item.completed", "item": {"id": "t1", "type": "todo_list",
                                                         "items": [{"text": "answer"}]}}))
if mode == "tool":
    print(json.dumps({"type": "item.started", "item": {"id": "i1", "type": "command_execution",
                                                       "command": "cat ~/.ssh/id_rsa"}}))
answers = json.loads(os.environ["FAKE_ANSWERS"])
n = sum(1 for _ in open(log)) - 1
ans = answers[min(n, len(answers) - 1)]
out = args[args.index("-o") + 1]
with open(out, "w") as fh:
    fh.write(ans if isinstance(ans, str) else json.dumps(ans))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 4}}))
"""


def _install(tmp_path, name, body):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    path = bindir / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def fakes(isolated_env, monkeypatch):
    tmp = isolated_env
    log = tmp / "calls.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_BIN", str(_install(tmp, "claude", FAKE_CLAUDE)))
    monkeypatch.setenv("AUTOONTOLOGY_CODEX_BIN", str(_install(tmp, "codex", FAKE_CODEX)))
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"answer": "pong"}]))

    def calls():
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return calls


def _settings(provider="claude"):
    return dataclasses.replace(Settings.from_env(), chat_provider=provider)


def _client(provider="claude", **kwargs):
    return TestClient(web.create_app(_settings(provider), **kwargs))


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


# --- dispatch ----------------------------------------------------------------------


def test_split_model():
    assert split_model("openai/claude:claude-sonnet-5-5") == ("claude", "claude-sonnet-5-5")
    assert split_model("codex:gpt-6-astra") == ("codex", "gpt-6-astra")
    assert split_model("openai/chatgpt:gpt-5.5") == ("codex", "gpt-5.5")
    # the Ollama alias keeps its literal openai/ name (passthrough)
    assert split_model("openai/qwen3:8b") == (PASSTHROUGH, "openai/qwen3:8b")
    assert split_model("openai/claude:") == (PASSTHROUGH, "openai/claude:")
    assert split_model("openai/meta/llama-4") == (PASSTHROUGH, "openai/meta/llama-4")


def test_cli_prefix_of_an_inactive_provider_is_refused(fakes):
    res = _chat(_client("claude"), "openai/chatgpt:gpt-6-astra")
    assert res.status_code == 400 and res.json()["error"]["type"] == "provider_not_active"
    assert "AUTOONTOLOGY_CHAT_PROVIDER=codex" in res.json()["error"]["remedy"]
    res = _chat(_client("ollama"), "openai/claude:sonnet")
    assert res.status_code == 400 and res.json()["error"]["type"] == "provider_not_active"
    # a passthrough name while a CLI provider is active
    res = _chat(_client("codex"), "openai/qwen3:8b")
    assert res.status_code == 400 and res.json()["error"]["type"] == "provider_not_active"
    assert fakes() == []


def test_strict_compatible():
    assert strict_compatible(SCHEMA)
    loose = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    }
    assert not strict_compatible(loose)
    assert not strict_compatible(
        {**SCHEMA, "properties": {"answer": {"type": "string", "default": "x"}}}
    )


# --- claude --------------------------------------------------------------------------


def test_claude_json_schema_call(fakes, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    res = _chat(_client(), "openai/claude:claude-sonnet-5-5", response_format=FMT)
    assert res.status_code == 200, res.text
    body = res.json()
    assert json.loads(body["choices"][0]["message"]["content"]) == {"answer": "pong"}
    assert body["usage"]["prompt_tokens"] == 18 and body["usage"]["completion_tokens"] == 7
    assert body["autoontology_cost_usd"] == pytest.approx(0.0012)
    (call,) = fakes()
    argv = call["argv"]
    assert argv[:2] == ["-p", "--no-session-persistence"] and "--bare" not in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--system-prompt" not in argv  # not visible in ps: a file
    assert call["system"] == "Be terse."
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert "--strict-mcp-config" in argv and argv[argv.index("--setting-sources") + 1] == ""
    assert call["stdin"] == "say pong"
    assert call["env"] == {
        "CLAUDECODE": None,
        "CLAUDE_CODE_ENTRYPOINT": None,
        "ANTHROPIC_API_KEY": None,
        "CLAUDE_CODE_DISABLE_FILE_MENTIONS": "1",
        "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": None,
    }
    assert "--disable-slash-commands" in argv
    assert body["choices"][0]["finish_reason"] == "stop"
    # an empty temp dir outside the repo and the OntoSpace home, removed after the call
    assert os.path.basename(call["cwd"]).startswith("autoontology-provider-claude-")
    assert not os.path.realpath(call["cwd"]).startswith(os.path.realpath(os.getcwd()))
    assert not os.path.exists(call["cwd"])


def test_claude_input_lockdown_survives_a_parent_override(fakes, monkeypatch):
    # the CLAUDE_CODE_* scrub must not drop the lockdown, nor may a parent value of "0" win
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_FILE_MENTIONS", "0")
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 200, res.text
    (call,) = fakes()
    assert call["env"]["CLAUDE_CODE_DISABLE_FILE_MENTIONS"] == "1"
    assert call["env"]["CLAUDE_CODE_DISABLE_ATTACHMENTS"] == "1"


def test_claude_tool_call_mode_answers_with_tool_calls(fakes):
    tools = [{"type": "function", "function": {"name": "Ping", "parameters": SCHEMA}}]
    res = _chat(_client(), "openai/claude:sonnet", tools=tools)
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == {
        "answer": "pong"
    }


def test_schema_mismatch_reasks_once_then_succeeds(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"wrong": 1}, {"answer": "pong"}]))
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 200, res.text
    first, second = fakes()
    assert "did not validate" in second["stdin"] and "answer" in second["stdin"]
    assert res.json()["usage"]["completion_tokens"] == 14  # both calls counted


def test_schema_mismatch_twice_is_422(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["not json"]))
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 422
    assert res.json()["error"]["type"] == "schema_mismatch"
    assert len(fakes()) == 2


def test_claude_not_logged_in_is_401_with_remedy(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT)
    assert res.status_code == 401
    err = res.json()["error"]
    assert err["type"] == "not_logged_in" and "claude auth login" in err["remedy"]


def test_cli_timeout_is_504(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("AUTOONTOLOGY_PROVIDER_TIMEOUT_S", "1")
    res = _chat(_client(), "openai/claude:sonnet")
    assert res.status_code == 504 and res.json()["error"]["type"] == "upstream_timeout"


def test_missing_cli_is_503(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_BIN", str(isolated_env / "nope" / "claude"))
    monkeypatch.setenv("PATH", str(isolated_env / "empty"))
    res = _chat(_client(), "openai/claude:sonnet")
    assert res.status_code == 503 and res.json()["error"]["type"] == "cli_not_found"


def test_stream_request_gets_single_sse_chunk(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["plain text answer"]))
    with _client().stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "openai/claude:sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        raw = res.read().decode()
    assert raw.endswith("data: [DONE]\n\n") and "plain text answer" in raw


# --- codex ---------------------------------------------------------------------------


def test_codex_strict_schema_uses_output_schema(fakes, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    assert json.loads(res.json()["choices"][0]["message"]["content"]) == {"answer": "pong"}
    assert res.json()["usage"]["prompt_tokens"] == 100
    (call,) = fakes()
    argv = call["argv"]
    assert argv[:3] == ["exec", "-m", "gpt-6-astra"]
    for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--json"):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert os.path.realpath(argv[argv.index("-C") + 1]) == os.path.realpath(call["cwd"])
    assert "--output-schema" in argv and argv[-1] == "-"
    assert call["stdin"].startswith("System:\nBe terse.\n\nUser:\nsay pong")
    assert "JSON Schema" not in call["stdin"]
    assert call["env"]["OPENAI_API_KEY"] is None
    # tools off, own CODEX_HOME with only the login (no personal AGENTS.md/config)
    for feature in (
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "memories",
        "computer_use",
        "browser_use",
        "hooks",
        "multi_agent",
        "multi_agent_v2",
    ):
        assert argv[argv.index(feature) - 1] == "--disable"
    assert 'web_search="disabled"' in argv
    assert [f for f in call["codex_home"] if not f.startswith(".")] == ["auth.json"]
    assert call["codex_home_path"].endswith("/run/codex-home")
    assert os.path.basename(call["cwd"]).startswith("autoontology-provider-codex-")


def test_codex_tool_use_fails_closed(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "tool")
    res = _chat(_client("codex"), "openai/chatgpt:gpt-6-astra", response_format=FMT)
    assert res.status_code == 502
    err = res.json()["error"]
    assert err["type"] == "tool_use_refused" and "command_execution" in err["message"]


def test_codex_plan_updates_are_not_tool_use(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "todo")
    res = _chat(_client("codex"), "openai/chatgpt:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text


def test_codex_home_is_safe_under_concurrent_calls(isolated_env, monkeypatch):
    import threading

    from autoontology.web.providers import codex_cli

    user = isolated_env / "user-codex"
    user.mkdir()
    (user / "auth.json").write_text("tok")
    monkeypatch.setenv("CODEX_HOME", str(user))
    home = isolated_env / "h"
    target = codex_cli.ensure_codex_home(home)
    (target / "auth.json").unlink()
    (target / "auth.json").write_text("refreshed")  # replaced by a token refresh
    real = codex_cli._ensure_codex_home
    inside = []

    def slow(t):
        inside.append(1)
        assert len(inside) == 1, "two threads inside ensure_codex_home at once"
        try:
            import time

            time.sleep(0.02)
            return real(t)
        finally:
            inside.pop()

    monkeypatch.setattr(codex_cli, "_ensure_codex_home", slow)
    errors = []

    def worker():
        try:
            codex_cli.ensure_codex_home(home)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert (target / "auth.json").is_symlink() and (user / "auth.json").read_text() == "refreshed"


def test_codex_home_links_auth_and_syncs_a_refreshed_copy_back(isolated_env, monkeypatch):
    from autoontology.web.providers.codex_cli import ensure_codex_home

    user = isolated_env / "user-codex"
    user.mkdir()
    (user / "auth.json").write_text("old")
    monkeypatch.setenv("CODEX_HOME", str(user))
    home = isolated_env / "h"
    target = ensure_codex_home(home)
    link = target / "auth.json"
    assert link.is_symlink() and link.read_text() == "old"
    # codex refreshed its token by replacing the symlink with a newer file
    link.unlink()
    link.write_text("new")
    os.utime(link, (os.path.getmtime(user / "auth.json") + 10,) * 2)
    (target / "AGENTS.md").write_text("stray")
    ensure_codex_home(home)
    assert (user / "auth.json").read_text() == "new"  # the user's login keeps working
    assert link.is_symlink() and not (target / "AGENTS.md").exists()


def test_codex_loose_schema_goes_into_the_prompt(fakes):
    loose = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
        "required": ["answer"],
    }
    fmt = {"type": "json_schema", "json_schema": {"name": "P", "schema": loose}}
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=fmt)
    assert res.status_code == 200, res.text
    (call,) = fakes()
    assert "--output-schema" not in call["argv"]
    assert "JSON Schema" in call["stdin"] and '"note"' in call["stdin"]


def test_codex_not_logged_in_is_401(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 401 and "codex login" in res.json()["error"]["remedy"]


# --- models / status / ollama dispatch -------------------------------------------------


def test_models_lists_the_active_cli_provider_when_logged_in(fakes, monkeypatch):
    ids = [m["id"] for m in _client("claude").get("/v1/models").json()["data"]]
    assert ids[0] == "openai/claude:claude-sonnet-5-5" and "openai/claude:haiku" in ids
    assert not any(i.startswith("openai/chatgpt:") for i in ids)
    ids = [m["id"] for m in _client("codex").get("/v1/models").json()["data"]]
    assert ids == ["openai/chatgpt:gpt-6-astra", "openai/chatgpt:gpt-5.5"]
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_MODEL", "claude-opus-5-5")
    ids = [m["id"] for m in _client("claude").get("/v1/models").json()["data"]]
    assert ids[0] == "openai/claude:claude-opus-5-5" and len(ids) == len(set(ids))
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    assert _client("claude").get("/v1/models").json()["data"] == []


def test_claude_status_never_exposes_account_details(fakes):
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider

    st = ClaudeCLIProvider(Settings.from_env().home, ["sonnet"]).status()
    assert st.ok and st.detail == "logged in (claude.ai, max)"
    assert "example.com" not in json.dumps(st.as_dict())


def test_other_models_still_pass_through_to_ollama(fakes):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = _client("ollama", llm_transport=httpx.MockTransport(handler))
    res = _chat(client, "openai/qwen3:8b", response_format=FMT)
    assert res.status_code == 200
    assert seen["body"]["model"] == "openai/qwen3:8b"
    assert seen["body"]["reasoning_effort"] == "none"
    assert fakes() == []


# --- upstream-style client -------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "method"),
    [
        ("openai/claude:claude-sonnet-5-5", "function_calling"),
        ("openai/claude:claude-sonnet-5-5", "json_schema"),
        # the upstream name for codex must keep langchain on /v1/chat/completions
        ("openai/chatgpt:gpt-6-astra", "json_schema"),
    ],
)
def test_langchain_chatopenai_structured_output_through_router(fakes, model, method):
    """The path upstream takes: ChatOpenAI(<upstream model>).with_structured_output."""
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel

    class Ping(BaseModel):
        answer: str

    provider = "claude" if "claude" in model else "codex"
    with serve(web.create_app(_settings(provider))) as base_url:
        llm = ChatOpenAI(
            model=model, api_key="ollama", base_url=f"{base_url}/v1", max_retries=0, timeout=30
        )
        kwargs = {"method": method}
        if method == "function_calling":
            kwargs["tool_choice"] = None
        result = llm.with_structured_output(Ping, **kwargs).invoke("Return answer='pong'.")
    assert result == Ping(answer="pong")
    (call,) = fakes()
    assert ("--json-schema" if "claude" in model else "--output-schema") in call["argv"]


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


# --- caller schemas (review #2, #5) ---------------------------------------------------


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


def _schema_fmt(schema):
    return {"type": "json_schema", "json_schema": {"name": "X", "schema": schema}}


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "http://127.0.0.1:9/evil.json"},
        {"$ref": "https://example.invalid/s.json#/x"},
        {"$ref": "file:///etc/passwd"},
        {"type": "object", "properties": {"a": {"items": {"$ref": "http://example.invalid/n"}}}},
        {"$id": "http://example.invalid/root.json", "properties": {"a": {"$ref": "other.json"}}},
        {"properties": {"a": {"$ref": "sibling.json#/defs/a"}}},
        {"$defs": {"a": {"$dynamicRef": "https://example.invalid/d"}}},
    ],
    ids=["http", "https", "file", "nested", "relative-with-id", "relative", "dynamic"],
)
def test_non_local_schema_refs_are_refused_before_the_cli(fakes, no_fetch, schema):
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
    assert res.status_code == 400, res.text
    assert res.json()["error"]["type"] == "invalid_schema"
    assert fakes() == [] and no_fetch == []


def test_tool_parameters_with_remote_ref_are_refused(fakes, no_fetch):
    tools = [
        {
            "type": "function",
            "function": {"name": "Ping", "parameters": {"$ref": "http://example.invalid/p.json"}},
        }
    ]
    res = _chat(_client(), "openai/claude:sonnet", tools=tools)
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_schema"
    assert fakes() == []


def test_local_refs_still_validate_offline(fakes, no_fetch, monkeypatch):
    schema = {
        "$id": "https://example.invalid/root.json",
        "type": "object",
        "$defs": {"s": {"type": "string"}},
        "properties": {"answer": {"$ref": "#/$defs/s"}},
        "required": ["answer"],
    }
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
    assert res.status_code == 200, res.text
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"answer": 1}, {"answer": 2}]))
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
    assert res.status_code == 422  # the local $ref is enforced


@pytest.mark.parametrize(
    "schema", [{"type": "bogus"}, {"required": "answer"}, {"properties": {"a": {"minLength": -1}}}]
)
def test_invalid_schema_is_400_before_the_cli(fakes, schema):
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=_schema_fmt(schema))
    assert res.status_code == 400, res.text
    assert res.json()["error"]["type"] == "invalid_schema"
    assert fakes() == []


def test_schema_size_and_depth_are_bounded(fakes):
    from autoontology.web.providers import base

    deep: dict = {"type": "string"}
    for _ in range(base.MAX_SCHEMA_DEPTH + 5):
        deep = {"type": "object", "properties": {"a": deep}}
    big = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string", "description": "x" * 200} for i in range(1000)},
    }
    for schema in (deep, big):
        res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
        assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_schema"
    assert fakes() == []


def test_schema_problem_never_treats_a_bad_schema_as_valid(no_fetch):
    from autoontology.web.providers.base import schema_problem

    with pytest.raises(ProviderError) as bad:
        schema_problem({"a": 1}, {"type": "bogus"})
    assert bad.value.status == 400
    with pytest.raises(ProviderError):
        schema_problem({"a": 1}, {"properties": {"a": {"$ref": "file:///etc/hosts"}}})
    assert no_fetch == []
    assert schema_problem("x", {"type": "string"}) is None


# --- output budget (review #8) --------------------------------------------------------


def test_claude_max_tokens_is_the_cli_native_limit(fakes, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "99999")  # a parent's value: scrubbed
    res = _chat(
        _client(),
        "openai/claude:sonnet",
        response_format=FMT,
        max_tokens=300,
        max_completion_tokens=200,
    )
    assert res.status_code == 200, res.text
    (call,) = fakes()
    assert call["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "200"  # the smaller budget


def test_claude_over_budget_is_finish_reason_length_without_reask(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "overbudget")
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT, max_tokens=8)
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["finish_reason"] == "length"
    assert len(fakes()) == 1  # no schema re-ask of a cut answer


def test_codex_answer_is_cut_to_max_tokens(fakes, monkeypatch):
    long = "word " * 200
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([long]))
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", max_tokens=5)
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "length"
    from autoontology.unstructured.chunker import count_tokens

    assert 0 < count_tokens(choice["message"]["content"]) <= 5
    assert res.json()["usage"]["completion_tokens"] == 5
    # structured: the cut answer is returned as is (length), never re-asked
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"answer": long}]))
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT, max_tokens=5)
    assert res.status_code == 200 and res.json()["choices"][0]["finish_reason"] == "length"
    assert len(fakes()) == 2
    # within budget: untouched
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["short"]))
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", max_tokens=50)
    assert res.json()["choices"][0] == {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "short"},
    }


@pytest.mark.parametrize(
    "extra",
    [{"max_tokens": 0}, {"max_tokens": "10"}, {"max_completion_tokens": True}, {"max_tokens": -5}],
)
def test_bad_token_limits_are_400_before_the_cli(fakes, extra):
    res = _chat(_client(), "openai/claude:sonnet", **extra)
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_request"
    assert fakes() == []


# --- validation work is bounded (recheck #1) -------------------------------------------


def test_regex_backtracking_schema_is_cut_by_the_validation_budget(fakes, monkeypatch):
    import time

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "pattern": "^(a+)+$"}},
        "required": ["answer"],
    }
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([{"answer": "a" * 32 + "!"}]))
    start = time.monotonic()
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
    assert time.monotonic() - start < 4, "validation was not interrupted"
    assert res.status_code == 400, res.text
    assert res.json()["error"]["type"] == "schema_too_complex"


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {"allOf": [{"$ref": "#"}]},
        {
            "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"anyOf": [{"$ref": "#/$defs/a"}]}},
            "$ref": "#/$defs/a",
        },
    ],
    ids=["self", "via-allOf", "defs-cycle"],
)
def test_cyclic_refs_are_refused_at_admission(fakes, schema):
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(schema))
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_schema", res.text
    assert "cycl" in res.json()["error"]["message"]
    assert fakes() == []


def test_recursive_schemas_through_properties_are_fine(fakes, monkeypatch):
    tree = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "kids": {"type": "array", "items": {"$ref": "#"}},
        },
        "required": ["answer"],
    }
    monkeypatch.setenv(
        "FAKE_ANSWERS", json.dumps([{"answer": "pong", "kids": [{"answer": "x", "kids": []}]}])
    )
    res = _chat(_client(), "openai/claude:sonnet", response_format=_schema_fmt(tree))
    assert res.status_code == 200, res.text


def test_schema_problem_maps_recursion_to_400(monkeypatch):
    from autoontology.web.providers import base

    # bypass admission to reach the worker with a self-referencing schema
    monkeypatch.setattr(base, "check_schema", lambda schema: None)
    with pytest.raises(ProviderError) as err:
        base.schema_problem({"a": 1}, {"$ref": "#"})
    assert err.value.status == 400
    assert err.value.type in ("schema_too_complex", "invalid_schema")


# --- the output budget holds on every path (recheck #2) ---------------------------------


def _tokens(text):
    from autoontology.unstructured.chunker import _encoding

    return len(_encoding().encode(text, disallowed_special=()))


def test_codex_retry_answer_is_cut_to_the_budget(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["not json", {"answer": "word " * 100}]))
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT, max_tokens=10)
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "length" and len(fakes()) == 2
    assert _tokens(choice["message"]["content"]) <= 10


def test_normalisation_never_grows_an_answer_past_the_budget(fakes, monkeypatch):
    compact = '{"a":1,"b":2,"c":3,"d":4}'
    budget = _tokens(compact)
    assert _tokens(json.dumps(json.loads(compact))) > budget  # default spacing would exceed
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps([compact]))
    res = _chat(
        _client("codex"),
        "openai/codex:gpt-6-astra",
        response_format={"type": "json_object"},
        max_tokens=budget,
    )
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "stop" and choice["message"]["content"] == compact


def test_claude_retry_over_budget_is_length_not_schema_mismatch(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["not json", "__OVERBUDGET__"]))
    res = _chat(_client(), "openai/claude:sonnet", response_format=FMT, max_tokens=20)
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["finish_reason"] == "length" and len(fakes()) == 2


def test_special_token_text_is_ordinary_output(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(["see <|endoftext|> and <|fim_prefix|> " * 5]))
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", max_tokens=8)
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["finish_reason"] == "length"
    assert _tokens(res.json()["choices"][0]["message"]["content"]) <= 8
