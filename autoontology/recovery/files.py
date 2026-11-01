# SPDX-License-Identifier: MIT
"""Private resumable copies inside a host-reserved inactive destination."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from autoontology.recovery.checkpoint import _file, _private
from autoontology.security.policy import AccessError


def conflict():
    return AccessError(
        "Preserve this destination; its existing files do not match the reserved restoration.",
        "restore_destination_changed",
        409,
    )


def mkdir(path):
    path = Path(path)
    try:
        path.mkdir(mode=0o700)
        sync_dir(path.parent)
    except FileExistsError:
        _private(path.lstat(), directory=True)
    except FileNotFoundError:
        mkdir(path.parent)
        mkdir(path)


def stamp(path):
    with _file(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _temporary(destination, operation):
    suffix = hashlib.sha256(str(destination).encode()).hexdigest()[:24]
    return destination.with_name(".ao-copy-" + operation + "-" + suffix)


def install(destination, expected, operation, writer):
    """Resume only this operation's deterministic temporary file, never user data."""
    destination = Path(destination)
    mkdir(destination.parent)
    temporary = _temporary(destination, operation)
    if destination.exists() or destination.is_symlink():
        if stamp(destination) != expected:
            raise conflict()
        # A finished destination takes precedence, but do not bless a leftover
        # unknown temporary entry. This writer removes only its known temp file.
        if temporary.exists() or temporary.is_symlink():
            _private(temporary.lstat())
            temporary.unlink()
        return
    if temporary.exists() or temporary.is_symlink():
        _private(temporary.lstat())
        temporary.unlink()
    writer(temporary)
    if stamp(temporary) != expected:
        raise conflict()
    with _file(temporary) as stream:
        os.fsync(stream.fileno())
    # The host destination lock excludes all supported writers. Normal runtime
    # construction is fenced before the first copied DB appears. The OS owner
    # remains trusted; do not concurrently edit this reserved directory by hand.
    if destination.exists() or destination.is_symlink():
        raise conflict()
    os.rename(temporary, destination)
    sync_dir(destination.parent)


def copy_file(source, destination, expected, operation, mode):
    def writer(temporary):
        if mode == "clone":
            if sys.platform != "darwin":
                raise AccessError(
                    "APFS clone is required; choose explicit copy only if space permits.",
                    "restore_clone_unavailable",
                    409,
                )
            result = subprocess.run(
                ["/bin/cp", "-c", str(source), str(temporary)], capture_output=True
            )
            if result.returncode:
                raise AccessError(
                    "The private clone could not finish; preserve the reservation and resume.",
                    "restore_copy_failed",
                    503,
                )
            shutil.copystat(source, temporary)
        else:
            # copy2 applies the source mode only after copying. Reserve 0600
            # first so an abrupt death during the write stays resumable/private.
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            os.close(fd)
            shutil.copy2(source, temporary)

    install(destination, expected, operation, writer)


def write_file(path, data, operation):
    def writer(temporary):
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    install(path, hashlib.sha256(data).hexdigest(), operation, writer)


def copy_tree(source, destination, inventory, operation, mode, *, omit=()):
    mkdir(destination)
    for path in sorted(source.rglob("*")):
        name = path.relative_to(source).as_posix()
        if any(name == item or name.startswith(item + "/") for item in omit):
            continue
        info = path.lstat()
        if path.is_dir() and not path.is_symlink():
            _private(info, directory=True)
            mkdir(destination / name)
        else:
            _private(info)
            if name not in inventory:
                raise conflict()
            copy_file(path, destination / name, inventory[name], operation, mode)
