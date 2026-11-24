# SPDX-License-Identifier: MIT
"""Accounts (schema v9): users, passwords, sessions, single-use login tokens, project
memberships and the audit log.

Secrets are never stored: a password is kept as ``pbkdf2_sha256$<rounds>$<salt hex>$<dk
hex>`` (PBKDF2-HMAC-SHA256, 240k rounds, 16-byte salt), a session id and a login token as
their SHA-256 (hex). The raw session id / token is returned once, by the call that creates
it. :meth:`Accounts.verify_password` spends one PBKDF2 on every path (unknown account, no
password, disabled, wrong password) so its timing does not tell whether an account exists.

Emails are stored lower-cased (and the column is ``COLLATE NOCASE``). Roles in a project:
``owner`` > ``editor`` > ``reviewer`` > ``viewer``; a project's last owner cannot be
demoted or removed. The audit log has no foreign keys: it outlives what it names. An
entry made by something that is not a user (the CLI: ``actor="cli"``) keeps ``user_id``
NULL and names the actor in its detail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from autoontology.store.db import Store, now_iso

PBKDF2_ROUNDS = 240_000
PASSWORD_MIN = 12
#: Longer passwords are refused (each login attempt hashes the input).
PASSWORD_MAX = 1024
ROLES = ("owner", "editor", "reviewer", "viewer")
PURPOSES = ("magic", "invite", "reset")
#: How long a login token stays valid, per purpose.
TOKEN_TTL: dict[str, timedelta] = {
    "magic": timedelta(minutes=15),
    "reset": timedelta(minutes=15),
    "invite": timedelta(days=7),
}
DEFAULT_SESSION_HOURS = 12
#: A session's ``last_seen_at`` is written at most this often (seconds).
LAST_SEEN_THROTTLE_S = 60
EMAIL_MAX = 254
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$")
NAME_MAX = 200
AUDIT_LIMIT_MAX = 1000

#: The hash of a random password, checked against when there is no real hash to check.
_DUMMY_HASH: str | None = None


class AccountError(ValueError):
    """A refused account operation; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- hashing --------------------------------------------------------------------------


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    return _DUMMY_HASH


def check_password(password: str, stored: str | None) -> bool:
    """Whether *password* matches *stored* (constant-time compare). A missing or malformed
    *stored* still costs one PBKDF2 (against a dummy hash) and answers False."""
    try:
        algo, rounds_s, salt_hex, want = (stored or "").split("$")
        rounds, salt = int(rounds_s), bytes.fromhex(salt_hex)
        if algo != "pbkdf2_sha256" or not 1 <= rounds <= 10_000_000 or not salt:
            raise ValueError(algo)
    except ValueError:
        _, rounds_s, salt_hex, want = _dummy_hash().split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds_s)
        )
        hmac.compare_digest(dk.hex(), want)
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk.hex(), want)


def check_password_policy(password: str) -> None:
    if not isinstance(password, str) or len(password) > PASSWORD_MAX:
        raise AccountError("weak_password", f"a password has at most {PASSWORD_MAX} characters")
    if len(password) < PASSWORD_MIN or not password.strip():
        raise AccountError("weak_password", f"a password needs at least {PASSWORD_MIN} characters")


