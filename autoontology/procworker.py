# SPDX-License-Identifier: MIT
"""Terminable helper processes speaking JSON lines (SPARQL evaluation, schema validation).

Work that cannot be interrupted inside the service process (rdflib evaluation, regex
backtracking in jsonschema ``pattern``) runs in a ``python -m <module>`` child: it prints
``{"event": "ready"}`` once, then answers each request line with one or more lines. A
caller that runs out of time kills the child's whole process group, which really stops
the work; :class:`WorkerPool` then starts a fresh one for the next request.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

STARTUP_TIMEOUT_S = 30.0


class WorkerDied(Exception):
    pass


class LineWorker:
    """One child process; :meth:`read` raises ``TimeoutError`` / :class:`WorkerDied`."""

    def __init__(self, module: str, args: list[str] | None = None) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", module, *(args or [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._buf = b""
        try:
            ready = self.read(STARTUP_TIMEOUT_S)
        except BaseException:
            self.kill()
            raise
        if ready.get("event") != "ready":
            self.kill()
            raise WorkerDied(f"unexpected worker greeting {ready!r}")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as exc:
            raise WorkerDied(f"worker gone: {exc}") from exc

    def read(self, timeout: float) -> dict[str, Any]:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                code = self.proc.wait(timeout=5)
                raise WorkerDied(f"worker exited {code}")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            with contextlib.suppress(Exception):
                stream.close()  # type: ignore[union-attr]


class WorkerPool:
    """Idle workers kept for reuse (at most *keep*); a worker whose request failed by time
    or death is killed, never reused."""

    def __init__(self, factory: Callable[[], LineWorker], keep: int = 2) -> None:
        self.factory = factory
        self.keep = keep
        self._idle: list[LineWorker] = []
        self._lock = threading.Lock()
        atexit.register(self.close)

    def take(self) -> LineWorker:
        with self._lock:
            while self._idle:
                worker = self._idle.pop()
                if worker.alive():
                    return worker
                worker.kill()
        return self.factory()

    def give_back(self, worker: LineWorker) -> None:
        with self._lock:
            if worker.alive() and len(self._idle) < self.keep:
                self._idle.append(worker)
                return
        worker.kill()

    @property
    def idle(self) -> list[LineWorker]:
        return self._idle

    def close(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for worker in idle:
            worker.kill()


def serve(handle: Callable[[dict[str, Any], Callable[[dict[str, Any]], None]], None]) -> int:
    """Child side: greet, then call ``handle(request, emit)`` for every request line; each
    ``emit`` writes (and flushes) one message at once, so progress reaches the parent
    while the work is still running."""

    def emit(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    emit({"event": "ready"})
    for line in sys.stdin:
        handle(json.loads(line), emit)
    return 0
