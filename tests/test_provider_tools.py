# SPDX-License-Identifier: MIT
"""Multi-tool (function) calling through the claude/codex CLI providers.

Upstream's ReAct text-to-SQL agent binds several tools (``llm.bind_tools``) and loops over
tool calls with ``role: tool`` results; the router turns each turn into one structured CLI
call whose JSON answer is either a tool call or a text answer.
"""

import json

import pytest

from autoontology.web import app as web
from autoontology.web.providers.openai_compat import (
    MAX_TOOL_OUTPUT_CHARS,
    ProviderError,
    parse_request,
    to_openai,
    to_sse,
)
from tests.test_providers_cli import _client, _settings, fakes, serve  # noqa: F401 - fixture

RETRIEVE = {
    "type": "function",
    "function": {
        "name": "retrieve",
        "description": "Search the semantic layer.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}
RUN_SQL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": "Run a read-only query and return the first rows.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT query."}},
            "required": ["sql"],
        },
    },
}
SUBMIT = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "Submit the final query that answers the question.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}, "response": {"type": "string"}},
            "required": ["sql", "response"],
        },
    },
}
TOOLS = [RETRIEVE, RUN_SQL, SUBMIT]
MSGS = [
    {"role": "system", "content": "You write SQL."},
    {"role": "user", "content": "How many orders?"},
]


def _body(**extra):
    return {"model": "m", "messages": MSGS, "tools": TOOLS, **extra}


def _post(client, model="openai/claude:sonnet", **extra):
    body = {"model": model, "messages": MSGS, "tools": TOOLS, **extra}
    return client.post("/v1/chat/completions", json=body)


def _answers(monkeypatch, *answers):
    monkeypatch.setenv("FAKE_ANSWERS", json.dumps(list(answers)))


def call(tool, **arguments):
    return {"type": "tool_call", "name": tool, "arguments": arguments}


# --- request parsing -------------------------------------------------------------------


def test_several_tools_select_tool_mode_with_an_envelope_schema():
    req = parse_request(_body(), "m")
    assert req.mode == "tools" and req.structured
    assert [t.name for t in req.tools] == ["retrieve", "run_sql", "submit"]
    assert req.tool_choice == "auto"
    props = req.schema["properties"]
    assert props["type"]["enum"] == ["tool_call", "text"]
    assert props["name"]["enum"] == ["retrieve", "run_sql", "submit"]
    # the instructions carry every tool's name, description and parameters schema
    system = req.system_text()
    assert system.startswith("You write SQL.")
    for tool in TOOLS:
        fn = tool["function"]
        assert fn["name"] in system and fn["description"] in system
    assert json.dumps(RUN_SQL["function"]["parameters"], ensure_ascii=False) in system


def test_tool_choice_variants():
    assert parse_request(_body(tool_choice="none"), "m").mode == ""
    auto = parse_request(_body(tool_choice="auto"), "m")
    assert auto.mode == "tools" and "text" in auto.schema["properties"]["type"]["enum"]
    for required in ("required", "any"):
        req = parse_request(_body(tool_choice=required), "m")
        assert req.tool_choice == "required"
        assert req.schema["properties"]["type"]["enum"] == ["tool_call"]
        assert "must call one of the tools" in req.system_text()
    specific = parse_request(
        _body(tool_choice={"type": "function", "function": {"name": "submit"}}), "m"
    )
    assert specific.tool_choice == "submit"
    assert specific.schema["properties"]["name"]["enum"] == ["submit"]
    assert specific.schema["properties"]["type"]["enum"] == ["tool_call"]
    # a single tool with "auto" may be answered in text: tool mode, not forced output
    one_auto = parse_request(_body(tools=[RUN_SQL], tool_choice="auto"), "m")
    assert one_auto.mode == "tools"


def test_single_tool_structured_output_path_is_unchanged():
    for extra in (
        {},
        {"tool_choice": "required"},
        {"tool_choice": {"type": "function", "function": {"name": "submit"}}},
    ):
        req = parse_request(_body(tools=[SUBMIT], **extra), "m")
        assert (req.mode, req.schema_name) == ("tool", "submit")
        assert req.schema == SUBMIT["function"]["parameters"]


@pytest.mark.parametrize(
    "extra",
    [
        {"tool_choice": {"type": "function", "function": {"name": "nope"}}},
        {"tools": [SUBMIT], "tool_choice": {"type": "function", "function": {"name": "nope"}}},
        {"tool_choice": "sometimes"},
        {"tools": [RUN_SQL, RUN_SQL]},
        {"tools": [RUN_SQL, {"type": "function", "function": {"parameters": {}}}]},
    ],
)
def test_bad_tool_requests_are_400(extra):
    with pytest.raises(ProviderError) as exc:
        parse_request(_body(**extra), "m")
    assert exc.value.status == 400


