# SPDX-License-Identifier: MIT
"""Logic definitions (schema v10, tables ``logic_defs`` and ``logic_def_history``), per
project: functions, actions and workflows as versioned specs.

Lifecycle: ``draft`` (saved, not used) -> ``active`` (used) -> ``retired``. A definition
whose spec no longer validates against the ontology is ``broken`` (``broken_reason``)
and goes back to the status it had once it validates again (the newest non-broken
status in its history). Every save (a new version) and every status change writes a
history row.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

KINDS: tuple[str, ...] = ("function", "action", "workflow")
STATUSES: tuple[str, ...] = ("draft", "active", "retired", "broken")
ORIGINS: tuple[str, ...] = ("manual", "extracted")
#: store_meta key prefix: the fingerprint the definitions were last revalidated at
META_VALIDATED = "logic_validated:"


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class LogicDef:
    kind: str
    name: str
    spec: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    status: str = "draft"
    origin: str = "manual"
    evidence: list[Any] = field(default_factory=list)
    broken_reason: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: str = ""
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "label": self.spec.get("label") or "",
            "object_type": self.spec.get("object_type")
            or (self.spec.get("for_each") or {}).get("object_type", ""),
            "version": self.version,
            "status": self.status,
            "origin": self.origin,
            "spec": self.spec,
            "evidence": self.evidence,
            "broken_reason": self.broken_reason,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _def(row: sqlite3.Row) -> LogicDef:
    return LogicDef(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        name=row["name"],
        version=row["version"],
        status=row["status"],
        spec=_loads(row["spec_json"], {}),
        origin=row["origin"],
        evidence=_loads(row["evidence_json"], []),
        broken_reason=row["broken_reason"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class LogicDefs:
    """The logic definitions of one project (``store.project(...).logic_defs()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def get(self, kind: str, name: str) -> LogicDef | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logic_defs WHERE project_id = ? AND kind = ? AND name = ?",
                (self.project_id, kind, name),
            ).fetchone()
        return _def(row) if row else None

    def list(
        self, kind: str | None = None, status: str | tuple[str, ...] | None = None
    ) -> list[LogicDef]:
        sql, args = "SELECT * FROM logic_defs WHERE project_id = ?", [self.project_id]
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        if status is not None:
            wanted = (status,) if isinstance(status, str) else tuple(status)
            sql += f" AND status IN ({', '.join('?' * len(wanted))})"
            args += list(wanted)
        with self.store._conn() as conn:
            rows = conn.execute(sql + " ORDER BY kind, name", args).fetchall()
        return [_def(r) for r in rows]

    def _history(self, conn: sqlite3.Connection, d: LogicDef, actor: str, note: str = "") -> None:
        conn.execute(
            "INSERT INTO logic_def_history (project_id, def_id, version, spec_json, status, "
            "changed_by, changed_at) VALUES (?,?,?,?,?,?,?)",
            (
                self.project_id,
                d.id,
                d.version,
                _dumps({"spec": d.spec, "note": note} if note else {"spec": d.spec}),
                d.status,
                actor,
                now_iso(),
            ),
        )

    def save(
        self,
        kind: str,
        name: str,
        spec: dict[str, Any],
        *,
        actor: str = "",
        origin: str = "manual",
        evidence: list[Any] | None = None,
    ) -> LogicDef:
        """Create *name* as a draft (version 1), or save a new version of it: the version
        goes up and the definition is a draft again (an edited definition is activated
        again on purpose). An unchanged spec of a draft changes nothing."""
        if kind not in KINDS:
            raise ValueError(f"unknown logic kind {kind!r}")
        if origin not in ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        now = now_iso()
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logic_defs WHERE project_id = ? AND kind = ? AND name = ?",
                (self.project_id, kind, name),
            ).fetchone()
            if row is None:
                d = LogicDef(
                    kind=kind,
                    name=name,
                    spec=spec,
                    origin=origin,
                    evidence=list(evidence or []),
                    created_by=actor,
                    id="ld_" + secrets.token_hex(6),
                )
                conn.execute(
                    "INSERT INTO logic_defs (project_id, id, kind, name, version, status, "
                    "spec_json, origin, evidence_json, broken_reason, created_by, created_at, "
                    "updated_at) VALUES (?,?,?,?,1,'draft',?,?,?,'',?,?,?)",
                    (
                        self.project_id,
                        d.id,
                        kind,
                        name,
                        _dumps(spec),
                        origin,
                        _dumps(d.evidence),
                        actor,
                        now,
                        now,
                    ),
                )
                self._history(conn, d, actor, "created")
            else:
                d = _def(row)
                if d.status == "draft" and _dumps(d.spec) == _dumps(spec):
                    return d
                d.version += 1
                d.spec, d.status, d.broken_reason = spec, "draft", ""
                if evidence is not None:
                    d.evidence = list(evidence)
                conn.execute(
                    "UPDATE logic_defs SET version = ?, status = 'draft', spec_json = ?, "
                    "evidence_json = ?, broken_reason = '', updated_at = ? WHERE id = ?",
                    (d.version, _dumps(spec), _dumps(d.evidence), now, d.id),
                )
                self._history(conn, d, actor, "saved")
        saved = self.get(kind, name)
        assert saved is not None
        return saved

    def set_status(
        self,
        kind: str,
        name: str,
        status: str,
        *,
        actor: str = "",
        reason: str = "",
        record_same: bool = False,
    ) -> LogicDef | None:
        """Change the status (``broken`` with *reason*); a history row records it.
        *record_same*: record a history row even when the status does not change (an
        owner activating an already active workflow becomes its activator)."""
        if status not in STATUSES:
            raise ValueError(f"unknown logic status {status!r}")
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logic_defs WHERE project_id = ? AND kind = ? AND name = ?",
                (self.project_id, kind, name),
            ).fetchone()
            if row is None:
                return None
            d = _def(row)
            reason = reason if status == "broken" else ""
            if d.status == status and d.broken_reason == reason:
                if record_same:
                    self._history(conn, d, actor, f"status {status} (again)")
                return d
            d.status, d.broken_reason = status, reason
            conn.execute(
                "UPDATE logic_defs SET status = ?, broken_reason = ?, updated_at = ? WHERE id = ?",
                (status, reason, now_iso(), d.id),
            )
            self._history(conn, d, actor, reason or f"status {status}")
        return self.get(kind, name)

    def history(self, kind: str, name: str) -> list[dict[str, Any]]:
        """Every saved version and status change, newest first."""
        d = self.get(kind, name)
        if d is None:
            return []
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM logic_def_history WHERE project_id = ? AND def_id = ? "
                "ORDER BY id DESC",
                (self.project_id, d.id),
            ).fetchall()
        out = []
        for r in rows:
            body = _loads(r["spec_json"], {})
            out.append(
                {
                    "version": r["version"],
                    "status": r["status"],
                    "spec": body.get("spec", {}),
                    "note": body.get("note", ""),
                    "changed_by": r["changed_by"],
                    "changed_at": r["changed_at"],
                }
            )
        return out

    def status_before_broken(self, kind: str, name: str) -> str:
        """The newest status of *name* that was not ``broken`` (``draft`` when none)."""
        for row in self.history(kind, name):
            if row["status"] != "broken":
                return row["status"]
        return "draft"

    # --- the revalidation marker ------------------------------------------------------

    def validated_fingerprint(self) -> str | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT value FROM store_meta WHERE key = ?", (META_VALIDATED + self.project_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def set_validated_fingerprint(self, value: str) -> None:
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO store_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (META_VALIDATED + self.project_id, value),
            )
