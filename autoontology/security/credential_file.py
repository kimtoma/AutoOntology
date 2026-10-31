# SPDX-License-Identifier: MIT
"""Owner-only CLI credentials bound to one workspace and service origin.

These helpers never print secrets or send requests. Adapters must load against the
actual destination before adding a bearer header, and must not follow redirects.
The operating-system account remains trusted; this is not protection from its owner.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from autoontology.security.credentials import IssuedCredential, Secret
from autoontology.security.policy import AccessError

MAX_BYTES = 8192
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
TOKEN = re.compile(r"ao_[A-Za-z0-9_-]{43}\Z")


def service_origin(value: str) -> str:
    """Canonical HTTPS or literal loopback HTTP origin, without URL credentials."""
    try:
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError
        url = urlsplit(value)
        host = url.hostname
        port = url.port
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or (url.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError
        if host != "::1":
            host = host.encode("idna").decode("ascii").lower()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError
        display = f"[{host}]" if ":" in host else host
        suffix = (
            f":{port}"
            if port is not None and port != (443 if url.scheme == "https" else 80)
            else ""
        )
        return f"{url.scheme}://{display}{suffix}"
    except (ValueError, UnicodeError):
        raise AccessError(
            "Use an HTTPS or loopback HTTP service origin without a path or URL credentials.",
            "invalid_origin",
            400,
        ) from None


def _error():
    return AccessError(
        "Use an owner-only credential file in an owner-only directory; sign in again if it is missing or invalid.",
        "credential_file_invalid",
        401,
    )


@contextmanager
def _parent(path):
    path = Path(path).expanduser().absolute()
    if path.name in {"", ".", ".."}:
        raise _error()
    fd = None
    try:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise _error()
        yield fd, path.name
    except OSError:
        raise _error() from None
    finally:
        if fd is not None:
            os.close(fd)


@dataclass(frozen=True)
class CredentialFile:
    workspace_id: str
    origin: str
    principal_id: str
    credential_id: str
    expires_at: float
    secret: Secret

    def public(self):
        return {
            "workspace_id": self.workspace_id,
            "origin": self.origin,
            "principal_id": self.principal_id,
            "credential_id": self.credential_id,
            "expires_at": self.expires_at,
        }


def _decode(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise _error()
    for key in ("workspace_id", "principal_id", "credential_id"):
        if not isinstance(data.get(key), str) or not IDENTIFIER.fullmatch(data[key]):
            raise _error()
    expiry = data.get("expires_at")
    if (
        isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not 0 < expiry <= 253402300799
        or not math.isfinite(expiry)
    ):
        raise _error()
    if not isinstance(data.get("token"), str) or not TOKEN.fullmatch(data["token"]):
        raise _error()
    origin = service_origin(data.get("origin"))
    return CredentialFile(
        data["workspace_id"],
        origin,
        data["principal_id"],
        data["credential_id"],
        expiry,
        Secret(data["token"]),
    )


def save_credential(path, issued: IssuedCredential, *, workspace_id, origin):
    """Install exclusively as 0600; never truncate or overwrite an existing file."""
    data = {
        "version": 1,
        "workspace_id": workspace_id,
        "origin": service_origin(origin),
        "principal_id": issued.principal["id"],
        "credential_id": issued.credential_id,
        "expires_at": issued.expires_at,
        "token": issued.secret.reveal(),
    }
    result = _decode(data)
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise _error()
    with _parent(path) as (directory, name):
        temporary = ".credential-" + secrets.token_hex(16)
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(fd, "wb") as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.link(
                temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
            )
            os.fsync(directory)
        except FileExistsError:
            raise AccessError(
                "The credential file already exists; sign out or choose a new file.",
                "credential_file_exists",
                409,
            ) from None
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    return result


def load_credential(path, *, workspace_id, origin, clock=time.time):
    """Reject unsafe files, expired sessions and destination/workspace mismatches."""
    with _parent(path) as (directory, name):
        fd = None
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory
            )
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > MAX_BYTES
            ):
                raise _error()
            with os.fdopen(fd, "rb") as file:
                fd = None
                payload = file.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise _error()
            result = _decode(json.loads(payload))
        except (OSError, ValueError, UnicodeError):
            raise _error() from None
        finally:
            if fd is not None:
                os.close(fd)
    if result.workspace_id != workspace_id or result.origin != service_origin(origin):
        raise AccessError(
            "This credential belongs to another workspace or service origin. Sign in to the selected destination.",
            "credential_destination_mismatch",
            401,
        )
    if result.expires_at <= clock():
        raise AccessError("This credential has expired. Sign in again.", "credential_expired", 401)
    return result
