import copy

import pytest

from autoontology.config import Settings
from autoontology.engine import align as aj
from autoontology.engine.jobs import JobError
from autoontology.store.alignments import AlignmentStore
from autoontology.store.db import Store
from autoontology.store.documents import ConceptRecord, DocumentStore, EvidenceRecord
from autoontology.unstructured import align as al
from autoontology.unstructured.schemas import AlignmentVerdict, validate_verdict
from tests.test_documents_store import C

CANDS = {"t-contract": "Contracts", "t-customer": "Customers"}


def _v(term_id="t-contract", relation="same", confidence=0.9, rationale="ok"):
    return AlignmentVerdict(
        term_id=term_id, relation=relation, confidence=confidence, rationale=rationale
    )


# --- judge post-validation ----------------------------------------------------------------


def test_verdict_must_name_a_candidate():
    v = validate_verdict(_v(term_id="t-other"), CANDS)
    assert (v.term_id, v.relation) == ("", "none") and v.notes
    v = validate_verdict(_v(term_id=" T-CONTRACT "), CANDS)
    assert (v.term_id, v.relation) == ("t-contract", "same")
    v = validate_verdict(_v(term_id="customers", relation="related"), CANDS)  # name -> id
    assert (v.term_id, v.relation) == ("t-customer", "related")
    v = validate_verdict(_v(term_id="", relation="broader"), CANDS)
    assert v.relation == "none"


def test_verdict_none_drops_term_and_confidence_is_clamped():
    v = validate_verdict(_v(relation="none", confidence=1.7), CANDS)
    assert (v.term_id, v.relation, v.confidence) == ("", "none", 1.0)
    assert validate_verdict(_v(confidence=-3), CANDS).confidence == 0.0
    assert validate_verdict(_v(confidence=float("nan")), CANDS).confidence == 0.0


def test_texts_and_candidates():
    c = al.ConceptInfo("c", "계약", "entity", "고객과 맺는 약정", name_en="Contract")
    assert al.concept_text(c) == "계약 (Contract): 고객과 맺는 약정"
    t = al.TermInfo("t", "Contract", "계약 정보", ["계약서"], attributes=[{"name": "계약id"}])
    assert al.term_text(t) == "Contract: 계약 정보 | synonyms: 계약서 | columns: 계약id"
    found = al.top_candidates(
        [1.0, 0.0], {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.9, 0.1], "d": None}, k=5, floor=0.55
    )
    assert [c.term_id for c in found] == ["a", "c"]
    assert al.top_candidates(None, {"a": [1.0]}) == []


def test_merge_synonyms_is_case_insensitive_and_never_removes():
    assert al.merge_synonyms(
        ["Contract", "계약서"], ["contract", "계약", " 계약 ", "Contract", ""], "Contract"
    ) == ["Contract", "계약서", "계약"]


# --- apply policy with a fake writer --------------------------------------------------------


class FakeWriter:
    def __init__(self, terms):
        self.terms = terms
        self.calls = []

    def add_synonyms(self, term_id, additions):
        self.calls.append(("synonyms", term_id, list(additions)))
        term = self.terms[term_id]
        before = list(term.synonyms)
        term.synonyms = al.merge_synonyms(before, additions, term.name)
        return {
            "synonyms": term.synonyms,
            "added": term.synonyms[len(before) :],
            "embeddings_refreshed": True,
        }

    def patch_description(self, term_id, description):
        self.calls.append(("description", term_id, description))
        self.terms[term_id].description = description
        return {"id": term_id, "description": description}

    def refresh_embeddings(self, term_id):
        self.calls.append(("refresh", term_id))
        return True


def test_apply_policy_is_idempotent_and_respects_real_descriptions():
    terms = {
        "t1": al.TermInfo("t1", "Contract", "계약 정보", ["Contract"]),
        "t2": al.TermInfo("t2", "Invoices", "Business entity represented by table invoices"),
    }
    matches = {
        "t1": [al.SameMatch(1, "계약", ["계약서", "contract"], "고객과 맺는 약정", 0.9)],
        "t2": [
            al.SameMatch(2, "청구서", [], "대금을 요구하는 문서", 0.8),
            al.SameMatch(3, "청구", ["청구서"], "청구 행위", 0.75),
        ],
    }
    writer = FakeWriter(terms)
    patches = al.plan_patches(matches, terms)
    by = {p.term_id: p for p in patches}
    assert by["t1"].synonyms_added == ["계약", "계약서"] and by["t1"].description is None
    assert by["t1"].kept_definitions == ["고객과 맺는 약정"]  # real description: export only
    assert by["t2"].description == "대금을 요구하는 문서"  # fallback: highest confidence wins
    assert by["t2"].synonyms_added == ["청구서", "청구"]
    for p in patches:
        al.apply_patch(p, writer)
    assert terms["t1"].synonyms == ["Contract", "계약", "계약서"]
    assert len(writer.calls) == 3
    # second run plans from the current state: nothing to do, nothing written
    again = al.plan_patches(matches, terms)
    assert not any(p.changed for p in again)
    for p in again:
        assert al.apply_patch(p, writer) == {
            "term_id": p.term_id,
            "synonyms_added": [],
            "description_set": False,
        }
    assert len(writer.calls) == 3


