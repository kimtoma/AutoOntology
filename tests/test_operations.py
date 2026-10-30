# SPDX-License-Identifier: MIT
"""Published knowledge remains stable while candidate changes are reviewed."""

from copy import deepcopy

import pytest

from autoontology.ontology.model import MergedOntology, OClass, ODocument, OEvidence, OProperty
from autoontology.store.db import Store
from autoontology.store.operations import Operations, OperationsError


class CitedAnswer:
    async def stream(self, messages):
        yield "대표이사 승인입니다. [E1]"


def snapshot(store, model, kind="build"):
    run = store.create_run(kind=kind, source_id=None)
    rev = store.save_snapshot(run.id, model)
    store.update_run(run.id, status="succeeded")
    return rev


def example():
    m = MergedOntology("org", "https://example.test/ontology#", documents_sources=["rules"])
    m.documents["doc"] = ODocument("doc", "규정", "old-sha", "/rules.md", "rules")
    m.evidence["ev-1"] = OEvidence("ev-1", "doc", "3억원 이상은 대표이사 승인", 1, True)
    m.classes["concept-approval"] = OClass(
        "concept-approval",
        "승인 기준",
        "document",
        definitions=["3억원 이상"],
        derived_from=["ev-1"],
        documents_source="rules",
    )
    return m


def test_candidates_and_decisions_do_not_change_published_snapshot(tmp_path):
    store = Store(tmp_path / "s.db")
    original = example()
    first = snapshot(store, original)
    ops = Operations(store)
    assert ops.state()["published_revision"] == first
    candidate = deepcopy(original)
    candidate.classes["concept-approval"].definitions = ["2억원 이상"]
    candidate.evidence["ev-1"].quote = "2억원 이상은 대표이사 승인"
    candidate.documents["doc"].sha256 = "new-sha"
    second = snapshot(store, candidate)
    assert (
        ops.state()["published_revision"] == first and ops.state()["candidate_revision"] == second
    )
    changes = ops.changes()
    assert len(changes) == 1
    ops.decide(changes[0]["id"], "accept", candidate=second, note="개정 원문 확인", actor="owner")
    assert store.get_snapshot(ops.state()["published_revision"]).model == original.to_dict()
    composed, items = ops.compose(second)
    assert composed.classes["concept-approval"].definitions == ["2억원 이상"]
    assert composed.evidence[composed.classes["concept-approval"].derived_from[0]].quote.startswith(
        "2억원"
    )
    assert store.get_snapshot(first).model == original.to_dict()
    assert len(items) == 1


def test_only_approved_changes_publish_and_reused_evidence_ids_stay_separate(tmp_path):
    store = Store(tmp_path / "s.db")
    base = example()
    snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-new"] = OClass(
        "concept-new",
        "새 지식",
        "document",
        definitions=["새 정의"],
        derived_from=["ev-1"],
        documents_source="rules",
    )
    draft.evidence["ev-1"].quote = "새 지식을 설명하는 원문"
    draft.documents["doc"].sha256 = "new"
    rev = snapshot(store, draft)
    change = next(c for c in ops.changes() if c["key"] == "concept-new")
    ops.decide(change["id"], "accept", candidate=rev)
    model, _ = ops.compose(rev)
    assert (
        model.evidence[model.classes["concept-approval"].derived_from[0]].quote
        == "3억원 이상은 대표이사 승인"
    )
    assert (
        model.evidence[model.classes["concept-new"].derived_from[0]].quote
        == "새 지식을 설명하는 원문"
    )
    assert len(model.documents) == 2
    assert model.classes["concept-approval"].definitions == ["3억원 이상"]


def test_hold_reject_and_stale_decision_are_fail_closed(tmp_path):
    store = Store(tmp_path / "s.db")
    base = example()
    first = snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    rev = snapshot(store, draft)
    item = ops.changes()[0]
    ops.decide(item["id"], "hold", candidate=rev)
    with pytest.raises(OperationsError, match="approved"):
        ops.compose(rev)
    ops.decide(item["id"], "reject", candidate=rev, note="보완 필요")
    assert ops.changes()[0]["decision"]["decision"] == "reject"
    newer = snapshot(store, draft)
    with pytest.raises(OperationsError, match="changed"):
        ops.decide(item["id"], "accept", candidate=rev)
    assert ops.state()["published_revision"] == first and ops.state()["candidate_revision"] == newer


