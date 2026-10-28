"""Playground tables: element index and conversations.

Shares :class:`autoontology.store.db.Store`'s connection helper, like ``AlignmentStore``."""

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
    def __init__(self, store: Store) -> None:
        self.store = store

    def _conn(self):
        return self.store._conn()

    # --- element index ------------------------------------------------------------------

    def index_meta(self, source_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM playground_index_meta WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def index_rows(self, source_id: str) -> dict[str, IndexRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM playground_index WHERE source_id = ?", (source_id,)
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

    def replace_index(
        self, source_id: str, rows: list[IndexRow], *, revision: int, model: str
    ) -> None:
        """The index of *source_id* becomes exactly *rows* (one transaction)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM playground_index WHERE source_id = ?", (source_id,))
            conn.executemany(
                "INSERT INTO playground_index (source_id, element_id, element_type, text_sha, "
                "embedding, model) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        source_id,
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
                "INSERT OR REPLACE INTO playground_index_meta (source_id, revision, model, "
                "elements, built_at) VALUES (?, ?, ?, ?, ?)",
                (source_id, revision, model, len(rows), now_iso()),
            )

    # --- conversations ------------------------------------------------------------------

    def create_conversation(self, source_id: str, title: str) -> str:
        cid = uuid.uuid4().hex
        stamp = now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO playground_conversations (id, source_id, title, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?)",
                (cid, source_id, title[:200], stamp, stamp),
            )
            conn.execute(
                "DELETE FROM playground_conversations WHERE source_id = ? AND id NOT IN ("
                "SELECT id FROM playground_conversations WHERE source_id = ? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?)",
                (source_id, source_id, MAX_CONVERSATIONS),
            )
        return cid

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._conn() as conn:
            return (
                conn.execute(
                    "DELETE FROM playground_conversations WHERE id = ?", (conversation_id,)
                ).rowcount
                > 0
            )

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM playground_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, source_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.*, (SELECT count(*) FROM playground_messages m WHERE "
                "m.conversation_id = c.id) AS messages FROM playground_conversations c "
                "WHERE c.source_id = ? ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
                (source_id, limit),
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
            }
            for r in rows
        ]
