# SPDX-License-Identifier: MIT
"""Individual sessions and human-admin identity management; no anonymous enrollment."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from autoontology.security.context import current_actor
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=1024, repr=False)
    client: Literal["browser", "cli"] = "browser"


class HumanIn(Strict):
    username: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=15, max_length=1024, repr=False)
    roles: list[str] = Field(min_length=1, max_length=5)
    domains: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)


class RolesIn(Strict):
    roles: list[str] = Field(min_length=1, max_length=5)
    domains: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)


class PasswordIn(Strict):
    password: str = Field(min_length=15, max_length=1024, repr=False)


class DomainIn(Strict):
    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)


class SourceDomainIn(Strict):
    domain_id: str = Field(min_length=1, max_length=64)


class DelegationIn(Strict):
    username: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    actions: list[str] = Field(min_length=1, max_length=30)
    domains: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=100)
    ttl: int = Field(default=3600, ge=1, le=7 * 24 * 3600)


def cookie_name(api):
    state = Identity(api.store).status()
    return f"autoontology_{state.get('workspace_id', 'unconfigured')}_{api.settings.port}"


def install(app, api):
    service = Identity(api.store)
    router = APIRouter(prefix="/api/identity")

    def actor():
        current = current_actor(api.store)
        if current is None:
            raise AccessError(
                "Individual identity is not configured.", "identity_unconfigured", 409
            )
        return current

    @router.get("/status")
    def status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        state = service.status()
        return {"configured": state["configured"], "workspace_id": state.get("workspace_id")}

    @router.post("/login")
    def login(body: LoginIn, request: Request, response: Response):
        issued = service.login(
            body.username, body.password, peer=request.client.host if request.client else "unknown"
        )
        response.headers["Cache-Control"] = "no-store"
        if body.client == "browser":
            secure = request.url.scheme == "https" or request.url.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "testserver",
            }
            response.set_cookie(
                cookie_name(api),
                issued.secret.reveal(),
                httponly=True,
                secure=secure,
                samesite="strict",
                path="/",
                max_age=12 * 3600,
            )
            return issued.public()
        return {
            **issued.public(),
            "workspace_id": service.status()["workspace_id"],
            "token": issued.secret.reveal(),
        }

    @router.get("/me")
    def me(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.describe(actor())

    @router.post("/logout")
    def logout(response: Response):
        current = actor()
        service.revoke_credential(current, current.credential_id)
        response.delete_cookie(cookie_name(api), path="/", httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"signed_out": True}

    @router.get("/principals")
    def principals():
        return {"items": service.principals(actor())}

    @router.post("/principals", status_code=201)
    def human(body: HumanIn):
        return service.create_human(
            actor(), body.username, body.name, body.password, body.roles, domains=body.domains
        )

    @router.put("/principals/{principal_id}/roles")
    def roles(principal_id: str, body: RolesIn):
        return service.set_roles(actor(), principal_id, body.roles, domains=body.domains)

    @router.post("/principals/{principal_id}/disable")
    def disable(principal_id: str):
        return service.disable(actor(), principal_id)

    @router.put("/principals/{principal_id}/password")
    def password(principal_id: str, body: PasswordIn):
        service.change_password(actor(), principal_id, body.password)
        return {"changed": True, "sessions_invalidated": True}

    @router.get("/domains")
    def domains():
        with api.store._conn() as conn:
            service.require(actor(), "identity.admin")
            return {
                "items": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id,workspace_id,slug,name FROM identity_domains ORDER BY slug"
                    )
                ]
            }

    @router.post("/domains", status_code=201)
    def domain(body: DomainIn):
        return service.create_domain(actor(), body.slug, body.name)

    @router.put("/sources/{source_id}/domain")
    def source_domain(source_id: str, body: SourceDomainIn):
        return {"version": service.assign_source(actor(), source_id, body.domain_id)}

    @router.post("/delegations", status_code=201)
    def delegate(body: DelegationIn, response: Response):
        issued = service.delegate(
            actor(), body.username, body.name, body.actions, domains=body.domains, ttl=body.ttl
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            **issued.public(),
            "workspace_id": service.status()["workspace_id"],
            "token": issued.secret.reveal(),
        }

    @router.delete("/delegations/{principal_id}")
    def revoke_delegation(principal_id: str):
        service.revoke_delegation(actor(), principal_id)
        return {"revoked": principal_id}

    @router.delete("/credentials/{credential_id}")
    def revoke_credential(credential_id: str):
        service.revoke_credential(actor(), credential_id)
        return {"revoked": credential_id}

    @router.get("/audit")
    def audit():
        current = actor()
        return {"verification": service.verify_audit(current), "items": service.audit(current)}

    app.include_router(router)
