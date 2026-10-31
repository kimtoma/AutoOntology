# SPDX-License-Identifier: MIT
"""Immutable evaluation-suite versions and deterministic candidate/answer checks."""

import hashlib
import json
import re
import uuid
from pathlib import Path

from autoontology.security.context import audit_effect, authorize
from autoontology.security.policy import AccessError
from autoontology.store.db import now_iso
from autoontology.store.operations import digest


def fail(message, code="quality_input_invalid", status=400):
    raise AccessError(message, code, status)


class Quality:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def policy():
        from autoontology.ontology import rules

        return {
            "version": 1,
            "validator_sha256": hashlib.sha256(
                Path(__file__).read_bytes() + Path(rules.__file__).read_bytes()
            ).hexdigest(),
        }

    @staticmethod
    def clean(data):
        if not isinstance(data, dict) or set(data) - {
            "title",
            "description",
            "questions",
            "assertions",
        }:
            fail("Use the evaluation suite schema.")
        try:
            data = json.loads(json.dumps(data, ensure_ascii=False))
        except (TypeError, ValueError):
            fail("Use JSON-safe evaluation fields.")
        result = {
            "title": data.get("title"),
            "description": data.get("description", ""),
            "questions": data.get("questions", []),
            "assertions": data.get("assertions", []),
        }
        if (
            not isinstance(result["title"], str)
            or not 1 <= len(result["title"].strip()) <= 200
            or not isinstance(result["description"], str)
            or len(result["description"]) > 5000
        ):
            fail("Use a bounded title and description.")
        if any(
            not isinstance(result[k], list) or len(result[k]) > 100
            for k in ("questions", "assertions")
        ) or not (result["questions"] or result["assertions"]):
            fail("Configure 1–100 bounded questions or model assertions.")
        seen = set()
        for case in [*result["questions"], *result["assertions"]]:
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", case["id"])
                or case["id"] in seen
            ):
                fail("Each suite item needs a distinct stable identifier.")
            seen.add(case["id"])
        for q in result["questions"]:
            if set(q) - {"id", "question", "expected", "mode", "forbidden", "minimum_citations"}:
                fail("Unsupported question criterion.")
            for key in ("question", "expected"):
                if not isinstance(q.get(key), str) or not 1 <= len(q[key].strip()) <= 2000:
                    fail("Questions and expected text must be bounded and nonempty.")
            if q.setdefault("mode", "contains") not in ("contains", "exact", "all_terms"):
                fail("Use contains, exact or all_terms matching.")
            forbidden = q.setdefault("forbidden", [])
            if (
                not isinstance(forbidden, list)
                or len(forbidden) > 30
                or any(not isinstance(v, str) or not 1 <= len(v) <= 200 for v in forbidden)
            ):
                fail("Use bounded forbidden phrases.")
            minimum = q.setdefault("minimum_citations", 1)
            if type(minimum) is not int or not 1 <= minimum <= 12:
                fail("Require between one and twelve known citations.")
        for a in result["assertions"]:
            if (
                set(a) != {"id", "collection", "subject", "field", "expected"}
                or a.get("collection") not in ("classes", "individuals", "properties")
                or not isinstance(a.get("subject"), str)
                or not 1 <= len(a["subject"]) <= 300
                or a.get("field")
                not in (
                    "label",
                    "type",
                    "domain",
                    "range",
                    "rule.scope",
                    "rule.measure",
                    "rule.operator",
                    "rule.amount",
                    "rule.currency",
                    "rule.authority",
                    "rule.effective_from",
                    "rule.effective_to",
                    "rule.priority",
                )
                or not isinstance(a["expected"], (str, int, type(None)))
                or isinstance(a["expected"], str)
                and len(a["expected"]) > 2000
            ):
                fail("Use a bounded semantic-field equality assertion.")
        return json.loads(json.dumps(result, ensure_ascii=False))

    @staticmethod
    def row(row):
        if row is None:
            fail("Evaluation version not found.", "quality_missing", 404)
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        if digest(item["payload"]) != item["sha256"]:
            fail("Evaluation suite integrity check failed.", "quality_corrupt", 409)
        return item

    def get(self, ident):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            return self.row(
                conn.execute("SELECT * FROM quality_versions WHERE id=?", (ident,)).fetchone()
            )

    def versions(self, key=None):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            rows = conn.execute("SELECT * FROM quality_versions ORDER BY rowid DESC").fetchall()
            return [self.row(row) for row in rows if key is None or row["suite_key"] == key]

    def active(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='quality_heads'"
            ).fetchone():
                return []
            return [
                self.row(row)
                for row in conn.execute(
                    "SELECT v.* FROM quality_versions v JOIN quality_heads h ON v.id=h.version_id ORDER BY v.suite_key"
                )
            ]

    def prepare(self, key, data, *, previous):
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            fail("Use a bounded suite key.")
        payload = self.clean(data)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            old = conn.execute(
                "SELECT version_id FROM quality_heads WHERE suite_key=?", (key,)
            ).fetchone()
            if (old[0] if old else None) != previous:
                fail(
                    "The active evaluation changed. Refresh before preparing.", "quality_stale", 409
                )
            version = conn.execute(
                "SELECT coalesce(max(version),0)+1 FROM quality_versions WHERE suite_key=?", (key,)
            ).fetchone()[0]
            ident = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO quality_versions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    key,
                    version,
                    previous,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    digest(payload),
                    authority.actor.principal_id if authority else "local-unverified",
                    now_iso(),
                    authority.policy_version if authority else None,
                ),
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {
                    "operation": "quality_prepared",
                    "suite_key": key,
                    "payload_sha256": digest(payload),
                },
                conn=conn,
            )
            return self.row(
                conn.execute("SELECT * FROM quality_versions WHERE id=?", (ident,)).fetchone()
            )

    def activate(self, ident, sha256, *, confirm=False):
        if confirm is not True:
            fail("Explicitly confirm the exact suite version.", "quality_confirmation_required")
        with self.store._immediate() as conn:
            authority = authorize(self.store, "checks.edit", conn=conn)
            item = self.row(
                conn.execute("SELECT * FROM quality_versions WHERE id=?", (ident,)).fetchone()
            )
            if item["sha256"] != sha256:
                fail("The suite content hash changed.", "quality_changed", 409)
            head = conn.execute(
                "SELECT version_id FROM quality_heads WHERE suite_key=?", (item["suite_key"],)
            ).fetchone()
            current = head[0] if head else None
            if current == ident:
                return item
            if current != item["previous_id"] or item["policy_version"] != (
                authority.policy_version if authority else None
            ):
                fail(
                    "Evaluation base or permission policy changed. Prepare a fresh version.",
                    "quality_stale",
                    409,
                )
            if not head and conn.execute("SELECT count(*) FROM quality_heads").fetchone()[0] >= 100:
                fail("At most one hundred suites may be active.")
            active_questions = sum(
                len(json.loads(row[0])["questions"])
                for row in conn.execute(
                    "SELECT v.payload_json FROM quality_versions v JOIN quality_heads h ON h.version_id=v.id WHERE h.suite_key<>?",
                    (item["suite_key"],),
                )
            )
            if active_questions + len(item["payload"]["questions"]) > 100:
                fail("At most one hundred versioned evaluation questions may be active.")
            conn.execute(
                "INSERT INTO quality_heads VALUES(?,?) ON CONFLICT(suite_key) DO UPDATE SET version_id=excluded.version_id",
                (item["suite_key"], ident),
            )
            conn.execute(
                "INSERT INTO quality_events(suite_key,version_id,operation,actor,created_at) VALUES(?,?,?,?,?)",
                (
                    item["suite_key"],
                    ident,
                    "activated",
                    authority.actor.principal_id if authority else "local-unverified",
                    now_iso(),
                ),
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "quality_activated", "sha256": sha256},
                conn=conn,
            )
            return item

    def deactivate(self, ident, sha256, *, confirm=False):
        if confirm is not True:
            fail(
                "Confirm removal from active evaluation; history will remain.",
                "quality_confirmation_required",
            )
        with self.store._immediate() as conn:
            authority = authorize(self.store, "checks.edit", conn=conn)
            item = self.row(
                conn.execute("SELECT * FROM quality_versions WHERE id=?", (ident,)).fetchone()
            )
            if item["sha256"] != sha256:
                fail("The suite hash changed.", "quality_changed", 409)
            row = conn.execute(
                "SELECT version_id FROM quality_heads WHERE suite_key=?", (item["suite_key"],)
            ).fetchone()
            if not row or row[0] != ident:
                fail("Only the current exact evaluation can be deactivated.", "quality_stale", 409)
            conn.execute("DELETE FROM quality_heads WHERE suite_key=?", (item["suite_key"],))
            conn.execute(
                "INSERT INTO quality_events(suite_key,version_id,operation,actor,created_at) VALUES(?,?,?,?,?)",
                (
                    item["suite_key"],
                    ident,
                    "deactivated",
                    authority.actor.principal_id if authority else "local-unverified",
                    now_iso(),
                ),
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "quality_deactivated", "sha256": sha256},
                conn=conn,
            )
            return item

    def questions(self):
        return [
            {
                **q,
                "id": f"{suite['id']}:{q['id']}",
                "suite_id": suite["id"],
                "suite_key": suite["suite_key"],
                "suite_version": suite["version"],
            }
            for suite in self.active()
            for q in suite["payload"]["questions"]
        ]

    @staticmethod
    def answer_check(case, text, citations, unknown):
        # Criteria are deterministic lexical checks, not a semantic-support claim.
        normalized = re.sub(r"\[\s*E\d+(?:\s*[,;/]?\s*E\d+)*\s*\]", "", text).strip().casefold()
        expected = case["expected"].strip().casefold()
        mode = case.get("mode", "contains")
        match = (
            normalized == expected
            if mode == "exact"
            else all(term in normalized for term in expected.split())
            if mode == "all_terms"
            else expected in normalized
        )
        return bool(
            match
            and len(citations) >= case.get("minimum_citations", 1)
            and not unknown
            and not any(term.casefold() in text.casefold() for term in case.get("forbidden", []))
        )

    def model_checks(self, model):
        from autoontology.ontology.rules import rule_checks

        rules = rule_checks(model)
        records = model.to_dict()
        results = []
        for suite in self.active():
            for assertion in suite["payload"]["assertions"]:
                value = records[assertion["collection"]].get(assertion["subject"])
                exists = assertion["subject"] in records[assertion["collection"]]
                for key in assertion["field"].split("."):
                    exists = exists and isinstance(value, dict) and key in value
                    value = value.get(key) if isinstance(value, dict) else None
                results.append(
                    {
                        **assertion,
                        "suite_id": suite["id"],
                        "suite_key": suite["suite_key"],
                        "actual": value,
                        "ok": exists
                        and type(value) is type(assertion["expected"])
                        and value == assertion["expected"],
                    }
                )
        return {
            "policy": self.policy(),
            "ok": rules["ok"] and all(row["ok"] for row in results),
            "rules": rules,
            "assertions": results,
            "suite_versions": [{"id": s["id"], "sha256": s["sha256"]} for s in self.active()],
        }

    def runs(self, ident=None):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            rows = conn.execute("SELECT * FROM quality_runs ORDER BY rowid DESC").fetchall()
            result = []
            for row in rows:
                if ident is not None and row["id"] != ident:
                    continue
                payload = json.loads(row["result_json"])
                if digest(payload) != row["sha256"]:
                    fail("Evaluation run integrity failed.", "quality_corrupt", 409)
                result.append(
                    {
                        "id": row["id"],
                        "revision": row["revision"],
                        "created_at": row["created_at"],
                        "result": payload,
                        "sha256": row["sha256"],
                    }
                )
            return result

    def rules(self, revision=None):
        from autoontology.ontology.rules import rule_checks
        from autoontology.store.operations import Operations, OperationsError

        authorize(self.store, "candidate.read")
        ops = Operations(self.store)
        candidate = ops.state()["candidate_revision"]
        revision = revision or candidate
        try:
            model = (
                ops.compose(revision)[0]
                if revision and revision == candidate
                else ops.model(revision)
            )
        except OperationsError as error:
            fail(str(error), "quality_proposal_invalid", 409)
        return {
            "revision": revision,
            "view": "reviewed_proposal" if revision == candidate else "snapshot",
            "items": [
                {
                    "id": node.local,
                    "label": node.label,
                    "rule": node.rule,
                    "evidence": node.derived_from,
                }
                for node in model.individuals.values()
                if node.type == "BusinessRule"
            ]
            if model
            else [],
            "checks": rule_checks(model) if model else None,
        }
