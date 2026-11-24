# SPDX-License-Identifier: MIT
"""What expressions are checked against: the project's object types as a
:class:`Schema` of :class:`TypeInfo` (fields and links).

A **field** is a column of the type's table, named in an expression by its column name
or by the label of the snapshot property bound to it (exact, then ignoring case) --
never by the property's local id, which collides for Hangul names. Operational
properties (written by actions, no column) are fields of type ``any``.

A **link** is a foreign key between two object types (``ObjectType.links``), named by the
link's name, its label, the target type's label, the target's table (``invoices`` or
``public.invoices``, so specs need not depend on the language of the labels) or
(outgoing links) its column.

Value types: ``number``, ``text``, ``bool``, ``date``, ``null`` and ``any`` (known only
when evaluated). :func:`column_type` maps a Postgres / XSD datatype to one of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoontology.objects import naming
from autoontology.objects.naming import TypeNames

VALUE_TYPES: tuple[str, ...] = ("number", "text", "bool", "date")

_NUMBER_HINTS = (
    "int",
    "numeric",
    "decimal",
    "double",
    "real",
    "float",
    "money",
    "serial",
    "long",
    "short",
    "byte",
)


def column_type(kind: str | None) -> str:
    """``number`` / ``bool`` / ``date`` / ``text`` for a column datatype (Postgres
    ``information_schema`` names or ``xsd:`` types; unknown or empty: ``text``)."""
    k = (kind or "").lower().removeprefix("xsd:")
    if not k:
        return "text"
    if k.startswith("bool"):
        return "bool"
    if "date" in k or "timestamp" in k:
        return "date"
    if any(h in k for h in _NUMBER_HINTS):
        return "number"
    return "text"


@dataclass
class LinkInfo:
    name: str
    label: str
    direction: str  # out | in
    column: str
    target: str  # the target type's class_local


@dataclass
class TypeInfo:
    name: str  # class_local
    label: str
    key: str
    status: str
    #: column -> value type
    columns: dict[str, str] = field(default_factory=dict)
    #: property label -> column
    labels: dict[str, str] = field(default_factory=dict)
    links: list[LinkInfo] = field(default_factory=list)
    #: operational property -> value type (declared by actions)
    operational: dict[str, str] = field(default_factory=dict)
    schema: str = ""
    table: str = ""
    id: str = ""

    def names(self) -> TypeNames:
        return TypeNames(self.name, self.id, self.label, self.schema, self.table, self.status)

    def field(self, name: str) -> tuple[str, str] | None:
        """``(column or property, value type)`` for a field name (column, label, then the
        same ignoring case; operational properties last)."""
        if name in self.columns:
            return name, self.columns[name]
        if name in self.labels:
            column = self.labels[name]
            return column, self.columns.get(column, "text")
        folded = name.casefold()
        for column, kind in self.columns.items():
            if column.casefold() == folded:
                return column, kind
        for label, column in self.labels.items():
            if label.casefold() == folded:
                return column, self.columns.get(column, "text")
        if name in self.operational:
            return name, self.operational[name]
        return None

    def link(self, name: str, schema: Schema) -> list[LinkInfo]:
        """The links *name* may mean (several: ambiguous)."""
        exact = [lk for lk in self.links if lk.name == name or lk.label == name]
        if exact:
            return exact
        folded = name.casefold()
        found = []
        for lk in self.links:
            target = schema.types.get(lk.target)
            names = [lk.label]
            if target is not None:
                names.append(target.label)
                names.append(target.table)
                if target.schema:
                    names.append(f"{target.schema}.{target.table}")
            if lk.direction == "out":
                names.append(lk.column)
            if any(n and n.casefold() == folded for n in names):
                found.append(lk)
        return found

    def field_names(self) -> list[str]:
        return [*self.columns, *self.operational]


@dataclass
class Schema:
    types: dict[str, TypeInfo] = field(default_factory=dict)

    def resolve(self, ref: str) -> TypeInfo | None:
        """The type *ref* names (:mod:`~autoontology.objects.naming`: id, class name,
        label, table); an ambiguous reference resolves to none (:meth:`candidates`)."""
        if ref in self.types:
            return self.types[ref]
        found = self.candidates(ref)
        return found[0] if len(found) == 1 else None

    def candidates(self, ref: str) -> list[TypeInfo]:
        """Every type *ref* may name (several: ambiguous)."""
        return naming.matches(self.types.values(), ref or "", lambda t: t.names())


def _labels(model: Any, otype: Any) -> dict[str, str]:
    """property label -> column of *otype*'s table, from the snapshot *model*."""
    if model is None:
        return {}
    out: dict[str, str] = {}
    qualified = otype.qualified_table
    for p in sorted(model.properties.values(), key=lambda p: p.local):
        if p.domain != otype.class_local or p.kind != "datatype" or not p.source_column:
            continue
        table, _, column = p.source_column.rpartition(".")
        if table == qualified and column and p.label and p.label not in out:
            out[p.label] = column
    return out


def _datatypes(model: Any, otype: Any) -> dict[str, str]:
    if model is None:
        return {}
    out: dict[str, str] = {}
    for p in model.properties.values():
        if p.domain != otype.class_local or not p.source_column:
            continue
        table, _, column = p.source_column.rpartition(".")
        if table == otype.qualified_table and column:
            out.setdefault(column, p.datatype or (p.range if p.kind == "datatype" else ""))
    return out


def type_info(otype: Any, model: Any = None) -> TypeInfo:
    """A :class:`TypeInfo` of an :class:`~autoontology.store.objects.ObjectType`."""
    declared = _datatypes(model, otype)
    columns: dict[str, str] = {}
    for c in otype.columns:
        name = str(c.get("name") or "")
        if not name:
            continue
        kind = str(c.get("type") or "") or declared.get(name, "")
        columns[name] = column_type(kind)
    labels = {k: v for k, v in _labels(model, otype).items() if v in columns and k not in columns}
    return TypeInfo(
        name=otype.class_local,
        label=otype.label or otype.class_local,
        key=otype.key,
        status=otype.status,
        schema=otype.schema,
        table=otype.table,
        id=otype.id,
        columns=columns,
        labels=labels,
        links=[
            LinkInfo(
                name=str(lk.get("name", "")),
                label=str(lk.get("label", "")),
                direction=str(lk.get("direction", "out")),
                column=str(lk.get("column", "")),
                target=str(lk.get("target", "")),
            )
            for lk in otype.links
        ],
    )


def build_schema(otypes: list[Any], model: Any = None) -> Schema:
    """A :class:`Schema` of every (non-retired) object type in *otypes*."""
    return Schema({t.class_local: type_info(t, model) for t in otypes if t.status != "retired"})
