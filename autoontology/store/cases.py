# SPDX-License-Identifier: MIT
"""Business changes, append-only review packets and exact human confirmation."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from autoontology.security.context import audit_effect, authorize, current_actor
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity
from autoontology.store.operations import Operations, OperationsError, digest
from autoontology.store.publications import Publications


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def deny(detail, code="case_conflict", status=409):
    raise AccessError(detail, code, status)


class Cases:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        self.ops = Operations(store)

    def stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def row(row):
        result = dict(row)
        result["changes"] = json.loads(result.pop("changes_json"))
        result["links"] = json.loads(result.pop("links_json"))
        result["outcome"] = json.loads(result.pop("outcome_json"))
        return result

    def _get(self, conn, ident):
        if not isinstance(ident, str) or not 1 <= len(ident) <= 200:
            deny("Use a valid case identifier.", "case_input_invalid", 400)
        row = conn.execute("SELECT * FROM ops_cases WHERE id=?", (ident,)).fetchone()
        if row is None:
            deny("No case with this identifier.", "case_missing", 404)
        return self.row(row)

    def _actor(self, conn):
        actor = current_actor(self.store, conn=conn)
        return (
            {"id": actor.principal_id, "kind": actor.kind}
            if actor
            else {"id": "local-unverified", "kind": "unverified"}
        )

    def _append(self, conn, case, operation, actor, note=""):
        previous = conn.execute(
            "SELECT sequence,sha256 FROM ops_case_events WHERE case_id=? ORDER BY sequence DESC LIMIT 1",
            (case["id"],),
        ).fetchone()
        record = {
            "case_id": case["id"],
            "sequence": previous["sequence"] + 1 if previous else 1,
            "operation": operation,
            "actor_json": encoded(actor),
            "data_json": encoded({"snapshot": case, "note": note}),
            "previous_sha256": previous["sha256"] if previous else "",
            "created_at": self.stamp(),
        }
        conn.execute(
            "INSERT INTO ops_case_events(case_id,sequence,operation,actor_json,data_json,previous_sha256,sha256,created_at) VALUES(:case_id,:sequence,:operation,:actor_json,:data_json,:previous_sha256,:sha256,:created_at)",
            {**record, "sha256": digest(record)},
        )

    def _change(self, conn, ident, version, operation, actor, updates, note=""):
        row = self._get(conn, ident)
        self._history(conn, ident)
        if row["version"] != version:
            deny("This case changed. Refresh before continuing.", "case_version_changed")
        updates = {**updates, "version": version + 1, "updated_at": self.stamp()}
        fields = ",".join(key + "=?" for key in updates)
        conn.execute(
            "UPDATE ops_cases SET " + fields + " WHERE id=? AND version=?",
            (*updates.values(), ident, version),
        )
        result = self._get(conn, ident)
        self._append(conn, result, operation, actor, note)
        return result

    def _context(self):
        state = self.ops.state()
        revisions = [state["published_revision"], state["candidate_revision"]]
        snapshots = [
            self.store.get_snapshot(revision) if revision else None for revision in revisions
        ]
        publication = Publications(self.store).check_model(revisions[0]) if revisions[0] else None
        return {
            "base_revision": revisions[0],
            "candidate_revision": revisions[1],
            "base_publication_id": publication["id"] if publication else None,
            "models_sha256": digest(
                [snapshot.model if snapshot else None for snapshot in snapshots]
            ),
            "sources_sha256": digest(
                [snapshot.sources if snapshot else None for snapshot in snapshots]
            ),
            "reviews_sha256": digest({item["id"]: item["decision"] for item in self.ops.changes()}),
        }

    def _owner(self, conn, owner):
        if (
            owner is not None
            and not conn.execute(
                "SELECT 1 FROM identity_principals WHERE id=? AND kind='human' AND status='active'",
                (owner,),
            ).fetchone()
        ):
            deny("Choose an active human owner.", "case_owner_invalid", 400)

    def _clean(self, conn, title, reason, owner_id, priority, due_at, changes, links):
        if (
            not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 200
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 5000
        ):
            deny("Supply a title and a business reason.", "case_input_invalid", 400)
        if not isinstance(priority, str) or priority not in {"low", "normal", "high", "urgent"}:
            deny("Choose a supported priority.", "case_input_invalid", 400)
        if due_at is not None and not isinstance(due_at, str):
            deny("Use a timezone-qualified due date.", "case_input_invalid", 400)
        if due_at:
            try:
                due = datetime.fromisoformat(due_at)
                if due.tzinfo is None:
                    raise ValueError
                due_at = due.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                deny("Use a timezone-qualified due date.", "case_input_invalid", 400)
        if owner_id is not None and not isinstance(owner_id, str):
            deny("Choose an active human owner.", "case_owner_invalid", 400)
        self._owner(conn, owner_id)
        if (
            not isinstance(changes, list)
            or len(changes) > 1000
            or not isinstance(links, list)
            or len(links) > 100
        ):
            deny("The case selection is too large or invalid.", "case_input_invalid", 400)
        known = {item["id"]: item for item in self.ops.changes()}
        selected = []
        for change in changes:
            if (
                not isinstance(change, dict)
                or set(change) - {"id", "decision", "note", "edits"}
                or not isinstance(change.get("id"), str)
                or change.get("id") not in known
                or change["id"] in {p["id"] for p in selected}
            ):
                deny("Select distinct current knowledge changes.", "case_selection_changed", 400)
            note = change.get("note", "")
            if not isinstance(note, str) or len(note) > 5000:
                deny("Use a bounded review reason.", "case_input_invalid", 400)
            edits = self.ops.validate_review(
                known[change["id"]], change.get("decision"), note, change.get("edits")
            )
            selected.append(
                {"id": change["id"], "decision": change["decision"], "note": note, "edits": edits}
            )
        for link in links:
            if (
                not isinstance(link, dict)
                or set(link) != {"relation", "case_id"}
                or not isinstance(link["relation"], str)
                or link["relation"] not in {"depends_on", "duplicate_of", "supersedes"}
            ):
                deny("Choose a supported case relation.", "case_input_invalid", 400)
            self._get(conn, link["case_id"])
        return {
            "title": title.strip(),
            "reason": reason.strip(),
            "owner_id": owner_id,
            "priority": priority,
            "due_at": due_at or None,
            "changes_json": encoded(selected),
            "links_json": encoded(links),
        }

    def create(
        self,
        *,
        title,
        reason,
        owner_id=None,
        priority="normal",
        due_at=None,
        changes=None,
        links=None,
        request_key=None,
    ):
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            data = self._clean(
                conn,
                title,
                reason,
                owner_id,
                priority,
                due_at,
                [] if changes is None else changes,
                [] if links is None else links,
            )
            request_key = uuid.uuid4().hex if request_key is None else request_key
            if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
                deny("Use a bounded request key.", "case_input_invalid", 400)
            request_sha = digest(data)
            old = conn.execute(
                "SELECT id,request_sha FROM ops_cases WHERE request_key=?", (request_key,)
            ).fetchone()
            if old:
                if old["request_sha"] != request_sha:
                    deny("This request key belongs to different content.", "case_request_conflict")
                return self._get(conn, old["id"])
            context = self._context()
            ident, stamp = uuid.uuid4().hex, self.stamp()
            record = {
                **data,
                **{
                    key: context[key]
                    for key in ("base_revision", "candidate_revision", "base_publication_id")
                },
                "id": ident,
                "status": "open",
                "version": 1,
                "request_key": request_key,
                "request_sha": request_sha,
                "created_at": stamp,
                "updated_at": stamp,
            }
            conn.execute(
                "INSERT INTO ops_cases("
                + ",".join(record)
                + ") VALUES("
                + ",".join("?" for _ in record)
                + ")",
                tuple(record.values()),
            )
            case = self._get(conn, ident)
            self._append(conn, case, "created", self._actor(conn))
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "case_created", "request_sha256": request_sha},
                conn=conn,
            )
            return case

    def update(self, ident, *, version, **changes):
        allowed = {
            "title",
            "reason",
            "owner_id",
            "priority",
            "due_at",
            "changes",
            "links",
            "status",
        }
        if set(changes) - allowed:
            deny("Unsupported case fields.", "case_input_invalid", 400)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            case = self._get(conn, ident)
            if case["status"] in {"reviewed", "resolved"}:
                deny("Reopen a resolved case before changing it.", "case_transition_invalid")
            status = changes.pop("status", case["status"])
            if (
                status not in {"open", "preparing", "held", "cancelled"}
                or case["status"] == "resolved"
            ):
                if status != case["status"]:
                    deny("Use the explicit resolution/reopen workflow.", "case_transition_invalid")
            data = self._clean(
                conn,
                **{
                    key: changes.get(key, case[key])
                    for key in (
                        "title",
                        "reason",
                        "owner_id",
                        "priority",
                        "due_at",
                        "changes",
                        "links",
                    )
                },
            )

            def visit(current, seen):
                if current == ident:
                    deny("Case dependencies cannot form a cycle.", "case_dependency_cycle")
                if current in seen:
                    return
                seen.add(current)
                for link in self._get(conn, current)["links"]:
                    if link["relation"] == "depends_on":
                        visit(link["case_id"], seen)

            for link in json.loads(data["links_json"]):
                if link["case_id"] == ident:
                    deny("A case cannot relate to itself.", "case_dependency_cycle")
                if link["relation"] == "depends_on":
                    visit(link["case_id"], set())
            result = self._change(
                conn, ident, version, "updated", self._actor(conn), {**data, "status": status}
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "case_updated", "version": result["version"]},
                conn=conn,
            )
            return result

    def _plan(self, case):
        items = {item["id"]: item for item in self.ops.changes()}
        plan = {item["id"]: dict(item) for item in case["changes"]}
        if not plan or set(plan) - set(items):
            deny("Choose changes from the current candidate.", "case_selection_changed")
        base = self.ops.model(case["base_revision"])
        known = set(base.classes) | set(base.individuals) if base else set()
        added = {
            item["key"]: item
            for item in items.values()
            if item["collection"] in {"classes", "individuals"}
            and item["key"] not in known
            and item["after"]
        }
        dependencies = []
        pending = list(plan)
        while pending:
            ident = pending.pop()
            if plan[ident]["decision"] != "accept":
                continue
            record = items[ident]["after"] or {}
            refs = [
                record.get(key)
                for key in (
                    "domain",
                    "range",
                    "subject",
                    "object",
                    "subject_node",
                    "object_node",
                    "term_local",
                )
            ]
            for ref in refs:
                dependency = added.get(ref)
                if dependency is None or dependency["id"] == ident:
                    continue
                required = dependency["id"]
                dependencies.append(
                    {"change_id": ident, "requires": required, "label": dependency["label"]}
                )
                if required not in plan:
                    if dependency["decision"] and dependency["decision"]["decision"] in {
                        "reject",
                        "hold",
                    }:
                        deny(
                            "A required concept was rejected or held. Review that dependency explicitly.",
                            "case_dependency_blocked",
                        )
                    plan[required] = {
                        "id": required,
                        "decision": "accept",
                        "note": "Required structural dependency",
                        "edits": {},
                    }
                    pending.append(required)
                elif plan[required]["decision"] != "accept":
                    deny(
                        "A selected decision excludes a required concept.",
                        "case_dependency_blocked",
                    )
        for proposal in plan.values():
            self.ops.validate_review(
                items[proposal["id"]], proposal["decision"], proposal["note"], proposal["edits"]
            )
        if any(item["decision"] == "accept" for item in plan.values()):
            try:
                self.ops.compose(case["candidate_revision"], reviews=plan)
            except OperationsError as error:
                deny(str(error), "case_dependency_blocked")
        return list(plan.values()), dependencies, [items[ident] for ident in plan]

    def prepare(self, ident, *, version, executor_id=None, ttl=1800):
        if not isinstance(ttl, (int, float)) or not 10 <= ttl <= 86400:
            deny("Use a review expiry between 10 seconds and one day.", "case_input_invalid", 400)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            case = self._get(conn, ident)
            if case["status"] in {"reviewed", "resolved", "cancelled"}:
                deny("Reopen the case before preparing another packet.", "case_transition_invalid")
            actor = current_actor(self.store, conn=conn)
            executor_id = executor_id or (
                actor.principal_id if actor and actor.kind == "human" else case["owner_id"]
            )
            human = conn.execute(
                "SELECT id FROM identity_principals WHERE id=? AND kind='human' AND status='active'",
                (executor_id,),
            ).fetchone()
            if not human or not Identity(self.store)._human_allowed(
                conn, executor_id, "review.decide", ("*",)
            ):
                deny("Choose a currently authorized human reviewer.", "case_reviewer_required", 403)
            for link in case["links"]:
                if link["relation"] == "depends_on" and self._get(conn, link["case_id"])[
                    "status"
                ] not in {"reviewed", "resolved"}:
                    deny("A prerequisite case still needs review.", "case_dependency_blocked")
            context = self._context()
            if any(
                case[key] != context[key]
                for key in ("base_revision", "candidate_revision", "base_publication_id")
            ):
                deny(
                    "The operational base or candidate changed. Rebase this case explicitly.",
                    "case_base_changed",
                )
            plan, dependencies, items = self._plan(case)
            case = self._change(
                conn,
                ident,
                version,
                "ready_for_review",
                self._actor(conn),
                {"status": "awaiting_review"},
            )
            payload = {
                "version": 1,
                "case_id": ident,
                "case_version": case["version"],
                "business": {
                    key: case[key]
                    for key in ("title", "reason", "owner_id", "priority", "due_at", "links")
                },
                "allowed_effect": "review.decisions",
                "executor_id": executor_id,
                "context": context,
                "policy_version": authority.policy_version if authority else None,
                "decisions": plan,
                "dependencies": dependencies,
                "items": items,
                "expires_at": self.clock() + ttl,
            }
            packet_id, sha = uuid.uuid4().hex, digest(payload)
            conn.execute(
                "INSERT INTO ops_case_packets VALUES(?,?,?,?,?,?)",
                (packet_id, ident, encoded(payload), sha, payload["expires_at"], self.stamp()),
            )
            return {"id": packet_id, "sha256": sha, "payload": payload}

    def confirm(self, packet_id, sha256):
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.decide", conn=conn)
            if authority is None:
                deny(
                    "Individual human authentication is required for case confirmation.",
                    "case_identity_required",
                    403,
                )
            row = conn.execute("SELECT * FROM ops_case_packets WHERE id=?", (packet_id,)).fetchone()
            if row is None:
                deny("No review packet with this identifier.", "case_packet_missing", 404)
            payload = json.loads(row["payload_json"])
            if digest(payload) != row["sha256"] or sha256 != row["sha256"]:
                deny("The exact review packet hash does not match.", "case_packet_changed")
            if payload["executor_id"] != authority.actor.principal_id:
                deny("This packet names another human reviewer.", "case_executor_mismatch", 403)
            old = conn.execute(
                "SELECT * FROM ops_case_receipts WHERE packet_id=?", (packet_id,)
            ).fetchone()
            if old:
                record = json.loads(old["record_json"])
                if digest(record) != old["sha256"]:
                    deny(
                        "The review receipt failed integrity verification.", "case_receipt_invalid"
                    )
                return record
            case = self._get(conn, row["case_id"])
            self._owner(conn, case["owner_id"])
            if payload["expires_at"] <= self.clock():
                deny("This review packet expired. Prepare a fresh packet.", "case_packet_expired")
            if (
                case["status"] != "awaiting_review"
                or case["version"] != payload["case_version"]
                or authority.policy_version != payload["policy_version"]
                or self._context() != payload["context"]
            ):
                deny(
                    "The case, candidate, decisions or policy changed after preparation.",
                    "case_packet_stale",
                )
            effects = []
            for decision in payload["decisions"]:
                result = self.ops.decide(
                    decision["id"],
                    decision["decision"],
                    candidate=case["candidate_revision"],
                    note=decision["note"],
                    edits=decision["edits"],
                    conn=conn,
                )
                effects.append(result)
            if payload["expires_at"] <= self.clock():
                deny("This review packet expired before completion.", "case_packet_expired")
            authorize(
                self.store, "review.decide", expected_policy=payload["policy_version"], conn=conn
            )
            result = self._change(
                conn,
                case["id"],
                case["version"],
                "human_confirmed",
                self._actor(conn),
                {"status": "reviewed"},
                note="Exact review packet " + sha256,
            )
            record = {
                "id": uuid.uuid4().hex,
                "packet_id": packet_id,
                "packet_sha256": sha256,
                "case_id": case["id"],
                "status": "reviewed",
                "candidate_revision": case["candidate_revision"],
                "base_revision": case["base_revision"],
                "confirmed_by": authority.actor.principal_id,
                "credential_id": authority.actor.credential_id,
                "policy_version": authority.policy_version,
                "case_version": result["version"],
                "decisions": effects,
                "confirmed_at": self.stamp(),
            }
            conn.execute(
                "INSERT INTO ops_case_receipts VALUES(?,?,?,?,?)",
                (packet_id, case["id"], encoded(record), digest(record), self.stamp()),
            )
            audit_effect(
                self.store,
                authority,
                case["id"],
                {
                    "operation": "case_review_confirmed",
                    "packet_sha256": sha256,
                    "receipt_id": record["id"],
                },
                conn=conn,
            )
            return record

    def _release_unpublished(self, conn, ident):
        row = conn.execute(
            "SELECT record_json FROM ops_case_receipts WHERE case_id=? ORDER BY rowid DESC LIMIT 1",
            (ident,),
        ).fetchone()
        if not row:
            return
        authorize(self.store, "review.decide", conn=conn)
        for effect in json.loads(row[0])["decisions"]:
            current = conn.execute(
                "SELECT * FROM ops_reviews WHERE id=?", (effect["id"],)
            ).fetchone()
            if (
                current
                and current["applied_revision"] is None
                and all(
                    current[key] == effect[key]
                    for key in ("decision", "note", "actor", "created_at", "edits_json")
                )
            ):
                self.ops.undo(effect["id"], conn=conn)

    def rebase(self, ident, *, version, reason, changes):
        if not isinstance(reason, str) or not reason.strip():
            deny("Recomparison needs a business reason.", "case_input_invalid", 400)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.suggest", conn=conn)
            case = self._get(conn, ident)
            if case["status"] in {"resolved", "cancelled"}:
                deny("Reopen the case before rebasing.", "case_transition_invalid")
            data = self._clean(
                conn,
                case["title"],
                reason,
                case["owner_id"],
                case["priority"],
                case["due_at"],
                changes,
                case["links"],
            )
            self._release_unpublished(conn, ident)
            context = self._context()
            result = self._change(
                conn,
                ident,
                version,
                "rebased",
                self._actor(conn),
                {
                    **data,
                    **{
                        key: context[key]
                        for key in ("base_revision", "candidate_revision", "base_publication_id")
                    },
                    "status": "preparing",
                },
                reason,
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "case_rebased", "version": result["version"]},
                conn=conn,
            )
            return result

    def reopen(self, ident, *, version, reason):
        if not isinstance(reason, str) or not reason.strip():
            deny("Reopening needs a reason.", "case_input_invalid", 400)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.decide", conn=conn)
            self._get(conn, ident)
            self._release_unpublished(conn, ident)
            context = self._context()
            result = self._change(
                conn,
                ident,
                version,
                "reopened",
                self._actor(conn),
                {
                    "status": "open",
                    "reason": reason.strip(),
                    "changes_json": "[]",
                    "outcome_json": "{}",
                    **{
                        key: context[key]
                        for key in ("base_revision", "candidate_revision", "base_publication_id")
                    },
                },
                reason,
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {"operation": "case_reopened", "version": result["version"]},
                conn=conn,
            )
            return result

    def resolve(self, ident, *, version, reason, publication=None):
        if not isinstance(reason, str) or not reason.strip():
            deny("Resolution needs a business reason.", "case_input_invalid", 400)
        with self.store._immediate() as conn:
            authority = authorize(self.store, "review.decide", conn=conn)
            case = self._get(conn, ident)
            if (
                case["status"] == "resolved"
                and case["outcome"].get("publication_revision") == publication
            ):
                return case
            if case["status"] != "reviewed":
                deny("Confirm the review before resolving this case.", "case_transition_invalid")
            saved = conn.execute(
                "SELECT record_json,sha256 FROM ops_case_receipts WHERE case_id=? ORDER BY rowid DESC LIMIT 1",
                (ident,),
            ).fetchone()
            receipt = json.loads(saved[0]) if saved else None
            if not receipt or digest(receipt) != saved[1]:
                deny("The confirmed review receipt is unavailable.", "case_receipt_invalid")
            packet = json.loads(
                conn.execute(
                    "SELECT payload_json FROM ops_case_packets WHERE id=?", (receipt["packet_id"],)
                ).fetchone()[0]
            )
            if any(decision["decision"] == "hold" for decision in packet["decisions"]):
                deny("Held decisions still need follow-up.", "case_resolution_incomplete")
            accepted = [
                item
                for item in packet["items"]
                if next(p for p in packet["decisions"] if p["id"] == item["id"])["decision"]
                == "accept"
            ]
            outcome = {
                "reason": reason.strip(),
                "publication_revision": publication,
                "receipt_id": receipt["id"],
                "resolved_at": self.stamp(),
            }
            if accepted:
                published = (
                    Publications(self.store).check_model(publication) if publication else None
                )
                if (
                    not published
                    or not published.get("committed_at")
                    or published["committed_at"] < receipt["confirmed_at"]
                ):
                    deny(
                        "Choose a committed publication created after this confirmation.",
                        "case_resolution_publication_required",
                    )
                model = self.ops.model(publication)
                draft = self.ops.model(receipt["candidate_revision"])
                for item in accepted:
                    effect = next(e for e in receipt["decisions"] if e["id"] == item["id"])
                    signature = digest(self.ops.semantic(item["after"], draft))
                    if not any(
                        d.get("kind") == "knowledge"
                        and d.get("collection") == item["collection"]
                        and d.get("subject") == item["key"]
                        and d.get("raw_after_signature") == signature
                        and d.get("by") == effect["actor"]
                        and d.get("at") == effect["created_at"]
                        and d.get("edits", {}) == json.loads(effect["edits_json"])
                        and d.get("note") == effect["note"]
                        for d in model.decisions
                    ):
                        deny(
                            "This publication does not contain the exact confirmed case decisions.",
                            "case_resolution_mismatch",
                        )
                outcome.update(
                    publication_id=published["id"],
                    usable_at=published["committed_at"],
                    manifest_sha256=digest(published["manifest"]),
                )
            else:
                if publication is not None:
                    deny(
                        "An all-rejected case has no publication effect.",
                        "case_resolution_mismatch",
                    )
                for effect in receipt["decisions"]:
                    row = conn.execute(
                        "SELECT * FROM ops_reviews WHERE id=?", (effect["id"],)
                    ).fetchone()
                    if not row or any(
                        row[key] != effect[key]
                        for key in ("decision", "note", "actor", "created_at", "edits_json")
                    ):
                        deny(
                            "The rejected decisions changed. Review again.",
                            "case_resolution_mismatch",
                        )
                outcome.update(usable_at=self.stamp(), resolution="no_change")
            result = self._change(
                conn,
                ident,
                version,
                "resolved",
                self._actor(conn),
                {"status": "resolved", "outcome_json": encoded(outcome)},
                reason,
            )
            audit_effect(
                self.store,
                authority,
                ident,
                {
                    "operation": "case_resolved",
                    "receipt_id": receipt["id"],
                    "publication": publication,
                },
                conn=conn,
            )
            return result

    def _history(self, conn, ident):
        records, previous = [], ""
        for index, row in enumerate(
            conn.execute(
                "SELECT * FROM ops_case_events WHERE case_id=? ORDER BY sequence", (ident,)
            ),
            1,
        ):
            raw = dict(row)
            sha = raw.pop("sha256")
            raw.pop("id")
            if raw["sequence"] != index or raw["previous_sha256"] != previous or digest(raw) != sha:
                deny("Case history failed integrity verification.", "case_history_invalid")
            records.append(
                {
                    **raw,
                    "actor": json.loads(raw["actor_json"]),
                    "data": json.loads(raw["data_json"]),
                    "sha256": sha,
                }
            )
            previous = sha
        if not records or records[-1]["data"]["snapshot"] != self._get(conn, ident):
            deny("Case state differs from its history.", "case_history_invalid")
        return records

    def history(self, ident):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            self._get(conn, ident)
            return self._history(conn, ident)

    def get(self, ident):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            result = self._get(conn, ident)
            self._history(conn, ident)
            packets = conn.execute(
                "SELECT * FROM ops_case_packets WHERE case_id=? ORDER BY rowid DESC", (ident,)
            ).fetchall()
            receipts = []
            for row in conn.execute(
                "SELECT record_json,sha256 FROM ops_case_receipts WHERE case_id=? ORDER BY rowid",
                (ident,),
            ):
                record = json.loads(row["record_json"])
                if digest(record) != row["sha256"]:
                    deny(
                        "The review receipt failed integrity verification.", "case_receipt_invalid"
                    )
                receipts.append(record)
        context = self._context()
        packet = packets[0] if packets else None
        latest = (
            {
                "id": packet["id"],
                "sha256": packet["sha256"],
                "payload": json.loads(packet["payload_json"]),
            }
            if packet
            else None
        )
        if latest and digest(latest["payload"]) != latest["sha256"]:
            deny("Review packet failed integrity verification.", "case_packet_changed")
        authority = authorize(self.store, "candidate.read")
        return {
            **result,
            "current_publication": context["base_revision"],
            "packet_expired": bool(latest and latest["payload"]["expires_at"] <= self.clock()),
            "packet_stale": bool(
                latest
                and (
                    latest["payload"]["context"] != context
                    or latest["payload"]["case_version"] != result["version"]
                    or authority is None
                    or latest["payload"]["policy_version"] != authority.policy_version
                )
            ),
            "stale": any(
                result[key] != context[key]
                for key in ("base_revision", "candidate_revision", "base_publication_id")
            ),
            "packet": latest,
            "receipts": receipts,
        }

    def listing(self, *, status=None):
        authorize(self.store, "candidate.read")
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_cases ORDER BY updated_at DESC,rowid DESC"
            ).fetchall()
        return [self.row(row) for row in rows if status is None or row["status"] == status]

    def summary(self):
        items = self.listing()
        active = [item for item in items if item["status"] not in {"resolved", "cancelled"}]
        return {
            "active": len(active),
            "awaiting_review": sum(item["status"] == "awaiting_review" for item in active),
            "overdue": sum(
                bool(item["due_at"])
                and datetime.fromisoformat(item["due_at"]).timestamp() < self.clock()
                for item in active
            ),
            "unassigned": sum(not item["owner_id"] for item in active),
            "held": sum(item["status"] == "held" for item in active),
        }

    def members(self):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            authorize(self.store, "candidate.read", conn=conn)
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "can_review": Identity(self.store)._human_allowed(
                        conn, row["id"], "review.decide", ("*",)
                    ),
                }
                for row in conn.execute(
                    "SELECT id,name FROM identity_principals WHERE kind='human' AND status='active' ORDER BY name"
                )
            ]
