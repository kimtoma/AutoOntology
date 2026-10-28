"""OpenAI chat-completions wire format <-> :class:`ChatRequest`/:class:`ChatResponse`.

Upstream (LangChain ``ChatOpenAI``) asks for structured output in one of two ways:

* ``response_format: {type: json_schema, json_schema: {name, schema, strict}}``
  (LangChain's default) -> the answer is ``choices[0].message.content`` (JSON text);
* ``tools: [{type: function, function: {name, parameters}}]`` (upstream forces
  ``function_calling`` for some model names) -> the answer is one ``tool_calls`` entry
  whose ``arguments`` is the JSON.

The passthrough providers forward bodies verbatim; these types serve ``doctor``'s probe
calls and the router's error bodies.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


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

    @property
    def structured(self) -> bool:
        return self.mode in ("json_schema", "json_object", "tool")

    def system_text(self) -> str:
        return "\n\n".join(m.content for m in self.messages if m.role == "system" and m.content)

    def conversation(self) -> list[ChatMessage]:
        return [m for m in self.messages if m.role != "system"]

    def transcript(self) -> str:
        """User/assistant turns as one prompt. A single user turn is sent verbatim."""
        turns = self.conversation()
        if len(turns) == 1 and turns[0].role == "user":
            return turns[0].content
        labels = {"user": "User", "assistant": "Assistant", "tool": "Tool result"}
        return "\n\n".join(f"{labels.get(m.role, m.role.title())}:\n{m.content}"
                           for m in turns)

    def with_extra_user(self, text: str) -> ChatRequest:
        return ChatRequest(model=self.model, messages=[*self.messages, ChatMessage("user", text)],
                           schema=self.schema, schema_name=self.schema_name, mode=self.mode,
                           stream=self.stream, max_tokens=self.max_tokens)


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
                raise ProviderError(400, "invalid_request",
                                    f"unsupported content part {part.get('type')!r} (text only)")
        return "\n".join(parts)
    raise ProviderError(400, "invalid_request", "message content must be a string or parts")


def _message(raw: Any) -> ChatMessage:
    if not isinstance(raw, dict) or not isinstance(raw.get("role"), str):
        raise ProviderError(400, "invalid_request", "each message needs a role")
    role = raw["role"]
    if role == "developer":
        role = "system"
    text = _text(raw.get("content"))
    calls = raw.get("tool_calls") or []
    if role == "assistant" and calls:
        rendered = [f"{(c.get('function') or {}).get('name', 'tool')}("
                    f"{(c.get('function') or {}).get('arguments', '')})"
                    for c in calls if isinstance(c, dict)]
        text = "\n".join(filter(None, [text, *rendered]))
    if role not in ("system", "user", "assistant", "tool"):
        raise ProviderError(400, "invalid_request", f"unsupported message role {role!r}")
    return ChatMessage(role, text)


def parse_request(body: Any, model: str) -> ChatRequest:
    """Request body (already JSON-decoded) -> :class:`ChatRequest` for *model* (the name
    without its router prefix)."""
    if not isinstance(body, dict):
        raise ProviderError(400, "invalid_request", "request body must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProviderError(400, "invalid_request", "messages must be a non-empty list")
    req = ChatRequest(model=model, messages=[_message(m) for m in messages],
                      stream=bool(body.get("stream")), max_tokens=_max_tokens(body))
    tools = [t for t in body.get("tools") or [] if isinstance(t, dict)]
    fmt = body.get("response_format") or {}
    if tools:
        if len(tools) != 1:
            raise ProviderError(400, "unsupported", "only one tool is accepted "
                                "(structured output); got " + str(len(tools)))
        fn = tools[0].get("function") or {}
        if not isinstance(fn.get("name"), str):
            raise ProviderError(400, "invalid_request", "tool function needs a name")
        req.schema = fn.get("parameters") or {"type": "object"}
        req.schema_name, req.mode = fn["name"], "tool"
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
    if req.mode == "tool":
        call = {"id": f"call_{uuid.uuid4().hex[:24]}", "type": "function",
                "function": {"name": req.schema_name, "arguments": resp.content}}
        return ({"role": "assistant", "content": None, "tool_calls": [call]},
                resp.finish_reason or "tool_calls")
    return {"role": "assistant", "content": resp.content}, resp.finish_reason or "stop"


def _usage(resp: ChatResponse) -> dict[str, Any]:
    return {"prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
            "total_tokens": resp.prompt_tokens + resp.completion_tokens}


def to_openai(req: ChatRequest, resp: ChatResponse, model_name: str) -> dict[str, Any]:
    """Non-streaming ``chat.completion`` body. *model_name* = what the caller asked for."""
    message, finish = _message_out(req, resp)
    out: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
        "created": int(time.time()), "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage(resp),
    }
    if resp.cost_usd is not None:
        out["autoontology_cost_usd"] = resp.cost_usd
    return out


def to_sse(req: ChatRequest, resp: ChatResponse, model_name: str) -> bytes:
    """``stream: true`` shim: the whole answer in one chunk, then ``[DONE]``."""
    message, finish = _message_out(req, resp)
    base = {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model_name}
    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": 0, **c} for c in message["tool_calls"]]
    else:
        delta["content"] = message["content"]
    first = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
             "usage": _usage(resp)}
    return (f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            "data: [DONE]\n\n").encode("utf-8")
