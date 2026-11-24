# SPDX-License-Identifier: MIT
"""Bearer tokens for outside agents (schema v7, tables ``agent_tokens`` / ``agent_calls``).

A token is ``aok_`` + ``secrets.token_urlsafe(32)``. It is returned once, when it is
created or rotated; the store keeps only its SHA-256 (hex). Rotating replaces the hash
(the old token stops working at once); revoking clears it and keeps the row, so the call
history still names the token. ``revision_pin`` NULL means "follow the latest snapshot".

Schema v8: a token belongs to one project (``project_id``) and its ``revision_pin`` is a
revision of that project. :meth:`AgentTokens.lookup` is store-wide (the raw token names
its project); every other method is limited to the instance's project.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from autoontology.store.db import Store, now_iso

TOKEN_PREFIX = "aok_"
#: agent_calls keeps the newest rows only
MAX_CALLS = 5000


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        out = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AgentToken:
    id: str
    name: str
    revision_pin: int | None
    expires_at: str | None
    created_at: str
    last_used_at: str | None
    revoked_at: str | None
    project_id: str = ""
    #: the user who created (or last rotated) it; None = before accounts / the CLI
    created_by: str | None = None

    @property
    def expired(self) -> bool:
        exp = _parse(self.expires_at)
        return exp is not None and exp <= datetime.now(timezone.utc)

    @property
    def status(self) -> str:
        if self.revoked_at:
            return "revoked"
        return "expired" if self.expired else "active"

    def to_dict(self) -> dict[str, Any]:
        """The public record (never a hash)."""
        return {
            "id": self.id,
            "name": self.name,
            "revision_pin": self.revision_pin,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
            "status": self.status,
            "created_by": self.created_by,
        }


_COLUMNS = (
    "id, name, revision_pin, expires_at, created_at, last_used_at, revoked_at, project_id, "
    "created_by"
)


def _token(row: Any) -> AgentToken:
    return AgentToken(**{k: row[k] for k in row.keys() if k != "token_hash"})


class TokenError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class AgentTokens:
    """The agent tokens of one project (*project_id* None = the default project;
    deprecated, prefer ``store.project(...).agent_tokens()``)."""

    def __init__(self, store: Store, project_id: str | None = None) -> None:
        self.store = store
        self.project_id = project_id or store.default_project_id

    # --- tokens -------------------------------------------------------------------------

    def create(
        self,
        name: str,
        *,
        revision_pin: int | None = None,
        expires_days: int | None = None,
        created_by: str | None = None,
    ) -> tuple[str, AgentToken]:
        """``(raw token, record)``; the raw token is not kept anywhere."""
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        expires = (
            (created + timedelta(days=expires_days)).isoformat(timespec="milliseconds")
            if expires_days
            else None
        )
        token_id = "tok_" + secrets.token_hex(6)
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO agent_tokens (id, name, token_hash, revision_pin, expires_at, "
                "created_at, project_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    token_id,
                    name,
                    token_hash(raw),
                    revision_pin,
                    expires,
                    created.isoformat(timespec="milliseconds"),
                    self.project_id,
                    created_by,
                ),
            )
        found = self.get(token_id)
        assert found is not None
        return raw, found

    def get(self, token_id: str) -> AgentToken | None:
        with self.store._conn() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM agent_tokens WHERE id = ? AND project_id = ?",
                (token_id, self.project_id),
            ).fetchone()
        return _token(row) if row else None

    def list_tokens(self) -> list[AgentToken]:
        with self.store._conn() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM agent_tokens WHERE project_id = ? "
                "ORDER BY created_at DESC, id",
                (self.project_id,),
            ).fetchall()
        return [_token(r) for r in rows]

    def _require(self, token_id: str) -> AgentToken:
        found = self.get(token_id)
        if found is None:
            raise TokenError(404, "unknown_token", f"unknown token {token_id[:80]!r}")
        return found

    def rotate(self, token_id: str, *, created_by: str | None = None) -> tuple[str, AgentToken]:
        """A new raw token for *token_id* (same id, pin and expiry); the old one dies. With
        *created_by* the rotating user becomes the token's creator (it lives on their
        access)."""
        current = self._require(token_id)
        if current.revoked_at:
            raise TokenError(409, "token_revoked", "a revoked token cannot be rotated")
        if current.expired:
            raise TokenError(409, "token_expired", "an expired token cannot be rotated")
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self.store._conn() as conn:
            conn.execute(
                "UPDATE agent_tokens SET token_hash = ?, created_by = COALESCE(?, created_by) "
                "WHERE id = ? AND project_id = ? AND revoked_at IS NULL",
                (token_hash(raw), created_by, token_id, self.project_id),
            )
        return raw, self._require(token_id)

    def revoke(self, token_id: str) -> AgentToken:
        """Revoke *token_id* (idempotent): the hash is cleared, the row and calls stay."""
        current = self._require(token_id)
        if not current.revoked_at:
            with self.store._conn() as conn:
                conn.execute(
                    "UPDATE agent_tokens SET token_hash = NULL, revoked_at = ? "
                    "WHERE id = ? AND project_id = ?",
                    (now_iso(), token_id, self.project_id),
                )
        return self._require(token_id)

    def lookup(self, raw: str) -> AgentToken | None:
        """The active token whose hash matches *raw* (revoked/expired/unknown: None), in any
        project: check its ``project_id``."""
        if not raw or not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
            return None
        digest = token_hash(raw)
        with self.store._conn() as conn:
            row = conn.execute(
                f"SELECT token_hash, {_COLUMNS} FROM agent_tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
        if row is None or not hmac.compare_digest(str(row["token_hash"] or ""), digest):
            return None
        found = _token(row)
        return found if found.status == "active" else None

    # --- calls --------------------------------------------------------------------------

    def touch(self, token_id: str) -> None:
        with self.store._conn() as conn:
            conn.execute(
                "UPDATE agent_tokens SET last_used_at = ? WHERE id = ?", (now_iso(), token_id)
            )

    def record_call(self, token_id: str, surface: str, tool: str, ok: bool, ms: int) -> None:
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO agent_calls (ts, token_id, surface, tool, ok, ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_iso(), token_id, surface, tool[:80], int(bool(ok)), max(0, int(ms))),
            )
            conn.execute(
                "DELETE FROM agent_calls WHERE id <= "
                "(SELECT id FROM agent_calls ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (MAX_CALLS,),
            )

    def usage(self, days: int | None = None) -> dict[str, Any]:
        """Call counts: totals, by tool, by day (UTC) and per token, over the last *days*
        days (default: every kept row), of the project's tokens."""
        where = "WHERE c.token_id IN (SELECT id FROM agent_tokens WHERE project_id = ?)"
        params: tuple[Any, ...] = (self.project_id,)
        if days:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            where += " AND c.ts >= ?"
            params += (since.isoformat(timespec="milliseconds"),)
        with self.store._conn() as conn:
            total = conn.execute(
                f"SELECT count(*) n, coalesce(sum(ok), 0) ok FROM agent_calls c {where}", params
            ).fetchone()
            by_tool = conn.execute(
                "SELECT tool, count(*) calls, coalesce(sum(ok), 0) ok, "
                f"cast(round(avg(ms)) AS INTEGER) avg_ms FROM agent_calls c {where} "
                "GROUP BY tool ORDER BY calls DESC, tool",
                params,
            ).fetchall()
            by_day = conn.execute(
                f"SELECT substr(ts, 1, 10) day, count(*) calls FROM agent_calls c {where} "
                "GROUP BY day ORDER BY day",
                params,
            ).fetchall()
            by_token = conn.execute(
                "SELECT c.token_id, t.name, count(*) calls, coalesce(sum(c.ok), 0) ok, "
                "sum(c.surface = 'mcp') mcp, sum(c.surface = 'rest') rest, "
                "max(c.ts) last_call_at "
                f"FROM agent_calls c LEFT JOIN agent_tokens t ON t.id = c.token_id {where} "
                "GROUP BY c.token_id ORDER BY calls DESC, c.token_id",
                params,
            ).fetchall()
        return {
            "days": days,
            "total": total["n"],
            "ok": total["ok"],
            "by_tool": [dict(r) for r in by_tool],
            "by_day": [dict(r) for r in by_day],
            "by_token": [
                {
                    "token_id": r["token_id"],
                    "name": r["name"],
                    "calls": r["calls"],
                    "ok": r["ok"],
                    "by_surface": {"mcp": r["mcp"] or 0, "rest": r["rest"] or 0},
                    "last_call_at": r["last_call_at"],
                }
                for r in by_token
            ],
        }
