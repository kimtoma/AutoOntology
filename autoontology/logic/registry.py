# SPDX-License-Identifier: MIT
"""The logic definitions of a project as a governed registry: check, save, change
status, and keep them valid as the ontology moves.

* :func:`context` -- the :class:`~autoontology.logic.specs.LogicContext` specs are
  validated against: the object types (synced with the latest snapshot), field labels
  from that snapshot, and every non-retired definition.
* :func:`check` / :func:`save` -- parse and validate a spec; :func:`save` refuses an
  invalid one (400 ``invalid_spec`` with ``errors``) and stores a valid one as a draft
  (a new version when the name exists).
* :func:`set_status` -- ``active`` needs a valid spec whose references (functions it
  calls, actions a workflow runs) are active too; ``retired`` / ``draft`` always work.
* :func:`ensure_validated` -- run lazily before every use: when the fingerprint of the
  object types (snapshot revision, statuses, columns, links) and the definitions
  changed since the last run, every non-retired definition is validated again. One that
  fails becomes ``broken`` (reason: the first errors); a broken one that passes gets
  its previous status back. An active definition whose references are not active is
  broken too. Repeated until nothing changes, so a chain of references settles.

Function pins
-------------
Activating an action or workflow pins the versions of every function it calls,
transitively (``function_pins``). Running it (:func:`check_pins`: submit, approve, a
workflow run starting or resuming) is refused with 409 ``function_changed`` while an
active function's version differs from its pin, so an edited function never changes what
an owner activated. A broken action or workflow whose pinned functions changed is
restored to ``draft`` (reason kept: an owner must activate it again), never to
``active``. Activating or retiring a function that an active (or broken-from-active)
action or workflow uses needs ``logic.activate`` (:func:`used_by_active`, enforced by
the route).

Errors are :class:`~autoontology.objects.reader.ObjectError` (the API maps them).
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from typing import Any

from autoontology.logic import specs
from autoontology.logic.schema import Schema, build_schema
from autoontology.logic.specs import LogicContext, Spec, SpecError
from autoontology.objects import binding
from autoontology.objects.reader import ObjectError
from autoontology.store.logic import KINDS, LogicDef

logger = logging.getLogger(__name__)

LogicError = ObjectError
SYSTEM_ACTOR = "system"


@functools.lru_cache(maxsize=16)
def _model(path: str, project_id: str, revision: int, created_at: str) -> Any:
    from autoontology.ontology.model import MergedOntology
    from autoontology.store.db import Store

    view = Store(path).project(project_id)
    snap = view.get_snapshot(revision)
    if snap is None or not snap.model:
        return None
    return MergedOntology.from_dict(snap.model)


def latest_model(view: Any) -> Any:
    """The project's latest snapshot as a model (cached per revision), or None."""
    head = view.latest_snapshot(with_model=False)
    if head is None:
        return None
    return _model(str(view.store.path), view.id, head.revision, head.created_at)


def schema_of(view: Any) -> Schema:
    binding.ensure_synced(view)
    return build_schema(view.object_types().list(), latest_model(view))


def _parsed(d: LogicDef) -> Spec | None:
    try:
        return specs.parse_spec(d.spec)
    except SpecError:
        return None


def context(view: Any, *, validated: bool = True) -> LogicContext:
    """The validation context of *view* (``validated``: revalidate first when due)."""
    if validated:
        ensure_validated(view)
    ctx = LogicContext(schema_of(view))
    for d in view.logic_defs().list():
        if d.status == "retired":
            continue
        spec = _parsed(d)
        if spec is None:
            continue
        getattr(ctx, d.kind + "s")[d.name] = spec
        ctx.statuses[(d.kind, d.name)] = d.status
    return ctx


def check(view: Any, data: Any) -> tuple[Spec | None, list[dict[str, Any]]]:
    """*data* (a mapping or YAML/JSON text of one spec) parsed and validated:
    ``(spec, errors)``; ``spec`` is None when it does not even fit the schema."""
    try:
        spec = specs.parse_spec(data)
    except SpecError as exc:
        return None, exc.errors
    return spec, specs.validate(spec, context(view))


def check_many(view: Any, items: list[Any]) -> list[tuple[Spec | None, list[dict[str, Any]]]]:
    """Several specs checked together: each is validated with the others in place (a
    file whose functions call each other), in the order given."""
    parsed: list[tuple[Spec | None, list[dict[str, Any]]]] = []
    for item in items:
        try:
            parsed.append((specs.parse_spec(item), []))
        except SpecError as exc:
            parsed.append((None, exc.errors))
    ctx = context(view)
    for spec, _ in parsed:
        if spec is not None:
            ctx = ctx.with_spec(spec)
    return [(spec, specs.validate(spec, ctx) if spec else errors) for spec, errors in parsed]


