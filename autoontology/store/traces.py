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

from autoontology.security.context import audit_effect, authorize
from autoontology.security.policy import AccessError
from autoontology.security.results import require_result, selection
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
        self._writers: set[str] = set()

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
            if conversation_id:
                conversation = conn.execute(
                    "SELECT principal_id,identity_workspace FROM playground_conversations WHERE id=?",
                    (conversation_id,),
                ).fetchone()
                if conversation is None:
                    raise TraceError("No conversation with this identifier.", "trace_missing", 404)
                require_result(
                    self.store,
                    conversation["principal_id"],
                    conversation["identity_workspace"],
                    action="query.run",
                    conn=conn,
                )
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
                "INSERT INTO usage_traces (id,workspace,kind,revision,publication_id,status,conversation_id,owner_pid,started_at,finished_at,data_json,sha256,principal_id,identity_workspace) VALUES(?,?,?,?,?,'running',?,?,?,NULL,?,NULL,?,?)",
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
                    authority.actor.principal_id if authority else None,
                    authority.workspace_id if authority else None,
                ),
            )
        self._clocks[trace_id] = time.monotonic()
        self._writers.add(trace_id)
        return trace_id

    @staticmethod
    def _record(row) -> dict:
        try:
            # Ownership columns are an index of the already-hashed actor, not a
            # reason to rewrite historical receipt hashes when adding schema v13.
            record = {
                k: row[k]
                for k in row.keys()
                if k not in ("data_json", "sha256", "principal_id", "identity_workspace")
            }
            record["data"] = json.loads(row["data_json"])
        except (ValueError, TypeError) as exc:
            raise TraceError("usage trace integrity check failed") from exc
        if row["status"] != "running" and digest(record) != row["sha256"]:
            raise TraceError("usage trace integrity check failed")
        if row["principal_id"] is not None and (
            record["data"].get("actor", {}).get("verified") is not True
            or record["data"]["actor"].get("id") != row["principal_id"]
            or record["data"]["actor"].get("workspace_id") != row["identity_workspace"]
        ):
            raise TraceError("usage trace ownership differs from its receipt")
        return {**record, "sha256": row["sha256"]}

    def get(self, trace_id: str, *, action="results.read") -> dict:
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM usage_traces WHERE id=?", (trace_id,)).fetchone()
            if row is None:
                raise TraceError("no usage trace with this identifier", "trace_missing", 404)
            require_result(
                self.store, row["principal_id"], row["identity_workspace"], action=action, conn=conn
            )
            return self._record(row)

    def context(self, trace_id: str, context: dict) -> None:
        with self.store._immediate() as conn:
            row = conn.execute(
                "SELECT * FROM usage_traces WHERE id=? AND status='running'", (trace_id,)
            ).fetchone()
            if row is None:
                raise TraceError("usage trace is already terminal or deleted")
            if (
                conn.execute("SELECT 1 FROM identity_state").fetchone()
                and trace_id not in self._writers
            ):
                raise AccessError(
                    "Only the originating request may update this trace.",
                    "trace_producer_required",
                    403,
                )
            require_result(
                self.store,
                row["principal_id"],
                row["identity_workspace"],
                action="query.run",
                conn=conn,
            )
            data = json.loads(row["data_json"])
            data["context"].update(context)
            conn.execute(
                "UPDATE usage_traces SET data_json=? WHERE id=? AND status='running'",
                (json.dumps(data, ensure_ascii=False), trace_id),
            )

    def finish(
        self, trace_id: str, status: str, outcome: dict, *, conversation_id: str | None = None
    ) -> bool:
        return self._finish(trace_id, status, outcome, conversation_id=conversation_id)

    def _finish(self, trace_id, status, outcome, *, conversation_id=None, maintenance=False):
        if status not in ("succeeded", "failed", "cancelled", "interrupted"):
            raise TraceError("unknown terminal trace state")
        with self.store._immediate() as conn:
            row = conn.execute(
                "SELECT * FROM usage_traces WHERE id=? AND status='running'", (trace_id,)
            ).fetchone()
            if row is None:
                self._clocks.pop(trace_id, None)
                self._writers.discard(trace_id)
                return False
            configured = conn.execute("SELECT 1 FROM identity_state").fetchone()
            if maintenance:
                if status != "interrupted" or pid_alive(row["owner_pid"]):
                    raise TraceError("only a dead producer may be reaped")
            elif configured and trace_id not in self._writers:
                raise AccessError(
                    "Only the originating request may finish this trace.",
                    "trace_producer_required",
                    403,
                )
            if status == "succeeded":
                require_result(
                    self.store,
                    row["principal_id"],
                    row["identity_workspace"],
                    action="query.run",
                    conn=conn,
                )
            if conversation_id:
                conversation = conn.execute(
                    "SELECT principal_id,identity_workspace FROM playground_conversations WHERE id=?",
                    (conversation_id,),
                ).fetchone()
                if conversation is None or tuple(conversation) != (
                    row["principal_id"],
                    row["identity_workspace"],
                ):
                    raise TraceError("conversation ownership differs from its request")
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
        self._writers.discard(trace_id)
        return changed == 1

    def reap(self) -> None:
        """A dead owner's unfinished request is interrupted, never silently retried."""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT id,owner_pid FROM usage_traces WHERE status='running'"
            ).fetchall()
        for row in rows:
            if not pid_alive(row["owner_pid"]):
                self._finish(
                    row["id"],
                    "interrupted",
                    {"error": "owner_process_exited", "partial": True},
                    maintenance=True,
                )

    def listing(self, *, workspace: str, limit: int = 50, all_users=False) -> list[dict]:
        self.reap()
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            owner = selection(self.store, all_users=all_users, conn=conn)
            clause = " AND principal_id=? AND identity_workspace=?" if owner else ""
            rows = conn.execute(
                "SELECT * FROM usage_traces WHERE workspace=?"
                + clause
                + " ORDER BY started_at DESC,rowid DESC LIMIT ?",
                (workspace, *(owner or ()), max(1, min(500, limit))),
            ).fetchall()
            names = {
                r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM identity_principals")
            }
        # Listing avoids duplicating private prompts and results in the overview.
        return [
            {
                **{k: v for k, v in self._record(row).items() if k != "data"},
                "owner": {
                    "principal_id": row["principal_id"],
                    "name": names.get(row["principal_id"]),
                    "legacy": row["principal_id"] is None,
                },
            }
            for row in rows
        ]

    def verify(self, trace_id: str) -> dict:
        record = self.get(trace_id)
        return {
            "ok": record["status"] != "running",
            "id": trace_id,
            "status": record["status"],
            "sha256": record["sha256"],
        }

    def delete(self, trace_id: str) -> bool:
        with self.store._immediate() as conn:
            row = conn.execute("SELECT * FROM usage_traces WHERE id=?", (trace_id,)).fetchone()
            if row:
                decision = require_result(
                    self.store,
                    row["principal_id"],
                    row["identity_workspace"],
                    action="results.delete",
                    conn=conn,
                )
            if row and row["status"] == "running":
                raise TraceError("stop the request before deleting its usage record")
            removed = conn.execute("DELETE FROM usage_traces WHERE id=?", (trace_id,)).rowcount > 0
            if removed:
                audit_effect(
                    self.store,
                    decision,
                    trace_id,
                    {"kind": "usage_trace", "owner": row["principal_id"]},
                    conn=conn,
                )
            return removed
