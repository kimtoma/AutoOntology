# SPDX-License-Identifier: MIT
"""GraphML writer for the merged ontology (``xml.etree``, no extra dependency).

Nodes: classes, properties, individuals, documents, plus one ``concept`` node per document
concept folded into a Term class (so the ``aligned_same`` link stays visible). Every node
has ``type``, ``label`` and ``source``. Edges are typed: ``subclass_of``, ``related``,
``object_property`` (domain -> property -> range), ``datatype_property``
(domain -> property), ``aligned_same`` (concept -> Term class) and ``evidence``
(node -> document, with the number of quotes).

The file is a directed multigraph in the layout networkx's ``write_graphml`` produces:
one ``<key>`` per attribute name and domain (all ``attr.type="string"``), edges carry an
``id`` that numbers parallel edges between the same pair from 0, and an edge to a node
that was never added creates a bare node.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from autoontology.ontology.model import THING, MergedOntology

GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_SCHEMA_LOCATION = (
    "http://graphml.graphdrawing.org/xmlns http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"
)

#: Characters XML 1.0 cannot carry even escaped (C0 controls but tab/LF/CR, lone
#: surrogates, U+FFFE/U+FFFF); PDF/HWP text can contain them.
_XML_INVALID_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_safe(value: str) -> str:
    """*value* without the characters XML 1.0 forbids (ElementTree writes them raw)."""
    return _XML_INVALID_RE.sub("", value)


class Graph:
    """A minimal directed multigraph with string attributes (insertion ordered)."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: list[tuple[str, str, int, dict[str, str]]] = []
        self._pair_count: Counter[tuple[str, str]] = Counter()

    def add_node(self, node: str, **attrs: Any) -> None:
        self.nodes.setdefault(node, {}).update({k: str(v) for k, v in attrs.items()})

    def add_edge(self, u: str, v: str, **attrs: Any) -> None:
        for node in (u, v):
            self.nodes.setdefault(node, {})
        key = self._pair_count[(u, v)]
        self._pair_count[(u, v)] += 1
        self.edges.append((u, v, key, {k: str(val) for k, val in attrs.items()}))

    def __contains__(self, node: str) -> bool:
        return node in self.nodes


VOCAB_NODES = {
    "BusinessRule": "ao:BusinessRule",
    "Process": "ao:Process",
    "Concept": "skos:Concept",
    THING: "owl:Thing",
}


def to_graph(model: MergedOntology) -> Graph:
    g = Graph(name=model.name)

    def add(local: str, type_: str, label: str, source: str, **extra: str) -> None:
        g.add_node(
            local,
            type=type_,
            label=label or local,
            source=source,
            **{k: v for k, v in extra.items() if v},
        )

    def ensure(local: str) -> None:
        if local not in g and local in VOCAB_NODES:
            add(local, "vocabulary", VOCAB_NODES[local], "vocabulary")

    for c in model.classes.values():
        add(
            c.local,
            "class",
            c.label,
            c.source,
            kind=c.kind,
            metric="true" if "Metric" in c.extra_types else "",
        )
    for i in model.individuals.values():
        add(i.local, "individual", i.label, "document", kind=i.kind, individual_type=i.type)
    for d in model.documents.values():
        add(d.local, "document", d.title, "document", path=d.path, sha256=d.sha256)
    for m in model.merges:
        add(m.concept_local, "concept", m.concept_name, "document", kind=m.concept_kind)
        g.add_edge(
            m.concept_local,
            m.term_local,
            type="aligned_same",
            label="same",
            confidence=f"{m.confidence:.3f}" if m.confidence is not None else "",
        )
    for p in model.properties.values():
        if p.source == "vocabulary":
            continue
        type_ = "object_property" if p.kind == "object" else "datatype_property"
        add(
            p.local,
            type_,
            p.label,
            p.source,
            source_column=p.source_column,
            predicate=p.predicate,
            datatype=p.datatype,
        )
        ensure(p.domain)
        g.add_edge(p.domain, p.local, type=type_, label="domain")
        if p.kind == "object":
            ensure(p.range)
            g.add_edge(p.local, p.range, type=type_, label="range")
        elif p.value_concept:
            g.add_edge(p.local, p.value_concept, type=type_, label="value concept")
    for a in model.axioms:
        g.add_edge(
            a.subject,
            a.object,
            type=a.kind,
            label="subClassOf" if a.kind == "subclass_of" else "related",
            source=a.source,
        )
    evidence_docs = {e.local: e.document for e in model.evidence.values()}

    def evidence_edges(local: str, refs: list[str]) -> None:
        for doc, n in sorted(Counter(evidence_docs[r] for r in refs if r in evidence_docs).items()):
            g.add_edge(local, doc, type="evidence", label="evidence", quotes=str(n))

    for c in model.classes.values():
        if c.source == "document":
            evidence_edges(c.local, c.derived_from)
    for m in model.merges:
        evidence_edges(m.concept_local, m.derived_from)
    for i in model.individuals.values():
        evidence_edges(i.local, i.derived_from)
    for p in model.properties.values():
        if p.derived_from:
            evidence_edges(p.local, p.derived_from)
    return g


