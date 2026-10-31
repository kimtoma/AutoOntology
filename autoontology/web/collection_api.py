# SPDX-License-Identifier: MIT
"""Collection UI calls the same persistent service and credential-owned worker."""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autoontology.security.policy import AccessError
from autoontology.store.collection import Collection


class Configure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=80)
    worker_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    interval: int = Field(60, ge=30, le=86400)
    mode: Literal["observe", "prepare"] = "observe"
    reason: str = Field(min_length=1, max_length=2000)
    max_attempts: int = Field(3, ge=1, le=10)
    ttl: int = Field(86400, ge=60, le=604800)
    confirm: bool = False


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


def install(app, api):
    router = APIRouter(prefix="/api/collection")

    def queue():
        return Collection(api.store)

    def response(value):
        return JSONResponse({"schema": 1, **value}, headers={"Cache-Control": "private, no-store"})

    def check(value):
        if not value:
            raise AccessError(
                "Confirm the exact source, worker and collection effect.",
                "collection_confirmation_required",
                400,
            )

    @router.get("")
    def listing():
        q = queue()
        return response(
            {
                "schedules": q.listing(),
                "jobs": q.jobs(),
                "notices": q.notices(),
                "workers": q.worker_status(),
            }
        )

    @router.get("/summary")
    def summary():
        return response(queue().summary())

    @router.get("/workers")
    def workers():
        return response({"items": queue().workers()})

    @router.post("")
    def configure(body: Configure):
        check(body.confirm)
        return response(queue().configure(**body.model_dump(exclude={"confirm"})))

    @router.get("/jobs/{ident}")
    def job(ident: str):
        return response(queue().job(ident))

    @router.get("/{ident}/history")
    def history(ident: str):
        return response({"items": queue().history(ident)})

    @router.post("/{ident}/pause")
    def pause(ident: str, body: Change):
        check(body.confirm)
        return response(queue().pause(ident, reason=body.reason))

    @router.post("/{ident}/resume")
    def resume(ident: str, body: Change):
        check(body.confirm)
        return response(queue().resume(ident, reason=body.reason))

    @router.post("/jobs/{ident}/cancel")
    def cancel(ident: str, body: Change):
        check(body.confirm)
        return response(queue().cancel(ident, reason=body.reason))

    @router.post("/jobs/{ident}/retry")
    def retry(ident: str, body: Change):
        check(body.confirm)
        return response(queue().retry(ident, reason=body.reason))

    @router.post("/notices/{ident}/ack")
    def ack(ident: str):
        return response(queue().ack(ident))

    @router.post("/{ident}/poll")
    def poll(ident: str):
        return response(queue().poll(ident))

    @router.post("/work")
    def work():
        from autoontology.engine.collection import cycle

        return response(cycle(api.store, api.settings))

    app.include_router(router)
