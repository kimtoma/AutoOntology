# SPDX-License-Identifier: MIT
"""The ``logic`` run (``ontology/logic/extract.py``), the review inbox's ``logic``
category (``ontology/logic/candidates.py``) and ``onto logic extract|candidates|accept|
reject``: a golden run over the quickstart 청구수납규정 (제4조 지급기한, 제6조 연체 판정,
제7조 독촉 절차) with a deterministic fake chat model -- a valid function, one that needs a
column the data lacks (지급기한), an action and a workflow that validate together, junk
that is dropped and counted -- then accept, accept refused, edit-then-accept, reject, undo,
dedupe on a re-run, the run's steps and log, and the roles."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontology.config import Settings
from ontology.engine.jobs import JobError
from ontology.logic import candidates, extract, functions
from ontology.store import accounts as acc
from ontology.store.accounts import Accounts
from ontology.store.db import Store
from ontology.store.documents import ConceptRecord, DocumentStore, EvidenceRecord
from ontology.unstructured import prompts
from ontology.web import app as web
from ontology.web.runs import RunManager, run_log_path
from tests import logic_fixtures as fx
from tests.test_logic import TODAY

API = "/api"
DOC = Path(__file__).resolve().parents[1] / "samples/quickstart/documents/청구수납규정.md"
TITLE = "한빛상사 청구수납규정"

# --- the documents source: 청구수납규정 as chunks, its rule/process concepts --------------


@dataclass
class _Chunk:
    index: int
    section_path: str
    page: int | None
    kind: str
    text: str
    tokens: int


def _sections(text: str) -> list[tuple[str, str]]:
    """``(heading, body)`` of every ``###`` article of the document."""
    out = []
    for part in re.split(r"\n(?=### )", text):
        if part.startswith("### "):
            heading, _, body = part.partition("\n")
            out.append((heading[4:].strip(), body.strip()))
    return out


CONCEPTS = [
    # (name, kind, definition, article, evidence quote -- verbatim from the article)
    (
        "지급기한",
        "rule",
        "청구서의 지급기한은 청구일부터 30일이다.",
        "제4조(지급기한)",
        "청구서의 지급기한은 청구일부터 30일로 한다.",
    ),
    (
        "연체 판정",
        "rule",
        "지급기한이 지나도록 완납되지 않은 청구서는 연체이며 연체일수는 지급기한 다음 날부터 센다.",
        "제6조(연체 판정)",
        "연체일수는 지급기한 다음 날부터 계산한다.",
    ),
    (
        "독촉 절차",
        "process",
        "연체일수에 따라 유선 안내, 서면 독촉장, 본부장 보고, 계약 해지 검토 순으로 독촉한다.",
        "제7조(독촉 절차)",
        "| 30일 이상 | 서면 독촉장 발송 |",
    ),
]


def seed_documents(view) -> str:
    """A documents source with 청구수납규정 read and its concepts consolidated (the
    fixture's build run finished)."""
    for run in view.store.active_runs():
        view.update_run(run.id, status="succeeded")
    source = view.add_source(name="docs", kind="documents", locator="/nonexistent/docs")
    docs = DocumentStore(view)
    sections = _sections(DOC.read_text("utf-8"))
    chunks = [
        _Chunk(i, heading, None, "text", f"{heading}\n{body}", len(body))
        for i, (heading, body) in enumerate(sections)
    ]
    doc, _ = docs.replace_document(
        source_id=source.id,
        path=str(DOC),
        title=TITLE,
        mime="text/markdown",
        engine="markdown",
        sha256="0" * 64,
        size=DOC.stat().st_size,
        pages=None,
        chunks=chunks,
    )
    by_heading = {c.section_path: c.id for c in docs.chunks_for_document(doc.id)}
    records = [
        ConceptRecord(
            name=name,
            kind=kind,
            definition=definition,
            evidence=[EvidenceRecord(by_heading[article], quote, True)],
        )
        for name, kind, definition, article, quote in CONCEPTS
    ]
    records.append(
        ConceptRecord(
            name="연체일수",
            kind="metric",
            definition="지급기한 다음 날부터 센 연체 기간(일).",
            evidence=[EvidenceRecord(by_heading["제6조(연체 판정)"], "연체일수", True)],
        )
    )
    docs.replace_ontology(source.id, records, [])
    return source.id


