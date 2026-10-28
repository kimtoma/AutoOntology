"""The concept list and detail of ``GET /api/ontology/concepts[/{id}]``: Terms and document
concepts of one snapshot model in one list.

Item ids are the model's node locals: ``term-<id>`` for a Term class, ``concept-<id>`` for
a document concept (its own class, an individual, or folded into a Term). ``origin`` is
the graph's: ``structured`` | ``document`` | ``aligned`` (a Term joined to documents).
"""

from __future__ import annotations

from typing import Any

from autoontology.ontology.model import MergedOntology

KIND_ORDER = {"term": 0, "entity": 1, "metric": 2, "attribute": 3, "process": 4, "rule": 5}


def _alignments(model: MergedOntology) -> dict[str, list[dict[str, Any]]]:
    """node local -> the alignment links touching it (same / broader / narrower / related)."""
    out: dict[str, list[dict[str, Any]]] = {}

    def add(node: str, entry: dict[str, Any]) -> None:
        out.setdefault(node, []).append(entry)

    for m in model.merges:
        term = model.classes.get(m.term_local)
        add(
            m.concept_local,
            {
                "relation": "same",
                "with": m.term_local,
                "label": term.label if term else "",
                "confidence": m.confidence,
                "accepted": True,
            },
        )
        add(
            m.term_local,
            {
                "relation": "same",
                "with": m.concept_local,
                "label": m.concept_name,
                "confidence": m.confidence,
                "accepted": True,
            },
        )
    for a in model.axioms:
        if a.source != "alignment":
            continue
        subject = model.classes.get(a.subject)
        term_is_subject = subject is not None and subject.source == "structured"
        relation = (
            "related" if a.kind == "related" else ("narrower" if term_is_subject else "broader")
        )
        term, concept = (a.subject, a.object) if term_is_subject else (a.object, a.subject)
        for node, other in ((concept, term), (term, concept)):
            other_node = model.classes.get(other)
            add(
                node,
                {
                    "relation": relation,
                    "with": other,
                    "label": other_node.label if other_node else "",
                    "confidence": a.confidence,
                    "accepted": True,
                },
            )
    return out


