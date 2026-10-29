# SPDX-License-Identifier: MIT
"""Review decisions (schema v6): the store, the migration from older stores."""

import sqlite3

import pytest

from autoontology.ontology import model as merged
from autoontology.ontology.model import MergedOntology
from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import MIGRATIONS, Store
from autoontology.store.decisions import Decision, DecisionStore, alias_key
from tests import test_build as tb
from tests.test_align import FakeJudge, _v
from tests.test_build import env  # noqa: F401 - the build fixture


def _downgrade(path, version):
    """Turn a current store back into a v<version> one (the later migrations' DDL undone)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE review_decisions")
    conn.execute("ALTER TABLE alignments DROP COLUMN judged_json")
    if version < 5:
        conn.execute("ALTER TABLE documents DROP COLUMN reader_fingerprint")
    if version < 4:
        conn.execute("DROP TABLE term_legacy_descriptions")
    conn.execute("DELETE FROM schema_version WHERE version > ?", (version,))
    conn.execute(
        "INSERT INTO alignments (source_id, concept_id, concept_name, term_id, relation, "
        "run_id, created_at) VALUES ('s', 'c1', '계약', 't1', 'same', 'r', 'x')"
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("version", [5, 3])
def test_migration_v6_upgrades_older_stores(tmp_path, version):
    path = tmp_path / "autoontology.db"
    Store(path)
    _downgrade(path, version)
    store = Store(path)
    assert store.schema_version() == MIGRATIONS[-1][0] == 6
    row = AlignmentStore(store).get("c1")
    assert row is not None and row.judged == {} and row.relation == "same"
    decisions = DecisionStore(store)
    assert decisions.list() == []
    d = decisions.put(Decision("concept", "c1", "acknowledge"))
    assert d.id and d.decided_by == "local" and d.applied_revision is None


def test_decisions_upsert_delete_and_applied(tmp_path):
    store = Store(tmp_path / "s.db")
    decisions = DecisionStore(store)
    first = decisions.put(
        Decision("alignment", "c1", "accept", {"relation": "related", "term_id": "t1"}, note="확인")
    )
    assert decisions.mark_applied([first], 3) == 1
    assert decisions.get("alignment", "c1").applied_revision == 3
    # one active decision per subject: a new one replaces it and is pending again
    again = decisions.put(Decision("alignment", "c1", "reject", decided_by="kim@example.test"))
    assert again.id == first.id and again.decision == "reject" and again.relation == "none"
    assert again.applied_revision is None and again.decided_by == "kim@example.test"
    assert [d.subject_key for d in decisions.list(pending=True)] == ["c1"]
    decisions.put(Decision("alias", alias_key("t1", "홍길동"), "accept"))
    assert len(decisions.list()) == 2 and len(decisions.list("alias")) == 1
    assert decisions.delete("alignment", "c1") and not decisions.delete("alignment", "c1")
    assert decisions.get("alignment", "c1") is None
    with pytest.raises(ValueError, match="cannot be"):
        decisions.put(Decision("concept", "c2", "accept"))
    with pytest.raises(ValueError, match="relation"):
        decisions.put(Decision("alignment", "c2", "accept", {"relation": "none", "term_id": "t"}))
    with pytest.raises(ValueError, match="Term"):
        decisions.put(Decision("alignment", "c2", "accept", {"relation": "same"}))


def test_alignment_row_keeps_the_judged_verdict(tmp_path):
    store = Store(tmp_path / "s.db")
    src = store.add_source(name="docs", kind="documents", locator="{}")
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO concepts (id, source_id, name, name_en, kind, definition, "
            "created_at) VALUES ('c1', ?, '계약', '', 'entity', '', 'x')",
            (src.id,),
        )
    aligns = AlignmentStore(store)
    judged = {"relation": "related", "term_id": "t1", "confidence": 0.7}
    aligns.put(
        AlignmentRecord(
            src.id, "c1", "계약", "same", "r", term_id="t1", accepted=True, judged=judged
        )
    )
    assert aligns.get("c1").judged == judged
    aligns.put(AlignmentRecord(src.id, "c1", "계약", "related", "r", term_id="t1"))
    assert aligns.get("c1").judged == {}


# --- the overlay: decisions survive the align judge -----------------------------------


class OpsJudge(FakeJudge):
    """The align tests' judge, plus a refused `same` (수납: a proposal) and a related
    verdict (청구서), recording which concepts it judged."""

    def __init__(self):
        super().__init__()
        self.judged: list[str] = []

    def structured(self, system, human, schema):
        concept = human.split("\n", 2)[1]
        self.judged.append(concept)
        if '"수납"' in concept:
            self.calls += 1
            return _v("t-customer", "same", 0.95, "수납 = Customer?")
        if '"청구서"' in concept:
            self.calls += 1
            return _v("t-contract", "related", 0.7, "청구서는 계약에 따른다")
        return super().structured(system, human, schema)


@pytest.fixture
def ops(env, monkeypatch):  # noqa: F811
    monkeypatch.setitem(tb.VECTORS, "수납", [0.1, 0.9, 0.0])
    monkeypatch.setitem(tb.VECTORS, "청구서", [0.9, 0.1, 0.0])
    tb.add_structured(env)
    tb.add_documents(env)
    env["judge"] = OpsJudge()
    result = tb.run(env, judge=env["judge"])
    assert result.status == "succeeded", result.problems
    env["source"] = env["store"].get_source("hanbit-docs")
    env["aligns"] = AlignmentStore(env["store"])
    env["decisions"] = DecisionStore(env["store"])
    return env


def _rows(ops):
    return {a.concept_name: a for a in ops["aligns"].list(ops["source"].id)}


def _accept(ops, name, relation, term_id, **kw):
    row = _rows(ops)[name]
    return ops["decisions"].put(
        Decision(
            "alignment",
            row.concept_id,
            "accept",
            {"relation": relation, "term_id": term_id, "term_name": kw.get("term_name", "")},
            note=kw.get("note", ""),
        )
    )


def test_fake_build_has_a_proposal_and_a_related_verdict(ops):
    rows = _rows(ops)
    assert rows["수납"].relation == "related" and rows["수납"].proposal["relation"] == "same"
    assert rows["청구서"].relation == "related" and not rows["청구서"].accepted
    assert rows["계약"].accepted and rows["계약"].relation == "same"


def test_decisions_override_the_judge_and_survive_a_re_align(ops, monkeypatch):
    rows = _rows(ops)
    _accept(ops, "수납", "same", "t-customer", note="수납은 고객 계정")
    _accept(ops, "청구서", "broader", "t-contract")
    ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    ops["judge"].judged.clear()
    # every concept is judged again (a new judge key), but never a decided one
    from autoontology.engine import align as align_job

    key = align_job.al.judge_key
    monkeypatch.setattr(align_job.al, "judge_key", lambda *a, **kw: key(*a, **kw) + "-v2")
    result = tb.run(ops, judge=ops["judge"])
    assert result.status == "succeeded", result.problems
    judged = ops["judge"].judged
    assert any('"고객"' in c for c in judged)  # the undecided one was judged again
    assert not [c for c in judged if any(f'"{n}"' in c for n in ("수납", "청구서", "계약"))]
    rows = _rows(ops)
    assert (rows["수납"].relation, rows["수납"].accepted) == ("same", True)
    assert rows["수납"].rationale == "human decision: 수납은 고객 계정"
    assert rows["수납"].judged["relation"] == "related" and rows["수납"].judge_key == ""
    assert (rows["청구서"].relation, rows["청구서"].accepted) == ("broader", True)
    assert (rows["계약"].relation, rows["계약"].accepted) == ("none", False)
    assert rows["계약"].judged["relation"] == "same" and rows["계약"].judged["accepted"]
    steps = {s.name: s for s in ops["store"].get_steps(result.run_id)}
    assert steps[f"concept:{rows['수납'].concept_id}"].detail["path"] == "decision"
    assert steps["align:hanbit-docs"].detail["relations"]["none"] >= 1
    # a person's `same` is applied upstream like an accepted judge verdict
    assert "수납" in ops["client"].terms["t-customer"]["synonyms"]
    model = MergedOntology.from_dict(ops["store"].latest_snapshot().model)
    folded = {m.concept_name for m in model.merges}
    assert "수납" in folded and "계약" not in folded
    axiom = next(a for a in model.axioms if a.source == "alignment")
    assert axiom.accepted and axiom.kind == "subclass_of" and axiom.object == "term-t-contract"
    assert "term-t-contract" in model.aligned_terms()  # through the accepted axiom
    assert {d["name"] for d in model.decisions} == {"수납", "청구서", "계약"}


def test_removing_a_decision_gives_the_concept_back_to_the_judge(ops):
    rows = _rows(ops)
    ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    tb.run(ops, judge=ops["judge"])
    assert _rows(ops)["계약"].relation == "none"
    ops["decisions"].delete("alignment", rows["계약"].concept_id)
    tb.run(ops, judge=ops["judge"])
    row = _rows(ops)["계약"]
    assert (row.relation, row.accepted, row.judged) == ("same", True, {})
    assert row.rationale == "계약 = Contract" and row.judge_key


def test_a_decision_whose_term_is_gone_is_judged_again(ops):
    rows = _rows(ops)
    ops["decisions"].put(
        Decision(
            "alignment",
            rows["청구서"].concept_id,
            "accept",
            {"relation": "same", "term_id": "t-gone"},
        )
    )
    tb.run(ops, judge=ops["judge"])
    assert _rows(ops)["청구서"].relation == "related"


# --- the model and the exporters --------------------------------------------------------


def test_accepted_axioms_export_like_proposals():
    from autoontology.export import rdf
    from tests import test_ossie_okf as fx

    docs = fx.documents()
    accepted = merged.DocumentsInput(
        docs.source,
        docs.documents,
        docs.concepts,
        docs.relations,
        docs.evidence,
        [
            a if a.concept_id != "c3" else AlignmentRecord(**{**a.__dict__, "accepted": True})
            for a in docs.alignments
        ],
    )
    proposal = merged.build("ws", structured=[fx.structured()], documents=[docs])
    decided = merged.build("ws", structured=[fx.structured()], documents=[accepted])
    assert "term-t-i" not in proposal.aligned_terms() and "term-t-i" in decided.aligned_terms()
    from rdflib.compare import isomorphic

    assert isomorphic(rdf.to_graph(proposal), rdf.to_graph(decided))


def test_okf_marks_decided_concepts_stable(tmp_path):
    from autoontology.export import okf
    from tests import test_ossie_okf as fx

    def status(model, out):
        okf.write(model, out, revision=1, created_at=fx.CREATED)
        meta, _, _ = okf.split_frontmatter((out / "okf/concepts/담당자.md").read_text("utf-8"))
        return meta["status"]

    plain = merged.build("ws", structured=[fx.structured()], documents=[fx.documents()])
    assert status(plain, tmp_path / "a") == "draft"
    decided = merged.build(
        "ws",
        structured=[fx.structured()],
        documents=[fx.documents()],
        decisions=[Decision("concept", "c9", "acknowledge")],
    )
    assert decided.decisions == [
        {
            "kind": "concept",
            "subject": "c9",
            "decision": "acknowledge",
            "relation": None,
            "source": "hanbit-docs",
            "name": "담당자",
        }
    ]
    assert status(decided, tmp_path / "b") == "stable"


def test_accepted_alias_goes_onto_the_term():
    from tests import test_ossie_okf as fx

    base = fx.documents()
    concepts = [
        fx._concept(c.id, c.name, c.kind, c.definition, c.name_en, [*c.synonyms, "계약금액"])
        if c.id == "c1"
        else c
        for c in base.concepts
    ]
    docs = merged.DocumentsInput(
        base.source, base.documents, concepts, base.relations, base.evidence, base.alignments
    )
    plain = merged.build("ws", structured=[fx.structured()], documents=[docs])
    term = next(c for c in plain.classes.values() if c.rejected_aliases)
    alias = term.rejected_aliases[0]["alias"]
    decided = merged.build(
        "ws",
        structured=[fx.structured()],
        documents=[docs],
        decisions=[Decision("alias", alias_key(term.term_id, alias), "accept")],
    )
    cls = decided.classes[term.local]
    assert alias in cls.alt_labels and alias not in [r["alias"] for r in cls.rejected_aliases]
    assert decided.decisions[0]["name"] == alias


# --- the apply run ------------------------------------------------------------------------


def _no_models(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("apply must not reach a model")

    from autoontology.engine import align as align_job
    from autoontology.engine import extract as extract_job
    from autoontology.engine import jobs

    for module, name in (
        (align_job, "upstream_backend"),
        (align_job, "make_typed_embedder"),
        (extract_job, "upstream_backend"),
        (extract_job, "make_embedder"),
        (jobs, "require_llm_proxy"),
        (jobs, "upstream_compile"),
    ):
        monkeypatch.setattr(module, name, refuse)


def _apply(ops):
    from autoontology.engine.apply import run_apply
    from tests.test_align import ClientWriter

    return run_apply(
        ops["store"], ops["settings"], client=ops["client"], writer=ClientWriter(ops["client"])
    )


def test_apply_reflects_the_decisions_in_a_new_revision_without_a_model(ops, monkeypatch):
    rows = _rows(ops)
    store = ops["store"]
    assert store.latest_revision() == 1
    _accept(ops, "수납", "same", "t-customer")
    _accept(ops, "청구서", "narrower", "t-contract")
    ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    ops["decisions"].put(Decision("concept", rows["고객"].concept_id, "acknowledge"))
    _no_models(monkeypatch)
    calls = ops["judge"].calls
    result = _apply(ops)
    assert result.status == "succeeded" and result.revision == 2
    assert ops["judge"].calls == calls
    run = store.get_run(result.run_id)
    assert run.kind == "apply" and run.status == "succeeded"
    assert run.params["applied_revision"] == 2 and run.params["decisions_applied"] == 4
    steps = {s.name: s for s in store.get_steps(result.run_id)}
    assert list(steps) == ["decisions", "merge", "snapshot"]
    assert steps["decisions"].detail["written"] == 3
    assert steps["decisions"].detail["synonyms_added"] >= 1
    assert "수납" in ops["client"].terms["t-customer"]["synonyms"]
    assert {d.applied_revision for d in ops["decisions"].list()} == {2}
    model = MergedOntology.from_dict(store.latest_snapshot().model)
    assert {m.concept_name for m in model.merges} == {"고객", "수납"}
    axiom = next(a for a in model.axioms if a.source == "alignment")
    assert (axiom.subject, axiom.kind, axiom.accepted) == ("term-t-contract", "subclass_of", True)
    # nothing pending: a second apply still snapshots, and writes no row
    again = _apply(ops)
    detail = {s.name: s.detail for s in store.get_steps(again.run_id)}["decisions"]
    assert again.revision == 3 and detail["written"] == 0 and detail["unchanged"] == 3
    # a removed decision: apply gives the row its judged verdict back
    ops["decisions"].delete("alignment", rows["계약"].concept_id)
    _apply(ops)
    row = _rows(ops)["계약"]
    assert (row.relation, row.accepted, row.judged) == ("same", True, {})


def test_apply_refuses_while_another_run_holds_the_workspace(ops):
    from autoontology.engine.build import WORKSPACE_LEASE
    from autoontology.engine.jobs import JobError

    with ops["store"].lease([WORKSPACE_LEASE], "build"):
        with pytest.raises(JobError, match="already has an active build"):
            _apply(ops)


def test_apply_needs_a_snapshot(env):  # noqa: F811
    from autoontology.engine.jobs import JobError

    tb.add_documents(env)
    with pytest.raises(JobError, match="no ontology snapshot"):
        _apply(env)


def test_a_build_marks_the_decisions_it_reflects(ops):
    rows = _rows(ops)
    d = ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    result = tb.run(ops, judge=ops["judge"])
    assert ops["decisions"].get("alignment", d.subject_key).applied_revision == result.revision
