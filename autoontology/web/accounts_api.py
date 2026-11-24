# SPDX-License-Identifier: MIT
"""Account management in the REST API: users (admins), project members (owners) and the
audit log.

``/api/users`` (permission ``users.manage``: admins)
    ``GET``  -> ``{users: [user + memberships: [{project_id, slug, role}]]}``
    ``POST`` ``{email, name?, is_admin?, send?}`` -> 201 ``{user, invite_link, expires_at,
    mail_sent}``: a new user without a password and an invitation link (mailed when SMTP is
    set up and ``send`` is not false); 409 ``user_exists``, 400 ``invalid_email``.
    ``PATCH /{user_id}`` ``{name?, is_admin?, disabled?}`` -> the user; 409 ``last_admin``
    when the change would leave no active admin; disabling ends the user's sessions.
    ``POST /{user_id}/link`` ``{purpose: magic|reset|invite, send?}`` -> ``{email,
    purpose, link, expires_at, mail_sent}`` (a fresh single-use link; earlier unused links
    of that purpose stop working); 409 ``user_disabled``.

``/api/projects/{project}/members`` (``members.manage``: owners and admins)
    ``GET`` -> ``{members: [user + role + member_since]}``
    ``POST`` ``{email, role}`` -> 201 ``{member, created, invite_link?, expires_at?,
    mail_sent}``: an existing active user is added; 409 ``member_exists``. Only an admin
    creates accounts: for an admin an unknown email becomes a new user with an invitation
    link; an owner who is not an admin gets 403 ``admin_required_for_new_user`` for an
    unknown email *and* for a disabled user (one answer: it does not tell an unknown
    address from a disabled account). Adding an existing user does show an owner that
    the address has an account -- accepted: owners pick members among the installation's
    users.
    ``PATCH /{user_id}`` ``{role}`` -> the member; ``DELETE /{user_id}`` -> ``{removed}``;
    409 ``last_owner`` (a project keeps an owner), 404 ``not_a_member``.

``/api/audit?project=&user=&action=&before_id=&limit=``
    ``{entries: [...], next_before_id}``, newest first. Admins see everything; an owner
    sees the projects they own and must name one (``project``; 400 ``project_required``).
    ``user`` is an email or a user id.

Every change is in the audit log (``user.invite``, ``user.update``, ``user.link``,
``member.add``, ``member.role``, ``member.remove``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from autoontology.store.accounts import ROLES, AccountError, Accounts, User

if TYPE_CHECKING:  # pragma: no cover
    from autoontology.web.api import Api
    from autoontology.web.projects import ProjectContext

LINK_PURPOSES = ("magic", "reset", "invite")


def _api_error(exc: AccountError) -> Exception:
    from autoontology.web.api import ApiError

    status = {
        "unknown_user": 404,
        "not_a_member": 404,
        "unknown_project": 404,
        "user_exists": 409,
        "member_exists": 409,
        "last_owner": 409,
    }.get(exc.code, 400)
    return ApiError(status, exc.code, str(exc))


def _bad(detail: str) -> Exception:
    from autoontology.web.api import ApiError

    return ApiError(400, "invalid_request", detail)


def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise _bad("the body must be a JSON object")
    return body


def _opt_str(body: dict[str, Any], name: str, limit: int) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise _bad(f"{name}: a string of at most {limit} characters")
    return value


def _opt_bool(body: dict[str, Any], name: str) -> bool | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _bad(f"{name}: true or false")
    return value


def _role(body: dict[str, Any]) -> str:
    role = body.get("role")
    if role not in ROLES:
        raise _bad(f"role: one of {', '.join(ROLES)}")
    return str(role)


def _actor(api: Api, request: Request) -> Any:
    from autoontology.web.api import ApiError
    from autoontology.web.projects import request_actor

    actor = request_actor(api, request)
    if actor is None:
        raise ApiError(401, "login_required", "sign in first")
    return actor


def _record(
    api: Api,
    request: Request,
    action: str,
    *,
    project_id: str | None = None,
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    from autoontology.web.auth import client_ip
    from autoontology.web.projects import audit

    actor = _actor(api, request)
    audit(
        api.store,
        action,
        actor.name,
        actor.user_id,
        actor.via,
        client_ip(request),
        project_id=project_id,
        target=target,
        detail=detail,
    )


def _issue(
    request: Request,
    background: BackgroundTasks,
    user: User,
    purpose: str,
    *,
    send: bool = True,
) -> dict[str, Any]:
    """A fresh link of *purpose* for *user*; mailed (in the background) when SMTP is set
    up and *send*."""
    from autoontology.web.auth import _lang, service

    auth = service(request)
    raw, expires = auth.magic.issue(auth.accounts, user, purpose)
    link = auth.magic.link(auth.base_url(request), raw, purpose)
    mail = bool(send and auth.mailer.enabled)
    if mail:
        background.add_task(
            auth.mailer.send_link, purpose, user.email, link, lang=_lang(request), name=user.name
        )
    return {
        "email": user.email,
        "purpose": purpose,
        "link": link,
        "expires_at": expires,
        "mail_sent": mail,
    }


def _active_admins(accounts: Accounts) -> list[User]:
    return [u for u in accounts.list_users() if u.is_admin and u.active]


# --- users --------------------------------------------------------------------------------


def users_router(api: Api, prefix: str) -> APIRouter:
    from autoontology.web.api import ApiError
    from autoontology.web.projects import require_installation

    router = APIRouter(prefix=f"{prefix}/users")

    def admin(request: Request) -> None:
        require_installation(request, "users.manage")

    @router.get("")
    def list_users(request: Request) -> dict[str, Any]:
        admin(request)
        accounts = Accounts(api.store)
        projects = {p.id: p for p in api.store.list_projects(include_archived=True)}
        rows = []
        for user in accounts.list_users():
            memberships = [
                {"project_id": pid, "slug": projects[pid].slug, "role": role}
                for pid, role in accounts.projects_of(user.id)
                if pid in projects
            ]
            rows.append({**user.to_dict(), "memberships": memberships})
        return {"users": rows}

    @router.post("", status_code=201)
    def invite_user(
        body: dict[str, Any], request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        admin(request)
        body = _object(body)
        email = _opt_str(body, "email", 320)
        if not email:
            raise _bad("email: required")
        name = _opt_str(body, "name", 200) or ""
        is_admin = bool(_opt_bool(body, "is_admin"))
        send = _opt_bool(body, "send") is not False
        accounts = Accounts(api.store)
        try:
            user = accounts.create_user(email, name=name, is_admin=is_admin)
        except AccountError as exc:
            raise _api_error(exc) from exc
        issued = _issue(request, background, user, "invite", send=send)
        _record(api, request, "user.invite", target=user.email, detail={"admin": user.is_admin})
        return {
            "user": user.to_dict(),
            "invite_link": issued["link"],
            "expires_at": issued["expires_at"],
            "mail_sent": issued["mail_sent"],
        }

    @router.patch("/{user_id}")
    def update_user(user_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
        admin(request)
        body = _object(body)
        unknown = set(body) - {"name", "is_admin", "disabled"}
        if unknown:
            raise _bad(f"unknown fields: {', '.join(sorted(unknown))}")
        name = _opt_str(body, "name", 200)
        is_admin = _opt_bool(body, "is_admin")
        disabled = _opt_bool(body, "disabled")
        accounts = Accounts(api.store)
        user = accounts.get(user_id)
        if user is None:
            raise ApiError(404, "unknown_user", "unknown user")
        loses_admin = user.is_admin and user.active and (is_admin is False or disabled is True)
        if loses_admin and len(_active_admins(accounts)) <= 1:
            raise ApiError(409, "last_admin", "the installation keeps at least one active admin")
        changes: dict[str, Any] = {}
        if name is not None and name.strip() != user.name:
            user = accounts.set_name(user.id, name)
            changes["name"] = user.name
        if is_admin is not None and is_admin != user.is_admin:
            user = accounts.set_admin(user.id, is_admin)
            changes["is_admin"] = is_admin
        if disabled is not None and disabled == user.active:
            user = accounts.disable(user.id) if disabled else accounts.enable(user.id)
            changes["disabled"] = disabled
        if changes:
            _record(api, request, "user.update", target=user.email, detail=changes)
        return user.to_dict()

    @router.post("/{user_id}/link")
    def user_link(
        user_id: str, body: dict[str, Any], request: Request, background: BackgroundTasks
    ) -> dict[str, Any]:
        admin(request)
        body = _object(body)
        purpose = body.get("purpose", "magic")
        if purpose not in LINK_PURPOSES:
            raise _bad(f"purpose: one of {', '.join(LINK_PURPOSES)}")
        send = _opt_bool(body, "send") is True
        user = Accounts(api.store).get(user_id)
        if user is None:
            raise ApiError(404, "unknown_user", "unknown user")
        if not user.active:
            raise ApiError(409, "user_disabled", "the user is disabled: enable them first")
        issued = _issue(request, background, user, str(purpose), send=send)
        _record(api, request, "user.link", target=user.email, detail={"purpose": purpose})
        return issued

    return router


# --- members ------------------------------------------------------------------------------


def add_member_routes(router: APIRouter, api: Api, ctx_dep: Any) -> None:
    """``/{project}/members`` on the projects router."""
    from autoontology.web.api import ApiError

    Ctx = Depends(ctx_dep)  # noqa: N806

    def member(accounts: Accounts, ctx: ProjectContext, user_id: str) -> dict[str, Any]:
        for row in accounts.list_members(ctx.id):
            if row["id"] == user_id:
                return row
        raise ApiError(404, "not_a_member", "not a member of the project")

    @router.get("/{project}/members")
    def list_members(ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("members.manage")
        return {"members": Accounts(api.store).list_members(ctx.id)}

    @router.post("/{project}/members", status_code=201)
    def add_member(
        body: dict[str, Any],
        request: Request,
        background: BackgroundTasks,
        ctx: ProjectContext = Ctx,
    ) -> dict[str, Any]:
        ctx.require("members.manage")
        body = _object(body)
        email = _opt_str(body, "email", 320)
        if not email:
            raise _bad("email: required")
        role = _role(body)
        accounts = Accounts(api.store)
        user = accounts.by_email(email)
        if not ctx.is_admin and (user is None or not user.active):
            raise ApiError(
                403,
                "admin_required_for_new_user",
                "this address has no active account: ask an administrator to invite this person",
            )
        created = user is None
        out: dict[str, Any] = {"created": created, "mail_sent": False}
        try:
            if user is None:
                user = accounts.create_user(email)
            accounts.add_member(ctx.id, user.id, role)
        except AccountError as exc:
            raise _api_error(exc) from exc
        if created:
            issued = _issue(request, background, user, "invite", send=body.get("send") is not False)
            out.update(
                invite_link=issued["link"],
                expires_at=issued["expires_at"],
                mail_sent=issued["mail_sent"],
            )
            ctx.audit("user.invite", target=user.email, detail={"via": "members"})
        ctx.audit("member.add", target=user.email, detail={"role": role})
        out["member"] = member(accounts, ctx, user.id)
        return out

    @router.patch("/{project}/members/{user_id}")
    def set_member_role(
        user_id: str, body: dict[str, Any], ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("members.manage")
        role = _role(_object(body))
        accounts = Accounts(api.store)
        before = member(accounts, ctx, user_id)
        try:
            accounts.set_role(ctx.id, user_id, role)
        except AccountError as exc:
            raise _api_error(exc) from exc
        if before["role"] != role:
            ctx.audit(
                "member.role", target=before["email"], detail={"from": before["role"], "to": role}
            )
        return member(accounts, ctx, user_id)

    @router.delete("/{project}/members/{user_id}")
    def remove_member(user_id: str, ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("members.manage")
        accounts = Accounts(api.store)
        before = member(accounts, ctx, user_id)
        try:
            accounts.remove_member(ctx.id, user_id)
        except AccountError as exc:
            raise _api_error(exc) from exc
        ctx.audit("member.remove", target=before["email"], detail={"role": before["role"]})
        return {"removed": user_id}


# --- audit --------------------------------------------------------------------------------


def audit_router(api: Api, prefix: str) -> APIRouter:
    from autoontology.web.api import ApiError
    from autoontology.web.projects import resolve

    router = APIRouter(prefix=prefix)

    @router.get("/audit")
    def list_audit(
        request: Request,
        project: str | None = Query(None, max_length=80),
        user: str | None = Query(None, max_length=320),
        action: str | None = Query(None, max_length=100),
        before_id: int | None = Query(None, ge=1),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        actor = _actor(api, request)
        project_id = None
        if project:
            ctx = resolve(api, project, request)  # 404 for a non-member
            if not actor.is_admin:
                ctx.require("audit.read")
            project_id = ctx.id
        elif not actor.is_admin:
            raise ApiError(400, "project_required", "name a project you own (?project=)")
        accounts = Accounts(api.store)
        user_id = None
        if user:
            found = accounts.get(user) or accounts.by_email(user)
            if found is None:
                return {"entries": [], "next_before_id": None}
            user_id = found.id
        entries = accounts.list_audit(
            project_id=project_id,
            user_id=user_id,
            action=action or None,
            before_id=before_id,
            limit=limit,
        )
        slugs = {p.id: p.slug for p in api.store.list_projects(include_archived=True)}
        for entry in entries:
            entry["project"] = slugs.get(entry["project_id"]) if entry["project_id"] else None
        next_id = entries[-1]["id"] if len(entries) == limit else None
        return {"entries": entries, "next_before_id": next_id}

    return router
