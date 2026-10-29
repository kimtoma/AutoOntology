# SPDX-License-Identifier: MIT
"""Ontology operations API: review inbox and decisions, the apply run, revisions and diff,
source freshness -- over a fake build (``tests/test_decisions.py``'s ``ops`` fixture)."""

import pytest
from fastapi.testclient import TestClient

from autoontology.config import Settings
from autoontology.engine.apply import run_apply
from autoontology.web import app as web
from autoontology.web.runs import RunManager
from tests.test_align import ClientWriter
from tests.test_build import env  # noqa: F401 - the build fixture
from tests.test_decisions import OpsJudge, _no_models, _rows, ops  # noqa: F401

API = "/api"
TS_HOST = "box.example.ts.net"


def _client(ops, monkeypatch=None, hosts=""):  # noqa: F811
    if monkeypatch is not None:
        monkeypatch.setenv("AUTOONTOLOGY_TAILSCALE_HOSTS", hosts)
    settings = Settings.from_env()

    def jobs():
        def apply(store, settings_, **kw):
            return run_apply(
                store, settings_, client=ops["client"], writer=ClientWriter(ops["client"]), **kw
            )

        return {"apply": apply}

    runs = RunManager(settings, jobs=jobs)
    return TestClient(web.create_app(settings, runs=runs)), runs


@pytest.fixture
def api(ops):  # noqa: F811
    client, runs = _client(ops)
    ops["api"], ops["runs"] = client, runs
    return ops


