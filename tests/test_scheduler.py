# SPDX-License-Identifier: MIT
"""The workflow scheduler (``ontology/logic/scheduler.py``): slots of daily / hourly /
weekly schedules in a fixed zone, the grace window and activation time, one firing per
slot across ticks and schedulers, the off setting, reconciling on every tick, the thread's
start and stop, and the service lifespan."""

from __future__ import annotations

import datetime as dt
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from ontology.config import Settings
from ontology.logic import scheduler, workflows
from ontology.logic.scheduler import latest_slot, next_slot, slot_key
from tests.test_workflows import WORKFLOW, _activate, env, sample_workflow  # noqa: F401

UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))


def at(text: str, tz: dt.tzinfo = KST) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=tz)


@pytest.mark.parametrize(
    ("schedule", "now", "latest", "following"),
    [
        ("daily 02:00", "2026-11-25T03:10", "2026-11-25T02:00", "2026-11-26T02:00"),
        ("daily 02:00", "2026-11-25T01:59", "2026-11-24T02:00", "2026-11-25T02:00"),
        ("daily 02:00", "2026-11-25T02:00", "2026-11-25T02:00", "2026-11-26T02:00"),
        ("daily 23:30", "2026-01-01T00:10", "2025-12-31T23:30", "2026-01-01T23:30"),
        ("hourly", "2026-11-25T03:59", "2026-11-25T03:00", "2026-11-25T04:00"),
        ("hourly", "2026-11-25T23:00", "2026-11-25T23:00", "2026-11-26T00:00"),
        # 2026-11-25 is a Wednesday
        ("weekly MON 09:00", "2026-11-25T10:00", "2026-11-23T09:00", "2026-11-30T09:00"),
        ("weekly WED 09:00", "2026-11-25T08:00", "2026-11-18T09:00", "2026-11-25T09:00"),
        ("weekly WED 09:00", "2026-11-25T09:30", "2026-11-25T09:00", "2026-12-02T09:00"),
        ("weekly SUN 00:00", "2026-11-25T09:30", "2026-11-22T00:00", "2026-11-29T00:00"),
    ],
)
def test_slots(schedule, now, latest, following):
    n = at(now)
    assert slot_key(latest_slot(schedule, n)) == latest
    assert slot_key(next_slot(schedule, n)) == following
    assert latest_slot(schedule, n).tzinfo is KST


