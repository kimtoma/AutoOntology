# SPDX-License-Identifier: MIT
"""Action routes (``/api/projects/{project}/actions/...`` and the ``/api/actions/...``
aliases), over :mod:`autoontology.logic.actions`.

* ``GET /actions?type=`` -- the active actions (of an object type);
* ``POST /actions/{name}/preview`` ``{key, params?}`` -- what the action would do: the
  parameters, preconditions, effects, approval and notifications; nothing is stored;
* ``POST /actions/{name}/submit`` ``{key, params?, idempotency_key?}`` -- run it: 201
  with the run (``applied`` or ``pending_approval``); 200 with the earlier run when the
  idempotency key was used before; 409 ``precondition_failed`` (``failures``);
* ``GET /actions/runs?status=&action=&key=&type=&limit=`` and ``GET /actions/runs/{id}``;
* ``POST /actions/runs/{id}/approve``, ``.../reject`` ``{note?}``, ``.../revert``
  ``{force?}``.

Every route needs ``objects.read``; the action runner enforces the rest from the specs
(an action's ``permission.min_role`` for submit and revert, a run's approval role and
four-eyes for approve / reject; 403 ``forbidden`` names ``action.run`` /
``action.approve``). Transitions are audited by the runner (``action.*``, target: the
run id). Runs list the caller's ``can_approve`` / ``can_revert``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoontology.logic import actions
from autoontology.logic.actions import Caller
from autoontology.store.actions import MAX_LIST, STATUSES
from autoontology.web.projects import ProjectContext, allowed_when_archived


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


class SubmitIn(PreviewIn):
    idempotency_key: str | None = Field(None, min_length=1, max_length=200)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field("", max_length=actions.MAX_NOTE_CHARS)


class RevertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


def caller_of(request: Request, ctx: ProjectContext, settings: Any) -> Caller:
    """The :class:`Caller` of a UI / CLI request (the CLI's in-process app has no sign-in
    state; ``ao --as`` and the local key say ``via: cli``)."""
    cli = ctx.via == "cli" or getattr(request.app.state, "auth", None) is None
    return Caller.from_context(ctx, settings, via="cli" if cli else "ui")


def mailer_of(request: Request) -> Any:
    auth = getattr(request.app.state, "auth", None)
    return getattr(auth, "mailer", None)


def add_action_routes(router: APIRouter, api: Any, ctx_dep: Any) -> None:
    from autoontology.web.api import ApiError, _validation_text, object_call

    Ctx = Depends(ctx_dep)  # noqa: N806 - a dependency marker shared by the handlers

    def body_of(model: type[BaseModel], body: dict[str, Any]) -> Any:
        try:
            return model.model_validate(body)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc

    @router.get("/actions")
    def list_actions(
        type: str | None = Query(None, max_length=200),  # noqa: A002
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        return {"actions": object_call(lambda: actions.list_actions(ctx.store, type))}

    @router.get("/actions/runs")
    def list_runs(
        request: Request,
        status: str | None = Query(None, max_length=30),
        action: str | None = Query(None, max_length=200),
        key: str | None = Query(None, max_length=200),
        type: str | None = Query(None, max_length=200),  # noqa: A002
        limit: int = Query(50, ge=1, le=MAX_LIST),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        if status is not None and status not in STATUSES:
            raise ApiError(400, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        caller = caller_of(request, ctx, api.settings)
        runs = ctx.store.action_runs().list(
            status=status, action=action, object_key=key, object_type=type, limit=limit
        )
        return {"runs": [actions.run_dict(r, caller) for r in runs]}

    @router.get("/actions/runs/{run_id}")
    def get_run(run_id: str, request: Request, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("objects.read")
        run = ctx.store.action_runs().get(run_id)
        if run is None:
            raise ApiError(404, "unknown_run", f"no action run {run_id[:80]!r}")
        caller = caller_of(request, ctx, api.settings)
        edits = [e.to_dict() for e in ctx.store.object_edits().by_run(run.id)]
        return {"run": actions.run_dict(run, caller), "edits": edits}

    @router.post("/actions/{name}/preview")
    @allowed_when_archived
    def preview(
        name: str, body: dict[str, Any], request: Request, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        req = body_of(PreviewIn, body)
        caller = caller_of(request, ctx, api.settings)
        return {
            "preview": object_call(
                lambda: actions.preview(api.settings, ctx.store, name, req.key, req.params, caller)
            )
        }

    @router.post("/actions/{name}/submit")
    def submit(
        name: str, body: dict[str, Any], request: Request, ctx: ProjectContext = Ctx
    ) -> JSONResponse:
        ctx.require("objects.read")
        req = body_of(SubmitIn, body)
        caller = caller_of(request, ctx, api.settings)
        run, created = object_call(
            lambda: actions.submit(
                api.settings,
                ctx.store,
                name,
                req.key,
                req.params,
                caller,
                idempotency_key=req.idempotency_key,
                mailer=mailer_of(request),
            )
        )
        return JSONResponse(
            {"run": actions.run_dict(run, caller), "created": created},
            status_code=201 if created else 200,
        )

    @router.post("/actions/runs/{run_id}/approve")
    def approve(run_id: str, request: Request, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("objects.read")
        caller = caller_of(request, ctx, api.settings)
        run = object_call(
            lambda: actions.approve(
                api.settings, ctx.store, run_id, caller, mailer=mailer_of(request)
            )
        )
        return {"run": actions.run_dict(run, caller)}

    @router.post("/actions/runs/{run_id}/reject")
    def reject(
        run_id: str, request: Request, body: dict[str, Any] | None = None, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        req = body_of(RejectIn, body or {})
        caller = caller_of(request, ctx, api.settings)
        run = object_call(lambda: actions.reject(ctx.store, run_id, caller, req.note))
        return {"run": actions.run_dict(run, caller)}

    @router.post("/actions/runs/{run_id}/revert")
    def revert(
        run_id: str, request: Request, body: dict[str, Any] | None = None, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        req = body_of(RevertIn, body or {})
        caller = caller_of(request, ctx, api.settings)
        run = object_call(
            lambda: actions.revert(api.settings, ctx.store, run_id, caller, force=req.force)
        )
        return {"run": actions.run_dict(run, caller)}