def test_every_tool_schema_is_admitted_before_the_cli():
    bad = {
        "type": "function",
        "function": {"name": "x", "parameters": {"$ref": "https://example.com/s.json"}},
    }
    with pytest.raises(ProviderError) as exc:
        parse_request(_body(tools=[RUN_SQL, bad]), "m")
    assert exc.value.type == "invalid_schema"


def _history():
    return [
        *MSGS,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "1 row: (1,)"},
    ]


def test_history_renders_tool_calls_and_results():
    req = parse_request(_body(messages=_history()), "m")
    text = req.transcript()
    assert 'Assistant called tool run_sql with {"sql": "SELECT 1"} (id call_1)' in text
    assert "Tool run_sql (id call_1) returned:\n1 row: (1,)" in text
    assert text.index("How many orders?") < text.index("called tool") < text.index("returned")
    assert req.system_text().startswith("You write SQL.")


def test_huge_tool_output_is_truncated_with_a_marker():
    history = _history()
    history[-1]["content"] = "A" * (MAX_TOOL_OUTPUT_CHARS * 3) + "TAIL"
    req = parse_request(_body(messages=history), "m")
    tool_turn = req.conversation()[-1].content
    assert len(tool_turn) < MAX_TOOL_OUTPUT_CHARS + 500
    assert "characters truncated" in tool_turn and tool_turn.endswith("TAIL")


def test_long_histories_shrink_older_tool_results_first():
    from autoontology.web.providers import openai_compat

    history = list(MSGS)
    for i in range(40):
        history.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "retrieve", "arguments": json.dumps({"query": i})},
                    }
                ],
            }
        )
        history.append(
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"R{i}:" + "x" * 11_000}
        )
    req = parse_request(_body(messages=history), "m")
    total = sum(len(m.content) for m in req.conversation())
    assert total <= openai_compat.MAX_HISTORY_CHARS
    turns = req.conversation()
    assert "characters truncated" in turns[2].content  # the oldest result shrank
    assert "x" * 10_000 in turns[-1].content  # the newest is kept whole
    assert req.system_text().startswith("You write SQL.")  # never dropped


# --- wire format out ---------------------------------------------------------------------


def _resp(content):
    from autoontology.web.providers.openai_compat import ChatResponse

    return ChatResponse(content=content, model="m", prompt_tokens=1, completion_tokens=2)


def test_to_openai_tool_call_and_text_answers():
    req = parse_request(_body(), "m")
    out = to_openai(req, _resp(json.dumps(call("run_sql", sql="SELECT 1"))), "x")
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    msg = choice["message"]
    assert msg["content"] is None
    (tc,) = msg["tool_calls"]
    assert tc["id"].startswith("call_") and tc["type"] == "function"
    assert tc["function"]["name"] == "run_sql"
    assert json.loads(tc["function"]["arguments"]) == {"sql": "SELECT 1"}
    text = to_openai(req, _resp(json.dumps({"type": "text", "content": "42"})), "x")
    assert text["choices"][0]["message"] == {"role": "assistant", "content": "42"}
    assert text["choices"][0]["finish_reason"] == "stop"


def test_to_sse_streams_a_tool_call_delta():
    req = parse_request(_body(stream=True), "m")
    raw = to_sse(req, _resp(json.dumps(call("retrieve", query="orders"))), "x").decode()
    first = json.loads(raw.split("\n\n")[0].removeprefix("data: "))
    delta = first["choices"][0]["delta"]
    assert first["choices"][0]["finish_reason"] == "tool_calls"
    (tc,) = delta["tool_calls"]
    assert tc["index"] == 0 and tc["function"]["name"] == "retrieve"
    assert raw.endswith("data: [DONE]\n\n")


# --- through the router with fake CLIs --------------------------------------------------


def test_claude_multi_tool_turn_returns_a_tool_call(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("run_sql", sql="SELECT count(*) FROM orders"))
    res = _post(_client())
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls" and choice["message"]["content"] is None
    (tc,) = choice["message"]["tool_calls"]
    assert tc["function"]["name"] == "run_sql"
    assert json.loads(tc["function"]["arguments"]) == {"sql": "SELECT count(*) FROM orders"}
    (cli,) = fakes()
    schema = json.loads(cli["argv"][cli["argv"].index("--json-schema") + 1])
    assert schema["properties"]["name"]["enum"] == ["retrieve", "run_sql", "submit"]
    assert "Run a read-only query" in cli["system"] and "You write SQL." in cli["system"]