# --- run_align end to end with fakes ---------------------------------------------------------


class FakeClient:
    """The upstream reads run_align/fetch_snapshot make, over mutable Terms."""

    def __init__(self):
        self.terms = {
            "t-contract": {
                "id": "t-contract",
                "name": "Contract",
                "description": "계약 관련 정보",
                "synonyms": [],
                "tables": [{"id": "tb-c", "name": "contracts", "schema_id": "s1"}],
            },
            "t-customer": {
                "id": "t-customer",
                "name": "Customer",
                "description": "Business entity represented by table customers",
                "synonyms": [],
                "tables": [{"id": "tb-k", "name": "customers", "schema_id": "s1"}],
            },
        }
        self.attrs = {
            "t-contract": [
                {
                    "id": "a1",
                    "name": "계약id",
                    "source_column": "계약id",
                    "datatype": "text",
                    "primary_column": {
                        "table_id": "tb-c",
                        "table_name": "contracts",
                        "schema_id": "s1",
                        "column_name": "계약id",
                    },
                    "referenced_columns": [],
                }
            ],
            "t-customer": [
                {
                    "id": "a2",
                    "name": "고객id",
                    "source_column": "고객id",
                    "datatype": "text",
                    "primary_column": {
                        "table_id": "tb-k",
                        "table_name": "customers",
                        "schema_id": "s1",
                        "column_name": "고객id",
                    },
                    "referenced_columns": [
                        {
                            "table_id": "tb-c",
                            "table_name": "contracts",
                            "schema_id": "s1",
                            "column_name": "고객id",
                        }
                    ],
                }
            ],
        }
        self.patches = []

    def health(self):
        return {"status": "ok"}

    def database_id_by_name(self, name):
        return "db1"

    def semantic_graph(self, database_id=None):
        return {
            "nodes": [{"id": k, "name": v["name"]} for k, v in self.terms.items()],
            "links": [
                {
                    "source": "t-customer",
                    "target": "t-contract",
                    "relationship_types": ["SEMANTIC_FK"],
                }
            ],
        }

    def schemas(self, database_id):
        return [{"id": "s1", "schema_name": "public"}]

    def get_term(self, term_id):
        return copy.deepcopy(self.terms[term_id])

    def term_column_attributes(self, term_id):
        return copy.deepcopy(self.attrs[term_id])

    def patch_term(self, term_id, payload):
        self.patches.append((term_id, payload))
        self.terms[term_id].update(payload)
        return {"id": term_id, **payload}


class ClientWriter:
    """Merges at write time like the real writer (so a concurrent edit is kept)."""

    def __init__(self, client):
        self.client = client
        self.calls = 0
        self.refresh_ok = True
        self.refreshes = 0
        self.concurrent: dict[str, str] = {}  # term id -> synonym a person adds meanwhile

    def add_synonyms(self, term_id, additions):
        self.calls += 1
        term = self.client.terms[term_id]
        if term_id in self.concurrent:
            term["synonyms"].append(self.concurrent.pop(term_id))
        before = list(term["synonyms"])
        term["synonyms"] = al.merge_synonyms(before, additions, term["name"])
        self.refreshes += 1
        return {
            "synonyms": term["synonyms"],
            "added": term["synonyms"][len(before) :],
            "embeddings_refreshed": self.refresh_ok,
        }

    def patch_description(self, term_id, description):
        self.calls += 1
        return self.client.patch_term(term_id, {"description": description})

    def refresh_embeddings(self, term_id):
        self.refreshes += 1
        return self.refresh_ok

    def remove_synonyms(self, term_id, removals):
        self.calls += 1
        term = self.client.terms[term_id]
        drop = {al.norm_alias(r) for r in removals}
        removed = [x for x in term["synonyms"] if al.norm_alias(x) in drop]
        term["synonyms"] = [x for x in term["synonyms"] if al.norm_alias(x) not in drop]
        self.refreshes += 1
        return {
            "synonyms": term["synonyms"],
            "removed": removed,
            "embeddings_refreshed": self.refresh_ok,
        }


VECS = {
    "Contract": [1.0, 0.0, 0.0],
    "Customer": [0.0, 1.0, 0.0],
    "계약": [0.95, 0.05, 0.0],
    "고객": [0.1, 0.9, 0.0],
    "계약금액": [0.7, 0.0, 0.7],
    "날씨": [0.0, 0.0, 1.0],
}


def fake_embed(calls):
    def embed(texts, input_type):
        calls.append((input_type, len(texts)))
        return [VECS[t.split(":")[0].split(" (")[0]] for t in texts]

    return embed


class FakeJudge:
    def __init__(self):
        self.calls = 0

    def structured(self, system, human, schema):
        assert schema is AlignmentVerdict
        self.calls += 1
        concept = human.split("\n", 2)[1]
        if '"계약"' in concept:
            return _v("t-contract", "same", 0.97, "계약 = Contract")
        if '"고객"' in concept:
            return _v("t-customer", "same", 0.92, "고객 = Customer")
        return _v("t-contract", "related", 0.6, "계약의 속성")

    def text(self, prompt):
        raise AssertionError("structured path expected")


