# SPDX-License-Identifier: MIT
"""Consequential service operations recheck authenticated authority at the effect."""

import pytest

from autoontology.engine.operations import run_publish, validate, write_exports
from autoontology.engine.restorations import prepare_restore, run_restore
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity
from autoontology.store.operations import read_snapshot
from tests.test_initial_publication import (
    CitedAnswer,
    approve,
    embeddings,
    initial,  # noqa: F401 - shared isolated candidate
)


@pytest.fixture
def managed(initial):  # noqa: F811 - shared fixture
    store, ops, revision, settings = initial
    service = Identity(store)
    issued = service.bootstrap("Managed", "owner", "Owner", "managed acceptance password")
    return store, ops, revision, settings, service, service.authenticate(issued.secret.reveal())


@pytest.mark.parametrize(
    "operation",
    [
        "decide",
        "undo",
        "check",
        "feedback",
        "resolve",
        "validate",
        "publish",
        "restore_prepare",
        "restore_execute",
        "commit",
    ],
)
def test_configured_workspace_never_accepts_implicit_local_authority(managed, operation):
    store, ops, revision, settings, _, _ = managed
    calls = {
        "decide": lambda: ops.decide("unknown", "accept", candidate=revision, actor="owner"),
        "undo": lambda: ops.undo("unknown"),
        "check": lambda: ops.add_check("Question", "Answer"),
        "feedback": lambda: ops.add_feedback("Question", "Answer", "Improve", actor="owner"),
        "resolve": lambda: ops.resolve_feedback(1, False),
        "validate": lambda: validate(store, settings, revision=revision),
        "publish": lambda: run_publish(store, settings, revision=revision, token="0" * 64),
        "restore_prepare": lambda: prepare_restore(store, settings, revision=1, reason="Restore"),
        "restore_execute": lambda: run_restore(
            store, settings, proposal="missing", confirmation="yes"
        ),
        "commit": lambda: ops.commit(revision, None, []),
    }
    with pytest.raises(AccessError) as error:
        calls[operation]()
    assert error.value.status == 401
    assert ops.state()["published_revision"] is None


def test_verified_human_is_recorded_instead_of_supplied_actor_name(managed):
    _, ops, revision, _, service, owner = managed
    item = ops.changes()[0]
    with acting_as(service.store, owner):
        decision = ops.decide(item["id"], "accept", candidate=revision, actor="forged-name")
        feedback = ops.add_feedback("Question", "Answer", "Missing evidence", actor="forged-name")
    assert decision["actor"] == feedback["actor"] == owner.principal_id
    assert "forged-name" not in str(service.audit(owner))
    assert service.verify_audit(owner)["ok"]


def test_viewer_feedback_is_allowed_but_business_confirmation_is_denied(managed):
    _, ops, revision, _, service, owner = managed
    service.create_human(owner, "reader", "Reader", "reader acceptance password", ["viewer"])
    actor = service.authenticate(
        service.login("reader", "reader acceptance password").secret.reveal()
    )
    with acting_as(service.store, actor):
        assert (
            ops.add_feedback("Question", "Answer", "Needs improvement")["actor"]
            == actor.principal_id
        )
        with pytest.raises(AccessError):
            ops.decide(ops.changes()[0]["id"], "accept", candidate=revision)
        with pytest.raises(AccessError):
            ops.add_check("Question", "Answer")


def validated(managed):
    store, ops, revision, settings, _, _ = managed
    approve(ops, revision)
    ops.add_check("승인권자는?", "대표이사")
    result = validate(
        store, settings, revision=revision, embedder=(embeddings, "test"), llm=CitedAnswer()
    )
    assert result["ok"]
    return result


def test_authenticated_publication_records_the_human_and_atomic_effect(managed):
    store, _, revision, settings, service, owner = managed
    with acting_as(store, owner):
        result = validated(managed)
        publication = run_publish(
            store, settings, revision=revision, token=result["token"], embedder=(embeddings, "test")
        )
    assert read_snapshot(store).revision == publication.revision
    events = [event for event in service.audit(owner) if event["action"] == "knowledge.publish"]
    assert len(events) == 1 and events[0]["actor_id"] == owner.principal_id


@pytest.mark.parametrize("change", ["role", "policy", "credential"])
def test_authority_change_during_preparation_blocks_final_publication(managed, change):
    store, _, revision, settings, service, owner = managed
    service.create_human(owner, "backup", "Backup", "backup acceptance password", ["admin"])
    backup = service.authenticate(
        service.login("backup", "backup acceptance password").secret.reveal()
    )

    def exporter(model, folder, new_revision, run):
        write_exports(model, folder, new_revision, run)
        if change == "role":
            service.set_roles(backup, owner.principal_id, ["viewer"])
        elif change == "policy":
            service.create_domain(backup, "changed", "Changed")
        else:
            service.revoke_credential(backup, owner.credential_id)

    with acting_as(store, owner):
        result = validated(managed)
        with pytest.raises(AccessError):
            run_publish(
                store,
                settings,
                revision=revision,
                token=result["token"],
                embedder=(embeddings, "test"),
                exporter=exporter,
            )
    assert read_snapshot(store) is None
    assert not [event for event in service.audit(backup) if event["action"] == "knowledge.publish"]
