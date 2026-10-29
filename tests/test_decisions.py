# SPDX-License-Identifier: MIT
"""Review decisions (schema v6): the store, the migration from older stores."""

import sqlite3
import tempfile
from pathlib import Path

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
    conn.execute("DROP INDEX IF EXISTS alignments_review_idx")
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
    from rdflib import Literal, Namespace

    ao = Namespace("http://example.com/autoontology#")

    def statuses(model):
        return sorted(str(o) for o in rdf.to_graph(model).objects(None, ao.alignmentStatus))

    # serialised the same way, the status says which is which
    assert statuses(proposal) == ["accepted", "proposed"]  # 거래처 accepted, 연체율 proposed
    assert statuses(decided) == ["accepted", "accepted"]
    g = rdf.to_graph(decided)
    assert (None, ao.alignmentStatus, Literal("accepted")) in g
    from autoontology.export import okf

    okf.write(decided, Path(tempfile.mkdtemp()), revision=1, created_at=fx.CREATED)
    # a person's decision is "decided", and the judge's rationale is kept after the note
    human = merged.DocumentsInput(
        docs.source,
        docs.documents,
        docs.concepts,
        docs.relations,
        docs.evidence,
        [
            AlignmentRecord(
                **{
                    **a.__dict__,
                    "accepted": True,
                    "rationale": "human decision: 확인",
                    "judged": {"relation": "related", "rationale": "근거"},
                }
            )
            if a.concept_id == "c3"
            else a
            for a in docs.alignments
        ],
    )
    model = merged.build("ws", structured=[fx.structured()], documents=[human])
    axiom = next(a for a in model.axioms if a.object == "term-t-i")
    assert (axiom.status, axiom.rationale) == ("decided", "human decision: 확인; 근거")
    out = Path(tempfile.mkdtemp())
    okf.write(model, out, revision=1, created_at=fx.CREATED)
    body = (out / "okf/concepts/연체율.md").read_text("utf-8")
    assert "(alignment, decided, confidence 0.70)" in body


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


