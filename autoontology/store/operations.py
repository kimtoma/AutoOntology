# SPDX-License-Identifier: MIT
"""A published head, content-bound review decisions and feedback for Ontology Ops.

Opt in on the first operations request, pinning the workspace's existing snapshot.
Build/apply snapshots remain candidates thereafter. Publication switches the head
only after artifacts and the revision-specific retrieval index are ready.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from typing import Any

from autoontology.ontology.model import MergedOntology
from autoontology.store.db import Snapshot, Store, now_iso


class OperationsError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _publication_state(store: Store) -> tuple[bool, dict | None]:
    """Read managed state without opting a legacy workspace in to Operations.

    A build in a new managed workspace is a candidate, including the first build.
    Only explicit publication may initialize its operational head.
    """
    with store._conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ops_state'").fetchone():
            return False, None
        row = conn.execute("SELECT * FROM ops_state WHERE id=1").fetchone()
    if row is None:
        return True, None
    return True, dict(row)


def published_revision(store: Store) -> int | None:
    """Published revision, or None for a legacy/empty workspace.

    Use read_snapshot for consumers: None must not make a managed workspace fall
    back to an arbitrary latest candidate.
    """
    _, state = _publication_state(store)
    return state["published_revision"] if state else None


def read_snapshot(
    store: Store,
    revision: int | None = None,
    *,
    preview: bool = False,
    with_model: bool = True,
) -> Snapshot | None:
    """Select operational knowledge consistently for UI, CLI and exporters.

    Explicit unpublished reads require preview=True and a concrete revision.
    An incomplete/missing managed state never falls back to the latest snapshot.
    """
    if preview and revision is None:
        raise OperationsError("a preview requires an explicit revision")
    if revision is not None:
        head = store.get_snapshot(revision, with_model=with_model)
        if head and not preview and not visible_revision(store, revision):
            raise OperationsError(
                f"unpublished revision {revision}; use --revision {revision} --preview to inspect it"
            )
        return head
    managed, state = _publication_state(store)
    if not managed:
        return store.latest_snapshot(with_model=with_model)
    if not state or state["published_revision"] is None:
        return None
    head = store.get_snapshot(state["published_revision"], with_model=with_model)
    if head is None:
        raise OperationsError("the published snapshot is missing; restore the workspace")
    return head


class Operations:
    def __init__(self, store: Store):
        self.store = store
        with store._conn() as conn:
            managed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='ops_state'").fetchone()
            if managed and not conn.execute("SELECT 1 FROM ops_state WHERE id=1").fetchone():
                raise OperationsError("publication state is missing; restore the workspace")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ops_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), published_revision INTEGER,
                    initial_revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ops_reviews (
                    id TEXT PRIMARY KEY, decision TEXT NOT NULL, note TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL, applied_revision INTEGER);
                CREATE TABLE IF NOT EXISTS ops_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL,
                    answer TEXT NOT NULL, note TEXT NOT NULL, actor TEXT NOT NULL,
                    created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS ops_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL,
                    expected TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ops_connection_holds (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, note TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ops_validations (
                    token TEXT PRIMARY KEY, signature TEXT NOT NULL,
                    result_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            if "edits_json" not in {r[1] for r in conn.execute("PRAGMA table_info(ops_reviews)")}:
                try:
                    conn.execute(
                        "ALTER TABLE ops_reviews ADD COLUMN edits_json TEXT NOT NULL DEFAULT '{}'"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise
            head = conn.execute(
                "SELECT max(s.revision) FROM ontology_snapshots s LEFT JOIN runs r ON r.id=s.run_id WHERE r.id IS NULL OR r.status='succeeded'"
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO ops_state VALUES(1,?,?,?)", (head, head or 0, now_iso())
            )

    def state(self) -> dict[str, Any]:
        _, row = _publication_state(self.store)
        if row is None:
            raise OperationsError("publication state is missing; restore the workspace")
        candidates = [
            s
            for s in self.store.list_snapshots(500)
            if s.revision > row["initial_revision"]
            and self._complete(s)
            and ((run := self.store.get_run(s.run_id)) is None or run.kind != "publish")
        ]
        return {**row, "candidate_revision": candidates[0].revision if candidates else None}

    def _complete(self, snapshot) -> bool:
        run = self.store.get_run(snapshot.run_id)
        return run is None or run.status == "succeeded"

    def model(self, revision: int | None) -> MergedOntology | None:
        snap = self.store.get_snapshot(revision) if revision else None
        return MergedOntology.from_dict(snap.model) if snap and snap.model else None

    @staticmethod
    def records(model: MergedOntology | None) -> dict[str, dict[str, Any]]:
        data = model.to_dict() if model else {}
        out = {k: data.get(k, {}) for k in ("classes", "individuals", "properties")}
        out["axioms"] = {
            digest([a["subject"], a["kind"], a["object"], a["source"]]): a
            for a in data.get("axioms", [])
        }
        out["merges"] = {m["concept_local"]: m for m in data.get("merges", [])}
        return out

    @staticmethod
    def semantic(record: dict | None, model: MergedOntology | None) -> Any:
        if record is None or model is None:
            return None
        value = copy.deepcopy(record)
        value["derived_from"] = [
            [
                ev.quote,
                ev.page,
                ev.verified,
                model.documents[ev.document].sha256 if ev.document in model.documents else "",
            ]
            for eid in value.pop("derived_from", [])
            if (ev := model.evidence.get(eid))
        ]
        value["derived_from"].sort(key=str)
        return value

    @staticmethod
    def describe(record, model):
        if not record or not model:
            return "—"

        def label(key):
            item = model.classes.get(key) or model.individuals.get(key)
            return item.label if item else key

        if "subject" in record and "object" in record:
            return f"{label(record['subject'])} → {label(record['object'])} · {record['kind']} · {'accepted' if record.get('accepted') else 'proposed'}"
        if "domain" in record and "range" in record:
            return f"{record.get('label', '')} · {label(record.get('subject_node') or record['domain'])} → {label(record.get('object_node') or record.get('value_concept') or record['range'])}"
        return (
            " / ".join(record.get("definitions", []))
            or record.get("comment")
            or record.get("label")
            or record.get("concept_name")
            or str(record.get("kind", ""))
        )

    def changes(self) -> list[dict[str, Any]]:
        state = self.state()
        revision = state["candidate_revision"]
        if not revision:
            return []
        before = self.model(state["published_revision"])
        after = self.model(revision)
        old, new = self.records(before), self.records(after)
        with self.store._conn() as conn:
            decisions = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ops_reviews")}
        items = []
        prior = {
            (d.get("collection"), d.get("subject")): d
            for d in (before.decisions if before else [])
            if d.get("kind") == "knowledge"
        }
        for collection in old:
            for key in sorted(set(old[collection]) | set(new[collection])):
                a, b = old[collection].get(key), new[collection].get(key)
                sa, sb = self.semantic(a, before), self.semantic(b, after)
                if sa == sb:
                    continue
                journal = prior.get((collection, key))
                if journal and journal.get("raw_after_signature") == digest(sb):
                    continue
                ident = digest([collection, key, sa, sb])
                label = (
                    (b or a).get("label")
                    or (b or a).get("concept_name")
                    or (b or a).get("kind")
                    or key
                )
                if collection == "axioms":
                    label = self.describe(b or a, after if b else before).split(" · ")[0]
                evidence = []
                for record, model, version in [(a, before, "published"), (b, after, "candidate")]:
                    if not record or not model:
                        continue
                    for eid in record.get("derived_from", []):
                        if ev := model.evidence.get(eid):
                            doc = model.documents.get(ev.document)
                            evidence.append(
                                {
                                    "quote": ev.quote,
                                    "page": ev.page,
                                    "verified": ev.verified,
                                    "document": doc.title if doc else "",
                                    "version": version,
                                }
                            )
                items.append(
                    {
                        "id": ident,
                        "collection": collection,
                        "key": key,
                        "label": label,
                        "kind": "added" if a is None else "removed" if b is None else "changed",
                        "before": a,
                        "after": b,
                        "before_text": self.describe(a, before),
                        "after_text": self.describe(b, after),
                        "evidence": evidence,
                        "decision": {
                            **decisions[ident],
                            "edits": json.loads(decisions[ident]["edits_json"]),
                        }
                        if ident in decisions
                        else None,
                        "revision": revision,
                    }
                )
        return items

    def decide(
        self,
        change_id: str,
        decision: str,
        *,
        candidate: int,
        note: str = "",
        actor: str = "local",
        edits: dict | None = None,
    ) -> dict:
        if self.state()["candidate_revision"] != candidate:
            raise OperationsError("candidate changed; refresh before deciding")
        if decision not in ("accept", "reject", "hold"):
            raise OperationsError("invalid decision")
        item = next((i for i in self.changes() if i["id"] == change_id), None)
        if item is None:
            raise OperationsError("change no longer exists")
        edits = edits or {}
        allowed = {
            "classes": {"label", "definitions", "alt_labels", "comment"},
            "individuals": {"label", "comment", "alt_labels"},
            "properties": {"label", "comment"},
        }.get(item["collection"], set())
        if set(edits) - allowed or (edits and (decision != "accept" or item["after"] is None)):
            raise OperationsError("unsupported knowledge edits")
        if edits and not note.strip():
            raise OperationsError("a manual edit needs a reason")
        for key, value in edits.items():
            if key in ("definitions", "alt_labels"):
                if (
                    not isinstance(value, list)
                    or len(value) > 100
                    or any(not isinstance(v, str) or len(v) > 2000 for v in value)
                ):
                    raise OperationsError("invalid edited text list")
            elif not isinstance(value, str) or not value.strip() or len(value) > 5000:
                raise OperationsError("invalid edited text")
        if decision == "reject" and not note.strip():
            raise OperationsError("a rejection needs a reason")
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO ops_reviews(id,decision,note,actor,created_at,applied_revision,edits_json) VALUES(?,?,?,?,?,NULL,?) ON CONFLICT(id) DO UPDATE SET decision=excluded.decision,note=excluded.note,actor=excluded.actor,created_at=excluded.created_at,applied_revision=NULL,edits_json=excluded.edits_json",
                (
                    change_id,
                    decision,
                    note.strip(),
                    actor,
                    now_iso(),
                    json.dumps(edits, ensure_ascii=False),
                ),
            )
            return dict(
                conn.execute("SELECT * FROM ops_reviews WHERE id=?", (change_id,)).fetchone()
            )

    def undo(self, change_id: str) -> None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT applied_revision FROM ops_reviews WHERE id=?", (change_id,)
            ).fetchone()
            if row and row[0] is not None:
                raise OperationsError("published decisions require a new change")
            conn.execute("DELETE FROM ops_reviews WHERE id=?", (change_id,))

    def compose(self, revision: int) -> tuple[MergedOntology, list[dict]]:
        state = self.state()
        if state["candidate_revision"] != revision:
            raise OperationsError("candidate changed; refresh")
        base = self.model(state["published_revision"])
        draft = self.model(revision)
        if draft is None:
            raise OperationsError("build an ontology first")
        if base is None:
            if state["published_revision"] is not None:
                raise OperationsError("the published snapshot is missing; restore the workspace")
            base = MergedOntology(draft.name, draft.base)
        selected = [
            i for i in self.changes() if i["decision"] and i["decision"]["decision"] == "accept"
        ]
        if not selected:
            raise OperationsError("no approved changes to publish")
        data = base.to_dict()
        records = self.records(base)
        # Candidate evidence/document IDs are revision-qualified before transplant:
        # a new quote must never silently replace an old node's evidence.
        source = draft.to_dict()
        evmap = {eid: f"ev-r{revision}-{eid}" for eid in source["evidence"]}
        docmap = {did: f"doc-r{revision}-{did}" for did in source["documents"]}
        for item in selected:
            collection, key = item["collection"], item["key"]
            if item["after"] is None:
                records[collection].pop(key, None)
                continue
            record = copy.deepcopy(item["after"])
            record.update(item["decision"].get("edits", {}))
            if "derived_from" in record:
                record["derived_from"] = [evmap.get(e, e) for e in record["derived_from"]]
            records[collection][key] = record
        for collection in ("classes", "individuals", "properties"):
            data[collection] = records[collection]
        data["axioms"] = list(records["axioms"].values())
        data["merges"] = list(records["merges"].values())
        nodes = set(data["classes"]) | set(data["individuals"])

        def known(value):
            return (
                not value
                or value in nodes
                or value in {"THING", "LITERAL", "BusinessRule", "Process", "Concept", "Metric"}
                or value.startswith(("owl:", "xsd:", "ao:", "rdf:", "rdfs:"))
            )

        for prop in data["properties"].values():
            for field in ("domain", "range", "subject_node", "object_node", "value_concept"):
                if not known(prop.get(field, "")):
                    raise OperationsError(
                        "an approved relation references an unapproved or removed concept: "
                        + prop.get("label", "")
                    )
        for axiom in data["axioms"]:
            if not known(axiom["subject"]) or not known(axiom["object"]):
                raise OperationsError(
                    "an unapproved or removed concept is still referenced by an axiom"
                )
        evidence = copy.deepcopy(data["evidence"])
        documents = copy.deepcopy(data["documents"])
        for key, record in source["documents"].items():
            documents[docmap[key]] = {**record, "local": docmap[key]}
        for key, record in source["evidence"].items():
            evidence[evmap[key]] = {
                **record,
                "local": evmap[key],
                "document": docmap.get(record["document"], record["document"]),
            }
        used = {
            e
            for collection in records.values()
            for item in collection.values()
            for e in item.get("derived_from", [])
        }
        missing = used - set(evidence)
        if missing:
            raise OperationsError("approved knowledge references missing evidence")
        data["evidence"] = {e: evidence[e] for e in sorted(used)}
        docs = {e["document"] for e in data["evidence"].values()}
        data["documents"] = {d: documents[d] for d in sorted(docs) if d in documents}
        data["structured_sources"] = sorted(
            set(base.structured_sources) | set(draft.structured_sources)
        )
        data["documents_sources"] = sorted(
            set(base.documents_sources) | set(draft.documents_sources)
        )
        data["expected_classes"] = None
        for key in (
            "unaligned",
            "low_confidence",
            "source_missing",
            "dry_run_same",
            "without_evidence",
            "collapsed_relations",
        ):
            data[key] = copy.deepcopy(getattr(base, key))
        data["decisions"] = [
            *base.decisions,
            *[
                {
                    "kind": "knowledge",
                    "collection": item["collection"],
                    "raw_after_signature": digest(self.semantic(item["after"], draft)),
                    "edits": item["decision"].get("edits", {}),
                    "subject": item["key"],
                    "decision": "accept",
                    "relation": "",
                    "source": (item["after"] or item["before"]).get("documents_source", ""),
                    "name": item["label"],
                    "by": item["decision"]["actor"],
                    "at": item["decision"]["created_at"],
                    "note": item["decision"]["note"],
                }
                for item in selected
            ],
        ]
        model = MergedOntology.from_dict(data)
        # Every newly introduced document class/rule needs a traceable source quote.
        for item in selected:
            record = item["after"]
            if (
                record
                and item["collection"] in ("classes", "individuals")
                and record.get("source") != "structured"
                and not record.get("derived_from")
            ):
                raise OperationsError(
                    "approved document knowledge has no original evidence: " + item["label"]
                )
        return model, selected

    def signature(self, revision: int, profile: dict) -> str:
        from autoontology.store.decisions import DecisionStore

        return digest(
            {
                "state": self.state(),
                "publication_policy": {"version": 2, "minimum_questions": 1},
                "revision": revision,
                "changes": self.changes(),
                "checks": self.checks(),
                "connection_decisions": [d.to_dict() for d in DecisionStore(self.store).list()],
                "profile": profile,
            }
        )

    def commit(self, revision: int, expected: int | None, items: list[dict]) -> None:
        with self.store._conn() as conn:
            changed = conn.execute(
                "UPDATE ops_state SET published_revision=?,updated_at=? WHERE id=1 AND published_revision IS ?",
                (revision, now_iso(), expected),
            ).rowcount
            if changed != 1:
                raise OperationsError("published version changed; revalidate")
            conn.executemany(
                "UPDATE ops_reviews SET applied_revision=? WHERE id=?",
                [(revision, i["id"]) for i in items],
            )

    def add_feedback(self, question: str, answer: str, note: str, *, actor="local") -> dict:
        with self.store._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO ops_feedback(question,answer,note,actor,created_at) VALUES(?,?,?,?,?)",
                (question, answer, note, actor, now_iso()),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM ops_feedback WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
            )

    def feedback(self) -> list[dict]:
        with self.store._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM ops_feedback WHERE resolved=0 ORDER BY id DESC"
                )
            ]

    def resolve_feedback(self, item_id: int, reopen: bool) -> None:
        with self.store._conn() as conn:
            conn.execute(
                "UPDATE ops_feedback SET resolved=? WHERE id=?", (0 if reopen else 1, item_id)
            )

    def add_check(self, question: str, expected: str) -> dict:
        question, expected = question.strip(), expected.strip()
        if not question or not expected or len(question) > 2000 or len(expected) > 2000:
            raise OperationsError(
                "representative questions and expected answers must be non-empty and at most 2000 characters"
            )
        with self.store._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO ops_checks(question,expected,created_at) VALUES(?,?,?)",
                (question, expected, now_iso()),
            )
            return dict(
                conn.execute("SELECT * FROM ops_checks WHERE id=?", (cursor.lastrowid,)).fetchone()
            )

    def checks(self) -> list[dict]:
        with self.store._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ops_checks ORDER BY id")]


def visible_revision(store: Store, revision: int) -> bool:
    """Exports of unreviewed/failed candidate snapshots are not operational downloads."""
    managed, state = _publication_state(store)
    if not managed:
        return True
    if not state:
        return False
    if revision == state["published_revision"]:
        return True
    snap = store.get_snapshot(revision, with_model=False)
    run = store.get_run(snap.run_id) if snap else None
    if revision <= state["initial_revision"]:
        return bool(snap and (run is None or run.status == "succeeded"))
    return bool(run and run.kind == "publish" and run.status == "succeeded")
