# SPDX-License-Identifier: MIT
"""CLI providers in the REST API (/api/status, POST /api/providers/test), the probe and
the doctor rows, with fake claude/codex CLIs."""

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from autoontology import doctor
from autoontology.config import Settings
from autoontology.web import api as web_api
from autoontology.web import app as web
from autoontology.web.providers import probe
from tests.test_providers_cli import _settings, fakes  # noqa: F401 - fixture

API = "/api"


@pytest.fixture
def offline(monkeypatch):
    """No backend and no Postgres: /api/status reports them down quickly."""
    from autoontology.engine import pg

    def refused(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(web_api.httpx, "get", refused)
    monkeypatch.setattr(pg, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))


def _app(provider="claude"):
    return TestClient(web.create_app(_settings(provider)))


@pytest.mark.parametrize(
    ("provider", "model", "detail"),
    [
        ("claude", "claude-sonnet-5-5", "logged in (claude.ai, max)"),
        ("codex", "gpt-6-astra", "logged in (ChatGPT)"),
    ],
)
def test_status_shows_the_cli_provider_masked(fakes, offline, provider, model, detail):  # noqa: F811
    res = _app(provider).get(f"{API}/status")
    assert res.status_code == 200
    assert "example.com" not in res.text and "someone" not in res.text
    providers = res.json()["providers"]
    assert providers["chat_provider"] == provider and providers["chat_model"] == model
    assert providers["chat"] == {
        "name": provider,
        "model": model,
        "available": True,
        "logged_in": True,
        "detail": detail,
        "remedy": "",
        "last_test": None,
    }
    assert providers["embed"] == {"name": "ollama", "model": "bge-m3"}
    assert res.json()["checks"][provider] == {"status": "ok", "detail": detail}


def test_status_reports_a_missing_login_with_its_remedy(fakes, offline, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    body = _app("claude").get(f"{API}/status").json()
    chat = body["providers"]["chat"]
    assert chat["available"] and not chat["logged_in"]
    assert "claude auth login" in chat["remedy"]
    assert body["checks"]["claude"]["status"] == "error" and body["status"] == "error"


def test_provider_test_endpoint_pongs_and_is_remembered(fakes, offline):  # noqa: F811
    client = _app("claude")
    res = client.post(f"{API}/providers/test")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["answer"] == "pong" and body["via"] == "service"
    assert body["provider"] == "claude" and body["model"] == "openai/claude:claude-sonnet-5-5"
    assert body["usage"]["prompt_tokens"] == 18  # counters, not masked
    (call,) = fakes()
    assert "--json-schema" in call["argv"]
    saved = json.loads((Settings.from_env().run_dir / probe.LAST_TEST_FILE).read_text())
    assert saved["claude"]["ok"] is True and saved["claude"]["model"] == body["model"]
    last = client.get(f"{API}/status").json()["providers"]["chat"]["last_test"]
    assert last["ok"] is True and last["at"] == body["at"]


def test_provider_test_failure_is_reported_not_raised(fakes, offline, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    body = _app("codex").post(f"{API}/providers/test").json()
    assert body["ok"] is False and body["status"] == 401
    assert "codex login" in body["remedy"]
    assert probe.last_test(Settings.from_env(), "codex")["ok"] is False


def test_provider_test_is_one_at_a_time_and_same_origin(fakes, offline):  # noqa: F811
    client = _app("claude")
    api = client.app.state.api
    assert api._provider_test_lock.acquire(blocking=False)
    try:
        res = client.post(f"{API}/providers/test")
        assert res.status_code == 409 and res.json()["error"] == "test_in_progress"
    finally:
        api._provider_test_lock.release()
    res = client.post(f"{API}/providers/test", headers={"Origin": "http://evil.example"})
    assert res.status_code == 403
    assert fakes() == []


def test_run_test_uses_a_temporary_service_when_none_runs(fakes, monkeypatch):  # noqa: F811
    with socket.socket() as sock:  # a port where no service runs
        sock.bind(("127.0.0.1", 0))
        monkeypatch.setenv("AUTOONTOLOGY_PORT", str(sock.getsockname()[1]))
    out = probe.run_test(Settings.from_env(), "codex")
    assert out["via"] == "in-process" and out["ok"] is True, out
    assert out["model"] == "openai/chatgpt:gpt-6-astra"
    assert len(fakes()) == 1
    with pytest.raises(ValueError):
        probe.run_test(Settings.from_env(), "gemini")


def _stub_doctor(monkeypatch):
    for name, fn in (
        ("check_env_file", lambda: doctor.Check(".env", "OK")),
        ("check_postgres", lambda s: [doctor.Check("postgres", "OK")]),
        ("check_ports", lambda s: [doctor.Check("port", "OK")]),
        ("check_node", lambda: doctor.Check("node", "OK")),
        ("check_kordoc", lambda s: doctor.Check("kordoc", "OK")),
        ("check_ollama", lambda s: doctor.Check("ollama", "OK")),
        ("check_embed", lambda s: doctor.Check("embedding endpoint", "OK")),
        ("check_vector_tables", lambda s: doctor.Check("vector tables", "OK")),
    ):
        monkeypatch.setattr(doctor, name, fn)


@pytest.mark.parametrize(
    ("provider", "model", "remedy"),
    [("claude", "claude-sonnet-5-5", "claude auth login"), ("codex", "gpt-6-astra", "codex login")],
)
def test_doctor_rows_for_cli_providers(fakes, monkeypatch, provider, model, remedy):  # noqa: F811
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", provider)
    _stub_doctor(monkeypatch)
    checks = {c.name: c for c in doctor.run_checks(Settings.from_env())}
    assert checks["chat provider"].status == "OK", checks["chat provider"]
    assert f"{provider} {model}" in checks["chat provider"].detail
    assert "example.com" not in checks["chat provider"].detail
    assert checks["chat endpoint"].status == "OK" and "pong" in checks["chat endpoint"].detail
    assert "chat no-think" not in checks and "chat route" not in checks
    assert checks[f"model profile {provider}+ollama"].status == "OK"
    assert checks["embed provider"].detail.startswith("ollama bge-m3")
    monkeypatch.setenv("FAKE_MODE", "notlogged")
    checks = {c.name: c for c in doctor.run_checks(Settings.from_env())}
    assert checks["chat provider"].status == "FAIL"
    assert remedy in checks["chat provider"].remedy
    assert checks["chat endpoint"].status == "SKIP"


def test_doctor_warns_about_an_unknown_cli_model(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "claude")
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_MODEL", "claude-unknown-9")
    check = doctor.check_profile(Settings.from_env())
    assert check.status == "WARN" and "AUTOONTOLOGY_CLAUDE_MODEL" in check.detail
    monkeypatch.setenv("AUTOONTOLOGY_CLAUDE_MODEL", "-p bad")
    assert doctor.check_profile(Settings.from_env()).status == "FAIL"
