# SPDX-License-Identifier: MIT
"""Inspect complete local checkpoints without opening their original runtime.

File digests prove consistency with the supplied manifest, not provenance or
permission to activate an identity. Never import archived source, load its env,
start Postgres, migrate SQLite, or consult the live host registry here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tarfile
import time
from contextlib import closing
from pathlib import Path, PurePosixPath

from autoontology.security.context import _IDENTITY
from autoontology.security.policy import AccessError

MAX_MANIFEST = 2 * 1024 * 1024
MAX_ENTRIES = 40000
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_ARCHIVE = 128 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = {
    "version",
    "commit",
    "label",
    "databases",
    "runtime",
    "copy_mode",
    "excluded_runtime_caches",
    "host_database",
    "host_pin_present",
    "sha256",
}
REQUIRED = {
    "workspace/autoontology.db",
    "source.tar",
    "local.env",
    "control.py",
    "postgres/PG_VERSION",
    "postgres/global/pg_control",
    "postgres/postgresql.conf",
    "postgres/postgresql.auto.conf",
    "postgres/pg_hba.conf",
    "postgres/pg_ident.conf",
}
FORBIDDEN = {
    "workspace/autoontology.db-wal",
    "workspace/autoontology.db-shm",
    "postgres/postmaster.pid",
    "postgres/standby.signal",
    "postgres/recovery.signal",
}


def _invalid():
    return AccessError(
        "Use a complete offline checkpoint with private regular files, matching inventory, source archive and database/host-pin pair. Preserve the original backup.",
        "checkpoint_invalid",
        400,
    )


def _relative(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or PurePosixPath(value).is_absolute()
        or str(PurePosixPath(value)) != value
        or any(p in {".", ".."} for p in value.split("/"))
    ):
        raise _invalid()
    return value


def _absolute(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise _invalid()
    _relative(value[1:])


def _private(info, *, directory=False):
    if (
        info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
    ):
        raise _invalid()


def _file(path):
    """Open the selected regular file only; FIFO/link payloads cannot block IO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    stream = os.fdopen(fd, "rb")
    try:
        _private(os.fstat(fd))
    except BaseException:
        stream.close()
        raise
    return stream


def _object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise _invalid()
        data[key] = value
    return data


