import json

import pytest
from pydantic import ValidationError

from autoontology.unstructured import consolidate, extractor, prompts, schemas
from autoontology.unstructured.schemas import (
    ChunkExtraction,
    MergeDecision,
    ValidatedConcept,
    ValidatedExtraction,
    ValidatedRelation,
    post_validate,
)

CHUNK = "제2조(정의) **계약**이란 고객과 체결한 약정을 말한다.\n| 계약금액 | 부가세 제외 원 |"


def _concept(name, definition="정의", evidence="계약이란 고객과 체결한", **kw):
    return {"name": name, "kind": kw.pop("kind", "entity"), "definition": definition,
            "evidence": evidence, **kw}


def test_schema_is_strict():
    with pytest.raises(ValidationError):
        ChunkExtraction.model_validate({"concepts": [_concept("계약", extra=1)]})
    with pytest.raises(ValidationError):
        ChunkExtraction.model_validate({"concepts": [_concept("계약", kind="thing")]})


def test_post_validate_rules():
    raw = ChunkExtraction.model_validate({
        "concepts": [
            _concept("  계약. ", evidence="계약이란   고객과 체결한", synonyms=["약정", "계약"]),
            _concept("계약", synonyms=["contract"], attributes=["계약번호"]),  # duplicate
            _concept("고객", evidence="고객은 왕이다"),  # not in the chunk
            _concept("   ", definition="x"),
            _concept("빈정의", definition="  "),
            _concept("계약금액", kind="attribute", evidence="| 계약금액 | 부가세 제외 원 |"),
        ],
        "relations": [
            {"subject": "계약", "predicate": "belongs_to", "object": "고객",
             "evidence": "고객과 체결한 약정을"},
            {"subject": "계약", "predicate": "belongs_to", "object": "고객", "evidence": "dup"},
            {"subject": "계약", "predicate": "is_a", "object": "계약", "evidence": "x"},
            {"subject": "계약", "predicate": "owns", "object": "없는개념", "evidence": "x"},
            {"subject": "계약", "predicate": "has_attribute", "object": "계약금액",
             "label": "갖는다", "evidence": "없는 인용"},
        ],
    })
    out = post_validate(raw, CHUNK)
    names = [c.name for c in out.concepts]
    assert names == ["계약", "고객", "계약금액"]
    first = out.concepts[0]
    assert first.evidence_verified and first.synonyms == ["약정", "contract"]
    assert first.attributes == ["계약번호"] and first.evidence == "계약이란 고객과 체결한"
    assert not out.concepts[1].evidence_verified  # kept, flagged, never fabricated
    assert out.concepts[1].evidence == "고객은 왕이다"
    assert out.concepts[2].evidence_verified  # table pipes / bold ignored for matching
    assert [(r.subject, r.predicate, r.object) for r in out.relations] == [
        ("계약", "belongs_to", "고객"), ("계약", "has_attribute", "계약금액")]
    assert out.relations[0].evidence_verified and not out.relations[1].evidence_verified
    s = out.stats
    assert (s.dropped_concepts, s.merged_duplicates, s.dropped_relations,
            s.unverified_evidence) == (2, 1, 3, 2)
    assert ValidatedExtraction.from_dict(json.loads(json.dumps(out.to_dict()))) == out


def test_post_validate_caps_are_deterministic():
    raw = ChunkExtraction.model_validate({
        "concepts": [_concept(f"개념{i}") for i in range(30)],
        "relations": [{"subject": f"개념{i}", "predicate": "related_to",
                       "object": f"개념{j}", "evidence": "x"}
                      for i in range(10) for j in range(10) if i != j],
    })
    out = post_validate(raw, CHUNK)
    assert len(out.concepts) == 25 and out.concepts[-1].name == "개념24"
    assert len(out.relations) == 40 and out.stats.truncated_concepts == 5
    assert out.stats.truncated_relations == 50
    assert (out.relations[0].subject, out.relations[0].object) == ("개념0", "개념1")


def test_evidence_is_capped_at_200_chars():
    long = "가" * 300
    raw = ChunkExtraction.model_validate({"concepts": [_concept("가", evidence=long)]})
    out = post_validate(raw, long)
    assert out.concepts[0].evidence_verified and len(out.concepts[0].evidence) == 200


