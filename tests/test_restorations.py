# SPDX-License-Identifier: MIT
"""Managed restoration creates a revalidated publication without rewriting history."""

from copy import deepcopy

import pytest

from autoontology.engine.operations import run_publish, validate
from autoontology.engine.restorations import RestoreError, prepare_restore, run_restore
from autoontology.store.operations import Operations
from autoontology.store.publications import PublicationError, Publications
from autoontology.store.restorations import Restorations
from tests import test_publication_manifest as publication


@pytest.fixture
def history(tmp_path):
    prepared = publication.prepared.__wrapped__(tmp_path)
    store, settings, _, _, _ = prepared
    first = publication.publish(prepared).revision
    snapshot = store.get_snapshot(first)
    model = Operations(store).model(first)
    model.classes["concept-rule"].definitions = ["Revised approval policy"]
    run = store.create_run(kind="build", source_id=None)
    candidate = store.save_snapshot(run.id, model, sources=snapshot.sources)
    store.update_run(run.id, status="succeeded")
    ops = Operations(store)
    for item in ops.changes():
        ops.decide(item["id"], "accept", candidate=candidate, note="Reviewed amendment")
    checked = validate(
        store,
        settings,
        revision=candidate,
        embedder=(publication.embed, "test"),
        llm=publication.Answer(),
    )
    current = run_publish(
        store,
        settings,
        revision=candidate,
        token=checked["token"],
        embedder=(publication.embed, "test"),
    ).revision
    return store, settings, first, current, candidate


def prepare(history, **options):
    store, settings, target, _, _ = history
    return prepare_restore(
        store,
        settings,
        revision=target,
        reason="Restore the previously approved policy",
        embedder=(publication.embed, "test"),
        llm=publication.Answer(),
        **options,
    )


def execute(history, packet, **options):
    store, settings, _, _, _ = history
    return run_restore(
        store,
        settings,
        proposal=packet["id"],
        confirmation=packet["confirmation"],
        embedder=(publication.embed, "test"),
        **options,
    )


def test_preparation_shows_exact_impact_without_switching_the_head(history):
    store, _, target, current, _ = history
    packet = prepare(history)
    assert packet["status"] == "ready" and packet["packet"]["validation"]["ok"]
    assert (
        packet["packet"]["target_revision"] == target
        and packet["packet"]["base_revision"] == current
    )
    assert packet["packet"]["diff"]["classes"]["totals"]["changed"] == 1
    assert packet["packet"]["sql_data"] == "live_at_execution; database rows are not restored"
    assert Operations(store).state()["published_revision"] == current


def test_restore_needs_explicit_packet_confirmation_and_produces_a_new_publication(history):
    store, settings, target, current, candidate = history
    packet = prepare(history)
    with pytest.raises(RestoreError, match="confirmation"):
        run_restore(
            store,
            settings,
            proposal=packet["id"],
            confirmation="",
            embedder=(publication.embed, "test"),
        )
    before = deepcopy(store.get_snapshot(current))
    result = execute(history, packet)
    assert result.revision > current
    assert store.get_snapshot(result.revision).model == store.get_snapshot(target).model
    assert store.get_snapshot(current) == before
    assert Operations(store).state()["published_revision"] == result.revision
    assert Operations(store).state()["candidate_revision"] == candidate
    manifest = Publications(store).get(result.revision)["manifest"]
    assert manifest["restoration"]["target_revision"] == target
    assert manifest["previous_revision"] == current
    assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]
    assert Restorations(store).get(packet["id"])["status"] == "committed"


def test_duplicate_restore_returns_the_existing_receipt_without_another_snapshot(history):
    store, _, _, _, _ = history
    packet = prepare(history)
    first = execute(history, packet)
    count = len(store.list_snapshots())
    second = execute(history, packet)
    assert first.revision == second.revision and second.already_committed
    assert len(store.list_snapshots()) == count


def test_changed_checks_and_expired_packets_cannot_restore(history, monkeypatch):
    from datetime import timedelta

    from autoontology.engine import restorations

    store, _, _, current, _ = history
    packet = prepare(history)
    Operations(store).add_check("Another check", "Expected")
    with pytest.raises(RestoreError, match="changed"):
        execute(history, packet)
    with store._conn() as conn:
        conn.execute("DELETE FROM ops_checks WHERE question='Another check'")
    future = restorations.utc_now() + timedelta(hours=1)
    monkeypatch.setattr(restorations, "utc_now", lambda: future)
    with pytest.raises(RestoreError, match="expired"):
        execute(history, packet)
    assert Operations(store).state()["published_revision"] == current


