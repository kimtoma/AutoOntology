# SPDX-License-Identifier: MIT
"""Explicit workflow context and transparent human timing; no inferred productivity."""

import json
import math
import re
import statistics
import time
import uuid
from datetime import date, datetime, timezone

from autoontology.security.context import audit_effect, authorize
from autoontology.security.policy import AccessError
from autoontology.store.operations import digest


def fail(message, code="ax_input_invalid", status=400):
    raise AccessError(message, code, status)


def bounded(value, label, limit=1000):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        fail(f"Use a bounded nonempty {label}.")
    return value.strip()


def cohort(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        fail(f"Use a stable {label} key.")
    return value


def version_row(row):
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    if digest(item["payload"]) != item["sha256"]:
        fail("Measurement context integrity check failed.", "ax_corrupt", 409)
    return item


def context_binding(store, ident):
    """Internal pure read; callers already authorize the enclosing case operation."""
    with store._conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ax_context_heads'").fetchone():
            return None
        row = conn.execute(
            "SELECT v.* FROM ax_context_versions v JOIN ax_context_heads h ON h.version_id=v.id WHERE h.case_id=?",
            (ident,),
        ).fetchone()
        item = version_row(row)
        return (
            {"id": item["id"], "sha256": item["sha256"], "payload": item["payload"]}
            if item
            else None
        )


class AX:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    def stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="milliseconds")

    def human(self, action, conn):
        decision = authorize(self.store, action, conn=conn)
        if decision is None or decision.actor.kind != "human":
            fail(
                "Individual human authentication is required for human effort and baseline records.",
                "ax_human_required",
                403,
            )
        return decision

    def context(self, ident):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            from autoontology.store.cases import Cases

            Cases(self.store)._get(conn, ident)
            versions = [
                version_row(row)
                for row in conn.execute(
                    "SELECT * FROM ax_context_versions WHERE case_id=? ORDER BY version DESC",
                    (ident,),
                )
            ]
            head = conn.execute(
                "SELECT version_id FROM ax_context_heads WHERE case_id=?", (ident,)
            ).fetchone()
            current = next((v for v in versions if head and v["id"] == head[0]), None)
            feedback = []
            for feedback_id in current["payload"]["feedback_ids"] if current else []:
                item = conn.execute(
                    "SELECT * FROM ops_feedback WHERE id=?", (feedback_id,)
                ).fetchone()
                if item:
                    feedback.append(dict(item))
            effort_summary = None
            try:
                authorize(self.store, "ax.read", conn=conn)
            except AccessError:
                pass
            else:
                intervals = [
                    dict(row)
                    for row in conn.execute("SELECT * FROM ax_efforts WHERE case_id=?", (ident,))
                ]
                basis = digest(sorted(intervals, key=lambda row: row["id"]))
                effort_summary = {
                    "stopped_count": sum(row["status"] == "stopped" for row in intervals),
                    "running_count": sum(row["status"] == "running" for row in intervals),
                    "discarded_count": sum(row["status"] == "discarded" for row in intervals),
                    "recorded_seconds": sum(
                        row["elapsed_seconds"] for row in intervals if row["status"] == "stopped"
                    ),
                    "attested": bool(
                        conn.execute(
                            "SELECT 1 FROM ax_effort_attestations WHERE case_id=? AND basis_sha256=?",
                            (ident, basis),
                        ).fetchone()
                    ),
                }
            return {
                "current": next((v for v in versions if head and v["id"] == head[0]), None),
                "versions": versions,
                "feedback": feedback,
                "effort_summary": effort_summary,
            }

    def set_context(self, ident, data, *, previous, reason):
        from autoontology.store.cases import Cases
        from autoontology.store.quality import Quality

        reason = bounded(reason, "context reason", 5000)
        if not isinstance(data, dict) or set(data) - {"resolution_intent"} != {
            "workflow",
            "complexity",
            "effective_at",
            "required_suites",
            "feedback_ids",
            "measurement_scope",
        }:
            fail("Use the explicit case context schema.")
        payload = {
            **data,
            "resolution_intent": data.get("resolution_intent", "knowledge_change"),
            "workflow": cohort(data["workflow"], "workflow"),
            "complexity": cohort(data["complexity"], "complexity"),
        }
        if payload["resolution_intent"] not in ("knowledge_change", "no_change"):
            fail("Choose a knowledge change or an explicit no-change disposition.")
        if payload["measurement_scope"] not in ("business", "simulation"):
            fail("Identify business measurements or explicit simulations.")
        if payload["effective_at"] is not None:
            try:
                payload["effective_at"] = date.fromisoformat(payload["effective_at"]).isoformat()
            except (TypeError, ValueError):
                fail("Use an ISO effective date or null.")
        suites = payload["required_suites"]
        feedback = payload["feedback_ids"]
        if (
            not isinstance(suites, list)
            or len(suites) > 100
            or not isinstance(feedback, list)
            or len(feedback) > 100
            or any(type(i) is not int or i < 1 for i in feedback)
        ):
            fail("Use bounded evaluation and feedback selections.")
        if len(set(feedback)) != len(feedback):
            fail("Choose distinct feedback records.")
        active = {v["id"]: v["sha256"] for v in Quality(self.store).active()}
        if any(
            not isinstance(v, dict)
            or set(v) != {"id", "sha256"}
            or not isinstance(v["id"], str)
            or active.get(v["id"]) != v["sha256"]
            for v in suites
        ):
            fail(
                "Required evaluations must be current exact active versions.",
                "ax_suite_changed",
                409,
            )
        if len({v["id"] for v in suites}) != len(suites):
            fail("Choose distinct required evaluations.")
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            case = Cases(self.store)._get(conn, ident)
            if case["status"] in {"reviewed", "resolved", "cancelled"}:
                fail("Reopen the case before changing its confirmed context.", "ax_case_final", 409)
            head = conn.execute(
                "SELECT version_id FROM ax_context_heads WHERE case_id=?", (ident,)
            ).fetchone()
            if (head[0] if head else None) != previous:
                fail("Case context changed. Refresh before saving.", "ax_context_stale", 409)
            current = {
                v[0]: v[1]
                for v in conn.execute(
                    "SELECT v.id,v.sha256 FROM quality_versions v JOIN quality_heads h ON h.version_id=v.id"
                )
            }
            if any(current.get(v["id"]) != v["sha256"] for v in suites):
                fail("Required evaluation changed during preparation.", "ax_suite_changed", 409)
            for feedback_id in feedback:
                row = conn.execute(
                    "SELECT * FROM ops_feedback WHERE id=?", (feedback_id,)
                ).fetchone()
                if row is None:
                    fail("Feedback is missing.", "ax_feedback_missing", 404)
                linked = conn.execute(
                    "SELECT * FROM ax_feedback_links WHERE feedback_id=?", (feedback_id,)
                ).fetchone()
                source_hash = digest(
                    {k: row[k] for k in ("id", "question", "answer", "note", "actor", "created_at")}
                )
                if linked and (
                    linked["case_id"] != ident or linked["source_sha256"] != source_hash
                ):
                    fail(
                        "Feedback belongs to another case or changed.", "ax_feedback_assigned", 409
                    )
                if not linked:
                    conn.execute(
                        "INSERT INTO ax_feedback_links VALUES(?,?,?,?)",
                        (feedback_id, ident, source_hash, self.stamp()),
                    )
            version = conn.execute(
                "SELECT coalesce(max(version),0)+1 FROM ax_context_versions WHERE case_id=?",
                (ident,),
            ).fetchone()[0]
            key = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO ax_context_versions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    ident,
                    version,
                    previous,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    digest(payload),
                    authority.actor.principal_id if authority else "local-unverified",
                    reason,
                    self.stamp(),
                ),
            )
            conn.execute(
                "INSERT INTO ax_context_heads VALUES(?,?) ON CONFLICT(case_id) DO UPDATE SET version_id=excluded.version_id",
                (ident, key),
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "case_context_saved", "version_id": key, "sha256": digest(payload)},
                conn=conn,
            )
            return version_row(
                conn.execute("SELECT * FROM ax_context_versions WHERE id=?", (key,)).fetchone()
            )

    def efforts(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            decision = self.human("ax.record", conn)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ax_efforts WHERE actor=? ORDER BY started_at DESC",
                    (decision.actor.principal_id,),
                )
            ]

    def start_effort(self, ident, *, note):
        from autoontology.store.cases import Cases

        note = bounded(note, "work note", 1000)
        with self.store._immediate() as conn:
            decision = self.human("ax.record", conn)
            case = Cases(self.store)._get(conn, ident)
            if case["status"] in {"resolved", "cancelled"}:
                fail("Reopen the case before recording more work.", "ax_case_final", 409)
            running = conn.execute(
                "SELECT * FROM ax_efforts WHERE actor=? AND status='running'",
                (decision.actor.principal_id,),
            ).fetchone()
            if running:
                if running["case_id"] == ident:
                    return dict(running)
                fail(
                    "Stop or discard your existing timer before starting another.",
                    "ax_timer_running",
                    409,
                )
            key = uuid.uuid4().hex
            stamp = self.stamp()
            conn.execute(
                "INSERT INTO ax_efforts VALUES(?,?,?,'running',?,NULL,NULL,?,NULL,?)",
                (key, ident, decision.actor.principal_id, self.clock(), note, stamp),
            )
            conn.execute(
                "INSERT INTO ax_effort_events(effort_id,operation,actor,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    key,
                    "started",
                    decision.actor.principal_id,
                    json.dumps({"case_id": ident, "note": note}),
                    stamp,
                ),
            )
            audit_effect(
                self.store, decision, ident, {"operation": "effort_started", "id": key}, conn=conn
            )
            return dict(conn.execute("SELECT * FROM ax_efforts WHERE id=?", (key,)).fetchone())

    def stop_effort(self, ident, *, note, discard=False):
        note = bounded(note, "stop/discard reason", 1000)
        with self.store._immediate() as conn:
            decision = self.human("ax.record", conn)
            row = conn.execute(
                "SELECT * FROM ax_efforts WHERE id=? AND actor=?",
                (ident, decision.actor.principal_id),
            ).fetchone()
            if row is None:
                fail("No owned work timer with this identifier.", "ax_timer_missing", 404)
            if row["status"] != "running":
                return dict(row)
            elapsed = self.clock() - row["started_at"]
            if not discard and not 0 <= elapsed <= 4 * 3600:
                fail(
                    "A timer older than four hours or affected by clock reversal must be explicitly discarded.",
                    "ax_timer_unreliable",
                    409,
                )
            finished = self.clock()
            status = "discarded" if discard else "stopped"
            conn.execute(
                "UPDATE ax_efforts SET status=?,finished_at=?,elapsed_seconds=?,stop_note=? WHERE id=? AND status='running'",
                (status, finished, None if discard else round(elapsed, 3), note, ident),
            )
            conn.execute(
                "INSERT INTO ax_effort_events(effort_id,operation,actor,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    ident,
                    status,
                    decision.actor.principal_id,
                    json.dumps({"elapsed_seconds": None if discard else elapsed, "note": note}),
                    self.stamp(),
                ),
            )
            audit_effect(self.store, decision, ident, {"operation": "effort_" + status}, conn=conn)
            return dict(conn.execute("SELECT * FROM ax_efforts WHERE id=?", (ident,)).fetchone())

    def baselines(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "ax.read", conn=conn)
            return self._baselines(conn)

    @staticmethod
    def _baselines(conn):
        return [
            {**dict(row), "voided": row["void_reason"] is not None}
            for row in conn.execute(
                "SELECT b.*,v.reason AS void_reason FROM ax_baselines b LEFT JOIN ax_baseline_voids v ON v.sample_id=b.id ORDER BY b.rowid DESC"
            )
        ]

    def add_baseline(self, data, *, confirm=False):
        if confirm is not True:
            fail("Explicitly confirm the baseline provenance.", "ax_confirmation_required")
        if not isinstance(data, dict) or set(data) - {"request_key"} != {
            "workflow",
            "complexity",
            "lead_seconds",
            "active_seconds",
            "source",
            "reference",
            "measurement_scope",
        }:
            fail("Use the complete baseline schema.")
        workflow = cohort(data["workflow"], "workflow")
        complexity = cohort(data["complexity"], "complexity")
        for key in ("lead_seconds", "active_seconds"):
            if (
                type(data[key]) not in (int, float)
                or not math.isfinite(data[key])
                or not 0 < data[key] <= 366 * 24 * 3600
            ):
                fail("Use positive finite measured seconds, bounded to one year.")
        if data["source"] not in ("observed", "reported") or data["measurement_scope"] not in (
            "business",
            "simulation",
        ):
            fail("Identify observed/reported provenance and business/simulation scope.")
        reference = bounded(data["reference"], "measurement reference", 2000)
        with self.store._immediate() as conn:
            decision = self.human("ax.configure", conn)
            canonical = {
                "workflow": workflow,
                "complexity": complexity,
                "lead_seconds": float(data["lead_seconds"]),
                "active_seconds": float(data["active_seconds"]),
                "source": data["source"],
                "reference": reference,
                "measurement_scope": data["measurement_scope"],
            }
            supplied_key = data.get("request_key")
            if "request_key" in data and (
                not isinstance(supplied_key, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", supplied_key)
            ):
                fail("Use a nonempty bounded measurement request key.")
            ident = supplied_key if supplied_key is not None else digest(canonical)
            if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", ident):
                fail("Use a bounded measurement request key.")
            old = conn.execute("SELECT * FROM ax_baselines WHERE id=?", (ident,)).fetchone()
            if old:
                if any(old[key] != value for key, value in canonical.items()):
                    fail(
                        "This measurement request key belongs to different data.",
                        "ax_request_conflict",
                        409,
                    )
                return dict(old)
            conn.execute(
                "INSERT INTO ax_baselines VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    workflow,
                    complexity,
                    float(data["lead_seconds"]),
                    float(data["active_seconds"]),
                    data["source"],
                    reference,
                    data["measurement_scope"],
                    decision.actor.principal_id,
                    self.stamp(),
                ),
            )
            audit_effect(
                self.store,
                decision,
                ident,
                {
                    "operation": "baseline_recorded",
                    "provenance": data["source"],
                    "scope": data["measurement_scope"],
                },
                conn=conn,
            )
            return dict(conn.execute("SELECT * FROM ax_baselines WHERE id=?", (ident,)).fetchone())

    def void_baseline(self, ident, *, reason, confirm=False):
        if confirm is not True:
            fail("Confirm this immutable baseline correction.", "ax_confirmation_required")
        reason = bounded(reason, "baseline correction reason", 2000)
        with self.store._immediate() as conn:
            decision = self.human("ax.configure", conn)
            if not conn.execute("SELECT 1 FROM ax_baselines WHERE id=?", (ident,)).fetchone():
                fail("Baseline sample not found.", "ax_sample_missing", 404)
            old = conn.execute(
                "SELECT * FROM ax_baseline_voids WHERE sample_id=?", (ident,)
            ).fetchone()
            if old:
                return dict(old)
            conn.execute(
                "INSERT INTO ax_baseline_voids VALUES(?,?,?,?)",
                (ident, reason, decision.actor.principal_id, self.stamp()),
            )
            audit_effect(self.store, decision, ident, {"operation": "baseline_voided"}, conn=conn)
            return dict(
                conn.execute(
                    "SELECT * FROM ax_baseline_voids WHERE sample_id=?", (ident,)
                ).fetchone()
            )

    def baseline_basis(self, workflow, complexity):
        rows = [
            v
            for v in self.baselines()
            if v["workflow"] == workflow
            and v["complexity"] == complexity
            and v["source"] == "observed"
            and v["measurement_scope"] == "business"
            and not v["voided"]
        ]
        return digest(sorted(rows, key=lambda v: v["id"])) if rows else None

    def set_target(self, data, *, confirm=False):
        if confirm is not True:
            fail(
                "Confirm the owner-set numeric targets and their measured basis.",
                "ax_confirmation_required",
            )
        if not isinstance(data, dict) or set(data) != {
            "workflow",
            "complexity",
            "lead_reduction_percent",
            "effort_reduction_percent",
            "reason",
            "baseline_sha256",
        }:
            fail("Use the complete target and measured baseline hash.")
        data = {
            **data,
            "workflow": cohort(data["workflow"], "workflow"),
            "complexity": cohort(data["complexity"], "complexity"),
            "reason": bounded(data["reason"], "target reason", 2000),
        }
        for key in ("lead_reduction_percent", "effort_reduction_percent"):
            if (
                type(data[key]) not in (int, float)
                or not math.isfinite(data[key])
                or not 0 <= data[key] < 100
            ):
                fail("Use finite reduction targets from zero to below one hundred percent.")
        with self.store._immediate() as conn:
            decision = self.human("ax.configure", conn)
            rows = [
                v
                for v in self._baselines(conn)
                if v["workflow"] == data["workflow"]
                and v["complexity"] == data["complexity"]
                and v["source"] == "observed"
                and v["measurement_scope"] == "business"
                and not v["voided"]
            ]
            basis = digest(sorted(rows, key=lambda v: v["id"])) if rows else None
            if basis is None or data["baseline_sha256"] != basis:
                fail(
                    "A current measured business baseline is required; reported or simulated values are insufficient.",
                    "ax_baseline_required",
                    409,
                )
            old = conn.execute(
                "SELECT * FROM ax_targets WHERE workflow=? AND complexity=? ORDER BY rowid DESC LIMIT 1",
                (data["workflow"], data["complexity"]),
            ).fetchone()
            if old and old["sha256"] == digest(data):
                return version_row(old)
            ident = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO ax_targets VALUES(?,?,?,?,?,?,?)",
                (
                    ident,
                    data["workflow"],
                    data["complexity"],
                    json.dumps(data, sort_keys=True, ensure_ascii=False),
                    digest(data),
                    decision.actor.principal_id,
                    self.stamp(),
                ),
            )
            audit_effect(
                self.store,
                decision,
                ident,
                {"operation": "ax_target_set", "baseline_sha256": basis},
                conn=conn,
            )
            return version_row(
                conn.execute("SELECT * FROM ax_targets WHERE id=?", (ident,)).fetchone()
            )

    @staticmethod
    def distribution(values):
        values = sorted(values)
        return {
            "count": len(values),
            "median": statistics.median(values) if values else None,
            "p90": values[max(0, math.ceil(len(values) * 0.9) - 1)] if values else None,
        }

    def overview(self):
        from autoontology.store.cases import Cases

        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "ax.read", conn=conn)
            cases = [
                Cases.row(row)
                for row in conn.execute("SELECT * FROM ops_cases ORDER BY created_at")
            ]
            contexts = {
                row["case_id"]: version_row(row)["payload"]
                for row in conn.execute(
                    "SELECT v.* FROM ax_context_versions v JOIN ax_context_heads h ON h.version_id=v.id"
                )
            }
            efforts = [dict(row) for row in conn.execute("SELECT * FROM ax_efforts")]
            attestations = [
                dict(row)
                for row in conn.execute("SELECT * FROM ax_effort_attestations ORDER BY rowid DESC")
            ]
            baselines = self._baselines(conn)
            targets = [
                version_row(row)
                for row in conn.execute("SELECT * FROM ax_targets ORDER BY rowid DESC")
            ]
        groups = {}
        for case in cases:
            context = contexts.get(case["id"], {})
            key = (
                context.get("workflow", "unclassified"),
                context.get("complexity", "unclassified"),
                context.get("measurement_scope", "business"),
            )
            group = groups.setdefault(key, {"items": [], "lead": [], "effort": []})
            intervals = [v for v in efforts if v["case_id"] == case["id"]]
            stopped = [v for v in intervals if v["status"] == "stopped"]
            basis = digest(sorted(intervals, key=lambda value: value["id"]))
            attested = any(
                row["case_id"] == case["id"] and row["basis_sha256"] == basis
                for row in attestations
            )
            active = sum(v["elapsed_seconds"] for v in stopped) if stopped else None
            complete = case["status"] == "resolved"
            end = (
                case["outcome"].get("usable_at") or case["outcome"].get("resolved_at")
                if complete
                else None
            )
            lead = (
                max(
                    0,
                    datetime.fromisoformat(end).timestamp()
                    - datetime.fromisoformat(case["created_at"]).timestamp(),
                )
                if end
                else None
            )
            item = {
                "case_id": case["id"],
                "title": case["title"],
                "status": case["status"],
                "lead_seconds": lead,
                "recorded_effort_seconds": active,
                "effort_attested": attested,
                "running_timers": sum(v["status"] == "running" for v in intervals),
                "discarded_timers": sum(v["status"] == "discarded" for v in intervals),
                "elapsed_seconds": max(
                    0, self.clock() - datetime.fromisoformat(case["created_at"]).timestamp()
                ),
            }
            group["items"].append(item)
            if complete and lead is not None:
                group["lead"].append(lead)
            if complete and attested and active is not None and not item["running_timers"]:
                group["effort"].append(active)
        for row in baselines:
            key = (row["workflow"], row["complexity"], row["measurement_scope"])
            groups.setdefault(key, {"items": [], "lead": [], "effort": []})
        cohorts = []
        for (workflow, complexity, scope), group in sorted(groups.items()):
            samples = [
                v
                for v in baselines
                if (v["workflow"], v["complexity"], v["measurement_scope"])
                == (workflow, complexity, scope)
                and not v["voided"]
            ]
            observed = [v for v in samples if v["source"] == "observed"]
            baseline = {
                "observed_count": len(observed),
                "reported_count": sum(v["source"] == "reported" for v in samples),
                "lead": self.distribution([v["lead_seconds"] for v in observed]),
                "effort": self.distribution([v["active_seconds"] for v in observed]),
            }
            assisted = {
                "lead": self.distribution(group["lead"]),
                "effort": self.distribution(group["effort"]),
            }
            basis = digest(sorted(observed, key=lambda v: v["id"])) if observed else None
            target = (
                next(
                    (
                        v
                        for v in targets
                        if v["workflow"] == workflow and v["complexity"] == complexity
                    ),
                    None,
                )
                if scope == "business"
                else None
            )
            returned = (
                baseline["effort"]["median"] - assisted["effort"]["median"]
                if scope == "business"
                and observed
                and group["effort"]
                and len(group["effort"]) == len(group["lead"])
                else None
            )
            cohorts.append(
                {
                    "workflow": workflow,
                    "complexity": complexity,
                    "measurement_scope": scope,
                    "baseline": baseline,
                    "baseline_sha256": basis,
                    "assisted": assisted,
                    "target": target,
                    "target_stale": bool(target and target["payload"]["baseline_sha256"] != basis),
                    "time_returned_seconds": returned,
                    "active_cases": sum(
                        v["status"] not in ("resolved", "cancelled") for v in group["items"]
                    ),
                    "cancelled_cases": sum(v["status"] == "cancelled" for v in group["items"]),
                    "completed_cases": sum(v["status"] == "resolved" for v in group["items"]),
                    "cases": group["items"],
                }
            )
        return {
            "cohorts": cohorts,
            "voided_baselines": sum(v["voided"] for v in baselines),
            "provenance": "Explicit user-controlled timers and human-attested baseline observations; no covert presence tracking or causal savings claim.",
            "p90_method": "nearest rank ceil(0.9*n)",
            "generated_at": self.stamp(),
        }

    def attest_effort(self, ident, *, reason, confirm=False):
        from autoontology.store.cases import Cases

        if confirm is not True:
            fail(
                "Confirm that all human work for this completed case is recorded.",
                "ax_confirmation_required",
            )
        reason = bounded(reason, "effort coverage reason", 2000)
        with self.store._immediate() as conn:
            decision = self.human("ax.record", conn)
            case = Cases(self.store)._get(conn, ident)
            if case["owner_id"] != decision.actor.principal_id:
                authorize(self.store, "ax.configure", conn=conn)
            if case["status"] != "resolved":
                fail(
                    "Link a usable case outcome before attesting its human effort.",
                    "ax_case_incomplete",
                    409,
                )
            rows = [
                dict(row)
                for row in conn.execute("SELECT * FROM ax_efforts WHERE case_id=?", (ident,))
            ]
            if not any(row["status"] == "stopped" for row in rows) or any(
                row["status"] == "running" for row in rows
            ):
                fail(
                    "Record and stop the complete work intervals before confirming coverage.",
                    "ax_effort_incomplete",
                    409,
                )
            basis = digest(sorted(rows, key=lambda row: row["id"]))
            old = conn.execute(
                "SELECT * FROM ax_effort_attestations WHERE case_id=? AND basis_sha256=?",
                (ident, basis),
            ).fetchone()
            if old:
                return dict(old)
            key = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO ax_effort_attestations VALUES(?,?,?,?,?,?)",
                (key, ident, basis, decision.actor.principal_id, reason, self.stamp()),
            )
            audit_effect(
                self.store,
                decision,
                ident,
                {"operation": "effort_coverage_attested", "basis_sha256": basis},
                conn=conn,
            )
            return dict(
                conn.execute("SELECT * FROM ax_effort_attestations WHERE id=?", (key,)).fetchone()
            )

    def feedback_case(self, feedback_id, *, title, reason, owner_id=None):
        from autoontology.store.cases import Cases

        authorize(self.store, "review.suggest")
        title = bounded(title, "case title", 200)
        reason = bounded(reason, "business reason", 3000)
        with self.store._conn() as conn:
            row = conn.execute("SELECT * FROM ops_feedback WHERE id=?", (feedback_id,)).fetchone()
            link = conn.execute(
                "SELECT * FROM ax_feedback_links WHERE feedback_id=?", (feedback_id,)
            ).fetchone()
        if row is None:
            fail("Feedback is missing.", "ax_feedback_missing", 404)
        if link:
            return Cases(self.store).get(link["case_id"])
        if row["resolved"]:
            fail(
                "Reopen the feedback explicitly before preparing a new case.",
                "ax_feedback_resolved",
                409,
            )
        case = Cases(self.store).create(
            title=title,
            reason=reason + "\n\nFeedback question: " + row["question"][:1000],
            owner_id=owner_id,
            request_key=f"feedback:{feedback_id}",
        )
        # A crash between draft creation and linking leaves a named draft; retry uses
        # its stable request key and completes the link without duplicating a case.
        context = {
            "workflow": "unclassified",
            "complexity": "unclassified",
            "effective_at": None,
            "required_suites": [],
            "feedback_ids": [feedback_id],
            "measurement_scope": "business",
        }
        old = context_binding(self.store, case["id"])
        self.set_context(
            case["id"],
            context,
            previous=old["id"] if old else None,
            reason="Feedback triage: " + reason,
        )
        return Cases(self.store).get(case["id"])

    def resolve_feedback(self, feedback_id, ident, *, confirm=False):
        from autoontology.store.cases import Cases

        if confirm is not True:
            fail(
                "Confirm feedback resolution against the linked usable outcome.",
                "ax_confirmation_required",
            )
        with self.store._immediate() as conn:
            decision = authorize(self.store, "feedback.manage", conn=conn)
            case = Cases(self.store)._get(conn, ident)
            link = conn.execute(
                "SELECT * FROM ax_feedback_links WHERE feedback_id=?", (feedback_id,)
            ).fetchone()
            current = context_binding(self.store, ident)
            if (
                not link
                or link["case_id"] != ident
                or not current
                or feedback_id not in current["payload"]["feedback_ids"]
            ):
                fail(
                    "Feedback must belong to this exact current case context.",
                    "ax_feedback_assigned",
                    409,
                )
            if case["status"] != "resolved" or not case["outcome"].get("usable_at"):
                fail(
                    "Complete and link the case outcome before resolving feedback.",
                    "ax_case_incomplete",
                    409,
                )
            row = conn.execute("SELECT * FROM ops_feedback WHERE id=?", (feedback_id,)).fetchone()
            if (
                row is None
                or digest(
                    {
                        key: row[key]
                        for key in ("id", "question", "answer", "note", "actor", "created_at")
                    }
                )
                != link["source_sha256"]
            ):
                fail("The original feedback changed.", "ax_feedback_changed", 409)
            conn.execute("UPDATE ops_feedback SET resolved=1 WHERE id=?", (feedback_id,))
            audit_effect(
                self.store,
                decision,
                feedback_id,
                {
                    "operation": "feedback_case_resolved",
                    "case_id": ident,
                    "outcome_sha256": digest(case["outcome"]),
                },
                conn=conn,
            )
            return {
                "feedback_id": feedback_id,
                "case_id": ident,
                "resolved": True,
                "outcome": case["outcome"],
            }

    def reopen_linked_feedback(self, conn, ident):
        bound = context_binding(self.store, ident)
        if not bound or not bound["payload"]["feedback_ids"]:
            return
        decision = authorize(self.store, "feedback.manage", conn=conn)
        changed = []
        for feedback_id in bound["payload"]["feedback_ids"]:
            link = conn.execute(
                "SELECT * FROM ax_feedback_links WHERE feedback_id=? AND case_id=?",
                (feedback_id, ident),
            ).fetchone()
            row = conn.execute("SELECT * FROM ops_feedback WHERE id=?", (feedback_id,)).fetchone()
            if (
                not row
                or not link
                or digest(
                    {
                        key: row[key]
                        for key in ("id", "question", "answer", "note", "actor", "created_at")
                    }
                )
                != link["source_sha256"]
            ):
                fail(
                    "Linked original feedback changed; recover it before reopening.",
                    "ax_feedback_changed",
                    409,
                )
            if row["resolved"]:
                conn.execute("UPDATE ops_feedback SET resolved=0 WHERE id=?", (feedback_id,))
                changed.append(feedback_id)
        if changed:
            audit_effect(
                self.store,
                decision,
                ident,
                {"operation": "case_feedback_reopened", "feedback_ids": changed},
                conn=conn,
            )
