# SPDX-License-Identifier: MIT
"""Authority transfer retires the source before a new owner can use the restored home."""

import sqlite3

import pytest

from autoontology.recovery import quiescence, transfer
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from tests.test_recovery_checkpoint import bundle  # noqa: F401
from tests.test_restore_staging import PASSWORD, prepare, ready  # noqa: F401

NEW_PASSWORD = "synthetic newly restored owner password"


@pytest.fixture
def case(ready, tmp_path, monkeypatch):  # noqa: F811 - shared pytest fixture
    root, source, enrolled, ports = ready
    identity = Identity(source)
    old_session = identity.login("owner", PASSWORD)
    monkeypatch.setattr(
        quiescence,
        "check",
        lambda *a: {"source": "controlled offline fixture", "target": "controlled offline fixture"},
    )
    from tests.test_recovery_checkpoint import rehash

    rehash(root)
    destination = tmp_path / "authority-destination"
    prepare(ready, destination)
    return root, source, enrolled, destination, old_session


def activate(case, **kwargs):
    return transfer.activate(
        case[3], "owner", NEW_PASSWORD, workspace_id=case[2]["workspace_id"], **kwargs
    )


def test_source_retired_owner_relogin_old_tokens_denied_and_catalog_work_held(case):
    _, source, enrolled, dest, old = case
    before = source.path.read_bytes()
    result = activate(case)
    assert result["authority_active"] and result["runtime_start_supported"]
    assert result["catalog_ready"] is False
    assert result["workspace_id"] == enrolled["workspace_id"]
    assert source.path.read_bytes() == before
    with pytest.raises(AccessError):
        Identity(source).authenticate(old.secret.reveal())
    target = Identity(Store.for_home(dest / "workspace"))
    with pytest.raises(AccessError):
        target.authenticate(old.secret.reveal())
    with pytest.raises(AccessError):
        target.login("owner", PASSWORD)
    actor = target.authenticate(target.login("owner", NEW_PASSWORD).secret.reveal())
    assert target.require(actor, "knowledge.read")
    assert target.require(actor, "identity.admin")
    with pytest.raises(AccessError) as error:
        target.require(actor, "query.run")
    assert error.value.code == "restored_catalog_review_required"
    assert target.verify_audit(actor)["ok"]
    assert activate(case, resume=True)["event_sha256"] == result["event_sha256"]
    text = "\n".join(p.read_text() for p in host.registry_root().glob("*.json"))
    assert NEW_PASSWORD not in text and '"password_hash"' not in text


@pytest.mark.parametrize(
    "boundary",
    [
        "after_prepare",
        "after_source_fence",
        "before_commit",
        "after_commit",
        "after_runtime_files",
        "before_master_active",
        "after_master_active",
    ],
)
def test_all_transfer_boundaries_resume_without_minting_another_owner(case, monkeypatch, boundary):
    def crash(name):
        if name == boundary:
            raise RuntimeError("controlled transfer interruption")

    monkeypatch.setattr(transfer, "_boundary", crash)
    with pytest.raises(RuntimeError):
        activate(case)
    with pytest.raises(AccessError):
        Store.for_home(case[3] / "workspace") if boundary != "after_master_active" else Identity(
            Store.for_home(case[3] / "workspace")
        ).login("owner", PASSWORD)
    if boundary != "after_prepare":
        with pytest.raises(AccessError):
            Identity(case[1]).status()
    monkeypatch.setattr(transfer, "_boundary", lambda _: None)
    result = activate(case, resume=True)
    assert result["authority_active"]
    with sqlite3.connect(case[3] / "workspace/autoontology.db") as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.workspace_restored'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM identity_principals WHERE kind='human'").fetchone()[
                0
            ]
            == 1
        )


def test_failed_quiescence_does_not_change_any_authority(case, monkeypatch):
    source_pin = host.read(case[1].path)
    target_pin = host.read(case[3] / "workspace/autoontology.db")
    monkeypatch.setattr(
        quiescence,
        "check",
        lambda *a: (_ for _ in ()).throw(AccessError("not offline", "restore_source_busy", 409)),
    )
    with pytest.raises(AccessError):
        activate(case)
    assert (
        host.read(case[1].path) == source_pin
        and host.read(case[3] / "workspace/autoontology.db") == target_pin
    )


