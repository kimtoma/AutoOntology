# SPDX-License-Identifier: MIT
"""How an object type is named -- one resolver for the REST API, the CLI (through the
API), the agent tools and the logic specs.

A reference names a type by, in this order:

1. its id (``ot_...``);
2. its class's local name (``term-...``);
3. its label, exactly, then ignoring case;
4. its table: the table name (``invoices``) or ``schema.table`` (``public.invoices``),
   exactly, then ignoring case.

The first step that matches anything decides. When it matches several types, retired
ones drop out, then (if that still leaves several) only active ones stay; more than one
left is ambiguous. The table name makes specs independent of the language the build
labelled its classes in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

#: A reference longer than this names nothing.
MAX_REF = 200


@dataclass(frozen=True)
class TypeNames:
    """The names one object type answers to."""

    name: str  # class_local
    id: str = ""
    label: str = ""
    schema: str = ""
    table: str = ""
    status: str = ""


def _narrow(found: list[T], names: Callable[[T], TypeNames]) -> list[T]:
    if len(found) <= 1:
        return found
    live = [t for t in found if names(t).status != "retired"] or found
    if len(live) > 1:
        active = [t for t in live if names(t).status == "active"]
        if active:
            live = active
    return live


def matches(items: Iterable[T], ref: str, names: Callable[[T], TypeNames]) -> list[T]:
    """The types of *items* that *ref* names (module docstring): ``[]`` none, one item
    found, several ambiguous."""
    ref = (ref or "").strip()
    if not ref or len(ref) > MAX_REF:
        return []
    pool = list(items)
    folded = ref.casefold()

    def qualified(n: TypeNames) -> str:
        return f"{n.schema}.{n.table}" if n.schema else n.table

    steps: list[Callable[[TypeNames], bool]] = [
        lambda n: bool(n.id) and n.id == ref,
        lambda n: n.name == ref,
        lambda n: bool(n.label) and n.label == ref,
        lambda n: bool(n.label) and n.label.casefold() == folded,
        lambda n: bool(n.table) and ref in (n.table, qualified(n)),
        lambda n: bool(n.table) and folded in (n.table.casefold(), qualified(n).casefold()),
    ]
    for step in steps:
        found = [t for t in pool if step(names(t))]
        if found:
            return _narrow(found, names)
    return []


def describe(n: TypeNames) -> dict[str, str]:
    """A candidate as an ambiguity error lists it."""
    table = f"{n.schema}.{n.table}" if n.schema else n.table
    return {"name": n.name, "label": n.label, "table": table, "status": n.status}
