# SPDX-License-Identifier: MIT
"""Schema v9 and :mod:`autoontology.store.accounts`: users, passwords, sessions, login
tokens, memberships and the audit log, plus the v8 -> v9 migration."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from autoontology.store import accounts as acc
from autoontology.store import db
from autoontology.store.accounts import AccountError, Accounts
from autoontology.store.agent_tokens import AgentTokens
from autoontology.store.db import Store


@pytest.fixture(autouse=True)
def _fast_hashes(monkeypatch):
    """PBKDF2 at its real cost makes every test slow; the format stays the same."""
    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


@pytest.fixture
def accounts(store):
    return Accounts(store)


PW = "correct horse battery"


# --- migration ------------------------------------------------------------------------


def _v8_store(tmp_path, monkeypatch) -> Store:
    v8 = [m for m in db.MIGRATIONS if m[0] <= 8]
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", v8)
        store = Store(tmp_path / "v8.db")
        assert store.schema_version() == 8
        store.create_project("beta", name="Beta")
        AgentTokens(store).create("old-token")
    return store


def test_v8_store_migrates_to_v9_without_touching_its_rows(tmp_path, monkeypatch):
    store = _v8_store(tmp_path, monkeypatch)
    assert store.migrate() == 9 == db.MIGRATIONS[-1][0]
    with sqlite3.connect(store.path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "sessions", "login_tokens", "memberships", "audit_log"} <= tables
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_tokens)")]
        assert "created_by" in cols
        row = conn.execute("SELECT name, created_by FROM agent_tokens").fetchone()
        assert row == ("old-token", None)
        slugs = {r[0] for r in conn.execute("SELECT slug FROM projects")}
        assert "beta" in slugs
    assert [t.name for t in AgentTokens(store).list_tokens()] == ["old-token"]


def test_v9_is_idempotent_when_rerun(tmp_path, monkeypatch):
    store = _v8_store(tmp_path, monkeypatch)
    store.migrate()
    Accounts(store).create_user("a@example.com")
    with sqlite3.connect(store.path) as conn:  # a downgraded schema_version re-runs v9
        conn.execute("DELETE FROM schema_version WHERE version = 9")
    assert store.migrate() == 9
    assert [u.email for u in Accounts(store).list_users()] == ["a@example.com"]


def test_check_constraints(store, accounts):
    user = accounts.create_user("a@example.com")
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO login_tokens (id_hash, user_id, purpose, created_at, expires_at) "
            "VALUES ('x', ?, 'bogus', '', '')",
            (user.id,),
        )
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO memberships (project_id, user_id, role, created_at) "
            "VALUES (?, ?, 'god', '')",
            (store.default_project_id, user.id),
        )


# --- users ----------------------------------------------------------------------------


def test_create_user_normalizes_and_rejects_duplicates(accounts):
    user = accounts.create_user("  Kim@Example.COM ", name="Kim", is_admin=True)
    assert user.id.startswith("usr_") and user.email == "kim@example.com"
    assert user.name == "Kim" and user.is_admin and not user.has_password
    assert accounts.by_email("KIM@example.com") == user
    assert accounts.get(user.id) == user
    with pytest.raises(AccountError) as err:
        accounts.create_user("kim@EXAMPLE.com")
    assert err.value.code == "user_exists"


@pytest.mark.parametrize(
    "email", ["", "kim", "kim@", "@example.com", "a b@example.com", "a@b@c", "x" * 250 + "@a.io"]
)
def test_create_user_validates_the_email(accounts, email):
    with pytest.raises(AccountError) as err:
        accounts.create_user(email)
    assert err.value.code == "invalid_email"


def test_user_dict_never_has_the_hash(accounts):
    user = accounts.create_user("a@example.com", password=PW)
    body = user.to_dict()
    assert body["has_password"] is True
    assert "pw_hash" not in body and not any("pbkdf2" in str(v) for v in body.values())


def test_disable_enable_and_admin(accounts):
    user = accounts.create_user("a@example.com", password=PW)
    raw, _ = accounts.create_session(user.id)
    disabled = accounts.disable(user.id)
    assert disabled.disabled_at and not disabled.active
    assert accounts.lookup_session(raw) is None  # disabling ends the sessions
    assert accounts.verify_password("a@example.com", PW) is None
    assert accounts.enable(user.id).active
    assert accounts.verify_password("a@example.com", PW) is not None
    assert accounts.set_admin(user.id, True).is_admin
    assert not accounts.set_admin(user.id, False).is_admin
    assert [u.email for u in accounts.list_users()] == ["a@example.com"]
    with pytest.raises(AccountError) as err:
        accounts.disable("usr_missing")
    assert err.value.code == "unknown_user"


# --- passwords ------------------------------------------------------------------------


def test_password_hash_format_and_check():
    encoded = acc.hash_password(PW)
    algo, rounds, salt, dk = encoded.split("$")
    assert algo == "pbkdf2_sha256" and int(rounds) == acc.PBKDF2_ROUNDS
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(dk)) == 32
    assert acc.check_password(PW, encoded)
    assert not acc.check_password(PW + "x", encoded)
    assert acc.hash_password(PW) != encoded  # a fresh salt every time
    for broken in (None, "", "garbage", "md5$1$00$00", "pbkdf2_sha256$x$zz$00"):
        assert not acc.check_password(PW, broken)


def test_default_rounds_are_the_reference_cost():
    """The module constant itself (the autouse fixture lowers it in this process)."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from autoontology.store import accounts as a; print(a.PBKDF2_ROUNDS)",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip() == "240000"


