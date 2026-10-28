"""Provider protocol and the schema helpers shared by the chat providers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from autoontology.web.providers.openai_compat import ChatRequest, ChatResponse, ProviderError


@dataclass
class ProviderStatus:
    name: str
    #: The endpoint exists and answers.
    available: bool
    #: Credentials accepted (always True for Ollama, which needs none).
    logged_in: bool
    detail: str = ""
    remedy: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.available and self.logged_in

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "available": self.available,
                "logged_in": self.logged_in, "detail": self.detail, "remedy": self.remedy,
                "models": list(self.models)}


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse: ...

    def status(self) -> ProviderStatus: ...

    def models(self) -> list[str]: ...


def parse_json_answer(text: str) -> Any:
    """The model's JSON answer (```json fences tolerated). Raises ValueError."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z0-9]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return json.loads(t)


#: Bounds of a caller-supplied JSON schema (it is validated in the service process).
MAX_SCHEMA_BYTES = 128 * 1024
MAX_SCHEMA_DEPTH = 64
MAX_SCHEMA_NODES = 20_000
_REF_KEYS = ("$ref", "$dynamicRef", "$recursiveRef")


def _walk_schema(schema: Any) -> None:
    stack: list[tuple[Any, int]] = [(schema, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > MAX_SCHEMA_DEPTH:
            raise ProviderError(400, "invalid_schema",
                                f"the JSON schema nests deeper than {MAX_SCHEMA_DEPTH} levels")
        if nodes > MAX_SCHEMA_NODES:
            raise ProviderError(400, "invalid_schema",
                                f"the JSON schema has more than {MAX_SCHEMA_NODES} nodes")
        if isinstance(node, dict):
            for key in _REF_KEYS:
                ref = node.get(key)
                if ref is not None and not (isinstance(ref, str) and ref.startswith("#")):
                    raise ProviderError(400, "invalid_schema",
                                        f"{key} {str(ref)[:120]!r} is not allowed: only local "
                                        "references into the schema itself ('#...')")
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)


def check_schema(schema: Any) -> None:
    """Refuse (400 ``invalid_schema``) a caller schema that is not a valid JSON Schema, is
    too large/deep, or references anything outside itself. Called before any provider call,
    so a bad schema never costs a CLI call nor reads files/URLs in the service."""
    import jsonschema

    if not isinstance(schema, dict):
        raise ProviderError(400, "invalid_schema", "the JSON schema must be an object")
    size = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    if size > MAX_SCHEMA_BYTES:
        raise ProviderError(400, "invalid_schema",
                            f"the JSON schema is {size} bytes (max {MAX_SCHEMA_BYTES})")
    _walk_schema(schema)
    _refuse_cycles(schema)
    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    try:
        cls.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ProviderError(400, "invalid_schema",
                            f"not a valid JSON schema: {exc.message[:300]}") from None


def schema_problem(value: Any, schema: dict[str, Any]) -> str | None:
    """First validation error of *value* against *schema* (None = valid). An invalid or
    unsafe schema raises :class:`ProviderError` (400): it never counts as "valid".

    Only AutoOntology's own fixed schemas reach this (the playground's RAG answer), so
    validation runs in-process."""
    import jsonschema

    check_schema(schema)
    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    try:
        error = jsonschema.exceptions.best_match(cls(schema).iter_errors(value))
    except RecursionError:
        raise ProviderError(400, "schema_too_complex",
                            "the JSON schema recurses too deeply for this answer") from None
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path)
    return f"{where or '<root>'}: {error.message[:300]}"


_IN_PLACE_LISTS = ("allOf", "anyOf", "oneOf")
_IN_PLACE_ONE = ("not", "if", "then", "else")


def _refuse_cycles(schema: dict[str, Any]) -> None:
    """Refuse local references that loop without consuming any part of the instance
    (``{"$ref": "#"}``, a ``$defs`` ring through ``allOf``/``anyOf``...): they recurse until
    RecursionError. A reference reached through ``properties``/``items``/... is fine (the
    instance is finite), e.g. a tree schema."""
    nodes: dict[tuple, dict] = {}
    resource_of: dict[tuple, tuple] = {}
    anchors: dict[tuple[tuple, str], tuple] = {}
    known: set[tuple] = set()
    stack: list[tuple[tuple, Any, tuple]] = [((), schema, ())]
    while stack:
        path, node, resource = stack.pop()
        known.add(path)
        if isinstance(node, dict):
            if isinstance(node.get("$id"), str) and path:
                resource = path
            nodes[path] = node
            resource_of[path] = resource
            for key in ("$anchor", "$dynamicAnchor"):
                if isinstance(node.get(key), str):
                    anchors[(resource, node[key])] = path
            for k, v in node.items():
                stack.append(((*path, k), v, resource))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append(((*path, i), v, resource))

    def resolve(path: tuple, ref: str) -> tuple | None:
        resource = resource_of[path]
        fragment = ref[1:]
        if not fragment:
            return resource
        if fragment.startswith("/"):
            from urllib.parse import unquote

            target: tuple = resource
            for part in fragment[1:].split("/"):
                part = unquote(part).replace("~1", "/").replace("~0", "~")
                if (*target, part) in known:
                    target = (*target, part)
                elif part.isdigit() and (*target, int(part)) in known:
                    target = (*target, int(part))
                else:
                    return None  # unresolvable: validation reports it
            return target if target in nodes else None
        return anchors.get((resource, fragment))

    def edges(path: tuple) -> list[tuple]:
        node = nodes[path]
        out: list[tuple] = []
        for k in _IN_PLACE_LISTS:
            if isinstance(node.get(k), list):
                out += [(*path, k, i) for i in range(len(node[k]))]
        out += [(*path, k) for k in _IN_PLACE_ONE if isinstance(node.get(k), dict)]
        if isinstance(node.get("dependentSchemas"), dict):
            out += [(*path, "dependentSchemas", k) for k in node["dependentSchemas"]]
        for k in _REF_KEYS:
            if isinstance(node.get(k), str):
                target = resolve(path, node[k])
                if target is not None:
                    out.append(target)
        return [p for p in out if p in nodes]

    state: dict[tuple, int] = {}  # 1 = on the DFS stack, 2 = done
    for start in nodes:
        if state.get(start):
            continue
        work = [(start, iter(edges(start)))]
        state[start] = 1
        while work:
            path, it = work[-1]
            nxt = next(it, None)
            if nxt is None:
                state[path] = 2
                work.pop()
            elif state.get(nxt) == 1:
                raise ProviderError(400, "invalid_schema",
                                    "the JSON schema has a cyclic reference that consumes no "
                                    f"part of the answer (at #/{'/'.join(map(str, nxt))})")
            elif not state.get(nxt):
                state[nxt] = 1
                work.append((nxt, iter(edges(nxt))))
