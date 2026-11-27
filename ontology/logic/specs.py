# SPDX-License-Identifier: MIT
"""Function, action and workflow specs: their schema (pydantic), loading from YAML or
JSON, and :func:`validate` against the project's ontology (fail-closed).

A spec is a mapping with ``kind: function | action | workflow``::

    kind: function
    name: 미수금
    object_type: invoices          # an object type: its table, label or class name (term-...)
    returns: number               # number | text | bool | date
    expr: 청구금액 - coalesce(수납금액, 0)

Actions (``ActionSpec``) and workflows (``WorkflowSpec``) are fixed here too, so the
action runner and the workflow scheduler implement behaviour against one schema.
Message templates (``subject``/``body`` of a notification) embed expressions as
``{{ expr }}``.

:func:`validate` returns structured errors ``[{path, code, message, pos?}]`` -- empty
when the spec may be saved: the object type is an active one, every expression parses
and type-checks against its fields and links, referenced functions / actions exist (not
retired), parameters line up, and no function calls itself through others.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from ontology.logic import expr as ex
from ontology.logic.schema import Schema, TypeInfo
from ontology.objects import naming

KINDS: tuple[str, ...] = ("function", "action", "workflow")
ROLES: tuple[str, ...] = ("viewer", "reviewer", "editor", "owner")
#: a spec document (YAML / JSON text) is at most this long
MAX_SPEC_CHARS = 64_000
#: specs in one document (a YAML list or several ``---`` documents)
MAX_SPECS_PER_DOCUMENT = 50
MAX_STEPS = 50
MAX_STEP_DEPTH = 5
MAX_WORKFLOW_OBJECTS = 5000
NAME_RE = re.compile(r"^[^\W\d]\w{0,63}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
SCHEDULE_RE = re.compile(
    r"^(?:daily (?P<dh>[01]\d|2[0-3]):(?P<dm>[0-5]\d)"
    r"|hourly"
    r"|weekly (?P<wd>MON|TUE|WED|THU|FRI|SAT|SUN) (?P<wh>[01]\d|2[0-3]):(?P<wm>[0-5]\d))$"
)
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
RESERVED_NAMES = frozenset({*ex.KEYWORDS, *ex.BUILTINS, "param", "fn", "if"})


def _expr_text(value: Any) -> Any:
    """YAML turns ``expr: 30`` or ``if: true`` into numbers / bools: expressions are text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return value


Expr = Annotated[
    str,
    BeforeValidator(_expr_text),
    StringConstraints(min_length=1, max_length=ex.MAX_SOURCE_CHARS),
]
Name = Annotated[str, StringConstraints(pattern=NAME_RE.pattern)]
Label = Annotated[str, StringConstraints(max_length=200)]
Description = Annotated[str, StringConstraints(max_length=4000)]
TypeRef = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ValueType = Literal["number", "text", "bool", "date"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ParamSpec(_M):
    name: Name
    type: ValueType = "text"
    required: bool = True
    enum: list[Any] | None = Field(None, max_length=100)
    default: Any = None
    label: Label = ""


class FunctionSpec(_M):
    kind: Literal["function"] = "function"
    name: Name
    label: Label = ""
    description: Description = ""
    object_type: TypeRef
    returns: ValueType
    expr: Expr
    params: list[ParamSpec] = Field(default_factory=list, max_length=20)


class Precondition(_M):
    expr: Expr
    message: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class Effect(_M):
    #: the property to write: a column (name or label) or, with ``operational``, a
    #: property of the edit layer only
    set: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    value: Expr
    operational: bool = False


def _listify(value: Any) -> Any:
    return [value] if isinstance(value, str) else value


class NotifySpec(_M):
    #: ``role:<viewer|reviewer|editor|owner>``, ``email:<address>`` or ``field:<expr>``
    to: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=500)]],
        BeforeValidator(_listify),
    ] = Field(  # noqa: E501
        min_length=1, max_length=20
    )
    subject: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    body: Annotated[str, StringConstraints(max_length=8000)] = ""


