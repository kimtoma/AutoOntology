# SPDX-License-Identifier: MIT
"""Review fixes of the CLI providers: codex login sync-back (timing, failures, cross-home
safety, permissions), home paths in status payloads, reasoning-effort validation, error
classification, process-group kills, disconnects, env strip and the exact codex argv."""

import asyncio
import json
import logging
import os
import stat
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoontology import doctor
from autoontology.config import Settings
from autoontology.web import app as web
from autoontology.web.providers import base, codex_cli, probe
from autoontology.web.providers.codex_cli import DISABLED_FEATURES
from autoontology.web.providers.openai_compat import parse_request
from autoontology.web.providers.router import Registry
from tests.test_providers_api import offline  # noqa: F401 - fixture
from tests.test_providers_cli import FMT, _chat, _client, _settings, fakes  # noqa: F401

ROTATED = {"tokens": {"refresh_token": "rotated"}}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def codex_user(isolated_env, monkeypatch):
    """A user CODEX_HOME (0755 dir, 0600 auth.json) and an AutoOntology home."""
    user = isolated_env / "user-codex"
    user.mkdir(exist_ok=True)
    user.chmod(0o755)
    auth = user / "auth.json"
    auth.write_text(json.dumps({"tokens": {"refresh_token": "old"}}))
    auth.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(user))
    return user


def _refresh(home: Path, content: str, *, ahead: float = 10.0) -> Path:
    """Simulate codex replacing the private symlink with a refreshed file."""
    target = codex_cli.ensure_codex_home(home)
    link = target / "auth.json"
    link.unlink()
    link.write_text(content)
    user = codex_cli.user_codex_home() / "auth.json"
    os.utime(link, (user.stat().st_mtime + ahead,) * 2)
    return link


def _leftovers(user: Path) -> list[str]:
    return [p.name for p in user.iterdir() if p.name.startswith(".auth.json.")]


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def provider_log():
    handler = _Records()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    plog = logging.getLogger("autoontology.providers")
    plog.addHandler(handler)
    old = plog.level
    plog.setLevel(logging.INFO)
    yield handler.lines
    plog.removeHandler(handler)
    plog.setLevel(old)


# --- Important 1: sync-back timing and failure handling ----------------------------------


def test_codex_refresh_is_written_back_right_after_the_call(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "refresh")
    user = Path(os.environ["CODEX_HOME"]) / "auth.json"
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    # no later call, no status: the user's own codex already has the rotated token
    assert json.loads(user.read_text()) == ROTATED
    assert _mode(user) == 0o600
    private = Settings.from_env().home.resolve() / "run" / "codex-home" / "auth.json"
    assert private.is_symlink() and _leftovers(user.parent) == []


def test_service_shutdown_syncs_back(fakes, offline):  # noqa: F811
    with TestClient(web.create_app(_settings("codex"))):
        _refresh(Settings.from_env().home, json.dumps(ROTATED))
    user = Path(os.environ["CODEX_HOME"]) / "auth.json"
    assert json.loads(user.read_text()) == ROTATED


def test_status_syncs_back(fakes):  # noqa: F811
    _refresh(Settings.from_env().home, json.dumps(ROTATED))
    Registry(_settings("codex")).status(refresh=True)
    user = Path(os.environ["CODEX_HOME"]) / "auth.json"
    assert json.loads(user.read_text()) == ROTATED


def test_failed_copy_keeps_the_private_token_and_the_users_file(
    codex_user, isolated_env, monkeypatch, provider_log
):
    home = isolated_env / "h"
    secret = json.dumps({"tokens": {"refresh_token": "SECRET-ROTATED-XYZ"}})
    link = _refresh(home, secret)
    before = (codex_user / "auth.json").read_text()

    def broken(src, dst):
        raise OSError(28, "No space left on device")

    real = os.replace
    monkeypatch.setattr(codex_cli.os, "replace", broken)
    assert codex_cli.sync_codex_home(home) is False
    assert link.is_file() and not link.is_symlink() and link.read_text() == secret
    assert (codex_user / "auth.json").read_text() == before
    assert _leftovers(codex_user) == []
    # ensure_codex_home (before the next call) keeps the newer private copy, never relinks
    codex_cli.ensure_codex_home(home)
    assert not link.is_symlink() and link.read_text() == secret
    warnings = [line for line in provider_log if line.startswith("WARNING")]
    assert warnings and all("SECRET" not in line and "rotated" not in line for line in warnings)
    monkeypatch.setattr(codex_cli.os, "replace", real)
    assert codex_cli.sync_codex_home(home) is True  # the next attempt delivers it
    assert (codex_user / "auth.json").read_text() == secret and link.is_symlink()


