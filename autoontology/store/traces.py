# SPDX-License-Identifier: MIT
"""Durable request receipts: exact context, output and explicit terminal state.

Reading a receipt never calls a model, queries a live source or rebuilds an index.
Deleting a conversation removes its receipts too; standalone receipts are deletable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime

from autoontology.security.context import authorize
from autoontology.store.db import Store, now_iso, pid_alive
from autoontology.store.operations import digest
from autoontology.store.publications import Publications


class TraceError(ValueError):
    def __init__(self, message: str, code="trace_invalid", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def error_code(exc: BaseException) -> str:
    """Provider exception bodies can contain credentials; receipts keep codes only."""
    return str(getattr(exc, "code", getattr(exc, "error", "execution_failed")))[:80]


class UsageTraces:
    def __init__(self, store: Store):
        self.store = store
        self._clocks: dict[str, float] = {}

    def begin(
        self,
        *,
        kind: str,
        workspace: str,
        revision: int,
        model,
        request: dict,
        conversation_id: str | None = None,
    ) -> str:
        if kind not in ("chat", "retrieval", "sql", "sparql"):
            raise TraceError("unknown trace kind")
        publication = Publications(self.store).check_model(revision)
        trace_id = uuid.uuid4().hex
        stamp = now_iso()
        data = {
            "schema": 1,
            "actor": {"id": "local-unverified", "verified": False},
            "request": request,
            "binding": {
                "revision": revision,
                "publication_id": publication["id"] if publication else None,
                "legacy": publication is None,
                "model_sha256": digest(model.to_dict()),
            },
            "context": {},
            "outcome": {},
            "duration_ms": None,
        }
        with self.store._immediate() as conn:
            authority = authorize(self.store, "query.run", conn=conn)
            if authority is not None:
                data["actor"] = {
                    "id": authority.actor.principal_id,
                    "verified": True,
                    "kind": authority.actor.kind,
                    "credential_id": authority.actor.credential_id,
                    "workspace_id": authority.workspace_id,
                    "policy_version": authority.policy_version,
                }
            conn.execute(
                "INSERT INTO usage_traces VALUES(?,?,?,?,?,'running',?,?,?,NULL,?,NULL)",
                (
                    trace_id,
                    workspace,
                    kind,
                    revision,
                    publication["id"] if publication else None,
                    conversation_id,
                    os.getpid(),
                    stamp,
                    json.dumps(data, ensure_ascii=False),
                ),
            )
        self._clocks[trace_id] = time.monotonic()
        return trace_id

    @staticmethod
    def _record(row) -> dict:
        try:
            record = {k: row[k] for k in row.keys() if k not in ("data_json", "sha256")}
            record["data"] = json.loads(row["data_json"])
        except (ValueError, TypeError) as exc:
            raise TraceError("usage trace integrity check failed") from exc
        if row["status"] != "running" and digest(record) != row["sha256"]:
            raise TraceError("usage trace integrity check failed")
        return {**record, "sha256": row["sha256"]}

    def get(self, trace_id: str) -> dict:
        with self.store._conn() as conn:
            row = conn.execute("SELECT * FROM usage_traces WHERE id=?", (trace_id,)).fetchone()
        if row is None:
            raise TraceError("no usage trace with this identifier", "trace_missing", 404)
        return self._record(row)

    def context(self, trace_id: str, context: dict) -> None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM usage_traces WHERE id=? AND status='running'", (trace_id,)
            ).fetchone()
            if row is None:
                raise TraceError("usage trace is already terminal or deleted")
            data = json.loads(row["data_json"])
            data["context"].update(context)
            conn.execute(
                "UPDATE usage_traces SET data_json=? WHERE id=? AND status='running'",
                (json.dumps(data, ensure_ascii=False), trace_id),
            )

    def finish(
        self, trace_id: str, status: str, outcome: dict, *, conversation_id: str | None = None
    ) -> bool:
        if status not in ("succeeded", "failed", "cancelled", "interrupted"):
            raise TraceError("unknown terminal trace state")
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM usage_traces WHERE id=? AND status='running'", (trace_id,)
            ).fetchone()
            if row is None:
                self._clocks.pop(trace_id, None)
                return False
            record = self._record(row)
            record.pop("sha256")
            record["status"], record["finished_at"] = status, now_iso()
            if conversation_id:
                record["conversation_id"] = conversation_id
            data = record["data"]
            clock = self._clocks.pop(trace_id, None)
            elapsed = (
                time.monotonic() - clock
                if clock is not None
                else (
                    datetime.fromisoformat(record["finished_at"])
                    - datetime.fromisoformat(record["started_at"])
                ).total_seconds()
            )
            data["duration_ms"] = max(0, round(elapsed * 1000, 3))
            data["duration_clock"] = "monotonic" if clock is not None else "wall_after_restart"
            data["outcome"] = outcome
            changed = conn.execute(
                "UPDATE usage_traces SET status=?,conversation_id=?,finished_at=?,data_json=?,sha256=? WHERE id=? AND status='running'",
                (
                    status,
                    record["conversation_id"],
                    record["finished_at"],
                    json.dumps(data, ensure_ascii=False),
                    digest(record),
                    trace_id,
                ),
            ).rowcount
        return changed == 1

    def reap(self) -> None:
        """A dead owner's unfinished request is interrupted, never silently retried."""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT id,owner_pid FROM usage_traces WHERE status='running'"
            ).fetchall()
        for row in rows:
            if not pid_alive(row["owner_pid"]):
                self.finish(
                    row["id"], "interrupted", {"error": "owner_process_exited", "partial": True}
                )

    def listing(self, *, workspace: str, limit: int = 50) -> list[dict]:
        self.reap()
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM usage_traces WHERE workspace=? ORDER BY started_at DESC,rowid DESC LIMIT ?",
                (workspace, max(1, min(500, limit))),
            ).fetchall()
        # Listing avoids duplicating private prompts and results in the overview.
        return [{k: v for k, v in self._record(row).items() if k != "data"} for row in rows]

    def verify(self, trace_id: str) -> dict:
        record = self.get(trace_id)
        return {
            "ok": record["status"] != "running",
            "id": trace_id,
            "status": record["status"],
            "sha256": record["sha256"],
        }

    def delete(self, trace_id: str) -> bool:
        with self.store._conn() as conn:
            row = conn.execute("SELECT status FROM usage_traces WHERE id=?", (trace_id,)).fetchone()
            if row and row[0] == "running":
                raise TraceError("stop the request before deleting its usage record")
            return conn.execute("DELETE FROM usage_traces WHERE id=?", (trace_id,)).rowcount > 0
