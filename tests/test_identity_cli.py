# SPDX-License-Identifier: MIT
"""Native authentication and permission failures do not fall back to local authority."""

import json
import os

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.security.cli import GROUPS, ROOT
from autoontology.security.credential_file import load_credential, save_credential
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.store.operations import Operations
from tests.test_operations import example, snapshot


@pytest.fixture
def managed(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    ops = Operations(store)
    revision = snapshot(store, example())
    service = Identity(store)
    issued = service.bootstrap("CLI", "owner", "Owner", "CLI owner test password")
    private = settings.home / "private-passwords"
    private.mkdir(mode=0o700)
    password = private / "owner.txt"
    password.write_text("CLI owner test password\n")
    password.chmod(0o600)
    return settings, service, issued, ops, revision, password


def invoke(*args):
    return CliRunner().invoke(app, list(args))


def test_every_existing_business_cli_command_has_a_permission_rule():
    root = get_command(app)
    for name, command in root.commands.items():
        if name in {"auth", "version"}:
            continue
        if hasattr(command, "commands"):
            assert name in GROUPS and set(command.commands) == set(GROUPS[name])
        else:
            assert name in ROOT


def test_cli_login_whoami_and_logout_never_print_a_secret(managed):
    settings, service, issued, _, _, password = managed
    result = invoke("auth", "login", "owner", "--password-file", str(password))
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    saved = load_credential(
        data["credential_file"],
        workspace_id=service.status()["workspace_id"],
        origin=settings.service_url,
    )
    assert (
        saved.secret.reveal() not in result.stdout
        and "CLI owner test password" not in result.stdout
    )
    me = invoke("auth", "whoami")
    assert me.exit_code == 0 and json.loads(me.stdout)["principal"]["id"] == issued.principal["id"]
    assert invoke("auth", "login", "owner", "--password-file", str(password)).exit_code == 1
    assert invoke("auth", "logout").exit_code == 0
    with pytest.raises(AccessError):
        service.authenticate(saved.secret.reveal())
    assert not os.path.exists(data["credential_file"])


def test_missing_credential_cannot_use_native_ops_or_source_mutation(managed):
    _, _, _, ops, _, _ = managed
    for args in [("ops", "list"), ("source", "rm", "unknown"), ("build",)]:
        response = invoke(*args)
        assert response.exit_code == 1
        assert json.loads(response.stdout)["error"] == "credential_file_invalid"
    assert ops.state()["published_revision"] is None


def test_viewer_cannot_change_or_publish_through_native_commands(managed):
    settings, service, issued, _, revision, _ = managed
    owner = service.authenticate(issued.secret.reveal())
    service.create_human(owner, "reader", "Reader", "CLI reader test password", ["viewer"])
    token = service.login("reader", "CLI reader test password")
    directory = settings.home / "reader"
    directory.mkdir(mode=0o700)
    path = directory / "session.json"
    save_credential(
        path, token, workspace_id=service.status()["workspace_id"], origin=settings.service_url
    )
    for args in [
        ("ops", "check", "Question", "--expected", "Answer"),
        ("ops", "publish", "--revision", str(revision), "--token", "0" * 64),
        ("source", "rm", "unknown"),
        ("auth", "people"),
    ]:
        result = invoke("--credential-file", str(path), *args)
        assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "forbidden", (
            args,
            result.stdout,
        )


def test_native_review_records_verified_principal_and_revocation_applies(managed):
    _, service, issued, ops, revision, password = managed
    assert invoke("auth", "login", "owner", "--password-file", str(password)).exit_code == 0
    item = ops.changes()[0]
    result = invoke(
        "ops", "decide", item["id"], "--revision", str(revision), "--decision", "accept"
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["actor"] == issued.principal["id"]
    me = json.loads(invoke("auth", "whoami").stdout)
    service.revoke_credential(service.authenticate(issued.secret.reveal()), me["credential_id"])
    result = invoke("ops", "list")
    assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "credential_invalid"


def test_expired_session_can_be_removed_without_reactivating_it(managed):
    settings, service, _, _, _, _ = managed
    token = service.login("owner", "CLI owner test password", ttl=1)
    directory = settings.home / "security"
    directory.mkdir(mode=0o700)
    path = directory / "cli-session.json"
    save_credential(
        path, token, workspace_id=service.status()["workspace_id"], origin=settings.service_url
    )
    data = json.loads(path.read_text())
    data["expires_at"] = 1
    path.write_text(json.dumps(data))
    result = invoke("ops", "list")
    assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "credential_expired"
    result = invoke("auth", "logout")
    assert result.exit_code == 0 and not path.exists()


def test_explicit_credential_against_legacy_workspace_never_falls_back(
    managed, monkeypatch, tmp_path
):
    _, _, _, _, _, password = managed
    login = invoke("auth", "login", "owner", "--password-file", str(password))
    path = json.loads(login.stdout)["credential_file"]
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(tmp_path / "other"))
    result = invoke("--credential-file", path, "ops", "list")
    assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "identity_unconfigured"
    assert not (tmp_path / "other/autoontology.db").exists()


def test_native_admin_lists_resets_and_revokes_sessions(managed):
    _, service, issued, _, _, password = managed
    assert invoke("auth", "login", "owner", "--password-file", str(password)).exit_code == 0
    owner = service.authenticate(issued.secret.reveal())
    person = service.create_human(
        owner, "reset_reader", "Reset reader", "CLI reader old password", ["viewer"]
    )
    old = service.login("reset_reader", "CLI reader old password")
    packet = invoke("auth", "administration")
    assert packet.exit_code == 0 and json.loads(packet.stdout)["roles"]["viewer"]
    assert invoke("auth", "domains").exit_code == 0
    sessions = invoke("auth", "sessions", "--principal", person["id"])
    assert (
        sessions.exit_code == 0
        and json.loads(sessions.stdout)["items"][0]["id"] == old.credential_id
    )
    reset = invoke("auth", "reset-password", person["id"], "--password-file", str(password))
    assert reset.exit_code == 0 and json.loads(reset.stdout)["sessions_invalidated"]
    with pytest.raises(AccessError):
        service.authenticate(old.secret.reveal())
    revoke = invoke("auth", "revoke-session", old.credential_id)
    assert revoke.exit_code == 0 and json.loads(revoke.stdout)["revoked"] == old.credential_id
