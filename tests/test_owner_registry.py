# SPDX-License-Identifier: MIT
"""Independent host pins survive database loss without granting replacement authority."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as, authorize
from autoontology.security.internal import identity_enabled
from autoontology.security.owner_adoption import adopt
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.web.app import create_app


@pytest.fixture
def owner(isolated_env, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "host-owned" / "pins")
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    identity = Identity(store)
    issued = identity.bootstrap("Pinned", "owner", "Owner", "synthetic owner pin password")
    return settings, store, identity, issued


def test_status_is_read_only_and_does_not_create_registry(owner):
    settings, store, service, _ = owner
    assert service.status()["configured"]
    assert identity_enabled(settings.home)
    assert not host.registry_root().exists()
    assert not host.read(store.path)


def test_adoption_pins_same_workspace_and_is_idempotent(owner):
    settings, store, service, _ = owner
    before = service.status()
    result = adopt(store, "owner", "synthetic owner pin password")
    assert result["phase"] == "active"
    assert result["workspace_id"] == before["workspace_id"]
    assert adopt(store, "owner", "synthetic owner pin password") == result
    assert service.status() == before
    assert host.registry_root().stat().st_mode & 0o777 == 0o700
    files = list(host.registry_root().glob("*.json"))
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
    assert "password" not in files[0].read_text()
    actor = service.authenticate(
        service.login("owner", "synthetic owner pin password").secret.reveal()
    )
    assert service.verify_audit(actor)["ok"]
    events = [e for e in service.audit(actor) if e["action"] == "identity.owner_pinned"]
    assert len(events) == 1 and events[0]["actor_id"] == result["principal_id"]


@pytest.mark.parametrize("loss", ["empty", "missing", "other_workspace"])
def test_whole_database_loss_or_rollback_fails_every_boundary(owner, tmp_path, loss):
    settings, store, service, _ = owner
    client = TestClient(create_app(settings))
    adopt(store, "owner", "synthetic owner pin password")
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(store.path) + suffix).unlink(missing_ok=True)
    if loss != "missing":
        replacement = Store.for_home(tmp_path / "replacement")
        if loss == "other_workspace":
            Identity(replacement).bootstrap(
                "Other", "other", "Other", "different synthetic password"
            )
        shutil.copyfile(replacement.path, store.path)
    actions = [lambda: identity_enabled(settings.home), service.status]
    if loss != "missing":
        actions += [service.status, lambda: authorize(store, "settings.manage")]
    for action in actions:
        with pytest.raises(AccessError) as error:
            action()
        assert error.value.code == "identity_configuration_missing"
    if loss == "missing":
        with pytest.raises(AccessError):
            Store.for_home(settings.home)
        assert not store.path.exists()
    else:
        assert client.get("/api/identity/status").status_code == 503
    result = CliRunner().invoke(app, ["auth", "status"])
    assert result.exit_code == 1 and "identity_configuration_missing" in result.output


def test_deleting_marker_does_not_unpin_a_managed_database(owner):
    _, store, service, _ = owner
    adopt(store, "owner", "synthetic owner pin password")
    for path in host.registry_root().iterdir():
        path.unlink()
    host.registry_root().rmdir()
    with pytest.raises(AccessError):
        service.status()
    with pytest.raises(AccessError):
        adopt(store, "owner", "synthetic owner pin password")


@pytest.mark.parametrize("fault", ["before_commit", "after_commit", "before_finalize"])
def test_interrupted_adoption_resumes_same_operation_without_second_event(
    owner, monkeypatch, fault
):
    _, store, service, _ = owner
    from autoontology.security import owner_adoption

    def crash(boundary):
        if boundary == fault:
            raise RuntimeError("controlled process boundary")

    monkeypatch.setattr(owner_adoption, "_boundary", crash)
    with pytest.raises(RuntimeError):
        adopt(store, "owner", "synthetic owner pin password")
    pending = host.read(store.path)
    assert pending["phase"] == "pending"
    with pytest.raises(AccessError):
        service.status()
    with pytest.raises(AccessError):
        adopt(store, "owner", "synthetic owner pin password")
    monkeypatch.setattr(owner_adoption, "_boundary", lambda _: None)
    done = adopt(store, "owner", "synthetic owner pin password", resume=True)
    assert done["operation_id"] == pending["operation_id"]
    with store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_pinned'"
            ).fetchone()[0]
            == 1
        )
    assert service.status()["configured"]


@pytest.mark.parametrize(
    "unsafe",
    [
        "file_symlink",
        "directory_symlink",
        "world_readable",
        "bad_json",
        "oversize",
        "wrong_workspace",
        "hardlink",
    ],
)
def test_unsafe_or_inconsistent_registry_never_grants_access(owner, tmp_path, unsafe):
    _, store, service, _ = owner
    adopt(store, "owner", "synthetic owner pin password")
    path = next(host.registry_root().glob("*.json"))
    if unsafe == "file_symlink":
        original = tmp_path / "original"
        shutil.copyfile(path, original)
        path.unlink()
        path.symlink_to(original)
    elif unsafe == "directory_symlink":
        root = host.registry_root()
        original = root.with_name("moved")
        root.rename(original)
        root.symlink_to(original)
    elif unsafe == "world_readable":
        path.chmod(0o644)
    elif unsafe == "bad_json":
        path.write_text("{")
    elif unsafe == "oversize":
        path.write_bytes(b"x" * 16385)
    elif unsafe == "wrong_workspace":
        data = json.loads(path.read_text())
        data["workspace_id"] = "0" * 32
        path.write_text(json.dumps(data))
    elif unsafe == "hardlink":
        import os

        os.link(path, tmp_path / "second-link")
    with pytest.raises(AccessError) as error:
        service.status()
    assert error.value.code == "identity_configuration_missing"


def test_wrong_password_or_non_owner_cannot_create_pin(owner):
    _, store, service, issued = owner
    with pytest.raises(AccessError) as error:
        adopt(store, "owner", "incorrect synthetic password")
    assert error.value.code == "invalid_credentials"
    assert not host.read(store.path)
    actor = service.authenticate(issued.secret.reveal())
    service.create_human(actor, "reader", "Reader", "synthetic reader pin password", ["viewer"])
    with pytest.raises(AccessError) as error:
        adopt(store, "reader", "synthetic reader pin password")
    assert error.value.code == "forbidden"
    assert not host.read(store.path)
    with acting_as(store, actor), pytest.raises(AccessError):
        adopt(store, "owner", "synthetic owner pin password")


def test_cli_requires_explicit_local_confirmation_and_never_prints_password(owner):
    settings, store, service, _ = owner
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    password = private / "password"
    password.write_text("synthetic owner pin password\n")
    password.chmod(0o600)
    args = ["auth", "owner-adopt", "owner", "--password-file", str(password)]
    denied = CliRunner().invoke(app, args)
    assert denied.exit_code == 1 and "local_owner_confirmation_required" in denied.stdout
    assert not host.read(store.path)
    done = CliRunner().invoke(app, [*args, "--confirm-local-owner"])
    assert done.exit_code == 0, done.output
    assert json.loads(done.stdout)["phase"] == "active"
    assert "synthetic owner pin password" not in done.output
    assert service.status()["configured"]


def test_database_copy_requires_explicit_marker_recovery(owner, tmp_path):
    _, store, _, _ = owner
    adopt(store, "owner", "synthetic owner pin password")
    copied = tmp_path / "copied" / "autoontology.db"
    copied.parent.mkdir()
    with sqlite3.connect(store.path) as source, sqlite3.connect(copied) as target:
        source.backup(target)
    with pytest.raises(AccessError):
        Identity(Store(copied)).status()


def test_rollback_to_same_pre_pin_workspace_fails_closed(owner, tmp_path):
    _, store, service, _ = owner
    backup = tmp_path / "before-pin.db"
    with sqlite3.connect(store.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    adopt(store, "owner", "synthetic owner pin password")
    with sqlite3.connect(backup) as source, sqlite3.connect(store.path) as target:
        source.backup(target)
    with pytest.raises(AccessError):
        service.status()


def test_competing_local_ceremonies_share_one_durable_operation(owner):
    from concurrent.futures import ThreadPoolExecutor

    _, store, _, _ = owner
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: adopt(store, "owner", "synthetic owner pin password"), range(2))
        )
    assert results[0] == results[1]
    with store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_pinned'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_actual_process_death_preserves_pending_operation(owner, boundary):
    import subprocess
    import sys

    _, store, service, _ = owner
    code = """