def _manifest(root):
    with _file(root / "manifest.json") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise _invalid()
    data = json.loads(raw, object_pairs_hook=_object)
    if (
        not isinstance(data, dict)
        or set(data) != FIELDS
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["commit"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", data["commit"])
        or not isinstance(data["label"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", data["label"])
        or type(data["host_pin_present"]) is not bool
        or data["copy_mode"] not in {"auto", "clone", "copy"}
        or not isinstance(data["sha256"], dict)
        or not 1 <= len(data["sha256"]) <= MAX_ENTRIES
        or not isinstance(data["databases"], list)
        or not data["databases"]
        or any(
            not isinstance(n, str)
            or not re.fullmatch(r"postgres|autoontology(?:_src_[a-z0-9_]{1,49})?", n)
            for n in data["databases"]
        )
        or len(set(data["databases"])) != len(data["databases"])
        or "autoontology" not in data["databases"]
        or data["excluded_runtime_caches"] != ["workspace/kordoc", "workspace/run/codex-home"]
    ):
        raise _invalid()
    for key in ("runtime", "host_database"):
        _absolute(data[key])
    for name, stamp in data["sha256"].items():
        _relative(name)
        if not isinstance(stamp, str) or not SHA.fullmatch(stamp):
            raise _invalid()
    names = set(data["sha256"])
    if (
        not REQUIRED.issubset(names)
        or FORBIDDEN.intersection(names)
        or ("host-owner.json" in names) != data["host_pin_present"]
        or any(
            n.split("/")[0]
            not in {
                "workspace",
                "postgres",
                "exports",
                "source.tar",
                "local.env",
                "control.py",
                "host-owner.json",
            }
            for n in names
        )
    ):
        raise _invalid()
    return data, hashlib.sha256(raw).hexdigest()


def _inventory(payload):
    """Reject links/special files before opening; bound both file and dir counts."""
    _private(payload.lstat(), directory=True)
    result, pending, entries = {}, [payload], 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as listing:
            for entry in listing:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise _invalid()
                info = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                name = path.relative_to(payload).as_posix()
                _relative(name)
                if stat.S_ISDIR(info.st_mode):
                    _private(info, directory=True)
                    pending.append(path)
                else:
                    _private(info)
                    with _file(path) as stream:
                        before = os.fstat(stream.fileno())
                        result[name] = hashlib.file_digest(stream, "sha256").hexdigest()
                        after = os.fstat(stream.fileno())
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise _invalid()
    return result


def _source(path, commit):
    count, files, total, names = 0, 0, 0, set()
    with _file(path) as stream:
        if os.fstat(stream.fileno()).st_size > MAX_SOURCE_ARCHIVE:
            raise _invalid()
    with _file(path) as stream, tarfile.open(fileobj=stream, mode="r:") as archive:
        if archive.pax_headers.get("comment") != commit:
            raise _invalid()
        for member in archive:
            name = _relative(member.name)
            count += 1
            total += member.size
            if (
                count > MAX_ENTRIES
                or total > MAX_SOURCE_BYTES
                or member.size < 0
                or name in names
                or member.issparse()
                or not (member.isdir() or member.isfile())
            ):
                raise _invalid()
            names.add(name)
            files += int(member.isfile())
        if not files:
            raise _invalid()
    return files


def _sqlite(payload, manifest):
    from autoontology.security.enrollment import database_enrollment_state
    from autoontology.security.owner_adoption import _audit
    from autoontology.security.owner_registry import check_snapshot, decode_snapshot

    database = payload / "workspace/autoontology.db"
    deadline = time.monotonic() + 30
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if (
            conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or conn.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise _invalid()
        state = database_enrollment_state(conn)
        marker = None
        if manifest["host_pin_present"]:
            with _file(payload / "host-owner.json") as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise _invalid()
            marker = decode_snapshot(raw, manifest["host_database"])
            check_snapshot(conn, marker)
        elif (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_events'"
            ).fetchone()
            and conn.execute(
                "SELECT 1 FROM identity_events WHERE action='identity.owner_pinned' LIMIT 1"
            ).fetchone()
        ):
            raise _invalid()
        if state is not None:
            _audit(conn)
        version = conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
        return version, {
            "configured": state is not None,
            "workspace_id": state["workspace_id"] if state else None,
            "organization_id": state["organization_id"] if state else None,
            "host_pin_present": marker is not None,
            "pin_phase": marker["phase"] if marker else None,
            "recovery_phase": marker.get("recovery", {}).get("phase") if marker else None,
        }


def verify_bundle(checkpoint: Path) -> dict:
    """Local-owner diagnostics only. Return a non-secret, non-activating report."""
    if _IDENTITY.get() is not None:
        raise AccessError(
            "Application credentials cannot authorize host backup inspection.",
            "local_owner_required",
            403,
        )
    try:
        root = Path(checkpoint).absolute()
        _private(root.lstat(), directory=True)
        # Canonicalization is limited to the supplied checkpoint, never the old home.
        root = root.resolve(strict=True)
        manifest, stamp = _manifest(root)
        payload = root / "payload"
        inventory = _inventory(payload)
        if inventory != manifest["sha256"]:
            raise _invalid()
        for name in ("exports", "postgres/base", "postgres/pg_wal"):
            _private((payload / name).lstat(), directory=True)
        with _file(payload / "postgres/PG_VERSION") as stream:
            if stream.read(32) != b"17\n":
                raise _invalid()
        source_files = _source(payload / "source.tar", manifest["commit"])
        version, identity = _sqlite(payload, manifest)
        # Detect changes between byte hashing and format/identity inspection.
        # This is a frozen-input check, not a lock against the trusted OS owner.
        final_manifest, final_stamp = _manifest(root)
        if final_stamp != stamp or final_manifest != manifest or _inventory(payload) != inventory:
            raise _invalid()
        # No scripts, original settings or operational connections were used.
        return {
            "verified": True,
            "integrity_only": True,
            "activation_supported": False,
            "runtime_started": False,
            "runtime_configuration_safe": False,
            "source_provenance_checked": False,
            "host_authority_checked": False,
            "checkpoint_format": manifest["version"],
            "manifest_sha256": stamp,
            "declared_source_commit": manifest["commit"],
            "source_archive_sha256": inventory["source.tar"],
            "source_files": source_files,
            "payload_files": len(inventory),
            "postgres_major": 17,
            "postgres_started": False,
            "postgres_logical_contents_checked": False,
            "sqlite_schema_version": version,
            "identity": identity,
        }
    except AccessError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        sqlite3.DatabaseError,
        tarfile.TarError,
    ):
        raise _invalid() from None
