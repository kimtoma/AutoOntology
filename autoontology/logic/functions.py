# SPDX-License-Identifier: MIT
"""Evaluate functions on objects.

* :func:`computed` -- the values of every active function of an object type for a page
  of object records (``overlay.record`` dicts): ``{name: {value, type, error?}}`` per
  record. Functions with a required parameter that has no default are left out.
* :func:`attach_computed` -- the same, set as ``computed`` on records in place.
* :func:`call` -- one function (active only) on one object, with parameters.
* :func:`test` -- a function (a stored one by name, or an unsaved spec) on chosen objects,
  for editing: drafts may be called.

Links are followed through :class:`SourceLoader`: source rows read with the object
reader (read-only transactions, one connection per source for the whole page), the
edit layer applied, cached per value; the first hop of every link the functions use is
fetched for the whole page at once. A page has a budget of :data:`MAX_LINK_QUERIES`
queries and :data:`MAX_LINK_ROWS` rows; past it the remaining values are errors.

A failure of one function on one object (division by zero, a bad source value, a budget)
is that value's ``error``, never the request's.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from autoontology.logic import expr as ex
from autoontology.logic import registry, specs
from autoontology.logic.schema import LinkInfo, TypeInfo
from autoontology.logic.specs import FunctionSpec, LogicContext
from autoontology.objects import overlay, reader, service
from autoontology.objects.reader import ObjectError, TableQuery
from autoontology.store.objects import ObjectType

MAX_LINK_QUERIES = 100
MAX_LINK_ROWS = 5000
#: keys one test call evaluates at most
MAX_TEST_KEYS = 20


def today() -> dt.date:
    """Today's date for ``today()`` (local time; tests pass their own)."""
    return dt.date.today()


@dataclass
class SourceLoader:
    """:class:`~autoontology.logic.expr.Loader` over the project's sources."""

    settings: Any
    view: Any
    stack: ExitStack
    schema_types: dict[str, TypeInfo]
    queries: int = 0
    rows: int = 0
    _otypes: dict[str, ObjectType] = field(default_factory=dict)
    _backends: dict[str, Any] = field(default_factory=dict)
    _checked: dict[str, list[str]] = field(default_factory=dict)
    #: (target type, column, value as text) -> objects
    _cache: dict[tuple[str, str, str], list[ex.Obj]] = field(default_factory=dict)

    def _otype(self, name: str) -> ObjectType:
        if name not in self._otypes:
            self._otypes[name] = service.readable_type(self.view, name)
        return self._otypes[name]

    def _backend(self, otype: ObjectType, column: str) -> tuple[Any, list[str]]:
        source = otype.source_name
        if source not in self._backends:
            self._backends[source] = self.stack.enter_context(
                service._open(self.settings, self.view, otype)
            )
        backend = self._backends[source]
        if otype.class_local not in self._checked:
            self._checked[otype.class_local] = service._checked(self.view, backend, otype)
        names = self._checked[otype.class_local]
        if column not in names:
            raise ex.EvalError("source_changed", f"column {column} is missing from {otype.label}")
        return backend, names

    def _spend(self, *, queries: int = 0, rows: int = 0) -> None:
        self.queries += queries
        self.rows += rows
        if self.queries > MAX_LINK_QUERIES or self.rows > MAX_LINK_ROWS:
            raise ex.EvalError("link_budget", "following links read too many objects")

    def fetch(self, target: str, column: str, values: list[str]) -> None:
        """Read the objects of *target* whose *column* is one of *values* into the cache."""
        todo = sorted({v for v in values if (target, column, v) not in self._cache})
        if not todo:
            return
        otype = self._otype(target)
        info = self.schema_types.get(target)
        if info is None:
            raise ex.EvalError("link_target_inactive", f"{target} is not an active object type")
        backend, names = self._backend(otype, column)
        found: dict[str, list[dict[str, Any]]] = {v: [] for v in todo}
        for start in range(0, len(todo), reader.MAX_PAGE):
            chunk = todo[start : start + reader.MAX_PAGE]
            offset = 0
            while True:
                self._spend(queries=1)
                page = backend.select(
                    TableQuery(
                        otype.schema,
                        otype.table,
                        names,
                        order_by=otype.key,
                        any_of=(column, chunk),
                        limit=reader.MAX_PAGE,
                        offset=offset,
                    )
                )
                self._spend(rows=len(page))
                for row in page:
                    value = row.get(column)
                    if value is not None and str(value) in found:
                        found[str(value)].append(row)
                if len(page) < reader.MAX_PAGE:
                    break
                offset += reader.MAX_PAGE
        for value, rows in found.items():
            records = overlay.apply(self.view, otype, rows)
            self._cache[(target, column, value)] = [ex.Obj(info, r["values"]) for r in records]

    def _where(self, obj: ex.Obj, link: LinkInfo) -> tuple[str, str, str | None]:
        if link.direction == "out":
            target = self.schema_types.get(link.target)
            value = obj.values.get(link.column)
            return link.target, target.key if target else "", None if value is None else str(value)
        value = obj.values.get(obj.type.key)
        return link.target, link.column, None if value is None else str(value)

    def follow(self, obj: ex.Obj, link: LinkInfo) -> list[ex.Obj]:
        target, column, value = self._where(obj, link)
        if value is None or not column:
            return []
        if (target, column, value) not in self._cache:
            self.fetch(target, column, [value])
        return self._cache[(target, column, value)]

    def prefetch(self, objs: list[ex.Obj], links: list[LinkInfo]) -> None:
        for link in links:
            wanted: dict[tuple[str, str], list[str]] = {}
            for o in objs:
                target, column, value = self._where(o, link)
                if value is not None and column:
                    wanted.setdefault((target, column), []).append(value)
            for (target, column), values in wanted.items():
                self.fetch(target, column, values)


