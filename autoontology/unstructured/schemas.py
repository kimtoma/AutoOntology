"""LLM output schemas for document extraction and the fail-closed post-validation that
runs on every result before anything is stored."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("autoontology.extract")

ConceptKind = Literal["entity", "metric", "rule", "process", "attribute", "other"]
RelationKind = Literal[
    "is_a",
    "part_of",
    "has_attribute",
    "measured_by",
    "governed_by",
    "produces",
    "consumes",
    "owns",
    "belongs_to",
    "depends_on",
    "related_to",
]

MAX_CONCEPTS_PER_CHUNK = 25
MAX_RELATIONS_PER_CHUNK = 40
MAX_EVIDENCE_CHARS = 200


class ExtractedConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        description="Canonical business name in the source language, 1-6 words, "
        "no trailing punctuation"
    )
    name_en: str = Field(
        default="", description="English rendering of name; empty if the source is already English"
    )
    kind: ConceptKind
    definition: str = Field(description="One or two sentences, grounded ONLY in the provided text")
    synonyms: list[str] = Field(
        default_factory=list, description="Aliases, abbreviations, code names used in the text"
    )
    attributes: list[str] = Field(
        default_factory=list,
        description="Properties/fields explicitly mentioned (e.g. 계약번호, 만료일)",
    )
    evidence: str = Field(
        description="Verbatim quote (<= 200 chars) from the chunk that supports the definition"
    )


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(description="Must equal the `name` of a concept in this output")
    predicate: RelationKind
    object: str = Field(
        description="Must equal the `name` of a concept in this output; must differ from subject"
    )
    label: str = Field(
        default="", description="Free-text relation phrase as written in the text, e.g. '승인한다'"
    )
    evidence: str = Field(description="Verbatim quote (<= 200 chars)")


class ChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concepts: list[ExtractedConcept] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


class MergeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same: bool
    canonical_name: str = ""
    merged_definition: str = ""
    merged_synonyms: list[str] = Field(default_factory=list)


# --- post-validation --------------------------------------------------------------------


@dataclass
class ValidatedConcept:
    name: str
    name_en: str
    kind: str
    definition: str
    synonyms: list[str]
    attributes: list[str]
    evidence: str
    evidence_verified: bool


@dataclass
class ValidatedRelation:
    subject: str
    predicate: str
    object: str
    label: str
    evidence: str
    evidence_verified: bool


@dataclass
class ValidationStats:
    dropped_concepts: int = 0
    merged_duplicates: int = 0
    dropped_relations: int = 0
    unverified_evidence: int = 0
    truncated_concepts: int = 0
    truncated_relations: int = 0


@dataclass
class ValidatedExtraction:
    concepts: list[ValidatedConcept] = field(default_factory=list)
    relations: list[ValidatedRelation] = field(default_factory=list)
    stats: ValidationStats = field(default_factory=ValidationStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concepts": [asdict(c) for c in self.concepts],
            "relations": [asdict(r) for r in self.relations],
            "stats": asdict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidatedExtraction:
        return cls(
            [ValidatedConcept(**c) for c in data.get("concepts", [])],
            [ValidatedRelation(**r) for r in data.get("relations", [])],
            ValidationStats(**data.get("stats", {})),
        )


def collapse_ws(text: str) -> str:
    return " ".join(str(text or "").split())


_TRAILING_PUNCT = ".,;:·、。!?\"'“”‘’()[]{}「」『』<>"


def clean_name(text: str) -> str:
    return collapse_ws(text).strip(_TRAILING_PUNCT + " ")


def name_key(text: str) -> str:
    return clean_name(text).casefold()


_MARKUP_RE = re.compile(r"[*_`#|>\\\"'“”‘’「」『』]+|\s+")


def _loose(text: str) -> str:
    """Whitespace-, markup- and quote-mark-insensitive form for evidence matching (bold,
    table pipes, heading marks, line breaks and the quotes around a defined term in the
    chunk do not break a verbatim quote; models routinely drop them)."""
    return _MARKUP_RE.sub("", text or "").casefold()


MIN_EVIDENCE_CHARS = 8


def min_verifiable(name: str = "") -> int:
    """Shortest (loose) quote that can count as evidence: at least 8 characters and longer
    than the concept name, so a quote that merely repeats the name verifies nothing."""
    return max(MIN_EVIDENCE_CHARS, len(_loose(name)) + 1)


def evidence_verified(quote: str, chunk_text: str, name: str = "") -> bool:
    loose = _loose(quote)
    return len(loose) >= min_verifiable(name) and loose in _loose(chunk_text)


def _clean_list(values: list[str], exclude: set[str]) -> list[str]:
    out: list[str] = []
    seen = set(exclude)
    for value in values:
        cleaned = clean_name(value)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            out.append(cleaned)
    return out


def post_validate(raw: ChunkExtraction, chunk_text: str, *, label: str = "") -> ValidatedExtraction:
    """Fail-closed cleanup of one chunk's LLM output (never fabricates evidence)."""
    stats = ValidationStats()
    concepts: dict[str, ValidatedConcept] = {}
    for item in raw.concepts:
        name, definition = clean_name(item.name), collapse_ws(item.definition)
        if not name or not definition:
            stats.dropped_concepts += 1
            continue
        key = name.casefold()
        quote = collapse_ws(item.evidence)
        verified = evidence_verified(quote, chunk_text, name)
        if key in concepts:
            stats.merged_duplicates += 1
            first = concepts[key]
            first.synonyms = _clean_list(first.synonyms + item.synonyms, {key})
            first.attributes = _clean_list(first.attributes + item.attributes, set())
            if verified and not first.evidence_verified:  # keep the evidence that holds
                first.evidence, first.evidence_verified = quote[:MAX_EVIDENCE_CHARS], True
            continue
        concepts[key] = ValidatedConcept(
            name=name,
            name_en=collapse_ws(item.name_en),
            kind=item.kind,
            definition=definition,
            synonyms=_clean_list(item.synonyms, {key}),
            attributes=_clean_list(item.attributes, set()),
            evidence=quote[:MAX_EVIDENCE_CHARS],
            evidence_verified=verified,
        )
    kept = list(concepts.values())
    if len(kept) > MAX_CONCEPTS_PER_CHUNK:
        stats.truncated_concepts = len(kept) - MAX_CONCEPTS_PER_CHUNK
        logger.warning(
            "%s: %d concepts, keeping the first %d",
            label or "chunk",
            len(kept),
            MAX_CONCEPTS_PER_CHUNK,
        )
        kept = kept[:MAX_CONCEPTS_PER_CHUNK]
    names = {c.name.casefold(): c.name for c in kept}
    relations: list[ValidatedRelation] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw.relations:
        subject, obj = names.get(name_key(item.subject)), names.get(name_key(item.object))
        if subject is None or obj is None or subject == obj:
            stats.dropped_relations += 1
            continue
        key3 = (subject, item.predicate, obj)
        if key3 in seen:
            stats.dropped_relations += 1
            continue
        seen.add(key3)
        quote = collapse_ws(item.evidence)
        relations.append(
            ValidatedRelation(
                subject=subject,
                predicate=item.predicate,
                object=obj,
                label=collapse_ws(item.label),
                evidence=quote[:MAX_EVIDENCE_CHARS],
                evidence_verified=evidence_verified(quote, chunk_text),
            )
        )
    if len(relations) > MAX_RELATIONS_PER_CHUNK:
        stats.truncated_relations = len(relations) - MAX_RELATIONS_PER_CHUNK
        logger.warning(
            "%s: %d relations, keeping the first %d",
            label or "chunk",
            len(relations),
            MAX_RELATIONS_PER_CHUNK,
        )
        relations = relations[:MAX_RELATIONS_PER_CHUNK]
    stats.unverified_evidence = sum(not c.evidence_verified for c in kept) + sum(
        not r.evidence_verified for r in relations
    )
    return ValidatedExtraction(kept, relations, stats)