@pytest.fixture
def env(isolated_env):
    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    structured = store.add_source(name="hanbit", kind="files", locator="{}")
    store.set_catalog_database_id(structured.id, "db1")
    docs_src = store.add_source(name="hanbit-docs", kind="documents", locator="{}")
    docs = DocumentStore(store)
    doc, _ = docs.replace_document(
        source_id=docs_src.id,
        path="/x/a.md",
        title="A",
        mime="text/markdown",
        engine="native",
        sha256="s2",
        size=1,
        pages=None,
        chunks=[C(0, "계약은 고객과 맺는다")],
    )
    chunk = docs.chunks_for_document(doc.id)[0]
    ev = [EvidenceRecord(chunk.id, "계약은 고객과 맺는다", True)]
    docs.replace_ontology(
        docs_src.id,
        [
            ConceptRecord(
                "계약", "entity", "고객과 맺는 약정", "Contract", ["계약서"], evidence=ev
            ),
            ConceptRecord("고객", "entity", "계약 상대방", "Customer", evidence=ev),
            ConceptRecord("계약금액", "metric", "계약의 총 금액", evidence=ev),
            ConceptRecord("날씨", "entity", "관계없는 것", evidence=ev),
            ConceptRecord("승인", "process", "승인 절차", evidence=ev),
        ],
        [],
    )
    return settings, store


def _align(settings, store, client, judge, embed_calls, writer=None, **kw):
    backends = aj.AlignBackends(
        settings, judge_llm=judge, embedder=fake_embed(embed_calls), model="fake-embed"
    )
    return aj.run_align(
        "hanbit-docs",
        settings=settings,
        store=store,
        client=client,
        backends=backends,
        writer=writer or ClientWriter(client),
        check_service=False,
        **kw,
    )


def test_run_align_end_to_end_idempotent(env):
    settings, store = env
    client, judge, calls = FakeClient(), FakeJudge(), []
    writer = ClientWriter(client)
    result = _align(settings, store, client, judge, calls, writer=writer)
    assert result.status == "succeeded"
    assert list(result.steps) == list(aj.ALIGN_STEPS)
    src = store.get_source("hanbit-docs")
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}
    assert rows["계약"].relation == "same" and rows["계약"].term_name == "Contract"
    assert rows["계약"].accepted and rows["계약"].applied["synonyms_added"] == ["계약", "계약서"]
    assert rows["계약금액"].relation == "related" and not rows["계약금액"].accepted
    assert rows["날씨"].relation == "none" and "similarity" in rows["날씨"].rationale
    assert rows["승인"].relation == "skipped"
    assert judge.calls == 3  # 날씨 had no candidate, 승인 is a process
    assert client.terms["t-contract"]["synonyms"] == ["계약", "계약서"]
    # Contracts has a real description: kept; Customers is a fallback: set from the document
    assert client.patches == [("t-customer", {"description": "계약 상대방"})]
    assert ("passage", 2) in calls and ("query", 4) in calls
    report = (result.export_dir / "align-report.md").read_text(encoding="utf-8")
    assert "| same | identical | 2 |" in report and "| skipped |" in report and "날씨" in report
    summary = aj.summary(store, "hanbit-docs", result)
    assert summary["relations"]["same"] == 2 and summary["applied"] == 2

    # re-run: cached verdicts (no LLM), cached Term embeddings, nothing patched
    calls.clear()
    writes_before = writer.calls
    again = _align(settings, store, client, judge, calls, writer=writer)
    assert again.status == "succeeded" and judge.calls == 3
    assert again.details["judge"]["cached"] == 3
    assert again.details["candidates"]["term_embeddings"]["computed"] == 2  # texts changed
    assert again.details["apply"]["applied"] == 0 and writer.calls == writes_before
    assert client.terms["t-contract"]["synonyms"] == ["계약", "계약서"]
    third = _align(settings, store, client, judge, calls, writer=writer)
    assert third.details["candidates"]["term_embeddings"] == {"cached": 2, "computed": 0}


def test_dry_run_plans_without_writing(env):
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    writer = ClientWriter(client)
    result = _align(settings, store, client, judge, [], writer=writer, dry_run=True)
    apply = result.details["apply"]
    assert apply["dry_run"] and apply["planned"] == 2 and apply["applied"] == 0
    assert writer.calls == 0 and client.terms["t-contract"]["synonyms"] == []
    src = store.get_source("hanbit-docs")
    assert all(a.applied == {} for a in AlignmentStore(store).list(src.id))


class FlakyJudge(FakeJudge):
    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on

    def structured(self, system, human, schema):
        if self.fail_on and f'"{self.fail_on}"' in human.split("\n", 2)[1]:
            raise ValueError("bad answer")
        return super().structured(system, human, schema)


