# SPDX-License-Identifier: MIT
"""Verified credentials, scoped authority and revocation share one durable policy."""

import json
from dataclasses import replace

import pytest

from autoontology.security.context import acting_as
from autoontology.security.policy import AccessError, Actor
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def identity(tmp_path):
    clock = [1800000000.0]
    service = Identity(Store.for_home(tmp_path / "workspace"), clock=lambda: clock[0])
    password = "correct horse local fixture 2026"
    issued = service.bootstrap("org", "owner", "Acceptance owner", password)
    actor = service.authenticate(issued.secret.reveal())
    return service, actor, password, clock


def test_bootstrap_only_once_and_secret_never_appears_in_public_records(identity):
    service, actor, password, _ = identity
    assert service.status()["configured"] and actor.kind == "human"
    assert service.require(actor, "identity.admin").actor.principal_id == actor.principal_id
    with pytest.raises(AccessError) as error:
        service.bootstrap("org", "other", "Other", password)
    assert error.value.code == "identity_already_configured"
    logged = service.login("owner", password)
    raw = logged.secret.reveal()
    assert raw not in repr(logged) and raw not in json.dumps(logged.public())
    assert raw not in json.dumps(service.principals(actor))
    assert raw not in json.dumps(service.audit(actor)) and password not in json.dumps(
        service.audit(actor)
    )
    with service.store._conn() as conn:
        assert raw not in str(conn.execute("SELECT * FROM identity_credentials").fetchall())


def test_viewer_cannot_review_publish_or_change_scope(identity):
    service, owner, _, _ = identity
    created = service.create_human(
        owner, "reader", "Reader", "long reader test password", ["viewer"]
    )
    actor = service.authenticate(
        service.login("reader", "long reader test password").secret.reveal()
    )
    assert actor.principal_id == created["id"]
    assert service.require(actor, "knowledge.read")
    for action in ("review.decide", "knowledge.publish", "identity.admin", "source.edit"):
        with pytest.raises(AccessError) as error:
            service.require(actor, action)
        assert error.value.code == "forbidden"
    with pytest.raises(AccessError):
        service.set_roles(actor, owner.principal_id, ["admin"])
    with pytest.raises(AccessError):
        service.require(owner, "not-a-capability")


def test_domain_workspace_and_unknown_source_are_deny_by_default(identity):
    service, owner, _, _ = identity
    finance = service.create_domain(owner, "finance", "Finance")
    hr = service.create_domain(owner, "hr", "HR")
    created = service.create_human(
        owner,
        "finance_reader",
        "Finance reader",
        "long finance reader password",
        ["viewer"],
        domains=[finance["id"]],
    )
    reader = service.authenticate(
        service.login("finance_reader", "long finance reader password").secret.reveal()
    )
    assert service.require(reader, "knowledge.read", domains=[finance["id"]])
    for domains in ([hr["id"]], [finance["id"], hr["id"]], ["unknown"]):
        with pytest.raises(AccessError):
            service.require(reader, "knowledge.read", domains=domains)
    with pytest.raises(AccessError):
        service.require(reader, "knowledge.read", workspace="other-workspace")
    with pytest.raises(AccessError):
        service.source_domains(reader, ["unrecorded-source"])
    assert created["kind"] == "human"


def test_agent_cannot_self_approve_or_lie_about_its_kind_and_scope(identity):
    service, owner, _, _ = identity
    domain = service.create_domain(owner, "finance", "Finance")
    issued = service.delegate(
        owner,
        "preparer",
        "Preparation agent",
        ["knowledge.read", "candidate.prepare"],
        domains=[domain["id"]],
        ttl=300,
    )
    agent = service.authenticate(issued.secret.reveal())
    assert service.require(agent, "candidate.prepare", domains=[domain["id"]])
    forged = replace(agent, kind="human")
    for action in ("review.decide", "knowledge.publish", "knowledge.restore", "identity.admin"):
        with pytest.raises(AccessError):
            service.require(forged, action, domains=[domain["id"]])
    with pytest.raises(AccessError):
        service.require(agent, "knowledge.read", domains=["*"])
    with pytest.raises(AccessError):
        service.delegate(
            owner, "publisher_agent", "Not permitted", ["knowledge.publish"], domains=[domain["id"]]
        )


