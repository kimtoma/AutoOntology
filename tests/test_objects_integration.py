# SPDX-License-Identifier: MIT
"""The object reader against a real Postgres: a throwaway ``ontology_src_objtest``
database with the quickstart tables. Run with
``ONTOLOGY_INTEGRATION=1 uv run pytest -m integration tests/test_objects_integration.py``."""

from __future__ import annotations

import json

import psycopg
import pytest
from psycopg import sql

from ontology.config import Settings, validate_pg_database_name
from ontology.engine import pg
from ontology.objects import binding, reader, service
from ontology.objects.reader import ObjectError, TableQuery
from ontology.store.db import Store
from tests.test_objects import CON, CONTRACTS, INV, INVOICES, quickstart_model

pytestmark = pytest.mark.integration

DB = validate_pg_database_name("ontology_src_objtest")


@pytest.fixture
def source_db(tmp_path):
    settings = Settings.from_env()
    with pg.connect(settings, pg.PG_MAINTENANCE_DB) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(DB)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB)))
    try:
        with pg.connect(settings, DB) as conn:
            conn.execute(
                'CREATE TABLE public.contracts ("계약id" text, "고객id" text, "계약명" text, '
                '"계약금액" bigint, "상태" text)'
            )
            conn.execute(
                'CREATE TABLE public.invoices ("청구id" text PRIMARY KEY, "계약id" text, '
                '"청구금액" bigint, "수납상태" text)'
            )
            for r in CONTRACTS:
                conn.execute(
                    "INSERT INTO public.contracts VALUES (%s, %s, %s, %s, %s)", list(r.values())
                )
            for r in INVOICES:
                conn.execute(
                    "INSERT INTO public.invoices VALUES (%s, %s, %s, %s)", list(r.values())
                )
        store = Store(tmp_path / "home" / "ontology.db")
        view = store.project()
        view.add_source(
            name="objtest",
            kind="files",
            locator=json.dumps({"path": str(tmp_path)}),
            pg_database=DB,
        )
        model = quickstart_model()
        for cls in model.classes.values():
            cls.structured_source = "objtest"
        run = view.create_run(kind="build", source_id=None)
        view.save_snapshot(run.id, model)
        yield settings, view
    finally:
        with pg.connect(settings, pg.PG_MAINTENANCE_DB) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(DB))
            )


def test_reader_is_read_only_and_reads_real_rows(source_db):
    settings, view = source_db
    source = view.get_source("objtest")
    with reader.open_source(settings, source) as be:
        cols = be.columns("public", "contracts")
        assert [c["name"] for c in cols] == list(CONTRACTS[0])
        assert be.primary_key("public", "invoices") == ["청구id"]
        assert be.key_stats("public", "contracts", "고객id") == {
            "rows": 3,
            "distinct": 2,
            "nulls": 0,
        }
        q = TableQuery(
            "public",
            "contracts",
            ["계약id", "계약명"],
            order_by="계약id",
            search=(["계약명"], "온빛"),
        )
        assert [r["계약id"] for r in be.select(q)] == ["K2019-001", "K2019-002"]
        assert be.count(q) == 2
        # an injected identifier is just an unknown (quoted) column
        evil = TableQuery("public", "contracts", ['계약id"; DROP TABLE public.contracts; --'])
        with pytest.raises(psycopg.errors.UndefinedColumn):
            be.select(evil)
    # a write inside the reader's transaction fails
    with pytest.raises(ObjectError) as err, reader.open_source(settings, source) as be:
        be.conn.execute("DELETE FROM public.contracts")
    assert err.value.code == "source_error"
    with pg.connect(settings, DB) as conn:
        assert conn.execute("SELECT count(*) FROM public.contracts").fetchone()[0] == 3


def test_bind_and_read_through_the_service(source_db):
    settings, view = source_db
    binding.ensure_synced(view)
    con = service.bind(settings, view, CON)
    assert (con.key, con.key_checked) == ("계약id", True)
    inv = service.bind(settings, view, INV)
    assert inv.key_origin == "pk"
    one = service.get_object(settings, view, CON, "K2019-001")
    assert one["values"]["계약금액"] == 18000000
    in_link = next(link["name"] for link in one["links"] if link["direction"] == "in")
    found = service.follow(settings, view, CON, "K2019-001", in_link)
    assert [i["key"] for i in found["items"]] == ["INV-1", "INV-2"]
    # a dropped column breaks the type on the next read
    with pg.connect(settings, DB) as conn:
        conn.execute('ALTER TABLE public.contracts DROP COLUMN "계약명"')
    with pytest.raises(ObjectError) as err:
        service.search(settings, view, CON)
    assert err.value.code == "object_type_broken"
    assert view.object_types().get(CON).status == "broken"
