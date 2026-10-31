# SPDX-License-Identifier: MIT
"""AX measurement distinguishes elapsed time, human intervals and unmeasured savings."""

import pytest

from autoontology.ontology.model import MergedOntology
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.ax import AX
from autoontology.store.cases import Cases
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def workspace(tmp_path):
    store = Store.for_home(tmp_path)
    store.save_snapshot("base", MergedOntology("org", "https://example.test/#"))
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long AX owner password")
    owner = identity.authenticate(issued.secret.reveal())
    delegated = identity.delegate(
        owner, "preparer", "Preparer", ["candidate.read", "review.suggest", "ax.read"], ttl=600
    )
    return store, owner, identity.authenticate(delegated.secret.reveal())


def case(store):
    return Cases(store).create(title="Policy amendment", reason="Evidence changed")


def context(**updates):
    return {
        "workflow": "contract_amendment",
        "complexity": "standard",
        "effective_at": "2026-11-01",
        "required_suites": [],
        "feedback_ids": [],
        "measurement_scope": "business",
        **updates,
    }


def test_agent_can_prepare_context_but_cannot_fabricate_human_effort_or_baseline(workspace):
    store, owner, agent = workspace
    with acting_as(store, agent):
        event = case(store)
        current = AX(store).set_context(
            event["id"], context(), previous=None, reason="Prepare workflow"
        )
        assert current["payload"]["effective_at"] == "2026-11-01"
        with pytest.raises(AccessError):
            AX(store).start_effort(event["id"], note="Claim human work")
        with pytest.raises(AccessError):
            AX(store).add_baseline(
                {
                    "workflow": "contract_amendment",
                    "complexity": "standard",
                    "lead_seconds": 100,
                    "active_seconds": 50,
                    "source": "observed",
                    "reference": "Clock record",
                    "measurement_scope": "business",
                },
                confirm=True,
            )


def test_context_versions_and_case_packets_bind_required_exact_evaluation(workspace):
    store, owner, _ = workspace
    with acting_as(store, owner):
        event = case(store)
        service = AX(store)
        first = service.set_context(event["id"], context(), previous=None, reason="Initial context")
        second = service.set_context(
            event["id"],
            context(complexity="complex"),
            previous=first["id"],
            reason="Scope increased",
        )
        assert len(service.context(event["id"])["versions"]) == 2
        with pytest.raises(AccessError):
            service.set_context(event["id"], context(), previous=first["id"], reason="Stale client")
        assert service.context(event["id"])["current"]["id"] == second["id"]


def test_explicit_effort_intervals_survive_navigation_and_do_not_count_unknown_time(workspace):
    store, owner, _ = workspace
    clock = [1800000000.0]
    with acting_as(store, owner):
        event = case(store)
        service = AX(store, clock=lambda: clock[0])
        service.set_context(event["id"], context(), previous=None, reason="Measure workflow")
        timer = service.start_effort(event["id"], note="Review evidence")
        clock[0] += 120
        assert AX(store).efforts()[0]["status"] == "running"
        stopped = service.stop_effort(timer["id"], note="Review complete")
        assert stopped["elapsed_seconds"] == 120
        assert service.stop_effort(timer["id"], note="Retry") == stopped
        report = service.overview()
        assert report["cohorts"][0]["baseline"]["observed_count"] == 0
        assert report["cohorts"][0]["time_returned_seconds"] is None
        assert report["cohorts"][0]["active_cases"] == 1


def test_long_unattended_timer_requires_explicit_discard(workspace):
    store, owner, _ = workspace
    clock = [1800000000.0]
    with acting_as(store, owner):
        event = case(store)
        service = AX(store, clock=lambda: clock[0])
        timer = service.start_effort(event["id"], note="Review")
        clock[0] += 5 * 3600
        with pytest.raises(AccessError):
            service.stop_effort(timer["id"], note="Count overnight")
        assert (
            service.stop_effort(timer["id"], note="Unattended timer", discard=True)["status"]
            == "discarded"
        )


def test_reported_or_simulated_baseline_cannot_become_measured_business_savings(workspace):
    store, owner, _ = workspace
    with acting_as(store, owner):
        service = AX(store)
        event = case(store)
        service.set_context(event["id"], context(), previous=None, reason="Business cohort")
        for source, scope in [("reported", "business"), ("observed", "simulation")]:
            service.add_baseline(
                {
                    "workflow": "contract_amendment",
                    "complexity": "standard",
                    "lead_seconds": 600,
                    "active_seconds": 300,
                    "source": source,
                    "reference": "Explicit fixture",
                    "measurement_scope": scope,
                },
                confirm=True,
            )
        result = service.overview()["cohorts"][0]
        assert (
            result["baseline"]["observed_count"] == 0 and result["baseline"]["reported_count"] == 1
        )
        assert result["time_returned_seconds"] is None
        with pytest.raises(AccessError):
            service.set_target(
                {
                    "workflow": "contract_amendment",
                    "complexity": "standard",
                    "lead_reduction_percent": 20,
                    "effort_reduction_percent": 20,
                    "reason": "No measured baseline",
                },
                confirm=True,
            )


