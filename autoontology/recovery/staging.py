# SPDX-License-Identifier: MIT
"""Host-reserved inactive restoration; activation and authority transfer are separate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from contextlib import ExitStack
from pathlib import Path

from autoontology.recovery import files, profile
from autoontology.recovery.checkpoint import _file, _inventory, _manifest, _private, verify_bundle
from autoontology.security import owner_registry as host
from autoontology.security.context import _IDENTITY
from autoontology.security.credential_file import _parent
from autoontology.security.fs_paths import canonical_path
from autoontology.security.policy import AccessError

current_codex_binary = profile.current_codex_binary
RECEIPT = "recovery/stage-receipt.json"


def _boundary(name):
    """Durable boundaries for controlled interruption and process-death tests."""


def _owner():
    if _IDENTITY.get() is not None:
        raise AccessError(
            "Use a separate local OS-owner restoration ceremony.", "local_owner_required", 403
        )


def _error():
    return AccessError(
        "Preserve this backup and reservation; source authority or restoration binding cannot be verified.",
        "restore_binding_invalid",
        409,
    )


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(data):
    return hashlib.sha256(_json(data)).hexdigest()


def _ports(value, original):
    if (
        not isinstance(value, dict)
        or set(value) != {"ui", "backend", "postgres"}
        or any(type(v) is not int or not 1024 <= v <= 65535 for v in value.values())
        or len(set(value.values())) != 3
    ):
        raise _error()
    old = {
        int(original[k])
        for k in ("AUTOONTOLOGY_PORT", "BACKEND_PORT", "POSTGRES_PORT")
        if original.get(k, "").isdigit()
    }
    if old.intersection(value.values()):
        raise _error()
    try:
        with ExitStack() as stack:
            for port in value.values():
                probe = stack.enter_context(socket.socket())
                probe.bind(("127.0.0.1", port))
    except OSError:
        raise AccessError(
            "A selected restore port is unavailable; no fallback port was selected.",
            "restore_port_unavailable",
            409,
        ) from None


def snapshot_hashes(destination):
    result = _inventory(destination)
    result.pop(RECEIPT, None)
    return result


def _authority(manifest, frozen):
    marker = host.read(manifest["host_database"])
    if (
        marker is None
        or marker.get("version") not in {1, 2}
        or marker["phase"] != "active"
        or marker.get("recovery", {}).get("phase", "active") != "active"
    ):
        raise _error()
    if any(
        marker.get(k) != frozen.get(k)
        for k in (
            "workspace_id",
            "organization_id",
            "operation_id",
            "kind",
            "principal_id",
            "event_sha256",
        )
    ):
        raise _error()
    return marker


def _request_target(directory, operation, database):
    """Keep one source-serialized request bound to its first destination.

    The independently durable destination reservation is the request index.
    No separate mutable SQLite index can lose this binding on rollback. Only
    v1/v2 active sources are stageable here; their owner lock serializes all
    supported staging requests for that source, including native path aliases.
    """
    for name in os.listdir(directory):
        if not re.fullmatch(r"[0-9a-f]{64}\.json", name):
            continue
        data = json.loads(host._payload(directory, name))
        if not isinstance(data, dict):
            raise _error()
        if (
            data.get("kind") == "restore"
            and data.get("operation_id") == operation
            and host.database_path(data["database"]) != host.database_path(database)
        ):
            raise _error()


def _existing(destination):
    if destination.exists() or destination.is_symlink():
        _private(destination.lstat(), directory=True)
        # Reject links/special/multiply-linked entries before path authority lookup.
        _inventory(destination)
    return host.read(destination / "workspace/autoontology.db")


def _result(marker, destination):
    if marker["kind"] != "restore":
        raise _error()
    result = {
        "operation_id": marker["operation_id"],
        "workspace_id": marker["workspace_id"],
        "phase": marker["phase"],
        "ports": marker["ports"],
        "integrity_verified": False,
        "activation_supported": False,
        "source_authority_retired": False,
        "runtime_started": False,
    }
    if marker["phase"] != "staged":
        return result
    with _file(destination / RECEIPT) as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != marker["receipt_sha256"]:
        raise _error()
    receipt = json.loads(raw)
    if (
        receipt.get("directories")
        != sorted(
            p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_dir()
        )
        or receipt.get("operation_id") != marker["operation_id"]
        or receipt.get("manifest_sha256") != marker["manifest_sha256"]
        or snapshot_hashes(destination) != receipt.get("sha256")
        or files.stamp(destination / "profile.json") != marker["profile_sha256"]
    ):
        raise _error()
    result["integrity_verified"] = True
    try:
        source = host.read(marker["source_database"])
        result["source_pin_current"] = (
            source is not None and _digest(source) == marker["source_pin_sha256"]
        )
    except AccessError:
        result["source_pin_current"] = False
    return result


def restore_status(destination):
    _owner()
    try:
        destination = Path(destination).absolute()
        _private(destination.lstat(), directory=True)
        marker = host.read(destination / "workspace/autoontology.db")
        if marker is None:
            raise _error()
        from autoontology.recovery.transfer_registry import read as read_transfer

        if marker.get("version") == 4 or read_transfer(marker["workspace_id"]) is not None:
            from autoontology.recovery.transfer import status

            return status(destination)
        marker = _existing(destination)
        return _result(marker, destination)
    except AccessError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise _error() from None


def stage_restore(
    checkpoint, destination, *, workspace_id, request_id, ports, copy_mode="clone", resume=False
):
    _owner()
    try:
        if (
            not isinstance(workspace_id, str)
            or not host.ID.fullmatch(workspace_id)
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id)
            or copy_mode not in {"clone", "copy"}
        ):
            raise _error()
        checkpoint, destination = Path(checkpoint).absolute(), Path(destination).absolute()
        report = verify_bundle(checkpoint)
        manifest, stamp = _manifest(checkpoint)
        if (
            not manifest["host_pin_present"]
            or report["identity"]["workspace_id"] != workspace_id
            or report["identity"]["pin_phase"] != "active"
            or report["identity"]["recovery_phase"] not in {None, "active"}
        ):
            raise _error()
        original = profile.read_env(checkpoint / "payload/local.env")
        _ports(ports, original)
        with _file(checkpoint / "payload/host-owner.json") as stream:
            frozen = host.decode_snapshot(stream.read(16385), manifest["host_database"])
        source = _authority(manifest, frozen)
        with _parent(destination):
            # Resolve only the new host-selected path, not archived executable paths.
            destination = canonical_path(destination)
        if (
            destination == checkpoint
            or destination.is_relative_to(checkpoint)
            or checkpoint.is_relative_to(destination)
            or Path(source["database"]).is_relative_to(destination)
            or any(
                destination.is_relative_to(canonical_path(p))
                for p in (
                    Path(source["database"]).parent,
                    *(
                        Path(source["database"]).parent.parent / name
                        for name in ("exports", "postgres", "app")
                    ),
                )
            )
        ):
            raise _error()
        previous = _existing(destination)
        if destination.exists() and previous is None:
            raise files.conflict()
        database = destination / "workspace/autoontology.db"
        operation = _digest(
            {
                "purpose": "inactive-restore-v1",
                "workspace_id": workspace_id,
                "request_id": request_id,
            }
        )[:32]
        reservation = {
            "version": 3,
            "kind": "restore",
            "role": "destination",
            "phase": "reserved",
            "database": str(database),
            "workspace_id": workspace_id,
            "organization_id": frozen["organization_id"],
            "operation_id": operation,
            "request_sha256": hashlib.sha256(request_id.encode()).hexdigest(),
            "manifest_sha256": stamp,
            "source_database": source["database"],
            "source_pin_sha256": _digest(source),
            "backup_pin_sha256": _digest(frozen),
            "ports": ports,
            "copy_mode": copy_mode,
            "os_uid": os.getuid(),
            "builder_sha256": profile.builder_sha256(),
            "profile_sha256": None,
            "receipt_sha256": None,
        }
        # Stable lock ordering; both active-source and reserved-destination ceremonies serialize.
        with ExitStack() as stack:
            locks = {
                p: stack.enter_context(host.locked(p))
                for p in sorted({source["database"], str(database)})
            }
            current = host._read(locks[source["database"]], source["database"])
            if current != source:
                raise _error()
            _request_target(locks[source["database"]], operation, database)
            existing = host._read(locks[str(database)], database)
            if existing is not None:
                if any(
                    existing.get(k) != v
                    for k, v in reservation.items()
                    if k not in {"phase", "profile_sha256", "receipt_sha256"}
                ):
                    raise _error()
                if existing["phase"] == "staged":
                    return _result(existing, destination)
                if not resume:
                    raise _error()
            else:
                if resume or destination.exists():
                    raise files.conflict()
                host.write(locks[str(database)], database, reservation)
            _boundary("after_reserve")
            files.mkdir(destination)
            inventory = manifest["sha256"]
            files.copy_tree(
                checkpoint / "payload",
                destination / "recovery/input",
                inventory,
                operation,
                copy_mode,
            )
            _boundary("after_input")
            expected = {"recovery/input/" + k: v for k, v in inventory.items()}
            for section in ("workspace", "exports", "postgres"):
                subtree = {
                    k.removeprefix(section + "/"): v
                    for k, v in inventory.items()
                    if k.startswith(section + "/")
                }
                omit = (
                    profile.WORKSPACE_OMIT
                    if section == "workspace"
                    else profile.PG_CONFIGS
                    if section == "postgres"
                    else ()
                )
                files.copy_tree(
                    destination / "recovery/input" / section,
                    destination / section,
                    subtree,
                    operation,
                    copy_mode,
                    omit=omit,
                )
                for key, value in subtree.items():
                    if not any(key == item or key.startswith(item + "/") for item in omit):
                        expected[section + "/" + key] = value
            for section in ("workspace/run", "workspace/security"):
                files.mkdir(destination / section)
            _boundary("after_workspace")
            _, generated = profile.artifacts(destination, ports, original, current_codex_binary())
            for name, data in generated.items():
                files.write_file(destination / name, data, operation)
                expected[name] = hashlib.sha256(data).hexdigest()
            if snapshot_hashes(destination) != expected:
                raise files.conflict()
            if verify_bundle(checkpoint)["manifest_sha256"] != stamp:
                raise _error()
            receipt = _json(
                {
                    "version": 1,
                    "directories": sorted(
                        p.relative_to(destination).as_posix()
                        for p in destination.rglob("*")
                        if p.is_dir()
                    ),
                    "operation_id": operation,
                    "manifest_sha256": stamp,
                    "sha256": expected,
                }
            )
            if len(receipt) > 2 * 1024 * 1024:
                raise _error()
            files.write_file(destination / RECEIPT, receipt, operation)
            staged = {
                **reservation,
                "phase": "staged",
                "profile_sha256": expected["profile.json"],
                "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
            }
            _boundary("before_finalize")
            host.write(locks[str(database)], database, staged)
            _boundary("after_finalize")
            return _result(staged, destination)
    except AccessError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise _error() from None