def test_parse_json_object_tolerates_fences():
    assert schemas.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert schemas.parse_json_object('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        schemas.parse_json_object("no json here")


def test_human_message_keeps_braces_literal():
    msg = prompts.human_message(doc_title="규정", section_path="", index=0, total=2,
                                chunk_text="값 {x} 와 {}")
    assert msg == "[Document] 규정  [Section] -  [Chunk 1/2]\n\n값 {x} 와 {}"
    assert "untrusted" in prompts.SYSTEM_PROMPT
    assert '"concepts"' in prompts.json_mode_system_prompt()


class FakeBackend:
    def __init__(self, structured=(), texts=()):
        self.structured_answers = list(structured)
        self.text_answers = list(texts)
        self.structured_calls = 0
        self.prompts = []

    def structured(self, system, human, schema):
        self.structured_calls += 1
        answer = self.structured_answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer)

    def text(self, prompt):
        self.prompts.append(prompt)
        return self.text_answers.pop(0)


def _job(key="c1", text=CHUNK):
    return extractor.ChunkJob(key=key, doc_title="규정", section_path="제2조", index=0, total=1,
                              text=text)


GOOD = {"concepts": [_concept("계약")], "relations": []}


def test_extractor_structured_path():
    backend = FakeBackend(structured=[GOOD])
    out = extractor.Extractor(backend, mode="auto").extract(_job())
    assert out.error is None and out.detail["path"] == "structured"
    assert [c.name for c in out.extraction.concepts] == ["계약"]
    assert out.detail["concepts"] == 1 and out.detail["unverified_evidence"] == 0


def test_extractor_json_fallback_with_feedback():
    backend = FakeBackend(structured=[RuntimeError("Invalid JSON: fenced")],
                          texts=["not json", "```json\n" + json.dumps(GOOD) + "\n```"])
    out = extractor.Extractor(backend, mode="auto").extract(_job())
    assert out.error is None and out.detail["path"] == "json_fallback"
    assert out.detail["json_attempts"] == 2 and "fenced" in out.detail["structured_error"]
    assert "Your previous answer:\nnot json" in backend.prompts[1]
    assert "JSON Schema" in backend.prompts[0]


def test_extractor_failure_is_an_outcome_not_an_exception():
    backend = FakeBackend(structured=[RuntimeError("bad")], texts=["x", "y", "z"])
    out = extractor.Extractor(backend, mode="auto").extract(_job())
    assert out.extraction is None and "JSONDecodeError" in out.error
    conn = FakeBackend(structured=[ConnectionRefusedError("refused")])
    out = extractor.Extractor(conn, mode="auto").extract(_job())
    assert out.extraction is None and conn.prompts == []  # no JSON retry when down


def test_extractor_switches_to_json_after_streak():
    backend = FakeBackend(structured=[RuntimeError("e")] * 3,
                          texts=[json.dumps(GOOD)] * 5)
    ex = extractor.Extractor(backend, mode="auto")
    outcomes = ex.run([_job(f"c{i}") for i in range(5)], workers=1)
    assert backend.structured_calls == 3
    assert sorted(o.detail["path"] for o in outcomes) == ["json", "json", "json_fallback",
                                                          "json_fallback", "json_fallback"]


