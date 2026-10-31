# SPDX-License-Identifier: MIT
"""A business case binds an exact review packet to a current human and atomic effects."""

from copy import deepcopy

import pytest

from autoontology.ontology.model import MergedOntology, OClass, OProperty
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.cases import Cases
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.store.operations import Operations
from tests import test_publication_manifest as publication_cases

prepared = publication_cases.prepared


@pytest.fixture
def work(tmp_path):
    store = Store.for_home(tmp_path / "workspace")
    model = MergedOntology("org", "https://example.test/#", structured_sources=["contracts"])
    model.classes["term-existing"] = OClass(
        "term-existing", "Existing", "structured", structured_source="contracts"
    )
    store.save_snapshot("base", model)
    ops = Operations(store)
    candidate = deepcopy(model)
    candidate.classes["term-new"] = OClass(
        "term-new", "Contract", "structured", structured_source="contracts"
    )
    candidate.properties["attr-amount"] = OProperty(
        "attr-amount",
        "datatype",
        "Amount",
        "term-new",
        "xsd:decimal",
        "structured",
        owner="contracts",
    )
    revision = store.save_snapshot("candidate", candidate)
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long case owner password")
    owner = identity.authenticate(issued.secret.reveal())
    agent = identity.authenticate(
        identity.delegate(
            owner, "preparer", "Preparer", ["candidate.read", "review.suggest"], ttl=600
        ).secret.reveal()
    )
    changes = {c["key"]: c["id"] for c in ops.changes()}
    return store, identity, owner, agent, revision, changes


def create(store, changes, owner):
    return Cases(store).create(
        title="Contract policy amendment",
        reason="One business event",
        owner_id=owner.principal_id,
        changes=[{"id": changes["attr-amount"], "decision": "accept", "note": "Amount reviewed"}],
    )


def test_agent_prepares_dependency_complete_packet_and_human_confirms_once(work):
    store, _, owner, agent, revision, changes = work
    service = Cases(store)
    with acting_as(store, agent):
        case = create(store, changes, owner)
        packet = service.prepare(
            case["id"], version=case["version"], executor_id=owner.principal_id
        )
        assert {p["id"] for p in packet["payload"]["decisions"]} == set(changes.values())
        assert packet["payload"]["dependencies"]
        with pytest.raises(AccessError):
            service.confirm(packet["id"], packet["sha256"])
    with acting_as(store, owner):
        receipt = service.confirm(packet["id"], packet["sha256"])
        assert service.confirm(packet["id"], packet["sha256"]) == receipt
        assert service.get(case["id"])["status"] == "reviewed"
        assert len(service.history(case["id"])) == 3
        assert all(c["decision"]["decision"] == "accept" for c in Operations(store).changes())
        assert receipt["candidate_revision"] == revision


@pytest.mark.parametrize("drift", ["policy", "candidate", "case", "expired"])
def test_stale_packet_cannot_apply_any_review_decision(work, drift):
    store, identity, owner, _, _, changes = work
    clock = [1800000000.0]
    service = Cases(store, clock=lambda: clock[0])
    with acting_as(store, owner):
        case = create(store, changes, owner)
        packet = service.prepare(case["id"], version=case["version"], ttl=30)
        if drift == "policy":
            identity.create_domain(owner, "new", "New")
        elif drift == "candidate":
            store.save_snapshot("changed-candidate", Operations(store).model(2))
        elif drift == "case":
            service.update(
                case["id"],
                version=packet["payload"]["case_version"],
                reason="Revised business reason",
            )
        else:
            clock[0] += 31
        with pytest.raises(AccessError):
            service.confirm(packet["id"], packet["sha256"])
        assert not any(c["decision"] for c in Operations(store).changes())


