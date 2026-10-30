# SPDX-License-Identifier: MIT
import os
import socket
import subprocess
import sys
import time

import pytest

from autoontology.config import Settings
from autoontology.engine.supervisor import (
    ProcessSpec,
    ProcessStatus,
    Supervisor,
    SupervisorError,
    build_specs,
    child_env,
    format_status,
    pid_alive,
)

MARK = "autoontology-fake-child"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_spec(name="fake", code="import time; time.sleep(60)", urls=("http://x/live",)):
    # The trailing marker argument makes the command line identifiable (PID-reuse guard).
    return ProcessSpec(
        name=name,
        argv=[sys.executable, "-c", code, MARK],
        cwd=os.getcwd(),
        port=free_port(),
        health_urls=urls,
        match=MARK,
    )


class Probe:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.healthy if not callable(self.healthy) else self.healthy(url)


@pytest.fixture
def make_sup(tmp_path):
    created = []

    def _make(specs, probe=None, **kw):
        kw.setdefault("ready_timeout", 3)
        kw.setdefault("stop_timeout", 3)
        kw.setdefault("poll_interval", 0.05)
        sup = Supervisor(
            tmp_path / "run",
            tmp_path / "logs",
            specs,
            probe=probe or Probe(),
            echo=lambda m: messages.append(m),
            **kw,
        )
        created.append(sup)
        return sup

    messages: list[str] = []
    _make.messages = messages
    yield _make
    for sup in created:
        sup.down()


def test_up_is_idempotent_and_down_stops(make_sup):
    spec = fake_spec()
    sup = make_sup([spec])
    sup.up()
    pid = sup.read_pid(spec)
    assert pid and pid_alive(pid)
    assert sup.pid_file(spec).read_text().strip() == str(pid)
    assert sup.log_path(spec).is_file()

    sup.up()
    assert sup.read_pid(spec) == pid
    assert any("already running" in m for m in make_sup.messages)

    sup.down()
    assert not pid_alive(pid)
    assert not sup.pid_file(spec).exists()


def test_stale_and_garbage_pid_files_are_cleaned(make_sup):
    spec = fake_spec()
    sup = make_sup([spec])
    sup.run_dir.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sup.pid_file(spec).write_text(f"{dead.pid}\n")
    assert sup.read_pid(spec) is None
    assert not sup.pid_file(spec).exists()

    sup.pid_file(spec).write_text("not-a-pid")
    assert sup.read_pid(spec) is None
    assert not sup.pid_file(spec).exists()


def test_reused_pid_of_foreign_process_is_not_ours(make_sup):
    spec = fake_spec()
    sup = make_sup([spec])
    sup.run_dir.mkdir(parents=True)
    sup.pid_file(spec).write_text(str(os.getpid()))  # alive, but not our command line
    assert sup.read_pid(spec) is None
    sup.down()  # must not signal the test runner
    assert pid_alive(os.getpid())


def test_status_rows_and_format(make_sup):
    spec = fake_spec()
    probe = Probe()
    sup = make_sup([spec], probe=probe)
    [row] = sup.status()
    assert (row.pid, row.healthy) == (None, False)
    sup.up()
    [row] = sup.status()
    assert row.running and row.healthy and row.port == spec.port
    probe.healthy = False
    assert sup.status()[0].healthy is False
    table = format_status([row, ProcessStatus("service", None, 7800, False, sup.log_path(spec))])
    lines = table.splitlines()
    assert lines[0].split() == ["NAME", "PID", "PORT", "HEALTHY", "LOG"]
    assert lines[1].split()[:4] == ["fake", str(row.pid), str(spec.port), "yes"]
    assert lines[2].split()[:4] == ["service", "-", "7800", "no"]