def test_missing_questions_and_unpublished_target_never_restore(history):
    store, settings, _, current, candidate = history
    with pytest.raises(RestoreError, match="published"):
        prepare_restore(store, settings, revision=candidate, reason="Cannot restore a candidate")
    with store._conn() as conn:
        conn.execute("DELETE FROM ops_checks")
    packet = prepare(history)
    assert packet["status"] == "blocked" and not packet["packet"]["validation"]["ok"]
    with pytest.raises(RestoreError, match="blocked"):
        execute(history, packet)
    assert Operations(store).state()["published_revision"] == current


def test_target_artifact_tampering_and_changed_validation_index_block_restore(history):
    store, settings, target, current, _ = history
    packet = prepare(history)
    with store._conn() as conn:
        conn.execute(
            "UPDATE playground_index SET embedding=NULL WHERE scope=?",
            (packet["packet"]["validation"]["scope"],),
        )
    with pytest.raises(RestoreError, match="index"):
        execute(history, packet)
    path = settings.exports_dir / f"r{target:04}" / "ontology.ttl"
    path.write_text("corrupt")
    with pytest.raises(RestoreError, match="artifact"):
        prepare(history)
    assert Operations(store).state()["published_revision"] == current


def test_failed_preparation_keeps_old_head_and_can_retry_the_same_packet(history):
    store, settings, _, current, _ = history
    packet = prepare(history)

    def broken(*args):
        raise RuntimeError("artifact preparation failed")

    with pytest.raises(RuntimeError):
        execute(history, packet, exporter=broken)
    assert Operations(store).state()["published_revision"] == current
    assert Restorations(store).get(packet["id"])["status"] == "ready"
    restored = execute(history, packet)
    assert Publications(store).verify(restored.revision, settings.exports_dir)["ok"]


def test_post_commit_exception_preserves_receipt_and_retry_is_idempotent(history, monkeypatch):
    store, _, _, _, _ = history
    packet = prepare(history)
    real = Operations.commit

    def lost(self, *args, **kwargs):
        real(self, *args, **kwargs)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(Operations, "commit", lost)
    with pytest.raises(RuntimeError, match="response lost"):
        execute(history, packet)
    receipt = Restorations(store).get(packet["id"])
    assert receipt["status"] == "committed"
    result = execute(history, packet)
    assert result.revision == receipt["result_revision"] and result.already_committed
    assert store.get_run(result.run_id).status == "succeeded"


