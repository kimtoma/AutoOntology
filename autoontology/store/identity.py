# SPDX-License-Identifier: MIT
"""Durable principals, scoped role/delegation checks and append-only identity audit.

This policy core never accepts an actor name or kind as proof of identity. Authenticate
a credential first, then recheck it and current authority at every operation boundary.
Pass the caller's active SQLite transaction to ``require`` at an atomic write boundary.
HTTP/UI/CLI adapters must use this core; a capability catalog alone is not enforcement.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from autoontology.security.credentials import (
    IssuedCredential,
    Secret,
    _valid_password_bytes,
    check_password,
    hash_password,
    token_digest,
)
from autoontology.security.policy import CAPABILITIES, ROLES, AccessError, Actor, Decision
from autoontology.store.operations import digest

SESSION_TTL = 12 * 3600
MAX_DELEGATION_TTL = 7 * 24 * 3600
USERNAME = re.compile(r"[a-z][a-z0-9_-]{1,63}\Z")
_DUMMY_PASSWORD = json.dumps(
    {
        "version": 1,
        "algorithm": "scrypt",
        "n": 2**17,
        "r": 8,
        "p": 1,
        "salt": "00" * 16,
        "hash": "00" * 32,
    }
)


def _username(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise AccessError(
            "Use 2–64 lowercase letters, digits, underscores or hyphens, starting with a letter.",
            "invalid_username",
            400,
        )
    return value


def _name(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 100
        or any(ord(c) < 32 for c in value)
    ):
        raise AccessError("Provide a display name of 1–100 characters.", "invalid_name", 400)
    return value.strip()


def _ttl(value, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 1 <= value <= maximum
        or not math.isfinite(value)
    ):
        raise AccessError(
            "The credential lifetime is outside the allowed range.", "invalid_lifetime", 400
        )
    return value


class Identity:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    @contextmanager
    def _read(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            yield conn

    def _state(self, conn):
        row = conn.execute("SELECT * FROM identity_state WHERE id=1").fetchone()
        if row is None:
            raise AccessError(
                "Individual identity has not been configured.", "identity_unconfigured", 409
            )
        return dict(row)

    def status(self):
        with self._read() as conn:
            row = conn.execute("SELECT * FROM identity_state WHERE id=1").fetchone()
            if row is None and conn.execute("SELECT 1 FROM identity_principals LIMIT 1").fetchone():
                raise AccessError(
                    "Identity configuration is missing; use local-owner recovery before exposing the service.",
                    "identity_configuration_missing",
                    503,
                )
            return {"configured": row is not None, **(dict(row) if row else {})}

    def describe(self, actor):
        """Return only this principal's public metadata and current effective scopes."""
        with self._read() as conn:
            actor = self._actor(conn, actor)
            state = self._state(conn)
            principal = conn.execute(
                "SELECT * FROM identity_principals WHERE id=?", (actor.principal_id,)
            ).fetchone()
            credential = conn.execute(
                "SELECT expires_at FROM identity_credentials WHERE id=?", (actor.credential_id,)
            ).fetchone()
            grants = [
                dict(r)
                for r in conn.execute(
                    "SELECT role,domain_id FROM identity_grants WHERE principal_id=? AND revoked_at IS NULL ORDER BY domain_id,role",
                    (actor.principal_id,),
                )
            ]
            delegation = conn.execute(
                "SELECT delegator_id,actions_json,domains_json,expires_at FROM identity_delegations WHERE principal_id=?",
                (actor.principal_id,),
            ).fetchone()
            scopes = sorted(
                {g["domain_id"] for g in grants}
                | (set(json.loads(delegation["domains_json"])) if delegation else set())
            )
            capabilities = {}
            for action in CAPABILITIES:
                allowed = []
                for domain in scopes:
                    try:
                        self.require(actor, action, domains=[domain], conn=conn)
                    except AccessError as error:
                        if error.status != 403:
                            raise
                    else:
                        allowed.append(domain)
                if allowed:
                    capabilities[action] = allowed
            return {
                "principal": self._public(principal),
                "workspace": {
                    k: state[k] for k in ("workspace_id", "workspace_name", "organization_id")
                },
                "policy_version": state["policy_version"],
                "credential_id": actor.credential_id,
                "expires_at": credential["expires_at"],
                "grants": grants,
                "capabilities": capabilities,
            }

    @staticmethod
    def _public(row):
        return {k: row[k] for k in ("id", "username", "name", "kind", "status", "created_at")}

    def _event(self, conn, actor, action, target, detail):
        previous = conn.execute(
            "SELECT id,sha256 FROM identity_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        record = {
            "id": previous["id"] + 1 if previous else 1,
            "created_at": self.clock(),
            "actor_id": actor.principal_id if actor else None,
            "credential_id": actor.credential_id if actor else None,
            "action": action,
            "target": target,
            "detail_json": json.dumps(
                detail, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "previous_sha256": previous["sha256"] if previous else "",
        }
        conn.execute(
            "INSERT INTO identity_events VALUES(?,?,?,?,?,?,?,?,?)",
            (*record.values(), digest(record)),
        )

    @staticmethod
    def _bump(conn):
        conn.execute("UPDATE identity_state SET policy_version=policy_version+1 WHERE id=1")

    def _issue(self, conn, principal, ttl):
        raw = "ao_" + secrets.token_urlsafe(32)
        credential_id = uuid.uuid4().hex
        now = self.clock()
        conn.execute(
            "INSERT INTO identity_credentials VALUES(?,?,?,?,?,?,NULL)",
            (
                credential_id,
                principal["id"],
                token_digest(raw),
                principal["epoch"],
                now,
                now + ttl,
            ),
        )
        return IssuedCredential(self._public(principal), credential_id, now + ttl, Secret(raw))

    def _actor(self, conn, actor):
        if not isinstance(actor, Actor) or not isinstance(actor._proof, Secret):
            raise AccessError(
                "Authenticate an individual credential first.", "authentication_required", 401
            )
        row = conn.execute(
            "SELECT p.*,c.id AS credential_id,c.secret_sha256,c.epoch AS credential_epoch,c.expires_at,c.revoked_at FROM identity_credentials c JOIN identity_principals p ON p.id=c.principal_id WHERE c.id=? AND p.id=?",
            (actor.credential_id, actor.principal_id),
        ).fetchone()
        if (
            row is None
            or row["status"] != "active"
            or row["revoked_at"] is not None
            or row["expires_at"] <= self.clock()
            or row["epoch"] != row["credential_epoch"]
            or not hmac.compare_digest(actor._proof.reveal(), row["secret_sha256"])
        ):
            raise AccessError(
                "This credential is expired, revoked or invalid. Sign in again.",
                "credential_invalid",
                401,
            )
        # Reload kind/name from the store; caller-supplied actor labels grant nothing.
        return Actor(row["id"], row["credential_id"], row["kind"], row["name"], actor._proof)

    def authenticate(self, raw):
        if not isinstance(raw, str) or not re.fullmatch(r"ao_[A-Za-z0-9_-]{43}", raw):
            raise AccessError(
                "Authenticate an individual credential first.", "authentication_required", 401
            )
        stamp = token_digest(raw)
        with self._read() as conn:
            self._state(conn)
            row = conn.execute(
                "SELECT principal_id,id FROM identity_credentials WHERE secret_sha256=?", (stamp,)
            ).fetchone()
            if row is None:
                raise AccessError("This credential is invalid.", "credential_invalid", 401)
            return self._actor(conn, Actor(row["principal_id"], row["id"], "", "", Secret(stamp)))

    def _domains(self, conn, domains):
        if domains is None:
            return ("*",)
        if not isinstance(domains, (list, tuple)) or not domains or len(domains) > 100:
            raise AccessError("An explicit domain scope is required.", "invalid_scope", 400)
        if any(not isinstance(domain, str) for domain in domains):
            raise AccessError("Choose explicit domain identifiers.", "invalid_scope", 400)
        selected = tuple(sorted(set(domains)))
        if "*" in selected:
            if selected != ("*",):
                raise AccessError(
                    "Do not combine all-domain scope with individual domains.", "invalid_scope", 400
                )
            return selected
        known = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM identity_domains WHERE workspace_id=?",
                (self._state(conn)["workspace_id"],),
            )
        }
        if any(not isinstance(d, str) or d not in known for d in selected):
            raise AccessError("The requested domain is outside this workspace.")
        return selected

    def _human_allowed(self, conn, principal_id, action, domains):
        grants = conn.execute(
            "SELECT domain_id,role FROM identity_grants WHERE principal_id=? AND workspace_id=? AND revoked_at IS NULL",
            (principal_id, self._state(conn)["workspace_id"]),
        ).fetchall()
        return all(
            any(
                action in ROLES.get(g["role"], ())
                and (g["domain_id"] == "*" or g["domain_id"] == domain)
                for g in grants
            )
            for domain in domains
        )

    def require(
        self, actor, action, *, domains=None, workspace=None, expected_policy=None, conn=None
    ):
        if conn is None:
            with self._read() as read:
                return self.require(
                    actor,
                    action,
                    domains=domains,
                    workspace=workspace,
                    expected_policy=expected_policy,
                    conn=read,
                )
        if not conn.in_transaction:
            raise RuntimeError("authority checks at a write boundary need an active transaction")
        actor = self._actor(conn, actor)
        state = self._state(conn)
        if workspace is not None and workspace != state["workspace_id"]:
            raise AccessError("This credential does not authorize another workspace.")
        if expected_policy is not None and expected_policy != state["policy_version"]:
            raise AccessError(
                "Authority changed after preparation. Refresh the review packet.",
                "authority_changed",
                409,
            )
        capability = CAPABILITIES.get(action) if isinstance(action, str) else None
        if capability is None:
            raise AccessError("No permission is defined for this operation.")
        selected = self._domains(conn, domains)
        if actor.kind == "human":
            allowed = self._human_allowed(conn, actor.principal_id, action, selected)
        else:
            delegation = conn.execute(
                "SELECT d.*,p.status AS owner_status,p.kind AS owner_kind FROM identity_delegations d JOIN identity_principals p ON p.id=d.delegator_id WHERE d.principal_id=?",
                (actor.principal_id,),
            ).fetchone()
            allowed = bool(
                delegation
                and not capability.human_only
                and delegation["revoked_at"] is None
                and delegation["expires_at"] > self.clock()
                and delegation["owner_status"] == "active"
                and delegation["owner_kind"] == "human"
                and delegation["workspace_id"] == state["workspace_id"]
                and action in json.loads(delegation["actions_json"])
            )
            if allowed:
                scopes = json.loads(delegation["domains_json"])
                allowed = all(
                    "*" in scopes or d in scopes for d in selected
                ) and self._human_allowed(conn, delegation["delegator_id"], action, selected)
        if not allowed:
            raise AccessError()
        return Decision(actor, action, state["workspace_id"], selected, state["policy_version"])

    def require_any(self, actor, action, *, conn=None):
        """An entry gate only: consumers must still select/authorize actual resources."""
        if conn is None:
            with self._read() as read:
                return self.require_any(actor, action, conn=read)
        try:
            return self.require(actor, action, conn=conn)
        except AccessError as error:
            if error.code != "forbidden":
                raise
        for (domain,) in conn.execute(
            "SELECT id FROM identity_domains WHERE workspace_id=? ORDER BY id",
            (self._state(conn)["workspace_id"],),
        ):
            try:
                return self.require(actor, action, domains=[domain], conn=conn)
            except AccessError as error:
                if error.code != "forbidden":
                    raise
        raise AccessError()

    def bootstrap(self, workspace, username, name, password):
        """Trusted host-owner enrollment only; never expose this as an anonymous API."""
        username, name = _username(username), _name(name)
        if not isinstance(workspace, str) or not workspace.strip() or len(workspace) > 100:
            raise AccessError("Provide the local workspace name.", "invalid_workspace", 400)
        password_record = hash_password(password)
        with self.store._immediate() as conn:
            if (
                not conn.execute("SELECT 1 FROM identity_state").fetchone()
                and conn.execute("SELECT 1 FROM identity_principals LIMIT 1").fetchone()
            ):
                raise AccessError(
                    "Identity configuration is missing; recover the existing workspace rather than enrolling a new owner.",
                    "identity_configuration_missing",
                    503,
                )
            if conn.execute("SELECT 1 FROM identity_state").fetchone():
                raise AccessError(
                    "Identity is already configured; use the existing owner login.",
                    "identity_already_configured",
                    409,
                )
            now = self.clock()
            ws, org, pid, domain = (uuid.uuid4().hex for _ in range(4))
            conn.execute(
                "INSERT INTO identity_state VALUES(1,?,?,?,?,?)", (org, ws, workspace, 1, now)
            )
            conn.execute(
                "INSERT INTO identity_domains VALUES(?,?,?,?,?)",
                (domain, ws, "general", "General", now),
            )
            conn.execute(
                "INSERT INTO identity_principals VALUES(?,?,?,'human','active',?,1,?)",
                (pid, username, name, password_record, now),
            )
            conn.execute(
                "INSERT INTO identity_grants VALUES(?,?,?,?,?,?,?,NULL)",
                (uuid.uuid4().hex, pid, ws, "*", "admin", pid, now),
            )
            for source in conn.execute("SELECT id FROM sources"):
                conn.execute(
                    "INSERT INTO identity_source_domains VALUES(?,1,?,?,?)",
                    (source[0], domain, pid, now),
                )
            principal = conn.execute(
                "SELECT * FROM identity_principals WHERE id=?", (pid,)
            ).fetchone()
            issued = self._issue(conn, principal, SESSION_TTL)
            actor = Actor(
                pid,
                issued.credential_id,
                "human",
                name,
                Secret(token_digest(issued.secret.reveal())),
            )
            self._event(
                conn,
                actor,
                "identity.bootstrap",
                ws,
                {"trust": "local-host owner enrollment", "default_domain": domain},
            )
            return issued

    def login(self, username, password, *, peer="local", ttl=SESSION_TTL):
        _ttl(ttl, SESSION_TTL)
        if (
            not isinstance(username, str)
            or len(username) > 64
            or not _valid_password_bytes(password)
        ):
            raise AccessError("The username or password is invalid.", "invalid_credentials", 401)
        # Account and peer scopes are hashed; audit never records passwords or request headers.
        keys = ["account:" + token_digest(username), "peer:" + token_digest(str(peer)[:200])]
        now = self.clock()
        with self.store._immediate() as conn:
            self._state(conn)
            for key in keys:
                row = conn.execute(
                    "SELECT * FROM identity_login_limits WHERE key=?", (key,)
                ).fetchone()
                if row and row["blocked_until"] > now:
                    raise AccessError(
                        "Too many login attempts. Retry after the cooldown.",
                        "login_throttled",
                        429,
                        retry_after=math.ceil(row["blocked_until"] - now),
                    )
            principal = conn.execute(
                "SELECT * FROM identity_principals WHERE username=?", (username,)
            ).fetchone()
            snapshot = dict(principal) if principal else None
        valid = check_password(
            password,
            snapshot["password_hash"]
            if snapshot and snapshot["password_hash"]
            else _DUMMY_PASSWORD,
        )
        failure = None
        with self.store._immediate() as conn:
            # Hashing is deliberately outside the write lock. Recheck all authority fields.
            current = conn.execute(
                "SELECT * FROM identity_principals WHERE username=?", (username,)
            ).fetchone()
            valid = bool(
                valid
                and snapshot
                and current
                and current["kind"] == "human"
                and current["status"] == "active"
                and dict(current) == snapshot
            )
            # Another concurrent failed login may have started a cooldown during hashing.
            blocked = max(
                [
                    0,
                    *[
                        r[0]
                        for key in keys
                        if (
                            r := conn.execute(
                                "SELECT blocked_until FROM identity_login_limits WHERE key=?",
                                (key,),
                            ).fetchone()
                        )
                    ],
                ]
            )
            if blocked > self.clock():
                failure = AccessError(
                    "Too many login attempts. Retry after the cooldown.",
                    "login_throttled",
                    429,
                    retry_after=math.ceil(blocked - self.clock()),
                )
            elif valid:
                conn.execute("DELETE FROM identity_login_limits WHERE key=?", (keys[0],))
                issued = self._issue(conn, current, ttl)
                actor = Actor(
                    current["id"],
                    issued.credential_id,
                    "human",
                    current["name"],
                    Secret(token_digest(issued.secret.reveal())),
                )
                self._event(conn, actor, "identity.login", current["id"], {})
            else:
                for key, limit in zip(keys, (5, 30), strict=True):
                    old = conn.execute(
                        "SELECT * FROM identity_login_limits WHERE key=?", (key,)
                    ).fetchone()
                    failures = (
                        old["failures"] + 1 if old and self.clock() - old["updated_at"] < 60 else 1
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO identity_login_limits VALUES(?,?,?,?)",
                        (
                            key,
                            failures,
                            self.clock() + 60 if failures >= limit else 0,
                            self.clock(),
                        ),
                    )
                self._event(conn, None, "identity.login_failed", keys[0], {})
                failure = AccessError(
                    "The username or password is invalid.", "invalid_credentials", 401
                )
        if failure:
            raise failure
        return issued

    def _roles(self, roles):
        if (
            not isinstance(roles, (list, tuple))
            or not roles
            or any(not isinstance(r, str) or r not in ROLES for r in roles)
        ):
            raise AccessError("Choose explicit supported roles.", "invalid_role", 400)
        return sorted(set(roles))

    def _set_grants(self, conn, actor, pid, roles, domains):
        now, ws = self.clock(), self._state(conn)["workspace_id"]
        conn.execute(
            "UPDATE identity_grants SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL",
            (now, pid),
        )
        for domain in domains:
            for role in roles:
                conn.execute(
                    "INSERT INTO identity_grants VALUES(?,?,?,?,?,?,?,NULL)",
                    (uuid.uuid4().hex, pid, ws, domain, role, actor.principal_id, now),
                )

    def _last_admin(self, conn):
        row = conn.execute(
            "SELECT count(DISTINCT p.id) FROM identity_principals p JOIN identity_grants g ON g.principal_id=p.id WHERE p.kind='human' AND p.status='active' AND g.role='admin' AND g.domain_id='*' AND g.revoked_at IS NULL AND g.workspace_id=?",
            (self._state(conn)["workspace_id"],),
        ).fetchone()
        if row[0] == 0:
            raise AccessError(
                "Keep at least one active human workspace administrator.", "last_administrator", 409
            )

    def create_human(self, actor, username, name, password, roles, *, domains=None):
        username, name, roles = _username(username), _name(name), self._roles(roles)
        # Refuse an unprivileged expensive password-hash request before deriving it.
        self.require(actor, "identity.admin")
        password_record = hash_password(password)
        try:
            with self.store._immediate() as conn:
                actor = self.require(actor, "identity.admin", conn=conn).actor
                selected = self._domains(conn, domains)
                pid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO identity_principals VALUES(?,?,?,'human','active',?,1,?)",
                    (pid, username, name, password_record, self.clock()),
                )
                self._set_grants(conn, actor, pid, roles, selected)
                self._bump(conn)
                self._event(
                    conn,
                    actor,
                    "identity.human_created",
                    pid,
                    {"roles": roles, "domains": selected},
                )
                return self._public(
                    conn.execute("SELECT * FROM identity_principals WHERE id=?", (pid,)).fetchone()
                )
        except sqlite3.IntegrityError:
            raise AccessError(
                "That username is already reserved.", "username_exists", 409
            ) from None

    def set_roles(self, actor, principal_id, roles, *, domains=None):
        roles = self._roles(roles)
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            target = conn.execute(
                "SELECT * FROM identity_principals WHERE id=?", (principal_id,)
            ).fetchone()
            if not target or target["kind"] != "human":
                raise AccessError(
                    "Roles belong to existing human accounts; agents use scoped delegation.",
                    "invalid_principal",
                    400,
                )
            selected = self._domains(conn, domains)
            self._set_grants(conn, actor, principal_id, roles, selected)
            self._last_admin(conn)
            self._bump(conn)
            self._event(
                conn,
                actor,
                "identity.roles_changed",
                principal_id,
                {"roles": roles, "domains": selected},
            )
            return self._public(target)

    def disable(self, actor, principal_id):
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            row = conn.execute(
                "SELECT * FROM identity_principals WHERE id=?", (principal_id,)
            ).fetchone()
            if not row:
                raise AccessError("No such principal.", "principal_missing", 404)
            if row["status"] == "disabled":
                return self._public(row)
            conn.execute(
                "UPDATE identity_principals SET status='disabled',epoch=epoch+1 WHERE id=?",
                (principal_id,),
            )
            self._last_admin(conn)
            self._bump(conn)
            self._event(conn, actor, "identity.disabled", principal_id, {})
            return self._public(
                conn.execute(
                    "SELECT * FROM identity_principals WHERE id=?", (principal_id,)
                ).fetchone()
            )

    def enable(self, actor, principal_id):
        """Restore a human's access without restoring any old credential."""
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            row = conn.execute(
                "SELECT * FROM identity_principals WHERE id=?", (principal_id,)
            ).fetchone()
            if not row or row["kind"] != "human":
                raise AccessError(
                    "Choose a human account; revoked agents need a new delegation.",
                    "invalid_principal",
                    400,
                )
            if row["status"] != "active":
                conn.execute(
                    "UPDATE identity_principals SET status='active',epoch=epoch+1 WHERE id=?",
                    (principal_id,),
                )
                self._bump(conn)
                self._event(
                    conn,
                    actor,
                    "identity.enabled",
                    principal_id,
                    {"old_sessions_invalidated": True},
                )
            return self._public(
                conn.execute(
                    "SELECT * FROM identity_principals WHERE id=?", (principal_id,)
                ).fetchone()
            )

    def change_password(self, actor, principal_id, password):
        self.require(actor, "identity.admin")
        record = hash_password(password)
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            row = conn.execute(
                "SELECT kind FROM identity_principals WHERE id=?", (principal_id,)
            ).fetchone()
            if not row or row["kind"] != "human":
                raise AccessError("Only human accounts have passwords.", "invalid_principal", 400)
            conn.execute(
                "UPDATE identity_principals SET password_hash=?,epoch=epoch+1 WHERE id=?",
                (record, principal_id),
            )
            self._bump(conn)
            self._event(
                conn,
                actor,
                "identity.password_changed",
                principal_id,
                {"sessions_invalidated": True},
            )

    def create_domain(self, actor, slug, name):
        slug, name = _username(slug), _name(name)
        try:
            with self.store._immediate() as conn:
                actor = self.require(actor, "identity.admin", conn=conn).actor
                ws = self._state(conn)["workspace_id"]
                domain = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO identity_domains VALUES(?,?,?,?,?)",
                    (domain, ws, slug, name, self.clock()),
                )
                self._bump(conn)
                self._event(conn, actor, "identity.domain_created", domain, {"slug": slug})
                return {"id": domain, "workspace_id": ws, "slug": slug, "name": name}
        except sqlite3.IntegrityError:
            raise AccessError(
                "That domain name is already reserved.", "domain_exists", 409
            ) from None

    def delegate(self, actor, username, name, actions, *, domains=None, ttl=3600):
        username, name = _username(username), _name(name)
        _ttl(ttl, MAX_DELEGATION_TTL)
        if (
            not isinstance(actions, (list, tuple))
            or not actions
            or any(
                not isinstance(a, str) or a not in CAPABILITIES or CAPABILITIES[a].human_only
                for a in actions
            )
        ):
            raise AccessError(
                "Delegate only explicit read, suggestion or preparation capabilities; human confirmation cannot be delegated."
            )
        actions = sorted(set(actions))
        try:
            with self.store._immediate() as conn:
                # Central provisioning remains human-admin-only; scope is still checked action by action.
                actor = self.require(actor, "identity.admin", conn=conn).actor
                selected = self._domains(conn, domains)
                for action in actions:
                    self.require(actor, action, domains=selected, conn=conn)
                state = self._state(conn)
                pid = uuid.uuid4().hex
                now = self.clock()
                conn.execute(
                    "INSERT INTO identity_principals VALUES(?,?,?,'agent','active',NULL,1,?)",
                    (pid, username, name, now),
                )
                conn.execute(
                    "INSERT INTO identity_delegations VALUES(?,?,?,?,?,?,?,NULL)",
                    (
                        pid,
                        actor.principal_id,
                        state["workspace_id"],
                        json.dumps(actions),
                        json.dumps(selected),
                        now,
                        now + ttl,
                    ),
                )
                principal = conn.execute(
                    "SELECT * FROM identity_principals WHERE id=?", (pid,)
                ).fetchone()
                issued = self._issue(conn, principal, ttl)
                self._bump(conn)
                self._event(
                    conn,
                    actor,
                    "identity.delegated",
                    pid,
                    {"actions": actions, "domains": selected, "expires_at": now + ttl},
                )
                return issued
        except sqlite3.IntegrityError:
            raise AccessError(
                "That principal name is already reserved.", "username_exists", 409
            ) from None

    def revoke_delegation(self, actor, principal_id):
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            row = conn.execute(
                "SELECT * FROM identity_delegations WHERE principal_id=?", (principal_id,)
            ).fetchone()
            if not row:
                raise AccessError("No such agent delegation.", "delegation_missing", 404)
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE identity_delegations SET revoked_at=? WHERE principal_id=?",
                    (self.clock(), principal_id),
                )
                conn.execute(
                    "UPDATE identity_credentials SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL",
                    (self.clock(), principal_id),
                )
                self._bump(conn)
                self._event(conn, actor, "identity.delegation_revoked", principal_id, {})

    def revoke_credential(self, actor, credential_id):
        with self.store._immediate() as conn:
            actor = self._actor(conn, actor)
            row = conn.execute(
                "SELECT * FROM identity_credentials WHERE id=?", (credential_id,)
            ).fetchone()
            if not row:
                raise AccessError("No such credential.", "credential_missing", 404)
            if row["principal_id"] != actor.principal_id:
                self.require(actor, "identity.admin", conn=conn)
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE identity_credentials SET revoked_at=? WHERE id=?",
                    (self.clock(), credential_id),
                )
                self._event(conn, actor, "identity.credential_revoked", credential_id, {})

    def assign_source(self, actor, source_id, domain_id):
        with self.store._immediate() as conn:
            actor = self.require(actor, "identity.admin", conn=conn).actor
            self._domains(conn, [domain_id])
            if not conn.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
                raise AccessError("Choose a registered source.", "source_missing", 404)
            old = conn.execute(
                "SELECT version,domain_id FROM identity_source_domains WHERE source_id=? ORDER BY version DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            if old and old["domain_id"] == domain_id:
                return old["version"]
            version = old["version"] + 1 if old else 1
            conn.execute(
                "INSERT INTO identity_source_domains VALUES(?,?,?,?,?)",
                (source_id, version, domain_id, actor.principal_id, self.clock()),
            )
            self._bump(conn)
            self._event(
                conn,
                actor,
                "identity.source_domain_changed",
                source_id,
                {"version": version, "domain": domain_id},
            )
            return version

    def source_domains(self, actor, source_ids, *, action="knowledge.read", conn=None):
        if conn is None:
            with self._read() as read:
                return self.source_domains(actor, source_ids, action=action, conn=read)
        self._actor(conn, actor)
        if not isinstance(source_ids, (list, tuple)) or any(
            not isinstance(s, str) for s in source_ids
        ):
            raise AccessError("Provide source identifiers.", "invalid_scope", 400)
        domains = set()
        for source_id in source_ids:
            rows = conn.execute(
                "SELECT domain_id FROM identity_source_domains WHERE source_id=?", (source_id,)
            ).fetchall()
            if not rows:
                raise AccessError(
                    "This source has no verified domain binding. An administrator must classify it first.",
                    "source_scope_missing",
                )
            # Until per-publication domains are pinned, require all historical domains.
            # Moving a source must never expose its former private history to a new reader.
            domains.update(r[0] for r in rows)
        selected = tuple(sorted(domains)) if domains else ("*",)
        self.require(actor, action, domains=selected, conn=conn)
        return selected

    def principals(self, actor):
        with self._read() as conn:
            self.require(actor, "identity.admin", conn=conn)
            return [
                self._public(row)
                for row in conn.execute("SELECT * FROM identity_principals ORDER BY created_at,id")
            ]

    def administration(self, actor):
        """One coherent, secret-free policy packet for human administration UI/CLI."""
        with self._read() as conn:
            self.require(actor, "identity.admin", conn=conn)
            state = self._state(conn)
            people = []
            for row in conn.execute("SELECT * FROM identity_principals ORDER BY created_at,id"):
                person = self._public(row)
                person["grants"] = [
                    dict(grant)
                    for grant in conn.execute(
                        "SELECT role,domain_id FROM identity_grants WHERE principal_id=? AND revoked_at IS NULL ORDER BY role,domain_id",
                        (row["id"],),
                    )
                ]
                delegation = conn.execute(
                    "SELECT delegator_id,actions_json,domains_json,expires_at,revoked_at FROM identity_delegations WHERE principal_id=?",
                    (row["id"],),
                ).fetchone()
                person["delegation"] = (
                    None
                    if not delegation
                    else {
                        "delegator_id": delegation["delegator_id"],
                        "actions": json.loads(delegation["actions_json"]),
                        "domains": json.loads(delegation["domains_json"]),
                        "expires_at": delegation["expires_at"],
                        "revoked_at": delegation["revoked_at"],
                    }
                )
                people.append(person)
            return {
                "workspace_id": state["workspace_id"],
                "policy_version": state["policy_version"],
                "principals": people,
                "roles": {name: sorted(actions) for name, actions in ROLES.items()},
                "delegatable_actions": sorted(
                    a for a, c in CAPABILITIES.items() if not c.human_only
                ),
                "domains": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id,slug,name FROM identity_domains ORDER BY slug"
                    )
                ],
                "sources": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT s.id,s.name,d.domain_id,d.version FROM sources s LEFT JOIN identity_source_domains d ON d.source_id=s.id AND d.version=(SELECT max(version) FROM identity_source_domains WHERE source_id=s.id) ORDER BY s.name,s.id"
                    )
                ],
            }

    def credentials(self, actor, *, principal_id=None):
        """List own sessions by default; inspecting another principal needs admin."""
        with self._read() as conn:
            actor = self._actor(conn, actor)
            selected = actor.principal_id if principal_id is None else principal_id
            if selected != actor.principal_id:
                self.require(actor, "identity.admin", conn=conn)
            rows = conn.execute(
                "SELECT c.id,c.principal_id,c.created_at,c.expires_at,c.revoked_at,c.epoch AS credential_epoch,p.epoch AS principal_epoch,p.status AS principal_status,p.username,p.kind FROM identity_credentials c JOIN identity_principals p ON p.id=c.principal_id WHERE (?='*' OR c.principal_id=?) ORDER BY c.created_at DESC,c.id",
                (selected, selected),
            ).fetchall()
            return [
                {
                    **{
                        k: row[k]
                        for k in (
                            "id",
                            "principal_id",
                            "username",
                            "kind",
                            "created_at",
                            "expires_at",
                            "revoked_at",
                        )
                    },
                    "current": row["id"] == actor.credential_id,
                    "status": "revoked"
                    if row["revoked_at"] is not None
                    else "invalidated"
                    if row["credential_epoch"] != row["principal_epoch"]
                    or row["principal_status"] != "active"
                    else "expired"
                    if row["expires_at"] <= self.clock()
                    else "active",
                }
                for row in rows
            ]

    def audit(self, actor):
        with self._read() as conn:
            self.require(actor, "audit.read", conn=conn)
            return [dict(row) for row in conn.execute("SELECT * FROM identity_events ORDER BY id")]

    def verify_audit(self, actor):
        records = self.audit(actor)
        previous = ""
        expected = 1
        for row in records:
            stamp = row.pop("sha256")
            if row["id"] != expected or row["previous_sha256"] != previous or digest(row) != stamp:
                return {"ok": False, "first_invalid_event": row["id"]}
            expected += 1
            previous = stamp
        return {"ok": bool(records), "events": len(records), "head_sha256": previous}
