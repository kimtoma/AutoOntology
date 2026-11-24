# SPDX-License-Identifier: MIT
"""Object routes (``/api/projects/{project}/objects/...`` and the ``/api/objects/...``
aliases), over :mod:`autoontology.objects.service`.

* ``GET /objects/types`` -- the project's object types (``?status=``), synced with the
  latest snapshot; ``GET /objects/types/{type}`` one of them;
* ``PUT /objects/types/{type}`` -- bind key / title columns by hand (``objects.manage``),
  verified on the source; the type becomes active;
* ``GET /objects/{type}?q=&filter=col=value&limit=&offset=&computed=`` -- objects of a
  type (``computed=1``: each with the values of the type's active functions);
* ``GET /objects/{type}/{key}`` -- one object: values with the edit layer applied, the
  edit marks (conflicts, operational properties), links, history and ``computed`` (the
  active functions' values: ``{name: {value, type, error?}}``, ``functions.call``) and
  ``actions`` (the type's active actions: ``[{name, label, available, reason?}]``,
  available = the caller's role and the preconditions at default parameters);
* ``GET /objects/{type}/{key}/links/{link}?then=&limit=&offset=`` -- the objects a link
  reaches (``then``: one more hop).

Reads need ``objects.read``. A type is named by its id, class name (``term-...``), label or
table (:mod:`autoontology.objects.naming`); an ambiguous name is 409 ``ambiguous_object_type``.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoontology.logic import functions
from autoontology.objects import service
from autoontology.objects.reader import DEFAULT_PAGE, MAX_PAGE
from autoontology.store.objects import STATUSES
from autoontology.web.projects import ProjectContext


class BindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(None, min_length=1, max_length=63)
    #: "" = no title column
    title: str | None = Field(None, max_length=63)


def add_object_routes(router: APIRouter, api: Any, ctx_dep: Any) -> None:
    from autoontology.web.api import ApiError, _validation_text, object_call

    Ctx = Depends(ctx_dep)  # noqa: N806 - a dependency marker shared by the handlers

    @router.get("/objects/types")
    def object_types(
        status: str | None = Query(None, max_length=20), ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        if status is not None and status not in STATUSES:
            raise ApiError(400, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        types = object_call(lambda: service.list_types(ctx.store, status=status))
        return {
            "revision": ctx.store.object_types().synced_revision(),
            "types": [t.to_dict() for t in types],
        }

    @router.get("/objects/types/{type_name}")
    def object_type(type_name: str, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("objects.read")
        otype = object_call(lambda: service.find_type(ctx.store, type_name))
        return {"type": otype.to_dict()}

    @router.put("/objects/types/{type_name}")
    async def bind_object_type(
        type_name: str, body: dict[str, Any], ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("objects.manage")
        try:
            req = BindIn.model_validate(body)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc

        def run() -> Any:
            otype = object_call(
                lambda: service.bind(
                    api.settings,
                    ctx.store,
                    type_name,
                    key=req.key,
                    title=req.title,
                    actor=ctx.actor,
                )
            )
            ctx.audit(
                "object.bind",
                target=otype.class_local,
                detail={"key": otype.key, "title": otype.title_column},
            )
            return otype

        otype = await anyio.to_thread.run_sync(run)
        return {"type": otype.to_dict()}

    @router.get("/objects/{type_name}")
    def objects(
        type_name: str,
        q: str = Query("", max_length=200),
        filter: list[str] = Query([], max_length=service.MAX_FILTERS),  # noqa: A002, B008
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0, le=1_000_000),
        computed: bool = Query(False),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        if computed:
            ctx.require("functions.call")
        found = object_call(
            lambda: service.search(
                api.settings,
                ctx.store,
                type_name,
                q=q,
                filters=filter,
                limit=limit,
                offset=offset,
            )
        )
        if computed:
            object_call(
                lambda: functions.attach_computed(
                    api.settings, ctx.store, found["type"]["name"], found["items"]
                )
            )
        return found

    @router.get("/objects/{type_name}/{key}")
    def object_detail(
        type_name: str, key: str, request: Request, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        from autoontology.logic import actions
        from autoontology.web.actions_api import caller_of

        ctx.require("objects.read")
        found = object_call(lambda: service.get_object(api.settings, ctx.store, type_name, key))
        if ctx.can("functions.call"):
            object_call(
                lambda: functions.attach_computed(
                    api.settings, ctx.store, found["type"]["name"], [found]
                )
            )
        caller = caller_of(request, ctx, api.settings)
        found["actions"] = object_call(
            lambda: actions.available(api.settings, ctx.store, found["type"]["name"], found, caller)
        )
        return found

    @router.get("/objects/{type_name}/{key}/links/{link}")
    def object_links(
        type_name: str,
        key: str,
        link: str,
        then: str | None = Query(None, max_length=200),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0, le=1_000_000),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("objects.read")
        return object_call(
            lambda: service.follow(
                api.settings,
                ctx.store,
                type_name,
                key,
                link,
                then=then,
                limit=limit,
                offset=offset,
            )
        )
