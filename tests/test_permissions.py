# SPDX-License-Identifier: MIT
"""Accounts mode enforcement: the role table against representative routes, non-members,
every route covered by the sign-in gate, the engine's /v1 and /ingest, agent tokens on their
creator's access, members/users/audit management, magic links in two steps, archived
playground, and off mode unchanged."""

from __future__ import annotations

import pytest
from fastapi.routing import _IncludedRouter
from fastapi.testclient import TestClient

from autoontology.config import (
    INTERNAL_AS_HEADER,
    INTERNAL_HEADER,
    Settings,
    derived_key,
    engine_api_key,
    expand_model_profile,
    ingestion_service_url,
    internal_headers,
)
from autoontology.store import accounts as acc
from autoontology.store.accounts import Accounts
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web.projects import (
    INSTALLATION_PERMISSIONS,
    PROJECT_PERMISSIONS,
    role_permissions,
)
from autoontology.web.runs import RunManager

API = "/api"
BETA = f"{API}/projects/beta"
ROLES = ("viewer", "reviewer", "editor", "owner")
SPARQL = {"query": "SELECT * WHERE { ?s ?p ?o }"}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    from autoontology.engine import jobs

    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)
    # a request may load the upstream env into os.environ (restored by isolated_env): do
    # not leave the "already applied" marker behind for later tests
    monkeypatch.setattr(jobs, "_APPLIED_ENV", jobs._APPLIED_ENV)


def _app(monkeypatch, **env):
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings.from_env()
    app = web.create_app(settings, runs=RunManager(settings, jobs=dict))
    return app, settings


class Env:
    def __init__(self, monkeypatch, **env):
        self.app, self.settings = _app(monkeypatch, **env)
        self.store = Store.for_home(self.settings.home)
        self.accounts = Accounts(self.store)
        self.users: dict[str, acc.User] = {}
        self.admin = self.client("admin@example.com", admin=True)
        resp = self.admin.post(f"{API}/projects", json={"slug": "beta", "name": "Beta"})
        assert resp.status_code == 201, resp.text
        self.beta = self.store.project("beta")

    def client(self, email: str | None, *, admin: bool = False, role: str | None = None):
        c = TestClient(self.app)
        if email is None:
            return c
        user = self.accounts.by_email(email) or self.accounts.create_user(email, is_admin=admin)
        self.users[email] = user
        if role:
            self.accounts.add_member(self.store.project("beta").id, user.id, role)
        raw, _ = self.accounts.create_session(user.id)
        c.cookies.set("ao_session", raw)
        return c


@pytest.fixture
def env(isolated_env, monkeypatch):
    return Env(monkeypatch)


# --- the table ------------------------------------------------------------------------------


def test_role_table_is_cumulative():
    assert role_permissions("viewer") == ["project.read", "exports.read", "playground.ask"]
    assert set(role_permissions("reviewer")) - set(role_permissions("viewer")) == {
        "review.decide",
        "review.apply",
    }
    assert set(role_permissions("editor")) - set(role_permissions("reviewer")) == {
        "sources.write",
        "runs.start",
        "agent.tokens",
        "playground.manage",
    }
    assert set(role_permissions("owner")) == set(PROJECT_PERMISSIONS)
    assert role_permissions(None) == role_permissions("god") == []
    assert not set(INSTALLATION_PERMISSIONS) & set(PROJECT_PERMISSIONS)


#: (method, path, body, the permission the route needs). The archive pair runs last.
ROUTES = [
    ("GET", f"{BETA}/sources", None, "project.read"),
    ("GET", f"{BETA}/runs", None, "project.read"),
    ("GET", f"{BETA}/agent/tokens", None, "agent.tokens"),
    ("GET", f"{BETA}/agent/usage", None, "agent.tokens"),
    ("GET", f"{BETA}/agent/tools", None, "project.read"),
    ("GET", f"{BETA}/exports/r1.zip", None, "exports.read"),
    ("POST", f"{BETA}/playground/sparql", SPARQL, "playground.ask"),
    ("GET", f"{BETA}/playground/conversations", None, "playground.ask"),
    ("DELETE", f"{BETA}/playground/conversations/{'0' * 32}", None, "playground.manage"),
    ("POST", f"{BETA}/review/x:1/decision", {"decision": "accept"}, "review.decide"),
    ("DELETE", f"{BETA}/review/x:1/decision", None, "review.decide"),
    ("POST", f"{BETA}/runs", {"kind": "apply"}, "review.apply"),
    (
        "POST",
        f"{BETA}/sources",
        {"name": "docs", "kind": "documents", "locator": "/nonexistent/x"},
        "sources.write",
    ),
    ("POST", f"{BETA}/runs", {"kind": "build"}, "runs.start"),
    ("POST", f"{BETA}/agent/tokens", {"name": "t"}, "agent.tokens"),
    ("PATCH", BETA, {"name": "Beta 2"}, "project.manage"),
    ("GET", f"{BETA}/members", None, "members.manage"),
    ("GET", f"{API}/audit?project=beta", None, "audit.read"),
    ("GET", f"{API}/users", None, "users.manage"),
    ("GET", f"{API}/settings", None, "settings.manage"),
    ("POST", f"{API}/providers/test", None, "settings.manage"),
    ("POST", f"{API}/projects", {"slug": "gamma"}, "project.create"),
    ("POST", f"{BETA}/archive", None, "project.manage"),
    ("POST", f"{BETA}/unarchive", None, "project.manage"),
]