def test_partial_failure_then_resume_judges_only_the_failed(env, monkeypatch):
    settings, store = env
    monkeypatch.setenv("AUTOONTOLOGY_EXTRACT_MODE", "structured")
    client, judge = FakeClient(), FlakyJudge("고객")
    result = _align(settings, store, client, judge, [])
    assert result.status == "failed" and result.details["judge"]["failed"] == 1
    run = store.get_run(result.run_id)
    assert run.status == "failed" and run.error.startswith("partial: 1 concept(s)")
    judge.fail_on = None
    before = judge.calls
    resumed = _align(settings, store, client, judge, [], resume=True)
    assert resumed.run_id == result.run_id and resumed.status == "succeeded"
    assert judge.calls - before == 1
    assert resumed.details["judge"]["resumed"] == 4
    assert client.terms["t-customer"]["synonyms"] == ["고객"]


def test_min_conf_gates_application(env):
    settings, store = env
    client = FakeClient()
    result = _align(settings, store, client, FakeJudge(), [], min_conf=0.98)
    assert result.details["apply"]["accepted"] == 0
    assert client.terms["t-contract"]["synonyms"] == []


def test_run_align_validation(env):
    settings, store = env
    client = FakeClient()
    with pytest.raises(JobError, match="documents source"):
        aj.run_align("hanbit", settings=settings, store=store, client=client, check_service=False)
    with pytest.raises(JobError, match="between 0 and 1"):
        aj.run_align(
            "hanbit-docs",
            settings=settings,
            store=store,
            client=client,
            check_service=False,
            min_sim=2.0,
        )
    store.add_source(name="other", kind="files", locator="{}")
    store.set_catalog_database_id(store.get_source("other").id, "db2")
    with pytest.raises(JobError, match="--structured-source"):
        aj.run_align(
            "hanbit-docs", settings=settings, store=store, client=client, check_service=False
        )
    with pytest.raises(JobError, match="is a documents source"):
        aj.run_align(
            "hanbit-docs",
            settings=settings,
            store=store,
            client=client,
            structured_source="hanbit-docs",
            check_service=False,
        )


def test_snapshot_derives_semantic_fks_from_referenced_columns():
    from autoontology.store.db import Source
    from autoontology.structured.semantic import fetch_snapshot

    src = Source("id", "hanbit", "files", "{}", "autoontology_src_hanbit", "db1", "", "")
    snap = fetch_snapshot(FakeClient(), src)
    assert [t.name for t in snap.terms] == ["Contract", "Customer"]
    [fk] = snap.fks
    assert (fk.owner_term_id, fk.target_term_id, fk.column) == (
        "t-contract",
        "t-customer",
        "public.contracts.고객id",
    )
    assert snap.term_map()["t-customer"].attributes[0]["schema"] == "public"


def test_attribute_and_column_concepts_are_never_same():
    term = al.TermInfo(
        "t-contract",
        "Contract",
        "계약",
        attributes=[{"name": "계약금액", "source_column": "계약금액"}, {"name": "계약id"}],
    )

    def judge(concept, candidates):
        return _v("t-contract", "same", 0.95)

    for name, kind in (
        ("계약금액", "entity"),
        ("계약 ID", "entity"),
        ("계약상태", "attribute"),
        ("계약기간", "metric"),
    ):
        v = al.judge_concept(judge, al.ConceptInfo("c", name, kind), [term])
        assert v.relation == "related" and v.notes, name
    v = al.judge_concept(judge, al.ConceptInfo("c", "계약", "entity"), [term])
    assert v.relation == "same" and not v.notes


def test_dry_run_persists_nothing_accepted_and_a_real_run_then_accepts(env):
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    _align(settings, store, client, judge, [], dry_run=True)
    src = store.get_source("hanbit-docs")
    assert AlignmentStore(store).list(src.id) == []  # previews only
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id, preview=True)}
    assert rows["계약"].relation == "same" and not rows["계약"].accepted
    assert rows["계약"].dry_run
    result = _align(settings, store, client, judge, [])  # cached verdicts, now accepted
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}
    assert rows["계약"].accepted and not rows["계약"].dry_run and judge.calls == 3
    assert result.details["apply"]["applied"] == 2


def test_resumed_dry_run_becomes_accepted_on_a_real_resume(env, monkeypatch):
    settings, store = env
    monkeypatch.setenv("AUTOONTOLOGY_EXTRACT_MODE", "structured")
    client, judge = FakeClient(), FlakyJudge("고객")
    first = _align(settings, store, client, judge, [], dry_run=True)
    assert first.status == "failed"
    judge.fail_on = None
    resumed = _align(settings, store, client, judge, [], resume=True)
    assert resumed.status == "succeeded" and resumed.details["judge"]["resumed"] == 4
    src = store.get_source("hanbit-docs")
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}
    assert rows["계약"].accepted and not rows["계약"].dry_run
    assert client.terms["t-contract"]["synonyms"] == ["계약", "계약서"]


