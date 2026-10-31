# SPDX-License-Identifier: MIT
"""Durable collection keeps identity, sequence, fencing and retry authority intact."""

import json
import time

import pytest

from autoontology.security.context import acting_as
from autoontology.security.host_files import HostFiles
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def collection(tmp_path):
    from autoontology.store.collection import Collection

    store = Store.for_home(tmp_path / "workspace")
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "collection owner password")
    owner = identity.authenticate(issued.secret.reveal())
    agent = identity.authenticate(
        identity.delegate(
            owner,
            "collector",
            "Collector",
            [
                "collection.read",
                "collection.work",
                "source.read",
                "candidate.prepare",
                "candidate.read",
            ],
            ttl=3600,
        ).secret.reveal()
    )
    folder = tmp_path / "business"
    folder.mkdir()
    (folder / "rule.md").write_text("Rule A")
    with acting_as(store, owner):
        HostFiles(store).allow(folder, reason="Business intake")
        source = store.add_source(
            name="rules", kind="documents", locator=json.dumps({"path": str(folder)})
        )
        with store._conn() as conn:
            domain = conn.execute("SELECT id FROM identity_domains LIMIT 1").fetchone()[0]
        identity.assign_source(owner, source.id, domain)
    clock = [time.time()]
    return (
        store,
        identity,
        owner,
        agent,
        source,
        folder,
        Collection(store, clock=lambda: clock[0]),
        clock,
    )


def configure(work, **kw):
    store, _, owner, agent, source, _, service, _ = work
    with acting_as(store, owner):
        return service.configure(
            source.name,
            worker_id=agent.principal_id,
            interval=60,
            mode="observe",
            reason="Observe approved business source",
            **kw,
        )


def test_deduplicated_poll_and_aba_are_distinct_events(collection):
    store, _, _, agent, _, folder, service, _ = collection
    schedule = configure(collection)
    with acting_as(store, agent):
        first = service.observe(schedule["id"], {"sha256": "a", "files": 1})
        assert service.observe(schedule["id"], {"sha256": "a", "files": 1})["id"] == first["id"]
        second = service.observe(schedule["id"], {"sha256": "b", "files": 1})
        third = service.observe(schedule["id"], {"sha256": "a", "files": 1})
        assert len({first["id"], second["id"], third["id"]}) == 3
        assert third["sequence"] == 3
        claim = service.claim()
        assert claim["id"] == third["id"]
        assert service.claim() is None


def test_lease_expiry_fences_old_completion_and_backoff_survives_restart(collection):
    from autoontology.store.collection import Collection

    store, _, _, agent, _, _, service, clock = collection
    schedule = configure(collection)
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
        first = service.claim()
        clock[0] += 121
        assert service.claim() is None
        clock[0] += 61
        restarted = Collection(store, clock=lambda: clock[0])
        second = restarted.claim()
        assert second and second["attempt"] == 2 and second["lease_id"] != first["lease_id"]
        with pytest.raises(AccessError):
            service.finish(first["id"], first["lease_id"], {"ok": True})
        done = restarted.finish(second["id"], second["lease_id"], {"observed": True})
        assert done["status"] == "succeeded"
        assert restarted.finish(second["id"], second["lease_id"], {"observed": True}) == done


def test_revocation_or_policy_drift_blocks_effect_and_creates_owned_notice(collection):
    store, identity, owner, agent, _, _, service, _ = collection
    schedule = configure(collection)
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
        job = service.claim()
    with acting_as(store, owner):
        identity.revoke_delegation(owner, agent.principal_id)
    with acting_as(store, owner):
        with pytest.raises(AccessError):
            service.finish(job["id"], job["lease_id"], {"ok": True})
        assert {n["kind"] for n in service.notices()} == {"change_detected"}
        service.pause(schedule["id"], reason="Stop revoked worker")
        assert service.get(schedule["id"])["enabled"] == 0


def test_pause_and_cancel_stop_running_generation_and_never_publish(collection):
    store, _, owner, agent, _, _, service, _ = collection
    schedule = configure(collection)
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
        job = service.claim()
    with acting_as(store, owner):
        service.cancel(job["id"], reason="Human stop")
    with acting_as(store, agent), pytest.raises(AccessError):
        service.check(job["id"], job["lease_id"])
    with acting_as(store, owner):
        assert service.jobs()[0]["status"] == "cancelled"
        assert not store.list_snapshots()
        assert service.history(schedule["id"])


def test_only_human_can_configure_and_worker_assignment_is_enforced(collection):
    store, _, owner, agent, source, _, service, _ = collection
    with acting_as(store, agent), pytest.raises(AccessError):
        service.configure(
            source.name,
            worker_id=agent.principal_id,
            interval=60,
            mode="observe",
            reason="Agent expansion",
        )
    schedule = configure(collection)
    with acting_as(store, owner), pytest.raises(AccessError):
        service.observe(schedule["id"], {"sha256": "a"})


def test_retry_terminal_failure_is_explicit_and_preserves_prior_attempts(collection):
    store, _, owner, agent, _, _, service, clock = collection
    schedule = configure(collection, max_attempts=1)
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
        job = service.claim()
        failed = service.fail(job["id"], job["lease_id"], "input_unavailable")
        assert failed["status"] == "failed"
    with acting_as(store, owner):
        notice = service.notices()[0]
        assert notice["owner_id"] == owner.principal_id
        service.ack(notice["id"])
        assert service.notices()[0]["acknowledged_at"]
        service.retry(job["id"], reason="Access restored")
    with acting_as(store, agent):
        retried = service.claim()
        assert retried["attempt"] == 1 and retried["generation"] > job["generation"]
        service.finish(retried["id"], retried["lease_id"], {"observed": True})
    with acting_as(store, owner):
        assert len(service.history(schedule["id"])) >= 6


