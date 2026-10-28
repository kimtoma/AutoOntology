# SPDX-License-Identifier: MIT
"""Supervise the two AutoOntology child processes: upstream ``backend`` and our ``service``.

``up``/``down``/``status`` keep PID files in ``<home>/run`` and logs in
``<home>/logs/<name>.log``. Children run in their own session (process group) so ``down``
also stops any worker subprocesses they spawn.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from autoontology.config import (
    DERIVED_ENV_VAR,
    REPO_ROOT,
    SELECTION_VARS,
    VENDOR_DIR,
    Settings,
    env_file_path,
    expand_model_profile,
    postgres_env,
    shell_value,
    user_env,
)

READY_TIMEOUT_S = 90.0
STOP_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.5
LOG_TAIL_LINES = 30

HealthProbe = Callable[[str], bool]


class SupervisorError(RuntimeError):
    pass


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    argv: list[str]
    cwd: Path
    port: int
    #: Health URLs waited for in order (all must return 200).
    health_urls: tuple[str, ...]
    #: Substring of the command line that identifies our process (guards PID reuse).
    match: str
    env: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ProcessStatus:
    name: str
    pid: int | None
    port: int
    healthy: bool
    log_path: Path

    @property
    def running(self) -> bool:
        return self.pid is not None


def child_env(settings: Settings, base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for children. Never logged.

    Precedence is the project-wide rule: shell environment > ``.env`` (python-dotenv
    ``override=False``). On top come values AutoOntology derives from ``Settings`` (which was
    resolved with the same rule): ``POSTGRES_*``, the model-profile expansion and
    ``INGESTION_SERVICE_URL``.

    The provider selection keys reach the child only when they came from the shell (the
    child re-reads ``.env`` itself, so a ``.env`` value must not look like a shell value
    there), and the expansion keys are listed in ``AUTOONTOLOGY_DERIVED_ENV`` so a child
    re-derives them instead of mistaking them for user settings.
    """
    if base is None:
        env = user_env()  # minus what a parent AutoOntology process derived
        for var in SELECTION_VARS.values():
            if var in env and shell_value(var) is None:
                del env[var]
    else:
        env = dict(base)
    path = env_file_path()
    if path.is_file():
        for key, value in dotenv_values(path).items():
            if value is not None and key not in SELECTION_VARS.values():
                env.setdefault(key, value)
    env.update(postgres_env(settings))
    env["INGESTION_SERVICE_URL"] = settings.service_url
    models = expand_model_profile(settings)
    derived = sorted(k for k, v in models.items() if env.get(k) != v)
    env.update(models)
    env[DERIVED_ENV_VAR] = ",".join(derived)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def build_specs(settings: Settings) -> list[ProcessSpec]:
    env = child_env(settings)
    backend_app = "auto_ontology.server.__main__:create_app"
    service_app = "autoontology.web.app:app"
    backend = ProcessSpec(
        name="backend",
        argv=[
            sys.executable,
            "-m",
            "uvicorn",
            backend_app,
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(settings.backend_port),
        ],
        cwd=VENDOR_DIR,
        port=settings.backend_port,
        health_urls=(
            f"{settings.backend_url}/api/health/live",
            f"{settings.backend_url}/api/health",
        ),
        match=backend_app,
        env=env,
    )
    service = ProcessSpec(
        name="service",
        argv=[
            sys.executable,
            "-m",
            "uvicorn",
            service_app,
            "--host",
            "127.0.0.1",
            "--port",
            str(settings.port),
        ],
        cwd=REPO_ROOT,
        port=settings.port,
        health_urls=(f"{settings.service_url}/health/live",),
        match=service_app,
        env=env,
    )
    # The service starts first: it serves /v1/* (the ollama profile's chat endpoint) and
    # /health/live without needing the backend; `down` stops in reverse order.
    return [service, backend]


# --- process helpers -------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """True if ``pid`` exists and is not a zombie.

    Never ``waitpid``s: reaping a ``Popen`` child behind its back makes ``Popen`` report a
    wrong return code. The supervisor uses ``Popen.poll()`` for processes it spawned.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = _ps(pid, "stat")
    return stat is not None and not stat.startswith("Z")


