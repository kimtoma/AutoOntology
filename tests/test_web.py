import json

import httpx
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.web import app as web


def client(isolated_env):
    return TestClient(web.create_app(Settings.from_env()))


def test_health_live(isolated_env):
    assert client(isolated_env).get("/health/live").json() == {"status": "ok"}


def test_ingest_records_request_and_redacts(isolated_env, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "enqueue_ingest", lambda conn, settings=None: seen.append(conn))
    c = client(isolated_env)
    body = {
        "database": "autoontology_src_demo",
        "type": "postgres",
        "password": "hunter2",
        "options": {"api_key": "k"},
    }
    resp = c.post("/ingest", json=body)
    assert resp.status_code == 202 and resp.json() == {"status": "accepted"}
    assert seen == [body]
    log = Settings.from_env().run_dir / "ingest-requests.jsonl"
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["database"] == "autoontology_src_demo"
    assert record["connection"]["password"] == "***"
    assert record["connection"]["options"]["api_key"] == "***"
    assert "hunter2" not in log.read_text()


def test_health_degraded_when_dependencies_down(isolated_env, monkeypatch):
    def no_pg(*a, **k):
        raise OSError("refused")

    def no_backend(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(web, "connect", no_pg)
    monkeypatch.setattr(web.httpx, "get", no_backend)
    resp = client(isolated_env).get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"]["status"] == "error"
    assert body["checks"]["backend"]["detail"] == "ConnectError"


def test_health_ok(isolated_env, monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            return None

    monkeypatch.setattr(web, "connect", lambda s: Conn())
    monkeypatch.setattr(web.httpx, "get", lambda url, timeout: httpx.Response(200))
    resp = client(isolated_env).get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


def test_enqueue_ingest_runs_ingest_job_in_thread(isolated_env, monkeypatch):
    from autoontology.engine import jobs

    calls = []
    monkeypatch.setattr(
        jobs, "run_ingest_job", lambda db, settings, store: calls.append((db, store.path.name))
    )
    settings = Settings.from_env()
    assert web.enqueue_ingest({"type": "postgres"}, settings) is None
    thread = web.enqueue_ingest({"database": "autoontology_src_demo"}, settings)
    thread.join(5)
    assert calls == [("autoontology_src_demo", "autoontology.db")]


def test_enqueue_ingest_swallows_job_errors(isolated_env, monkeypatch):
    from autoontology.engine import jobs

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(jobs, "run_ingest_job", boom)
    web.enqueue_ingest({"database": "d"}, Settings.from_env()).join(5)  # no exception


def test_ingest_delete_shim(isolated_env, monkeypatch):
    seen = []
    monkeypatch.setattr(web, "enqueue_delete", lambda db, settings=None: seen.append(db))
    c = client(isolated_env)
    assert (
        c.post("/ingest/delete", params={"database_name": "autoontology_src_a"}).status_code == 202
    )
    assert c.post("/ingest/delete", params={"database_name": "  "}).status_code == 422
    assert c.post("/ingest/delete").status_code == 422
    assert seen == ["autoontology_src_a"]


def test_shims_and_proxy_refuse_foreign_host_and_cross_origin(isolated_env, monkeypatch):
    """Finding #1: /ingest*, /v1/* are behind the same Host/Origin guard as the API."""
    deleted, ingested = [], []
    monkeypatch.setattr(web, "enqueue_delete", lambda db, settings=None: deleted.append(db))
    monkeypatch.setattr(web, "enqueue_ingest", lambda c, settings=None: ingested.append(c))
    c = client(isolated_env)
    url = "/ingest/delete?database_name=autoontology_src_hanbit"
    resp = c.post(url, headers={"host": "evil.example:7800"})
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden_host"
    resp = c.post(url, headers={"origin": "http://evil.example"})
    assert resp.status_code == 403 and resp.json()["error"] == "cross_origin"
    resp = c.post(url, headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403
    resp = c.post("/ingest", json={"database": "x"}, headers={"origin": "http://evil.example"})
    assert resp.status_code == 403
    resp = c.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": []},
        headers={"host": "evil.example"},
    )
    assert resp.status_code == 403
    assert deleted == [] and ingested == []
    # upstream's own calls: Host 127.0.0.1:7800, no Origin
    resp = c.post(url, headers={"host": "127.0.0.1:7800"})
    assert resp.status_code == 202 and deleted == ["autoontology_src_hanbit"]
    resp = c.post("/ingest", json={"database": "x"}, headers={"host": "127.0.0.1:7800"})
    assert resp.status_code == 202 and ingested == [{"database": "x"}]
    # health probes stay open
    assert c.get("/health/live", headers={"host": "evil.example"}).status_code == 200
