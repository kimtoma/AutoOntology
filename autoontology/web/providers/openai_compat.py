# SPDX-License-Identifier: MIT
"""OpenAI chat-completions wire format <-> :class:`ChatRequest`/:class:`ChatResponse`.

Upstream (LangChain ``ChatOpenAI``) asks for structured output in one of two ways:

* ``response_format: {type: json_schema, json_schema: {name, schema, strict}}``
  (LangChain's default) -> the answer is ``choices[0].message.content`` (JSON text);
* ``tools: [{type: function, function: {name, parameters}}]`` (upstream forces
  ``function_calling`` for some model names) -> the answer is one ``tool_calls`` entry
  whose ``arguments`` is the JSON.

Agents bind several tools (``llm.bind_tools``: upstream's ReAct text-to-SQL loop) and
send ``tool_choice`` absent/"auto"/"required"/a named function. The CLIs have no native
tool calling, so such a request becomes ``mode == "tools"``: the CLI answers one JSON
envelope (:func:`tool_envelope_schema`), ``{"type": "tool_call", "name", "arguments"}`` or
``{"type": "text", "content"}``, which is validated (:func:`tool_answer_problem`) and turned
back into ``message.tool_calls``. At most one tool call per turn (``parallel_tool_calls``
is accepted and ignored: a later turn makes the next call). One tool with no
``tool_choice`` or a forcing one stays ``mode == "tool"`` (forced structured output).

The passthrough providers forward bodies verbatim; these types serve ``doctor``'s probe
calls and the router's error bodies.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

#: A single tool result longer than this is cut (head + tail kept) with a marker.
MAX_TOOL_OUTPUT_CHARS = 12_000
#: The user/assistant/tool turns together; older tool results shrink first to fit.
MAX_HISTORY_CHARS = 200_000
#: What an older tool result shrinks to when the history is over budget.
SHRUNK_TOOL_OUTPUT_CHARS = 1_000


class ProviderError(Exception):
    """A failure mapped to an OpenAI-style error body and HTTP status."""

    def __init__(self, status: int, type_: str, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.status, self.type, self.message, self.remedy = status, type_, message, remedy

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"message": self.message, "type": self.type, "code": self.type}
        if self.remedy:
            err["remedy"] = self.remedy
        return {"error": err}


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    #: JSON schema the answer must satisfy (from response_format or the single tool).
    schema: dict[str, Any] | None = None
    schema_name: str = "response"
    #: "json_schema" | "json_object" | "tool" | "" (free text).
    mode: str = ""
    stream: bool = False
    max_tokens: int | None = None
    #: mode "tools": the tools offered this turn (in the caller's order).
    tools: list[ToolSpec] = field(default_factory=list)
    #: mode "tools": "auto" (tool call or text), "required" (any tool) or a tool name.
    tool_choice: str = "auto"

    @property
    def structured(self) -> bool:
        return self.mode in ("json_schema", "json_object", "tool", "tools")

    def offered_tools(self) -> list[ToolSpec]:
        """The tools the answer may call (only the named one for a specific choice)."""
        if self.tool_choice in ("auto", "required"):
            return list(self.tools)
        return [t for t in self.tools if t.name == self.tool_choice]

    def system_text(self) -> str:
        text = "\n\n".join(m.content for m in self.messages if m.role == "system" and m.content)
        if self.mode == "tools":
            text = "\n\n".join(filter(None, [text, tool_instructions(self)]))
        return text

    def conversation(self) -> list[ChatMessage]:
        return [m for m in self.messages if m.role != "system"]

    def transcript(self) -> str:
        """User/assistant turns as one prompt. A single user turn is sent verbatim."""
        turns = self.conversation()
        if len(turns) == 1 and turns[0].role == "user":
            return turns[0].content
        labels = {"user": "User", "assistant": "Assistant", "tool": "Tool result"}
        return "\n\n".join(f"{labels.get(m.role, m.role.title())}:\n{m.content}" for m in turns)

    def with_extra_user(self, text: str) -> ChatRequest:
        return replace(self, messages=[*self.messages, ChatMessage("user", text)])


@dataclass
class ChatResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    #: Seconds the provider call took (including a schema re-ask).
    duration_s: float = 0.0
    #: "length" when the answer hit the caller's max_tokens (OpenAI semantics), else "".
    finish_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _text(content: Any) -> str:
    """Message content: a string or a list of parts (only text parts are kept)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict):
                raise ProviderError(
                    400,
                    "invalid_request",
                    f"unsupported content part {part.get('type')!r} (text only)",
                )
        return "\n".join(parts)
    raise ProviderError(400, "invalid_request", "message content must be a string or parts")


