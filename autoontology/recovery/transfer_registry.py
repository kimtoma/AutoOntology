# SPDX-License-Identifier: MIT
"""Independent single-workspace transfer journal and backward-reader fences."""

import fcntl
import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from autoontology.security import owner_registry as host
from autoontology.security.policy import AccessError


def error():
    return AccessError(
        "Preserve both homes, the backup and the host transfer journal; local owner recovery is required.",
        "restore_transfer_invalid",
        409,
    )


def digest(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _path(value):
    return (
        isinstance(value, str)
        and Path(value).is_absolute()
        and str(Path(value)) == value
        and ".." not in Path(value).parts
        and not any(ord(c) < 32 for c in value)
    )


def decode_pin(data, path, frozen=False):
    fields = {
        "version",
        "kind",
        "role",
        "phase",
        "database",
        "source_database",
        "destination_database",
        "workspace_id",
        "organization_id",
        "operation_id",
        "principal_id",
        "event_sha256",
        "manifest_sha256",
        "runtime_hashes",
        "os_uid",
    }
    if (
        set(data) - {"recovery"} != fields
        or data["kind"] != "transfer"
        or data["role"] not in {"source", "destination"}
        or data["phase"] not in {"pending", "active", "retired"}
    ):
        raise ValueError
    if (data["role"] == "source" and data["phase"] == "active") or (
        data["role"] == "destination" and data["phase"] == "retired"
    ):
        raise ValueError
    if any(not _path(data[k]) for k in ("database", "source_database", "destination_database")):
        raise ValueError
    if (
        data["database"] != str(path)
        if frozen
        else host.database_path(data["database"]) != host.database_path(path)
    ):
        raise ValueError
    expected = data["source_database"] if data["role"] == "source" else data["destination_database"]
    if data["database"] != expected or data["source_database"] == data["destination_database"]:
        raise ValueError
    for key in ("workspace_id", "organization_id", "operation_id", "principal_id"):
        if not isinstance(data[key], str) or not host.ID.fullmatch(data[key]):
            raise ValueError
    if type(data["os_uid"]) is not int or data["os_uid"] < 0:
        raise ValueError
    if not isinstance(data["manifest_sha256"], str) or not host.SHA.fullmatch(
        data["manifest_sha256"]
    ):
        raise ValueError
    stamp = data["event_sha256"]
    if data["phase"] == "pending" and stamp is not None:
        raise ValueError
    if data["phase"] != "pending" and (not isinstance(stamp, str) or not host.SHA.fullmatch(stamp)):
        raise ValueError
    hashes = data["runtime_hashes"]
    if not isinstance(hashes, dict) or set(hashes) != {
        "profile.json",
        "control.py",
        "local.env",
        "postgres/postgresql.conf",
        "postgres/postgresql.auto.conf",
        "postgres/pg_hba.conf",
        "postgres/pg_ident.conf",
    }:
        raise ValueError
    if any(not isinstance(v, str) or not host.SHA.fullmatch(v) for v in hashes.values()):
        raise ValueError
    if "recovery" in data:
        if data["role"] != "destination" or data["phase"] != "active":
            raise ValueError
        # Reuse the established recovery wire validator; this synthetic legacy
        # header validates format only and is never used as live authority.
        legacy = {
            "version": 2,
            "kind": "adopt",
            "phase": "active",
            "database": data["database"],
            **{
                k: data[k]
                for k in (
                    "workspace_id",
                    "organization_id",
                    "operation_id",
                    "principal_id",
                    "event_sha256",
                )
            },
            "recovery": data["recovery"],
        }
        host._decode(json.dumps(legacy), path, frozen=frozen)
    return data


def _name(workspace):
    if not isinstance(workspace, str) or not host.ID.fullmatch(workspace):
        raise error()
    return "transfer-" + workspace + ".json"


def _decode(raw, workspace):
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data.get("version") != 1
        or data.get("kind") != "workspace-transfer"
        or data.get("workspace_id") != workspace
        or data.get("phase") not in {"prepared", "pending", "active"}
    ):
        raise error()
    for key in ("workspace_id", "organization_id", "operation_id", "principal_id"):
        if not isinstance(data.get(key), str) or not host.ID.fullmatch(data[key]):
            raise error()
    if any(not _path(data.get(k)) for k in ("source_database", "destination_database")):
        raise error()
    if type(data.get("catalog_ready")) is not bool:
        raise error()
    review = data.get("catalog_review")
    if review is not None:
        if (
            not isinstance(review, dict)
            or set(review) != {"phase", "proposal_sha256", "event_sha256"}
            or review["phase"] not in {"pending", "active"}
            or not isinstance(review["proposal_sha256"], str)
            or not host.SHA.fullmatch(review["proposal_sha256"])
            or (review["phase"] == "pending" and review["event_sha256"] is not None)
            or (
                review["phase"] == "active"
                and (
                    not isinstance(review["event_sha256"], str)
                    or not host.SHA.fullmatch(review["event_sha256"])
                )
            )
            or data["catalog_ready"] != (review["phase"] == "active")
            or data["phase"] != "active"
        ):
            raise error()
    elif data["catalog_ready"]:
        raise error()
    if data.get("current_database") != (
        data["source_database"]
        if data["phase"] == "prepared"
        else data["destination_database"]
        if data["phase"] == "active"
        else None
    ):
        raise error()
    host.decode_snapshot(json.dumps(data["source_pin"]), data["source_database"])
    host.decode_snapshot(json.dumps(data["staged_pin"]), data["destination_database"])
    if digest(data["source_pin"]) != data.get("source_pin_sha256"):
        raise error()
    for key in ("workspace_id", "organization_id"):
        if data["source_pin"][key] != data[key] or data["staged_pin"][key] != data[key]:
            raise error()
    if (
        data["operation_id"] != data["staged_pin"]["operation_id"]
        or data["manifest_sha256"] != data["staged_pin"]["manifest_sha256"]
    ):
        raise error()
    for key in ("backup_policy", "policy_version", "before_epoch"):
        if type(data.get(key)) is not int or data[key] < 1:
            raise error()
    if (
        data["policy_version"] <= data["backup_policy"]
        or type(data.get("os_uid")) is not int
        or data["os_uid"] < 0
    ):
        raise error()
    stamp = data.get("event_sha256")
    if data["phase"] == "prepared" and stamp is not None:
        raise error()
    if data["phase"] == "active" and (not isinstance(stamp, str) or not host.SHA.fullmatch(stamp)):
        raise error()
    if stamp is not None and (not isinstance(stamp, str) or not host.SHA.fullmatch(stamp)):
        raise error()
    return data


