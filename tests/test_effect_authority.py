# SPDX-License-Identifier: MIT
"""Slow preparation cannot extend authority over a later source/settings effect."""

import json

import pytest

from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def effects(tmp_path):
    store = Store.for_home(tmp_path / "home")
    source = store.add_source(name="contracts", kind="files", locator="{}")
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long effect owner password")
    owner = identity.authenticate(issued.secret.reveal())
    from autoontology.security.host_files import HostFiles

    with acting_as(store, owner):
        HostFiles(store).allow(tmp_path, reason="Explicit effect-test input root")
    return store, identity, owner, source


@pytest.mark.parametrize("mutation", ["add", "locator", "catalog", "delete"])
def test_source_effects_require_a_live_bound_actor(effects, mutation):
    store, _, _, source = effects
    with pytest.raises(AccessError) as error:
        if mutation == "add":
            store.add_source(name="new", kind="documents", locator="{}")
        elif mutation == "locator":
            store.set_locator(source.id, "changed")
        elif mutation == "catalog":
            store.set_catalog_database_id(source.id, "changed")
        else:
            store.delete_source(source.id)
    assert error.value.code == "authentication_required"
    assert store.get_source(source.name).locator == "{}"


def test_source_effects_audit_safe_metadata_and_keep_agent_preparation_separate(effects):
    store, identity, owner, source = effects
    secret = "postgres://name:PRIVATE_PASSWORD@host/target"
    with acting_as(store, owner):
        store.set_locator(source.id, secret)
        created = store.add_source(name="new", kind="documents", locator=secret)
        store.delete_source(created.id)
    issued = identity.delegate(owner, "builder", "Builder", ["candidate.prepare"], ttl=300)
    agent = identity.authenticate(issued.secret.reveal())
    with acting_as(store, agent):
        store.set_catalog_database_id(source.id, "catalog")
        with pytest.raises(AccessError):
            store.set_locator(source.id, "agent replacement")
    audit = identity.audit(owner)
    assert "PRIVATE_PASSWORD" not in json.dumps(audit)
    assert store.get_source(source.name).catalog_database_id == "catalog"
    assert identity.verify_audit(owner)["ok"]


def test_revocation_during_source_validation_prevents_registration(effects, tmp_path, monkeypatch):
    from autoontology.structured import pipeline

    store, identity, owner, _ = effects
    file = tmp_path / "source.csv"
    file.write_text("id\n1\n")
    discover = pipeline.discover_files

    def revoke(*args, **kwargs):
        found = discover(*args, **kwargs)
        identity.revoke_credential(owner, owner.credential_id)
        return found

    monkeypatch.setattr(pipeline, "discover_files", revoke)
    with acting_as(store, owner), pytest.raises(AccessError):
        pipeline.add_source(store, str(file), name="late")
    assert store.get_source("late") is None


def test_upload_revocation_cleans_private_staging_and_keeps_the_authority_error(
    effects, isolated_env, monkeypatch
):
    import io
    from dataclasses import replace

    import anyio
    from starlette.datastructures import FormData, UploadFile

    from autoontology.config import Settings
    from autoontology.web.api import _add_upload_claimed
    from autoontology.web.app import create_app

    store, identity, owner, _ = effects
    api = create_app(replace(Settings.from_env(), home=store.path.parent)).state.api
    upload = UploadFile(file=io.BytesIO(b"id\n1\n"), filename="sample.csv")

    async def revoked_read(*args):
        identity.revoke_credential(owner, owner.credential_id)
        return b"id\n1\n"

    monkeypatch.setattr(upload, "read", revoked_read)

    async def work():
        with acting_as(store, owner), pytest.raises(AccessError) as error:
            await _add_upload_claimed(api, FormData([("files", upload)]), "upload_late", "files")
        assert error.value.code == "credential_invalid"

    anyio.run(work)
    assert (
        store.get_source("upload_late") is None and not (api.uploads_dir / "upload_late").exists()
    )


def test_policy_drift_during_source_validation_refuses_the_old_request(
    effects, tmp_path, monkeypatch
):
    from autoontology.structured import pipeline

    store, identity, owner, _ = effects
    file = tmp_path / "source.csv"
    file.write_text("id\n1\n")
    discover = pipeline.discover_files

    def change(*args, **kwargs):
        found = discover(*args, **kwargs)
        identity.create_domain(owner, "new", "New")
        return found

    monkeypatch.setattr(pipeline, "discover_files", change)
    with acting_as(store, owner), pytest.raises(AccessError) as error:
        pipeline.add_source(store, str(file), name="late")
    assert error.value.code == "authority_changed" and store.get_source("late") is None


