# SPDX-License-Identifier: MIT
"""Shared versioned evaluation and typed-rule inspection endpoints."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autoontology.security.policy import AccessError
from autoontology.store.quality import Quality


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    previous: str | None = None
    payload: dict


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool = False


def install(app, api):
    router = APIRouter(prefix="/api/quality")

    def result(value, status=200):
        return JSONResponse(
            {"schema": 1, **value},
            status_code=status,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get("")
    def listing():
        quality = Quality(api.store)
        return result({"active": quality.active(), "versions": quality.versions()})

    @router.post("/versions")
    def prepare(body: Prepare):
        return result(
            Quality(api.store).prepare(body.key, body.payload, previous=body.previous), 201
        )

    @router.get("/versions/{ident}")
    def detail(ident: str):
        return result(Quality(api.store).get(ident))

    @router.post("/versions/{ident}/activate")
    def activate(ident: str, body: Confirm):
        return result(Quality(api.store).activate(ident, body.sha256, confirm=body.confirm))

    @router.post("/versions/{ident}/deactivate")
    def deactivate(ident: str, body: Confirm):
        return result(Quality(api.store).deactivate(ident, body.sha256, confirm=body.confirm))

    @router.get("/rules")
    def rules(revision: int | None = Query(None, ge=1)):
        return result(Quality(api.store).rules(revision))

    @router.get("/runs")
    def runs():
        return result({"items": Quality(api.store).runs()})

    @router.get("/runs/{ident}")
    def run(ident: str):
        items = Quality(api.store).runs(ident)
        if not items:
            raise AccessError("Evaluation run not found.", "quality_run_missing", 404)
        return result(items[0])

    app.include_router(router)