def _allowed(role: str, permission: str) -> bool:
    if role == "admin":
        return True
    if permission in INSTALLATION_PERMISSIONS:
        return False
    return permission in role_permissions(role)


@pytest.mark.parametrize("role", ["anonymous", "nonmember", *ROLES, "admin"])
def test_role_route_matrix(env, role, monkeypatch):
    # the provider test would call a model: answer at once
    async def no_test(self, app):
        return {"ok": True}

    monkeypatch.setattr("autoontology.web.api.Api.test_provider", no_test)
    if role == "anonymous":
        c = env.client(None)
    elif role == "admin":
        c = env.admin
    elif role == "nonmember":
        c = env.client("stranger@example.com")
    else:
        c = env.client(f"{role}@example.com", role=role)
    for method, path, body, permission in ROUTES:
        resp = c.request(method, path, json=body)
        data = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        where = (role, method, path, resp.status_code, resp.text[:200])
        project_route = path.startswith(BETA) or "project=beta" in path
        if role == "anonymous":
            assert resp.status_code == 401 and data["error"] == "login_required", where
        elif role == "nonmember" and project_route:
            assert resp.status_code == 404 and data["error"] == "unknown_project", where
        elif role == "nonmember":
            assert resp.status_code == 403 and data["permission"] == permission, where
        elif _allowed(role, permission):
            assert resp.status_code not in (401, 403), where
            assert data.get("error") != "unknown_project", where
        else:
            assert resp.status_code == 403, where
            assert data == {
                "error": "forbidden",
                "detail": f"this needs the {permission!r} permission",
                "permission": permission,
            }, where


def test_project_list_shows_only_memberships(env):
    env.store.create_project("gamma")
    viewer = env.client("v@example.com", role="viewer")
    body = viewer.get(f"{API}/projects").json()
    assert [p["slug"] for p in body["projects"]] == ["beta"]
    assert body["projects"][0]["role"] == "viewer"
    assert body["projects"][0]["permissions"] == role_permissions("viewer")
    everything = {p["slug"] for p in env.admin.get(f"{API}/projects").json()["projects"]}
    assert {"beta", "gamma", "default"} <= everything
    # the default project's /api aliases hide it from a non-member too
    resp = viewer.get(f"{API}/sources")
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_project"
    # the creator of a project owns it (admins too)
    admin_id = env.users["admin@example.com"].id
    assert env.accounts.role_of(env.beta.id, admin_id) == "owner"


def test_me_lists_permissions(env):
    reviewer = env.client("r@example.com", role="reviewer")
    me = reviewer.get(f"{API}/auth/me").json()
    assert me["permissions"] == {"beta": role_permissions("reviewer")}
    assert me["installation_permissions"] == []
    assert me["memberships"][0]["permissions"] == role_permissions("reviewer")
    admin = env.admin.get(f"{API}/auth/me").json()
    assert admin["permissions"]["beta"] == list(PROJECT_PERMISSIONS)
    assert admin["installation_permissions"] == list(INSTALLATION_PERMISSIONS)
    anon = env.client(None).get(f"{API}/auth/me").json()
    assert anon["permissions"] == {} and anon["authenticated"] is False


def test_disabled_user_loses_access(env):
    c = env.client("e@example.com", role="editor")
    assert c.get(f"{BETA}/sources").status_code == 200
    env.accounts.disable(env.users["e@example.com"].id)
    assert c.get(f"{BETA}/sources").status_code == 401


