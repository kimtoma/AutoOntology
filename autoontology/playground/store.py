# SPDX-License-Identifier: MIT
"""Playground tables: element index and conversations.

Shares :class:`autoontology.store.db.Store`'s connection helper, like ``AlignmentStore``."""

from __future__ import annotations

import json
import uuid
from array import array
from dataclasses import dataclass
from typing import Any

from autoontology.security.context import audit_effect, authorize, authorize_any
from autoontology.security.policy import AccessError
from autoontology.security.query_scope import message_scope
from autoontology.security.results import require_result, selection
from autoontology.store.db import Store, now_iso

VERDICTS: tuple[str, ...] = ("pass", "fail", "unsure")
#: newest conversations kept per source / runs kept per test (older ones pruned on insert)
MAX_CONVERSATIONS = 50


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    out = array("f")
    out.frombytes(blob)
    return out.tolist()


@dataclass
class IndexRow:
    element_id: str
    element_type: str
    text_sha: str
    embedding: list[float] | None
    model: str


class PlaygroundStore:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _conn(self):
        return self.store._conn()

    # --- element index ------------------------------------------------------------------

    def index_meta(self, scope: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM playground_index_meta WHERE scope = ?", (scope,)
            ).fetchone()
        return dict(row) if row else None

    def index_rows(self, scope: str) -> dict[str, IndexRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM playground_index WHERE scope = ?", (scope,)
            ).fetchall()
        return {
            r["element_id"]: IndexRow(
                r["element_id"],
                r["element_type"],
                r["text_sha"],
                unpack(r["embedding"]),
                r["model"],
            )
            for r in rows
        }

    def replace_index(self, scope: str, rows: list[IndexRow], *, revision: int, model: str) -> None:
        """The index of *scope* becomes exactly *rows* (one transaction)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM playground_index WHERE scope = ?", (scope,))
            conn.executemany(
                "INSERT INTO playground_index (scope, element_id, element_type, text_sha, "
                "embedding, model) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        scope,
                        r.element_id,
                        r.element_type,
                        r.text_sha,
                        pack(r.embedding) if r.embedding else None,
                        r.model,
                    )
                    for r in rows
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO playground_index_meta (scope, revision, model, "
                "elements, built_at) VALUES (?, ?, ?, ?, ?)",
                (scope, revision, model, len(rows), now_iso()),
            )

    # --- conversations ------------------------------------------------------------------

    def create_conversation(
        self, scope: str, title: str, *, trace_writer=None, trace_id=None
    ) -> str:
        cid = uuid.uuid4().hex
        stamp = now_iso()
        with self.store._immediate() as conn:
            decision = authorize_any(self.store, "query.run", conn=conn)
            principal = decision.actor.principal_id if decision else None
            workspace = decision.workspace_id if decision else None
            conn.execute(
                "INSERT INTO playground_conversations (id, scope, title, created_at, "
                "updated_at,principal_id,identity_workspace) VALUES (?, ?, ?, ?, ?,?,?)",
                (cid, scope, title[:200], stamp, stamp, principal, workspace),
            )
            if trace_writer is not None and trace_id:
                if trace_writer.store.path != self.store.path:
                    raise AccessError("A request cannot bind another workspace.")
                trace_writer.attach_conversation(trace_id, cid, conn=conn)
            conn.execute(
                "DELETE FROM playground_conversations WHERE scope = ? AND principal_id IS ? AND identity_workspace IS ? AND id NOT IN ("
                "SELECT conversation_id FROM usage_traces WHERE status='running' AND conversation_id IS NOT NULL) AND id NOT IN ("
                "SELECT id FROM playground_conversations WHERE scope = ? AND principal_id IS ? AND identity_workspace IS ? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?)",
                (scope, principal, workspace, scope, principal, workspace, MAX_CONVERSATIONS),
            )
        return cid

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.store._immediate() as conn:
            row = conn.execute(
                "SELECT principal_id,identity_workspace FROM playground_conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if row is not None:
                decision = require_result(
                    self.store,
                    row["principal_id"],
                    row["identity_workspace"],
                    action="results.delete",
                    conn=conn,
                )
                if conn.execute(
                    "SELECT 1 FROM usage_traces WHERE conversation_id=? AND status='running'",
                    (conversation_id,),
                ).fetchone():
                    from autoontology.store.traces import TraceError

                    raise TraceError("Stop the request before deleting its conversation.")
            removed = (
                conn.execute(
                    "DELETE FROM playground_conversations WHERE id = ?", (conversation_id,)
                ).rowcount
                > 0
            )
            if removed:
                audit_effect(
                    self.store,
                    decision,
                    conversation_id,
                    {"kind": "conversation", "owner": row["principal_id"]},
                    conn=conn,
                )
            return removed

    def get_conversation(
        self, conversation_id: str, *, action="results.read"
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM playground_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is not None:
                require_result(
                    self.store,
                    row["principal_id"],
                    row["identity_workspace"],
                    action=action,
                    conn=conn,
                )
                message_scope(self.store, conversation_id, action, conn=conn)
                if action == "query.run":
                    # Replaying stored content is a read effect as well as a new query.
                    authorize_any(self.store, "results.read", conn=conn)
                    message_scope(self.store, conversation_id, "results.read", conn=conn)
        return dict(row) if row else None

    def list_conversations(
        self, scope: str, limit: int = 50, *, all_users=False
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            conn.execute("BEGIN")
            owner = selection(self.store, all_users=all_users, conn=conn)
            clause = " AND c.principal_id=? AND c.identity_workspace=?" if owner else ""
            rows = conn.execute(
                "SELECT c.*, (SELECT count(*) FROM playground_messages m WHERE "
                "m.conversation_id = c.id) AS messages FROM playground_conversations c "
                "WHERE c.scope = ?" + clause + " ORDER BY c.updated_at DESC, c.rowid DESC",
                (scope, *(owner or ())),
            ).fetchall()
            result, cache = [], {}
            for row in rows:
                try:
                    message_scope(self.store, row["id"], "results.read", conn=conn, cache=cache)
                except AccessError as error:
                    if error.status not in {403, 404}:
                        raise
                    continue
                result.append(dict(row))
                if len(result) >= max(1, min(500, limit)):
                    break
        return result

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        retrieved: list[str] | None = None,
        citations: dict[str, Any] | None = None,
        provider: str = "",
        model: str = "",
        incomplete: bool = False,
        trace_id: str | None = None,
        trace_writer=None,
    ) -> int:
        stamp = now_iso()
        with self.store._immediate() as conn:
            row = conn.execute(
                "SELECT principal_id,identity_workspace FROM playground_conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise AccessError("No conversation with this identifier.", "result_missing", 404)
            require_result(
                self.store,
                row["principal_id"],
                row["identity_workspace"],
                action="query.run",
                conn=conn,
            )
            message_scope(self.store, conversation_id, "query.run", conn=conn)
            binding = None
            if trace_writer is not None and trace_id:
                if trace_writer.store.path != self.store.path:
                    raise AccessError("A request cannot bind another workspace.")
                _, binding = trace_writer.writer(trace_id, conn=conn)
            elif conn.execute("SELECT 1 FROM identity_state").fetchone():
                authorize(self.store, "query.run", conn=conn)
            cur = conn.execute(
                "INSERT INTO playground_messages (conversation_id, role, content, "
                "retrieved_json, citations_json, provider, model, incomplete, created_at, trace_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    role,
                    content,
                    _dumps(retrieved or []),
                    _dumps(citations or {}),
                    provider,
                    model,
                    int(incomplete),
                    stamp,
                    trace_id,
                ),
            )
            conn.execute(
                "UPDATE playground_conversations SET updated_at = ? WHERE id = ?",
                (stamp, conversation_id),
            )
            if binding is not None:
                from autoontology.store.operations import digest

                message = dict(
                    conn.execute(
                        "SELECT * FROM playground_messages WHERE id=?", (cur.lastrowid,)
                    ).fetchone()
                )
                conn.execute(
                    "INSERT INTO playground_message_scopes VALUES(?,?,?)",
                    (cur.lastrowid, _dumps(binding), digest([message, binding])),
                )
            return int(cur.lastrowid)

    def messages(self, conversation_id: str, *, action="results.read") -> list[dict[str, Any]]:
        with self._conn() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT principal_id,identity_workspace FROM playground_conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if row is not None:
                require_result(
                    self.store,
                    row["principal_id"],
                    row["identity_workspace"],
                    action=action,
                    conn=conn,
                )
                message_scope(self.store, conversation_id, action, conn=conn)
                if action == "query.run":
                    authorize_any(self.store, "results.read", conn=conn)
                    message_scope(self.store, conversation_id, "results.read", conn=conn)
            rows = conn.execute(
                "SELECT * FROM playground_messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "retrieved": json.loads(r["retrieved_json"]),
                "citations": json.loads(r["citations_json"]),
                "provider": r["provider"],
                "model": r["model"],
                "incomplete": bool(r["incomplete"]),
                "created_at": r["created_at"],
                "trace_id": r["trace_id"],
            }
            for r in rows
        ]
