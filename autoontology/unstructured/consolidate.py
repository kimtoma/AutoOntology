"""Cross-chunk consolidation: per-chunk concepts -> one deduplicated concept set.

1. Exact merge (no LLM): candidates whose casefolded name or any synonym matches are one
   concept (union-find).
2. Semantic merge: each group is embedded as ``name: definition``; every pair with cosine
   >= ``AUTOONTOLOGY_MERGE_THRESHOLD`` (default 0.80) goes to the LLM merge judge, most
   similar first, at most ``AUTOONTOLOGY_MERGE_MAX_JUDGE`` calls (default 100). Judge
   answers are fail-closed: a ``canonical_name`` that is neither group's name nor synonym
   is ignored, and a judge error means "not the same".
3. Relations are re-pointed at the merged concepts (by the chunk-local name), deduplicated
   by (subject, predicate, object); self-relations created by a merge are dropped.

Evidence is never invented: each concept/relation keeps the quotes of its members.
"""

from __future__ import annotations

import logging
import math
import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from autoontology.store.documents import ConceptRecord, EvidenceRecord, RelationRecord
from autoontology.unstructured.schemas import MergeDecision, ValidatedExtraction, name_key

logger = logging.getLogger("autoontology.extract")

DEFAULT_THRESHOLD = 0.80
DEFAULT_MAX_JUDGE = 100

Embedder = Callable[[list[str]], list[list[float] | None]]
Judge = Callable[[dict[str, Any], dict[str, Any]], MergeDecision]


