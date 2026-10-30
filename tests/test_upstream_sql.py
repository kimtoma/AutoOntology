# SPDX-License-Identifier: MIT
"""Keep table-only SQL catalog validation working without changing the vendor."""

import os

import pandas as pd
import pytest

from autoontology.config import Settings
from autoontology.engine import jobs


@pytest.fixture
def catalog(isolated_env, monkeypatch):
    env, marker = dict(os.environ), jobs._APPLIED_ENV
    jobs.prepare_upstream_env(Settings.from_env())
    from auto_ontology.catalog import sql_parse
    from auto_ontology.catalog.constants import Labels
    from auto_ontology.catalog.model.node import CatalogNode

    monkeypatch.setattr(
        sql_parse, "extract_tables_and_columns", sql_parse.extract_tables_and_columns
    )

    class Schema:
        columns_df = pd.DataFrame([{"table_name": "customers", "column_name": "id"}])

        def table_exists(self, name):
            return name == "customers"

        def get_database_name(self):
            return "autoontology_src_test"

        def get_table_node(self, name):
            assert name == "customers"
            return CatalogNode(name=name, label=Labels.TABLE, props={}, existing_id="table-id")

        def is_column_in_table(self, _table, name):
            return name == "id"

        def get_column_node(self, name, _table):
            assert name == "id"
            return CatalogNode(name=name, label=Labels.COLUMN, props={}, existing_id="column-id")

    yield sql_parse, {"public": Schema()}
    os.environ.clear()
    os.environ.update(env)
    jobs._APPLIED_ENV = marker


def test_count_star_preserves_the_sql_and_known_table(catalog):
    parse, schemas = catalog
    query = "SELECT COUNT(*) AS total FROM public.customers"
    assert parse.parse_query_single(sql=query, schemas=schemas, dialects=["postgres"]) is None
    from autoontology.engine.upstream_sql import install

    install()
    install()  # startup hooks may run more than once
    result = parse.parse_query_single(sql=query, schemas=schemas, dialects=["postgres"])
    assert result.get_tables_ids() == ["table-id"]
    assert result.get_column_ids() == []
    assert result.sql_node.props["sql_full_query"] == query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM unknown_table",
        "SELECT COUNT(*) FROM absent.customers",
        "SELECT COUNT(*) FROM wrong_database.public.customers",
        "SELECT COUNT(missing_column) FROM public.customers",
        "SELECT COUNT(*) FROM customers; SELECT 1",
    ],
)
def test_table_only_fallback_does_not_accept_unknown_references(catalog, query):
    parse, schemas = catalog
    from autoontology.engine.upstream_sql import install

    install()
    result = parse.parse_query_single(sql=query, schemas=schemas, dialects=["postgres"])
    assert result is None


def test_normal_column_queries_retain_lineage(catalog):
    parse, schemas = catalog
    from autoontology.engine.upstream_sql import install

    install()
    result = parse.parse_query_single(
        sql="SELECT COUNT(id) FROM public.customers", schemas=schemas, dialects=["postgres"]
    )
    assert result.get_tables_ids() == ["table-id"]
    assert result.get_column_ids() == ["column-id"]


def test_bare_table_must_have_one_schema_owner(catalog):
    parse, schemas = catalog
    from autoontology.engine.upstream_sql import install

    install()
    schemas["another"] = schemas["public"]
    assert (
        parse.parse_query_single(
            sql="SELECT COUNT(*) FROM customers", schemas=schemas, dialects=["postgres"]
        )
        is None
    )