def _arguments_text(raw: Any) -> str:
    """A tool call's arguments as compact-ish JSON text (a string is kept as sent)."""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw if raw is not None else {}, ensure_ascii=False)


def _message(raw: Any, call_names: dict[str, str]) -> ChatMessage:
    """One request message. Assistant tool calls and tool results are rendered as text
    (the CLIs take a transcript); *call_names* maps tool_call ids seen so far to names."""
    if not isinstance(raw, dict) or not isinstance(raw.get("role"), str):
        raise ProviderError(400, "invalid_request", "each message needs a role")
    role = raw["role"]
    if role == "developer":
        role = "system"
    if role not in ("system", "user", "assistant", "tool"):
        raise ProviderError(400, "invalid_request", f"unsupported message role {role!r}")
    text = _text(raw.get("content"))
    calls = raw.get("tool_calls") or []
    if role == "assistant" and isinstance(calls, list) and calls:
        rendered = []
        for c in calls:
            if not isinstance(c, dict):
                continue
            fn = c.get("function") or {}
            name = str(fn.get("name") or "tool")
            call_id = str(c.get("id") or "")
            if call_id:
                call_names[call_id] = name
            rendered.append(
                f"Assistant called tool {name} with {_arguments_text(fn.get('arguments'))}"
                + (f" (id {call_id})" if call_id else "")
            )
        text = "\n".join(filter(None, [text, *rendered]))
    if role == "tool":
        call_id = str(raw.get("tool_call_id") or "")
        name = str(raw.get("name") or call_names.get(call_id) or "tool")
        head = f"Tool {name}" + (f" (id {call_id})" if call_id else "") + " returned:"
        text = f"{head}\n{_clip(text, MAX_TOOL_OUTPUT_CHARS)}"
    return ChatMessage(role, text)


def _clip(text: str, limit: int) -> str:
    """*text* cut to about *limit* characters, keeping the head and the tail."""
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    head = limit * 2 // 3
    return (
        f"{text[:head]}\n[... {cut} characters truncated ...]\n{text[len(text) - (limit - head) :]}"
    )


