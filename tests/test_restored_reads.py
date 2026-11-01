# SPDX-License-Identifier: MIT
"""Restored reads need exact local target review, not rewritten historical approval."""

import pytest

from autoontology.recovery import read_review, transfer
from autoontology.recovery import transfer_registry as registry
from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from tests.test_authority_transfer import NEW_PASSWORD
from tests.test_authority_transfer import case as transfer_fixture  # noqa: F401
from tests.test_recovery_checkpoint import bundle  # noqa: F401
from tests.test_restore_staging import ready  # noqa: F401
from tests.test_sql_catalog import frozen  # noqa: F401

REAL_INSPECT = read_review._inspect


@pytest.fixture
def reviewed_case(transfer_fixture, monkeypatch):  # noqa: F811 - imported pytest fixture
    case = transfer_fixture
    transfer.activate(case[3], "owner", NEW_PASSWORD, workspace_id=case[2]["workspace_id"])
    store = Store.for_home(case[3] / "workspace")
    monkeypatch.setattr(
        read_review,
        "_inspect",
        lambda *a: {
            "revision": 1,
            "publication_id": "controlled-publication",
            "catalogs": [],
            "physical_check": "controlled unit probe; not real PG",
        },
    )
    from autoontology.store.operations import Operations

    with store._conn() as conn:
        conn.execute(
            "INSERT INTO ontology_snapshots(revision,created_at,model_json) VALUES(1,'controlled timestamp','{}')"
        )
    Operations(store)
    prepared = read_review.prepare(case[3])
    return case, store, prepared


def confirm(fixture, **kwargs):
    case, _, prepared = fixture
    return read_review.confirm(case[3], prepared["sha256"], **kwargs)


def test_review_enables_queries_but_not_collection_preparation_or_effects(reviewed_case):
    case, store, p = reviewed_case
    identity = Identity(store)
    actor = identity.authenticate(identity.login("owner", NEW_PASSWORD).secret.reveal())
    with pytest.raises(AccessError):
        identity.require(actor, "query.run")
    result = confirm(reviewed_case)
    assert result["query_ready"] and result["proposal_sha256"] == p["sha256"]
    assert identity.require(actor, "query.run")
    for action in [
        "candidate.prepare",
        "collection.work",
        "knowledge.publish",
        "source.edit",
        "settings.manage",
    ]:
        with pytest.raises(AccessError):
            identity.require(actor, action)
    assert identity.verify_audit(actor)["ok"]
    assert read_review.confirm(case[3], p["sha256"], resume=True) == result


@pytest.mark.parametrize(
    "boundary",
    ["after_reserve", "before_commit", "after_commit", "before_activate", "after_activate"],
)
def test_review_interruption_resumes_exact_evidence_without_duplicate_decision(
    reviewed_case, monkeypatch, boundary
):
    case, store, p = reviewed_case

    def fail(name):
        if name == boundary:
            raise RuntimeError("controlled interruption")

    monkeypatch.setattr(read_review, "_boundary", fail)
    with pytest.raises(RuntimeError):
        confirm(reviewed_case)
    monkeypatch.setattr(read_review, "_boundary", lambda _: None)
    assert confirm(reviewed_case, resume=True)["query_ready"]
    with store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_events WHERE action='identity.restored_reads_enabled'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("change", ["policy", "source", "model", "proposal", "runtime"])
def test_changed_review_inputs_are_denied_and_preserved(reviewed_case, change):
    case, store, p = reviewed_case
    if change == "policy":
        with store._conn() as conn:
            conn.execute("UPDATE identity_state SET policy_version=policy_version+1")
    elif change == "source":
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO sources VALUES('controlled-source','changed','documents','changed',NULL,NULL,'now','now')"
            )
    elif change == "model":
        with store._conn() as conn:
            conn.execute(
                "UPDATE ontology_snapshots SET model_json='{\"changed\":true}' WHERE revision=1"
            )
    elif change == "proposal":
        proposal = read_review._proposal_path(case[3], p["sha256"])
        proposal.write_text("{}")
    else:
        (case[3] / "local.env").write_text("changed")
    with pytest.raises(AccessError):
        confirm(reviewed_case)
    assert registry.read(case[2]["workspace_id"])["catalog_ready"] is False


def test_reviewed_queries_close_again_after_policy_drift(reviewed_case):
    case, store, p = reviewed_case
    confirm(reviewed_case)
    identity = Identity(store)
    actor = identity.authenticate(identity.login("owner", NEW_PASSWORD).secret.reveal())
    assert identity.require(actor, "query.run")
    with store._conn() as conn:
        conn.execute("UPDATE identity_state SET policy_version=policy_version+1")
    with pytest.raises(AccessError) as error:
        identity.require(actor, "query.run")
    assert error.value.code == "restored_catalog_review_required"
    assert read_review.status(store)["query_ready"] is False


def test_authenticated_app_cannot_confirm_a_host_target_review(reviewed_case):
    case, store, p = reviewed_case
    identity = Identity(store)
    actor = identity.authenticate(identity.login("owner", NEW_PASSWORD).secret.reveal())
    with acting_as(store, actor), pytest.raises(AccessError):
        confirm(reviewed_case)


def test_an_unpublished_candidate_is_not_selected_for_target_review(reviewed_case):
    # Exercise the real inspector's operational selection gate, not its mocked PG probe.
    _, store, _ = reviewed_case
    with store._conn() as conn:
        conn.execute("UPDATE ops_state SET published_revision=NULL WHERE id=1")
    with pytest.raises(AccessError):
        REAL_INSPECT(store, read_review._settings(store.path.parent.parent))


def test_a_matching_legacy_config_cannot_skip_the_restored_target_gate(frozen, monkeypatch):  # noqa: F811
    from autoontology.playground.sql_catalog import legacy_binding, verify_binding

    store, settings, _, revision, source, _ = frozen
    binding = legacy_binding(store, revision, source)

    def deny(*args):
        raise AccessError("Exact restored target missing", "restored_catalog_review_required", 409)

    monkeypatch.setattr(read_review, "allow_binding", deny)
    with pytest.raises(AccessError) as error:
        verify_binding(store, settings, source.name, binding)
    assert error.value.code == "restored_catalog_review_required"


def test_lost_active_proposal_holds_queries_without_hiding_saved_knowledge(reviewed_case):
    case, store, prepared = reviewed_case
    confirm(reviewed_case)
    identity = Identity(store)
    actor = identity.authenticate(identity.login("owner", NEW_PASSWORD).secret.reveal())
    read_review._proposal_path(case[3], prepared["sha256"]).unlink()
    description = identity.describe(actor)
    assert "knowledge.read" in description["capabilities"]
    assert "query.run" not in description["capabilities"]
    with pytest.raises(AccessError) as error:
        identity.require(actor, "query.run")
    assert error.value.code == "restored_catalog_review_required"
