# SPDX-License-Identifier: MIT
"""Projects in the REST API and the CLI: canonical ``/api/projects/{project}/...`` routes,
the default project's ``/api/...`` aliases, project management, archived projects,
ownership checks (another project's ids read as unknown) and ``--project`` routing."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autoontology import cli_ops
from autoontology.cli import app as cli_app
from autoontology.config import Settings
from autoontology.ontology import model as merged
from autoontology.store.db import Store
from autoontology.store.decisions import Decision
from autoontology.web import api as web_api
from autoontology.web import app as web
from autoontology.web.runs import RunManager
from tests.test_build import env  # noqa: F401 - the build fixture (the ops fixture's)
from tests.test_decisions import _rows, ops  # noqa: F401
from tests.test_web_api import FakeJobs

API = "/api"
BETA = f"{API}/projects/beta"
runner = CliRunner()
log = logging.getLogger("autoontology.jobs")


@pytest.fixture
def web_env(isolated_env, monkeypatch):
    """The service app over a temp home with fake build/export jobs, plus project beta."""
    monkeypatch.setattr(web_api, "SSE_POLL_S", 0.01)
    settings = Settings.from_env()
    fake = FakeJobs()
    manager = RunManager(settings, jobs=fake.table)
    c = TestClient(web.create_app(settings, runs=manager))
    resp = c.post(f"{API}/projects", json={"slug": "beta", "name": "Beta"})
    assert resp.status_code == 201, resp.text
    store = Store.for_home(settings.home)
    return {"c": c, "settings": settings, "fake": fake, "runs": manager, "store": store}


def _folder(root: Path, name: str, files: dict[str, str]) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for fname, text in files.items():
        (d / fname).write_text(text, encoding="utf-8")
    return d


def _add(c, base: str, name: str, kind: str, locator: Path):
    resp = c.post(f"{base}/sources", json={"name": name, "kind": kind, "locator": str(locator)})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _snapshot(view, name: str) -> int:
    run = view.create_run(kind="build", source_id=None)
    view.update_run(run.id, status="succeeded")
    return view.save_snapshot(run.id, merged.build(name))


# --- management ------------------------------------------------------------------------------


def test_project_management_list_create_show(web_env):
    c = web_env["c"]
    body = c.get(f"{API}/projects").json()
    assert body["default"] == "default"
    assert [p["slug"] for p in body["projects"]] == ["default", "beta"]
    beta = body["projects"][1]
    assert beta["name"] == "Beta" and beta["default"] is False and beta["archived"] is False
    assert beta["latest_revision"] is None and beta["sources"] == 0
    assert beta["last_build"] is None and beta["api_base"] == "/api/projects/beta"
    assert body["projects"][0]["default"] is True
    # validation and duplicates
    bad = c.post(f"{API}/projects", json={"slug": "Bad Slug"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_slug"
    dup = c.post(f"{API}/projects", json={"slug": "beta"})
    assert dup.status_code == 409 and dup.json()["error"] == "project_exists"
    extra = c.post(f"{API}/projects", json={"slug": "gamma", "owner": "x"})
    assert extra.status_code == 400 and extra.json()["error"] == "invalid_request"
    gamma = c.post(f"{API}/projects", json={"slug": "gamma", "description": "세 번째"}).json()
    assert gamma["name"] == "gamma" and gamma["description"] == "세 번째"
    shown = c.get(f"{API}/projects/gamma").json()
    assert shown["id"] == gamma["id"] and shown["slug"] == "gamma"
    assert c.get(f"{API}/projects/{gamma['id']}").json()["slug"] == "gamma"  # by id too
    gone = c.get(f"{API}/projects/nope")
    assert gone.status_code == 404 and gone.json()["error"] == "unknown_project"
    assert c.get(f"{API}/projects/nope/sources").json()["error"] == "unknown_project"
    assert c.delete(f"{API}/projects/gamma").status_code == 405  # no delete in v1


def test_archived_projects_are_read_only(web_env, isolated_env):
    c = web_env["c"]
    docs = _folder(isolated_env, "d", {"a.md": "# a\n"})
    _add(c, BETA, "docs", "documents", docs)
    archived = c.post(f"{BETA}/archive")
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert c.get(f"{API}/projects").json()["projects"][1]["archived"] is True
    listed = c.get(f"{API}/projects", params={"include_archived": False}).json()["projects"]
    assert [p["slug"] for p in listed] == ["default"]
    # reads work, writes are refused
    assert [s["name"] for s in c.get(f"{BETA}/sources").json()["sources"]] == ["docs"]
    for method, path, body in (
        ("POST", f"{BETA}/sources", {"name": "x", "kind": "documents", "locator": str(docs)}),
        ("DELETE", f"{BETA}/sources/docs", None),
        ("POST", f"{BETA}/runs", {"kind": "build"}),
        ("POST", f"{BETA}/agent/tokens", {"name": "a"}),
        ("POST", f"{BETA}/samples/load", {"set": "quickstart"}),
        ("POST", f"{BETA}/playground/index", None),
        ("PATCH", BETA, {"name": "B"}),
    ):
        resp = c.request(method, path, json=body)
        assert resp.status_code == 409 and resp.json()["error"] == "project_archived", (
            method,
            path,
            resp.text,
        )
    # the playground's questions read the ontology: allowed (here: no snapshot yet)
    resp = c.post(f"{BETA}/playground/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"})
    assert resp.status_code == 404 and resp.json()["error"] == "no_snapshot"
    assert c.post(f"{BETA}/archive").status_code == 200  # idempotent
    assert c.post(f"{BETA}/unarchive").json()["archived"] is False
    assert c.patch(BETA, json={"name": "B"}).json()["name"] == "B"
    # the default project cannot be archived
    resp = c.post(f"{API}/projects/default/archive")
    assert resp.status_code == 409 and resp.json()["error"] == "default_project"


# --- isolation -------------------------------------------------------------------------------


def test_same_source_name_in_two_projects_and_aliases(web_env, isolated_env):
    c, store = web_env["c"], web_env["store"]
    a = _folder(isolated_env, "a", {"a.md": "# a\n"})
    b = _folder(isolated_env, "b", {"b.md": "# b\n"})
    _add(c, API, "docs", "documents", a)  # the alias: the default project
    _add(c, BETA, "docs", "documents", b)
    mine = c.get(f"{API}/sources").json()["sources"]
    canonical = c.get(f"{API}/projects/default/sources").json()["sources"]
    theirs = c.get(f"{BETA}/sources").json()["sources"]
    assert mine == canonical
    assert [s["locator"] for s in mine] == [str(a.resolve())]
    assert [s["locator"] for s in theirs] == [str(b.resolve())]
    assert store.project("beta").get_source("docs").project_id == store.project("beta").id
    # removing beta's leaves the default one
    assert c.delete(f"{BETA}/sources/docs").json()["removed"] == "docs"
    assert c.get(f"{BETA}/sources").json()["sources"] == []
    assert [s["name"] for s in c.get(f"{API}/sources").json()["sources"]] == ["docs"]
    # the projects list counts each project's sources
    counts = {p["slug"]: p["sources"] for p in c.get(f"{API}/projects").json()["projects"]}
    assert counts == {"default": 1, "beta": 0}


def test_uploads_of_the_same_name_live_in_their_project(web_env):
    c, settings, store = web_env["c"], web_env["settings"], web_env["store"]
    files = [("files", ("a.csv", b"x\n1\n", "text/csv"))]
    for base in (API, BETA):
        resp = c.post(f"{base}/sources", data={"name": "shop", "kind": "files"}, files=files)
        assert resp.status_code == 201, resp.text
        assert resp.json()["uploaded"] is True
    uploads = Path(settings.home) / "uploads"
    beta_id = store.project("beta").id
    assert (uploads / "shop" / "a.csv").is_file()
    assert (uploads / ".projects" / beta_id / "shop" / "a.csv").is_file()
    gone = c.delete(f"{BETA}/sources/shop", params={"force": True})
    assert gone.status_code == 200, gone.text
    assert not (uploads / ".projects" / beta_id / "shop").exists()
    assert (uploads / "shop" / "a.csv").is_file()


def test_runs_are_per_project_and_other_ids_are_unknown(web_env, isolated_env):
    c, fake, manager, store = web_env["c"], web_env["fake"], web_env["runs"], web_env["store"]
    csv = _folder(isolated_env, "csv", {"a.csv": "x\n1\n"})
    _add(c, API, "shop", "files", csv)
    _add(c, BETA, "shop", "files", csv)
    fake.gate.clear()
    resp = c.post(f"{BETA}/runs", json={"kind": "build"})
    assert resp.status_code == 202, resp.text
    beta_run = resp.json()["run_id"]
    assert store.get_run(beta_run).project_id == store.project("beta").id
    # the global engine gate: the default project waits its turn (409, never queued)
    busy = c.post(f"{API}/runs", json={"kind": "build"})
    assert busy.status_code == 409 and "engine already has an active" in busy.json()["detail"]
    # a slug change waits for the project's run
    resp = c.patch(BETA, json={"slug": "gamma"})
    assert resp.status_code == 409 and resp.json()["error"] == "run_active"
    assert c.patch(BETA, json={"description": "ok while running"}).status_code == 200
    projects = {p["slug"]: p for p in c.get(f"{API}/projects").json()["projects"]}
    assert projects["beta"]["active"] is True and projects["default"]["active"] is False
    fake.gate.set()
    manager.join(beta_run, 5)
    # beta's run: listed and readable in beta only
    assert [r["id"] for r in c.get(f"{BETA}/runs").json()["runs"]] == [beta_run]
    assert c.get(f"{API}/runs").json()["runs"] == []
    assert c.get(f"{BETA}/runs/{beta_run}").json()["status"] == "succeeded"
    for method, path in (
        ("GET", f"{API}/runs/{beta_run}"),
        ("GET", f"{API}/runs/{beta_run}/events"),
        ("POST", f"{API}/runs/{beta_run}/cancel"),
    ):
        resp = c.request(method, path)
        assert resp.status_code == 404 and resp.json()["error"] == "unknown_run", (method, path)
    with c.stream("GET", f"{BETA}/runs/{beta_run}/events") as resp:
        assert resp.status_code == 200
        assert "event: end" in "".join(resp.iter_text())
    # the default project builds its own run, which beta cannot see
    resp = c.post(f"{API}/runs", json={"kind": "build"})
    default_run = resp.json()["run_id"]
    manager.join(default_run, 5)
    assert [r["id"] for r in c.get(f"{API}/runs").json()["runs"]] == [default_run]
    assert c.get(f"{BETA}/runs/{default_run}").status_code == 404
    # the last build per project in the listing
    projects = {p["slug"]: p for p in c.get(f"{API}/projects").json()["projects"]}
    assert projects["beta"]["last_run"]["id"] == beta_run
    assert projects["default"]["last_run"]["id"] == default_run
    # a source's last run is its project's
    beta_src = c.get(f"{BETA}/sources").json()["sources"][0]
    assert beta_src["last_run"]["id"] == beta_run


def test_ontology_revisions_are_per_project(web_env):
    c, store = web_env["c"], web_env["store"]
    default, beta = store.project(), store.project("beta")
    _snapshot(default, "default")
    _snapshot(default, "default")
    _snapshot(beta, "Beta")
    assert [r["revision"] for r in c.get(f"{API}/ontology/revisions").json()["revisions"]] == [
        2,
        1,
    ]
    assert [r["revision"] for r in c.get(f"{BETA}/ontology/revisions").json()["revisions"]] == [1]
    assert c.get(f"{API}/ontology").json()["workspace"] == "default"
    summary = c.get(f"{BETA}/ontology").json()
    assert summary["workspace"] == "Beta" and summary["revision"] == 1
    assert c.get(f"{BETA}/ontology", params={"revision": 2}).json()["error"] == "unknown_revision"
    assert c.get(f"{BETA}/ontology/concepts").json()["revision"] == 1
    assert c.get(f"{BETA}/ontology/graph").json()["revision"] == 1
    assert c.get(f"{BETA}/ontology/diff").json()["from"] is None
    diff = c.get(f"{API}/ontology/diff").json()
    assert (diff["from"], diff["to"]) == (1, 2)
    listed = {p["slug"]: p["latest_revision"] for p in c.get(f"{API}/projects").json()["projects"]}
    assert listed == {"default": 2, "beta": 1}


def test_exports_are_per_project(web_env):
    c, settings = web_env["c"], web_env["settings"]
    root = Path(settings.exports_dir)
    (root / "default" / "r0001").mkdir(parents=True)
    (root / "default" / "r0001" / "report.md").write_text("default", encoding="utf-8")
    (root / "beta" / "r0001").mkdir(parents=True)
    (root / "beta" / "r0001" / "only-beta.md").write_text("beta", encoding="utf-8")
    mine = c.get(f"{API}/exports").json()["exports"]
    theirs = c.get(f"{BETA}/exports").json()["exports"]
    assert [f["name"] for f in mine[0]["files"]] == ["report.md"]
    assert [f["name"] for f in theirs[0]["files"]] == ["only-beta.md"]
    assert c.get(f"{BETA}/exports/r0001/only-beta.md").content == b"beta"
    assert c.get(f"{API}/exports/r0001/only-beta.md").status_code == 404
    assert c.get(f"{BETA}/exports/r0001/report.md").status_code == 404
    assert c.get(f"{BETA}/exports/1.zip").status_code == 200
    assert c.get(f"{API}/projects/default/exports/r0001/report.md").content == b"default"


def test_agent_tokens_are_per_project(web_env):
    c = web_env["c"]
    made = c.post(f"{BETA}/agent/tokens", json={"name": "beta-agent"})
    assert made.status_code == 201, made.text
    tid = made.json()["record"]["id"]
    assert [t["id"] for t in c.get(f"{BETA}/agent/tokens").json()["tokens"]] == [tid]
    assert c.get(f"{API}/agent/tokens").json()["tokens"] == []
    for method, path in (
        ("POST", f"{API}/agent/tokens/{tid}/rotate"),
        ("DELETE", f"{API}/agent/tokens/{tid}"),
    ):
        resp = c.request(method, path)
        assert resp.status_code == 404 and resp.json()["error"] == "unknown_token"
    # a pin names a revision of the token's own project
    _snapshot(web_env["store"].project(), "default")
    resp = c.post(f"{BETA}/agent/tokens", json={"name": "pinned", "revision_pin": 1})
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_revision"
    assert c.get(f"{BETA}/agent/usage").json()["total"] == 0
    assert c.delete(f"{BETA}/agent/tokens/{tid}").json()["status"] == "revoked"


def test_playground_conversations_are_per_project(web_env):
    c, store = web_env["c"], web_env["store"]
    _snapshot(store.project("beta"), "Beta")
    cid = store.project("beta").playground().create_conversation("Beta", "질문")
    assert c.get(f"{BETA}/playground/conversations/{cid}").status_code == 200
    resp = c.get(f"{API}/playground/conversations/{cid}")
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_conversation"
    assert c.delete(f"{API}/playground/conversations/{cid}").status_code == 404
    assert [x["id"] for x in c.get(f"{BETA}/playground/conversations").json()["conversations"]] == [
        cid
    ]
    # the default project has no snapshot: its playground has no scope
    assert c.get(f"{API}/playground/conversations").json()["error"] == "no_snapshot"
    sparql = c.post(f"{BETA}/playground/sparql", json={"query": "SELECT * WHERE { ?s ?p ?o }"})
    assert sparql.status_code == 200 and sparql.json()["revision"] == 1
    assert c.delete(f"{BETA}/playground/conversations/{cid}").json() == {"deleted": cid}


def test_samples_load_into_the_context_project(web_env, isolated_env, monkeypatch):
    root = Path(isolated_env) / "samples"
    (root / "quickstart" / "structured").mkdir(parents=True)
    (root / "quickstart" / "structured" / "customers.csv").write_text("id\n1\n2\n")
    (root / "quickstart" / "documents").mkdir()
    (root / "quickstart" / "documents" / "규정.md").write_text("# 규정\n본문\n", encoding="utf-8")
    monkeypatch.setenv("AUTOONTOLOGY_SAMPLES_DIR", str(root))
    settings = Settings.from_env()
    c = TestClient(web.create_app(settings, runs=RunManager(settings, jobs=FakeJobs().table)))
    resp = c.post(f"{BETA}/samples/load", json={"set": "quickstart"})
    assert resp.status_code == 200, resp.text
    assert [s["name"] for s in c.get(f"{BETA}/sources").json()["sources"]] == [
        "quick",
        "quick-docs",
    ]
    assert c.get(f"{API}/sources").json()["sources"] == []
    assert c.get(f"{BETA}/samples").json()[0]["registered"] is True
    assert c.get(f"{API}/samples").json()[0]["registered"] is False


def test_slug_rename_moves_exports_and_keeps_tokens_and_decisions(web_env):
    c, settings, store = web_env["c"], web_env["settings"], web_env["store"]
    beta = store.project("beta")
    (Path(settings.exports_dir) / "beta" / "r0001").mkdir(parents=True)
    (Path(settings.exports_dir) / "beta" / "r0001" / "x.md").write_text("x", encoding="utf-8")
    tid = c.post(f"{BETA}/agent/tokens", json={"name": "a"}).json()["record"]["id"]
    beta.decisions().put(Decision("alignment", "concept-x", "reject", {"relation": "none"}))
    # an exports folder of the new name already there: refused, nothing changes
    (Path(settings.exports_dir) / "taken").mkdir()
    store.create_project("other")
    clash = c.patch(BETA, json={"slug": "other"})
    assert clash.status_code == 409 and clash.json()["error"] == "project_exists"
    clash = c.patch(BETA, json={"slug": "taken"})
    assert clash.status_code == 409 and clash.json()["error"] == "exports_conflict"
    assert c.patch(BETA, json={"slug": "Bad"}).json()["error"] == "invalid_slug"
    assert c.patch(BETA, json={"name": "  "}).status_code == 400
    resp = c.patch(BETA, json={"slug": "gamma", "name": "Gamma"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["slug"], resp.json()["name"]) == ("gamma", "Gamma")
    assert not (Path(settings.exports_dir) / "beta").exists()
    assert (Path(settings.exports_dir) / "gamma" / "r0001" / "x.md").is_file()
    gamma = f"{API}/projects/gamma"
    assert c.get(f"{BETA}/sources").json()["error"] == "unknown_project"
    assert [e["dir"] for e in c.get(f"{gamma}/exports").json()["exports"]] == ["r0001"]
    assert [t["id"] for t in c.get(f"{gamma}/agent/tokens").json()["tokens"]] == [tid]
    assert [d.subject_key for d in store.project("gamma").decisions().list()] == ["concept-x"]
    assert store.project("gamma").id == beta.id


def test_review_decisions_are_per_project(ops):  # noqa: F811
    settings = Settings.from_env()
    c = TestClient(web.create_app(settings, runs=RunManager(settings, jobs=dict)))
    assert c.post(f"{API}/projects", json={"slug": "beta"}).status_code == 201
    item = f"al:{_rows(ops)['수납'].concept_id}"
    resp = c.post(f"{API}/review/{item}/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    assert (
        c.get(f"{API}/projects/default/review/pending-count").json()
        == c.get(f"{API}/review/pending-count").json()
    )
    # beta has no snapshot, items or decisions; the default project's item is unknown there
    assert c.get(f"{BETA}/review").json()["revision"] is None
    assert c.get(f"{BETA}/review", params={"status": "decided"}).json()["items"] == []
    assert c.get(f"{BETA}/review/pending-count").json()["pending"] == 0
    assert c.post(f"{BETA}/review/{item}/decision", json={"decision": "reject"}).status_code == 404
    assert c.delete(f"{BETA}/review/{item}/decision").status_code == 404
    assert ops["store"].project("beta").decisions().list() == []
    decided = c.get(f"{API}/review", params={"status": "decided"}).json()["items"]
    assert [i["id"] for i in decided] == [item]


def test_ingest_delete_takes_a_project(web_env):
    c = web_env["c"]
    resp = c.post("/ingest/delete", params={"database_name": "x", "project": "nope"})
    assert resp.status_code == 404


# --- CLI ---------------------------------------------------------------------------------------


def _down(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    return httpx.Client(base_url=settings.service_url, transport=httpx.MockTransport(refuse))


@pytest.fixture
def cli_env(isolated_env, monkeypatch):
    monkeypatch.setattr(cli_ops, "http_client", _down)
    monkeypatch.delenv("AUTOONTOLOGY_PROJECT", raising=False)
    settings = Settings.from_env()
    store = Store.for_home(settings.home, default_project_slug=settings.workspace)
    return {"settings": settings, "store": store}


def _ok(args, **kw):
    res = runner.invoke(cli_app, args, **kw)
    assert res.exit_code == 0, res.output
    return res


def test_api_path_routes_project_calls():
    assert cli_ops.api_path("/runs", None) == "/api/runs"
    assert cli_ops.api_path("/runs", "beta") == "/api/projects/beta/runs"
    for path in ("/settings", "/settings/test", "/status", "/providers/test", "/projects"):
        assert cli_ops.api_path(path, "beta") == f"/api{path}"


def test_cli_project_commands(cli_env):
    created = json.loads(
        _ok(["project", "create", "beta", "--name", "Beta", "--description", "d", "--json"]).output
    )
    assert created["slug"] == "beta" and created["name"] == "Beta"
    assert "created project gamma" in _ok(["project", "create", "gamma"]).output
    dup = runner.invoke(cli_app, ["project", "create", "beta", "--json"])
    assert dup.exit_code == 1 and json.loads(dup.stdout)["error"] == "project_exists"
    out = _ok(["project", "list"]).output
    assert "* default" in out and "beta" in out and "gamma" in out
    listed = json.loads(_ok(["project", "list", "--json"]).output)
    assert [p["slug"] for p in listed["projects"]] == ["default", "beta", "gamma"]
    assert "beta  (Beta," in _ok(["project", "show", "beta"]).output
    assert json.loads(_ok(["-p", "beta", "project", "show", "--json"]).output)["slug"] == "beta"
    assert json.loads(_ok(["project", "show", "--json"]).output)["slug"] == "default"
    renamed = json.loads(_ok(["project", "rename", "gamma", "--slug", "delta", "--json"]).output)
    assert renamed["slug"] == "delta"
    assert runner.invoke(cli_app, ["project", "rename", "delta"]).exit_code == 2
    assert "archived project delta" in _ok(["project", "archive", "delta"]).output
    assert json.loads(_ok(["project", "list", "--no-archived", "--json"]).output)["projects"][-1][
        "slug"
    ] == ("beta")
    # an archived project refuses writes from the CLI too
    refused = runner.invoke(cli_app, ["-p", "delta", "source", "add", "/tmp", "--name", "x"])
    assert refused.exit_code == 2 and "archived" in refused.output
    assert "writable again" in _ok(["project", "unarchive", "delta"]).output
    bad = runner.invoke(cli_app, ["project", "archive", "default", "--json"])
    assert bad.exit_code == 1 and json.loads(bad.stdout)["error"] == "default_project"


def test_cli_project_option_scopes_store_and_api_calls(cli_env, isolated_env, monkeypatch):
    store = cli_env["store"]
    beta = store.create_project("beta", name="Beta")
    _snapshot(store.project(), "default")
    _snapshot(store.project(), "default")
    _snapshot(beta, "Beta")
    docs = _folder(isolated_env, "d", {"a.md": "# a\n"})
    _ok(["-p", "beta", "source", "add", str(docs), "--name", "docs", "--kind", "documents"])
    assert [s.name for s in beta.list_sources()] == ["docs"]
    assert store.project().list_sources() == []
    assert "docs" in _ok(["--project", "beta", "source", "list"]).output
    assert "docs" not in _ok(["source", "list"]).output
    # LocalApi calls: the canonical routes of the project
    revs = json.loads(_ok(["-p", "beta", "ontology", "revisions", "--json"]).output)
    assert [r["revision"] for r in revs["revisions"]] == [1]
    revs = json.loads(_ok(["ontology", "revisions", "--json"]).output)
    assert [r["revision"] for r in revs["revisions"]] == [2, 1]
    monkeypatch.setenv("AUTOONTOLOGY_PROJECT", "beta")
    assert json.loads(_ok(["ontology", "--json"]).output)["workspace"] == "Beta"
    monkeypatch.delenv("AUTOONTOLOGY_PROJECT")
    tok = json.loads(_ok(["-p", "beta", "agent", "tokens", "create", "a", "--json"]).output)
    assert json.loads(_ok(["agent", "tokens", "list", "--json"]).output)["tokens"] == []
    listed = json.loads(_ok(["-p", "beta", "agent", "tokens", "list", "--json"]).output)
    assert [t["id"] for t in listed["tokens"]] == [tok["record"]["id"]]
    # an unknown project: exit 2 for store commands, the API error for API ones
    gone = runner.invoke(cli_app, ["-p", "nope", "source", "list"])
    assert gone.exit_code == 2 and "unknown project 'nope'" in gone.output
    gone = runner.invoke(cli_app, ["-p", "nope", "ontology", "revisions", "--json"])
    assert gone.exit_code == 1 and json.loads(gone.stdout)["error"] == "unknown_project"


def test_cli_project_option_routes_service_calls(cli_env, monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(404, json={"error": "unknown_run", "detail": "unknown run"})

    monkeypatch.setattr(
        cli_ops,
        "http_client",
        lambda s: httpx.Client(base_url=s.service_url, transport=httpx.MockTransport(handler)),
    )
    run_id = "20261124T000000Z-abcdef"
    runner.invoke(cli_app, ["-p", "beta", "runs", "show", run_id])
    runner.invoke(cli_app, ["runs", "show", run_id])
    runner.invoke(cli_app, ["-p", "beta", "runs", "cancel", run_id])
    assert [r.url.path for r in seen] == [
        f"/api/projects/beta/runs/{run_id}",
        f"/api/runs/{run_id}",
        f"/api/projects/beta/runs/{run_id}/cancel",
    ]
