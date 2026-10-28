"""Turtle and JSON-LD writers for the merged ontology (rdflib)."""

from __future__ import annotations

from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, PROV, RDF, RDFS, SKOS, XSD

from autoontology.export.merged import LITERAL, THING, MergedOntology, lang_of

VOCAB = {
    "Metric": "Metric (metric concept)",
    "BusinessRule": "Business rule",
    "Process": "Process",
}


def _text(value: str) -> Literal:
    return Literal(value, lang=lang_of(value))


def to_graph(model: MergedOntology) -> Graph:
    g = Graph()
    os_ = Namespace(model.base)
    for prefix, ns in (("owl", OWL), ("rdfs", RDFS), ("rdf", RDF), ("skos", SKOS),
                       ("prov", PROV), ("dcterms", DCTERMS), ("xsd", XSD), ("os", os_)):
        g.bind(prefix, ns, override=True)
    onto = URIRef(model.base.rstrip("#"))
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, DCTERMS.title, Literal(f"AutoOntology merged ontology: {model.name}")))
    for source in model.structured_sources + model.documents_sources:
        g.add((onto, DCTERMS.source, Literal(source)))

    def node(local: str) -> URIRef:
        if local == THING:
            return OWL.Thing
        if local == LITERAL:
            return RDFS.Literal
        if local.startswith("xsd:"):
            return XSD[local[4:]]
        if local in ("Concept",):
            return SKOS.Concept
        return os_[local]

    # vocabulary: rdfs:Class (not owl:Class), so owl:Class counts only the ontology's
    for local, label in VOCAB.items():
        g.add((os_[local], RDF.type, RDFS.Class))
        g.add((os_[local], RDFS.label, _text(label)))
    for prop, label in (("source", "source"), ("sourceColumn", "source column"),
                        ("datatype", "catalog datatype"), ("path", "path"), ("page", "page"),
                        ("quote", "quote"), ("valueConcept", "value concept"),
                        ("alignmentConfidence", "alignment confidence"),
                        ("verified", "quote verified")):
        g.add((os_[prop], RDF.type, OWL.AnnotationProperty))
        g.add((os_[prop], RDFS.label, Literal(label, lang="en")))

    for ev in model.evidence.values():
        e = os_[ev.local]
        g.add((e, RDF.type, PROV.Entity))
        g.add((e, DCTERMS.source, os_[ev.document]))
        g.add((e, os_.quote, _text(ev.quote)))
        g.add((e, os_.verified, Literal(ev.verified)))
        if ev.page is not None:
            g.add((e, os_.page, Literal(int(ev.page))))
    for d in model.documents.values():
        u = os_[d.local]
        g.add((u, RDF.type, PROV.Entity))
        g.add((u, DCTERMS.title, _text(d.title)))
        g.add((u, DCTERMS.identifier, Literal(d.sha256)))
        g.add((u, os_.path, Literal(d.path)))
        g.add((u, os_.source, Literal("document")))

    for c in model.classes.values():
        u = os_[c.local]
        g.add((u, RDF.type, OWL.Class))
        for t in c.extra_types:
            g.add((u, RDF.type, os_[t]))
        g.add((u, RDFS.label, _text(c.label)))
        g.add((u, SKOS.prefLabel, _text(c.label)))
        for alt in c.alt_labels:
            g.add((u, SKOS.altLabel, _text(alt)))
        if c.comment:
            g.add((u, RDFS.comment, _text(c.comment)))
        for definition in c.definitions:
            g.add((u, SKOS.definition, _text(definition)))
        g.add((u, os_.source, Literal(c.source)))
        for table in c.tables:
            g.add((u, DCTERMS.source, Literal(table)))
        for ev in c.derived_from:
            g.add((u, PROV.wasDerivedFrom, os_[ev]))

    for i in model.individuals.values():
        u = os_[i.local]
        g.add((u, RDF.type, OWL.NamedIndividual))
        g.add((u, RDF.type, node(i.type)))
        g.add((u, RDFS.label, _text(i.label)))
        g.add((u, SKOS.prefLabel, _text(i.label)))
        for alt in i.alt_labels:
            g.add((u, SKOS.altLabel, _text(alt)))
        if i.comment:
            g.add((u, RDFS.comment, _text(i.comment)))
        g.add((u, os_.source, Literal("document")))
        for ev in i.derived_from:
            g.add((u, PROV.wasDerivedFrom, os_[ev]))

    for p in model.properties.values():
        u = os_[p.local]
        g.add((u, RDF.type, OWL.ObjectProperty if p.kind == "object"
               else OWL.DatatypeProperty))
        g.add((u, RDFS.label, _text(p.label)))
        g.add((u, RDFS.domain, node(p.domain)))
        g.add((u, RDFS.range, node(p.range)))
        g.add((u, os_.source, Literal(p.source)))
        if p.comment:
            g.add((u, RDFS.comment, _text(p.comment)))
        if p.source_column:
            g.add((u, os_.sourceColumn, Literal(p.source_column)))
        if p.datatype:
            g.add((u, os_.datatype, Literal(p.datatype)))
        if p.sub_property_of:
            g.add((u, RDFS.subPropertyOf, os_[p.sub_property_of]))
        if p.value_concept:
            g.add((u, os_.valueConcept, os_[p.value_concept]))
        for ev in p.derived_from:
            g.add((u, PROV.wasDerivedFrom, os_[ev]))

    for a in model.axioms:
        s, o = node(a.subject), node(a.object)
        predicate = RDFS.subClassOf if a.kind == "subclass_of" else SKOS.related
        g.add((s, predicate, o))
        # reified so the axiom carries its provenance and confidence
        st = BNode()
        g.add((st, RDF.type, RDF.Statement))
        g.add((st, RDF.subject, s))
        g.add((st, RDF.predicate, predicate))
        g.add((st, RDF.object, o))
        g.add((st, os_.source, Literal(a.source)))
        if a.confidence is not None:
            g.add((st, os_.alignmentConfidence, Literal(round(float(a.confidence), 3))))
        if a.rationale:
            g.add((st, RDFS.comment, _text(a.rationale)))
        for ev in a.derived_from:
            g.add((st, PROV.wasDerivedFrom, os_[ev]))
    return g


