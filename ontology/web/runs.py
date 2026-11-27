# SPDX-License-Identifier: MIT
"""Background run manager for the web API (``POST /api/runs``).

Each run executes one of the CLI job functions (``build.run_build``,
``export_job.run_export``, ``apply.run_apply``, ``logic.extract.run_logic``) in its own
thread, so runs started from the UI persist exactly
like CLI runs (``runs``/``steps`` rows, ``<home>/logs/jobs.log``).

- One active run per project: an in-process lock on the project's key (``(project id,
  WORKSPACE)``; a source operation on ``(project id, source)``), on top of the jobs' own
  store lease (``Store.lease`` via ``jobs.hold_sources``, atomic across processes, so it
  also sees CLI runs and ``source rm``).
- The v1 global build gate: one build, export or apply at a time across all projects
  (the installation has one engine backend). A run of another project while one runs is
  refused (409), never queued; the jobs hold the same gate across processes
  (``jobs.hold_engine``).
- :meth:`RunManager.claim_idle` (a restart, a sample load) is installation-wide: it
  needs nothing at all active and holds every project until
  :meth:`RunManager.release_idle`.
- The run id is known only once the job has created (or resumed) its run row, so the job
  gets a :class:`TrackingStore` that reports it; :meth:`RunManager.start` waits for that
  (or for the job failing its pre-checks, which is returned as an error).
- Per-run log: a root logging handler that keeps the records emitted by the run's thread
  (and the extraction pool threads it starts) and writes them to
  ``<home>/logs/runs/<run_id>.log``, which the SSE endpoint tails.
- Cancel is best effort: a flag checked when the next top-level step starts.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ontology.config import Settings
from ontology.store.db import DEFAULT_PROJECT_ID, Run, Store, home_db_path

logger = logging.getLogger("ontology.web.runs")

RUN_KINDS: tuple[str, ...] = ("build", "export", "apply", "logic")
#: The run manager's key for project-wide runs (build, export, apply), per project.
WORKSPACE = "*workspace*"
#: The "project" of an installation-wide :meth:`RunManager.claim_idle`.
ALL_PROJECTS = "*"
#: The run kinds behind the global build gate (every run kind of the manager; a logic run
#: uses the chat model the builds use).
GATED_KINDS: frozenset[str] = frozenset(RUN_KINDS)
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
#: How long ``start`` waits for the job to create its run row.
START_TIMEOUT_S = 60.0


class RunCancelled(BaseException):  # noqa: N818 - BaseException so jobs never swallow it
    """Raised inside the run's thread when a cancelled run reaches its next step."""


