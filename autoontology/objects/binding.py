# SPDX-License-Identifier: MIT
"""Object type proposals and their validation against the ontology and the source.

:func:`candidates` reads a snapshot (:class:`~autoontology.ontology.model.MergedOntology`):
every structured class backed by exactly one table (``OClass.tables``) is a candidate, its
columns are what the snapshot knows of the table (``OProperty.source_column`` of its
datatype and foreign-key properties) and its links are the foreign-key object
properties from or to it.

:func:`sync` runs once per snapshot revision (:func:`ensure_synced`, called lazily by the
readers of object types): new candidates become ``proposed`` types (the review inbox's
``objects`` category), proposals are refreshed, and every type is checked against the
snapshot -- an active type whose class or table is gone becomes ``broken`` (reason
``snapshot: ...``) and comes back once they are. It never touches a source database.

:func:`verify` checks a type against the source (accept, re-bind): the table's columns,
its primary key (taken as the key when it has one column, unless a key was set by hand),
else the first key-like column (:func:`key_order`) that is unique and never null.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from autoontology.objects.reader import ObjectError, valid_identifier
from autoontology.store.objects import ObjectType

logger = logging.getLogger(__name__)

#: A column whose (lower-cased) name contains one of these is preferred as the title.
TITLE_HINTS: tuple[str, ...] = ("명", "이름", "제목", "name", "title", "label", "subject")
SNAPSHOT_REASON = "snapshot: "
SOURCE_REASON = "source: "


@dataclass
class Candidate:
    class_local: str
    label: str
    source_name: str
    schema: str
    table: str
    columns: list[dict[str, str]] = field(default_factory=list)
    fk_columns: set[str] = field(default_factory=set)
    links: list[dict[str, Any]] = field(default_factory=list)


def split_table(qualified: str) -> tuple[str, str]:
    schema, _, table = qualified.rpartition(".")
    return schema, table


def _column_of(source_column: str, qualified: str) -> str | None:
    table, _, column = source_column.rpartition(".")
    return column if table == qualified and column else None


def id_like(name: str) -> bool:
    """A column name that reads as an identifier: ends with ``ID``/``_id``/``id`` (any
    case)."""
    return name.strip().lower().endswith("id")


def is_text(kind: str) -> bool:
    kind = (kind or "").lower()
    return any(t in kind for t in ("char", "text", "string"))


def class_links(model: Any, local: str) -> list[dict[str, Any]]:
    """The links of *local*: ``out`` for a foreign key its table holds (the column holds
    the target's key), ``in`` for one another class's table holds to it."""
    out: list[dict[str, Any]] = []
    for p in sorted(model.properties.values(), key=lambda p: p.local):
        if p.kind != "object" or p.source != "semantic_fk" or not p.source_column:
            continue
        column = p.source_column.rpartition(".")[2]
        if p.domain == local:
            out.append(
                {
                    "name": p.local,
                    "label": p.label,
                    "direction": "out",
                    "column": column,
                    "target": p.range,
                }
            )
        if p.range == local:
            domain = model.classes.get(p.domain)
            out.append(
                {
                    "name": f"{p.local}-in",
                    "label": f"{domain.label if domain else p.domain} ({column})",
                    "direction": "in",
                    "column": column,
                    "target": p.domain,
                }
            )
    return out


def candidates(model: Any) -> list[Candidate]:
    """Every structured class of *model* backed by exactly one table."""
    out: list[Candidate] = []
    for cls in sorted(model.classes.values(), key=lambda c: c.local):
        if cls.source != "structured" or len(cls.tables) != 1 or not cls.structured_source:
            continue
        qualified = cls.tables[0]
        schema, table = split_table(qualified)
        if not valid_identifier(table) or (schema and not valid_identifier(schema)):
            continue
        columns: list[dict[str, str]] = []
        fks: set[str] = set()
        seen: set[str] = set()
        for p in sorted(model.properties.values(), key=lambda p: p.local):
            if p.domain != cls.local or not p.source_column:
                continue
            column = _column_of(p.source_column, qualified)
            if column is None or not valid_identifier(column):
                continue
            if p.kind == "object":
                fks.add(column)
            if column not in seen:
                seen.add(column)
                kind = p.datatype or (p.range if p.kind == "datatype" else "")
                columns.append({"name": column, "type": kind})
        out.append(
            Candidate(
                class_local=cls.local,
                label=cls.label,
                source_name=cls.structured_source,
                schema=schema,
                table=table,
                columns=columns,
                fk_columns=fks,
                links=class_links(model, cls.local),
            )
        )
    return out


def key_order(columns: list[str], fk_columns: set[str]) -> list[str]:
    """Key candidates, best first: key-like columns that are no foreign key, key-like
    foreign keys, then the first column."""
    own = [c for c in columns if id_like(c) and c not in fk_columns]
    fks = [c for c in columns if id_like(c) and c in fk_columns]
    order = own + fks
    if columns and columns[0] not in order:
        order.append(columns[0])
    return order


def infer_key(
    columns: list[str],
    fk_columns: set[str],
    *,
    pk: list[str] | None = None,
    check: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """``(key column, origin)``: a one-column primary key (``pk``), else the first of
    :func:`key_order` that passes *check* (unique and non-null; every one passes without
    a check) -- ``("", "inferred")`` when none does."""
    if pk and len(pk) == 1 and pk[0] in columns:
        return pk[0], "pk"
    for column in key_order(columns, fk_columns):
        if check is None or check(column):
            return column, "inferred"
    return "", "inferred"


def infer_title(columns: list[dict[str, str]], key: str) -> str:
    """The first name-like text column (:data:`TITLE_HINTS`) that is not the key nor
    key-like; "" when there is none (the object's key is its title then) -- a status or
    code column would make a poor title."""
    text = [
        c["name"]
        for c in columns
        if c["name"] != key and is_text(c.get("type", "")) and not id_like(c["name"])
    ]
    preferred = [c for c in text if any(h in c.lower() for h in TITLE_HINTS)]
    return (preferred or [""])[0]


def proposal(candidate: Candidate, revision: int | None) -> ObjectType:
    names = [c["name"] for c in candidate.columns]
    key, origin = infer_key(names, candidate.fk_columns)
    return ObjectType(
        class_local=candidate.class_local,
        label=candidate.label,
        source_name=candidate.source_name,
        schema=candidate.schema,
        table=candidate.table,
        key_columns=[key] if key else [],
        title_column=infer_title(candidate.columns, key),
        links=candidate.links,
        columns=candidate.columns,
        status="proposed",
        key_origin=origin,
        key_checked=False,
        validated_revision=revision,
    )


def snapshot_problem(model: Any, otype: ObjectType) -> str:
    """Why *otype* no longer fits *model* ("" when it does)."""
    cls = model.classes.get(otype.class_local)
    if cls is None:
        return f"{SNAPSHOT_REASON}class {otype.label or otype.class_local} is gone"
    if otype.qualified_table not in cls.tables:
        return f"{SNAPSHOT_REASON}table {otype.qualified_table} is no longer the class's table"
    return ""


def sync(view: Any, model: Any, revision: int) -> dict[str, int]:
    """Bring the project's object types in line with snapshot *revision* (module
    docstring); returns what changed: ``{proposed, refreshed, removed, broken, restored}``."""
    types = view.object_types()
    existing = {t.class_local: t for t in types.list()}
    found = {c.class_local: c for c in candidates(model)}
    counts = dict.fromkeys(("proposed", "refreshed", "removed", "broken", "restored"), 0)
    for local, cand in found.items():
        current = existing.get(local)
        if current is None:
            types.save(proposal(cand, revision))
            counts["proposed"] += 1
        elif current.status == "proposed" and current.key_origin != "manual":
            fresh = proposal(cand, revision)
            types.save(replace(fresh, id=current.id, created_at=current.created_at))
            counts["refreshed"] += 1
    for local, current in existing.items():
        if current.status == "retired":
            continue
        problem = snapshot_problem(model, current)
        if current.status == "proposed":
            if problem:
                types.delete(local)
                counts["removed"] += 1
            continue
        updated = replace(current, validated_revision=revision)
        cls = model.classes.get(local)
        if cls is not None:
            updated.label = cls.label
            updated.links = class_links(model, local)
        if problem and current.status == "active":
            updated.status, updated.broken_reason = "broken", problem
            counts["broken"] += 1
        elif (
            not problem
            and current.status == "broken"
            and current.broken_reason.startswith(SNAPSHOT_REASON)
        ):
            updated.status, updated.broken_reason = "active", ""
            counts["restored"] += 1
        types.save(updated)
    types.set_synced_revision(revision)
    if any(counts.values()):
        logger.info("object types synced with revision %d: %s", revision, counts)
    return counts


def ensure_synced(view: Any, model: Any | None = None, revision: int | None = None) -> None:
    """:func:`sync` with the project's latest snapshot unless it already was (*model* /
    *revision*: the latest snapshot's, when the caller has it loaded)."""
    from autoontology.ontology.model import MergedOntology

    if revision is None:
        head = view.latest_snapshot(with_model=False)
        if head is None:
            return
        revision, model = head.revision, None
    types = view.object_types()
    if types.synced_revision() == revision:
        return
    if model is None:
        snap = view.get_snapshot(revision)
        if snap is None or not snap.model:
            return
        model = MergedOntology.from_dict(snap.model)
    sync(view, model, revision)


def verify(
    backend: Any,
    otype: ObjectType,
    *,
    key: str | None = None,
    title: str | None = None,
) -> ObjectType:
    """*otype* checked against its source (*backend*: a
    :class:`~autoontology.objects.reader.PgBackend` in a read-only transaction): the
    table's columns as they are now, the key (*key*: set by hand) verified unique and
    never null, the title (*title*: set by hand, "" for none) a column of the table.
    :class:`ObjectError` when the table is missing (409 ``table_missing``), a column is
    unknown (400 ``unknown_column``) or no key qualifies (409 ``key_not_unique``)."""
    columns = backend.columns(otype.schema, otype.table)
    if not columns:
        raise ObjectError(
            409, "table_missing", f"table {otype.qualified_table} is not in the source"
        )
    names = [c["name"] for c in columns]
    for label, value in (("key", key), ("title", title)):
        if value and value not in names:
            raise ObjectError(
                400,
                "unknown_column",
                f"{label} column {value!r} is not a column of {otype.qualified_table}",
            )
    stats: dict[str, dict[str, int]] = {}

    def unique(column: str) -> bool:
        stats[column] = backend.key_stats(otype.schema, otype.table, column)
        s = stats[column]
        return s["nulls"] == 0 and s["distinct"] == s["rows"]

    fks = {link["column"] for link in otype.links if link.get("direction") == "out"}
    if key:
        chosen, origin = key, "manual"
    elif otype.key_origin == "manual" and otype.key in names:
        chosen, origin = otype.key, "manual"
    else:
        pk = backend.primary_key(otype.schema, otype.table)
        chosen, origin = infer_key(names, fks, pk=pk, check=unique)
        if not chosen:
            raise ObjectError(
                409,
                "key_not_unique",
                f"no column of {otype.qualified_table} is unique and never empty: "
                "choose the key by hand",
                checked=stats,
            )
    if origin != "pk":
        if chosen not in stats:
            unique(chosen)
        s = stats[chosen]
        if s["nulls"] or s["distinct"] != s["rows"]:
            raise ObjectError(
                409,
                "key_not_unique",
                f"{chosen!r} is not a key of {otype.qualified_table}: {s['rows']} rows, "
                f"{s['distinct']} distinct values, {s['nulls']} empty",
                checked=stats,
            )
    if title is not None:
        chosen_title = title
    elif otype.title_column in names and otype.title_column != chosen:
        chosen_title = otype.title_column
    else:
        chosen_title = infer_title(columns, chosen)
    return replace(
        otype,
        columns=columns,
        key_columns=[chosen],
        key_origin=origin,
        key_checked=True,
        title_column=chosen_title,
    )