def test_failed_embedding_refresh_is_reported_and_retried(env):
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    writer = ClientWriter(client)
    writer.refresh_ok = False
    result = _align(settings, store, client, judge, [], writer=writer)
    info = aj.summary(store, "hanbit-docs", result)
    assert sorted(info["refresh_failed"]) == ["Contract", "Customer"]
    src = store.get_source("hanbit-docs")
    row = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}["계약"]
    assert row.applied["embeddings_refreshed"] is False
    writer.refresh_ok = True
    before = (writer.calls, writer.refreshes)
    again = _align(settings, store, client, judge, [], writer=writer)
    assert again.details["apply"]["refreshed"] == 2 and again.details["apply"]["applied"] == 0
    assert writer.calls == before[0] and writer.refreshes == before[1] + 2
    row = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}["계약"]
    assert row.applied["embeddings_refreshed"] is True
    third = _align(settings, store, client, judge, [], writer=writer)
    assert third.details["apply"]["refreshed"] == 0 and writer.refreshes == before[1] + 2


def test_concurrent_synonym_edit_is_kept(env):
    settings, store = env
    client = FakeClient()
    writer = ClientWriter(client)
    writer.concurrent["t-contract"] = "약정"  # a person edits between plan and write
    result = _align(settings, store, client, FakeJudge(), [], writer=writer)
    assert client.terms["t-contract"]["synonyms"] == ["약정", "계약", "계약서"]
    patch = next(p for p in result.details["apply"]["patches"] if p["term_name"] == "Contract")
    assert patch["synonyms_added"] == ["계약", "계약서"]


def test_metric_same_is_configurable(monkeypatch):
    term = al.TermInfo("t", "Contract", "계약")
    verdict = lambda: al.ValidatedVerdict("t", "same", 0.9, "")  # noqa: E731
    metric = al.ConceptInfo("c", "월매출", "metric")
    assert al.apply_kind_guard(verdict(), metric, term).relation == "related"
    monkeypatch.setenv("AUTOONTOLOGY_ALIGN_ALLOW_METRIC_SAME", "1")
    assert al.apply_kind_guard(verdict(), metric, term).relation == "same"
    attribute = al.ConceptInfo("c", "상태값", "attribute")
    assert al.apply_kind_guard(verdict(), attribute, term).relation == "related"


def test_summary_is_scoped_to_the_structured_source(env):
    settings, store = env
    client = FakeClient()
    result = _align(settings, store, client, FakeJudge(), [])
    src = store.get_source("hanbit-docs")
    other = AlignmentStore(store).list(src.id)[0]
    other.structured_source, other.relation, other.accepted = "other", "same", False
    AlignmentStore(store).put(other)
    assert sum(aj.summary(store, "hanbit-docs", result)["relations"].values()) == 4
    assert sum(aj.summary(store, "hanbit-docs", result, "other")["relations"].values()) == 1


# --- dry runs never touch committed verdicts (review #7, #8) ---------------------------------


def _export_model(store):
    from autoontology.engine.export_job import documents_inputs
    from autoontology.ontology import model as merged

    docs = store.get_source("hanbit-docs")
    return merged.build("x", documents=documents_inputs(store, [docs]))


def test_dry_run_after_a_real_run_keeps_committed_verdicts(env):
    """Review #7: a dry run used to replace the accepted rows with unaccepted previews."""
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    _align(settings, store, client, judge, [])
    src = store.get_source("hanbit-docs")
    before = {
        a.concept_name: (a.accepted, a.relation, a.applied)
        for a in AlignmentStore(store).list(src.id)
    }
    assert before["계약"][0] and before["고객"][0]
    dry = _align(settings, store, client, judge, [], dry_run=True)
    assert dry.status == "succeeded"
    after = {
        a.concept_name: (a.accepted, a.relation, a.applied)
        for a in AlignmentStore(store).list(src.id)
    }
    assert after == before  # committed verdicts untouched, `applied` kept
    previews = AlignmentStore(store).list(src.id, preview=True)
    assert previews and all(p.dry_run and not p.accepted for p in previews)
    info = aj.summary(store, "hanbit-docs", dry)
    assert info["dry_run"] and info["would_accept"] == 2


def test_dry_run_verdicts_never_reach_the_export(env):
    from autoontology.engine.export_job import documents_inputs
    from autoontology.ontology import model as merged
    from autoontology.store.alignments import AlignmentRecord
    from autoontology.structured.semantic import fetch_snapshot

    settings, store = env
    src = store.get_source("hanbit-docs")
    docs = DocumentStore(store)
    c = {x.name: x for x in docs.list_concepts(src.id)}
    AlignmentStore(store).put(
        AlignmentRecord(
            source_id=src.id,
            concept_id=c["날씨"].id,
            concept_name="날씨",
            relation="broader",
            run_id="r",
            structured_source="hanbit",
            term_id="t-contract",
            term_name="Contract",
            confidence=0.9,
            dry_run=True,
        )
    )
    snapshot = fetch_snapshot(FakeClient(), store.get_source("hanbit"))
    model = merged.build("x", structured=[snapshot], documents=documents_inputs(store, [src]))
    assert not [a for a in model.axioms if a.source == "alignment"]
    assert model.alignment_counts.get("broader", 0) == 0


