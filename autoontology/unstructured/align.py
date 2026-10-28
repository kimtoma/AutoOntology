# SPDX-License-Identifier: MIT
"""Alignment of document concepts to upstream Terms (the align step of ``autoontology build``).

1. Candidate retrieval: every entity/metric/attribute concept is embedded as a query
   (``"{name} ({name_en}): {definition}"``), every Term as a passage
   (``"{name}: {description} | synonyms: ... | columns: ..."``, cached by text hash and
   model); the top-k Terms by cosine above a floor are the candidates.
2. LLM judge (:class:`AlignmentVerdict`), post-validated fail-closed
   (:func:`validate_verdict`).
3. Identity policy (:func:`apply_identity_policy`, versioned): ``same`` needs confidence
   >= max(--min-conf, 0.90) and a normalized full-alias match with the Term's trusted
   aliases (never ones AutoOntology added itself); otherwise ``related`` + a review proposal.
   ``identity-v2.1`` rule: aliases are compared after NFKC, case folding and whitespace
   removal, and an all-Latin alias longer than 3 characters also matches as its English
   singular (:func:`singular`: ``Contracts`` = ``Contract``, ``categories`` = ``category``,
   ``boxes`` = ``box``; ``-ss``/``-us``/``-is`` are kept). Plurals that are a different
   word than their "singular" are on a stop-list (:data:`NO_SINGULAR`: ``goods/good``,
   ``customs/custom``, ``news/new``, ``arms/arm``, ``glasses/glass``, ``means/mean``) and
   only match exactly, also as the last word of a longer alias (``Trade Goods`` is not
   ``Trade Good``; :func:`last_word`). Whole aliases only, never substrings.
4. Apply policy for accepted ``same`` verdicts: add the concept's
   name and synonyms to the Term's synonyms (case-insensitive dedupe, never remove), and
   set the description only when the Term has none or upstream's fallback.

The pieces here are pure (no store, no HTTP): ``engine/align.py`` wires them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from autoontology.engine.jobs import FALLBACK_TERM_PREFIX
from autoontology.unstructured import prompts
from autoontology.unstructured.consolidate import _cosine
from autoontology.unstructured.extractor import LLMBackend, _is_connection_error, invoke_json
from autoontology.unstructured.schemas import (
    AlignmentVerdict,
    ValidatedVerdict,
    validate_verdict,
)

logger = logging.getLogger("autoontology.align")

ALIGNABLE_KINDS = frozenset({"entity", "metric", "attribute"})
DEFAULT_TOP_K = 5
DEFAULT_MIN_SIM = 0.55
DEFAULT_MIN_CONF = 0.7


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def min_sim_from_env() -> float:
    return _float_env("AUTOONTOLOGY_ALIGN_MIN_SIM", DEFAULT_MIN_SIM)


def min_conf_from_env() -> float:
    return _float_env("AUTOONTOLOGY_ALIGN_MIN_CONF", DEFAULT_MIN_CONF)


@dataclass
class TermInfo:
    """One upstream Term as alignment and export need it."""

    id: str
    name: str
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    #: ColumnAttributes: {id, name, source_column, datatype, table, schema, column,
    #: referenced: [{table_id, table, schema, column}]}
    attributes: list[dict[str, Any]] = field(default_factory=list)
    #: REPRESENTS tables: {id, name, schema}
    tables: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_fallback(self) -> bool:
        description = (self.description or "").strip()
        return not description or description.startswith(FALLBACK_TERM_PREFIX)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "synonyms": list(self.synonyms),
            "attributes": self.attributes,
            "tables": self.tables,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TermInfo:
        return cls(
            id=data["id"],
            name=data.get("name") or "",
            description=data.get("description") or "",
            synonyms=list(data.get("synonyms") or []),
            attributes=list(data.get("attributes") or []),
            tables=list(data.get("tables") or []),
        )


@dataclass
class ConceptInfo:
    id: str
    name: str
    kind: str
    definition: str = ""
    name_en: str = ""
    synonyms: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    evidence: str = ""


def concept_text(c: ConceptInfo) -> str:
    head = f"{c.name} ({c.name_en})" if c.name_en else c.name
    return f"{head}: {c.definition}"


def term_text(t: TermInfo) -> str:
    columns = ", ".join(a.get("name") or "" for a in t.attributes if a.get("name"))
    return f"{t.name}: {t.description} | synonyms: {', '.join(t.synonyms)} | columns: {columns}"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candidate:
    term_id: str
    similarity: float


def top_candidates(
    vector: Sequence[float] | None,
    term_vectors: dict[str, Sequence[float] | None],
    *,
    k: int = DEFAULT_TOP_K,
    floor: float = DEFAULT_MIN_SIM,
) -> list[Candidate]:
    """Top-*k* Terms by cosine with similarity >= *floor* (ties broken by term id)."""
    if not vector:
        return []
    scored = [
        Candidate(tid, round(_cosine(vector, tv), 4)) for tid, tv in term_vectors.items() if tv
    ]
    scored = [c for c in scored if c.similarity >= floor]
    scored.sort(key=lambda c: (-c.similarity, c.term_id))
    return scored[:k]


# --- judge --------------------------------------------------------------------------------


def concept_payload(c: ConceptInfo) -> dict[str, Any]:
    return {
        "name": c.name,
        "name_en": c.name_en,
        "kind": c.kind,
        "definition": c.definition,
        "synonyms": c.synonyms,
        "attributes": c.attributes,
        "evidence": c.evidence,
    }


def candidate_payload(t: TermInfo) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "synonyms": t.synonyms,
        "columns": [a.get("name") for a in t.attributes if a.get("name")],
    }


def _judged_term(
    c: dict[str, Any],
    auto_added: dict[str, Sequence[str]] | None,
    auto_descriptions: dict[str, set[str]] | None,
) -> dict[str, Any]:
    """A candidate payload as the judge cache sees it: id, name, columns, description and
    synonyms minus what ``apply`` wrote (see :func:`judge_key`)."""
    tid = c.get("id")
    mine = set((auto_added or {}).get(tid, ()))  # the exact strings apply wrote
    description = str(c.get("description") or "").strip()
    auto = (auto_descriptions or {}).get(tid, set())
    if (
        ANY_DESCRIPTION in auto
        or description in auto
        or description.startswith(FALLBACK_TERM_PREFIX)  # what apply may replace
    ):
        description = ""
    return {
        **{k: c.get(k) for k in ("id", "name", "columns")},
        "description": description,
        "synonyms": [x for x in c.get("synonyms") or [] if x not in mine],
    }


def terms_fingerprint(
    terms: Sequence[TermInfo],
    auto_added: dict[str, Sequence[str]] | None = None,
    auto_descriptions: dict[str, set[str]] | None = None,
) -> str:
    """A hash of the upstream Terms align judges against (sorted by id: name, columns,
    description, synonyms, tables), minus apply's own writes as in :func:`judge_key`, so a
    Term edited, added or removed upstream changes it and apply alone does not."""
    rows = sorted(
        (
            {
                **_judged_term(candidate_payload(t), auto_added, auto_descriptions),
                "tables": sorted(
                    f"{x.get('schema') or ''}.{x.get('name') or ''}" for x in t.tables
                ),
            }
            for t in terms
        ),
        key=lambda r: str(r["id"]),
    )
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def judge_key(
    concept: dict[str, Any],
    candidates: list[dict[str, Any]],
    model: str,
    *,
    auto_added: dict[str, Sequence[str]] | None = None,
    auto_descriptions: dict[str, set[str]] | None = None,
) -> str:
    """Cache key of one judge call: same concept, same candidate Terms (id, name, columns,
    description, synonyms), model and prompt version = same verdict. Only what ``apply``
    itself wrote is left out, by recorded provenance: the synonyms AutoOntology added
    (*auto_added*: term id -> the exact strings written) and a description it
    set (*auto_descriptions*: term id -> texts; :data:`ANY_DESCRIPTION` when the text was
    not recorded) or the upstream fallback it replaces, so a re-run does not re-judge because of its own writes but a manual
    or upstream change of either re-judges."""
    terms = [_judged_term(c, auto_added, auto_descriptions) for c in candidates]
    blob = json.dumps(
        {
            "v": prompts.ALIGN_PROMPT_VERSION,
            "model": model,
            "concept": concept,
            "candidates": terms,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: :func:`judge_key`: a Term whose description apply set before the text was recorded,
#: until the next judge step records the description it sees as the baseline
#: (:meth:`~autoontology.store.alignments.AlignmentStore.settle_legacy_descriptions`).
ANY_DESCRIPTION = "\0any"

Judge = Callable[[dict[str, Any], list[dict[str, Any]]], AlignmentVerdict]


def make_alignment_judge(backend: LLMBackend, *, mode: str | None = None) -> Judge:
    """Structured output through upstream ``safe_invoke_with_structured_output`` (the
    backend's ``structured``), JSON-in-prompt fallback as for the merge judge."""
    from autoontology.unstructured.extractor import extract_mode

    mode = mode or extract_mode()

    def judge(concept: dict[str, Any], candidates: list[dict[str, Any]]) -> AlignmentVerdict:
        human = prompts.align_message(concept, candidates)
        if mode != "json":
            try:
                return backend.structured(prompts.ALIGN_SYSTEM_PROMPT, human, AlignmentVerdict)
            except Exception as exc:  # noqa: BLE001
                if mode == "structured" or _is_connection_error(exc):
                    raise
                logger.info(
                    "alignment judge structured output failed (%s); JSON mode", str(exc)[:160]
                )
        verdict, _ = invoke_json(
            backend, prompts.align_json_system_prompt(), human, AlignmentVerdict
        )
        return verdict

    return judge


def _norm(text: str) -> str:
    return "".join(str(text).split()).casefold()


def allow_metric_same() -> bool:
    """``AUTOONTOLOGY_ALIGN_ALLOW_METRIC_SAME=1`` lets a metric concept be ``same`` as a Term
    (e.g. a KPI table); off by default."""
    return os.environ.get("AUTOONTOLOGY_ALIGN_ALLOW_METRIC_SAME", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def apply_kind_guard(
    verdict: ValidatedVerdict,
    concept: ConceptInfo,
    term: TermInfo | None,
    *,
    metric_same: bool | None = None,
) -> ValidatedVerdict:
    """A Term is a table-level entity: an ``attribute`` concept, a ``metric`` concept (unless
    *metric_same* / ``AUTOONTOLOGY_ALIGN_ALLOW_METRIC_SAME``), or a concept whose name is one
    of the Term's column attributes, is at most ``related`` to it, never ``same``
    (otherwise column and measure names would be applied as the Term's synonyms; seen with
    qwen3:8b: 계약기간 judged the same as Contracts)."""
    if verdict.relation != "same" or term is None:
        return verdict
    metric_same = allow_metric_same() if metric_same is None else metric_same
    capped = {"attribute"} if metric_same else {"attribute", "metric"}
    columns = {_norm(a.get("name") or "") for a in term.attributes} | {
        _norm(a.get("source_column") or "") for a in term.attributes
    }
    reason = ""
    if concept.kind in capped:
        reason = f"a {concept.kind} concept"
    elif _norm(concept.name) in columns - {""}:
        reason = f"a column attribute of {term.name}"
    if reason:
        verdict.notes.append(f"same -> related: {concept.name} is {reason}")
        verdict.relation = "related"
    return verdict


# --- identity policy (review #4) -----------------------------------------------------------

#: Bumped whenever the rules below change; recorded on refused proposals and in the judge
#: step. Every path (fresh, cached, resumed verdicts) re-applies the current policy.
#: v2: an English plural and its singular count as the same alias (``Contracts`` /
#: ``contracts`` table vs. ``Contract``), still whole aliases only, never substrings.
#: v2.1: the :data:`NO_SINGULAR` stop-list (a workspace aligned under v2 re-judges its
#: stored verdicts on ``--resume``, no LLM call).
IDENTITY_POLICY_VERSION = "identity-v2.1"
#: A destructive identity merge needs at least this confidence (and at least --min-conf).
IDENTITY_MIN_CONF = 0.90


def alias_key(text: str) -> str:
    """Synonym dedupe key of the addition (:func:`merge_synonyms`): case-folded,
    whitespace runs collapsed, no NFKC and no space stripping, so a manual ``계약 서``
    and an automatic ``계약서`` stay two synonyms. Ownership (provenance match, removal)
    never uses it: only the exact strings recorded as added are AutoOntology's, so a
    legacy manual ``계약  서`` kept next to a recorded ``계약 서`` is never taken."""
    return " ".join(str(text).split()).casefold()


def norm_alias(text: str) -> str:
    """Alias comparison key: Unicode NFKC, case-folded, every whitespace removed."""
    import unicodedata

    return "".join(unicodedata.normalize("NFKC", str(text)).split()).casefold()


_LATIN_ALIAS = re.compile(r"^[a-z][a-z0-9_-]*[a-z]$")
#: Plurals whose naive singular is another word (known false-positive pairs): kept as is.
NO_SINGULAR = frozenset({"goods", "customs", "news", "arms", "glasses", "means"})


def singular(alias: str) -> str:
    """The English singular of a normalized all-Latin alias (``contracts`` -> ``contract``,
    ``categories`` -> ``category``, ``boxes`` -> ``box``); anything else, and every word of
    :data:`NO_SINGULAR`, unchanged."""
    if (
        len(alias) <= 3
        or alias in NO_SINGULAR
        or not _LATIN_ALIAS.fullmatch(alias)
        or not alias.endswith("s")
    ):
        return alias
    if alias.endswith("ies"):
        return alias[:-3] + "y"
    if alias.endswith(("sses", "xes", "ches", "shes", "zes")):
        return alias[:-2]
    if alias.endswith(("ss", "us", "is")):
        return alias
    return alias[:-1]


_WORD_BREAK = re.compile(r"[\s_\-]+|(?<=[a-z0-9])(?=[A-Z])")


def last_word(name: str) -> str:
    """The last word of a raw alias, NFKC and case-folded (split at whitespace, ``_``,
    ``-`` and camelCase): ``Trade Goods`` / ``trade_goods`` / ``TradeGoods`` -> ``goods``."""
    import unicodedata

    words = [w for w in _WORD_BREAK.split(unicodedata.normalize("NFKC", str(name))) if w]
    return words[-1].casefold() if words else ""


def identity_keys(aliases: set[str]) -> set[str]:
    """Comparison keys of normalized aliases: each alias and its English singular."""
    return aliases | {singular(a) for a in aliases}


def identity_keys_of(names: Sequence[str]) -> set[str]:
    """Comparison keys of raw names: :func:`identity_keys`, except that a multi-word name
    whose last word is on :data:`NO_SINGULAR` (``Trade Goods``) keeps its plural only."""
    keys: set[str] = set()
    for name in names:
        n = norm_alias(name)
        if not n:
            continue
        keys.add(n)
        if last_word(name) not in NO_SINGULAR:
            keys.add(singular(n))
    return keys


def concept_names(concept: ConceptInfo) -> list[str]:
    return [n for n in [concept.name, concept.name_en, *concept.synonyms] if norm_alias(n)]


def concept_aliases(concept: ConceptInfo) -> set[str]:
    return {norm_alias(n) for n in concept_names(concept)}


def trusted_term_names(term: TermInfo, auto_added: Sequence[str] = ()) -> list[str]:
    """The Term's own name, the names of the tables it represents, and its synonyms except
    those AutoOntology's alignment added (an auto-added alias is never evidence: a wrong merge
    would otherwise validate itself on the next run)."""
    added = {norm_alias(a) for a in auto_added}
    own = [term.name, *(t.get("name") or "" for t in term.tables)]
    synonyms = [n for n in term.synonyms if norm_alias(n) not in added]
    return [n for n in [*own, *synonyms] if norm_alias(n)]


def trusted_term_aliases(term: TermInfo, auto_added: Sequence[str] = ()) -> set[str]:
    return {norm_alias(n) for n in trusted_term_names(term, auto_added)}


def identity_bar(min_conf: float) -> float:
    return max(min_conf, IDENTITY_MIN_CONF)


def apply_identity_policy(
    verdict: ValidatedVerdict,
    concept: ConceptInfo,
    term: TermInfo | None,
    *,
    min_conf: float,
    auto_added: Sequence[str] = (),
) -> tuple[ValidatedVerdict, dict[str, Any]]:
    """``same`` (an identity merge: the concept's names become the Term's synonyms) needs
    confidence >= ``max(min_conf, 0.90)`` **and** a normalized full-alias match between the
    concept (name, English name, synonyms) and the Term's trusted aliases
    (:func:`trusted_term_aliases`); no substring matching. Otherwise the concepts stay
    separate (``same -> related``) and the refused proposal is returned for review."""
    if verdict.relation != "same" or term is None:
        return verdict, {}
    bar = identity_bar(min_conf)
    evidence = sorted(
        identity_keys_of(concept_names(concept))
        & identity_keys_of(trusted_term_names(term, auto_added))
    )
    reason = ""
    if verdict.confidence < bar:
        reason = f"confidence {verdict.confidence:.2f} < {bar:.2f}"
    elif not evidence:
        reason = (
            f"no identity evidence: none of {concept.name!r}'s names equals a trusted "
            f"alias of {term.name!r}"
        )
    if not reason:
        verdict.notes.append(
            f"identity evidence ({IDENTITY_POLICY_VERSION}): " + ", ".join(evidence)
        )
        return verdict, {}
    proposal = {
        "relation": "same",
        "term_id": verdict.term_id,
        "term_name": term.name,
        "confidence": verdict.confidence,
        "policy": IDENTITY_POLICY_VERSION,
        "reason": reason,
    }
    verdict.notes.append(
        f"same -> related ({IDENTITY_POLICY_VERSION}): {reason}; proposal kept for review"
    )
    verdict.relation = "related"
    return verdict, proposal


def guard_verdict(
    verdict: ValidatedVerdict,
    concept: ConceptInfo,
    term: TermInfo | None,
    *,
    min_conf: float,
    auto_added: Sequence[str] = (),
    metric_same: bool | None = None,
) -> tuple[ValidatedVerdict, dict[str, Any]]:
    """Every acceptance rule in order: the kind/column guard, then the identity policy."""
    verdict = apply_kind_guard(verdict, concept, term, metric_same=metric_same)
    return apply_identity_policy(verdict, concept, term, min_conf=min_conf, auto_added=auto_added)


@dataclass
class StaleAlias:
    """A synonym AutoOntology added to a Term whose source alignment is no longer accepted."""

    term_id: str
    term_name: str
    alias: str  # the spelling currently on the Term
    concepts: list[str]  # the concepts recorded with the write that added it


def accepted_aliases(concept: ConceptInfo) -> set[str]:
    """What an accepted ``same`` concept put (or would put) on its Term: name, English name,
    synonyms, normalized."""
    return {n for n in map(norm_alias, [concept.name, concept.name_en, *concept.synonyms]) if n}


def stale_auto_aliases(
    terms: dict[str, TermInfo],
    provenance: dict[str, Sequence[dict[str, Any]]],
    protected: dict[str, set[str]],
    source_id: str,
) -> tuple[list[StaleAlias], list[dict[str, Any]]]:
    """Repair plan of documents source *source_id*: every synonym currently on a Term that
    the recorded provenance (*provenance*: term id -> [{alias, source_id, concepts}],
    ``term_alias_additions`` x ``runs``) says this source's alignment added, and that **no
    accepted ``same`` alignment of any documents source** still accounts for (*protected*:
    term id -> normalized aliases of every accepted concept, :func:`accepted_aliases`).
    Aliases with unknown provenance or added by another documents source are kept and
    returned with a reason (that source's own repair handles them)."""
    stale: list[StaleAlias] = []
    kept: list[dict[str, Any]] = []
    for term_id, records in sorted(provenance.items()):
        term = terms.get(term_id)
        if term is None:
            continue  # a Term of another structured source (or rebuilt away)
        by_alias: dict[str, list[dict[str, Any]]] = {}
        for rec in records:  # exact recorded strings: ownership is never normalised
            by_alias.setdefault(rec["alias"], []).append(rec)
        own = {norm_alias(n) for n in [term.name, *(t.get("name") or "" for t in term.tables)]}
        safe = protected.get(term_id, set())
        for synonym in term.synonyms:
            n = norm_alias(synonym)
            recs = by_alias.get(synonym)
            if not recs or n in own or n in safe:
                continue
            sources = {r.get("source_id") for r in recs}
            if source_id in sources:
                stale.append(
                    StaleAlias(
                        term_id,
                        term.name,
                        synonym,
                        sorted({c for r in recs for c in r.get("concepts") or []}),
                    )
                )
            else:
                kept.append(
                    {
                        "term_id": term_id,
                        "term_name": term.name,
                        "alias": synonym,
                        "reason": "unknown provenance"
                        if sources == {None}
                        else "added by another documents source",
                    }
                )
    return stale, kept


def judge_concept(
    judge: Judge, concept: ConceptInfo, candidates: list[TermInfo]
) -> ValidatedVerdict:
    raw = judge(concept_payload(concept), [candidate_payload(t) for t in candidates])
    verdict = validate_verdict(raw, {t.id: t.name for t in candidates})
    return apply_kind_guard(
        verdict, concept, next((t for t in candidates if t.id == verdict.term_id), None)
    )


# --- apply policy -----------------------------------------------------------------------


def merge_synonyms(
    existing: Sequence[str], additions: Sequence[str], term_name: str = ""
) -> list[str]:
    """Existing synonyms in order, then new ones; case-insensitive dedupe, never removes
    an existing entry, never adds the Term's own name or blanks (:func:`alias_key`)."""
    out = list(existing)
    seen = {alias_key(s) for s in existing} | {alias_key(term_name)}
    for value in additions:
        clean = " ".join(str(value).split())
        if clean and alias_key(clean) not in seen:
            out.append(clean)
            seen.add(alias_key(clean))
    return out


@dataclass
class SameMatch:
    """An accepted ``same`` alignment feeding a Term patch."""

    alignment_id: int | None
    concept_name: str
    synonyms: list[str]
    definition: str
    confidence: float


@dataclass
class TermPatch:
    term_id: str
    term_name: str
    synonyms_before: list[str]
    synonyms_after: list[str]
    description: str | None = None  # set only when the Term's own is empty/fallback
    #: a real description exists and the document definition differs: export-only
    kept_definitions: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    alignment_ids: list[int] = field(default_factory=list)

    @property
    def synonyms_added(self) -> list[str]:
        before = {s.casefold() for s in self.synonyms_before}
        return [s for s in self.synonyms_after if s.casefold() not in before]

    @property
    def changed(self) -> bool:
        return bool(self.synonyms_added) or self.description is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "term_name": self.term_name,
            "synonyms_added": self.synonyms_added,
            "synonyms_after": self.synonyms_after,
            "description": self.description,
            "kept_definitions": self.kept_definitions,
            "concepts": self.concepts,
        }


def plan_patches(
    matches: dict[str, list[SameMatch]], terms: dict[str, TermInfo]
) -> list[TermPatch]:
    """One patch per Term with accepted ``same`` matches, from the Term's *current* state
    (so a re-run plans nothing for a Term that already has everything)."""
    patches = []
    for term_id in sorted(matches, key=lambda t: terms[t].name if t in terms else t):
        term = terms.get(term_id)
        if term is None:
            continue
        group = sorted(matches[term_id], key=lambda m: (-m.confidence, m.concept_name))
        additions: list[str] = []
        for m in group:
            additions += [m.concept_name, *m.synonyms]
        patch = TermPatch(
            term_id=term.id,
            term_name=term.name,
            synonyms_before=list(term.synonyms),
            synonyms_after=merge_synonyms(term.synonyms, additions, term.name),
            concepts=[m.concept_name for m in group],
            alignment_ids=[m.alignment_id for m in group if m.alignment_id is not None],
        )
        definitions = [m.definition.strip() for m in group if m.definition.strip()]
        if term.is_fallback:
            if definitions and definitions[0] != (term.description or "").strip():
                patch.description = definitions[0]
        else:
            patch.kept_definitions = [
                d for d in dict.fromkeys(definitions) if d != term.description.strip()
            ]
        patches.append(patch)
    return patches


class TermWriter(Protocol):
    def add_synonyms(self, term_id: str, additions: list[str]) -> dict[str, Any]:
        """Merge *additions* into the Term's current synonyms (atomically) and refresh its
        embeddings: ``{"synonyms", "added", "embeddings_refreshed"}``."""
        ...

    def patch_description(self, term_id: str, description: str) -> dict[str, Any]: ...

    def refresh_embeddings(self, term_id: str) -> bool: ...

    def remove_synonyms(self, term_id: str, removals: list[str]) -> dict[str, Any]:
        """Take *removals* off the Term's synonyms (atomically) and refresh its embeddings:
        ``{"synonyms", "removed", "embeddings_refreshed"}`` (``align repair`` only)."""
        ...


def apply_patch(patch: TermPatch, writer: TermWriter) -> dict[str, Any]:
    """Write one planned patch; unchanged Terms are not touched. The writer merges the
    additions into the Term's synonyms as they are at write time (not as planned)."""
    result: dict[str, Any] = {
        "term_id": patch.term_id,
        "synonyms_added": [],
        "description_set": False,
    }
    if patch.synonyms_added:
        written = writer.add_synonyms(patch.term_id, patch.synonyms_added)
        result["synonyms_added"] = list(written.get("added", patch.synonyms_added))
        result["synonyms"] = written.get("synonyms", patch.synonyms_after)
        result["embeddings_refreshed"] = written.get("embeddings_refreshed")
    if patch.description is not None:
        writer.patch_description(patch.term_id, patch.description)
        result["description_set"] = True
        result["description"] = patch.description  # provenance for the judge cache
    return result
