# SPDX-License-Identifier: MIT
import json

import pytest

from autoontology.config import Settings
from autoontology.store.db import Store
from autoontology.structured import pipeline
from autoontology.structured.loaders import LoadError
from autoontology.structured.pipeline import SourceError


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.csv").write_text("x\n1\n")
    return d


def test_add_files_source(store, data):
    src = pipeline.add_source(store, str(data), name="hanbit", recursive=True)
    assert src.kind == "files" and src.pg_database == "autoontology_src_hanbit"
    assert json.loads(src.locator) == {"path": str(data.resolve()), "recursive": True}
    assert pipeline.catalog_database_name(src) == "autoontology_src_hanbit"
    assert pipeline.display_locator(src).endswith("(recursive)")
    with pytest.raises(SourceError, match="already exists"):
        pipeline.add_source(store, str(data), name="hanbit")


@pytest.mark.parametrize("name", ["Hanbit", "han-bit", "", "한빛", "a b"])
def test_rejects_bad_names(store, data, name):
    with pytest.raises(SourceError, match="invalid source name"):
        pipeline.add_source(store, str(data), name=name)


def test_rejects_missing_or_empty(store, tmp_path):
    with pytest.raises(LoadError):
        pipeline.add_source(store, str(tmp_path / "nope"), name="x")
    (tmp_path / "empty").mkdir()
    with pytest.raises(SourceError, match="no supported files"):
        pipeline.add_source(store, str(tmp_path / "empty"), name="x")


def test_database_source_and_redaction(store):
    url = "postgresql://bob:s%40cret@db.example:6543/sales?schema=public,crm"
    src = pipeline.add_source(store, url, name="sales")
    assert src.kind == "database" and src.pg_database is None
    assert pipeline.catalog_database_name(src) == "sales"
    assert (
        pipeline.display_locator(src)
        == "postgresql://bob:***@db.example:6543/sales?schema=public,crm"
    )
    conn = pipeline.connection_for(src, Settings(home=store.path.parent))
    assert conn == {
        "type": "postgres",
        "host": "db.example",
        "user": "bob",
        "password": "s@cret",
        "database": "sales",
        "port": "6543",
        "schemas": ["public", "crm"],
    }
    with pytest.raises(SourceError, match="already uses database"):
        pipeline.add_source(store, "mysql://u:p@h/sales", name="sales2")


def test_parse_urls():
    assert pipeline.parse_database_url("mysql://u:p@h/db")["type"] == "mysql"
    sf = pipeline.parse_database_url("snowflake://u:p@acct?warehouse=W&database=D")
    assert sf == {
        "type": "snowflake",
        "account": "acct",
        "user": "u",
        "password": "p",
        "warehouse": "W",
        "database": "D",
    }
    with pytest.raises(SourceError):
        pipeline.parse_database_url("postgresql://h/db")
    with pytest.raises(SourceError, match="unsupported"):
        pipeline.parse_database_url("oracle://u:p@h/db")


def test_database_kind_needs_url(store):
    with pytest.raises(SourceError, match="needs a database URL"):
        pipeline.add_source(store, "./x", name="x", kind="database")


def test_files_connection_uses_settings(store, data):
    src = pipeline.add_source(store, str(data), name="demo")
    conn = pipeline.connection_for(src, Settings(home=store.path.parent, postgres_password=""))
    assert conn["password"] == pipeline.TRUST_AUTH_PASSWORD and conn["port"] == "5432"
    conn = pipeline.connection_for(src, Settings(home=store.path.parent, postgres_password="pw"))
    assert conn["password"] == "pw"
