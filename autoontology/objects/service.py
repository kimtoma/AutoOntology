# SPDX-License-Identifier: MIT
"""Object operations shared by the REST API, the CLI (through the API) and the agent
tools: list types, search / get / follow objects (source rows with the edit layer
applied), bind a type by hand and decide a proposal.

Object types are bound to the project's **latest** snapshot (:func:`binding.ensure_synced`
runs first on every call) and object values are read live from the sources, so they are
not versioned by revision.

Reads go through :func:`autoontology.objects.reader.open_source` (looked up at call time,
so tests can substitute it). Before every read the type's table and the columns the read
uses are checked against the source in the same read-only transaction: a missing one
marks an active type ``broken`` (reason ``source: ...``) and the read fails with 409
``object_type_broken``; a type broken that way is checked again on the next read and
becomes ``active`` once its table and columns are back.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from autoontology.objects import binding, naming, overlay, reader
from autoontology.objects.reader import MAX_PAGE, ObjectError, TableQuery
from autoontology.store.objects import ObjectType

#: At most this many ``column=value`` filters per search.
MAX_FILTERS = 5
#: Link hops :func:`follow` takes at most.
MAX_HOPS = 2


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))


def list_types(
    view: Any, *, status: str | None = None, model: Any = None, revision: int | None = None
) -> list[ObjectType]:
    binding.ensure_synced(view, model, revision)
    return view.object_types().list(status)


def find_type(view: Any, name: str) -> ObjectType:
    """The type *name* names (:mod:`~autoontology.objects.naming`: id, class name, label,
    table), synced; 404 ``unknown_object_type``, 409 ``ambiguous_object_type`` (with the
    ``candidates``)."""
    binding.ensure_synced(view)
    types = view.object_types()
    exact = types.get(name) if name and len(name) <= naming.MAX_REF else None
    if exact is not None:
        return exact
    found = naming.matches(types.list(), name, lambda t: t.names())
    if not found:
        raise ObjectError(404, "unknown_object_type", f"no object type {name[:80]!r}")
    if len(found) > 1:
        raise ObjectError(
            409,
            "ambiguous_object_type",
            f"{name[:80]!r} names {len(found)} object types: use the name (term-...) or the table",
            candidates=[naming.describe(t.names()) for t in found],
        )
    return found[0]


def readable_type(view: Any, name: str) -> ObjectType:
    """:func:`find_type`, for reading rows: active, or broken by the source (checked
    again by the read); 409 ``object_type_not_active`` otherwise."""
    otype = find_type(view, name)
    if otype.status == "active" or (
        otype.status == "broken" and otype.broken_reason.startswith(binding.SOURCE_REASON)
    ):
        if not otype.key:
            raise ObjectError(409, "object_type_without_key", f"{otype.label} has no key")
        return otype
    detail = f"object type {otype.label or otype.class_local} is {otype.status}"
    if otype.broken_reason:
        detail += f" ({otype.broken_reason})"
    raise ObjectError(409, "object_type_not_active", detail)


@contextmanager
def _open(settings: Any, view: Any, otype: ObjectType) -> Iterator[Any]:
    source = view.get_source(otype.source_name)
    if source is None:
        raise ObjectError(
            409, "source_missing", f"source {otype.source_name!r} is not registered any more"
        )
    with reader.open_source(settings, source) as backend:
        yield backend


def _checked(view: Any, backend: Any, otype: ObjectType, extra: Sequence[str] = ()) -> list[str]:
    """The table's column names now; marks *otype* broken (and raises) when the table or
    a column the read needs is missing, restores it when it was broken by the source."""
    types = view.object_types()
    columns = backend.columns(otype.schema, otype.table)
    names = [c["name"] for c in columns]
    if not names:
        problem = f"{binding.SOURCE_REASON}table {otype.qualified_table} is missing"
    else:
        needed = [c for c in (otype.key, otype.title_column, *extra) if c]
        missing = [c for c in needed if c not in names]
        problem = (
            f"{binding.SOURCE_REASON}column {', '.join(missing)} is missing from "
            f"{otype.qualified_table}"
            if missing
            else ""
        )
    if problem:
        if otype.status == "active":
            types.set_status(otype.class_local, "broken", reason=problem)
        raise reader.SourceChanged(problem)
    if otype.status == "broken":
        types.save(replace(otype, status="active", broken_reason="", columns=columns))
    elif otype.columns != columns:
        types.save(replace(otype, columns=columns))
    return names


def _filters(raw: list[str] | dict[str, str] | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    pairs = list(raw.items()) if isinstance(raw, dict) else []
    if isinstance(raw, list):
        for item in raw:
            column, sep, value = str(item).partition("=")
            if not sep or not column:
                raise ObjectError(400, "invalid_filter", f"a filter is column=value, got {item!r}")
            pairs.append((column, value))
    if len(pairs) > MAX_FILTERS:
        raise ObjectError(400, "invalid_filter", f"at most {MAX_FILTERS} filters")
    return [(str(c), str(v)) for c, v in pairs]


def _unknown(names: list[str], wanted: list[str], otype: ObjectType) -> None:
    bad = [c for c in wanted if c not in names]
    if bad:
        raise ObjectError(
            400,
            "unknown_column",
            f"{bad[0][:80]!r} is not a column of {otype.label or otype.qualified_table}",
        )


def search(
    settings: Any,
    view: Any,
    name: str,
    *,
    q: str = "",
    filters: list[str] | dict[str, str] | None = None,
    limit: int = reader.DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """Objects of type *name*: *q* searches the key and title (contains, any case),
    *filters* are ``column=value`` (compared as text). ``{type, items, total, limit,
    offset}``, ordered by key."""
    otype = readable_type(view, name)
    pairs = _filters(filters)
    limit, offset = _page(limit, offset)
    with _open(settings, view, otype) as backend:
        names = _checked(view, backend, otype)
        _unknown(names, [c for c, _ in pairs], otype)
        search_cols = [c for c in (otype.key, otype.title_column) if c]
        query = TableQuery(
            otype.schema,
            otype.table,
            names,
            order_by=otype.key,
            equals=pairs,
            search=(search_cols, q.strip()) if q.strip() else None,
            limit=limit,
            offset=offset,
        )
        rows = backend.select(query)
        total = backend.count(query)
    return {
        "type": otype.to_dict(),
        "items": overlay.apply(view, otype, rows),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _link(otype: ObjectType, name: str) -> dict[str, Any]:
    found = next((link for link in otype.links if link.get("name") == name), None)
    if found is None:
        raise ObjectError(404, "unknown_link", f"{otype.label} has no link {name[:80]!r}")
    return found


def _links_brief(view: Any, otype: ObjectType) -> list[dict[str, Any]]:
    types = view.object_types()
    out = []
    for link in otype.links:
        target = types.get(link["target"])
        out.append(
            {
                **link,
                "target_label": target.label if target else link["target"],
                "target_status": target.status if target else "unbound",
            }
        )
    return out


def get_object(settings: Any, view: Any, name: str, key: str) -> dict[str, Any]:
    """One object by its key: the :func:`overlay.record` plus ``links`` (each with its
    target type's status) and the edit ``history``. 404 ``unknown_object``."""
    otype = readable_type(view, name)
    if not key or len(key) > reader.MAX_VALUE_CHARS:
        raise ObjectError(404, "unknown_object", "no such object")
    with _open(settings, view, otype) as backend:
        names = _checked(view, backend, otype)
        rows = backend.select(
            TableQuery(otype.schema, otype.table, names, equals=[(otype.key, key)], limit=2)
        )
    if not rows:
        raise ObjectError(404, "unknown_object", f"no {otype.label} {key[:80]!r}")
    item = overlay.apply(view, otype, rows[:1])[0]
    history = view.object_edits().history(otype.class_local, item["key"])
    return {
        **item,
        "type": otype.to_dict(),
        "links": _links_brief(view, otype),
        "history": [e.to_dict() for e in history[:50]],
    }


def _hop(
    settings: Any,
    view: Any,
    otype: ObjectType,
    link: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    limit: int,
    offset: int = 0,
) -> tuple[ObjectType, list[dict[str, Any]], int]:
    """The objects *link* reaches from *rows* (source rows of *otype*): ``(target type,
    target rows, total)``."""
    target = readable_type(view, link["target"])
    if link["direction"] == "out":
        values = sorted({str(r[link["column"]]) for r in rows if r.get(link["column"]) is not None})
        column = target.key
    else:
        values = sorted({overlay.object_key(otype, r) for r in rows})
        column = link["column"]
    if not values:
        return target, [], 0
    with _open(settings, view, target) as backend:
        names = _checked(view, backend, target, [column])
        query = TableQuery(
            target.schema,
            target.table,
            names,
            order_by=target.key,
            any_of=(column, values),
            limit=limit,
            offset=offset,
        )
        return target, backend.select(query), backend.count(query)


def follow(
    settings: Any,
    view: Any,
    name: str,
    key: str,
    link_name: str,
    *,
    then: str | None = None,
    limit: int = reader.DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """The objects link *link_name* of object *key* reaches (*then*: a second link from
    there, :data:`MAX_HOPS` at most). ``{type, link, path, items, total, limit, offset}``."""
    otype = readable_type(view, name)
    limit, offset = _page(limit, offset)
    first = _link(otype, link_name)
    with _open(settings, view, otype) as backend:
        names = _checked(
            view, backend, otype, [first["column"]] if first["direction"] == "out" else []
        )
        rows = backend.select(
            TableQuery(otype.schema, otype.table, names, equals=[(otype.key, key)], limit=2)
        )
    if not rows:
        raise ObjectError(404, "unknown_object", f"no {otype.label} {key[:80]!r}")
    path = [link_name]
    if then is None:
        target, found, total = _hop(
            settings, view, otype, first, rows[:1], limit=limit, offset=offset
        )
    else:
        middle, between, _ = _hop(settings, view, otype, first, rows[:1], limit=MAX_PAGE)
        second = _link(middle, then)
        path.append(then)
        target, found, total = _hop(
            settings, view, middle, second, between, limit=limit, offset=offset
        )
    return {
        "type": target.to_dict(),
        "from": {"type": otype.class_local, "key": key},
        "path": path,
        "items": overlay.apply(view, target, found),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def bind(
    settings: Any,
    view: Any,
    name: str,
    *,
    key: str | None = None,
    title: str | None = None,
    actor: str = "local",
) -> ObjectType:
    """Bind type *name* by hand: *key* / *title* (None: keep or infer; ``title=""``: no
    title) verified against the source, then the type is ``active``."""
    otype = find_type(view, name)
    if otype.status == "broken" and otype.broken_reason.startswith(binding.SNAPSHOT_REASON):
        raise ObjectError(409, "object_type_not_active", otype.broken_reason)
    with _open(settings, view, otype) as backend:
        checked = binding.verify(backend, otype, key=key, title=title)
    return view.object_types().save(
        replace(checked, status="active", broken_reason="", decided_by=actor)
    )


def decide(settings: Any, view: Any, name: str, decision: str, actor: str) -> ObjectType:
    """Accept (verified against the source, then ``active``) or reject (``retired``) the
    proposed type *name*."""
    otype = find_type(view, name)
    if decision == "accept":
        return bind(settings, view, name, actor=actor)
    if decision == "reject":
        saved = view.object_types().set_status(otype.class_local, "retired", decided_by=actor)
        assert saved is not None
        return saved
    raise ObjectError(400, "invalid_decision", "an objects item takes accept or reject")


def undo(view: Any, name: str) -> ObjectType | None:
    """A decision on *name* was undone: the type is a proposal again."""
    otype = view.object_types().get(name)
    if otype is None:
        return None
    return view.object_types().set_status(otype.class_local, "proposed", decided_by="")