import os,sys,json
from pathlib import Path
from autoontology.security import owner_registry as host, owner_adoption
from autoontology.store.db import Store
payload=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(payload['registry'])
def boundary(name):
    if name==payload['boundary']: os._exit(83)
owner_adoption._boundary=boundary
owner_adoption.adopt(Store(Path(payload['database'])), 'owner', 'synthetic owner pin password')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {
                "registry": str(host.registry_root()),
                "database": str(store.path),
                "boundary": boundary,
            }
        ),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 83, result.stderr
    pending = host.read(store.path)
    with pytest.raises(AccessError):
        service.status()
    done = adopt(store, "owner", "synthetic owner pin password", resume=True)
    assert done["operation_id"] == pending["operation_id"]
    assert service.status()["configured"]


def test_symlink_database_cannot_change_the_pin_key(owner, tmp_path):
    _, store, service, _ = owner
    adopt(store, "owner", "synthetic owner pin password")
    moved = tmp_path / "moved.db"
    with sqlite3.connect(store.path) as source, sqlite3.connect(moved) as target:
        source.backup(target)
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.path.unlink()
    store.path.symlink_to(moved)
    with pytest.raises(AccessError):
        service.status()


def test_owner_ceremony_serializes_threads_independently_of_os_lock_semantics(owner, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    _, store, _, _ = owner
    # Some OS locks identify the process rather than the Python thread. The local
    # gate must still serialize the complete host ceremony if flock is reentrant.
    monkeypatch.setattr(host.fcntl, "flock", lambda *_: None)
    start, counter_lock = Barrier(2), Lock()
    counters = {"active": 0, "maximum": 0}

    def enter(_):
        start.wait(timeout=5)
        with host.locked(store.path):
            with counter_lock:
                counters["active"] += 1
                counters["maximum"] = max(counters["maximum"], counters["active"])
            time.sleep(0.04)
            with counter_lock:
                counters["active"] -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(enter, range(2)))
    assert counters["maximum"] == 1