def test_review_decision_records_the_email_and_audit(env, monkeypatch):
    seen = {}

    class Inbox:
        items = {"x:1": {"id": "x:1"}}

        def decide(self, item_id, **kw):
            seen.update(kw)

            class D:
                def to_dict(self):
                    return {"decided_by": kw["decided_by"]}

            return D()

    monkeypatch.setattr("autoontology.web.api.Api.inbox", lambda self, **kw: Inbox())
    c = env.client("rev@example.com", role="reviewer")
    resp = c.post(f"{BETA}/review/x:1/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    assert seen["decided_by"] == "rev@example.com"
    entry = env.accounts.list_audit(action="review.decide")[0]
    assert entry["actor"] == "rev@example.com" and entry["project_id"] == env.beta.id
    assert entry["target"] == "x:1"


# --- every route is covered -------------------------------------------------------------------


def _routes(app):
    def walk(routes):
        for r in routes:
            if isinstance(r, _IncludedRouter):
                yield from walk(r.effective_candidates())
            else:
                yield r

    for r in walk(app.router.routes):
        orig = getattr(r, "original_route", r)
        path = getattr(r, "path", None) or orig.path
        methods = sorted(getattr(orig, "methods", None) or [])
        endpoint = getattr(r, "endpoint", None) or getattr(orig, "endpoint", None)
        yield path, methods, endpoint


#: Reachable without a session (each checks what it needs itself).
PUBLIC = ("/api/auth/", "/auth/magic/", "/health", "/mcp", "/agent/v1/", "/ui")
#: Login-only routes without a project (static information or their own checks).
INSTALLATION_ROUTES = {
    "/api/status",
    "/api/projects",
    "/api/providers/test",
    "/api/settings",
    "/api/settings/test",
    "/api/settings/restart",
    "/api/settings/restart/{restart_id}",
    "/api/users",
    "/api/users/{user_id}",
    "/api/users/{user_id}/link",
    "/api/audit",
    "/api/playground/sparql/examples",
    "/api/projects/{project}/playground/sparql/examples",
}


def _fill(path: str) -> str:
    out = path
    for name, value in (
        ("{project}", "beta"),
        ("{item_id:path}", "x:1"),
        ("{file_path:path}", "a.ttl"),
        ("{path:path}", ""),
    ):
        out = out.replace(name, value)
    import re

    return re.sub(r"\{[^}]+\}", "x1", out)


def test_every_route_needs_a_session_unless_public(env):
    anon = env.client(None)
    checked = 0
    for path, methods, _endpoint in _routes(env.app):
        if path == "/" or any(path.startswith(p) or path == p.rstrip("/") for p in PUBLIC):
            continue
        for method in methods:
            if method == "HEAD":
                continue
            resp = anon.request(method, _fill(path), json={})
            assert resp.status_code in (401, 403), (method, path, resp.status_code, resp.text)
            checked += 1
    assert checked > 100


def test_every_project_route_resolves_a_project_context(env):
    """Each /api route either depends on the ProjectContext dependency (membership and
    role) or is an installation route checking itself."""
    from fastapi.dependencies.utils import get_dependant

    def uses_ctx(endpoint, path):
        dep = get_dependant(path=path, call=endpoint)
        stack = list(dep.dependencies)
        while stack:
            d = stack.pop()
            if getattr(d.call, "__qualname__", "").endswith(
                ("path_project.<locals>.dependency", "default_project.<locals>.dependency")
            ):
                return True
            stack.extend(d.dependencies)
        return False

    missing = []
    for path, _methods, endpoint in _routes(env.app):
        if not path.startswith("/api/") or path.startswith("/api/auth/"):
            continue
        if path in INSTALLATION_ROUTES:
            continue
        if not uses_ctx(endpoint, path):
            missing.append(path)
    assert missing == []


# --- /v1 and /ingest ------------------------------------------------------------------------


def test_v1_refuses_anonymous_and_users_but_lets_the_engine_in(env):
    anon = env.client(None)
    # the local gateway delivers public requests from 127.0.0.1 with Host 127.0.0.1
    local = {"host": "127.0.0.1:7800"}
    assert anon.get("/v1/models", headers=local).status_code == 401
    resp = anon.post("/v1/chat/completions", json={"model": "x", "messages": []}, headers=local)
    assert resp.status_code == 401
    # the constant key from before accounts opens nothing
    bad = {**local, "authorization": "Bearer autoontology-router"}
    assert anon.get("/v1/models", headers=bad).status_code == 401
    viewer = env.client("v@example.com", role="viewer")
    assert viewer.get("/v1/models").status_code == 403
    engine = {**local, "authorization": f"Bearer {engine_api_key(env.settings)}"}
    assert anon.get("/v1/models", headers=engine).status_code == 200
    assert anon.get("/v1/models", headers=internal_headers(env.settings)).status_code == 200
    assert env.admin.get("/v1/models").status_code == 200
    # the engine key opens /v1 only
    assert anon.get(f"{API}/status", headers=engine).status_code == 401


def test_ingest_only_through_the_engine_prefix(env, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "enqueue_delete", lambda db, settings=None: calls.append(db))
    anon = env.client(None)
    resp = anon.post("/ingest/delete", params={"database_name": "x"})
    assert resp.status_code == 401 and calls == []
    viewer = env.client("v@example.com", role="viewer")
    assert viewer.post("/ingest/delete", params={"database_name": "x"}).status_code == 403
    url = ingestion_service_url(env.settings)
    prefix = url.removeprefix(env.settings.service_url)
    assert prefix.startswith("/internal/") and len(prefix) > 40
    assert (
        anon.post("/internal/wrong/ingest/delete", params={"database_name": "x"}).status_code == 404
    )
    resp = anon.post(f"{prefix}/ingest/delete", params={"database_name": "x"})
    assert resp.status_code == 202, resp.text
    assert calls == ["x"]
    # the ingest key opens nothing else
    assert anon.get(f"{prefix}/../../api/status").status_code in (401, 404)
    assert (
        anon.get(f"{API}/status", headers={"authorization": f"Bearer {prefix}"}).status_code == 401
    )


def test_engine_env_uses_the_installation_keys(isolated_env, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    settings = Settings.from_env()
    env = expand_model_profile(settings)
    assert env["DEFAULT_MODELS_API_KEY"] == engine_api_key(settings)
    assert (settings.run_dir / "internal.key").stat().st_mode & 0o777 == 0o600
    assert derived_key(settings, "engine") != derived_key(settings, "local")
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "off")
    off = Settings.from_env()
    assert expand_model_profile(off)["DEFAULT_MODELS_API_KEY"] in ("ollama", "autoontology-router")
    assert ingestion_service_url(off) == off.service_url
    assert internal_headers(off) == {}


def test_local_key_is_a_trusted_cli_admin_with_as(env):
    anon = env.client(None)
    headers = internal_headers(env.settings)
    assert anon.get(f"{BETA}/sources", headers=headers).status_code == 200
    assert anon.get(f"{BETA}/sources", headers={INTERNAL_HEADER: "nope"}).status_code == 401
    env.client("rev@example.com", role="reviewer")
    resp = anon.post(f"{BETA}/archive", headers={**headers, INTERNAL_AS_HEADER: "rev@example.com"})
    assert resp.status_code == 200
    entry = env.accounts.list_audit(action="project.archive")[0]
    assert entry["actor"] == "rev@example.com" and entry["detail"]["via"] == "cli"


# --- agent tokens ---------------------------------------------------------------------------


def test_token_lives_on_its_creators_access(env):
    editor = env.client("ed@example.com", role="editor")
    raw = editor.post(f"{BETA}/agent/tokens", json={"name": "bot"}).json()["token"]
    record = env.beta.agent_tokens().list_tokens()[0]
    assert record.created_by == env.users["ed@example.com"].id
    anon = env.client(None)
    auth = {"authorization": f"Bearer {raw}"}
    assert anon.get("/agent/v1/revisions", headers=auth).status_code == 200
    # demoted to viewer: still allowed (viewer is enough to read)
    env.accounts.set_role(env.beta.id, record.created_by, "viewer")
    assert anon.get("/agent/v1/revisions", headers=auth).status_code == 200
    env.accounts.remove_member(env.beta.id, record.created_by)
    assert anon.get("/agent/v1/revisions", headers=auth).status_code == 401
    env.accounts.add_member(env.beta.id, record.created_by, "editor")
    assert anon.get("/agent/v1/revisions", headers=auth).status_code == 200
    env.accounts.disable(record.created_by)
    assert anon.get("/agent/v1/revisions", headers=auth).status_code == 401
    # a token without a creator (before accounts) keeps working
    legacy, _ = env.beta.agent_tokens().create("legacy")
    assert (
        anon.get("/agent/v1/revisions", headers={"authorization": f"Bearer {legacy}"}).status_code
        == 200
    )
    actions = [e["action"] for e in env.accounts.list_audit(project_id=env.beta.id)]
    assert "token.create" in actions


# --- management -----------------------------------------------------------------------------


def test_members_api_and_last_owner(env):
    owner = env.client("own@example.com", role="owner")
    resp = owner.post(f"{BETA}/members", json={"email": "new@example.com", "role": "viewer"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True and "/ui/invite#token=" in body["invite_link"]
    assert body["member"]["role"] == "viewer" and body["mail_sent"] is False
    new_id = body["member"]["id"]
    resp = owner.post(f"{BETA}/members", json={"email": "new@example.com", "role": "viewer"})
    assert resp.status_code == 409 and resp.json()["error"] == "member_exists"
    resp = owner.post(f"{BETA}/members", json={"email": "admin@example.com", "role": "editor"})
    assert resp.status_code == 409  # the admin created beta: already its owner
    assert (
        owner.patch(f"{BETA}/members/{new_id}", json={"role": "editor"}).json()["role"] == "editor"
    )
    assert owner.patch(f"{BETA}/members/{new_id}", json={"role": "boss"}).status_code == 400
    members = {m["email"]: m["role"] for m in owner.get(f"{BETA}/members").json()["members"]}
    assert members == {
        "admin@example.com": "owner",
        "own@example.com": "owner",
        "new@example.com": "editor",
    }
    assert owner.delete(f"{BETA}/members/{new_id}").json() == {"removed": new_id}
    assert owner.delete(f"{BETA}/members/{new_id}").status_code == 404
    admin_id = env.users["admin@example.com"].id
    assert owner.delete(f"{BETA}/members/{admin_id}").status_code == 200
    own_id = env.users["own@example.com"].id
    resp = owner.delete(f"{BETA}/members/{own_id}")
    assert resp.status_code == 409 and resp.json()["error"] == "last_owner"
    resp = owner.patch(f"{BETA}/members/{own_id}", json={"role": "viewer"})
    assert resp.status_code == 409 and resp.json()["error"] == "last_owner"
    actions = [e["action"] for e in env.accounts.list_audit(project_id=env.beta.id)]
    assert {"member.add", "member.role", "member.remove", "user.invite"} <= set(actions)


def test_users_api(env):
    c = env.admin
    resp = c.post(f"{API}/users", json={"email": "Ann@Example.com", "name": "Ann"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"] == "ann@example.com" and body["user"]["has_password"] is False
    assert "/ui/invite#token=" in body["invite_link"] and body["mail_sent"] is False
    ann = body["user"]["id"]
    assert c.post(f"{API}/users", json={"email": "ann@example.com"}).status_code == 409
    assert c.post(f"{API}/users", json={"email": "not an email"}).status_code == 400
    users = c.get(f"{API}/users").json()["users"]
    assert {u["email"] for u in users} == {"admin@example.com", "ann@example.com"}
    assert (
        next(u for u in users if u["email"] == "admin@example.com")["memberships"][0]["slug"]
        == "beta"
    )
    resp = c.patch(f"{API}/users/{ann}", json={"is_admin": True, "name": "Ann K"})
    assert resp.json()["is_admin"] is True and resp.json()["name"] == "Ann K"
    link = c.post(f"{API}/users/{ann}/link", json={"purpose": "magic"}).json()
    assert "/auth/magic/" in link["link"] and link["purpose"] == "magic"
    assert c.post(f"{API}/users/{ann}/link", json={"purpose": "x"}).status_code == 400
    assert c.patch(f"{API}/users/{ann}", json={"disabled": True}).json()["active"] is False
    resp = c.post(f"{API}/users/{ann}/link", json={"purpose": "reset"})
    assert resp.status_code == 409 and resp.json()["error"] == "user_disabled"
    # the last active admin can neither be demoted nor disabled
    me = env.users["admin@example.com"].id
    for change in ({"is_admin": False}, {"disabled": True}):
        resp = c.patch(f"{API}/users/{me}", json=change)
        assert resp.status_code == 409 and resp.json()["error"] == "last_admin"
    assert c.patch(f"{API}/users/{me}", json={"bogus": 1}).status_code == 400
    assert c.patch(f"{API}/users/usr_missing", json={"name": "x"}).status_code == 404
    actions = [e["action"] for e in env.accounts.list_audit()]
    assert {"user.invite", "user.update", "user.link"} <= set(actions)


def test_audit_api_scopes(env):
    owner = env.client("own@example.com", role="owner")
    env.store.create_project("gamma")
    owner.patch(BETA, json={"name": "Beta 2"})
    resp = owner.get(f"{API}/audit")
    assert resp.status_code == 400 and resp.json()["error"] == "project_required"
    entries = owner.get(f"{API}/audit?project=beta").json()["entries"]
    assert entries and all(e["project"] == "beta" for e in entries)
    assert entries[0]["action"] == "project.rename" and entries[0]["actor"] == "own@example.com"
    assert owner.get(f"{API}/audit?project=gamma").status_code == 404
    everything = env.admin.get(f"{API}/audit?limit=500").json()["entries"]
    assert any(e["action"] == "project.create" for e in everything)
    page = env.admin.get(f"{API}/audit?limit=1").json()
    assert len(page["entries"]) == 1 and page["next_before_id"] == page["entries"][0]["id"]
    older = env.admin.get(f"{API}/audit?limit=1&before_id={page['next_before_id']}").json()
    assert older["entries"][0]["id"] < page["entries"][0]["id"]
    mine = env.admin.get(f"{API}/audit?user=own@example.com").json()["entries"]
    assert {e["actor"] for e in mine} == {"own@example.com"}


def test_run_start_is_audited(env):
    editor = env.client("ed@example.com", role="editor")
    resp = editor.post(f"{BETA}/runs", json={"kind": "build"})
    assert resp.status_code == 400  # no sources yet: nothing started, nothing audited
    assert env.accounts.list_audit(action="run.start") == []


# --- archived playground ----------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "status"), [("allow", 404), ("deny", 409)])
def test_archived_playground_setting(isolated_env, monkeypatch, mode, status):
    e = Env(monkeypatch, AUTOONTOLOGY_ARCHIVED_PLAYGROUND=mode)
    assert e.admin.post(f"{BETA}/archive").status_code == 200
    viewer = e.client("v@example.com", role="viewer")
    resp = viewer.post(f"{BETA}/playground/sparql", json=SPARQL)
    assert resp.status_code == status, resp.text  # 404 = allowed, no snapshot yet
    if status == 409:
        assert resp.json()["error"] == "project_archived"
    assert viewer.get(f"{BETA}/sources").status_code == 200  # reads stay open


# --- magic links ------------------------------------------------------------------------------


def test_magic_link_needs_a_post(env):
    user = env.accounts.create_user("m@example.com")
    raw, _ = env.accounts.issue_token(user.id, "magic")
    c = TestClient(env.app, follow_redirects=False)
    for _ in range(2):  # a scanner's prefetch does not use the token
        page = c.get(f"/auth/magic/{raw}")
        assert page.status_code == 200 and "ao_session" not in page.cookies
        assert 'action="/api/auth/magic"' in page.text and raw in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    resp = c.post("/api/auth/magic", json={"token": raw})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == "m@example.com" and resp.cookies.get("ao_session")
    again = c.post("/api/auth/magic", json={"token": raw})
    assert again.status_code == 400 and again.json()["error"] == "invalid_token"
    gone = c.get(f"/auth/magic/{raw}")
    assert gone.status_code == 303 and gone.headers["location"] == "/ui/login?error=link"


def test_magic_link_form_post_redirects(env):
    user = env.accounts.create_user("f@example.com")
    raw, _ = env.accounts.issue_token(user.id, "magic")
    c = TestClient(env.app, follow_redirects=False)
    resp = c.post("/api/auth/magic", data={"token": raw})
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/"
    assert resp.cookies.get("ao_session")
    resp = c.post("/api/auth/magic", data={"token": raw})
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/login?error=link"


# --- off mode -------------------------------------------------------------------------------


def test_off_mode_is_open_as_before(isolated_env):
    settings = Settings.from_env()
    assert not settings.accounts_enabled
    c = TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict)))
    assert c.get(f"{API}/sources").status_code == 200
    assert c.get(f"{API}/settings").status_code == 200
    assert c.get(f"{API}/users").status_code == 200
    assert c.get("/v1/models").status_code == 200
    resp = c.post(f"{API}/projects", json={"slug": "beta"})
    assert resp.status_code == 201
    rows = c.get(f"{API}/projects").json()["projects"]
    assert all(r["role"] == "owner" for r in rows)
    # decided_by stays "local"; the audit entries name it
    c.patch(BETA, json={"name": "B"})
    entry = Accounts(Store.for_home(settings.home)).list_audit(action="project.rename")[0]
    assert entry["actor"] == "local"