def reverify(ext: ValidatedExtraction, chunk_text: str) -> ValidatedExtraction:
    """Recompute the evidence flags of a stored extraction with the current rules (no LLM),
    so a rule change applies to chunks extracted earlier."""
    concepts = [
        ValidatedConcept(
            **{**asdict(c), "evidence_verified": evidence_verified(c.evidence, chunk_text, c.name)}
        )
        for c in ext.concepts
    ]
    relations = [
        ValidatedRelation(
            **{**asdict(r), "evidence_verified": evidence_verified(r.evidence, chunk_text)}
        )
        for r in ext.relations
    ]
    stats = ValidationStats(**asdict(ext.stats))
    stats.unverified_evidence = sum(not c.evidence_verified for c in concepts) + sum(
        not r.evidence_verified for r in relations
    )
    return ValidatedExtraction(concepts, relations, stats)


# --- JSON-mode parsing ------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json_object(text: str) -> Any:
    """The JSON object in a model answer (tolerates ```json fences and surrounding prose)."""
    body = _FENCE_RE.sub("", (text or "").strip())
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(body[start : end + 1])


# --- alignment (the align step of ``autoontology build``) --------------------------------------------------

AlignRelation = Literal["same", "broader", "narrower", "related", "none"]


class AlignmentVerdict(BaseModel):
    """The judge's answer for one document concept against up to 5 candidate Terms.

    ``broader`` means the Term is broader than the concept (concept ⊑ Term); ``narrower``
    the opposite (Term ⊑ concept)."""

    model_config = ConfigDict(extra="forbid")
    term_id: str = Field(
        description="The `id` of the chosen candidate Term exactly as given; "
        "empty string when relation is none"
    )
    relation: AlignRelation = Field(
        description="same = the concept and the Term denote the "
        "same business thing; broader = the Term is "
        "broader than the concept; narrower = the "
        "Term is narrower than the concept; related = "
        "associated but neither; none = no candidate "
        "fits"
    )
    confidence: float = Field(description="Confidence in this verdict, 0 to 1")
    rationale: str = Field(description="One short sentence explaining the verdict")