def test_multi_decision_failure_rolls_back_effects_packet_and_events(work, monkeypatch):
    store, _, owner, _, _, changes = work
    service = Cases(store)
    original = Operations.decide
    count = []

    def fail_second(self, *args, **kwargs):
        count.append(True)
        if len(count) == 2:
            raise RuntimeError("controlled second decision failure")
        return original(self, *args, **kwargs)

    with acting_as(store, owner):
        case = create(store, changes, owner)
        packet = service.prepare(case["id"], version=case["version"])
        monkeypatch.setattr(Operations, "decide", fail_second)
        with pytest.raises(RuntimeError):
            service.confirm(packet["id"], packet["sha256"])
        assert service.get(case["id"])["status"] == "awaiting_review"
        assert not any(c["decision"] for c in Operations(store).changes())
        monkeypatch.setattr(Operations, "decide", original)
        assert service.confirm(packet["id"], packet["sha256"])["status"] == "reviewed"


def test_case_creation_retry_and_relations_preserve_business_identity(work):
    store, _, owner, _, _, changes = work
    service = Cases(store)
    with acting_as(store, owner):
        data = dict(
            title="Amendment",
            reason="One input event",
            request_key="same-business-event",
            changes=[
                {"id": changes["term-new"], "decision": "hold", "note": "Need policy evidence"}
            ],
        )
        first = service.create(**data)
        assert service.create(**data)["id"] == first["id"]
        with pytest.raises(AccessError):
            service.create(**{**data, "title": "Different event"})
        second = service.create(
            title="Follow-up",
            reason="Another event",
            links=[{"relation": "depends_on", "case_id": first["id"]}],
        )
        with pytest.raises(AccessError):
            service.update(
                first["id"],
                version=first["version"],
                links=[{"relation": "depends_on", "case_id": second["id"]}],
            )
        assert service.summary()["unassigned"] == 2


def test_resolution_links_exact_committed_decisions_and_reopens_without_erasing_history(prepared):
    from autoontology.engine.operations import run_publish, validate

    store, settings, revision, _, _ = prepared
    identity = Identity(store)
    issued = identity.bootstrap("Org", "owner", "Owner", "long publication case password")
    owner = identity.authenticate(issued.secret.reveal())
    service = Cases(store)
    with acting_as(store, owner):
        case = service.create(
            title="Approval rule amendment",
            reason="A real business case",
            owner_id=owner.principal_id,
            changes=[
                {"id": item["id"], "decision": "accept", "note": "Evidence checked"}
                for item in Operations(store).changes()
            ],
        )
        packet = service.prepare(case["id"], version=case["version"])
        receipt = service.confirm(packet["id"], packet["sha256"])
        with pytest.raises(AccessError):
            service.resolve(
                case["id"],
                version=receipt["case_version"],
                reason="Not yet published",
                publication=revision,
            )
        check = validate(
            store,
            settings,
            revision=revision,
            embedder=(publication_cases.embed, "test"),
            llm=publication_cases.Answer(),
        )
        result = run_publish(
            store,
            settings,
            revision=revision,
            token=check["token"],
            embedder=(publication_cases.embed, "test"),
        )
        resolved = service.resolve(
            case["id"],
            version=receipt["case_version"],
            reason="Rule operational",
            publication=result.revision,
        )
        assert resolved["status"] == "resolved" and resolved["outcome"]["publication_id"]
        assert (
            service.resolve(
                case["id"],
                version=receipt["case_version"],
                reason="Retry",
                publication=result.revision,
            )
            == resolved
        )
        reopened = service.reopen(
            case["id"], version=resolved["version"], reason="New policy event"
        )
        assert reopened["status"] == "open" and not reopened["outcome"]
        assert any(event["operation"] == "resolved" for event in service.history(case["id"]))
        assert store.get_snapshot(result.revision).model["decisions"]


def test_rebase_clears_only_its_own_unpublished_decisions_with_append_only_undo(work):
    store, _, owner, _, _, changes = work
    service = Cases(store)
    with acting_as(store, owner):
        case = create(store, changes, owner)
        packet = service.prepare(case["id"], version=case["version"])
        receipt = service.confirm(packet["id"], packet["sha256"])
        revised = service.rebase(
            case["id"],
            version=receipt["case_version"],
            reason="Recompare proposed amount",
            changes=case["changes"],
        )
        assert revised["status"] == "preparing"
        assert not any(item["decision"] for item in Operations(store).changes())
        with store._conn() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM ops_review_events WHERE operation='undo'"
                ).fetchone()[0]
                == 2
            )