def to_xml(g: Graph) -> ET.ElementTree:
    # Unqualified tags with literal xmlns attributes: the output is GraphML-namespaced
    # without ET.register_namespace (a process-wide registry) or ns0: prefixes.
    root = ET.Element(
        "graphml",
        {"xmlns": GRAPHML_NS, "xmlns:xsi": _XSI_NS, "xsi:schemaLocation": _SCHEMA_LOCATION},
    )
    keys: dict[tuple[str, str], str] = {}

    def key_for(domain: str, name: str) -> str:
        if (domain, name) not in keys:
            keys[(domain, name)] = f"d{len(keys)}"
        return keys[(domain, name)]

    # declare every key first (GraphML wants <key> before <graph>)
    graph_attrs = {"name": g.name} if g.name else {}
    for name in graph_attrs:
        key_for("graph", name)
    for attrs in g.nodes.values():
        for name in attrs:
            key_for("node", name)
    for *_, attrs in g.edges:
        for name in attrs:
            key_for("edge", name)
    for (domain, name), kid in keys.items():
        ET.SubElement(
            root,
            "key",
            {"id": kid, "for": domain, "attr.name": name, "attr.type": "string"},
        )
    graph = ET.SubElement(root, "graph", {"edgedefault": "directed"})

    def data(parent: ET.Element, domain: str, attrs: dict[str, str]) -> None:
        for name, value in attrs.items():
            ET.SubElement(parent, "data", {"key": keys[(domain, name)]}).text = xml_safe(value)

    data(graph, "graph", graph_attrs)
    for node, attrs in g.nodes.items():
        data(ET.SubElement(graph, "node", {"id": xml_safe(node)}), "node", attrs)
    # Edge ids must be unique across the whole document (GraphML ``id`` is an XML ID).
    for index, (u, v, _key, attrs) in enumerate(g.edges):
        data(
            ET.SubElement(
                graph,
                "edge",
                {"source": xml_safe(u), "target": xml_safe(v), "id": f"e{index}"},
            ),
            "edge",
            attrs,
        )
    tree = ET.ElementTree(root)
    ET.indent(tree)
    return tree


def write(model: MergedOntology, out_dir: Path) -> Path:
    path = out_dir / "ontology.graphml"
    to_xml(to_graph(model)).write(path, encoding="utf-8", xml_declaration=True)
    return path


def read(path: Path) -> tuple[dict[str, dict[str, str]], list[tuple[str, str, dict[str, str]]]]:
    """``(nodes, edges)`` of a GraphML file with string keys (for checks and tests)."""
    root = ET.parse(path).getroot()
    ns = {"g": GRAPHML_NS}
    names = {k.get("id"): k.get("attr.name") for k in root.findall("g:key", ns)}
    graph = root.find("g:graph", ns)
    if graph is None:
        raise ValueError(f"{path}: no <graph> element")

    def attrs(el: ET.Element) -> dict[str, str]:
        return {
            names.get(d.get("key"), d.get("key") or ""): d.text or ""
            for d in el.findall("g:data", ns)
        }

    nodes = {n.get("id") or "": attrs(n) for n in graph.findall("g:node", ns)}
    edges = [
        (e.get("source") or "", e.get("target") or "", attrs(e))
        for e in graph.findall("g:edge", ns)
    ]
    return nodes, edges