# --- the fake chat model --------------------------------------------------------------

FN_OVERDUE = {
    "kind": "function",
    "name": "연체일수",
    "label": "연체일수",
    "description": "지급기한(청구일 + 30일) 다음 날부터 오늘까지의 일수.",
    "object_type": "청구",
    "returns": "number",
    "expr": 'if(수납상태 == "완납", 0, max(today() - (청구일 + 30), 0))',
}
FN_DUE = {
    "kind": "function",
    "name": "지급기한경과",
    "label": "지급기한 경과",
    "object_type": "청구",
    "returns": "bool",
    "expr": '수납상태 != "완납" and today() > 지급기한',
}
FN_DUE_EDITED = {**FN_DUE, "expr": '수납상태 != "완납" and today() > add_days(청구일, 30)'}
FN_UNPAID = {
    "kind": "function",
    "name": "미수금",
    "label": "미수금",
    "object_type": "청구",
    "returns": "number",
    "expr": "청구금액 - coalesce(수납금액, 0)",
}
ACTION = {
    "kind": "action",
    "name": "독촉단계기록",
    "label": "독촉 단계 기록",
    "object_type": "청구",
    "params": [{"name": "단계", "type": "number", "enum": [1, 2, 3, 4]}],
    "preconditions": [{"expr": '수납상태 != "완납"', "message": "이미 완납된 청구서입니다."}],
    "effects": [{"set": "독촉단계", "value": "param.단계", "operational": True}],
    "approval": {"role": "owner", "when": "param.단계 >= 4"},
}
WORKFLOW = {
    "kind": "workflow",
    "name": "연체독촉",
    "label": "연체 청구서 독촉",
    "trigger": {"schedule": "daily 02:00"},
    "for_each": {"object_type": "청구", "where": "연체일수() > 0"},
    "steps": [
        {
            "if": "연체일수() >= 30 and coalesce(독촉단계, 0) < 2",
            "then": [{"action": "독촉단계기록", "params": {"단계": "2"}}],
        }
    ],
}
QUOTE_6 = "연체일수는 지급기한 다음 날부터 계산한다."
QUOTE_7 = "30일 이상 | 서면 독촉장 발송"


def golden_answer() -> dict:
    return {
        "candidates": [
            {
                "kind": "function",
                "spec": FN_OVERDUE,
                "evidence": [
                    {"document": TITLE, "quote": QUOTE_6},
                    {"document": TITLE, "quote": "청구서의 지급기한은 청구일부터 30일로 한다."},
                ],
                "confidence": 0.9,
                "rationale": "제6조 연체일수, 제4조의 지급기한(청구일 + 30일)으로 계산.",
            },
            {
                "kind": "function",
                "spec": FN_DUE,
                # not in the document: dropped, the candidate stays unverified
                "evidence": [{"document": TITLE, "quote": "지급기한 컬럼으로 연체를 판정한다"}],
                "confidence": 0.6,
                "rationale": "제6조: 지급기한이 지났는지. 데이터에 지급기한 컬럼이 없다.",
            },
            {
                "kind": "action",
                "spec": ACTION,
                "evidence": [{"document": TITLE, "quote": QUOTE_7}],
                "confidence": 0.8,
                "rationale": "제7조 독촉 단계를 운영 속성으로 기록.",
            },
            {
                "kind": "workflow",
                "spec": WORKFLOW,
                "evidence": [{"document": TITLE, "quote": QUOTE_7}],
                "confidence": 0.7,
                "rationale": "제7조: 매일 연체 청구서를 돌며 30일 이상이면 서면 독촉.",
            },
            {
                "kind": "function",
                "spec": FN_UNPAID,
                "evidence": [{"document": TITLE, "quote": "미수금"}],
                "confidence": 0.5,
                "rationale": "제2조 미수금.",
            },
            # junk: dropped and counted, never fails the run
            "just a string",
            {"kind": "macro", "spec": {"name": "x"}},
            {"kind": "function", "spec": "not a mapping"},
            {"kind": "function", "spec": {"kind": "action", "name": "섞임"}},
            {"kind": "function", "spec": {"name": "has space!", "expr": "1"}},
        ]
    }