def test_waits_for_health_urls_in_order(make_sup):
    seen = []
    start = time.monotonic()

    def healthy(url):
        seen.append(url)
        return url.endswith("live") or time.monotonic() - start > 0.3

    spec = fake_spec(urls=("http://x/live", "http://x/ready"))
    sup = make_sup([spec], probe=Probe(healthy))
    sup.up()
    assert seen.index("http://x/live") < seen.index("http://x/ready")
    assert any("healthy" in m for m in make_sup.messages)


def test_timeout_reports_log_tail_and_stops_child(make_sup):
    code = "import time\nfor i in range(40): print('line', i, flush=True)\ntime.sleep(60)"
    spec = fake_spec(code=code)
    sup = make_sup([spec], probe=Probe(False), ready_timeout=1)
    with pytest.raises(SupervisorError) as err:
        sup.up()
    msg = str(err.value)
    assert "not healthy" in msg and "line 39" in msg and "line 5\n" not in msg
    assert sup.read_pid(spec) is None


def test_early_exit_is_reported(make_sup):
    spec = fake_spec(code="import sys; print('boom'); sys.exit(3)")
    sup = make_sup([spec], probe=Probe(False))
    with pytest.raises(SupervisorError, match="exited before"):
        sup.up()


def test_port_taken_by_foreign_process(make_sup):
    spec = fake_spec()
    with socket.socket() as s:
        s.bind(("127.0.0.1", spec.port))
        s.listen()
        sup = make_sup([spec])
        with pytest.raises(SupervisorError, match="already in use"):
            sup.up()


def test_down_escalates_to_sigkill(make_sup):
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    spec = fake_spec(code=code)
    sup = make_sup([spec], stop_timeout=0.5)
    sup.up()
    pid = sup.read_pid(spec)
    time.sleep(0.3)  # let the child install its SIGTERM handler
    sup.down()
    assert not pid_alive(pid)
    assert any("SIGKILL" in m for m in make_sup.messages)


def test_down_also_stops_grandchildren(make_sup):
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(f'GC={p.pid}', flush=True)\n"
        "time.sleep(60)\n"
    )
    spec = fake_spec(code=code)
    sup = make_sup([spec])
    sup.up()
    deadline = time.monotonic() + 5
    grandchild = None
    while grandchild is None and time.monotonic() < deadline:
        words = sup.log_path(spec).read_text().split()
        grandchild = next((int(w[3:]) for w in words if w.startswith("GC=")), None)
        time.sleep(0.05)
    assert grandchild and pid_alive(grandchild)
    sup.down()
    time.sleep(0.2)
    assert not pid_alive(grandchild)


def test_partial_up_stops_what_it_started(make_sup):
    first, second = fake_spec("first"), fake_spec("second", urls=("http://x/second",))
    sup = make_sup([first, second], probe=Probe(lambda url: "second" not in url), ready_timeout=0.5)
    with pytest.raises(SupervisorError, match="Rolled back") as err:
        sup.up()
    assert "first: stopped" in str(err.value) and "second: stopped" in str(err.value)
    assert sup.read_pid(first) is None and sup.read_pid(second) is None


def test_partial_up_keeps_instances_started_earlier(make_sup):
    first, second = fake_spec("first"), fake_spec("second", urls=("http://x/second",))
    probe = Probe(lambda url: "second" not in url)
    sup = make_sup([first], probe=probe)
    sup.up()
    kept = sup.read_pid(first)
    sup2 = make_sup([first, second], probe=probe, ready_timeout=0.5)
    with pytest.raises(SupervisorError, match="left running") as err:
        sup2.up()
    assert f"first (pid {kept})" in str(err.value)
    assert sup2.read_pid(first) == kept


