"""Apache Ossie bundle of an ontology snapshot: ``exports/r<rev>/ossie/``.

Three documents of Ossie spec ``0.2.0.dev0`` plus a validation record:

* ``semantic_model.yaml`` -- the ``datasets`` (and ``relationships``, ``metrics``,
  ``custom_extensions``) of every structured source's upstream Ossie export
  (``upstream/<source>.ossie.zip``, fetched at export time) merged into one document named
  after the workspace. A dataset (relationship, metric) name held by more than one source is
  prefixed with ``<source>_`` in every source that holds it; references to a renamed
  dataset (``from``/``to``, ``<dataset>.`` in model metric expressions) follow.
* ``ontology.yaml`` -- the unified ontology (:class:`~autoontology.ontology.model.MergedOntology`):
  Terms and document concepts as ``EntityType`` concepts (``iri`` = the node's IRI in
  ``ontology.ttl``), column attributes and attached document attributes/metrics as
  relationships to a built-in value type, FKs and document relations as relationships
  between concepts, ``is_a`` and the alignment ``broader``/``narrower`` axioms as
  ``extends``, ``related`` alignments as ``related_to``. Folded (``same``) concepts are part
  of their Term already (alt labels -> "Also known as").
* ``mapping.yaml`` -- a standalone mapping document: each Term whose dataset declares a key
  gets ``object_mappings`` (a compound key through ``referent_mappings``) and, where both
  ends are mapped, ``link_mappings`` for its FK relationships.
* ``validation.json`` -- the result of validating the three documents against the vendored
  JSON Schemas (``schemas/ossie/``, unmodified copies of apache/ossie, see
  ``third_party/ossie/README.md``) plus the structural checks the schemas cannot express
  (unique names, declared role concepts, verbalization placeholders) and the round-trip of
  ``semantic_model.yaml`` through the upstream converter. Any error fails the export, with
  the JSON-pointer path of the offending value in the message.

Concept names are the node labels in PascalCase (``계약 상태`` -> ``계약상태``). The schema
puts no pattern on names and the spec allows any identifier, so Korean names stay Korean;
relationship names are snake_case. Everything is deterministic: two exports of the same
snapshot and the same upstream files are byte-identical.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from autoontology import __version__
from autoontology.ontology.model import MergedOntology, OClass

SPEC_VERSION = "0.2.0.dev0"
OSSIE_DIR = "ossie"
SEMANTIC_MODEL = "semantic_model.yaml"
ONTOLOGY = "ontology.yaml"
MAPPING = "mapping.yaml"
VALIDATION = "validation.json"
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "ossie"
#: document kind -> vendored schema file
SCHEMAS = {
    "semantic_model": "ossie-schema.json",
    "ontology": "ontology.json",
    "mapping": "mapping.json",
}
#: the URIs the vendored schemas' ``$ref`` use for each other besides their ``$id``
SCHEMA_ALIASES = {
    "ossie-schema.json": "https://raw.githubusercontent.com/apache/ossie/main/core-spec/ossie-schema.json",
    "ontology.json": "https://raw.githubusercontent.com/apache/ossie/main/ontology/ontology.json",
    "mapping.json": "https://raw.githubusercontent.com/apache/ossie/main/ontology/mapping.json",
}
#: concepts every ontology has (spec "Built-in concepts")
BUILTIN_CONCEPTS = frozenset(
    {"Any", "Boolean", "Date", "DateTime", "Decimal", "Float", "Integer", "String"}
)
#: model range (``xsd:<type>``) -> built-in value type; anything else is a String
VALUE_TYPES = {
    "xsd:string": "String",
    "xsd:integer": "Integer",
    "xsd:decimal": "Decimal",
    "xsd:double": "Float",
    "xsd:boolean": "Boolean",
    "xsd:date": "Date",
    "xsd:dateTime": "DateTime",
}
#: document predicates that attach an attribute/metric concept to its subject
ATTACHING = ("has_attribute", "measured_by")
SKOS_RELATED = "http://www.w3.org/2004/02/skos/core#related"
NVIDIA_VENDOR = "NVIDIA_AUTO_ONTOLOGY"
MERGEABLE_KEYS = ("relationships", "metrics", "custom_extensions")
_PLACEHOLDER = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")


class OssieError(RuntimeError):
    """The bundle does not validate; the message names every failing JSON pointer."""


# --- schema validation ----------------------------------------------------------------------


@cache
def _registry() -> Any:
    from referencing import Registry, Resource

    resources = []
    for filename, alias in SCHEMA_ALIASES.items():
        contents = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        resources += [(contents["$id"], resource), (alias, resource)]
    return Registry().with_resources(resources)


@cache
def _validator(kind: str) -> Any:
    from jsonschema import Draft202012Validator

    schema = json.loads((SCHEMA_DIR / SCHEMAS[kind]).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry())


def pointer(path: Any) -> str:
    """The JSON pointer (RFC 6901) of a jsonschema error path."""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts) if parts else "/"


def schema_errors(kind: str, doc: Any) -> list[str]:
    """``"<json pointer>: <message>"`` for every violation of the *kind* schema."""
    errors = sorted(
        _validator(kind).iter_errors(doc),
        # array indexes in numeric order (/2 before /10), then keys
        key=lambda e: [(0, p, "") if isinstance(p, int) else (1, 0, str(p)) for p in e.path],
    )
    return [f"{pointer(e.absolute_path)}: {e.message}" for e in errors]


# --- names ----------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\W_]+", str(text or "")) if w]


def concept_name(label: str, fallback: str = "Concept") -> str:
    """PascalCase identifier of *label*; Hangul and other letters are kept."""
    name = "".join(w[:1].upper() + w[1:] for w in _words(label))
    if name and not name[0].isalpha():
        name = f"C{name}"
    return name if name.isidentifier() else fallback


def relationship_name(text: str, fallback: str = "rel") -> str:
    """snake_case identifier of *text*."""
    name = "_".join(w.lower() for w in _words(text))
    if name and not name[0].isalpha():
        name = f"r_{name}"
    return name if name.isidentifier() else fallback


def _unique(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if not text or text[-1] in ".!?。" else f"{text}."


def describe(parts: list[str], synonyms: list[str]) -> str:
    """The description of a concept: its texts, then ``Also known as: a, b.``."""
    out = [_sentence(p) for p in dict.fromkeys(p for p in parts if p and p.strip())]
    if synonyms:
        out.append(f"Also known as: {', '.join(synonyms)}.")
    return " ".join(out)


# --- semantic model -------------------------------------------------------------------------


@dataclass
class Dataset:
    """A dataset of the merged semantic model and where it came from."""

    source: str  # structured source
    name: str  # name in the merged document
    table: str  # schema.table of its ``source``
    keys: list[list[str]]  # primary_key first, then unique_keys
    columns: dict[str, str]  # field name -> column (its plain ANSI expression)


def read_upstream(path: Path) -> dict[str, Any]:
    """``semantic_model.yaml`` of an upstream Ossie zip."""
    try:
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
            text = zf.read(SEMANTIC_MODEL).decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise OssieError(f"{path.name}: no readable {SEMANTIC_MODEL} ({exc})") from exc
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise OssieError(f"{path.name}: {SEMANTIC_MODEL} is not a mapping")
    return doc


def _table_of(source: str) -> str:
    parts = str(source or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else str(source or "")


def _field_column(item: dict[str, Any]) -> str:
    for d in (item.get("expression") or {}).get("dialects") or []:
        expr = str(d.get("expression") or "").strip().strip('"')
        if expr.isidentifier():
            return expr
    return str(item.get("name") or "")


def _prefix(source: str) -> str:
    return re.sub(r"\W", "_", source)


def merge_semantic_models(
    name: str, upstream: list[tuple[str, dict[str, Any]]], warnings: list[str]
) -> tuple[dict[str, Any], list[Dataset]]:
    """One semantic model named *name* from ``(source, semantic_model)`` pairs."""
    held: dict[str, dict[str, set[str]]] = {k: {} for k in ("datasets", *MERGEABLE_KEYS[:2])}
    for source, doc in upstream:
        for key in held:
            for item in doc.get(key) or []:
                if isinstance(item, dict) and item.get("name"):
                    held[key].setdefault(str(item["name"]), set()).add(source)

    def renamed(key: str, source: str, original: str, taken: set[str]) -> str:
        base = original
        if len(held[key].get(original, ())) > 1:
            base = f"{_prefix(source)}_{original}"
        return _unique(base, taken)

    merged: dict[str, Any] = {
        "version": SPEC_VERSION,
        "name": name,
        "description": (
            f"Datasets of the structured sources of workspace {name} "
            f"({', '.join(s for s, _ in upstream)}), merged by AutoOntology"
        ),
        "datasets": [],
    }
    lists: dict[str, list[Any]] = {k: [] for k in MERGEABLE_KEYS}
    taken: dict[str, set[str]] = {k: set() for k in held}
    datasets: list[Dataset] = []
    for source, doc in upstream:
        names: dict[str, str] = {}
        for item in doc.get("datasets") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            new = renamed("datasets", source, str(item["name"]), taken["datasets"])
            names[str(item["name"])] = new
            if new != item["name"]:
                warnings.append(f"semantic model: dataset {source}.{item['name']} renamed {new}")
            merged["datasets"].append({**item, "name": new})
            keys = [list(item["primary_key"])] if item.get("primary_key") else []
            keys += [list(k) for k in item.get("unique_keys") or [] if list(k) not in keys]
            datasets.append(
                Dataset(
                    source=source,
                    name=new,
                    table=_table_of(item.get("source", "")),
                    keys=keys,
                    columns={
                        str(f.get("name")): _field_column(f)
                        for f in item.get("fields") or []
                        if isinstance(f, dict) and f.get("name")
                    },
                )
            )
        for rel in doc.get("relationships") or []:
            new = renamed("relationships", source, str(rel.get("name")), taken["relationships"])
            lists["relationships"].append(
                {
                    **rel,
                    "name": new,
                    "from": names.get(rel.get("from"), rel.get("from")),
                    "to": names.get(rel.get("to"), rel.get("to")),
                }
            )
        for metric in doc.get("metrics") or []:
            new = renamed("metrics", source, str(metric.get("name")), taken["metrics"])
            lists["metrics"].append({**_rewrite_refs(metric, names), "name": new})
        extensions = doc.get("custom_extensions") or []
        if len(upstream) > 1:
            dropped = [e for e in extensions if e.get("vendor_name") == NVIDIA_VENDOR]
            if dropped:
                warnings.append(
                    f"semantic model: dropped {source}'s model-level {NVIDIA_VENDOR} "
                    "extension (it describes that source's model alone)"
                )
            extensions = [e for e in extensions if e.get("vendor_name") != NVIDIA_VENDOR]
        lists["custom_extensions"] += extensions
    for key in MERGEABLE_KEYS:
        if lists[key]:
            merged[key] = lists[key]
    return merged, datasets


def _rewrite_refs(metric: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """*metric* with ``<old dataset>.`` references in its expressions renamed."""
    moved = {old: new for old, new in names.items() if old != new}
    if not moved:
        return metric

    def fix(text: str) -> str:
        for old, new in moved.items():
            text = re.sub(rf"(?<![\w.\"]){re.escape(old)}\.", f"{new}.", text)
        return text

    out = json.loads(json.dumps(metric))
    for d in (out.get("expression") or {}).get("dialects") or []:
        if isinstance(d.get("expression"), str):
            d["expression"] = fix(d["expression"])
    return out


# --- ontology -------------------------------------------------------------------------------


@dataclass
class _Rel:
    base: str
    roles: list[dict[str, str]]
    verbalize: str  # with {S} for the subject, {Role} placeholders already resolved
    multiplicity: str | None = None
    description: str = ""
    iri: str = ""
    value: bool = False  # role is a built-in value type
    name: str = ""


def _verbal(text: str) -> str:
    """Free text inside a verbalization: braces would read as role placeholders."""
    return text.replace("{", "(").replace("}", ")")


@dataclass
class _FK:
    subject: str  # node local
    target: str
    rel: _Rel
    table: str  # schema.table of the referencing column ("" when from a dataset)
    column: str = ""
    #: declared: the referenced dataset and fields ("" / []: a semantic FK, which
    #: references the Term's key attribute); declared before ``field``, which shadows
    #: :func:`dataclasses.field` in the class body
    to_dataset: str = ""
    to_fields: list[str] = field(default_factory=list)
    dataset: str = ""  # declared: the referencing dataset and its field
    field: str = ""


@dataclass
class OntologyResult:
    document: dict[str, Any]
    names: dict[str, str]  # node local -> concept name
    #: column attribute property local -> (node local, relationship)
    attributes: dict[str, tuple[str, _Rel]] = field(default_factory=dict)
    #: column attribute property local -> its schema.table.column
    columns: dict[str, str] = field(default_factory=dict)
    fks: list[_FK] = field(default_factory=list)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)


def _kind_of(model: MergedOntology, local: str) -> str:
    if local in model.classes:
        return model.classes[local].kind
    if local in model.individuals:
        return model.individuals[local].kind
    return ""


def _label(model: MergedOntology, local: str) -> str:
    if local in model.classes:
        return model.classes[local].label
    if local in model.individuals:
        return model.individuals[local].label
    return local


def _term_of_table(model: MergedOntology, source: str, table: str) -> list[str]:
    return sorted(
        c.local
        for c in model.classes.values()
        if c.source == "structured" and c.structured_source == source and table in c.tables
    )


def build_ontology(
    model: MergedOntology,
    *,
    revision: int,
    created_at: str,
    datasets: list[Dataset],
    semantic_relationships: list[dict[str, Any]],
    warnings: list[str],
) -> OntologyResult:
    """The unified ``ontology.yaml`` document of *model*."""
    doc_props = sorted(
        (p for p in model.properties.values() if p.source == "document"), key=lambda p: p.local
    )

    def attached_value(p: Any) -> bool:
        target = model.classes.get(p.object_node)
        return (
            p.predicate in ATTACHING
            and target is not None
            and target.kind in ("attribute", "metric")
        )

    attaching = [p for p in doc_props if attached_value(p)]
    others = [p for p in doc_props if not attached_value(p)]
    referenced = {p.subject_node for p in doc_props}
    referenced |= {p.object_node for p in others}
    referenced |= {end for a in model.axioms for end in (a.subject, a.object)}
    attached_only = {p.object_node for p in attaching} - referenced

    terms = sorted(
        (c for c in model.classes.values() if c.source == "structured"),
        key=lambda c: (c.label, c.local),
    )
    doc_classes = sorted(
        (
            c
            for c in model.classes.values()
            if c.source != "structured" and c.local not in attached_only
        ),
        key=lambda c: (c.label, c.local),
    )
    individuals = sorted(model.individuals.values(), key=lambda i: (i.label, i.local))
    taken = set(BUILTIN_CONCEPTS)
    names: dict[str, str] = {}
    components: dict[str, dict[str, Any]] = {}
    for node in [*terms, *doc_classes, *individuals]:
        names[node.local] = _unique(concept_name(node.label), taken)
        if isinstance(node, OClass):
            parts = (
                [node.comment, *node.definitions]
                if node.source == "structured"
                else list(node.definitions)
            )
        else:
            parts = [node.comment]
        description = describe(parts, node.alt_labels)
        if not description:
            origin = "Term" if getattr(node, "source", "") == "structured" else "document concept"
            description = f"{node.label} ({origin}, kind {_kind_of(model, node.local) or 'term'})."
        components[node.local] = {
            "concept": names[node.local],
            "type": "EntityType",
            "description": description,
            "iri": f"ao:{node.local}",
        }
    for local in sorted(attached_only):
        warnings.append(
            f"ontology: {_label(model, local)} is modelled only as a value relationship "
            "of the concept(s) it is attached to"
        )

    rels: dict[str, list[_Rel]] = {local: [] for local in components}
    extends: dict[str, list[str]] = {local: [] for local in components}
    result = OntologyResult(document={}, names=names, components=components)

    # column attributes and FKs of the Terms
    for p in sorted(model.properties.values(), key=lambda p: (p.domain, p.label, p.local)):
        if p.domain not in components:
            continue
        if p.source == "structured":
            vtype = VALUE_TYPES.get(p.range, "String")
            rel = _Rel(
                base=relationship_name(p.label),
                roles=[{"concept": vtype}],
                verbalize=f"{{S}} has {_verbal(p.label)} {{{vtype}}}",
                multiplicity="ManyToOne",
                description=p.comment,
                iri=f"ao:{p.local}",
                value=True,
            )
            rels[p.domain].append(rel)
            result.attributes[p.local] = (p.domain, rel)
            result.columns[p.local] = p.source_column
        elif p.source == "semantic_fk" and p.range in components:
            fk = _fk_rel(names, p.domain, p.range, f"ao:{p.local}", p.label)
            rels[p.domain].append(fk)
            table, _, column = p.source_column.rpartition(".")
            result.fks.append(_FK(p.domain, p.range, fk, table, column))

    # declared FKs of the semantic model (between datasets that back Terms)
    by_name = {d.name: d for d in datasets}
    for r in semantic_relationships:
        src, dst = by_name.get(r.get("from")), by_name.get(r.get("to"))
        if src is None or dst is None:
            continue
        for a in _term_of_table(model, src.source, src.table):
            for b in _term_of_table(model, dst.source, dst.table):
                cols = list(r.get("from_columns") or [])
                columns = [src.columns.get(c, c) for c in cols]
                if any(
                    f.subject == a and f.target == b and f.column in columns for f in result.fks
                ):
                    continue  # the semantic FK of that column says it already
                fk = _fk_rel(
                    names,
                    a,
                    b,
                    "",
                    f"declared relationship {r.get('name')} ({', '.join(cols)})",
                )
                rels[a].append(fk)
                result.fks.append(
                    _FK(
                        a,
                        b,
                        fk,
                        "",
                        dataset=src.name,
                        field=cols[0] if len(cols) == 1 else "",
                        to_dataset=dst.name,
                        to_fields=list(r.get("to_columns") or []),
                    )
                )

    # document attributes/metrics attached to a subject -> value relationships
    for p in attaching:
        if p.subject_node not in components:
            continue
        target = model.classes[p.object_node]
        vtype = "Decimal" if target.kind == "metric" else "String"
        base = relationship_name(target.label)
        if any(r.value and r.base == base for r in rels[p.subject_node]):
            continue  # the Term's column says it already
        rels[p.subject_node].append(
            _Rel(
                base=base,
                roles=[{"concept": vtype}],
                verbalize=f"{{S}} has {_verbal(target.label)} {{{vtype}}}",
                multiplicity="ManyToOne",
                description=describe(list(target.definitions), target.alt_labels),
                iri=f"ao:{p.local}",
                value=True,
            )
        )

    # document relations
    for p in others:
        s, o = p.subject_node, p.object_node
        if s not in components or o not in components:
            warnings.append(f"ontology: relation {p.label!r} has an end that is not a concept")
            continue
        if p.predicate == "is_a":
            _extend(extends, s, o, names, warnings)
            continue
        rels[s].append(
            _Rel(
                base=relationship_name(p.predicate),
                roles=[{"concept": names[o]}],
                verbalize=f"{{S}} {_verbal(p.predicate.replace('_', ' '))} {{{names[o]}}}",
                # the model's fallback label is "<subject> <predicate> <object>": no news
                description="" if f" {p.predicate} " in f" {p.label} " else p.label,
                iri=f"ao:{p.local}",
            )
        )

    # alignment and is_a axioms
    for a in model.axioms:
        if a.subject not in components or a.object not in components:
            continue
        if a.kind == "subclass_of":
            _extend(extends, a.subject, a.object, names, warnings)
        elif a.kind == "related":
            rels[a.subject].append(
                _Rel(
                    base="related_to",
                    roles=[{"concept": names[a.object]}],
                    verbalize=f"{{S}} is related to {{{names[a.object]}}}",
                    description=a.rationale,
                    iri=SKOS_RELATED,
                )
            )

    ontology: list[dict[str, Any]] = []
    for local, comp in components.items():
        if extends[local]:
            comp["extends"] = extends[local]
        relationships = _named(rels[local], comp["concept"])
        if relationships:
            comp["relationships"] = relationships
        ontology.append(comp)
    counts = {
        "concepts": len(ontology),
        "relationships": sum(len(c.get("relationships", [])) for c in ontology),
        "evidence": len(model.evidence),
    }
    result.document = {
        "version": SPEC_VERSION,
        "name": f"{model.name}_ontology",
        "description": (
            f"Unified ontology of workspace {model.name}: structured Terms and document "
            "concepts joined by their alignment (generated by AutoOntology)"
        ),
        "ai_context": {
            "generator": f"autoontology {__version__}",
            "revision": revision,
            "created_at": created_at,
            "sources": [*model.structured_sources, *model.documents_sources],
            "counts": counts,
        },
        "prefixes": {"ao": model.base},
        "ontology": ontology,
    }
    return result


def _fk_rel(names: dict[str, str], a: str, b: str, iri: str, description: str) -> _Rel:
    if a == b:
        roles = [{"concept": names[b], "name": "target"}]
        verbalize = f"{{S}} refers to {{{names[b]}:target}}"
    else:
        roles = [{"concept": names[b]}]
        verbalize = f"{{S}} refers to {{{names[b]}}}"
    return _Rel(
        base=relationship_name(names[b]),
        roles=roles,
        verbalize=verbalize,
        multiplicity="ManyToOne",
        description=description,
        iri=iri,
    )


def _extend(
    extends: dict[str, list[str]], s: str, o: str, names: dict[str, str], warnings: list[str]
) -> None:
    if s == o or names[o] in extends[s]:
        return
    # a cycle would make s its own supertype: drop the edge that closes it
    by_name = {v: k for k, v in names.items()}
    seen, stack = set(), [o]
    while stack:
        node = stack.pop()
        if node == s:
            warnings.append(f"ontology: {names[s]} extends {names[o]} dropped (cycle)")
            return
        if node not in seen:
            seen.add(node)
            stack += [by_name[n] for n in extends.get(node, [])]
    extends[s].append(names[o])


def _named(rels: list[_Rel], subject: str) -> list[dict[str, Any]]:
    """Relationship documents with names unique within the concept."""
    counts: dict[str, int] = {}
    for r in rels:
        counts[r.base] = counts.get(r.base, 0) + 1
    taken: set[str] = set()
    out = []
    for r in rels:
        base = r.base
        if counts[base] > 1 and not r.value:
            base = f"{base}_{relationship_name(r.roles[0]['concept'])}"
        r.name = _unique(base, taken)
        item: dict[str, Any] = {"name": r.name}
        if r.description:
            item["description"] = r.description
        item["roles"] = r.roles
        if r.multiplicity:
            item["multiplicity"] = r.multiplicity
        item["verbalizes"] = [r.verbalize.replace("{S}", f"{{{subject}}}")]
        if r.iri:
            item["iri"] = r.iri
        out.append(item)
    return out


# --- mapping --------------------------------------------------------------------------------


def build_mapping(
    model: MergedOntology,
    onto: OntologyResult,
    datasets: list[Dataset],
    semantic_model_name: str,
    warnings: list[str],
) -> dict[str, Any]:
    """The standalone ``mapping.yaml`` from the Terms' datasets to the ontology."""
    simple: dict[str, tuple[str, str]] = {}  # term local -> (dataset, key field)
    mappings: dict[str, dict[str, Any]] = {}
    for cls in sorted(
        (c for c in model.classes.values() if c.source == "structured"), key=lambda c: c.label
    ):
        name = onto.names[cls.local]
        comp = onto.components[cls.local]
        object_mappings: list[dict[str, Any]] = []
        for ds in datasets:
            if ds.source != cls.structured_source or ds.table not in cls.tables:
                continue
            if not ds.keys:
                warnings.append(f"mapping: {name}: dataset {ds.name} declares no key, not mapped")
                continue
            key = next((k for k in ds.keys if len(k) == 1), ds.keys[0])
            rels = [_key_relationship(onto, cls.local, ds, f) for f in key]
            if len(key) == 1:
                object_mappings.append({"expression": f"{ds.name}.{key[0]}"})
                simple.setdefault(cls.local, (ds.name, key[0]))
                if rels[0] is None:
                    warnings.append(
                        f"mapping: {name}: key field {ds.name}.{key[0]} has no attribute "
                        "relationship, so no identify_by"
                    )
            elif all(rels):
                object_mappings.append(
                    {
                        "referent_mappings": [
                            {"relationship": r, "expression": f"{ds.name}.{f}"}
                            for r, f in zip(rels, key, strict=True)
                        ]
                    }
                )
            else:
                warnings.append(
                    f"mapping: {name}: compound key {ds.name}({', '.join(key)}) has a field "
                    "without an attribute relationship, not mapped"
                )
                continue
            if "identify_by" not in comp and all(rels):
                comp["identify_by"] = list(rels)
                if len(rels) == 1:  # a single-field key names one instance: one-to-one
                    for item in comp.get("relationships", []):
                        if item["name"] == rels[0]:
                            item["multiplicity"] = "OneToOne"
        if object_mappings:
            mappings[cls.local] = {"concept": name, "object_mappings": object_mappings}

    for fk in onto.fks:
        a, b = onto.names[fk.subject], onto.names[fk.target]
        if fk.subject not in simple or fk.target not in simple:
            warnings.append(
                f"mapping: {a}.{fk.rel.name} -> {b}: both ends need a single-field key, "
                "no link mapping"
            )
            continue
        ds_name, key = simple[fk.subject]
        target_ds, target_key = simple[fk.target]
        if fk.to_dataset and (fk.to_dataset, fk.to_fields) != (target_ds, [target_key]):
            warnings.append(
                f"mapping: {a}.{fk.rel.name} -> {b}: references "
                f"{fk.to_dataset}({', '.join(fk.to_fields)}), not {b}'s key "
                f"{target_ds}.{target_key}, no link mapping"
            )
            continue
        field_name = fk.field if fk.dataset == ds_name else ""
        if not field_name and fk.column:
            ds = next(d for d in datasets if d.name == ds_name)
            if ds.table == fk.table:
                field_name = next((f for f, c in ds.columns.items() if c == fk.column), "")
        if not field_name:
            warnings.append(
                f"mapping: {a}.{fk.rel.name} -> {b}: no field of {ds_name} holds the "
                "referencing column, no link mapping"
            )
            continue
        entry = mappings[fk.subject].setdefault("link_mappings", [])
        if not entry:
            entry.append({"object_mapping": {"expression": f"{ds_name}.{key}"}, "children": []})
        entry[0]["children"].append(
            {
                "relationship": fk.rel.name,
                "object_mapping": {"concept": b, "expression": f"{ds_name}.{field_name}"},
            }
        )
    return {
        "version": SPEC_VERSION,
        "name": f"{model.name}_mapping",
        "description": (f"Maps the datasets of {semantic_model_name} onto {onto.document['name']}"),
        "ontology_ref": {"name": onto.document["name"], "iri": f"./{ONTOLOGY}"},
        "semantic_model_ref": {"name": semantic_model_name, "iri": f"./{SEMANTIC_MODEL}"},
        "concept_mappings": [mappings[k] for k in sorted(mappings, key=lambda k: onto.names[k])],
    }