def test_baseline_correction_preserves_original_and_target_requires_exact_basis(workspace):
    store, owner, _ = workspace
    with acting_as(store, owner):
        service = AX(store)
        baseline = service.add_baseline(
            {
                "workflow": "contract_amendment",
                "complexity": "standard",
                "lead_seconds": 600,
                "active_seconds": 300,
                "source": "observed",
                "reference": "Human stopwatch attestation",
                "measurement_scope": "business",
            },
            confirm=True,
        )
        basis = service.baseline_basis("contract_amendment", "standard")
        target = service.set_target(
            {
                "workflow": "contract_amendment",
                "complexity": "standard",
                "lead_reduction_percent": 20,
                "effort_reduction_percent": 30,
                "reason": "Owner goal",
                "baseline_sha256": basis,
            },
            confirm=True,
        )
        assert target["payload"]["baseline_sha256"] == basis
        service.void_baseline(baseline["id"], reason="Wrong workflow", confirm=True)
        assert (
            service.baselines()[0]["voided"]
            and service.baselines()[0]["reference"] == "Human stopwatch attestation"
        )
        with pytest.raises(AccessError):
            service.set_target({**target["payload"]}, confirm=True)


def test_case_context_change_invalidates_exact_packet_without_review_effect(tmp_path):
    from tests import test_cases as cases_fixture

    store, _, owner, _, _, changes = cases_fixture.work.__wrapped__(tmp_path)
    with acting_as(store, owner):
        event = cases_fixture.create(store, changes, owner)
        ax = AX(store)
        profile = ax.set_context(event["id"], context(), previous=None, reason="Effective date")
        packet = Cases(store).prepare(event["id"], version=event["version"])
        ax.set_context(
            event["id"],
            context(effective_at="2026-12-01"),
            previous=profile["id"],
            reason="Later start",
        )
        assert Cases(store).get(event["id"])["packet_stale"]
        with pytest.raises(AccessError):
            Cases(store).confirm(packet["id"], packet["sha256"])
        assert not any(item["decision"] for item in Cases(store).ops.changes())


def test_feedback_handoff_is_idempotent_and_cannot_resolve_without_usable_case(workspace):
    from autoontology.store.operations import Operations

    store, owner, _ = workspace
    with acting_as(store, owner):
        feedback = Operations(store).add_feedback(
            "Who approves?", "Incorrect answer", "Evidence mismatch"
        )
        ax = AX(store)
        event = ax.feedback_case(
            feedback["id"],
            title="Correct approval",
            reason="Source checked",
            owner_id=owner.principal_id,
        )
        assert ax.feedback_case(feedback["id"], title="Retry", reason="Retry")["id"] == event["id"]
        assert ax.context(event["id"])["feedback"][0]["answer"] == "Incorrect answer"
        with pytest.raises(AccessError):
            ax.resolve_feedback(feedback["id"], event["id"], confirm=True)
        assert Operations(store).feedback()[0]["id"] == feedback["id"]


def test_baseline_request_retry_is_not_an_extra_observation(workspace):
    store, owner, _ = workspace
    with acting_as(store, owner):
        service = AX(store)
        data = {
            "request_key": "measurement-1",
            "workflow": "contracts",
            "complexity": "standard",
            "lead_seconds": 600,
            "active_seconds": 300,
            "source": "observed",
            "reference": "Timer worksheet",
            "measurement_scope": "simulation",
        }
        first = service.add_baseline(data, confirm=True)
        assert service.add_baseline(data, confirm=True) == first
        assert len(service.baselines()) == 1
        with pytest.raises(AccessError):
            service.add_baseline({**data, "active_seconds": 600}, confirm=True)


def test_recorded_effort_needs_complete_case_coverage_before_comparing_savings(tmp_path):
    from tests import test_cases as cases_fixture

    store, _, owner, _, _, changes = cases_fixture.work.__wrapped__(tmp_path)
    now = [1800000000.0]
    with acting_as(store, owner):
        service = AX(store, clock=lambda: now[0])
        cases = Cases(store)
        item = cases.ops.changes()[0]
        event = cases.create(
            title="Exclude unsupported update",
            reason="Controlled test",
            owner_id=owner.principal_id,
            changes=[{"id": item["id"], "decision": "reject", "note": "No change needed"}],
        )
        service.set_context(event["id"], context(), previous=None, reason="Comparable workflow")
        timer = service.start_effort(event["id"], note="Inspect original")
        now[0] += 60
        service.stop_effort(timer["id"], note="Done")
        packet = cases.prepare(event["id"], version=event["version"])
        receipt = cases.confirm(packet["id"], packet["sha256"])
        cases.resolve(
            event["id"], version=receipt["case_version"], reason="Excluded, no publication effect"
        )
        service.add_baseline(
            {
                "workflow": "contract_amendment",
                "complexity": "standard",
                "lead_seconds": 600,
                "active_seconds": 300,
                "source": "observed",
                "reference": "Test only synthetic baseline",
                "measurement_scope": "business",
            },
            confirm=True,
        )
        assert service.overview()["cohorts"][0]["time_returned_seconds"] is None
        service.attest_effort(
            event["id"], reason="All intervals accounted for in controlled fixture", confirm=True
        )
        measured = service.overview()["cohorts"][0]
        assert measured["assisted"]["effort"]["median"] == 60
        assert measured["time_returned_seconds"] == 240
        service.void_baseline(
            service.baselines()[0]["id"],
            reason="Synthetic fixture is not real business data",
            confirm=True,
        )
        assert service.overview()["cohorts"][0]["time_returned_seconds"] is None