def test_claude_text_answer_is_stop(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, {"type": "text", "content": "I need more detail."})
    res = _post(_client())
    assert res.status_code == 200, res.text
    choice = res.json()["choices"][0]
    assert choice["message"]["content"] == "I need more detail."
    assert choice["finish_reason"] == "stop" and "tool_calls" not in choice["message"]


def test_unknown_tool_name_is_reasked_once(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("drop_table", name="x"), call("retrieve", query="orders"))
    res = _post(_client())
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "retrieve"
    first, second = fakes()
    assert "drop_table" in second["stdin"] and "retrieve, run_sql, submit" in second["stdin"]


def test_arguments_violating_the_tool_schema_are_reasked_then_422(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("run_sql", query="SELECT 1"))
    res = _post(_client())
    assert res.status_code == 422, res.text
    assert res.json()["error"]["type"] == "schema_mismatch"
    first, second = fakes()
    assert "'sql' is a required property" in second["stdin"]


def test_required_tool_choice_refuses_a_text_answer(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, {"type": "text", "content": "no"}, call("submit", sql="S", response="r"))
    res = _post(_client(), tool_choice="required")
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "submit"
    assert len(fakes()) == 2


def test_specific_tool_choice_refuses_another_tool(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("run_sql", sql="S"), call("submit", sql="S", response="r"))
    res = _post(_client(), tool_choice={"type": "function", "function": {"name": "submit"}})
    assert res.status_code == 200, res.text
    assert len(fakes()) == 2


def test_tool_choice_none_is_a_plain_text_call(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, "just text")
    res = _post(_client(), tool_choice="none")
    assert res.status_code == 200, res.text
    assert res.json()["choices"][0]["message"]["content"] == "just text"
    (cli,) = fakes()
    assert "--json-schema" not in cli["argv"]


def test_streamed_tool_call(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("retrieve", query="orders"))
    body = {"model": "openai/claude:sonnet", "messages": MSGS, "tools": TOOLS, "stream": True}
    with _client().stream("POST", "/v1/chat/completions", json=body) as res:
        assert res.status_code == 200
        raw = res.read().decode()
    first = json.loads(raw.split("\n\n")[0].removeprefix("data: "))
    assert first["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "retrieve"


def test_history_reaches_the_cli(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("submit", sql="SELECT 1", response="One."))
    res = _client().post(
        "/v1/chat/completions",
        json={"model": "openai/claude:sonnet", "messages": _history(), "tools": TOOLS},
    )
    assert res.status_code == 200, res.text
    (cli,) = fakes()
    assert "called tool run_sql" in cli["stdin"] and "1 row: (1,)" in cli["stdin"]


def test_codex_multi_tool_turn(fakes, monkeypatch):  # noqa: F811
    _answers(monkeypatch, call("retrieve", query="orders"))
    res = _post(_client("codex"), model="openai/chatgpt:gpt-6-astra")
    assert res.status_code == 200, res.text
    tc = res.json()["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "retrieve"
    (cli,) = fakes()
    assert "Search the semantic layer." in cli["stdin"] and '"tool_call"' in cli["stdin"]


def test_langchain_react_loop_through_the_router(fakes, monkeypatch):  # noqa: F811
    """A tiny ReAct loop the way upstream drives it: bind_tools, ToolMessages back."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI

    _answers(
        monkeypatch,
        call("retrieve", query="orders"),
        call("run_sql", sql="SELECT count(*) FROM orders"),
        call("submit", sql="SELECT count(*) FROM orders", response="There are 3 orders."),
    )
    observations = {"retrieve": "table orders(id)", "run_sql": "count=3"}
    with serve(web.create_app(_settings("claude"))) as base_url:
        llm = ChatOpenAI(
            model="openai/claude:claude-sonnet-5-5",
            api_key="ollama",
            base_url=f"{base_url}/v1",
            max_retries=0,
            timeout=30,
        )
        bound = llm.bind_tools(TOOLS)
        messages = [SystemMessage("You write SQL."), HumanMessage("How many orders?")]
        submitted = None
        for _ in range(5):
            reply = bound.invoke(messages)
            messages.append(reply)
            (tc,) = reply.tool_calls
            if tc["name"] == "submit":
                submitted = tc["args"]
                break
            messages.append(ToolMessage(observations[tc["name"]], tool_call_id=tc["id"]))
    assert submitted == {"sql": "SELECT count(*) FROM orders", "response": "There are 3 orders."}
    calls = fakes()
    assert len(calls) == 3
    last = calls[-1]["stdin"]
    assert "returned:\ntable orders(id)" in last and "returned:\ncount=3" in last
    assert "called tool run_sql" in last
