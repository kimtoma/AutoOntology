# SPDX-License-Identifier: MIT
"""The unified ontology model: structured Terms (upstream) and document concepts
(AutoOntology store) joined through the alignment verdicts.

Built once per ``autoontology build`` by :func:`build` (its ``merge`` step), persisted as a
JSON snapshot (:meth:`MergedOntology.to_dict` / :meth:`MergedOntology.from_dict`, table
``ontology_snapshots``), then read by the API, the playground and the format writers
(``export/rdf.py`` for Turtle/JSON-LD, ``export/graphml.py``, ``export/report.py``). Everything here is plain data and
deterministic (sorted, ids derived from upstream/store ids), so two exports of unchanged
inputs are identical.

Modelling (README "Ontology modelling"):

* Term -> class ``term-<id>`` (source ``structured``), column attributes -> datatype
  properties ``attr-<id>``, SEMANTIC_FK -> object properties ``fk-<attr id>-<n>``.
* Accepted ``same`` alignment -> the concept is folded into the Term's class (alt labels,
  document definition, evidence). ``broader``: concept ⊑ Term; ``narrower``: Term ⊑
  concept; ``related``: ``skos:related``. Other entity/metric/attribute concepts -> their
  own class ``concept-<id>`` (source ``document``); metric also typed ``ao:Metric``;
  rule/process (and other) concepts -> individuals of ``ao:BusinessRule``/``ao:Process``
  (``skos:Concept``).
* Document relations -> one property per relation ``rel-<id>`` (sub-property of
  ``ao:<predicate>``), ``is_a`` -> subclass axiom, ``has_attribute`` to an attribute
  concept -> datatype property; evidence -> ``prov:Entity`` nodes ``ev-<id>``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from autoontology.config import DEFAULT_BASE_IRI
from autoontology.store.alignments import AlignmentRecord
from autoontology.store.documents import ConceptRow, DocumentRow, RelationRow
from autoontology.structured.semantic import StructuredSnapshot, qualified

#: Version of the snapshot JSON (:meth:`MergedOntology.to_dict`).
MODEL_FORMAT = 1
CLASS_KINDS = frozenset({"entity", "metric", "attribute"})
INDIVIDUAL_TYPES = {"rule": "BusinessRule", "process": "Process"}
#: catalog datatype -> XSD local name (anything else stays rdfs:Literal)
XSD_TYPES = {
    "text": "string",
    "varchar": "string",
    "character varying": "string",
    "char": "string",
    "string": "string",
    "bigint": "integer",
    "integer": "integer",
    "int": "integer",
    "smallint": "integer",
    "int64": "integer",
    "int32": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "double precision": "double",
    "real": "double",
    "float": "double",
    "double": "double",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "timestamp": "dateTime",
    "timestamp without time zone": "dateTime",
    "timestamp with time zone": "dateTime",
    "timestamptz": "dateTime",
}
LITERAL = "LITERAL"  # rdfs:Literal
THING = "THING"  # owl:Thing

_HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
_LATIN = re.compile(r"[A-Za-z]")


def lang_of(text: str) -> str | None:
    if _HANGUL.search(text or ""):
        return "ko"
    if _LATIN.search(text or ""):
        return "en"
    return None


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-").lower() or "x"


@dataclass
class OClass:
    local: str
    label: str
    source: str  # structured | document
    alt_labels: list[str] = field(default_factory=list)
    comment: str = ""
    definitions: list[str] = field(default_factory=list)  # skos:definition (documents)
    tables: list[str] = field(default_factory=list)  # dcterms:source (schema.table)
    derived_from: list[str] = field(default_factory=list)  # evidence locals
    extra_types: list[str] = field(default_factory=list)  # ao:Metric
    term_id: str = ""
    concept_ids: list[str] = field(default_factory=list)
    kind: str = ""  # concept kind (document classes)
    structured_source: str = ""
    documents_source: str = ""
    #: instance-like names of folded concepts left off alt_labels: {alias, reason, concept}
    rejected_aliases: list[dict[str, str]] = field(default_factory=list)


@dataclass
class OProperty:
    local: str
    kind: str  # object | datatype
    label: str
    domain: str  # node local
    range: str  # node local, LITERAL, THING or "xsd:<type>"
    source: str  # structured | semantic_fk | document | vocabulary
    source_column: str = ""
    datatype: str = ""
    predicate: str = ""  # document relations: the extraction predicate
    sub_property_of: str = ""
    value_concept: str = ""  # has_attribute -> attribute concept node
    derived_from: list[str] = field(default_factory=list)
    comment: str = ""
    owner: str = ""  # the source (structured or documents) it comes from
    #: document relations: the endpoints' own nodes (an individual's domain/range is its
    #: type, e.g. ao:Process, so the graph view needs the individual itself)
    subject_node: str = ""
    object_node: str = ""


@dataclass
class OIndividual:
    local: str
    label: str
    type: str  # BusinessRule | Process | Concept
    kind: str
    comment: str = ""
    alt_labels: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    documents_source: str = ""


@dataclass
class ODocument:
    local: str
    title: str
    sha256: str
    path: str
    source: str


@dataclass
class OEvidence:
    local: str
    document: str
    quote: str
    page: int | None
    verified: bool


@dataclass
class OAxiom:
    subject: str
    kind: str  # subclass_of | related
    object: str
    source: str  # alignment | document
    derived_from: list[str] = field(default_factory=list)
    confidence: float | None = None
    rationale: str = ""
    #: an alignment axiom from a judge verdict that passed the accept threshold (the
    #: persisted ``accepted``); a mere proposal is still exported but changes no origin
    accepted: bool = False


@dataclass
class OMerge:
    """A document concept folded into a Term class (``same``)."""

    concept_local: str
    concept_name: str
    concept_kind: str
    term_local: str
    confidence: float | None
    derived_from: list[str] = field(default_factory=list)
    #: the documents source the concept came from (several may fold into one Term)
    documents_source: str = ""


@dataclass
class DocumentsInput:
    source: str
    documents: Sequence[DocumentRow]
    concepts: Sequence[ConceptRow]
    relations: Sequence[RelationRow]
    evidence: Sequence[dict[str, Any]]
    alignments: Sequence[AlignmentRecord]
    #: `align --dry-run` verdicts: listed in the report only, never change the model
    previews: Sequence[AlignmentRecord] = ()


@dataclass
class MergedOntology:
    name: str
    base: str
    structured_sources: list[str] = field(default_factory=list)
    documents_sources: list[str] = field(default_factory=list)
    classes: dict[str, OClass] = field(default_factory=dict)
    properties: dict[str, OProperty] = field(default_factory=dict)
    individuals: dict[str, OIndividual] = field(default_factory=dict)
    documents: dict[str, ODocument] = field(default_factory=dict)
    evidence: dict[str, OEvidence] = field(default_factory=dict)
    axioms: list[OAxiom] = field(default_factory=list)
    merges: list[OMerge] = field(default_factory=list)
    #: report lists
    fallback_terms: list[str] = field(default_factory=list)
    unaligned: list[dict[str, Any]] = field(default_factory=list)
    low_confidence: list[dict[str, Any]] = field(default_factory=list)
    #: accepted `same` whose Term's structured source is not part of this export
    source_missing: list[dict[str, Any]] = field(default_factory=list)
    #: `same` verdicts from `align --dry-run` (never folded)
    dry_run_same: list[dict[str, Any]] = field(default_factory=list)
    collapsed_relations: list[str] = field(default_factory=list)
    without_evidence: list[str] = field(default_factory=list)
    alignment_counts: dict[str, int] = field(default_factory=dict)
    #: owl:Class count the Turtle must have (:func:`expected_class_count` over the inputs)
    expected_classes: int | None = None

    # --- snapshot (de)serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict; :meth:`from_dict` rebuilds an equal model."""
        return {"format": MODEL_FORMAT, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MergedOntology:
        """The model of a :meth:`to_dict` snapshot; a snapshot written in a newer
        :data:`MODEL_FORMAT` than this code knows is refused, never silently mis-read."""
        version = data.get("format", 1)
        if not isinstance(version, int) or version > MODEL_FORMAT:
            raise ValueError(
                f"ontology snapshot format {version!r} is newer than this AutoOntology "
                f"(format {MODEL_FORMAT}): upgrade AutoOntology to read it"
            )
        data = {k: v for k, v in data.items() if k != "format"}
        nested: dict[str, type] = {
            "classes": OClass,
            "properties": OProperty,
            "individuals": OIndividual,
            "documents": ODocument,
            "evidence": OEvidence,
        }
        known = {f.name for f in fields(cls)}
        kw: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        for key, typ in nested.items():
            kw[key] = {k: _make(typ, v) for k, v in (data.get(key) or {}).items()}
        kw["axioms"] = [_make(OAxiom, a) for a in data.get("axioms") or []]
        kw["merges"] = [_make(OMerge, m) for m in data.get("merges") or []]
        return cls(**kw)

    # --- origins -----------------------------------------------------------------------

    def aligned_terms(self) -> set[str]:
        """Term classes joined to documents: a folded (``same``) concept, or an *accepted*
        alignment axiom (broader/narrower/related) touching them. A verdict below the
        accept threshold is only a proposal and leaves the Term ``structured``."""
        out = {c.local for c in self.classes.values() if c.source == "structured" and c.concept_ids}
        for a in self.axioms:
            if a.source == "alignment" and a.accepted:
                for end in (a.subject, a.object):
                    cls = self.classes.get(end)
                    if cls is not None and cls.source == "structured":
                        out.add(end)
        return out

    def origin(self, local: str, aligned: set[str] | None = None) -> str:
        """``structured`` | ``document`` | ``aligned`` of a class/individual node."""
        aligned = self.aligned_terms() if aligned is None else aligned
        if local in aligned:
            return "aligned"
        cls = self.classes.get(local)
        return "structured" if cls is not None and cls.source == "structured" else "document"

    def node_class(self, local: str) -> str:
        """The class standing for a node in domain/range: itself for classes, the
        individual's type for individuals."""
        if local in self.individuals:
            return self.individuals[local].type
        return local

    def counts(self) -> dict[str, Any]:
        by_source: dict[str, dict[str, int]] = {}

        def bump(source: str, kind: str) -> None:
            by_source.setdefault(source, {}).setdefault(kind, 0)
            by_source[source][kind] += 1

        for c in self.classes.values():
            bump(c.structured_source or c.documents_source, f"class:{c.source}")
        for p in self.properties.values():
            if p.source != "vocabulary":
                bump(p.owner, f"{p.kind}_property:{p.source}")
        for i in self.individuals.values():
            bump(i.documents_source, f"individual:{i.type}")
        return {
            "classes": len(self.classes),
            "classes_structured": sum(c.source == "structured" for c in self.classes.values()),
            "classes_document": sum(c.source == "document" for c in self.classes.values()),
            "metric_classes": sum("Metric" in c.extra_types for c in self.classes.values()),
            "object_properties": sum(
                p.kind == "object" and p.source != "vocabulary" for p in self.properties.values()
            ),
            "datatype_properties": sum(p.kind == "datatype" for p in self.properties.values()),
            "semantic_fk_properties": sum(
                p.source == "semantic_fk" for p in self.properties.values()
            ),
            "relation_properties": sum(
                p.source == "document" and p.kind == "object" for p in self.properties.values()
            ),
            "column_properties": sum(p.source == "structured" for p in self.properties.values()),
            "relation_datatype_properties": sum(
                p.source == "document" and p.kind == "datatype" for p in self.properties.values()
            ),
            "predicate_properties": sum(p.source == "vocabulary" for p in self.properties.values()),
            "object_properties_total": sum(p.kind == "object" for p in self.properties.values()),
            "individuals": len(self.individuals),
            "business_rules": sum(i.type == "BusinessRule" for i in self.individuals.values()),
            "processes": sum(i.type == "Process" for i in self.individuals.values()),
            "documents": len(self.documents),
            "evidence": len(self.evidence),
            "subclass_axioms": sum(a.kind == "subclass_of" for a in self.axioms),
            "related_axioms": sum(a.kind == "related" for a in self.axioms),
            "merged_concepts": len(self.merges),
            "by_source": by_source,
        }


def _make(typ: type, value: dict[str, Any]) -> Any:
    known = {f.name for f in fields(typ)}
    return typ(**{k: v for k, v in value.items() if k in known})


def _xsd(datatype: str | None) -> str:
    key = (datatype or "").strip().lower()
    key = re.sub(r"\(.*\)$", "", key).strip()
    return f"xsd:{XSD_TYPES[key]}" if key in XSD_TYPES else LITERAL


def _add_unique(values: list[str], *new: str) -> None:
    seen = {v.casefold() for v in values}
    for value in new:
        clean = " ".join(str(value or "").split())
        if clean and clean.casefold() not in seen:
            values.append(clean)
            seen.add(clean.casefold())


def _add_structured(model: MergedOntology, snap: StructuredSnapshot) -> dict[str, str]:
    term_class: dict[str, str] = {}
    for term in sorted(snap.terms, key=lambda t: (t.name, t.id)):
        local = f"term-{slug(term.id)}"
        term_class[term.id] = local
        cls = OClass(
            local=local,
            label=term.name,
            source="structured",
            comment=term.description,
            term_id=term.id,
            structured_source=snap.source,
            tables=sorted(
                ".".join(p for p in (t.get("schema"), t.get("name")) if p) for t in term.tables
            ),
        )
        _add_unique(cls.alt_labels, *term.synonyms)
        model.classes[local] = cls
        if term.is_fallback:
            model.fallback_terms.append(term.name)
        for attr in sorted(term.attributes, key=lambda a: (a.get("name") or "", a["id"])):
            plocal = f"attr-{slug(attr['id'])}"
            model.properties[plocal] = OProperty(
                local=plocal,
                kind="datatype",
                label=attr.get("name") or "",
                domain=local,
                range=_xsd(attr.get("datatype")),
                source="structured",
                source_column=qualified(attr),
                datatype=attr.get("datatype") or "",
                comment=attr.get("description") or "",
                owner=snap.source,
            )
    for n, fk in enumerate(sorted(snap.fks, key=lambda f: (f.attribute_id, f.column))):
        if fk.owner_term_id not in term_class or fk.target_term_id not in term_class:
            continue
        target = model.classes[term_class[fk.target_term_id]]
        plocal = f"fk-{slug(fk.attribute_id)}-{n}"
        model.properties[plocal] = OProperty(
            local=plocal,
            kind="object",
            label=f"{fk.attribute_name} refers to {target.label}",
            domain=term_class[fk.owner_term_id],
            range=target.local,
            source="semantic_fk",
            source_column=fk.column,
            owner=snap.source,
        )
    return term_class


def _add_documents(model: MergedOntology, docs: DocumentsInput, term_class: dict[str, str]) -> None:
    for d in sorted(docs.documents, key=lambda d: d.path):
        model.documents[f"doc-{slug(d.id)}"] = ODocument(
            local=f"doc-{slug(d.id)}",
            title=d.title,
            sha256=d.sha256,
            path=d.path,
            source=docs.source,
        )
    concept_ev: dict[str, list[str]] = {}
    relation_ev: dict[str, list[str]] = {}
    for row in docs.evidence:
        local = f"ev-{row['id']}"
        doc_local = f"doc-{slug(row['doc_id'])}"
        if doc_local not in model.documents:
            continue
        model.evidence[local] = OEvidence(
            local=local,
            document=doc_local,
            quote=row["quote"],
            page=row.get("page"),
            verified=bool(row["verified"]),
        )
        if row.get("concept_id"):
            concept_ev.setdefault(row["concept_id"], []).append(local)
        if row.get("relation_id"):
            relation_ev.setdefault(row["relation_id"], []).append(local)
    from autoontology.unstructured.align import split_aliases

    # committed verdicts only: a dry-run row (wherever it comes from) never folds, never
    # becomes an axiom and is not counted; it is only reported (dry_run_same)
    alignments = {a.concept_id: a for a in docs.alignments if not a.dry_run}
    previews = {
        a.concept_id: a for a in [*docs.previews, *(a for a in docs.alignments if a.dry_run)]
    }
    counts: dict[str, int] = {}
    node_of: dict[str, str] = {}  # concept name -> node local
    kind_of: dict[str, str] = {}
    for c in sorted(docs.concepts, key=lambda c: (c.name, c.id)):
        evidence = concept_ev.get(c.id, [])
        if not evidence:
            model.without_evidence.append(c.name)
        kind_of[c.name] = c.kind
        a = alignments.get(c.id)
        relation = a.relation if a else "unaligned"
        counts[relation] = counts.get(relation, 0) + 1
        term_local = term_class.get(a.term_id) if a and a.term_id else None
        if a and a.accepted and a.relation == "same" and term_local:
            cls = model.classes[term_local]
            # never an instance-like name (record ID, company name) on the Term's class
            kept, rejected = split_aliases([c.name, *c.synonyms, c.name_en], c.name)
            _add_unique(cls.alt_labels, *kept)
            cls.rejected_aliases += [r for r in rejected if r not in cls.rejected_aliases]
            if c.definition:
                _add_unique(cls.definitions, c.definition)
            cls.derived_from += evidence
            cls.concept_ids.append(c.id)
            cls.documents_source = cls.documents_source or docs.source
            model.merges.append(
                OMerge(
                    f"concept-{slug(c.id)}",
                    c.name,
                    c.kind,
                    term_local,
                    a.confidence,
                    evidence,
                    documents_source=docs.source,
                )
            )
            node_of[c.name] = term_local
            continue
        local = f"concept-{slug(c.id)}"
        node_of[c.name] = local
        if c.kind in CLASS_KINDS:
            cls = OClass(
                local=local,
                label=c.name,
                source="document",
                kind=c.kind,
                derived_from=list(evidence),
                concept_ids=[c.id],
                documents_source=docs.source,
                extra_types=["Metric"] if c.kind == "metric" else [],
            )
            _add_unique(cls.alt_labels, *c.synonyms, c.name_en)
            if c.definition:
                cls.definitions.append(c.definition)
            model.classes[local] = cls
            entry = {
                "name": c.name,
                "kind": c.kind,
                "source": docs.source,
                "relation": relation,
                "term": a.term_name if a else "",
            }
            preview = previews.get(c.id)
            if a and a.relation == "same":  # judged same but not folded: say why
                if a.accepted and not term_local:
                    model.source_missing.append(
                        {
                            **entry,
                            "confidence": a.confidence,
                            "structured_source": a.structured_source,
                        }
                    )
                else:
                    model.low_confidence.append({**entry, "confidence": a.confidence})
            elif preview is not None and preview.relation == "same":
                model.dry_run_same.append(
                    {**entry, "term": preview.term_name, "confidence": preview.confidence}
                )
            if term_local and a and a.relation in ("broader", "narrower", "related"):
                # broader: concept ⊑ Term; narrower: Term ⊑ concept; related: concept ~ Term
                subject, obj = (
                    (term_local, local) if a.relation == "narrower" else (local, term_local)
                )
                model.axioms.append(
                    OAxiom(
                        subject=subject,
                        kind="related" if a.relation == "related" else "subclass_of",
                        object=obj,
                        source="alignment",
                        derived_from=list(evidence),
                        confidence=a.confidence,
                        rationale=a.rationale,
                        accepted=bool(a.accepted),
                    )
                )
            else:
                model.unaligned.append(entry)
        else:
            model.individuals[local] = OIndividual(
                local=local,
                label=c.name,
                type=INDIVIDUAL_TYPES.get(c.kind, "Concept"),
                kind=c.kind,
                comment=c.definition,
                derived_from=list(evidence),
                documents_source=docs.source,
            )
            _add_unique(model.individuals[local].alt_labels, *c.synonyms, c.name_en)
    # summed over every documents source of the export
    totals = {**model.alignment_counts}
    for k, v in counts.items():
        totals[k] = totals.get(k, 0) + v
    model.alignment_counts = {k: totals[k] for k in sorted(totals)}
    for r in sorted(docs.relations, key=lambda r: (r.subject, r.predicate, r.object, r.id)):
        s, o = node_of.get(r.subject), node_of.get(r.object)
        if not s or not o:
            continue
        evidence = relation_ev.get(r.id, [])
        if s == o:
            model.collapsed_relations.append(f"{r.subject} {r.predicate} {r.object}")
            continue
        if r.predicate == "is_a" and s in model.classes and o in model.classes:
            model.axioms.append(
                OAxiom(
                    subject=s,
                    kind="subclass_of",
                    object=o,
                    source="document",
                    derived_from=evidence,
                )
            )
            continue
        local = f"rel-{slug(r.id)}"
        label = (
            r.label
            if r.label and r.label != r.predicate
            else f"{r.subject} {r.predicate} {r.object}"
        )
        if r.predicate == "has_attribute" and kind_of.get(r.object) == "attribute":
            model.properties[local] = OProperty(
                local=local,
                kind="datatype",
                label=label,
                domain=model.node_class(s),
                range=LITERAL,
                source="document",
                predicate=r.predicate,
                value_concept=o,
                derived_from=evidence,
                owner=docs.source,
                subject_node=s,
                object_node=o,
            )
            continue
        model.properties[local] = OProperty(
            local=local,
            kind="object",
            label=label,
            domain=model.node_class(s),
            range=model.node_class(o),
            source="document",
            predicate=r.predicate,
            sub_property_of=r.predicate,
            derived_from=evidence,
            owner=docs.source,
            subject_node=s,
            object_node=o,
        )
        if r.predicate not in model.properties:
            model.properties[r.predicate] = OProperty(
                local=r.predicate,
                kind="object",
                label=r.predicate.replace("_", " "),
                domain=THING,
                range=THING,
                source="vocabulary",
                predicate=r.predicate,
            )


def expected_class_count(
    structured: Sequence[StructuredSnapshot], documents: Sequence[DocumentsInput]
) -> int:
    """owl:Class count the export must have, from the inputs alone (not the model): every
    Term, plus every entity/metric/attribute concept not folded into an exported Term by an
    accepted ``same``."""
    term_ids = {t.id for snap in structured for t in snap.terms}
    count = len(term_ids)
    for docs in documents:
        folded = {
            a.concept_id
            for a in docs.alignments
            if a.accepted and not a.dry_run and a.relation == "same" and a.term_id in term_ids
        }
        count += sum(c.kind in CLASS_KINDS and c.id not in folded for c in docs.concepts)
    return count


def build(
    name: str,
    *,
    structured: Sequence[StructuredSnapshot] = (),
    documents: Sequence[DocumentsInput] = (),
    base: str = DEFAULT_BASE_IRI,
) -> MergedOntology:
    """The unified model of *structured* and *documents*; *name* is the workspace name and
    *base* the namespace of every IRI (``AUTOONTOLOGY_BASE_IRI``)."""
    model = MergedOntology(
        name=name,
        base=base,
        structured_sources=[s.source for s in structured],
        documents_sources=[d.source for d in documents],
    )
    term_class: dict[str, str] = {}
    for snap in structured:
        term_class.update(_add_structured(model, snap))
    for docs in documents:
        _add_documents(model, docs, term_class)
    model.expected_classes = expected_class_count(structured, documents)
    return model