class FakeLLM:
    """A chat model answering logic calls from a script: the first call answers prose
    (JSON mode retries with the error fed back), then *answer*."""

    def __init__(self, answer=None, *, junk_first: bool = True) -> None:
        self.answer = answer or golden_answer()
        self.prompts: list[str] = []
        self.junk_first = junk_first

    def text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.junk_first and len(self.prompts) == 1:
            return "Sure! Here are some ideas."
        return json.dumps(self.answer, ensure_ascii=False)

    def structured(self, system, human, schema):  # pragma: no cover - JSON mode only
        raise AssertionError("logic calls use JSON mode")


# --- fixtures ---------------------------------------------------------------------------


@pytest.fixture
def env(isolated_env, monkeypatch):
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    fx.install_backend(monkeypatch)
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    view = fx.seed(store)
    seed_documents(view)
    llm = FakeLLM()
    monkeypatch.setattr(extract, "make_backend", lambda s: llm)
    return settings, store, view, llm


def _items(c, status="pending") -> dict[str, dict]:
    body = c.get(f"{API}/review", params={"category": "logic", "status": status, "size": 100})
    assert body.status_code == 200, body.text
    return {i["logic"]["name"]: i for i in body.json()["items"]}


def _audit(store) -> list[tuple[str, str]]:
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT action, target FROM audit_log WHERE action LIKE 'logic.%' ORDER BY id"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


# --- the job ----------------------------------------------------------------------------


def test_golden_run_stores_candidates_with_errors_and_counts(env):
    settings, store, view, llm = env
    result = extract.run_logic(view, settings)
    assert result.status == "succeeded", result.error
    run = view.get_run(result.run_id)
    assert run.kind == "logic" and run.status == "succeeded"
    assert run.params["prompt_version"] == prompts.LOGIC_PROMPT_VERSION
    counts = run.params["counts"]
    assert counts["concepts"] == 3 and counts["calls"] == 1 and counts["failed_calls"] == 0
    assert counts["dropped"] == {
        "malformed": 2,
        "unknown_kind": 1,
        "kind_mismatch": 1,
        "invalid_name": 1,
    }
    assert (counts["candidates"], counts["stored"], counts["ready"]) == (5, 5, 4)
    assert counts["needs_edit"] == 1 and counts["dropped_evidence"] == 2
    steps = {s.name: s for s in view.get_steps(run.id)}
    assert list(steps) == ["collect", "extract", "validate", "store"]
    assert all(s.status == "succeeded" for s in steps.values())
    assert steps["extract"].detail["calls"][0]["attempts"] == 2  # prose, then JSON
    assert steps["validate"].detail == {
        "candidates": 5,
        "ready": 4,
        "needs_edit": 1,
        "duplicates_in_run": 0,
        "over_run_limit": 0,
    }
    # the prompt: the schema (labels, columns with types, links), the concepts, the grammar
    human = llm.prompts[-1]
    assert '"label": "청구"' in human and '"청구일": "date"' in human
    assert "연체 판정" in human and QUOTE_6 in human and '"name": "연체일수"' in human
    assert "days_between" in human and "today()" in human
    by_name = {c.name: c for c in view.logic_candidates().list()}
    assert set(by_name) == {"연체일수", "지급기한경과", "독촉단계기록", "연체독촉", "미수금"}
    ok = by_name["연체일수"]
    assert ok.errors == [] and ok.evidence_verified and ok.confidence == 0.9
    assert [e["quote"] for e in ok.evidence] == [
        QUOTE_6,
        "청구서의 지급기한은 청구일부터 30일로 한다.",
    ]
    assert ok.evidence[0]["document"] == TITLE and ok.evidence[0]["verified"] is True
    assert ok.run_id == run.id and ok.document == TITLE
    due = by_name["지급기한경과"]
    assert due.errors and any("지급기한" in e["message"] for e in due.errors)
    assert due.evidence == [] and not due.evidence_verified and due.confidence == 0.3
    # the workflow validated together with the run's action and function
    assert by_name["연체독촉"].errors == [] and by_name["독촉단계기록"].errors == []
    assert by_name["미수금"].evidence_verified is False  # "미수금" is too short to verify