def test_role_revocation_owner_disable_and_policy_changes_apply_to_existing_tokens(identity):
    service, owner, _, _ = identity
    reviewer = service.create_human(
        owner, "reviewer", "Reviewer", "reviewer long test password", ["reviewer"]
    )
    actor = service.authenticate(
        service.login("reviewer", "reviewer long test password").secret.reveal()
    )
    decision = service.require(actor, "review.decide")
    service.set_roles(owner, reviewer["id"], ["viewer"])
    with pytest.raises(AccessError):
        service.require(actor, "review.decide")
    with pytest.raises(AccessError) as error:
        service.require(owner, "knowledge.read", expected_policy=decision.policy_version)
    assert error.value.code == "authority_changed"
    service.disable(owner, reviewer["id"])
    with pytest.raises(AccessError):
        service.require(actor, "knowledge.read")
    with pytest.raises(AccessError):
        service.disable(owner, owner.principal_id)


def test_delegation_expires_and_revocation_invalidates_preexisting_tokens(identity):
    service, owner, _, clock = identity
    issued = service.delegate(owner, "short_lived", "Agent", ["knowledge.read"], ttl=60)
    agent = service.authenticate(issued.secret.reveal())
    assert service.require(agent, "knowledge.read")
    service.revoke_delegation(owner, agent.principal_id)
    with pytest.raises(AccessError):
        service.require(agent, "knowledge.read")
    other = service.delegate(owner, "expires", "Agent", ["knowledge.read"], ttl=60)
    clock[0] += 61
    with pytest.raises(AccessError):
        service.authenticate(other.secret.reveal())


def test_invalid_login_cooldown_never_records_password_and_password_reset_revokes_sessions(
    identity,
):
    service, owner, old_password, clock = identity
    token = service.login("owner", old_password)
    for _ in range(5):
        with pytest.raises(AccessError):
            service.login("owner", "wrong secret value")
    with pytest.raises(AccessError) as error:
        service.login("owner", old_password)
    assert error.value.code == "login_throttled" and error.value.retry_after > 0
    clock[0] += 61
    assert service.login("owner", old_password)
    assert "wrong secret value" not in json.dumps(service.audit(owner))
    service.change_password(owner, owner.principal_id, "rotated fixture password long")
    with pytest.raises(AccessError):
        service.authenticate(token.secret.reveal())
    assert service.authenticate(
        service.login("owner", "rotated fixture password long").secret.reveal()
    )


def test_audit_is_append_only_verifiable_and_survives_backup(identity, tmp_path):
    import sqlite3

    service, owner, _, _ = identity
    assert service.verify_audit(owner)["ok"]
    with service.store._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM identity_events")
    backup = tmp_path / "restored"
    backup.mkdir()
    with service.store._conn() as conn, sqlite3.connect(backup / "autoontology.db") as target:
        conn.backup(target)
    recovered = Identity(Store.for_home(backup), clock=service.clock)
    assert recovered.audit(owner) == service.audit(owner)
    assert recovered.verify_audit(owner)["ok"]


def test_actor_ids_kind_and_credential_metadata_are_not_authentication(identity):
    service, owner, _, _ = identity
    forged = Actor(owner.principal_id, owner.credential_id, "human", "Owner")
    with pytest.raises(AccessError) as error:
        service.require(forged, "identity.admin")
    assert error.value.status == 401
    created = service.create_human(owner, "other", "Other", "other long test password", ["viewer"])
    with pytest.raises(AccessError):
        service.require(replace(owner, principal_id=created["id"]), "identity.admin")
    with pytest.raises(AccessError):
        service.source_domains(forged, ["nonexistent"])


@pytest.mark.parametrize("scope", [[{}], [1], [None], "*", [], ["*", "unknown"]])
def test_invalid_domain_values_fail_closed(identity, scope):
    service, owner, _, _ = identity
    with pytest.raises(AccessError):
        service.require(owner, "knowledge.read", domains=scope)