def test_password_policy(accounts):
    user = accounts.create_user("a@example.com")
    for weak in ("short", "x" * 11, " " * 12, "x" * 1025):
        with pytest.raises(AccountError) as err:
            accounts.set_password(user.id, weak)
        assert err.value.code == "weak_password"
    accounts.set_password(user.id, "x" * 12)
    assert accounts.get(user.id).has_password


def test_verify_password_spends_a_hash_on_every_path(accounts, monkeypatch):
    """Unknown user, user without a password, disabled user, wrong password: each runs one
    PBKDF2 (no timing difference that tells whether the account exists)."""
    accounts.create_user("pw@example.com", password=PW)
    accounts.create_user("nopw@example.com")
    off = accounts.create_user("off@example.com", password=PW)
    accounts.disable(off.id)
    calls = []
    real = acc.hashlib.pbkdf2_hmac

    def counting(*args, **kwargs):
        calls.append(args[3])
        return real(*args, **kwargs)

    acc._dummy_hash()  # computed once up front, like the service does on start
    monkeypatch.setattr(acc.hashlib, "pbkdf2_hmac", counting)
    for email, pw in (
        ("nobody@example.com", PW),
        ("nopw@example.com", PW),
        ("off@example.com", PW),
        ("pw@example.com", "wrong password!"),
    ):
        calls.clear()
        assert accounts.verify_password(email, pw) is None
        assert calls == [acc.PBKDF2_ROUNDS], email
    calls.clear()
    assert accounts.verify_password("PW@example.com", PW).email == "pw@example.com"
    assert calls == [acc.PBKDF2_ROUNDS]


# --- sessions -------------------------------------------------------------------------


def test_sessions_store_only_a_hash(store, accounts):
    user = accounts.create_user("a@example.com")
    raw, session = accounts.create_session(user.id, hours=12, ip="1.2.3.4", user_agent="ua")
    assert len(raw) >= 40
    with sqlite3.connect(store.path) as conn:
        stored = [r[0] for r in conn.execute("SELECT id_hash FROM sessions")]
    assert stored == [acc.token_hash(raw)] and raw not in stored
    found = accounts.lookup_session(raw)
    assert found is not None
    got_session, got_user = found
    assert got_user.id == user.id and got_session.ip == "1.2.3.4"
    assert accounts.get(user.id).last_login_at  # a session is a login
    assert accounts.lookup_session("not-a-session") is None
    assert accounts.lookup_session("") is None