def test_extract_mode_env(monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_EXTRACT_MODE", "bogus")
    with pytest.raises(ValueError, match="AUTOONTOLOGY_EXTRACT_MODE"):
        extractor.extract_mode()


# --- consolidation ------------------------------------------------------------------


def _vc(name, definition="d", synonyms=(), kind="entity", evidence="q", verified=True,
        attributes=()):
    return ValidatedConcept(name, "", kind, definition, list(synonyms), list(attributes),
                            evidence, verified)


def _vr(s, p, o, evidence="r"):
    return ValidatedRelation(s, p, o, "", evidence, True)


def test_exact_merge_without_llm():
    exts = [
        ("k1", ValidatedExtraction([_vc("고객", "거래 상대방", ["거래처"]), _vc("계약")],
                                   [_vr("계약", "belongs_to", "고객")])),
        ("k2", ValidatedExtraction([_vc("거래처", "물품을 사는 회사", attributes=["고객ID"]),
                                    _vc("고객", "고객이란 계약 상대방", evidence="q2"),
                                    _vc("계약")],
                                   [_vr("계약", "belongs_to", "거래처"),
                                    _vr("거래처", "related_to", "고객")])),
    ]

    def boom(*_):
        raise AssertionError("no LLM for exact merges")

    result = consolidate.consolidate(exts, embedder=None, judge=boom)
    by = {c.name: c for c in result.concepts}
    assert set(by) == {"고객", "계약"}
    cust = by["고객"]
    assert cust.synonyms == ["거래처"] and cust.attributes == ["고객ID"]
    assert {(m["from"], m["into"], m["via"]) for m in cust.merged_from} == {
        ("거래처", "고객", "synonym")}
    assert cust.definition == "고객이란 계약 상대방"  # longest verified under the canonical name
    assert len(cust.evidence) == 3 and len(by["계약"].evidence) == 2
    [rel] = result.relations
    assert (rel.subject, rel.predicate, rel.object) == ("계약", "belongs_to", "고객")
    assert [e.chunk_id for e in rel.evidence] == ["k1", "k2"]
    assert result.stats.exact_merges == 3 and result.stats.self_relations_dropped == 1


def test_embedding_and_judge_path():
    exts = [("k1", ValidatedExtraction(
        [_vc("청구서", "대금 청구 문서"), _vc("인보이스", "청구 문서"), _vc("연체", "기한 경과")],
        [_vr("연체", "related_to", "인보이스"), _vr("인보이스", "related_to", "청구서")]))]
    vectors = {"청구서": [1.0, 0.0], "인보이스": [0.95, 0.31], "연체": [0.0, 1.0]}
    seen = []

    def embed(texts):
        return [vectors[t.split(":")[0]] for t in texts]

    def judge(a, b):
        seen.append((a["name"], b["name"]))
        return MergeDecision(same=True, canonical_name="청구서", merged_definition="청구 문서.",
                             merged_synonyms=["인보이스", "invoice"])

    result = consolidate.consolidate(exts, embedder=embed, judge=judge, threshold=0.8)
    assert seen == [("청구서", "인보이스")]  # 연체 is below the threshold: never judged
    by = {c.name: c for c in result.concepts}
    assert set(by) == {"청구서", "연체"}
    assert by["청구서"].definition == "청구 문서." and by["청구서"].synonyms == ["인보이스",
                                                                              "invoice"]
    assert [(r.subject, r.object) for r in result.relations] == [("연체", "청구서")]
    s = result.stats
    assert (s.similar_pairs, s.judge_calls, s.judge_merges, s.self_relations_dropped) == (
        1, 1, 1, 1)


def test_judge_fail_closed():
    exts = [("k1", ValidatedExtraction([_vc("A"), _vc("B")], []))]
    embed = lambda texts: [[1.0, 0.0]] * len(texts)  # noqa: E731

    def bad_judge(a, b):
        raise RuntimeError("model down")

    result = consolidate.consolidate(exts, embedder=embed, judge=bad_judge)
    assert {c.name for c in result.concepts} == {"A", "B"}
    assert result.stats.judge_failures == 1

    def invented(a, b):
        return MergeDecision(same=True, canonical_name="전혀다른이름", merged_definition="")

    result = consolidate.consolidate(exts, embedder=embed, judge=invented)
    [c] = result.concepts
    assert c.name == "A" and c.definition == "d"  # invented canonical name ignored
    result = consolidate.consolidate(exts, embedder=embed, judge=invented, max_judge=0)
    assert len(result.concepts) == 2

    def broken_embed(texts):
        raise RuntimeError("embed down")

    result = consolidate.consolidate(exts, embedder=broken_embed, judge=invented)
    assert len(result.concepts) == 2 and "embed down" in result.stats.embedding_error


def test_merge_env(monkeypatch):
    monkeypatch.setenv("AUTOONTOLOGY_MERGE_THRESHOLD", "1.5")
    with pytest.raises(ValueError):
        consolidate.threshold_from_env()
    monkeypatch.setenv("AUTOONTOLOGY_MERGE_THRESHOLD", "0.9")
    assert consolidate.threshold_from_env() == 0.9


def test_judge_uses_json_fallback():
    backend = FakeBackend(structured=[RuntimeError("fenced")],
                          texts=['{"same": false, "canonical_name": "", '
                                 '"merged_definition": "", "merged_synonyms": []}'])
    judge = extractor.make_judge(backend, mode="auto")
    assert judge({"name": "a"}, {"name": "b"}).same is False


def test_evidence_ignores_quote_marks_but_not_typos():
    text = '3. "수납"이란 고객이 청구서에 따라 지급한 대금을 받는 것을 말한다.'
    assert schemas.evidence_verified("수납이란 고객이 청구서에 따라", text)
    assert not schemas.evidence_verified("수납이란 고객이 청구서로", text)
    assert not schemas.evidence_verified("", text)


def test_workers_default_is_one_for_ollama(isolated_env, monkeypatch):
    from autoontology.config import Settings

    assert extractor.workers_default(Settings.from_env()) == 1
    monkeypatch.setenv("AUTOONTOLOGY_CHAT_PROVIDER", "openai")
    assert extractor.workers_default(Settings.from_env()) == 3
    monkeypatch.setenv("AUTOONTOLOGY_LLM_WORKERS", "5")
    assert extractor.workers_default(Settings.from_env()) == 5


def test_chained_judge_merges_keep_the_latest_canonical():
    exts = [("k1", ValidatedExtraction(
        [_vc("알파", "a 정의", evidence="qa"), _vc("베타", "b 정의", evidence="qb"),
         _vc("감마", "c 정의", evidence="qc")], []))]
    seen = []

    def judge(a, b):
        seen.append((a["name"], a["definition"], b["name"]))
        if len(seen) == 1:
            return MergeDecision(same=True, canonical_name="베타", merged_definition="AB 정의",
                                 merged_synonyms=["알파"])
        return MergeDecision(same=True, canonical_name=a["name"],
                             merged_definition=a["definition"] + " + C",
                             merged_synonyms=[b["name"]])

    result = consolidate.consolidate(exts, embedder=lambda t: [[1.0, 0.0]] * len(t),
                                     judge=judge, threshold=0.8)
    # the second call sees the merged group, not the stale pre-merge summary
    assert seen == [("알파", "a 정의", "베타"), ("베타", "AB 정의", "감마")]
    [c] = result.concepts
    assert c.name == "베타" and c.definition == "AB 정의 + C"
    assert set(c.synonyms) == {"알파", "감마"}
    assert sorted(e.quote for e in c.evidence) == ["qa", "qb", "qc"]
    assert [m["via"] for m in c.merged_from] == ["judge", "judge"]
    assert result.stats.judge_calls == 2 and result.stats.judge_merges == 2


def test_synonym_merge_needs_the_same_kind_but_name_merge_does_not():
    exts = [("k1", ValidatedExtraction([_vc("고객", synonyms=["거래처"]),
                                        _vc("거래처", kind="attribute")], [])),
            ("k2", ValidatedExtraction([_vc("계약")], [])),
            ("k3", ValidatedExtraction([_vc("계약", kind="process")], []))]
    result = consolidate.consolidate(exts)
    by = {c.name: c for c in result.concepts}
    assert set(by) == {"고객", "거래처", "계약"}  # synonym across kinds: not merged
    assert by["거래처"].kind == "attribute" and by["고객"].merged_from == []
    assert result.stats.exact_merges == 1  # the two 계약 (same name, different kind)


def test_empty_quotes_are_not_evidence():
    exts = [("k1", ValidatedExtraction([_vc("계약", evidence="")], [])),
            ("k2", ValidatedExtraction([_vc("계약", evidence="계약 원문")], []))]
    [c] = consolidate.consolidate(exts).concepts
    assert [e.quote for e in c.evidence] == ["계약 원문"]


def test_short_quotes_do_not_verify_and_verified_duplicate_wins():
    text = "제3조 계약금액이란 계약서에 기재된 총 거래금액을 말한다. 계약은 승인 후 체결한다."
    raw = ChunkExtraction.model_validate({
        "concepts": [
            _concept("계약금액", evidence="계약금액"),  # only the name itself
            _concept("계약", evidence="조작된 인용"),
            _concept("계약", evidence="계약은 승인 후 체결한다"),
            _concept("승인", evidence="승인 후"),  # < 8 chars
            _concept("총액", evidence=""),
        ],
        "relations": [{"subject": "계약", "predicate": "governed_by", "object": "승인",
                       "evidence": "승인 후"}],
    })
    out = post_validate(raw, text)
    by = {c.name: c for c in out.concepts}
    assert not by["계약금액"].evidence_verified and not by["승인"].evidence_verified
    assert by["계약"].evidence_verified and by["계약"].evidence == "계약은 승인 후 체결한다"
    assert by["총액"].evidence == "" and not by["총액"].evidence_verified
    assert not out.relations[0].evidence_verified
    assert schemas.min_verifiable("계약금액") == 8
    assert schemas.min_verifiable("가" * 10) == 11


def test_timeouts_do_not_fall_back_to_json_mode():
    import httpx

    class APITimeoutError(Exception):
        pass

    for exc in (APITimeoutError("Request timed out."), httpx.ReadTimeout("read timeout")):
        backend = FakeBackend(structured=[exc])
        out = extractor.Extractor(backend, mode="auto").extract(_job())
        assert out.extraction is None and backend.prompts == []


def test_reverify_applies_current_rules_to_stored_extractions():
    stored = ValidatedExtraction([_vc("계약", evidence="계약", verified=True),
                                  _vc("고객", evidence="고객과 체결한 약정을", verified=False)],
                                 [ValidatedRelation("계약", "related_to", "고객", "",
                                                    "고객과 체결한 약정", False)])
    out = schemas.reverify(stored, CHUNK)
    assert [c.evidence_verified for c in out.concepts] == [False, True]
    assert out.relations[0].evidence_verified and out.stats.unverified_evidence == 1


def test_upstream_retries_are_counted_per_chunk(monkeypatch):
    import logging

    monkeypatch.setenv("LLM_INVOKE_TIMEOUT_S", "0.0001")
    upstream = logging.getLogger(extractor.UPSTREAM_LLM_LOGGER)

    class Retrying(FakeBackend):
        def structured(self, system, human, schema):
            upstream.warning("Retryable LLM error (endpoint saturated/rate-limited) on "
                             "attempt 1/3 for ChunkExtraction — retrying in 2.8s")
            return super().structured(system, human, schema)

    out = extractor.Extractor(Retrying(structured=[GOOD]), mode="auto").extract(_job())
    assert out.detail["upstream_retries"] == 1 and out.detail["llm_timeout_s"] == 0.0001
    out = extractor.Extractor(FakeBackend(structured=[GOOD]), mode="auto").extract(_job())
    assert out.detail["upstream_retries"] == 0


def test_same_name_merges_even_if_an_earlier_kind_used_it_as_synonym():
    exts = [("k1", ValidatedExtraction([_vc("수납상태", kind="attribute",
                                            synonyms=["상태"])], [])),
            ("k2", ValidatedExtraction([_vc("상태")], [])),
            ("k3", ValidatedExtraction([_vc("상태", evidence="q3")], []))]
    result = consolidate.consolidate(exts)
    by = {c.name: c for c in result.concepts}
    assert set(by) == {"수납상태", "상태"} and result.stats.exact_merges == 1
    for c in result.concepts:
        assert all(m["from"] != m["into"] for m in c.merged_from)
    assert len(by["상태"].evidence) == 2


def test_retry_counter_matches_the_vendored_upstream_wording():
    """Pins the strings _RetryCounter keys on to vendor/auto-ontology's llm_invoke.py, so an
    upstream bump that rewords them fails here (over_timeout stays the robust signal)."""
    import logging

    from autoontology.config import VENDOR_DIR

    source = (VENDOR_DIR / "auto_ontology" / "utils" / "llm_invoke.py").read_text()
    templates = [
        "LLM invoke timed out after %ds on attempt %d/%d for %s",
        "Retryable LLM error (endpoint saturated/rate-limited) on attempt ",
        "Retryable LLM error on attempt %d/%d — retrying in %.1fs: %s",
    ]
    for template in templates:
        assert template in source, template
    counter = extractor._RetryCounter()
    samples = ["LLM invoke timed out after 180s on attempt 1/3 for ChunkExtraction",
               "Retryable LLM error (endpoint saturated/rate-limited) on attempt 1/3 for "
               "ChunkExtraction — retrying in 2.8s",
               "Retryable LLM error on attempt 1/3 — retrying in 2.1s: boom"]
    for message in samples:
        counter.emit(logging.LogRecord("x", logging.WARNING, "f", 1, message, None, None))
    assert sum(counter.counts.values()) == 3


def test_swallowed_failures_mark_consolidation_incomplete():
    """Review #10: judge errors, embedder errors and missing vectors make the result
    incomplete (the job then neither caches it nor reports success)."""
    exts = [("k1", ValidatedExtraction([_vc("A"), _vc("B")], []))]
    ok = consolidate.consolidate(exts, embedder=lambda t: [[1.0, 0.0]] * len(t),
                                 judge=lambda a, b: MergeDecision(same=False))
    assert not ok.stats.incomplete and ok.stats.to_dict()["incomplete"] is False

    def bad_judge(a, b):
        raise RuntimeError("model down")

    def down(texts):
        raise ConnectionError("down")

    assert consolidate.consolidate(exts, embedder=lambda t: [[1.0, 0.0]] * len(t),
                                   judge=bad_judge).stats.incomplete
    assert consolidate.consolidate(exts, embedder=down, judge=bad_judge).stats.incomplete
    missing = consolidate.consolidate(exts, embedder=lambda t: [[1.0, 0.0], None],
                                      judge=bad_judge)
    assert missing.stats.missing_vectors == 1 and missing.stats.incomplete
