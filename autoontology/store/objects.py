# SPDX-License-Identifier: MIT
"""Object types and the object edit layer (schema v10, tables ``object_types`` and
``object_edits``), per project.

An **object type** binds a class of the ontology to one source table: the key column that
identifies a row, the title column people read, and the links (foreign-key properties) to
other object types. Lifecycle: ``proposed`` (inferred from a snapshot, waiting in the
review inbox) -> ``active`` (accepted) or ``retired`` (rejected); an active type whose
class, table or columns are gone is ``broken`` until they are back. A type is named by its
class's local name (``term-...``), stable across revisions.

An **object edit** is a value written over a source row without touching the source (the
sources are reloaded or owned elsewhere): the newest edit of ``(type, key, property)``
that is not reverted is the value shown. ``source_value`` is the source's value when the
edit was made, so a later source change shows as a conflict; ``operational`` marks a
property that is no source column (a workflow's state, a reminder stage).
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

STATUSES: tuple[str, ...] = ("proposed", "active", "broken", "retired")
KEY_ORIGINS: tuple[str, ...] = ("pk", "inferred", "manual")
#: store_meta key prefix: the revision the project's object types were last synced with
META_SYNCED = "objects_synced_revision:"


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class ObjectType:
    class_local: str
    label: str = ""
    source_name: str = ""
    schema: str = ""
    table: str = ""
    key_columns: list[str] = field(default_factory=list)
    title_column: str = ""
    #: [{name, label, direction (out | in), column, target}] -- ``out``: this table's
    #: ``column`` holds the target's key; ``in``: the target table's ``column`` holds ours
    links: list[dict[str, Any]] = field(default_factory=list)
    #: the table's columns as last read: [{name, type}]
    columns: list[dict[str, str]] = field(default_factory=list)
    status: str = "proposed"
    broken_reason: str = ""
    key_origin: str = "inferred"
    key_checked: bool = False
    validated_revision: int | None = None
    decided_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: str = ""
    project_id: str = ""

    @property
    def key(self) -> str:
        """The (single) key column ("" when none could be inferred)."""
        return self.key_columns[0] if self.key_columns else ""

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table

    def column_names(self) -> list[str]:
        return [str(c.get("name")) for c in self.columns if c.get("name")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.class_local,
            "class": self.class_local,
            "label": self.label,
            "source": self.source_name,
            "schema": self.schema,
            "table": self.table,
            "key": self.key,
            "key_columns": list(self.key_columns),
            "key_origin": self.key_origin,
            "key_checked": self.key_checked,
            "title_column": self.title_column,
            "links": list(self.links),
            "columns": list(self.columns),
            "status": self.status,
            "broken_reason": self.broken_reason,
            "validated_revision": self.validated_revision,
            "decided_by": self.decided_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _otype(row: sqlite3.Row) -> ObjectType:
    return ObjectType(
        id=row["id"],
        project_id=row["project_id"],
        class_local=row["class_local"],
        label=row["label"],
        source_name=row["source_name"],
        schema=row["schema_name"],
        table=row["table_name"],
        key_columns=_loads(row["key_columns_json"], []),
        title_column=row["title_column"],
        links=_loads(row["links_json"], []),
        columns=_loads(row["columns_json"], []),
        status=row["status"],
        broken_reason=row["broken_reason"],
        key_origin=row["key_origin"],
        key_checked=bool(row["key_checked"]),
        validated_revision=row["validated_revision"],
        decided_by=row["decided_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ObjectTypes:
    """The object types of one project (``store.project(...).object_types()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def get(self, ref: str) -> ObjectType | None:
        """The type named *ref* (its class's local name, or its id)."""
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM object_types WHERE project_id = ? AND (class_local = ? OR id = ?)",
                (self.project_id, ref, ref),
            ).fetchone()
        return _otype(row) if row else None

    def list(self, status: str | tuple[str, ...] | None = None) -> list[ObjectType]:
        sql, args = "SELECT * FROM object_types WHERE project_id = ?", [self.project_id]
        if status is not None:
            wanted = (status,) if isinstance(status, str) else tuple(status)
            sql += f" AND status IN ({', '.join('?' * len(wanted))})"
            args += list(wanted)
        with self.store._conn() as conn:
            rows = conn.execute(sql + " ORDER BY label, class_local", args).fetchall()
        return [_otype(r) for r in rows]

    def save(self, otype: ObjectType) -> ObjectType:
        """Insert or replace *otype* (by its class); returns the stored row."""
        if otype.status not in STATUSES:
            raise ValueError(f"unknown object type status {otype.status!r}")
        if otype.key_origin not in KEY_ORIGINS:
            raise ValueError(f"unknown key origin {otype.key_origin!r}")
        now = now_iso()
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO object_types (project_id, id, class_local, label, source_name, "
                "schema_name, table_name, key_columns_json, title_column, links_json, "
                "columns_json, status, broken_reason, key_origin, key_checked, "
                "validated_revision, decided_by, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (project_id, class_local) DO UPDATE SET label = excluded.label, "
                "source_name = excluded.source_name, schema_name = excluded.schema_name, "
                "table_name = excluded.table_name, key_columns_json = excluded.key_columns_json, "
                "title_column = excluded.title_column, links_json = excluded.links_json, "
                "columns_json = excluded.columns_json, status = excluded.status, "
                "broken_reason = excluded.broken_reason, key_origin = excluded.key_origin, "
                "key_checked = excluded.key_checked, "
                "validated_revision = excluded.validated_revision, "
                "decided_by = excluded.decided_by, updated_at = excluded.updated_at",
                (
                    self.project_id,
                    otype.id or "ot_" + secrets.token_hex(6),
                    otype.class_local,
                    otype.label,
                    otype.source_name,
                    otype.schema,
                    otype.table,
                    _dumps(otype.key_columns),
                    otype.title_column,
                    _dumps(otype.links),
                    _dumps(otype.columns),
                    otype.status,
                    otype.broken_reason,
                    otype.key_origin,
                    int(otype.key_checked),
                    otype.validated_revision,
                    otype.decided_by,
                    otype.created_at or now,
                    now,
                ),
            )
        saved = self.get(otype.class_local)
        assert saved is not None
        return saved

    def set_status(
        self, ref: str, status: str, *, reason: str = "", decided_by: str | None = None
    ) -> ObjectType | None:
        if status not in STATUSES:
            raise ValueError(f"unknown object type status {status!r}")
        sets, args = (
            ["status = ?", "broken_reason = ?", "updated_at = ?"],
            [status, reason, now_iso()],
        )
        if decided_by is not None:
            sets.append("decided_by = ?")
            args.append(decided_by)
        with self.store._conn() as conn:
            conn.execute(
                f"UPDATE object_types SET {', '.join(sets)} "
                "WHERE project_id = ? AND (class_local = ? OR id = ?)",
                (*args, self.project_id, ref, ref),
            )
        return self.get(ref)

    def delete(self, ref: str) -> bool:
        with self.store._conn() as conn:
            cur = conn.execute(
                "DELETE FROM object_types WHERE project_id = ? AND (class_local = ? OR id = ?)",
                (self.project_id, ref, ref),
            )
        return cur.rowcount > 0

    # --- the snapshot they were synced with ---------------------------------------------

    def synced_revision(self) -> int | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT value FROM store_meta WHERE key = ?", (META_SYNCED + self.project_id,)
            ).fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else None

    def set_synced_revision(self, revision: int) -> None:
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO store_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (META_SYNCED + self.project_id, str(revision)),
            )


