# SPDX-License-Identifier: MIT
"""The object layer (``ontology/objects``, schema v10): the v10 migration, object type
inference from a snapshot and its verification on the source, the read-only reader (SQL
composition, transaction setup, identifier checks), the edit overlay (conflicts,
operational properties, revert), the review inbox's ``objects`` category and the
``/api/objects`` routes.

Sources are faked with :class:`FakeBackend` (the interface of
:class:`ontology.objects.reader.PgBackend`); the real Postgres path is covered by
``test_objects_integration.py`` behind ``ONTOLOGY_INTEGRATION=1``."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ontology.config import Settings
from ontology.objects import binding, overlay, reader, service
from ontology.objects.reader import ObjectError, TableQuery
from ontology.ontology import model as merged
from ontology.store import db
from ontology.store.agent_tokens import AgentTokens
from ontology.store.db import Store
from ontology.store.decisions import Decision, DecisionStore
from ontology.structured.semantic import SemanticFK, StructuredSnapshot
from ontology.unstructured.align import TermInfo
from ontology.web import app as web
from ontology.web.runs import RunManager

API = "/api"

# --- the quickstart sample as a snapshot and as source tables --------------------------

CONTRACTS = [
    {
        "계약id": "K2019-001",
        "고객id": "C035",
        "계약명": "온빛테크 ERP 유지보수",
        "계약금액": 18000000,
        "상태": "종료",
    },
    {
        "계약id": "K2019-002",
        "고객id": "C035",
        "계약명": "온빛테크 정기 납품",
        "계약금액": 96000000,
        "상태": "종료",
    },
    {
        "계약id": "K2024-003",
        "고객id": "C001",
        "계약명": "가람물산 물류 시스템",
        "계약금액": 52000000,
        "상태": "진행",
    },
]
CUSTOMERS = [
    {"고객id": "C001", "고객명": "주식회사 가람물산", "업종": "도소매", "지역": "부산"},
    {"고객id": "C035", "고객명": "온빛테크", "업종": "IT서비스", "지역": "서울"},
]
INVOICES = [
    {"청구id": "INV-1", "계약id": "K2019-001", "청구금액": 4500000, "수납상태": "완납"},
    {"청구id": "INV-2", "계약id": "K2019-001", "청구금액": 3000000, "수납상태": "완납"},
    {"청구id": "INV-3", "계약id": "K2024-003", "청구금액": 9000000, "수납상태": "미납"},
]
TYPES = {
    "계약id": "text",
    "고객id": "text",
    "청구id": "text",
    "계약금액": "bigint",
    "청구금액": "bigint",
}


def _columns(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": c, "type": TYPES.get(c, "text")} for c in rows[0]]


def _attrs(table: str, rows: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[dict]:
    return [
        {
            "id": f"{table}-{n}",
            "name": c,
            "datatype": TYPES.get(c, "text"),
            "column": c,
            "schema": "public",
            "table": table,
        }
        for n, c in enumerate(rows[0])
        if c not in skip
    ]


def quickstart_snapshot(*, without: tuple[str, ...] = ()) -> StructuredSnapshot:
    """contracts / customers / invoices as upstream describes them: one Term per table,
    an attribute per column (foreign-key columns are FK properties), two foreign keys."""
    terms = {
        "contracts": TermInfo(
            "t-con",
            "계약",
            "계약 정보",
            attributes=_attrs("contracts", CONTRACTS, skip=("고객id",)),
            tables=[{"id": "tb-con", "name": "contracts", "schema": "public"}],
        ),
        "customers": TermInfo(
            "t-cus",
            "고객",
            "고객",
            attributes=_attrs("customers", CUSTOMERS),
            tables=[{"id": "tb-cus", "name": "customers", "schema": "public"}],
        ),
        "invoices": TermInfo(
            "t-inv",
            "청구",
            "청구서",
            attributes=_attrs("invoices", INVOICES, skip=("계약id",)),
            tables=[{"id": "tb-inv", "name": "invoices", "schema": "public"}],
        ),
    }
    fks = [
        SemanticFK("t-con", "t-cus", "fk-a", "고객id", "public.contracts.고객id"),
        SemanticFK("t-inv", "t-con", "fk-b", "계약id", "public.invoices.계약id"),
    ]
    kept = [t for name, t in terms.items() if name not in without]
    ids = {t.id for t in kept}
    return StructuredSnapshot(
        "quickstart",
        "db1",
        "ontology_src_quickstart",
        kept,
        [f for f in fks if f.owner_term_id in ids and f.target_term_id in ids],
    )


def quickstart_model(**kw: Any) -> merged.MergedOntology:
    return merged.build("default", structured=[quickstart_snapshot(**kw)])


CON, CUS, INV = "term-t-con", "term-t-cus", "term-t-inv"


class FakeBackend:
    """:class:`ontology.objects.reader.PgBackend` over in-memory tables."""

    def __init__(self, tables: dict[str, dict[str, Any]]) -> None:
        self.tables = tables
        self.queries: list[TableQuery] = []

    def _t(self, schema: str, table: str) -> dict[str, Any] | None:
        return self.tables.get(f"{schema}.{table}" if schema else table)

    def columns(self, schema: str, table: str) -> list[dict[str, str]]:
        t = self._t(schema, table)
        return [dict(c) for c in t["columns"]] if t else []

    def primary_key(self, schema: str, table: str) -> list[str]:
        t = self._t(schema, table)
        return list(t.get("pk", [])) if t else []

    def key_stats(self, schema: str, table: str, column: str) -> dict[str, int]:
        rows = self._t(schema, table)["rows"]
        values = [r.get(column) for r in rows]
        present = [v for v in values if v is not None]
        return {
            "rows": len(values),
            "distinct": len(set(present)),
            "nulls": len(values) - len(present),
        }

    def _match(self, q: TableQuery) -> list[dict[str, Any]]:
        t = self._t(q.schema, q.table)
        names = {c["name"] for c in t["columns"]}
        assert q.identifiers() <= names, q.identifiers() - names  # only known identifiers
        rows = list(t["rows"])
        for column, value in q.equals:
            rows = [r for r in rows if str(r.get(column)) == value]
        if q.search:
            cols, text = q.search
            rows = [r for r in rows if any(text.lower() in str(r.get(c)).lower() for c in cols)]
        if q.any_of:
            column, values = q.any_of
            rows = [r for r in rows if str(r.get(column)) in values]
        if q.order_by:
            rows.sort(key=lambda r: str(r.get(q.order_by)))
        return rows

    def select(self, q: TableQuery) -> list[dict[str, Any]]:
        self.queries.append(q)
        rows = self._match(q)[q.offset : q.offset + min(q.limit, reader.MAX_PAGE)]
        return [{c: r.get(c) for c in q.columns} for r in rows]

    def count(self, q: TableQuery) -> int:
        return len(self._match(q))


def quickstart_tables() -> dict[str, dict[str, Any]]:
    return {
        "public.contracts": {"columns": _columns(CONTRACTS), "rows": [dict(r) for r in CONTRACTS]},
        "public.customers": {"columns": _columns(CUSTOMERS), "rows": [dict(r) for r in CUSTOMERS]},
        "public.invoices": {"columns": _columns(INVOICES), "rows": [dict(r) for r in INVOICES]},
    }


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend(quickstart_tables())
    opened: list[str] = []

    @contextmanager
    def open_source(settings, source):
        opened.append(source.name)
        yield fake

    monkeypatch.setattr(reader, "open_source", open_source)
    fake.opened = opened  # type: ignore[attr-defined]
    return fake


def seed(store: Store, model: merged.MergedOntology | None = None) -> int:
    """The quickstart files source and a snapshot of *model* (default: the sample)."""
    view = store.project()
    if view.get_source("quickstart") is None:
        view.add_source(
            name="quickstart",
            kind="files",
            locator=json.dumps({"path": "/nonexistent/quickstart"}),
            pg_database="ontology_src_quickstart",
        )
    run = view.create_run(kind="build", source_id=None)
    return view.save_snapshot(run.id, model or quickstart_model())


@pytest.fixture
def store(isolated_env) -> Store:
    return Store.for_home(Settings.from_env().home)


@pytest.fixture
def client(isolated_env):
    settings = Settings.from_env()
    with TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict))) as c:
        yield c, settings, Store.for_home(settings.home)


# --- migration -----------------------------------------------------------------------------


V10_TABLES = {
    "object_types",
    "logic_defs",
    "logic_def_history",
    "logic_candidates",
    "action_runs",
    "object_edits",
    "workflow_runs",
}


def _v9_store(tmp_path, monkeypatch) -> Store:
    with monkeypatch.context() as m:
        m.setattr(db, "MIGRATIONS", [x for x in db.MIGRATIONS if x[0] <= 9])
        store = Store(tmp_path / "v9.db")
        assert store.schema_version() == 9
        DecisionStore(store).put(Decision("concept", "c-1", "acknowledge", {"x": 1}, note="n"))
        DecisionStore(store).put(Decision("alias", "t-1|별칭", "reject"))
        with sqlite3.connect(store.path) as conn:  # a v9 token (no scopes column yet)
            conn.execute(
                "INSERT INTO agent_tokens (id, name, token_hash, created_at, project_id) "
                "VALUES ('tok_old', 'old', 'x', '2026-01-01T00:00:00.000+00:00', ?)",
                (store.default_project_id,),
            )
        with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO review_decisions (project_id, kind, subject_key, decision, "
                "decided_at) VALUES (?, 'object', 'x', 'accept', '')",
                (store.default_project_id,),
            )
    return store


def test_v9_store_migrates_to_v10_keeping_review_decisions(tmp_path, monkeypatch):
    store = _v9_store(tmp_path, monkeypatch)
    assert store.migrate() == 13 == db.MIGRATIONS[-1][0]
    with sqlite3.connect(store.path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert V10_TABLES <= tables
        for table in V10_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            assert "project_id" in cols, table
        rows = conn.execute(
            "SELECT kind, subject_key, decision, payload_json, note FROM review_decisions "
            "ORDER BY id"
        ).fetchall()
        assert rows == [
            ("concept", "c-1", "acknowledge", '{"x": 1}', "n"),
            ("alias", "t-1|별칭", "reject", "{}", ""),
        ]
        candidate_cols = {r[1] for r in conn.execute("PRAGMA table_info(logic_candidates)")}
        assert {"rationale", "spec_hash", "evidence_verified", "document"} <= candidate_cols
        scopes = conn.execute("SELECT scopes FROM agent_tokens").fetchall()
        assert scopes == [('["read"]',)]
    assert [t.scopes for t in AgentTokens(store).list_tokens()] == [("read",)]
    # the widened kinds are accepted now; an unknown one still is not
    decisions = DecisionStore(store)
    decisions.put(Decision("object", CON, "accept"))
    decisions.put(Decision("logic", "lc-1", "reject"))
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO review_decisions (project_id, kind, subject_key, decision, decided_at) "
            "VALUES (?, 'bogus', 'x', 'accept', '')",
            (store.default_project_id,),
        )
    # apply/build read only the ontology kinds
    assert {d.kind for d in decisions.list()} == {"concept", "alias"}
    assert {d.kind for d in decisions.list(actionable=True)} == {
        "concept",
        "alias",
        "object",
        "logic",
    }


def test_v10_is_idempotent_when_rerun(tmp_path, monkeypatch):
    store = _v9_store(tmp_path, monkeypatch)
    store.migrate()
    store.project().object_types().save(
        binding.proposal(binding.candidates(quickstart_model())[0], 1)
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM schema_version WHERE version >= 10")
    assert store.migrate() == 13
    assert len(store.project().object_types().list()) == 1
    assert len(DecisionStore(store).list()) == 2


def test_v10_check_constraints(store):
    with sqlite3.connect(store.path) as conn:
        for sql in (
            "INSERT INTO object_types (project_id, id, class_local, status, created_at, "
            "updated_at) VALUES (?, 'o1', 'c', 'bogus', '', '')",
            "INSERT INTO logic_defs (project_id, id, kind, name, created_at, updated_at) "
            "VALUES (?, 'l1', 'macro', 'n', '', '')",
            "INSERT INTO action_runs (project_id, id, action_name, status, created_at) "
            "VALUES (?, 'a1', 'x', 'done', '')",
            "INSERT INTO workflow_runs (project_id, id, workflow_name, trigger, fire_slot, "
            "status, started_at) VALUES (?, 'w1', 'x', 'cron', 's', 'running', '')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, (store.default_project_id,))
        pid = store.default_project_id
        conn.execute(
            "INSERT INTO workflow_runs (project_id, id, workflow_name, trigger, fire_slot, "
            "status, started_at) VALUES (?, 'w1', 'x', 'schedule', '2026-11-25T02:00', "
            "'running', '')",
            (pid,),
        )
        with pytest.raises(sqlite3.IntegrityError):  # one firing per slot
            conn.execute(
                "INSERT INTO workflow_runs (project_id, id, workflow_name, trigger, "
                "fire_slot, status, started_at) VALUES (?, 'w2', 'x', 'schedule', "
                "'2026-11-25T02:00', 'running', '')",
                (pid,),
            )
        conn.execute(
            "INSERT INTO action_runs (project_id, id, action_name, status, created_at, "
            "idempotency_key) VALUES (?, 'a1', 'x', 'previewed', '', 'k')",
            (pid,),
        )
        with pytest.raises(sqlite3.IntegrityError):  # idempotency keys are unique
            conn.execute(
                "INSERT INTO action_runs (project_id, id, action_name, status, created_at, "
                "idempotency_key) VALUES (?, 'a2', 'x', 'previewed', '', 'k')",
                (pid,),
            )


# --- inference -------------------------------------------------------------------------------


def test_candidates_of_the_quickstart_snapshot():
    found = {c.class_local: c for c in binding.candidates(quickstart_model())}
    assert set(found) == {CON, CUS, INV}
    con = found[CON]
    assert (con.source_name, con.schema, con.table) == ("quickstart", "public", "contracts")
    assert con.fk_columns == {"고객id"}
    proposals = {local: binding.proposal(c, 1) for local, c in found.items()}
    assert {k: p.key for k, p in proposals.items()} == {CON: "계약id", CUS: "고객id", INV: "청구id"}
    assert {k: p.title_column for k, p in proposals.items()} == {
        CON: "계약명",
        CUS: "고객명",
        INV: "",  # no name-like column: the key is the title (not 수납상태)
    }
    links = {(link["direction"], link["target"], link["column"]) for link in con.links}
    assert links == {("out", CUS, "고객id"), ("in", INV, "계약id")}


def test_classes_with_several_tables_or_documents_are_no_candidates():
    model = quickstart_model()
    model.classes[CON].tables.append("public.contracts_archive")
    assert CON not in {c.class_local for c in binding.candidates(model)}


def test_key_order_prefers_own_ids_then_foreign_keys_then_the_first_column():
    assert binding.key_order(["계약명", "고객id", "계약id"], {"고객id"}) == [
        "계약id",
        "고객id",
        "계약명",
    ]
    assert binding.key_order(["name", "amount"], set()) == ["name"]
    assert binding.infer_key(["a", "b_ID"], set()) == ("b_ID", "inferred")
    assert binding.infer_key(["a", "b"], set(), pk=["b"]) == ("b", "pk")
    assert binding.infer_key(["a", "b"], set(), pk=["a", "b"]) == ("a", "inferred")  # composite
    assert binding.infer_key(["x_id", "y_id"], set(), check=lambda c: c == "y_id") == (
        "y_id",
        "inferred",
    )
    assert binding.infer_key(["x_id"], set(), check=lambda c: False) == ("", "inferred")


def test_verify_checks_uniqueness_and_nulls_on_the_source():
    model = quickstart_model()
    con = binding.proposal(next(c for c in binding.candidates(model) if c.class_local == CON), 1)
    tables = quickstart_tables()
    checked = binding.verify(FakeBackend(tables), con)
    assert (checked.key, checked.key_origin, checked.key_checked) == ("계약id", "inferred", True)
    assert checked.column_names() == list(CONTRACTS[0])
    # a duplicated id: the next key-like column that is unique wins, none -> refused
    tables["public.contracts"]["rows"][1]["계약id"] = "K2019-001"
    with pytest.raises(ObjectError) as err:
        binding.verify(FakeBackend(tables), con)
    assert err.value.code == "key_not_unique" and "계약id" in err.value.extra["checked"]
    # an empty value disqualifies a column too
    tables = quickstart_tables()
    tables["public.contracts"]["rows"][2]["계약id"] = None
    with pytest.raises(ObjectError):
        binding.verify(FakeBackend(tables), con)
    # a hand-set key is verified as well
    with pytest.raises(ObjectError) as err:
        binding.verify(FakeBackend(quickstart_tables()), con, key="고객id")
    assert err.value.code == "key_not_unique"
    ok = binding.verify(FakeBackend(quickstart_tables()), con, key="계약명", title="상태")
    assert (ok.key, ok.key_origin, ok.title_column) == ("계약명", "manual", "상태")
    with pytest.raises(ObjectError) as err:
        binding.verify(FakeBackend(quickstart_tables()), con, key='계약id"; DROP TABLE x; --')
    assert err.value.code == "unknown_column"
    # a primary key is taken as it is
    tables = quickstart_tables()
    tables["public.contracts"]["pk"] = ["계약명"]
    assert binding.verify(FakeBackend(tables), con).key_origin == "pk"
    with pytest.raises(ObjectError) as err:
        binding.verify(FakeBackend({}), con)
    assert err.value.code == "table_missing"


# --- reader ------------------------------------------------------------------------------------


def test_select_quotes_identifiers_and_passes_values_as_parameters():
    evil = 'x"; DROP TABLE contracts; --'
    q = TableQuery(
        "public",
        "contracts",
        ["계약id", evil],
        order_by="계약id",
        equals=[(evil, "1' OR '1'='1")],
        search=(["계약id", "계약명"], "50%_off"),
        limit=10_000,
        offset=5,
    )
    query, params = reader.build_select(q)
    text = query.as_string(None)
    assert text == (
        'SELECT "계약id", "x""; DROP TABLE contracts; --" FROM "public"."contracts" '
        'WHERE CAST("x""; DROP TABLE contracts; --" AS text) = %s AND '
        '(CAST("계약id" AS text) ILIKE %s OR CAST("계약명" AS text) ILIKE %s) '
        'ORDER BY "계약id" NULLS LAST LIMIT %s OFFSET %s'
    )
    assert params == ["1' OR '1'='1", "%50\\%\\_off%", "%50\\%\\_off%", reader.MAX_PAGE, 5]
    count, cparams = reader.build_count(TableQuery("", "t", ["a"], any_of=("a", ["1", "2"])))
    assert count.as_string(None) == 'SELECT count(*) FROM "t" WHERE CAST("a" AS text) = ANY(%s)'
    assert cparams == [["1", "2"]]


class _Conn:
    """A psycopg connection stand-in that records statements and refuses writes after
    ``SET TRANSACTION READ ONLY`` the way Postgres does."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.read_only = False
        self.rolled_back = self.closed = False

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(None)
        self.statements.append(text)
        if text == "SET TRANSACTION READ ONLY":
            self.read_only = True
        if self.read_only and text.split()[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE"):
            import psycopg

            raise psycopg.errors.ReadOnlySqlTransaction("cannot execute in a read-only transaction")
        return self

    def fetchall(self):
        return []

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_open_source_is_a_read_only_transaction_with_a_timeout(monkeypatch, store):
    conn = _Conn()
    monkeypatch.setattr(reader, "connect_source", lambda settings, source: conn)
    source = type("S", (), {"name": "quickstart", "kind": "files"})()
    with reader.open_source(Settings.from_env(), source) as be:
        assert be.columns("public", "contracts") == []
    assert conn.rolled_back and conn.closed
    conn = _Conn()
    with pytest.raises(ObjectError) as err:  # a write inside the transaction fails
        with reader.open_source(Settings.from_env(), source) as be:
            be.conn.execute("INSERT INTO contracts VALUES (1)")
    assert err.value.code == "source_error"
    assert conn.statements[:2] == [
        "SET TRANSACTION READ ONLY",
        f"SET LOCAL statement_timeout = {reader.STATEMENT_TIMEOUT_MS}",
    ]
    assert conn.rolled_back and conn.closed


def test_source_session_reuses_a_connection_with_a_fresh_read_only_transaction(monkeypatch, store):
    """Inside :func:`reader.source_session` a source's connection is kept between reads,
    but every read is still its own READ ONLY transaction with the timeout, rolled back;
    a nested read gets its own connection; a failed read's connection is dropped."""
    made: list[_Conn] = []

    def connect(settings, source):
        made.append(_Conn())
        return made[-1]

    monkeypatch.setattr(reader, "connect_source", connect)
    settings = Settings.from_env()
    source = type("S", (), {"name": "quickstart", "kind": "files", "id": "s1"})()
    other = type("S", (), {"name": "other", "kind": "files", "id": "s2"})()
    head = [
        "SET TRANSACTION READ ONLY",
        f"SET LOCAL statement_timeout = {reader.STATEMENT_TIMEOUT_MS}",
    ]
    with reader.source_session():
        for _ in range(3):
            with reader.open_source(settings, source) as be:
                be.columns("public", "contracts")
                with reader.open_source(settings, source) as nested:
                    assert nested.conn is not be.conn
        assert len(made) == 4  # one kept + three nested (closed)
        kept = made[0]
        assert not kept.closed and kept.rolled_back
        assert kept.statements.count(head[0]) == 3 and kept.statements.count(head[1]) == 3
        assert all(c.closed for c in made[1:])
        with reader.open_source(settings, other):
            pass
        assert len(made) == 5  # another source: another connection
        with pytest.raises(ObjectError):
            with reader.open_source(settings, source) as be:
                be.conn.execute("INSERT INTO contracts VALUES (1)")
        assert kept.closed  # dropped after the failure
        with reader.open_source(settings, source):
            pass
        assert len(made) == 6
    assert all(c.closed for c in made)
    with reader.open_source(settings, source):  # outside a session: closed every time
        pass
    assert made[-1].closed and len(made) == 7


def test_open_source_refuses_non_postgres_and_reports_unreachable(monkeypatch, store):
    settings = Settings.from_env()
    mysql = type("S", (), {"name": "m", "kind": "database", "locator": "mysql://u:p@h/d"})()
    with pytest.raises(ObjectError) as err, reader.open_source(settings, mysql):
        pass
    assert err.value.code == "unsupported_source"
    # a files source whose database name is not an Ontology one never connects
    bad = type("S", (), {"name": "x", "kind": "files", "pg_database": "postgres"})()
    with pytest.raises(ObjectError) as err, reader.open_source(settings, bad):
        pass
    assert err.value.code == "source_unreachable"


def test_jsonable_source_values():
    import datetime as dt
    import decimal

    assert reader.jsonable(decimal.Decimal("18000000")) == 18000000
    assert reader.jsonable(decimal.Decimal("1.5")) == 1.5
    assert reader.jsonable(dt.date(2019, 3, 6)) == "2019-03-06"
    assert reader.jsonable(b"abc") == "<3 bytes>"


# --- sync, overlay, service ------------------------------------------------------------------


def test_sync_proposes_and_breaks_on_new_snapshots(store):
    view = store.project()
    rev1 = seed(store)
    binding.ensure_synced(view)
    types = {t.class_local: t for t in view.object_types().list()}
    assert {t.status for t in types.values()} == {"proposed"}
    assert view.object_types().synced_revision() == rev1
    view.object_types().set_status(CON, "active")
    view.object_types().set_status(INV, "retired")
    # contracts and invoices disappear from the next revision
    rev2 = seed(store, quickstart_model(without=("contracts", "invoices")))
    binding.ensure_synced(view)
    con = view.object_types().get(CON)
    assert con.status == "broken" and con.broken_reason.startswith("snapshot: class")
    assert view.object_types().get(INV).status == "retired"  # retired stays retired
    assert view.object_types().get(CUS).status == "proposed"
    assert view.object_types().synced_revision() == rev2
    # back again: restored
    seed(store)
    binding.ensure_synced(view)
    assert view.object_types().get(CON).status == "active"
    assert view.object_types().get(CON).broken_reason == ""


def test_a_proposal_whose_class_left_is_withdrawn(store):
    view = store.project()
    seed(store)
    binding.ensure_synced(view)
    seed(store, quickstart_model(without=("invoices",)))
    binding.ensure_synced(view)
    assert view.object_types().get(INV) is None


def _activate(store, *names):
    view = store.project()
    binding.ensure_synced(view)
    for name in names:
        service.bind(Settings.from_env(), view, name)
    return view


def test_search_get_and_follow_links(store, backend):
    seed(store)
    view = _activate(store, CON, CUS, INV)
    settings = Settings.from_env()
    found = service.search(settings, view, CON, q="온빛")
    assert found["total"] == 2 and [i["key"] for i in found["items"]] == ["K2019-001", "K2019-002"]
    assert found["items"][0]["title"] == "온빛테크 ERP 유지보수"
    filtered = service.search(settings, view, CON, filters=["상태=진행"])
    assert [i["key"] for i in filtered["items"]] == ["K2024-003"]
    with pytest.raises(ObjectError) as err:
        service.search(settings, view, CON, filters=['상태" = 1; DROP TABLE x; --=1'])
    assert err.value.code == "unknown_column"
    with pytest.raises(ObjectError):
        service.search(settings, view, CON, filters=["noequals"])
    one = service.get_object(settings, view, CON, "K2019-001")
    assert one["values"]["계약금액"] == 18000000 and one["edits"] == {}
    assert {link["target_status"] for link in one["links"]} == {"active"}
    with pytest.raises(ObjectError) as err:
        service.get_object(settings, view, CON, "nope")
    assert err.value.code == "unknown_object"
    customer = service.follow(
        settings,
        view,
        CON,
        "K2019-001",
        next(link["name"] for link in one["links"] if link["direction"] == "out"),
    )
    assert [i["key"] for i in customer["items"]] == ["C035"]
    in_link = next(link["name"] for link in one["links"] if link["direction"] == "in")
    invoices = service.follow(settings, view, CON, "K2019-001", in_link)
    assert [i["key"] for i in invoices["items"]] == ["INV-1", "INV-2"] and invoices["total"] == 2
    # two hops: customer C035 -> its contracts -> their invoices
    cus_in = next(
        link["name"] for link in view.object_types().get(CUS).links if link["direction"] == "in"
    )
    two = service.follow(settings, view, CUS, "C035", cus_in, then=in_link)
    assert two["path"] == [cus_in, in_link] and [i["key"] for i in two["items"]] == [
        "INV-1",
        "INV-2",
    ]
    with pytest.raises(ObjectError) as err:
        service.follow(settings, view, CON, "K2019-001", "no-such-link")
    assert err.value.code == "unknown_link"


def test_reading_a_proposed_type_is_refused(store, backend):
    seed(store)
    binding.ensure_synced(store.project())
    with pytest.raises(ObjectError) as err:
        service.search(Settings.from_env(), store.project(), CON)
    assert err.value.code == "object_type_not_active"
    with pytest.raises(ObjectError) as err:
        service.search(Settings.from_env(), store.project(), "term-nope")
    assert err.value.code == "unknown_object_type"


def test_a_missing_column_breaks_the_type_until_it_is_back(store, backend):
    seed(store)
    view = _activate(store, CON)
    settings = Settings.from_env()
    table = backend.tables["public.contracts"]
    saved = table["columns"]
    table["columns"] = [c for c in saved if c["name"] != "계약명"]  # the title column
    with pytest.raises(ObjectError) as err:
        service.search(settings, view, CON)
    assert err.value.code == "object_type_broken"
    con = view.object_types().get(CON)
    assert con.status == "broken" and "계약명" in con.broken_reason
    table["columns"] = saved
    assert service.search(settings, view, CON)["total"] == 3
    assert view.object_types().get(CON).status == "active"


def test_overlay_conflict_operational_and_revert(store, backend):
    seed(store)
    view = _activate(store, CON)
    settings = Settings.from_env()
    con = view.object_types().get(CON)
    row = service.get_object(settings, view, CON, "K2019-001")["source"]
    first = overlay.record_edit(
        view, con, "K2019-001", "상태", "보류", source_row=row, edited_by="a"
    )
    assert (first.old_value, first.source_value, first.operational) == ("종료", "종료", False)
    stage = overlay.record_edit(
        view, con, "K2019-001", "독촉단계", 1, source_row=row, action_run_id="ar_1", edited_by="a"
    )
    assert stage.operational and stage.source_value is None
    second = overlay.record_edit(
        view, con, "K2019-001", "상태", "진행", source_row=row, action_run_id="ar_1"
    )
    assert second.old_value == "보류"
    obj = service.get_object(settings, view, CON, "K2019-001")
    assert obj["values"]["상태"] == "진행" and obj["values"]["독촉단계"] == 1
    assert obj["source"]["상태"] == "종료"
    assert obj["edits"]["독촉단계"]["operational"] and obj["conflicts"] == []
    assert len(obj["history"]) == 3
    # the source changes after the edit: a conflict
    backend.tables["public.contracts"]["rows"][0]["상태"] = "해지"
    obj = service.get_object(settings, view, CON, "K2019-001")
    assert obj["conflicts"] == ["상태"] and obj["edits"]["상태"]["source_value"] == "해지"
    # revert the action run: the first edit shows again
    reverted = overlay.revert(view, action_run_id="ar_1")
    assert {e.property for e in reverted} == {"상태", "독촉단계"}
    obj = service.get_object(settings, view, CON, "K2019-001")
    assert obj["values"]["상태"] == "보류" and "독촉단계" not in obj["values"]
    assert overlay.revert(view, action_run_id="ar_1") == []
    assert [e.id for e in overlay.revert(view, edit_id=first.id)] == [first.id]
    obj = service.get_object(settings, view, CON, "K2019-001")
    assert obj["values"]["상태"] == "해지" and obj["edits"] == {}
    with pytest.raises(ValueError):
        overlay.revert(view)
    # search results carry the overlay too
    overlay.record_edit(view, con, "K2019-002", "계약명", "새 이름", source_row=CONTRACTS[1])
    items = service.search(settings, view, CON, q="K2019-002")["items"]
    assert items[0]["title"] == "새 이름"


def test_edits_are_per_project(store, backend):
    seed(store)
    view = _activate(store, CON)
    con = view.object_types().get(CON)
    overlay.record_edit(view, con, "K2019-001", "상태", "보류", source_row=CONTRACTS[0])
    other = store.create_project("gamma")
    assert store.project(other.id).object_edits().current(CON) == {}
    assert store.project(other.id).object_types().list() == []


# --- review inbox and API ----------------------------------------------------------------------


def test_review_lists_object_proposals_and_accept_binds_at_once(client, backend):
    c, settings, store = client
    seed(store)
    body = c.get(f"{API}/review", params={"category": "objects"}).json()
    assert body["counts"]["objects"] == 3 and body["total"] == 3
    items = {i["id"]: i for i in body["items"]}
    assert set(items) == {f"ob:{CON}", f"ob:{CUS}", f"ob:{INV}"}
    assert items[f"ob:{CON}"]["object_type"]["key"] == "계약id"
    resp = c.post(f"{API}/review/ob:{CON}/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["object_type"]["status"] == "active"
    assert resp.json()["decision"]["kind"] == "object"
    assert store.project().object_types().get(CON).key_checked
    # immediately readable, no apply run; nothing waits for an apply
    count = c.get(f"{API}/review/pending-count").json()
    assert count["counts"]["objects"] == 2 and count["unapplied"] == 0
    assert c.get(f"{API}/objects/{CON}").json()["total"] == 3
    # decided: listed under decided from the copy the decision stored
    decided = c.get(f"{API}/review", params={"status": "decided"}).json()["items"]
    assert [i["id"] for i in decided] == [f"ob:{CON}"]
    # reject retires; undo makes it a proposal again
    assert c.post(f"{API}/review/ob:{INV}/decision", json={"decision": "reject"}).status_code == 200
    assert store.project().object_types().get(INV).status == "retired"
    assert c.delete(f"{API}/review/ob:{INV}/decision").status_code == 200
    assert store.project().object_types().get(INV).status == "proposed"
    bad = c.post(f"{API}/review/ob:{CUS}/decision", json={"decision": "acknowledge"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_decision"
    rel = c.post(f"{API}/review/ob:{CUS}/decision", json={"decision": "accept", "relation": "same"})
    assert rel.status_code == 400
    assert (
        c.post(f"{API}/review/ob:term-x/decision", json={"decision": "accept"}).status_code == 404
    )
    entries = [
        e["action"]
        for e in __import__("ontology.store.accounts", fromlist=["Accounts"])
        .Accounts(store)
        .list_audit()
    ]
    assert "review.decide" in entries and "review.undo" in entries


def test_cli_review_accept_reject_undo_of_objects_matches_the_web_path(client, backend):
    """`onto review accept ob:...` binds the type like the web decision does (the same key
    and title), reject retires it and undo makes it a proposal again."""
    from typer.testing import CliRunner

    from ontology.cli import app

    c, settings, store = client
    seed(store)
    runner = CliRunner()

    def ok(args):
        out = runner.invoke(app, args)
        assert out.exit_code == 0, out.output
        return out.output

    ok(["review", "accept", f"ob:{CON}"])
    cli_type = store.project().object_types().get(CON)
    assert cli_type.status == "active" and cli_type.key_checked
    assert DecisionStore(store).get("object", CON).decision == "accept"
    pending = c.get(f"{API}/review/pending-count").json()
    assert pending["counts"]["objects"] == 2
    assert c.get(f"{API}/objects/{CON}").json()["total"] == 3
    ok(["review", "undo", f"ob:{CON}"])
    assert store.project().object_types().get(CON).status == "proposed"
    assert DecisionStore(store).get("object", CON) is None
    # the web path binds the same key and title
    resp = c.post(f"{API}/review/ob:{CON}/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    web_type = store.project().object_types().get(CON)
    assert (web_type.key, web_type.title_column) == (cli_type.key, cli_type.title_column)
    assert web_type.status == "active"
    # reject retires; undo makes it a proposal again
    ok(["review", "reject", f"ob:{INV}"])
    assert store.project().object_types().get(INV).status == "retired"
    ok(["review", "undo", f"ob:{INV}"])
    assert store.project().object_types().get(INV).status == "proposed"
    bad = runner.invoke(app, ["review", "acknowledge", f"ob:{CUS}"])
    assert bad.exit_code == 1 and "invalid_decision" in bad.output
    # a key that is not unique is refused and nothing is recorded
    backend.tables["public.customers"]["rows"].append(
        dict(backend.tables["public.customers"]["rows"][0])
    )
    bad = runner.invoke(app, ["review", "accept", f"ob:{CUS}"])
    assert bad.exit_code == 1 and "key_not_unique" in bad.output
    assert store.project().object_types().get(CUS).status == "proposed"
    assert DecisionStore(store).get("object", CUS) is None
    # audited like the web route: one entry per decision, actor cli (web: local)
    entries = [
        (e["action"], e["target"], e["actor"], e["detail"].get("decision"), e["project_id"])
        for e in reversed(
            __import__("ontology.store.accounts", fromlist=["Accounts"])
            .Accounts(store)
            .list_audit()
        )
        if e["action"].startswith("review.")
    ]
    pid = store.project().id
    assert entries == [
        ("review.decide", f"ob:{CON}", "cli", "accept", pid),
        ("review.undo", f"ob:{CON}", "cli", None, pid),
        ("review.decide", f"ob:{CON}", "local", "accept", pid),
        ("review.decide", f"ob:{INV}", "cli", "reject", pid),
        ("review.undo", f"ob:{INV}", "cli", None, pid),
    ]


def test_accepting_a_type_without_a_unique_key_is_refused(client, backend):
    c, settings, store = client
    seed(store)
    backend.tables["public.contracts"]["rows"][1]["계약id"] = "K2019-001"
    resp = c.post(f"{API}/review/ob:{CON}/decision", json={"decision": "accept"})
    assert resp.status_code == 409 and resp.json()["error"] == "key_not_unique"
    assert store.project().object_types().get(CON).status == "proposed"
    assert DecisionStore(store).get("object", CON) is None
    # bound by hand with another key instead
    resp = c.put(f"{API}/objects/types/{CON}", json={"key": "계약명", "title": "계약명"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"]["key_origin"] == "manual"
    assert resp.json()["type"]["status"] == "active"


def test_object_routes(client, backend):
    c, settings, store = client
    assert c.get(f"{API}/objects/types").json() == {"revision": None, "types": []}
    seed(store)
    body = c.get(f"{API}/objects/types").json()
    assert body["revision"] == 1 and len(body["types"]) == 3
    assert c.get(f"{API}/objects/types", params={"status": "active"}).json()["types"] == []
    assert c.get(f"{API}/objects/types", params={"status": "x"}).status_code == 400
    assert c.get(f"{API}/objects/types/{CON}").json()["type"]["table"] == "contracts"
    assert c.get(f"{API}/objects/types/term-x").status_code == 404
    assert c.get(f"{API}/objects/{CON}").json()["error"] == "object_type_not_active"
    for name in (CON, CUS, INV):
        assert c.put(f"{API}/objects/types/{name}", json={}).status_code == 200
    bad = c.put(f"{API}/objects/types/{CON}", json={"key": "nope"})
    assert bad.status_code == 400 and bad.json()["error"] == "unknown_column"
    assert c.put(f"{API}/objects/types/{CON}", json={"bogus": 1}).status_code == 400
    listing = c.get(f"{API}/objects/{CON}", params={"q": "K2024", "limit": 999})
    assert listing.status_code == 400  # over the page cap
    listing = c.get(f"{API}/objects/{CON}", params=[("filter", "상태=종료"), ("limit", 1)]).json()
    assert listing["total"] == 2 and len(listing["items"]) == 1
    one = c.get(f"{API}/objects/{CON}/K2019-001").json()
    assert one["key"] == "K2019-001" and one["type"]["name"] == CON
    in_link = next(link["name"] for link in one["links"] if link["direction"] == "in")
    linked = c.get(f"{API}/objects/{CON}/K2019-001/links/{in_link}").json()
    assert [i["key"] for i in linked["items"]] == ["INV-1", "INV-2"]
    canon = c.get(f"{API}/projects/default/objects/{CON}/K2019-001").json()
    assert canon["key"] == "K2019-001"
    assert c.get(f"{API}/objects/{CON}/nope").status_code == 404
    from ontology.store.accounts import Accounts

    bound = Accounts(store).list_audit(action="object.bind")
    assert len(bound) >= 3 and bound[0]["target"] in (CON, CUS, INV)


def test_object_types_are_named_by_id_class_label_or_table(client, backend):
    """One resolver for every route: id, class name, label (exact, any case), table."""
    c, settings, store = client
    seed(store)
    for name in (CON, CUS, INV):
        assert c.put(f"{API}/objects/types/{name}", json={}).status_code == 200
    ot_id = c.get(f"{API}/objects/types/{INV}").json()["type"]["id"]
    assert ot_id.startswith("ot_")
    for ref in (INV, ot_id, "청구", "invoices", "public.invoices", "INVOICES"):
        resp = c.get(f"{API}/objects/{ref}", params={"limit": 1})
        assert resp.status_code == 200, ref
        assert resp.json()["type"]["name"] == INV
        assert c.get(f"{API}/objects/{ref}/INV-1").json()["key"] == "INV-1"
        assert c.get(f"{API}/objects/types/{ref}").json()["type"]["name"] == INV
    assert c.get(f"{API}/objects/types/nope").json()["error"] == "unknown_object_type"
    # two types with the same label: 409 with the candidates
    types = store.project().object_types()
    other = types.get(CUS)
    types.save(replace(other, label="청구"))
    amb = c.get(f"{API}/objects/청구")
    assert amb.status_code == 409 and amb.json()["error"] == "ambiguous_object_type"
    assert {x["name"] for x in amb.json()["candidates"]} == {CUS, INV}
    assert c.get(f"{API}/objects/invoices", params={"limit": 1}).status_code == 200


def test_off_mode_reads_and_binds_without_a_role(client, backend):
    c, settings, store = client
    assert not settings.accounts_enabled
    seed(store)
    assert c.get(f"{API}/objects/types").status_code == 200
    assert c.put(f"{API}/objects/types/{CON}", json={}).status_code == 200
    assert store.project().object_types().get(CON).decided_by == "local"


# --- agent tools -------------------------------------------------------------------------------


def _agent_rpc(c, token, name, args):
    resp = c.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def test_agent_object_tools(client, backend):
    c, settings, store = client
    seed(store)
    c.put(f"{API}/objects/types/{CON}", json={})
    token = c.post(f"{API}/agent/tokens", json={"name": "agent"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    types = _agent_rpc(c, token, "list_object_types", {})
    assert types["isError"] is False
    assert [t["name"] for t in types["structuredContent"]["types"]] == [CON]
    every = _agent_rpc(c, token, "list_object_types", {"status": "all"})["structuredContent"]
    assert len(every["types"]) == 3
    found = _agent_rpc(c, token, "search_objects", {"type": CON, "query": "온빛", "limit": 1})
    data = found["structuredContent"]
    assert data["total"] == 2 and len(data["items"]) == 1 and data["revision"] == 1
    filtered = _agent_rpc(c, token, "search_objects", {"type": CON, "filters": {"상태": "진행"}})
    assert [i["key"] for i in filtered["structuredContent"]["items"]] == ["K2024-003"]
    one = _agent_rpc(c, token, "get_object", {"type": CON, "key": "K2019-001"})
    assert one["structuredContent"]["values"]["계약명"] == "온빛테크 ERP 유지보수"
    # failures are tool errors
    proposed = _agent_rpc(c, token, "search_objects", {"type": CUS})
    assert (
        proposed["isError"] is True and "object_type_not_active" in proposed["content"][0]["text"]
    )
    bad = _agent_rpc(c, token, "search_objects", {"type": CON, "filters": {'x"--': "1"}})
    assert bad["isError"] is True and "unknown_column" in bad["content"][0]["text"]
    # the REST surface answers the same
    rest = c.get(f"/agent/v1/objects/{CON}", params={"q": "온빛", "limit": 1}, headers=h).json()
    assert rest == data
    rest = c.get(f"/agent/v1/objects/{CON}", params=[("filter", "상태=진행")], headers=h).json()
    assert [i["key"] for i in rest["items"]] == ["K2024-003"]
    assert c.get(f"/agent/v1/objects/{CON}/K2019-001", headers=h).json() == one["structuredContent"]
    assert c.get("/agent/v1/objects/types", headers=h).json() == types["structuredContent"]
    assert c.get(f"/agent/v1/objects/{CON}/nope", headers=h).status_code == 404
    assert c.get(f"/agent/v1/objects/{CON}").status_code == 401
    paths = c.get("/agent/v1/openapi.json", headers=h).json()["paths"]
    assert "/agent/v1/objects/{type}/{key}" in paths
    # objects are live: a token pinned to an older revision may not read them
    seed(store)
    pinned = c.post(f"{API}/agent/tokens", json={"name": "p", "revision_pin": 1}).json()["token"]
    refused = _agent_rpc(c, pinned, "list_object_types", {})
    assert refused["isError"] is True and "revision_pinned" in refused["content"][0]["text"]
    latest = c.post(f"{API}/agent/tokens", json={"name": "q", "revision_pin": 2}).json()["token"]
    assert (
        _agent_rpc(c, latest, "get_object", {"type": CON, "key": "K2019-001"})["isError"] is False
    )


def test_agent_tools_write_nothing(client, backend):
    """The object tools are reads: no edit, decision or type change comes from them."""
    c, settings, store = client
    seed(store)
    c.put(f"{API}/objects/types/{CON}", json={})
    before = [t.to_dict() for t in store.project().object_types().list()]
    token = c.post(f"{API}/agent/tokens", json={"name": "agent"}).json()["token"]
    _agent_rpc(c, token, "search_objects", {"type": CON})
    _agent_rpc(c, token, "get_object", {"type": CON, "key": "K2019-001"})
    assert [t.to_dict() for t in store.project().object_types().list()] == before
    assert store.project().object_edits().current(CON) == {}


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_object_commands(isolated_env, backend):
    from typer.testing import CliRunner

    from ontology.cli import app

    runner = CliRunner()

    def ok(args):
        out = runner.invoke(app, args)
        assert out.exit_code == 0, out.output
        return out.output

    store = Store.for_home(Settings.from_env().home)
    assert "no object types yet" in ok(["object", "types"])
    seed(store)
    listed = ok(["object", "types"])
    assert CON in listed and "proposed" in listed and "계약id" in listed
    # by label or by name; --key/--title verified on the source
    bound = ok(["object", "bind", "계약", "--title", "상태"])
    assert "active" in bound and "상태" in bound
    data = json.loads(ok(["object", "bind", CON, "--json"]))
    assert data["type"]["key"] == "계약id" and data["type"]["title_column"] == "상태"
    bad = runner.invoke(app, ["object", "bind", CON, "--key", "nope", "--json"])
    assert bad.exit_code == 1 and '"error": "unknown_column"' in bad.output
    out = ok(["object", "list", CON, "-q", "K2019"])
    assert "K2019-001" in out and "2 of 2" in out
    rows = json.loads(ok(["object", "list", CON, "--filter", "상태=진행", "--json"]))
    assert [i["key"] for i in rows["items"]] == ["K2024-003"]
    con = store.project().object_types().get(CON)
    overlay.record_edit(store.project(), con, "K2019-001", "독촉단계", 2, source_row=CONTRACTS[0])
    detail = ok(["object", "get", CON, "K2019-001"])
    assert "독촉단계: 2  (operational)" in detail and "-> " in detail
    assert json.loads(ok(["object", "get", CON, "K2019-001", "--json"]))["key"] == "K2019-001"
    missing = runner.invoke(app, ["object", "get", CON, "nope"])
    assert missing.exit_code == 1
    assert ok(["object", "types", "--status", "active", "--json"]).count('"name"') >= 1
