# SPDX-License-Identifier: MIT
"""Per-source freshness (``web/freshness.py``, ``GET /api/sources/freshness``) after a fake
build."""

import time

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.engine import build as bj
from autoontology.structured import pipeline
from autoontology.web import app as web
from autoontology.web.freshness import freshness, source_freshness
from autoontology.web.runs import RunManager
from tests import test_build as tb
from tests.test_build import env  # noqa: F401 - the build fixture


@pytest.fixture
def built(env):  # noqa: F811
    tb.add_structured(env)
    tb.add_documents(env)
    assert tb.run(env).status == "succeeded"
    return env


def _by_name(built):
    return {r["name"]: r for r in freshness(built["store"], built["settings"])}


def _reasons(row):
    return {r["kind"]: r for r in row["reasons"]}


def test_a_fresh_build_is_fresh(built):
    rows = _by_name(built)
    assert {n: r["state"] for n, r in rows.items()} == {"hanbit": "fresh", "hanbit-docs": "fresh"}
    assert all(
        r["stale"] is False and r["last_built_at"] and r["last_inputs_sha"] for r in rows.values()
    )


def test_documents_added_changed_removed(built):
    docs = built["docs"]
    (docs / "신규.md").write_text("# 신규\n\n새 문서.", encoding="utf-8")
    row = _by_name(built)["hanbit-docs"]
    assert row["state"] == "stale" and row["stale"] is True
    added = _reasons(row)["files_added"]
    assert added["count"] == 1 and added["examples"][0].endswith("신규.md")
    (docs / "신규.md").unlink()
    (docs / "계약.md").write_text("# 계약규정\n\n바뀐 내용.", encoding="utf-8")
    (docs / "청구.md").unlink()
    reasons = _reasons(_by_name(built)["hanbit-docs"])
    assert reasons["files_changed"]["count"] == 1 and reasons["files_removed"]["count"] == 1
    assert "files_added" not in reasons


def test_reader_settings_change_is_named(built, monkeypatch):
    monkeypatch.setattr(bj.extract_job, "reader_settings", lambda s: "reader=99")
    row = _by_name(built)["hanbit-docs"]
    assert row["state"] == "stale" and [r["kind"] for r in row["reasons"]] == ["reader_changed"]


def test_extraction_selection_change_marks_processing_stale(built):
    from dataclasses import replace

    changed = replace(built["settings"], chat_provider="codex", codex_model="next-model")
    rows = {r["name"]: r for r in freshness(built["store"], changed)}
    assert rows["hanbit-docs"]["state"] == "stale"
    assert [r["kind"] for r in rows["hanbit-docs"]["reasons"]] == ["reader_changed"]


def test_files_source_changed_file(built):
    src = built["store"].get_source("hanbit")
    loaded = {"contracts": {"source_file": str(built["data"] / "contracts.csv")}}
    built["store"].set_file_manifest(src.id, loaded)
    time.sleep(0.01)
    (built["data"] / "contracts.csv").write_text("x\n1\n2\n")
    (built["data"] / "extra.csv").write_text("y\n1\n")
    reasons = _reasons(_by_name(built)["hanbit"])
    assert reasons["files_changed"]["examples"][0].endswith("contracts.csv")
    assert reasons["files_added"]["examples"][0].endswith("extra.csv")
    # without a manifest: the files modified since the build
    built["store"].set_file_manifest(src.id, {})
    reasons = _reasons(_by_name(built)["hanbit"])
    assert list(reasons) == ["files_changed"] and reasons["files_changed"]["count"] == 2


def test_never_built_and_missing_inputs(built, tmp_path):
    from autoontology.engine import extract as ex

    other = tmp_path / "other"
    other.mkdir()
    (other / "a.md").write_text("# a", encoding="utf-8")
    ex.ensure_documents_source(built["store"], "later-docs", other)
    rows = _by_name(built)
    assert rows["later-docs"]["state"] == "never_built"
    assert rows["later-docs"]["reasons"] == [{"kind": "never_built", "count": 0, "examples": []}]
    # the documents folder is gone: unknown, never an exception
    import shutil

    shutil.rmtree(built["docs"])
    row = _by_name(built)["hanbit-docs"]
    assert row["state"] == "unknown" and row["stale"] is None and row["error"]


def test_database_schema_change_and_unreachable(built, monkeypatch):
    store = built["store"]
    src = store.add_source(name="crm", kind="database", locator="postgresql://u:p@127.0.0.1:1/crm")
    schema = {"rows": [("public", "t", "a", "text")]}
    monkeypatch.setitem(pipeline.SCHEMA_INTROSPECTORS, "postgres", lambda conn: schema["rows"])
    sha = bj.input_fingerprint(src, built["settings"].home, reader="x")
    run = store.create_run(kind="build", source_id=None, params={})
    store.finish_step(
        run.id, "structured:crm:register", status="succeeded", detail={"inputs_sha": sha}
    )
    assert source_freshness(store, built["settings"], src)["state"] == "fresh"
    schema["rows"] = [("public", "t", "a", "integer")]
    row = source_freshness(store, built["settings"], src)
    assert row["state"] == "stale" and row["reasons"][0]["kind"] == "schema_changed"

    def unreachable(conn):
        raise ConnectionRefusedError("no server")

    monkeypatch.setitem(pipeline.SCHEMA_INTROSPECTORS, "postgres", unreachable)
    row = source_freshness(store, built["settings"], src)
    assert (row["state"], row["error"]) == ("unknown", "ConnectionRefusedError")
    assert "p@" not in str(row)


def test_freshness_api_caches_for_a_minute(built):
    settings = Settings.from_env()
    c = TestClient(web.create_app(settings, runs=RunManager(settings)))
    first = c.get("/api/sources/freshness").json()
    assert first["stale"] == 0 and {r["name"] for r in first["sources"]} == {
        "hanbit",
        "hanbit-docs",
    }
    (built["docs"] / "신규.md").write_text("# 신규", encoding="utf-8")
    cached = c.get("/api/sources/freshness").json()
    assert cached["stale"] == 0 and cached["cache_s"] == 60.0
    fresh = c.get("/api/sources/freshness", params={"refresh": 1}).json()
    assert fresh["stale"] == 1 and fresh["age_s"] == 0.0
    # the review inbox lists the stale source
    items = c.get("/api/review", params={"category": "stale_sources"}).json()["items"]
    assert [i["id"] for i in items] == ["src:hanbit-docs"]
    assert items[0]["suggested_action"] == "build" and items[0]["source"]["stale"] is True
