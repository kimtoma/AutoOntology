"""Pins the upstream internals ``autoontology align`` writes Term synonyms through
(``autoontology/engine/upstream_terms.py``), against the vendored source. If upstream changes
any of these, this fails instead of the write silently doing nothing."""

import ast
import re

import pytest

from autoontology.config import REPO_ROOT

UP = REPO_ROOT / "vendor" / "auto-ontology" / "auto_ontology"


def _src(rel: str) -> str:
    return (UP / rel).read_text(encoding="utf-8")


def _functions(rel: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_src(rel))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_public_patch_still_has_no_synonyms_field():
    tree = ast.parse(_src("server/terms/router.py"))
    [cls] = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TermUpdate"]
    fields = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
    assert "description" in fields
    # if upstream ever adds it, switch upstream_terms.write_term_synonyms to the PATCH route
    assert "synonyms" not in fields


def test_term_table_has_a_synonyms_text_array():
    schema = _src("dal/schema.py")
    block = schema[schema.index('term = Table(\n    "term",'):]
    block = block[: block.index("\n)\n")]
    assert re.search(r'Column\("synonyms", ARRAY\(Text\)', block)
    assert "*_edited()" in block  # the `modified` column the write stamps


def test_session_helpers_and_embedding_refresh_exist():
    session = _functions("dal/session.py")
    assert {"store", "write_transaction"} <= set(session)
    refresh = _functions("server/terms/service.py")["refresh_term_embeddings"]
    assert [a.arg for a in refresh.args.args] == ["term_id"]
    assert [a.arg for a in refresh.args.kwonlyargs] == ["refresh_dependent_attrs"]
    # the embedded Term text includes its synonyms (why the refresh matters)
    assert "term_synonyms" in _src("semantic/embed.py")


@pytest.fixture
def upstream_env(isolated_env, monkeypatch):
    """Apply the upstream env first (the documented order: env before auto_ontology), and
    put os.environ and the applied-env marker back afterwards."""
    import os

    from autoontology.config import Settings
    from autoontology.engine import jobs

    saved_env, saved_marker = dict(os.environ), jobs._APPLIED_ENV
    settings = Settings.from_env()
    jobs.prepare_upstream_env(settings)
    yield settings
    os.environ.clear()
    os.environ.update(saved_env)
    jobs._APPLIED_ENV = saved_marker


def test_runtime_guard_imports_the_pinned_names(upstream_env):
    from autoontology.engine.upstream_terms import require_internals

    up = require_internals()
    assert {"synonyms", "name", "modified", "modified_by"} <= set(up["s"].term.c.keys())
    assert callable(up["refresh_term_embeddings"])


def test_store_conn_has_query_write():
    session = ast.parse(_src("dal/session.py"))
    [conn] = [n for n in session.body if isinstance(n, ast.ClassDef) and n.name == "StoreConn"]
    methods = {n.name for n in conn.body if isinstance(n, ast.FunctionDef)}
    assert {"query_write", "query_read"} <= methods


def test_term_edited_columns_include_modified_by():
    block = _src("dal/schema.py")
    edited = block[block.index("def _edited()"):]
    edited = edited[: edited.index("\ndef ")]
    assert 'Column("modified", DateTime' in edited and 'Column("modified_by", Text' in edited


class _FakeStore:
    """Stands in for upstream ``store()``: the row a concurrent editor already changed."""

    def __init__(self, log, current):
        self.log, self.current = log, current

    def query_write(self, statement, parameters=None):
        from sqlalchemy.sql import Select, Update

        if isinstance(statement, Select):
            self.log.append(("select", statement._for_update_arg is not None))
            return [{"name": "Contracts", "synonyms": list(self.current)}]
        assert isinstance(statement, Update)
        params = statement.compile().params
        self.log.append(("update", params))
        return []


def test_add_synonyms_merges_inside_the_transaction(upstream_env, monkeypatch):
    from contextlib import contextmanager

    from autoontology.engine import upstream_terms as ut

    real = ut.require_internals()
    log: list = []
    fake = _FakeStore(log, ["계약", "약정"])  # 약정 was added after align planned

    @contextmanager
    def tx():
        log.append("begin")
        yield
        log.append("commit")

    monkeypatch.setattr(ut, "require_internals", lambda: {
        **real, "store": lambda: fake, "write_transaction": tx,
        "refresh_term_embeddings": lambda term_id, refresh_dependent_attrs: log.append(
            ("refresh", term_id, refresh_dependent_attrs))})
    out = ut.add_term_synonyms(upstream_env, "t1", ["계약", "계약서", "contracts"])
    assert out == {"synonyms": ["계약", "약정", "계약서"], "added": ["계약서"],
                   "embeddings_refreshed": True}
    assert log[0] == "begin" and log[1] == ("select", True)  # FOR UPDATE, in the tx
    kind, params = log[2]
    assert kind == "update" and params["synonyms"] == ["계약", "약정", "계약서"]
    assert params["modified_by"] == "autoontology"
    assert log[3] == "commit" and log[4] == ("refresh", "t1", False)
    # nothing new: no update, no refresh
    log.clear()
    fake.current = ["계약", "약정", "계약서"]
    assert ut.add_term_synonyms(upstream_env, "t1", ["계약서"])["added"] == []
    assert [e for e in log if isinstance(e, tuple)] == [("select", True)]