def test_dedupe_on_rerun_and_replacement_of_undecided_candidates(env):
    settings, store, view, llm = env
    extract.run_logic(view, settings)
    first = {c.name: c.id for c in view.logic_candidates().list()}
    again = extract.run_logic(view, settings)
    assert again.counts["stored"] == 0 and again.counts["duplicate"] == 5
    assert {c.name: c.id for c in view.logic_candidates().list()} == first
    # a different spec for an undecided candidate replaces it
    changed = golden_answer()
    changed["candidates"][0]["spec"] = {**FN_OVERDUE, "expr": "max(today() - 청구일 - 30, 0)"}
    llm.answer, llm.junk_first = changed, False
    third = extract.run_logic(view, settings)
    assert third.counts["stored"] == 1 and third.counts["replaced"] == 1
    now = {c.name: c.id for c in view.logic_candidates().list()}
    assert now["연체일수"] != first["연체일수"] and now["미수금"] == first["미수금"]
    # a decided candidate is never replaced; a spec equal to a definition is skipped
    candidates.decide(view, now["연체일수"], "accept", actor="tester")
    candidates.decide(view, now["미수금"], "reject", actor="tester")
    llm.answer = golden_answer()  # the original 연체일수 spec again: a new candidate
    fourth = extract.run_logic(view, settings)
    assert fourth.counts["stored"] == 1 and fourth.counts["replaced"] == 0
    names = [c.name for c in view.logic_candidates().list()]
    assert names.count("연체일수") == 2 and names.count("미수금") == 1
    llm.answer = changed  # the accepted spec: already defined
    fifth = extract.run_logic(view, settings)
    assert fifth.counts["already_defined"] == 1 and fifth.counts["stored"] == 0


def test_preflight_and_failed_calls(env, monkeypatch):
    settings, store, view, llm = env

    class Down:
        def text(self, prompt):
            raise ConnectionError("model endpoint down")

    monkeypatch.setattr(extract, "make_backend", lambda s: Down())
    failed = extract.run_logic(view, settings)
    assert failed.status == "failed" and "every logic call failed" in failed.error
    run = view.get_run(failed.run_id)
    assert run.status == "failed"
    assert {s.name: s.status for s in view.get_steps(run.id)}["extract"] == "failed"
    assert view.logic_candidates().list() == []
    # nothing to bind to: refused before a run row exists
    for t in view.object_types().list():
        view.object_types().set_status(t.class_local, "proposed")
    with pytest.raises(JobError, match="no active object types"):
        extract.run_logic(view, settings)


def test_no_rule_concepts_is_refused(isolated_env, monkeypatch):
    fx.install_backend(monkeypatch)
    settings = Settings.from_env()
    view = fx.seed(Store.for_home(settings.home))
    for run in view.store.active_runs():
        view.update_run(run.id, status="succeeded")
    with pytest.raises(JobError, match="no rule or process concepts"):
        extract.run_logic(view, settings)


def test_parse_candidate_is_fail_closed():
    batch = extract._Batch("doc", "doc.md", "src", [], [], ["본문 텍스트입니다 아주 길게"])
    assert extract.parse_candidate(None, batch) == (None, "malformed")
    assert extract.parse_candidate({"kind": "function"}, batch) == (None, "malformed")
    huge = {"kind": "function", "spec": {"name": "f", "expr": "1" * 70_000}}
    assert extract.parse_candidate(huge, batch) == (None, "too_large")
    reserved = {"kind": "function", "spec": {"name": "today"}}
    assert extract.parse_candidate(reserved, batch) == (None, "invalid_name")
    c, _ = extract.parse_candidate(
        {"spec": {"kind": "function", "name": "미수 금"}, "confidence": "high"}, batch
    )
    assert c.kind == "function" and c.name == "미수_금" and c.confidence == 0.25
    c, _ = extract.parse_candidate(
        {"kind": "function", "spec": {"name": "f"}, "confidence": 7}, batch
    )
    assert c.confidence == 0.5  # clamped to 1, halved without verified evidence