def _bound_history(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Shrink the oldest tool results until the non-system turns fit
    :data:`MAX_HISTORY_CHARS`. System messages and other turns are never touched."""
    total = sum(len(m.content) for m in messages if m.role != "system")
    if total <= MAX_HISTORY_CHARS:
        return messages
    out = list(messages)
    tool_turns = [i for i, m in enumerate(out) if m.role == "tool"]
    for i in tool_turns[:-1]:  # the newest result stays whole
        if total <= MAX_HISTORY_CHARS:
            break
        head, _, body = out[i].content.partition("\n")
        shrunk = f"{head}\n{_clip(body, SHRUNK_TOOL_OUTPUT_CHARS)}"
        total -= len(out[i].content) - len(shrunk)
        out[i] = ChatMessage("tool", shrunk)
    return out


def _tool_specs(raw_tools: list[dict[str, Any]]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for tool in raw_tools:
        fn = tool.get("function") or {}
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise ProviderError(400, "invalid_request", "tool function needs a name")
        params = fn.get("parameters") or {"type": "object"}
        if not isinstance(params, dict):
            raise ProviderError(400, "invalid_schema", "tool parameters must be an object")
        specs.append(ToolSpec(fn["name"], str(fn.get("description") or ""), params))
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise ProviderError(400, "invalid_request", "tool names must be unique")
    return specs


def _tool_choice(raw: Any, names: list[str]) -> str:
    """``tool_choice`` -> "none" | "auto" | "required" | a tool name (absent = "")."""
    if raw is None:
        return ""
    if raw in ("none", "auto", "required"):
        return raw
    if raw == "any":  # Anthropic's spelling of "required"
        return "required"
    if isinstance(raw, dict):
        fn = raw.get("function") if raw.get("type") == "function" else None
        name = (fn or {}).get("name") if isinstance(fn, dict) else raw.get("name")
        if isinstance(name, str):
            if name not in names:
                raise ProviderError(
                    400, "invalid_request", f"tool_choice names an unknown tool {name[:80]!r}"
                )
            return name
    raise ProviderError(
        400,
        "invalid_request",
        'tool_choice must be "none", "auto", "required" or {"type": "function", ...}',
    )


def tool_envelope_schema(request: ChatRequest) -> dict[str, Any]:
    """The JSON the CLI answers in tool mode. A flat discriminated object rather than a
    top-level ``oneOf`` (claude's --json-schema becomes a tool input schema, which must be
    a plain object); the per-tool arguments are checked by :func:`tool_answer_problem`."""
    kinds = ["tool_call"] if request.tool_choice != "auto" else ["tool_call", "text"]
    props: dict[str, Any] = {
        "type": {"type": "string", "enum": kinds},
        "name": {"type": "string", "enum": [t.name for t in request.offered_tools()]},
        "arguments": {"type": "object"},
    }
    if "text" in kinds:
        props["content"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": ["type"]}


def tool_instructions(request: ChatRequest) -> str:
    """How to answer in tool mode, with each offered tool's name, description and
    parameters schema (appended to the caller's system prompt)."""
    lines = [
        "# Tools",
        "You can call the tools below. Reply with exactly one JSON object and nothing else:",
        '- to call a tool: {"type": "tool_call", "name": "<tool name>", "arguments": '
        "{<arguments matching that tool's parameters schema>}}",
    ]
    if request.tool_choice == "auto":
        lines.append('- to answer without a tool: {"type": "text", "content": "<your answer>"}')
    elif request.tool_choice == "required":
        lines.append("You must call one of the tools now; a text answer is not accepted.")
    else:
        lines.append(f"You must call the tool {request.tool_choice} now.")
    lines.append(
        "Call one tool per reply; its result comes back in the next message. Earlier calls "
        'appear in the conversation as "Assistant called tool <name> with <arguments>" and '
        'their results as "Tool <name> returned: ...".'
    )
    for tool in request.offered_tools():
        lines += [
            "",
            f"## {tool.name}",
            tool.description or "(no description)",
            "Parameters (JSON Schema): " + json.dumps(tool.parameters, ensure_ascii=False),
        ]
    return "\n".join(lines)


def tool_answer_problem(value: Any, request: ChatRequest) -> str | None:
    """Why *value* (the parsed envelope) is not an acceptable answer (None = fine).
    Arguments are validated against the tool's parameters schema in the terminable
    schema worker (:func:`base.schema_problem`). Blocking."""
    from autoontology.web.providers.base import schema_problem

    if not isinstance(value, dict):
        return "the answer must be a JSON object"
    kind = value.get("type")
    allowed = [t.name for t in request.offered_tools()]
    if kind == "text":
        if request.tool_choice != "auto":
            return "a text answer is not accepted here: call one of the tools " + ", ".join(allowed)
        if not isinstance(value.get("content"), str):
            return 'a text answer needs a string "content"'
        return None
    if kind != "tool_call":
        return '"type" must be "tool_call"' + (
            ' or "text"' if request.tool_choice == "auto" else ""
        )
    name = value.get("name")
    spec = next((t for t in request.offered_tools() if t.name == name), None)
    if spec is None:
        return f"unknown tool {str(name)[:80]!r}; the tools are: " + ", ".join(allowed)
    arguments = value.get("arguments", {})
    if not isinstance(arguments, dict):
        return f'"arguments" for tool {spec.name} must be a JSON object'
    problem = schema_problem(arguments, spec.parameters)
    if problem is not None:
        return f"the arguments for tool {spec.name} do not match its parameters: {problem}"
    return None


def parse_request(body: Any, model: str) -> ChatRequest:
    """Request body (already JSON-decoded) -> :class:`ChatRequest` for *model* (the name
    without its router prefix)."""
    if not isinstance(body, dict):
        raise ProviderError(400, "invalid_request", "request body must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProviderError(400, "invalid_request", "messages must be a non-empty list")
    call_names: dict[str, str] = {}
    req = ChatRequest(
        model=model,
        messages=_bound_history([_message(m, call_names) for m in messages]),
        stream=bool(body.get("stream")),
        max_tokens=_max_tokens(body),
    )
    specs = _tool_specs([t for t in body.get("tools") or [] if isinstance(t, dict)])
    choice = _tool_choice(body.get("tool_choice"), [t.name for t in specs]) if specs else ""
    fmt = body.get("response_format") or {}
    if specs and choice == "none":
        specs = []  # tools offered but not to be called: a plain (or response_format) call
    if len(specs) == 1 and choice != "auto":
        # one tool, absent or forcing tool_choice: forced structured output (LangChain's
        # with_structured_output(method="function_calling"))
        req.schema, req.schema_name, req.mode = specs[0].parameters, specs[0].name, "tool"
    elif specs:
        req.mode, req.tools, req.tool_choice = "tools", specs, choice or "auto"
        req.schema_name = "tool_call"
        from autoontology.web.providers.base import check_schema

        for spec in specs:  # every tool schema: 400 before any provider call
            check_schema(spec.parameters)
        req.schema = tool_envelope_schema(req)
    elif isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        spec = fmt.get("json_schema") or {}
        if not isinstance(spec.get("schema"), dict):
            raise ProviderError(400, "invalid_request", "json_schema.schema must be an object")
        req.schema, req.mode = spec["schema"], "json_schema"
        req.schema_name = str(spec.get("name") or "response")
    elif isinstance(fmt, dict) and fmt.get("type") == "json_object":
        req.schema, req.mode = {"type": "object"}, "json_object"
    if req.schema is not None:
        from autoontology.web.providers.base import check_schema

        check_schema(req.schema)  # before any provider call: 400, never "valid"
    return req


def _max_tokens(body: dict[str, Any]) -> int | None:
    """The output budget (``max_tokens`` / ``max_completion_tokens``; the smaller if both).
    Never silently dropped."""
    values = []
    for key in ("max_completion_tokens", "max_tokens"):
        value = body.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProviderError(400, "invalid_request", f"{key} must be a positive integer")
        values.append(value)
    return min(values) if values else None


def _message_out(req: ChatRequest, resp: ChatResponse) -> tuple[dict[str, Any], str]:
    if req.mode == "tools":
        try:
            value = json.loads(resp.content)
        except ValueError:
            value = None  # an answer cut at max_tokens ("length"): returned as text
        if isinstance(value, dict) and value.get("type") == "tool_call":
            call = {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": str(value.get("name")),
                    "arguments": json.dumps(value.get("arguments") or {}, ensure_ascii=False),
                },
            }
            return (
                {"role": "assistant", "content": None, "tool_calls": [call]},
                resp.finish_reason or "tool_calls",
            )
        if isinstance(value, dict) and value.get("type") == "text":
            return (
                {"role": "assistant", "content": str(value.get("content") or "")},
                resp.finish_reason or "stop",
            )
        return {"role": "assistant", "content": resp.content}, resp.finish_reason or "stop"
    if req.mode == "tool":
        call = {
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": req.schema_name, "arguments": resp.content},
        }
        return (
            {"role": "assistant", "content": None, "tool_calls": [call]},
            resp.finish_reason or "tool_calls",
        )
    return {"role": "assistant", "content": resp.content}, resp.finish_reason or "stop"


def _usage(resp: ChatResponse) -> dict[str, Any]:
    return {
        "prompt_tokens": resp.prompt_tokens,
        "completion_tokens": resp.completion_tokens,
        "total_tokens": resp.prompt_tokens + resp.completion_tokens,
    }


def to_openai(req: ChatRequest, resp: ChatResponse, model_name: str) -> dict[str, Any]:
    """Non-streaming ``chat.completion`` body. *model_name* = what the caller asked for."""
    message, finish = _message_out(req, resp)
    out: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage(resp),
    }
    if resp.cost_usd is not None:
        out["autoontology_cost_usd"] = resp.cost_usd
    return out


def to_sse(req: ChatRequest, resp: ChatResponse, model_name: str) -> bytes:
    """``stream: true`` shim: the whole answer in one chunk, then ``[DONE]``."""
    message, finish = _message_out(req, resp)
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
    }
    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": 0, **c} for c in message["tool_calls"]]
    else:
        delta["content"] = message["content"]
    first = {
        **base,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": _usage(resp),
    }
    return (f"data: {json.dumps(first, ensure_ascii=False)}\n\ndata: [DONE]\n\n").encode("utf-8")