def test_dangling_relations_block_publication(tmp_path):
    store = Store(tmp_path / "s.db")
    base = example()
    snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-new"] = OClass(
        "concept-new", "새 개념", "document", derived_from=["ev-1"]
    )
    draft.properties["rel-new"] = OProperty(
        "rel-new",
        "object",
        "새 관계",
        "concept-approval",
        "concept-new",
        "document",
        derived_from=["ev-1"],
    )
    rev = snapshot(store, draft)
    item = next(c for c in ops.changes() if c["collection"] == "properties")
    ops.decide(item["id"], "accept", candidate=rev)
    with pytest.raises(OperationsError, match="unapproved"):
        ops.compose(rev)


def test_feedback_and_checks_are_persisted_without_model_calls(tmp_path):
    store = Store(tmp_path / "s.db")
    snapshot(store, example())
    ops = Operations(store)
    item = ops.add_feedback(
        "연체 첫날 조치는?", "근거가 없습니다", "원문의 유선 안내 누락", actor="owner"
    )
    assert ops.feedback()[0]["id"] == item["id"]
    ops.resolve_feedback(item["id"], False)
    assert not ops.feedback()
    check = ops.add_check("2억5천만원 계약 승인자는?", "대표이사")
    assert ops.checks()[0]["id"] == check["id"]


def test_failed_preparation_never_switches_the_live_version(tmp_path, isolated_env):
    from dataclasses import replace

    from autoontology.config import Settings
    from autoontology.engine.operations import run_publish, validate

    store = Store(tmp_path / "s.db")
    base = example()
    first = snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    rev = snapshot(store, draft)
    ops.decide(ops.changes()[0]["id"], "accept", candidate=rev)
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    embed = (lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts], "test")
    ops.add_check("승인권자는?", "대표이사")
    result = validate(store, settings, revision=rev, embedder=embed, llm=CitedAnswer())
    assert result["ok"] and len(result["questions"]) == 1

    def fail(*args):
        raise RuntimeError("artifact generation failed")

    with pytest.raises(RuntimeError, match="artifact"):
        run_publish(
            store, settings, revision=rev, token=result["token"], embedder=embed, exporter=fail
        )
    assert ops.state()["published_revision"] == first
    assert store.get_snapshot(first).model == base.to_dict()


def test_stale_validation_and_partial_publication(tmp_path, isolated_env):
    from dataclasses import replace

    from autoontology.config import Settings
    from autoontology.engine.operations import run_publish, validate

    store = Store(tmp_path / "s.db")
    base = example()
    snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    draft.classes["concept-extra"] = OClass(
        "concept-extra", "추가 개념", "document", derived_from=["ev-1"]
    )
    rev = snapshot(store, draft)
    change = next(i for i in ops.changes() if i["key"] == "concept-approval")
    ops.decide(change["id"], "accept", candidate=rev)
    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    embed = (lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts], "test")
    ops.add_check("승인권자는?", "대표이사")
    result = validate(store, settings, revision=rev, embedder=embed, llm=CitedAnswer())
    ops.decide(change["id"], "hold", candidate=rev)
    with pytest.raises(OperationsError, match="revalidate"):
        run_publish(store, settings, revision=rev, token=result["token"], embedder=embed)
    ops.decide(change["id"], "accept", candidate=rev)
    result = validate(store, settings, revision=rev, embedder=embed, llm=CitedAnswer())
    result = run_publish(store, settings, revision=rev, token=result["token"], embedder=embed)
    head = store.get_snapshot(ops.state()["published_revision"])
    assert head.revision > rev
    assert head.model["classes"]["concept-approval"]["definitions"] == ["2억원 이상"]
    assert "concept-extra" not in head.model["classes"]
    assert len(ops.changes()) == 1 and ops.changes()[0]["key"] == "concept-extra"
    assert (settings.exports_dir / f"r{head.revision:04}" / "ontology.ttl").exists()