def test_stop_escalates_when_a_grandchild_ignores_sigterm(make_sup):
    code = (
        "import subprocess, sys, time\n"
        "gc = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'print("ready", flush=True); time.sleep(60)\'\n'
        "p = subprocess.Popen([sys.executable, '-c', gc], stdout=subprocess.PIPE)\n"
        "p.stdout.readline()\n"
        "print(f'GC={p.pid}', flush=True)\n"
        "time.sleep(60)\n"
    )
    spec = fake_spec(code=code)
    sup = make_sup([spec], stop_timeout=0.5)
    sup.up()
    deadline = time.monotonic() + 5
    grandchild = None
    while grandchild is None and time.monotonic() < deadline:
        words = sup.log_path(spec).read_text().split()
        grandchild = next((int(w[3:]) for w in words if w.startswith("GC=")), None)
        time.sleep(0.05)
    assert grandchild and pid_alive(grandchild)
    sup.down()
    assert not pid_alive(grandchild)
    assert any("SIGKILL" in m for m in make_sup.messages)


def test_stop_is_parallel(make_sup):
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    specs = [fake_spec("a", code=code), fake_spec("b", code=code)]
    sup = make_sup(specs, stop_timeout=1)
    sup.up()
    time.sleep(0.3)  # let both children install their SIGTERM handler
    start = time.monotonic()
    sup.down()
    elapsed = time.monotonic() - start
    assert elapsed < 1.9, f"sequential stop ({elapsed:.1f}s)"
    assert sum("SIGKILL" in m for m in make_sup.messages) == 2


def test_pid_alive_does_not_reap_popen_children():
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    deadline = time.monotonic() + 5
    while pid_alive(proc.pid):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert proc.wait(timeout=5) == 3


