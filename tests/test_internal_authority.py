# SPDX-License-Identifier: MIT
"""Internal model keys are private and never stand in for a user principal."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from autoontology.security.internal import identity_enabled, router_secret
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity


def test_concurrent_router_key_creation_is_atomic_owner_only_and_not_a_user_session(tmp_path):
    service = Identity(Store.for_home(tmp_path / "home"))
    service.bootstrap("Internal", "owner", "Owner", "internal model key password")
    with ThreadPoolExecutor(6) as executor:
        values = list(executor.map(lambda _: router_secret(service.store.path.parent), range(12)))
    assert len({secret.reveal() for secret in values}) == 1
    assert all("ir_" not in repr(secret) for secret in values)
    path = service.store.path.parent / "security/router-secret"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]
    with pytest.raises(AccessError):
        service.authenticate(values[0].reveal())


def test_identity_probe_does_not_create_or_migrate_a_legacy_workspace(tmp_path, monkeypatch):
    from autoontology.store import db

    home = tmp_path / "absent"
    assert not identity_enabled(home) and not home.exists()
    with monkeypatch.context() as legacy:
        legacy.setattr(db, "MIGRATIONS", db.MIGRATIONS[:3])
        store = Store.for_home(home)
    assert not identity_enabled(home)
    assert store.schema_version() == 3


def test_lost_identity_state_cannot_reenroll_a_new_owner(tmp_path):
    service = Identity(Store.for_home(tmp_path / "home"))
    service.bootstrap("Internal", "owner", "Owner", "lost identity test password")
    with service.store._immediate() as conn:
        conn.execute("DELETE FROM identity_state")
    with pytest.raises(AccessError) as error:
        identity_enabled(service.store.path.parent)
    assert error.value.code == "identity_configuration_missing"
    with pytest.raises(AccessError) as error:
        service.bootstrap("Other", "intruder", "Intruder", "new enrollment test password")
    assert error.value.code == "identity_configuration_missing"
    with service.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 1


def test_unsafe_router_key_permissions_fail_closed(tmp_path):
    Store.for_home(tmp_path / "home")
    router_secret(tmp_path / "home")
    (tmp_path / "home/security/router-secret").chmod(0o644)
    with pytest.raises(AccessError):
        router_secret(tmp_path / "home")