def test_real_resume_after_a_dry_run_commits_and_applies(env):
    """Review #8: `--resume` after a successful dry run used to patch the Terms while the
    verdicts stayed unaccepted dry-run rows (so export did not fold them)."""
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    first = _align(settings, store, client, judge, [], dry_run=True)
    assert first.status == "succeeded" and client.terms["t-contract"]["synonyms"] == []
    calls = judge.calls
    resumed = _align(settings, store, client, judge, [], resume=True)
    assert resumed.run_id == first.run_id and resumed.status == "succeeded"
    assert judge.calls == calls  # acceptance recomputed from the stored verdicts
    src = store.get_source("hanbit-docs")
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}
    assert rows["계약"].accepted and not rows["계약"].dry_run
    assert rows["계약"].applied.get("synonyms_added") == ["계약", "계약서"]
    assert client.terms["t-contract"]["synonyms"] == ["계약", "계약서"]
    model = _export_model(store)
    assert model.alignment_counts["same"] == 2


def test_resume_with_a_stricter_min_conf_recomputes_acceptance(env):
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    first = _align(settings, store, client, judge, [])
    resumed = _align(settings, store, client, judge, [], resume=True, min_conf=0.95)
    assert resumed.run_id == first.run_id
    src = store.get_source("hanbit-docs")
    rows = {a.concept_name: a for a in AlignmentStore(store).list(src.id)}
    assert rows["계약"].accepted and not rows["고객"].accepted  # 0.97 vs 0.92
    assert resumed.details["apply"]["accepted"] == 1


def test_resume_with_another_threshold_starts_a_new_run(env):
    settings, store = env
    client, judge = FakeClient(), FakeJudge()
    first = _align(settings, store, client, judge, [])
    again = _align(settings, store, client, judge, [], resume=True, min_sim=0.3)
    assert again.run_id != first.run_id and again.status == "succeeded"


def test_align_and_build_reread_sources_under_the_lease(env, monkeypatch):
    """Fix wave 2 #7: a source replaced while align/build waited for the lease is refused,
    not used through the stale object."""
    from autoontology.engine import build
    from tests.test_jobs import _replace_on_lease

    settings, store = env
    client = FakeClient()
    _replace_on_lease(store, "hanbit", monkeypatch)
    with pytest.raises(JobError, match="removed or replaced"):
        _align(settings, store, client, FakeJudge(), [])
    _replace_on_lease(store, "hanbit-docs", monkeypatch)
    with pytest.raises(JobError, match="removed or replaced"):
        build.run_build(store, settings, client=client, check_service=False)


# --- identity policy (review #4) ---------------------------------------------------------


class ApproverJudge(FakeJudge):
    """The live failure: a 0.99 `same` for 승인권자 -> Contract."""

    def structured(self, system, human, schema):
        if '"승인권자"' in human.split("\n", 2)[1]:
            self.calls += 1
            return _v("t-contract", "same", 0.99, "승인권자 = Contract")
        return super().structured(system, human, schema)


@pytest.fixture
def approver_env(env):
    settings, store = env
    docs = DocumentStore(store)
    src = store.get_source("hanbit-docs")
    chunk = docs.chunks_for_document(docs.list_documents(src.id)[0].id)[0]
    ev = [EvidenceRecord(chunk.id, "계약은 고객과 맺는다", True)]
    docs.replace_ontology(
        src.id,
        [
            ConceptRecord(
                "계약", "entity", "고객과 맺는 약정", "Contract", ["계약서"], evidence=ev
            ),
            ConceptRecord("고객", "entity", "계약 상대방", "Customer", evidence=ev),
            ConceptRecord("계약금액", "metric", "계약의 총 금액", evidence=ev),
            ConceptRecord(
                "승인권자",
                "entity",
                "계약을 승인하는 사람",
                "Approver",
                ["승인자", "승인 담당자"],
                evidence=ev,
            ),
        ],
        [],
    )
    VECS["승인권자"] = [0.9, 0.1, 0.0]
    yield settings, store
    VECS.pop("승인권자", None)


def _concept(name, name_en="", synonyms=(), kind="entity"):
    return al.ConceptInfo(name, name, kind, "", name_en=name_en, synonyms=list(synonyms))


CONTRACT = al.TermInfo(
    "t-contract",
    "Contract",
    "계약",
    [],
    attributes=[{"name": "계약금액", "source_column": "계약금액"}],
    tables=[{"id": "tb", "name": "contracts"}],
)


def _guard(concept, term=CONTRACT, conf=0.99, min_conf=0.7, auto_added=()):
    verdict = al.ValidatedVerdict(term.id, "same", conf, "r")
    return al.guard_verdict(verdict, concept, term, min_conf=min_conf, auto_added=auto_added)


