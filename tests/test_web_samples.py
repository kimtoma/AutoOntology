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


def test_list_samples_names_formats_and_expected_build_times(client):
    """The UI's "Load sample data" dialog shows each source's formats and the expected
    build time per provider (the table `demo` prints)."""
    from autoontology.onboarding import DEMO_DURATIONS

    c, *_ = client
    quick = c.get(f"{API}/samples").json()[0]
    assert [s["formats"] for s in quick["sources"]] == [[".csv"], [".md"]]
    assert quick["durations"] == {
        provider: {"text": text, "measured": measured}
        for provider, (text, measured) in DEMO_DURATIONS["quickstart"].items()
    }


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


@pytest.mark.parametrize("raw", ["[1, 2]", '"quickstart"', "3", "{not json"])
def test_load_needs_a_json_object(client, raw):
    c, *_ = client
    resp = c.post(f"{API}/samples/load", content=raw, headers={"content-type": "application/json"})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_body"


@pytest.mark.parametrize("name", ["../quickstart", "quickstart/..", "quickstart/structured"])
def test_load_rejects_path_shaped_names(client, name):
    c, *_ = client
    resp = c.post(f"{API}/samples/load", json={"set": name})
    assert resp.status_code == 400 and resp.json()["error"] == "unknown_sample_set"


def test_replacing_a_source_needs_replace_true(client, monkeypatch):
    from autoontology.structured import remove

    c, settings, _ = client
    removed = []

    def fake_remove(settings, store, src, **kw):
        removed.append(src.name)
        store.delete_source(src.id)

    monkeypatch.setattr(remove, "remove_source", fake_remove)
    assert c.post(f"{API}/samples/load", json={"set": "quickstart"}).status_code == 200
    store = Store.for_home(settings.home)
    store.set_locator(store.get_source("quick").id, '{"path": "/old/place", "recursive": false}')
    resp = c.post(f"{API}/samples/load", json={"set": "quickstart"})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "sample_conflict" and removed == []
    assert [(x["name"], x["registered"]) for x in body["conflicts"]] == [
        ("quick", str(Path("/old/place").resolve()))
    ]
    assert body["conflicts"][0]["sample"].endswith("quickstart/structured")
    bad = c.post(f"{API}/samples/load", json={"set": "quickstart", "replace": "yes"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_body"
    ok = c.post(f"{API}/samples/load", json={"set": "quickstart", "replace": True})
    assert ok.status_code == 200, ok.text
    assert [s["action"] for s in ok.json()["sources"]] == ["replaced", "kept"]
    assert removed == ["quick"]


def test_load_holds_the_workspace_while_registering(client, monkeypatch):
    """No run can start between the run-active check and the registration."""
    from autoontology import onboarding
    from autoontology.web.runs import RunStartError

    c, _settings, manager = client
    seen = []
    real = onboarding.register_sample_set

    def register(*a, **kw):
        with pytest.raises(RunStartError):
            manager.claim("*workspace*", "build")
        seen.append(manager.busy().kind)
        return real(*a, **kw)

    monkeypatch.setattr(onboarding, "register_sample_set", register)
    assert c.post(f"{API}/samples/load", json={"set": "quickstart"}).status_code == 200
    assert seen == ["samples"] and manager.busy() is None


def test_source_registration_during_a_sample_load_is_409_busy(client, monkeypatch, tmp_path):
    """A JSON `POST /api/sources` while a sample load holds the workspace is refused (409
    `busy`) instead of registering a source the load could then remove."""
    from autoontology import onboarding

    c, settings, manager = client
    other = tmp_path / "mine"
    other.mkdir()
    (other / "a.md").write_text("# a\n", encoding="utf-8")
    answers = []
    real = onboarding.register_sample_set

    def register(*a, **kw):
        answers.append(
            c.post(
                f"{API}/sources",
                json={"name": "quick-docs", "kind": "documents", "locator": str(other)},
            )
        )
        return real(*a, **kw)

    monkeypatch.setattr(onboarding, "register_sample_set", register)
    resp = c.post(f"{API}/samples/load", json={"set": "quickstart"})
    assert resp.status_code == 200, resp.text
    assert [s["action"] for s in resp.json()["sources"]] == ["created", "created"]
    assert answers[0].status_code == 409 and answers[0].json()["error"] == "busy"
    sample = (Path(settings.samples_dir) / "quickstart" / "documents").resolve()
    src = Store.for_home(settings.home).get_source("quick-docs")
    assert str(sample) in src.locator
    assert manager.busy() is None
    # once the load is done, registration works again
    ok = c.post(f"{API}/sources", json={"name": "mine", "kind": "documents", "locator": str(other)})
    assert ok.status_code == 201, ok.text
    assert ok.json()["active_run"] is None and manager.busy() is None