def test_an_older_private_copy_is_not_synced(codex_user, isolated_env):
    home = isolated_env / "h"
    _refresh(home, "stale", ahead=-10)
    codex_cli.sync_codex_home(home)
    assert json.loads((codex_user / "auth.json").read_text())["tokens"]["refresh_token"] == "old"


# --- Important 2: cross-home safety and permissions --------------------------------------


def test_written_back_file_is_0600_at_every_step(codex_user, isolated_env, monkeypatch):
    old_umask = os.umask(0o022)
    try:
        home = isolated_env / "h"
        _refresh(home, json.dumps(ROTATED))
        real = os.replace
        seen = []

        def checked(src, dst):
            seen.append((_mode(Path(src)), Path(src).parent == codex_user))
            return real(src, dst)

        monkeypatch.setattr(codex_cli.os, "replace", checked)
        assert codex_cli.sync_codex_home(home) is True
    finally:
        os.umask(old_umask)
    assert seen == [(0o600, True)]
    assert _mode(codex_user / "auth.json") == 0o600
    assert json.loads((codex_user / "auth.json").read_text()) == ROTATED


def test_two_homes_sync_back_concurrently(codex_user, isolated_env):
    homes = [isolated_env / "h1", isolated_env / "h2"]
    contents = [json.dumps({"tokens": {"refresh_token": f"home-{i}" * 2000}}) for i in (1, 2)]
    for _ in range(15):
        for home, content in zip(homes, contents):
            _refresh(home, content)
        barrier = threading.Barrier(2)
        errors = []

        def worker(home):
            try:
                barrier.wait()
                codex_cli.sync_codex_home(home)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(h,)) for h in homes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert (codex_user / "auth.json").read_text() in contents
        assert _mode(codex_user / "auth.json") == 0o600
        assert _leftovers(codex_user) == []


# --- Important 3: no home paths in status payloads ----------------------------------------


def test_scrub_replaces_the_home_directory(monkeypatch, tmp_path):
    home = str(Path.home())
    assert base.scrub(f"ENOENT: {home}/.local/share/claude/x") == "ENOENT: ~/.local/share/claude/x"
    assert base.scrub(f"at {os.path.realpath(home)}/lib/cli.js:1") == "at ~/lib/cli.js:1"
    assert base.scrub(f"cwd {home}") == "cwd ~"
    assert base.scrub(f"{home}2/x") == f"{home}2/x"  # a sibling directory, not the home
    real = tmp_path / "real-home"
    real.mkdir()
    (tmp_path / "link-home").symlink_to(real)
    monkeypatch.setenv("HOME", str(tmp_path / "link-home"))
    text = base.scrub(f"{tmp_path}/link-home/a and {real.resolve()}/b")
    assert text == "~/a and ~/b"


def test_home_paths_never_reach_the_status_payload(fakes, offline, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_MODE", "homepath")
    home = str(Path.home())
    client = TestClient(web.create_app(_settings("claude")))
    body = client.post("/api/providers/test").json()
    assert body["ok"] is False and home not in json.dumps(body)
    assert "~/.local/share/claude" in body["error"]
    saved = (Settings.from_env().run_dir / probe.LAST_TEST_FILE).read_text()
    assert home not in saved
    res = client.get("/api/status")
    assert home not in res.text and os.path.realpath(home) not in res.text
    chat = res.json()["providers"]["chat"]
    assert "~/.local/share/claude" in chat["detail"]


def test_old_last_test_entries_are_scrubbed_on_read(fakes, offline):  # noqa: F811
    home = str(Path.home())
    settings = Settings.from_env()
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / probe.LAST_TEST_FILE).write_text(
        json.dumps({"claude": {"ok": False, "error": f"boom in {home}/x"}})
    )
    res = TestClient(web.create_app(_settings("claude"))).get("/api/status")
    assert home not in res.text
    assert res.json()["providers"]["chat"]["last_test"]["error"] == "boom in ~/x"


# --- Minor 4: reasoning effort ------------------------------------------------------------


