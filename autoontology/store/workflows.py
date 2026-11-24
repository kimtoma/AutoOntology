# SPDX-License-Identifier: MIT
"""Workflow runs (schema v10, table ``workflow_runs``), per project.

One row per firing of a workflow: which workflow (name and the version that ran), the
trigger (``schedule``, ``on_action``, ``manual``) and its ``fire_slot`` -- unique per
project and workflow, so a schedule slot or an action run starts a workflow once however
many ticks, processes or listeners see it (:meth:`WorkflowRuns.start` returns the
existing row on a duplicate).

``steps_json`` holds the run's state (a mapping, see :mod:`autoontology.logic.workflows`):
``objects`` (per-object detail, bounded), ``paused`` (continuations keyed by the pending
action run id) and ``resuming`` (continuations being resumed). ``summary_json`` holds the
counts and run facts. Every change goes through :meth:`WorkflowRuns.mutate`, a
read-modify-write inside one ``BEGIN IMMEDIATE`` transaction, so the iterating thread and
resumes from approvals never lose each other's updates.

Statuses: ``running`` -> ``paused`` (every object done but some wait for an approval) ->
``succeeded`` | ``failed``; ``running`` / ``paused`` -> ``cancelled``.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.db import Store, now_iso

TRIGGERS: tuple[str, ...] = ("schedule", "on_action", "manual")
STATUSES: tuple[str, ...] = ("running", "paused", "succeeded", "failed", "cancelled")
OPEN: tuple[str, ...] = ("running", "paused")
#: rows one listing returns at most
MAX_LIST = 500
RUN_ID_PREFIX = "wfr_"


def _loads(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class WorkflowRun:
    id: str
    workflow_name: str
    workflow_version: int
    trigger: str
    fire_slot: str
    status: str
    started_by: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    project_id: str = ""

    @property
    def open(self) -> bool:
        return self.status in OPEN

    def to_dict(self, *, detail: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "workflow": self.workflow_name,
            "workflow_version": self.workflow_version,
            "trigger": self.trigger,
            "fire_slot": self.fire_slot,
            "status": self.status,
            "started_by": self.started_by,
            "summary": self.summary,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if detail:
            out["objects"] = list(self.state.get("objects") or [])
            out["paused"] = [
                {"action_run_id": run_id, **entry}
                for run_id, entry in (self.state.get("paused") or {}).items()
            ]
        return out


def _run(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(
        id=row["id"],
        project_id=row["project_id"],
        workflow_name=row["workflow_name"],
        workflow_version=row["workflow_version"],
        trigger=row["trigger"],
        fire_slot=row["fire_slot"],
        status=row["status"],
        started_by=row["started_by"],
        state=_loads(row["steps_json"], {}),
        summary=_loads(row["summary_json"], {}),
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class RunActive(Exception):
    """The workflow already has a ``running`` run (:meth:`WorkflowRuns.start` with
    ``exclusive``)."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


