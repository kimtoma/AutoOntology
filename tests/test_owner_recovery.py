# SPDX-License-Identifier: MIT
"""OS-owner credential recovery preserves identity and fences prepared effects."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.security import owner_recovery as ceremony
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as
from autoontology.security.internal import identity_enabled
from autoontology.security.owner_adoption import adopt
from autoontology.security.owner_enrollment import enroll
from autoontology.security.policy import AccessError
from autoontology.store.cases import Cases
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.web.app import create_app
from tests.test_cases import create
from tests.test_cases import work as work

OLD = "synthetic original owner password"
NEW = "synthetic recovered owner password"


@pytest.fixture(params=["adopt", "enroll"])
def managed(isolated_env, tmp_path, monkeypatch, request):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "host/pins")
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    identity = Identity(store)
    if request.param == "adopt":
        issued = identity.bootstrap("Recovery", "owner", "Owner", OLD)
        adopt(store, "owner", OLD)
    else:
        enroll(store, "Recovery", "owner", "Owner", OLD)
        issued = identity.login("owner", OLD)
    owner = identity.authenticate(issued.secret.reveal())
    viewer = identity.create_human(owner, "viewer", "Viewer", OLD, ["viewer"])
    agent = identity.delegate(owner, "reader_agent", "Reader agent", ["knowledge.read"])
    with store._conn() as conn:
        conn.execute("CREATE TABLE preserved_knowledge(value TEXT)")
        conn.execute("INSERT INTO preserved_knowledge VALUES('기존 한국어 문서')")
    return settings, store, identity, issued, owner, viewer, agent


def recover(managed, *, request_id="reset-one", resume=False, password=NEW):
    _, store, identity, *_ = managed
    # Pending callers must use the original known workspace, not ordinary status.
    workspace = host.read(store.path)["workspace_id"]
    return ceremony.recover(
        store, "owner", password, workspace_id=workspace, request_id=request_id, resume=resume
    )


def test_recovery_revokes_sessions_delegations_and_preserves_same_identity(managed):
    settings, store, identity, issued, owner, viewer, agent = managed
    before = identity.status()
    result = recover(managed)
    after = identity.status()
    assert after["workspace_id"] == before["workspace_id"]
    assert after["organization_id"] == before["organization_id"]
    assert result["principal_id"] == owner.principal_id
    assert after["policy_version"] == before["policy_version"] + 1
    assert result["phase"] == "active"
    assert identity_enabled(settings.home)
    for credential in [issued, agent]:
        with pytest.raises(AccessError):
            identity.authenticate(credential.secret.reveal())
    with pytest.raises(AccessError):
        identity.login("owner", OLD)
    current = identity.authenticate(identity.login("owner", NEW).secret.reveal())
    assert current.principal_id == owner.principal_id
    assert identity.require(current, "settings.manage")
    assert identity.verify_audit(current)["ok"]
    assert recover(managed) == result
    with store._conn() as conn:
        assert (
            conn.execute("SELECT value FROM preserved_knowledge").fetchone()[0]
            == "기존 한국어 문서"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_recovered'"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 3
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_delegations WHERE revoked_at IS NULL"
            ).fetchone()[0]
            == 0
        )
    pin = next(host.registry_root().glob("*.json")).read_text()
    assert NEW not in pin and "password_hash" not in pin and '"token"' not in pin


@pytest.mark.parametrize(
    "boundary", ["after_reserve", "before_commit", "after_commit", "before_finalize"]
)
def test_recovery_interruption_requires_explicit_same_request_resume(
    managed, monkeypatch, boundary
):
    _, store, identity, *_ = managed
    before = identity.status()

    def crash(name):
        if name == boundary:
            raise RuntimeError("controlled recovery interruption")

    monkeypatch.setattr(ceremony, "_boundary", crash)
    with pytest.raises(RuntimeError):
        recover(managed)
    marker = host.read(store.path)
    assert marker["recovery"]["phase"] == "pending"
    with pytest.raises(AccessError):
        identity.status()
    with pytest.raises(AccessError):
        recover(managed)
    with pytest.raises(AccessError):
        recover(managed, request_id="other", resume=True)
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    result = recover(managed, resume=True)
    assert result["operation_id"] == marker["recovery"]["operation_id"]
    assert identity.status()["policy_version"] == before["policy_version"] + 1
    assert recover(managed) == result


def test_committed_resume_must_prove_the_recovered_password(managed, monkeypatch):
    _, store, _, *_ = managed
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "after_commit" else None,
    )
    with pytest.raises(RuntimeError):
        recover(managed)
    pending = host.read(store.path)
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    with pytest.raises(AccessError):
        recover(managed, resume=True, password="a different new synthetic password")
    assert host.read(store.path) == pending
    recover(managed, resume=True)


def test_superseded_request_cannot_rotate_password_again_or_accept_old_backup(managed, tmp_path):
    _, store, identity, *_ = managed
    first = recover(managed)
    backup = tmp_path / "after-first.db"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    second = recover(
        managed, request_id="reset-two", password="second recovered synthetic password"
    )
    assert first["operation_id"] != second["operation_id"]
    with pytest.raises(AccessError):
        recover(managed)
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(store.path)) as target:
        source.backup(target)
    with pytest.raises(AccessError):
        identity.status()
    with pytest.raises(AccessError):
        recover(managed, request_id="reset-three")


def test_application_actor_and_non_admin_target_cannot_run_recovery(managed):
    _, store, identity, _, owner, viewer, agent = managed
    ws = identity.status()["workspace_id"]
    before = host.read(store.path)
    for actor in [owner, identity.authenticate(agent.secret.reveal())]:
        with acting_as(store, actor), pytest.raises(AccessError) as error:
            recover(managed)
        assert error.value.code == "local_owner_required"
    with pytest.raises(AccessError):
        ceremony.recover(store, "viewer", NEW, workspace_id=ws, request_id="viewer-reset")
    with pytest.raises(AccessError):
        ceremony.recover(store, "owner", NEW, workspace_id="0" * 32, request_id="wrong-home")
    assert host.read(store.path) == before
    assert identity.authenticate(identity.login("owner", OLD).secret.reveal())


@pytest.mark.parametrize("damage", ["pin_missing", "metadata", "foreign_key"])
def test_credential_recovery_never_repairs_corrupt_identity(managed, damage):
    _, store, _, issued, owner, _, _ = managed
    ws = host.read(store.path)["workspace_id"]
    if damage == "pin_missing":
        for path in host.registry_root().glob("*.json"):
            path.unlink()
    else:
        with closing(sqlite3.connect(store.path)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            if damage == "metadata":
                conn.execute("DELETE FROM identity_state")
            else:
                conn.execute(
                    "UPDATE identity_credentials SET principal_id='missing' WHERE id=?",
                    (issued.credential_id,),
                )
            conn.commit()
    with pytest.raises(AccessError):
        ceremony.recover(store, "owner", NEW, workspace_id=ws, request_id="damaged")
    with closing(sqlite3.connect(store.path)) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_recovered'"
            ).fetchone()[0]
            == 0
        )


def test_unpinned_legacy_workspace_cannot_reset_a_password(isolated_env, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "host/pins")
    store = Store.for_home(Settings.from_env().home)
    service = Identity(store)
    service.bootstrap("Legacy", "owner", "Owner", OLD)
    with pytest.raises(AccessError):
        ceremony.recover(
            store, "owner", NEW, workspace_id=service.status()["workspace_id"], request_id="legacy"
        )
    assert host.read(store.path) is None


def test_native_cli_recovery_does_not_replace_credential_file_and_supports_relogin(managed):
    settings, store, identity, *_ = managed
    private = settings.home / "private"
    private.mkdir(mode=0o700)
    old = private / "old"
    old.write_text(OLD)
    old.chmod(0o600)
    new = private / "new"
    new.write_text(NEW)
    new.chmod(0o600)
    runner = CliRunner()
    login = runner.invoke(app, ["auth", "login", "owner", "--password-file", str(old)])
    assert login.exit_code == 0, login.output
    saved = Path(json.loads(login.stdout)["credential_file"])
    before = saved.read_bytes()
    args = [
        "auth",
        "owner-recover",
        "owner",
        "--workspace-id",
        identity.status()["workspace_id"],
        "--request-id",
        "native-reset",
        "--password-file",
        str(new),
    ]
    denied = runner.invoke(app, args)
    assert denied.exit_code == 1 and "local_owner_confirmation_required" in denied.stdout
    denied = runner.invoke(app, [*args, "--confirm-local-owner"])
    assert denied.exit_code == 1 and "recovery_scope_confirmation_required" in denied.stdout
    done = runner.invoke(app, [*args, "--confirm-local-owner", "--revoke-all-sessions"])
    assert done.exit_code == 0, done.output
    assert NEW not in done.stdout and '"token"' not in done.stdout
    assert saved.read_bytes() == before
    assert runner.invoke(app, ["auth", "whoami"]).exit_code == 1
    assert runner.invoke(app, ["auth", "logout"]).exit_code == 0
    assert (
        runner.invoke(app, ["auth", "login", "owner", "--password-file", str(new)]).exit_code == 0
    )
    assert runner.invoke(app, ["auth", "whoami"]).exit_code == 0
    listing = runner.invoke(app, ["source", "list"])
    assert listing.exit_code == 0
    denied = runner.invoke(
        app,
        ["--credential-file", str(saved), *args, "--confirm-local-owner", "--revoke-all-sessions"],
    )
    assert denied.exit_code == 1 and "local_owner_required" in denied.stdout
    application = create_app(settings)
    assert not any(
        getattr(route, "path", "") == "/api/identity/owner-recover" for route in application.routes
    )
    client = TestClient(application)
    assert client.post("/api/identity/owner-recover", json={}).status_code == 401
    response = client.post(
        "/api/identity/owner-recover",
        json={},
        headers={"Authorization": "Bearer " + json.loads(saved.read_text())["token"]},
    )
    assert response.status_code in {403, 404, 405}


def test_prepared_review_packet_is_stale_after_owner_recovery(work, tmp_path, monkeypatch):
    store, identity, owner, agent, revision, changes = work
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "host/pins")
    adopt(store, "owner", "long case owner password")
    cases = Cases(store)
    with acting_as(store, owner):
        case = create(store, changes, owner)
        packet = cases.prepare(case["id"], version=case["version"])
    result = ceremony.recover(
        store, "owner", NEW, workspace_id=identity.status()["workspace_id"], request_id="case-reset"
    )
    assert result["invalidated_packets"] == 1
    current = identity.authenticate(identity.login("owner", NEW).secret.reveal())
    with acting_as(store, current):
        with pytest.raises(AccessError) as error:
            cases.confirm(packet["id"], packet["sha256"])
        assert error.value.code == "case_packet_stale"
        assert not any(c["decision"] for c in cases.ops.changes())
        fresh = cases.prepare(case["id"], version=packet["payload"]["case_version"])
        assert cases.confirm(fresh["id"], fresh["sha256"])["status"] == "reviewed"


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_pending_recovery_backup_preserves_fence_without_installing_authority(
    managed, tmp_path, monkeypatch, boundary
):
    from tests.test_local_checkpoint import checkpoint

    _, store, *_ = managed
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == boundary else None,
    )
    with pytest.raises(RuntimeError):
        recover(managed)
    root = tmp_path / "pending-recovery"
    payload = root / "payload"
    (payload / "workspace").mkdir(parents=True)
    with (
        closing(sqlite3.connect(store.path)) as source,
        closing(sqlite3.connect(payload / "workspace/autoontology.db")) as target,
    ):
        source.backup(target)
    (payload / "host-owner.json").write_text(json.dumps(host.read(store.path)))
    manifest = {
        "label": "recovery",
        "commit": "fixture",
        "host_pin_present": True,
        "host_database": str(store.path),
        "sha256": checkpoint.hashes(payload),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert checkpoint.verify(root)["host_pin_present"]
    with closing(
        sqlite3.connect(
            (payload / "workspace/autoontology.db").as_uri() + "?mode=ro&immutable=1", uri=True
        )
    ) as plain:
        plain.execute("BEGIN")
        host.check_snapshot(plain, host.read(store.path))
    destination = tmp_path / "staged-recovery"
    checkpoint.stage(root, destination)
    assert not host.read(destination / "workspace/autoontology.db")
    assert checkpoint.verify(root)["sha256"] == manifest["sha256"]


def test_local_owner_status_is_read_only_and_resumes_without_remembering_label(
    managed, monkeypatch
):
    _, store, identity, *_ = managed
    before = identity.status()
    monkeypatch.setattr(
        ceremony,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "after_commit" else None,
    )
    with pytest.raises(RuntimeError):
        recover(managed)
    status = ceremony.local_status(store.path)
    assert status["workspace_id"] == before["workspace_id"]
    assert status["recovery"]["phase"] == "pending"
    assert status["recovery"]["database_committed"]
    monkeypatch.setattr(ceremony, "_boundary", lambda _: None)
    done = ceremony.recover(
        store, "owner", NEW, workspace_id=before["workspace_id"], request_id=None, resume=True
    )
    assert done["operation_id"] == status["recovery"]["operation_id"]
    active = ceremony.local_status(store.path)
    assert active["recovery"]["phase"] == "active"
    with store._conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.path.unlink()
    missing = ceremony.local_status(store.path)
    assert missing["database_state"] == "missing"
    assert not store.path.exists()


@pytest.mark.parametrize(
    "fault", ["after_reserve", "before_commit", "after_commit", "before_finalize"]
)
def test_real_process_exit_resumes_one_recovery_operation(managed, fault):
    import subprocess
    import sys

    _, store, identity, *_ = managed
    ws = identity.status()["workspace_id"]
    code = """
