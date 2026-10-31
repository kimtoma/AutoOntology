# SPDX-License-Identifier: MIT
"""Administrator-approved live-input roots; never expose a generic filesystem browser."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autoontology.security.host_files import HostFiles


class Root(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    confirm: bool = False


def install(app, api):
    from autoontology.security.policy import AccessError

    router = APIRouter(prefix="/api/host-files")

    def response(value):
        return JSONResponse({"schema": 1, **value}, headers={"Cache-Control": "private, no-store"})

    @router.get("")
    def listing():
        return response(HostFiles(api.store).listing())

    def apply(body, operation):
        if not body.confirm:
            raise AccessError(
                "Confirm the exact host root and its effect on future reads.",
                "host_root_confirmation_required",
                400,
            )
        return response(operation(body.path, reason=body.reason))

    @router.post("/allow")
    def allow(body: Root):
        return apply(body, HostFiles(api.store).allow)

    @router.post("/revoke")
    def revoke(body: Root):
        return apply(body, HostFiles(api.store).revoke)

    app.include_router(router)
