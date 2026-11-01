# SPDX-License-Identifier: MIT
"""First-owner registration is one recoverable host/SQLite operation."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.security import owner_enrollment as ceremony
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as, authorize
from autoontology.security.credential_file import load_credential
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.web.app import create_app

PASSWORD = "synthetic first owner password"


@pytest.fixture
def fresh(isolated_env, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "host" / "pins")
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    return settings, store, Identity(store)


def enroll(store, **kw):
    return ceremony.enroll(store, "Knowledge OS", "owner", "Owner", PASSWORD, **kw)


def test_first_owner_is_pinned_once_and_preserves_existing_knowledge(fresh):
    _, store, service = fresh
    with store._conn() as conn:
        conn.execute("CREATE TABLE preserved_knowledge(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO preserved_knowledge VALUES(1,'한국어 근거 문서')")
    before = store.schema_version()
    result = enroll(store)
    assert result["phase"] == "active"
    assert result == enroll(store)
    assert service.status()["workspace_id"] == result["workspace_id"]
    actor = service.authenticate(service.login("owner", PASSWORD).secret.reveal())
    assert service.verify_audit(actor)["ok"]
    assert service.require(actor, "settings.manage")
    with store._conn() as conn:
        assert (
            conn.execute("SELECT value FROM preserved_knowledge").fetchone()[0]
            == "한국어 근거 문서"
        )
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.bootstrap'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_pinned'"
            ).fetchone()[0]
            == 1
        )
        # The ceremony itself never issues an unrecoverable bearer credential.
        assert conn.execute("SELECT count(*) FROM identity_credentials").fetchone()[0] == 1
    assert store.schema_version() == before
    payload = next(host.registry_root().glob("*.json")).read_text()
    assert PASSWORD not in payload and "password_hash" not in payload and '"token"' not in payload


@pytest.mark.parametrize(
    "fault", ["after_reserve", "before_commit", "after_commit", "before_finalize"]
)
def test_every_interruption_requires_resume_and_reuses_reserved_identity(fresh, monkeypatch, fault):
    settings, store, service = fresh

    def crash(boundary):
        if boundary == fault:
            raise RuntimeError("controlled interruption")

    monkeypatch.setattr(ceremony, "_boundary", crash)
    with pytest.raises(RuntimeError):
        enroll(store)
    pending = host.read(store.path)
    assert pending["kind"] == "enroll" and pending["phase"] == "pending"
    with pytest.raises(AccessError):
        service.status()
    with pytest.raises(AccessError):
        authorize(store, "settings.manage")
    assert TestClient(create_app(settings)).get("/api/identity/status").status_code == 503
    with pytest.raises(AccessError):
        enroll(store)
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    done = enroll(store, resume=True)
    assert done["operation_id"] == pending["operation_id"]
    assert done["principal_id"] == pending["principal_id"]
    assert done["workspace_id"] == pending["workspace_id"]
    assert service.status()["configured"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM identity_events").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM identity_credentials").fetchone()[0] == 0


def test_committed_pending_cannot_be_resumed_with_new_password_or_identity(fresh, monkeypatch):
    _, store, _ = fresh
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "after_commit" else None,
    )
    with pytest.raises(RuntimeError):
        enroll(store)
    old = host.read(store.path)
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    with pytest.raises(AccessError):
        ceremony.enroll(
            store, "Knowledge OS", "owner", "Owner", "different synthetic password", resume=True
        )
    with pytest.raises(AccessError):
        ceremony.enroll(store, "Replacement", "owner", "Owner", PASSWORD, resume=True)
    assert host.read(store.path) == old
    assert enroll(store, resume=True)["operation_id"] == old["operation_id"]


@pytest.mark.parametrize(
    "field,value", [("workspace", ""), ("username", "X"), ("name", ""), ("password", "short")]
)
def test_invalid_input_never_reserves_host_identity(fresh, field, value):
    _, store, service = fresh
    args = dict(workspace="Knowledge OS", username="owner", name="Owner", password=PASSWORD)
    args[field] = value
    with pytest.raises(AccessError):
        ceremony.enroll(store, **args)
    assert host.read(store.path) is None and not service.status()["configured"]


def test_existing_legacy_identity_requires_adoption_and_agent_cannot_enroll(fresh):
    _, store, service = fresh
    issued = service.bootstrap("Legacy", "owner", "Owner", PASSWORD)
    with pytest.raises(AccessError) as error:
        enroll(store)
    assert error.value.code == "identity_already_configured"
    actor = service.authenticate(issued.secret.reveal())
    with acting_as(store, actor), pytest.raises(AccessError) as error:
        enroll(store)
    assert error.value.code == "local_owner_required"
    assert host.read(store.path) is None


@pytest.mark.parametrize("loss", ["missing", "pre_enrollment", "pin_missing"])
def test_finished_enrollment_cannot_be_replaced_or_restarted(fresh, tmp_path, loss):
    _, store, service = fresh
    backup = tmp_path / "before.db"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    enroll(store)
    if loss == "pin_missing":
        for path in host.registry_root().glob("*.json"):
            path.unlink()
    elif loss == "missing":
        with closing(sqlite3.connect(store.path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.path.unlink()
    else:
        with (
            closing(sqlite3.connect(backup)) as source,
            closing(sqlite3.connect(store.path)) as target,
        ):
            source.backup(target)
    with pytest.raises(AccessError):
        service.status()
    with pytest.raises(AccessError):
        enroll(store, resume=True)
    if loss == "missing":
        assert not store.path.exists()


def test_cli_requires_confirmation_and_saves_owner_only_session(fresh):
    settings, store, service = fresh
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    password = private / "password"
    password.write_text(PASSWORD + "\n")
    password.chmod(0o600)
    args = [
        "auth",
        "enroll",
        "owner",
        "--workspace",
        "Knowledge OS",
        "--name",
        "Owner",
        "--password-file",
        str(password),
    ]
    runner = CliRunner()
    denied = runner.invoke(app, args)
    assert denied.exit_code == 1 and "local_owner_confirmation_required" in denied.stdout
    assert host.read(store.path) is None
    done = runner.invoke(app, [*args, "--confirm-local-owner"])
    assert done.exit_code == 0, done.output
    result = json.loads(done.stdout)
    assert PASSWORD not in done.output and '"token"' not in done.output
    session = Path(result["credential_file"])
    assert session.stat().st_mode & 0o777 == 0o600
    assert session.parent.stat().st_mode & 0o777 == 0o700
    saved = load_credential(
        session, workspace_id=result["workspace_id"], origin=settings.service_url
    )
    assert service.authenticate(saved.secret.reveal()).principal_id == result["principal_id"]
    assert runner.invoke(app, [*args, "--confirm-local-owner"]).exit_code == 0
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM identity_credentials").fetchone()[0] == 1
    assert runner.invoke(app, ["auth", "whoami"]).exit_code == 0
    blocked = runner.invoke(
        app, ["--credential-file", str(session), *args, "--confirm-local-owner"]
    )
    assert blocked.exit_code == 1 and "local_owner_required" in blocked.stdout


@pytest.mark.parametrize("boundary", ["after_reserve", "after_commit"])
def test_pending_enrollment_checkpoint_is_verified_without_activating_it(
    fresh, tmp_path, monkeypatch, boundary
):
    from tests.test_local_checkpoint import checkpoint

    _, store, _ = fresh
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == boundary else None,
    )
    with pytest.raises(RuntimeError):
        enroll(store)
    root = tmp_path / "pending-backup"
    payload = root / "payload"
    (payload / "workspace").mkdir(parents=True)
    with (
        closing(sqlite3.connect(store.path)) as source,
        closing(sqlite3.connect(payload / "workspace/autoontology.db")) as target,
    ):
        source.backup(target)
    (payload / "host-owner.json").write_text(json.dumps(host.read(store.path)))
    manifest = {
        "label": "pending",
        "commit": "fixture",
        "host_database": str(store.path),
        "host_pin_present": True,
        "sha256": checkpoint.hashes(payload),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert checkpoint.verify(root)["host_pin_present"]
    destination = tmp_path / "restored-pending"
    checkpoint.stage(root, destination)
    assert checkpoint.verify(root)["sha256"] == manifest["sha256"]
    assert host.read(store.path)["phase"] == "pending"
    assert (destination / "host-owner.json").exists()
    assert not host.read(destination / "workspace/autoontology.db")


def test_competing_native_processes_create_only_one_owner(fresh):
    import subprocess
    import sys

    _, store, service = fresh
    code = """