def _key_relationship(onto: OntologyResult, term: str, ds: Dataset, field_name: str) -> str | None:
    """The attribute relationship of *term* over the column behind *field_name*."""
    column = f"{ds.table}.{ds.columns.get(field_name, field_name)}"
    for local, (owner, rel) in onto.attributes.items():
        if owner == term and onto.columns.get(local) == column:
            return rel.name
    return None


# --- structural checks ----------------------------------------------------------------------


def ontology_errors(doc: dict[str, Any]) -> list[str]:
    """What the schema cannot check: unique concept/relationship names, declared role
    concepts and ``extends`` targets, and verbalization placeholders."""
    errors: list[str] = []
    comps = doc.get("ontology") or []
    declared = [c.get("concept") for c in comps]
    known = set(declared) | BUILTIN_CONCEPTS
    seen: set[str] = set()
    for i, comp in enumerate(comps):
        name = comp.get("concept")
        here = f"/ontology/{i}"
        if not isinstance(name, str) or not name.isidentifier():
            errors.append(f"{here}/concept: {name!r} is not an identifier")
        if name in seen or name in BUILTIN_CONCEPTS:
            errors.append(f"{here}/concept: {name!r} is declared twice or is built in")
        seen.add(name)
        for j, ext in enumerate(comp.get("extends") or []):
            if ext not in known:
                errors.append(f"{here}/extends/{j}: unknown concept {ext!r}")
        rel_names: set[str] = set()
        for j, rel in enumerate(comp.get("relationships") or []):
            at = f"{here}/relationships/{j}"
            rname = rel.get("name")
            if not isinstance(rname, str) or not rname.isidentifier():
                errors.append(f"{at}/name: {rname!r} is not an identifier")
            if rname in rel_names:
                errors.append(f"{at}/name: {rname!r} is not unique in {name}")
            rel_names.add(rname)
            roles = {name}
            for k, role in enumerate(rel.get("roles") or []):
                if role.get("concept") not in known:
                    errors.append(
                        f"{at}/roles/{k}/concept: unknown concept {role.get('concept')!r}"
                    )
                roles.add(role.get("name") or role.get("concept"))
            for k, text in enumerate(rel.get("verbalizes") or []):
                for m in _PLACEHOLDER.finditer(text):
                    ref = m.group(2) or m.group(1)
                    if ref not in roles:
                        errors.append(f"{at}/verbalizes/{k}: placeholder {m.group(0)} is no role")
    return errors


