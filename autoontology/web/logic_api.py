# SPDX-License-Identifier: MIT
"""Logic routes (``/api/projects/{project}/logic/...`` and the ``/api/logic/...``
aliases), over :mod:`autoontology.logic.registry` and :mod:`autoontology.logic.functions`.

* ``GET /logic?kind=&status=`` -- the definitions (``logic.read``);
* ``GET /logic/{kind}/{name}`` -- one definition and its history (``logic.read``); a
  function also lists ``used_by``: the active actions and workflows that use it;
* ``POST /logic/validate`` ``{spec}`` -- parse and validate, save nothing
  (``logic.read``): ``{valid, kind, name, errors}``; ``{specs: [...]}`` checks several
  together (each with the others in place): ``{valid, results: [...]}``;
* ``POST /logic`` ``{spec}`` -- save a valid spec as a draft (a new version when the name
  exists; ``logic.edit``), 400 ``invalid_spec`` with ``errors`` otherwise;
* ``POST /logic/{kind}/{name}/status`` ``{status: active|retired|draft}`` -- functions
  need ``logic.edit`` (``logic.activate`` when an active action or workflow uses the
  function: :func:`~autoontology.logic.registry.used_by_active`), actions and workflows
  ``logic.activate``; ``active`` needs a valid spec whose references are active (409
  otherwise);
* ``POST /logic/functions/{name}/test`` ``{keys, params?}`` and ``POST /logic/test``
  ``{spec, keys, params?}`` -- evaluate a stored (draft or active) or unsaved function on
  chosen objects (``functions.call``);
* ``POST /logic/functions/{name}/call`` ``{key, params?}`` -- an active function on one
  object (``functions.call``);
* ``GET /logic/candidates?ready=&status=`` -- the candidates a ``logic`` run proposed, as
  review inbox items (``logic.read``; ``status`` pending (default) | decided | all,
  ``ready`` true: only valid ones, false: only those that need an edit). They are decided
  through the review routes (``POST /review/lg:<id>/decision``, ``logic.edit``);
* ``GET /logic/skill.zip`` -- the active definitions as an Agent Skill bundle
  (:mod:`autoontology.logic.skills`; ``logic.read``, audited ``skill.export``).

``spec`` is a mapping or YAML/JSON text. Writes are audited (``logic.save``,
``logic.status``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoontology.logic import functions, registry
from autoontology.logic.specs import MAX_SPEC_CHARS
from autoontology.store.logic import KINDS, STATUSES
from autoontology.web.projects import ProjectContext, allowed_when_archived

MAX_KEYS = functions.MAX_TEST_KEYS


class SpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str | dict[str, Any]


class ValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str | dict[str, Any] | None = None
    specs: list[dict[str, Any]] | None = Field(None, min_length=1, max_length=50)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=20)


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str | dict[str, Any] | None = None
    keys: list[str] = Field(min_length=1, max_length=MAX_KEYS)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


def add_logic_routes(router: APIRouter, api: Any, ctx_dep: Any) -> None:
    from autoontology.web.api import ApiError, _validation_text, object_call

    Ctx = Depends(ctx_dep)  # noqa: N806 - a dependency marker shared by the handlers

    def body_of(model: type[BaseModel], body: dict[str, Any]) -> Any:
        try:
            req = model.model_validate(body)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        spec = getattr(req, "spec", None)
        if isinstance(spec, str) and len(spec) > MAX_SPEC_CHARS:
            raise ApiError(413, "too_large", f"a spec is at most {MAX_SPEC_CHARS} characters")
        return req

    def kind_of(kind: str) -> str:
        if kind not in KINDS:
            raise ApiError(400, "invalid_kind", f"kind must be one of {', '.join(KINDS)}")
        return kind

    @router.get("/logic")
    def logic_list(
        kind: str | None = Query(None, max_length=20),
        status: str | None = Query(None, max_length=20),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("logic.read")
        if kind is not None:
            kind_of(kind)
        if status is not None and status not in STATUSES:
            raise ApiError(400, "invalid_status", f"status must be one of {', '.join(STATUSES)}")
        object_call(lambda: registry.ensure_validated(ctx.store))
        defs = ctx.store.logic_defs().list(kind, status)
        return {"definitions": [d.to_dict() for d in defs]}

    @router.get("/logic/skill.zip")
    def logic_skill(ctx: ProjectContext = Ctx) -> Response:
        """The active definitions as an Agent Skill bundle
        (:func:`autoontology.logic.skills.build_skill`). Sync: runs in the threadpool."""
        from autoontology.logic import skills

        ctx.require("logic.read")
        bundle = object_call(lambda: skills.build_skill(ctx.store))
        data = bundle.zip_bytes()
        ctx.audit("skill.export", target=bundle.name, detail=dict(bundle.counts))
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{bundle.name}.zip"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/logic/candidates")
    def logic_candidates(
        ready: bool | None = None,
        status: str = Query("pending", max_length=20),
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        from autoontology.logic import candidates

        ctx.require("logic.read")
        if status not in ("pending", "decided", "all"):
            raise ApiError(400, "invalid_status", "status must be one of pending, decided, all")
        items = candidates.listing(ctx.store, ready=ready)
        if status != "all":
            items = [i for i in items if (i["decision"] is None) == (status == "pending")]
        return {"candidates": items}

    def result(spec: Any, errors: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "valid": spec is not None and not errors,
            "kind": spec.kind if spec else None,
            "name": spec.name if spec else None,
            "errors": errors,
        }

    @router.post("/logic/validate")
    @allowed_when_archived
    def logic_validate(body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("logic.read")
        req = body_of(ValidateIn, body)
        if (req.spec is None) == (req.specs is None):
            raise ApiError(400, "invalid_request", "give spec or specs")
        if req.specs is not None:
            checked = object_call(lambda: registry.check_many(ctx.store, req.specs))
            results = [result(spec, errors) for spec, errors in checked]
            return {"valid": all(r["valid"] for r in results), "results": results}
        spec, errors = object_call(lambda: registry.check(ctx.store, req.spec))
        return result(spec, errors)

    @router.post("/logic/test")
    @allowed_when_archived
    def logic_test(body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("functions.call")
        req = body_of(TestIn, body)
        if req.spec is None:
            raise ApiError(400, "invalid_request", "spec is required")
        return object_call(
            lambda: functions.test(api.settings, ctx.store, req.spec, req.keys, params=req.params)
        )

    @router.post("/logic")
    def logic_save(body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("logic.edit")
        req = body_of(SpecIn, body)
        saved = object_call(lambda: registry.save(ctx.store, req.spec, actor=ctx.actor))
        ctx.audit(
            "logic.save",
            target=f"{saved.kind}:{saved.name}"[:300],
            detail={"version": saved.version, "status": saved.status},
        )
        return {"definition": saved.to_dict()}

    @router.get("/logic/{kind}/{name}")
    def logic_get(kind: str, name: str, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("logic.read")
        found = object_call(lambda: registry.get(ctx.store, kind_of(kind), name))
        out: dict[str, Any] = {
            "definition": found.to_dict(),
            "history": ctx.store.logic_defs().history(kind, name)[:100],
        }
        if kind == "function":
            out["used_by"] = object_call(lambda: registry.used_by_active(ctx.store, name))
        return out

    @router.post("/logic/{kind}/{name}/status")
    def logic_status(
        kind: str, name: str, body: dict[str, Any], ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("logic.edit" if kind_of(kind) == "function" else "logic.activate")
        req = body_of(StatusIn, body)
        before = object_call(lambda: registry.get(ctx.store, kind, name))
        if kind == "function" and not ctx.can("logic.activate"):
            users = object_call(lambda: registry.used_by_active(ctx.store, name))
            if users:
                raise ApiError(
                    403,
                    "forbidden",
                    f"function {name} is used by {', '.join(users[:5])}: changing its "
                    "status needs the 'logic.activate' permission (owner)",
                    permission="logic.activate",
                    used_by=users,
                )
        updated = object_call(
            lambda: registry.set_status(ctx.store, kind, name, req.status, actor=ctx.actor)
        )
        ctx.audit(
            "logic.status",
            target=f"{kind}:{name}"[:300],
            detail={"from": before.status, "to": updated.status, "version": updated.version},
        )
        return {"definition": updated.to_dict()}

    @router.post("/logic/functions/{name}/test")
    @allowed_when_archived
    def function_test(name: str, body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("functions.call")
        req = body_of(TestIn, body)
        if req.spec is not None:
            raise ApiError(400, "invalid_request", "a stored function takes no spec")
        object_call(lambda: registry.get(ctx.store, "function", name))
        return object_call(
            lambda: functions.test(api.settings, ctx.store, name, req.keys, params=req.params)
        )

    @router.post("/logic/functions/{name}/call")
    @allowed_when_archived
    def function_call(name: str, body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("functions.call")
        req = body_of(CallIn, body)
        return object_call(
            lambda: functions.call(api.settings, ctx.store, name, req.key, params=req.params)
        )