def test_refresh_failure_is_reported_not_raised(upstream_env, monkeypatch):
    from autoontology.engine import upstream_terms as ut

    real = ut.require_internals()

    def boom(term_id, refresh_dependent_attrs):
        raise RuntimeError("vdb down")

    monkeypatch.setattr(ut, "require_internals",
                        lambda: {**real, "refresh_term_embeddings": boom})
    assert ut.refresh_term(upstream_env, "t1") is False


def test_scoped_semantic_reset_and_incremental_compile_contracts():
    """`build` resets a database's semantic layer when its schema changed (review #4):
    upstream's scoped reset must still take a database name, and compile must still only
    visit tables without a Term (which is why the reset is needed)."""
    reset = _functions("dal/reset.py")["delete_semantic_layer"]
    assert [a.arg for a in reset.args.args] == ["database_name"]
    assert "_delete_scoped_semantic" in _src("dal/reset.py")
    assert "fetch_all_tables_without_term(database_name)" in _src("semantic/pipeline.py")
    # catalog diff drops tables/columns absent from the source database (review #5)
    diff = _src("catalog/diff.py")
    assert "tables_names_to_delete" in diff and "delete_columns_batch" in diff


def _catalog_sqlite():
    """The upstream catalog/link tables the reset statements touch, as plain SQLite."""
    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        for ddl in (
            "CREATE TABLE catalog_database (id TEXT PRIMARY KEY, name TEXT)",
            "CREATE TABLE catalog_schema (id TEXT PRIMARY KEY, database_id TEXT, name TEXT)",
            "CREATE TABLE catalog_table (id TEXT PRIMARY KEY, schema_id TEXT, name TEXT)",
            "CREATE TABLE catalog_column (id TEXT PRIMARY KEY, table_id TEXT, name TEXT, "
            "data_type TEXT)",
            "CREATE TABLE term (id TEXT PRIMARY KEY, name TEXT)",
            "CREATE TABLE table__term (table_id TEXT, term_id TEXT)",
            "CREATE TABLE column_attribute (id TEXT PRIMARY KEY, table_id TEXT)",
            "CREATE TABLE column__foreign_key (source_column_id TEXT, target_column_id TEXT)",
        ):
            c.execute(sa.text(ddl))
        c.execute(sa.text("INSERT INTO catalog_database VALUES ('dA', 'autoontology_src_a'), "
                          "('dB', 'autoontology_src_b')"))
        c.execute(sa.text("INSERT INTO catalog_schema VALUES ('sA', 'dA', 'public'), "
                          "('sB', 'dB', 'public')"))
        c.execute(sa.text("INSERT INTO catalog_table VALUES ('tA', 'sA', 'customers'), "
                          "('tB', 'sB', 'customers')"))
        c.execute(sa.text("INSERT INTO catalog_column VALUES ('c1', 'tA', 'id', 'bigint'), "
                          "('c2', 'tA', 'name', 'text')"))
        c.execute(sa.text("INSERT INTO column__foreign_key VALUES ('c2', 'c1')"))
        c.execute(sa.text("INSERT INTO term VALUES ('shared', 'Customers'), ('gone', 'Old')"))
        c.execute(sa.text("INSERT INTO table__term VALUES ('tA', 'shared'), ('tB', 'shared')"))
        c.execute(sa.text("INSERT INTO column_attribute VALUES ('x1', 'tGone'), ('x2', 'tB')"))
    return engine


