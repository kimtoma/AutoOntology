"""The UI graph view of the merged ontology (``GET /api/graph``).

Built from the same :class:`~autoontology.export.merged.MergedOntology` the exporters write,
so the page and the files never disagree. Compared with the GraphML writer the view is
condensed for reading: object properties become direct domain -> range edges (no property
nodes), datatype properties stay on their class (``attributes``), documents and evidence
stay out (they are on the concept detail panel).

Layers:
- ``structured``: Term classes and the semantic-FK edges between them.
- ``documents``: document classes and individuals, document relations, ``is_a`` axioms.
- ``aligned``: the alignment links (folded concept -> Term ``same``; broader / narrower /
  related axioms) plus both endpoints of each.
- ``all``: everything.

``evidence=True`` adds the evidence layer after the node cap (document nodes never count against
it and only ground nodes that survived it): one ``document`` node per document that grounds a
shown node and one ``evidence`` edge node -> document (``count`` = evidence rows). Every node
carries ``evidence_count``; alignment edges carry ``relation`` (same / broader / narrower /
related). ``AUTOONTOLOGY_GRAPH_MAX_NODES`` overrides the node cap.
"""

from __future__ import annotations

import os
from typing import Any

from autoontology.export.merged import MergedOntology

LAYERS: tuple[str, ...] = ("all", "structured", "documents", "aligned")
#: Largest node count sent to the browser (Cytoscape's cose layout gets slow beyond this).
MAX_NODES = 1500


def max_nodes_from_env() -> int:
    """``AUTOONTOLOGY_GRAPH_MAX_NODES`` (positive int) or :data:`MAX_NODES`."""
    try:
        value = int(os.environ.get("AUTOONTOLOGY_GRAPH_MAX_NODES", "") or MAX_NODES)
    except ValueError:
        return MAX_NODES
    return value if value > 0 else MAX_NODES


def _node(
    local: str, label: str, type_: str, source: str, kind: str, origin: str, **extra: Any
) -> dict[str, Any]:
    return {
        "id": local,
        "label": label or local,
        "type": type_,
        "source": source,
        "kind": kind,
        "origin": origin,
        **{k: v for k, v in extra.items() if v},
    }


