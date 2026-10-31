# SPDX-License-Identifier: MIT
"""Memory-hard password hashes and credentials whose default representation is safe."""

import hashlib
import hmac
import json
import re
import secrets
import threading
from dataclasses import dataclass

from autoontology.security.policy import AccessError

SCRYPT_N = 2**17
PASSWORD_SLOTS = threading.BoundedSemaphore(2)


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def reveal(self):
        return self._value

    def __repr__(self):
        return "Secret(<redacted>)"

    def __str__(self):
        return "<redacted>"


@dataclass(frozen=True)
class IssuedCredential:
    principal: dict
    credential_id: str
    expires_at: float
    secret: Secret

    def public(self):
        return {
            "principal": self.principal,
            "credential_id": self.credential_id,
            "expires_at": self.expires_at,
        }


def token_digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def derive(password, salt):
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise AccessError(
            "Login capacity is busy; retry shortly.", "login_busy", 429, retry_after=1
        )
    try:
        return hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=SCRYPT_N,
            r=8,
            p=1,
            maxmem=256 * 1024 * 1024,
            dklen=32,
        ).hex()
    finally:
        PASSWORD_SLOTS.release()


def hash_password(password):
    if not _valid_password_bytes(password) or len(password) < 15:
        raise AccessError(
            "Use a password of at least 15 characters and at most 1024 UTF-8 bytes.",
            "invalid_password",
            400,
        )
    salt = secrets.token_hex(16)
    return json.dumps(
        {
            "version": 1,
            "algorithm": "scrypt",
            "n": SCRYPT_N,
            "r": 8,
            "p": 1,
            "salt": salt,
            "hash": derive(password, salt),
        },
        sort_keys=True,
    )


def check_password(password, record):
    if not _valid_password_bytes(password):
        return False
    try:
        data = json.loads(record)
        if (data["version"], data["algorithm"], data["n"], data["r"], data["p"]) != (
            1,
            "scrypt",
            SCRYPT_N,
            8,
            1,
        ):
            return False
        if not isinstance(data["salt"], str) or not re.fullmatch(r"[0-9a-f]{32}", data["salt"]):
            return False
        if not isinstance(data["hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["hash"]):
            return False
        return hmac.compare_digest(derive(password, data["salt"]), data["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def _valid_password_bytes(password):
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        return len(password.encode("utf-8")) <= 1024
    except UnicodeEncodeError:
        return False