def read(workspace, directory=None):
    if directory is None:
        with host._root() as fd:
            return read(workspace, fd) if fd is not None else None
    name = _name(workspace)
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    try:
        return _decode(host._payload(directory, name), workspace)
    except (ValueError, KeyError, TypeError, RecursionError):
        raise error() from None


def put(directory, data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > host.MAX_BYTES:
        raise error()
    _decode(raw, data["workspace_id"])
    temporary = ".transfer-write-" + secrets.token_hex(16)
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary, _name(data["workspace_id"]), src_dir_fd=directory, dst_dir_fd=directory
        )
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


@contextmanager
def locked(workspace):
    with host._LOCAL_CEREMONY, host._root(create=True) as directory:
        name = _name(workspace) + ".lock"
        try:
            fd = os.open(
                name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
        except FileExistsError:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        try:
            host._private(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(fd)


def guard_binding(marker):
    data = read(marker["workspace_id"])
    if data is None:
        if marker.get("version") == 4:
            raise error()
        return
    path = host.database_path(marker["database"])
    if (
        data["phase"] == "prepared"
        and path == host.database_path(data["source_database"])
        and digest(marker) == data["source_pin_sha256"]
    ):
        return
    if (
        data["phase"] == "active"
        and path == host.database_path(data["destination_database"])
        and marker.get("version") == 4
        and marker["role"] == "destination"
        and marker["phase"] == "active"
        and marker["event_sha256"] == data["event_sha256"]
        and all(
            marker[k] == data[k]
            for k in (
                "operation_id",
                "principal_id",
                "organization_id",
                "source_database",
                "destination_database",
                "manifest_sha256",
                "runtime_hashes",
                "os_uid",
            )
        )
    ):
        return
    raise AccessError(
        "This workspace binding is retired or awaiting transfer completion.",
        "workspace_transfer_pending",
        503,
    )