@dataclass
class ValidatedVerdict:
    term_id: str
    relation: str
    confidence: float
    rationale: str
    notes: list[str] = field(default_factory=list)


def validate_verdict(verdict: AlignmentVerdict, candidates: dict[str, str]) -> ValidatedVerdict:
    """Fail-closed post-validation. *candidates* maps candidate Term id -> name.

    The term id must be one of the candidates (an exact Term name is accepted as its id);
    anything else becomes ``none``. ``none`` never carries a term id. Confidence is clamped
    to [0, 1] (NaN -> 0)."""
    notes: list[str] = []
    raw_id = (verdict.term_id or "").strip()
    by_id = {k.casefold(): k for k in candidates}
    by_name = {v.casefold(): k for k, v in candidates.items() if v}
    term_id = by_id.get(raw_id.casefold()) or by_name.get(raw_id.casefold()) or ""
    if raw_id and term_id and term_id != raw_id:
        notes.append(f"term_id {raw_id!r} mapped to candidate {term_id}")
    relation = verdict.relation
    if relation != "none" and not term_id:
        notes.append(f"term_id {raw_id!r} is not a candidate: relation {relation} -> none")
        relation = "none"
    if relation == "none":
        term_id = ""
    confidence = verdict.confidence
    if confidence != confidence:  # NaN
        confidence = 0.0
    clamped = min(1.0, max(0.0, float(confidence)))
    if clamped != confidence:
        notes.append(f"confidence {confidence} clamped to {clamped}")
    return ValidatedVerdict(
        term_id, relation, clamped, collapse_ws(verdict.rationale or "")[:500], notes
    )
