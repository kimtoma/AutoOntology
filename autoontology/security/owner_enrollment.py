# SPDX-License-Identifier: MIT
"""Host-reserved, resumable first enrollment; deliberately absent from HTTP."""

from __future__ import annotations

import json
import uuid

from autoontology.security import owner_registry as host
from autoontology.security.context import _IDENTITY
from autoontology.security.credentials import Secret, check_password, hash_password
from autoontology.security.enrollment import database_enrollment_state
from autoontology.security.owner_adoption import _audit
from autoontology.security.policy import AccessError, Actor
from autoontology.store.identity import Identity, _name, _username


def _boundary(name):
    """Named durable boundaries used by process-interruption acceptance."""


def _committed(conn, marker):
    """Verify a committed pending enrollment, without bypassing normal serving gates."""
    state = database_enrollment_state(conn)
    spec = marker["enrollment"]
    if state is None or any(state[k] != marker[k] for k in ("workspace_id", "organization_id")):
        raise host.recovery_error()
    if state["workspace_name"] != spec["workspace_name"]:
        raise host.recovery_error()
    _audit(conn)
    principal = conn.execute(
        "SELECT * FROM identity_principals WHERE id=?", (marker["principal_id"],)
    ).fetchone()
    if (
        principal is None
        or principal["kind"] != "human"
        or principal["status"] != "active"
        or principal["username"] != spec["username"]
        or principal["name"] != spec["name"]
        or not conn.execute(
            "SELECT 1 FROM identity_domains WHERE id=? AND workspace_id=? AND slug='general'",
            (spec["domain_id"], marker["workspace_id"]),
        ).fetchone()
        or not conn.execute(
            "SELECT 1 FROM identity_grants WHERE principal_id=? AND workspace_id=? AND role='admin' AND domain_id='*' AND revoked_at IS NULL",
            (principal["id"], marker["workspace_id"]),
        ).fetchone()
    ):
        raise host.recovery_error()
    events = list(
        conn.execute(
            "SELECT * FROM identity_events WHERE action IN ('identity.bootstrap','identity.owner_pinned') ORDER BY id"
        )
    )
    owned = [
        row
        for row in events
        if json.loads(row["detail_json"]).get("operation_id") == marker["operation_id"]
    ]
    if len(owned) != 2 or [row["action"] for row in owned] != [
        "identity.bootstrap",
        "identity.owner_pinned",
    ]:
        raise host.recovery_error()
    for row in owned:
        if row["actor_id"] != marker["principal_id"] or row["target"] != marker["workspace_id"]:
            raise host.recovery_error()
    detail = json.loads(owned[0]["detail_json"])
    if (
        detail.get("default_domain") != spec["domain_id"]
        or json.loads(owned[1]["detail_json"]).get("kind") != "enroll"
    ):
        raise host.recovery_error()
    return principal, owned[1]["sha256"]


def enroll(store, workspace, username, name, password, *, resume=False):
    if _IDENTITY.get() is not None:
        raise AccessError(
            "First enrollment requires a separate local OS-owner ceremony, not an application session.",
            "local_owner_required",
            403,
        )
    username, name = _username(username), _name(name)
    if (
        not isinstance(workspace, str)
        or not workspace.strip()
        or len(workspace) > 100
        or any(ord(c) < 32 for c in workspace)
    ):
        raise AccessError("Provide the local workspace name.", "invalid_workspace", 400)
    workspace = workspace.strip()
    # Validate/derive before reserving a pin; never persist a password verifier outside SQLite.
    password_record = hash_password(password)
    with host.locked(store.path) as directory:
        with store._immediate() as conn:
            state = database_enrollment_state(conn)
            marker = host._read(directory, store.path)
            if marker is None:
                if state is not None:
                    if conn.execute(
                        "SELECT 1 FROM identity_events WHERE action='identity.owner_pinned' LIMIT 1"
                    ).fetchone():
                        raise host.recovery_error()
                    raise AccessError(
                        "Identity is already configured; use login or explicit owner-adopt for a legacy workspace.",
                        "identity_already_configured",
                        409,
                    )
                ws, org, pid, domain = (uuid.uuid4().hex for _ in range(4))
                marker = {
                    "version": 1,
                    "database": host.database_path(store.path),
                    "kind": "enroll",
                    "phase": "pending",
                    "operation_id": uuid.uuid4().hex,
                    "workspace_id": ws,
                    "organization_id": org,
                    "principal_id": pid,
                    "event_sha256": None,
                    "enrollment": {
                        "workspace_name": workspace,
                        "username": username,
                        "name": name,
                        "domain_id": domain,
                    },
                }
                host.write(directory, store.path, marker)
                _boundary("after_reserve")
            else:
                if marker["kind"] != "enroll":
                    raise host.recovery_error()
                spec = marker["enrollment"]
                if (workspace, username, name) != (
                    spec["workspace_name"],
                    spec["username"],
                    spec["name"],
                ):
                    raise host.recovery_error()
                if marker["phase"] == "pending" and not resume:
                    raise host.recovery_error()
            spec = marker["enrollment"]
            if state is None:
                if marker["phase"] != "pending":
                    raise host.recovery_error()
                service = Identity(store)
                service._seed_owner(
                    conn,
                    workspace,
                    username,
                    name,
                    password_record,
                    (
                        marker["workspace_id"],
                        marker["organization_id"],
                        marker["principal_id"],
                        spec["domain_id"],
                    ),
                )
                actor = Actor(marker["principal_id"], None, "human", name, Secret("host-proof"))
                service._event(
                    conn,
                    actor,
                    "identity.bootstrap",
                    marker["workspace_id"],
                    {
                        "trust": "local OS-owner first enrollment",
                        "default_domain": spec["domain_id"],
                        "operation_id": marker["operation_id"],
                    },
                )
                service._event(
                    conn,
                    actor,
                    "identity.owner_pinned",
                    marker["workspace_id"],
                    {
                        "trust": "local OS-owner first enrollment",
                        "kind": "enroll",
                        "operation_id": marker["operation_id"],
                    },
                )
            principal, stamp = _committed(conn, marker)
            if not check_password(password, principal["password_hash"]):
                raise AccessError(
                    "The username or password is invalid.", "invalid_credentials", 401
                )
            if marker["phase"] == "active":
                host.check_pair(conn, state, marker)
                return host.public(marker)
            active = {**marker, "phase": "active", "event_sha256": stamp}
            _boundary("before_commit")
        _boundary("after_commit")
        _boundary("before_finalize")
        host.write(directory, store.path, active)
        return host.public(active)
