# SPDX-License-Identifier: MIT
"""Persistent source observation, fenced preparation jobs and owner-only local outbox."""

from __future__ import annotations

import json
import re
import time
import uuid

from autoontology.security.context import audit_effect, authorize, current_actor
from autoontology.security.policy import AccessError
from autoontology.store.operations import digest


def fail(message, code="collection_conflict", status=409):
    raise AccessError(message, code, status)


def source_binding(source):
    return digest([source.id, source.kind, source.locator])


def decoded(row):
    value = dict(row)
    for key in ("input_json", "result_json", "cursor_json"):
        if key in value:
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
    return value


class Collection:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    def _authority(self, conn, action):
        authority = authorize(self.store, action, conn=conn)
        if authority is None:
            fail("Enroll an individual workspace owner first.", "collection_owner_required", 403)
        return authority

    def _event(self, conn, schedule, operation, *, job=None, detail=None):
        actor = current_actor(self.store, conn=conn)
        previous = conn.execute(
            "SELECT sha256 FROM collection_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        value = {
            "schedule_id": schedule,
            "job_id": job,
            "operation": operation,
            "actor_json": json.dumps({"id": actor.principal_id, "kind": actor.kind}),
            "detail_json": json.dumps(detail or {}, sort_keys=True),
            "previous_sha256": previous[0] if previous else "",
            "created_at": self.clock(),
        }
        conn.execute(
            "INSERT INTO collection_events(schedule_id,job_id,operation,actor_json,detail_json,previous_sha256,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (*list(value.values())[:-1], digest(value), value["created_at"]),
        )

    def _schedule(self, conn, ident):
        row = conn.execute("SELECT * FROM collection_schedules WHERE id=?", (ident,)).fetchone()
        if not row:
            fail("Collection schedule is missing.", "collection_missing", 404)
        return dict(row)

    def _job(self, conn, ident):
        row = conn.execute("SELECT * FROM collection_jobs WHERE id=?", (ident,)).fetchone()
        if not row:
            fail("Collection job is missing.", "collection_missing", 404)
        return dict(row)

    def _binding(self, source, mode):
        if mode == "observe":
            return source_binding(source)
        return digest(
            [
                source_binding(source),
                sorted((s.id, source_binding(s)) for s in self.store.list_sources()),
            ]
        )

    def _worker(self, conn, schedule):
        authority = self._authority(conn, "collection.work")
        if authority.actor.principal_id != schedule["worker_id"]:
            fail("Run this job as its assigned worker.", "collection_worker_required", 403)
        authorize(self.store, "source.read", source_ids=[schedule["source_id"]], conn=conn)
        if schedule["mode"] == "prepare":
            authorize(self.store, "candidate.prepare", conn=conn)
        if authority.policy_version != schedule["policy_version"]:
            fail(
                "Policy changed. An administrator must review and resume this schedule.",
                "collection_policy_changed",
            )
        if not schedule["enabled"] or schedule["expires_at"] <= self.clock():
            fail("Collection is paused or its authorization has expired.", "collection_paused")
        source = self.store.get_source(schedule["source_name"])
        if not source or self._binding(source, schedule["mode"]) != schedule["source_sha256"]:
            fail(
                "Source target changed. Configure a new schedule for the exact target.",
                "collection_source_changed",
            )
        return authority

    def configure(self, source, *, worker_id, interval, mode, reason, max_attempts=3, ttl=86400):
        if (
            mode not in {"observe", "prepare"}
            or type(interval) is not int
            or not 30 <= interval <= 86400
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 10
            or type(ttl) is not int
            or not 60 <= ttl <= 604800
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 2000
        ):
            fail("Choose a bounded interval, expiry, attempts and reason.", "collection_input", 400)
        selected = self.store.get_source(source)
        if selected is None:
            fail("Register and classify this source first.", "collection_source_missing", 404)
        with self.store._immediate() as conn:
            authority = self._authority(conn, "collection.configure")
            if self.store.get_source(source) != selected:
                fail(
                    "Source changed during configuration. Refresh first.",
                    "collection_source_changed",
                )
            authorize(self.store, "source.read", source_ids=[selected.id], conn=conn)
            worker = conn.execute(
                "SELECT * FROM identity_principals WHERE id=? AND status='active'", (worker_id,)
            ).fetchone()
            if worker is None:
                fail("Choose an active worker.", "collection_worker_missing", 400)
            if worker["kind"] == "agent":
                delegation = conn.execute(
                    "SELECT actions_json,expires_at,revoked_at FROM identity_delegations WHERE principal_id=?",
                    (worker_id,),
                ).fetchone()
                required = {"collection.read", "collection.work", "source.read"} | (
                    {"candidate.read", "candidate.prepare"} if mode == "prepare" else set()
                )
                if (
                    not delegation
                    or delegation["revoked_at"] is not None
                    or delegation["expires_at"] <= self.clock()
                    or not required <= set(json.loads(delegation["actions_json"]))
                ):
                    fail(
                        "Delegate the required observation/preparation capabilities to this worker first.",
                        "collection_worker_scope",
                        400,
                    )
            elif worker_id != authority.actor.principal_id:
                fail(
                    "Use yourself or an explicitly delegated agent.", "collection_worker_scope", 400
                )
            existing = conn.execute(
                "SELECT * FROM collection_schedules WHERE source_id=? AND enabled=1", (selected.id,)
            ).fetchone()
            if existing:
                same = (
                    existing["worker_id"] == worker_id
                    and existing["owner_id"] == authority.actor.principal_id
                    and existing["interval_s"] == interval
                    and existing["mode"] == mode
                    and existing["max_attempts"] == max_attempts
                    and existing["reason"] == reason.strip()
                    and existing["expires_at"] - existing["created_at"] == ttl
                    and existing["source_sha256"] == self._binding(selected, mode)
                )
                if same:
                    return decoded(existing)
                fail(
                    "Pause the existing schedule before changing its worker or scope.",
                    "collection_schedule_exists",
                )
            key = uuid.uuid4().hex
            now = self.clock()
            conn.execute(
                "INSERT INTO collection_schedules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'{}',?,?,?)",
                (
                    key,
                    selected.id,
                    selected.name,
                    self._binding(selected, mode),
                    worker_id,
                    authority.actor.principal_id,
                    interval,
                    mode,
                    1,
                    authority.policy_version,
                    1,
                    max_attempts,
                    now + ttl,
                    now,
                    reason.strip(),
                    now,
                    now,
                ),
            )
            self._event(
                conn,
                key,
                "configured",
                detail={
                    "mode": mode,
                    "source": selected.name,
                    "worker_id": worker_id,
                    "expires_at": now + ttl,
                },
            )
            audit_effect(
                self.store,
                authority,
                key,
                {"operation": "collection_configured", "mode": mode},
                conn=conn,
            )
            return decoded(self._schedule(conn, key))

    def listing(self):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return [
                decoded(v)
                for v in conn.execute(
                    "SELECT * FROM collection_schedules ORDER BY created_at DESC,rowid DESC"
                )
            ]

    def summary(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            a = self._authority(conn, "collection.read")
            return {
                "active": conn.execute(
                    "SELECT count(*) FROM collection_schedules WHERE enabled=1 AND expires_at>?",
                    (self.clock(),),
                ).fetchone()[0],
                "waiting": conn.execute(
                    "SELECT count(*) FROM collection_jobs WHERE status IN ('queued','retry')"
                ).fetchone()[0],
                "attention": conn.execute(
                    "SELECT count(*) FROM collection_jobs WHERE status IN ('failed','blocked') OR (status='running' AND lease_until<=?)",
                    (self.clock(),),
                ).fetchone()[0],
                "unread": conn.execute(
                    "SELECT count(*) FROM collection_notices WHERE owner_id=? AND acknowledged_at IS NULL",
                    (a.actor.principal_id,),
                ).fetchone()[0],
            }

    def get(self, ident):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return decoded(self._schedule(conn, ident))

    def workers(self):
        authorize(self.store, "collection.configure")
        with self.store._conn() as conn:
            return [
                dict(v)
                for v in conn.execute(
                    "SELECT id,name,kind FROM identity_principals WHERE status='active' ORDER BY name"
                )
            ]

    def job(self, ident):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return decoded(self._job(conn, ident))

    def pulse(self):
        with self.store._immediate() as conn:
            authority = self._authority(conn, "collection.work")
            conn.execute(
                "INSERT INTO collection_workers VALUES(?,?,?) ON CONFLICT(principal_id) DO UPDATE SET credential_id=excluded.credential_id,last_at=excluded.last_at",
                (authority.actor.principal_id, authority.actor.credential_id, self.clock()),
            )

    def worker_status(self):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return [
                dict(v)
                for v in conn.execute(
                    "SELECT w.principal_id,p.name,p.kind,w.last_at FROM collection_workers w JOIN identity_principals p ON p.id=w.principal_id ORDER BY w.last_at DESC"
                )
            ]

    def jobs(self):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return [
                {
                    **decoded(v),
                    "lease_expired": v["status"] == "running" and v["lease_until"] <= self.clock(),
                }
                for v in conn.execute(
                    "SELECT * FROM collection_jobs ORDER BY created_at DESC,rowid DESC LIMIT 200"
                )
            ]

    def history(self, ident):
        authorize(self.store, "collection.read")
        with self.store._conn() as conn:
            return [
                dict(v)
                for v in conn.execute(
                    "SELECT * FROM collection_events WHERE schedule_id=? ORDER BY id", (ident,)
                )
            ]

    def poll(self, ident):
        with self.store._immediate() as conn:
            s = self._schedule(conn, ident)
            self._worker(conn, s)
            conn.execute(
                "UPDATE collection_schedules SET next_at=? WHERE id=?", (self.clock(), ident)
            )
            self._event(conn, ident, "poll_requested")
            return {"id": ident, "requested": True}

    def due(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            a = self._authority(conn, "collection.work")
            return [
                decoded(v)
                for v in conn.execute(
                    "SELECT * FROM collection_schedules WHERE worker_id=? AND enabled=1 AND next_at<=? AND expires_at>? ORDER BY next_at LIMIT 20",
                    (a.actor.principal_id, self.clock(), self.clock()),
                )
            ]

    def observe(self, ident, observation):
        # Called by the server-owned connector worker, never a client observation endpoint.
        if not isinstance(observation, dict) or len(json.dumps(observation)) > 200000:
            fail("Observation exceeds its bounded contract.", "collection_input", 400)
        with self.store._immediate() as conn:
            schedule = self._schedule(conn, ident)
            self._worker(conn, schedule)
            encoded = json.dumps(observation, sort_keys=True, ensure_ascii=False)
            now = self.clock()
            conn.execute(
                "UPDATE collection_schedules SET next_at=?,updated_at=? WHERE id=?",
                (now + schedule["interval_s"], now, ident),
            )
            if encoded == schedule["cursor_json"]:
                row = conn.execute(
                    "SELECT * FROM collection_jobs WHERE schedule_id=? AND sequence=?",
                    (ident, schedule["sequence"]),
                ).fetchone()
                return decoded(row) if row else None
            sequence = schedule["sequence"] + 1
            key = uuid.uuid4().hex
            conn.execute(
                "UPDATE collection_schedules SET sequence=?,cursor_json=? WHERE id=?",
                (sequence, encoded, ident),
            )
            conn.execute(
                "UPDATE collection_jobs SET status='cancelled',error_code='superseded',updated_at=? WHERE schedule_id=? AND status IN ('queued','retry','running')",
                (now, ident),
            )
            conn.execute(
                "INSERT INTO collection_jobs(id,schedule_id,sequence,schedule_version,input_json,status,next_at,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?)",
                (key, ident, sequence, schedule["version"], encoded, now, now, now),
            )
            self._event(
                conn,
                ident,
                "change_observed",
                job=key,
                detail={"sequence": sequence, "input_sha256": digest(observation)},
            )
            self._notice(conn, schedule, self._job(conn, key), "change_detected")
            return decoded(self._job(conn, key))

    def _notice(self, conn, schedule, job, kind):
        conn.execute(
            "INSERT OR IGNORE INTO collection_notices VALUES(?,?,?,?,?,?,?,NULL)",
            (
                uuid.uuid4().hex,
                schedule["owner_id"],
                schedule["id"],
                job["id"],
                kind,
                job["generation"],
                self.clock(),
            ),
        )

    def claim(self):
        with self.store._immediate() as conn:
            a = self._authority(conn, "collection.work")
            now = self.clock()
            expired = conn.execute(
                "SELECT j.* FROM collection_jobs j JOIN collection_schedules s ON s.id=j.schedule_id WHERE s.worker_id=? AND j.status='running' AND j.lease_until<=?",
                (a.actor.principal_id, now),
            ).fetchall()
            for row in expired:
                self._failure(
                    conn, self._schedule(conn, row["schedule_id"]), dict(row), "worker_interrupted"
                )
            rows = conn.execute(
                "SELECT j.* FROM collection_jobs j JOIN collection_schedules s ON s.id=j.schedule_id WHERE s.worker_id=? AND j.status IN ('queued','retry') AND j.next_at<=? ORDER BY j.created_at LIMIT 20",
                (a.actor.principal_id, now),
            ).fetchall()
            for row in rows:
                job = dict(row)
                schedule = self._schedule(conn, job["schedule_id"])
                try:
                    self._worker(conn, schedule)
                except AccessError as error:
                    conn.execute(
                        "UPDATE collection_jobs SET status='blocked',error_code=?,updated_at=? WHERE id=?",
                        (error.code, now, job["id"]),
                    )
                    self._notice(conn, schedule, job, "human_attention")
                    self._event(
                        conn, schedule["id"], "blocked", job=job["id"], detail={"code": error.code}
                    )
                    continue
                if (
                    job["sequence"] != schedule["sequence"]
                    or job["schedule_version"] != schedule["version"]
                ):
                    conn.execute(
                        "UPDATE collection_jobs SET status='blocked',error_code='schedule_changed' WHERE id=?",
                        (job["id"],),
                    )
                    self._notice(conn, schedule, job, "human_attention")
                    continue
                lease = uuid.uuid4().hex
                conn.execute(
                    "UPDATE collection_jobs SET status='running',attempt=attempt+1,lease_id=?,credential_id=?,lease_until=?,updated_at=? WHERE id=?",
                    (lease, a.actor.credential_id, now + 120, now, job["id"]),
                )
                self._event(
                    conn,
                    schedule["id"],
                    "claimed",
                    job=job["id"],
                    detail={"attempt": job["attempt"] + 1, "generation": job["generation"]},
                )
                return decoded(self._job(conn, job["id"]))
            return None

    def _check(self, conn, ident, lease, *, completed=False):
        job = self._job(conn, ident)
        schedule = self._schedule(conn, job["schedule_id"])
        a = self._worker(conn, schedule)
        if (
            job["lease_id"] != lease
            or job["credential_id"] != a.actor.credential_id
            or job["schedule_version"] != schedule["version"]
            or (job["status"] != "succeeded" or not completed)
            and (job["status"] != "running" or job["lease_until"] <= self.clock())
        ):
            fail("This job generation no longer owns execution.", "collection_lease_lost")
        return job, schedule

    def check(self, ident, lease, *, conn=None):
        if conn is not None:
            return self._check(conn, ident, lease)[0]
        with self.store._conn() as read:
            read.execute("BEGIN")
            return self._check(read, ident, lease)[0]

    def heartbeat(self, ident, lease):
        with self.store._immediate() as conn:
            self._check(conn, ident, lease)
            conn.execute(
                "UPDATE collection_jobs SET lease_until=? WHERE id=?", (self.clock() + 120, ident)
            )

    def attach_run(self, ident, lease, run_id):
        with self.store._immediate() as conn:
            self._check(conn, ident, lease)
            conn.execute("UPDATE collection_jobs SET run_id=? WHERE id=?", (run_id, ident))

    def finish(self, ident, lease, result):
        if not isinstance(result, dict) or len(json.dumps(result)) > 200000:
            fail("Result exceeds its bounded contract.", "collection_input", 400)
        with self.store._immediate() as conn:
            job, schedule = self._check(conn, ident, lease, completed=True)
            if job["status"] == "succeeded":
                if json.loads(job["result_json"]) != result:
                    fail("Completed receipt differs.", "collection_result_changed")
                return decoded(job)
            conn.execute(
                "UPDATE collection_jobs SET status='succeeded',result_json=?,error_code=NULL,updated_at=? WHERE id=?",
                (json.dumps(result, sort_keys=True), self.clock(), ident),
            )
            self._event(
                conn,
                schedule["id"],
                "succeeded",
                job=ident,
                detail={"result_sha256": digest(result)},
            )
            self._notice(
                conn, schedule, job, "prepared" if schedule["mode"] == "prepare" else "observed"
            )
            return decoded(self._job(conn, ident))

    def _failure(self, conn, schedule, job, code):
        terminal = job["attempt"] >= schedule["max_attempts"]
        status = "failed" if terminal else "retry"
        delay = min(3600, 30 * 2 ** max(0, job["attempt"] - 1))
        conn.execute(
            "UPDATE collection_jobs SET status=?,error_code=?,next_at=?,lease_until=NULL,updated_at=? WHERE id=?",
            (status, code, self.clock() + delay, self.clock(), job["id"]),
        )
        self._event(
            conn,
            schedule["id"],
            status,
            job=job["id"],
            detail={"code": code, "attempt": job["attempt"]},
        )
        if terminal:
            self._notice(conn, schedule, job, "human_attention")
        return decoded(self._job(conn, job["id"]))

    def fail(self, ident, lease, code):
        code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
            else "collection_failed"
        )
        with self.store._immediate() as conn:
            job, schedule = self._check(conn, ident, lease)
            return self._failure(conn, schedule, job, code)

    def _reason(self, reason):
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            fail("Provide a bounded human reason.", "collection_input", 400)
        return reason.strip()

    def pause(self, ident, *, reason):
        return self._toggle(ident, False, reason)

    def resume(self, ident, *, reason):
        return self._toggle(ident, True, reason)

    def _toggle(self, ident, enabled, reason):
        reason = self._reason(reason)
        with self.store._immediate() as conn:
            a = self._authority(conn, "collection.configure")
            s = self._schedule(conn, ident)
            source = self.store.get_source(s["source_name"])
            if enabled and (not source or self._binding(source, s["mode"]) != s["source_sha256"]):
                fail(
                    "Source target changed. Configure its exact target again.",
                    "collection_source_changed",
                )
            if (
                enabled
                and conn.execute(
                    "SELECT 1 FROM collection_schedules WHERE source_id=? AND enabled=1 AND id!=?",
                    (s["source_id"], ident),
                ).fetchone()
            ):
                fail(
                    "Another active schedule already owns this source.",
                    "collection_schedule_exists",
                )
            conn.execute(
                "UPDATE collection_schedules SET enabled=?,version=version+1,policy_version=?,expires_at=?,reason=?,next_at=?,updated_at=? WHERE id=?",
                (
                    int(enabled),
                    a.policy_version,
                    self.clock() + 86400,
                    reason,
                    self.clock(),
                    self.clock(),
                    ident,
                ),
            )
            self._event(conn, ident, "resumed" if enabled else "paused", detail={"reason": reason})
            return decoded(self._schedule(conn, ident))

    def cancel(self, ident, *, reason):
        reason = self._reason(reason)
        with self.store._immediate() as conn:
            self._authority(conn, "collection.configure")
            job = self._job(conn, ident)
            if job["status"] == "succeeded":
                fail("A completed receipt cannot be cancelled. Review its candidate.")
            conn.execute(
                "UPDATE collection_jobs SET status='cancelled',error_code='human_cancelled',updated_at=? WHERE id=?",
                (self.clock(), ident),
            )
            self._event(conn, job["schedule_id"], "cancelled", job=ident, detail={"reason": reason})
            return decoded(self._job(conn, ident))

    def retry(self, ident, *, reason):
        reason = self._reason(reason)
        with self.store._immediate() as conn:
            self._authority(conn, "collection.configure")
            job = self._job(conn, ident)
            schedule = self._schedule(conn, job["schedule_id"])
            if (
                job["status"] not in {"failed", "blocked", "cancelled"}
                or job["sequence"] != schedule["sequence"]
            ):
                fail("Retry the latest terminal job only; superseded inputs remain history.")
            conn.execute(
                "UPDATE collection_jobs SET status='queued',attempt=0,generation=generation+1,schedule_version=?,next_at=?,lease_id=NULL,credential_id=NULL,lease_until=NULL,error_code=NULL,updated_at=? WHERE id=?",
                (schedule["version"], self.clock(), self.clock(), ident),
            )
            self._event(conn, schedule["id"], "human_retry", job=ident, detail={"reason": reason})
            return decoded(self._job(conn, ident))

    def notices(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            a = self._authority(conn, "collection.read")
            return [
                dict(v)
                for v in conn.execute(
                    "SELECT * FROM collection_notices WHERE owner_id=? ORDER BY created_at DESC,rowid DESC LIMIT 200",
                    (a.actor.principal_id,),
                )
            ]

    def ack(self, ident):
        with self.store._immediate() as conn:
            a = self._authority(conn, "collection.read")
            row = conn.execute(
                "SELECT * FROM collection_notices WHERE id=? AND owner_id=?",
                (ident, a.actor.principal_id),
            ).fetchone()
            if not row:
                fail(
                    "This notification belongs to another owner.", "collection_notice_missing", 404
                )
            conn.execute(
                "UPDATE collection_notices SET acknowledged_at=coalesce(acknowledged_at,?) WHERE id=?",
                (self.clock(), ident),
            )
            return {"acknowledged": True}
