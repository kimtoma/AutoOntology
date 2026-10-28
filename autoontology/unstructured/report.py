# SPDX-License-Identifier: MIT
"""``report.md`` for an ``extract`` run (English, user-facing)."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.store.documents import ChunkRow, ConceptRow, DocumentRow, RelationRow

TOP_CONCEPTS = 20
MAX_RELATIONS_LISTED = 60


@dataclass
class ReportData:
    source: str
    run_id: str
    generated_at: str
    documents: list[DocumentRow]
    chunks: list[ChunkRow]
    concepts: list[ConceptRow]
    relations: list[RelationRow]
    concept_docs: dict[str, set[str]]
    evidence: dict[str, int]
    read_detail: dict[str, Any] = field(default_factory=dict)
    extract_detail: dict[str, Any] = field(default_factory=dict)
    consolidate_detail: dict[str, Any] = field(default_factory=dict)
    #: LLM_INVOKE_TIMEOUT_S in effect (env or model profile); None = unknown
    llm_timeout_s: float | None = None


def display_path(path: str) -> str:
    """A stored (absolute) document path relative to the cwd when it is below it."""
    try:
        return str(Path(path).relative_to(Path.cwd().resolve()))
    except ValueError:
        return path


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def _over_timeout(chunk: ChunkRow, default: float | None = None) -> bool:
    """Wall time above LLM_INVOKE_TIMEOUT_S means at least one attempt timed out and was
    retried upstream (also for chunks extracted before retries were counted)."""
    detail = chunk.extract_detail
    if detail.get("over_timeout"):
        return True
    timeout = detail.get("llm_timeout_s") or default
    if timeout is None:
        try:
            timeout = float(os.environ.get("LLM_INVOKE_TIMEOUT_S", "") or 0) or None
        except ValueError:
            timeout = None
    return bool(timeout) and float(detail.get("seconds", 0.0)) > float(timeout)


def summary_counts(data: ReportData) -> dict[str, Any]:
    extracted = [c for c in data.chunks if c.extraction is not None]
    failed = [c for c in data.chunks if c.extraction is None and c.extract_detail.get("error")]
    stats = data.consolidate_detail.get("stats", {})
    return {
        "documents": len(data.documents),
        "documents_failed": len(data.read_detail.get("failed", [])),
        "chunks": len(data.chunks),
        "chunks_extracted": len(extracted),
        "chunks_failed": len(failed),
        "chunks_pending": len(data.chunks) - len(extracted) - len(failed),
        "concepts": len(data.concepts),
        "concepts_without_evidence": sum(1 for c in data.concepts if not c.evidence_count),
        "relations": len(data.relations),
        "evidence": data.evidence.get("total", 0),
        "evidence_verified": data.evidence.get("verified", 0),
        "merges": int(stats.get("exact_merges", 0)) + int(stats.get("judge_merges", 0)),
        "paths": dict(Counter(c.extract_detail.get("path", "?") for c in extracted)),
        "retried_chunks": sum(1 for c in data.chunks if c.extract_detail.get("upstream_retries")),
        "over_timeout_chunks": sum(1 for c in data.chunks if _over_timeout(c, data.llm_timeout_s)),
        "max_chunk_seconds": max(
            (float(c.extract_detail.get("seconds", 0.0)) for c in data.chunks), default=0.0
        ),
    }


def render(data: ReportData) -> str:
    counts = summary_counts(data)
    stats = data.consolidate_detail.get("stats", {})
    by_doc = Counter(path for paths in data.concept_docs.values() for path in paths)
    chunks_by_doc = Counter(c.doc_id for c in data.chunks)
    actions = {d.get("path"): d for d in data.read_detail.get("documents", [])}
    out: list[str] = [
        f"# Document extraction report — {data.source}",
        "",
        f"- Run ID: `{data.run_id}`",
        f"- Generated at: {data.generated_at}",
        "",
        "## Summary",
        "",
        _table(
            ["Item", "Value"],
            [
                ["Documents", counts["documents"]],
                ["Documents failed to read", counts["documents_failed"]],
                [
                    "Chunks (extracted / failed / pending)",
                    f"{counts['chunks']} ({counts['chunks_extracted']} / {counts['chunks_failed']} / "
                    f"{counts['chunks_pending']})",
                ],
                [
                    "Concepts (without evidence)",
                    f"{counts['concepts']} ({counts['concepts_without_evidence']})",
                ],
                ["Relations", counts["relations"]],
                [
                    "Evidence (verified)",
                    f"{counts['evidence']} ({counts['evidence_verified']}, "
                    f"{_pct(counts['evidence_verified'], counts['evidence'])})",
                ],
                [
                    "Merges (name/synonym match / LLM judgement)",
                    f"{counts['merges']} ({stats.get('exact_merges', 0)} / "
                    f"{stats.get('judge_merges', 0)}; {stats.get('judge_calls', 0)} judge calls)",
                ],
                [
                    "Extraction paths",
                    ", ".join(f"{k} {v}" for k, v in sorted(counts["paths"].items())) or "-",
                ],
                ["Slowest chunk", f"{counts['max_chunk_seconds']:.1f}s"],
                [
                    "Chunks with upstream retries / chunks over LLM_INVOKE_TIMEOUT_S",
                    f"{counts['retried_chunks']} / {counts['over_timeout_chunks']}",
                ],
            ],
        ),
        "",
        "## Documents",
        "",
        _table(
            ["Document", "Engine", "Pages", "Chunks", "Concepts", "This run"],
            [
                [
                    display_path(d.path),
                    d.engine,
                    d.pages or "-",
                    chunks_by_doc[d.id],
                    by_doc[d.path],
                    actions.get(d.path, {}).get("action", "-"),
                ]
                for d in data.documents
            ],
        ),
    ]
    failed_docs = data.read_detail.get("failed", [])
    if failed_docs:
        out += [
            "",
            "### Read failures",
            "",
            _table(
                ["Document", "Code", "Error"],
                [
                    [display_path(f["path"]), f.get("code", ""), f.get("error", "")]
                    for f in failed_docs
                ],
            ),
        ]
    duplicates = data.read_detail.get("duplicates", [])
    if duplicates:
        out += [
            "",
            "### Duplicate documents (skipped, identical content)",
            "",
            _table(
                ["Document", "Same content as"],
                [[display_path(d["path"]), display_path(d["of"])] for d in duplicates],
            ),
        ]
    pruned = data.read_detail.get("pruned", [])
    if pruned:
        out += [
            "",
            "### Removed documents (no longer in the target path)",
            "",
            "\n".join(f"- {display_path(p)}" for p in pruned),
        ]
    kinds = Counter(c.kind for c in data.concepts)
    preds = Counter(r.predicate for r in data.relations)
    out += [
        "",
        "## Concept kinds",
        "",
        _table(["kind", "Count"], sorted(kinds.items())),
        "",
        "## Relation kinds",
        "",
        _table(["predicate", "Count"], sorted(preds.items())) if preds else "(none)",
    ]
    out += [
        "",
        f"## Top {TOP_CONCEPTS} concepts by evidence",
        "",
        _table(
            ["Concept", "English", "kind", "Evidence (verified)", "Documents", "Definition"],
            [
                [
                    c.name,
                    c.name_en,
                    c.kind,
                    f"{c.evidence_count} ({c.verified_count})",
                    len(data.concept_docs.get(c.name, ())),
                    c.definition[:120],
                ]
                for c in data.concepts[:TOP_CONCEPTS]
            ],
        ),
    ]
    if data.relations:
        listed = sorted(data.relations, key=lambda r: (-r.evidence_count, r.subject))
        out += [
            "",
            f"## Relations (by evidence, up to {MAX_RELATIONS_LISTED})",
            "",
            _table(
                ["Subject", "predicate", "Object", "Label", "Evidence"],
                [
                    [r.subject, r.predicate, r.object, r.label, r.evidence_count]
                    for r in listed[:MAX_RELATIONS_LISTED]
                ],
            ),
        ]
    failed = [c for c in data.chunks if c.extraction is None and c.extract_detail.get("error")]
    if failed:
        paths = {d.id: display_path(d.path) for d in data.documents}
        out += [
            "",
            "## Failed chunks",
            "",
            _table(
                ["Document", "Chunk", "Section", "Error"],
                [
                    [
                        paths.get(c.doc_id, c.doc_id),
                        c.idx,
                        c.section_path,
                        str(c.extract_detail.get("error"))[:200],
                    ]
                    for c in failed
                ],
            ),
        ]
    if counts["chunks_pending"]:
        out += [
            "",
            f"> {counts['chunks_pending']} pending chunks: not extracted yet because of the `--max-chunks` limit or "
            "an interruption. Re-run the same command to continue extraction.",
        ]
    return "\n".join(out) + "\n"


def write(data: ReportData, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text(render(data), encoding="utf-8")
    return path
