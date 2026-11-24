# SPDX-License-Identifier: MIT
"""The action runner: preview, submit, approve / reject, apply and revert actions on
objects. Sources are never written: an action's effects become rows of the edit layer
(``object_edits``, :mod:`autoontology.objects.overlay`) tied to an ``action_runs`` row
(:mod:`autoontology.store.actions`), so every run can be reverted.

Flow
----
* :func:`preview` -- the action (active only) on one object: parameters coerced and
  checked (types, required, enum, defaults), every precondition evaluated (failures with
  their messages), the effects (property, the value shown now, the new value), whether
  approval is needed (``approval.role`` and its ``when``; an unknown ``when`` needs it:
  fail-closed), the notifications (rendered subject/body and a recipient *count*, never
  addresses) and whether the caller may submit. Nothing is stored.
* :func:`submit` -- the preview again, then (409 ``precondition_failed`` with
  ``failures`` when a precondition does not hold) either a ``pending_approval`` run or an
  ``applied`` one with its edits. The same ``idempotency_key`` returns the first run
  (409 ``idempotency_conflict`` when it was for another action or object).
* :func:`approve` / :func:`reject` -- a pending run. The approver needs the run's
  approval role and, with accounts on, must not be the person behind the request
  (four-eyes: neither ``requested_by`` nor ``requested_for`` -- an agent token's creator
  may not approve what their token proposed; accounts off: one local user, so
  self-approval is allowed). The requester (or the token's creator) may reject
  (withdraw) their own request. A workflow's runs (``requested_by: workflow:<name>``)
  may be approved by the owner who activated the workflow: it is owner-activated
  automation that editors cannot change (function pins). Approving evaluates the
  preconditions again on the current data and the current active version of the action:
  a run whose precondition no longer holds, whose action changed or is no longer active,
  or one of whose functions changed since the submit (``function_changed``), becomes
  ``failed`` (with ``error``) instead of applied.
* :func:`revert` -- an applied run's edits reverted (the run becomes ``reverted``):
  needs editor or the action's ``min_role``, whichever is higher; 409 ``superseded`` when
  a later edit changed one of the same properties, unless an owner passes ``force``.

Function pins: an action runs only while the functions it uses are the versions it was
activated with (:func:`autoontology.logic.registry.check_pins`, 409
``function_changed``); a submit records those versions in the run's preview
(``functions``).

Permissions are the engine's: routes only need ``objects.read``; :class:`Caller` carries
the role (admins and accounts-off callers are owners). Submitting needs the action's
``permission.min_role`` (403 ``forbidden``, ``permission: action.run``); deciding needs
the run's ``approval_role`` (``permission: action.approve``).

Agents (``via="agent"``) act with their token creator's current role and their token's
scope (:class:`Caller` ``agent_mode``: ``propose`` | ``execute``, ``allowed_actions``).
The effective mode is the lower of the token scope and the spec's ``agent`` policy:
``none`` refuses (403 ``agent_not_allowed``), ``propose`` always creates a
``pending_approval`` run (approval role: the spec's, else ``reviewer``), ``execute``
follows the spec's approval rules.

Concurrency: everything is evaluated outside the store's write lock (sources, links,
functions); then, inside one ``BEGIN IMMEDIATE`` transaction, the object's edit-layer
state is compared with the one the evaluation read (a marker of its live edits) and the
run row and its edits are written together. A changed marker re-evaluates (at most
:data:`MAX_ATTEMPTS` times, then 409 ``object_busy``), so two submits on one object
serialise and each sees the other's result.

Notifications are sent after the run committed, from a daemon thread
(:func:`wait_notifications` joins them, for tests and the CLI); a mail failure never
fails the run. Their outcome (counts only) is merged into the run's ``result``.

Every transition is audited (``action.submit``, ``action.approve``, ``action.reject``,
``action.apply``, ``action.revert``, ``action.fail``) with the run id as target.
Callbacks registered with :func:`on_run_decided` are called with ``(view, run)`` after a
run reached ``applied``, ``rejected``, ``failed`` or ``reverted`` (the workflow runner
resumes paused steps from them; it can also poll ``view.action_runs().get(id)``).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from autoontology.logic import expr as ex
from autoontology.logic import functions, registry, specs
from autoontology.logic.schema import TypeInfo
from autoontology.logic.specs import ActionSpec, LogicContext
from autoontology.objects import overlay, reader, service
from autoontology.objects.reader import ObjectError
from autoontology.store.actions import ActionRun
from autoontology.store.db import now_iso
from autoontology.store.objects import ObjectType

logger = logging.getLogger("autoontology.actions")

ROLES: tuple[str, ...] = specs.ROLES
#: parameters of one call, as JSON, at most this long
MAX_PARAMS_CHARS = 16_000
MAX_IDEMPOTENCY_CHARS = 200
#: evaluations a submit or approval repeats when the object changed meanwhile
MAX_ATTEMPTS = 3
#: recipients one notification is sent to at most
MAX_RECIPIENTS = 50
#: recipients one action run notifies at most, over all its messages (the rest are
#: dropped and the run records ``notifications_capped``)
MAX_RUN_RECIPIENTS = 200
MAX_NOTE_CHARS = 1000
TERMINAL = ("applied", "rejected", "failed", "reverted")
AGENT_MODES = ("none", "propose", "execute")


def rank(role: str | None) -> int:
    """The position of *role* in :data:`ROLES` (-1: none)."""
    return ROLES.index(role) if role in ROLES else -1


@dataclass(frozen=True)
class Caller:
    """Who asks. ``role``: the caller's project role (admins / accounts off: owner);
    ``four_eyes``: accounts are on (a requester may not approve their own run);
    ``via``: ui | cli | agent | workflow. Agents: ``token_id``, ``agent_mode`` (the token's
    highest write scope: propose | execute, None: read only) and ``allowed_actions``
    (None: every action). Workflows: ``workflow_run_id``."""

    name: str
    role: str = "owner"
    user_id: str | None = None
    four_eyes: bool = False
    via: str = "ui"
    ip: str = ""
    token_id: str | None = None
    agent_mode: str | None = None
    allowed_actions: tuple[str, ...] | None = None
    workflow_run_id: str | None = None
    #: the person behind an agent call (the token creator's email; "" none)
    requested_for: str | None = None

    @property
    def person(self) -> str:
        """Who the four-eyes rule treats as the requester."""
        if self.requested_for is not None:
            return self.requested_for
        return "" if self.via == "agent" else self.name

    @classmethod
    def from_context(cls, ctx: Any, settings: Any, *, via: str = "ui") -> Caller:
        """The caller of a :class:`~autoontology.web.projects.ProjectContext`."""
        return cls(
            name=ctx.actor,
            role="owner" if ctx.is_admin else ctx.role,
            user_id=ctx.user_id,
            four_eyes=bool(settings.accounts_enabled),
            via=via,
            ip=ctx.ip,
        )


# --- listeners and notifications threads ----------------------------------------------

_listeners: list[Callable[[Any, ActionRun], None]] = []
_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()


def on_run_decided(callback: Callable[[Any, ActionRun], None]) -> Callable[[Any, ActionRun], None]:
    """Call *callback(view, run)* whenever a run reaches applied, rejected, failed or
    reverted (after commit; its exceptions are logged). Returns *callback* (a decorator)."""
    if callback not in _listeners:
        _listeners.append(callback)
    return callback


def remove_listener(callback: Callable[[Any, ActionRun], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def _decided(view: Any, run: ActionRun) -> None:
    for callback in list(_listeners):
        try:
            callback(view, run)
        except Exception:  # noqa: BLE001 - a listener never breaks the runner
            logger.exception("action run listener failed for %s", run.id)


def wait_notifications(timeout: float = 30.0) -> None:
    """Join the notification threads started so far."""
    with _threads_lock:
        pending = list(_threads)
        _threads.clear()
    for t in pending:
        t.join(timeout)


# --- planning ---------------------------------------------------------------------------


def _err(status: int, code: str, message: str, **extra: Any) -> ObjectError:
    return ObjectError(status, code, message, **extra)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


@dataclass
class _Plan:
    name: str
    version: int
    spec: ActionSpec
    info: TypeInfo
    otype: ObjectType
    record: dict[str, Any]
    params: dict[str, Any]
    marker: tuple[Any, ...]
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    approval_role: str | None = None
    approval_reason: str = ""
    #: [{subject, body, to: [kinds], recipients: [emails], invalid, skipped_roles}]
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: {function: version} the action uses now (its pins)
    functions: dict[str, int] = field(default_factory=dict)
    #: recipients dropped by :data:`MAX_RUN_RECIPIENTS`
    capped: int = 0

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [p for p in self.preconditions if not p["ok"]]

    @property
    def effect_errors(self) -> list[dict[str, Any]]:
        return [e for e in self.effects if e.get("error")]


def _active_action(ctx: LogicContext, view: Any, name: str) -> tuple[ActionSpec, int]:
    spec = ctx.actions.get(name) if name and len(name) <= 200 else None
    if spec is None:
        raise _err(404, "unknown_action", f"no action {name[:80]!r}")
    status = ctx.statuses.get(("action", name))
    if status != "active":
        raise _err(409, "action_not_active", f"action {name} is {status}, not active")
    d = view.logic_defs().get("action", name)
    return spec, d.version if d is not None else 1


def _check_params(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise _err(400, "invalid_param", "params must be an object")
    try:
        size = len(json.dumps(params, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise _err(400, "invalid_param", "params must be JSON values") from exc
    if size > MAX_PARAMS_CHARS:
        raise _err(400, "params_too_large", f"params are at most {MAX_PARAMS_CHARS} characters")
    return params


def _marker(conn: Any, view: Any, type_name: str, key: str) -> tuple[Any, ...]:
    """The live edits of one object, summarised: changes whenever an edit is added or
    reverted."""
    sql = (
        "SELECT count(*), coalesce(max(id), 0), coalesce(sum(id), 0) FROM object_edits "
        "WHERE project_id = ? AND object_type = ? AND object_key = ? AND reverted_at IS NULL"
    )
    args = (view.id, type_name, str(key))
    if conn is not None:
        return tuple(conn.execute(sql, args).fetchone())
    with view.store._conn() as own:
        return tuple(own.execute(sql, args).fetchone())


class _Evaluator:
    """Expressions of one action on one object (links through a shared loader)."""

    def __init__(
        self,
        settings: Any,
        view: Any,
        ctx: LogicContext,
        info: TypeInfo,
        spec: ActionSpec,
        stack: ExitStack,
        when: dt.date,
    ) -> None:
        self.ctx, self.info, self.spec, self.when = ctx, info, spec, when
        self.scope = ctx.scope(info, spec.params)
        self.functions = functions.Functions(
            ctx, lambda n: ctx.statuses.get(("function", n)) == "active"
        )
        self.loader = functions.SourceLoader(settings, view, stack, ctx.schema.types)

    def run(self, text: str, obj: ex.Obj, params: dict[str, Any]) -> Any:
        """The value of *text*; raises :class:`~autoontology.logic.expr.EvalError` /
        :class:`ObjectError` (the caller decides what a failure means)."""
        try:
            compiled = ex.compile_expr(text, self.scope)
        except ex.ExprError as exc:  # the stored spec no longer fits: it is broken
            raise ex.EvalError(exc.code, exc.message) from exc
        ectx = ex.EvalContext(today=self.when, loader=self.loader, function=self.functions.runnable)
        return compiled.evaluate(obj, ectx, params)

    def template(self, text: str, obj: ex.Obj, params: dict[str, Any]) -> str:
        def one(m: re.Match[str]) -> str:
            inner = m.group(1).strip()
            try:
                return _text(self.run(inner, obj, params))
            except (ex.EvalError, ObjectError):
                return ""

        return specs.TEMPLATE_RE.sub(one, text or "")


def _read(settings: Any, view: Any, otype: ObjectType, key: str) -> dict[str, Any]:
    if not key or len(str(key)) > reader.MAX_VALUE_CHARS:
        raise _err(404, "unknown_object", "no such object")
    return functions._records(settings, view, otype, [str(key)])[0]


def _members(view: Any, settings: Any) -> list[dict[str, Any]] | None:
    """Active project members with an email (None: accounts off, no members)."""
    if not settings.accounts_enabled:
        return None
    from autoontology.store.accounts import Accounts

    return [
        m
        for m in Accounts(view.store).list_members(view.id)
        if m.get("email") and not m.get("disabled_at")
    ]


def _recipients(
    n: specs.NotifySpec,
    ev: _Evaluator,
    obj: ex.Obj,
    params: dict[str, Any],
    members: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    emails: list[str] = []
    invalid = 0
    skipped_roles = False
    kinds: list[str] = []
    for to in n.to:
        kind, _, rest = to.partition(":")
        rest = rest.strip()
        if kind == "role":
            kinds.append(f"role:{rest}")
            if members is None:
                skipped_roles = True
                continue
            emails += [m["email"] for m in members if rank(m["role"]) >= rank(rest)]
        elif kind == "email":
            kinds.append("email")
            emails.append(rest)
        elif kind == "field":
            kinds.append("field")
            try:
                value = ev.run(rest, obj, params)
            except (ex.EvalError, ObjectError):
                value = None
            text = _text(value).strip()
            if specs.EMAIL_RE.match(text):
                emails.append(text)
            else:
                invalid += 1
    unique = list(dict.fromkeys(e.strip().lower() for e in emails if e.strip()))
    return {
        "to": kinds,
        "recipients": unique[:MAX_RECIPIENTS],
        "invalid": invalid,
        "skipped_roles": skipped_roles,
    }


def _plan(
    settings: Any,
    view: Any,
    name: str,
    key: str,
    params: Any,
    *,
    when: dt.date | None = None,
    ctx: LogicContext | None = None,
) -> _Plan:
    ctx = ctx or registry.context(view)
    spec, version = _active_action(ctx, view, name)
    registry.check_pins(view, "action", name, ctx)
    info = ctx.type_of(spec.object_type)
    if info is None:
        raise _err(409, "action_not_active", f"the object type of {name} is not active")
    otype = service.readable_type(view, info.name)
    coerced = functions._params(spec, _check_params(params))  # type: ignore[arg-type]
    marker = _marker(None, view, otype.class_local, str(key))
    record = _read(settings, view, otype, str(key))
    plan = _Plan(name, version, spec, info, otype, record, coerced, marker)
    plan.functions = registry.function_pins(view, spec, ctx)
    full = {p.name: coerced.get(p.name) for p in spec.params}
    obj = ex.Obj(info, dict(record["values"]))
    with ExitStack() as stack:
        ev = _Evaluator(settings, view, ctx, info, spec, stack, when or functions.today())
        for i, pre in enumerate(spec.preconditions):
            item: dict[str, Any] = {"index": i, "message": pre.message}
            try:
                item["ok"] = ev.run(pre.expr, obj, full) is True
            except (ex.EvalError, ObjectError) as exc:
                item["ok"] = False
                item["error"] = {"code": exc.code, "message": exc.message}
            plan.preconditions.append(item)
        after = dict(record["values"])
        for i, eff in enumerate(spec.effects):
            found = None if eff.operational else info.field(eff.set)
            prop = found[0] if found else eff.set
            kind = found[1] if found else None
            effect: dict[str, Any] = {
                "index": i,
                "property": prop,
                "label": eff.set,
                "operational": bool(eff.operational),
                "old_value": record["values"].get(prop),
            }
            try:
                value = ev.run(eff.value, obj, full)
                if kind is not None:
                    value = ex.coerce(value, kind, prop)
                effect["new_value"] = ex.to_json(value)
            except (ex.EvalError, ObjectError) as exc:
                effect["new_value"] = None
                effect["error"] = {"code": exc.code, "message": exc.message}
            after[prop] = effect["new_value"]
            plan.effects.append(effect)
        role = spec.approval.role
        if role != "none":
            if spec.approval.when is None:
                plan.approval_role, plan.approval_reason = role, "always"
            else:
                try:
                    needed = ev.run(spec.approval.when, obj, full)
                except (ex.EvalError, ObjectError):
                    needed = None
                if needed is not False:
                    plan.approval_role = role
                    plan.approval_reason = "when" if needed is True else "when_unknown"
        if spec.notify:
            members = _members(view, settings)
            obj_after = ex.Obj(info, after)
            for n in spec.notify:
                plan.messages.append(
                    {
                        "subject": ev.template(n.subject, obj_after, full)[:300],
                        "body": ev.template(n.body, obj_after, full)[:8000],
                        **_recipients(n, ev, obj_after, full, members),
                    }
                )
            plan.capped = cap_recipients(plan.messages, MAX_RUN_RECIPIENTS)
    return plan


def cap_recipients(messages: list[dict[str, Any]], budget: int) -> int:
    """Keep at most *budget* recipients over *messages* (in order; the rest dropped):
    the number dropped."""
    dropped = 0
    for m in messages:
        keep = max(0, min(len(m["recipients"]), budget))
        dropped += len(m["recipients"]) - keep
        m["recipients"] = m["recipients"][:keep]
        budget -= keep
    return dropped


# --- permission -------------------------------------------------------------------------


def _forbidden(permission: str, message: str, **extra: Any) -> ObjectError:
    return _err(403, "forbidden", message, permission=permission, **extra)


def _require_run(spec: ActionSpec, caller: Caller) -> None:
    need = spec.permission.min_role
    if rank(caller.role) < rank(need):
        raise _forbidden("action.run", f"this action needs the {need} role", min_role=need)


def _agent_mode(spec: ActionSpec, name: str, caller: Caller) -> str | None:
    """The effective agent mode (None: not an agent call); raises when refused."""
    if caller.via != "agent":
        return None
    if spec.agent == "none":
        raise _err(403, "agent_not_allowed", f"action {name} may not be run by agents")
    if caller.agent_mode not in ("propose", "execute"):
        raise _err(403, "scope_required", "this token may only read (scope propose or execute)")
    if caller.allowed_actions is not None and name not in caller.allowed_actions:
        raise _err(403, "action_not_allowed", f"this token may not run action {name}")
    return min(caller.agent_mode, spec.agent, key=AGENT_MODES.index)


def _approval_role(plan: _Plan, caller: Caller) -> str | None:
    mode = _agent_mode(plan.spec, plan.name, caller)
    if mode == "propose":
        spec_role = plan.spec.approval.role
        return plan.approval_role or (spec_role if spec_role != "none" else "reviewer")
    return plan.approval_role


def may_decide(run: ActionRun, caller: Caller) -> bool:
    """Whether *caller* may approve *run* (role and four-eyes; status not checked)."""
    if caller.via == "agent":
        return False
    if rank(caller.role) < rank(run.approval_role or "reviewer"):
        return False
    return not (caller.four_eyes and _own(run, caller))


def _own(run: ActionRun, caller: Caller) -> bool:
    """Whether *run* was requested by *caller*, directly or through their agent token."""
    me = caller.name.strip().lower()
    return bool(me) and me in (
        run.requested_by.strip().lower(),
        (run.requested_for or "").strip().lower(),
    )


# --- views ------------------------------------------------------------------------------


def _preview_dict(plan: _Plan, caller: Caller) -> dict[str, Any]:
    spec = plan.spec
    allowed = rank(caller.role) >= rank(spec.permission.min_role)
    refusal = ""
    approval_role = plan.approval_role
    if allowed:
        try:
            approval_role = _approval_role(plan, caller)
        except ObjectError as exc:
            allowed, refusal = False, exc.code
    out = {
        "action": plan.name,
        "label": spec.label,
        "description": spec.description,
        "version": plan.version,
        "object_type": plan.info.name,
        "object_type_label": plan.info.label,
        "key": plan.record["key"],
        "title": plan.record["title"],
        "params": plan.params,
        "preconditions": plan.preconditions,
        "effects": plan.effects,
        "approval": {
            "required": approval_role is not None,
            "role": approval_role,
            "reason": plan.approval_reason
            if approval_role == plan.approval_role
            else "agent_propose",
        },
        "notify": [
            {
                "subject": m["subject"],
                "body": m["body"],
                "to": m["to"],
                "recipients": len(m["recipients"]),
                "invalid": m["invalid"],
                "skipped_roles": m["skipped_roles"],
            }
            for m in plan.messages
        ],
        "permission": {
            "min_role": spec.permission.min_role,
            "allowed": allowed,
            **({"refused": refusal} if refusal else {}),
        },
        "ok": not plan.failures and not plan.effect_errors,
        "functions": plan.functions,
    }
    out["can_submit"] = bool(out["ok"] and allowed)
    return out


def preview(
    settings: Any,
    view: Any,
    name: str,
    key: str,
    params: Any = None,
    caller: Caller | None = None,
    *,
    when: dt.date | None = None,
) -> dict[str, Any]:
    """The preview of action *name* on object *key* (module docstring). Stores nothing."""
    caller = caller or Caller("local")
    plan = _plan(settings, view, name, key, params, when=when)
    return _preview_dict(plan, caller)


def list_actions(view: Any, type_name: str | None = None) -> list[dict[str, Any]]:
    """The active actions (of *type_name*)."""
    ctx = registry.context(view)
    wanted = ctx.schema.resolve(type_name) if type_name else None
    if type_name and wanted is None:
        raise _err(404, "unknown_object_type", f"no object type {type_name[:80]!r}")
    out = []
    for name in sorted(ctx.actions):
        if ctx.statuses.get(("action", name)) != "active":
            continue
        spec = ctx.actions[name]
        t = ctx.schema.resolve(spec.object_type)
        if t is None or (wanted is not None and t.name != wanted.name):
            continue
        out.append(
            {
                "name": name,
                "label": spec.label,
                "description": spec.description,
                "object_type": t.name,
                "object_type_label": t.label,
                "params": [p.model_dump(mode="json", exclude_none=True) for p in spec.params],
                "preconditions": [p.message for p in spec.preconditions],
                "effects": [{"set": e.set, "operational": e.operational} for e in spec.effects],
                "notifies": len(spec.notify),
                "permission": {"min_role": spec.permission.min_role},
                "approval": spec.approval.model_dump(mode="json", exclude_none=True),
                "agent": spec.agent,
            }
        )
    return out


def available(
    settings: Any,
    view: Any,
    type_name: str,
    record: dict[str, Any],
    caller: Caller,
    *,
    when: dt.date | None = None,
) -> list[dict[str, Any]]:
    """The active actions of *type_name* for one object record: ``[{name, label,
    available, reason?}]`` -- not available without the role (``reason: permission``)
    or when a precondition is false (its message; parameters at their defaults). A
    precondition that cannot be decided without parameters leaves the action available
    (the preview decides)."""
    ctx = registry.context(view)
    info = ctx.type_of(type_name)
    if info is None:
        return []
    out = []
    with ExitStack() as stack:
        for name in sorted(ctx.actions):
            spec = ctx.actions[name]
            if ctx.statuses.get(("action", name)) != "active":
                continue
            t = ctx.schema.resolve(spec.object_type)
            if t is None or t.name != info.name:
                continue
            item: dict[str, Any] = {"name": name, "label": spec.label, "available": True}
            if rank(caller.role) < rank(spec.permission.min_role):
                item.update(available=False, reason="permission")
                out.append(item)
                continue
            params = {p.name: p.default for p in spec.params}
            ev = _Evaluator(settings, view, ctx, info, spec, stack, when or functions.today())
            obj = ex.Obj(info, dict(record.get("values") or {}))
            for pre in spec.preconditions:
                try:
                    value = ev.run(pre.expr, obj, params)
                except (ex.EvalError, ObjectError):
                    continue
                if value is False:
                    item.update(available=False, reason=pre.message)
                    break
            out.append(item)
    return out


# --- submit / apply ---------------------------------------------------------------------


def _audit(
    view: Any, caller: Caller, action: str, run: ActionRun, detail: dict[str, Any] | None = None
) -> None:
    from autoontology.web.projects import audit

    body = {"action": run.action_name, "key": run.object_key, **(detail or {})}
    if caller.via in ("agent", "workflow"):
        body["via"] = caller.via
    if caller.token_id:
        body["token_id"] = caller.token_id
    try:
        audit(
            view.store,
            action,
            caller.name,
            caller.user_id,
            caller.via,
            caller.ip,
            project_id=view.id,
            target=run.id,
            detail=body,
        )
    except Exception:  # noqa: BLE001 - the run already committed
        logger.exception("audit of %s failed", run.id)


def _write_effects(view: Any, plan: _Plan, run_id: str, edited_by: str, conn: Any) -> list:
    written = []
    for e in plan.effects:
        edit = overlay.record_edit(
            view,
            plan.otype,
            plan.record["key"],
            e["property"],
            e["new_value"],
            source_row=plan.record["source"],
            action_run_id=run_id,
            edited_by=edited_by,
            operational=e["operational"],
            conn=conn,
        )
        written.append(
            {
                "property": e["property"],
                "old_value": edit.old_value,
                "new_value": edit.new_value,
                "operational": edit.operational,
                "edit_id": edit.id,
            }
        )
    return written


def _queued(plan: _Plan) -> dict[str, Any]:
    out = {
        "messages": len(plan.messages),
        "recipients": sum(len(m["recipients"]) for m in plan.messages),
        "invalid": sum(m["invalid"] for m in plan.messages),
        "skipped_roles": any(m["skipped_roles"] for m in plan.messages),
        "status": "queued" if plan.messages else "none",
    }
    if plan.capped:
        out["notifications_capped"] = plan.capped
    return out


def _notify(settings: Any, view: Any, run_id: str, plan: _Plan, mailer: Any) -> None:
    """Send the plan's messages from a daemon thread; merge the counts into the run."""
    messages = [m for m in plan.messages if m["recipients"]]
    if any(m["skipped_roles"] for m in plan.messages):
        logger.info("action run %s: role recipients skipped (accounts are off)", run_id)
    if not plan.messages:
        return
    summary = _queued(plan)

    def work() -> None:
        sent = failed = 0
        status = "sent"
        try:
            from autoontology.web.mailer import Mailer

            m = mailer if mailer is not None else Mailer.from_settings(settings)
            if not getattr(m, "enabled", False):
                status = "mail_disabled"
            else:
                for msg in messages:
                    for to in msg["recipients"]:
                        if send_one(m, to, msg["subject"], msg["body"]):
                            sent += 1
                        else:
                            failed += 1
                if failed:
                    status = "partial" if sent else "failed"
        except Exception:  # noqa: BLE001 - mail never fails a run
            logger.exception("action run %s: notification failed", run_id)
            status = "failed"
        logger.info(
            "action run %s: notifications %s (%d sent, %d failed)", run_id, status, sent, failed
        )
        try:
            view.action_runs().merge_result(
                run_id,
                {"notifications": {**summary, "status": status, "sent": sent, "failed": failed}},
            )
        except Exception:  # noqa: BLE001
            logger.exception("action run %s: recording the notifications failed", run_id)

    t = threading.Thread(target=work, name=f"notify-{run_id}", daemon=True)
    with _threads_lock:
        _threads[:] = [x for x in _threads if x.is_alive()]
        _threads.append(t)
    t.start()


