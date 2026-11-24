# SPDX-License-Identifier: MIT
"""Playground tables: element index and conversations.

Shares :class:`autoontology.store.db.Store`'s connection helper, like ``AlignmentStore``.
Schema v8: every row belongs to a project; ``scope`` is a key inside the project (the
ontology name today). A :class:`PlaygroundStore` reads and writes one project."""

from __future__ import annotations

import json
import uuid
from array import array
from dataclasses import dataclass
from typing import Any

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
    """The playground tables of one project (*project_id* None = the default project;
    deprecated, prefer ``store.project(...).playground()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    def _conn(self):
        return self.store._conn()

    # --- element index ------------------------------------------------------------------

    def index_meta(self, scope: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM playground_index_meta WHERE project_id = ? AND scope = ?",
                (self.project_id, scope),
            ).fetchone()
        return dict(row) if row else None

    def index_rows(self, scope: str) -> dict[str, IndexRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM playground_index WHERE project_id = ? AND scope = ?",
                (self.project_id, scope),
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
            conn.execute(
                "DELETE FROM playground_index WHERE project_id = ? AND scope = ?",
                (self.project_id, scope),
            )
            conn.executemany(
                "INSERT INTO playground_index (project_id, scope, element_id, element_type, "
                "text_sha, embedding, model) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        self.project_id,
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
                "INSERT OR REPLACE INTO playground_index_meta (project_id, scope, revision, "
                "model, elements, built_at) VALUES (?, ?, ?, ?, ?, ?)",
                (self.project_id, scope, revision, model, len(rows), now_iso()),
            )

    # --- conversations ------------------------------------------------------------------

    def create_conversation(self, scope: str, title: str) -> str:
        cid = uuid.uuid4().hex
        stamp = now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO playground_conversations (id, project_id, scope, title, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, self.project_id, scope, title[:200], stamp, stamp),
            )
            conn.execute(
                "DELETE FROM playground_conversations WHERE project_id = ? AND scope = ? "
                "AND id NOT IN (SELECT id FROM playground_conversations WHERE project_id = ? "
                "AND scope = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?)",
                (self.project_id, scope, self.project_id, scope, MAX_CONVERSATIONS),
            )
        return cid

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._conn() as conn:
            return (
                conn.execute(
                    "DELETE FROM playground_conversations WHERE id = ? AND project_id = ?",
                    (conversation_id, self.project_id),
                ).rowcount
                > 0
            )

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM playground_conversations WHERE id = ? AND project_id = ?",
                (conversation_id, self.project_id),
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.*, (SELECT count(*) FROM playground_messages m WHERE "
                "m.conversation_id = c.id) AS messages FROM playground_conversations c "
                "WHERE c.project_id = ? AND c.scope = ? "
                "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
                (self.project_id, scope, limit),
            ).fetchall()
        return [dict(r) for r in rows]

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
    ) -> int:
        stamp = now_iso()
        with self._conn() as conn:
            if not conn.execute(
                "SELECT 1 FROM playground_conversations WHERE id = ? AND project_id = ?",
                (conversation_id, self.project_id),
            ).fetchone():
                raise KeyError(f"no conversation {conversation_id!r} in this project")
            cur = conn.execute(
                "INSERT INTO playground_messages (conversation_id, role, content, "
                "retrieved_json, citations_json, provider, model, incomplete, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
            conn.execute(
                "UPDATE playground_conversations SET updated_at = ? WHERE id = ?",
                (stamp, conversation_id),
            )
            return int(cur.lastrowid)

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT m.* FROM playground_messages m JOIN playground_conversations c "
                "ON c.id = m.conversation_id WHERE m.conversation_id = ? AND c.project_id = ? "
                "ORDER BY m.id",
                (conversation_id, self.project_id),
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
            }
            for r in rows
        ]