def normalize_email(email: str) -> str:
    """*email* stripped and lower-cased; AccountError ``invalid_email`` when it is not an
    address (``local@domain``)."""
    value = (email or "").strip().lower()
    if len(value) > EMAIL_MAX or not EMAIL_RE.fullmatch(value):
        raise AccountError("invalid_email", "not an email address")
    return value


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- records --------------------------------------------------------------------------


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    is_admin: bool
    created_at: str
    disabled_at: str | None
    last_login_at: str | None
    has_password: bool

    @property
    def active(self) -> bool:
        return self.disabled_at is None

    def to_dict(self) -> dict[str, Any]:
        """The public record (never the hash)."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
            "disabled_at": self.disabled_at,
            "last_login_at": self.last_login_at,
            "has_password": self.has_password,
            "active": self.active,
        }


@dataclass(frozen=True)
class Session:
    user_id: str
    created_at: str
    expires_at: str
    last_seen_at: str
    ip: str
    user_agent: str


_USER_COLUMNS = (
    "id, email, name, is_admin, created_at, disabled_at, last_login_at, "
    "pw_hash IS NOT NULL AS has_password"
)


def _user(row: Any) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
        disabled_at=row["disabled_at"],
        last_login_at=row["last_login_at"],
        has_password=bool(row["has_password"]),
    )


class Accounts:
    """The accounts of a :class:`~autoontology.store.db.Store` (installation-wide)."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # --- users ------------------------------------------------------------------------

    def create_user(
        self,
        email: str,
        *,
        name: str = "",
        is_admin: bool = False,
        password: str | None = None,
    ) -> User:
        email = normalize_email(email)
        name = (name or "").strip()[:NAME_MAX]
        pw_hash = None
        if password is not None:
            check_password_policy(password)
            pw_hash = hash_password(password)
        user_id = "usr_" + secrets.token_hex(8)
        try:
            with self.store._conn() as conn:
                conn.execute(
                    "INSERT INTO users (id, email, name, pw_hash, is_admin, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, email, name, pw_hash, int(is_admin), now_iso()),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountError("user_exists", f"a user with email {email!r} exists") from exc
        return self._require(user_id)

    def get(self, user_id: str) -> User | None:
        with self.store._conn() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return _user(row) if row else None

    def by_email(self, email: str) -> User | None:
        value = (email or "").strip().lower()
        if not value:
            return None
        with self.store._conn() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE email = ?", (value,)
            ).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        with self.store._conn() as conn:
            rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY email").fetchall()
        return [_user(r) for r in rows]

    def count_users(self) -> int:
        with self.store._conn() as conn:
            return int(conn.execute("SELECT count(*) FROM users").fetchone()[0])

    def _require(self, user_id: str) -> User:
        found = self.get(user_id)
        if found is None:
            raise AccountError("unknown_user", "unknown user")
        return found

    def _update(self, user_id: str, sql: str, params: tuple[Any, ...]) -> User:
        with self.store._conn() as conn:
            changed = conn.execute(f"UPDATE users SET {sql} WHERE id = ?", (*params, user_id))
            if not changed.rowcount:
                raise AccountError("unknown_user", "unknown user")
        return self._require(user_id)

    def disable(self, user_id: str) -> User:
        """Refuse the user everywhere from now on (their sessions and unused login links
        end now)."""
        with self.store._conn() as conn:
            changed = conn.execute(
                "UPDATE users SET disabled_at = COALESCE(disabled_at, ?) WHERE id = ?",
                (now_iso(), user_id),
            )
            if not changed.rowcount:
                raise AccountError("unknown_user", "unknown user")
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute(
                "DELETE FROM login_tokens WHERE user_id = ? AND used_at IS NULL", (user_id,)
            )
        return self._require(user_id)

    def enable(self, user_id: str) -> User:
        return self._update(user_id, "disabled_at = NULL", ())

    def set_admin(self, user_id: str, is_admin: bool) -> User:
        return self._update(user_id, "is_admin = ?", (int(is_admin),))

    def set_name(self, user_id: str, name: str) -> User:
        return self._update(user_id, "name = ?", ((name or "").strip()[:NAME_MAX],))

    def set_password(self, user_id: str, password: str) -> User:
        check_password_policy(password)
        return self._update(user_id, "pw_hash = ?", (hash_password(password),))

    def verify_password(self, email: str, password: str) -> User | None:
        """The active user with *email* whose password is *password*, else None. One
        PBKDF2 on every path (unknown email included)."""
        if not isinstance(password, str) or len(password) > PASSWORD_MAX:
            check_password("", None)
            return None
        value = (email or "").strip().lower()
        row = None
        if value and len(value) <= EMAIL_MAX:
            with self.store._conn() as conn:
                row = conn.execute(
                    f"SELECT {_USER_COLUMNS}, pw_hash FROM users WHERE email = ?", (value,)
                ).fetchone()
        stored = row["pw_hash"] if row is not None else None
        ok = check_password(password, stored)
        if not ok or row is None or row["disabled_at"] is not None:
            return None
        return _user(row)

    # --- sessions ---------------------------------------------------------------------

    def create_session(
        self,
        user_id: str,
        *,
        hours: float = DEFAULT_SESSION_HOURS,
        ip: str = "",
        user_agent: str = "",
    ) -> tuple[str, Session]:
        """A new session for *user_id* (a login: ``last_login_at`` is set). Returns the raw
        session id (for the cookie; not kept) and the record."""
        raw = secrets.token_urlsafe(32)
        now = _now()
        session = Session(
            user_id=user_id,
            created_at=_iso(now),
            expires_at=_iso(now + timedelta(hours=hours)),
            last_seen_at=_iso(now),
            ip=(ip or "")[:100],
            user_agent=(user_agent or "")[:300],
        )
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id_hash, user_id, created_at, expires_at, last_seen_at, "
                "ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token_hash(raw),
                    user_id,
                    session.created_at,
                    session.expires_at,
                    session.last_seen_at,
                    session.ip,
                    session.user_agent,
                ),
            )
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (session.created_at, user_id)
            )
        return raw, session

    def lookup_session(self, raw: str | None) -> tuple[Session, User] | None:
        """The live session with id *raw* and its (active) user, else None. An expired
        session is deleted; ``last_seen_at`` is refreshed at most every
        :data:`LAST_SEEN_THROTTLE_S` seconds."""
        if not raw or len(raw) > 200:
            return None
        key = token_hash(raw)
        now = _now()
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT s.user_id, s.created_at AS s_created, s.expires_at, s.last_seen_at, "
                "s.ip, s.user_agent, u.id, u.email, u.name, u.is_admin, u.created_at, "
                "u.disabled_at, u.last_login_at, u.pw_hash IS NOT NULL AS has_password "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= _iso(now):
                conn.execute("DELETE FROM sessions WHERE id_hash = ?", (key,))
                return None
            if row["disabled_at"] is not None:
                return None
            seen = row["last_seen_at"]
            try:
                stale = (now - datetime.fromisoformat(seen)).total_seconds()
            except ValueError:
                stale = LAST_SEEN_THROTTLE_S + 1
            if stale >= LAST_SEEN_THROTTLE_S:
                seen = _iso(now)
                conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id_hash = ?", (seen, key))
        session = Session(
            user_id=row["user_id"],
            created_at=row["s_created"],
            expires_at=row["expires_at"],
            last_seen_at=seen,
            ip=row["ip"],
            user_agent=row["user_agent"],
        )
        return session, _user(row)

    def revoke_session(self, raw: str | None) -> bool:
        if not raw or len(raw) > 200:
            return False
        with self.store._conn() as conn:
            gone = conn.execute("DELETE FROM sessions WHERE id_hash = ?", (token_hash(raw),))
        return bool(gone.rowcount)

    def revoke_all_for_user(self, user_id: str, *, except_raw: str | None = None) -> int:
        """End every session of *user_id* (but the one with id *except_raw*): the count."""
        keep = token_hash(except_raw) if except_raw else ""
        with self.store._conn() as conn:
            gone = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id_hash != ?", (user_id, keep)
            )
        return gone.rowcount

    def purge_expired(self) -> int:
        """Delete expired sessions and expired or used login tokens: the count."""
        now = now_iso()
        with self.store._conn() as conn:
            a = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,)).rowcount
            b = conn.execute(
                "DELETE FROM login_tokens WHERE expires_at <= ? OR used_at IS NOT NULL", (now,)
            ).rowcount
        return a + b

    # --- login tokens -----------------------------------------------------------------

    def issue_token(
        self, user_id: str, purpose: str, *, ttl: timedelta | None = None
    ) -> tuple[str, str]:
        """A single-use login token: ``(raw token, expires_at)``. Earlier unused tokens of
        the same purpose for the user stop working."""
        if purpose not in PURPOSES:
            raise AccountError("invalid_purpose", f"purpose must be one of {PURPOSES}")
        raw = secrets.token_urlsafe(32)
        now = _now()
        expires = _iso(now + (ttl or TOKEN_TTL[purpose]))
        with self.store._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise AccountError("unknown_user", "unknown user")
            conn.execute(
                "DELETE FROM login_tokens WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
                (user_id, purpose),
            )
            conn.execute(
                "INSERT INTO login_tokens (id_hash, user_id, purpose, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash(raw), user_id, purpose, _iso(now), expires),
            )
        return raw, expires

    @staticmethod
    def _token_row(conn: Any, raw: str, purposes: tuple[str, ...]) -> Any:
        if not raw or len(raw) > 200 or not purposes:
            return None
        marks = ",".join("?" * len(purposes))
        return conn.execute(
            "SELECT t.id_hash, t.user_id FROM login_tokens t JOIN users u ON u.id = t.user_id "
            f"WHERE t.id_hash = ? AND t.purpose IN ({marks}) AND t.used_at IS NULL "
            "AND t.expires_at > ? AND u.disabled_at IS NULL",
            (token_hash(raw), *purposes, now_iso()),
        ).fetchone()

    def peek_token(self, raw: str, purposes: tuple[str, ...]) -> User | None:
        """The user of a valid (unused, unexpired, one of *purposes*) token, without
        consuming it."""
        with self.store._conn() as conn:
            row = self._token_row(conn, raw, tuple(purposes))
        return self.get(row["user_id"]) if row else None

    def consume_token(
        self, raw: str, purposes: tuple[str, ...], *, new_password: str | None = None
    ) -> User | None:
        """Use the token *raw* (single use, atomic): its user, or None when it is unknown,
        used, expired, of another purpose or its user is disabled. With *new_password*
        (checked first: a weak one raises and leaves the token unused) the user's password
        is set in the same transaction."""
        pw_hash = None
        if new_password is not None:
            check_password_policy(new_password)
            pw_hash = hash_password(new_password)
        with self.store._immediate() as conn:
            row = self._token_row(conn, raw, tuple(purposes))
            if row is None:
                return None
            conn.execute(
                "UPDATE login_tokens SET used_at = ? WHERE id_hash = ?", (now_iso(), row["id_hash"])
            )
            if pw_hash is not None:
                conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (pw_hash, row["user_id"]))
            user_id = row["user_id"]
        return self.get(user_id)

    # --- memberships ------------------------------------------------------------------

    @staticmethod
    def _role(role: str) -> str:
        if role not in ROLES:
            raise AccountError("invalid_role", f"role must be one of {', '.join(ROLES)}")
        return role

    def add_member(self, project_id: str, user_id: str, role: str) -> None:
        self._role(role)
        with self.store._immediate() as conn:
            if (
                conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
                is None
            ):
                raise AccountError("unknown_project", "unknown project")
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise AccountError("unknown_user", "unknown user")
            try:
                conn.execute(
                    "INSERT INTO memberships (project_id, user_id, role, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (project_id, user_id, role, now_iso()),
                )
            except sqlite3.IntegrityError as exc:
                raise AccountError("member_exists", "already a member of the project") from exc

    @staticmethod
    def _check_last_owner(conn: Any, project_id: str, user_id: str) -> str:
        row = conn.execute(
            "SELECT role FROM memberships WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        if row is None:
            raise AccountError("not_a_member", "not a member of the project")
        if row["role"] == "owner":
            owners = conn.execute(
                "SELECT count(*) FROM memberships WHERE project_id = ? AND role = 'owner'",
                (project_id,),
            ).fetchone()[0]
            if owners <= 1:
                raise AccountError("last_owner", "a project keeps at least one owner")
        return row["role"]

    def set_role(self, project_id: str, user_id: str, role: str) -> None:
        self._role(role)
        with self.store._immediate() as conn:
            if role != "owner":
                self._check_last_owner(conn, project_id, user_id)
            changed = conn.execute(
                "UPDATE memberships SET role = ? WHERE project_id = ? AND user_id = ?",
                (role, project_id, user_id),
            )
            if not changed.rowcount:
                raise AccountError("not_a_member", "not a member of the project")

    def remove_member(self, project_id: str, user_id: str) -> None:
        with self.store._immediate() as conn:
            self._check_last_owner(conn, project_id, user_id)
            conn.execute(
                "DELETE FROM memberships WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )

    def role_of(self, project_id: str, user_id: str) -> str | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT role FROM memberships WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        return row["role"] if row else None

    def list_members(self, project_id: str) -> list[dict[str, Any]]:
        """The project's members (``user`` fields + ``role``, ``member_since``), by email."""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT u.id, u.email, u.name, u.is_admin, u.created_at, u.disabled_at, "
                "u.last_login_at, u.pw_hash IS NOT NULL AS has_password, m.role, "
                "m.created_at AS member_since "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.project_id = ? ORDER BY u.email",
                (project_id,),
            ).fetchall()
        return [
            {**_user(r).to_dict(), "role": r["role"], "member_since": r["member_since"]}
            for r in rows
        ]

    def projects_of(self, user_id: str) -> list[tuple[str, str]]:
        """``(project_id, role)`` of every project *user_id* is a member of."""
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT project_id, role FROM memberships WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [(r["project_id"], r["role"]) for r in rows]

    # --- audit ------------------------------------------------------------------------

    def record(
        self,
        action: str,
        *,
        user_id: str | None = None,
        actor: str | None = None,
        project_id: str | None = None,
        target: str = "",
        detail: dict[str, Any] | None = None,
        ip: str = "",
    ) -> None:
        """Append to the audit log. *actor* names a non-user caller (``"cli"``); never put
        a secret in *detail*."""
        body = dict(detail or {})
        if actor:
            body["actor"] = actor
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (at, user_id, project_id, action, target, detail_json, ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now_iso(),
                    user_id,
                    project_id,
                    action[:100],
                    (target or "")[:300],
                    json.dumps(body, ensure_ascii=False, default=str)[:4000],
                    (ip or "")[:100],
                ),
            )

    def list_audit(
        self,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        since: str | None = None,
        before_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Audit entries, newest first; ``actor`` is the user's email, else the detail's
        actor, else ``"-"``."""
        where, params = [], []
        for column, value in (
            ("a.project_id", project_id),
            ("a.user_id", user_id),
            ("a.action", action),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        if since:
            where.append("a.at >= ?")
            params.append(since)
        if before_id is not None:
            where.append("a.id < ?")
            params.append(before_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        limit = max(1, min(int(limit), AUDIT_LIMIT_MAX))
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT a.*, u.email FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
                f"{clause} ORDER BY a.id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        out = []
        for r in rows:
            try:
                detail = json.loads(r["detail_json"] or "{}")
            except ValueError:
                detail = {}
            out.append(
                {
                    "id": r["id"],
                    "at": r["at"],
                    "user_id": r["user_id"],
                    "actor": r["email"] or detail.get("actor") or "-",
                    "project_id": r["project_id"],
                    "action": r["action"],
                    "target": r["target"],
                    "detail": detail,
                    "ip": r["ip"],
                }
            )
        return out
