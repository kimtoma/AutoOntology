"""``report.md`` of ``autoontology export`` (English)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoontology.ontology.model import MergedOntology
from autoontology.store.db import now_iso

TYPE_LABELS = {
    "classes": "Classes (owl:Class)",
    "classes_structured": "  └ Structured Terms",
    "classes_document": "  └ Document concepts (unaligned, broader, narrower, related)",
    "metric_classes": "  └ of which ao:Metric",
    "object_properties": "Object properties (owl:ObjectProperty)",
    "semantic_fk_properties": "  └ SEMANTIC_FK",
    "relation_properties": "  └ Document relations",
    "predicate_properties": "Predicate super-properties (owl:ObjectProperty `ao:<predicate>`, "
    "domain/range owl:Thing)",
    "object_properties_total": "owl:ObjectProperty total (Turtle)",
    "datatype_properties": "Datatype properties (owl:DatatypeProperty)",
    "column_properties": "  └ Structured column properties",
    "relation_datatype_properties": "  └ Document has_attribute (object is an attribute)",
    "individuals": "Individuals (rules, processes)",
    "business_rules": "  └ ao:BusinessRule",
    "processes": "  └ ao:Process",
    "documents": "Documents (prov:Entity)",
    "evidence": "Evidence (prov:Entity)",
    "subclass_axioms": "rdfs:subClassOf axioms",
    "related_axioms": "skos:related axioms",
    "merged_concepts": "Document concepts merged into Terms (same)",
}


def _cell(value: Any) -> str:
    return " ".join(("" if value is None else str(value)).replace("|", "\\|").split())


def render(
    model: MergedOntology,
    *,
    run_id: str,
    files: list[dict[str, Any]],
    upstream: list[dict[str, Any]],
    revision: int | None = None,
) -> str:
    counts = model.counts()
    lines = [
        f"# Ontology report — {model.name}",
        "",
        *([f"- Revision: {revision}"] if revision is not None else []),
        f"- Run ID: `{run_id}`",
        f"- Generated at: {now_iso()}",
        f"- Base IRI: `{model.base}`",
        f"- Structured sources: {', '.join(model.structured_sources) or '-'}",
        f"- Document sources: {', '.join(model.documents_sources) or '-'}",
        "",
        "## Counts by type",
        "",
        "| Type | Count |",
        "|---|---|",
    ]
    for key, label in TYPE_LABELS.items():
        lines.append(f"| {label} | {counts[key]} |")
    lines += ["", "## Counts by source", "", "| Source | Type | Count |", "|---|---|---|"]
    for source, kinds in sorted(counts["by_source"].items()):
        for kind, n in sorted(kinds.items()):
            lines.append(f"| {_cell(source)} | {kind} | {n} |")
    lines += [
        "",
        "## Alignment summary (document concepts)",
        "",
        "| Alignment | Concepts |",
        "|---|---|",
    ]
    for relation, n in model.alignment_counts.items():
        lines.append(f"| {relation} | {n} |")
    if not model.alignment_counts:
        lines.append("| - | 0 |")
    lines += ["", "### Concepts merged into Terms", ""]
    if model.merges:
        lines += ["| Concept | kind | Term | Confidence |", "|---|---|---|---|"]
        for m in sorted(
            model.merges, key=lambda m: (model.classes[m.term_local].label, m.concept_name)
        ):
            lines.append(
                f"| {_cell(m.concept_name)} | {m.concept_kind} | "
                f"{_cell(model.classes[m.term_local].label)} | "
                f"{'-' if m.confidence is None else f'{m.confidence:.2f}'} |"
            )
    else:
        lines.append("none (run `autoontology build` first)")
    lines += [
        "",
        "## Items to review",
        "",
        f"- upstream fallback Terms (LLM description failed): "
        f"{', '.join(model.fallback_terms) or 'none'}",
        f"- Document concepts without evidence: {', '.join(model.without_evidence) or 'none'}",
        f"- Relations dropped because merging made them self-referential: "
        f"{', '.join(model.collapsed_relations) or 'none'}",
    ]
    if model.source_missing:
        lines.append(
            "- same applied but the Term source is not in the alignment targets (kept as separate classes): "
            + ", ".join(
                f"{e['name']}→{e['term']} ({e['structured_source']})" for e in model.source_missing
            )
        )
    if model.dry_run_same:
        lines.append(
            "- same judged but not applied due to dry run (kept as separate classes): "
            + ", ".join(f"{e['name']}→{e['term']}" for e in model.dry_run_same)
        )
    if model.low_confidence:
        lines.append(
            "- same judged but below the confidence threshold (kept as separate classes): "
            + ", ".join(
                f"{e['name']}→{e['term']} ({e['confidence']:.2f})" for e in model.low_confidence
            )
        )
    lines += ["", f"### Unaligned concepts ({len(model.unaligned)}, kept as separate classes)", ""]
    if model.unaligned:
        lines += ["| Concept | kind | Source | Alignment |", "|---|---|---|---|"]
        for e in sorted(model.unaligned, key=lambda e: e["name"]):
            lines.append(
                f"| {_cell(e['name'])} | {e['kind']} | {_cell(e['source'])} | {e['relation']} |"
            )
    else:
        lines.append("none")
    lines += ["", "## Files", "", "| File | Size (bytes) | sha256 |", "|---|---|---|"]
    for f in files:
        lines.append(f"| {f['name']} | {f['bytes']} | `{f['sha256'][:16]}…` |")
    for u in upstream:
        lines.append(f"- {u['name']}: {u['origin']}")
    return "\n".join(lines) + "\n"


def write(model: MergedOntology, out_dir: Path, **kwargs: Any) -> Path:
    path = out_dir / "report.md"
    path.write_text(render(model, **kwargs), encoding="utf-8")
    return path