def test_competing_processes_share_one_host_operation(owner):
    import subprocess
    import sys

    _, store, service, _ = owner
    code = """
import json,sys
from pathlib import Path
from autoontology.security import owner_registry as host
from autoontology.security.owner_adoption import adopt
from autoontology.store.db import Store
payload=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(payload['registry'])
print(json.dumps(adopt(Store(Path(payload['database'])), 'owner', 'synthetic owner pin password')))
"""
    payload = json.dumps({"registry": str(host.registry_root()), "database": str(store.path)})
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for process in processes:
        process.stdin.write(payload)
        process.stdin.close()
        process.stdin = None
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout))
    assert outputs[0] == outputs[1]
    assert service.status()["configured"]


def test_checkpoint_keeps_and_verifies_frozen_database_host_pin_pair(owner, tmp_path):
    from tests.test_local_checkpoint import checkpoint

    _, store, _, _ = owner
    adopt(store, "owner", "synthetic owner pin password")
    root = tmp_path / "paired-checkpoint"
    payload = root / "payload"
    (payload / "workspace").mkdir(parents=True)
    with (
        sqlite3.connect(store.path) as source,
        sqlite3.connect(payload / "workspace/autoontology.db") as target,
    ):
        source.backup(target)
    (payload / "host-owner.json").write_text(json.dumps(host.read(store.path)))
    manifest = {
        "label": "owner-pin",
        "commit": "test",
        "host_database": str(store.path.resolve()),
        "host_pin_present": True,
        "sha256": checkpoint.hashes(payload),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert checkpoint.verify(root)["host_pin_present"]
    restored = tmp_path / "offline-restore"
    checkpoint.stage(root, restored)
    assert (restored / "host-owner.json").read_bytes() == (payload / "host-owner.json").read_bytes()
    assert checkpoint.verify(root)["sha256"] == manifest["sha256"]
    # Staging files cannot silently enroll a different canonical home.
    with pytest.raises(AccessError):
        Identity(Store(restored / "workspace/autoontology.db")).status()


def test_checkpoint_rejects_pair_from_before_the_pin_even_when_payload_hashes_match(
    owner, tmp_path
):
    from tests.test_local_checkpoint import checkpoint

    _, store, _, _ = owner
    root = tmp_path / "rolled-back-checkpoint"
    payload = root / "payload"
    (payload / "workspace").mkdir(parents=True)
    with (
        sqlite3.connect(store.path) as source,
        sqlite3.connect(payload / "workspace/autoontology.db") as target,
    ):
        source.backup(target)
    adopt(store, "owner", "synthetic owner pin password")
    (payload / "host-owner.json").write_text(json.dumps(host.read(store.path)))
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "label": "wrong-pair",
                "commit": "test",
                "host_database": str(store.path.resolve()),
                "host_pin_present": True,
                "sha256": checkpoint.hashes(payload),
            }
        )
    )
    with pytest.raises(AccessError):
        checkpoint.verify(root)
    with pytest.raises(AccessError):
        checkpoint.stage(root, tmp_path / "must-not-create")
    assert not (tmp_path / "must-not-create").exists()