# --- the review inbox and the API -----------------------------------------------------


@pytest.fixture
def client(env):
    settings, store, view, llm = env
    runs = RunManager(settings)
    with TestClient(web.create_app(settings, runs=runs)) as c:
        yield c, store, view, runs, llm


def test_run_review_accept_reject_undo_through_the_api(client):
    c, store, view, runs, llm = client
    started = c.post(f"{API}/runs", json={"kind": "logic"})
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    runs.join(run_id, 30)
    run = c.get(f"{API}/runs/{run_id}").json()
    assert run["status"] == "succeeded" and run["kind"] == "logic"
    assert [s["name"] for s in run["steps"]] == ["collect", "extract", "validate", "store"]
    log = run_log_path(Settings.from_env(), run_id).read_text("utf-8")
    assert "5 candidate(s) stored (4 ready)" in log and "dropped 2 malformed" in log
    assert c.post(f"{API}/runs", json={"kind": "logic", "force": True}).status_code == 400
    assert c.post(f"{API}/runs", json={"kind": "logic", "resume": True}).status_code == 400
    # the inbox: counts and items
    pending = c.get(f"{API}/review/pending-count").json()
    assert pending["counts"]["logic"] == 5 and pending["pending"] >= 5
    items = _items(c)
    assert set(items) == {"연체일수", "지급기한경과", "독촉단계기록", "연체독촉", "미수금"}
    ok, due = items["연체일수"], items["지급기한경과"]
    assert ok["id"].startswith("lg:lc_") and ok["category"] == "logic"
    assert ok["suggested_action"] == "accept" and ok["confidence"] == 0.9
    assert ok["evidence"][0] == {
        "quote": QUOTE_6,
        "document": TITLE,
        "path": ok["evidence"][0]["path"],
        "page": None,
        "verified": True,
    }
    lg = ok["logic"]
    assert lg["kind"] == "function" and lg["label"] == "연체일수" and lg["object_type"] == "청구"
    assert lg["ready"] and lg["errors"] == [] and lg["evidence_verified"]
    assert "expr: if(수납상태" in lg["spec_yaml"] and lg["spec"]["returns"] == "number"
    assert lg["existing"] is None and lg["run_id"] == run_id and lg["document"] == TITLE
    assert ok["rationale"].startswith("제6조")
    assert due["suggested_action"] == "edit" and not due["logic"]["ready"]
    assert due["logic"]["errors"] and due["evidence"] == []
    listed = c.get(f"{API}/logic/candidates", params={"ready": "false"}).json()["candidates"]
    assert [i["logic"]["name"] for i in listed] == ["지급기한경과"]
    assert len(c.get(f"{API}/logic/candidates").json()["candidates"]) == 5
    assert c.get(f"{API}/logic/candidates", params={"status": "x"}).status_code == 400

    # accept: a draft with origin extracted and the evidence
    resp = c.post(f"{API}/review/{ok['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["logic"]["existing"] == {"status": "draft", "version": 1}
    d = c.get(f"{API}/logic/function/연체일수").json()["definition"]
    assert (d["status"], d["origin"], d["version"]) == ("draft", "extracted", 1)
    assert d["evidence"][0]["quote"] == QUOTE_6
    # accepting an invalid candidate is refused; an edited spec that validates is saved
    resp = c.post(f"{API}/review/{due['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 409 and resp.json()["error"] == "invalid_spec"
    assert any("지급기한" in e["message"] for e in resp.json()["errors"])
    bad_edit = {**FN_DUE, "expr": "today() > 납기"}
    resp = c.post(
        f"{API}/review/{due['id']}/decision", json={"decision": "accept", "spec": bad_edit}
    )
    assert resp.status_code == 409 and resp.json()["errors"]
    action_spec = {**ACTION, "name": "엉뚱"}
    resp = c.post(
        f"{API}/review/{due['id']}/decision", json={"decision": "accept", "spec": action_spec}
    )
    assert resp.status_code == 400 and resp.json()["error"] == "kind_mismatch"
    import yaml

    edited = yaml.safe_dump(FN_DUE_EDITED, allow_unicode=True)
    resp = c.post(f"{API}/review/{due['id']}/decision", json={"decision": "accept", "spec": edited})
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"]["payload"]["edited"] is True
    saved = c.get(f"{API}/logic/function/지급기한경과").json()["definition"]
    assert saved["spec"]["expr"] == FN_DUE_EDITED["expr"] and saved["origin"] == "extracted"
    # the workflow needs its action defined first; then it saves
    resp = c.post(f"{API}/review/{items['연체독촉']['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 409 and resp.json()["error"] == "invalid_spec"
    for name in ("독촉단계기록", "연체독촉"):
        resp = c.post(f"{API}/review/{items[name]['id']}/decision", json={"decision": "accept"})
        assert resp.status_code == 200, resp.text
    assert c.get(f"{API}/logic/workflow/연체독촉").json()["definition"]["status"] == "draft"
    # reject: hidden from pending; spec is for accepting only
    unpaid = items["미수금"]["id"]
    resp = c.post(f"{API}/review/{unpaid}/decision", json={"decision": "reject", "spec": FN_UNPAID})
    assert resp.status_code == 400
    assert c.post(f"{API}/review/{unpaid}/decision", json={"decision": "reject"}).status_code == 200
    redo = c.post(f"{API}/review/{unpaid}/decision", json={"decision": "accept"})
    assert redo.status_code == 409 and redo.json()["error"] == "already_decided"
    assert _items(c) == {}
    assert c.get(f"{API}/review/pending-count").json()["counts"]["logic"] == 0
    assert set(_items(c, "decided")) == set(items)
    # undo: a reject is pending again; an accept retires the draft it made
    assert c.delete(f"{API}/review/{unpaid}/decision").status_code == 200
    assert set(_items(c)) == {"미수금"}
    assert c.delete(f"{API}/review/{ok['id']}/decision").status_code == 200
    assert c.get(f"{API}/logic/function/연체일수").json()["definition"]["status"] == "retired"
    resp = c.post(f"{API}/review/{ok['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 200
    again = c.get(f"{API}/logic/function/연체일수").json()["definition"]
    assert (again["status"], again["version"]) == ("draft", 2)
    # a candidate whose name a live definition holds is refused (it would demote it)
    llm.answer = {"candidates": [{**golden_answer()["candidates"][0], "spec": FN_UNPAID}]}
    llm.answer["candidates"][0]["spec"] = {**FN_OVERDUE, "expr": "1"}
    run2 = c.post(f"{API}/runs", json={"kind": "logic"}).json()["run_id"]
    runs.join(run2, 30)
    fresh = next(i for i in _items(c).values() if i["logic"]["run_id"] == run2)
    assert fresh["logic"]["existing"] == {"status": "draft", "version": 2}
    resp = c.post(f"{API}/review/{fresh['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 409 and resp.json()["error"] == "definition_exists"
    # audit
    assert _audit(store) == [
        ("logic.accept", "function:연체일수"),
        ("logic.accept", "function:지급기한경과"),
        ("logic.accept", "action:독촉단계기록"),
        ("logic.accept", "workflow:연체독촉"),
        ("logic.reject", "function:미수금"),
        ("logic.accept", "function:연체일수"),
    ]
    # unknown items
    assert (
        c.post(f"{API}/review/lg:lc_none/decision", json={"decision": "accept"}).status_code == 404
    )


def test_cli_logic_commands(client, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from ontology import cli_ops
    from ontology.cli import app

    c, store, view, runs, llm = client

    def down(*a, **kw):
        raise cli_ops.ServiceDown("http://127.0.0.1:0")

    class _Down:
        call = staticmethod(down)
        raw = staticmethod(down)

    monkeypatch.setattr(cli_ops, "_service", lambda *a, **kw: _Down())
    runner = CliRunner()

    def ok(args, code=0):
        out = runner.invoke(app, args)
        assert out.exit_code == code, out.output
        return out.output

    assert "no logic candidates" in ok(["logic", "candidates"])
    data = json.loads(ok(["logic", "extract", "--json"]))  # service down: runs here
    assert data["run"]["status"] == "succeeded"
    with sqlite3.connect(store.path) as conn:  # audited like POST /runs {"kind": "logic"}
        started = conn.execute(
            "SELECT target, detail_json, project_id FROM audit_log WHERE action = 'run.start'"
        ).fetchall()
    assert [(r[0], json.loads(r[1]), r[2]) for r in started] == [
        (data["run"]["id"], {"kind": "logic", "actor": "cli", "via": "cli"}, view.id)
    ]
    assert data["run"]["params"]["counts"]["stored"] == 5
    listed = ok(["logic", "candidates"])
    assert "지급기한경과" in listed and "1 error(s)" in listed and "pending" in listed
    ready = json.loads(ok(["logic", "candidates", "--ready", "--json"]))["candidates"]
    assert len(ready) == 4
    by_name = {
        i["logic"]["name"]: i["logic"]["candidate_id"]
        for i in json.loads(ok(["logic", "candidates", "--json"]))["candidates"]
    }
    out = ok(["logic", "accept", by_name["연체일수"]])
    assert "accept" in out and "연체일수: version 1, draft" in out
    out = runner.invoke(app, ["logic", "accept", by_name["지급기한경과"]])
    assert out.exit_code == 1 and "invalid_spec" in out.output and "지급기한" in out.output
    spec_file = tmp_path / "edited.yaml"
    import yaml

    spec_file.write_text(yaml.safe_dump(FN_DUE_EDITED, allow_unicode=True), "utf-8")
    assert "draft" in ok(["logic", "accept", "lg:" + by_name["지급기한경과"], "-f", str(spec_file)])
    assert "reject" in ok(["logic", "reject", by_name["미수금"], "--note", "dup"])
    assert ok(["logic", "accept", "../x"], code=2)
    assert "lc_" in ok(["logic", "candidates", "--status", "decided"])


def test_cli_review_accept_and_undo_of_logic_candidates(client):
    """`onto review accept lg:...` saves the draft definition like the web decision."""
    from typer.testing import CliRunner

    from ontology.cli import app

    c, store, view, runs, llm = client
    run_id = c.post(f"{API}/runs", json={"kind": "logic"}).json()["run_id"]
    runs.join(run_id, 30)
    items = _items(c)
    runner = CliRunner()
    out = runner.invoke(app, ["review", "accept", items["연체일수"]["id"]])
    assert out.exit_code == 0, out.output
    d = c.get(f"{API}/logic/function/연체일수").json()["definition"]
    assert (d["status"], d["origin"], d["version"]) == ("draft", "extracted", 1)
    out = runner.invoke(app, ["review", "reject", items["미수금"]["id"]])
    assert out.exit_code == 0, out.output
    assert set(_items(c)) == {"지급기한경과", "독촉단계기록", "연체독촉"}
    out = runner.invoke(app, ["review", "undo", items["연체일수"]["id"]])
    assert out.exit_code == 0, out.output
    assert c.get(f"{API}/logic/function/연체일수").json()["definition"]["status"] == "retired"
    # the web path saves the next version of the same draft
    resp = c.post(f"{API}/review/{items['연체일수']['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 200, resp.text
    again = c.get(f"{API}/logic/function/연체일수").json()["definition"]
    assert (again["status"], again["version"]) == ("draft", 2)
    # the CLI audits like the web route (one entry each; the web one says local)
    assert _audit(store) == [
        ("logic.accept", "function:연체일수"),
        ("logic.reject", "function:미수금"),
        ("logic.accept", "function:연체일수"),
    ]
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT action, target, detail_json, project_id FROM audit_log "
            "WHERE action IN ('logic.accept', 'logic.reject', 'review.undo') ORDER BY id"
        ).fetchall()
    details = [json.loads(r[2]) for r in rows]
    assert [r[0] for r in rows] == ["logic.accept", "logic.reject", "review.undo", "logic.accept"]
    assert details[0] == {
        "candidate": items["연체일수"]["id"][3:],
        "version": 1,
        "edited": False,
        "actor": "cli",
        "via": "cli",
    }
    assert details[1] == {"candidate": items["미수금"]["id"][3:], "actor": "cli", "via": "cli"}
    assert rows[2][1] == items["연체일수"]["id"] and details[2] == {"actor": "cli", "via": "cli"}
    assert details[3]["actor"] == "local" and "via" not in details[3]
    assert {r[3] for r in rows} == {view.id}


# --- roles --------------------------------------------------------------------------------


def test_accounts_roles_on_logic_extraction(isolated_env, monkeypatch):
    monkeypatch.setattr(acc, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setattr(acc, "_DUMMY_HASH", None)
    monkeypatch.setenv("ONTOLOGY_AUTH", "accounts")
    monkeypatch.setattr(functions, "today", lambda: TODAY)
    fx.install_backend(monkeypatch)
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    view = fx.seed(store)
    seed_documents(view)
    monkeypatch.setattr(extract, "make_backend", lambda s: FakeLLM(junk_first=False))
    runs = RunManager(settings)
    app = web.create_app(settings, runs=runs)
    accounts = Accounts(store)

    def client(email: str, role: str) -> TestClient:
        user = accounts.by_email(email) or accounts.create_user(email)
        accounts.add_member(view.id, user.id, role)
        raw, _ = accounts.create_session(user.id)
        c = TestClient(app)
        c.cookies.set("onto_session", raw)
        return c

    viewer, reviewer, editor = (
        client("v@example.com", "viewer"),
        client("r@example.com", "reviewer"),
        client("e@example.com", "editor"),
    )
    resp = reviewer.post(f"{API}/runs", json={"kind": "logic"})
    assert resp.status_code == 403 and resp.json()["permission"] == "logic.edit"
    run_id = editor.post(f"{API}/runs", json={"kind": "logic"}).json()["run_id"]
    runs.join(run_id, 30)
    items = viewer.get(f"{API}/logic/candidates").json()["candidates"]
    assert len(items) == 5
    item = next(i for i in items if i["logic"]["name"] == "연체일수")
    resp = reviewer.post(f"{API}/review/{item['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 403 and resp.json()["permission"] == "logic.edit"
    resp = editor.post(f"{API}/review/{item['id']}/decision", json={"decision": "accept"})
    assert resp.status_code == 200
    assert resp.json()["decision"]["decided_by"] == "e@example.com"
    d = view.logic_defs().get("function", "연체일수")
    assert d.created_by == "e@example.com" and d.origin == "extracted"
    # activating stays the registry's (functions: editor) -- not part of the decision
    assert d.status == "draft"
    entry = accounts.list_audit(action="logic.accept")[0]
    assert entry["actor"] == "e@example.com" and entry["target"] == "function:연체일수"
    assert reviewer.delete(f"{API}/review/{item['id']}/decision").status_code == 403


def test_the_prompt_shows_tables_and_the_approval_rule(env):
    """Types carry their table (a language-independent object_type) and the prompt
    states that approval.when needs a reviewer or owner role."""
    settings, store, view, llm = env
    types = {t["label"]: t for t in extract.schema_payload(view)}
    assert types["청구"]["table"] == "invoices" and types["계약"]["table"] == "contracts"
    assert prompts.LOGIC_PROMPT_VERSION == "2"
    assert "approval.when" in prompts.LOGIC_SYSTEM_PROMPT
    assert 'role "none"' in prompts.LOGIC_SYSTEM_PROMPT