@pytest.mark.parametrize("roles", [[{}], [None], [1], "admin", [], ["unknown"]])
def test_invalid_roles_do_not_mutate_policy_or_grants(identity, roles):
    service, owner, _, _ = identity
    before = service.status()
    events = service.audit(owner)
    with pytest.raises(AccessError):
        service.set_roles(owner, owner.principal_id, roles)
    assert service.status() == before and service.audit(owner) == events


@pytest.mark.parametrize("actions", [[{}], [None], [1], "query.run", [], ["unknown"]])
def test_invalid_agent_actions_fail_closed_without_principal_creation(identity, actions):
    service, owner, _, _ = identity
    before = service.principals(owner)
    with pytest.raises(AccessError):
        service.delegate(owner, "invalid_agent", "Invalid agent", actions)
    assert service.principals(owner) == before


def test_delegator_current_roles_and_disabled_state_bound_existing_agent(identity):
    service, owner, _, _ = identity
    service.create_human(owner, "backup", "Second owner", "second owner test password", ["admin"])
    backup = service.authenticate(
        service.login("backup", "second owner test password").secret.reveal()
    )
    issued = service.delegate(owner, "preparer", "Agent", ["knowledge.read", "candidate.prepare"])
    agent = service.authenticate(issued.secret.reveal())
    service.set_roles(backup, owner.principal_id, ["viewer"])
    assert service.require(agent, "knowledge.read")
    with pytest.raises(AccessError):
        service.require(agent, "candidate.prepare")
    service.disable(backup, owner.principal_id)
    with pytest.raises(AccessError):
        service.require(agent, "knowledge.read")


def test_reclassification_never_exposes_private_historical_source(identity):
    service, owner, _, _ = identity
    private = service.create_domain(owner, "private", "Private")
    public = service.create_domain(owner, "public", "Public")
    with acting_as(service.store, owner):
        source = service.store.add_source(name="policies", kind="documents", locator="{}")
    service.assign_source(owner, source.id, private["id"])
    service.assign_source(owner, source.id, public["id"])
    service.create_human(
        owner,
        "public_reader",
        "Public reader",
        "public reader test password",
        ["viewer"],
        domains=[public["id"]],
    )
    reader = service.authenticate(
        service.login("public_reader", "public reader test password").secret.reveal()
    )
    with pytest.raises(AccessError):
        service.source_domains(reader, [source.id])
    assert set(service.source_domains(owner, [source.id])) == {private["id"], public["id"]}


def test_stale_policy_check_inside_write_transaction_prevents_side_effect(identity):
    service, owner, _, _ = identity
    prepared = service.require(owner, "knowledge.publish")
    service.create_domain(owner, "changed", "Changed")
    with pytest.raises(AccessError) as error, service.store._immediate() as conn:
        service.require(
            owner, "knowledge.publish", expected_policy=prepared.policy_version, conn=conn
        )
        conn.execute("UPDATE identity_state SET workspace_name='unexpected'")
    assert error.value.code == "authority_changed"
    assert service.status()["workspace_name"] == "org"


def test_concurrent_last_admin_role_removal_keeps_one_owner(identity):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    service, owner, _, _ = identity
    service.create_human(owner, "backup", "Backup", "backup owner test password", ["admin"])
    backup = service.authenticate(
        service.login("backup", "backup owner test password").secret.reveal()
    )
    barrier = Barrier(2)

    def remove(actor):
        barrier.wait(timeout=5)
        try:
            service.set_roles(actor, actor.principal_id, ["viewer"])
            return "removed"
        except AccessError:
            return "retained"

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(remove, [owner, backup])) == ["removed", "retained"]
    with service.store._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity_grants WHERE role='admin' AND revoked_at IS NULL"
            ).fetchone()[0]
            == 1
        )