@pytest.mark.parametrize("table", ["identity_credentials", "identity_grants"])
def test_adoption_refuses_dangling_identity_references(owner, table):
    _, store, service, issued = owner
    actor = service.authenticate(issued.secret.reveal())
    reader = service.create_human(
        actor, "reader", "Reader", "synthetic reader pin password", ["viewer"]
    )
    service.login("reader", "synthetic reader pin password")
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            f'UPDATE "{table}" SET principal_id=? WHERE principal_id=?', ("0" * 32, reader["id"])
        )
    with pytest.raises(AccessError) as error:
        adopt(store, "owner", "synthetic owner pin password")
    assert error.value.code == "identity_configuration_missing"
    assert host.read(store.path) is None


def test_lock_creation_is_exclusive_before_opening_an_existing_lock(owner, monkeypatch):
    _, store, _, _ = owner
    original = host.os.open
    flags_seen = []

    def observed(path, flags, *args, **kwargs):
        if str(path).endswith(".json.lock"):
            flags_seen.append(flags)
            # Observed macOS race: a nonexclusive O_CREAT|O_NOFOLLOW open may
            # return ENOENT while its competitor created the same existing name.
            if flags & host.os.O_CREAT and not flags & host.os.O_EXCL:
                raise FileNotFoundError("controlled concurrent nonexclusive creation")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(host.os, "open", observed)
    with host.locked(store.path):
        pass
    with host.locked(store.path):
        pass
    assert flags_seen[0] & host.os.O_EXCL
    assert any(not flags & host.os.O_CREAT for flags in flags_seen)


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions"])
def test_existing_lock_stays_unmodified_when_unsafe(owner, tmp_path, unsafe):
    _, store, _, _ = owner
    with host.locked(store.path):
        pass
    lock = host.registry_root() / (host._filename(store.path) + ".lock")
    if unsafe == "symlink":
        target = tmp_path / "lock-target"
        lock.rename(target)
        lock.symlink_to(target)
    elif unsafe == "hardlink":
        import os

        os.link(lock, tmp_path / "lock-second-link")
    else:
        lock.chmod(0o644)
    with pytest.raises(AccessError):
        with host.locked(store.path):
            pass
    assert lock.is_symlink() if unsafe == "symlink" else lock.exists()