class RunStartError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def run_log_path(settings: Settings, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id {run_id!r}")
    return settings.logs_dir / "runs" / f"{run_id}.log"


class TrackingStore(Store):
    """A :class:`Store` that reports the run id the job creates or resumes, and raises
    :class:`RunCancelled` at the next top-level step once the run is cancelled."""

    def __init__(
        self,
        path: Path,
        on_run: Callable[[str], None],
        cancelled: Callable[[], bool],
        is_top: Callable[[str], bool],
    ) -> None:
        super().__init__(path)
        self._on_run = on_run
        self._cancelled = cancelled
        self._is_top = is_top
        self.run_id: str | None = None

    def _seen(self, run_id: str) -> None:
        if self.run_id is None:
            self.run_id = run_id
            self._on_run(run_id)

    def create_run(self, **kw: Any) -> Run:
        run = super().create_run(**kw)
        self._seen(run.id)
        return run

    def update_run(self, run_id: str, **kw: Any) -> None:
        super().update_run(run_id, **kw)
        if kw.get("status") == "running":  # --resume reuses an earlier run row
            self._seen(run_id)

    def start_step(self, run_id: str, name: str) -> None:
        if self._is_top(name) and self._cancelled():
            raise RunCancelled(name)
        super().start_step(run_id, name)


class _RunLogHandler(logging.Handler):
    """Root handler keeping the records of one run's threads, buffered until the run id
    (and so the file name) is known."""

    def __init__(self, thread_name: str) -> None:
        super().__init__(logging.INFO)
        self.thread_name = thread_name
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        self._buffer: list[str] = []
        self._fh: Any = None

    def owns(self, record: logging.LogRecord) -> bool:
        name = record.threadName or ""
        # extraction pool threads are "<run thread>:extract_<n>" (extractor.pool_thread_prefix)
        return name == self.thread_name or name.startswith(self.thread_name + ":")

    def emit(self, record: logging.LogRecord) -> None:
        if not self.owns(record):
            return
        try:
            line = self.format(record).replace("\r", "")
            if record.exc_info:  # tracebacks stay in jobs.log
                line = line.split("\n", 1)[0]
            self.acquire()
            try:
                if self._fh is None:
                    self._buffer.append(line)
                else:
                    self._fh.write(line + "\n")
                    self._fh.flush()
            finally:
                self.release()
        except Exception:  # noqa: BLE001 - logging must never break the job
            self.handleError(record)

    def open(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.acquire()
        try:
            self._fh = path.open("a", encoding="utf-8")
            for line in self._buffer:
                self._fh.write(line + "\n")
            self._fh.flush()
            self._buffer.clear()
        finally:
            self.release()

    def close(self) -> None:
        self.acquire()
        try:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        finally:
            self.release()
        super().close()


@dataclass
class _Active:
    source: str
    kind: str
    thread_name: str
    project: str = DEFAULT_PROJECT_ID
    run_id: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    error_status: int = 400


JobFn = Callable[..., Any]


def default_jobs() -> dict[str, JobFn]:
    """The job entry points by run kind, looked up at call time (tests monkeypatch them)."""
    from ontology.engine import apply, build, export_job
    from ontology.logic import extract

    return {
        "build": build.run_build,
        "export": export_job.run_export,
        "apply": apply.run_apply,
        "logic": extract.run_logic,
    }


#: Fixed top-level steps per kind (a build's steps depend on its sources).
TOP_STEPS: dict[str, tuple[str, ...]] = {
    "build": (),
    "export": ("collect", "serialize", "upstream", "ossie", "okf", "report"),
    "apply": ("decisions", "merge", "snapshot"),
    "logic": ("collect", "extract", "validate", "store"),
}


def is_top_step(name: str) -> bool:
    """A step the run manager can cancel before (not a per-chunk/per-concept row)."""
    from ontology.engine.build import is_top_step as top

    return top(name)


class RunManager:
    def __init__(
        self,
        settings: Settings,
        jobs: Callable[[], dict[str, JobFn]] = default_jobs,
        *,
        default_project: str = DEFAULT_PROJECT_ID,
    ) -> None:
        self.settings = settings
        self._jobs = jobs
        #: the project a call without ``project=`` means
        self.default_project = default_project
        self._lock = threading.Lock()
        #: ``(project id, source or WORKSPACE)`` -> the run or claim holding it
        self._by_source: dict[tuple[str, str], _Active] = {}
        self._by_run: dict[str, _Active] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._seq = 0

    def _pid(self, project: str | None) -> str:
        return project or self.default_project

    def _global_claim(self) -> _Active | None:
        """An installation-wide :meth:`claim_idle` (caller holds the lock)."""
        return next((a for (p, _), a in self._by_source.items() if p == ALL_PROJECTS), None)

    def _holder(self, project: str, source: str) -> _Active | None:
        """What blocks *source* of *project* (caller holds the lock): its own entry, the
        project-wide one, or an installation-wide claim."""
        return (
            self._by_source.get((project, source))
            or self._by_source.get((project, WORKSPACE))
            or self._global_claim()
        )

    def _gate(self, project: str) -> _Active | None:
        """A run behind the global build gate in another project (caller holds the lock)."""
        return next(
            (
                a
                for a in self._by_source.values()
                if a.thread_name and a.kind in GATED_KINDS and a.project != project
            ),
            None,
        )

    # --- queries ----------------------------------------------------------------

    def active_source(self, source: str, *, project: str | None = None) -> _Active | None:
        """The run holding *source* of *project* (None = the default project): its own,
        or a project-wide build/export/apply, or an installation-wide claim."""
        with self._lock:
            return self._holder(self._pid(project), source)

    def busy(self, *, project: str | None = None) -> _Active | None:
        """Any run or claim this service holds (None when idle); with *project*, only
        that project's (and installation-wide claims)."""
        with self._lock:
            if project is None:
                return next(iter(self._by_source.values()), None)
            return next(
                (a for (p, _), a in self._by_source.items() if p in (project, ALL_PROJECTS)),
                None,
            )

    def is_managed(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._by_run

    def join(self, run_id: str, timeout: float | None = None) -> None:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)

    # --- start / cancel ---------------------------------------------------------

    def start(
        self,
        kind: str,
        source: str,
        call: Callable[[JobFn, Store], Any],
        *,
        timeout: float = START_TIMEOUT_S,
        project: str | None = None,
    ) -> str:
        """Start *call(job_fn, store)* for *source* of *project* (None = the default
        project) in a thread; return the run id. *store* is the whole (tracking) store:
        the call picks the project (``store.project(id)``). A run of another project
        behind the global build gate is a 409."""
        if kind not in RUN_KINDS:
            raise RunStartError(f"unknown run kind {kind!r}")
        pid = self._pid(project)
        key = (pid, source)
        with self._lock:
            busy = self._by_source.get(key) or self._global_claim()
            if busy is not None:
                raise RunStartError(
                    f"source {source!r} already has an active {busy.kind} run"
                    + (f" {busy.run_id}" if busy.run_id else ""),
                    status=409,
                )
            gate = self._gate(pid) if kind in GATED_KINDS else None
            if gate is not None:
                raise RunStartError(
                    f"the engine already has an active {gate.kind} run"
                    + (f" {gate.run_id}" if gate.run_id else "")
                    + " of another project; one build, export or apply runs at a time",
                    status=409,
                )
            self._seq += 1
            active = _Active(
                source=source,
                kind=kind,
                thread_name=f"ontology-run-{self._seq}-{kind}",
                project=pid,
            )
            self._by_source[key] = active
        handler = _RunLogHandler(active.thread_name)
        logging.getLogger().addHandler(handler)

        def on_run(run_id: str) -> None:
            handler.open(run_log_path(self.settings, run_id))
            with self._lock:
                active.run_id = run_id
                self._by_run[run_id] = active
                self._threads[run_id] = threading.current_thread()
            active.started.set()

        store = TrackingStore(
            home_db_path(self.settings.home),
            on_run,
            active.cancel.is_set,
            is_top_step,
        )

        def body() -> None:
            from ontology.engine.jobs import JobError, setup_job_logging

            setup_job_logging(self.settings)
            try:
                result = call(self._jobs()[kind], store)
                logger.info(
                    "%s run %s for %s: %s",
                    kind,
                    store.run_id,
                    source,
                    getattr(result, "status", "done"),
                )
            except RunCancelled as exc:
                if store.run_id:
                    store.update_run(
                        store.run_id, status="cancelled", error=f"cancelled before step {exc}"
                    )
                logger.info(
                    "%s run %s for %s: cancelled before step %s", kind, store.run_id, source, exc
                )
            except JobError as exc:
                if store.run_id is None:  # failed its pre-checks: no run row
                    active.error = str(exc)
                    active.error_status = 409 if "already has an active" in str(exc) else 400
                logger.error("%s run %s for %s failed: %s", kind, store.run_id, source, exc)
            except Exception as exc:  # noqa: BLE001 - the run row (if any) holds the error
                from ontology.engine.client import BackendError
                from ontology.engine.jobs import short_error

                if store.run_id is None:
                    active.error = short_error(exc)
                    active.error_status = 503 if isinstance(exc, BackendError) else 500
                elif (run := store.get_run(store.run_id)) and run.status == "running":
                    store.update_run(store.run_id, status="failed", error=short_error(exc))
                logger.error(
                    "%s run %s for %s failed: %s",
                    kind,
                    store.run_id,
                    source,
                    short_error(exc),
                    exc_info=exc,
                )
            finally:
                active.started.set()
                logging.getLogger().removeHandler(handler)
                handler.close()
                with self._lock:
                    self._by_source.pop(key, None)
                    if active.run_id:
                        self._by_run.pop(active.run_id, None)

        thread = threading.Thread(target=body, name=active.thread_name, daemon=True)
        thread.start()
        if not active.started.wait(timeout):
            raise RunStartError(
                f"{kind} run for {source!r} did not start within {timeout:.0f}s", status=504
            )
        if active.run_id is None:
            raise RunStartError(
                active.error or f"{kind} run for {source!r} did not start",
                status=active.error_status,
            )
        return active.run_id

    def claim(self, source: str, kind: str, *, project: str | None = None) -> None:
        """Hold *source* of *project* for a non-run operation (an upload): runs and other
        claims get 409 until :meth:`release`."""
        pid = self._pid(project)
        with self._lock:
            busy = self._holder(pid, source)
            if busy is not None:
                raise RunStartError(f"source {source!r} is busy ({busy.kind})", status=409)
            self._by_source[(pid, source)] = _Active(
                source=source, kind=kind, thread_name="", project=pid
            )

    def claim_idle(self, source: str, kind: str) -> None:
        """Like :meth:`claim`, but only when nothing at all (no run, no other claim, in
        any project) is active: the whole installation is held until :meth:`release_idle`."""
        with self._lock:
            busy = next(iter(self._by_source.values()), None)
            if busy is not None:
                raise RunStartError(f"the workspace is busy ({busy.kind})", status=409)
            self._by_source[(ALL_PROJECTS, source)] = _Active(
                source=source, kind=kind, thread_name="", project=ALL_PROJECTS
            )

    def _release_key(self, key: tuple[str, str]) -> None:
        with self._lock:
            active = self._by_source.get(key)
            if active is not None and active.run_id is None and not active.thread_name:
                self._by_source.pop(key, None)

    def release(self, source: str, *, project: str | None = None) -> None:
        """Release a :meth:`claim` on *source* of *project* (None = the default project).
        Never touches an installation-wide claim (:meth:`release_idle`)."""
        self._release_key((self._pid(project), source))

    def release_idle(self, source: str) -> None:
        """Release the installation-wide claim of that name (:meth:`claim_idle`) only: a
        project's claim of the same name (a rename) stays held."""
        self._release_key((ALL_PROJECTS, source))

    def cancel(self, run_id: str) -> bool:
        """Flag a managed run for cancellation; False when it is not running here."""
        with self._lock:
            active = self._by_run.get(run_id)
        if active is None:
            return False
        active.cancel.set()
        return True
