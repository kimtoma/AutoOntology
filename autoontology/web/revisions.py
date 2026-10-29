# SPDX-License-Identifier: MIT
"""Snapshot history for the API: the revision list and the diff of two revisions
(``GET /api/ontology/revisions``, ``GET /api/ontology/diff``).

Nodes are keyed by their model local (``term-<id>`` / ``concept-<id>``, derived from
upstream and store ids, so a concept keeps its key across builds). A node is *changed*
when its label, kind, definitions, synonyms (alt labels), tables, or the number of
relations or properties touching it differ. Every list is capped at :data:`DIFF_CAP`
entries; ``totals`` always counts everything.
"""

from __future__ import annotations

from typing import Any

from autoontology.store.db import Store

DIFF_CAP = 500
#: node fields compared by the diff (``changed[].fields``)
NODE_FIELDS: tuple[str, ...] = (
    "label",
    "kind",
    "definitions",
    "synonyms",
    "tables",
    "relations",
    "properties",
)


def revision_list(store: Store, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first: revision, time, the run that wrote it (and its kind), counts, sources."""
    out = []
    kinds: dict[str, str] = {}
    for snap in store.list_snapshots(limit):
        if snap.run_id not in kinds:
            run = store.get_run(snap.run_id)
            kinds[snap.run_id] = run.kind if run is not None else ""
        out.append(
            {
                "revision": snap.revision,
                "created_at": snap.created_at,
                "run_id": snap.run_id,
                "run_kind": kinds[snap.run_id] or None,
                "counts": {k: v for k, v in snap.counts.items() if k != "by_source"},
                "sources": [
                    {"name": s.get("name"), "kind": s.get("kind"), "included": s.get("included")}
                    for s in snap.sources
                ],
            }
        )
    return out


def _nodes(model: Any) -> dict[str, dict[str, Any]]:
    relations: dict[str, int] = {}
    properties: dict[str, int] = {}
    for p in model.properties.values():
        if p.source == "vocabulary":
            continue
        if p.kind == "object":
            for end in {p.subject_node or p.domain, p.object_node or p.range}:
                relations[end] = relations.get(end, 0) + 1
        else:
            owner = p.subject_node or p.domain
            properties[owner] = properties.get(owner, 0) + 1
    for a in model.axioms:
        for end in (a.subject, a.object):
            relations[end] = relations.get(end, 0) + 1
    out: dict[str, dict[str, Any]] = {}
    for c in model.classes.values():
        out[c.local] = {
            "type": "class",
            "label": c.label,
            "kind": c.kind or ("term" if c.source == "structured" else ""),
            "definitions": sorted({*c.definitions, *([c.comment] if c.comment else [])}),
            "synonyms": sorted(set(c.alt_labels)),
            "tables": sorted(set(c.tables)),
            "relations": relations.get(c.local, 0),
            "properties": properties.get(c.local, 0),
        }
    for i in model.individuals.values():
        out[i.local] = {
            "type": "individual",
            "label": i.label,
            "kind": i.kind,
            "definitions": [i.comment] if i.comment else [],
            "synonyms": sorted(set(i.alt_labels)),
            "tables": [],
            "relations": relations.get(i.local, 0),
            "properties": properties.get(i.local, 0),
        }
    return out


def _capped(items: list[Any]) -> list[Any]:
    return items[:DIFF_CAP]


def _node_ref(local: str, node: dict[str, Any]) -> dict[str, Any]:
    return {"id": local, "name": node["label"], "type": node["type"], "kind": node["kind"]}


def diff(old: Any | None, new: Any, *, old_sources=(), new_sources=()) -> dict[str, Any]:
    """What changed from model *old* (None: nothing) to model *new*."""
    a = _nodes(old) if old is not None else {}
    b = _nodes(new)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = []
    for local in sorted(set(a) & set(b)):
        fields = [f for f in NODE_FIELDS if a[local][f] != b[local][f]]
        if fields:
            changed.append({"id": local, "name": b[local]["label"], "fields": fields})

    def axioms(model: Any | None) -> dict[tuple[str, str, str, str], bool]:
        if model is None:
            return {}
        return {(x.subject, x.kind, x.object, x.source): bool(x.accepted) for x in model.axioms}

    ax_a, ax_b = axioms(old), axioms(new)

    def ax_ref(key: tuple[str, str, str, str], accepted: bool) -> dict[str, Any]:
        return {
            "subject": key[0],
            "kind": key[1],
            "object": key[2],
            "source": key[3],
            "accepted": accepted,
        }

    ax_added = [ax_ref(k, ax_b[k]) for k in sorted(set(ax_b) - set(ax_a))]
    ax_removed = [ax_ref(k, ax_a[k]) for k in sorted(set(ax_a) - set(ax_b))]
    ax_changed = [
        {**ax_ref(k, ax_b[k]), "fields": ["accepted"]}
        for k in sorted(set(ax_a) & set(ax_b))
        if ax_a[k] != ax_b[k]
    ]

    def merges(model: Any | None) -> dict[tuple[str, str], str]:
        if model is None:
            return {}
        return {(m.concept_local, m.term_local): m.concept_name for m in model.merges}

    mg_a, mg_b = merges(old), merges(new)

    def mg_ref(key: tuple[str, str], names: dict[tuple[str, str], str]) -> dict[str, Any]:
        return {"concept": key[0], "concept_name": names[key], "term": key[1]}

    mg_added = [mg_ref(k, mg_b) for k in sorted(set(mg_b) - set(mg_a))]
    mg_removed = [mg_ref(k, mg_a) for k in sorted(set(mg_a) - set(mg_b))]
    src_a = {s.get("name") for s in old_sources if s.get("included")}
    src_b = {s.get("name") for s in new_sources if s.get("included")}
    count_a = old.counts() if old is not None else {}
    count_b = new.counts()
    delta = {
        k: v - (count_a.get(k) or 0)
        for k, v in count_b.items()
        if isinstance(v, int) and v != (count_a.get(k) or 0)
    }
    return {
        "classes": {
            "added": _capped([_node_ref(n, b[n]) for n in added]),
            "removed": _capped([_node_ref(n, a[n]) for n in removed]),
            "changed": _capped(changed),
            "totals": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        },
        "axioms": {
            "added": _capped(ax_added),
            "removed": _capped(ax_removed),
            "changed": _capped(ax_changed),
            "totals": {
                "added": len(ax_added),
                "removed": len(ax_removed),
                "changed": len(ax_changed),
            },
        },
        "merges": {
            "added": _capped(mg_added),
            "removed": _capped(mg_removed),
            "totals": {"added": len(mg_added), "removed": len(mg_removed)},
        },
        "sources": {"added": sorted(src_b - src_a), "removed": sorted(src_a - src_b)},
        "counts_delta": delta,
    }
