# SPDX-License-Identifier: MIT
"""Restart the service and the backend with the settings ``.env`` holds now.

The upstream engine reads its model endpoints once, at import time, in both processes, so
a provider change only applies after both restart. The service cannot ``down`` itself (it
would kill its own process group mid-way), so ``POST /api/settings/restart`` starts this
module as a detached helper in its own session::

    python -m autoontology.engine.restart --home H --env-file F [--only backend] [--id ID]

The helper runs with the environment cleaned of everything the service derived or copied
from ``.env`` (:func:`helper_env`), re-reads ``.env``, then ``down`` + ``up`` exactly like
the CLI. Its state goes to ``<home>/run/restart-<id>.json`` (``{id, state, started_at,
finished_at, error, previous_pid, new_pid}``; ``state`` is ``running``, ``done`` or
``failed``), its output to ``<home>/logs/restart.log``. ``autoontology restart`` runs the
same helper in the foreground.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESTART_ID_RE = re.compile(r"^[0-9a-f]{12}$")
LOG_NAME = "restart.log"
LOCK_NAME = "restart.lock"
#: Before stopping the service, the helper waits this long so the 202 reaches the browser.
GRACE_S = 0.5
#: A "running" record older than this is stale (the helper died without finishing).
STALE_AFTER_S = 600.0
ONLY_CHOICES = ("backend",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return secrets.token_hex(6)


def record_path(run_dir: Path, restart_id: str) -> Path:
    if not RESTART_ID_RE.fullmatch(restart_id):
        raise ValueError(f"invalid restart id {restart_id!r}")
    return Path(run_dir) / f"restart-{restart_id}.json"


def write_record(run_dir: Path, record: dict[str, Any]) -> None:
    path = record_path(run_dir, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_record(run_dir: Path, restart_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(record_path(run_dir, restart_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _age_s(record: dict[str, Any]) -> float:
    try:
        started = datetime.fromisoformat(str(record.get("started_at")))
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - started).total_seconds()


def running_restart(run_dir: Path) -> dict[str, Any] | None:
    """The record of a restart still running (not stale), if any."""
    for path in sorted(Path(run_dir).glob("restart-*.json")):
        restart_id = path.stem.removeprefix("restart-")
        if not RESTART_ID_RE.fullmatch(restart_id):
            continue
        record = read_record(run_dir, restart_id)
        if record and record.get("state") == "running" and _age_s(record) < STALE_AFTER_S:
            return record
    return None


def log_tail(logs_dir: Path, restart_id: str, lines: int = 20) -> str:
    """The helper's last output lines for *restart_id* (masked)."""
    from autoontology.web.providers.base import scrub

    path = Path(logs_dir) / LOG_NAME
    if not path.is_file():
        return ""
    text = path.read_bytes()[-128_000:].decode("utf-8", errors="replace")
    tag = f"[{restart_id}]"
    picked = [line for line in text.splitlines() if line.startswith(tag)][-lines:]
    return scrub("\n".join(picked), 4000)