# --- compiling the functions of a context -------------------------------------------------


class Functions:
    """The compiled functions of a context; ``callable`` decides which may run (active
    ones at execution, any non-broken one while testing)."""

    def __init__(self, ctx: LogicContext, callable_: Callable[[str], bool]) -> None:
        self.ctx = ctx
        self.callable = callable_
        self._compiled: dict[str, ex.Compiled | None] = {}

    def compiled(self, name: str) -> ex.Compiled | None:
        if name not in self._compiled:
            self._compiled[name] = None
            spec = self.ctx.functions.get(name)
            t = self.ctx.type_of(spec.object_type) if spec else None
            if spec is not None and t is not None:
                try:
                    self._compiled[name] = ex.compile_expr(
                        spec.expr, self.ctx.scope(t, spec.params)
                    )
                except ex.ExprError:
                    self._compiled[name] = None
        return self._compiled[name]

    def runnable(self, name: str) -> ex.Compiled | None:
        return self.compiled(name) if self.callable(name) else None


def _defaults(spec: FunctionSpec) -> dict[str, Any] | None:
    """The default parameters of *spec*; None when a required one has no default."""
    out: dict[str, Any] = {}
    for p in spec.params:
        if p.default is not None:
            out[p.name] = p.default
        elif p.required:
            return None
    return out


def _params(spec: FunctionSpec, given: dict[str, Any] | None) -> dict[str, Any]:
    given = dict(given or {})
    unknown = set(given) - {p.name for p in spec.params}
    if unknown:
        raise ObjectError(400, "unknown_param", f"no parameter {sorted(unknown)[0]!r}")
    out: dict[str, Any] = {}
    for p in spec.params:
        value = given.get(p.name, p.default)
        if value is None:
            if p.required:
                raise ObjectError(400, "missing_param", f"parameter {p.name} is required")
            continue
        try:
            value = ex.coerce(value, p.type, p.name)
        except ex.EvalError as exc:
            raise ObjectError(400, "invalid_param", exc.message) from exc
        if p.enum and value not in p.enum:
            raise ObjectError(400, "invalid_param", f"{p.name} must be one of {p.enum}")
        out[p.name] = ex.to_json(value)
    return out


