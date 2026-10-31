# SPDX-License-Identifier: MIT
"""Read-only snapshot of a structured source's upstream semantic layer: its Terms (with
synonyms, REPRESENTS tables and column attributes) and SEMANTIC_FK references.

Used by the align step of ``autoontology build`` (candidate Terms) and ``autoontology export`` (owl classes and
properties). Terms are scoped to the source's catalog database through
``GET /api/exploration/semantic-graph?database_id=`` (``GET /api/terms`` has no database
filter), then each is read with ``GET /api/terms/{id}`` and its column attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoontology.engine.client import BackendClient
    from autoontology.store.db import Source
    from autoontology.unstructured.align import TermInfo


@dataclass
class SemanticFK:
    """A column of *owner* (the Term whose table holds the column) references *target*
    (the Term owning the ColumnAttribute)."""

    owner_term_id: str
    target_term_id: str
    attribute_id: str
    attribute_name: str
    column: str  # schema.table.column of the referencing column

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class StructuredSnapshot:
    source: str
    database_id: str
    database_name: str
    terms: list[TermInfo] = field(default_factory=list)
    fks: list[SemanticFK] = field(default_factory=list)
    graph_links: int = 0
    #: term id -> the structured source it was read from (a combined snapshot spans several)
    term_sources: dict[str, str] = field(default_factory=dict)

    def term_map(self) -> dict[str, TermInfo]:
        return {t.id: t for t in self.terms}

    def source_of(self, term_id: str) -> str:
        return self.term_sources.get(term_id, self.source)


def combine(snapshots: list[StructuredSnapshot]) -> StructuredSnapshot:
    """One snapshot over the Terms of every structured source (the align candidates of
    ``autoontology build``). A Term upstream links to tables of several databases is listed
    once, under the first source that has it."""
    out = StructuredSnapshot(
        source="+".join(s.source for s in snapshots),
        database_id=",".join(s.database_id for s in snapshots),
        database_name=",".join(s.database_name for s in snapshots),
    )
    for snap in snapshots:
        for term in snap.terms:
            if term.id in out.term_sources:
                continue
            out.terms.append(term)
            out.term_sources[term.id] = snap.source_of(term.id)
        out.fks += snap.fks
        out.graph_links += snap.graph_links
    return out


def database_id_for(client: BackendClient, source: Source) -> tuple[str, str]:
    from autoontology.structured import pipeline

    name = pipeline.catalog_database_name(source)
    db_id = client.database_id_by_name(name) or source.catalog_database_id
    if not db_id:
        raise LookupError(
            f"structured source {source.name!r} ({name}) is not in the upstream "
            f"catalog; run `uv run autoontology build --source {source.name}` first"
        )
    return db_id, name


def _column(ref: dict[str, Any] | None, schemas: dict[str, str]) -> dict[str, Any]:
    ref = ref or {}
    return {
        "table_id": ref.get("table_id"),
        "table": ref.get("table_name"),
        "schema": schemas.get(str(ref.get("schema_id")), ""),
        "column": ref.get("column_name"),
    }


def qualified(col: dict[str, Any]) -> str:
    return ".".join(p for p in (col.get("schema"), col.get("table"), col.get("column")) if p)


def fetch_snapshot(client: BackendClient, source: Source) -> StructuredSnapshot:
    from autoontology.unstructured.align import TermInfo

    db_id, db_name = database_id_for(client, source)
    graph = client.semantic_graph(db_id)
    schemas = {str(s.get("id")): str(s.get("schema_name") or "") for s in client.schemas(db_id)}
    snapshot = StructuredSnapshot(
        source=source.name,
        database_id=db_id,
        database_name=db_name,
        graph_links=len(graph.get("links") or []),
    )
    table_owner: dict[str, str] = {}
    for node in sorted(graph.get("nodes") or [], key=lambda n: str(n.get("name"))):
        detail = client.get_term(str(node["id"]))
        attrs = []
        for a in client.term_column_attributes(str(node["id"])):
            primary = _column(a.get("primary_column"), schemas)
            attrs.append(
                {
                    "id": a.get("id"),
                    "name": a.get("name") or a.get("source_column") or "",
                    "description": a.get("description") or "",
                    "source_column": a.get("source_column") or "",
                    "datatype": a.get("datatype"),
                    **{k: primary[k] for k in ("table_id", "table", "schema", "column")},
                    "referenced": [_column(r, schemas) for r in a.get("referenced_columns") or []],
                }
            )
        tables = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "schema": schemas.get(str(t.get("schema_id")), ""),
            }
            for t in detail.get("tables") or []
        ]
        for t in tables:
            table_owner[str(t["id"])] = str(detail["id"])
        snapshot.term_sources[str(detail["id"])] = source.name
        snapshot.terms.append(
            TermInfo(
                id=str(detail["id"]),
                name=detail.get("name") or "",
                description=detail.get("description") or "",
                synonyms=list(detail.get("synonyms") or []),
                attributes=attrs,
                tables=tables,
            )
        )
    for term in snapshot.terms:
        for a in term.attributes:
            for ref in a["referenced"]:
                owner = table_owner.get(str(ref.get("table_id")))
                if owner and owner != term.id:
                    snapshot.fks.append(
                        SemanticFK(owner, term.id, str(a["id"]), a["name"], qualified(ref))
                    )
    return snapshot
