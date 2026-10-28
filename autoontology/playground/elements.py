# SPDX-License-Identifier: MIT
"""The playground's retrieval units: one :class:`Element` per element of the merged
ontology (``ontology.model.MergedOntology``), with the text that gets embedded, a UI link and
its 1-hop neighbours.

Element ids are the model's node/property locals (``term-…``, ``concept-…``, ``rel-…``,
``fk-…``, ``ev-…``) or the graph view's axiom edge id (``ax-<s>-<kind>-<o>``), so a citation
deep-links into the Graph page (``?focus=<id>``) and, for document concepts, the Concepts
page's detail sheet (``?concept=<concept id>``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import quote

from autoontology.ontology.model import LITERAL, THING, MergedOntology

ELEMENT_TYPES: tuple[str, ...] = (
    "term",
    "concept",
    "rule",
    "process",
    "individual",
    "relation",
    "evidence",
)
TEXT_LIMIT = 1500
INDIVIDUAL_TYPES = {"BusinessRule": "rule", "Process": "process"}
KIND_KO = {
    "entity": "엔터티",
    "metric": "지표",
    "attribute": "속성",
    "rule": "규칙",
    "process": "절차",
}
PREDICATE_KO = {
    "is_a": "의 하위 개념",
    "has_attribute": "속성 보유",
    "part_of": "의 일부",
    "governed_by": "규칙 적용",
    "owns": "소유/관리",
    "related_to": "관련",
}


@dataclass
class Element:
    id: str
    type: str
    label: str
    text: str
    #: client route of the UI (relative to /ui), e.g. /ontology?concept=<term-/concept-id>
    link: str
    #: the Graph page focused on the node standing for this element
    graph_link: str
    neighbours: list[str] = field(default_factory=list)
    #: node locals this element connects (relations) or is (classes)
    nodes: list[str] = field(default_factory=list)

    @property
    def text_sha(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def chip(self) -> dict[str, str]:
        return {
            "type": self.type,
            "id": self.id,
            "label": self.label,
            "link": self.link,
            "graph_link": self.graph_link,
        }


def _clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _join(values: list[str], sep: str = ", ") -> str:
    return sep.join(v for v in values if v)


def concept_link(local: str) -> str:
    """The Ontology page with the concept sheet of *local* open ("" unless *local* is a
    ``term-``/``concept-`` id, the ids ``/api/ontology/concepts/{id}`` serves)."""
    if local.startswith(("term-", "concept-")):
        return f"/ontology?concept={quote(local, safe='')}"
    return ""


def graph_link(local: str, nodes: set[str] | frozenset[str]) -> str:
    """The Graph page focused on *local*, or "" when *local* is not a graph node (a property
    endpoint such as owl:Thing / a literal type)."""
    return f"/graph?focus={quote(local, safe='')}" if local in nodes else ""


def elements_from_model(model: MergedOntology) -> list[Element]:
    """Every element of *model*, deterministic order (classes, individuals, relations,
    axioms, evidence)."""
    graph_nodes = frozenset(model.classes) | frozenset(model.individuals)

    def _graph(local: str) -> str:
        return graph_link(local, graph_nodes)

    def _concept_link(local: str) -> str:
        return concept_link(local) or _graph(local) or "/graph"

    def label_of(local: str) -> str:
        if local in model.classes:
            return model.classes[local].label
        if local in model.individuals:
            return model.individuals[local].label
        if local in (THING, LITERAL):
            return {THING: "Thing", LITERAL: "Literal"}[local]
        return local.removeprefix("xsd:")

    columns: dict[str, list[str]] = {}
    attributes: dict[str, list[str]] = {}
    for p in model.properties.values():
        if p.source == "structured":
            columns.setdefault(p.domain, []).append(
                _join(
                    [p.label, f"({p.source_column}{', ' + p.datatype if p.datatype else ''})"], " "
                )
            )
        elif p.source == "document" and p.kind == "datatype" and p.subject_node:
            attributes.setdefault(p.subject_node, []).append(label_of(p.value_concept))
    owner_of_evidence: dict[str, str] = {}
    for c in model.classes.values():
        for ev in c.derived_from:
            owner_of_evidence.setdefault(ev, c.local)
    for i in model.individuals.values():
        for ev in i.derived_from:
            owner_of_evidence.setdefault(ev, i.local)

    out: list[Element] = []
    for c in sorted(model.classes.values(), key=lambda c: (c.source, c.label, c.local)):
        if c.source == "structured":
            text = (
                f"Term(정형 데이터 개념) {c.label}. 설명: {c.comment}. "
                f"문서 정의: {_join(c.definitions, ' / ')}. 동의어: {_join(c.alt_labels)}. "
                f"테이블: {_join(c.tables)}. 컬럼: {_join(columns.get(c.local, []))}"
            )
            out.append(
                Element(
                    c.local,
                    "term",
                    c.label,
                    _clip(text),
                    _concept_link(c.local),
                    _graph(c.local),
                    nodes=[c.local],
                )
            )
        else:
            text = (
                f"문서 개념({KIND_KO.get(c.kind, c.kind)}) {c.label}. "
                f"정의: {_join(c.definitions, ' / ')}. 동의어: {_join(c.alt_labels)}. "
                f"속성: {_join(attributes.get(c.local, []))}"
            )
            out.append(
                Element(
                    c.local,
                    "concept",
                    c.label,
                    _clip(text),
                    _concept_link(c.local),
                    _graph(c.local),
                    nodes=[c.local],
                )
            )
    for i in sorted(model.individuals.values(), key=lambda i: (i.label, i.local)):
        kind = INDIVIDUAL_TYPES.get(i.type, "individual")
        text = (
            f"{'업무 규칙' if kind == 'rule' else '업무 절차' if kind == 'process' else '개체'}"
            f" {i.label}: {i.comment}. 동의어: {_join(i.alt_labels)}"
        )
        out.append(
            Element(
                i.local,
                kind,
                i.label,
                _clip(text),
                _concept_link(i.local),
                _graph(i.local),
                nodes=[i.local],
            )
        )
    for p in sorted(model.properties.values(), key=lambda p: p.local):
        if p.source == "semantic_fk":
            s, o = p.domain, p.range
            text = f"관계 {label_of(s)} → {label_of(o)}: {p.label} (외래키 컬럼 {p.source_column})"
        elif p.source == "document":
            s, o = p.subject_node or p.domain, p.object_node or p.range
            verb = PREDICATE_KO.get(p.predicate, p.predicate)
            text = f"관계 {label_of(s)} {p.predicate}({verb}) {label_of(o)}: {p.label}"
        else:
            continue
        out.append(
            Element(
                p.local,
                "relation",
                p.label or f"{label_of(s)} → {label_of(o)}",
                _clip(text),
                _graph(s) or _graph(o) or "/graph",
                _graph(s) or _graph(o),
                nodes=[s, o],
            )
        )
    axioms: dict[str, Element] = {}
    for a in model.axioms:
        aid = f"ax-{a.subject}-{a.kind}-{a.object}"
        how = "의 하위 개념" if a.kind == "subclass_of" else "와 관련"
        how_en = "is a subclass of" if a.kind == "subclass_of" else "is related to"
        origin = "정렬 판정" if a.source == "alignment" else "문서"
        conf = f", 신뢰도 {a.confidence:.2f}" if a.confidence is not None else ""
        text = (
            f"관계 {label_of(a.subject)}{how} {label_of(a.object)} ({origin}{conf}). {a.rationale}"
        )
        if aid in axioms:  # the same axiom from an alignment and from a document
            axioms[aid].text = _clip(f"{axioms[aid].text} / {origin}{conf}. {a.rationale}")
            continue
        axioms[aid] = Element(
            aid,
            "relation",
            f"{label_of(a.subject)} {how_en} {label_of(a.object)}",
            _clip(text),
            _graph(a.subject) or "/graph",
            _graph(a.subject),
            nodes=[a.subject, a.object],
        )
        out.append(axioms[aid])
    for m in model.merges:
        mid = f"same-{m.concept_local}"
        text = (
            f"관계 문서 개념 {m.concept_name}은(는) Term {label_of(m.term_local)}와 같은 개념"
            f"(정렬 판정{f', 신뢰도 {m.confidence:.2f}' if m.confidence is not None else ''})"
        )
        out.append(
            Element(
                mid,
                "relation",
                f"{m.concept_name} = {label_of(m.term_local)}",
                _clip(text),
                _concept_link(m.term_local),
                _graph(m.term_local),
                nodes=[m.term_local],
            )
        )
    for ev in sorted(model.evidence.values(), key=lambda e: e.local):
        doc = model.documents.get(ev.document)
        owner = owner_of_evidence.get(ev.local, "")
        page = f", {ev.page}쪽" if ev.page is not None else ""
        text = (
            f'근거 "{ev.quote}" (문서 {doc.title if doc else ev.document}{page}'
            f"{', 검증됨' if ev.verified else ''}) — {label_of(owner) if owner else ''}"
        )
        title = doc.title if doc else ev.document
        out.append(
            Element(
                ev.local,
                "evidence",
                f"{title}, p. {ev.page}" if ev.page is not None else title,
                _clip(text),
                _concept_link(owner) if owner else "/ontology",
                _graph(owner) if owner else "",
                nodes=[owner] if owner else [],
            )
        )

    # 1-hop neighbours: a relation connects its endpoints; an endpoint sees the relation
    # and the node at its other end
    by_node: dict[str, Element] = {
        e.id: e for e in out if e.type != "relation" and e.type != "evidence"
    }
    for e in out:
        if e.type != "relation":
            continue
        for node in e.nodes:
            owner = by_node.get(node)
            if owner is None:
                continue
            owner.neighbours.append(e.id)
            owner.neighbours += [n for n in e.nodes if n != node and n in by_node]
        e.neighbours = [n for n in e.nodes if n in by_node]
    for e in out:
        e.neighbours = list(dict.fromkeys(e.neighbours))
    return out