def test_codex_reasoning_effort_is_passed_when_valid(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AUTOONTOLOGY_CODEX_REASONING_EFFORT", "high")
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    (call,) = fakes()
    argv = call["argv"]
    assert argv[argv.index('model_reasoning_effort="high"') - 1] == "-c"


@pytest.mark.parametrize("value", ["hihg", 'high" x="1', "none"])
def test_bad_codex_reasoning_effort_is_400_and_doctor_fail(fakes, monkeypatch, value):  # noqa: F811
    monkeypatch.setenv("AUTOONTOLOGY_CODEX_REASONING_EFFORT", value)
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 400 and res.json()["error"]["type"] == "invalid_reasoning_effort"
    assert fakes() == []
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "codex")
    check = doctor.check_profile(Settings.from_env())
    assert check.status == "FAIL" and "AUTOONTOLOGY_CODEX_REASONING_EFFORT" in check.detail


# --- Minor 8: concurrent last-test writes -------------------------------------------------


def test_save_result_is_safe_under_concurrent_writers(isolated_env):
    settings = Settings.from_env()
    errors = []

    def worker(i):
        try:
            for j in range(20):
                probe.save_result(settings, {"provider": f"p{i}", "ok": True, "at": str(j)})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = probe.last_tests(settings)
    assert sorted(data) == [f"p{i}" for i in range(6)]
    assert all(v["at"] == "19" for v in data.values())
    assert [p.name for p in settings.run_dir.iterdir() if "tmp" in p.name] == []


# --- Minor 9: word-bounded status codes ---------------------------------------------------


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("unexpected status 401 Unauthorized: Missing bearer", 401),
        ("HTTP 401", 401),
        ("exceeded retry limit, last status: 429 Too Many Requests", 429),
        ("You've hit your usage limit", 429),
        ("token 4291 of the stream was malformed", 502),
        ("request id 14010 failed", 502),
        ("used 1429 tokens", 502),
    ],
)
def test_codex_error_codes_are_word_bounded(message, status):
    assert codex_cli._error(message, "gpt-6-astra", 1).status == status


# --- Minor 10: Registry.status does not swallow TypeError --------------------------------


def test_registry_status_propagates_a_providers_type_error():
    calls = []

    class Broken:
        name = "claude"

        def status(self, *, refresh=False):
            calls.append(refresh)
            raise TypeError("bug inside status")

    class Legacy:
        name = "claude"

        def status(self):
            return "legacy-ok"

    with pytest.raises(TypeError, match="bug inside status"):
        Registry(_settings("claude"), providers={"claude": Broken()}).status(refresh=True)
    assert calls == [True]
    assert Registry(_settings("claude"), providers={"claude": Legacy()}).status() == "legacy-ok"


# --- test gaps: process groups, disconnect, env, argv -------------------------------------


def _gone(pid: int, wait: float = 5.0) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _grandchild_pid(path: Path, wait: float = 10.0) -> int:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("the fake CLI never started its child")


def test_cancel_kills_the_whole_process_group(fakes, isolated_env, monkeypatch):  # noqa: F811
    from autoontology.web.providers.claude_cli import ClaudeCLIProvider

    marker = isolated_env / "grandchild.pid"
    monkeypatch.setenv("FAKE_MODE", "grandchild")
    monkeypatch.setenv("FAKE_GRANDCHILD", str(marker))
    provider = ClaudeCLIProvider(Settings.from_env().home, ["sonnet"])
    req = parse_request({"messages": [{"role": "user", "content": "x"}]}, "sonnet")
    start = time.monotonic()

    async def go():
        task = asyncio.ensure_future(provider.complete(req))
        while not (marker.is_file() and marker.read_text().strip()):
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert time.monotonic() - start < 15, "the cancel waited for the CLI's child to exit"
    assert _gone(_grandchild_pid(marker)), "the CLI's child survived the cancel"


def test_timeout_kills_the_whole_process_group(fakes, isolated_env, monkeypatch):  # noqa: F811
    marker = isolated_env / "grandchild.pid"
    monkeypatch.setenv("FAKE_MODE", "grandchild")
    monkeypatch.setenv("FAKE_GRANDCHILD", str(marker))
    monkeypatch.setenv("AUTOONTOLOGY_PROVIDER_TIMEOUT_S", "2")
    start = time.monotonic()
    res = _chat(_client(), "openai/claude:sonnet")
    assert res.status_code == 504
    # asyncio's wait() also waits for the pipes: a surviving child would hold them for 60 s
    assert time.monotonic() - start < 15, "the timeout waited for the CLI's child to exit"
    assert _gone(_grandchild_pid(marker)), "the CLI's child survived the timeout"
    assert _gone(fakes()[0]["pid"])