def threshold_from_env() -> float:
    raw = os.environ.get("AUTOONTOLOGY_MERGE_THRESHOLD", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_THRESHOLD
    except ValueError as exc:
        raise ValueError(f"AUTOONTOLOGY_MERGE_THRESHOLD must be a number, got {raw!r}") from exc
    if not 0.0 < value <= 1.0:
        raise ValueError(f"AUTOONTOLOGY_MERGE_THRESHOLD must be in (0, 1], got {value}")
    return value


def max_judge_from_env() -> int:
    raw = os.environ.get("AUTOONTOLOGY_MERGE_MAX_JUDGE", "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_MAX_JUDGE
    except ValueError as exc:
        raise ValueError(f"AUTOONTOLOGY_MERGE_MAX_JUDGE must be an integer, got {raw!r}") from exc


@dataclass
class _Candidate:
    chunk_id: str
    name: str
    name_en: str
    kind: str
    definition: str
    synonyms: list[str]
    attributes: list[str]
    quote: str
    verified: bool


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # the earlier candidate stays the root: deterministic canonical choices
            ra, rb = min(ra, rb), max(ra, rb)
            self.parent[rb] = ra
        return ra


@dataclass
class ConsolidationStats:
    candidates: int = 0
    exact_merges: int = 0
    similar_pairs: int = 0
    judge_calls: int = 0
    judge_merges: int = 0
    judge_failures: int = 0
    judge_skipped: int = 0
    relations_in: int = 0
    self_relations_dropped: int = 0
    embedding_error: str | None = None
    #: groups the embedder returned no vector for (their semantic merge was skipped)
    missing_vectors: int = 0

    @property
    def incomplete(self) -> bool:
        """A swallowed failure left the semantic merge unfinished: the result is partial
        and must not be cached as a successful consolidation."""
        return bool(self.embedding_error or self.judge_failures or self.missing_vectors)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "incomplete": self.incomplete}


@dataclass
class ConsolidationResult:
    concepts: list[ConceptRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    stats: ConsolidationStats = field(default_factory=ConsolidationStats)


def _most_common(values: Sequence[str]) -> str:
    """Most frequent non-empty value; ties go to the earliest."""
    values = [v for v in values if v]
    if not values:
        return ""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)


def _dedupe(values: Sequence[str], exclude: set[str]) -> list[str]:
    seen, out = set(exclude), []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def consolidate(
    extractions: Sequence[tuple[str, ValidatedExtraction]],
    *,
    embedder: Embedder | None = None,
    judge: Judge | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_judge: int = DEFAULT_MAX_JUDGE,
) -> ConsolidationResult:
    stats = ConsolidationStats()
    cands: list[_Candidate] = []
    local: dict[tuple[str, str], int] = {}  # (chunk_id, name key) -> candidate index
    for chunk_id, ext in extractions:
        for c in ext.concepts:
            local[(chunk_id, name_key(c.name))] = len(cands)
            cands.append(
                _Candidate(
                    chunk_id,
                    c.name,
                    c.name_en,
                    c.kind,
                    c.definition,
                    list(c.synonyms),
                    list(c.attributes),
                    c.evidence,
                    c.evidence_verified,
                )
            )
    stats.candidates = len(cands)
    uf = _UnionFind(len(cands))
    events: dict[int, list[dict[str, Any]]] = {}  # root -> merge chain (merged_from)
    overrides: dict[int, dict[str, Any]] = {}

    def union(a: int, b: int, event: dict[str, Any] | None) -> int:
        ra, rb = uf.find(a), uf.find(b)
        chain = [*events.pop(ra, []), *events.pop(rb, [])]
        ov = {**overrides.pop(rb, {}), **overrides.pop(ra, {})}
        root = uf.union(ra, rb)
        if event:
            chain.append(event)
        if chain:
            events[root] = chain
        if ov:
            overrides[root] = ov
        return root

    # 1. exact match. Same name: always one concept. A key that is a synonym on either side
    # only merges candidates of the same kind (an attribute never absorbs an entity).
    # Synonym-to-synonym matches also count (two candidates both calling something 거래처).
    # Names and synonyms keep separate owner maps: a synonym registered by a candidate of
    # another kind must never stop two candidates with the same name from merging.
    name_owner: dict[str, int] = {}
    syn_owner: dict[str, int] = {}

    def merge_exact(j: int, i: int, key: str, by_name: bool) -> None:
        if uf.find(j) == uf.find(i):
            return
        other, cand = cands[j], cands[i]
        if not by_name and other.kind != cand.kind:
            logger.debug(
                "exact merge refused: %r (%s) / %r (%s) share %r but differ in kind",
                other.name,
                other.kind,
                cand.name,
                cand.kind,
                key,
            )
            return
        same_name = name_key(other.name) == name_key(cand.name)
        event = (
            None
            if same_name
            else {"from": cand.name, "into": other.name, "via": "synonym", "key": key}
        )
        logger.debug(
            "exact merge %r + %r via %s %r",
            other.name,
            cand.name,
            "name" if by_name else "synonym",
            key,
        )
        union(j, i, event)
        stats.exact_merges += 1

    for i, cand in enumerate(cands):
        own = name_key(cand.name)
        if own:
            if own in name_owner:
                merge_exact(name_owner[own], i, own, by_name=True)
            else:
                name_owner[own] = i
            if own in syn_owner:  # an earlier candidate lists this name as a synonym
                merge_exact(syn_owner[own], i, own, by_name=False)
        for key in {name_key(x) for x in cand.synonyms} - {own, ""}:
            if key in name_owner:
                merge_exact(name_owner[key], i, key, by_name=False)
            if key in syn_owner:
                merge_exact(syn_owner[key], i, key, by_name=False)
            else:
                syn_owner[key] = i

    def summary(root: int) -> dict[str, Any]:
        members = [c for j, c in enumerate(cands) if uf.find(j) == root]
        ov = overrides.get(root, {})
        name = ov.get("name") or _most_common([m.name for m in members])
        return {
            "name": name,
            "kind": _most_common([m.kind for m in members]),
            "definition": ov.get("definition") or _best_definition(members, name),
            "synonyms": _dedupe(
                [s for m in members for s in [m.name, *m.synonyms]] + list(ov.get("synonyms", [])),
                {name.casefold()},
            ),
        }

    # 2. embedding similarity + LLM judge (always on the current, merged groups)
    roots = sorted({uf.find(i) for i in range(len(cands))})
    if embedder is not None and judge is not None and len(roots) > 1 and max_judge > 0:
        initial = {r: summary(r) for r in roots}
        try:
            vectors = embedder([f"{initial[r]['name']}: {initial[r]['definition']}" for r in roots])
        except Exception as exc:  # noqa: BLE001 - exact merges still stand
            stats.embedding_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(
                "consolidate: embedding failed (%s); semantic merge skipped", stats.embedding_error
            )
            vectors = []
        if vectors:
            stats.missing_vectors = sum(1 for v in vectors if not v) + max(
                0, len(roots) - len(vectors)
            )
            if stats.missing_vectors:
                logger.warning(
                    "consolidate: %d of %d group(s) got no embedding; their "
                    "semantic merge was skipped",
                    stats.missing_vectors,
                    len(roots),
                )
        pairs = []
        for a in range(len(vectors)):
            for b in range(a + 1, len(vectors)):
                if vectors[a] and vectors[b]:
                    sim = _cosine(vectors[a], vectors[b])  # type: ignore[arg-type]
                    if sim >= threshold:
                        pairs.append((sim, roots[a], roots[b]))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        stats.similar_pairs = len(pairs)
        for sim, a, b in pairs:
            ra, rb = uf.find(a), uf.find(b)
            if ra == rb:
                continue
            if stats.judge_calls >= max_judge:
                stats.judge_skipped += 1
                continue
            stats.judge_calls += 1
            sa, sb = summary(ra), summary(rb)
            try:
                decision = judge(sa, sb)
            except Exception as exc:  # noqa: BLE001 - fail closed: not the same
                stats.judge_failures += 1
                logger.warning(
                    "merge judge failed for %s / %s: %s", sa["name"], sb["name"], str(exc)[:160]
                )
                continue
            if not decision.same:
                continue
            root = union(
                ra,
                rb,
                {"from": sb["name"], "into": sa["name"], "via": "judge", "cosine": round(sim, 3)},
            )
            stats.judge_merges += 1
            logger.info("merged %r + %r (cosine %.3f)", sa["name"], sb["name"], sim)
            allowed = {n.casefold(): n for x in (sa, sb) for n in [x["name"], *x["synonyms"]]}
            ov = overrides.setdefault(root, {})
            canonical = allowed.get(name_key(decision.canonical_name))
            ov["name"] = canonical or sa["name"]
            ov["definition"] = " ".join(decision.merged_definition.split()) or sa["definition"]
            ov["synonyms"] = [
                *ov.get("synonyms", []),
                *sa["synonyms"],
                *sb["synonyms"],
                sb["name"],
                sa["name"],
                *(" ".join(x.split()) for x in decision.merged_synonyms),
            ]

    # 3. build the records (one per root; equal canonical names fold together)
    by_root: dict[int, list[int]] = {}
    for i in range(len(cands)):
        by_root.setdefault(uf.find(i), []).append(i)
    records: dict[str, ConceptRecord] = {}
    root_name: dict[int, str] = {}
    for root, idxs in sorted(by_root.items()):
        members = [cands[i] for i in idxs]
        ov = overrides.get(root, {})
        name = ov.get("name") or _most_common([m.name for m in members])
        key = name.casefold()
        surface = _dedupe([m.name for m in members], set())
        evidence = _dedupe_evidence(
            [EvidenceRecord(m.chunk_id, m.quote, m.verified) for m in members]
        )
        synonyms = [*surface, *(s for m in members for s in m.synonyms), *ov.get("synonyms", [])]
        chain = events.get(root, [])
        if key in records:  # another group ended with the same canonical name
            rec = records[key]
            rec.synonyms = _dedupe([*rec.synonyms, *synonyms], {key})
            rec.attributes = _dedupe(
                [*rec.attributes, *(a for m in members for a in m.attributes)], set()
            )
            rec.merged_from = [
                *rec.merged_from,
                *chain,
                {"from": surface[0], "into": rec.name, "via": "name"},
            ]
            rec.evidence = _dedupe_evidence([*rec.evidence, *evidence])
        else:
            records[key] = ConceptRecord(
                name=name,
                kind=_most_common([m.kind for m in members]),
                definition=ov.get("definition") or _best_definition(members, name),
                name_en=_most_common([m.name_en for m in members]),
                synonyms=_dedupe(synonyms, {key}),
                attributes=_dedupe([a for m in members for a in m.attributes], set()),
                merged_from=list(chain),
                evidence=evidence,
            )
        root_name[root] = records[key].name

    # 4. relations
    rels: dict[tuple[str, str, str], RelationRecord] = {}
    for chunk_id, ext in extractions:
        for r in ext.relations:
            stats.relations_in += 1
            si = local.get((chunk_id, name_key(r.subject)))
            oi = local.get((chunk_id, name_key(r.object)))
            if si is None or oi is None:
                continue
            subject, obj = root_name[uf.find(si)], root_name[uf.find(oi)]
            if subject == obj:
                stats.self_relations_dropped += 1
                continue
            rel = rels.setdefault(
                (subject, r.predicate, obj), RelationRecord(subject, r.predicate, obj, r.label)
            )
            rel.label = rel.label or r.label
            rel.evidence = _dedupe_evidence(
                [*rel.evidence, EvidenceRecord(chunk_id, r.evidence, r.evidence_verified)]
            )
    return ConsolidationResult(list(records.values()), list(rels.values()), stats)


def _best_definition(members: Sequence[_Candidate], name: str) -> str:
    ranked = sorted(
        members, key=lambda m: (m.name == name, m.verified, len(m.definition)), reverse=True
    )
    return ranked[0].definition if ranked else ""


def _dedupe_evidence(evidence: Sequence[EvidenceRecord]) -> list[EvidenceRecord]:
    """Unique (chunk, quote) pairs; empty quotes are not evidence."""
    seen: set[tuple[str, str]] = set()
    out = []
    for e in evidence:
        if e.quote.strip() and (e.chunk_id, e.quote) not in seen:
            seen.add((e.chunk_id, e.quote))
            out.append(e)
    return out
