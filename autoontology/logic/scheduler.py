# SPDX-License-Identifier: MIT
"""The workflow scheduler: one daemon thread in the service that fires scheduled
workflows and reconciles paused runs.

Every tick (60 s by default) :meth:`Scheduler.tick` goes through the projects (archived
ones are skipped: they are read-only), first :func:`~autoontology.logic.workflows.reconcile`
(resume objects whose approvals were decided elsewhere, fail interrupted runs), then --
when ``AUTOONTOLOGY_WORKFLOWS`` is ``on`` (the default) -- fires the due slot of every
active workflow with a ``trigger.schedule``:

* ``daily HH:MM``, ``hourly`` (minute 0) and ``weekly MON HH:MM``, in local time:
  ``AUTOONTOLOGY_TZ`` (an IANA name such as ``Asia/Seoul``) or the system's zone. A slot
  is named by its local wall time, ``2026-11-25T02:00``. Across a DST change a wall time
  that does not exist fires at the shifted instant, one that exists twice fires once
  (its name is the same).
* Only the most recent slot is ever considered: it fires when it is at most
  :data:`GRACE` (6 h) old and not older than the workflow's activation -- after
  downtime the latest missed slot fires once, older ones are never backfilled.
* ``workflow_runs`` is unique per (project, workflow, slot): two schedulers (two
  processes, two ticks) fire a slot once.

A fired run goes through its objects on a worker thread
(:func:`~autoontology.logic.workflows.start_background`). A failing tick is logged and
the next one comes; :meth:`Scheduler.stop` ends the thread at shutdown. With
``AUTOONTOLOGY_WORKFLOWS=off`` the thread only reconciles (manual runs can still pause
and resume) and ``on_action`` triggers are off too.

The service starts the scheduler from its lifespan when :data:`AUTOSTART` is true
(tests set it false and drive :meth:`Scheduler.tick` themselves).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from autoontology.logic import registry, specs, workflows

logger = logging.getLogger("autoontology.scheduler")

#: the service's lifespan starts the scheduler (tests turn this off)
AUTOSTART = True
TICK_SECONDS = 60.0
#: a slot older than this is not fired any more (downtime)
GRACE = dt.timedelta(hours=6)


def local_zone(settings: Any) -> dt.tzinfo:
    """``AUTOONTOLOGY_TZ`` or the system's local zone."""
    name = (getattr(settings, "tz", "") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("AUTOONTOLOGY_TZ=%r is not a known time zone: using local time", name)
    zone = dt.datetime.now().astimezone().tzinfo
    assert zone is not None
    return zone


def local_now(settings: Any) -> dt.datetime:
    return dt.datetime.now(local_zone(settings))


def slot_key(slot: dt.datetime) -> str:
    return slot.strftime("%Y-%m-%dT%H:%M")


def _at(day: dt.date, hour: int, minute: int, tz: dt.tzinfo | None) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz)


def latest_slot(schedule: str, now: dt.datetime) -> dt.datetime:
    """The most recent slot of *schedule* at or before *now* (same zone as *now*)."""
    s = specs.parse_schedule(schedule)
    tz = now.tzinfo
    if s["every"] == "hourly":
        return now.replace(minute=0, second=0, microsecond=0)
    if s["every"] == "daily":
        slot = _at(now.date(), s["hour"], s["minute"], tz)
        return (
            slot
            if slot <= now
            else _at(now.date() - dt.timedelta(days=1), s["hour"], s["minute"], tz)
        )
    back = (now.weekday() - s["weekday"]) % 7
    slot = _at(now.date() - dt.timedelta(days=back), s["hour"], s["minute"], tz)
    return slot if slot <= now else slot - dt.timedelta(days=7)


def next_slot(schedule: str, now: dt.datetime) -> dt.datetime:
    """The first slot of *schedule* after *now*."""
    s = specs.parse_schedule(schedule)
    last = latest_slot(schedule, now)
    if s["every"] == "hourly":
        return last + dt.timedelta(hours=1)
    days = 1 if s["every"] == "daily" else 7
    return _at(last.date() + dt.timedelta(days=days), last.hour, last.minute, now.tzinfo)


def _parse(stamp: str) -> dt.datetime | None:
    try:
        value = dt.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def due(view: Any, now: dt.datetime) -> list[tuple[str, str]]:
    """``(workflow, slot)`` of the active scheduled workflows of *view* whose latest slot
    is within :data:`GRACE` of *now* and after their activation."""
    out = []
    for d in view.logic_defs().list("workflow", "active"):
        schedule = (d.spec.get("trigger") or {}).get("schedule")
        if not schedule:
            continue
        try:
            slot = latest_slot(schedule, now)
        except ValueError:
            continue
        if now - slot > GRACE:
            continue
        found = workflows.activation(view, d.name)
        activated = _parse(found[1]) if found else None
        if activated is None or slot < activated:
            continue
        out.append((d.name, slot_key(slot)))
    return out


class Scheduler:
    """The scheduler thread over *store* (a :class:`~autoontology.store.db.Store`)."""

    def __init__(
        self,
        settings: Any,
        store: Any,
        *,
        tick_seconds: float | None = None,
        mailer: Any = None,
        background: bool = True,
    ) -> None:
        self.settings = settings
        self.store = store
        self.tick_seconds = TICK_SECONDS if tick_seconds is None else tick_seconds
        self.mailer = mailer
        #: fired runs go through their objects on worker threads (False: in the tick)
        self.background = background
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def fires(self) -> bool:
        return workflows._enabled(self.settings)

    def tick(self, now: dt.datetime | None = None) -> list[Any]:
        """One round (module docstring); returns the runs it fired."""
        fired: list[Any] = []
        now = now or local_now(self.settings)
        for project in self.store.list_projects():
            try:
                view = self.store.project(project.id)
                workflows.reconcile(self.settings, view, mailer=self.mailer)
                if not self.fires:
                    continue
                if not view.logic_defs().list("workflow", "active"):
                    continue
                registry.ensure_validated(view)  # a broken workflow is not due
                for name, slot in due(view, now):
                    fired += self._fire(view, name, slot)
            except Exception:  # noqa: BLE001 - one project never stops the others
                logger.exception("scheduler tick failed for project %s", project.slug)
        return fired

    def _fire(self, view: Any, name: str, slot: str) -> list[Any]:
        kw = {"trigger": "schedule", "fire_slot": slot, "started_by": "schedule"}
        try:
            if self.background:
                run, created = workflows.start_background(
                    self.settings, view, name, mailer=self.mailer, **kw
                )
            else:
                run, created = workflows.start(self.settings, view, name, **kw)
                if created:
                    run = workflows.execute(self.settings, view, run.id, mailer=self.mailer) or run
        except Exception:  # noqa: BLE001
            logger.exception("firing workflow %s at %s failed", name, slot)
            return []
        if created:
            logger.info("workflow %s fired for %s (run %s)", name, slot, run.id)
            return [run]
        return []

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler never crashes the service
                logger.exception("scheduler tick failed")
            self._stop.wait(self.tick_seconds)

    def start(self) -> Scheduler:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="workflow-scheduler", daemon=True
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