def test_reset_statements_detach_shared_terms_and_drop_orphans(upstream_env):
    """Fix wave 2 #3: after upstream's scoped reset a Term shared with database B keeps
    database A's table link, so A's table would never recompile. The detach statement
    removes only A's links; the shared Term stays for B; orphan Terms go."""
    import sqlalchemy as sa
    from auto_ontology.dal import schema as s

    from autoontology.engine import upstream_semantic as us

    engine = _catalog_sqlite()
    with engine.begin() as c:
        rows = [dict(r._mapping) for r in c.execute(us.snapshot_statement(s, "autoontology_src_a"))]
        snap = us.snapshot_from_rows(rows)
        assert snap == {"public.customers": {"id": "tA", "columns": {"id": "bigint",
                                                                     "name": "text"},
                                             "terms": ["shared"], "fk_columns": ["name"]}}
        detached = c.execute(us.detach_statement(s, "autoontology_src_a")).fetchall()
        assert [r[0] for r in detached] == ["shared"]
        links = c.execute(sa.text("SELECT table_id, term_id FROM table__term")).fetchall()
        assert links == [("tB", "shared")]  # B keeps its link
        gone = c.execute(us.orphan_terms_statement(s, ["shared", "gone"])).fetchall()
        assert [r[0] for r in gone] == ["gone"]  # still linked by B: kept
        stale = c.execute(us.stale_attributes_statement(s, ["tGone"])).fetchall()
        assert [r[0] for r in stale] == ["x1"]
        assert c.execute(sa.text("SELECT id FROM column_attribute")).fetchall() == [("x2",)]


def test_reset_order_and_blank_name(upstream_env, monkeypatch):
    """`delete_semantic_layer` runs first (it finds the database's own Terms through the
    links), then the detach and orphan deletes in one write transaction."""
    import contextlib

    import auto_ontology.dal.reset as reset
    import auto_ontology.dal.session as session

    from autoontology.engine import upstream_semantic as us

    calls = []
    monkeypatch.setattr(reset, "delete_semantic_layer",
                        lambda db: calls.append(("reset", db)) or 2)

    class _Conn:
        def query_write(self, stmt):
            calls.append(("write", stmt.table.name))
            return [{"term_id": "shared"}] if stmt.table.name == "table__term" else []

    monkeypatch.setattr(session, "store", lambda: _Conn())
    monkeypatch.setattr(session, "write_transaction", contextlib.nullcontext)
    out = us.reset_database_semantics(upstream_env, "autoontology_src_a", stale_table_ids=["t"])
    assert calls == [("reset", "autoontology_src_a"), ("write", "table__term"),
                     ("write", "term"), ("write", "column_attribute")]
    assert out["detached_terms"] == ["shared"] and out["vectors_removed"] == 2
    with pytest.raises(ValueError):
        us.reset_database_semantics(upstream_env, " ")


def test_upstream_gives_fk_columns_no_attribute():
    """The compile gate only reports uncovered columns (never fails on them) because
    upstream deliberately excludes declared and inferred FK columns from ColumnAttribute
    candidates (fix wave 4 #1)."""
    det = _functions("semantic/deterministic.py")["column_attribute_specs"]
    assert ast.get_docstring(det).startswith("Non-FK columns")
    assert "suggested_fk_columns" in [a.arg for a in det.args.args]


def test_remove_synonyms_is_a_reviewed_repair_with_refresh(upstream_env, monkeypatch):
    """Review #4, case 6: a rejected, previously accepted alignment's aliases come off the
    Term (whole normalized aliases only, under the row lock) and its embeddings refresh."""
    from contextlib import contextmanager

    from autoontology.engine import upstream_terms as ut

    real = ut.require_internals()
    log: list = []
    fake = _FakeStore(log, ["계약서", "승인권자", "승인자", "승인 담당자", "승인자 목록"])

    @contextmanager
    def tx():
        log.append("begin")
        yield
        log.append("commit")

    monkeypatch.setattr(ut, "require_internals", lambda: {
        **real, "store": lambda: fake, "write_transaction": tx,
        "refresh_term_embeddings": lambda term_id, refresh_dependent_attrs: log.append(
            ("refresh", term_id, refresh_dependent_attrs))})
    out = ut.remove_term_synonyms(upstream_env, "t1", ["승인권자", "승인자", "승인담당자"])
    # "승인자 목록" contains 승인자 but is a different alias: kept (no substring removal)
    assert out == {"synonyms": ["계약서", "승인자 목록"],
                   "removed": ["승인권자", "승인자", "승인 담당자"], "embeddings_refreshed": True}
    assert log[:2] == ["begin", ("select", True)]
    assert log[2][0] == "update" and log[2][1]["synonyms"] == ["계약서", "승인자 목록"]
    assert log[3] == "commit" and log[4] == ("refresh", "t1", False)
    log.clear()
    fake.current = ["계약서"]
    assert ut.remove_term_synonyms(upstream_env, "t1", ["승인권자"])["removed"] == []
    assert [e for e in log if isinstance(e, tuple)] == [("select", True)]
