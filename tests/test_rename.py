# SPDX-License-Identifier: MIT
"""The rename AutoOntology -> Ontology (0.3.0): the legacy ``AUTOONTOLOGY_*`` env names
still work (with one warning per process), a home's ``autoontology.db`` becomes
``ontology.db`` and store migration v13 rewrites the persisted source database names."""

import os
import sqlite3
import warnings

import pytest

from ontology import config
from ontology.store import db
from ontology.store.db import Store, home_db_path


@pytest.fixture
def fresh_warning(monkeypatch):
    monkeypatch.setattr(config, "_LEGACY_ENV_WARNED", False)


def _legacy_warnings(caught) -> list[str]:
    return [str(w.message) for w in caught if issubclass(w.category, config.LegacyEnvWarning)]


def test_legacy_shell_variable_fills_the_unset_new_name(isolated_env, monkeypatch, fresh_warning):
    monkeypatch.setenv("AUTOONTOLOGY_PORT", "7911")
    monkeypatch.setenv("AUTOONTOLOGY_TZ", "Asia/Seoul")
    monkeypatch.delenv("ONTOLOGY_PORT", raising=False)
    monkeypatch.delenv("ONTOLOGY_TZ", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = config.Settings.from_env()
        config.Settings.from_env()  # a second read: no second warning
    assert settings.port == 7911
    assert os.environ["ONTOLOGY_TZ"] == "Asia/Seoul"
    messages = _legacy_warnings(caught)
    assert len(messages) == 1
    assert "AUTOONTOLOGY_PORT" in messages[0] and "AUTOONTOLOGY_TZ" in messages[0]
    assert config.env_origin("ONTOLOGY_PORT") == "shell"


def test_new_name_wins_over_the_legacy_one(isolated_env, monkeypatch, fresh_warning):
    monkeypatch.setenv("ONTOLOGY_PORT", "7801")
    monkeypatch.setenv("AUTOONTOLOGY_PORT", "7911")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert config.Settings.from_env().port == 7801
    assert _legacy_warnings(caught) == []


def test_legacy_names_in_the_env_file(isolated_env, monkeypatch, fresh_warning):
    env_file = isolated_env / "legacy.env"
    env_file.write_text(
        "AUTOONTOLOGY_PORT=7912\nAUTOONTOLOGY_WORKFLOWS=off\nONTOLOGY_TZ=UTC\n"
        "AUTOONTOLOGY_TZ=Asia/Seoul\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ONTOLOGY_ENV_FILE", str(env_file))
    for name in ("ONTOLOGY_PORT", "ONTOLOGY_WORKFLOWS", "ONTOLOGY_TZ"):
        monkeypatch.delenv(name, raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = config.Settings.from_env()
    assert settings.port == 7912
    assert os.environ["ONTOLOGY_WORKFLOWS"] == "off"
    assert os.environ["ONTOLOGY_TZ"] == "UTC"  # the new name in the file wins
    assert config.env_origin("ONTOLOGY_PORT") == ".env"
    assert config.dotenv_layer()["ONTOLOGY_PORT"] == "7912"
    messages = _legacy_warnings(caught)
    assert len(messages) == 1
    assert "AUTOONTOLOGY_PORT" in messages[0] and "AUTOONTOLOGY_TZ" not in messages[0]
    config.unload_env()
    assert "ONTOLOGY_PORT" not in os.environ


def test_a_shell_value_beats_a_legacy_name_in_the_env_file(
    isolated_env, monkeypatch, fresh_warning
):
    env_file = isolated_env / "legacy.env"
    env_file.write_text("AUTOONTOLOGY_PORT=7912\n", encoding="utf-8")
    monkeypatch.setenv("ONTOLOGY_ENV_FILE", str(env_file))
    monkeypatch.setenv("ONTOLOGY_PORT", "7801")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert config.Settings.from_env().port == 7801
    assert _legacy_warnings(caught) == []


def test_home_db_is_renamed_once_with_its_side_files(tmp_path):
    legacy = tmp_path / "autoontology.db"
    store = Store(legacy)
    store.add_source(name="a", kind="files", locator="{}", pg_database="ontology_src_a")
    del store
    (tmp_path / "autoontology.db-wal").write_bytes(b"")
    (tmp_path / "autoontology.db-shm").write_bytes(b"")
    path = home_db_path(tmp_path)
    assert path == tmp_path / "ontology.db" and path.is_file()
    assert not legacy.exists()
    assert (tmp_path / "ontology.db-wal").exists() and (tmp_path / "ontology.db-shm").exists()
    assert not (tmp_path / "autoontology.db-wal").exists()
    assert Store.for_home(tmp_path).get_source("a") is not None
    # once: a later legacy file next to the new one is left alone
    legacy.write_bytes(b"stale")
    assert home_db_path(tmp_path) == path
    assert legacy.read_bytes() == b"stale"


def test_home_without_any_store_stays_empty(tmp_path):
    assert home_db_path(tmp_path) == tmp_path / "ontology.db"
    assert list(tmp_path.iterdir()) == []


def test_migration_v13_rewrites_legacy_source_database_names(tmp_path):
    store = Store(tmp_path / "ontology.db")
    store.add_source(name="old", kind="files", locator="{}", pg_database="ontology_src_old")
    store.add_source(name="beta", kind="files", locator="{}", pg_database="ontology_src_b__x")
    store.add_source(name="none", kind="files", locator="{}", pg_database=None)
    store.add_source(
        name="ext",
        kind="database",
        locator="postgresql://u:p@db.example:5432/autoontology_src_ext",
        pg_database=None,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE sources SET pg_database = 'autoontology_src_' || substr(pg_database, 14) "
            "WHERE pg_database LIKE 'ontology_src_%'"
        )
        conn.execute("DELETE FROM schema_version WHERE version >= 13")
    assert store.migrate() == 13 == db.MIGRATIONS[-1][0]
    assert store.migrate() == 13  # idempotent
    assert store.get_source("old").pg_database == "ontology_src_old"
    assert store.get_source("beta").pg_database == "ontology_src_b__x"
    assert store.get_source("none").pg_database is None
    # a database source names a database the user chose: kept
    assert store.get_source("ext").locator.endswith("/autoontology_src_ext")