def test_identity_case1_approver_is_never_merged_into_contract(approver_env):
    settings, store = approver_env
    client, judge = FakeClient(), ApproverJudge()
    # unit: a 0.99 same without any shared alias stays separate, proposal kept
    verdict, proposal = _guard(_concept("승인권자", "Approver", ["승인자", "승인 담당자"]))
    assert verdict.relation == "related" and proposal["relation"] == "same"
    assert proposal["policy"] == al.IDENTITY_POLICY_VERSION and proposal["confidence"] == 0.99
    # end to end: no identity merge, no synonym write for it
    result = _align(settings, store, client, judge, [])
    rows = {
        a.concept_name: a for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
    }
    assert rows["승인권자"].relation == "related" and not rows["승인권자"].accepted
    assert rows["승인권자"].proposal["term_id"] == "t-contract"
    assert rows["승인권자"].applied == {}
    assert client.terms["t-contract"]["synonyms"] == ["계약", "계약서"]
    assert result.details["judge"]["proposals"] == 1
    assert result.details["judge"]["identity_policy"] == al.IDENTITY_POLICY_VERSION
    report = (result.export_dir / "align-report.md").read_text(encoding="utf-8")
    assert (
        "same proposals needing review (1)" in report and "| 승인권자 | Contract | 0.99 |" in report
    )


def test_identity_case2_contract_with_a_trusted_alias_is_eligible():
    verdict, proposal = _guard(_concept("계약", "Contract", ["계약서"]))
    assert verdict.relation == "same" and proposal == {}
    assert any("identity evidence" in n and "contract" in n for n in verdict.notes)
    # the table name is a trusted alias too; normalisation: NFKC, case, spaces
    verdict, _ = _guard(_concept("계약 테이블", "Ｃｏｎｔｒａｃｔｓ"))
    assert verdict.relation == "same"
    # no substring matching: "Contract Approver" is not "Contract"
    verdict, proposal = _guard(_concept("계약 승인자", "Contract Approver"))
    assert verdict.relation == "related" and "no identity evidence" in proposal["reason"]
    # below the identity bar max(--min-conf, 0.90): kept separate even with evidence
    verdict, proposal = _guard(_concept("계약", "Contract"), conf=0.85)
    assert verdict.relation == "related" and "0.85 < 0.90" in proposal["reason"]
    verdict, proposal = _guard(_concept("계약", "Contract"), conf=0.93, min_conf=0.95)
    assert verdict.relation == "related" and "0.93 < 0.95" in proposal["reason"]


def test_identity_case3_metric_guard_still_applies():
    verdict, proposal = _guard(_concept("계약금액", "Contract", kind="metric"))
    assert verdict.relation == "related" and proposal == {}  # kind guard first
    assert any("metric" in n for n in verdict.notes)
    verdict, _ = _guard(_concept("계약금액", "Contract"))  # a column name of the Term
    assert verdict.relation == "related"


def test_identity_case4_auto_added_alias_is_no_evidence(approver_env):
    settings, store = approver_env
    term = al.TermInfo("t-contract", "Contract", "계약", ["승인권자", "계약서"])
    approver = _concept("승인권자", "Approver", ["승인자"])
    # an original (upstream) synonym is evidence ...
    assert _guard(approver, term)[0].relation == "same"
    # ... the same alias added by an earlier alignment is not
    verdict, proposal = _guard(approver, term, auto_added=["승인권자"])
    assert verdict.relation == "related" and proposal
    # end to end: the registry the engine reads
    client = FakeClient()
    client.terms["t-contract"]["synonyms"] = ["승인권자"]
    AlignmentStore(store).record_alias_additions("t-contract", ["승인권자"], run_id="old")
    _align(settings, store, client, ApproverJudge(), [])
    rows = {
        a.concept_name: a for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
    }
    assert rows["승인권자"].relation == "related" and not rows["승인권자"].accepted
    assert client.terms["t-contract"]["synonyms"] == ["승인권자", "계약", "계약서"]
    # apply records what it added: those aliases are never evidence either
    added = AlignmentStore(store).alias_additions()["t-contract"]
    assert set(added) == {"승인권자", "계약", "계약서"}


def _bad_first_run(settings, store, client, monkeypatch):
    """A run under the old rules: the 0.99 same was accepted and its aliases applied."""
    monkeypatch.setattr(al, "apply_identity_policy", lambda v, *a, **k: (v, {}))
    judge = ApproverJudge()
    first = _align(settings, store, client, judge, [])
    monkeypatch.undo()
    rows = {
        a.concept_name: a for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
    }
    assert rows["승인권자"].relation == "same" and rows["승인권자"].accepted
    assert "승인권자" in client.terms["t-contract"]["synonyms"]
    return first, judge


def test_identity_case5_cached_bad_same_is_recomputed(approver_env, monkeypatch):
    settings, store = approver_env
    client = FakeClient()
    _first, judge = _bad_first_run(settings, store, client, monkeypatch)
    calls = judge.calls
    again = _align(settings, store, client, judge, [])
    assert judge.calls == calls  # cached verdicts: no LLM call
    rows = {
        a.concept_name: a for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
    }
    assert rows["승인권자"].relation == "related" and not rows["승인권자"].accepted
    assert rows["승인권자"].proposal["confidence"] == 0.99
    assert rows["계약"].relation == "same" and rows["계약"].accepted
    assert again.details["apply"]["applied"] == 0  # nothing new written
    # the earlier wrong aliases are registered: not evidence on later runs
    assert "승인권자" in AlignmentStore(store).alias_additions()["t-contract"]


