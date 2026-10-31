# SPDX-License-Identifier: MIT
"""Case context, feedback handoff and transparent time records use shared operations."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autoontology.store.ax import AX


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Context(Strict):
    payload: dict
    previous: str | None = None
    reason: str = Field(min_length=1, max_length=5000)


class Start(Strict):
    case_id: str
    note: str = Field(min_length=1, max_length=1000)


class Stop(Strict):
    note: str = Field(min_length=1, max_length=1000)
    discard: bool = False


class Confirm(Strict):
    confirm: bool = False


class Payload(Confirm):
    payload: dict


class Reason(Confirm):
    reason: str = Field(min_length=1, max_length=2000)


class FeedbackCase(Strict):
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=3000)
    owner_id: str | None = None


class FeedbackResolve(Confirm):
    case_id: str


def install(app, api):
    router = APIRouter(prefix="/api/ax")

    def response(value, status=200):
        return JSONResponse(
            {"schema": 1, **value},
            status_code=status,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get("")
    def overview():
        return response(AX(api.store).overview())

    @router.get("/baselines")
    def baselines():
        return response({"items": AX(api.store).baselines()})

    @router.get("/efforts")
    def efforts():
        return response({"items": AX(api.store).efforts()})

    @router.get("/cases/{ident}")
    def context(ident: str):
        return response(AX(api.store).context(ident))

    @router.put("/cases/{ident}")
    def save_context(ident: str, body: Context):
        return response(
            AX(api.store).set_context(
                ident, body.payload, previous=body.previous, reason=body.reason
            )
        )

    @router.post("/efforts")
    def start(body: Start):
        return response(AX(api.store).start_effort(body.case_id, note=body.note), 201)

    @router.post("/efforts/{ident}/stop")
    def stop(ident: str, body: Stop):
        return response(AX(api.store).stop_effort(ident, **body.model_dump()))

    @router.post("/cases/{ident}/attest-effort")
    def attest(ident: str, body: Reason):
        return response(AX(api.store).attest_effort(ident, **body.model_dump()))

    @router.post("/baselines")
    def baseline(body: Payload):
        return response(AX(api.store).add_baseline(body.payload, confirm=body.confirm), 201)

    @router.post("/baselines/{ident}/void")
    def void(ident: str, body: Reason):
        return response(AX(api.store).void_baseline(ident, **body.model_dump()))

    @router.post("/targets")
    def target(body: Payload):
        return response(AX(api.store).set_target(body.payload, confirm=body.confirm), 201)

    @router.post("/feedback/{ident}/case")
    def feedback_case(ident: int, body: FeedbackCase):
        return response(AX(api.store).feedback_case(ident, **body.model_dump()), 201)

    @router.post("/feedback/{ident}/resolve")
    def feedback_resolve(ident: int, body: FeedbackResolve):
        return response(AX(api.store).resolve_feedback(ident, body.case_id, confirm=body.confirm))

    app.include_router(router)