def mapping_errors(mapping: dict[str, Any], ontology: dict[str, Any]) -> list[str]:
    """Concept mappings name declared concepts and relationships."""
    comps = {c["concept"]: c for c in ontology.get("ontology") or []}
    errors = []
    for i, cm in enumerate(mapping.get("concept_mappings") or []):
        comp = comps.get(cm.get("concept"))
        if comp is None:
            errors.append(f"/concept_mappings/{i}/concept: unknown concept {cm.get('concept')!r}")
            continue
        rels = {r["name"] for r in comp.get("relationships") or []}
        for j, om in enumerate(cm.get("object_mappings") or []):
            for k, ref in enumerate(om.get("referent_mappings") or []):
                if ref.get("relationship") not in rels:
                    errors.append(
                        f"/concept_mappings/{i}/object_mappings/{j}/referent_mappings/{k}"
                        f"/relationship: unknown relationship {ref.get('relationship')!r}"
                    )
        for j, lm in enumerate(cm.get("link_mappings") or []):
            for k, child in enumerate(lm.get("children") or []):
                if child.get("relationship") not in rels:
                    errors.append(
                        f"/concept_mappings/{i}/link_mappings/{j}/children/{k}/relationship: "
                        f"unknown relationship {child.get('relationship')!r}"
                    )
    return errors