def test_default_api_and_exports_stay_on_published_head(tmp_path, isolated_env):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from autoontology.config import Settings
    from autoontology.web.app import create_app

    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    store = Store.for_home(tmp_path)
    base = example()
    first = snapshot(store, base)
    client = TestClient(create_app(settings))
    assert client.get("/api/ops").json()["published_revision"] == first
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    revision = snapshot(store, draft)
    assert client.get("/api/ontology").json()["revision"] == first
    assert client.get("/api/ops").json()["candidate_revision"] == revision
    folder = settings.exports_dir / f"r{revision:04}"
    folder.mkdir(parents=True)
    (folder / "ontology.ttl").write_text("unapproved")
    assert client.get("/api/exports").json()["exports"] == []
    assert client.get(f"/api/exports/r{revision:04}/ontology.ttl").status_code == 404
    assert (
        client.post("/api/runs", json={"kind": "export", "revision": revision}).status_code == 409
    )
    change = client.get("/api/ops/changes").json()["items"][0]
    response = client.post(
        "/api/ops/changes/" + change["id"] + "/decision",
        json={"candidate": revision, "decision": "accept", "note": "원문 확인"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/ontology").json()["revision"] == first
    assert (
        client.post(
            "/api/ops/feedback",
            json={"question": "근거가 부족한 질문", "answer": "답변", "note": "근거 보완"},
        ).status_code
        == 201
    )
    assert client.get("/api/ops").json()["counts"]["feedback"] == 1


def test_question_check_failure_preserves_published_head(tmp_path, isolated_env):
    from dataclasses import replace

    from autoontology.config import Settings
    from autoontology.engine.operations import run_publish, validate

    store = Store(tmp_path / "s.db")
    base = example()
    first = snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    revision = snapshot(store, draft)
    ops.decide(ops.changes()[0]["id"], "accept", candidate=revision)
    ops.add_check("계약 승인자는 누구인가요?", "대표이사")

    class Incorrect:
        async def stream(self, messages):
            yield "온톨로지에 근거가 없습니다"

    settings = replace(Settings.from_env(), home=tmp_path, exports_dir=tmp_path / "exports")
    embed = (lambda texts, kind: [[1.0, 0.0, 0.0] for _ in texts], "test")
    result = validate(store, settings, revision=revision, embedder=embed, llm=Incorrect())
    assert result["checks_configured"] and not result["ok"]
    with pytest.raises(OperationsError, match="revalidate"):
        run_publish(store, settings, revision=revision, token=result["token"], embedder=embed)
    assert ops.state()["published_revision"] == first


def test_human_edits_are_recorded_and_not_reproposed_on_an_unchanged_source(tmp_path):
    store = Store(tmp_path / "s.db")
    base = example()
    snapshot(store, base)
    ops = Operations(store)
    ops.state()
    draft = deepcopy(base)
    draft.classes["concept-approval"].definitions = ["2억원 이상"]
    revision = snapshot(store, draft)
    item = ops.changes()[0]
    with pytest.raises(OperationsError, match="reason"):
        ops.decide(item["id"], "accept", candidate=revision, edits={"label": "대표이사 승인 기준"})
    ops.decide(
        item["id"],
        "accept",
        candidate=revision,
        note="업무 용어 명확화",
        edits={"label": "대표이사 승인 기준"},
    )
    model, items = ops.compose(revision)
    assert model.classes["concept-approval"].label == "대표이사 승인 기준"
    published = snapshot(store, model, kind="publish")
    ops.commit(published, 1, items)
    assert ops.changes() == []
    # A rebuild of the same proposal must preserve the human label edit.
    snapshot(store, draft)
    assert ops.changes() == []
    draft.evidence["ev-1"].quote = "기준이 다시 개정된 새로운 원문"
    snapshot(store, draft)
    assert len(ops.changes()) == 1