def send_one(mailer: Any, to: str, subject: str, body: str) -> bool:
    """One message through *mailer*; an exception is a failed recipient, never the end of
    the remaining ones."""
    try:
        return bool(mailer.send(to, subject, body))
    except Exception:  # noqa: BLE001 - one recipient never stops the others
        logger.warning("notification to one recipient failed", exc_info=True)
        return False


def _check_idempotency(existing: ActionRun, name: str, key: str) -> ActionRun:
    if existing.action_name != name or existing.object_key != str(key):
        raise _err(
            409,
            "idempotency_conflict",
            "this idempotency key was used for another action or object",
            run_id=existing.id,
        )
    return existing


def submit(
    settings: Any,
    view: Any,
    name: str,
    key: str,
    params: Any = None,
    caller: Caller | None = None,
    *,
    idempotency_key: str | None = None,
    mailer: Any = None,
    when: dt.date | None = None,
) -> tuple[ActionRun, bool]:
    """Submit action *name* on object *key*: ``(run, created)`` -- ``created`` False when
    *idempotency_key* found an earlier run (returned as it is now)."""
    caller = caller or Caller("local")
    if idempotency_key is not None:
        idempotency_key = str(idempotency_key).strip()
        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_CHARS:
            raise _err(400, "invalid_idempotency_key", "idempotency_key is 1-200 characters")
        found = view.action_runs().by_idempotency_key(idempotency_key)
        if found is not None:
            return _check_idempotency(found, name, key), False
    runs = view.action_runs()
    for _ in range(MAX_ATTEMPTS):
        plan = _plan(settings, view, name, key, params, when=when)
        _require_run(plan.spec, caller)
        approval_role = _approval_role(plan, caller)
        if plan.failures:
            raise _err(
                409,
                "precondition_failed",
                "; ".join(f["message"] for f in plan.failures)[:500],
                failures=plan.failures,
            )
        if plan.effect_errors:
            raise _err(
                409,
                "evaluation_failed",
                "; ".join(f"{e['property']}: {e['error']['message']}" for e in plan.effect_errors)[
                    :500
                ],
                effects=plan.effect_errors,
            )
        preview_data = _preview_dict(plan, caller)
        with view.store._immediate() as conn:
            if idempotency_key is not None:
                found = runs.by_idempotency_key(idempotency_key, conn=conn)
                if found is not None:
                    return _check_idempotency(found, name, key), False
            if _marker(conn, view, plan.otype.class_local, plan.record["key"]) != plan.marker:
                continue
            common = {
                "action_name": name,
                "action_version": plan.version,
                "object_type": plan.otype.class_local,
                "object_key": plan.record["key"],
                "params": plan.params,
                "requested_by": caller.name,
                "requested_for": caller.person,
                "via": caller.via,
                "token_id": caller.token_id,
                "workflow_run_id": caller.workflow_run_id,
                "preview": preview_data,
                "idempotency_key": idempotency_key,
                "conn": conn,
            }
            if approval_role is not None:
                run = runs.create(status="pending_approval", approval_role=approval_role, **common)
            else:
                run = runs.create(status="applied", **common)
                written = _write_effects(view, plan, run.id, caller.name, conn)
                run = runs.update(
                    run.id,
                    conn=conn,
                    applied_at=now_iso(),
                    result={"effects": written, "notifications": _queued(plan)},
                )
        _audit(view, caller, "action.submit", run, {"status": run.status, "via": caller.via})
        if run.status == "applied":
            _audit(view, caller, "action.apply", run, {"effects": len(plan.effects)})
            _notify(settings, view, run.id, plan, mailer)
            _decided(view, run)
        return run, True
    raise _err(409, "object_busy", "the object changed while the action ran: try again")