def roundtrip(semantic_model_yaml: str) -> str:
    """``ok``, or why the upstream converter cannot read *semantic_model_yaml*."""
    try:
        from ossie_nvidia_auto_ontology import convert_ossie_to_auto_ontology
    except ImportError:  # pragma: no cover - the engine is a hard dependency
        return "skipped: ossie_nvidia_auto_ontology is not installed"
    try:
        out = convert_ossie_to_auto_ontology(semantic_model_yaml)
        if not isinstance(yaml.safe_load(out), dict):
            return "error: the converter returned no document"
    except Exception as exc:  # noqa: BLE001 - any converter failure is the finding
        return f"error: {type(exc).__name__}: {exc}"
    return "ok"


# --- writer ---------------------------------------------------------------------------------


def dump(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1_000_000)


def write(
    model: MergedOntology,
    out_dir: Path,
    *,
    revision: int,
    created_at: str,
    upstream: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Write ``<out_dir>/ossie/`` and return the validation record; raises
    :class:`OssieError` (after writing ``validation.json``, which the export job copies to
    ``logs/`` because it removes the failed work folder) when anything fails."""
    folder = out_dir / OSSIE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    record: dict[str, Any] = {}
    sm_doc: dict[str, Any] | None = None
    datasets: list[Dataset] = []
    if upstream:
        sm_doc, datasets = merge_semantic_models(
            model.name, [(s, read_upstream(p)) for s, p in upstream], warnings
        )
    onto = build_ontology(
        model,
        revision=revision,
        created_at=created_at,
        datasets=datasets,
        semantic_relationships=(sm_doc or {}).get("relationships") or [],
        warnings=warnings,
    )
    files: list[Path] = []
    if sm_doc is not None:
        mapping = build_mapping(model, onto, datasets, sm_doc["name"], warnings)
        text = dump(sm_doc)
        (folder / SEMANTIC_MODEL).write_text(text, encoding="utf-8")
        files.append(folder / SEMANTIC_MODEL)
        record["semantic_model"] = schema_errors("semantic_model", sm_doc) or "ok"
        record["roundtrip"] = roundtrip(text)
    else:
        mapping = None
        record["semantic_model"] = "skipped: no structured source"
        record["roundtrip"] = "skipped: no semantic model"
    (folder / ONTOLOGY).write_text(dump(onto.document), encoding="utf-8")
    files.append(folder / ONTOLOGY)
    record["ontology"] = (
        schema_errors("ontology", onto.document) + ontology_errors(onto.document)
    ) or "ok"
    if mapping is not None:
        (folder / MAPPING).write_text(dump(mapping), encoding="utf-8")
        files.append(folder / MAPPING)
        record["mapping"] = (
            schema_errors("mapping", mapping) + mapping_errors(mapping, onto.document)
        ) or "ok"
    else:
        record["mapping"] = "skipped: no semantic model"
    record["warnings"] = warnings
    (folder / VALIDATION).write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    files.append(folder / VALIDATION)
    failed = [
        f"{OSSIE_DIR}/{name}{err if err.startswith('/') else ' ' + err}"
        for key, name in (
            ("semantic_model", SEMANTIC_MODEL),
            ("ontology", ONTOLOGY),
            ("mapping", MAPPING),
        )
        if isinstance(record[key], list)
        for err in record[key]
    ]
    if str(record["roundtrip"]).startswith("error"):
        failed.append(f"{OSSIE_DIR}/{SEMANTIC_MODEL} round-trip: {record['roundtrip']}")
    if failed:
        shown = "; ".join(failed[:10]) + (
            f"; ... ({len(failed)} errors)" if len(failed) > 10 else ""
        )
        raise OssieError(f"Ossie validation failed: {shown}")
    return {"files": files, "validation": record, "names": onto.names}