def test_corrupted_stage_cannot_fence_source(case):
    path = case[3] / "local.env"
    path.write_text("changed")
    with pytest.raises(AccessError):
        activate(case)
    assert host.read(case[1].path)["version"] == 1


def test_authenticated_application_cannot_transfer(case):
    actor = Identity(case[1]).authenticate(case[4].secret.reveal())
    with acting_as(case[1], actor), pytest.raises(AccessError) as error:
        activate(case)
    assert error.value.code == "local_owner_required"


def test_blank_rollback_cannot_reactivate_destination_or_source(case):
    activate(case)
    target = case[3] / "workspace/autoontology.db"
    backup = case[3] / "recovery/input/workspace/autoontology.db"
    target.write_bytes(backup.read_bytes())
    with pytest.raises(AccessError):
        Identity(Store(target)).status()
    with pytest.raises(AccessError):
        Identity(case[1]).status()


def test_already_open_source_transaction_is_rolled_back_at_fence(case):
    source = case[1]
    with pytest.raises(AccessError):
        with source._immediate() as conn:
            conn.execute(
                "INSERT INTO host_file_roots VALUES('/controlled',1,'pending','test','now')"
            )
            # Emulate the independently durable source fence while a transaction
            # admitted before it remains open. Normal activation requires idle source.
            marker = host.read(case[3] / "workspace/autoontology.db")
            with host.locked(source.path) as directory:
                host.write(
                    directory, source.path, {**marker, "database": host.database_path(source.path)}
                )
    with sqlite3.connect(source.path) as conn:
        assert not conn.execute("SELECT 1 FROM host_file_roots WHERE path='/controlled'").fetchone()


def test_prepared_transfer_blocks_a_competing_owner_recovery_without_pin_or_db_changes(
    case, monkeypatch
):
    from autoontology.security.owner_recovery import recover

    monkeypatch.setattr(
        transfer,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "after_prepare" else None,
    )
    with pytest.raises(RuntimeError):
        activate(case)
    marker = host.read(case[1].path)
    before = case[1].path.read_bytes()
    with pytest.raises(AccessError):
        recover(
            case[1],
            "owner",
            "another synthetic owner password",
            workspace_id=case[2]["workspace_id"],
            request_id="competing-recovery",
        )
    assert host.read(case[1].path) == marker and case[1].path.read_bytes() == before
    monkeypatch.setattr(transfer, "_boundary", lambda _: None)
    assert activate(case, resume=True)["authority_active"]


def test_transfer_preserves_pending_steps_of_historical_failed_runs(ready, tmp_path, monkeypatch):  # noqa: F811
    from tests.test_recovery_checkpoint import rehash

    root, source, enrolled, _ = ready
    with source._conn() as conn:
        conn.execute(
            "INSERT INTO runs(id,kind,status) VALUES('historical-failure','build','failed')"
        )
        conn.execute(
            "INSERT INTO steps(run_id,name,status) VALUES('historical-failure','report','pending')"
        )
    rehash(root)
    dest = tmp_path / "history-preserved"
    prepare(ready, dest)
    monkeypatch.setattr(quiescence, "check", lambda *a: {"scope": "controlled offline fixture"})
    transfer.activate(dest, "owner", NEW_PASSWORD, workspace_id=enrolled["workspace_id"])
    with Store.for_home(dest / "workspace")._conn() as conn:
        assert (
            conn.execute("SELECT status FROM steps WHERE run_id='historical-failure'").fetchone()[0]
            == "pending"
        )


