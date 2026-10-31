# SPDX-License-Identifier: MIT
"""Shared business-case and exact human review operations, never publication shortcuts."""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from autoontology.security.policy import AccessError
from autoontology.store.cases import Cases
from autoontology.store.operations import OperationsError
from autoontology.web.api import ApiError


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accept", "reject", "hold"]
    note: str = Field("", max_length=5000)
    edits: dict = Field(default_factory=dict, max_length=20)


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relation: Literal["depends_on", "duplicate_of", "supersedes"]
    case_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class Fields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=200)
    reason: str | None = Field(None, min_length=1, max_length=5000)
    owner_id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$")
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    due_at: str | None = Field(None, max_length=64)
    changes: list[Proposal] | None = Field(None, max_length=1000)
    links: list[Link] | None = Field(None, max_length=100)


class Create(Fields):
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=5000)
    request_key: str | None = Field(None, max_length=200)


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    patch: Fields
    status: Literal["open", "preparing", "held", "cancelled"] | None = None


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    executor_id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$")
    ttl: int = Field(1800, ge=10, le=86400)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: bool = False


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=5000)
    publication: int | None = Field(None, ge=1)


class Rebase(Transition):
    changes: list[Proposal] = Field(max_length=1000)


def install(app, api):
    router = APIRouter(prefix="/api/cases")

    def call(function, status=200):
        try:
            value = function(Cases(api.store))
            return JSONResponse(
                {"schema": 1, **value},
                status_code=status,
                headers={"Cache-Control": "private, no-store"},
            )
        except OperationsError as error:
            raise ApiError(409, "case_plan_invalid", str(error)) from error

    @router.get("")
    def listing(status: str | None = None):
        return call(lambda cases: {"items": cases.listing(status=status)})

    @router.get("/summary")
    def summary():
        return call(lambda cases: cases.summary())

    @router.get("/members")
    def members():
        return call(lambda cases: {"items": cases.members()})

    @router.post("", status_code=201)
    def create(body: Create):
        return call(lambda cases: cases.create(**body.model_dump(exclude_unset=True)), 201)

    @router.get("/{ident}")
    def detail(ident: str):
        return call(lambda cases: {**cases.get(ident), "history": cases.history(ident)})

    @router.patch("/{ident}")
    def update(ident: str, body: Update):
        return call(
            lambda cases: cases.update(
                ident,
                version=body.version,
                **body.patch.model_dump(exclude_unset=True),
                **({"status": body.status} if body.status else {}),
            )
        )

    @router.post("/{ident}/prepare")
    def prepare(ident: str, body: Prepare):
        return call(lambda cases: cases.prepare(ident, **body.model_dump()))

    @router.post("/packets/{packet}/confirm")
    def confirm(packet: str, body: Confirm):
        if not body.confirm:
            raise AccessError(
                "Explicitly confirm the exact review packet.", "case_confirmation_required", 400
            )
        return call(lambda cases: cases.confirm(packet, body.sha256))

    @router.post("/{ident}/rebase")
    def rebase(ident: str, body: Rebase):
        return call(
            lambda cases: cases.rebase(
                ident,
                version=body.version,
                reason=body.reason,
                changes=[change.model_dump() for change in body.changes],
            )
        )

    @router.post("/{ident}/resolve")
    def resolve(ident: str, body: Transition):
        return call(lambda cases: cases.resolve(ident, **body.model_dump()))

    @router.post("/{ident}/reopen")
    def reopen(ident: str, body: Transition):
        return call(lambda cases: cases.reopen(ident, version=body.version, reason=body.reason))

    app.include_router(router)
