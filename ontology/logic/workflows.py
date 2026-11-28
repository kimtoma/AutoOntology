# SPDX-License-Identifier: MIT
"""The workflow runner: go through the objects of a workflow's ``for_each`` and run its
steps (``if`` / ``action`` / ``notify``) on each, pausing an object's steps while an
action waits for approval and resuming them when it is decided.

Runs
----
:func:`run` (or :func:`start` + :func:`execute`; :func:`start_background` runs the
objects on a worker thread) fires one *active* workflow. The ``workflow_runs`` row is
inserted first, with the trigger's ``fire_slot`` (``2026-11-25T02:00`` for a schedule,
``action:<run id>`` for ``on_action``, ``manual:<id>`` for a manual run): the slot is
unique per workflow, so a duplicate firing (two schedulers, a listener and a
reconciliation) is a no-op that returns the existing run.

The objects are read page by page (the object reader's pages, ``MAX_PAGE`` rows) in key
order, the ``where`` filter is evaluated per object (an object whose filter cannot be
evaluated is left out and counted in ``where_errors``), and at most ``min(for_each.limit,
max_objects)`` matching objects are processed: when another object matches after that,
the run records ``truncated`` (also when :data:`MAX_SCAN` rows were read). An
``on_action`` run processes only the object the action ran on (when its type is the
workflow's ``for_each`` type and ``where`` holds).

Steps, per object, in order: ``if`` evaluates its condition (true: ``then``, else
``else``); ``action`` maps its parameters from expressions and submits the action with
an idempotency key derived from (workflow run id, step path, object key), so retrying
never applies an action twice (but see "Pending elsewhere" below); ``notify`` renders the message like an action's
notification and sends it (role recipients need accounts). After an applied action the
object is read again, so later steps see its new values.

Object outcomes: ``done`` (every step ran), ``paused`` (an action waits for approval),
``skipped`` (an action's precondition does not hold, its approval was rejected, the
same request already waits for approval, or the workflow changed while the object was
paused) and ``failed`` (an expression or an action
failed: that object's remaining steps do not run; other objects go on).

Statuses
--------
``running`` while the objects are processed; then ``paused`` while objects wait for
approvals; finally ``succeeded`` -- or ``failed`` when any object failed or the run
itself could not go on (``error``: the workflow is not active, its activator lost the
owner role, a source cannot be read, or the service stopped mid-run). ``cancelled`` by
:func:`cancel`: the runner stops before the next object; paused objects are dropped and
their pending action runs are left as they are (decide them in the approvals list; a
decision no longer resumes anything).

``summary`` counts ``matched``, ``completed``, ``skipped``, ``failed``,
``actions_applied``, ``pending_approval`` (objects waiting now), ``already_pending``
(objects skipped because the same request already waits for approval; also counted in
``skipped``), ``notified`` (notify steps that ran), ``where_errors``, ``scanned`` and ``truncated``; ``objects`` keeps the
detail of the first :data:`MAX_DETAIL_OBJECTS` objects (their steps, outcome and reason).

Pause and resume
----------------
An action that comes back ``pending_approval`` stops the object's steps; the run stores
a continuation (object key + the action step's path) under the action run's id. The
:func:`install`-ed listener of :func:`ontology.logic.actions.on_run_decided` resumes
it when the action run is decided: ``applied`` continues with the steps after the
action (an enclosing ``if`` is not evaluated again); ``rejected`` skips the object and
``failed`` fails it. A continuation is claimed (moved to ``resuming``) atomically before
it runs, so the listener and :func:`reconcile` never resume it twice. :func:`reconcile`
(run by the scheduler on every tick, and at startup) polls the action runs of paused
continuations -- approvals made by another process or while the service was down are
picked up -- fails continuations and runs interrupted mid-way, and finishes runs with
nothing left to wait for.

Who acts
--------
A workflow acts with the role of the user who activated it: the newest ``active`` entry
of its history not written by the system. With accounts on that user must still exist,
be enabled and be an owner of the project (an admin, or the trusted CLI ``cli``) when
the run starts and whenever a paused object resumes -- otherwise the run (or that
object) fails with the reason. With accounts off the role is owner. Action runs carry
``requested_by: workflow:<name>``, ``via: workflow`` and the ``workflow_run_id``; the
audit log records ``workflow.run``, ``workflow.finish`` and ``workflow.cancel``.

Pending elsewhere
-----------------
The idempotency key only de-duplicates within one run, so a daily workflow would ask
again every day while yesterday's request still waits (a guard such as
``coalesce(독촉단계, 0) < 4`` only holds once the approval is applied). Before an action
step submits, the runner therefore looks for a ``pending_approval`` run of the same action
on the same object in the project -- from any requester, any workflow run or a manual
submit -- whose parameters equal the step's (both normalised by the action's parameter
coercion, compared as JSON). If there is one, nothing is submitted: the object is
``skipped`` with reason ``already_pending: <action run id>``, its step entry records
``result: already_pending`` and the existing ``action_run_id``, and the run counts it in
``already_pending``. The object does not pause and is not linked to the other run: when
that run is decided, only the run that created it resumes; the next scheduled run sees the
applied (or rejected) outcome. Different parameters submit as usual. Manual, UI and agent
submits are not affected. A retry of the same run (same idempotency key) is not treated as
pending elsewhere: it returns its own run as before.

``on_action``
-------------
When an action is applied (any surface) and ``ONTOLOGY_WORKFLOWS`` is on, every
active workflow with ``trigger.on_action`` naming it starts for that object. A
workflow-triggered action may trigger further workflows up to :data:`MAX_DEPTH` levels;
deeper ones are recorded as failed runs (``depth_limit``) and do not run. Spec
validation already refuses a workflow that runs the action it is triggered by.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
import threading
from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ontology.logic import actions, functions, registry
from ontology.logic import expr as ex
from ontology.logic.actions import Caller
from ontology.logic.schema import TypeInfo
from ontology.logic.specs import LogicContext, StepSpec, WorkflowSpec
from ontology.objects import reader, service
from ontology.objects.reader import ObjectError
from ontology.store.actions import ActionRun
from ontology.store.db import now_iso, root_store
from ontology.store.workflows import OPEN, UNCHANGED, RunActive, WorkflowRun

logger = logging.getLogger("ontology.workflows")

#: objects whose detail (steps, outcome) a run keeps; the rest are only counted
MAX_DETAIL_OBJECTS = 200
#: step entries kept per object
MAX_DETAIL_STEPS = 60
#: workflow runs a chain of on_action triggers may nest
MAX_DEPTH = 3
#: source rows one run reads at most while filtering
MAX_SCAN = 50_000
#: a continuation being resumed, or a running run without a heartbeat, for this long
#: was interrupted (the process stopped)
STALE_SECONDS = 15 * 60
#: recipients the notify steps of one run reach at most, over all its objects (the rest
#: are dropped and counted in ``notifications_capped``; actions have their own cap,
#: :data:`ontology.logic.actions.MAX_RUN_RECIPIENTS`)
MAX_RUN_RECIPIENTS = 500
COUNTERS = (
    "matched",
    "completed",
    "skipped",
    "failed",
    "actions_applied",
    "pending_approval",
    "already_pending",
    "notified",
    "where_errors",
    "scanned",
)
DECIDED = ("applied", "rejected", "failed", "reverted")


def _err(status: int, code: str, message: str, **extra: Any) -> ObjectError:
    return ObjectError(status, code, message, **extra)


# --- worker threads -----------------------------------------------------------------------

_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()
#: workflow run ids executing in this process (reconcile leaves their heartbeat alone)
_active: set[str] = set()


def _dispatch(target: Callable[[], Any], name: str) -> threading.Thread:
    def work() -> None:
        try:
            target()
        except Exception:  # noqa: BLE001 - a worker never takes the service down
            logger.exception("workflow worker %s failed", name)

    t = threading.Thread(target=work, name=name, daemon=True)
    with _threads_lock:
        _threads[:] = [x for x in _threads if x.is_alive()]
        _threads.append(t)
    t.start()
    return t


def wait(timeout: float = 60.0) -> None:
    """Join the worker threads started so far (and those they start), for tests and the
    CLI."""
    while True:
        with _threads_lock:
            pending = [t for t in _threads if t.is_alive()]
            _threads[:] = pending
        if not pending:
            return
        for t in pending:
            t.join(timeout)
        if any(t.is_alive() for t in pending):
            return


# --- the listener ---------------------------------------------------------------------------


@dataclass
class _Config:
    settings: Any
    mailer: Any = None


_config: _Config | None = None


def install(settings: Any, mailer: Any = None) -> None:
    """Resume paused objects and start ``on_action`` workflows from action decisions
    (the service and the CLI call this once; the latest settings win)."""
    global _config
    _config = _Config(settings, mailer)
    actions.on_run_decided(_on_decided)


def uninstall() -> None:
    global _config
    _config = None
    actions.remove_listener(_on_decided)


def _on_decided(view: Any, run: ActionRun) -> None:
    cfg = _config
    if cfg is None:
        return
    # an action the runner submitted and that applied at once was never paused
    if run.workflow_run_id and run.decided_at and run.status in DECIDED:
        _dispatch(
            lambda: resume(cfg.settings, view, run.id, mailer=cfg.mailer),
            f"workflow-resume-{run.id}",
        )
    if run.status == "applied" and _enabled(cfg.settings) and _triggered_by(view, run.action_name):
        _dispatch(
            lambda: on_action(cfg.settings, view, run, mailer=cfg.mailer),
            f"workflow-on-action-{run.id}",
        )


def _enabled(settings: Any) -> bool:
    return getattr(settings, "workflows", "on") == "on"


def _triggered_by(view: Any, action_name: str) -> bool:
    for d in view.logic_defs().list("workflow", "active"):
        if (d.spec.get("trigger") or {}).get("on_action") == action_name:
            return True
    return False


# --- who acts -------------------------------------------------------------------------------


def activation(view: Any, name: str) -> tuple[str, str] | None:
    """``(activator, activated_at)`` of workflow *name*: the newest ``active`` entry of its
    history not written by the system (None: never activated by someone)."""
    for row in view.logic_defs().history("workflow", name):
        if row["status"] == "active" and row["changed_by"] not in ("", registry.SYSTEM_ACTOR):
            return row["changed_by"], row["changed_at"]
    return None


def caller_for(settings: Any, view: Any, name: str, run_id: str | None) -> Caller:
    """The :class:`Caller` workflow *name* acts as (module docstring); 409
    ``activator_not_allowed`` when its activator may not run it any more."""
    found = activation(view, name)
    if found is None:
        raise _err(409, "activator_not_allowed", f"workflow {name} has no recorded activator")
    activator = found[0]
    if settings.accounts_enabled and activator != "cli":
        from ontology.store.accounts import Accounts

        accounts = Accounts(view.store)
        user = accounts.by_email(activator)
        if user is None or not user.active:
            raise _err(
                409,
                "activator_not_allowed",
                f"{activator}, who activated workflow {name}, is not an active user: "
                "an owner must activate it again",
            )
        role = "owner" if user.is_admin else accounts.role_of(view.id, user.id)
        if role != "owner":
            raise _err(
                409,
                "activator_not_allowed",
                f"{activator}, who activated workflow {name}, is no longer an owner of the "
                "project: an owner must activate it again",
            )
    return Caller(
        name=f"workflow:{name}",
        role="owner",
        four_eyes=bool(settings.accounts_enabled),
        via="workflow",
        workflow_run_id=run_id,
    )


# --- audit ----------------------------------------------------------------------------------


def _audit(
    view: Any,
    action: str,
    run: WorkflowRun,
    detail: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
    user_id: str | None = None,
    via: str = "workflow",
    ip: str = "",
) -> None:
    from ontology.web.projects import audit

    body = {"workflow": run.workflow_name, **(detail or {})}
    try:
        audit(
            view.store,
            action,
            actor or f"workflow:{run.workflow_name}",
            user_id,
            via,
            ip,
            project_id=view.id,
            target=run.id,
            detail=body,
        )
    except Exception:  # noqa: BLE001 - the run already committed
        logger.exception("audit of %s failed", run.id)


# --- definitions --------------------------------------------------------------------------


@dataclass
class _Env:
    """What every object of one run needs."""

    settings: Any
    view: Any
    ctx: LogicContext
    spec: WorkflowSpec
    info: TypeInfo
    caller: Caller
    run_id: str
    when: dt.date
    mailer: Any = None
    members: list[dict[str, Any]] | None = None
    _members_loaded: bool = False

    def evaluator(self, stack: ExitStack) -> actions._Evaluator:
        # the action runner's evaluator: a workflow's expressions have no parameters
        return actions._Evaluator(
            self.settings,
            self.view,
            self.ctx,
            self.info,
            SimpleNamespace(params=[]),  # type: ignore[arg-type]
            stack,
            self.when,
        )

    def recipients_members(self) -> list[dict[str, Any]] | None:
        if not self._members_loaded:
            self.members = actions._members(self.view, self.settings)
            self._members_loaded = True
        return self.members


def _active_workflow(ctx: LogicContext, view: Any, name: str) -> tuple[WorkflowSpec, int]:
    spec = ctx.workflows.get(name) if name and len(name) <= 200 else None
    if spec is None:
        raise _err(404, "unknown_workflow", f"no workflow {str(name)[:80]!r}")
    status = ctx.statuses.get(("workflow", name))
    if status != "active":
        raise _err(409, "workflow_not_active", f"workflow {name} is {status}, not active")
    d = view.logic_defs().get("workflow", name)
    return spec, d.version if d is not None else 1


def _env(
    settings: Any,
    view: Any,
    run: WorkflowRun,
    *,
    mailer: Any,
    when: dt.date | None,
) -> _Env:
    """The run's environment now: 409 when the workflow is no longer the active version
    that started the run, its object type is not active or its activator lost the role."""
    ctx = registry.context(view)
    spec, version = _active_workflow(ctx, view, run.workflow_name)
    registry.check_pins(view, "workflow", run.workflow_name, ctx)
    if version != run.workflow_version:
        raise _err(
            409,
            "workflow_changed",
            f"workflow {run.workflow_name} changed (v{run.workflow_version} -> v{version})",
        )
    info = ctx.type_of(spec.for_each.object_type)
    if info is None:
        raise _err(
            409, "object_type_not_active", f"the object type of {run.workflow_name} is not active"
        )
    caller = caller_for(settings, view, run.workflow_name, run.id)
    if mailer is None and _config is not None:
        mailer = _config.mailer
    return _Env(settings, view, ctx, spec, info, caller, run.id, when or functions.today(), mailer)


# --- steps ----------------------------------------------------------------------------------

Path = list[list[Any]]  # [["steps", 0], ["then", 1], ...]


def path_text(path: Path) -> str:
    return ".".join(f"{branch}[{index}]" for branch, index in path)


def idempotency_key(run_id: str, path: Path, key: str) -> str:
    digest = hashlib.sha256(f"{path_text(path)}\x00{key}".encode()).hexdigest()[:40]
    return f"wf:{run_id}:{digest}"


@dataclass
class _Object:
    """One object going through the steps."""

    key: str
    title: str
    obj: ex.Obj
    status: str = "done"
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    paused: dict[str, Any] | None = None
    applied: int = 0
    notified: int = 0
    already_pending: int = 0

    def note(self, entry: dict[str, Any]) -> None:
        if len(self.steps) < MAX_DETAIL_STEPS:
            self.steps.append(entry)

    def stop(self, status: str, reason: str) -> bool:
        self.status, self.reason = status, reason[:500]
        return False


def _reread(env: _Env, o: _Object) -> None:
    otype = service.readable_type(env.view, env.info.name)
    record = functions._records(env.settings, env.view, otype, [o.key])[0]
    o.obj = ex.Obj(env.info, dict(record["values"]))


def _send(env: _Env, messages: list[dict[str, Any]]) -> dict[str, Any]:
    sent = failed = 0
    status = "sent"
    if not any(m["recipients"] for m in messages):
        return {"status": "none", "sent": 0, "failed": 0}
    try:
        from ontology.web.mailer import Mailer

        m = env.mailer if env.mailer is not None else Mailer.from_settings(env.settings)
        if not getattr(m, "enabled", False):
            return {"status": "mail_disabled", "sent": 0, "failed": 0}
        for msg in messages:
            for to in msg["recipients"]:
                if actions.send_one(m, to, msg["subject"], msg["body"]):
                    sent += 1
                else:
                    failed += 1
        if failed:
            status = "partial" if sent else "failed"
    except Exception:  # noqa: BLE001 - mail never fails a workflow
        logger.exception("workflow run %s: notification failed", env.run_id)
        status = "failed"
    return {"status": status, "sent": sent, "failed": failed}


def _pending_elsewhere(
    env: _Env, name: str, key: str, params: dict[str, Any], own_key: str
) -> ActionRun | None:
    """A ``pending_approval`` run of action *name* on object *key* with the same
    (normalised) parameters, other than this step's own earlier submit; None when there is
    none or the parameters cannot be normalised (the submit then reports why)."""
    spec = env.ctx.actions.get(name)
    if spec is None:
        return None
    try:
        wanted = _canonical(functions._params(spec, actions._check_params(params)))  # type: ignore[arg-type]
    except ObjectError:
        return None
    for found in env.view.action_runs().list(
        status="pending_approval", action=name, object_key=str(key), limit=500
    ):
        if found.idempotency_key == own_key:
            return None  # a retry of this very step: submit returns it
        if _canonical(found.params) == wanted:
            return found
    return None


def _canonical(params: Any) -> str:
    return json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)


def _step_action(env: _Env, ev: Any, o: _Object, step: StepSpec, path: Path) -> bool:
    entry: dict[str, Any] = {"path": path_text(path), "kind": "action", "action": step.action}
    try:
        params = {k: ex.to_json(ev.run(v, o.obj, {})) for k, v in step.params.items()}
    except (ex.EvalError, ObjectError) as exc:
        o.note({**entry, "result": "failed", "error": exc.message})
        return o.stop("failed", f"{path_text(path)}: {exc.message}")
    entry["params"] = params
    key = idempotency_key(env.run_id, path, o.key)
    waiting = _pending_elsewhere(env, str(step.action), o.key, params, key)
    if waiting is not None:
        o.already_pending += 1
        o.note({**entry, "result": "already_pending", "action_run_id": waiting.id})
        return o.stop(
            "skipped", f"already_pending: {step.action} waits for approval in {waiting.id}"
        )
    try:
        run, _ = actions.submit(
            env.settings,
            env.view,
            str(step.action),
            o.key,
            params,
            env.caller,
            idempotency_key=key,
            mailer=env.mailer,
            when=env.when,
        )
    except ObjectError as exc:
        status = "skipped" if exc.code == "precondition_failed" else "failed"
        o.note({**entry, "result": status, "error": exc.message, "code": exc.code})
        return o.stop(status, f"{step.action}: {exc.message}")
    entry["action_run_id"] = run.id
    if run.status == "applied":
        o.applied += 1
        o.note({**entry, "result": "applied"})
        _reread(env, o)
        return True
    if run.status == "pending_approval":
        o.note({**entry, "result": "pending_approval", "approval_role": run.approval_role})
        o.status = "paused"
        o.paused = {"action_run_id": run.id, "path": path}
        return False
    # an earlier submit with the same key that was decided meanwhile
    o.note({**entry, "result": run.status})
    if run.status == "rejected":
        return o.stop("skipped", f"{step.action}: the approval was rejected")
    return o.stop("failed", f"{step.action}: {run.error or run.status}")


def _step_notify(env: _Env, ev: Any, o: _Object, step: StepSpec, path: Path) -> bool:
    n = step.notify
    assert n is not None
    message = {
        "subject": ev.template(n.subject, o.obj, {})[:300],
        "body": ev.template(n.body, o.obj, {})[:8000],
        **actions._recipients(n, ev, o.obj, {}, env.recipients_members()),
    }
    capped = _reserve(env, message)
    outcome = _send(env, [message])
    if capped:
        outcome["capped"] = capped
    o.notified += 1
    o.note(
        {
            "path": path_text(path),
            "kind": "notify",
            "subject": message["subject"],
            "recipients": len(message["recipients"]),
            "invalid": message["invalid"],
            "skipped_roles": message["skipped_roles"],
            **outcome,
        }
    )
    return True


def _reserve(env: _Env, message: dict[str, Any]) -> int:
    """Take *message*'s recipients from the run's budget (:data:`MAX_RUN_RECIPIENTS`,
    kept in ``summary.recipients_notified``): drops those over it, returns how many."""
    wanted = len(message["recipients"])
    if not wanted:
        return 0

    def take(run: WorkflowRun) -> Any:
        used = int(run.summary.get("recipients_notified") or 0)
        allowed = max(0, min(wanted, MAX_RUN_RECIPIENTS - used))
        run.summary["recipients_notified"] = used + allowed
        if allowed < wanted:
            run.summary["notifications_capped"] = (
                int(run.summary.get("notifications_capped") or 0) + wanted - allowed
            )
        return allowed

    _, allowed = env.view.workflow_runs().mutate(env.run_id, take)
    allowed = int(allowed or 0)
    message["recipients"] = message["recipients"][:allowed]
    return wanted - allowed


def _run_steps(
    env: _Env, ev: Any, o: _Object, steps: list[StepSpec], parent: Path, branch: str, start: int
) -> bool:
    """Run ``steps[start:]``; False when the object stopped (paused, skipped, failed)."""
    for i in range(start, len(steps)):
        step = steps[i]
        path = [*parent, [branch, i]]
        if step.if_ is not None:
            try:
                value = ev.run(step.if_, o.obj, {})
            except (ex.EvalError, ObjectError) as exc:
                o.note({"path": path_text(path), "kind": "if", "error": exc.message})
                return o.stop("failed", f"{path_text(path)}: {exc.message}")
            taken = value is True
            o.note({"path": path_text(path), "kind": "if", "value": taken})
            nested = step.then if taken else step.else_
            if not _run_steps(env, ev, o, nested, path, "then" if taken else "else", 0):
                return False
        elif step.action is not None:
            if not _step_action(env, ev, o, step, path):
                return False
        elif step.notify is not None:
            _step_notify(env, ev, o, step, path)
    return True


def _resume_steps(env: _Env, ev: Any, o: _Object, path: Path) -> bool:
    """Continue after the step at *path*: its later siblings, then those of every
    enclosing step, outermost last."""
    bad = _err(409, "workflow_changed", f"unknown step path {path_text(path)}")
    frames: list[tuple[list[StepSpec], Path, str, int]] = []
    steps: list[StepSpec] = env.spec.steps
    owner: StepSpec | None = None
    parent: Path = []
    for depth, (branch, raw) in enumerate(path):
        index = int(raw)
        if depth == 0:
            if branch != "steps":
                raise bad
        elif owner is not None and branch in ("then", "else"):
            steps = owner.then if branch == "then" else owner.else_
        else:
            raise bad
        if not 0 <= index < len(steps):
            raise bad
        frames.append((steps, parent, branch, index))
        owner = steps[index]
        parent = [*parent, [branch, index]]
    if not frames:
        raise bad
    for steps, parent, branch, index in reversed(frames):
        if not _run_steps(env, ev, o, steps, parent, branch, index + 1):
            return False
    return True


# --- recording ------------------------------------------------------------------------------


def _counts(run: WorkflowRun) -> dict[str, Any]:
    for name in COUNTERS:
        run.summary.setdefault(name, 0)
    return run.summary


def _finish_check(run: WorkflowRun) -> str | None:
    """Move an open run on when nothing is left: ``paused`` / ``succeeded`` / ``failed``.
    Returns the final status when it finished now."""
    if run.status not in OPEN or not run.summary.get("iterated"):
        return None
    paused = run.state.get("paused") or {}
    resuming = run.state.get("resuming") or {}
    run.summary["pending_approval"] = len(paused) + len(resuming)
    if paused or resuming:
        run.status = "paused"
        return None
    run.status = "failed" if (run.summary.get("failed") or run.error) else "succeeded"
    run.finished_at = now_iso()
    return run.status


def _detail(o: _Object) -> dict[str, Any]:
    out: dict[str, Any] = {"key": o.key, "title": o.title, "status": o.status, "steps": o.steps}
    if o.reason:
        out["reason"] = o.reason
    return out


def _record(
    view: Any, run_id: str, o: _Object, *, resumed: str | None = None
) -> WorkflowRun | None:
    """Write one object's outcome into the run (``resumed``: the continuation it came
    from) and finish the run when nothing is left."""
    finished: list[str] = []

    def change(run: WorkflowRun) -> None:
        s = _counts(run)
        paused = run.state.setdefault("paused", {})
        resuming = run.state.setdefault("resuming", {})
        objects = run.state.setdefault("objects", [])
        if resumed is None:
            s["matched"] += 1
        else:
            resuming.pop(resumed, None)
        if o.status in ("done", "skipped", "failed"):
            s["completed" if o.status == "done" else o.status] += 1
        s["actions_applied"] += o.applied
        s["already_pending"] = s.get("already_pending", 0) + o.already_pending
        s["notified"] += o.notified
        if o.paused is not None and run.status in OPEN:
            paused[o.paused["action_run_id"]] = {
                "key": o.key,
                "title": o.title,
                "path": o.paused["path"],
            }
        elif o.paused is not None:  # cancelled meanwhile: the continuation is dropped
            s["dropped"] = s.get("dropped", 0) + 1
        existing = next((d for d in objects if d.get("key") == o.key), None)
        if existing is not None and resumed is not None:
            existing["status"] = o.status
            existing["steps"] = (existing.get("steps") or []) + o.steps
            existing["steps"] = existing["steps"][:MAX_DETAIL_STEPS]
            if o.reason:
                existing["reason"] = o.reason
            else:
                existing.pop("reason", None)
        elif resumed is None and len(objects) < MAX_DETAIL_OBJECTS:
            objects.append(_detail(o))
        s["pending_approval"] = len(paused) + len(resuming)
        s["heartbeat"] = now_iso()
        status = _finish_check(run)
        if status:
            finished.append(status)

    run, _ = view.workflow_runs().mutate(run_id, change)
    if run is not None and finished:
        _audit(view, "workflow.finish", run, _finish_detail(run))
    return run


def _finish_detail(run: WorkflowRun) -> dict[str, Any]:
    keep = ("matched", "completed", "skipped", "failed", "actions_applied", "notified")
    return {"status": run.status, **{k: run.summary.get(k, 0) for k in keep}}


def _update(view: Any, run_id: str, change: Callable[[WorkflowRun], Any]) -> WorkflowRun | None:
    """A change to the run followed by the finish check (audited when it finished)."""
    finished: list[str] = []

    def wrapped(run: WorkflowRun) -> Any:
        result = change(run)
        if result is UNCHANGED:
            return UNCHANGED
        _counts(run)
        status = _finish_check(run)
        if status:
            finished.append(status)
        return result

    run, _ = view.workflow_runs().mutate(run_id, wrapped)
    if run is not None and finished:
        _audit(view, "workflow.finish", run, _finish_detail(run))
    return run


def _fail_run(view: Any, run_id: str, reason: str) -> WorkflowRun | None:
    def change(run: WorkflowRun) -> Any:
        if run.status not in OPEN:
            return UNCHANGED
        run.error = reason[:500]
        run.summary["iterated"] = True
        return None

    return _update(view, run_id, change)


# --- starting and executing -----------------------------------------------------------------


def start(
    settings: Any,
    view: Any,
    name: str,
    *,
    trigger: str,
    fire_slot: str,
    started_by: str,
    object_key: str | None = None,
    depth: int = 1,
    user_id: str | None = None,
    via: str = "workflow",
    ip: str = "",
) -> tuple[WorkflowRun, bool]:
    """Insert the run of active workflow *name* for *fire_slot*: ``(run, created)``
    (``created`` False: the slot already has a run, returned as it is). 404
    ``unknown_workflow``, 409 ``workflow_not_active``, ``function_changed`` (a function
    it uses is not the pinned version), ``project_archived``, and for a manual run
    ``run_active`` while another run of it is ``running``."""
    if archived(view):
        raise _err(409, "project_archived", f"project {view.project.slug!r} is archived")
    ctx = registry.context(view)
    _, version = _active_workflow(ctx, view, name)
    registry.check_pins(view, "workflow", name, ctx)
    summary: dict[str, Any] = {k: 0 for k in COUNTERS}
    summary.update(depth=depth, truncated=False, iterated=False, heartbeat=now_iso())
    if object_key is not None:
        summary["object_key"] = str(object_key)
    try:
        run, created = view.workflow_runs().start(
            workflow_name=name,
            workflow_version=version,
            trigger=trigger,
            fire_slot=fire_slot,
            started_by=started_by,
            summary=summary,
            state={"objects": [], "paused": {}, "resuming": {}},
            exclusive=trigger == "manual",
        )
    except RunActive as exc:
        raise _err(
            409,
            "run_active",
            f"workflow {name} is running ({exc.run_id}): wait for it to finish or cancel it",
            run_id=exc.run_id,
        ) from exc
    if created:
        detail = {"trigger": trigger, "fire_slot": fire_slot, "version": version}
        actor = started_by if trigger == "manual" else None
        _audit(view, "workflow.run", run, detail, actor=actor, user_id=user_id, via=via, ip=ip)
    return run, created


def archived(view: Any) -> bool:
    """Whether *view*'s project is archived now (read from the store, not the view's
    copy)."""
    found = view.store.get_project(view.id)
    return bool(found is not None and found.archived)


def manual_slot() -> str:
    return "manual:" + now_iso() + ":" + secrets.token_hex(3)


def _cancelled(view: Any, run_id: str) -> bool:
    current = view.workflow_runs().get(run_id)
    return current is None or current.status == "cancelled"


def _process(env: _Env, record: dict[str, Any]) -> None:
    o = _Object(
        str(record["key"]), str(record.get("title") or ""), ex.Obj(env.info, dict(record["values"]))
    )
    try:
        with ExitStack() as stack:
            ev = env.evaluator(stack)
            _run_steps(env, ev, o, env.spec.steps, [], "steps", 0)
    except ObjectError as exc:
        o.stop("failed", exc.message)
    except Exception as exc:  # noqa: BLE001 - one object never stops the run
        logger.exception("workflow run %s: object %s failed", env.run_id, o.key)
        o.stop("failed", f"internal error ({type(exc).__name__})")
    _record(env.view, env.run_id, o)


def _matches(env: _Env, record: dict[str, Any]) -> tuple[bool, str]:
    where = env.spec.for_each.where
    if where is None:
        return True, ""
    try:
        with ExitStack() as stack:
            value = env.evaluator(stack).run(where, ex.Obj(env.info, dict(record["values"])), {})
    except (ex.EvalError, ObjectError) as exc:
        return False, exc.message
    return value is True, ""


def _objects(env: _Env, object_key: str | None) -> Any:
    """The records to go through: ``(record, None)`` pages, or a single record."""
    otype = service.readable_type(env.view, env.info.name)
    if object_key is not None:
        yield from functions._records(env.settings, env.view, otype, [object_key])
        return
    offset = 0
    while True:
        page = service.search(
            env.settings, env.view, otype.class_local, limit=reader.MAX_PAGE, offset=offset
        )
        yield from page["items"]
        if len(page["items"]) < reader.MAX_PAGE:
            return
        offset += reader.MAX_PAGE


def _session(view: Any) -> ExitStack:
    """Connection reuse for one run: the store's (:meth:`Store.session`; a run makes
    dozens of store calls per object). Every store call is still its own transaction."""
    with ExitStack() as stack:
        session = getattr(root_store(view), "session", None)
        stack.enter_context(session() if callable(session) else nullcontext())
        return stack.pop_all()


def execute(
    settings: Any,
    view: Any,
    run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    """Go through the objects of a ``running`` run that has not iterated yet."""
    with _session(view):
        return _execute(settings, view, run_id, mailer=mailer, when=when)


def _execute(
    settings: Any,
    view: Any,
    run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    run = view.workflow_runs().get(run_id)
    if run is None or run.status != "running" or run.summary.get("iterated"):
        return run
    _active.add(run_id)
    try:
        try:
            env = _env(settings, view, run, mailer=mailer, when=when)
        except ObjectError as exc:
            return _fail_run(view, run_id, exc.message)
        if run.summary.get("depth", 1) > MAX_DEPTH:
            return _fail_run(
                view, run_id, f"depth_limit: on_action chains nest at most {MAX_DEPTH} deep"
            )
        cap = min(env.spec.for_each.limit or env.spec.max_objects, env.spec.max_objects)
        matched = scanned = where_errors = 0
        truncated: str | None = None
        first_where_error = ""
        try:
            for record in _objects(env, run.summary.get("object_key")):
                if _cancelled(view, run_id):
                    break
                if scanned >= MAX_SCAN:
                    truncated = "scan_limit"
                    break
                scanned += 1
                ok, problem = _matches(env, record)
                if problem:
                    where_errors += 1
                    first_where_error = first_where_error or f"{record.get('key')}: {problem}"
                if not ok:
                    continue
                if matched >= cap:
                    truncated = "max_objects"
                    break
                matched += 1
                _process(env, record)
        except ObjectError as exc:
            error = exc.message
        else:
            error = None

        def done(r: WorkflowRun) -> Any:
            if r.status == "cancelled":
                r.summary.update(scanned=scanned, iterated=True)
                return None
            r.summary.update(
                scanned=scanned,
                where_errors=where_errors,
                truncated=bool(truncated),
                iterated=True,
            )
            if truncated:
                r.summary["truncated_by"] = truncated
            if first_where_error:
                r.summary["first_where_error"] = first_where_error[:300]
            if error:
                r.error = error[:500]
            return None

        _update(view, run_id, done)
    finally:
        _active.discard(run_id)
    # an approval decided before its continuation was stored
    reconcile_run(settings, view, run_id, mailer=mailer, when=when)
    return view.workflow_runs().get(run_id)


def run(
    settings: Any,
    view: Any,
    name: str,
    *,
    trigger: str,
    fire_slot: str,
    started_by: str,
    mailer: Any = None,
    when: dt.date | None = None,
    object_key: str | None = None,
    depth: int = 1,
    user_id: str | None = None,
    via: str = "workflow",
    ip: str = "",
) -> WorkflowRun:
    """Fire workflow *name* and go through its objects in this thread (module
    docstring). A duplicate *fire_slot* returns the existing run untouched."""
    with _session(view):
        created_run, created = start(
            settings,
            view,
            name,
            trigger=trigger,
            fire_slot=fire_slot,
            started_by=started_by,
            object_key=object_key,
            depth=depth,
            user_id=user_id,
            via=via,
            ip=ip,
        )
        if not created:
            return created_run
        return execute(settings, view, created_run.id, mailer=mailer, when=when) or created_run


def start_background(
    settings: Any,
    view: Any,
    name: str,
    *,
    trigger: str,
    fire_slot: str,
    started_by: str,
    mailer: Any = None,
    when: dt.date | None = None,
    **kw: Any,
) -> tuple[WorkflowRun, bool]:
    """:func:`start`, then :func:`execute` on a worker thread (when the run is new)."""
    created_run, created = start(
        settings, view, name, trigger=trigger, fire_slot=fire_slot, started_by=started_by, **kw
    )
    if created:
        _dispatch(
            lambda: execute(settings, view, created_run.id, mailer=mailer, when=when),
            f"workflow-{created_run.id}",
        )
    return created_run, created


def on_action(
    settings: Any, view: Any, action_run: ActionRun, *, mailer: Any = None
) -> list[WorkflowRun]:
    """Start the active workflows triggered by applied *action_run* (module docstring)."""
    if not _enabled(settings) or action_run.status != "applied" or archived(view):
        return []
    depth = 1
    if action_run.workflow_run_id:
        parent = view.workflow_runs().get(action_run.workflow_run_id)
        depth = int((parent.summary.get("depth") if parent else 0) or 0) + 1
    ctx = registry.context(view)
    started = []
    for name in sorted(ctx.workflows):
        spec = ctx.workflows[name]
        if ctx.statuses.get(("workflow", name)) != "active":
            continue
        if spec.trigger.on_action != action_run.action_name:
            continue
        info = ctx.type_of(spec.for_each.object_type)
        if info is None or info.name != action_run.object_type:
            continue
        try:
            started.append(
                run(
                    settings,
                    view,
                    name,
                    trigger="on_action",
                    fire_slot=f"action:{action_run.id}",
                    started_by=f"action:{action_run.id}",
                    object_key=action_run.object_key,
                    depth=depth,
                    mailer=mailer,
                )
            )
        except ObjectError as exc:
            logger.warning("workflow %s on %s: %s", name, action_run.id, exc.message)
    return started


# --- resume ---------------------------------------------------------------------------------


def resume(
    settings: Any,
    view: Any,
    action_run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    """Resume the object paused on *action_run_id* once that run is decided (module
    docstring). Returns the workflow run, or None when nothing waited on it."""
    with _session(view):
        return _resume(settings, view, action_run_id, mailer=mailer, when=when)


def _resume(
    settings: Any,
    view: Any,
    action_run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    action_run = view.action_runs().get(action_run_id)
    if action_run is None or not action_run.workflow_run_id:
        return None
    if action_run.status not in DECIDED:
        return None
    run_id = action_run.workflow_run_id

    def claim(run: WorkflowRun) -> Any:
        paused = run.state.get("paused") or {}
        entry = paused.get(action_run.id)
        if entry is None or run.status not in OPEN:
            return UNCHANGED
        paused.pop(action_run.id)
        run.state["paused"] = paused
        run.state.setdefault("resuming", {})[action_run.id] = {**entry, "since": now_iso()}
        return entry

    run, entry = view.workflow_runs().mutate(run_id, claim)
    if run is None or not entry:
        return None
    o = _Object(str(entry["key"]), str(entry.get("title") or ""), ex.Obj(None, {}))  # type: ignore[arg-type]
    where = {"path": path_text(entry["path"]), "kind": "action", "action": action_run.action_name}
    where["action_run_id"] = action_run.id
    if action_run.status == "rejected":
        o.note({**where, "result": "rejected"})
        o.stop("skipped", f"{action_run.action_name}: the approval was rejected")
    elif action_run.status == "failed":
        o.note({**where, "result": "failed", "error": action_run.error})
        o.stop("failed", f"{action_run.action_name}: {action_run.error or 'failed'}")
    elif action_run.status == "reverted":
        o.note({**where, "result": "reverted"})
        o.stop("skipped", f"{action_run.action_name}: the run was reverted")
    else:
        o.applied += 1
        o.note({**where, "result": "applied"})
        try:
            env = _env(settings, view, run, mailer=mailer, when=when)
            o.obj = ex.Obj(env.info, {})
            _reread(env, o)
            with ExitStack() as stack:
                ev = env.evaluator(stack)
                _resume_steps(env, ev, o, entry["path"])
        except ObjectError as exc:
            o.stop("skipped" if exc.code == "workflow_changed" else "failed", exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("workflow run %s: resuming %s failed", run_id, o.key)
            o.stop("failed", f"internal error ({type(exc).__name__})")
    return _record(view, run_id, o, resumed=action_run.id)


# --- reconciliation and cancel -----------------------------------------------------------


def _age(stamp: str | None) -> float:
    if not stamp:
        return float("inf")
    try:
        then = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - then).total_seconds()


def reconcile_run(
    settings: Any,
    view: Any,
    run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    """Bring one open run up to date (module docstring)."""
    with _session(view):
        return _reconcile_run(settings, view, run_id, mailer=mailer, when=when)


def _reconcile_run(
    settings: Any,
    view: Any,
    run_id: str,
    *,
    mailer: Any = None,
    when: dt.date | None = None,
) -> WorkflowRun | None:
    run = view.workflow_runs().get(run_id)
    if run is None or not run.open:
        return run
    for action_run_id in list((run.state.get("paused") or {}).keys()):
        action_run = view.action_runs().get(action_run_id)
        if action_run is None:
            entry = run.state["paused"][action_run_id]

            def drop(r: WorkflowRun, a: str = action_run_id, e: dict = entry) -> Any:
                if (r.state.get("paused") or {}).pop(a, None) is None:
                    return UNCHANGED
                r.summary["failed"] = r.summary.get("failed", 0) + 1
                for d in r.state.get("objects") or []:
                    if d.get("key") == e.get("key"):
                        d.update(status="failed", reason="the action run is gone")
                return None

            _update(view, run_id, drop)
        elif action_run.status in DECIDED:
            resume(settings, view, action_run_id, mailer=mailer, when=when)
    run = view.workflow_runs().get(run_id)
    if run is None or not run.open:
        return run
    stale = [
        a
        for a, e in (run.state.get("resuming") or {}).items()
        if _age(e.get("since")) > STALE_SECONDS
    ]
    interrupted = (
        run.status == "running"
        and not run.summary.get("iterated")
        and run_id not in _active
        and _age(run.summary.get("heartbeat") or run.started_at) > STALE_SECONDS
    )

    def change(r: WorkflowRun) -> Any:
        resuming = r.state.get("resuming") or {}
        for a in stale:
            e = resuming.pop(a, None)
            if e is None:
                continue
            r.summary["failed"] = r.summary.get("failed", 0) + 1
            for d in r.state.get("objects") or []:
                if d.get("key") == e.get("key"):
                    d.update(status="failed", reason="interrupted while resuming")
        if interrupted and r.status == "running" and not r.summary.get("iterated"):
            r.summary["iterated"] = True
            r.error = "interrupted: the process stopped while the run went through its objects"
        return None

    return _update(view, run_id, change)


def reconcile(settings: Any, view: Any, *, mailer: Any = None) -> int:
    """:func:`reconcile_run` for every open run of *view*; returns how many were open."""
    open_runs = view.workflow_runs().list(status=OPEN, limit=500)
    for run in open_runs:
        try:
            reconcile_run(settings, view, run.id, mailer=mailer)
        except Exception:  # noqa: BLE001 - one run never stops the others
            logger.exception("reconciling workflow run %s failed", run.id)
    return len(open_runs)


def cancel(
    view: Any,
    run_id: str,
    *,
    actor: str = "local",
    user_id: str | None = None,
    via: str = "",
    ip: str = "",
) -> WorkflowRun:
    """Cancel an open run (module docstring); 404 ``unknown_run``, 409 ``not_cancellable``."""
    dropped: list[int] = []

    def change(r: WorkflowRun) -> Any:
        if r.status not in OPEN:
            return UNCHANGED
        paused = r.state.get("paused") or {}
        resuming = r.state.get("resuming") or {}
        dropped.append(len(paused) + len(resuming))
        r.summary["dropped"] = r.summary.get("dropped", 0) + len(paused) + len(resuming)
        r.summary["pending_approval"] = 0
        r.state["paused"], r.state["resuming"] = {}, {}
        r.status = "cancelled"
        r.finished_at = now_iso()
        return None

    run, _ = view.workflow_runs().mutate(run_id, change)
    if run is None:
        raise _err(404, "unknown_run", f"no workflow run {str(run_id)[:80]!r}")
    if not dropped:
        raise _err(409, "not_cancellable", f"the run is {run.status}")
    _audit(
        view,
        "workflow.cancel",
        run,
        {"dropped": dropped[0]},
        actor=actor,
        user_id=user_id,
        via=via,
        ip=ip,
    )
    return run


# --- listing --------------------------------------------------------------------------------


def run_dict(view: Any, run: WorkflowRun, *, actions_too: bool = False) -> dict[str, Any]:
    out = run.to_dict()
    if actions_too:
        linked = view.action_runs().list(workflow_run_id=run.id, limit=500)
        out["action_runs"] = [a.to_dict() for a in linked]
    return out


def list_workflows(
    settings: Any, view: Any, *, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """The active workflows: trigger, object type, the next scheduled slot (local time)
    and the latest run."""
    from ontology.logic import scheduler

    ctx = registry.context(view)
    runs = view.workflow_runs()
    out = []
    for name in sorted(ctx.workflows):
        if ctx.statuses.get(("workflow", name)) != "active":
            continue
        spec = ctx.workflows[name]
        t = ctx.type_of(spec.for_each.object_type)
        d = view.logic_defs().get("workflow", name)
        item: dict[str, Any] = {
            "name": name,
            "label": spec.label,
            "description": spec.description,
            "version": d.version if d else 1,
            "trigger": spec.trigger.model_dump(mode="json", exclude_none=True),
            "object_type": t.name if t else spec.for_each.object_type,
            "object_type_label": t.label if t else "",
            "max_objects": spec.max_objects,
            "next_slot": None,
        }
        found = activation(view, name)
        item["activated_by"] = found[0] if found else None
        if spec.trigger.schedule:
            local_now = now or scheduler.local_now(settings)
            item["next_slot"] = scheduler.slot_key(
                scheduler.next_slot(spec.trigger.schedule, local_now)
            )
        latest = runs.latest(name)
        item["last_run"] = latest.to_dict(detail=False) if latest else None
        out.append(item)
    return out