import json,sys
from pathlib import Path
from autoontology.security import owner_registry as host
from autoontology.security.owner_enrollment import enroll
from autoontology.store.db import Store
p=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(p['registry'])
print(json.dumps(enroll(Store(Path(p['database'])), 'Knowledge OS', 'owner', 'Owner', 'synthetic first owner password')))
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
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout))
    assert results[0] == results[1]
    assert service.status()["configured"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM identity_events").fetchone()[0] == 2


@pytest.mark.parametrize(
    "fault", ["after_reserve", "before_commit", "after_commit", "before_finalize"]
)
def test_real_process_exit_recovers_same_reserved_enrollment(fresh, fault):
    import subprocess
    import sys

    _, store, service = fresh
    code = """
import json,os,sys
from pathlib import Path
from autoontology.security import owner_registry as host, owner_enrollment as ceremony
from autoontology.store.db import Store
p=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(p['registry'])
ceremony._boundary=lambda name: os._exit(83) if name == p['fault'] else None
ceremony.enroll(Store(Path(p['database'])), 'Knowledge OS', 'owner', 'Owner', 'synthetic first owner password')
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {"registry": str(host.registry_root()), "database": str(store.path), "fault": fault}
        ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 83, child.stderr
    pending = host.read(store.path)
    with pytest.raises(AccessError):
        service.status()
    assert enroll(store, resume=True)["operation_id"] == pending["operation_id"]
    assert service.status()["configured"]


@pytest.mark.parametrize("unsafe", ["permissions", "symlink", "directory"])
def test_first_enrollment_password_file_must_be_private(fresh, unsafe):
    settings, store, _ = fresh
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    password = private / "password"
    password.write_text(PASSWORD)
    password.chmod(0o600)
    if unsafe == "permissions":
        password.chmod(0o644)
    elif unsafe == "symlink":
        target = private / "target"
        password.rename(target)
        password.symlink_to(target)
    else:
        private.chmod(0o755)
    result = CliRunner().invoke(
        app,
        [
            "auth",
            "enroll",
            "owner",
            "--workspace",
            "Knowledge OS",
            "--name",
            "Owner",
            "--confirm-local-owner",
            "--password-file",
            str(password),
        ],
    )
    assert result.exit_code == 1 and "password_file_invalid" in result.stdout
    assert PASSWORD not in result.stdout
    assert host.read(store.path) is None


def test_lost_session_save_does_not_reset_enrollment_or_leave_live_bearer(fresh, monkeypatch):
    from autoontology.security import cli

    settings, store, service = fresh
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    password = private / "password"
    password.write_text(PASSWORD)
    password.chmod(0o600)
    original = cli.save_credential

    def fail(*args, **kw):
        raise OSError("controlled filesystem failure")

    monkeypatch.setattr(cli, "save_credential", fail)
    args = [
        "auth",
        "enroll",
        "owner",
        "--workspace",
        "Knowledge OS",
        "--name",
        "Owner",
        "--confirm-local-owner",
        "--password-file",
        str(password),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1 and "owner_enrollment_incomplete" in result.stdout
    assert service.status()["configured"]
    operation = host.read(store.path)["operation_id"]
    with store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_credentials WHERE revoked_at IS NULL"
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(cli, "save_credential", original)
    done = CliRunner().invoke(app, args)
    assert done.exit_code == 0, done.output
    assert json.loads(done.stdout)["operation_id"] == operation


def test_partial_metadata_and_remote_requests_cannot_create_an_owner(fresh):
    settings, store, service = fresh
    client = TestClient(create_app(settings))
    assert client.post("/api/identity/enroll", json={}).status_code in {404, 405}
    service.bootstrap("Legacy", "owner", "Owner", PASSWORD)
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM identity_state")
        conn.execute("DELETE FROM identity_principals")
        conn.execute("DELETE FROM identity_domains")
    with pytest.raises(AccessError):
        enroll(store)
    assert host.read(store.path) is None


@pytest.mark.parametrize("declare_pin", [True, False])
def test_checkpoint_cannot_omit_pin_of_a_pinned_database(fresh, tmp_path, declare_pin):
    from tests.test_local_checkpoint import checkpoint

    _, store, _ = fresh
    enroll(store)
    root = tmp_path / "missing-pin-checkpoint"
    payload = root / "payload/workspace"
    payload.mkdir(parents=True)
    with (
        closing(sqlite3.connect(store.path)) as source,
        closing(sqlite3.connect(payload / "autoontology.db")) as target,
    ):
        source.backup(target)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "label": "missing",
                "commit": "fixture",
                "host_pin_present": declare_pin,
                "host_database": str(store.path),
                "sha256": checkpoint.hashes(root / "payload"),
            }
        )
    )
    with pytest.raises((AccessError, ValueError)):
        checkpoint.verify(root)


def test_checkpoint_with_pin_but_no_database_is_not_a_recovery_point(fresh, tmp_path):
    from tests.test_local_checkpoint import checkpoint

    _, store, _ = fresh
    enroll(store)
    root = tmp_path / "missing-db"
    payload = root / "payload"
    payload.mkdir(parents=True)
    (payload / "host-owner.json").write_text(json.dumps(host.read(store.path)))
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "label": "missing",
                "commit": "fixture",
                "host_pin_present": True,
                "host_database": str(store.path),
                "sha256": checkpoint.hashes(payload),
            }
        )
    )
    with pytest.raises(ValueError):
        checkpoint.verify(root)


def test_precommit_resume_preserves_reserved_ids_and_has_no_prior_password(fresh, monkeypatch):
    _, store, service = fresh
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "before_commit" else None,
    )
    with pytest.raises(RuntimeError):
        enroll(store)
    pending = host.read(store.path)
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 0
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    different = "new uncommitted synthetic password"
    result = ceremony.enroll(store, "Knowledge OS", "owner", "Owner", different, resume=True)
    assert result["principal_id"] == pending["principal_id"]
    assert result["workspace_id"] == pending["workspace_id"]
    assert service.login("owner", different)
    with pytest.raises(AccessError):
        service.login("owner", PASSWORD)


def test_cli_masks_database_failure_and_retains_a_resumable_reservation(fresh, monkeypatch):
    settings, store, service = fresh
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    password = private / "password"
    password.write_text(PASSWORD)
    password.chmod(0o600)
    seed = Identity._seed_owner

    def fail(*args, **kwargs):
        raise sqlite3.IntegrityError("controlled SQLite detail")

    monkeypatch.setattr(Identity, "_seed_owner", fail)
    args = [
        "auth",
        "enroll",
        "owner",
        "--workspace",
        "Knowledge OS",
        "--name",
        "Owner",
        "--confirm-local-owner",
        "--password-file",
        str(password),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1 and "owner_enrollment_incomplete" in result.stdout
    assert "controlled SQLite detail" not in result.stdout and PASSWORD not in result.stdout
    assert host.read(store.path)["phase"] == "pending"
    monkeypatch.setattr(Identity, "_seed_owner", seed)
    result = CliRunner().invoke(app, [*args, "--resume"])
    assert result.exit_code == 0, result.output
    assert service.status()["configured"]