def test_child_env_shell_wins_over_dotenv(isolated_env, monkeypatch):
    env_file = isolated_env / "precedence.env"
    env_file.write_text("SHARED_KEY=from-dotenv\nDOTENV_ONLY=yes\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    settings = Settings(home=isolated_env / "home")  # from_env would load it into os.environ
    env = child_env(settings, base={"SHARED_KEY": "from-shell"})
    assert env["SHARED_KEY"] == "from-shell" and env["DOTENV_ONLY"] == "yes"


def test_build_specs_and_child_env(isolated_env, monkeypatch):
    settings = Settings.from_env()
    # service first: it must not depend on the backend, and it serves the LLM proxy
    service, backend = build_specs(settings)
    assert backend.name == "backend" and backend.port == 3101
    assert "--factory" in backend.argv and "3101" in backend.argv
    assert backend.argv[2] == "autoontology.engine.backend"
    assert "3001" not in " ".join(backend.argv)
    assert backend.cwd.name == "auto-ontology"
    assert backend.health_urls == (
        "http://127.0.0.1:3101/api/health/live",
        "http://127.0.0.1:3101/api/health",
    )
    assert service.health_urls == ("http://127.0.0.1:7800/health/live",)
    assert "autoontology.web.app:app" in service.argv
    env = child_env(settings, base={"PATH": "/usr/bin"})
    assert env["INGESTION_SERVICE_URL"] == "http://127.0.0.1:7800"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["POSTGRES_DATABASE"] == "autoontology" and "POSTGRES_PASSWORD" in env
    assert env["REASONING_MODEL"] == "openai/qwen3:8b" and env["EMBED_MODEL"] == "bge-m3"
    assert env["LLM_INVOKE_TIMEOUT_S"] == "180" and env["SEMANTIC_LLM_MAX_INFLIGHT"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_spawn_failure_rolls_back_what_this_call_started(make_sup, monkeypatch):
    """Review #18: the service starts, spawning the backend fails: the service must be
    stopped like on a readiness failure."""
    first, second = fake_spec("first"), fake_spec("second")
    sup = make_sup([first, second])
    real_spawn = sup._spawn

    def spawn(spec):
        if spec.name == "second":
            raise OSError("exec format error")
        return real_spawn(spec)

    monkeypatch.setattr(sup, "_spawn", spawn)
    with pytest.raises(SupervisorError, match="Rolled back") as err:
        sup.up()
    text = str(err.value)
    assert "second: could not start: OSError" in text and "first: stopped" in text
    assert sup.read_pid(first) is None and sup.read_pid(second) is None


# --- v2: restart one child with fresh settings -------------------------------------------


def test_child_env_marks_derived_keys_and_keeps_dotenv_selection_out(isolated_env, monkeypatch):
    from autoontology.config import Settings as S
    from autoontology.config import load_env

    env_file = isolated_env / "sel.env"
    env_file.write_text(
        "AUTOONTOLOGY_CHAT_PROVIDER=openai\nAUTOONTOLOGY_OPENAI_CHAT_MODEL=m1\nOTHER=1\n"
    )
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    load_env()
    settings = S.from_env()
    assert settings.chat_provider == "openai"
    env = child_env(settings)
    # the child re-reads .env itself: a .env selection must not look like a shell value
    assert "AUTOONTOLOGY_CHAT_PROVIDER" not in env and env["OTHER"] == "1"
    derived = set(env["AUTOONTOLOGY_DERIVED_ENV"].split(","))
    assert {"REASONING_MODEL", "EMBED_MODEL"} <= derived
    assert env["REASONING_MODEL"] == "openai/m1"
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "ollama")  # a real shell value passes
    assert child_env(S.from_env())["AUTOONTOLOGY_CHAT_PROVIDER"] == "ollama"


def test_child_env_marks_dotenv_copies_as_derived(isolated_env, monkeypatch):
    """A .env value a child got from its parent is not a shell value there: after the .env
    is rewritten, the child (a restarted service, the restart helper) reads the new value."""
    from autoontology.config import Settings as S
    from autoontology.config import load_env, shell_value, user_env

    env_file = isolated_env / "copy.env"
    env_file.write_text(
        "AUTOONTOLOGY_CHAT_PROVIDER=openai\nAUTOONTOLOGY_OPENAI_BASE_URL=http://old.test/v1\n"
        "AUTOONTOLOGY_OPENAI_API_KEY=sk-oldkeyvalue\nAUTOONTOLOGY_OPENAI_CHAT_MODEL=m1\n"
    )
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    monkeypatch.setenv("SHELL_ONLY_KEY", "kept")
    load_env()
    env = child_env(S.from_env())
    derived = set(env["AUTOONTOLOGY_DERIVED_ENV"].split(","))
    assert {
        "AUTOONTOLOGY_OPENAI_BASE_URL",
        "AUTOONTOLOGY_OPENAI_API_KEY",
        "AUTOONTOLOGY_OPENAI_CHAT_MODEL",
    } <= derived
    assert "SHELL_ONLY_KEY" not in derived and env["SHELL_ONLY_KEY"] == "kept"
    # now be the child: its environment is exactly `env`; then the user rewrites .env
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    env_file.write_text("AUTOONTOLOGY_CHAT_PROVIDER=claude\nAUTOONTOLOGY_OPENAI_CHAT_MODEL=m2\n")
    assert shell_value("AUTOONTOLOGY_OPENAI_CHAT_MODEL") is None
    child = S.from_env()
    assert child.chat_provider == "claude" and child.openai_chat_model == "m2"
    assert child.openai_api_key == "" and child.openai_base_url == ""
    assert "AUTOONTOLOGY_OPENAI_API_KEY" not in user_env()


def test_child_env_keeps_a_shell_value_that_shadows_dotenv(isolated_env, monkeypatch):
    from autoontology.config import Settings as S
    from autoontology.config import load_env

    env_file = isolated_env / "shadow.env"
    env_file.write_text("AUTOONTOLOGY_OPENAI_CHAT_MODEL=from-dotenv\n")
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    monkeypatch.setenv("AUTOONTOLOGY_OPENAI_CHAT_MODEL", "from-shell")
    load_env()
    env = child_env(S.from_env())
    assert env["AUTOONTOLOGY_OPENAI_CHAT_MODEL"] == "from-shell"
    assert "AUTOONTOLOGY_OPENAI_CHAT_MODEL" not in env["AUTOONTOLOGY_DERIVED_ENV"].split(",")