def write(model: MergedOntology, out_dir: Path) -> dict[str, Path]:
    g = to_graph(model)
    ttl = out_dir / "ontology.ttl"
    jsonld = out_dir / "ontology.jsonld"
    g.serialize(destination=str(ttl), format="turtle", encoding="utf-8")
    context = {k: str(v) for k, v in g.namespaces() if k in
               ("owl", "rdfs", "rdf", "skos", "prov", "dcterms", "xsd", "os")}
    jsonld.write_text(g.serialize(format="json-ld", context=context, indent=2,
                                  ensure_ascii=False), encoding="utf-8")
    return {"ttl": ttl, "jsonld": jsonld}


def roundtrip(ttl: Path, model: MergedOntology | None = None, *,
              expected_classes: int | None = None) -> dict[str, object]:
    """Re-parse the Turtle file and check the invariants the export promises:

    * owl:Class count = *expected_classes* (computed by the caller from the inputs,
      :func:`autoontology.export.merged.expected_class_count`, not from the model);
    * every owl:ObjectProperty / owl:DatatypeProperty has an rdfs:domain and rdfs:range;
    * every document concept (own class, individual, or folded into a Term) has at least
      one ``prov:wasDerivedFrom`` -- except concepts that have no evidence row at all, which
      are reported (``concepts_without_evidence``) but do not fail the check."""
    g = Graph()
    g.parse(str(ttl), format="turtle")
    classes = set(g.subjects(RDF.type, OWL.Class))
    props = set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(
        g.subjects(RDF.type, OWL.DatatypeProperty))
    no_domain_range = sorted(str(p) for p in props
                             if (p, RDFS.domain, None) not in g or (p, RDFS.range, None) not in g)
    result: dict[str, object] = {
        "triples": len(g), "classes": len(classes), "properties": len(props),
        "properties_without_domain_or_range": no_domain_range,
        "individuals": len(set(g.subjects(RDF.type, OWL.NamedIndividual))),
        "expected_classes": expected_classes,
    }
    ok = not no_domain_range and (expected_classes is None
                                  or len(classes) == expected_classes)
    if model is not None:
        os_ = Namespace(model.base)
        nodes: dict[str, list[str]] = {}  # node IRI -> its evidence in the model
        for c in model.classes.values():
            if c.source == "document":
                nodes[str(os_[c.local])] = c.derived_from
        for i in model.individuals.values():
            nodes[str(os_[i.local])] = i.derived_from
        for m in model.merges:
            nodes.setdefault(str(os_[m.term_local]), [])
            nodes[str(os_[m.term_local])] = nodes[str(os_[m.term_local])] + m.derived_from
        missing = sorted(n for n in nodes if (URIRef(n), PROV.wasDerivedFrom, None) not in g)
        no_evidence = [n for n in missing if not nodes[n]]
        unexpected = [n for n in missing if nodes[n]]
        result.update(concepts_checked=len(nodes), concepts_without_provenance=unexpected,
                      concepts_without_evidence=no_evidence)
        ok = ok and not unexpected
    result["ok"] = ok
    return result