def _ps(pid: int, column: str) -> str | None:
    proc = subprocess.run(
        ["ps", "-o", f"{column}=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    out = proc.stdout.strip()
    return out or None


def pid_command(pid: int) -> str | None:
    return _ps(pid, "command")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def listening_pids(port: int) -> list[int]:
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted({int(x) for x in proc.stdout.split() if x.strip().isdigit()})


def group_alive(pgid: int) -> bool:
    """True while any process is left in process group ``pgid``."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _leads_group(pid: int) -> bool:
    try:
        return os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError):
        return False


def _send(pid: int, pgid: int | None, sig: int) -> None:
    """Signal the whole group when the child leads one (it does: own session), else the pid."""
    try:
        if pgid is not None:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS answers EPERM for a group whose members are all zombies (exited, not yet
        # reaped by their parent): nothing left to signal
        pass


def tail(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    if not path.is_file():
        return "(no log)"
    data = path.read_bytes()[-64_000:].decode("utf-8", errors="replace")
    return "\n".join(data.splitlines()[-lines:])


# --- supervisor ------------------------------------------------------------------


class Supervisor:
    def __init__(
        self,
        run_dir: Path,
        logs_dir: Path,
        specs: list[ProcessSpec],
        *,
        probe: HealthProbe = http_ok,
        ready_timeout: float = READY_TIMEOUT_S,
        stop_timeout: float = STOP_TIMEOUT_S,
        poll_interval: float = POLL_INTERVAL_S,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.run_dir = run_dir
        self.logs_dir = logs_dir
        self.specs = specs
        self.probe = probe
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self.echo = echo
        #: Children this instance spawned (polled instead of waitpid'ed behind Popen's back).
        self._procs: dict[int, subprocess.Popen] = {}

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> Supervisor:
        return cls(settings.run_dir, settings.logs_dir, build_specs(settings), **kwargs)

    def spec(self, name: str) -> ProcessSpec:
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise SupervisorError(f"no process named {name!r}")

    # paths
    def pid_file(self, spec: ProcessSpec) -> Path:
        return self.run_dir / f"{spec.name}.pid"

    def log_path(self, spec: ProcessSpec) -> Path:
        return self.logs_dir / f"{spec.name}.log"

    def owned_pids(self) -> set[int]:
        return {pid for spec in self.specs if (pid := self.read_pid(spec)) is not None}

    def read_pid(self, spec: ProcessSpec) -> int | None:
        """Live PID from the PID file; removes the file if the process is gone or foreign."""
        path = self.pid_file(spec)
        try:
            pid = int(path.read_text().strip())
        except FileNotFoundError:
            return None
        except ValueError:
            path.unlink(missing_ok=True)
            return None
        command = pid_command(pid) if pid_alive(pid) else None
        if command is None or spec.match not in command:
            path.unlink(missing_ok=True)
            return None
        return pid

    def healthy(self, spec: ProcessSpec) -> bool:
        return all(self.probe(url) for url in spec.health_urls)

    def status(self) -> list[ProcessStatus]:
        out = []
        for spec in self.specs:
            pid = self.read_pid(spec)
            out.append(
                ProcessStatus(
                    name=spec.name,
                    pid=pid,
                    port=spec.port,
                    healthy=pid is not None and self.healthy(spec),
                    log_path=self.log_path(spec),
                )
            )
        return out

    # start / wait / stop
    def _spawn(self, spec: ProcessSpec) -> subprocess.Popen:
        if port_in_use(spec.port):
            holders = listening_pids(spec.port)
            raise SupervisorError(
                f"{spec.name}: port {spec.port} is already in use by another process "
                f"(pids {holders or 'unknown'})"
            )
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_path(spec)
            with log_path.open("ab") as log:
                stamp = datetime.now().isoformat(timespec="seconds")
                log.write(f"\n=== {stamp} starting {spec.name} on port {spec.port}\n".encode())
                log.flush()
                proc = subprocess.Popen(
                    spec.argv,
                    cwd=spec.cwd,
                    env=spec.env or None,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:  # missing executable, unwritable log dir, ...
            raise SupervisorError(
                f"{spec.name}: could not start: {type(exc).__name__}: {exc}"
            ) from exc
        # registered at once: whatever fails next, this instance knows and stops the child
        self._procs[proc.pid] = proc
        try:
            self.pid_file(spec).write_text(f"{proc.pid}\n")
        except OSError as exc:
            self._abort_spawn(spec, proc)
            raise SupervisorError(
                f"{spec.name}: could not start: recording pid {proc.pid} "
                f"failed ({type(exc).__name__}: {exc}); the child was "
                "stopped"
            ) from exc
        return proc

    def _abort_spawn(self, spec: ProcessSpec, proc: subprocess.Popen) -> None:
        """Undo a half-done spawn: stop the child's group, reap it, forget its pid."""
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        self._procs.pop(proc.pid, None)
        with contextlib.suppress(OSError):
            if self.pid_file(spec).read_text().strip() == str(proc.pid):
                self.pid_file(spec).unlink()

    def _alive(self, pid: int) -> bool:
        proc = self._procs.get(pid)
        if proc is not None:
            return proc.poll() is None
        return pid_alive(pid)

    def wait_healthy(
        self,
        spec: ProcessSpec,
        pid: int,
        *,
        deadline: float | None = None,
        should_abort: Callable[[], bool] = lambda: False,
    ) -> None:
        deadline = deadline or time.monotonic() + self.ready_timeout
        for url in spec.health_urls:
            while not self.probe(url):
                if should_abort():
                    raise SupervisorError(f"{spec.name}: aborted while waiting for {url}")
                if not self._alive(pid):
                    raise SupervisorError(
                        f"{spec.name} (pid {pid}) exited before {url} became healthy. "
                        f"Last log lines from {self.log_path(spec)}:\n{tail(self.log_path(spec))}"
                    )
                if time.monotonic() > deadline:
                    raise SupervisorError(
                        f"{spec.name} (pid {pid}) not healthy at {url} after "
                        f"{self.ready_timeout:.0f}s. Last log lines from "
                        f"{self.log_path(spec)}:\n{tail(self.log_path(spec))}"
                    )
                time.sleep(self.poll_interval)

    def stop_pids(self, pids: Iterable[int]) -> set[int]:
        """Stop several children in parallel: SIGTERM every group, wait up to
        ``stop_timeout`` for each group (leader *and* descendants) to vanish, then SIGKILL
        the rest. Returns the pids that needed SIGKILL."""
        pids = list(dict.fromkeys(pids))
        # Our children lead their own session; pgid == pid. Checked for unknown pids.
        groups = {pid: pid if pid in self._procs or _leads_group(pid) else None for pid in pids}

        def running(pid: int) -> bool:
            pgid = groups[pid]
            return self._alive(pid) or (pgid is not None and group_alive(pgid))

        for pid in pids:
            _send(pid, groups[pid], signal.SIGTERM)
        pending = set(pids)
        deadline = time.monotonic() + self.stop_timeout
        while pending := {p for p in pending if running(p)}:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        killed = set(pending)
        for pid in killed:
            _send(pid, groups[pid], signal.SIGKILL)
        deadline = time.monotonic() + 5
        while (pending := {p for p in pending if running(p)}) and time.monotonic() < deadline:
            time.sleep(0.1)
        return killed

    def stop_pid(self, pid: int) -> bool:
        """Stop one child (and its group). Returns True if it had to SIGKILL."""
        return pid in self.stop_pids([pid])

    def _stop_named(self, targets: list[tuple[ProcessSpec, int]]) -> list[str]:
        killed = self.stop_pids(pid for _, pid in targets)
        lines = []
        for spec, pid in targets:
            self.pid_file(spec).unlink(missing_ok=True)
            how = "killed (SIGKILL after timeout)" if pid in killed else "stopped"
            lines.append(f"{spec.name}: {how} pid {pid}")
        return lines

    def up(self) -> None:
        started: list[tuple[ProcessSpec, int]] = []
        for spec in self.specs:
            pid = self.read_pid(spec)
            if pid is not None and self.healthy(spec):
                self.echo(f"{spec.name}: already running (pid {pid}, port {spec.port})")
                continue
            try:
                # spawning is inside the rollback scope too: if a later child fails to
                # start, the ones this call already started are stopped
                if pid is None:
                    pid = self._spawn(spec).pid
                    started.append((spec, pid))
                    self.echo(f"{spec.name}: started pid {pid} on port {spec.port}, waiting...")
                else:
                    self.echo(f"{spec.name}: pid {pid} running but not healthy yet, waiting...")
                self.wait_healthy(spec, pid)
            except Exception as exc:  # noqa: BLE001 - re-raised as SupervisorError below
                # Roll back everything this call started (plus the unhealthy process).
                targets = [*(t for t in started if t[1] != pid)]
                if pid is not None:
                    targets.append((spec, pid))
                stopped = self._stop_named(targets) if targets else []
                kept = [
                    f"{s.name} (pid {p})" for s in self.specs if (p := self.read_pid(s)) is not None
                ]
                note = "; ".join(stopped) or "nothing to stop"
                if kept:
                    note += f"; left running (started before this call): {', '.join(kept)}"
                message = (
                    str(exc)
                    if isinstance(exc, SupervisorError)
                    else (f"{spec.name}: could not start: {type(exc).__name__}: {exc}")
                )
                raise SupervisorError(f"{message}\nRolled back: {note}") from None
            self.echo(f"{spec.name}: healthy ({', '.join(spec.health_urls)})")

    def down(self) -> None:
        targets = []
        for spec in reversed(self.specs):
            pid = self.read_pid(spec)  # also clears stale PID files
            if pid is None:
                self.echo(f"{spec.name}: not running")
            else:
                targets.append((spec, pid))
        for line in self._stop_named(targets) if targets else []:
            self.echo(line)


def format_status(rows: Iterable[ProcessStatus]) -> str:
    header = ("NAME", "PID", "PORT", "HEALTHY", "LOG")
    data = [
        (
            r.name,
            str(r.pid) if r.pid else "-",
            str(r.port),
            "yes" if r.healthy else "no",
            str(r.log_path),
        )
        for r in rows
    ]
    widths = [max(len(row[i]) for row in [header, *data]) for i in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in [header, *data]
    )