def test_revocation_during_backend_teardown_prevents_drop_and_local_removal(
    effects, isolated_env, monkeypatch
):
    from autoontology.config import Settings
    from autoontology.engine import client, pg
    from autoontology.structured.remove import remove_source

    store, identity, owner, _ = effects
    with acting_as(store, owner):
        source = store.add_source(
            name="owned", kind="files", locator="{}", pg_database="autoontology_src_owned"
        )
    changes = []

    class Backend:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def delete_connection(self, name):
            changes.append("prior_authorized_teardown")

        def database_id_by_name(self, name):
            identity.revoke_credential(owner, owner.credential_id)
            return None

    def forbidden_drop(*args):
        changes.append("drop")
        raise AssertionError("DROP reached after revocation")

    monkeypatch.setattr(client, "BackendClient", Backend)
    monkeypatch.setattr(pg, "connect", forbidden_drop)
    with acting_as(store, owner), pytest.raises(AccessError):
        remove_source(Settings.from_env(), store, source, drop_db=True, timeout=1)
    assert changes == ["prior_authorized_teardown"] and store.get_source("owned")


def test_source_change_and_audit_roll_back_together(effects, monkeypatch):
    from autoontology.security import context

    store, identity, owner, source = effects
    before = identity.audit(owner)

    def fail(*args, **kwargs):
        raise RuntimeError("controlled audit failure")

    monkeypatch.setattr(context, "audit_effect", fail)
    with acting_as(store, owner), pytest.raises(RuntimeError):
        store.set_locator(source.id, "new locator")
    assert store.get_source(source.name).locator == "{}" and identity.audit(owner) == before


def test_lost_modern_identity_table_cannot_fall_back_to_legacy_authority(effects):
    from autoontology.security.internal import identity_enabled

    store, _, _, _ = effects
    with store._conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE identity_state")
    with pytest.raises(AccessError) as error:
        store.add_source(name="not_legacy", kind="documents", locator="{}")
    assert error.value.code == "identity_configuration_missing"
    with pytest.raises(AccessError) as probe:
        identity_enabled(store.path.parent)
    assert probe.value.code == "identity_configuration_missing"
    assert store.get_source("not_legacy") is None


def test_native_provider_change_rechecks_the_same_prepared_policy(
    effects, isolated_env, monkeypatch
):
    from typer.testing import CliRunner

    from autoontology import onboarding
    from autoontology.cli import app
    from autoontology.config import Settings
    from autoontology.security.credential_file import save_credential

    store, identity, owner, _ = effects
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(store.path.parent))
    path = store.path.parent / "selection.env"
    path.write_text("AUTOONTOLOGY_CHAT_PROVIDER=ollama\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(path))
    folder = store.path.parent / "security"
    folder.mkdir(mode=0o700, exist_ok=True)
    issued = identity.login("owner", "long effect owner password")
    save_credential(
        folder / "cli.json",
        issued,
        workspace_id=identity.status()["workspace_id"],
        origin=Settings.from_env().service_url,
    )
    original = onboarding.EnvFile.set
    changed = []

    def drift(self, *args):
        original(self, *args)
        if not changed:
            identity.create_domain(owner, "new", "New")
            changed.append(True)

    monkeypatch.setattr(onboarding.EnvFile, "set", drift)
    monkeypatch.setattr(
        onboarding,
        "provider_info",
        lambda *args: onboarding.ProviderInfo("codex", True, True, "ready", ""),
    )
    result = CliRunner().invoke(
        app, ["--credential-file", str(folder / "cli.json"), "provider", "use", "codex"]
    )
    assert result.exit_code == 1 and "authority_changed" in result.stdout
    assert path.read_text() == "AUTOONTOLOGY_CHAT_PROVIDER=ollama\n"


def test_http_source_preparation_preserves_revocation_status(
    effects, isolated_env, tmp_path, monkeypatch
):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from autoontology.config import Settings
    from autoontology.structured import pipeline
    from autoontology.web.app import create_app

    store, identity, owner, _ = effects
    issued = identity.login("owner", "long effect owner password")
    settings = replace(Settings.from_env(), home=store.path.parent)
    credential_id = identity.authenticate(issued.secret.reveal()).credential_id
    file = tmp_path / "source.csv"
    file.write_text("id\n1\n")
    discover = pipeline.discover_files

    def drift(*args, **kwargs):
        found = discover(*args, **kwargs)
        identity.revoke_credential(owner, credential_id)
        return found

    monkeypatch.setattr(pipeline, "discover_files", drift)
    with TestClient(create_app(settings)) as client:
        result = client.post(
            "/api/sources",
            headers={"Authorization": "Bearer " + issued.secret.reveal()},
            json={"name": "late", "kind": "files", "locator": str(file)},
        )
        assert result.status_code == 401 and result.json()["error"] == "credential_invalid", (
            result.text
        )
    assert store.get_source("late") is None
