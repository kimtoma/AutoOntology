# SPDX-License-Identifier: MIT
"""Source-complete knowledge views; mixed facts require every contributing source.

Do not trim a merged definition or alias and invent a new, unreviewed meaning. A
published component is indivisible unless its finer provenance was recorded.
Unknown origins remain unclassified, including when every named source is allowed.
"""

from __future__ import annotations

from autoontology.ontology.model import MergedOntology

UNKNOWN = "\x00unclassified"
BUILTINS = {"THING", "LITERAL", "BusinessRule", "Process", "Concept", "Metric"}


def project(model: MergedOntology, allowed_sources: set[str]) -> MergedOntology:
    allowed = set(allowed_sources) - {UNKNOWN, ""}
    data = model.to_dict()

    def evidence_sources(record):
        result = set()
        for key in record.get("derived_from", []):
            evidence = model.evidence.get(key)
            document = model.documents.get(evidence.document) if evidence else None
            result.add(document.source if document and document.source else UNKNOWN)
        return result

    def origins(record, *fields):
        result = evidence_sources(record)
        result.update(record[f] for f in fields if record.get(f))
        return result or {UNKNOWN}

    dependencies = {}
    for key, record in data["classes"].items():
        deps = origins(record, "structured_source", "documents_source")
        if record["source"] == "structured":
            # Upstream Terms can span databases; legacy snapshots retain only one
            # structured_source and cannot prove individual field/table ownership.
            deps.update(model.structured_sources)
        primary = "structured_source" if record["source"] == "structured" else "documents_source"
        if not record.get(primary):
            deps.add(UNKNOWN)
        for merge in data["merges"]:
            if merge["term_local"] == key:
                deps.update(origins(merge, "documents_source"))
        dependencies[key] = deps
    for key, record in data["individuals"].items():
        dependencies[key] = origins(record, "documents_source")
        if not record.get("documents_source"):
            dependencies[key].add(UNKNOWN)

    def endpoint(value):
        if (
            not value
            or value in BUILTINS
            or value.startswith(("owl:", "xsd:", "ao:", "rdf:", "rdfs:"))
        ):
            return set()
        return dependencies.get(value, {UNKNOWN})

    def visible(deps):
        return bool(deps) and deps <= allowed

    for collection in ("classes", "individuals"):
        data[collection] = {
            key: record for key, record in data[collection].items() if visible(dependencies[key])
        }
    properties = {}
    property_dependencies = {}
    for key, record in data["properties"].items():
        if record["source"] == "vocabulary":
            continue
        deps = origins(record, "owner")
        if not record.get("owner"):
            deps.add(UNKNOWN)
        for field in ("domain", "range", "subject_node", "object_node", "value_concept"):
            deps.update(endpoint(record.get(field, "")))
        property_dependencies[key] = deps
        if visible(deps):
            properties[key] = record
    for key, record in data["properties"].items():
        if record["source"] != "vocabulary":
            continue
        users = [
            p
            for p in data["properties"].values()
            if p["source"] != "vocabulary" and key in (p.get("predicate"), p.get("sub_property_of"))
        ]
        deps = (
            set().union(*(property_dependencies[p["local"]] for p in users)) if users else {UNKNOWN}
        )
        if visible(deps):
            properties[key] = record
    data["properties"] = properties
    axioms = []
    for record in data["axioms"]:
        deps = evidence_sources(record) | endpoint(record["subject"]) | endpoint(record["object"])
        if not record.get("derived_from"):
            deps.add(UNKNOWN)
        # Alignment rationales can quote candidate Terms beyond the chosen target.
        if record["source"] == "alignment":
            deps.update(model.structured_sources)
        if visible(deps):
            axioms.append(record)
    data["axioms"] = axioms
    data["merges"] = [
        record
        for record in data["merges"]
        if visible(origins(record, "documents_source") | endpoint(record["term_local"]))
    ]
    used = {
        key
        for collection in ("classes", "individuals", "properties")
        for record in data[collection].values()
        for key in record.get("derived_from", [])
    }
    used.update(
        key
        for collection in ("axioms", "merges")
        for record in data[collection]
        for key in record.get("derived_from", [])
    )
    data["documents"] = {
        key: record for key, record in data["documents"].items() if record["source"] in allowed
    }
    data["evidence"] = {
        key: record
        for key, record in data["evidence"].items()
        if key in used and record["document"] in data["documents"]
    }
    for collection in ("structured_sources", "documents_sources"):
        data[collection] = [name for name in data[collection] if name in allowed]
    for collection in (
        "fallback_terms",
        "unaligned",
        "low_confidence",
        "source_missing",
        "dry_run_same",
        "collapsed_relations",
        "without_evidence",
        "decisions",
    ):
        # Legacy diagnostics and human notes have no complete per-field provenance.
        data[collection] = []
    data["alignment_counts"] = {}
    data["expected_classes"] = None
    return MergedOntology.from_dict(data)