def test_real_folder_observation_detects_add_move_duplicate_and_removal(collection):
    from autoontology.engine.collection import observe_source

    store, _, _, agent, source, folder, service, _ = collection
    configure(collection)
    with acting_as(store, agent):
        first = observe_source(store, source)
        assert first["files"] == 1
        (folder / "rule.md").rename(folder / "renamed.md")
        second = observe_source(store, source)
        assert first["sha256"] != second["sha256"]
        (folder / "copy.md").write_bytes((folder / "renamed.md").read_bytes())
        third = observe_source(store, source)
        assert third["duplicate_contents"] == 1
        (folder / "renamed.md").unlink()
        fourth = observe_source(store, source)
        assert fourth["files"] == 1 and fourth["sha256"] != third["sha256"]


def test_worker_scans_without_model_and_same_input_is_not_requeued(collection):
    from autoontology.engine.collection import cycle

    store, _, _, agent, source, folder, service, clock = collection
    configure(collection)
    with acting_as(store, agent):
        done = cycle(store, None, collection=service)
        assert done["job"]["status"] == "succeeded" and done["job"]["result"]["mode"] == "observe"
        clock[0] += 61
        assert cycle(store, None, collection=service)["job"] is None
        assert len(service.jobs()) == 1 and not store.list_snapshots()
    with acting_as(store, collection[2]):
        assert len(service.notices()) == 2


def test_completed_preparation_recovered_without_repeating_effect(collection):
    from types import SimpleNamespace

    from autoontology.engine.collection import cycle
    from autoontology.ontology.model import MergedOntology

    store, _, owner, agent, source, _, service, clock = collection
    with acting_as(store, owner):
        service.configure(
            source.name,
            worker_id=agent.principal_id,
            interval=60,
            mode="prepare",
            reason="Candidate only",
        )
    calls = []

    def build(guarded, settings, **kw):
        calls.append(True)
        run = guarded.create_run(kind="build", source_id=None, params={})
        guarded.save_snapshot(run.id, MergedOntology("Org", "https://example.test/#"))
        guarded.update_run(run.id, status="succeeded")
        return SimpleNamespace(run_id=run.id, status="succeeded")

    real_finish = service.finish

    def crash(*a, **kw):
        raise SystemExit(73)

    service.finish = crash
    with acting_as(store, agent), pytest.raises(SystemExit):
        cycle(store, None, collection=service, builder=build)
    service.finish = real_finish
    clock[0] += 121
    with acting_as(store, agent):
        assert service.claim() is None
    clock[0] += 61
    with acting_as(store, agent):
        result = cycle(store, None, collection=service, builder=build)
        assert (
            result["job"]["status"] == "succeeded"
            and result["job"]["result"]["recovered_completed_run"]
        )
        assert len(calls) == 1 and len(store.list_snapshots()) == 1


def test_expired_schedule_and_changed_workspace_cannot_prepare(collection):
    store, _, owner, agent, source, folder, service, clock = collection
    with acting_as(store, owner):
        schedule = service.configure(
            source.name,
            worker_id=agent.principal_id,
            interval=60,
            mode="prepare",
            reason="Exact registered source set",
            ttl=60,
        )
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
    clock[0] += 61
    with acting_as(store, agent):
        assert service.claim() is None
        assert service.jobs()[0]["error_code"] == "collection_paused"
    with acting_as(store, owner):
        service.resume(schedule["id"], reason="Renew exact scope")
        service.retry(service.jobs()[0]["id"], reason="Retry after renewal")
        store.add_source(name="added", kind="documents", locator=json.dumps({"path": str(folder)}))
    with acting_as(store, agent):
        assert service.claim() is None
        assert service.jobs()[0]["error_code"] == "collection_source_changed"


def test_running_job_fences_every_participating_store_write_after_cancel(collection):
    from autoontology.engine.collection import JobStore
    from autoontology.ontology.model import MergedOntology

    store, _, owner, agent, _, _, service, _ = collection
    schedule = configure(collection)
    with acting_as(store, agent):
        service.observe(schedule["id"], {"sha256": "a"})
        job = service.claim()
        guarded = JobStore(store, service, job)
        run = guarded.create_run(kind="build", source_id=None)
    with acting_as(store, owner):
        service.cancel(job["id"], reason="Stop before candidate write")
    with acting_as(store, agent), pytest.raises(AccessError):
        guarded.save_snapshot(run.id, MergedOntology("Org", "https://example.test/#"))
    assert store.list_snapshots() == []


def test_http_confirmation_and_native_read_share_current_state(collection, monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.config import Settings
    from autoontology.web.app import create_app

    store, identity, owner, agent, source, _, service, _ = collection
    settings = replace(Settings.from_env(), home=store.path.parent)
    login = identity.login("owner", "collection owner password")
    headers = {"Authorization": "Bearer " + login.secret.reveal()}
    client = TestClient(create_app(settings), client=("127.0.0.1", 50000))
    assert client.get("/api/collection").status_code == 401
    body = {
        "source": source.name,
        "worker_id": agent.principal_id,
        "reason": "Observe only",
        "confirm": False,
    }
    assert client.post("/api/collection", json=body, headers=headers).status_code == 400
    body["confirm"] = True
    created = client.post("/api/collection", json=body, headers=headers)
    assert created.status_code == 200, created.text
    assert client.get("/api/collection/summary", headers=headers).json()["active"] == 1
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    unauthenticated = CliRunner().invoke(cli.app, ["ops", "collection"])
    assert unauthenticated.exit_code != 0
    with acting_as(store, owner):
        result = CliRunner().invoke(cli.app, ["ops", "collection"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schedules"][0]["id"] == created.json()["id"]