def _value(
    compiled: ex.Compiled,
    spec: FunctionSpec,
    obj: ex.Obj,
    ctx: ex.EvalContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = compiled.evaluate(obj, ctx, params)
        value = ex.coerce(
            value if not isinstance(value, dt.date) else value.isoformat(), spec.returns, spec.name
        )
        return {"value": ex.to_json(value), "type": spec.returns}
    except ex.EvalError as exc:
        return {
            "value": None,
            "type": spec.returns,
            "error": {"code": exc.code, "message": exc.message},
        }
    except ObjectError as exc:
        return {
            "value": None,
            "type": spec.returns,
            "error": {"code": exc.code, "message": exc.message},
        }


def _evaluate(
    settings: Any,
    view: Any,
    info: TypeInfo,
    records: list[dict[str, Any]],
    chosen: list[tuple[FunctionSpec, ex.Compiled, dict[str, Any]]],
    functions: Functions,
    when: dt.date,
) -> list[dict[str, dict[str, Any]]]:
    objs = [ex.Obj(info, r["values"]) for r in records]
    out: list[dict[str, dict[str, Any]]] = [{} for _ in records]
    if not chosen:
        return out
    links = {lk.name: lk for lk in info.links}
    first = sorted({name for _, c, _ in chosen for name in c.links})
    with ExitStack() as stack:
        loader = SourceLoader(settings, view, stack, functions.ctx.schema.types)
        prefetch_error: dict[str, Any] | None = None
        try:
            loader.prefetch(objs, [links[n] for n in first if n in links])
        except (ex.EvalError, ObjectError) as exc:
            prefetch_error = {"code": exc.code, "message": exc.message}
        for i, obj in enumerate(objs):
            for spec, compiled, params in chosen:
                if prefetch_error is not None and compiled.links:
                    out[i][spec.name] = {
                        "value": None,
                        "type": spec.returns,
                        "error": prefetch_error,
                    }
                    continue
                ctx = ex.EvalContext(today=when, loader=loader, function=functions.runnable)
                out[i][spec.name] = _value(compiled, spec, obj, ctx, params)
    return out


def _type_functions(
    ctx: LogicContext, info: TypeInfo, functions: Functions
) -> list[tuple[FunctionSpec, ex.Compiled, dict[str, Any]]]:
    chosen = []
    for name in sorted(ctx.functions):
        spec = ctx.functions[name]
        if ctx.statuses.get(("function", name)) != "active":
            continue
        t = ctx.schema.resolve(spec.object_type)
        if t is None or t.name != info.name:
            continue
        params = _defaults(spec)
        compiled = functions.compiled(name)
        if params is None or compiled is None:
            continue
        chosen.append((spec, compiled, params))
    return chosen


def _active(ctx: LogicContext) -> Callable[[str], bool]:
    return lambda name: ctx.statuses.get(("function", name)) == "active"


def computed(
    settings: Any,
    view: Any,
    otype: ObjectType,
    records: list[dict[str, Any]],
    *,
    when: dt.date | None = None,
) -> list[dict[str, dict[str, Any]]]:
    """The values of the active functions of *otype* for each record (module docstring)."""
    ctx = registry.context(view)
    info = ctx.type_of(otype.class_local)
    if info is None or not records:
        return [{} for _ in records]
    functions = Functions(ctx, _active(ctx))
    chosen = _type_functions(ctx, info, functions)
    return _evaluate(settings, view, info, records, chosen, functions, when or today())


def attach_computed(
    settings: Any,
    view: Any,
    type_name: str,
    records: list[dict[str, Any]],
    *,
    when: dt.date | None = None,
) -> list[dict[str, Any]]:
    """Set ``computed`` on every record of type *type_name* (in place; returned)."""
    if not records:
        return records
    otype = view.object_types().get(type_name)
    if otype is None:
        return records
    values = computed(settings, view, otype, records, when=when)
    for record, value in zip(records, values, strict=True):
        record["computed"] = value
    return records


def _records(settings: Any, view: Any, otype: ObjectType, keys: list[str]) -> list[dict[str, Any]]:
    """The records of *keys* (missing keys: 404 ``unknown_object``), in order."""
    with service._open(settings, view, otype) as backend:
        names = service._checked(view, backend, otype)
        rows = backend.select(
            TableQuery(
                otype.schema,
                otype.table,
                names,
                order_by=otype.key,
                any_of=(otype.key, keys),
                limit=reader.MAX_PAGE,
            )
        )
    by_key = {overlay.object_key(otype, r): r for r in rows}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise ObjectError(404, "unknown_object", f"no {otype.label} {missing[0][:80]!r}")
    return overlay.apply(view, otype, [by_key[k] for k in keys])


def call(
    settings: Any,
    view: Any,
    name: str,
    key: str,
    *,
    params: dict[str, Any] | None = None,
    type_name: str | None = None,
    when: dt.date | None = None,
) -> dict[str, Any]:
    """Active function *name* on object *key*: ``{function, object_type, key, value,
    type, error?}``. 404 ``unknown_function``, 409 ``function_not_active``."""
    ctx = registry.context(view)
    spec = ctx.functions.get(name)
    if spec is None:
        raise ObjectError(404, "unknown_function", f"no function {name[:80]!r}")
    if ctx.statuses.get(("function", name)) != "active":
        raise ObjectError(
            409, "function_not_active", f"function {name} is {ctx.statuses.get(('function', name))}"
        )
    info = ctx.type_of(spec.object_type)
    if info is None:
        raise ObjectError(409, "function_not_active", f"the object type of {name} is not active")
    if type_name is not None:
        asked = ctx.schema.resolve(type_name)
        if asked is None or asked.name != info.name:
            raise ObjectError(
                400, "wrong_object_type", f"function {name} is defined on {info.label}"
            )
    if not key or len(key) > reader.MAX_VALUE_CHARS:
        raise ObjectError(404, "unknown_object", "no such object")
    functions = Functions(ctx, _active(ctx))
    compiled = functions.compiled(name)
    if compiled is None:
        raise ObjectError(409, "function_not_active", f"function {name} does not compile")
    otype = service.readable_type(view, info.name)
    record = _records(settings, view, otype, [key])[0]
    value = _evaluate(
        settings,
        view,
        info,
        [record],
        [(spec, compiled, _params(spec, params))],
        functions,
        when or today(),
    )[0][name]
    return {
        "function": name,
        "object_type": info.name,
        "key": record["key"],
        "title": record["title"],
        **value,
    }


def test(
    settings: Any,
    view: Any,
    spec_or_name: Any,
    keys: list[str],
    *,
    params: dict[str, Any] | None = None,
    when: dt.date | None = None,
) -> dict[str, Any]:
    """A function on chosen objects while editing it: *spec_or_name* is a stored
    function's name or an unsaved spec (mapping or YAML/JSON text). The function and
    those it calls may be drafts. ``{function, type, errors, results: [{key, title,
    value, error?}]}``; invalid specs give ``errors`` and no results."""
    ctx = registry.context(view)
    if isinstance(spec_or_name, str) and spec_or_name in ctx.functions:
        spec: Any = ctx.functions[spec_or_name]
    else:
        try:
            spec = specs.parse_spec(spec_or_name)
        except specs.SpecError as exc:
            return {"function": None, "errors": exc.errors, "results": []}
    if not isinstance(spec, FunctionSpec):
        return {
            "function": getattr(spec, "name", None),
            "errors": [
                {
                    "path": "kind",
                    "code": "not_a_function",
                    "message": "only functions can be tested",
                }
            ],
            "results": [],
        }
    errors = specs.validate(spec, ctx)
    out: dict[str, Any] = {
        "function": spec.name,
        "type": spec.returns,
        "errors": errors,
        "results": [],
    }
    if errors:
        return out
    keys = [str(k) for k in keys if str(k)]
    if not keys:
        raise ObjectError(400, "missing_keys", "give at least one object key")
    if len(keys) > MAX_TEST_KEYS:
        raise ObjectError(400, "too_many_keys", f"at most {MAX_TEST_KEYS} keys")
    ctx = ctx.with_spec(spec)
    functions = Functions(
        ctx, lambda name: ctx.statuses.get(("function", name)) != "broken" or name == spec.name
    )
    compiled = functions.compiled(spec.name)
    info = ctx.type_of(spec.object_type)
    assert compiled is not None and info is not None
    otype = service.readable_type(view, info.name)
    records = _records(settings, view, otype, list(dict.fromkeys(keys)))
    values = _evaluate(
        settings,
        view,
        info,
        records,
        [(spec, compiled, _params(spec, params))],
        functions,
        when or today(),
    )
    out["results"] = [
        {"key": r["key"], "title": r["title"], **v[spec.name]}
        for r, v in zip(records, values, strict=True)
    ]
    return out


def list_active(view: Any, type_name: str | None = None) -> list[dict[str, Any]]:
    """The active functions (of *type_name*): ``[{name, label, description, object_type,
    returns, params, expr}]``."""
    ctx = registry.context(view)
    wanted = ctx.schema.resolve(type_name) if type_name else None
    if type_name and wanted is None:
        raise ObjectError(404, "unknown_object_type", f"no object type {type_name[:80]!r}")
    out = []
    for name in sorted(ctx.functions):
        if ctx.statuses.get(("function", name)) != "active":
            continue
        spec = ctx.functions[name]
        t = ctx.schema.resolve(spec.object_type)
        if t is None or (wanted is not None and t.name != wanted.name):
            continue
        out.append(
            {
                "name": name,
                "label": spec.label,
                "description": spec.description,
                "object_type": t.name,
                "object_type_label": t.label,
                "returns": spec.returns,
                "params": [p.model_dump(mode="json", exclude_none=True) for p in spec.params],
                "expr": spec.expr,
            }
        )
    return out