def test_no_change_feedback_case_confirms_exact_disposition_and_reopens_request(workspace):
    from autoontology.store.operations import Operations

    store, owner, _ = workspace
    with acting_as(store, owner):
        ax = AX(store)
        cases = Cases(store)
        feedback = Operations(store).add_feedback(
            "Is this policy duplicated?", "Current answer", "Please verify duplication"
        )
        event = ax.feedback_case(
            feedback["id"],
            title="Duplicate policy request",
            reason="No knowledge update needed",
            owner_id=owner.principal_id,
        )
        profile = ax.context(event["id"])["current"]
        ax.set_context(
            event["id"],
            {
                **profile["payload"],
                "resolution_intent": "no_change",
                "measurement_scope": "simulation",
            },
            previous=profile["id"],
            reason="Original evidence confirms no update",
        )
        packet = cases.prepare(event["id"], version=event["version"])
        assert (
            packet["payload"]["allowed_effect"] == "case.no_change"
            and not packet["payload"]["decisions"]
        )
        with pytest.raises(AccessError):
            Operations(store).resolve_feedback(feedback["id"], False)
        receipt = cases.confirm(packet["id"], packet["sha256"])
        resolved = cases.resolve(
            event["id"],
            version=receipt["case_version"],
            reason="No knowledge change, original answer verified",
        )
        result = ax.resolve_feedback(feedback["id"], event["id"], confirm=True)
        assert result["resolved"] and not Operations(store).feedback()
        assert ax.context(event["id"])["feedback"][0]["answer"] == "Current answer"
        cases.reopen(
            event["id"], version=resolved["version"], reason="A subsequent request needs review"
        )
        assert Operations(store).feedback()[0]["id"] == feedback["id"]


def test_http_native_context_and_baseline_retry_share_the_same_records(
    workspace, isolated_env, monkeypatch, tmp_path
):
    import json
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology.cli import app as cli
    from autoontology.config import Settings
    from autoontology.security.credential_file import save_credential
    from autoontology.web.app import create_app

    store, owner, _ = workspace
    identity = Identity(store)
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(store.path.parent))
    settings = replace(Settings.from_env(), home=store.path.parent)
    issued = identity.login("owner", "long AX owner password")
    folder = store.path.parent / "security"
    folder.mkdir(mode=0o700)
    credential = folder / "cli.json"
    save_credential(
        credential,
        issued,
        workspace_id=identity.status()["workspace_id"],
        origin=settings.service_url,
    )
    with acting_as(store, owner):
        event = case(store)
    payload = tmp_path / "context.json"
    payload.write_text(json.dumps(context(), ensure_ascii=False))
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--credential-file",
            str(credential),
            "ops",
            "ax-context-set",
            event["id"],
            "--input",
            str(payload),
            "--reason",
            "Classify",
        ],
    )
    assert result.exit_code == 0, result.stdout
    headers = {"Authorization": "Bearer " + issued.secret.reveal()}
    sample = {
        "request_key": "http-sample",
        "workflow": "contract_amendment",
        "complexity": "standard",
        "lead_seconds": 600,
        "active_seconds": 300,
        "source": "reported",
        "reference": "User estimate",
        "measurement_scope": "simulation",
    }
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/ax/baselines", headers=headers, json={"payload": sample, "confirm": True}
        )
        assert response.status_code == 201, response.text
        again = client.post(
            "/api/ax/baselines", headers=headers, json={"payload": sample, "confirm": True}
        )
        assert again.json() == response.json()
        profile = client.get(f"/api/ax/cases/{event['id']}", headers=headers).json()
    native = runner.invoke(
        cli, ["--credential-file", str(credential), "ops", "ax-context", event["id"]]
    )
    assert native.exit_code == 0 and json.loads(native.stdout) == profile
    baselines = runner.invoke(cli, ["--credential-file", str(credential), "ops", "ax-baselines"])
    assert baselines.exit_code == 0 and len(json.loads(baselines.stdout)["items"]) == 1