def test_transfer_pauses_workers_revokes_agents_and_preserves_identity_rows(
    ready,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from tests.test_recovery_checkpoint import rehash

    root, source, enrolled, _ = ready
    identity = Identity(source)
    owner = identity.authenticate(identity.login("owner", PASSWORD).secret.reveal())
    viewer = identity.create_human(owner, "viewer", "Viewer", PASSWORD, ["viewer"])
    agent = identity.delegate(owner, "reader_agent", "Reader agent", ["knowledge.read"])
    with source._conn() as conn:
        policy = identity.status()["policy_version"]
        conn.execute(
            "INSERT INTO collection_schedules(id,source_id,source_name,source_sha256,worker_id,owner_id,interval_s,mode,enabled,policy_version,version,max_attempts,expires_at,next_at,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,60,'observe',1,?,1,3,9999999999,0,'configured',0,0)",
            (
                "1" * 32,
                "c" * 32,
                "fixture-source",
                "a" * 64,
                agent.principal["id"],
                owner.principal_id,
                policy,
            ),
        )
        conn.execute(
            "INSERT INTO collection_jobs(id,schedule_id,sequence,schedule_version,input_json,status,next_at,created_at,updated_at) VALUES(?,?,1,1,'{}','queued',0,0,0)",
            ("2" * 32, "1" * 32),
        )
        conn.execute(
            "INSERT INTO collection_workers VALUES(?,?,0)",
            (agent.principal["id"], agent.credential_id),
        )
    rehash(root)
    dest = tmp_path / "paused-restore"
    prepare(ready, dest)
    monkeypatch.setattr(quiescence, "check", lambda *a: {"scope": "controlled offline fixture"})
    transfer.activate(dest, "owner", NEW_PASSWORD, workspace_id=enrolled["workspace_id"])
    target = Identity(Store.for_home(dest / "workspace"))
    with pytest.raises(AccessError):
        target.authenticate(agent.secret.reveal())
    restored = target.authenticate(target.login("owner", NEW_PASSWORD).secret.reveal())
    assert target.require(restored, "collection.read") and target.require(restored, "ax.read")
    with target.store._conn() as conn:
        assert conn.execute("SELECT enabled FROM collection_schedules").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM collection_jobs").fetchone()[0] == "cancelled"
        assert conn.execute("SELECT count(*) FROM collection_workers").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT status FROM identity_principals WHERE id=?", (viewer["id"],)
            ).fetchone()[0]
            == "disabled"
        )
        assert conn.execute("SELECT count(*) FROM identity_principals").fetchone()[0] == 3


def test_status_reports_durable_journal_even_before_destination_marker_advances(case, monkeypatch):
    from autoontology.recovery.staging import restore_status

    monkeypatch.setattr(
        transfer,
        "_boundary",
        lambda name: (
            (_ for _ in ()).throw(RuntimeError()) if name == "after_source_fence" else None
        ),
    )
    with pytest.raises(RuntimeError):
        activate(case)
    assert host.read(case[3] / "workspace/autoontology.db")["version"] == 3
    result = restore_status(case[3])
    assert (
        result["phase"] == "pending"
        and result["source_authority_retired"]
        and not result["authority_active"]
    )


def test_identity_description_reports_read_scope_when_consumers_are_held(case):
    activate(case)
    target = Identity(Store.for_home(case[3] / "workspace"))
    actor = target.authenticate(target.login("owner", NEW_PASSWORD).secret.reveal())
    description = target.describe(actor)
    assert "knowledge.read" in description["capabilities"]
    assert "collection.read" in description["capabilities"]
    assert "query.run" not in description["capabilities"]


def test_transferred_home_supports_later_local_owner_password_recovery(case):
    from autoontology.security.owner_recovery import recover

    activate(case)
    store = Store.for_home(case[3] / "workspace")
    identity = Identity(store)
    prior = identity.login("owner", NEW_PASSWORD)
    recovered = "synthetic later owner recovery password"
    result = recover(
        store,
        "owner",
        recovered,
        workspace_id=case[2]["workspace_id"],
        request_id="after-transfer-owner-recovery",
    )
    assert result["phase"] == "active"
    assert host.read(store.path)["version"] == 4
    with pytest.raises(AccessError):
        identity.authenticate(prior.secret.reveal())
    actor = identity.authenticate(identity.login("owner", recovered).secret.reveal())
    assert identity.require(actor, "knowledge.read")
    with pytest.raises(AccessError):
        identity.require(actor, "query.run")
    assert identity.verify_audit(actor)["ok"]