class Permission(_M):
    min_role: Literal["viewer", "reviewer", "editor", "owner"] = "editor"


class Approval(_M):
    role: Literal["none", "reviewer", "owner"] = "none"
    #: approval is needed only when this is true (default: always, when role is set)
    when: Expr | None = None


class ActionSpec(_M):
    kind: Literal["action"] = "action"
    name: Name
    label: Label
    description: Description = ""
    object_type: TypeRef
    params: list[ParamSpec] = Field(default_factory=list, max_length=20)
    preconditions: list[Precondition] = Field(default_factory=list, max_length=20)
    effects: list[Effect] = Field(default_factory=list, max_length=20)
    notify: list[NotifySpec] = Field(default_factory=list, max_length=10)
    permission: Permission = Field(default_factory=Permission)
    approval: Approval = Field(default_factory=Approval)
    agent: Literal["none", "propose", "execute"] = "none"

    @model_validator(mode="after")
    def _does_something(self) -> ActionSpec:
        if not self.effects and not self.notify:
            raise ValueError("an action needs at least one effect or notification")
        return self


class Trigger(_M):
    schedule: Annotated[str, StringConstraints(max_length=40)] | None = None
    on_action: Name | None = None
    manual: bool | None = None

    @model_validator(mode="after")
    def _one(self) -> Trigger:
        given = [
            k
            for k, v in (
                ("schedule", self.schedule),
                ("on_action", self.on_action),
                ("manual", self.manual),
            )
            if v not in (None, False)
        ]
        if len(given) != 1:
            raise ValueError("a trigger is exactly one of schedule, on_action, manual: true")
        if self.schedule is not None and not SCHEDULE_RE.match(self.schedule):
            raise ValueError("schedule is 'daily HH:MM', 'hourly' or 'weekly MON HH:MM' (MON..SUN)")
        return self


class ForEach(_M):
    object_type: TypeRef
    where: Expr | None = None
    limit: int | None = Field(None, ge=1, le=MAX_WORKFLOW_OBJECTS)


class StepSpec(_M):
    if_: Expr | None = Field(None, alias="if")
    then: list[StepSpec] = Field(default_factory=list, max_length=MAX_STEPS)
    else_: list[StepSpec] = Field(default_factory=list, alias="else", max_length=MAX_STEPS)
    action: Name | None = None
    params: dict[str, Expr] = Field(default_factory=dict, max_length=20)
    notify: NotifySpec | None = None

    @model_validator(mode="after")
    def _one(self) -> StepSpec:
        given = [
            k for k, v in (("if", self.if_), ("action", self.action), ("notify", self.notify)) if v
        ]
        if len(given) != 1:
            raise ValueError("a step is exactly one of if, action, notify")
        if (self.then or self.else_) and self.if_ is None:
            raise ValueError("then/else belong to an if step")
        if self.if_ is not None and not self.then:
            raise ValueError("an if step needs then")
        if self.params and self.action is None:
            raise ValueError("params belong to an action step")
        return self


class WorkflowSpec(_M):
    kind: Literal["workflow"] = "workflow"
    name: Name
    label: Label
    description: Description = ""
    trigger: Trigger
    for_each: ForEach
    steps: list[StepSpec] = Field(min_length=1, max_length=MAX_STEPS)
    max_objects: int = Field(500, ge=1, le=MAX_WORKFLOW_OBJECTS)


Spec = FunctionSpec | ActionSpec | WorkflowSpec
MODELS: dict[str, type[BaseModel]] = {
    "function": FunctionSpec,
    "action": ActionSpec,
    "workflow": WorkflowSpec,
}


def dump(spec: Spec) -> dict[str, Any]:
    """*spec* as the JSON stored (``if``/``else`` under their own names, no unset
    optionals)."""
    return spec.model_dump(mode="json", by_alias=True, exclude_none=True)