def helper_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """*environ* (default ``os.environ``) minus what this process derived or copied from
    ``.env``, so the helper reads ``.env`` afresh and its children see the new file. Shell
    values stay."""
    from autoontology.config import DERIVED_ENV_VAR, derived_env_keys, dotenv_copies

    env = dict(os.environ if environ is None else environ)
    for key in derived_env_keys(env):
        env.pop(key, None)
    if environ is None:
        for key, value in dotenv_copies().items():
            if env.get(key) == value:
                del env[key]
    env.pop(DERIVED_ENV_VAR, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def helper_argv(
    home: Path,
    env_file: Path,
    *,
    restart_id: str,
    only: str | None = None,
    wait_pid: int | None = None,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "autoontology.engine.restart",
        "--home",
        str(home),
        "--env-file",
        str(env_file),
        "--id",
        restart_id,
    ]
    if only:
        argv += ["--only", only]
    if wait_pid:
        argv += ["--wait-pid", str(wait_pid)]
    return argv


def spawn(
    settings: Any,
    *,
    only: str | None = None,
    previous_pid: int | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    """Start the helper detached (own session, stdio to ``logs/restart.log``) and return
    its first record (already written, so the new service can answer for it at once)."""
    from autoontology.config import REPO_ROOT, env_file_path

    restart_id = new_id()
    record = {
        "id": restart_id,
        "state": "running",
        "only": only,
        "started_at": _now(),
        "finished_at": None,
        "error": None,
        "previous_pid": previous_pid,
        "new_pid": None,
    }
    write_record(settings.run_dir, record)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    argv = helper_argv(
        settings.home,
        env_file_path(),
        restart_id=restart_id,
        only=only,
        wait_pid=previous_pid,
    )
    try:
        with (settings.logs_dir / LOG_NAME).open("ab") as log:
            popen(
                argv,
                cwd=REPO_ROOT,
                env=helper_env(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        record.update(state="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
        write_record(settings.run_dir, record)
        raise
    return record


def _wait_gone(pid: int, timeout: float) -> bool:
    from autoontology.engine.supervisor import pid_alive

    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _default_supervisor(settings: Any, echo: Callable[[str], None], only: str | None) -> Any:
    from autoontology.engine.supervisor import Supervisor

    sup = Supervisor.from_settings(settings, echo=echo)
    if only:
        sup = Supervisor(settings.run_dir, settings.logs_dir, [sup.spec(only)], echo=echo)
    return sup


def run(
    home: Path,
    env_file: Path | None = None,
    *,
    restart_id: str | None = None,
    only: str | None = None,
    wait_pid: int | None = None,
    echo: Callable[[str], None] = print,
    supervisor: Callable[[Any, Callable[[str], None], str | None], Any] = _default_supervisor,
    grace_s: float = GRACE_S,
) -> dict[str, Any]:
    """Restart: re-read ``.env``, ``down``, ``up``. Returns the final record (also written).
    Never raises for a failed restart: the record says what failed."""
    from autoontology.config import Settings

    restart_id = restart_id or new_id()
    home = Path(home).expanduser()
    run_dir = home / "run"
    record = read_record(run_dir, restart_id) or {
        "id": restart_id,
        "only": only,
        "started_at": _now(),
        "previous_pid": wait_pid,
    }
    record.update(state="running", finished_at=None, error=None, new_pid=None)
    write_record(run_dir, record)

    def say(line: str) -> None:
        echo(f"[{restart_id}] {line}")

    def finish(state: str, error: str | None = None, new_pid: int | None = None):
        from autoontology.web.api import mask

        record.update(
            state=state,
            finished_at=_now(),
            error=mask(error) if error else None,
            new_pid=new_pid,
        )
        write_record(run_dir, record)
        say(f"{state}" + (f": {record['error']}" if error else ""))
        return record

    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / LOCK_NAME, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return finish("failed", "another restart is running")
        try:
            if env_file is not None:
                os.environ["AUTOONTOLOGY_ENV_FILE"] = str(env_file)
            try:
                settings = Settings.from_env()
            except ValueError as exc:
                return finish("failed", f"config error: {exc}")
            if settings.home.resolve() != home.resolve():
                return finish(
                    "failed",
                    f"AUTOONTOLOGY_HOME is now {settings.home}, not {home}: "
                    "run `uv run autoontology down && uv run autoontology up`",
                )
            say(
                f"restarting {only or 'service and backend'}: chat {settings.chat_provider} "
                f"({settings.effective_chat_model or '-'}), embed {settings.embed_provider} "
                f"({settings.effective_embed_model or '-'})"
            )
            if grace_s:
                time.sleep(grace_s)
            sup = supervisor(settings, say, only)
            try:
                sup.down()
                if wait_pid and not only and not _wait_gone(wait_pid, 20):
                    return finish("failed", f"the previous service (pid {wait_pid}) did not stop")
                sup.up()
            except Exception as exc:  # noqa: BLE001 - SupervisorError and the unexpected
                return finish("failed", str(exc))
            new_pid = None
            with contextlib.suppress(Exception):
                new_pid = sup.read_pid(sup.spec(only or "service"))
            return finish("done", new_pid=new_pid)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autoontology.engine.restart")
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--only", choices=ONLY_CHOICES, default=None)
    parser.add_argument("--id", dest="restart_id", default=None)
    parser.add_argument("--wait-pid", type=int, default=None)
    args = parser.parse_args(argv)
    if args.restart_id is not None and not RESTART_ID_RE.fullmatch(args.restart_id):
        parser.error("--id must be 12 hex digits")
    record = run(
        args.home,
        args.env_file,
        restart_id=args.restart_id,
        only=args.only,
        wait_pid=args.wait_pid,
        echo=lambda line: print(line, flush=True),
        grace_s=GRACE_S if args.wait_pid else 0.0,
    )
    return 0 if record["state"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