class WorkflowRuns:
    """The workflow runs of one project (``store.project(...).workflow_runs()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def _get(self, conn: sqlite3.Connection, run_id: str) -> WorkflowRun | None:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE project_id = ? AND id = ?",
            (self.project_id, run_id),
        ).fetchone()
        return _run(row) if row else None

    def start(
        self,
        *,
        workflow_name: str,
        workflow_version: int,
        trigger: str,
        fire_slot: str,
        started_by: str,
        summary: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        exclusive: bool = False,
    ) -> tuple[WorkflowRun, bool]:
        """Insert a ``running`` run: ``(run, True)``; ``(the existing run, False)`` when
        this workflow already has a run for *fire_slot*. *exclusive*: raise
        :class:`RunActive` instead when the workflow has a ``running`` run (checked in the
        same transaction)."""
        if trigger not in TRIGGERS:
            raise ValueError(f"invalid trigger {trigger!r}")
        run_id = RUN_ID_PREFIX + secrets.token_hex(8)
        with self.store._immediate() as conn:
            found = conn.execute(
                "SELECT * FROM workflow_runs WHERE project_id = ? AND workflow_name = ? "
                "AND fire_slot = ?",
                (self.project_id, workflow_name, fire_slot),
            ).fetchone()
            if found is not None:
                return _run(found), False
            if exclusive:
                busy = conn.execute(
                    "SELECT id FROM workflow_runs WHERE project_id = ? AND workflow_name = ? "
                    "AND status = 'running' LIMIT 1",
                    (self.project_id, workflow_name),
                ).fetchone()
                if busy is not None:
                    raise RunActive(str(busy[0]))
            try:
                conn.execute(
                    "INSERT INTO workflow_runs (project_id, id, workflow_name, "
                    "workflow_version, trigger, fire_slot, status, started_by, steps_json, "
                    "summary_json, started_at) VALUES (?,?,?,?,?,?,'running',?,?,?,?)",
                    (
                        self.project_id,
                        run_id,
                        workflow_name,
                        int(workflow_version),
                        trigger,
                        fire_slot,
                        started_by,
                        _dumps(state or {}),
                        _dumps(summary or {}),
                        now_iso(),
                    ),
                )
            except sqlite3.IntegrityError:  # pragma: no cover - the lock makes this rare
                found = conn.execute(
                    "SELECT * FROM workflow_runs WHERE project_id = ? AND workflow_name = ? "
                    "AND fire_slot = ?",
                    (self.project_id, workflow_name, fire_slot),
                ).fetchone()
                return _run(found), False
            run = self._get(conn, run_id)
        assert run is not None
        return run, True

    def get(self, run_id: str) -> WorkflowRun | None:
        if not run_id or len(run_id) > 80:
            return None
        with self.store._conn() as conn:
            return self._get(conn, run_id)

    def list(
        self,
        *,
        workflow: str | None = None,
        status: str | tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        """Runs, newest first."""
        where, args = ["project_id = ?"], [self.project_id]
        if workflow is not None:
            where.append("workflow_name = ?")
            args.append(workflow)
        if status is not None:
            wanted = (status,) if isinstance(status, str) else tuple(status)
            where.append(f"status IN ({', '.join('?' * len(wanted))})")
            args += list(wanted)
        limit = max(1, min(int(limit), MAX_LIST))
        with self.store._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM workflow_runs WHERE {' AND '.join(where)} "
                "ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [_run(r) for r in rows]

    def latest(self, workflow: str) -> WorkflowRun | None:
        found = self.list(workflow=workflow, limit=1)
        return found[0] if found else None

    def mutate(
        self, run_id: str, change: Callable[[WorkflowRun], Any]
    ) -> tuple[WorkflowRun | None, Any]:
        """Read the run, let *change* edit it in place (``state``, ``summary``,
        ``status``, ``error``, ``finished_at``) and write it back, atomically. Returns
        ``(the run as written, what change returned)``; ``(None, None)`` for an unknown
        run. *change* returning the sentinel :data:`UNCHANGED` writes nothing."""
        with self.store._immediate() as conn:
            run = self._get(conn, run_id)
            if run is None:
                return None, None
            result = change(run)
            if result is UNCHANGED:
                return run, None
            if run.status not in STATUSES:
                raise ValueError(f"invalid status {run.status!r}")
            conn.execute(
                "UPDATE workflow_runs SET status = ?, steps_json = ?, summary_json = ?, "
                "error = ?, finished_at = ? WHERE project_id = ? AND id = ?",
                (
                    run.status,
                    _dumps(run.state),
                    _dumps(run.summary),
                    run.error,
                    run.finished_at,
                    self.project_id,
                    run_id,
                ),
            )
            written = self._get(conn, run_id)
        return written, result


#: what a :meth:`WorkflowRuns.mutate` change returns to write nothing
UNCHANGED = object()
