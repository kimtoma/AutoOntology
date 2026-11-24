# SPDX-License-Identifier: MIT
"""The edit layer over source rows (``object_edits``): what actions write, shown on top
of the source values without ever writing to a source.

* :func:`apply` -- source rows of one object type as object records: every property's
  newest edit that is not reverted replaces the source value; an edit whose recorded
  source value differs from the source's current value is a ``conflict`` (the source
  changed after the edit); edits of properties that are no source column are
  ``operational`` properties of the object.
* :func:`record_edit` -- write one edit (the action runner's primitive).
* :func:`revert` -- revert one edit, or every edit of an action run.
"""

from __future__ import annotations

import json
from typing import Any

from autoontology.objects.reader import jsonable
from autoontology.store.objects import ObjectEdit, ObjectType


def _same(a: Any, b: Any) -> bool:
    """Equal as JSON (a source value and a stored one went through different paths)."""
    dump = lambda v: json.dumps(jsonable(v), ensure_ascii=False, sort_keys=True)  # noqa: E731
    return dump(a) == dump(b)


def object_key(otype: ObjectType, row: dict[str, Any]) -> str:
    value = row.get(otype.key)
    return "" if value is None else str(value)


def record(
    otype: ObjectType,
    row: dict[str, Any],
    edits: dict[str, ObjectEdit] | None = None,
) -> dict[str, Any]:
    """One object: ``{type, key, title, values, source, edits, conflicts}``. ``values``
    is what to show (edits applied), ``source`` the source row as read; ``edits`` maps
    each edited property to ``{edit_id, value, source_value, source_value_at_edit,
    conflict, operational, edited_by, edited_at, action_run_id}``."""
    values = dict(row)
    marks: dict[str, dict[str, Any]] = {}
    for prop, edit in sorted((edits or {}).items()):
        in_source = prop in row
        operational = edit.operational or not in_source
        current = row.get(prop) if in_source else None
        conflict = not operational and not _same(current, edit.source_value)
        values[prop] = edit.new_value
        marks[prop] = {
            "edit_id": edit.id,
            "value": edit.new_value,
            "source_value": current,
            "source_value_at_edit": edit.source_value,
            "conflict": conflict,
            "operational": operational,
            "edited_by": edit.edited_by,
            "edited_at": edit.edited_at,
            "action_run_id": edit.action_run_id,
        }
    title = values.get(otype.title_column) if otype.title_column else None
    key = object_key(otype, row)
    return {
        "type": otype.class_local,
        "key": key,
        "title": title if title not in (None, "") else key,
        "values": values,
        "source": row,
        "edits": marks,
        "conflicts": sorted(p for p, m in marks.items() if m["conflict"]),
    }


def apply(view: Any, otype: ObjectType, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* (source rows of *otype*) as :func:`record` objects with the project's
    current edits applied."""
    keys = [object_key(otype, r) for r in rows]
    current = view.object_edits().current(otype.class_local, keys)
    by_key: dict[str, dict[str, ObjectEdit]] = {}
    for (key, prop), edit in current.items():
        by_key.setdefault(key, {})[prop] = edit
    return [record(otype, r, by_key.get(k)) for r, k in zip(rows, keys, strict=True)]


def record_edit(
    view: Any,
    otype: ObjectType,
    key: str,
    prop: str,
    value: Any,
    *,
    source_row: dict[str, Any] | None,
    action_run_id: str | None = None,
    edited_by: str = "",
    operational: bool | None = None,
    conn: Any = None,
) -> ObjectEdit:
    """Write *value* over property *prop* of object *key*. *source_row*: the object's
    source row as read now (its value of *prop* is recorded, for conflict detection);
    ``operational`` defaults to "*prop* is no column of the type". ``old_value`` is the
    value shown before (the previous edit's, else the source's). *conn*: write inside the
    caller's open transaction (several edits and an action run atomically)."""
    columns = set(otype.column_names()) or set(source_row or {})
    if operational is None:
        operational = prop not in columns
    source_value = None if operational or source_row is None else source_row.get(prop)
    edits = view.object_edits()
    previous = edits.current(otype.class_local, [str(key)], conn=conn).get((str(key), prop))
    old_value = previous.new_value if previous is not None else source_value
    return edits.add(
        otype.class_local,
        str(key),
        prop,
        new_value=jsonable(value),
        old_value=old_value,
        source_value=jsonable(source_value),
        operational=operational,
        action_run_id=action_run_id,
        edited_by=edited_by,
        conn=conn,
    )


def revert(
    view: Any,
    *,
    edit_id: int | None = None,
    action_run_id: str | None = None,
    conn: Any = None,
) -> list[ObjectEdit]:
    """Revert one edit (*edit_id*) or every edit of an action run (*action_run_id*);
    returns the edits reverted now (empty: nothing left to revert)."""
    edits = view.object_edits()
    if (edit_id is None) == (action_run_id is None):
        raise ValueError("give edit_id or action_run_id")
    if edit_id is not None:
        done = edits.revert(edit_id, conn=conn)
        return [done] if done is not None else []
    return edits.revert_run(str(action_run_id), conn=conn)
