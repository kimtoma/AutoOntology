# SPDX-License-Identifier: MIT
"""Individual HTTP sessions, route inventory and worker identity on real local stores."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings, expand_model_profile
from autoontology.security.context import authorize
from autoontology.security.internal import router_secret
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from autoontology.store.operations import Operations
from autoontology.web.app import create_app
from autoontology.web.authority import POLICIES, public_path
from autoontology.web.runs import RunManager
from tests.test_operations import example, snapshot


@pytest.fixture
def managed(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    ops = Operations(store)
    revision = snapshot(store, example())
    identity = Identity(store)
    issued = identity.bootstrap("Managed", "owner", "Owner", "HTTP owner test password")
    observed = []

    def build(store, settings, **kwargs):
        decision = authorize(store, "candidate.prepare")
        observed.append(decision.actor.principal_id)
        run = store.create_run(kind="build", source_id=None)
        store.update_run(run.id, status="succeeded")
        return SimpleNamespace(status="succeeded")

    manager = RunManager(settings, jobs=lambda: {"build": build})
    app = create_app(settings, runs=manager)
    return (
        TestClient(app, client=("127.0.0.1", 50000)),
        identity,
        issued,
        ops,
        revision,
        app,
        observed,
        manager,
    )


def bearer(issued):
    return {"Authorization": "Bearer " + issued.secret.reveal()}


def test_public_shell_and_status_do_not_expose_a_principal_or_bootstrap(managed):
    client, identity, _, _, _, _, _, _ = managed
    result = client.get("/api/identity/status")
    assert result.status_code == 200 and result.json() == {
        "configured": True,
        "workspace_id": identity.status()["workspace_id"],
    }
    assert client.get("/ui/").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert client.post("/api/identity/bootstrap", json={}).status_code == 401
    assert client.get("/api/sources").status_code == 401


def test_every_business_api_is_in_the_explicit_permission_inventory(managed):
    _, _, _, _, _, app, _, _ = managed
    missing = []
    declared = {(method, path.replace(":path", "")) for method, path in POLICIES}
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method in operations:
            method = method.upper()
            checked += 1
            if not public_path(path, method) and (method, path) not in declared:
                missing.append((method, path))
    assert checked > 50 and not missing


def test_browser_cookie_is_http_only_and_logout_revokes_existing_copies(managed):
    client, _, _, _, _, _, _, _ = managed
    result = client.post(
        "/api/identity/login", json={"username": "owner", "password": "HTTP owner test password"}
    )
    assert result.status_code == 200 and "token" not in result.json()
    header = result.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header
    cookie = result.headers["set-cookie"].split(";", 1)[0]
    me = client.get("/api/identity/me")
    assert me.status_code == 200 and me.json()["principal"]["kind"] == "human"
    assert "knowledge.publish" in me.json()["capabilities"]
    assert client.post("/api/identity/logout").status_code == 200
    assert client.get("/api/identity/me", headers={"Cookie": cookie}).status_code == 401


def test_cli_session_uses_explicit_password_and_has_no_browser_cookie(managed):
    client, identity, _, _, _, _, _, _ = managed
    result = client.post(
        "/api/identity/login",
        json={"username": "owner", "password": "HTTP owner test password", "client": "cli"},
    )
    assert result.status_code == 200 and "set-cookie" not in result.headers
    data = result.json()
    actor = identity.authenticate(data["token"])
    assert actor.principal_id == data["principal"]["id"]
    response = client.get("/api/identity/me", headers={"Authorization": "Bearer " + data["token"]})
    assert response.status_code == 200


def test_viewer_cannot_call_review_settings_or_identity_endpoints_directly(managed):
    client, identity, issued, _, revision, _, _, _ = managed
    owner = identity.authenticate(issued.secret.reveal())
    identity.create_human(owner, "reader", "Reader", "HTTP reader test password", ["viewer"])
    token = identity.login("reader", "HTTP reader test password")
    headers = bearer(token)
    response = client.get("/api/sources", headers=headers)
    assert response.status_code == 200
    for method, path, body in [
        (
            "POST",
            "/api/ops/changes/unknown/decision",
            {"candidate": revision, "decision": "accept"},
        ),
        ("PUT", "/api/settings", {}),
        ("GET", "/api/identity/principals", None),
        ("POST", "/api/ops/publish", {"revision": revision, "token": "0" * 64}),
    ]:
        assert client.request(method, path, json=body, headers=headers).status_code == 403
    assert client.get(f"/api/ontology?revision={revision}", headers=headers).status_code == 403
    assert client.get("/api/not-a-permitted-route", headers=bearer(issued)).status_code == 403


def test_actor_headers_are_ignored_and_verified_principal_reaches_the_effect(managed):
    client, identity, issued, ops, revision, _, _, _ = managed
    item = ops.changes()[0]
    response = client.post(
        f"/api/ops/changes/{item['id']}/decision",
        json={"candidate": revision, "decision": "accept"},
        headers={**bearer(issued), "X-Actor": "forged-person", "X-Actor-Kind": "human"},
    )
    assert response.status_code == 200
    assert response.json()["actor"] == issued.principal["id"]
    owner = identity.authenticate(issued.secret.reveal())
    assert "forged-person" not in str(identity.audit(owner))


def test_explicit_published_history_is_readable_but_candidates_stay_private(managed):
    client, identity, issued, _, revision, _, _, _ = managed
    owner = identity.authenticate(issued.secret.reveal())
    identity.create_human(
        owner, "history_reader", "History reader", "history reader test password", ["viewer"]
    )
    token = identity.login("history_reader", "history reader test password")
    # Trusted fixture setup: mark only this existing complete snapshot operational.
    with identity.store._immediate() as conn:
        conn.execute("UPDATE ops_state SET published_revision=? WHERE id=1", (revision,))
    response = client.get(f"/api/ontology?revision={revision}", headers=bearer(token))
    assert response.status_code == 200
    candidate = snapshot(identity.store, example())
    response = client.get(f"/api/ontology?revision={candidate}", headers=bearer(token))
    assert response.status_code == 403


def test_agent_can_prepare_but_cannot_approve_itself_and_revocation_is_immediate(managed):
    client, identity, issued, _, revision, _, observed, manager = managed
    owner = identity.authenticate(issued.secret.reveal())
    delegated = identity.delegate(owner, "preparer", "Preparer", ["candidate.prepare"])
    headers = bearer(delegated)
    response = client.post("/api/runs", json={"kind": "build"}, headers=headers)
    assert response.status_code == 202
    manager.join(response.json()["run_id"], timeout=5)
    assert observed == [delegated.principal["id"]]
    assert (
        client.post(
            "/api/ops/publish",
            json={"revision": revision, "token": "0" * 64},
            headers={**headers, "X-Actor-Kind": "human"},
        ).status_code
        == 403
    )
    identity.revoke_delegation(owner, delegated.principal["id"])
    assert client.post("/api/runs", json={"kind": "build"}, headers=headers).status_code == 401


def test_internal_router_key_has_no_business_authority_and_public_placeholder_fails(managed):
    client, identity, issued, _, _, _, _, _ = managed
    assert client.get("/v1/models", headers=bearer(issued)).status_code == 401
    assert (
        client.get(
            "/v1/models", headers={"Authorization": "Bearer autoontology-router"}
        ).status_code
        == 401
    )
    key = router_secret(identity.store.path.parent)
    headers = {"Authorization": "Bearer " + key.reveal()}
    assert client.get("/v1/models", headers=headers).status_code == 200
    assert client.get("/api/sources", headers=headers).status_code == 401
    assert (
        client.post(
            "/ingest", json={"database": "autoontology_src_unknown"}, headers=headers
        ).status_code
        == 401
    )
    assert expand_model_profile(client.app.state.settings)["REASONING_API_KEY"] == key.reveal()
    remote = TestClient(client.app, client=("198.51.100.2", 50001))
    response = remote.get("/v1/models", headers=headers)
    assert response.status_code == 401


def test_login_errors_are_safe_throttled_and_never_reflect_the_password(managed):
    client, _, _, _, _, _, _, _ = managed
    for _ in range(5):
        response = client.post(
            "/api/identity/login",
            json={"username": "owner", "password": "do not reflect this test password"},
        )
        assert response.status_code == 401
        assert "do not reflect" not in response.text
    response = client.post(
        "/api/identity/login", json={"username": "owner", "password": "HTTP owner test password"}
    )
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_identity_loss_is_not_legacy_or_anonymous_enrollment(managed):
    client, identity, _, _, _, _, _, _ = managed
    with identity.store._immediate() as conn:
        conn.execute("DELETE FROM identity_state")
    assert client.get("/api/sources").status_code == 503
    assert client.get("/api/identity/status").status_code == 503
