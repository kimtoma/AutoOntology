# SPDX-License-Identifier: MIT
"""Host-owned identity pins, independent of replaceable workspace databases.

The OS account is trusted. Registry location is derived from its passwd entry,
not a request, workspace setting or HOME environment override. Readers never
create files. Writers serialize, reserve durably, commit SQLite, then finalize.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from autoontology.security.fs_paths import canonical_path
from autoontology.security.policy import AccessError

_LOCAL_CEREMONY = threading.RLock()
_HEADER_LOCK = threading.RLock()
_HEADER_INDEX = {}

MAX_BYTES = 16384
ID = re.compile(r"[0-9a-f]{32}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def recovery_error():
    return AccessError(
        "Host enrollment cannot be verified; preserve the database and host pin, then use local-owner recovery.",
        "identity_configuration_missing",
        503,
    )


def registry_root():
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/share/autoontology/owner-registry"


def database_path(path):
    try:
        return str(canonical_path(path, single_link=True))
    except (OSError, ValueError, RuntimeError):
        raise recovery_error() from None


def _literal_filename(path):
    return hashlib.sha256(str(path).encode()).hexdigest() + ".json"


def _filename(path):
    return _literal_filename(database_path(path))


def _fold(path):
    # Candidate lookup only. Authority requires native canonical equality below.
    return unicodedata.normalize("NFD", str(path)).casefold()


def _private(info, *, directory=False):
    if (
        info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
    ):
        raise recovery_error()


@contextmanager
def _root(*, create=False):
    """Walk without following symlinks, including registry ancestor components."""
    root = registry_root().absolute()
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in root.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
            except FileNotFoundError:
                if create:
                    raise
                yield None
                return
            info = os.fstat(child)
            trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not trusted_sticky):
                os.close(child)
                raise recovery_error()
            os.close(fd)
            fd = child
        _private(os.fstat(fd), directory=True)
        yield fd
    except OSError as error:
        raise recovery_error() from error
    finally:
        os.close(fd)


def restore_required():
    return AccessError(
        "This restored workspace is reserved and inactive; explicit local-owner activation is required.",
        "restore_activation_required",
        503,
    )


def _decode_restore(data, path, *, frozen):
    keys = {
        "version",
        "kind",
        "role",
        "phase",
        "database",
        "workspace_id",
        "organization_id",
        "operation_id",
        "request_sha256",
        "manifest_sha256",
        "source_database",
        "source_pin_sha256",
        "backup_pin_sha256",
        "ports",
        "copy_mode",
        "os_uid",
        "builder_sha256",
        "profile_sha256",
        "receipt_sha256",
    }
    if (
        set(data) != keys
        or data["kind"] != "restore"
        or data["role"] != "destination"
        or data["phase"] not in {"reserved", "staged"}
        or data["copy_mode"] not in {"clone", "copy"}
        or type(data["os_uid"]) is not int
        or data["os_uid"] < 0
    ):
        raise ValueError
    for key in ("database", "source_database"):
        value = data[key]
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or str(Path(value)) != value
            or ".." in Path(value).parts
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError
    if (
        data["database"] != str(path)
        if frozen
        else database_path(data["database"]) != database_path(path)
    ):
        raise ValueError
    for key in ("workspace_id", "organization_id", "operation_id"):
        if not isinstance(data[key], str) or not ID.fullmatch(data[key]):
            raise ValueError
    for key in (
        "request_sha256",
        "manifest_sha256",
        "source_pin_sha256",
        "backup_pin_sha256",
        "builder_sha256",
    ):
        if not isinstance(data[key], str) or not SHA.fullmatch(data[key]):
            raise ValueError
    for key in ("profile_sha256", "receipt_sha256"):
        if data["phase"] == "reserved" and data[key] is not None:
            raise ValueError
        if data["phase"] == "staged" and (
            not isinstance(data[key], str) or not SHA.fullmatch(data[key])
        ):
            raise ValueError
    ports = data["ports"]
    if (
        not isinstance(ports, dict)
        or set(ports) != {"ui", "backend", "postgres"}
        or any(type(v) is not int or not 1024 <= v <= 65535 for v in ports.values())
        or len(set(ports.values())) != 3
    ):
        raise ValueError
    return data


def _decode(payload, path, *, frozen=False):
    try:
        data = json.loads(payload)
        if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 4:
            from autoontology.recovery.transfer_registry import decode_pin

            return decode_pin(data, path, frozen)
        if isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 3:
            return _decode_restore(data, path, frozen=frozen)
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data["version"] not in {1, 2}
            or not isinstance(data.get("database"), str)
            or (
                data["database"] != str(path)
                if frozen
                else database_path(data["database"]) != database_path(path)
            )
            or data.get("kind") not in {"adopt", "enroll"}
            or data.get("phase") not in {"pending", "active"}
        ):
            raise ValueError
        for key in ("workspace_id", "organization_id", "operation_id", "principal_id"):
            if not isinstance(data.get(key), str) or not ID.fullmatch(data[key]):
                raise ValueError
        if data["kind"] == "enroll":
            spec = data.get("enrollment")
            if not isinstance(spec, dict) or set(spec) != {
                "workspace_name",
                "username",
                "name",
                "domain_id",
            }:
                raise ValueError
            if not isinstance(spec["domain_id"], str) or not ID.fullmatch(spec["domain_id"]):
                raise ValueError
            if not isinstance(spec["username"], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{1,63}", spec["username"]
            ):
                raise ValueError
            for key in ("workspace_name", "name"):
                value = spec[key]
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > 100
                    or any(ord(c) < 32 for c in value)
                ):
                    raise ValueError
        if ("recovery" in data) != (data["version"] == 2):
            raise ValueError
        if "recovery" in data:
            recovery = data["recovery"]
            if (
                data["phase"] != "active"
                or not isinstance(recovery, dict)
                or set(recovery)
                != {
                    "operation_id",
                    "phase",
                    "principal_id",
                    "username",
                    "before_policy_version",
                    "before_epoch",
                    "os_uid",
                    "previous_event_sha256",
                    "event_sha256",
                }
            ):
                raise ValueError
            if recovery["phase"] not in {"pending", "active"}:
                raise ValueError
            for key in ("operation_id", "principal_id"):
                if not isinstance(recovery[key], str) or not ID.fullmatch(recovery[key]):
                    raise ValueError
            if not isinstance(recovery["username"], str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{1,63}", recovery["username"]
            ):
                raise ValueError
            for key in ("before_policy_version", "before_epoch", "os_uid"):
                if type(recovery[key]) is not int or recovery[key] < (0 if key == "os_uid" else 1):
                    raise ValueError
            previous = recovery["previous_event_sha256"]
            if not isinstance(previous, str) or not SHA.fullmatch(previous):
                raise ValueError
            recovered_stamp = recovery["event_sha256"]
            if recovery["phase"] == "active" and (
                not isinstance(recovered_stamp, str) or not SHA.fullmatch(recovered_stamp)
            ):
                raise ValueError
            if recovery["phase"] == "pending" and recovered_stamp is not None:
                raise ValueError
        stamp = data.get("event_sha256")
        if data["phase"] == "active" and (not isinstance(stamp, str) or not SHA.fullmatch(stamp)):
            raise ValueError
        if data["phase"] == "pending" and stamp is not None:
            raise ValueError
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise recovery_error() from None


def decode_snapshot(payload, database):
    """Decode an offline literal header, without dereferencing its original path.

    This checks a frozen DB/pin pair only. It is never used by live host gates
    and cannot grant permission to rebind or run a second workspace home.
    """
    if (
        not isinstance(database, str)
        or not Path(database).is_absolute()
        or str(Path(database)) != database
        or ".." in Path(database).parts
        or any(ord(c) < 32 for c in database)
    ):
        raise recovery_error()
    return _decode(payload, database, frozen=True)


def _payload(fd, name):
    try:
        handle = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd
        )
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            _private(info)
            if info.st_size > MAX_BYTES:
                raise recovery_error()
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise recovery_error()
        return payload
    except OSError:
        raise recovery_error() from None


def _headers(fd):
    """Cache only immutable path headers, never authorization or journal payloads.

    The filename is a digest of the exact path header. Enumerate names afresh on
    every lookup; timestamp collisions cannot hide an added or removed marker.
    Selected payloads are always freshly read and fully checked.
    """
    names = tuple(sorted(n for n in os.listdir(fd) if re.fullmatch(r"[0-9a-f]{64}\.json", n)))
    info = os.fstat(fd)
    key = (info.st_dev, info.st_ino, names)
    with _HEADER_LOCK:
        if key in _HEADER_INDEX:
            return _HEADER_INDEX[key]
        index = {}
        for name in names:
            try:
                raw = json.loads(_payload(fd, name))
                path = raw["database"]
                if not isinstance(path, str) or not Path(path).is_absolute():
                    raise ValueError
                if _literal_filename(path) != name:
                    raise ValueError
            except (ValueError, TypeError, KeyError, RecursionError):
                raise recovery_error() from None
            index.setdefault(_fold(path), []).append((name, path))
        if len(_HEADER_INDEX) >= 16:
            _HEADER_INDEX.pop(next(iter(_HEADER_INDEX)))
        _HEADER_INDEX[key] = index
        return index


def _read_entry(fd, path):
    canonical = database_path(path)
    matches = []
    for name, recorded in _headers(fd).get(_fold(canonical), ()):
        actual = database_path(recorded)
        if actual != canonical:
            # Distinct existing objects on a case-sensitive filesystem stay
            # distinct. Missing same-fold paths cannot be safely disambiguated.
            if not Path(recorded).exists() or not Path(canonical).exists():
                raise recovery_error()
            continue
        payload = _payload(fd, name)
        data = _decode(payload, path)
        if data["database"] != recorded:
            raise recovery_error()
        matches.append((name, data))
    if len(matches) > 1:
        raise recovery_error()
    return matches[0] if matches else None


def _read(fd, path):
    entry = _read_entry(fd, path)
    return entry[1] if entry else None


def read(path):
    database_path(path)
    with _root() as fd:
        return _read(fd, path) if fd is not None else None


def _open_lock(directory, path):
    """Separate exclusive creation from reopening a concurrently created lock.

    On this macOS host O_CREAT|O_NOFOLLOW without O_EXCL can return ENOENT
    when another process creates that same name. Never recreate on reopen:
    removal, symlinks, unsafe modes and multiple links must still fail closed.
    """
    entry = _read_entry(directory, path)
    name = (entry[0] if entry else _filename(path)) + ".lock"
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    except FileExistsError:
        return os.open(name, flags, dir_fd=directory)


@contextmanager
def locked(path):
    """Only the trusted local ceremony may reserve/finalize a pin."""
    with _LOCAL_CEREMONY:
        with _root(create=True) as fd:
            handle = _open_lock(fd, path)
            try:
                _private(os.fstat(handle))
                fcntl.flock(handle, fcntl.LOCK_EX)
                yield fd
            finally:
                os.close(handle)


def write(fd, path, data):
    """Install a fully validated journal record atomically under the host lock."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    _decode(payload, path)
    if data.get("version") != 4:
        from autoontology.recovery.transfer_registry import read as read_transfer

        if read_transfer(data["workspace_id"], fd) is not None:
            raise AccessError(
                "Resolve the reserved host transfer before changing its owner pin.",
                "workspace_transfer_pending",
                503,
            )
    entry = _read_entry(fd, path)
    name = entry[0] if entry else _filename(path)
    # Continue using a genuine older spelling's key and lock. Never mint a
    # second marker or silently move a preexisting host authority record.
    if entry:
        data = {**data, "database": entry[1]["database"]}
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    if _literal_filename(data["database"]) != name:
        raise recovery_error()
    temporary = ".pin-" + secrets.token_hex(16)
    try:
        handle = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=fd,
        )
        with os.fdopen(handle, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    except OSError:
        raise recovery_error() from None
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def _dict_row(conn, sql, parameters=()):
    cursor = conn.execute(sql, parameters)
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row)) if row else None