def activation_errors(spec: Spec, ctx: LogicContext) -> list[dict[str, Any]]:
    """What keeps a valid *spec* from being active: references that are not active."""
    out = []
    for kind, name in specs.references(spec, ctx):
        status = ctx.statuses.get((kind, name))
        if status != "active":
            out.append(
                {
                    "path": kind,
                    "code": "reference_not_active",
                    "message": f"{kind} {name} is {status or 'missing'}, not active",
                }
            )
    return out


# --- function pins ---------------------------------------------------------------------


def function_closure(spec: Spec, ctx: LogicContext) -> list[str]:
    """The functions *spec* calls, and the functions those call, sorted (an action a
    workflow runs is not followed: it has its own pins)."""
    seen: set[str] = set()
    todo = [name for kind, name in specs.references(spec, ctx) if kind == "function"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        called = ctx.functions.get(name)
        if called is not None:
            todo += [n for k, n in specs.references(called, ctx) if k == "function"]
    return sorted(seen)


def function_pins(view: Any, spec: Spec, ctx: LogicContext) -> dict[str, int]:
    """``{function: version}`` of the functions *spec* uses now (:func:`function_closure`;
    a missing function is pinned at version 0)."""
    versions = {d.name: d.version for d in view.logic_defs().list("function")}
    return {name: versions.get(name, 0) for name in function_closure(spec, ctx)}


def pin_changes(pinned: dict[str, int] | None, current: dict[str, int]) -> list[str]:
    """What differs between *pinned* and *current* pins, readably (``[]``: nothing)."""
    if pinned is None:
        return ["the function versions were never pinned"]
    out = []
    for name in sorted(set(pinned) | set(current)):
        before, now = pinned.get(name), current.get(name)
        if before != now:
            if before is None:
                out.append(f"function {name} is new (v{now})")
            elif now is None:
                out.append(f"function {name} is no longer used")
            else:
                out.append(f"function {name} changed (v{before} -> v{now})")
    return out


def check_pins(view: Any, kind: str, name: str, ctx: LogicContext | None = None) -> None:
    """409 ``function_changed`` when the functions action/workflow *name* uses are not
    the versions it was activated with. A definition activated before pins existed is
    pinned now (its first use after the upgrade)."""
    d = view.logic_defs().get(kind, name)
    if d is None:
        return
    ctx = ctx or context(view)
    spec = getattr(ctx, kind + "s").get(name)
    if spec is None:
        return
    current = function_pins(view, spec, ctx)
    if d.function_pins is None:
        view.logic_defs().set_pins(kind, name, current)
        return
    changes = pin_changes(d.function_pins, current)
    if changes:
        raise LogicError(
            409,
            "function_changed",
            f"{kind} {name}: " + "; ".join(changes)[:400] + ": an owner must activate it again",
            changes=changes,
        )


def used_by_active(view: Any, function: str) -> list[str]:
    """The actions and workflows (``kind:name``) that are active, or broken while
    meant to be active, and use *function* (transitively)."""
    ensure_validated(view)
    defs = view.logic_defs()
    ctx = context(view, validated=False)
    out = []
    for kind in ("action", "workflow"):
        for d in defs.list(kind, ("active", "broken")):
            if d.status == "broken" and defs.status_before_broken(kind, d.name) != "active":
                continue
            spec = _parsed(d)
            pinned = set(d.function_pins or {})
            uses = set(function_closure(spec, ctx)) if spec is not None else set()
            if function in uses | pinned:
                out.append(f"{kind}:{d.name}")
    return out


def _summary(errors: list[dict[str, Any]]) -> str:
    text = "; ".join(
        (f"{e['path']}: " if e.get("path") else "") + str(e["message"]) for e in errors[:3]
    )
    if len(errors) > 3:
        text += f" (+{len(errors) - 3} more)"
    return text[:500]


def save(
    view: Any,
    data: Any,
    *,
    actor: str = "local",
    origin: str = "manual",
    evidence: list[Any] | None = None,
) -> LogicDef:
    """Store a valid spec as a draft (400 ``invalid_spec`` with ``errors`` otherwise)."""
    spec, errors = check(view, data)
    if spec is None or errors:
        raise LogicError(400, "invalid_spec", _summary(errors) or "invalid spec", errors=errors)
    saved = view.logic_defs().save(
        spec.kind, spec.name, specs.dump(spec), actor=actor, origin=origin, evidence=evidence
    )
    ensure_validated(view)
    return view.logic_defs().get(spec.kind, spec.name) or saved


def get(view: Any, kind: str, name: str) -> LogicDef:
    if kind not in KINDS:
        raise LogicError(400, "invalid_kind", f"kind must be one of {', '.join(KINDS)}")
    ensure_validated(view)
    found = view.logic_defs().get(kind, name) if name and len(name) <= 200 else None
    if found is None:
        raise LogicError(404, "unknown_definition", f"no {kind} {name[:80]!r}")
    return found


def set_status(view: Any, kind: str, name: str, status: str, *, actor: str = "local") -> LogicDef:
    """``active`` (a valid spec with active references; 409 ``invalid_spec`` /
    ``reference_not_active`` otherwise), ``retired`` or ``draft``."""
    if status not in ("active", "retired", "draft"):
        raise LogicError(400, "invalid_status", "status must be active, retired or draft")
    d = get(view, kind, name)
    defs = view.logic_defs()
    if status == "active":
        ctx = context(view)
        spec = _parsed(d)
        if spec is None:
            raise LogicError(409, "invalid_spec", "the stored spec does not fit the schema")
        errors = specs.validate(spec, ctx)
        if errors:
            raise LogicError(409, "invalid_spec", _summary(errors), errors=errors)
        errors = activation_errors(spec, ctx)
        if errors:
            raise LogicError(409, "reference_not_active", _summary(errors), errors=errors)
        if kind in ("action", "workflow"):
            defs.set_pins(kind, name, function_pins(view, spec, ctx))
    # a person activating an already active definition becomes its activator (a workflow
    # acts with its activator's role): record it unless they already are
    again = (
        status == "active"
        and d.status == "active"
        and actor not in ("", SYSTEM_ACTOR)
        and _activator(view, kind, name) != actor
    )
    updated = defs.set_status(kind, name, status, actor=actor, record_same=again)
    assert updated is not None
    ensure_validated(view)
    return defs.get(kind, name) or updated


def _activator(view: Any, kind: str, name: str) -> str | None:
    """Who last activated *kind* *name* (the newest ``active`` history entry by a
    person)."""
    for row in view.logic_defs().history(kind, name):
        if row["status"] == "active" and row["changed_by"] not in ("", SYSTEM_ACTOR):
            return str(row["changed_by"])
    return None


# --- revalidation -----------------------------------------------------------------------


def fingerprint(view: Any) -> str:
    """A hash of what validity depends on: the synced revision, the object types and the
    definitions (kind, name, version, status)."""
    types = view.object_types()
    parts: list[Any] = [types.synced_revision()]
    for t in types.list():
        parts.append([t.class_local, t.label, t.status, t.key, t.columns, t.links])
    for d in view.logic_defs().list():
        parts.append([d.kind, d.name, d.version, d.status])
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_validated(view: Any) -> dict[str, int]:
    """:func:`revalidate` when the fingerprint changed since the last run."""
    binding.ensure_synced(view)
    defs = view.logic_defs()
    current = fingerprint(view)
    if defs.validated_fingerprint() == current:
        return {"broken": 0, "restored": 0}
    counts = revalidate(view)
    defs.set_validated_fingerprint(fingerprint(view))
    return counts


def revalidate(view: Any) -> dict[str, int]:
    """Validate every non-retired definition again (module docstring)."""
    defs = view.logic_defs()
    counts = {"broken": 0, "restored": 0}
    for _ in range(len(defs.list()) + 2):
        ctx = context(view, validated=False)
        changed = False
        for d in defs.list():
            if d.status == "retired":
                continue
            spec = _parsed(d)
            if spec is None:
                errors: list[dict[str, Any]] = [
                    {"path": "", "code": "schema", "message": "the spec does not fit the schema"}
                ]
            else:
                errors = specs.validate(spec, ctx)
            target = d.status if d.status != "broken" else defs.status_before_broken(d.kind, d.name)
            if not errors and target == "active" and spec is not None:
                errors = activation_errors(spec, ctx)
            if errors:
                reason = _summary(errors)
                if d.status != "broken" or d.broken_reason != reason:
                    defs.set_status(d.kind, d.name, "broken", actor=SYSTEM_ACTOR, reason=reason)
                    counts["broken"] += d.status != "broken"
                    changed = True
            elif d.status == "broken":
                reason = ""
                if target == "active" and d.kind in ("action", "workflow") and spec is not None:
                    changes = pin_changes(d.function_pins, function_pins(view, spec, ctx))
                    if changes:
                        # an edited function never re-activates what an owner activated
                        target = "draft"
                        reason = (
                            _summary([{"path": "", "message": c} for c in changes])
                            + ": an owner must activate it again"
                        )
                defs.set_status(d.kind, d.name, target, actor=SYSTEM_ACTOR, reason=reason)
                counts["restored"] += 1
                changed = True
        if not changed:
            break
    if any(counts.values()):
        logger.info("logic definitions revalidated: %s", counts)
    return counts
