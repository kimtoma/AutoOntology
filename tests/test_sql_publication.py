# SPDX-License-Identifier: MIT
"""SQL reads only the approved publication's physical and semantic projection."""

import pytest

from autoontology.playground.sql_guard import SqlError, validate_sql


@pytest.fixture
def catalog():
    return {
        "version": 1,
        "source": "finance",
        "dialect": "postgres",
        "database": "autoontology_src_finance",
        "tables": [
            {
                "schema": "public",
                "name": "customers",
                "columns": {"id": {"type": "text"}, "이름": {"type": "text"}},
            },
            {
                "schema": "public",
                "name": "contracts",
                "columns": {"customer_id": {"type": "text"}, "amount": {"type": "bigint"}},
            },
        ],
    }


def test_count_star_is_a_known_table_query(catalog):
    checked = validate_sql("SELECT count(*) AS total FROM customers", catalog)
    assert "COUNT(*)" in checked and '"public"."customers"' in checked


def test_table_only_approval_permits_count_but_never_exposes_unapproved_star_columns(catalog):
    catalog["tables"][0]["columns"] = {}
    assert validate_sql("SELECT count(*) FROM customers", catalog)
    with pytest.raises(SqlError):
        validate_sql("SELECT * FROM customers", catalog)


def test_columns_stars_ctes_joins_and_korean_names_are_qualified(catalog):
    sql = 'WITH c AS (SELECT id, "이름" FROM customers) SELECT c."이름", sum(k.amount) AS total FROM c JOIN contracts k ON c.id=k.customer_id GROUP BY c."이름"'
    result = validate_sql(sql, catalog)
    assert "SUM(" in result and '"이름"' in result
    assert '"id"' in validate_sql("SELECT * FROM customers", catalog)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "SELECT * FROM customers; SELECT 1",
        "WITH x AS (DELETE FROM customers RETURNING *) SELECT * FROM x",
        "SELECT * INTO copied FROM customers",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT pg_read_file('/etc/passwd') FROM customers",
        "SELECT public.lower(id) FROM customers",
        "SELECT id OPERATOR(public.||) id FROM customers",
        "SELECT id::regclass FROM customers",
        "SELECT set_config('statement_timeout','0',false) FROM customers",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM absent.customers",
        "SELECT * FROM unapproved_table",
        "SELECT password FROM customers",
        "SELECT * FROM otherdb.public.customers",
        "SELECT * FROM generate_series(1,100)",
        "SELECT 1",
        "SELECT id::public.custom_type FROM customers",
        "WITH RECURSIVE x AS (SELECT id FROM customers UNION ALL SELECT id FROM x) SELECT * FROM x",
    ],
)
def test_unsafe_or_unpublished_sql_is_never_executed(catalog, sql):
    with pytest.raises(SqlError):
        validate_sql(sql, catalog)


def test_comments_and_strings_do_not_grant_authority(catalog):
    assert validate_sql("SELECT 'DROP TABLE customers' AS note FROM customers -- readonly", catalog)
    with pytest.raises(SqlError):
        validate_sql("SELECT /*+ SET_VAR(max_execution_time=0) */ * FROM customers", catalog)


@pytest.mark.parametrize(
    "dialect,schema,table,column,sql",
    [
        ("mysql", "finance", "customers", "id", "SELECT count(*) FROM finance.customers"),
        ("snowflake", "PUBLIC", "CUSTOMERS", "ID", "SELECT COUNT(ID) FROM PUBLIC.CUSTOMERS"),
    ],
)
def test_supported_source_dialects_keep_the_same_scope_guard(
    catalog, dialect, schema, table, column, sql
):
    catalog.update(
        dialect=dialect,
        database="FINANCE" if dialect == "snowflake" else "finance",
        tables=[{"schema": schema, "name": table, "columns": {column: {"type": "text"}}}],
    )
    assert validate_sql(sql, catalog)
