# SPDX-License-Identifier: MIT
"""Execution identity is explicit, live and bound to one workspace database."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from autoontology.security.context import acting_as, authorize, current_actor
from autoontology.security.policy import AccessError, Actor
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def service(tmp_path):
    value = Identity(Store.for_home(tmp_path / "managed"))
    issued = value.bootstrap("Managed", "owner", "Owner", "local context test password")
    return value, value.authenticate(issued.secret.reveal())


def test_legacy_store_has_no_individual_claim_but_configured_store_requires_it(tmp_path, service):
    identity, _ = service
    legacy = Store.for_home(tmp_path / "legacy")
    assert current_actor(legacy) is None and authorize(legacy, "knowledge.read") is None
    with pytest.raises(AccessError) as error:
        authorize(identity.store, "knowledge.read")
    assert error.value.code == "authentication_required"


def test_actor_names_or_known_identifiers_cannot_bind_an_execution(service):
    identity, owner = service
    with (
        pytest.raises(AccessError),
        acting_as(identity.store, Actor(owner.principal_id, owner.credential_id, "human", "Owner")),
    ):
        pytest.fail("Unauthenticated actor entered the context")
    with pytest.raises(AccessError):
        current_actor(identity.store)


def test_context_is_reset_and_cannot_cross_to_legacy_or_another_managed_database(service, tmp_path):
    identity, owner = service
    other = Store.for_home(tmp_path / "other")
    with acting_as(identity.store, owner):
        assert (
            authorize(identity.store, "knowledge.publish").actor.principal_id == owner.principal_id
        )
        with pytest.raises(AccessError):
            authorize(other, "knowledge.read")
    with pytest.raises(AccessError):
        authorize(identity.store, "knowledge.read")


def test_existing_execution_rechecks_revocation_and_stale_policy(service):
    identity, owner = service
    token = identity.login("owner", "local context test password")
    actor = identity.authenticate(token.secret.reveal())
    with acting_as(identity.store, actor):
        original = authorize(identity.store, "knowledge.publish")
        identity.create_domain(owner, "changed", "Changed")
        with pytest.raises(AccessError) as error, identity.store._immediate() as conn:
            authorize(
                identity.store,
                "knowledge.publish",
                expected_policy=original.policy_version,
                conn=conn,
            )
        assert error.value.code == "authority_changed"
        identity.revoke_credential(owner, actor.credential_id)
        with pytest.raises(AccessError):
            authorize(identity.store, "knowledge.read")


def test_thread_context_is_copied_explicitly_and_still_uses_current_policy(service):
    identity, owner = service
    with acting_as(identity.store, owner), ThreadPoolExecutor(1) as executor:
        with pytest.raises(AccessError):
            executor.submit(authorize, identity.store, "knowledge.publish").result()
        copied = copy_context()
        assert (
            executor.submit(copied.run, authorize, identity.store, "knowledge.publish")
            .result()
            .actor.principal_id
            == owner.principal_id
        )


def test_removed_configuration_does_not_turn_a_managed_execution_into_legacy(service):
    identity, owner = service
    with acting_as(identity.store, owner):
        with identity.store._immediate() as conn:
            conn.execute("DELETE FROM identity_state")
        with pytest.raises(AccessError) as error:
            authorize(identity.store, "knowledge.read")
        assert error.value.code == "execution_workspace_mismatch"


def test_domain_scope_is_derived_from_the_resource_and_rechecked(service):
    identity, owner = service
    finance = identity.create_domain(owner, "finance", "Finance")
    with acting_as(identity.store, owner):
        source = identity.store.add_source(name="finance", kind="documents", locator="{}")
    identity.assign_source(owner, source.id, finance["id"])
    issued = identity.delegate(
        owner, "reader_agent", "Reader agent", ["knowledge.read"], domains=[finance["id"]]
    )
    actor = identity.authenticate(issued.secret.reveal())
    with acting_as(identity.store, actor):
        assert authorize(identity.store, "knowledge.read", source_ids=[source.id]).domains == (
            finance["id"],
        )
        with pytest.raises(AccessError):
            authorize(identity.store, "knowledge.read")
        with pytest.raises(AccessError):
            authorize(identity.store, "knowledge.publish", source_ids=[source.id])
