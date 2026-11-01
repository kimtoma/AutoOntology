# SPDX-License-Identifier: MIT
"""Bounded comparisons of submitted frozen tables; never live SQL or approval.

SQLite authorizer and limits: https://www.sqlite.org/c3ref/set_authorizer.html
Results validate explicit reference hypotheses, not business truth or model quality.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from autoontology.config import REPO_ROOT
from autoontology.security.context import authorize
from autoontology.security.policy import AccessError
from autoontology.store.operations import digest

ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_BYTES = 2 * 1024 * 1024
SAFE_FUNCTIONS = frozenset(
    {
        "sum",
        "count",
        "avg",
        "min",
        "max",
        "total",
        "coalesce",
        "nullif",
        "abs",
        "round",
        "lower",
        "upper",
        "length",
        "substr",
        "like",
    }
)


def invalid():
    raise AccessError(
        "Use a bounded frozen comparison pack with complete, distinct case and arm IDs.",
        "comparison_input_invalid",
        400,
    )


def keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        invalid()


def text(value, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        invalid()


def distinct(items):
    if len(items) != len(set(items)):
        invalid()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,79}", value):
        invalid()


def clean(payload):
    try:
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        if len(encoded.encode()) > MAX_BYTES:
            invalid()
        payload = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        invalid()
    keys(payload, {"pack", "attempts"})
    pack, attempts = payload["pack"], payload["attempts"]
    keys(
        pack,
        {"schema", "id", "title", "data_class", "roles", "sources", "tables", "evidence", "cases"},
    )
    if pack["schema"] != "knowledge-comparison-pack/v1" or pack["data_class"] not in (
        "synthetic",
        "business_reported",
    ):
        invalid()
    identifier(pack["id"])
    text(pack["title"], 200)
    keys(pack["roles"], {"service_owner", "business_definition_approver", "operating_recipient"})
    for value in pack["roles"].values():
        if value is not None:
            text(value, 200)
    for name, limit in [("tables", 10), ("sources", 20), ("evidence", 30), ("cases", 20)]:
        if not isinstance(pack[name], list) or not 1 <= len(pack[name]) <= limit:
            invalid()
    table_ids, total_rows = [], 0
    for table in pack["tables"]:
        keys(table, {"name", "columns", "rows"})
        name = table["name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", name)
            or name.lower().startswith("sqlite_")
        ):
            invalid()
        table_ids.append(name.lower())
        if (
            not isinstance(table["columns"], list)
            or not 1 <= len(table["columns"]) <= 40
            or not isinstance(table["rows"], list)
        ):
            invalid()
        for col in table["columns"]:
            text(col, 100)
        distinct([c.casefold() for c in table["columns"]])
        total_rows += len(table["rows"])
        if total_rows > 5000:
            invalid()
        for row in table["rows"]:
            if not isinstance(row, list) or len(row) != len(table["columns"]):
                invalid()
            if any(
                v is not None and (not isinstance(v, str) or len(v) > 2000 or "\0" in v)
                for v in row
            ):
                invalid()
    distinct(table_ids)
    sources = []
    for source in pack["sources"]:
        keys(source, {"id", "title", "sha256"})
        identifier(source["id"])
        text(source["title"], 200)
        if not isinstance(source["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", source["sha256"]
        ):
            invalid()
        sources.append(source["id"])
    distinct(sources)
    evidence_ids = []
    for evidence in pack["evidence"]:
        keys(evidence, {"id", "title", "text", "source_id"})
        identifier(evidence["id"])
        text(evidence["title"], 200)
        text(evidence["text"], 6000)
        if evidence["source_id"] not in sources:
            invalid()
        evidence_ids.append(evidence["id"])
    distinct(evidence_ids)
    case_ids = []
    for case in pack["cases"]:
        keys(
            case,
            {
                "id",
                "question",
                "kind",
                "reference_sql",
                "expected_state",
                "required_evidence",
                "definition",
                "tolerance",
            },
        )
        identifier(case["id"])
        case_ids.append(case["id"])
        text(case["question"])
        text(case["definition"])
        if case["kind"] not in ("sql", "abstention"):
            invalid()
        if case["kind"] == "sql":
            text(case["reference_sql"], 10000)
            if case["expected_state"] is not None:
                invalid()
        else:
            if case["reference_sql"] is not None:
                invalid()
            identifier(case["expected_state"])
        required = case["required_evidence"]
        if not isinstance(required, list) or any(v not in evidence_ids for v in required):
            invalid()
        distinct(required)
        tolerance = case["tolerance"]
        if type(tolerance) not in (int, float) or not 0 <= tolerance <= 0.000001:
            invalid()
    distinct(case_ids)
    if not isinstance(attempts, list) or not 2 <= len(attempts) <= 3:
        invalid()
    arm_ids = []
    for arm in attempts:
        keys(arm, {"id", "label", "origin", "answers"})
        identifier(arm["id"])
        arm_ids.append(arm["id"])
        text(arm["label"], 200)
        if arm["origin"] not in ("constructed_example", "reported_unverified"):
            invalid()
        keys(arm["answers"], case_ids)
        for answer in arm["answers"].values():
            if not isinstance(answer, dict) or not isinstance(answer.get("state"), str):
                invalid()
            if answer["state"] == "sql":
                keys(answer, {"state", "sql", "citations"})
                text(answer["sql"], 10000)
            else:
                keys(answer, {"state", "reason", "citations"})
                identifier(answer["state"])
                text(answer["reason"])
            if not isinstance(answer["citations"], list) or len(answer["citations"]) > 30:
                invalid()
            for citation in answer["citations"]:
                identifier(citation)
            distinct(answer["citations"])
    distinct(arm_ids)
    return payload


def sample_comparison():
    try:
        return clean(
            json.loads((REPO_ROOT / "samples/evaluation/hanbit-billing-v1.json").read_text())
        )
    except OSError:
        raise AccessError(
            "The bundled comparison sample is unavailable.", "comparison_sample_missing", 503
        ) from None


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def sandbox(tables):
    conn = sqlite3.connect(":memory:", cached_statements=0)
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 100)
        conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 40)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 5)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        for table in tables:
            conn.execute(
                "CREATE TABLE "
                + quote(table["name"])
                + "("
                + ",".join(quote(c) + " TEXT" for c in table["columns"])
                + ")"
            )
            conn.executemany(
                "INSERT INTO "
                + quote(table["name"])
                + " VALUES("
                + ",".join("?" for _ in table["columns"])
                + ")",
                table["rows"],
            )
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
        allowed = {t["name"].lower() for t in tables}

        def authorizer(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if (
                action == sqlite3.SQLITE_READ
                and first
                and first.lower() in allowed
                and database == "main"
            ):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and second and second.lower() in SAFE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        return conn
    except BaseException:
        conn.close()
        raise


def execute(conn, query, deadline):
    steps = 0

    def budget():
        nonlocal steps
        steps += 1000
        return int(steps > 200000 or time.monotonic() > deadline)

    conn.set_progress_handler(budget, 1000)
    try:
        if time.monotonic() > deadline:
            raise sqlite3.OperationalError("budget")
        cursor = conn.execute(query)
        columns = [v[0] for v in cursor.description]
        rows = cursor.fetchmany(101)
        if len(rows) > 100 or len(json.dumps(rows, allow_nan=False).encode()) > 128 * 1024:
            raise ValueError
        if any(
            isinstance(v, (bytes, float)) and (isinstance(v, bytes) or not math.isfinite(v))
            for row in rows
            for v in row
        ):
            raise ValueError
        return {"columns": columns, "rows": [list(row) for row in rows]}
    except (sqlite3.Error, ValueError, OverflowError):
        raise AccessError(
            "SQL is outside the submitted read-only tables or evaluation budget.",
            "comparison_sql_rejected",
            400,
        ) from None
    finally:
        conn.set_progress_handler(None, 0)


def matches(expected, actual, tolerance):
    if expected["columns"] != actual["columns"] or len(expected["rows"]) != len(actual["rows"]):
        return False
    # Row order is part of this first contract; require ORDER BY for multirow results.
    for left, right in zip(expected["rows"], actual["rows"]):
        for a, b in zip(left, right):
            if type(a) in (int, float) and type(b) in (int, float):
                if type(a) is int and type(b) is int:
                    if a != b:
                        return False
                elif abs(Decimal(str(a)) - Decimal(str(b))) > Decimal(str(tolerance)):
                    return False
            elif type(a) is not type(b) or a != b:
                return False
    return True


def json_safe(value):
    if type(value) is int and abs(value) > 2**53 - 1:
        return {"type": "integer", "value": str(value)}
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


class Comparison:
    def __init__(self, store):
        self.store = store

    def gate(self, expected_policy=None):
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            return authorize(
                self.store, "validation.run", expected_policy=expected_policy, conn=conn
            )

    def sample(self):
        self.gate()
        return sample_comparison()

    def compare(self, payload):
        decision = self.gate()
        payload = clean(payload)
        pack, attempts = payload["pack"], payload["attempts"]
        deadline = time.monotonic() + 2
        cases = []
        with closing(sandbox(pack["tables"])) as conn:
            for case in pack["cases"]:
                reference = (
                    execute(conn, case["reference_sql"], deadline)
                    if case["kind"] == "sql"
                    else None
                )
                outcomes = []
                for arm in attempts:
                    answer = arm["answers"][case["id"]]
                    outcome = {
                        "id": arm["id"],
                        "label": arm["label"],
                        "origin": arm["origin"],
                        "answer": answer,
                    }
                    if reference is None:
                        outcome["verdict"] = (
                            "expected_abstention"
                            if answer["state"] == case["expected_state"]
                            else "unexpected_answer"
                        )
                    elif answer["state"] != "sql":
                        outcome["verdict"] = "unexpected_abstention"
                    else:
                        try:
                            outcome["result"] = execute(conn, answer["sql"], deadline)
                            outcome["verdict"] = (
                                "matches_reference"
                                if matches(reference, outcome["result"], case["tolerance"])
                                else "different"
                            )
                        except AccessError as error:
                            outcome.update(verdict="execution_error", error=error.code)
                    if outcome["verdict"] in {
                        "matches_reference",
                        "expected_abstention",
                    } and (
                        not set(case["required_evidence"]).issubset(answer["citations"])
                        or not set(answer["citations"]).issubset(
                            {e["id"] for e in pack["evidence"]}
                        )
                    ):
                        outcome["verdict"] = "evidence_missing"
                    outcomes.append(outcome)
                cases.append({**case, "reference": reference, "arms": outcomes})
        self.gate(decision.policy_version if decision else None)
        core = {
            "schema": "knowledge-comparison-report/v1",
            "engine": {
                "name": "frozen-sqlite-v1",
                "sqlite_version": sqlite3.sqlite_version,
                "validator_sha256": ENGINE_SHA256,
            },
            "pack_sha256": digest(pack),
            "attempts_sha256": digest(attempts),
            "pack_id": pack["id"],
            "title": pack["title"],
            "data_class": pack["data_class"],
            "roles": pack["roles"],
            "sources": pack["sources"],
            "evidence": pack["evidence"],
            "cases": cases,
            "human_approval": False,
            "baseline_measured": False,
            "model_calls": 0,
            "data_scope": "submitted_frozen_tables_only",
            "semantic_evidence_review": "not_performed",
            "reference_status": "unapproved_hypothesis",
            "comparison_status": "constructed_examples"
            if all(a["origin"] == "constructed_example" for a in attempts)
            else "reported_unverified",
            "live_postgres_queries": 0,
        }
        core = json_safe(core)
        return {
            **core,
            "sha256": digest(core),
            "actor": {
                "principal_id": decision.actor.principal_id,
                "workspace_id": decision.workspace_id,
                "policy_version": decision.policy_version,
            }
            if decision
            else {"mode": "local_legacy_no_individual_identity"},
        }
