# SPDX-License-Identifier: MIT
"""The Settings page API (/api/settings): read, test, save, restart; secrets never leave
.env (fake claude/codex CLIs, a fake embedder, no Postgres)."""

import json
import logging
import os
import stat
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.engine import restart as restart_mod
from autoontology.web import app as web
from autoontology.web import settings_api
from autoontology.web.runs import WORKSPACE
from tests.test_providers_cli import fakes  # noqa: F401 - fixture

API = "/api/settings"
KEY = "sk-test-SECRETVALUE-4f9a1c77e2"
NEW_KEY = "sk-test-NEWSECRET-77aa12bb90"


@pytest.fixture
def envfile(isolated_env, monkeypatch, fakes):  # noqa: F811
    path = isolated_env / "settings.env"
    path.write_text(
        "# my settings\nAUTOONTOLOGY_CHAT_PROVIDER=claude\n# keep me\nPOSTGRES_PORT=5432\n"
    )
    path.chmod(0o600)
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(path))
    return path


@pytest.fixture
def stubs(monkeypatch):
    """No Postgres, no Ollama, an embedder answering *dims* dimensions."""
    state = {"dims": 1024, "vectors": {"rows": 0, "dimension": None}, "embed_calls": []}

    def embed_vector(endpoint, key, model, boot):
        state["embed_calls"].append((endpoint, model))
        if state.get("embed_error"):
            raise RuntimeError(state["embed_error"])
        return [0.1] * state["dims"]

    monkeypatch.setattr(settings_api, "embed_vector", embed_vector)
    monkeypatch.setattr(settings_api, "vector_state", lambda s: dict(state["vectors"]))

    def no_ollama(*a, **k):
        raise httpx.ConnectError("refused")

    from autoontology.engine import ollama

    monkeypatch.setattr(ollama, "list_models", no_ollama)
    return state


def _client():
    return TestClient(web.create_app(Settings.from_env()))


def test_get_reports_the_selection_with_origins_and_no_key(envfile, stubs):
    envfile.write_text(
        envfile.read_text()
        + f"AUTOONTOLOGY_OPENAI_API_KEY={KEY}\nAUTOONTOLOGY_OPENAI_BASE_URL=https://api.test/v1/\n"
    )
    res = _client().get(API)
    assert res.status_code == 200, res.text
    assert KEY not in res.text
    body = res.json()
    assert body["chat"] == {"provider": "claude", "model": "claude-sonnet-5-5", "origin": ".env"}
    assert body["embed"]["provider"] == "ollama" and body["embed"]["origin"] == "default"
    assert body["openai"] == {
        "base_url": "https://api.test/v1",
        "api_key_set": True,
        "chat_model": "",
        "embed_model": "",
    }
    claude = body["cli"]["claude"]
    assert (
        claude["found"] and claude["logged_in"] and claude["login_command"] == "claude auth login"
    )
    assert "claude-opus-5-5" in claude["known_models"] and claude["model"] == "claude-sonnet-5-5"
    assert body["cli"]["codex"]["default_model"] == "gpt-6-astra"
    assert body["ollama"]["reachable"] is False and body["ollama"]["models"] == []
    assert body["locked_keys"] == [] and body["restart_required"] is False
    assert body["service_pid"] == os.getpid() and body["supervised"] is False
    assert body["running"]["chat_provider"] == "claude"


