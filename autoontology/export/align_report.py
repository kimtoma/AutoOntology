"""``align-report.md`` for ``autoontology align`` (English, like the other reports)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoontology.store.alignments import AlignmentRecord
from autoontology.store.db import now_iso
from autoontology.store.documents import ConceptRow

RELATION_ORDER = ("same", "broader", "narrower", "related", "none", "skipped")
RELATION_LABELS = {
    "same": "identical",
    "broader": "Term is broader",
    "narrower": "Term is narrower",
    "related": "related",
    "none": "none",
    "skipped": "excluded (rules, processes)",
}


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("|", "\\|").split())


def _num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render(
    *,
    source: str,
    structured: str,
    run_id: str,
    alignments: Sequence[AlignmentRecord],
    concepts: Sequence[ConceptRow],
    steps: dict[str, dict[str, Any]],
    min_sim: float,
    min_conf: float,
) -> str:
    kinds = {c.id: c.kind for c in concepts}
    counts = Counter(a.relation for a in alignments)
    apply = steps.get("apply", {})
    judge = steps.get("judge", {})
    cand = steps.get("candidates", {})
    lines = [
        f"# Alignment report — {source} → {structured}",
        "",
        f"- Run ID: `{run_id}`",
        f"- Generated at: {now_iso()}",
        f"- Candidate similarity floor: {min_sim:.2f}, apply confidence floor: {min_conf:.2f}, "
        f"candidates: {cand.get('top_k', '-')}",
        f"- Embedding model: {cand.get('model', '-')} (Term embeddings cached "
        f"{cand.get('term_embeddings', {}).get('cached', 0)} / newly computed "
        f"{cand.get('term_embeddings', {}).get('computed', 0)})",
        f"- Judgement: LLM calls {judge.get('judged', 0)}, cache reuse {judge.get('cached', 0)}, "
        f"failed {judge.get('failed', 0)}",
        "",
        "## Summary",
        "",
        "| Relation | Meaning | Concepts |",
        "|---|---|---|",
    ]
    for rel in RELATION_ORDER:
        lines.append(f"| {rel} | {RELATION_LABELS[rel]} | {counts.get(rel, 0)} |")
    lines += [
        "",
        f"- To apply (same, confidence ≥ {min_conf:.2f}): {sum(a.accepted for a in alignments)}",
        f"- Term patches: applied {apply.get('applied', 0)}, planned (dry run) "
        f"{apply.get('planned', 0)}, unchanged {apply.get('unchanged', 0)}"
        + (" — **dry run: nothing written to upstream**" if apply.get("dry_run") else ""),
        "",
        "## Term patches",
        "",
    ]
    patches = apply.get("patches", [])
    if patches:
        lines += [
            "| Term | Result | Synonyms added | Description set | Document definitions (export only) | Concepts |",
            "|---|---|---|---|---|---|",
        ]
        for p in patches:
            lines.append(
                f"| {_cell(p['term_name'])} | {p['action']} | "
                f"{_cell(', '.join(p['synonyms_added']) or '-')} | "
                f"{'yes' if p.get('description') else '-'} | {len(p.get('kept_definitions', []))}"
                f" | {_cell(', '.join(p['concepts']))} |"
            )
    else:
        lines.append("No same alignments to apply.")
    lines += [
        "",
        "## Aligned concepts (excluding none and skipped)",
        "",
        "| Concept | kind | Relation | Term | Similarity | Confidence | Applied | Rationale |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in sorted(
        (a for a in alignments if a.relation not in ("none", "skipped")),
        key=lambda a: (RELATION_ORDER.index(a.relation), -(a.confidence or 0), a.concept_name),
    ):
        lines.append(
            f"| {_cell(a.concept_name)} | {kinds.get(a.concept_id, '')} | "
            f"{a.relation} | {_cell(a.term_name)} | {_num(a.similarity, 3)} | "
            f"{_num(a.confidence)} | {'yes' if a.accepted else '-'} | "
            f"{_cell(a.rationale)} |"
        )
    proposals = [a for a in alignments if a.proposal]
    if proposals:
        lines += [
            "",
            f"## same proposals needing review ({len(proposals)})",
            "",
            "same judgements rejected by the identity policy. The concept stays separate "
            "and no synonym is written to the Term.",
            "",
            "| Concept | Proposed Term | Confidence | Policy | Reason |",
            "|---|---|---|---|---|",
        ]
        for a in sorted(proposals, key=lambda a: a.concept_name):
            p = a.proposal
            lines.append(
                f"| {_cell(a.concept_name)} | {_cell(p.get('term_name', ''))} | "
                f"{_num(p.get('confidence'))} | {_cell(p.get('policy', ''))} | "
                f"{_cell(p.get('reason', ''))} |"
            )
    none = [a for a in alignments if a.relation == "none"]
    lines += ["", f"## Unaligned concepts ({len(none)})", ""]
    if none:
        lines += [
            "| Concept | kind | Top candidate | Similarity | Reason |",
            "|---|---|---|---|---|",
        ]
        for a in sorted(none, key=lambda a: a.concept_name):
            top = a.candidates[0] if a.candidates else {}
            lines.append(
                f"| {_cell(a.concept_name)} | {kinds.get(a.concept_id, '')} | "
                f"{_cell(top.get('term_name', '-'))} | "
                f"{_num(top.get('similarity'), 3)} | {_cell(a.rationale)} |"
            )
    skipped = sorted(a.concept_name for a in alignments if a.relation == "skipped")
    lines += [
        "",
        f"## Excluded concepts (rule, process, {len(skipped)})",
        "",
        ", ".join(skipped) if skipped else "-",
    ]
    failed = judge.get("failed_concepts", [])
    if failed:
        lines += ["", "## Judgement failures", ""] + [
            f"- {f['concept']}: {_cell(f['error'])}" for f in failed
        ]
    return "\n".join(lines) + "\n"


def write(out_dir: Path, **kwargs: Any) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "align-report.md"
    path.write_text(render(**kwargs), encoding="utf-8")
    return path
