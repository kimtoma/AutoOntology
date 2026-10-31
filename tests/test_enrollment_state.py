# SPDX-License-Identifier: MIT
"""All identity boundaries distinguish metadata loss from first enrollment."""

import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology.cli import app as cli
from autoontology.config import Settings
from autoontology.security.context import authorize
from autoontology.security.internal import identity_enabled
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.web.app import create_app


def lose_metadata(store, loss):
    # Disposable corruption/partial-restore simulation, never an application API.
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        if loss == "missing_table":
            conn.execute("DROP TABLE identity_domains")
            return
        tables = ["identity_state", "identity_principals"]
        if loss == "principals_only":
            tables = ["identity_principals"]
        elif loss == "domains_too":
            tables.append("identity_domains")
        elif loss == "audit_only":
            tables += [
                "identity_domains",
                "identity_grants",
                "identity_credentials",
                "identity_delegations",
                "identity_source_domains",
                "identity_login_limits",
            ]
        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')


@pytest.fixture
def enrolled(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    identity = Identity(store)
    identity.bootstrap("Original", "owner", "Owner", "synthetic enrollment owner password")
    return settings, store, identity


@pytest.mark.parametrize(
    "loss",
    ["state_and_principals", "domains_too", "principals_only", "missing_table", "audit_only"],
)
@pytest.mark.parametrize("surface", ["probe", "status", "effect", "bootstrap"])
def test_corrupt_enrollment_never_grants_legacy_authority(enrolled, loss, surface):
    settings, store, identity = enrolled
    lose_metadata(store, loss)
    actions = {
        "probe": lambda: identity_enabled(settings.home),
        "status": identity.status,
        "effect": lambda: authorize(store, "settings.manage"),
        "bootstrap": lambda: identity.bootstrap(
            "Replacement", "replacement", "Replacement", "synthetic replacement owner password"
        ),
    }
    with pytest.raises(AccessError) as error:
        actions[surface]()
    assert error.value.code == "identity_configuration_missing" and error.value.status == 503


@pytest.mark.parametrize("loss", ["state_and_principals", "domains_too"])
def test_http_and_native_cli_fail_closed_on_partial_identity_restore(enrolled, loss):
    settings, store, _identity = enrolled
    client = TestClient(create_app(settings))
    lose_metadata(store, loss)
    for path in ("/api/identity/status", "/api/sources"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error"] == "identity_configuration_missing"
    for arguments in (["auth", "status"], ["source", "list"]):
        result = CliRunner().invoke(cli, arguments)
        assert result.exit_code == 1 and "identity_configuration_missing" in result.output


def test_unconfigured_store_with_stray_login_metadata_requires_recovery(isolated_env):
    store = Store.for_home(isolated_env / "home")
    with store._immediate() as conn:
        conn.execute("INSERT INTO identity_login_limits VALUES('synthetic-peer',1,0,1)")
    with pytest.raises(AccessError) as error:
        identity_enabled(store.path.parent)
    assert error.value.code == "identity_configuration_missing"


@pytest.mark.parametrize("column,value", [("workspace_id", "invalid"), ("policy_version", 0)])
def test_invalid_workspace_metadata_is_a_recovery_state(enrolled, column, value):
    settings, store, identity = enrolled
    with sqlite3.connect(store.path) as conn:
        conn.execute(f'UPDATE identity_state SET "{column}"=? WHERE id=1', (value,))
    for action in (identity.status, lambda: identity_enabled(settings.home)):
        with pytest.raises(AccessError) as error:
            action()
        assert error.value.code == "identity_configuration_missing"


def test_incomplete_state_schema_is_not_a_valid_configured_workspace(enrolled):
    settings, store, identity = enrolled
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TABLE identity_state")
        conn.execute("CREATE TABLE identity_state(id INTEGER, workspace_id TEXT)")
        conn.execute("INSERT INTO identity_state VALUES(1,?)", ("0" * 32,))
    for action in (identity.status, lambda: identity_enabled(settings.home)):
        with pytest.raises(AccessError) as error:
            action()
        assert error.value.code == "identity_configuration_missing"


def test_denied_reenrollment_and_reads_preserve_surviving_metadata(enrolled):
    settings, store, identity = enrolled
    lose_metadata(store, "domains_too")

    def fingerprint():
        with sqlite3.connect(store.path) as conn:
            return hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()

    before = fingerprint()
    for action in (
        identity.status,
        lambda: identity_enabled(settings.home),
        lambda: authorize(store, "settings.manage"),
        lambda: identity.bootstrap(
            "Replacement", "replacement", "Replacement", "synthetic replacement owner password"
        ),
    ):
        with pytest.raises(AccessError):
            action()
    assert fingerprint() == before