def _items(c, **params):
    resp = c.get(f"{API}/review", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_review_lists_every_category_with_stable_ids(api):
    c = api["api"]
    rows = _rows(api)
    body = _items(c)
    assert body["revision"] == 1 and body["status"] == "pending"
    ids = {i["id"]: i for i in body["items"]}
    proposal = ids[f"al:{rows['수납'].concept_id}"]
    assert proposal["category"] == "proposals" and proposal["relation"] == "same"
    assert proposal["term"] == {"id": "t-customer", "node": "term-t-customer", "name": "Customer"}
    assert proposal["concept"]["name"] == "수납" and proposal["concept"]["source"] == "hanbit-docs"
    assert proposal["reason"] and proposal["suggested_action"] == "accept"
    assert proposal["decision"] is None and proposal["evidence"]
    assert {"quote", "document", "page", "verified"} <= set(proposal["evidence"][0])
    axiom = ids[f"ax:{rows['청구서'].concept_id}"]
    assert axiom["relation"] == "related" and axiom["confidence"] == 0.7
    assert body["counts"]["proposals"] == 1 and body["counts"]["axioms"] == 1
    assert body["counts"]["total"] == len(body["items"]) == body["total"]
    # stable across calls
    assert [i["id"] for i in _items(c)["items"]] == [i["id"] for i in body["items"]]
    only = _items(c, category="axioms")
    assert [i["category"] for i in only["items"]] == ["axioms"]
    assert c.get(f"{API}/review", params={"category": "nope"}).status_code == 400
    assert c.get(f"{API}/review", params={"status": "nope"}).status_code == 400


def test_review_pagination(api):
    c = api["api"]
    full = _items(c, size=100)
    first, second = _items(c, size=1, page=1), _items(c, size=1, page=2)
    assert first["pages"] == full["total"] and len(first["items"]) == 1
    assert [first["items"][0]["id"], second["items"][0]["id"]] == [
        i["id"] for i in full["items"][:2]
    ]
    assert _items(c, size=1, page=99)["items"] == []
    assert c.get(f"{API}/review", params={"size": 101}).status_code == 400


def test_decisions_upsert_delete_and_pending_count(api):
    c = api["api"]
    rows = _rows(api)
    item = f"al:{rows['수납'].concept_id}"
    resp = c.post(f"{API}/review/{item}/decision", json={"decision": "accept", "note": "맞음"})
    assert resp.status_code == 200, resp.text
    d = resp.json()["decision"]
    assert (d["decision"], d["relation"], d["decided_by"], d["note"]) == (
        "accept",
        "same",
        "local",
        "맞음",
    )
    assert d["payload"]["term_id"] == "t-customer" and d["applied_revision"] is None
    count = c.get(f"{API}/review/pending-count").json()
    assert count["unapplied"] == 1 and count["counts"]["proposals"] == 0
    # one decision per subject: the axiom view of the same concept is the same subject
    again = c.post(f"{API}/review/{item}/decision", json={"decision": "reject"})
    assert again.json()["decision"]["id"] == d["id"]
    assert again.json()["decision"]["relation"] == "none"
    decided = _items(c, status="decided")["items"]
    assert [i["id"] for i in decided] == [item]
    assert c.delete(f"{API}/review/{item}/decision").json() == {"removed": item}
    assert c.delete(f"{API}/review/{item}/decision").status_code == 404
    assert c.get(f"{API}/review/pending-count").json()["unapplied"] == 0


def test_decision_validation(api):
    c = api["api"]
    rows = _rows(api)
    ax = f"ax:{rows['청구서'].concept_id}"

    def post(item, body):
        return c.post(f"{API}/review/{item}/decision", json=body)

    assert post(ax, {"decision": "acknowledge"}).json()["error"] == "invalid_decision"
    assert post(ax, {"decision": "accept", "relation": "none"}).status_code == 400
    assert post(ax, {"decision": "accept", "term_id": "t-nope"}).json()["error"] == "invalid_term"
    assert post(ax, {"decision": "accept", "extra": 1}).status_code == 400
    assert post("al:nope", {"decision": "accept"}).status_code == 404
    assert post("zz:1", {"decision": "accept"}).json()["error"] == "invalid_item_id"
    assert post("src:hanbit", {"decision": "acknowledge"}).json()["error"] == "not_decidable"
    ok = post(ax, {"decision": "accept", "relation": "broader"})
    assert ok.status_code == 200 and ok.json()["decision"]["relation"] == "broader"


def test_review_writes_are_guarded(api):
    c = api["api"]
    rows = _rows(api)
    item = f"al:{rows['수납'].concept_id}"
    evil = c.post(
        f"{API}/review/{item}/decision",
        json={"decision": "reject"},
        headers={"origin": "http://evil.example"},
    )
    assert evil.status_code == 403 and evil.json()["error"] == "cross_origin"
    foreign = c.get(f"{API}/review", headers={"host": "evil.example"})
    assert foreign.status_code == 403
    gone = c.delete(f"{API}/review/{item}/decision", headers={"origin": "http://evil.example"})
    assert gone.status_code == 403
    run = c.post(f"{API}/runs", json={"kind": "apply"}, headers={"origin": "http://evil.example"})
    assert run.status_code == 403


def test_decided_by_is_the_tailnet_login(ops, monkeypatch):  # noqa: F811
    c, _ = _client(ops, monkeypatch, hosts=TS_HOST)
    rows = _rows(ops)
    item = f"al:{rows['수납'].concept_id}"
    headers = {"host": TS_HOST, "tailscale-user-login": "Alice@Example.com"}
    resp = c.post(f"{API}/review/{item}/decision", json={"decision": "reject"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"]["decided_by"] == "alice@example.com"
    # the header alone on localhost is not trusted
    local = c.post(
        f"{API}/review/{item}/decision",
        json={"decision": "reject"},
        headers={"tailscale-user-login": "mallory@example.com"},
    )
    assert local.json()["decision"]["decided_by"] == "local"
    assert (
        c.post(
            f"{API}/review/{item}/decision", json={"decision": "reject"}, headers={"host": TS_HOST}
        ).status_code
        == 403
    )


def test_apply_run_from_the_api_and_the_diff(api, monkeypatch):
    c, runs = api["api"], api["runs"]
    rows = _rows(api)
    c.post(f"{API}/review/al:{rows['수납'].concept_id}/decision", json={"decision": "accept"})
    _no_models(monkeypatch)
    bad = c.post(f"{API}/runs", json={"kind": "apply", "resume": True})
    assert bad.status_code == 400
    resp = c.post(f"{API}/runs", json={"kind": "apply"})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    runs.join(run_id, 10)
    run = c.get(f"{API}/runs/{run_id}").json()
    assert run["kind"] == "apply" and run["status"] == "succeeded", run
    assert [s["name"] for s in run["steps"]] == ["decisions", "merge", "snapshot"]
    assert run["params"]["applied_revision"] == 2
    revisions = c.get(f"{API}/ontology/revisions").json()["revisions"]
    assert [(r["revision"], r["run_kind"]) for r in revisions] == [(2, "apply"), (1, "build")]
    assert "by_source" not in revisions[0]["counts"]
    diff = c.get(f"{API}/ontology/diff").json()
    assert (diff["from"], diff["to"]) == (1, 2)
    removed = [n["name"] for n in diff["classes"]["removed"]]
    assert removed == ["수납"] and diff["classes"]["totals"]["removed"] == 1
    changed = {n["name"]: n["fields"] for n in diff["classes"]["changed"]}
    assert "synonyms" in changed["Customer"]
    assert [m["concept_name"] for m in diff["merges"]["added"]] == ["수납"]
    assert diff["counts_delta"]["merged_concepts"] == 1
    back = c.get(f"{API}/ontology/diff", params={"from": 2, "to": 1}).json()
    assert [n["name"] for n in back["classes"]["added"]] == ["수납"]
    assert c.get(f"{API}/ontology/diff", params={"to": 9}).status_code == 404
    # the decision is applied: the inbox shows it decided, nothing left to apply
    count = c.get(f"{API}/review/pending-count").json()
    assert count["unapplied"] == 0
    decided = _items(c, status="decided")["items"]
    assert decided[0]["decision"]["applied_revision"] == 2
    # a second apply while one is active is refused
    with api["store"].lease(["*workspace*"], "build"):
        busy = c.post(f"{API}/runs", json={"kind": "apply"})
    assert busy.status_code == 409


def test_apply_needs_a_snapshot_api(isolated_env):
    settings = Settings.from_env()
    c = TestClient(web.create_app(settings, runs=RunManager(settings)))
    resp = c.post(f"{API}/runs", json={"kind": "apply"})
    assert resp.status_code == 404 and resp.json()["error"] == "no_snapshot"
    assert c.get(f"{API}/review").json()["revision"] is None
    assert c.get(f"{API}/ontology/revisions").json() == {"revisions": []}


# --- CLI parity ---------------------------------------------------------------------------


def test_cli_review_and_apply(ops, monkeypatch):  # noqa: F811
    import json

    from typer.testing import CliRunner

    from autoontology import cli
    from autoontology.engine import align as align_job
    from autoontology.engine import apply as apply_job

    runner = CliRunner()
    rows = _rows(ops)
    res = runner.invoke(cli.app, ["review", "list"])
    assert res.exit_code == 0, res.output
    assert "proposals=1" in res.output and f"al:{rows['수납'].concept_id}" in res.output
    listed = json.loads(runner.invoke(cli.app, ["review", "list", "--json"]).output)
    assert listed["counts"]["axioms"] == 1
    item = f"al:{rows['수납'].concept_id}"
    res = runner.invoke(cli.app, ["review", "accept", item, "--note", "확인"])
    assert res.exit_code == 0 and "accept (same)" in res.output, res.output
    res = runner.invoke(cli.app, ["review", "reject", f"ax:{rows['청구서'].concept_id}"])
    assert res.exit_code == 0 and "reject (none)" in res.output
    bad = runner.invoke(cli.app, ["review", "acknowledge", f"ax:{rows['청구서'].concept_id}"])
    assert bad.exit_code == 1 and "invalid_decision" in bad.output
    assert runner.invoke(cli.app, ["review", "list", "--category", "x"]).exit_code == 2
    decided = json.loads(
        runner.invoke(cli.app, ["review", "list", "--status", "decided", "--json"]).output
    )
    assert {i["id"] for i in decided["items"]} == {item, f"ax:{rows['청구서'].concept_id}"}
    # apply, with the fake upstream and no model
    _no_models(monkeypatch)
    monkeypatch.setattr(apply_job, "BackendClient", lambda url: ops["client"])
    monkeypatch.setattr(align_job, "UpstreamTermWriter", lambda s, c: ClientWriter(c))
    res = runner.invoke(cli.app, ["apply"])
    assert res.exit_code == 0, res.output
    assert "revision 2" in res.output and "2 decision(s) applied" in res.output
    res = runner.invoke(cli.app, ["apply"])
    assert res.exit_code == 0 and "nothing to apply (revision 2)" in res.output, res.output
    res = runner.invoke(cli.app, ["apply", "--force"])
    assert res.exit_code == 0 and "revision 3" in res.output, res.output
    res = runner.invoke(cli.app, ["review", "undo", item])
    assert res.exit_code == 0 and "decision removed" in res.output
    assert runner.invoke(cli.app, ["review", "undo", item]).exit_code == 1


def test_diff_of_models(monkeypatch):
    from autoontology.ontology import model as merged
    from autoontology.store.alignments import AlignmentRecord
    from autoontology.web import revisions
    from tests import test_ossie_okf as fx

    docs = fx.documents()
    old = merged.build("ws", structured=[fx.structured()], documents=[docs])
    accepted = merged.DocumentsInput(
        docs.source,
        docs.documents,
        [c for c in docs.concepts if c.name != "승인 기준"],
        docs.relations,
        docs.evidence,
        [
            AlignmentRecord(**{**a.__dict__, "accepted": True}) if a.concept_id == "c3" else a
            for a in docs.alignments
        ],
    )
    new = merged.build("ws", structured=[fx.structured()], documents=[accepted])
    d = revisions.diff(old, new, old_sources=[{"name": "a", "included": True}], new_sources=[])
    assert [n["name"] for n in d["classes"]["removed"]] == ["승인 기준"]
    assert d["classes"]["removed"][0]["type"] == "individual"
    assert d["axioms"]["changed"][0]["fields"] == ["accepted"]
    assert d["axioms"]["changed"][0]["object"] == "term-t-i"
    assert d["sources"] == {"added": [], "removed": ["a"]}
    assert d["counts_delta"]["individuals"] == -1
    everything = revisions.diff(None, new)
    assert everything["classes"]["totals"]["added"] == len(new.classes) + len(new.individuals)
    monkeypatch.setattr(revisions, "DIFF_CAP", 2)
    capped = revisions.diff(None, new)
    assert len(capped["classes"]["added"]) == 2
    assert capped["classes"]["totals"]["added"] == everything["classes"]["totals"]["added"]
    assert revisions.diff(new, new)["classes"]["totals"] == {"added": 0, "removed": 0, "changed": 0}


# --- fix wave -----------------------------------------------------------------------------


def test_badge_and_inbox_never_compute_freshness(api, monkeypatch):
    from autoontology.web import freshness as fr

    calls = []
    real = fr.freshness

    def counted(store, settings):
        calls.append(1)
        return real(store, settings)

    monkeypatch.setattr(fr, "freshness", counted)
    c = api["api"]
    assert c.get(f"{API}/review/pending-count").status_code == 200
    assert c.get(f"{API}/review").status_code == 200
    assert c.get(f"{API}/review", params={"category": "axioms"}).status_code == 200
    assert calls == []
    c.get(f"{API}/review", params={"category": "stale_sources"})
    assert len(calls) == 1
    c.get(f"{API}/review", params={"sources": 1})  # cached for a minute
    c.get(f"{API}/review/pending-count")
    assert len(calls) == 1


def test_freshness_is_computed_outside_the_lock(ops, monkeypatch):  # noqa: F811
    import threading

    from autoontology.web import freshness as fr
    from autoontology.web.api import Api

    api_ = Api(Settings.from_env())
    entered, release = threading.Event(), threading.Event()

    def slow(store, settings):
        entered.set()
        release.wait(5)
        return [{"name": "x", "stale": True}]

    monkeypatch.setattr(fr, "freshness", slow)
    worker = threading.Thread(target=lambda: api_.freshness(refresh=True))
    worker.start()
    assert entered.wait(5)
    # the cache can be read (and the badge served) while the computation runs
    assert api_.cached_freshness() is None
    release.set()
    worker.join(5)
    assert api_.cached_freshness()[0] == [{"name": "x", "stale": True}]


def test_apply_with_nothing_pending_writes_no_revision(api):
    c, runs = api["api"], api["runs"]

    def apply(body):
        resp = c.post(f"{API}/runs", json=body)
        assert resp.status_code == 202, resp.text
        runs.join(resp.json()["run_id"], 10)
        return c.get(f"{API}/runs/{resp.json()['run_id']}").json()

    run = apply({"kind": "apply"})
    assert run["status"] == "succeeded" and run["params"]["applied_revision"] == 1
    steps = {s["name"]: s for s in run["steps"]}
    assert steps["snapshot"]["status"] == "skipped"
    assert steps["snapshot"]["detail"]["reason"] == "no pending decisions"
    assert c.get(f"{API}/ontology/revisions").json()["revisions"][0]["revision"] == 1
    forced = apply({"kind": "apply", "force": True})
    assert forced["params"]["applied_revision"] == 2
    assert c.post(f"{API}/runs", json={"kind": "build", "force": True}).status_code == 400


def test_rejecting_an_automatic_fold_from_the_inbox(api):
    c, runs = api["api"], api["runs"]
    rows = _rows(api)
    assert "fold:" not in str([i["id"] for i in _items(c, size=100)["items"]])  # on request
    folds = _items(c, category="folds")
    ids = {i["concept"]["name"]: i["id"] for i in folds["items"]}
    assert ids["계약"] == f"fold:{rows['계약'].concept_id}" and folds["counts"]["folds"] == 2
    assert folds["counts"]["total"] == _items(c)["counts"]["total"]  # folds not in the badge
    resp = c.post(f"{API}/review/{ids['계약']}/decision", json={"decision": "reject"})
    assert resp.status_code == 200 and resp.json()["decision"]["relation"] == "none"
    assert resp.json()["decision"]["payload"]["category"] == "folds"
    run_id = c.post(f"{API}/runs", json={"kind": "apply"}).json()["run_id"]
    runs.join(run_id, 10)
    assert "계약" not in api["client"].terms["t-contract"]["synonyms"]
    diff = c.get(f"{API}/ontology/diff").json()
    assert [n["name"] for n in diff["classes"]["added"]] == ["계약"]
    assert [m["concept_name"] for m in diff["merges"]["removed"]] == ["계약"]
    decided = _items(c, status="decided")["items"]
    assert [i["id"] for i in decided] == [ids["계약"]]


def test_an_undone_alias_decision_is_pending_again(api):
    c, runs = api["api"], api["runs"]
    rows = _rows(api)
    # a column name on Customer: the fold of 수납 keeps it off the Term (a rejected alias)
    api["client"].attrs["t-customer"].append(
        {
            "id": "a9",
            "name": "수납",
            "source_column": "수납",
            "datatype": "text",
            "primary_column": {
                "table_id": "tb-k",
                "table_name": "customers",
                "schema_id": "s1",
                "column_name": "수납",
            },
            "referenced_columns": [],
        }
    )

    def apply():
        run_id = c.post(f"{API}/runs", json={"kind": "apply"}).json()["run_id"]
        runs.join(run_id, 10)

    c.post(
        f"{API}/review/al:{rows['수납'].concept_id}/decision",
        json={"decision": "accept"},
    )
    apply()
    item = "alias:t-customer|수납"
    aliases = _items(c, category="aliases")["items"]
    assert [i["id"] for i in aliases] == [item]
    c.post(f"{API}/review/{item}/decision", json={"decision": "accept"})
    apply()
    assert _items(c, category="aliases")["items"] == []
    assert c.get(f"{API}/review/pending-count").json()["unapplied"] == 0
    c.delete(f"{API}/review/{item}/decision")
    again = _items(c, category="aliases")
    assert [i["id"] for i in again["items"]] == [item] and again["items"][0]["decision"] is None
    assert again["unapplied"] == 1
    apply()
    after = _items(c, category="aliases")
    assert [i["id"] for i in after["items"]] == [item] and after["unapplied"] == 0


def test_diff_default_from_is_the_revision_before_to(api):
    c = api["api"]
    for _ in range(2):
        api["runs"].join(
            c.post(f"{API}/runs", json={"kind": "apply", "force": True}).json()["run_id"], 10
        )
    assert c.get(f"{API}/ontology/diff", params={"to": 2}).json()["from"] == 1
    assert c.get(f"{API}/ontology/diff", params={"to": 1}).json()["from"] is None
    assert api["store"].previous_revision(3) == 2


def test_concurrent_freshness_requests_share_one_computation(ops, monkeypatch):  # noqa: F811
    import threading

    from autoontology.web import freshness as fr
    from autoontology.web.api import Api

    api_ = Api(Settings.from_env())
    calls, entered, release = [], threading.Event(), threading.Event()

    def slow(store, settings):
        calls.append(1)
        entered.set()
        release.wait(5)
        return [{"name": "x", "stale": False}]

    monkeypatch.setattr(fr, "freshness", slow)
    results = []
    first = threading.Thread(target=lambda: results.append(api_.freshness(refresh=True)))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=lambda: results.append(api_.freshness()))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert len(calls) == 1 and [r[0] for r in results] == [[{"name": "x", "stale": False}]] * 2


def test_pending_work_reads_only_the_review_rows(ops):  # noqa: F811
    store = ops["store"]
    rows = _rows(ops)
    assert {r.concept_name for r in ops["aligns"].review_rows()} == {"계약", "고객"}  # applied
    with store._conn() as conn:
        plan = " ".join(
            str(r[3])
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM alignments "
                "WHERE judged_json != '{}' OR applied_json != '{}'"
            )
        )
    assert "alignments_review_idx" in plan
    assert rows