def test_cli_and_api_share_packet_execution_and_idempotent_receipts(history, monkeypatch):
    import json
    import time

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.engine import restorations
    from autoontology.web.app import create_app

    store, settings, target, _, _ = history
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    real_prepare, real_execute = restorations.prepare_restore, restorations.run_restore
    monkeypatch.setattr(
        restorations,
        "prepare_restore",
        lambda *args, **kw: real_prepare(
            *args, **kw, embedder=(publication.embed, "test"), llm=publication.Answer()
        ),
    )
    monkeypatch.setattr(
        restorations,
        "run_restore",
        lambda *args, **kw: real_execute(*args, **kw, embedder=(publication.embed, "test")),
    )
    output = CliRunner().invoke(
        cli.app,
        [
            "ops",
            "restore-prepare",
            "--revision",
            str(target),
            "--reason",
            "Revert an incorrect policy",
        ],
    )
    assert output.exit_code == 0, output.output
    packet = json.loads(output.output)
    client = TestClient(create_app(settings))
    assert client.get("/api/ops/restores/" + packet["id"]).json() == packet
    blocked = client.post(
        "/api/ops/restores/" + packet["id"] + "/execute", json={"confirmation": "wrong"}
    )
    assert blocked.status_code == 409
    accepted = client.post(
        "/api/ops/restores/" + packet["id"] + "/execute",
        json={"confirmation": packet["confirmation"]},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    for _ in range(100):
        run = store.get_run(run_id)
        if run.status != "running":
            break
        time.sleep(0.02)
    assert run.status == "succeeded"
    again = client.post(
        "/api/ops/restores/" + packet["id"] + "/execute",
        json={"confirmation": packet["confirmation"]},
    )
    assert again.json()["already_committed"] and again.json()["run_id"] == run_id
    output = CliRunner().invoke(
        cli.app, ["ops", "restore", "--proposal", packet["id"], "--confirm", packet["confirmation"]]
    )
    assert output.exit_code == 0 and json.loads(output.output)["already_committed"]


def test_legacy_operational_knowledge_is_revalidated_without_fabricating_old_validation(history):
    store, settings, target, current, _ = history
    # Simulate an operational version created before publication manifests existed.
    with store._conn() as conn:
        conn.execute("DELETE FROM publications WHERE revision=?", (target,))
    packet = prepare(history)
    assert packet["packet"]["target"]["publication_id"] is None
    result = execute(history, packet)
    manifest = Publications(store).get(result.revision)["manifest"]
    assert manifest["restoration"]["target_publication_id"] is None
    assert manifest["validation"]["questions"] and manifest["previous_revision"] == current
    assert Publications(store).verify(result.revision, settings.exports_dir)["ok"]


@pytest.mark.parametrize("after_commit", [False, True])
def test_restore_survives_real_process_death_at_the_publication_boundary(history, after_commit):
    import subprocess
    import sys
    from pathlib import Path

    store, settings, _, old, _ = history
    packet = prepare(history)
    script = """
import os,sys
from dataclasses import replace
from pathlib import Path
from autoontology.config import Settings
from autoontology.store.db import Store
from autoontology.store.operations import Operations
from autoontology.engine.restorations import run_restore
from tests.test_publication_manifest import embed
settings=replace(Settings.from_env(),home=Path(sys.argv[1]),exports_dir=Path(sys.argv[2]))
store=Store.for_home(settings.home)
real=Operations.commit
def die(self,*args,**kwargs):
    if sys.argv[5]=='True':real(self,*args,**kwargs)
    os._exit(73)
Operations.commit=die
run_restore(store,settings,proposal=sys.argv[3],confirmation=sys.argv[4],embedder=(embed,'test'))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(settings.home),
            str(settings.exports_dir),
            packet["id"],
            packet["confirmation"],
            str(after_commit),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 73, child.stderr
    listing = Restorations(store).listing()
    current = Operations(store).state()["published_revision"]
    if after_commit:
        assert current != old and listing[0]["status"] == "committed"
        assert execute(history, packet).already_committed
    else:
        assert current == old and listing[0]["status"] == "ready"
        retried = execute(history, packet)
        assert retried.revision > old
    assert Publications(store).verify(
        Operations(store).state()["published_revision"], settings.exports_dir
    )["ok"]


def test_changed_profile_and_tampered_packet_cannot_execute(history):
    import json

    store, settings, _, current, _ = history
    packet = prepare(history)
    with pytest.raises(RestoreError, match="changed"):
        run_restore(
            store,
            settings,
            proposal=packet["id"],
            confirmation=packet["confirmation"],
            embedder=(publication.embed, "other-model"),
        )
    with store._conn() as conn:
        altered = {**packet["packet"], "reason": "Different reason"}
        conn.execute(
            "UPDATE restore_proposals SET packet_json=? WHERE id=?",
            (json.dumps(altered), packet["id"]),
        )
    with pytest.raises(RestoreError, match="integrity"):
        execute(history, packet)
    assert Operations(store).state()["published_revision"] == current


def test_provider_failure_has_a_durable_failed_packet_without_sensitive_error_text(history):
    from autoontology.playground.llm import LLMError

    store, settings, target, current, _ = history

    class Broken:
        async def stream(self, messages):
            raise LLMError(503, "provider_unavailable", "private provider body")
            yield

    with pytest.raises(RestoreError, match="provider login"):
        prepare_restore(
            store,
            settings,
            revision=target,
            reason="Provider unavailable",
            embedder=(publication.embed, "test"),
            llm=Broken(),
        )
    packet = Restorations(store).listing()[0]
    assert packet["status"] == "failed" and "private provider body" not in str(packet)
    assert Operations(store).state()["published_revision"] == current


def test_expired_api_execution_returns_a_conflict_code_without_an_internal_error(
    history, monkeypatch
):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from autoontology.engine import restorations
    from autoontology.web.app import create_app

    store, settings, _, current, _ = history
    packet = prepare(history)
    real = restorations.run_restore
    monkeypatch.setattr(
        restorations,
        "run_restore",
        lambda *a, **kw: real(*a, **kw, embedder=(publication.embed, "test")),
    )
    future = restorations.utc_now() + timedelta(hours=1)
    monkeypatch.setattr(restorations, "utc_now", lambda: future)
    response = TestClient(create_app(settings)).post(
        "/api/ops/restores/" + packet["id"] + "/execute",
        json={"confirmation": packet["confirmation"]},
    )
    assert response.status_code == 409 and response.json()["error"] == "restore_expired"
    assert Operations(store).state()["published_revision"] == current


def test_restored_export_metadata_is_bound_by_the_manifest(history):
    import json

    store, settings, _, _, _ = history
    restored = execute(history, prepare(history))
    marker = settings.exports_dir / f"r{restored.revision:04}" / "export.json"
    data = json.loads(marker.read_text())
    data["upstream_fetched_at"] = "forged"
    marker.write_text(json.dumps(data))
    with pytest.raises(PublicationError, match="artifact receipt"):
        Publications(store).verify(restored.revision, settings.exports_dir)


def test_confirmation_expiring_during_export_preparation_cannot_commit(history, monkeypatch):
    from datetime import timedelta

    from autoontology.engine import restorations
    from autoontology.engine.operations import write_exports

    store, _, _, current, _ = history
    packet = prepare(history)
    future = restorations.utc_now() + timedelta(hours=1)

    def slow_export(model, folder, revision, run):
        write_exports(model, folder, revision, run)
        monkeypatch.setattr(restorations, "utc_now", lambda: future)

    with pytest.raises(RestoreError, match="expired"):
        execute(history, packet, exporter=slow_export)
    assert Operations(store).state()["published_revision"] == current
    assert Restorations(store).get(packet["id"])["status"] == "ready"


def test_restoration_packet_contains_actual_changed_values_and_pinned_originals(history):
    packet = prepare(history)["packet"]
    change = next(c for c in packet["changes"] if c["key"] == "concept-rule")
    field = next(f for f in change["fields"] if f["name"] == "definitions")
    assert field["before"] == ["Revised approval policy"] and field["after"] == []
    assert packet["originals"][0]["original"]["url"].endswith("revision=2")
    assert packet["originals"][0]["original"]["recover_url"] is None


def test_failed_restore_snapshot_is_not_an_operational_revision(history):
    from fastapi.testclient import TestClient

    from autoontology.web.app import create_app

    store, settings, _, current, _ = history

    def broken(*args):
        raise RuntimeError("failed export")

    with pytest.raises(RuntimeError):
        execute(history, prepare(history), exporter=broken)
    listing = TestClient(create_app(settings)).get("/api/ontology/revisions").json()["revisions"]
    failed = next(r for r in listing if r["run_kind"] == "restore")
    assert failed["operational"] is False and failed["run_status"] == "failed"
    assert Operations(store).state()["published_revision"] == current


def test_cli_can_cancel_a_service_restore_before_the_atomic_switch(history, monkeypatch):
    import json
    import threading
    import time

    import httpx
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.engine import restorations
    from autoontology.engine.operations import write_exports
    from autoontology.web.app import create_app

    store, settings, _, current, _ = history
    packet = prepare(history)
    entered, release = threading.Event(), threading.Event()

    def wait_before_publish(model, folder, revision, run):
        write_exports(model, folder, revision, run)
        entered.set()
        assert release.wait(10)

    real = restorations.run_restore
    monkeypatch.setattr(
        restorations,
        "run_restore",
        lambda *a, **kw: real(
            *a, **kw, embedder=(publication.embed, "test"), exporter=wait_before_publish
        ),
    )
    client = TestClient(create_app(settings))
    accepted = client.post(
        "/api/ops/restores/" + packet["id"] + "/execute",
        json={"confirmation": packet["confirmation"]},
    )
    run_id = accepted.json()["run_id"]
    assert entered.wait(5)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(
        httpx, "request", lambda method, url, **kw: client.request(method, url, json=kw.get("json"))
    )
    try:
        response = CliRunner().invoke(cli.app, ["ops", "execution", run_id, "--cancel"])
        assert response.exit_code == 0 and json.loads(response.output)["cancel_requested"]
    finally:
        release.set()
    for _ in range(200):
        status = store.get_run(run_id).status
        if status not in ("running", "pending"):
            break
        time.sleep(0.02)
    assert status == "cancelled"
    assert Operations(store).state()["published_revision"] == current
    assert Restorations(store).get(packet["id"])["status"] == "ready"


def test_duplicate_receipt_with_a_missing_publication_never_reexecutes(history):
    store, _, _, _, _ = history
    packet = prepare(history)
    done = execute(history, packet)
    with store._conn() as conn:
        conn.execute("DELETE FROM publications WHERE revision=?", (done.revision,))
    count = len(store.list_snapshots())
    with pytest.raises(RestoreError, match="publication is missing"):
        execute(history, packet)
    assert len(store.list_snapshots()) == count
