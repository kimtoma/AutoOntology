# SPDX-License-Identifier: MIT
"""Shared endpoint and host-file boundaries must not silently reuse authority."""

import pytest

from autoontology.onboarding import EnvFile
from autoontology.security.policy import AccessError
from autoontology.security.settings import save_env
from autoontology.store.db import Store


def test_native_settings_boundary_refuses_saved_key_origin_change(isolated_env):
    path = isolated_env / ".env"
    path.write_text(
        "AUTOONTOLOGY_OPENAI_BASE_URL=https://old.example/v1\nAUTOONTOLOGY_OPENAI_API_KEY=synthetic-private-value\n"
    )
    env = EnvFile.load(path)
    env.set("AUTOONTOLOGY_OPENAI_BASE_URL", "https://other.example/v1")
    original = path.read_bytes()
    with pytest.raises(AccessError) as error:
        save_env(Store.for_home(isolated_env / "home"), env, path)
    assert error.value.code == "key_required" and path.read_bytes() == original
    assert "synthetic-private-value" not in str(error.value)


def test_same_origin_path_change_and_explicit_key_replacement_are_allowed(isolated_env):
    path = isolated_env / ".env"
    path.write_text(
        "AUTOONTOLOGY_OPENAI_BASE_URL=https://old.example/v1\nAUTOONTOLOGY_OPENAI_API_KEY=synthetic-private-value\n"
    )
    store = Store.for_home(isolated_env / "home")
    env = EnvFile.load(path)
    env.set("AUTOONTOLOGY_OPENAI_BASE_URL", "https://old.example:443/api")
    save_env(store, env, path)
    env = EnvFile.load(path)
    env.set("AUTOONTOLOGY_OPENAI_BASE_URL", "https://new.example/v1")
    env.set("AUTOONTOLOGY_OPENAI_API_KEY", "")
    save_env(store, env, path, key_replaced=True)
    assert EnvFile.load(path).get("AUTOONTOLOGY_OPENAI_API_KEY") == ""


def test_shell_secret_cannot_be_rebound_by_clearing_only_the_file(isolated_env, monkeypatch):
    path = isolated_env / ".env"
    path.write_text("AUTOONTOLOGY_OPENAI_BASE_URL=https://old.example/v1\n")
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_API_KEY", "synthetic-shell-value")
    env = EnvFile.load(path)
    env.set("AUTOONTOLOGY_OPENAI_BASE_URL", "https://new.example/v1")
    with pytest.raises(AccessError):
        save_env(Store.for_home(isolated_env / "home"), env, path, key_replaced=True)


@pytest.fixture
def host(tmp_path):
    from autoontology.security.context import acting_as
    from autoontology.security.host_files import HostFiles
    from autoontology.store.identity import Identity

    store = Store.for_home(tmp_path / "workspace")
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long host owner password")
    owner = identity.authenticate(issued.secret.reveal())
    folder = tmp_path / "business"
    folder.mkdir()
    return store, owner, folder, HostFiles(store), acting_as


def test_host_root_requires_explicit_owner_approval_at_registration_and_re_read(host):
    import json

    store, owner, folder, guard, acting = host
    file = folder / "policy.md"
    file.write_text("Business rule")
    with acting(store, owner):
        with pytest.raises(AccessError) as error:
            store.add_source(
                name="rules", kind="documents", locator=json.dumps({"path": str(folder)})
            )
        assert error.value.code == "host_root_required"
        guard.allow(folder, reason="Approved business intake")
        source = store.add_source(
            name="rules", kind="documents", locator=json.dumps({"path": str(folder)})
        )
        from autoontology.store.identity import Identity

        identity = Identity(store)
        with store._conn() as conn:
            domain = conn.execute("SELECT id FROM identity_domains LIMIT 1").fetchone()[0]
        identity.assign_source(owner, source.id, domain)
        assert guard.sha256(file, source_id=source.id)
        guard.revoke(folder, reason="Stop live collection")
        with pytest.raises(AccessError):
            guard.sha256(file, source_id=source.id)


def test_secret_paths_and_links_cannot_be_added_to_a_trusted_business_root(host):
    import json

    store, owner, folder, guard, acting = host
    with acting(store, owner):
        guard.allow(folder, reason="Business only")
        source = store.add_source(
            name="rules", kind="documents", locator=json.dumps({"path": str(folder)})
        )
        secret = folder / "credentials.json"
        secret.write_text('{"secret":"synthetic"}')
        with pytest.raises(AccessError):
            guard.sha256(secret, source_id=source.id)
        ordinary = folder / "ordinary.md"
        ordinary.write_text("Allowed")
        linked = folder / "alias.md"
        linked.symlink_to(ordinary)
        with pytest.raises(AccessError):
            guard.sha256(linked, source_id=source.id)


def test_revocation_uses_stored_path_after_directory_becomes_a_symlink(host):
    store, owner, folder, guard, acting = host
    with acting(store, owner):
        approved = guard.allow(folder, reason="Business intake")
        folder.rmdir()
        other = folder.parent / "other"
        other.mkdir()
        folder.symlink_to(other, target_is_directory=True)
        result = guard.revoke(approved["path"], reason="Disable replaced input")
        assert result == {"path": approved["path"], "active": False}
        assert not guard.listing()["roots"][0]["active"]
        with pytest.raises(AccessError) as error:
            guard.revoke(folder.parent / "missing", reason="Unknown root")
        assert error.value.status == 404


def test_revoked_root_is_rechecked_during_stream_and_archived_bytes_remain(host):
    import json

    from autoontology.store.provenance import SourceArchive

    store, owner, folder, guard, acting = host
    file = folder / "policy.md"
    file.write_text("Business rule")
    with acting(store, owner):
        guard.allow(folder, reason="Initial approval")
        source = store.add_source(
            name="rules", kind="documents", locator=json.dumps({"path": str(folder)})
        )
        from autoontology.store.identity import Identity

        with store._conn() as conn:
            domain = conn.execute("SELECT id FROM identity_domains LIMIT 1").fetchone()[0]
        Identity(store).assign_source(owner, source.id, domain)
        archived = SourceArchive(store).capture_files(source, [file], run_id="capture", root=folder)
        with pytest.raises(AccessError), guard.open(file, source_id=source.id) as stream:
            assert stream.read(4) == b"Busi"
            guard.revoke(folder, reason="Revoked during read")
            stream.read(4)
        assert archived.paths[file.resolve()].read_text() == "Business rule"


def test_native_private_key_file_is_masked_and_requires_owner_only_permissions(isolated_env):
    from typer.testing import CliRunner

    from autoontology.cli import app

    path = isolated_env / ".env"
    path.write_text(
        "AUTOONTOLOGY_OPENAI_BASE_URL=https://old.example/v1\nAUTOONTOLOGY_OPENAI_API_KEY=old-synthetic-secret\n"
    )
    key = isolated_env / "private-key"
    key.write_text("new-synthetic-secret\n")
    key.chmod(0o600)
    result = CliRunner().invoke(
        app,
        [
            "provider",
            "use",
            "openai",
            "--base-url",
            "https://new.example/v1",
            "--api-key-file",
            str(key),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (
        "new-synthetic-secret" not in result.stdout and "old-synthetic-secret" not in result.stdout
    )
    key.chmod(0o644)
    denied = CliRunner().invoke(app, ["provider", "use", "openai", "--api-key-file", str(key)])
    assert denied.exit_code == 2 and "new-synthetic-secret" not in denied.stdout
