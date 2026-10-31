# SPDX-License-Identifier: MIT
"""Credentials stay private and are never reusable against an unrelated service."""

import json
import os
import secrets

import pytest

from autoontology.security.credential_file import load_credential, save_credential, service_origin
from autoontology.security.credentials import IssuedCredential, Secret
from autoontology.security.policy import AccessError


@pytest.fixture
def credential(tmp_path):
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    issued = IssuedCredential(
        {"id": "a" * 32}, "b" * 32, 2000000000, Secret("ao_" + secrets.token_urlsafe(32))
    )
    options = {"workspace_id": "c" * 32, "origin": "http://127.0.0.1:7830"}
    path = directory / "session.json"
    save_credential(path, issued, **options)
    return path, issued, options


def test_round_trip_is_private_bound_and_secret_safe(credential):
    path, issued, options = credential
    result = load_credential(path, **options)
    assert result.secret.reveal() == issued.secret.reveal()
    assert issued.secret.reveal() not in repr(result)
    assert issued.secret.reveal() not in json.dumps(result.public())
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_existing_file_and_symlink_are_never_overwritten(credential, tmp_path):
    path, issued, options = credential
    before = path.read_bytes()
    with pytest.raises(AccessError) as error:
        save_credential(path, issued, **options)
    assert error.value.code == "credential_file_exists" and path.read_bytes() == before
    link = path.parent / "link.json"
    link.symlink_to(path)
    with pytest.raises(AccessError):
        save_credential(link, issued, **options)
    with pytest.raises(AccessError):
        load_credential(link, **options)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "d" * 32},
        {"origin": "http://127.0.0.1:7831"},
        {"origin": "https://elsewhere.example"},
    ],
)
def test_wrong_destination_is_rejected_before_secret_is_returned(credential, change):
    path, _, options = credential
    with pytest.raises(AccessError) as error:
        load_credential(path, **(options | change))
    assert error.value.code == "credential_destination_mismatch"


def test_expired_file_requires_login(credential):
    path, issued, options = credential
    with pytest.raises(AccessError) as error:
        load_credential(path, clock=lambda: issued.expires_at, **options)
    assert error.value.code == "credential_expired"


@pytest.mark.parametrize("target", ["file", "parent"])
def test_group_or_world_readable_paths_are_rejected(credential, target):
    path, _, options = credential
    (path if target == "file" else path.parent).chmod(0o644 if target == "file" else 0o755)
    with pytest.raises(AccessError):
        load_credential(path, **options)


def test_symlink_directory_and_fifo_are_rejected_without_blocking(credential):
    path, issued, options = credential
    link = path.parent.parent / "alias"
    link.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(AccessError):
        load_credential(link / path.name, **options)
    with pytest.raises(AccessError):
        save_credential(link / "new.json", issued, **options)
    pipe = path.parent / "fifo"
    os.mkfifo(pipe, mode=0o600)
    with pytest.raises(AccessError):
        load_credential(pipe, **options)


@pytest.mark.parametrize("payload", [b"null", b"{}", b"[1]", b"not JSON", b"x" * 8193, b"\xff"])
def test_malformed_or_oversized_file_fails_safely(credential, payload):
    path, _, options = credential
    path.write_bytes(payload)
    with pytest.raises(AccessError):
        load_credential(path, **options)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com#fragment",
        "https://example.com?",
        "https://example.com#",
        "https://example.com\\@evil.example",
        "http://localhost.evil.example",
        "file:///private/path",
        "https://example.com:invalid",
        "https://example.com\n",
        None,
    ],
)
def test_unsafe_service_origins_are_rejected(origin):
    with pytest.raises(AccessError):
        service_origin(origin)


@pytest.mark.parametrize(
    "origin, expected",
    [
        ("https://EXAMPLE.com:443/", "https://example.com"),
        ("http://localhost:80", "http://localhost"),
        ("http://[::1]:7830/", "http://[::1]:7830"),
        ("http://127.0.0.1:7830", "http://127.0.0.1:7830"),
    ],
)
def test_canonical_service_origins(origin, expected):
    assert service_origin(origin) == expected


@pytest.mark.parametrize(
    "value", [None, True, -1, 10**1000, float("inf"), float("nan"), "2000000000"]
)
def test_malformed_expiry_is_rejected_before_use(credential, value):
    path, _, options = credential
    data = json.loads(path.read_text())
    data["expires_at"] = value
    path.write_text(json.dumps(data))
    with pytest.raises(AccessError):
        load_credential(path, **options)
