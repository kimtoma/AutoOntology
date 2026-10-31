# SPDX-License-Identifier: MIT
# ruff: noqa: F811
"""Administration read models omit secrets and obey the same UI/CLI policy."""

import json

import pytest

from autoontology.security.policy import AccessError
from tests.test_identity_api import bearer, managed  # noqa: F401


def test_management_packet_contains_current_grants_and_safe_delegation(managed):
    _, service, issued, *_ = managed
    owner = service.authenticate(issued.secret.reveal())
    domain = service.create_domain(owner, "contracts", "Contracts")
    person = service.create_human(
        owner,
        "domain_reader",
        "Domain reader",
        "administration reader password",
        ["viewer"],
        domains=[domain["id"]],
    )
    agent = service.delegate(owner, "prepare_agent", "Preparation", ["candidate.prepare"], ttl=120)
    data = service.administration(owner)
    found = next(p for p in data["principals"] if p["id"] == person["id"])
    assert found["grants"] == [{"role": "viewer", "domain_id": domain["id"]}]
    delegated = next(p for p in data["principals"] if p["id"] == agent.principal["id"])
    assert delegated["delegation"]["actions"] == ["candidate.prepare"]
    assert delegated["delegation"]["delegator_id"] == owner.principal_id
    assert data["policy_version"] == service.status()["policy_version"]
    assert "knowledge.publish" not in data["delegatable_actions"]
    assert "candidate.prepare" in data["delegatable_actions"]
    text = json.dumps(data)
    assert all(
        s not in text
        for s in (issued.secret.reveal(), agent.secret.reveal(), "password_hash", "token_hash")
    )


def test_reader_lists_only_own_sessions_and_admin_can_inspect_others(managed):
    _, service, issued, *_ = managed
    owner = service.authenticate(issued.secret.reveal())
    person = service.create_human(
        owner, "reader", "Reader", "administration reader password", ["viewer"]
    )
    session = service.login("reader", "administration reader password")
    actor = service.authenticate(session.secret.reveal())
    listed = service.credentials(actor)
    assert len(listed) == 1 and listed[0]["id"] == session.credential_id
    assert listed[0]["current"] and listed[0]["status"] == "active"
    assert not any(k in listed[0] for k in ("token_hash", "epoch", "password_hash"))
    with pytest.raises(AccessError):
        service.credentials(actor, principal_id=owner.principal_id)
    with pytest.raises(AccessError):
        service.credentials(actor, principal_id="*")
    with pytest.raises(AccessError):
        service.administration(actor)
    assert len(service.credentials(owner, principal_id="*")) == 2
    service.change_password(owner, person["id"], "new administration password")
    assert service.credentials(owner, principal_id=person["id"])[0]["status"] == "invalidated"
    service.revoke_credential(owner, session.credential_id)
    assert service.credentials(owner, principal_id=person["id"])[0]["status"] == "revoked"


def test_reenabling_a_person_never_resurrects_their_old_session(managed):
    client, service, issued, *_ = managed
    owner = service.authenticate(issued.secret.reveal())
    person = service.create_human(
        owner, "returning", "Returning", "administration reader password", ["viewer"]
    )
    old = service.login("returning", "administration reader password")
    service.disable(owner, person["id"])
    response = client.post(
        f"/api/identity/principals/{person['id']}/enable", headers=bearer(issued)
    )
    assert response.status_code == 200 and response.json()["status"] == "active"
    with pytest.raises(AccessError):
        service.authenticate(old.secret.reveal())
    new = service.login("returning", "administration reader password")
    assert service.authenticate(new.secret.reveal()).principal_id == person["id"]


def test_http_management_and_session_read_scopes(managed):
    client, service, issued, *_ = managed
    response = client.get("/api/identity/management", headers=bearer(issued))
    assert response.status_code == 200 and response.json()["roles"]["admin"]
    owner = service.authenticate(issued.secret.reveal())
    service.create_human(owner, "reader", "Reader", "administration reader password", ["viewer"])
    token = service.login("reader", "administration reader password")
    response = client.get("/api/identity/credentials", headers=bearer(token))
    assert response.status_code == 200 and len(response.json()["items"]) == 1
    assert response.headers["cache-control"] == "no-store"
    for path in (
        "/api/identity/management",
        "/api/identity/credentials?principal_id=*",
        f"/api/identity/credentials?principal_id={owner.principal_id}",
    ):
        response = client.get(path, headers=bearer(token))
        assert response.status_code == 403


def test_usage_creation_records_verified_principal_and_rechecks_revocation(managed):
    from autoontology.security.context import acting_as
    from autoontology.store.traces import UsageTraces
    from tests.test_operations import example

    _, service, issued, _, revision, *_ = managed
    owner = service.authenticate(issued.secret.reveal())
    traces = UsageTraces(service.store)
    with acting_as(service.store, owner):
        model = example()
        trace = traces.begin(
            kind="retrieval",
            workspace="fixture",
            revision=revision,
            model=model,
            request={"question": "test"},
        )
        actor = traces.get(trace)["data"]["actor"]
        assert actor["id"] == owner.principal_id and actor["verified"] is True
        assert actor["credential_id"] == issued.credential_id
        service.revoke_credential(owner, issued.credential_id)
        with pytest.raises(AccessError):
            traces.begin(
                kind="retrieval",
                workspace="fixture",
                revision=revision,
                model=model,
                request={"question": "test"},
            )
