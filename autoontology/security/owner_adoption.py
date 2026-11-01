# SPDX-License-Identifier: MIT
"""Explicit local adoption of a complete pre-marker identity, without replacement.

No HTTP route calls this ceremony. A password-authenticated active human workspace
administrator and a trusted local OS invocation are both required. A pending
operation can be resumed; damaged metadata requires a complete backup instead.
"""

from __future__ import annotations

import uuid

from autoontology.security import owner_registry as host
from autoontology.security.context import _IDENTITY
from autoontology.security.credentials import Secret, check_password
from autoontology.security.enrollment import database_enrollment_state
from autoontology.security.policy import AccessError, Actor
from autoontology.store.identity import Identity
from autoontology.store.operations import digest


def _boundary(name):
    """Named durability boundaries exercised by interruption acceptance tests."""


def _audit(conn):
    if any(row[0].startswith("identity_") for row in conn.execute("PRAGMA foreign_key_check")):
        raise host.recovery_error()
    previous, expected = "", 1
    cursor = conn.execute("SELECT * FROM identity_events ORDER BY id")
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        data = dict(zip(columns, row))
        stamp = data.pop("sha256")
        if data["id"] != expected or data["previous_sha256"] != previous or digest(data) != stamp:
            raise host.recovery_error()
        previous, expected = stamp, expected + 1
    if expected == 1:
        raise host.recovery_error()


def adopt(store, username, password, *, resume=False):
    if _IDENTITY.get() is not None:
        raise AccessError(
            "Owner adoption requires a separate local OS-owner ceremony, not an application delegation.",
            "local_owner_required",
            403,
        )
    # No pin is created on a missing, unconfigured or corrupt database.
    with Identity(store)._read() as conn:
        state = database_enrollment_state(conn)
        if state is None:
            raise AccessError("Enroll identity before adoption.", "identity_unconfigured", 409)
    with host.locked(store.path) as directory:
        with store._immediate() as conn:
            state = database_enrollment_state(conn)
            if state is None:
                raise host.recovery_error()
            _audit(conn)
            principal = conn.execute(
                "SELECT * FROM identity_principals WHERE username=?", (username,)
            ).fetchone()
            if (
                principal is None
                or principal["kind"] != "human"
                or principal["status"] != "active"
                or not check_password(password, principal["password_hash"])
            ):
                raise AccessError(
                    "The username or password is invalid.", "invalid_credentials", 401
                )
            grant = conn.execute(
                "SELECT 1 FROM identity_grants WHERE principal_id=? AND workspace_id=? AND role='admin' AND domain_id='*' AND revoked_at IS NULL",
                (principal["id"], state["workspace_id"]),
            ).fetchone()
            if grant is None:
                raise AccessError()
            marker = host._read(directory, store.path)
            if marker is not None and marker["phase"] == "active":
                host.check_pair(conn, state, marker)
                return host.public(marker)
            if marker is None:
                if conn.execute(
                    "SELECT 1 FROM identity_events WHERE action='identity.owner_pinned' LIMIT 1"
                ).fetchone():
                    raise host.recovery_error()
                marker = {
                    "version": 1,
                    "database": host.database_path(store.path),
                    "kind": "adopt",
                    "phase": "pending",
                    "operation_id": uuid.uuid4().hex,
                    "workspace_id": state["workspace_id"],
                    "organization_id": state["organization_id"],
                    "principal_id": principal["id"],
                    "event_sha256": None,
                }
                host.write(directory, store.path, marker)
            elif (
                marker["kind"] != "adopt"
                or not resume
                or marker["workspace_id"] != state["workspace_id"]
                or marker["organization_id"] != state["organization_id"]
                or marker["principal_id"] != principal["id"]
            ):
                raise host.recovery_error()
            existing = conn.execute(
                "SELECT * FROM identity_events WHERE action='identity.owner_pinned' AND json_extract(detail_json,'$.operation_id')=?",
                (marker["operation_id"],),
            ).fetchone()
            if existing is None:
                actor = Actor(
                    principal["id"], None, "human", principal["name"], Secret("host-proof")
                )
                Identity(store)._event(
                    conn,
                    actor,
                    "identity.owner_pinned",
                    state["workspace_id"],
                    {
                        "operation_id": marker["operation_id"],
                        "trust": "local OS owner and human administrator password",
                    },
                )
                existing = conn.execute(
                    "SELECT * FROM identity_events ORDER BY id DESC LIMIT 1"
                ).fetchone()
            marker = {**marker, "phase": "active", "event_sha256": existing["sha256"]}
            _boundary("before_commit")
        _boundary("after_commit")
        _boundary("before_finalize")
        host.write(directory, store.path, marker)
        return host.public(marker)
