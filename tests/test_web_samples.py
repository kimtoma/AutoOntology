# SPDX-License-Identifier: MIT
"""``GET /api/samples`` and ``POST /api/samples/load`` against a temp home."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.store.db import Store
from autoontology.web import app as web
from autoontology.web.runs import RunManager
from tests.test_web_api import API, FakeJobs


@pytest.fixture
def client(isolated_env, monkeypatch):
    root = Path(isolated_env) / "samples"
    (root / "quickstart" / "structured").mkdir(parents=True)
    (root / "quickstart" / "structured" / "customers.csv").write_text("id\n1\n2\n")
    (root / "quickstart" / "documents").mkdir()
    (root / "quickstart" / "documents" / "규정.md").write_text("# 규정\n본문\n", encoding="utf-8")
    (root / "secret").mkdir()  # outside the allowlist: never listed
    (root / "secret" / "structured").mkdir()
    (root / "secret" / "structured" / "x.csv").write_text("a\n1\n")
    monkeypatch.setenv("AUTOONTOLOGY_SAMPLES_DIR", str(root))
    settings = Settings.from_env()
    manager = RunManager(settings, jobs=FakeJobs().table)
    return TestClient(web.create_app(settings, runs=manager)), settings, manager


def test_list_samples(client):
    c, settings, _ = client
    resp = c.get(f"{API}/samples")
    assert resp.status_code == 200, resp.text
    sets = resp.json()
    assert [s["set"] for s in sets] == ["quickstart"]
    quick = sets[0]
    assert quick["registered"] is False
    assert quick["path"] == str((Path(settings.samples_dir) / "quickstart").resolve())
    assert [(s["name"], s["kind"], s["files"], s["bytes"]) for s in quick["sources"]] == [
        ("quick", "structured", 1, 7),
        ("quick-docs", "documents", 1, len("# 규정\n본문\n".encode())),
    ]


def test_load_samples_registers_like_demo(client):
    c, settings, _ = client
    resp = c.post(f"{API}/samples/load", json={"set": "quickstart"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(s["name"], s["action"]) for s in body["sources"]] == [
        ("quick", "created"),
        ("quick-docs", "created"),
    ]
    assert body["sources"][0]["kind"] == "files"
    again = c.post(f"{API}/samples/load", json={"set": "quickstart"}).json()
    assert [s["action"] for s in again["sources"]] == ["kept", "kept"]
    assert c.get(f"{API}/samples").json()[0]["registered"] is True
    names = [s.name for s in Store.for_home(settings.home).list_sources()]
    assert names == ["quick", "quick-docs"]


@pytest.mark.parametrize("body", [{"set": "secret"}, {"set": "hanbit"}, {"set": 3}, {}])
def test_load_unknown_or_missing_set_is_400(client, body):
    c, *_ = client
    resp = c.post(f"{API}/samples/load", json=body)
    assert resp.status_code == 400 and resp.json()["error"] == "unknown_sample_set"


def test_load_while_a_run_is_active_is_409(client):
    c, settings, manager = client
    manager.claim("*workspace*", "build")
    try:
        resp = c.post(f"{API}/samples/load", json={"set": "quickstart"})
    finally:
        manager.release("*workspace*")
    assert resp.status_code == 409 and resp.json()["error"] == "run_active"
    store = Store.for_home(settings.home)
    assert store.list_sources() == []
    run = store.create_run(kind="build", source_id=None, params={"pid": 1})
    assert c.post(f"{API}/samples/load", json={"set": "quickstart"}).status_code == 409
    store.update_run(run.id, status="succeeded")
    assert c.post(f"{API}/samples/load", json={"set": "quickstart"}).status_code == 200