def test_local_zone_from_settings(isolated_env, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_TZ", "Asia/Seoul")
    settings = Settings.from_env()
    assert settings.tz == "Asia/Seoul" and settings.workflows == "on"
    assert scheduler.local_now(settings).utcoffset() == dt.timedelta(hours=9)
    monkeypatch.setenv("ONTOLOGY_TZ", "Not/AZone")
    assert scheduler.local_zone(Settings.from_env()) is not None  # falls back to local
    monkeypatch.setenv("ONTOLOGY_WORKFLOWS", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()


def _backdate(view, days: int = 30) -> None:
    """Pretend every definition was activated *days* ago."""
    old = (dt.datetime.now(UTC) - dt.timedelta(days=days)).isoformat()
    with sqlite3.connect(view.store.path) as conn:
        conn.execute("UPDATE logic_def_history SET changed_at = ?", (old,))


def _daily(view, when: str = "02:00") -> None:
    _activate(view, {**sample_workflow(), "trigger": {"schedule": f"daily {when}"}})


def test_due_grace_window_and_activation(env):  # noqa: F811
    _, view, _, _ = env
    _daily(view)
    now = dt.datetime.now(KST)
    slot = latest_slot("daily 02:00", now)
    # activated just now, after the latest slot: nothing is due until the next one
    if slot < dt.datetime.now(UTC):
        assert scheduler.due(view, now) == []
    _backdate(view)
    assert scheduler.due(view, slot + dt.timedelta(hours=5)) == [(WORKFLOW, slot_key(slot))]
    # 6 h after the slot: missed for good (no backfill of older slots either)
    assert scheduler.due(view, slot + dt.timedelta(hours=6, minutes=1)) == []
    # manual and on_action workflows are never due
    _activate(view, {**sample_workflow(), "name": "수동", "trigger": {"manual": True}})
    _backdate(view)
    assert [n for n, _ in scheduler.due(view, slot + dt.timedelta(minutes=1))] == [WORKFLOW]


def test_tick_fires_a_slot_once_across_ticks_and_schedulers(env):  # noqa: F811
    settings, view, _, _ = env
    _daily(view)
    _backdate(view)
    now = latest_slot("daily 02:00", dt.datetime.now(KST)) + dt.timedelta(minutes=3)
    first = scheduler.Scheduler(settings, view.store, background=False)
    second = scheduler.Scheduler(settings, view.store, background=False)
    fired = first.tick(now)
    assert len(fired) == 1 and fired[0].trigger == "schedule"
    assert fired[0].fire_slot == slot_key(now - dt.timedelta(minutes=3))
    assert fired[0].started_by == "schedule"
    assert first.tick(now + dt.timedelta(minutes=1)) == []
    assert second.tick(now) == []
    runs = view.workflow_runs().list()
    assert len(runs) == 1 and runs[0].status == "paused"
    # the next day's slot fires again
    assert len(first.tick(now + dt.timedelta(days=1))) == 1


def test_background_tick_runs_on_a_worker(env):  # noqa: F811
    settings, view, _, _ = env
    _daily(view)
    _backdate(view)
    now = latest_slot("daily 02:00", dt.datetime.now(KST)) + dt.timedelta(minutes=3)
    fired = scheduler.Scheduler(settings, view.store).tick(now)
    workflows.wait()
    assert len(fired) == 1
    assert view.workflow_runs().get(fired[0].id).summary["matched"] == 4


def test_off_fires_nothing_but_still_reconciles(env, monkeypatch):  # noqa: F811
    settings, view, _, _ = env
    _daily(view)
    _backdate(view)
    run = workflows.run(settings, view, WORKFLOW, trigger="manual", fire_slot="m1", started_by="t")
    pending = view.action_runs().list(workflow_run_id=run.id, status="pending_approval")[0]
    workflows.uninstall()
    from ontology.logic import actions
    from tests.test_workflows import OWNER

    actions.approve(settings, view, pending.id, OWNER)
    monkeypatch.setenv("ONTOLOGY_WORKFLOWS", "off")
    off = Settings.from_env()
    now = latest_slot("daily 02:00", dt.datetime.now(KST)) + dt.timedelta(minutes=3)
    assert scheduler.Scheduler(off, view.store, background=False).tick(now) == []
    assert [r.id for r in view.workflow_runs().list()] == [run.id]
    assert view.workflow_runs().get(run.id).status == "succeeded"


def test_archived_projects_are_skipped_and_errors_do_not_stop_the_tick(env, monkeypatch):  # noqa: F811
    settings, view, _, _ = env
    _daily(view)
    _backdate(view)
    store = view.store
    other = store.create_project("gamma")
    now = latest_slot("daily 02:00", dt.datetime.now(KST)) + dt.timedelta(minutes=3)
    calls = []

    def boom(settings, v, mailer=None):
        calls.append(v.id)
        if v.id == other.id:
            raise RuntimeError("broken project")
        return 0

    monkeypatch.setattr(workflows, "reconcile", boom)
    fired = scheduler.Scheduler(settings, store, background=False).tick(now)
    assert len(fired) == 1 and set(calls) == {view.id, other.id}
    calls.clear()
    store.archive_project(other.id)
    scheduler.Scheduler(settings, store, background=False).tick(now)
    assert calls == [view.id]


def test_thread_starts_and_stops(env, monkeypatch):  # noqa: F811
    settings, view, _, _ = env
    ticks = []
    monkeypatch.setattr(scheduler.Scheduler, "tick", lambda self, now=None: ticks.append(1) or [])
    s = scheduler.Scheduler(settings, view.store, tick_seconds=0.01).start()
    assert s.running
    deadline = dt.datetime.now() + dt.timedelta(seconds=5)
    while len(ticks) < 3 and dt.datetime.now() < deadline:
        time.sleep(0.01)
    s.stop()
    assert not s.running and len(ticks) >= 3


def test_a_failing_tick_never_stops_the_thread(env, monkeypatch):  # noqa: F811
    settings, view, _, _ = env
    ticks = []

    def bad(self, now=None):
        ticks.append(1)
        raise RuntimeError("tick failed")

    monkeypatch.setattr(scheduler.Scheduler, "tick", bad)
    s = scheduler.Scheduler(settings, view.store, tick_seconds=0.01).start()
    deadline = dt.datetime.now() + dt.timedelta(seconds=5)
    while len(ticks) < 2 and dt.datetime.now() < deadline:
        time.sleep(0.01)
    assert s.running
    s.stop()
    assert len(ticks) >= 2


def test_lifespan_starts_only_when_asked(isolated_env, monkeypatch):
    from ontology.web import app as web

    settings = Settings.from_env()
    with TestClient(web.create_app(settings)) as client:
        assert client.app.state.scheduler is None  # tests: AUTOSTART is off
    monkeypatch.setattr(scheduler, "AUTOSTART", True)
    monkeypatch.setattr(scheduler, "TICK_SECONDS", 3600.0)
    app = web.create_app(settings)
    with TestClient(app):
        assert app.state.scheduler is not None and app.state.scheduler.running
        sched = app.state.scheduler
    assert not sched.running