def test_session_expiry_and_purge(store, accounts):
    user = accounts.create_user("a@example.com")
    raw, _ = accounts.create_session(user.id, hours=12)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="milliseconds")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    assert accounts.lookup_session(raw) is None
    raw2, _ = accounts.create_session(user.id)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE id_hash != ?", (past, acc.token_hash(raw2))
        )
    assert accounts.purge_expired() >= 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_last_seen_is_throttled(store, accounts, monkeypatch):
    user = accounts.create_user("a@example.com")
    raw, _ = accounts.create_session(user.id)
    first = accounts.lookup_session(raw)[0].last_seen_at
    assert accounts.lookup_session(raw)[0].last_seen_at == first  # within the throttle
    monkeypatch.setattr(acc, "LAST_SEEN_THROTTLE_S", 0)
    time.sleep(0.01)
    assert accounts.lookup_session(raw)[0].last_seen_at > first


def test_revoke_sessions(accounts):
    user = accounts.create_user("a@example.com")
    other = accounts.create_user("b@example.com")
    a1, _ = accounts.create_session(user.id)
    a2, _ = accounts.create_session(user.id)
    a3, _ = accounts.create_session(user.id)
    b1, _ = accounts.create_session(other.id)
    accounts.revoke_session(a1)
    assert accounts.lookup_session(a1) is None and accounts.lookup_session(a2)
    assert accounts.revoke_all_for_user(user.id, except_raw=a3) == 1
    assert accounts.lookup_session(a2) is None and accounts.lookup_session(a3)
    assert accounts.revoke_all_for_user(user.id) == 1
    assert accounts.lookup_session(a3) is None and accounts.lookup_session(b1)


# --- login tokens ---------------------------------------------------------------------


def test_login_token_is_single_use(store, accounts):
    user = accounts.create_user("a@example.com")
    raw, expires = accounts.issue_token(user.id, "magic")
    assert expires > db.now_iso()
    with sqlite3.connect(store.path) as conn:
        assert raw not in {r[0] for r in conn.execute("SELECT id_hash FROM login_tokens")}
    assert accounts.consume_token(raw, ("invite",)) is None  # wrong purpose: not consumed
    assert accounts.consume_token(raw, ("magic",)).id == user.id
    assert accounts.consume_token(raw, ("magic",)) is None


def test_login_token_ttls_and_expiry(store, accounts):
    user = accounts.create_user("a@example.com")
    now = datetime.now(timezone.utc)
    _, magic = accounts.issue_token(user.id, "magic")
    _, invite = accounts.issue_token(user.id, "invite")
    assert datetime.fromisoformat(magic) - now <= timedelta(minutes=15, seconds=5)
    assert datetime.fromisoformat(invite) - now >= timedelta(days=6, hours=23)
    raw, _ = accounts.issue_token(user.id, "reset")
    past = (now - timedelta(seconds=1)).isoformat(timespec="milliseconds")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE login_tokens SET expires_at = ?", (past,))
    assert accounts.consume_token(raw, ("reset",)) is None
    assert accounts.purge_expired() >= 3


def test_new_token_replaces_unused_ones_of_its_purpose(accounts):
    user = accounts.create_user("a@example.com")
    old, _ = accounts.issue_token(user.id, "magic")
    invite, _ = accounts.issue_token(user.id, "invite")
    new, _ = accounts.issue_token(user.id, "magic")
    assert accounts.consume_token(old, ("magic",)) is None
    assert accounts.consume_token(new, ("magic",)) is not None
    assert accounts.consume_token(invite, ("invite",)) is not None


def test_token_of_a_disabled_user_is_refused(accounts):
    user = accounts.create_user("a@example.com")
    raw, _ = accounts.issue_token(user.id, "magic")
    accounts.disable(user.id)
    assert accounts.consume_token(raw, ("magic",)) is None


def test_consume_with_a_password_sets_it_and_a_weak_one_keeps_the_token(accounts):
    user = accounts.create_user("a@example.com")
    raw, _ = accounts.issue_token(user.id, "invite")
    with pytest.raises(AccountError):
        accounts.consume_token(raw, ("invite",), new_password="short")
    got = accounts.consume_token(raw, ("invite", "reset"), new_password=PW)
    assert got.id == user.id and got.has_password
    assert accounts.verify_password("a@example.com", PW) is not None