def test_successful_login_does_not_reset_peer_bruteforce_budget(identity, monkeypatch):
    from autoontology.security.credentials import token_digest
    from autoontology.store import identity as module

    service, owner, password, _ = identity
    # Isolate rate-limit behavior without spending thirty memory-hard hash calls.
    monkeypatch.setattr(module, "check_password", lambda supplied, record: supplied == password)
    for index in range(30):
        with pytest.raises(AccessError):
            service.login(f"unknown_{index}", "wrong", peer="one-peer")
        if index < 29:
            service.login("owner", password, peer="one-peer")
    with pytest.raises(AccessError) as error:
        service.login("owner", password, peer="one-peer")
    assert error.value.code == "login_throttled"
    assert "one-peer" not in json.dumps(service.audit(owner))
    with service.store._conn() as conn:
        assert (
            conn.execute(
                "SELECT failures FROM identity_login_limits WHERE key=?",
                ("peer:" + token_digest("one-peer"),),
            ).fetchone()[0]
            == 30
        )


@pytest.mark.parametrize("password", [None, 42, "x" * 1025, "\ud800"])
def test_invalid_password_encoding_and_types_are_safe(identity, password):
    from autoontology.security.credentials import check_password, hash_password

    service, _, _, _ = identity
    with pytest.raises(AccessError):
        hash_password(password)
    with pytest.raises(AccessError):
        service.login("owner", password)
    assert not check_password(password, "{}")


def test_malformed_hash_never_controls_scrypt_resource_parameters(monkeypatch):
    from autoontology.security import credentials

    def unexpected(*args):
        pytest.fail("Malformed records must be rejected before key derivation")

    monkeypatch.setattr(credentials, "derive", unexpected)
    for record in (
        "null",
        "[]",
        "{}",
        '{"n":999999999}',
        '{"version":1,"algorithm":"scrypt","n":131072,"r":8,"p":1,"salt":"no","hash":"no"}',
    ):
        assert not credentials.check_password("a valid length password", record)


def test_atomic_v11_upgrade_preserves_existing_rows_and_leaves_identity_unconfigured(
    tmp_path, monkeypatch
):
    from autoontology.store import db

    path = tmp_path / "legacy.db"
    with monkeypatch.context() as old_schema:
        old_schema.setattr(db, "MIGRATIONS", [m for m in db.MIGRATIONS if m[0] <= 11])
        legacy = Store(path)
        legacy.add_source(name="legacy", kind="documents", locator="{}")
        with legacy._conn() as conn:
            tables = [
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if r[0] != "schema_version"
            ]
            before = {
                name: [tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"')]
                for name in tables
            }
    upgraded = Store(path)
    assert upgraded.schema_version() == 16 and not Identity(upgraded).status()["configured"]
    with upgraded._conn() as conn:
        assert {
            name: [tuple(row) for row in conn.execute(f'SELECT * FROM "{name}"')] for name in tables
        } == before


def test_concurrent_host_enrollment_creates_exactly_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    service = Identity(Store.for_home(tmp_path / "workspace"))
    barrier = Barrier(2)

    def enroll(name):
        barrier.wait(timeout=5)
        try:
            return service.bootstrap("Concurrent", name, name, "concurrent enrollment password")
        except AccessError as error:
            assert error.code == "identity_already_configured"
            return None

    with ThreadPoolExecutor(2) as executor:
        issued = [
            credential for credential in executor.map(enroll, ["first", "second"]) if credential
        ]
    assert len(issued) == 1
    actor = service.authenticate(issued[0].secret.reveal())
    assert len(service.principals(actor)) == 1
    assert service.verify_audit(actor)["events"] == 1


@pytest.mark.parametrize("ttl", [True, None, "3600", 0, -1, float("nan"), float("inf"), 10**1000])
def test_invalid_lifetimes_fail_without_creating_or_logging_a_session(identity, ttl):
    service, owner, password, _ = identity
    before = service.audit(owner)
    with pytest.raises(AccessError) as error:
        service.login("owner", password, ttl=ttl)
    assert error.value.code == "invalid_lifetime"
    with pytest.raises(AccessError):
        service.delegate(owner, "invalid_ttl", "Invalid TTL", ["knowledge.read"], ttl=ttl)
    assert service.audit(owner) == before