@dataclass
class ObjectEdit:
    id: int
    object_type: str
    object_key: str
    property: str
    new_value: Any
    old_value: Any
    source_value: Any
    operational: bool
    action_run_id: str | None
    edited_by: str
    edited_at: str
    reverted_at: str | None = None
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object_type": self.object_type,
            "object_key": self.object_key,
            "property": self.property,
            "value": self.new_value,
            "old_value": self.old_value,
            "source_value": self.source_value,
            "operational": self.operational,
            "action_run_id": self.action_run_id,
            "edited_by": self.edited_by,
            "edited_at": self.edited_at,
            "reverted_at": self.reverted_at,
        }


def _edit(row: sqlite3.Row) -> ObjectEdit:
    return ObjectEdit(
        id=row["id"],
        project_id=row["project_id"],
        object_type=row["object_type"],
        object_key=row["object_key"],
        property=row["property"],
        new_value=_loads(row["new_value_json"], None),
        old_value=_loads(row["old_value_json"], None),
        source_value=_loads(row["source_value_json"], None),
        operational=bool(row["operational"]),
        action_run_id=row["action_run_id"],
        edited_by=row["edited_by"],
        edited_at=row["edited_at"],
        reverted_at=row["reverted_at"],
    )


class ObjectEdits:
    """The object edit layer of one project (``store.project(...).object_edits()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def add(
        self,
        object_type: str,
        object_key: str,
        prop: str,
        *,
        new_value: Any,
        old_value: Any = None,
        source_value: Any = None,
        operational: bool = False,
        action_run_id: str | None = None,
        edited_by: str = "",
    ) -> ObjectEdit:
        with self.store._conn() as conn:
            cur = conn.execute(
                "INSERT INTO object_edits (project_id, object_type, object_key, property, "
                "old_value_json, new_value_json, source_value_json, operational, "
                "action_run_id, edited_by, edited_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    object_type,
                    str(object_key),
                    prop,
                    _dumps(old_value),
                    _dumps(new_value),
                    _dumps(source_value),
                    int(operational),
                    action_run_id,
                    edited_by,
                    now_iso(),
                ),
            )
            edit_id = cur.lastrowid
        found = self.get(int(edit_id or 0))
        assert found is not None
        return found

    def get(self, edit_id: int) -> ObjectEdit | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM object_edits WHERE project_id = ? AND id = ?",
                (self.project_id, edit_id),
            ).fetchone()
        return _edit(row) if row else None

    def current(
        self, object_type: str, keys: list[str] | None = None
    ) -> dict[tuple[str, str], ObjectEdit]:
        """The newest edit that is not reverted per ``(object key, property)`` of
        *object_type* (only *keys*, when given)."""
        sql = (
            "SELECT * FROM object_edits WHERE project_id = ? AND object_type = ? "
            "AND reverted_at IS NULL"
        )
        args: list[Any] = [self.project_id, object_type]
        if keys is not None:
            if not keys:
                return {}
            sql += f" AND object_key IN ({', '.join('?' * len(keys))})"
            args += [str(k) for k in keys]
        with self.store._conn() as conn:
            rows = conn.execute(sql + " ORDER BY id", args).fetchall()
        out: dict[tuple[str, str], ObjectEdit] = {}
        for row in rows:  # ascending id: the newest wins
            edit = _edit(row)
            out[(edit.object_key, edit.property)] = edit
        return out

    def history(self, object_type: str, object_key: str) -> list[ObjectEdit]:
        """Every edit of one object, newest first (reverted ones included)."""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM object_edits WHERE project_id = ? AND object_type = ? "
                "AND object_key = ? ORDER BY id DESC",
                (self.project_id, object_type, str(object_key)),
            ).fetchall()
        return [_edit(r) for r in rows]

    def by_run(self, action_run_id: str) -> list[ObjectEdit]:
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM object_edits WHERE project_id = ? AND action_run_id = ? ORDER BY id",
                (self.project_id, action_run_id),
            ).fetchall()
        return [_edit(r) for r in rows]

    def revert(self, edit_id: int) -> ObjectEdit | None:
        """Mark one edit reverted (the property shows its previous edit, or the source
        value, again); None when there is no such edit or it was already reverted."""
        with self.store._conn() as conn:
            cur = conn.execute(
                "UPDATE object_edits SET reverted_at = ? "
                "WHERE project_id = ? AND id = ? AND reverted_at IS NULL",
                (now_iso(), self.project_id, edit_id),
            )
        return self.get(edit_id) if cur.rowcount else None

    def revert_run(self, action_run_id: str) -> list[ObjectEdit]:
        """Revert every edit an action run made; returns the edits reverted now."""
        pending = [e for e in self.by_run(action_run_id) if e.reverted_at is None]
        return [r for r in (self.revert(e.id) for e in pending) if r is not None]