def _pending(view: Any, run_id: str) -> ActionRun:
    run = view.action_runs().get(run_id)
    if run is None:
        raise _err(404, "unknown_run", f"no action run {str(run_id)[:80]!r}")
    return run


def _fail(view: Any, run: ActionRun, caller: Caller, reason: str, code: str) -> ActionRun:
    runs = view.action_runs()
    with view.store._immediate() as conn:
        current = runs.get(run.id, conn=conn)
        if current is None or current.status != "pending_approval":
            raise _err(409, "not_pending", f"the run is {current.status if current else 'gone'}")
        failed = runs.update(
            run.id,
            conn=conn,
            status="failed",
            approver=caller.name,
            decided_at=now_iso(),
            error=reason[:500],
            result={"failure": code},
        )
    _audit(view, caller, "action.fail", failed, {"reason": code})
    _decided(view, failed)
    return failed


def approve(
    settings: Any,
    view: Any,
    run_id: str,
    caller: Caller,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> ActionRun:
    """Approve a pending run: applied, or failed when it cannot apply any more."""
    run = _pending(view, run_id)
    if run.status != "pending_approval":
        raise _err(409, "not_pending", f"the run is {run.status}")
    _require_decide(run, caller)
    runs = view.action_runs()
    for _ in range(MAX_ATTEMPTS):
        try:
            plan = _plan(settings, view, run.action_name, run.object_key, run.params, when=when)
        except ObjectError as exc:
            if exc.status >= 500:
                raise
            return _fail(view, run, caller, exc.message, exc.code)
        if plan.version != run.action_version:
            return _fail(
                view,
                run,
                caller,
                f"action {run.action_name} changed (v{run.action_version} -> v{plan.version})",
                "action_changed",
            )
        submitted = run.preview.get("functions")
        if isinstance(submitted, dict):
            changes = registry.pin_changes(submitted, plan.functions)
            if changes:
                reason = "; ".join(changes) + " since the run was submitted"
                return _fail(view, run, caller, reason, "function_changed")
        if plan.failures:
            reason = "; ".join(f["message"] for f in plan.failures)
            return _fail(view, run, caller, reason, "precondition_failed")
        if plan.effect_errors:
            reason = "; ".join(e["error"]["message"] for e in plan.effect_errors)
            return _fail(view, run, caller, reason, "evaluation_failed")
        with view.store._immediate() as conn:
            current = runs.get(run.id, conn=conn)
            if current is None or current.status != "pending_approval":
                raise _err(
                    409, "not_pending", f"the run is {current.status if current else 'gone'}"
                )
            if _marker(conn, view, plan.otype.class_local, plan.record["key"]) != plan.marker:
                continue
            written = _write_effects(view, plan, run.id, run.requested_by, conn)
            stamp = now_iso()
            done = runs.update(
                run.id,
                conn=conn,
                status="applied",
                approver=caller.name,
                decided_at=stamp,
                applied_at=stamp,
                result={"effects": written, "notifications": _queued(plan)},
            )
        _audit(view, caller, "action.approve", done)
        _audit(view, caller, "action.apply", done, {"effects": len(plan.effects)})
        _notify(settings, view, done.id, plan, mailer)
        _decided(view, done)
        return done
    raise _err(409, "object_busy", "the object changed while the action ran: try again")


def _require_decide(run: ActionRun, caller: Caller) -> None:
    if caller.via == "agent":
        raise _err(403, "agent_not_allowed", "agents cannot decide approvals")
    role = run.approval_role or "reviewer"
    if rank(caller.role) < rank(role):
        raise _forbidden("action.approve", f"approving this run needs the {role} role", role=role)
    if caller.four_eyes and _own(run, caller):
        raise _err(
            403,
            "self_approval",
            "you requested this run (directly or through your agent token): someone else "
            "must approve it",
        )


def reject(view: Any, run_id: str, caller: Caller, note: str = "") -> ActionRun:
    """Reject a pending run (the requester may withdraw their own)."""
    run = _pending(view, run_id)
    if run.status != "pending_approval":
        raise _err(409, "not_pending", f"the run is {run.status}")
    if caller.via == "agent" or not _own(run, caller):
        _require_decide(run, caller)
    note = (note or "").strip()[:MAX_NOTE_CHARS]
    runs = view.action_runs()
    with view.store._immediate() as conn:
        current = runs.get(run.id, conn=conn)
        if current is None or current.status != "pending_approval":
            raise _err(409, "not_pending", f"the run is {current.status if current else 'gone'}")
        done = runs.update(
            run.id,
            conn=conn,
            status="rejected",
            approver=caller.name,
            decided_at=now_iso(),
            result={"note": note} if note else {},
        )
    _audit(view, caller, "action.reject", done, {"note": note[:200]} if note else None)
    _decided(view, done)
    return done


def _superseded(conn: Any, view: Any, run_id: str) -> list[str]:
    """Properties a later live edit changed after *run_id*'s edits."""
    rows = conn.execute(
        "SELECT DISTINCT e.property FROM object_edits e JOIN object_edits r "
        "ON r.project_id = e.project_id AND r.object_type = e.object_type "
        "AND r.object_key = e.object_key AND r.property = e.property "
        "WHERE r.project_id = ? AND r.action_run_id = ? AND r.reverted_at IS NULL "
        "AND e.reverted_at IS NULL AND e.id > r.id ORDER BY e.property",
        (view.id, run_id),
    ).fetchall()
    return [r[0] for r in rows]


def revert(
    settings: Any,  # noqa: ARG001 - the signature every transition shares
    view: Any,
    run_id: str,
    caller: Caller,
    *,
    force: bool = False,
) -> ActionRun:
    """Revert an applied run's edits (module docstring)."""
    run = _pending(view, run_id)
    if run.status != "applied":
        raise _err(409, "not_applied", f"the run is {run.status}, not applied")
    min_role = (run.preview.get("permission") or {}).get("min_role") or "editor"
    need = min_role if rank(min_role) > rank("editor") else "editor"
    if caller.via == "agent":
        raise _err(403, "agent_not_allowed", "agents cannot revert runs")
    if rank(caller.role) < rank(need):
        raise _forbidden("action.run", f"reverting this run needs the {need} role", min_role=need)
    if force and rank(caller.role) < rank("owner"):
        raise _forbidden("action.run", "forcing a revert needs the owner role", min_role="owner")
    runs = view.action_runs()
    with view.store._immediate() as conn:
        current = runs.get(run.id, conn=conn)
        if current is None or current.status != "applied":
            raise _err(409, "not_applied", f"the run is {current.status if current else 'gone'}")
        later = _superseded(conn, view, run.id)
        if later and not force:
            raise _err(
                409,
                "superseded",
                f"a later edit changed {', '.join(later)}: an owner may force the revert",
                properties=later,
            )
        reverted = overlay.revert(view, action_run_id=run.id, conn=conn)
        done = runs.update(
            run.id,
            conn=conn,
            status="reverted",
            reverted_at=now_iso(),
            result={
                **current.result,
                "reverted": {
                    "by": caller.name,
                    "edits": [e.id for e in reverted],
                    "forced": bool(force and later),
                },
            },
        )
    _audit(view, caller, "action.revert", done, {"force": bool(force and later)})
    _decided(view, done)
    return done


# --- queries ----------------------------------------------------------------------------


def pending_approvals(view: Any, caller: Caller) -> int:
    """Pending runs *caller* may approve."""
    pending = view.action_runs().list(status="pending_approval", limit=500)
    return sum(1 for r in pending if may_decide(r, caller))


def run_dict(run: ActionRun, caller: Caller | None = None) -> dict[str, Any]:
    """A run as the API shows it (``can_approve`` for *caller* while pending)."""
    out = run.to_dict()
    if caller is not None:
        out["can_approve"] = run.status == "pending_approval" and may_decide(run, caller)
        out["can_revert"] = run.status == "applied" and caller.via != "agent"
    return out