def _apply(ops, **kw):
    from autoontology.engine.apply import run_apply
    from tests.test_align import ClientWriter

    return run_apply(
        ops["store"],
        ops["settings"],
        client=ops["client"],
        writer=ClientWriter(ops["client"]),
        **kw,
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
    # nothing pending: no new revision (merge and snapshot skipped), unless forced
    again = _apply(ops)
    steps = {s.name: s for s in store.get_steps(again.run_id)}
    assert again.status == "succeeded" and again.revision == 2 and store.latest_revision() == 2
    assert steps["decisions"].detail["written"] == 0 and steps["decisions"].detail["unchanged"] == 3
    assert {steps[n].status for n in ("merge", "snapshot")} == {"skipped"}
    assert steps["snapshot"].detail["reason"] == "no pending decisions"
    assert store.get_run(again.run_id).params["applied_revision"] == 2
    forced = _apply(ops, force=True)
    assert forced.revision == 3 and store.latest_revision() == 3
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


# --- fix wave: unfolding, one decision per subject, resumed path --------------------------


def _exports(model):
    """(RDF graph, Ossie ontology document as text, OKF Contract/Customer bodies)."""
    import json
    import tempfile
    from pathlib import Path

    from autoontology.export import okf, ossie, rdf

    graph = rdf.to_graph(model)
    onto = ossie.build_ontology(
        model,
        revision=1,
        created_at="2026-01-01T00:00:00+00:00",
        datasets=[],
        semantic_relationships=[],
        warnings=[],
    )
    out = Path(tempfile.mkdtemp())
    okf.write(model, out, revision=1, created_at="2026-01-01T00:00:00+00:00")
    bodies = {p.stem: p.read_text("utf-8") for p in (out / "okf/concepts").glob("*.md")}
    return graph, json.dumps(onto.document, ensure_ascii=False), bodies


def _alt_labels(graph, local):
    from rdflib import SKOS, URIRef

    base = "http://example.com/autoontology#"
    return {str(o) for o in graph.objects(URIRef(base + local), SKOS.altLabel)}


def _check_unfolded(ops, name, term_id, term_local):
    """*name* is its own class again and no longer a synonym of the Term anywhere."""
    assert name not in ops["client"].terms[term_id]["synonyms"]
    model = MergedOntology.from_dict(ops["store"].latest_snapshot().model)
    assert name not in model.classes[term_local].alt_labels
    assert name in {c.label for c in model.classes.values() if c.source == "document"}
    assert name not in {m.concept_name for m in model.merges}
    graph, onto, bodies = _exports(model)
    assert name not in _alt_labels(graph, term_local)
    assert f"Also known as: {name}" not in onto and f", {name}" not in onto.split(term_local)[0]
    term_body = next(b for b in bodies.values() if f"#{term_local}" in b)
    assert f"- {name}\n" not in term_body
    live = AlignmentStore(ops["store"]).alias_provenance(live=True).get(term_id, [])
    assert name not in {r["alias"] for r in live}


def test_rejecting_an_applied_fold_takes_its_synonyms_off_the_term(ops):
    rows = _rows(ops)
    assert "계약" in ops["client"].terms["t-contract"]["synonyms"]  # the judge's fold
    ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    result = _apply(ops)
    detail = {s.name: s.detail for s in ops["store"].get_steps(result.run_id)}["decisions"]
    assert detail["synonyms_removed"] >= 1
    _check_unfolded(ops, "계약", "t-contract", "term-t-contract")
    # the other fold is untouched
    assert "고객" in ops["client"].terms["t-customer"]["synonyms"]


def test_undoing_an_applied_same_takes_its_synonyms_off_the_term(ops):
    _accept(ops, "수납", "same", "t-customer")
    _apply(ops)
    assert "수납" in ops["client"].terms["t-customer"]["synonyms"]
    ops["decisions"].delete("alignment", _rows(ops)["수납"].concept_id)
    _apply(ops)
    assert _rows(ops)["수납"].relation == "related"  # the judge's verdict again
    _check_unfolded(ops, "수납", "t-customer", "term-t-customer")
    assert "고객" in ops["client"].terms["t-customer"]["synonyms"]


def test_accepting_a_candidate_term_runs_through_apply(ops):
    rows = _rows(ops)
    ops["decisions"].put(
        Decision(
            "alignment",
            rows["청구서"].concept_id,
            "accept",
            {"relation": "same", "term_id": "t-customer", "term_name": "Customer"},
        )
    )
    _apply(ops)
    row = _rows(ops)["청구서"]
    assert (row.relation, row.term_id, row.accepted) == ("same", "t-customer", True)
    assert "청구서" in ops["client"].terms["t-customer"]["synonyms"]
    model = MergedOntology.from_dict(ops["store"].latest_snapshot().model)
    assert ("청구서", "term-t-customer") in {(m.concept_name, m.term_local) for m in model.merges}


def test_one_decision_per_concept_across_kinds(tmp_path):
    store = Store(tmp_path / "s.db")
    decisions = DecisionStore(store)
    decisions.put(Decision("concept", "c1", "acknowledge"))
    decisions.put(Decision("alignment", "c1", "accept", {"relation": "related", "term_id": "t"}))
    assert [(d.kind, d.subject_key) for d in decisions.list()] == [("alignment", "c1")]
    decisions.put(Decision("concept", "c1", "acknowledge"))
    assert [(d.kind, d.subject_key) for d in decisions.list()] == [("concept", "c1")]


def test_a_decision_holds_on_the_resumed_judge_path(ops, monkeypatch):
    """A decision added after the run judged its concept: a resumed build that re-enters
    align (the Terms moved) reuses the other concepts' verdicts and applies the decision."""
    rows = _rows(ops)
    ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    monkeypatch.setattr(
        tb.bj,
        "_terms_moved",
        lambda store, run, s, c, already: {n for n in already if n.startswith("align:")},
    )
    ops["judge"].judged.clear()
    result = tb.run(ops, judge=ops["judge"], resume=True)
    assert result.status == "succeeded", result.problems
    detail = {s.name: s.detail for s in ops["store"].get_steps(result.run_id)}["align:hanbit-docs"]
    assert detail["resumed"] >= 1 and ops["judge"].judged == []
    assert (_rows(ops)["계약"].relation, _rows(ops)["계약"].accepted) == ("none", False)


# --- re-review: the unfold follows the rows' state (retries and resumes) -----------------


def _flaky_writer(ops, fails=1):
    from tests.test_align import ClientWriter

    class Flaky(ClientWriter):
        def __init__(self, client):
            super().__init__(client)
            self.fails = fails

        def remove_synonyms(self, term_id, removals):
            if self.fails:
                self.fails -= 1
                raise ConnectionError("backend went away")
            return super().remove_synonyms(term_id, removals)

    return Flaky(ops["client"])


def test_a_failed_unfold_is_retried_by_the_next_apply(ops):
    from autoontology.engine.apply import run_apply
    from autoontology.engine.jobs import JobError

    rows = _rows(ops)
    d = ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    writer = _flaky_writer(ops)
    with pytest.raises(JobError, match="decisions"):
        run_apply(ops["store"], ops["settings"], client=ops["client"], writer=writer)
    assert "계약" in ops["client"].terms["t-contract"]["synonyms"]
    assert ops["decisions"].get("alignment", d.subject_key).applied_revision is None
    result = run_apply(ops["store"], ops["settings"], client=ops["client"], writer=writer)
    assert result.revision == 2
    _check_unfolded(ops, "계약", "t-contract", "term-t-contract")
    assert ops["decisions"].get("alignment", d.subject_key).applied_revision == 2
    assert not _rows(ops)["계약"].applied  # the trace is cleared once the synonyms are off


def test_a_build_unfolds_a_rejected_fold(ops):
    rows = _rows(ops)
    d = ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    result = tb.run(ops, judge=ops["judge"])
    assert result.status == "succeeded", result.problems
    _check_unfolded(ops, "계약", "t-contract", "term-t-contract")
    assert ops["decisions"].get("alignment", d.subject_key).applied_revision == result.revision


def test_a_build_resumed_after_a_crash_between_judge_and_apply_unfolds(ops, monkeypatch):
    from autoontology.engine import align as align_job
    from autoontology.engine.jobs import JobError

    rows = _rows(ops)
    d = ops["decisions"].put(Decision("alignment", rows["계약"].concept_id, "reject"))
    real = align_job.ALIGN_STEP_FUNCS["apply"]

    def crash(ctx):
        raise ConnectionError("backend went away")

    monkeypatch.setitem(align_job.ALIGN_STEP_FUNCS, "apply", crash)
    with pytest.raises(JobError, match="align"):
        tb.run(ops, judge=ops["judge"])
    assert _rows(ops)["계약"].relation == "none"  # the judge step wrote the decision
    assert "계약" in ops["client"].terms["t-contract"]["synonyms"]
    monkeypatch.setitem(align_job.ALIGN_STEP_FUNCS, "apply", real)
    result = tb.run(ops, judge=ops["judge"], resume=True)
    assert result.status == "succeeded", result.problems
    _check_unfolded(ops, "계약", "t-contract", "term-t-contract")
    assert ops["decisions"].get("alignment", d.subject_key).applied_revision == result.revision


def test_an_undone_same_on_a_never_judged_concept_still_unfolds(ops):
    """A concept with no judged verdict before its decision: removing the decision keeps
    the row (unaligned) until its synonyms are off the Term."""
    rows = _rows(ops)
    cid = rows["청구서"].concept_id
    ops["aligns"].delete(cid)  # as if never judged
    ops["decisions"].put(
        Decision("alignment", cid, "accept", {"relation": "same", "term_id": "t-customer"})
    )
    _apply(ops)
    assert "청구서" in ops["client"].terms["t-customer"]["synonyms"]
    ops["decisions"].delete("alignment", cid)
    writer = _flaky_writer(ops)
    from autoontology.engine.apply import run_apply
    from autoontology.engine.jobs import JobError

    with pytest.raises(JobError):
        run_apply(ops["store"], ops["settings"], client=ops["client"], writer=writer)
    run_apply(ops["store"], ops["settings"], client=ops["client"], writer=writer)
    _check_unfolded(ops, "청구서", "t-customer", "term-t-customer")


def test_a_v6_store_without_the_review_index_gets_it_on_open(tmp_path):
    path = tmp_path / "autoontology.db"
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX alignments_review_idx")

    def indexes():
        with sqlite3.connect(path) as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert "alignments_review_idx" not in indexes()
    assert Store(path).schema_version() == 6
    assert "alignments_review_idx" in indexes()
