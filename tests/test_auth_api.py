# SPDX-License-Identifier: MIT
"""``/api/auth`` and ``/auth/magic``: login, rate limits, cookies, logout, me, magic links,
invitations, password changes, the Tailscale mapping, off mode, and the mailer."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.store import accounts as acc
from autoontology.store.accounts import Accounts
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web import auth as auth_mod
from autoontology.web import mailer as mailer_mod
from autoontology.web.mailer import Mailer
from autoontology.web.runs import RunManager

PW = "correct horse battery"
TAILNET = "box.tail1234.ts.net"


class FakeSMTP:
    """Stands in for ``smtplib.SMTP``: records what would be sent."""

    sent: list = []
    calls: list = []
    fail = False

    def __init__(self, host, port, timeout=None, **kw):
        FakeSMTP.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user, password))

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("connection refused")
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)
    monkeypatch.setattr(auth_mod, "FAIL_DELAY_S", 0.0)
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail = [], [], False
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", FakeSMTP)


def _client(monkeypatch, **env) -> tuple[TestClient, Settings, Accounts]:
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings.from_env()
    client = TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict)))
    return client, settings, Accounts(Store.for_home(settings.home))


@pytest.fixture
def env(isolated_env, monkeypatch):
    client, settings, accounts = _client(monkeypatch)
    user = accounts.create_user("kim@example.com", name="Kim", password=PW)
    return client, settings, accounts, user


SMTP_ENV = {
    "AUTOONTOLOGY_SMTP_HOST": "smtp.example.com",
    "AUTOONTOLOGY_SMTP_USER": "mailer@example.com",
    "AUTOONTOLOGY_SMTP_PASSWORD": "smtp-secret-value",
    "AUTOONTOLOGY_SMTP_FROM": "ao@example.com",
}


def _login(client, email="kim@example.com", password=PW, **kw):
    return client.post("/api/auth/login", json={"email": email, "password": password}, **kw)


def _set_cookie(resp) -> str:
    return "; ".join(v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie")


# --- login ------------------------------------------------------------------------------


def test_login_sets_a_strict_http_only_cookie_and_me_answers(env):
    client, _, accounts, user = env
    assert client.get("/api/auth/me").json()["authenticated"] is False
    resp = _login(client, email="KIM@example.com")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["authenticated"] and body["user"]["email"] == "kim@example.com"
    assert body["auth"] == "accounts" and body["is_admin"] is False
    assert "pw_hash" not in body["user"]
    cookie = _set_cookie(resp)
    assert cookie.startswith("ao_session=")
    for flag in ("HttpOnly", "Path=/", "SameSite=strict", "Max-Age=43200"):
        assert flag.lower() in cookie.lower(), cookie
    assert "secure" not in cookie.lower()  # 127.0.0.1/localhost/testserver
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] and me["via"] == "session" and me["session_expires_at"]
    assert [r["action"] for r in accounts.list_audit()] == ["login_ok"]


def test_login_failures_are_uniform(env):
    client, _, accounts, _ = env
    nopw = accounts.create_user("nopw@example.com")
    off = accounts.create_user("off@example.com", password=PW)
    accounts.disable(off.id)
    answers = set()
    for email, pw in (
        ("kim@example.com", "wrong password!!"),
        ("nobody@example.com", PW),
        (nopw.email, PW),
        (off.email, PW),
        ("not an email", PW),
    ):
        resp = _login(client, email=email, password=pw)
        assert resp.status_code == 401, (email, resp.text)
        assert "set-cookie" not in resp.headers
        answers.add(resp.text)
    assert answers == {'{"error":"invalid_credentials","detail":"wrong email or password"}'}
    assert {r["action"] for r in accounts.list_audit()} == {"login_failed"}


def test_login_runs_one_hash_for_unknown_and_known_emails(env, monkeypatch):
    client, *_ = env
    acc._dummy_hash()
    calls = []
    real = acc.hashlib.pbkdf2_hmac
    monkeypatch.setattr(
        acc.hashlib, "pbkdf2_hmac", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    _login(client, email="nobody@example.com")
    unknown = len(calls)
    calls.clear()
    _login(client, password="wrong password!!")
    assert unknown == len(calls) == 1


def test_login_bad_request_shapes(env):
    client, *_ = env
    assert client.post("/api/auth/login", json={"email": "a@b.c"}).status_code == 400
    resp = client.post("/api/auth/login", json={"email": 1, "password": PW})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"
    long_pw = "x" * (acc.PASSWORD_MAX + 1)
    assert _login(client, password=long_pw).status_code == 400


def test_rate_limit_per_email_and_per_ip(env, monkeypatch):
    client, _, accounts, _ = env
    delays = []

    async def no_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(auth_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(auth_mod, "FAIL_DELAY_S", 0.25)
    for _ in range(5):
        assert _login(client, password="wrong password!!").status_code == 401
    assert delays == [0.25, 0.5, 1.0, 2.0, 4.0]  # exponential, capped
    resp = _login(client)  # the right password, but the email is locked for now
    assert resp.status_code == 429 and resp.json()["error"] == "too_many_attempts"
    assert int(resp.headers["retry-after"]) >= 1
    # per IP: other emails from the same address
    accounts.create_user("lee@example.com", password=PW)
    resp = _login(client, email="lee@example.com")
    assert resp.status_code == 429
    # another client IP (behind the local gateway) is not limited by the first one
    other = _login(client, email="lee@example.com", headers={"cf-connecting-ip": "203.0.113.9"})
    assert other.status_code == 200


def test_login_rotates_the_session(env):
    client, *_ = env
    first = _login(client).cookies["ao_session"]
    second = _login(client).cookies["ao_session"]
    assert first != second
    stale = TestClient(client.app)
    stale.cookies.set("ao_session", first)
    assert stale.get("/api/auth/me").json()["authenticated"] is False


def test_cookie_is_secure_with_an_https_public_url(isolated_env, monkeypatch):
    client, _, accounts = _client(
        monkeypatch, AUTOONTOLOGY_PUBLIC_URL="https://autoontology.example.com/"
    )
    accounts.create_user("kim@example.com", password=PW)
    resp = _login(client)
    assert resp.status_code == 200
    assert "secure" in _set_cookie(resp).lower()


def test_cookie_is_secure_on_a_tailnet_host_and_behind_https(isolated_env, monkeypatch):
    client, _, accounts = _client(monkeypatch, AUTOONTOLOGY_TAILSCALE_HOSTS=TAILNET)
    accounts.create_user("kim@example.com", password=PW)
    headers = {"host": TAILNET, "tailscale-user-login": "someone@example.com"}
    resp = _login(client, headers=headers)
    assert resp.status_code == 200, resp.text
    assert "secure" in _set_cookie(resp).lower()
    resp = _login(client, headers={"x-forwarded-proto": "https"})
    assert "secure" in _set_cookie(resp).lower()


def test_auth_routes_keep_the_host_and_cross_origin_checks(env):
    client, *_ = env
    resp = _login(client, headers={"origin": "https://evil.example"})
    assert resp.status_code == 403 and resp.json()["error"] == "cross_origin"
    resp = _login(client, headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403
    resp = _login(client, headers={"host": "evil.example"})
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden_host"
    resp = client.get("/auth/magic/abc", headers={"host": "evil.example"}, follow_redirects=False)
    assert resp.status_code == 403
    assert _login(client, headers={"origin": "http://testserver"}).status_code == 200


# --- logout / me ------------------------------------------------------------------------


def test_logout_ends_the_session(env):
    client, _, accounts, _ = env
    raw = _login(client).cookies["ao_session"]
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert 'ao_session=""' in _set_cookie(resp) or "Max-Age=0" in _set_cookie(resp)
    assert accounts.lookup_session(raw) is None
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert "logout" in [r["action"] for r in accounts.list_audit()]


def test_me_lists_memberships(env):
    client, settings, accounts, user = env
    store = Store.for_home(settings.home)
    beta = store.create_project("beta", name="Beta").project
    accounts.add_member(beta.id, user.id, "editor")
    _login(client)
    me = client.get("/api/auth/me").json()
    assert me["memberships"] == [
        {
            "project_id": beta.id,
            "slug": "beta",
            "name": "Beta",
            "archived": False,
            "role": "editor",
            "permissions": [
                "project.read",
                "exports.read",
                "playground.ask",
                "review.decide",
                "review.apply",
                "sources.write",
                "runs.start",
                "agent.tokens",
            ],
        }
    ]
    assert me["magic_link_enabled"] is False


def test_disabled_user_loses_the_session(env):
    client, _, accounts, user = env
    _login(client)
    accounts.disable(user.id)
    assert client.get("/api/auth/me").json()["authenticated"] is False


# --- magic links ------------------------------------------------------------------------


def test_magic_link_answers_the_same_without_smtp(env):
    client, _, accounts, _ = env
    known = client.post("/api/auth/magic-link", json={"email": "kim@example.com"})
    unknown = client.post("/api/auth/magic-link", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert FakeSMTP.sent == []
    assert client.get("/api/auth/me").json()["magic_link_enabled"] is False


def test_magic_link_mail_and_single_use_redeem(isolated_env, monkeypatch, caplog):
    client, settings, accounts = _client(
        monkeypatch, AUTOONTOLOGY_PUBLIC_URL="https://ao.example.com", **SMTP_ENV
    )
    accounts.create_user("kim@example.com", name="Kim", password=PW)
    off = accounts.create_user("off@example.com")
    accounts.disable(off.id)
    assert client.get("/api/auth/me").json()["magic_link_enabled"] is True
    caplog.set_level(logging.DEBUG)
    answers = {
        client.post("/api/auth/magic-link", json={"email": e}).text
        for e in ("kim@example.com", "nobody@example.com", "off@example.com")
    }
    assert len(answers) == 1
    assert len(FakeSMTP.sent) == 1
    message = FakeSMTP.sent[0]
    assert message["To"] == "kim@example.com" and "ao@example.com" in message["From"]
    assert ("login", "mailer@example.com", "smtp-secret-value") in FakeSMTP.calls
    assert ("starttls",) in FakeSMTP.calls
    link = re.search(r"https://ao\.example\.com/auth/magic/(\S+)", message.get_content())
    assert link, message.get_content()
    token = link.group(1)
    assert "smtp-secret-value" not in caplog.text and token not in caplog.text
    page = client.get(f"/auth/magic/{token}", follow_redirects=False)
    assert page.status_code == 200 and "set-cookie" not in page.headers  # GET never signs in
    assert page.headers["referrer-policy"] == "no-referrer"
    resp = client.post("/api/auth/magic", json={"token": token})
    assert resp.status_code == 200 and resp.json()["user"]["email"] == "kim@example.com"
    assert "secure" in _set_cookie(resp).lower()  # https public URL
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert accounts.lookup_session(resp.cookies["ao_session"])[1].email == "kim@example.com"
    again = TestClient(client.app).get(f"/auth/magic/{token}", follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == "/ui/login?error=link"
    assert "set-cookie" not in again.headers


def test_magic_link_in_korean(isolated_env, monkeypatch):
    client, _, accounts = _client(monkeypatch, **SMTP_ENV)
    accounts.create_user("kim@example.com")
    client.post(
        "/api/auth/magic-link",
        json={"email": "kim@example.com"},
        headers={"accept-language": "ko-KR,ko;q=0.9"},
    )
    body = FakeSMTP.sent[0].get_content()
    assert "로그인" in body and "http://testserver/auth/magic/" in body  # request base URL


def test_magic_link_requests_are_limited(isolated_env, monkeypatch):
    client, _, accounts = _client(monkeypatch, **SMTP_ENV)
    accounts.create_user("kim@example.com")
    for _ in range(auth_mod.MAGIC_LIMIT + 3):
        resp = client.post("/api/auth/magic-link", json={"email": "kim@example.com"})
        assert resp.status_code == 202
    assert len(FakeSMTP.sent) == auth_mod.MAGIC_LIMIT


def test_expired_magic_token_is_refused(env, monkeypatch):
    client, _, accounts, user = env
    from datetime import timedelta

    raw, _ = accounts.issue_token(user.id, "magic", ttl=timedelta(seconds=-1))
    resp = client.get(f"/auth/magic/{raw}", follow_redirects=False)
    assert resp.headers["location"] == "/ui/login?error=link"


def test_an_invite_token_does_not_work_as_a_magic_link(env):
    client, _, accounts, user = env
    raw, _ = accounts.issue_token(user.id, "invite")
    resp = client.get(f"/auth/magic/{raw}", follow_redirects=False)
    assert resp.headers["location"] == "/ui/login?error=link"
    assert accounts.peek_token(raw, ("invite",)) is not None


def test_mailer_failure_is_logged_without_secrets(caplog):
    FakeSMTP.fail = True
    mailer = Mailer(
        host="smtp.example.com", user="u@example.com", password="pw-secret", sender="a@b.c"
    )
    assert "pw-secret" not in repr(mailer)
    caplog.set_level(logging.DEBUG)
    ok = mailer.send_link("magic", "kim@example.com", "https://x/auth/magic/TOKEN123")
    assert ok is False
    assert "pw-secret" not in caplog.text and "TOKEN123" not in caplog.text
    assert "example.com" in caplog.text and "OSError" in caplog.text
    assert Mailer().enabled is False and Mailer().send("a@b.c", "s", "b") is False


def test_mail_templates_render_in_both_languages():
    for kind in ("magic", "invite", "reset"):
        for lang in ("en", "ko"):
            subject, body = mailer_mod.render(kind, lang, link="https://L", name="Kim")
            assert subject and "https://L" in body and "Kim" in body
    assert "15" in mailer_mod.render("magic", "en", link="x")[1]
    assert "7" in mailer_mod.render("invite", "ko", link="x")[1]


def test_access_log_drops_magic_tokens():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/auth/magic/SECRETTOKEN", "1.1", 303),
        None,
    )
    auth_mod.RedactMagicLinks().filter(record)
    assert "SECRETTOKEN" not in record.getMessage() and "/auth/magic/***" in record.getMessage()


# --- invitations / password -------------------------------------------------------------


def test_accept_invite_sets_the_password_and_signs_in(env):
    client, _, accounts, _ = env
    new = accounts.create_user("new@example.com")
    raw, _ = accounts.issue_token(new.id, "invite")
    weak = client.post("/api/auth/accept-invite", json={"token": raw, "password": "short"})
    assert weak.status_code == 400 and weak.json()["error"] == "weak_password"
    resp = client.post("/api/auth/accept-invite", json={"token": raw, "password": PW})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == "new@example.com"
    assert "ao_session=" in _set_cookie(resp)
    assert accounts.verify_password("new@example.com", PW) is not None
    again = client.post("/api/auth/accept-invite", json={"token": raw, "password": PW})
    assert again.status_code == 400 and again.json()["error"] == "invalid_token"
    actions = [r["action"] for r in accounts.list_audit()]
    assert "password_set" in actions


def test_reset_link_ends_other_sessions(env):
    client, _, accounts, user = env
    old = _login(client).cookies["ao_session"]
    raw, _ = accounts.issue_token(user.id, "reset")
    other = TestClient(client.app)
    resp = other.post("/api/auth/accept-invite", json={"token": raw, "password": "a new password!"})
    assert resp.status_code == 200
    assert accounts.lookup_session(old) is None
    assert other.get("/api/auth/me").json()["authenticated"]


def test_password_change_revokes_other_sessions(env):
    client, _, accounts, _ = env
    _login(client)
    second = TestClient(client.app)
    second_raw = _login(second).cookies["ao_session"]
    resp = client.post("/api/auth/password", json={"current": "nope nope nope", "new": "x" * 12})
    assert resp.status_code == 403 and resp.json()["error"] == "wrong_password"
    resp = client.post("/api/auth/password", json={"current": PW, "new": "short"})
    assert resp.status_code == 400 and resp.json()["error"] == "weak_password"
    resp = client.post("/api/auth/password", json={"current": PW, "new": "a brand new password"})
    assert resp.status_code == 200 and resp.json() == {"ok": True, "revoked_sessions": 1}
    assert accounts.lookup_session(second_raw) is None
    assert client.get("/api/auth/me").json()["authenticated"]  # this session stays
    assert accounts.verify_password("kim@example.com", "a brand new password")


def test_password_change_needs_a_session(env):
    client, *_ = env
    resp = client.post("/api/auth/password", json={"current": PW, "new": "x" * 12})
    assert resp.status_code == 401 and resp.json()["error"] == "login_required"


# --- Tailscale / off mode ---------------------------------------------------------------


def test_tailscale_login_maps_to_the_user_with_that_email(isolated_env, monkeypatch):
    client, _, accounts = _client(monkeypatch, AUTOONTOLOGY_TAILSCALE_HOSTS=TAILNET)
    accounts.create_user("kim@example.com", is_admin=True)
    known = client.get(
        "/api/auth/me", headers={"host": TAILNET, "tailscale-user-login": "Kim@example.com"}
    ).json()
    assert known["authenticated"] and known["via"] == "tailscale" and known["is_admin"]
    stranger = client.get(
        "/api/auth/me", headers={"host": TAILNET, "tailscale-user-login": "other@example.com"}
    ).json()
    assert stranger["authenticated"] is False
    # the identity header means nothing on the local host
    local = client.get("/api/auth/me", headers={"tailscale-user-login": "kim@example.com"}).json()
    assert local["authenticated"] is False


def test_off_mode_is_unchanged(isolated_env, monkeypatch):
    settings = Settings.from_env()
    assert settings.auth_mode == "off"
    client = TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict)))
    me = client.get("/api/auth/me").json()
    assert me["auth"] == "off" and me["authenticated"] and me["is_admin"]
    assert me["actor"] == "local" and me["user"] is None
    assert _login(client).status_code == 409
    assert _login(client).json()["error"] == "accounts_off"
    resp = client.post("/api/auth/magic-link", json={"email": "a@example.com"})
    assert resp.status_code == 409
    resp = client.get("/auth/magic/whatever", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/projects").status_code == 200


def test_accounts_mode_requires_a_session_elsewhere(env):
    """The other routes need a signed-in user (tests/test_permissions.py has the matrix)."""
    client, *_ = env
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/status").status_code == 401
    _login(client)
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/status").status_code == 200


# --- settings ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("AUTOONTOLOGY_AUTH", "sso"),
        ("AUTOONTOLOGY_ARCHIVED_PLAYGROUND", "maybe"),
        ("AUTOONTOLOGY_SESSION_HOURS", "0"),
        ("AUTOONTOLOGY_PUBLIC_URL", "ftp://x"),
        ("AUTOONTOLOGY_PUBLIC_URL", "https://x/?a=1"),
    ],
)
def test_settings_validation(isolated_env, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError, match=var):
        Settings.from_env()


def test_settings_defaults_and_values(isolated_env, monkeypatch):
    s = Settings.from_env()
    assert (s.auth_mode, s.session_hours, s.public_url, s.archived_playground) == (
        "off",
        12,
        "",
        "allow",
    )
    assert not s.smtp_enabled and s.smtp_port == 587 and s.smtp_starttls
    for key, value in {
        **SMTP_ENV,
        "AUTOONTOLOGY_AUTH": "Accounts",
        "AUTOONTOLOGY_SESSION_HOURS": "8",
        "AUTOONTOLOGY_PUBLIC_URL": "https://ao.example.com/",
        "AUTOONTOLOGY_ARCHIVED_PLAYGROUND": "deny",
        "AUTOONTOLOGY_SMTP_PORT": "465",
        "AUTOONTOLOGY_SMTP_STARTTLS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    s = Settings.from_env()
    assert s.accounts_enabled and s.session_hours == 8 and s.archived_playground == "deny"
    assert s.public_url == "https://ao.example.com" and s.smtp_enabled
    assert s.smtp_port == 465 and not s.smtp_starttls
    assert "smtp-secret-value" not in repr(s)
