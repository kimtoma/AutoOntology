# SPDX-License-Identifier: MIT
"""Orphaned upstream connections: ``source rm --force --drop-db`` with the backend down,
the ``doctor`` row and ``prune_orphan_connections``."""

import pytest

from autoontology import doctor
from autoontology.config import Settings
from autoontology.engine import pg
from autoontology.engine.client import BackendError, BackendUnavailable
from autoontology.store.db import Store
from autoontology.structured import remove
from tests.test_cli_onboarding import FakeBackend


class _Conn:
    def __init__(self, executed):
        self.executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        self.executed.append(sql)


class _DownClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def delete_connection(self, db):
        raise BackendUnavailable("upstream backend not reachable")

    def database_id_by_name(self, db):
        raise BackendUnavailable("upstream backend not reachable")


@pytest.fixture
def files_source(isolated_env, monkeypatch):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    src = store.add_source(name="x", kind="files", locator="{}", pg_database="autoontology_src_x")
    executed: list[str] = []
    monkeypatch.setattr(pg, "connect", lambda *a, **k: _Conn(executed))
    return settings, store, src, executed


@pytest.mark.parametrize(
    "client_error",
    [BackendUnavailable("down"), BackendError("HTTP 500", status=500)],
)
def test_forced_drop_keeps_the_database_when_the_connection_stays(
    files_source, monkeypatch, client_error
):
    import autoontology.engine.client as client_mod

    settings, store, src, executed = files_source

    class Client(_DownClient):
        def delete_connection(self, db):
            raise client_error

    monkeypatch.setattr(client_mod, "BackendClient", Client)
    said: list[tuple[str, bool]] = []
    remove.remove_source(
        settings, store, src, drop_db=True, force=True, notify=lambda m, w: said.append((m, w))
    )
    assert executed == []  # no DROP DATABASE under a live connection
    assert store.get_source("x") is None  # --force: the local row still goes
    text = "\n".join(m for m, _w in said)
    assert "database autoontology_src_x kept" in text
    assert (
        "uv run autoontology tools prune-connections autoontology_src_x "
        "--include-unowned --drop-db" in text
    )
    assert "re-adding the source" not in text


def test_drop_happens_when_the_connection_was_deleted(files_source, monkeypatch):
    import autoontology.engine.client as client_mod

    settings, store, src, executed = files_source

    class Client(_DownClient):
        def delete_connection(self, db):
            return {}

        def database_id_by_name(self, db):
            return None

    monkeypatch.setattr(client_mod, "BackendClient", Client)
    remove.remove_source(settings, store, src, drop_db=True, force=True)
    assert executed == ['DROP DATABASE IF EXISTS "autoontology_src_x" WITH (FORCE)']


def test_find_orphans_by_missing_database_or_owner(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    store.add_source(name="a", kind="files", locator="{}", pg_database="autoontology_src_a")
    store.add_source(name="b", kind="files", locator="{}", pg_database="autoontology_src_b")
    fake = FakeBackend(
        ["autoontology_src_a", "autoontology_src_b", "autoontology_src_c", "crm", "x-y"]
    )
    present = {"autoontology_src_a", "autoontology_src_c"}
    orphans = remove.find_orphan_connections(
        settings, store, fake, database_exists=present.__contains__
    )
    assert [(o.name, o.reason) for o in orphans] == [
        ("autoontology_src_b", "database missing"),
        ("autoontology_src_c", "no registered source"),
    ]
    # no store yet: every autoontology_src_* connection is unowned
    assert (
        len(
            remove.find_orphan_connections(
                settings, None, fake, database_exists=present.__contains__
            )
        )
        == 3
    )


def test_prune_only_touches_autoontology_names_and_drops_unowned_databases(
    isolated_env, monkeypatch
):
    settings = Settings.from_env()
    executed: list[str] = []
    monkeypatch.setattr(pg, "connect", lambda *a, **k: _Conn(executed))
    fake = FakeBackend(["autoontology_src_b", "autoontology_src_c", "crm"])
    orphans = [
        remove.OrphanConnection("autoontology_src_b", True, True),
        remove.OrphanConnection("autoontology_src_c", False, False),
        remove.OrphanConnection("crm", True, False),  # never pruned, even if asked
    ]
    said: list[str] = []
    deleted = remove.prune_orphan_connections(
        settings,
        fake,
        orphans,
        drop_db=True,
        say=lambda m, w=False: said.append(m),
        database_exists=lambda name: name == "autoontology_src_c",
    )
    assert deleted == fake.deleted == ["autoontology_src_b", "autoontology_src_c"]
    assert executed == ['DROP DATABASE IF EXISTS "autoontology_src_c" WITH (FORCE)']
    assert any("skipped crm" in m for m in said)


def _doctor_settings(isolated_env, monkeypatch, names):
    import autoontology.engine.client as client_mod

    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    store.add_source(name="a", kind="files", locator="{}", pg_database="autoontology_src_a")
    monkeypatch.setattr(remove, "backend_is_ours", lambda s: True)
    monkeypatch.setattr(client_mod, "BackendClient", lambda *a, **k: FakeBackend(names))
    monkeypatch.setattr(pg, "database_exists", lambda s, name: name != "autoontology_src_gone")
    return settings


def test_doctor_warns_about_orphaned_connections(isolated_env, monkeypatch):
    settings = _doctor_settings(
        isolated_env, monkeypatch, ["autoontology_src_a", "autoontology_src_gone"]
    )
    check = doctor.check_upstream_connections(settings)
    assert check.status == "WARN"
    assert "database gone (breaks every ingest): autoontology_src_gone" in check.detail
    assert check.remedy == "uv run autoontology tools prune-connections"


def test_doctor_reports_live_unowned_connections_without_plain_prune(isolated_env, monkeypatch):
    settings = _doctor_settings(isolated_env, monkeypatch, ["autoontology_src_other"])
    check = doctor.check_upstream_connections(settings)
    assert check.status == "WARN"
    assert check.detail == "no source in this checkout: autoontology_src_other"
    assert check.remedy == (
        "if no other checkout uses them: uv run autoontology tools prune-connections "
        "--include-unowned"
    )


def test_doctor_skips_when_postgres_is_down(isolated_env, monkeypatch):
    import psycopg

    settings = _doctor_settings(isolated_env, monkeypatch, ["autoontology_src_a"])

    def down(s, name):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(pg, "database_exists", down)
    check = doctor.check_upstream_connections(settings)
    assert check.status == "SKIP" and "Postgres not reachable" in check.detail
    assert "prune-connections" not in check.remedy and "docker compose" in check.remedy


def test_doctor_upstream_row_ok_and_skip(isolated_env, monkeypatch):
    settings = _doctor_settings(isolated_env, monkeypatch, ["autoontology_src_a", "crm"])
    assert doctor.check_upstream_connections(settings).status == "OK"
    monkeypatch.setattr(remove, "backend_is_ours", lambda s: False)
    skipped = doctor.check_upstream_connections(settings)
    assert skipped.status == "SKIP" and "autoontology up" in skipped.detail