def to_view(
    model: MergedOntology, layer: str = "all", *, evidence: bool = False, max_nodes: int = MAX_NODES
) -> dict[str, Any]:
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {', '.join(LAYERS)}")
    nodes: dict[str, dict[str, Any]] = {}
    attrs: dict[str, list[str]] = {}
    for p in model.properties.values():
        if p.kind == "datatype" and p.source != "vocabulary":
            attrs.setdefault(p.domain, []).append(p.label)
    for c in model.classes.values():
        nodes[c.local] = _node(
            c.local,
            c.label,
            "class",
            c.structured_source or c.documents_source,
            c.kind or ("term" if c.source == "structured" else ""),
            "structured" if c.source == "structured" else "document",
            metric="Metric" in c.extra_types,
            synonyms=c.alt_labels[:12],
            definition=(c.definitions[0] if c.definitions else c.comment)[:400],
            tables=c.tables,
            attributes=sorted(attrs.get(c.local, []))[:30],
            enriched_by=c.documents_source if c.source == "structured" else "",
            concept_ids=c.concept_ids,
            evidence_count=len(set(c.derived_from)),
        )
    for i in model.individuals.values():
        nodes[i.local] = _node(
            i.local,
            i.label,
            "individual",
            i.documents_source,
            i.kind,
            "document",
            individual_type=i.type,
            definition=i.comment[:400],
            synonyms=i.alt_labels[:12],
            concept_ids=[i.local.removeprefix("concept-")],
            evidence_count=len(set(i.derived_from)),
        )
    for m in model.merges:
        term = model.classes.get(m.term_local)
        nodes[m.concept_local] = _node(
            m.concept_local,
            m.concept_name,
            "concept",
            term.documents_source if term else "",
            m.concept_kind,
            "document",
            folded_into=m.term_local,
            concept_ids=[m.concept_local.removeprefix("concept-")],
            evidence_count=len(set(m.derived_from)),
        )
    grounds: dict[str, list[str]] = {}  # node -> evidence locals
    for c in model.classes.values():
        grounds[c.local] = c.derived_from
    for i in model.individuals.values():
        grounds[i.local] = i.derived_from
    for m in model.merges:
        grounds[m.concept_local] = m.derived_from

    edges: list[dict[str, Any]] = []
    for p in model.properties.values():
        if p.kind != "object" or p.source == "vocabulary":
            continue
        # the relation's own endpoints: an individual's domain/range is only its type
        source, target = p.subject_node or p.domain, p.object_node or p.range
        if source not in nodes or target not in nodes:
            continue
        layer_of = "structured" if p.source == "semantic_fk" else "documents"
        edges.append(
            {
                "id": p.local,
                "source": source,
                "target": target,
                "type": p.source,
                "label": p.predicate or p.label,
                "title": p.label,
                "layer": layer_of,
            }
        )
    for a in model.axioms:
        edge = {
            "id": f"ax-{a.subject}-{a.kind}-{a.object}",
            "source": a.subject,
            "target": a.object,
            "type": a.kind,
            "label": "subClassOf" if a.kind == "subclass_of" else "related",
            "layer": "aligned" if a.source == "alignment" else "documents",
            "confidence": a.confidence,
        }
        if a.source == "alignment":  # narrower: Term ⊑ concept; broader: concept ⊑ Term
            subject = model.classes.get(a.subject)
            edge["relation"] = (
                "related"
                if a.kind == "related"
                else "narrower"
                if subject is not None and subject.source == "structured"
                else "broader"
            )
        edges.append(edge)
    for m in model.merges:
        edges.append(
            {
                "id": f"same-{m.concept_local}",
                "source": m.concept_local,
                "target": m.term_local,
                "type": "aligned_same",
                "label": "same",
                "layer": "aligned",
                "confidence": m.confidence,
                "relation": "same",
            }
        )

    def node_layer(n: dict[str, Any]) -> str:
        if n["type"] == "concept":
            return "aligned"
        return "structured" if n["origin"] == "structured" else "documents"

    if layer == "all":
        keep_edges = [dict(e) for e in edges]
        keep = set(nodes)
    else:
        keep_edges = [dict(e) for e in edges if e["layer"] == layer]
        keep = {k for k, n in nodes.items() if node_layer(n) == layer and layer != "aligned"}
        for e in keep_edges:
            keep.update((e["source"], e["target"]))
    out_nodes = [{**nodes[k], "layer": node_layer(nodes[k])} for k in nodes if k in keep]
    out_edges = [e for e in keep_edges if e["source"] in keep and e["target"] in keep]
    total_nodes = len(out_nodes)
    truncated = total_nodes > max_nodes
    if truncated:  # keep the best-connected nodes
        degree: dict[str, int] = {}
        for e in out_edges:
            for end in (e["source"], e["target"]):
                degree[end] = degree.get(end, 0) + 1
        ranked = sorted(out_nodes, key=lambda n: (-degree.get(n["id"], 0), n["label"]))
        kept = {n["id"] for n in ranked[:max_nodes]}
        out_nodes = [n for n in out_nodes if n["id"] in kept]
        out_edges = [e for e in out_edges if e["source"] in kept and e["target"] in kept]
    if evidence:  # after the cap: only for nodes that survived it, never counted against it
        shown = [n["id"] for n in out_nodes]
        docs_seen: dict[str, None] = {}
        for node_id in shown:
            per_doc: dict[str, int] = {}
            for ev in dict.fromkeys(grounds.get(node_id, [])):
                row = model.evidence.get(ev)
                if row is not None and row.document in model.documents:
                    per_doc[row.document] = per_doc.get(row.document, 0) + 1
            for doc_local, count in per_doc.items():
                docs_seen.setdefault(doc_local)
                out_edges.append(
                    {
                        "id": f"ev-{node_id}-{doc_local}",
                        "source": node_id,
                        "target": doc_local,
                        "type": "evidence",
                        "label": "evidence",
                        "layer": "evidence",
                        "count": count,
                    }
                )
        for doc_local in docs_seen:
            d = model.documents[doc_local]
            out_nodes.append(
                {
                    **_node(
                        doc_local,
                        d.title or d.path.rsplit("/", 1)[-1],
                        "document",
                        d.source,
                        "document",
                        "document",
                        path=d.path.rsplit("/", 1)[-1],
                    ),
                    "layer": "evidence",
                }
            )
    seen: dict[str, int] = {}
    for e in out_edges:  # axioms can repeat (same subject/kind/object from two sources)
        n = seen.get(e["id"], 0)
        seen[e["id"]] = n + 1
        if n:
            e["id"] = f"{e['id']}~{n}"
    return {
        "name": model.name,
        "layer": layer,
        "structured_sources": model.structured_sources,
        "documents_sources": model.documents_sources,
        "nodes": out_nodes,
        "edges": out_edges,
        "counts": {"nodes": len(out_nodes), "edges": len(out_edges), "total_nodes": total_nodes},
        "truncated": truncated,
    }