import json,os,sys
from pathlib import Path
from autoontology.security import owner_registry as host, owner_recovery as ceremony
from autoontology.store.db import Store
p=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(p['registry'])
ceremony._boundary=lambda name: os._exit(83) if name == p['fault'] else None
ceremony.recover(Store(Path(p['database'])),'owner','synthetic recovered owner password',workspace_id=p['workspace'],request_id='reset-one')
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {
                "registry": str(host.registry_root()),
                "database": str(store.path),
                "workspace": ws,
                "fault": fault,
            }
        ),
        text=True,
        capture_output=True,
        timeout=40,
    )
    assert child.returncode == 83, child.stderr
    pending = host.read(store.path)["recovery"]
    done = recover(managed, resume=True)
    assert done["operation_id"] == pending["operation_id"]
    assert identity.status()["configured"]


def test_competing_processes_finish_one_recovery_request(managed):
    import subprocess
    import sys

    _, store, identity, *_ = managed
    code = """
import json,sys
from pathlib import Path
from autoontology.security import owner_registry as host
from autoontology.security.owner_recovery import recover
from autoontology.store.db import Store
p=json.loads(sys.stdin.read())
host.registry_root=lambda: Path(p['registry'])
print(json.dumps(recover(Store(Path(p['database'])),'owner','synthetic recovered owner password',workspace_id=p['workspace'],request_id='reset-one')))
"""
    payload = json.dumps(
        {
            "registry": str(host.registry_root()),
            "database": str(store.path),
            "workspace": identity.status()["workspace_id"],
        }
    )
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
    with store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.owner_recovered'"
            ).fetchone()[0]
            == 1
        )


def test_unknown_resume_does_not_start_a_new_recovery(managed):
    _, store, identity, *_ = managed
    before = host.read(store.path)
    with pytest.raises(AccessError):
        recover(managed, request_id="not-started", resume=True)
    assert host.read(store.path) == before
    recover(managed)
    before = host.read(store.path)
    with pytest.raises(AccessError):
        recover(managed, request_id="another-not-started", resume=True)
    assert host.read(store.path) == before


def test_latest_receipt_hash_uses_an_index(managed):
    _, store, *_ = managed
    with store._conn() as conn:
        query = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM identity_events WHERE sha256=?", ("0" * 64,)
        ).fetchall()
    assert any("USING INDEX identity_events_sha_idx" in row[3] for row in query)


def test_recovery_journal_cannot_claim_the_legacy_host_protocol(managed):
    _, store, *_ = managed
    recover(managed)
    marker = host.read(store.path)
    assert marker["version"] == 2
    marker["version"] = 1
    with pytest.raises(AccessError):
        host._decode(json.dumps(marker), store.path)