def test_shell_keys_are_locked_and_refused(envfile, stubs, monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_CODEX_MODEL", "gpt-5.5")
    c = _client()
    body = c.get(API).json()
    assert body["locked_keys"] == ["AUTOONTOLOGY_CODEX_MODEL"]
    assert body["origins"]["AUTOONTOLOGY_CODEX_MODEL"] == "shell"
    res = c.put(API, json={"chat_provider": "codex", "chat_model": "gpt-6-astra"})
    assert res.status_code == 400 and res.json()["error"] == "shell_override"
    assert res.json()["key"] == "AUTOONTOLOGY_CODEX_MODEL"
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in envfile.read_text()  # nothing written
    # the shell's own value is fine (nothing to write for it)
    res = c.put(API, json={"chat_provider": "codex", "chat_model": "gpt-5.5"})
    assert res.status_code == 200, res.text
    assert res.json()["written"] == ["AUTOONTOLOGY_CHAT_PROVIDER"]


def test_put_writes_env_with_a_private_backup_and_flags_the_restart(envfile, stubs):
    c = _client()
    res = c.put(API, json={"chat_provider": "codex", "chat_model": "gpt-5.5"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["written"] == ["AUTOONTOLOGY_CHAT_PROVIDER", "AUTOONTOLOGY_CODEX_MODEL"]
    assert out["restart_required"] is True and out["backup"].startswith("settings.env.bak-")
    text = envfile.read_text()
    assert "AUTOONTOLOGY_CHAT_PROVIDER=codex" in text and "AUTOONTOLOGY_CODEX_MODEL=gpt-5.5" in text
    assert "# my settings" in text and "# keep me" in text and "POSTGRES_PORT=5432" in text
    assert stat.S_IMODE(envfile.stat().st_mode) == 0o600
    backup = envfile.with_name(out["backup"])
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in backup.read_text()
    body = c.get(API).json()
    assert body["chat"]["provider"] == "codex" and body["chat"]["model"] == "gpt-5.5"
    assert body["running"]["chat_provider"] == "claude" and body["restart_required"] is True
    # the running service still serves claude until the restart
    assert c.get("/api/status").json()["providers"]["chat_provider"] == "claude"
    # an empty model removes the line (= the provider's default)
    res = c.put(API, json={"chat_model": ""})
    assert res.json()["written"] == ["AUTOONTOLOGY_CODEX_MODEL"]
    assert "AUTOONTOLOGY_CODEX_MODEL" not in envfile.read_text()
    assert c.get(API).json()["chat"]["model"] == "gpt-6-astra"
    # saving what is already saved writes nothing
    res = c.put(API, json={"chat_provider": "codex"})
    assert res.json() == {"written": [], "backup": None, "restart_required": True}


def test_put_validates_the_candidate(envfile, stubs):
    c = _client()
    res = c.put(API, json={"chat_provider": "openai", "chat_model": "gpt-x"})
    assert res.status_code == 400 and res.json()["error"] == "invalid_settings"
    assert "AUTOONTOLOGY_OPENAI_BASE_URL is not set" in res.json()["problems"]
    for bad in (
        {"chat_model": "bad model; rm"},
        {"openai_base_url": "ftp://x"},
        {"chat_provider": "gemini"},
        {"unknown_field": 1},
        {"api_key": "two words"},
    ):
        res = c.put(API, json=bad)
        assert res.status_code == 400, (bad, res.text)
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in envfile.read_text()


def test_openai_key_is_saved_only_to_env_and_never_echoed(envfile, stubs, tmp_path):
    log_file = tmp_path / "service.log"
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    texts = []
    try:
        c = _client()
        stubs["embed_error"] = f"Incorrect API key provided: {KEY}"
        candidate = {
            "chat_provider": "openai",
            "chat_model": "gpt-test",
            "embed_provider": "openai",
            "embed_model": "text-embedding-test",
            "openai_base_url": "http://127.0.0.1:9/v1",
            "api_key": KEY,
        }
        res = c.post(f"{API}/test", json=candidate)
        texts.append(res.text)
        assert res.status_code == 200, res.text
        assert res.json()["chat"]["ok"] is False and res.json()["embed"]["ok"] is False
        assert "***" in res.json()["embed"]["error"]
        stubs.pop("embed_error")
        res = c.put(API, json=candidate)
        texts.append(res.text)
        assert res.status_code == 200, res.text
        assert "AUTOONTOLOGY_OPENAI_API_KEY" in res.json()["written"]
        assert f"AUTOONTOLOGY_OPENAI_API_KEY={KEY}" in envfile.read_text()
        got = c.get(API)
        texts.append(got.text)
        assert got.json()["openai"]["api_key_set"] is True
        texts.append(c.get("/api/status").text)
        # an empty key keeps the saved one; a new key replaces it; clear removes it
        res = c.put(API, json={"openai_base_url": "http://127.0.0.1:9/v2", "api_key": ""})
        texts.append(res.text)
        assert f"AUTOONTOLOGY_OPENAI_API_KEY={KEY}" in envfile.read_text()
        res = c.put(API, json={"api_key": NEW_KEY})
        texts.append(res.text)
        assert NEW_KEY in envfile.read_text() and KEY not in envfile.read_text()
        res = c.put(API, json={"chat_provider": "claude", "embed_provider": "ollama"})
        texts.append(res.text)
        res = c.put(API, json={"clear_api_key": True})
        texts.append(res.text)
        assert "AUTOONTOLOGY_OPENAI_API_KEY" not in envfile.read_text()
        texts.append(c.get(API).text)
        res = c.put(API, json={"api_key": f"{KEY} x"})  # invalid: refused without echo
        texts.append(res.text)
        assert res.status_code == 400
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()
    logged = log_file.read_text()
    for secret in (KEY, NEW_KEY):
        assert secret not in logged
        for text in texts:
            assert secret not in text


def test_test_endpoint_pongs_the_candidate_without_applying_it(envfile, stubs, fakes):  # noqa: F811
    c = _client()
    res = c.post(f"{API}/test", json={"chat_provider": "codex"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["chat"]["ok"] is True and out["chat"]["provider"] == "codex"
    assert out["chat"]["model"] == "gpt-6-astra" and out["chat"]["answer"] == "pong"
    assert out["embed"] == {
        "ok": True,
        "model": "bge-m3",
        "seconds": out["embed"]["seconds"],
        "dimension": 1024,
        "mismatch_with_store": False,
        "stored_dimension": None,
        "provider": "ollama",
    }
    assert "codex_home" in fakes()[-1]  # the codex CLI answered
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in envfile.read_text()
    assert c.get(API).json()["restart_required"] is False
    # a store holding 1024-d vectors and a 768-d candidate
    stubs["vectors"] = {"rows": 42, "dimension": 1024}
    stubs["dims"] = 768
    out = c.post(f"{API}/test", json={"embed_model": "other-embed"}).json()
    assert out["embed"]["mismatch_with_store"] is True and out["embed"]["stored_dimension"] == 1024


def test_one_test_at_a_time(envfile, stubs):
    c = _client()
    lock = c.app.state.api._provider_test_lock
    assert lock.acquire(blocking=False)
    try:
        res = c.post(f"{API}/test", json={})
        assert res.status_code == 409 and res.json()["error"] == "test_in_progress"
    finally:
        lock.release()


def test_put_refuses_an_embedding_dimension_change_without_consent(envfile, stubs):
    stubs["vectors"] = {"rows": 42, "dimension": 1024}
    stubs["dims"] = 768
    c = _client()
    res = c.put(API, json={"embed_model": "nomic-embed-text"})
    assert res.status_code == 409 and res.json()["error"] == "embedding_dimension_changed"
    assert res.json()["dimension"] == 768 and res.json()["stored_dimension"] == 1024
    assert "re-ingested" in res.json()["detail"]
    assert "AUTOONTOLOGY_OLLAMA_EMBED_MODEL" not in envfile.read_text()
    res = c.put(API, json={"embed_model": "nomic-embed-text", "confirm_reingest": True})
    assert res.status_code == 200 and res.json()["written"] == ["AUTOONTOLOGY_OLLAMA_EMBED_MODEL"]
    # same dimension: no consent needed; no stored vectors: not even probed
    stubs["dims"] = 1024
    assert c.put(API, json={"embed_model": "bge-m3-alt"}).status_code == 200
    stubs["vectors"] = {"rows": 0, "dimension": None}
    calls = len(stubs["embed_calls"])
    assert c.put(API, json={"embed_model": "x-embed"}).status_code == 200
    assert len(stubs["embed_calls"]) == calls
    # an unreachable embedder against stored vectors needs consent too
    stubs["vectors"] = {"rows": 3, "dimension": 1024}
    stubs["embed_error"] = "connection refused"
    res = c.put(API, json={"embed_model": "y-embed"})
    assert res.status_code == 409 and res.json()["error"] == "embedding_check_failed"


def test_settings_routes_are_behind_the_local_guard(envfile, stubs):
    c = _client()
    res = c.get(API, headers={"host": "evil.example:7860"})
    assert res.status_code == 403 and res.json()["error"] == "forbidden_host"
    for method, path in (("put", API), ("post", f"{API}/test"), ("post", f"{API}/restart")):
        res = getattr(c, method)(
            path, json={"chat_provider": "codex"}, headers={"origin": "http://evil.example"}
        )
        assert res.status_code == 403 and res.json()["error"] == "cross_origin", path
    assert "AUTOONTOLOGY_CHAT_PROVIDER=claude" in envfile.read_text()


# --- restart -------------------------------------------------------------------------


@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def spawn(settings, *, only=None, previous_pid=None):
        record = {
            "id": "0123456789ab",
            "state": "running",
            "started_at": restart_mod._now(),
            "previous_pid": previous_pid,
        }
        restart_mod.write_record(settings.run_dir, record)
        calls.append(previous_pid)
        return record

    monkeypatch.setattr(restart_mod, "spawn", spawn)
    return calls


def test_restart_needs_a_supervised_service(envfile, stubs, spawned):
    res = _client().post(f"{API}/restart")
    assert res.status_code == 409 and res.json()["error"] == "not_supervised"
    assert spawned == []


def test_restart_starts_the_helper_and_reports_it(envfile, stubs, spawned, monkeypatch):
    monkeypatch.setattr(settings_api, "supervised", lambda s: True)
    c = _client()
    res = c.post(f"{API}/restart")
    assert res.status_code == 202, res.text
    assert res.json() == {"restart_id": "0123456789ab", "previous_pid": os.getpid()}
    assert spawned == [os.getpid()]
    # the workspace stays claimed: no build can start before the service goes down
    assert c.app.state.api.runs.busy().kind == "restart"
    status = c.get(f"{API}/restart/0123456789ab").json()
    assert status["state"] == "running" and status["service_pid"] == os.getpid()
    assert c.get(API).json()["restart_in_progress"] is True
    res = c.post(f"{API}/restart")
    assert res.status_code == 409 and res.json()["error"] == "restart_in_progress"
    res = c.put(API, json={"chat_provider": "codex"})
    assert res.status_code == 409 and res.json()["error"] == "restart_in_progress"
    for bad in ("nope", "../../etc", "0123456789ac"):
        assert c.get(f"{API}/restart/{bad}").status_code == 404


def test_restart_waits_for_running_work(envfile, stubs, spawned, monkeypatch):
    monkeypatch.setattr(settings_api, "supervised", lambda s: True)
    c = _client()
    runs = c.app.state.api.runs
    runs.claim("orders", "upload")
    res = c.post(f"{API}/restart")
    assert res.status_code == 409 and res.json()["error"] == "run_active"
    runs.release("orders")
    lock = c.app.state.api._provider_test_lock
    lock.acquire()
    try:
        assert c.post(f"{API}/restart").json()["error"] == "run_active"
    finally:
        lock.release()
    assert spawned == []
    assert c.post(f"{API}/restart").status_code == 202
    runs.release(WORKSPACE)


# --- the helper ----------------------------------------------------------------------


class FakeSupervisor:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def down(self):
        self.calls.append("down")

    def up(self):
        self.calls.append("up")
        if self.fail:
            raise RuntimeError(self.fail)

    def spec(self, name):
        return name

    def read_pid(self, spec):
        return 4242


def test_helper_restarts_with_the_current_env_file(envfile, isolated_env, monkeypatch):
    from autoontology.config import unload_env

    home = Settings.from_env().home
    unload_env()  # the helper is a fresh process: nothing loaded from .env yet
    envfile.write_text("AUTOONTOLOGY_CHAT_PROVIDER=codex\n")
    sup = FakeSupervisor()
    seen = {}

    def factory(settings, echo, only):
        seen["provider"], seen["only"] = settings.chat_provider, only
        return sup

    lines = []
    record = restart_mod.run(
        home, envfile, restart_id="aaaaaaaaaaaa", echo=lines.append, supervisor=factory, grace_s=0
    )
    assert record["state"] == "done" and record["new_pid"] == 4242 and record["error"] is None
    assert sup.calls == ["down", "up"] and seen == {"provider": "codex", "only": None}
    saved = restart_mod.read_record(home / "run", "aaaaaaaaaaaa")
    assert saved["state"] == "done" and saved["finished_at"]
    assert all(line.startswith("[aaaaaaaaaaaa] ") for line in lines)


def test_helper_reports_a_failed_up_masked(envfile, isolated_env):
    home = Settings.from_env().home
    sup = FakeSupervisor(fail="service exited: POSTGRES_PASSWORD=hunter2 was refused")
    record = restart_mod.run(
        home, envfile, restart_id="bbbbbbbbbbbb", echo=lambda _l: None,
        supervisor=lambda s, e, o: sup, grace_s=0,
    )  # fmt: skip
    assert record["state"] == "failed" and "hunter2" not in record["error"]
    assert "POSTGRES_PASSWORD=***" in record["error"]


def test_helper_refuses_a_concurrent_restart(envfile, isolated_env):
    import fcntl

    home = Settings.from_env().home
    (home / "run").mkdir(parents=True, exist_ok=True)
    with open(home / "run" / restart_mod.LOCK_NAME, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        sup = FakeSupervisor()
        record = restart_mod.run(
            home, envfile, restart_id="cccccccccccc", echo=lambda _l: None,
            supervisor=lambda s, e, o: sup, grace_s=0,
        )  # fmt: skip
    assert record["state"] == "failed" and "another restart" in record["error"]
    assert sup.calls == []


def test_helper_env_drops_derived_and_dotenv_copied_values(isolated_env, monkeypatch):
    from autoontology.config import load_env

    envfile = isolated_env / "h.env"
    envfile.write_text("AUTOONTOLOGY_OPENAI_CHAT_MODEL=from-dotenv\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(envfile))
    monkeypatch.setenv("REASONING_MODEL", "openai/derived")
    monkeypatch.setenv("AUTOONTOLOGY_DERIVED_ENV", "REASONING_MODEL")
    monkeypatch.setenv("MY_SHELL_KEY", "kept")
    load_env()
    env = restart_mod.helper_env()
    assert "REASONING_MODEL" not in env and "AUTOONTOLOGY_DERIVED_ENV" not in env
    assert "AUTOONTOLOGY_OPENAI_CHAT_MODEL" not in env and env["MY_SHELL_KEY"] == "kept"
    assert env["AUTOONTOLOGY_ENV_FILE"] == str(envfile)


def test_spawned_helper_is_detached_and_logs_to_restart_log(isolated_env, monkeypatch):
    """A real detached process (it only writes the record): its own session, stdio in
    logs/restart.log, the cleaned environment."""
    settings = Settings.from_env()
    monkeypatch.setenv("REASONING_MODEL", "openai/derived")
    monkeypatch.setenv("AUTOONTOLOGY_DERIVED_ENV", "REASONING_MODEL")
    script = (
        "import json, os, sys\n"
        "from autoontology.engine import restart\n"
        "rid = sys.argv[sys.argv.index('--id') + 1]\n"
        "run = os.path.join(sys.argv[sys.argv.index('--home') + 1], 'run')\n"
        "print(f'[{rid}] helper here', flush=True)\n"
        "rec = restart.read_record(run, rid)\n"
        "rec.update(state='done', sid=os.getsid(0), pid=os.getpid(),\n"
        "           derived='REASONING_MODEL' in os.environ)\n"
        "restart.write_record(run, rec)\n"
    )
    real_argv = restart_mod.helper_argv

    def argv(*args, **kwargs):
        full = real_argv(*args, **kwargs)
        return [full[0], "-c", script, *full[3:]]

    monkeypatch.setattr(restart_mod, "helper_argv", argv)
    record = restart_mod.spawn(settings, previous_pid=os.getpid())
    rid = record["id"]
    assert record["state"] == "running" and record["previous_pid"] == os.getpid()
    done = threading.Event()
    for _ in range(100):
        got = restart_mod.read_record(settings.run_dir, rid)
        if got and got.get("state") == "done":
            done.set()
            break
        threading.Event().wait(0.1)
    assert done.is_set()
    assert got["sid"] == got["pid"] != os.getsid(0)  # leads its own session
    assert got["derived"] is False
    assert "helper here" in restart_mod.log_tail(settings.logs_dir, rid)
    log = (settings.logs_dir / "restart.log").read_text()
    assert json.dumps(rid)[1:-1] in log