def parse_schedule(text: str) -> dict[str, Any]:
    """``{every: daily|hourly|weekly, weekday: 0-6 | None, hour, minute}``."""
    m = SCHEDULE_RE.match(text or "")
    if m is None:
        raise ValueError(f"invalid schedule {text!r}")
    if text == "hourly":
        return {"every": "hourly", "weekday": None, "hour": None, "minute": 0}
    if m["dh"] is not None:
        return {"every": "daily", "weekday": None, "hour": int(m["dh"]), "minute": int(m["dm"])}
    return {
        "every": "weekly",
        "weekday": WEEKDAYS.index(m["wd"]),
        "hour": int(m["wh"]),
        "minute": int(m["wm"]),
    }


def template_exprs(text: str) -> list[tuple[int, str]]:
    """``(offset, expression)`` of every ``{{ expr }}`` in a message template."""
    return [(m.start(1), m.group(1).strip()) for m in TEMPLATE_RE.finditer(text or "")]


# --- loading ----------------------------------------------------------------------------


class SpecError(Exception):
    """A spec that cannot be read or does not fit the schema: ``errors`` lists why."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors[:5]))
        self.errors = errors


def _err(path: str, code: str, message: str, pos: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"path": path, "code": code, "message": message}
    if pos is not None:
        out["pos"] = pos
    return out


class _Loader(yaml.SafeLoader):
    """``yaml.SafeLoader`` without aliases (no "billion laughs") and with dates kept as
    text."""

    def compose_node(self, parent: Any, index: Any) -> Any:  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "aliases (*name) are not allowed", event.start_mark
            )
        return super().compose_node(parent, index)


_Loader.add_constructor(
    "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
)


def load_documents(text: str) -> list[dict[str, Any]]:
    """The spec mappings in *text* (JSON, or YAML: one mapping, a list of them or several
    ``---`` documents). :class:`SpecError` when it cannot be read."""
    if not isinstance(text, str) or not text.strip():
        raise SpecError([_err("", "empty", "the spec is empty")])
    if len(text) > MAX_SPEC_CHARS:
        raise SpecError(
            [_err("", "too_long", f"a spec document is at most {MAX_SPEC_CHARS} characters")]
        )
    stripped = text.lstrip()
    try:
        if stripped[:1] in "{[":
            try:
                docs: list[Any] = [json.loads(text)]
            except ValueError:
                docs = list(yaml.load_all(text, Loader=_Loader))  # noqa: S506 - safe loader
        else:
            docs = list(yaml.load_all(text, Loader=_Loader))  # noqa: S506 - safe loader
    except (yaml.YAMLError, RecursionError) as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise SpecError(
            [_err("", "invalid_yaml", f"cannot read the spec{where}: {problem}")]
        ) from None
    out: list[Any] = []
    for doc in docs:
        if doc is None:
            continue
        out.extend(doc if isinstance(doc, list) else [doc])
    if not out:
        raise SpecError([_err("", "empty", "the spec is empty")])
    if len(out) > MAX_SPECS_PER_DOCUMENT:
        raise SpecError([_err("", "too_many", f"at most {MAX_SPECS_PER_DOCUMENT} specs at once")])
    for i, item in enumerate(out):
        if not isinstance(item, dict):
            raise SpecError([_err(f"[{i}]", "not_a_mapping", "a spec is a mapping (key: value)")])
    return out


def _loc(loc: tuple[Any, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += ("." if path else "") + str(part)
    return path


def parse_spec(data: Any) -> Spec:
    """One spec mapping as its model; :class:`SpecError` with every schema error."""
    if isinstance(data, str):
        docs = load_documents(data)
        if len(docs) != 1:
            raise SpecError([_err("", "too_many", "give one spec here")])
        data = docs[0]
    if not isinstance(data, dict):
        raise SpecError([_err("", "not_a_mapping", "a spec is a mapping (key: value)")])
    kind = data.get("kind")
    if kind not in KINDS:
        raise SpecError(
            [_err("kind", "invalid_kind", f"kind must be one of {', '.join(KINDS)}, got {kind!r}")]
        )
    try:
        return MODELS[kind].model_validate(data)  # type: ignore[return-value]
    except ValidationError as exc:
        errors = []
        for e in exc.errors():
            errors.append(
                _err(_loc(tuple(e["loc"])), "schema", e["msg"].removeprefix("Value error, "))
            )
        raise SpecError(errors) from None


# --- validation against the ontology ------------------------------------------------------


@dataclass
class LogicContext:
    """What specs are validated against: the object types and the other (non-retired)
    definitions, with their statuses."""

    schema: Schema
    functions: dict[str, FunctionSpec] = field(default_factory=dict)
    actions: dict[str, ActionSpec] = field(default_factory=dict)
    workflows: dict[str, WorkflowSpec] = field(default_factory=dict)
    statuses: dict[tuple[str, str], str] = field(default_factory=dict)

    def with_spec(self, spec: Spec) -> LogicContext:
        """This context with *spec* in place of the stored definition of its name."""
        out = replace(
            self,
            functions=dict(self.functions),
            actions=dict(self.actions),
            workflows=dict(self.workflows),
        )
        getattr(out, spec.kind + "s")[spec.name] = spec
        return out

    def active_type(self, ref: str) -> TypeInfo | None:
        t = self.schema.resolve(ref)
        return t if t is not None and t.status == "active" else None

    def type_of(self, ref: str) -> TypeInfo | None:
        """The active object type *ref* names, with the operational properties actions
        declare on it as fields of type ``any``."""
        t = self.active_type(ref)
        if t is None:
            return None
        extra: dict[str, str] = {}
        for action in self.actions.values():
            if self.schema.resolve(action.object_type) is not t:
                continue
            for effect in action.effects:
                if effect.operational and t.field(effect.set) is None:
                    extra.setdefault(effect.set, "any")
        if not extra:
            return t
        return replace(t, operational={**t.operational, **extra})

    def signature(self, name: str) -> ex.FunctionSig | None:
        spec = self.functions.get(name)
        if spec is None:
            return None
        t = self.schema.resolve(spec.object_type)
        if t is None:
            return None
        return ex.FunctionSig(name, t.name, spec.returns, [(p.name, p.type) for p in spec.params])

    def scope(self, t: TypeInfo, params: list[ParamSpec] | None = None) -> ex.Scope:
        return ex.Scope(self.schema, t, {p.name: p.type for p in params or []}, self.signature)


def _type_error(path: str, ref: str, ctx: LogicContext) -> dict[str, Any]:
    t = ctx.schema.resolve(ref)
    if t is None and len(found := ctx.schema.candidates(ref)) > 1:
        names = ", ".join(
            f"{x.label} ({naming.describe(x.names())['table'] or x.name})" for x in found
        )
        return _err(
            path,
            "ambiguous_object_type",
            f"{ref!r} names several object types ({names}): use the table or the name",
        )
    if t is None:
        known = ", ".join(
            sorted(x.label for x in ctx.schema.types.values() if x.status == "active")
        )
        return _err(
            path, "unknown_object_type", f"no object type {ref!r} (active: {known or 'none'})"
        )
    return _err(path, "object_type_not_active", f"object type {t.label} is {t.status}, not active")


class _Checker:
    def __init__(self, ctx: LogicContext) -> None:
        self.ctx = ctx
        self.errors: list[dict[str, Any]] = []

    def expr(
        self, path: str, text: str | None, scope: ex.Scope, want: str | None = None
    ) -> ex.Compiled | None:
        if text is None:
            return None
        try:
            compiled = ex.compile_expr(text, scope)
        except ex.ExprError as exc:
            self.errors.append(_err(path, exc.code, exc.message, exc.pos))
            return None
        if want is not None and not ex.fits(compiled.type, want):
            self.errors.append(
                _err(path, "type_error", f"this is {compiled.type}, it should be {want}")
            )
        return compiled

    def params(self, path: str, params: list[ParamSpec]) -> None:
        seen: set[str] = set()
        for i, p in enumerate(params):
            where = f"{path}[{i}]"
            if p.name in seen:
                self.errors.append(_err(f"{where}.name", "duplicate", f"parameter {p.name} twice"))
            seen.add(p.name)
            values = list(p.enum or [])
            if p.default is not None:
                values.append(p.default)
            for v in values:
                try:
                    ok = ex.type_of(ex.coerce(v, p.type)) in (p.type, "null")
                except ex.EvalError:
                    ok = False
                if not ok:
                    self.errors.append(
                        _err(where, "type_error", f"{v!r} is not a {p.type} for parameter {p.name}")
                    )
            if p.enum and p.default is not None and p.default not in p.enum:
                self.errors.append(
                    _err(f"{where}.default", "not_in_enum", "default is not in enum")
                )

    def template(self, path: str, text: str, scope: ex.Scope) -> None:
        for offset, inner in template_exprs(text):
            if not inner:
                self.errors.append(
                    _err(path, "syntax_error", "empty {{ }} in the template", offset)
                )
                continue
            try:
                ex.compile_expr(inner, scope)
            except ex.ExprError as exc:
                pos = None if exc.pos is None else offset + exc.pos
                self.errors.append(_err(path, exc.code, exc.message, pos))

    def notify(self, path: str, n: NotifySpec, scope: ex.Scope) -> None:
        for i, to in enumerate(n.to):
            where = f"{path}.to[{i}]"
            kind, sep, rest = to.partition(":")
            rest = rest.strip()
            if not sep or kind not in ("role", "email", "field"):
                self.errors.append(
                    _err(
                        where,
                        "invalid_recipient",
                        "a recipient is role:<role>, email:<address> or field:<expr>",
                    )
                )
            elif kind == "role" and rest not in ROLES:
                self.errors.append(
                    _err(where, "invalid_recipient", f"role must be one of {', '.join(ROLES)}")
                )
            elif kind == "email" and not EMAIL_RE.match(rest):
                self.errors.append(
                    _err(where, "invalid_recipient", f"{rest[:80]!r} is not an email address")
                )
            elif kind == "field":
                self.expr(where, rest, scope, "text")
        self.template(f"{path}.subject", n.subject, scope)
        self.template(f"{path}.body", n.body, scope)


def _function_cycle(ctx: LogicContext, start: str) -> list[str] | None:
    """A call cycle through *start* (the names on it), or None."""
    deps: dict[str, set[str]] = {}
    for name, spec in ctx.functions.items():
        t = ctx.schema.resolve(spec.object_type)
        if t is None:
            deps[name] = set()
            continue
        try:
            compiled = ex.compile_expr(spec.expr, ctx.scope(t, spec.params))
            deps[name] = set(compiled.functions)
        except ex.ExprError:
            deps[name] = set()
    path: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> list[str] | None:
        for nxt in sorted(deps.get(name, ())):
            if nxt == start:
                return [*path, nxt]
            if nxt not in seen:
                seen.add(nxt)
                path.append(nxt)
                found = visit(nxt)
                if found:
                    return found
                path.pop()
        return None

    path.append(start)
    return visit(start)


def _validate_function(spec: FunctionSpec, ctx: LogicContext, c: _Checker) -> None:
    if spec.name in RESERVED_NAMES:
        c.errors.append(_err("name", "reserved_name", f"{spec.name!r} is a reserved name"))
    t = ctx.type_of(spec.object_type)
    if t is None:
        c.errors.append(_type_error("object_type", spec.object_type, ctx))
        return
    if t.field(spec.name) is not None:
        c.errors.append(
            _err("name", "shadows_field", f"{spec.name!r} is already a field of {t.label}")
        )
    c.params("params", spec.params)
    compiled = c.expr("expr", spec.expr, ctx.scope(t, spec.params), spec.returns)
    if compiled is None:
        return
    for name in sorted(compiled.functions):
        if ctx.statuses.get(("function", name)) == "broken":
            c.errors.append(_err("expr", "reference_broken", f"function {name} is broken"))
    cycle = _function_cycle(ctx, spec.name)
    if cycle:
        c.errors.append(
            _err("expr", "cycle", "functions call each other in a cycle: " + " -> ".join(cycle))
        )


def _validate_action(spec: ActionSpec, ctx: LogicContext, c: _Checker) -> None:
    t = ctx.type_of(spec.object_type)
    if t is None:
        c.errors.append(_type_error("object_type", spec.object_type, ctx))
        return
    c.params("params", spec.params)
    scope = ctx.scope(t, spec.params)
    for i, pre in enumerate(spec.preconditions):
        c.expr(f"preconditions[{i}].expr", pre.expr, scope, "bool")
    seen: set[str] = set()
    for i, eff in enumerate(spec.effects):
        where = f"effects[{i}]"
        found = None if eff.operational else t.field(eff.set)
        if eff.operational:
            if not ex.NAME_LIKE.match(eff.set):
                c.errors.append(
                    _err(f"{where}.set", "invalid_name", f"{eff.set!r} is no property name")
                )
            if eff.set in t.columns:
                c.errors.append(
                    _err(
                        f"{where}.set",
                        "operational_is_column",
                        f"{eff.set} is a column of {t.label}: drop operational",
                    )
                )
            want = None
        elif found is None or found[0] not in t.columns:
            c.errors.append(
                _err(
                    f"{where}.set",
                    "unknown_property",
                    f"{t.label} has no column {eff.set!r} (an operational property needs operational: true)",
                )
            )
            want = None
        else:
            want = found[1]
            if found[0] == t.key:
                c.errors.append(
                    _err(f"{where}.set", "key_readonly", "the key column cannot be set")
                )
        prop = found[0] if found else eff.set
        if prop in seen:
            c.errors.append(_err(f"{where}.set", "duplicate", f"{prop} is set twice"))
        seen.add(prop)
        c.expr(f"{where}.value", eff.value, scope, want)
    for i, n in enumerate(spec.notify):
        c.notify(f"notify[{i}]", n, scope)
    if spec.approval.when is not None:
        if spec.approval.role == "none":
            c.errors.append(
                _err(
                    "approval.when",
                    "schema",
                    "approval.when is set but approval.role is none; choose reviewer or "
                    "owner (or remove approval.when)",
                )
            )
        c.expr("approval.when", spec.approval.when, scope, "bool")


def _validate_steps(
    path: str,
    steps: list[StepSpec],
    spec: WorkflowSpec,
    t: TypeInfo,
    ctx: LogicContext,
    c: _Checker,
    depth: int,
    count: list[int],
) -> None:
    if depth > MAX_STEP_DEPTH:
        c.errors.append(_err(path, "too_deep", f"steps nest at most {MAX_STEP_DEPTH} deep"))
        return
    scope = ctx.scope(t)
    for i, step in enumerate(steps):
        where = f"{path}[{i}]"
        count[0] += 1
        if step.if_ is not None:
            c.expr(f"{where}.if", step.if_, scope, "bool")
            _validate_steps(f"{where}.then", step.then, spec, t, ctx, c, depth + 1, count)
            _validate_steps(f"{where}.else", step.else_, spec, t, ctx, c, depth + 1, count)
        elif step.action is not None:
            action = ctx.actions.get(step.action)
            if action is None:
                c.errors.append(
                    _err(f"{where}.action", "unknown_action", f"no action {step.action!r}")
                )
                continue
            if ctx.statuses.get(("action", step.action)) == "broken":
                c.errors.append(
                    _err(f"{where}.action", "reference_broken", f"action {step.action} is broken")
                )
            if ctx.schema.resolve(action.object_type) is not ctx.schema.resolve(
                spec.for_each.object_type
            ):
                c.errors.append(
                    _err(
                        f"{where}.action",
                        "wrong_object_type",
                        f"action {step.action} works on {action.object_type}, not {spec.for_each.object_type}",
                    )
                )
            declared = {p.name: p for p in action.params}
            for name, value in step.params.items():
                p = declared.get(name)
                if p is None:
                    c.errors.append(
                        _err(
                            f"{where}.params.{name}",
                            "unknown_param",
                            f"action {step.action} has no parameter {name!r}",
                        )
                    )
                    continue
                c.expr(f"{where}.params.{name}", value, scope, p.type)
            for p in action.params:
                if p.required and p.default is None and p.name not in step.params:
                    c.errors.append(
                        _err(
                            f"{where}.params",
                            "missing_param",
                            f"action {step.action} needs parameter {p.name}",
                        )
                    )
            if spec.trigger.on_action == step.action:
                c.errors.append(
                    _err(
                        f"{where}.action",
                        "cycle",
                        f"the workflow runs on {step.action} and runs {step.action} again",
                    )
                )
        elif step.notify is not None:
            c.notify(f"{where}.notify", step.notify, scope)
    if count[0] > MAX_STEPS:
        c.errors.append(_err(path, "too_many", f"a workflow has at most {MAX_STEPS} steps"))


def _validate_workflow(spec: WorkflowSpec, ctx: LogicContext, c: _Checker) -> None:
    if spec.trigger.on_action is not None and spec.trigger.on_action not in ctx.actions:
        c.errors.append(
            _err("trigger.on_action", "unknown_action", f"no action {spec.trigger.on_action!r}")
        )
    t = ctx.type_of(spec.for_each.object_type)
    if t is None:
        c.errors.append(_type_error("for_each.object_type", spec.for_each.object_type, ctx))
        return
    c.expr("for_each.where", spec.for_each.where, ctx.scope(t), "bool")
    _validate_steps("steps", spec.steps, spec, t, ctx, c, 1, [0])


def validate(spec: Spec, ctx: LogicContext) -> list[dict[str, Any]]:
    """Why *spec* cannot be used in *ctx* (empty: it can). *ctx* gets *spec* in place of a
    stored definition of the same kind and name."""
    ctx = ctx.with_spec(spec)
    c = _Checker(ctx)
    if isinstance(spec, FunctionSpec):
        _validate_function(spec, ctx, c)
    elif isinstance(spec, ActionSpec):
        _validate_action(spec, ctx, c)
    else:
        _validate_workflow(spec, ctx, c)
    return c.errors


def references(spec: Spec, ctx: LogicContext) -> list[tuple[str, str]]:
    """The definitions *spec* uses: ``(kind, name)`` -- functions its expressions call,
    actions a workflow runs or is triggered by."""
    out: set[tuple[str, str]] = set()
    ctx = ctx.with_spec(spec)

    def exprs_of(
        text: str | None, t: TypeInfo | None, params: list[ParamSpec] | None = None
    ) -> None:
        if text is None or t is None:
            return
        try:
            compiled = ex.compile_expr(text, ctx.scope(t, params))
        except ex.ExprError:
            return
        out.update(("function", f) for f in compiled.functions)

    def templates(n: NotifySpec, t: TypeInfo | None, params: list[ParamSpec] | None = None) -> None:
        for to in n.to:
            if to.startswith("field:"):
                exprs_of(to[6:].strip(), t, params)
        for text in (n.subject, n.body):
            for _, inner in template_exprs(text):
                if inner:
                    exprs_of(inner, t, params)

    if isinstance(spec, FunctionSpec):
        exprs_of(spec.expr, ctx.type_of(spec.object_type), spec.params)
    elif isinstance(spec, ActionSpec):
        t = ctx.type_of(spec.object_type)
        for pre in spec.preconditions:
            exprs_of(pre.expr, t, spec.params)
        for eff in spec.effects:
            exprs_of(eff.value, t, spec.params)
        for n in spec.notify:
            templates(n, t, spec.params)
        exprs_of(spec.approval.when, t, spec.params)
    else:
        t = ctx.type_of(spec.for_each.object_type)
        exprs_of(spec.for_each.where, t)
        if spec.trigger.on_action:
            out.add(("action", spec.trigger.on_action))

        def walk(steps: list[StepSpec]) -> None:
            for step in steps:
                if step.if_ is not None:
                    exprs_of(step.if_, t)
                    walk(step.then)
                    walk(step.else_)
                elif step.action is not None:
                    out.add(("action", step.action))
                    for value in step.params.values():
                        exprs_of(value, t)
                elif step.notify is not None:
                    templates(step.notify, t)

        walk(spec.steps)
    out.discard((spec.kind, spec.name))
    return sorted(out)