def test_identity_case5_resumed_bad_same_is_recomputed(approver_env, monkeypatch):
    settings, store = approver_env
    client = FakeClient()
    first, judge = _bad_first_run(settings, store, client, monkeypatch)
    run = store.get_run(first.run_id)
    params = {k: v for k, v in run.params.items() if k != "identity_policy"}
    with store._conn() as conn:  # a run judged before the policy existed
        conn.execute(
            "UPDATE runs SET params_json = ? WHERE id = ?",
            (__import__("json").dumps(params), run.id),
        )
    calls = judge.calls
    resumed = _align(settings, store, client, judge, [], resume=True)
    assert resumed.run_id == first.run_id and judge.calls == calls
    assert resumed.details["judge"]["resumed"] == 4
    rows = {
        a.concept_name: a for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
    }
    assert rows["승인권자"].relation == "related" and not rows["승인권자"].accepted
    assert rows["승인권자"].proposal["relation"] == "same"
    assert resumed.details["apply"]["accepted"] == 2  # 계약, 고객


def test_identity_case6_rejected_alignment_no_longer_reaches_the_export(approver_env, monkeypatch):
    settings, store = approver_env
    client = FakeClient()
    _bad_first_run(settings, store, client, monkeypatch)
    _align(settings, store, client, ApproverJudge(), [])
    model = _export_model(store)
    own = {c.label for c in model.classes.values() if c.source == "document"}
    assert "승인권자" in own  # kept as its own concept, not folded into Contract
    folded = {
        a.concept_name
        for a in AlignmentStore(store).list(store.get_source("hanbit-docs").id)
        if a.accepted and a.relation == "same"
    }
    assert folded == {"계약", "고객"}  # what export folds into Terms
    # the reviewed alias repair itself is tested in test_upstream_terms
    # (remove_term_synonyms: row lock, whole aliases, embeddings refreshed)
    aligns = AlignmentStore(store)
    aligns.mark_aliases_removed("t-contract", ["승인권자", "승인자", "승인 담당자"])
    assert "승인권자" in aligns.alias_additions()["t-contract"]  # still never evidence


def test_stale_auto_aliases_plan():
    term = al.TermInfo(
        "t",
        "Contract",
        "",
        ["계약", "계약서", "승인권자", "승인자", "약정", "수기 별칭", "남의 별칭"],
        tables=[{"name": "contracts"}],
    )
    prov = {
        "t": [
            {"alias": a, "source_id": "A", "concepts": ["승인권자"]}
            for a in ("계약", "계약서", "승인권자", "승인 자")
        ]
        + [
            {"alias": "수기 별칭", "source_id": None, "concepts": []},
            {"alias": "남의 별칭", "source_id": "C", "concepts": ["x"]},
        ]
    }
    protected = {"t": {al.norm_alias(x) for x in ("계약", "계약서")}}
    stale, kept = al.stale_auto_aliases({"t": term}, prov, protected, "A")
    # only aliases recorded as added by source A and accounted for by no accepted alignment
    # of any source; 약정 (upstream's own) is never touched; whole normalized aliases
    assert [x.alias for x in stale] == ["승인권자", "승인자"]
    assert {(k["alias"], k["reason"]) for k in kept} == {
        ("수기 별칭", "unknown provenance"),
        ("남의 별칭", "added by another documents source"),
    }
    every = {"t": {al.norm_alias(x) for x in ("계약", "계약서", "승인권자", "승인자")}}
    assert al.stale_auto_aliases({"t": term}, prov, every, "A")[0] == []


def _second_docs_source(store, name, concepts):
    docs = DocumentStore(store)
    src = store.add_source(name=name, kind="documents", locator="{}")
    doc, _ = docs.replace_document(
        source_id=src.id,
        path=f"/x/{name}.md",
        title=name,
        mime="text/markdown",
        engine="native",
        sha256=name,
        size=1,
        pages=None,
        chunks=[C(0, "약정")],
    )
    chunk = docs.chunks_for_document(doc.id)[0]
    ev = [EvidenceRecord(chunk.id, "약정", True)]
    docs.replace_ontology(src.id, [ConceptRecord(*c, evidence=ev) for c in concepts], [])
    return src


def test_identity_policy_matches_english_plurals_only_as_whole_aliases():
    assert [
        al.singular(a) for a in ("contracts", "categories", "boxes", "status", "계약들", "gas")
    ] == [
        "contract",
        "category",
        "box",
        "status",
        "계약들",
        "gas",
    ]
    term = al.TermInfo("t", "Contracts", "계약 정보", [], tables=[{"name": "contracts"}])
    same = al.ValidatedVerdict("t", "same", 0.95, "ok")
    concept = al.ConceptInfo("c", "계약", "entity", "약정", name_en="Contract")
    verdict, proposal = al.apply_identity_policy(same, concept, term, min_conf=0.7)
    assert verdict.relation == "same" and proposal == {}
    other = al.ConceptInfo("c2", "계약금", "entity", "돈", name_en="Contract fee")
    verdict, proposal = al.apply_identity_policy(
        al.ValidatedVerdict("t", "same", 0.95, "ok"), other, term, min_conf=0.7
    )
    assert verdict.relation == "related" and proposal["policy"] == "identity-v2"