def test_http_and_native_share_case_confirmation_and_machine_errors(
    work, isolated_env, monkeypatch, tmp_path
):
    import json
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autoontology.cli import app as cli
    from autoontology.config import Settings
    from autoontology.security.credential_file import save_credential
    from autoontology.web.app import create_app

    store, identity, owner, agent, _, changes = work
    settings = replace(Settings.from_env(), home=store.path.parent)
    issued = identity.login("owner", "long case owner password")
    folder = store.path.parent / "security"
    folder.mkdir(mode=0o700)
    credential = folder / "cli.json"
    save_credential(
        credential,
        issued,
        workspace_id=identity.status()["workspace_id"],
        origin=settings.service_url,
    )
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(store.path.parent))
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + issued.secret.reveal()}
        result = client.post(
            "/api/cases",
            headers=headers,
            json={
                "title": "Native/HTTP case",
                "reason": "Same service",
                "owner_id": owner.principal_id,
                "changes": [{"id": changes["attr-amount"], "decision": "accept"}],
            },
        )
        assert result.status_code == 201, result.text
        case = result.json()
        result = client.post(
            f"/api/cases/{case['id']}/prepare", headers=headers, json={"version": case["version"]}
        )
        assert result.status_code == 200, result.text
        packet = result.json()
        assert (
            client.post(
                f"/api/cases/packets/{packet['id']}/confirm",
                headers=headers,
                json={"sha256": packet["sha256"]},
            ).status_code
            == 400
        )
        args = [
            "--credential-file",
            str(credential),
            "ops",
            "case-confirm",
            packet["id"],
            "--sha256",
            packet["sha256"],
        ]
        denied = CliRunner().invoke(cli, args)
        assert (
            denied.exit_code == 1
            and json.loads(denied.stdout)["error"] == "case_confirmation_required"
        )
        applied = CliRunner().invoke(cli, [*args, "--confirm"])
        assert applied.exit_code == 0, applied.stdout
        receipt = json.loads(applied.stdout)
        repeated = client.post(
            f"/api/cases/packets/{packet['id']}/confirm",
            headers=headers,
            json={"sha256": packet["sha256"], "confirm": True},
        )
        assert repeated.status_code == 200 and repeated.json()["id"] == receipt["id"]
        assert (
            client.get(f"/api/cases/{case['id']}", headers=headers).json()["history"][-1][
                "operation"
            ]
            == "human_confirmed"
        )


@pytest.mark.parametrize(
    "invalid",
    [
        {"priority": []},
        {"owner_id": []},
        {"due_at": 42},
        {"changes": {}},
        {"links": {}},
        {"changes": [{"id": [], "decision": "accept"}]},
        {"links": [{"relation": [], "case_id": "unknown"}]},
        {"request_key": ""},
    ],
)
def test_native_case_payload_rejects_malformed_types_without_partial_events(work, invalid):
    store, _, owner, _, _, _ = work
    with acting_as(store, owner):
        with pytest.raises(AccessError) as error:
            Cases(store).create(title="Policy update", reason="Business event", **invalid)
        assert error.value.status == 400
        assert Cases(store).listing() == []


def test_expiry_during_confirmation_rolls_back_all_decisions(work, monkeypatch):
    store, _, owner, _, _, changes = work
    now = [1800000000.0]
    service = Cases(store, clock=lambda: now[0])
    original = Operations.decide

    def expire(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        now[0] += 60
        return result

    with acting_as(store, owner):
        case = create(store, changes, owner)
        packet = service.prepare(case["id"], version=case["version"], ttl=30)
        monkeypatch.setattr(Operations, "decide", expire)
        with pytest.raises(AccessError) as error:
            service.confirm(packet["id"], packet["sha256"])
        assert error.value.code == "case_packet_expired"
        assert not any(item["decision"] for item in Operations(store).changes())
        current = service.get(case["id"])
        assert current["packet_expired"] and current["status"] == "awaiting_review"
        assert not current["receipts"]