def _summary(links: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for link in links:
        counts[link["relation"]] = counts.get(link["relation"], 0) + 1
    best = max(links, key=lambda x: x.get("confidence") or 0.0) if links else None
    return {"counts": counts, "best": best}


def concept_items(model: MergedOntology) -> list[dict[str, Any]]:
    """Every Term class and document concept of *model*, Terms first, then by name. Keys
    starting with ``_`` are internal (``_text``: the search haystack)."""
    aligned = model.aligned_terms()
    links = _alignments(model)
    folded: dict[str, list[str]] = {}
    for m in model.merges:
        folded.setdefault(m.term_local, []).append(m.concept_name)
    items: list[dict[str, Any]] = []
    for c in model.classes.values():
        term = c.source == "structured"
        items.append(
            {
                "id": c.local,
                "name": c.label,
                "kind": "term" if term else c.kind,
                "origin": model.origin(c.local, aligned),
                "source": c.structured_source if term else c.documents_source,
                "definition": ((c.definitions[0] if c.definitions else "") or c.comment)[:300],
                "synonyms": c.alt_labels,
                "merged_concepts": sorted(folded.get(c.local, [])) if term else [],
                "folded_into": None,
                "enriched_by": c.documents_source if term else "",
                "evidence_count": len(set(c.derived_from)),
                "alignment": _summary(links.get(c.local, [])),
                "_text": " ".join([c.label, *c.alt_labels, c.comment, *c.definitions]).casefold(),
            }
        )
    for i in model.individuals.values():
        items.append(
            {
                "id": i.local,
                "name": i.label,
                "kind": i.kind,
                "origin": "document",
                "source": i.documents_source,
                "definition": i.comment[:300],
                "synonyms": i.alt_labels,
                "merged_concepts": [],
                "folded_into": None,
                "enriched_by": "",
                "evidence_count": len(set(i.derived_from)),
                "alignment": _summary(links.get(i.local, [])),
                "_text": " ".join([i.label, *i.alt_labels, i.comment]).casefold(),
            }
        )
    for m in model.merges:
        term = model.classes.get(m.term_local)
        items.append(
            {
                "id": m.concept_local,
                "name": m.concept_name,
                "kind": m.concept_kind,
                "origin": "document",
                "source": term.documents_source if term else "",
                "definition": "",
                "synonyms": [],
                "merged_concepts": [],
                "folded_into": {"id": m.term_local, "name": term.label if term else ""},
                "enriched_by": "",
                "evidence_count": len(set(m.derived_from)),
                "alignment": _summary(links.get(m.concept_local, [])),
                "_text": m.concept_name.casefold(),
            }
        )
    items.sort(key=lambda x: (x["kind"] != "term", KIND_ORDER.get(x["kind"], 9), x["name"]))
    return items


def concept_detail(model: MergedOntology, local: str) -> dict[str, Any] | None:
    item = next((i for i in concept_items(model) if i["id"] == local), None)
    if item is None:
        return None
    item = {k: v for k, v in item.items() if not k.startswith("_")}
    cls = model.classes.get(local)
    ind = model.individuals.get(local)
    merge = next((m for m in model.merges if m.concept_local == local), None)
    grounds = (
        cls.derived_from
        if cls
        else ind.derived_from
        if ind
        else merge.derived_from
        if merge
        else []
    )
    evidence = []
    for ev in dict.fromkeys(grounds):
        row = model.evidence.get(ev)
        if row is None:
            continue
        doc = model.documents.get(row.document)
        evidence.append(
            {
                "quote": row.quote,
                "verified": row.verified,
                "page": row.page,
                "document": doc.title if doc else "",
                "path": doc.path if doc else "",
            }
        )
    node = merge.term_local if merge else local
    attributes = [
        {
            "id": p.local,
            "label": p.label,
            "range": p.range,
            "source": p.source,
            "column": p.source_column,
            "datatype": p.datatype,
            "description": p.comment,
        }
        for p in sorted(model.properties.values(), key=lambda p: p.label)
        if p.kind == "datatype" and (p.domain == node or p.subject_node == node)
    ]
    relations = []
    for p in sorted(model.properties.values(), key=lambda p: (p.label, p.local)):
        if p.kind != "object" or p.source == "vocabulary":
            continue
        subject, obj = p.subject_node or p.domain, p.object_node or p.range
        if node not in (subject, obj):
            continue
        other = obj if subject == node else subject
        target = model.classes.get(other) or model.individuals.get(other)
        relations.append(
            {
                "id": p.local,
                "direction": "out" if subject == node else "in",
                "predicate": p.predicate or p.source,
                "label": p.label,
                "other": other,
                "other_label": target.label if target else other,
                "source": p.source,
            }
        )
    for a in model.axioms:
        if a.source == "document" and node in (a.subject, a.object):
            other = a.object if a.subject == node else a.subject
            target = model.classes.get(other)
            relations.append(
                {
                    "id": f"ax-{a.subject}-{a.kind}-{a.object}",
                    "direction": "out" if a.subject == node else "in",
                    "predicate": "is_a" if a.kind == "subclass_of" else a.kind,
                    "label": a.kind,
                    "other": other,
                    "other_label": target.label if target else other,
                    "source": "document",
                }
            )
    return {
        **item,
        "description": cls.comment if cls else ind.comment if ind else "",
        "definitions": cls.definitions if cls else [],
        "tables": cls.tables if cls else [],
        "individual_type": ind.type if ind else None,
        "attributes": attributes,
        "relations": relations,
        "alignments": _alignments(model).get(local, []),
        "evidence": evidence,
    }
