# SPDX-License-Identifier: MIT
"""Local OS-owner credential recovery; never reconstructs missing identity rows."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from autoontology.security import owner_registry as host
from autoontology.security.context import _IDENTITY
from autoontology.security.credentials import Secret, check_password, hash_password
from autoontology.security.enrollment import database_enrollment_state
from autoontology.security.owner_adoption import _audit
from autoontology.security.policy import AccessError, Actor
from autoontology.store.identity import Identity, _username
from autoontology.store.operations import digest

REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
COUNTS = ("sessions_revoked", "delegations_revoked", "invalidated_packets")


def _boundary(name):
    """Durable boundaries for interruption acceptance, with no environment switch."""


def _base(marker):
    return {
        **{key: value for key, value in marker.items() if key != "recovery"},
        "version": 4 if marker.get("version") == 4 else 1,
    }


def _principal(conn, username, workspace_id):
    row = host._dict_row(conn, "SELECT * FROM identity_principals WHERE username=?", (username,))
    if row is None or row["kind"] != "human" or row["status"] != "active":
        raise host.recovery_error()
    if not conn.execute(
        "SELECT 1 FROM identity_grants WHERE principal_id=? AND workspace_id=? AND role='admin' AND domain_id='*' AND revoked_at IS NULL",
        (row["id"], workspace_id),
    ).fetchone():
        raise AccessError("Recover only an existing active whole-workspace human administrator.")
    return row


def _detail(row):
    try:
        data = json.loads(row["detail_json"])
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (TypeError, ValueError):
        raise host.recovery_error() from None


def _event(conn, stamp):
    row = host._dict_row(conn, "SELECT * FROM identity_events WHERE sha256=?", (stamp,))
    if row is None:
        raise host.recovery_error()
    payload = {key: value for key, value in row.items() if key != "sha256"}
    if digest(payload) != stamp:
        raise host.recovery_error()
    return row


def _previous(conn, marker, spec):
    stamp = spec["previous_event_sha256"]
    if stamp == marker["event_sha256"]:
        return
    row = _event(conn, stamp)
    detail = _detail(row)
    if (
        row["action"] != "identity.owner_recovered"
        or row["target"] != marker["workspace_id"]
        or detail.get("root_event_sha256") != marker["event_sha256"]
    ):
        raise host.recovery_error()


def _receipt(conn, marker, spec, *, optional=False):
    if spec["phase"] == "active":
        row = host._dict_row(
            conn, "SELECT * FROM identity_events WHERE sha256=?", (spec["event_sha256"],)
        )
    else:
        row = host._dict_row(
            conn,
            "SELECT * FROM identity_events WHERE action='identity.owner_recovered' AND json_extract(detail_json,'$.operation_id')=?",
            (spec["operation_id"],),
        )
    if row is None:
        if optional:
            return None
        raise host.recovery_error()
    row = _event(conn, row["sha256"])
    detail = _detail(row)
    if (
        row["action"] != "identity.owner_recovered"
        or detail.get("operation_id") != spec["operation_id"]
        or row["target"] != marker["workspace_id"]
        or row["actor_id"] != f"local-os-owner:{spec['os_uid']}"
        or row["credential_id"] is not None
        or detail.get("principal_id") != spec["principal_id"]
        or detail.get("root_event_sha256") != marker["event_sha256"]
        or detail.get("previous_event_sha256") != spec["previous_event_sha256"]
        or detail.get("before_policy_version") != spec["before_policy_version"]
        or detail.get("policy_version") != spec["before_policy_version"] + 1
        or detail.get("principal_epoch") != spec["before_epoch"] + 1
        or any(type(detail.get(key)) is not int or detail[key] < 0 for key in COUNTS)
        or (spec["phase"] == "active" and row["sha256"] != spec["event_sha256"])
    ):
        raise host.recovery_error()
    return {**detail, "event_sha256": row["sha256"]}


def check_active(conn, state, marker):
    """Cheap latest-recovery fence on the ordinary shared authority boundary."""
    spec = marker["recovery"]
    if spec["phase"] != "active":
        raise host.recovery_error()
    receipt = _receipt(conn, marker, spec)
    if state["policy_version"] < receipt["policy_version"]:
        raise host.recovery_error()


def _snapshot(conn, marker):
    """Validate a pending recovery before or after commit, without activating it."""
    state = database_enrollment_state(conn)
    host.check_pair(conn, state, _base(marker))
    _audit(conn)
    spec = marker["recovery"]
    _previous(conn, marker, spec)
    principal = _principal(conn, spec["username"], marker["workspace_id"])
    if principal["id"] != spec["principal_id"]:
        raise host.recovery_error()
    receipt = _receipt(conn, marker, spec, optional=True)
    increment = int(receipt is not None)
    if (
        state["policy_version"] != spec["before_policy_version"] + increment
        or principal["epoch"] != spec["before_epoch"] + increment
    ):
        raise host.recovery_error()
    if receipt is not None and (
        conn.execute(
            "SELECT 1 FROM identity_credentials WHERE revoked_at IS NULL LIMIT 1"
        ).fetchone()
        or conn.execute(
            "SELECT 1 FROM identity_delegations WHERE revoked_at IS NULL LIMIT 1"
        ).fetchone()
    ):
        raise host.recovery_error()
    return state, principal, receipt


def _public(marker, receipt):
    return {
        "workspace_id": marker["workspace_id"],
        "organization_id": marker["organization_id"],
        "principal_id": receipt["principal_id"],
        "operation_id": receipt["operation_id"],
        "phase": "active",
        "policy_version": receipt["policy_version"],
        "event_sha256": receipt["event_sha256"],
        **{key: receipt[key] for key in COUNTS},
    }


def recover(store, username, password, *, workspace_id, request_id, resume=False):
    if _IDENTITY.get() is not None:
        raise AccessError(
            "Use a separate local OS-owner ceremony, not an application session.",
            "local_owner_required",
            403,
        )
    username = _username(username)
    if not isinstance(workspace_id, str) or not host.ID.fullmatch(workspace_id):
        raise AccessError("Specify the exact workspace identifier.", "invalid_workspace", 400)
    if not (resume and request_id is None) and (
        not isinstance(request_id, str) or not REQUEST.fullmatch(request_id)
    ):
        raise AccessError(
            "Use a stable non-secret request ID of 1–128 letters, digits, dots, colons, underscores or hyphens.",
            "invalid_request_id",
            400,
        )
    # No independent host proof exists for unpinned historical metadata.
    if host.read(store.path) is None:
        raise host.recovery_error()
    record = hash_password(password)
    operation = (
        digest(
            {
                "workspace_id": workspace_id,
                "request_id": request_id,
                "purpose": "owner-password-recovery",
            }
        )[:32]
        if request_id is not None
        else None
    )
    with host.locked(store.path) as directory:
        with store._immediate() as conn:
            marker = host._read(directory, store.path)
            if marker is None or marker["workspace_id"] != workspace_id:
                raise host.recovery_error()
            spec = marker.get("recovery")
            if operation is None:
                if spec is None or spec["username"] != username:
                    raise host.recovery_error()
                operation = spec["operation_id"]
            if resume and (
                spec is None or spec["operation_id"] != operation or spec["username"] != username
            ):
                raise host.recovery_error()
            if spec is not None and spec["phase"] == "pending":
                if not resume or spec["operation_id"] != operation or spec["username"] != username:
                    raise host.recovery_error()
                state, principal, receipt = _snapshot(conn, marker)
            else:
                state = database_enrollment_state(conn)
                host.check_pair(conn, state, marker)
                _audit(conn)
                principal = _principal(conn, username, workspace_id)
                if spec is not None and spec["operation_id"] == operation:
                    if principal["id"] != spec["principal_id"] or not check_password(
                        password, principal["password_hash"]
                    ):
                        raise AccessError(
                            "The recovered password or principal does not match this completed request.",
                            "invalid_credentials",
                            401,
                        )
                    return _public(marker, _receipt(conn, marker, spec))
                if conn.execute(
                    "SELECT 1 FROM identity_events WHERE action='identity.owner_recovered' AND json_extract(detail_json,'$.operation_id')=?",
                    (operation,),
                ).fetchone():
                    raise AccessError(
                        "This recovery request was superseded. Use a new explicit request ID.",
                        "recovery_request_superseded",
                        409,
                    )
                spec = {
                    "operation_id": operation,
                    "phase": "pending",
                    "principal_id": principal["id"],
                    "username": username,
                    "before_policy_version": state["policy_version"],
                    "before_epoch": principal["epoch"],
                    "os_uid": os.getuid(),
                    "previous_event_sha256": spec["event_sha256"]
                    if spec
                    else marker["event_sha256"],
                    "event_sha256": None,
                }
                marker = {
                    **marker,
                    "version": 4 if marker.get("version") == 4 else 2,
                    "recovery": spec,
                }
                host.write(directory, store.path, marker)
                _boundary("after_reserve")
                receipt = None
            if receipt is None:
                service = Identity(store)
                now = service.clock()
                sessions = conn.execute(
                    "UPDATE identity_credentials SET revoked_at=? WHERE revoked_at IS NULL", (now,)
                ).rowcount
                delegations = conn.execute(
                    "UPDATE identity_delegations SET revoked_at=? WHERE revoked_at IS NULL", (now,)
                ).rowcount
                packets = conn.execute(
                    "SELECT count(*) FROM ops_case_packets p WHERE p.expires_at>? AND json_extract(p.payload_json,'$.policy_version')=? AND NOT EXISTS(SELECT 1 FROM ops_case_receipts r WHERE r.packet_id=p.id)",
                    (now, state["policy_version"]),
                ).fetchone()[0]
                conn.execute("UPDATE identity_principals SET epoch=epoch+1")
                conn.execute(
                    "UPDATE identity_principals SET password_hash=? WHERE id=?",
                    (record, principal["id"]),
                )
                service._bump(conn)
                actor = Actor(
                    f"local-os-owner:{spec['os_uid']}",
                    None,
                    "human",
                    "Local OS owner",
                    Secret("host-proof"),
                )
                service._event(
                    conn,
                    actor,
                    "identity.owner_recovered",
                    workspace_id,
                    {
                        "operation_id": operation,
                        "principal_id": principal["id"],
                        "root_event_sha256": marker["event_sha256"],
                        "previous_event_sha256": spec["previous_event_sha256"],
                        "before_policy_version": spec["before_policy_version"],
                        "policy_version": spec["before_policy_version"] + 1,
                        "principal_epoch": spec["before_epoch"] + 1,
                        "sessions_revoked": sessions,
                        "delegations_revoked": delegations,
                        "invalidated_packets": packets,
                        "trust": "local OS-owner credential recovery for an existing human administrator",
                    },
                )
                receipt = _receipt(conn, marker, spec)
            if not check_password(
                password,
                conn.execute(
                    "SELECT password_hash FROM identity_principals WHERE id=?", (principal["id"],)
                ).fetchone()[0],
            ):
                raise AccessError(
                    "The recovered password does not match the committed request.",
                    "invalid_credentials",
                    401,
                )
            active = {
                **marker,
                "recovery": {**spec, "phase": "active", "event_sha256": receipt["event_sha256"]},
            }
            _boundary("before_commit")
        _boundary("after_commit")
        _boundary("before_finalize")
        host.write(directory, store.path, active)
        return _public(active, receipt)


def local_status(path):
    """Host-local diagnostics only; never create/migrate a missing database."""
    if _IDENTITY.get() is not None:
        raise AccessError(
            "Use local OS-owner diagnostics, not an application session.",
            "local_owner_required",
            403,
        )
    marker = host.read(path)
    if marker is None:
        raise host.recovery_error()
    if marker.get("version") == 4:
        if marker["role"] == "source":
            return {
                "workspace_id": marker["workspace_id"],
                "restore_phase": marker["phase"],
                "database_state": "retired_not_opened",
                "authority_active": False,
            }
        if "recovery" not in marker:
            from autoontology.recovery.transfer import status

            return status(Path(path).parent.parent)
    if marker.get("kind") == "restore":
        return {
            "workspace_id": marker["workspace_id"],
            "organization_id": marker["organization_id"],
            "restore_phase": marker["phase"],
            "database_state": "inactive_not_opened",
            "activation_required": True,
        }
    result = {
        "workspace_id": marker["workspace_id"],
        "organization_id": marker["organization_id"],
        "principal_id": marker["principal_id"],
        "pin_phase": marker["phase"],
        "database_state": "missing" if not Path(path).exists() else "unverified",
    }
    spec = marker.get("recovery")
    if spec:
        result["recovery"] = {
            key: spec[key] for key in ("operation_id", "phase", "principal_id", "username")
        }
    if result["database_state"] == "missing":
        return result
    with closing(
        sqlite3.connect(Path(host.database_path(path)).as_uri() + "?mode=ro", uri=True)
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            if spec and spec["phase"] == "pending":
                _, _, receipt = _snapshot(conn, marker)
                result["recovery"]["database_committed"] = receipt is not None
            else:
                host.check_snapshot(conn, marker)
                _audit(conn)
            result["database_state"] = "verified"
        except AccessError as error:
            result["database_state"] = "inconsistent"
            result["error"] = error.code
    return result