def check_pair(conn, state, marker):
    """Validate frozen marker/DB data without consulting or changing the host."""
    if marker.get("version") == 4:
        from autoontology.recovery.transfer import check_snapshot

        check_snapshot(conn, marker)
        return
    if marker.get("kind") == "restore":
        raise restore_required()
    if marker["phase"] != "active" or state is None:
        raise recovery_error()
    if any(state[key] != marker[key] for key in ("workspace_id", "organization_id")):
        raise recovery_error()
    row = _dict_row(conn, "SELECT * FROM identity_events WHERE sha256=?", (marker["event_sha256"],))
    if row is None:
        raise recovery_error()
    from autoontology.store.operations import digest

    stamp = row.pop("sha256")
    detail = json.loads(row["detail_json"])
    if (
        digest(row) != stamp
        or row["action"] != "identity.owner_pinned"
        or row["target"] != marker["workspace_id"]
        or row["actor_id"] != marker["principal_id"]
        or detail.get("operation_id") != marker["operation_id"]
        or (marker["kind"] == "enroll" and detail.get("kind") != "enroll")
    ):
        raise recovery_error()
    if "recovery" in marker:
        from autoontology.security.owner_recovery import check_active

        check_active(conn, state, marker)


def check_snapshot(conn, marker):
    """Validate an offline active or interrupted pair without activating it."""
    from autoontology.security.enrollment import database_enrollment_state

    state = database_enrollment_state(conn)
    if marker.get("recovery", {}).get("phase") == "pending":
        from autoontology.security.owner_recovery import _snapshot

        _snapshot(conn, marker)
    elif marker["phase"] == "active":
        check_pair(conn, state, marker)
    elif marker["kind"] == "enroll":
        if state is not None:
            from autoontology.security.owner_enrollment import _committed

            _committed(conn, marker)
    else:
        # Adoption snapshots were required to be active before this extension.
        raise recovery_error()


def check(conn, state):
    """Apply the host gate to every shared enrollment classification."""
    try:
        path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
        if not path:  # In-memory synthetic stores have no independently restorable path.
            return
        marker = read(path)
        if marker is not None:
            from autoontology.recovery.transfer_registry import guard_binding

            guard_binding(marker)
            check_pair(conn, state, marker)
            return
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_events'"
        ).fetchone()
        if (
            exists
            and conn.execute(
                "SELECT 1 FROM identity_events WHERE action='identity.owner_pinned' LIMIT 1"
            ).fetchone()
        ):
            raise recovery_error()
    except (ValueError, TypeError, KeyError):
        raise recovery_error() from None


def guard_missing_database(path):
    database_path(path)
    marker = read(path)
    if marker is not None:
        from autoontology.recovery.transfer_registry import guard_binding

        guard_binding(marker)
    if marker is not None and marker.get("kind") == "restore":
        raise restore_required()
    if not Path(path).exists() and marker is not None:
        raise recovery_error()


def public(marker):
    return {
        key: value for key, value in marker.items() if key not in {"database", "version", "kind"}
    }