def test_client_disconnect_is_499_and_kills_the_cli(fakes, isolated_env, monkeypatch):  # noqa: F811
    """Through a real uvicorn server: the client hangs up while the CLI runs."""
    import socket

    import httpx
    import uvicorn

    marker = isolated_env / "grandchild.pid"
    monkeypatch.setenv("FAKE_MODE", "grandchild")
    monkeypatch.setenv("FAKE_GRANDCHILD", str(marker))
    inner = web.create_app(_settings("claude"))
    statuses: list[int] = []

    async def recording(scope, receive, send):
        async def send_and_record(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
            await send(message)

        await inner(scope, receive, send_and_record)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(recording, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        body = {"model": "openai/claude:sonnet", "messages": [{"role": "user", "content": "x"}]}
        with pytest.raises(httpx.ReadTimeout):
            httpx.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json=body,
                timeout=httpx.Timeout(10.0, read=1.5),
            )
        grandchild = _grandchild_pid(marker)
        deadline = time.monotonic() + 10
        while not statuses and time.monotonic() < deadline:
            time.sleep(0.05)
        assert statuses == [499]
        assert _gone(grandchild) and _gone(fakes()[0]["pid"])
    finally:
        server.should_exit = True
        thread.join(15)


def test_claude_strips_both_api_keys_and_sets_no_color(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak")
    monkeypatch.setenv("NO_COLOR", "")
    assert _chat(_client(), "openai/claude:sonnet", response_format=FMT).status_code == 200
    (call,) = fakes()
    env = call["env"]
    assert env["ANTHROPIC_API_KEY"] is None and env["ANTHROPIC_AUTH_TOKEN"] is None
    assert env["NO_COLOR"] == "1"


def test_codex_strips_both_api_keys_and_sets_no_color(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "should-not-leak")
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    (call,) = fakes()
    env = call["env"]
    assert env["OPENAI_API_KEY"] is None and env["CODEX_API_KEY"] is None
    assert env["NO_COLOR"] == "1"


def test_codex_argv_is_exactly_the_specified_one(fakes):  # noqa: F811
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    (call,) = fakes()
    argv = call["argv"]
    workdir = argv[argv.index("-C") + 1]
    disabled = [x for f in DISABLED_FEATURES for x in ("--disable", f)]
    assert len(DISABLED_FEATURES) == 22
    assert argv == [
        "exec",
        "-m",
        "gpt-6-astra",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "-s",
        "read-only",
        "-C",
        workdir,
        "--json",
        "--color",
        "never",
        "-c",
        'web_search="disabled"',
        *disabled,
        "-o",
        f"{workdir}/last.txt",
        "--output-schema",
        f"{workdir}/schema.json",
        "-",
    ]


def test_codex_call_raises_no_provider_error_for_a_sync_failure(fakes, monkeypatch):  # noqa: F811
    """A failing write-back after the call is logged, never turned into a failed answer."""
    monkeypatch.setenv("FAKE_MODE", "refresh")

    def broken(src, dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(codex_cli.os, "replace", broken)
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 200, res.text
    private = Settings.from_env().home.resolve() / "run" / "codex-home" / "auth.json"
    assert not private.is_symlink() and json.loads(private.read_text()) == ROTATED


# --- re-review follow-ups (N1-N3) ---------------------------------------------------------


def test_child_env_failure_is_503_not_500(fakes, monkeypatch):  # noqa: F811
    def unwritable(home):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(codex_cli, "ensure_codex_home", unwritable)
    res = _chat(_client("codex"), "openai/codex:gpt-6-astra", response_format=FMT)
    assert res.status_code == 503, res.text
    assert res.json()["error"]["type"] == "provider_unavailable"
    assert fakes() == []


def test_a_failed_disconnect_watcher_is_not_a_disconnect():
    from autoontology.web.providers.router import until_disconnected

    class Req:
        async def receive(self):
            raise RuntimeError("Unexpected message received: http.request")

    async def answer():
        await asyncio.sleep(0.2)
        return "answer"

    async def go():
        return await until_disconnected(Req(), answer())

    assert asyncio.run(go()) == "answer"


def test_a_token_renamed_in_during_the_sync_is_not_deleted(codex_user, isolated_env, monkeypatch):
    home = isolated_env / "h"
    link = _refresh(home, json.dumps({"tokens": {"refresh_token": "first"}}))
    newer = json.dumps({"tokens": {"refresh_token": "second"}})
    real = codex_cli._write_back

    def copy_then_codex_refreshes_again(data, seen, dest):
        real(data, seen, dest)
        # a second codex call renames an even newer token over the private file
        tmp = link.with_name("auth.json.codex-new")
        tmp.write_text(newer)
        os.replace(tmp, link)

    monkeypatch.setattr(codex_cli, "_write_back", copy_then_codex_refreshes_again)
    assert codex_cli.sync_codex_home(home) is False
    assert not link.is_symlink() and link.read_text() == newer  # not unlinked
    monkeypatch.setattr(codex_cli, "_write_back", real)
    os.utime(link, (time.time() + 20,) * 2)
    assert codex_cli.sync_codex_home(home) is True  # the next sync delivers it
    assert (codex_user / "auth.json").read_text() == newer and link.is_symlink()


# --- codex round 1, finding 1: the newest token is never lost ---------------------------


def _same_shape_token(link: Path, content: str, mtime_ns: int) -> None:
    """codex renaming token *content* over the private file, with the given mtime."""
    tmp = link.with_name("auth.json.codex-new")
    tmp.write_text(content)
    os.utime(tmp, ns=(mtime_ns, mtime_ns))
    os.replace(tmp, link)


def _sync_until_linked(home: Path, link: Path, rounds: int = 4) -> None:
    for _ in range(rounds):
        if codex_cli.sync_codex_home(home) and link.is_symlink():
            return


def test_a_token_renamed_in_while_the_old_one_is_read_is_delivered(
    codex_user, isolated_env, monkeypatch
):
    """Token A is read, then codex renames B (same size and mtime) over the private file
    before its metadata is taken: B must reach the user's auth.json, never be deleted."""
    home = isolated_env / "h"
    first = json.dumps({"tokens": {"refresh_token": "AAAAAA"}})
    second = json.dumps({"tokens": {"refresh_token": "BBBBBB"}})
    link = _refresh(home, first)
    a_ino = link.stat().st_ino
    a_mtime = link.stat().st_mtime_ns
    fired = []

    def interleave() -> None:
        if not fired:
            fired.append(True)
            _same_shape_token(link, second, a_mtime)

    real_read, real_read_bytes = os.read, Path.read_bytes

    def read(fd, n):
        data = real_read(fd, n)
        if os.fstat(fd).st_ino == a_ino and not data:  # A fully read
            interleave()
        return data

    def read_bytes(self):
        data = real_read_bytes(self)
        if self == link:
            interleave()
        return data

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    codex_cli.sync_codex_home(home)
    monkeypatch.undo()
    monkeypatch.setenv("CODEX_HOME", str(codex_user))
    assert fired
    _sync_until_linked(home, link)
    assert (codex_user / "auth.json").read_text() == second
    assert link.is_symlink()
    assert _mode(codex_user / "auth.json") == 0o600


def test_a_token_renamed_in_right_before_the_relink_is_delivered(
    codex_user, isolated_env, monkeypatch
):
    """B arrives after the identity check, just before the private file is removed."""
    home = isolated_env / "h"
    first = json.dumps({"tokens": {"refresh_token": "AAAAAA"}})
    second = json.dumps({"tokens": {"refresh_token": "BBBBBB"}})
    link = _refresh(home, first)
    a_mtime = link.stat().st_mtime_ns
    fired = []

    def interleave(path) -> None:
        if not fired and Path(path) == link and not link.is_symlink():
            fired.append(True)
            _same_shape_token(link, second, a_mtime)

    real_unlink, real_rename = Path.unlink, os.rename

    def unlink(self, missing_ok=False):
        interleave(self)
        return real_unlink(self, missing_ok=missing_ok)

    def rename(src, dst, *a, **kw):
        interleave(src)
        return real_rename(src, dst, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(os, "rename", rename)
    codex_cli.sync_codex_home(home)
    monkeypatch.undo()
    monkeypatch.setenv("CODEX_HOME", str(codex_user))
    assert fired
    _sync_until_linked(home, link)
    assert (codex_user / "auth.json").read_text() == second
    assert link.is_symlink()
    assert not [p.name for p in link.parent.iterdir() if p.name.startswith(".auth.json")]
