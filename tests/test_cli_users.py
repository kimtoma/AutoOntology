# SPDX-License-Identifier: MIT
"""``ao user create|list|disable|enable|link``: the server-side admin commands."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.config import Settings
from autoontology.store import accounts as acc
from autoontology.store.accounts import Accounts
from autoontology.store.db import Store

runner = CliRunner()
PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)


@pytest.fixture
def accounts(isolated_env):
    return lambda: Accounts(Store.for_home(Settings.from_env().home))


def test_create_without_a_password_prints_an_invite_link(accounts, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_PUBLIC_URL", "https://ao.example.com")
    result = runner.invoke(app, ["user", "create", "Kim@Example.com", "--name", "Kim", "--admin"])
    assert result.exit_code == 0, result.output
    link = re.search(
        r"https://ao\.example\.com/ui/invite#token=([\w-]+)&purpose=invite", result.output
    )
    assert link, result.output
    user = accounts().by_email("kim@example.com")
    assert user.is_admin and user.name == "Kim" and not user.has_password
    assert accounts().peek_token(link.group(1), ("invite",)).id == user.id
    audit = accounts().list_audit()
    assert audit[0]["action"] == "user.create" and audit[0]["actor"] == "cli"
    assert audit[0]["target"] == "kim@example.com" and audit[0]["detail"]["admin"] is True
    assert link.group(1) not in json.dumps(audit)


def test_create_with_password_stdin(accounts):
    result = runner.invoke(
        app, ["user", "create", "a@example.com", "--password-stdin"], input=PW + "\n"
    )
    assert result.exit_code == 0, result.output
    assert "token=" not in result.output and PW not in result.output
    assert accounts().verify_password("a@example.com", PW) is not None


def test_create_refusals(accounts):
    result = runner.invoke(
        app, ["user", "create", "a@example.com", "--password-stdin"], input="short\n"
    )
    assert result.exit_code == 2 and "weak_password" in result.output
    assert accounts().by_email("a@example.com") is None
    assert runner.invoke(app, ["user", "create", "not-an-email"]).exit_code == 2
    runner.invoke(app, ["user", "create", "a@example.com"])
    again = runner.invoke(app, ["user", "create", "A@example.com", "--json"])
    assert again.exit_code == 1 and json.loads(again.stdout)["error"] == "user_exists"


def test_list_disable_enable(accounts):
    runner.invoke(app, ["user", "create", "a@example.com", "--admin"])
    runner.invoke(app, ["user", "create", "b@example.com", "--password-stdin"], input=PW + "\n")
    out = runner.invoke(app, ["user", "list"])
    assert out.exit_code == 0 and "a@example.com" in out.output and "b@example.com" in out.output
    assert runner.invoke(app, ["user", "disable", "B@example.com"]).exit_code == 0
    body = json.loads(runner.invoke(app, ["user", "list", "--json"]).stdout)
    rows = {u["email"]: u for u in body["users"]}
    assert rows["a@example.com"]["is_admin"] and rows["a@example.com"]["active"]
    assert not rows["b@example.com"]["active"] and rows["b@example.com"]["has_password"]
    assert "pw_hash" not in json.dumps(body)
    assert runner.invoke(app, ["user", "enable", "b@example.com"]).exit_code == 0
    assert accounts().by_email("b@example.com").active
    actions = [r["action"] for r in accounts().list_audit()]
    assert actions[:2] == ["user.enable", "user.disable"]
    missing = runner.invoke(app, ["user", "disable", "nobody@example.com"])
    assert missing.exit_code == 1 and "unknown_user" in missing.output


def test_link_magic_and_reset(accounts, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_AUTH", "accounts")
    runner.invoke(app, ["user", "create", "a@example.com"])
    out = runner.invoke(app, ["user", "link", "a@example.com"])
    assert out.exit_code == 0, out.output
    token = re.search(r"http://127\.0\.0\.1:7800/auth/magic/([\w-]+)", out.output).group(1)
    assert accounts().peek_token(token, ("magic",)) is not None
    out = runner.invoke(app, ["user", "link", "a@example.com", "--purpose", "reset", "--json"])
    body = json.loads(out.stdout)
    assert "/ui/invite#token=" in body["link"] and body["purpose"] == "reset" and body["expires_at"]
    assert (
        runner.invoke(app, ["user", "link", "a@example.com", "--purpose", "bogus"]).exit_code == 2
    )
    assert runner.invoke(app, ["user", "link", "nobody@example.com"]).exit_code == 1
    assert [r["action"] for r in accounts().list_audit()][0] == "user.link"


def test_link_warns_when_accounts_are_off(accounts):
    runner.invoke(app, ["user", "create", "a@example.com"])
    out = runner.invoke(app, ["user", "link", "a@example.com"])
    assert out.exit_code == 0 and "AUTOONTOLOGY_AUTH=accounts" in out.output
