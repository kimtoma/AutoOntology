# SPDX-License-Identifier: MIT
"""A purpose-limited local model-router credential, separate from human sessions."""

import os
import re
import secrets
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from autoontology.security.credential_file import _parent
from autoontology.security.credentials import Secret
from autoontology.security.policy import AccessError


def identity_enabled(home):
    """Probe without creating a store or migrating during a CLI dry-run."""
    path = Path(home).absolute() / "autoontology.db"
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_state'"
        ).fetchone():
            principal_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_principals'"
            ).fetchone()
            version_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            version = (
                conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
                if version_table
                else None
            )
            if principal_table or version is None or version >= 12:
                raise AccessError(
                    "Identity configuration is missing; local-owner recovery is required.",
                    "identity_configuration_missing",
                    503,
                )
            return False
        if conn.execute("SELECT 1 FROM identity_state WHERE id=1").fetchone():
            return True
        if conn.execute("SELECT 1 FROM identity_principals LIMIT 1").fetchone():
            raise AccessError(
                "Identity configuration is missing; local-owner recovery is required.",
                "identity_configuration_missing",
                503,
            )
    return False


def router_secret(home):
    """Create/read atomically under the trusted OS account; never grant user actions."""
    directory = Path(home).absolute() / "security"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "router-secret"
    with _parent(path) as (parent, name):
        if not path.exists():
            temporary = ".router-" + secrets.token_hex(16)
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                with os.fdopen(fd, "wb") as file:
                    os.fchmod(file.fileno(), 0o600)
                    file.write(("ir_" + secrets.token_urlsafe(32)).encode())
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    os.link(
                        temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                except FileExistsError:
                    pass
                os.fsync(parent)
            finally:
                os.unlink(temporary, dir_fd=parent)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise AccessError(
                    "The local router credential is not owner-only.",
                    "router_credential_invalid",
                    503,
                )
            payload = file.read(100)
    if not re.fullmatch(rb"ir_[A-Za-z0-9_-]{43}", payload):
        raise AccessError(
            "The local router credential is invalid.", "router_credential_invalid", 503
        )
    return Secret(payload.decode())


def router_headers(settings):
    return (
        {"Authorization": "Bearer " + router_secret(settings.home).reveal()}
        if identity_enabled(settings.home)
        else {}
    )