def test_peek_token(accounts):
    user = accounts.create_user("a@example.com")
    raw, _ = accounts.issue_token(user.id, "invite")
    assert accounts.peek_token(raw, ("invite",)).id == user.id
    assert accounts.peek_token(raw, ("magic",)) is None
    assert accounts.consume_token(raw, ("invite",)) is not None  # peek did not consume


# --- memberships ----------------------------------------------------------------------


def test_memberships(store, accounts):
    beta = store.create_project("beta").project
    owner = accounts.create_user("o@example.com")
    viewer = accounts.create_user("v@example.com")
    accounts.add_member(beta.id, owner.id, "owner")
    accounts.add_member(beta.id, viewer.id, "viewer")
    with pytest.raises(AccountError) as err:
        accounts.add_member(beta.id, viewer.id, "editor")
    assert err.value.code == "member_exists"
    with pytest.raises(AccountError) as err:
        accounts.add_member(beta.id, viewer.id, "god")
    assert err.value.code == "invalid_role"
    with pytest.raises(AccountError) as err:
        accounts.add_member("prj_nope", viewer.id, "viewer")
    assert err.value.code == "unknown_project"
    with pytest.raises(AccountError) as err:
        accounts.add_member(beta.id, "usr_nope", "viewer")
    assert err.value.code == "unknown_user"
    assert accounts.role_of(beta.id, viewer.id) == "viewer"
    assert accounts.role_of(store.default_project_id, viewer.id) is None
    accounts.set_role(beta.id, viewer.id, "editor")
    members = accounts.list_members(beta.id)
    assert [(m["email"], m["role"]) for m in members] == [
        ("o@example.com", "owner"),
        ("v@example.com", "editor"),
    ]
    assert accounts.projects_of(viewer.id) == [(beta.id, "editor")]
    accounts.remove_member(beta.id, viewer.id)
    assert accounts.role_of(beta.id, viewer.id) is None
    with pytest.raises(AccountError) as err:
        accounts.remove_member(beta.id, viewer.id)
    assert err.value.code == "not_a_member"


def test_last_owner_is_protected(store, accounts):
    beta = store.create_project("beta").project
    a = accounts.create_user("a@example.com")
    b = accounts.create_user("b@example.com")
    accounts.add_member(beta.id, a.id, "owner")
    for call in (
        lambda: accounts.set_role(beta.id, a.id, "editor"),
        lambda: accounts.remove_member(beta.id, a.id),
    ):
        with pytest.raises(AccountError) as err:
            call()
        assert err.value.code == "last_owner"
    accounts.add_member(beta.id, b.id, "owner")
    accounts.set_role(beta.id, a.id, "viewer")  # b remains an owner
    with pytest.raises(AccountError):
        accounts.remove_member(beta.id, b.id)


# --- audit ----------------------------------------------------------------------------


def test_audit_record_and_filters(store, accounts):
    user = accounts.create_user("a@example.com")
    beta = store.create_project("beta").project
    accounts.record("login_ok", user_id=user.id, ip="1.2.3.4")
    accounts.record("user.create", actor="cli", target="b@example.com", detail={"admin": True})
    accounts.record("member.add", user_id=user.id, project_id=beta.id, target="x")
    rows = accounts.list_audit()
    assert [r["action"] for r in rows] == ["member.add", "user.create", "login_ok"]
    cli_row = rows[1]
    assert cli_row["actor"] == "cli" and cli_row["user_id"] is None
    assert cli_row["detail"] == {"admin": True, "actor": "cli"}
    assert rows[2]["actor"] == "a@example.com" and rows[2]["ip"] == "1.2.3.4"
    assert [r["action"] for r in accounts.list_audit(project_id=beta.id)] == ["member.add"]
    assert len(accounts.list_audit(user_id=user.id)) == 2
    assert [r["action"] for r in accounts.list_audit(action="login_ok")] == ["login_ok"]
    assert len(accounts.list_audit(limit=1)) == 1
    older = accounts.list_audit(before_id=rows[0]["id"])
    assert [r["action"] for r in older] == ["user.create", "login_ok"]
