# SPDX-License-Identifier: MIT
"""Action runs (schema v10, table ``action_runs``), per project.

One row per submitted action (previews are not stored): what was asked (action, its
version, the object, the parameters), by whom and through which surface (``via``: ui,
cli, agent, workflow; ``token_id`` / ``workflow_run_id``), the preview the requester saw
(``preview_json``), the approval (``approval_role``, ``approver``, ``decided_at``) and
the outcome (``result_json``: the effects written and the notifications sent, ``error``).

Statuses: ``pending_approval`` -> ``applied`` | ``rejected`` | ``failed``;
``applied`` -> ``reverted``. (``previewed`` and ``approved`` exist in the schema; the
runner never leaves a run in them.) ``idempotency_key`` is unique per project: a repeated
submit with the same key finds the first run instead of running again.

Writes take an optional ``conn`` (the runner's ``BEGIN IMMEDIATE`` transaction) so a run
row and its object edits commit together.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

STATUSES: tuple[str, ...] = (
    "previewed",
    "pending_approval",
    "approved",
    "rejected",
    "applied",
    "failed",
    "reverted",
)
VIAS: tuple[str, ...] = ("ui", "cli", "agent", "workflow")
#: rows one listing returns at most
MAX_LIST = 500


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class ActionRun:
    id: str
    action_name: str
    action_version: int
    object_type: str
    object_key: str
    status: str
    params: dict[str, Any] = field(default_factory=dict)
    requested_by: str = ""
    via: str = "ui"
    token_id: str | None = None
    workflow_run_id: str | None = None
    approver: str | None = None
    approval_role: str | None = None
    preview: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    idempotency_key: str | None = None
    created_at: str = ""
    decided_at: str | None = None
    applied_at: str | None = None
    reverted_at: str | None = None
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action_name,
            "action_version": self.action_version,
            "object_type": self.object_type,
            "object_key": self.object_key,
            "params": self.params,
            "status": self.status,
            "requested_by": self.requested_by,
            "via": self.via,
            "token_id": self.token_id,
            "workflow_run_id": self.workflow_run_id,
            "approver": self.approver,
            "approval_role": self.approval_role,
            "preview": self.preview,
            "result": self.result,
            "error": self.error,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "applied_at": self.applied_at,
            "reverted_at": self.reverted_at,
        }


def _run(row: sqlite3.Row) -> ActionRun:
    return ActionRun(
        id=row["id"],
        project_id=row["project_id"],
        action_name=row["action_name"],
        action_version=row["action_version"],
        object_type=row["object_type"],
        object_key=row["object_key"],
        params=_loads(row["params_json"], {}),
        status=row["status"],
        requested_by=row["requested_by"],
        via=row["via"],
        token_id=row["token_id"],
        workflow_run_id=row["workflow_run_id"],
        approver=row["approver"],
        approval_role=row["approval_role"],
        preview=_loads(row["preview_json"], {}),
        result=_loads(row["result_json"], {}),
        error=row["error"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        applied_at=row["applied_at"],
        reverted_at=row["reverted_at"],
    )


#: columns :meth:`ActionRuns.update` may set, with their JSON-encoded ones
_UPDATABLE = {
    "status": None,
    "approver": None,
    "approval_role": None,
    "error": None,
    "decided_at": None,
    "applied_at": None,
    "reverted_at": None,
    "result": "result_json",
    "preview": "preview_json",
}


class ActionRuns:
    """The action runs of one project (``store.project(...).action_runs()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    @contextmanager
    def _use(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.store._conn() as own:
                yield own

    def create(
        self,
        *,
        action_name: str,
        action_version: int,
        object_type: str,
        object_key: str,
        params: dict[str, Any],
        status: str,
        requested_by: str,
        via: str = "ui",
        token_id: str | None = None,
        workflow_run_id: str | None = None,
        approval_role: str | None = None,
        preview: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> ActionRun:
        if status not in STATUSES:
            raise ValueError(f"invalid status {status!r}")
        if via not in VIAS:
            raise ValueError(f"invalid via {via!r}")
        run_id = "act_" + secrets.token_hex(8)
        with self._use(conn) as db:
            db.execute(
                "INSERT INTO action_runs (project_id, id, action_name, action_version, "
                "object_type, object_key, params_json, status, requested_by, via, token_id, "
                "workflow_run_id, approval_role, preview_json, idempotency_key, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    run_id,
                    action_name,
                    int(action_version),
                    object_type,
                    str(object_key),
                    _dumps(params),
                    status,
                    requested_by,
                    via,
                    token_id,
                    workflow_run_id,
                    approval_role,
                    _dumps(preview or {}),
                    idempotency_key,
                    now_iso(),
                ),
            )
            row = db.execute(
                "SELECT * FROM action_runs WHERE project_id = ? AND id = ?",
                (self.project_id, run_id),
            ).fetchone()
        return _run(row)

    def get(self, run_id: str, *, conn: sqlite3.Connection | None = None) -> ActionRun | None:
        if not run_id or len(run_id) > 80:
            return None
        with self._use(conn) as db:
            row = db.execute(
                "SELECT * FROM action_runs WHERE project_id = ? AND id = ?",
                (self.project_id, run_id),
            ).fetchone()
        return _run(row) if row else None

    def by_idempotency_key(
        self, key: str, *, conn: sqlite3.Connection | None = None
    ) -> ActionRun | None:
        with self._use(conn) as db:
            row = db.execute(
                "SELECT * FROM action_runs WHERE project_id = ? AND idempotency_key = ?",
                (self.project_id, key),
            ).fetchone()
        return _run(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        action: str | None = None,
        object_type: str | None = None,
        object_key: str | None = None,
        workflow_run_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionRun]:
        """Runs, newest first, filtered by the arguments given."""
        where, args = ["project_id = ?"], [self.project_id]
        for column, value in (
            ("status", status),
            ("action_name", action),
            ("object_type", object_type),
            ("object_key", object_key),
            ("workflow_run_id", workflow_run_id),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        limit = max(1, min(int(limit), MAX_LIST))
        with self.store._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM action_runs WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [_run(r) for r in rows]

    def update(
        self, run_id: str, *, conn: sqlite3.Connection | None = None, **fields: Any
    ) -> ActionRun:
        """Set *fields* (status, approver, approval_role, error, decided_at, applied_at,
        reverted_at, result, preview) of *run_id*."""
        sets, args = [], []
        for name, value in fields.items():
            if name not in _UPDATABLE:
                raise ValueError(f"cannot update {name!r}")
            if name == "status" and value not in STATUSES:
                raise ValueError(f"invalid status {value!r}")
            column = _UPDATABLE[name] or name
            sets.append(f"{column} = ?")
            args.append(_dumps(value) if _UPDATABLE[name] else value)
        with self._use(conn) as db:
            if sets:
                db.execute(
                    f"UPDATE action_runs SET {', '.join(sets)} WHERE project_id = ? AND id = ?",
                    (*args, self.project_id, run_id),
                )
            row = db.execute(
                "SELECT * FROM action_runs WHERE project_id = ? AND id = ?",
                (self.project_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run(row)

    def merge_result(self, run_id: str, part: dict[str, Any]) -> ActionRun | None:
        """Merge *part* into ``result_json`` (the notification outcome, written after the
        run committed)."""
        with self.store._immediate() as conn:
            row = conn.execute(
                "SELECT result_json FROM action_runs WHERE project_id = ? AND id = ?",
                (self.project_id, run_id),
            ).fetchone()
            if row is None:
                return None
            result = {**_loads(row["result_json"], {}), **part}
            conn.execute(
                "UPDATE action_runs SET result_json = ? WHERE project_id = ? AND id = ?",
                (_dumps(result), self.project_id, run_id),
            )
        return self.get(run_id)
