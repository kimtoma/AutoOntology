# SPDX-License-Identifier: MIT
"""Workflow routes (``/api/projects/{project}/workflows/...`` and the ``/api/workflows/...``
aliases), over :mod:`autoontology.logic.workflows`.

* ``GET /workflows`` -- the active workflows with their trigger, the next scheduled slot
  and the latest run (``logic.read``);
* ``POST /workflows/{name}/run`` ``{wait?}`` -- fire it now (``workflow.run``; a workflow
  without ``trigger.manual: true`` needs ``logic.activate``, i.e. an owner): 202 with
  the ``running`` run, whose objects are processed on a worker thread; ``wait: true``
  processes them in the request and answers 200 with the run as it ended (or paused);
  409 ``run_active`` while another run of the workflow is ``running``;
* ``GET /workflows/runs?workflow=&status=&limit=`` -- runs, newest first (``logic.read``);
* ``GET /workflows/runs/{id}`` -- one run with its objects and its action runs
  (``logic.read``);
* ``POST /workflows/runs/{id}/cancel`` -- stop an open run (``workflow.run``; 409
  ``not_cancellable`` once it ended).

A run acts with the role of the workflow's activator, not the caller's
(:mod:`autoontology.logic.workflows`). Audited: ``workflow.run``, ``workflow.finish``,
``workflow.cancel``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from autoontology.logic import workflows
from autoontology.store.workflows import MAX_LIST, STATUSES
from autoontology.web.projects import ProjectContext


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wait: bool = False


def add_workflow_routes(router: APIRouter, api: Any, ctx_dep: Any) -> None:
    from autoontology.web.actions_api import mailer_of
    from autoontology.web.api import ApiError, _validation_text, object_call

    Ctx = Depends(ctx_dep)  # noqa: N806 - a dependency marker shared by the handlers

    @router.get("/workflows")
    def list_workflows(ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("logic.read")
        return {
            "workflows": object_call(lambda: workflows.list_workflows(api.settings, ctx.store)),
            "triggers": "on" if workflows._enabled(api.settings) else "off",
        }

    @router.get("/workflows/runs")
    def list_runs(
        workflow: str | None = Query(None, max_length=200),
        status: str | None = Query(None, max_length=20),
        limit: int = Query(50, ge=1, le=MAX_LIST),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("logic.read")
        if status is not None and status not in STATUSES:
            raise ApiError(400, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        runs = ctx.store.workflow_runs().list(workflow=workflow, status=status, limit=limit)
        return {"runs": [r.to_dict(detail=False) for r in runs]}

    @router.get("/workflows/runs/{run_id}")
    def get_run(run_id: str, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("logic.read")
        run = ctx.store.workflow_runs().get(run_id)
        if run is None:
            raise ApiError(404, "unknown_run", f"no workflow run {run_id[:80]!r}")
        return {"run": workflows.run_dict(ctx.store, run, actions_too=True)}

    @router.post("/workflows/runs/{run_id}/cancel")
    def cancel(run_id: str, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("workflow.run")
        run = object_call(
            lambda: workflows.cancel(
                ctx.store,
                run_id,
                actor=ctx.actor,
                user_id=ctx.user_id,
                via=ctx.via,
                ip=ctx.ip,
            )
        )
        return {"run": run.to_dict(detail=False)}

    @router.post("/workflows/{name}/run")
    def run(
        name: str,
        request: Request,
        body: dict[str, Any] | None = None,
        ctx: ProjectContext = Ctx,
    ) -> JSONResponse:
        ctx.require("workflow.run")
        found = ctx.store.logic_defs().get("workflow", name) if len(name) <= 200 else None
        if found is not None and (found.spec.get("trigger") or {}).get("manual") is not True:
            # a scheduled or on_action workflow runs by itself; firing it at will is an
            # owner's decision (each run may notify many people)
            ctx.require("logic.activate")
        try:
            req = RunIn.model_validate(body or {})
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        kw: dict[str, Any] = {
            "trigger": "manual",
            "fire_slot": workflows.manual_slot(),
            "started_by": ctx.actor,
            "mailer": mailer_of(request),
            "user_id": ctx.user_id,
            "via": ctx.via,
            "ip": ctx.ip,
        }
        if req.wait:
            done = object_call(lambda: workflows.run(api.settings, ctx.store, name, **kw))
            return JSONResponse(
                {"run": workflows.run_dict(ctx.store, done, actions_too=True)}, status_code=200
            )
        started, _ = object_call(
            lambda: workflows.start_background(api.settings, ctx.store, name, **kw)
        )
        return JSONResponse({"run": started.to_dict()}, status_code=202)
