# SPDX-License-Identifier: MIT
"""Deciding on logic candidates (the review inbox's ``logic`` category, ``lg:<id>``).

* :func:`item` -- a candidate as an inbox item (the shape the UI renders).
* :func:`decide` -- ``accept`` saves the candidate's spec -- or an edited spec the person
  supplies -- as a **draft** definition with ``origin: extracted`` and the candidate's
  evidence (:func:`autoontology.logic.registry.save`; activating it stays a separate,
  role-checked step). It is refused with 409 ``invalid_spec`` (and the ``errors``) when the
  spec does not validate against the project now, and with 409 ``definition_exists`` when a
  non-retired definition of that kind and name exists (saving would turn an active
  definition back into a draft: edit the name instead). ``reject`` hides the candidate.
  Both record a ``logic`` review decision (subject: the candidate id); a decided candidate
  is decided again only after :func:`undo` (409 ``already_decided``).
* :func:`undo` -- removes the decision; undoing an accept retires the draft it created
  when that draft is still the version the accept saved (otherwise the definition stays).

Errors are :class:`~autoontology.objects.reader.ObjectError`.
"""

from __future__ import annotations

from typing import Any

import yaml

from autoontology.logic import registry
from autoontology.objects.reader import ObjectError
from autoontology.store.decisions import Decision
from autoontology.store.logic_candidates import LogicCandidate

PREFIX = "lg"
DECISION_KIND = "logic"


def spec_text(spec: Any) -> str:
    """A spec as the YAML a person edits."""
    try:
        return yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    except yaml.YAMLError:  # pragma: no cover - specs are plain JSON values
        return ""


def get(view: Any, candidate_id: str) -> LogicCandidate:
    found = (
        view.logic_candidates().get(candidate_id)
        if candidate_id and len(candidate_id) <= 64
        else None
    )
    if found is None:
        raise ObjectError(404, "unknown_candidate", f"no logic candidate {candidate_id[:80]!r}")
    return found


def item(c: LogicCandidate, decision: Decision | None, existing: Any = None) -> dict[str, Any]:
    """The inbox item of *c* (``existing``: the definition of the same kind and name)."""
    data = c.to_dict()
    return {
        "id": f"{PREFIX}:{c.id}",
        "category": "logic",
        "concept": None,
        "term": None,
        "relation": None,
        "confidence": c.confidence,
        "rationale": c.rationale,
        "evidence": [
            {
                "quote": e.get("quote", ""),
                "document": e.get("document", ""),
                "path": e.get("path", ""),
                "page": e.get("page"),
                "verified": bool(e.get("verified")),
            }
            for e in c.evidence
        ],
        "suggested_action": "accept" if c.ready else "edit",
        "logic": {
            "candidate_id": c.id,
            "kind": c.kind,
            "name": c.name,
            "label": data["label"],
            "object_type": data["object_type"],
            "spec": c.spec,
            "spec_yaml": spec_text(c.spec),
            "errors": c.errors,
            "ready": c.ready,
            "evidence_verified": c.evidence_verified,
            "document": c.document,
            "run_id": c.run_id,
            "created_at": c.created_at,
            "existing": (
                {"status": existing.status, "version": existing.version}
                if existing is not None
                else None
            ),
        },
        "decision": decision.to_dict() if decision else None,
    }


def listing(view: Any, *, ready: bool | None = None) -> list[dict[str, Any]]:
    """Every candidate of the project as an inbox item (*ready*: only those whose spec
    validated / only those that need an edit), newest first."""
    decisions = view.decisions().by_subject(DECISION_KIND)
    defs = view.logic_defs()
    out = []
    for c in reversed(view.logic_candidates().list()):
        if ready is not None and c.ready != ready:
            continue
        out.append(item(c, decisions.get(c.id), defs.get(c.kind, c.name)))
    return out


def decide(
    view: Any,
    candidate_id: str,
    decision: str,
    *,
    actor: str,
    spec: Any = None,
    note: str = "",
    payload: dict[str, Any] | None = None,
) -> tuple[Decision, Any]:
    """Accept or reject a candidate (module docstring): ``(decision, saved definition or
    None)``."""
    c = get(view, candidate_id)
    if decision not in ("accept", "reject"):
        raise ObjectError(400, "invalid_decision", "a logic item takes accept or reject")
    if view.decisions().get(DECISION_KIND, c.id) is not None:
        raise ObjectError(
            409, "already_decided", "this candidate is decided already: undo the decision first"
        )
    body: dict[str, Any] = {
        "category": "logic",
        **(payload or {}),
        "candidate_id": c.id,
        "kind": c.kind,
        "name": c.name,
    }
    saved = None
    if decision == "accept":
        data = spec if spec is not None else c.spec
        parsed, errors = registry.check(view, data)
        if parsed is not None and parsed.kind != c.kind:
            raise ObjectError(
                400, "kind_mismatch", f"the edited spec must stay a {c.kind}, not a {parsed.kind}"
            )
        if parsed is None or errors:
            raise ObjectError(
                409,
                "invalid_spec",
                registry._summary(errors) or "invalid spec",
                errors=errors,
            )
        existing = view.logic_defs().get(parsed.kind, parsed.name)
        if existing is not None and existing.status != "retired":
            raise ObjectError(
                409,
                "definition_exists",
                f"a {parsed.kind} named {parsed.name} exists ({existing.status}, "
                f"v{existing.version}): accept it under another name",
            )
        saved = registry.save(view, data, actor=actor, origin="extracted", evidence=c.evidence)
        body.update(
            def_id=saved.id,
            def_name=saved.name,
            version=saved.version,
            edited=spec is not None,
        )
    elif spec is not None:
        raise ObjectError(400, "invalid_request", "spec is for accepting a logic item")
    recorded = view.decisions().put(
        Decision(DECISION_KIND, c.id, decision, body, note=note, decided_by=actor)
    )
    return recorded, saved


def undo(view: Any, candidate_id: str, *, actor: str = "local") -> bool:
    """Remove the decision on a candidate (module docstring); False when there was none."""
    decisions = view.decisions()
    d = decisions.get(DECISION_KIND, candidate_id)
    if d is None:
        return False
    if d.decision == "accept" and d.payload.get("def_id"):
        defs = view.logic_defs()
        current = defs.get(str(d.payload.get("kind")), str(d.payload.get("def_name") or ""))
        if (
            current is not None
            and current.id == d.payload.get("def_id")
            and current.version == d.payload.get("version")
            and current.status in ("draft", "broken")
        ):
            registry.set_status(view, current.kind, current.name, "retired", actor=actor)
    return decisions.delete(DECISION_KIND, candidate_id)
