# SPDX-License-Identifier: MIT
"""Projects in the REST API: the :class:`ProjectContext` dependency every project-scoped
route takes, and the project management routes (``/api/projects``).

Routes
------
Every project-scoped route exists twice, with the same handler:

* canonical: ``/api/projects/{project}/...`` -- ``{project}`` is the project's slug (or
  its id); an unknown one is 404 ``unknown_project``;
* alias: ``/api/...`` -- the default project (the URLs, the CLI and the UI from before
  projects keep working unchanged).

Installation-wide routes have no project: ``/api/status``, ``/api/providers/test``,
``/api/settings`` (models, providers, restart) and the management routes below. The
outside-agent surfaces (``/mcp``, ``/agent/v1``) take the project from the bearer token.

Archived projects are read-only: a write (any method but GET/HEAD/OPTIONS) is 409
``project_archived``, except the routes whose handler is marked
:func:`allowed_when_archived` (matched by the route's endpoint, never by path suffix):
``POST .../archive`` / ``.../unarchive`` and the playground's questions
(``POST .../playground/chat|sql|sparql``), which read the ontology.

Ownership: a route that looks an object up by a store-wide id (a run, a source, a token, a
conversation, a review item) goes through ``ctx.store`` (a
:class:`~autoontology.store.db.ProjectStore`), which answers "missing" for another
project's object -- the route answers 404, never 403, so a project cannot probe another
project's ids.

Permissions
-----------
With ``AUTOONTOLOGY_AUTH=off`` (the default) every caller past the host guard may do
everything; :attr:`ProjectContext.actor` is the tailnet login or ``"local"``.

With ``AUTOONTOLOGY_AUTH=accounts`` :func:`resolve` takes the signed-in user (401
``login_required`` without one), their membership in the project (404 ``unknown_project``
for a non-member: a project's existence is not revealed; admins are owners of every
project) and puts the role on the context; ``ctx.actor`` is the user's email (what
``decided_by`` and the audit log record). Routes call :meth:`ProjectContext.require` with a
permission of :data:`PROJECT_PERMISSIONS` (403 ``forbidden``, the body's ``permission``
names it); installation-wide routes call :func:`require_installation` (admins only). The
role table is :data:`PROJECT_PERMISSIONS`: viewer reads, downloads and asks the
playground; reviewer also decides reviews and applies them; editor also changes sources,
starts builds and exports and manages agent tokens; owner also manages the project and its
members and reads its audit log.

Archived projects: playground questions (:func:`playground_question`) stay allowed unless
``AUTOONTOLOGY_ARCHIVED_PLAYGROUND=deny`` (then 409 ``project_archived`` too).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoontology.store.db import Project, ProjectNotFound, ProjectStore, validate_slug

if TYPE_CHECKING:  # pragma: no cover - api imports this module
    from autoontology.web.api import Api

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: Attribute :func:`allowed_when_archived` sets on a route handler.
ARCHIVED_OK_ATTR = "_autoontology_allowed_when_archived"
#: Attribute :func:`playground_question` sets on a route handler.
PLAYGROUND_ATTR = "_autoontology_playground_question"

# --- roles and permissions ----------------------------------------------------------------

#: Project roles, weakest first (a role has every permission of the roles before it).
ROLE_ORDER = ("viewer", "reviewer", "editor", "owner")

#: The permission table: each project permission and the weakest role that has it. The one
#: place roles are defined -- :meth:`ProjectContext.require`, ``/api/auth/me`` and the tests
#: read it. Admins are owners of every project.
PROJECT_PERMISSIONS: dict[str, str] = {
    "project.read": "viewer",  # every GET of the project: sources, runs, ontology, review list
    "exports.read": "viewer",  # download exports
    "playground.ask": "viewer",  # chat / SQL / SPARQL, the index, conversations (incl. delete)
    "review.decide": "reviewer",  # decide and undo review items
    "review.apply": "reviewer",  # apply runs (decisions -> a new revision)
    "sources.write": "editor",  # add / delete sources, load samples
    "runs.start": "editor",  # build and export runs (and cancelling runs)
    "agent.tokens": "editor",  # create / rotate / revoke outside-agent tokens
    "project.manage": "owner",  # rename, archive, unarchive
    "members.manage": "owner",  # add / change / remove members
    "audit.read": "owner",  # the project's audit log
}

#: Installation-wide permissions (no project): admins only.
INSTALLATION_PERMISSIONS = (
    "project.create",
    "settings.manage",  # settings, provider test, restart
    "users.manage",  # /api/users
    "audit.read_all",  # the whole audit log
)

#: Every permission (documentation and tests).
PERMISSIONS = (*PROJECT_PERMISSIONS, *INSTALLATION_PERMISSIONS)


def role_permissions(role: str | None) -> list[str]:
    """The project permissions of *role* (None = no access), in table order."""
    if role not in ROLE_ORDER:
        return []
    rank = ROLE_ORDER.index(role)
    return [p for p, least in PROJECT_PERMISSIONS.items() if ROLE_ORDER.index(least) <= rank]


def role_allows(role: str | None, permission: str) -> bool:
    least = PROJECT_PERMISSIONS.get(permission)
    if least is None or role not in ROLE_ORDER:
        return False
    return ROLE_ORDER.index(role) >= ROLE_ORDER.index(least)


def _forbidden(permission: str) -> Exception:
    from autoontology.web.api import ApiError

    return ApiError(
        403, "forbidden", f"this needs the {permission!r} permission", permission=permission
    )


@dataclass(frozen=True)
class ProjectContext:
    """The project a request works on, and who asks.

    ``store`` is the project's :class:`ProjectStore` (every read and write limited to
    it); ``actor`` the caller's name (the user's email with accounts on; else the tailnet
    login or ``"local"``) -- it is what ``decided_by`` and the audit log record; ``alias``
    True when the request came through an ``/api/...`` alias of the default project;
    ``role`` the caller's role in the project (admins and accounts-off callers: ``owner``);
    ``user_id`` the signed-in user (None: accounts off, the CLI); ``ip`` the client."""

    store: ProjectStore
    actor: str = "local"
    alias: bool = False
    role: str = "owner"
    is_admin: bool = True
    user_id: str | None = None
    ip: str = ""
    via: str = ""

    @property
    def project(self) -> Project:
        return self.store.project

    @property
    def id(self) -> str:
        return self.store.id

    @property
    def slug(self) -> str:
        return self.store.slug

    def can(self, permission: str) -> bool:
        if self.is_admin:
            return True
        return role_allows(self.role, permission)

    def require(self, permission: str) -> None:
        """403 ``forbidden`` (``permission`` names what was missing) unless the caller's
        role has *permission* in this project (see :data:`PROJECT_PERMISSIONS`)."""
        if not self.can(permission):
            raise _forbidden(permission)

    @property
    def permissions(self) -> list[str]:
        return list(PROJECT_PERMISSIONS) if self.is_admin else role_permissions(self.role)

    def audit(self, action: str, target: str = "", detail: dict[str, Any] | None = None) -> None:
        """Record *action* on this project in the audit log (never a secret in *detail*)."""

        audit(
            self.store.store,
            action,
            self.actor,
            self.user_id,
            self.via,
            self.ip,
            project_id=self.id,
            target=target,
            detail=detail,
        )


def audit(
    store: Any,
    action: str,
    actor: str,
    user_id: str | None,
    via: str = "",
    ip: str = "",
    *,
    project_id: str | None = None,
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append to the audit log: a user's entry carries its ``user_id``; any other caller
    (accounts off, the CLI) its name as ``actor``. The CLI's entries say ``via: cli``."""
    from autoontology.store.accounts import Accounts

    body = dict(detail or {})
    if via in ("cli", "internal"):
        body["via"] = "cli"
    Accounts(store).record(
        action,
        user_id=user_id,
        actor=None if user_id else actor,
        project_id=project_id,
        target=target,
        detail=body,
        ip=ip,
    )


def request_actor(api: Api, request: Request) -> Any:
    """The caller of *request* as an :class:`~autoontology.web.auth.Actor` (None: signed out
    with accounts on). Without the service's sign-in state (the CLI's in-process app) the
    caller is the trusted CLI: an admin named ``cli`` (or ``X-AutoOntology-As``)."""
    from autoontology.web.auth import Actor, cli_actor, current_user

    if getattr(request.app.state, "auth", None) is None:
        if not api.settings.accounts_enabled:
            return Actor(name=api.decided_by(request), is_admin=True, via="off")
        return cli_actor(api, request)
    return current_user(request)


def require_installation(actor: Any, permission: str) -> None:
    """The installation-wide counterpart of :meth:`ProjectContext.require`: *actor* is the
    request (or an :class:`~autoontology.web.auth.Actor`); 401 ``login_required`` when
    signed out, 403 ``forbidden`` unless an admin (accounts off: everyone is)."""
    from autoontology.web.api import ApiError
    from autoontology.web.auth import Actor

    if isinstance(actor, Request):
        api = actor.app.state.api
        actor = request_actor(api, actor)
        if actor is None:
            raise ApiError(401, "login_required", "sign in first")
    if isinstance(actor, Actor) and not actor.is_admin:
        raise _forbidden(permission)


def allowed_when_archived(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a non-GET route *handler* as allowed on an archived project (it reads only:
    the archive/unarchive routes, the playground's questions). Put it under the route
    decorator, so the marked function is the one registered."""
    setattr(handler, ARCHIVED_OK_ATTR, True)
    return handler


def playground_question(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a playground question route (chat, SQL, SPARQL): allowed on an archived project
    unless ``AUTOONTOLOGY_ARCHIVED_PLAYGROUND=deny``."""
    setattr(handler, PLAYGROUND_ATTR, True)
    return allowed_when_archived(handler)


def is_write(request: Request) -> bool:
    """Whether *request* changes the project (refused on an archived one): every method
    but GET/HEAD/OPTIONS, unless the matched route's handler is
    :func:`allowed_when_archived`."""
    if request.method.upper() in SAFE_METHODS:
        return False
    return not getattr(request.scope.get("endpoint"), ARCHIVED_OK_ATTR, False)


def _archived_refused(api: Api, request: Request) -> bool:
    if is_write(request):
        return True
    return api.settings.archived_playground == "deny" and bool(
        getattr(request.scope.get("endpoint"), PLAYGROUND_ATTR, False)
    )


def member_role(api: Api, project_id: str, user: Any) -> str | None:
    """*user*'s role in the project: ``owner`` for an admin, None for a non-member."""
    from autoontology.store.accounts import Accounts

    if user is None or not user.active:
        return None
    if user.is_admin:
        return "owner"
    return Accounts(api.store).role_of(project_id, user.id)


def resolve(api: Api, ref: str | None, request: Request, *, alias: bool = False) -> ProjectContext:
    """The :class:`ProjectContext` of *ref* (a slug or id; None = the default project)
    for *request*: 401 ``login_required`` (accounts on, signed out), 404
    ``unknown_project`` (also for a project the user is not a member of), 409
    ``project_archived`` for a write."""
    from autoontology.web.api import ApiError
    from autoontology.web.auth import client_ip

    actor = request_actor(api, request)
    if actor is None:
        raise ApiError(401, "login_required", "sign in first")
    try:
        view = api.store.project(ref)
    except ProjectNotFound as exc:
        raise ApiError(404, "unknown_project", f"unknown project {str(ref)[:80]!r}") from exc
    role = "owner"
    if not actor.is_admin:
        role = member_role(api, view.id, actor.user) or ""
        if not role:  # hide the project's existence
            raise ApiError(404, "unknown_project", f"unknown project {str(ref)[:80]!r}")
    if view.project.archived and _archived_refused(api, request):
        raise ApiError(
            409,
            "project_archived",
            f"project {view.slug!r} is archived: unarchive it to change it",
        )
    ctx = ProjectContext(
        store=view,
        actor=actor.name,
        alias=alias,
        role=role,
        is_admin=actor.is_admin,
        user_id=actor.user_id,
        ip=client_ip(request),
        via=actor.via,
    )
    ctx.require("project.read")  # every request
    return ctx


def path_project(api: Api) -> Callable[..., ProjectContext]:
    """Dependency of the canonical routes: ``{project}`` from the path."""

    def dependency(project: str, request: Request) -> ProjectContext:
        return resolve(api, project, request)

    return dependency


def default_project(api: Api) -> Callable[..., ProjectContext]:
    """Dependency of the ``/api/...`` aliases: the default project."""

    def dependency(request: Request) -> ProjectContext:
        return resolve(api, None, request, alias=True)

    return dependency


# --- management ---------------------------------------------------------------------------


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=40)
    name: str = Field("", max_length=200)
    description: str = Field("", max_length=2000)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str | None = Field(None, min_length=1, max_length=40)
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=2000)


def _run_brief(run: Any) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def project_dict(api: Api, project: Project) -> dict[str, Any]:
    """One project with its counts (``GET /api/projects`` rows)."""
    view = ProjectStore(api.store, project)
    head = view.latest_snapshot(with_model=False)
    runs = view.list_runs(None, limit=1)
    active = api.runs.busy(project=view.id) is not None or bool(view.active_runs())
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at,
        "archived_at": project.archived_at,
        "archived": project.archived,
        "default": view.is_default,
        "latest_revision": head.revision if head else None,
        "latest_revision_at": head.created_at if head else None,
        "sources": len(view.list_sources()),
        "last_build": _run_brief(view.latest_project_run("build")),
        "last_run": _run_brief(runs[0] if runs else None),
        "active": active,
        "api_base": f"/api/projects/{project.slug}",
    }


def _validation(exc: Exception) -> Any:
    from autoontology.web.api import ApiError, _validation_text

    return ApiError(400, "invalid_request", _validation_text(exc))


def _slug(slug: str) -> str:
    from autoontology.web.api import ApiError

    try:
        return validate_slug(slug)
    except ValueError as exc:
        raise ApiError(400, "invalid_slug", str(exc)) from exc


def rename_project(api: Api, ctx: ProjectContext, patch: ProjectPatch) -> Project:
    """Apply *patch*; a slug change also renames ``exports/<old>/`` to ``exports/<new>/``,
    both or neither. A slug change is refused (409 ``run_active``) while anything else
    uses the project: it takes the run manager's project claim (this service), then the
    global build gate (``hold_engine``) and the project's ``WORKSPACE_LEASE`` lease (every
    process: a CLI build, export or apply holds them), and checks for active runs."""
    from autoontology.engine.build import WORKSPACE_LEASE
    from autoontology.engine.jobs import JobError, hold_engine
    from autoontology.store.db import SourceBusy
    from autoontology.web.api import ApiError
    from autoontology.web.runs import WORKSPACE, RunStartError

    store = api.store
    if patch.name is not None and not patch.name.strip():
        raise ApiError(400, "invalid_request", "name: must not be blank")
    new_slug = _slug(patch.slug) if patch.slug is not None else None
    if new_slug is None or new_slug == ctx.slug:
        try:
            return store.rename_project(ctx.id, name=patch.name, description=patch.description)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
    if store.get_project(new_slug) is not None:
        raise ApiError(409, "project_exists", f"project {new_slug!r} already exists")
    try:
        api.runs.claim(WORKSPACE, "rename", project=ctx.id)
    except RunStartError as exc:
        raise ApiError(
            409, "run_active", f"{exc}; rename the project when it has finished"
        ) from exc
    try:
        with ExitStack() as held:
            try:
                held.enter_context(hold_engine(ctx.store, "rename"))
                held.enter_context(ctx.store.lease([WORKSPACE_LEASE], "rename"))
            except (JobError, SourceBusy) as exc:
                raise ApiError(
                    409, "run_active", f"{exc}; rename the project when it has finished"
                ) from exc
            return _rename_held(api, ctx, patch, new_slug)
    finally:
        api.runs.release(WORKSPACE, project=ctx.id)


def _rename_held(api: Api, ctx: ProjectContext, patch: ProjectPatch, new_slug: str) -> Project:
    """The slug change itself, with every claim and lease of :func:`rename_project` held."""
    from autoontology.engine.export_job import project_exports_dir, rename_project_exports
    from autoontology.web.api import ApiError

    store = api.store
    if ctx.store.active_runs():  # a CLI run of this project
        raise ApiError(409, "run_active", "a run of this project is active: try again later")
    old_dir = project_exports_dir(api.settings, ctx.slug)
    new_dir = project_exports_dir(api.settings, new_slug)
    if new_dir.exists() or new_dir.is_symlink():
        raise ApiError(
            409, "exports_conflict", f"the exports folder {new_dir.name!r} already exists"
        )
    moved = old_dir.is_dir() and rename_project_exports(api.settings, ctx.slug, new_slug)
    try:
        return store.rename_project(
            ctx.id, name=patch.name, slug=new_slug, description=patch.description
        )
    except ValueError as exc:
        if moved:  # put the folder back: both or neither
            rename_project_exports(api.settings, new_slug, ctx.slug)
        code = "project_exists" if "already exists" in str(exc) else "invalid_request"
        raise ApiError(409 if code == "project_exists" else 400, code, str(exc)) from exc
    except BaseException:
        if moved:
            rename_project_exports(api.settings, new_slug, ctx.slug)
        raise


def _perms(actor: Any, role: str) -> list[str]:
    return list(PROJECT_PERMISSIONS) if actor.is_admin else role_permissions(role)


def build_router(api: Api, prefix: str) -> APIRouter:
    """``/api/projects`` (list, create) and ``/api/projects/{project}`` (show, patch,
    archive, unarchive). No delete in v1: archive keeps everything."""
    from autoontology.web.api import ApiError

    router = APIRouter(prefix=f"{prefix}/projects")
    ctx_dep = path_project(api)

    @router.get("")
    def list_projects(request: Request, include_archived: bool = True) -> dict[str, Any]:
        actor = request_actor(api, request)
        if actor is None:
            raise ApiError(401, "login_required", "sign in first")
        projects = api.store.list_projects(include_archived=include_archived)
        roles: dict[str, str] = {}
        if not actor.is_admin:
            from autoontology.store.accounts import Accounts

            roles = dict(Accounts(api.store).projects_of(actor.user_id)) if actor.user else {}
            projects = [p for p in projects if p.id in roles]
        rows = []
        for p in projects:
            role = "owner" if actor.is_admin else roles[p.id]
            rows.append({**project_dict(api, p), "role": role, "permissions": _perms(actor, role)})
        return {"projects": rows, "default": api.store.default_project().slug}

    @router.post("", status_code=201)
    def create_project(body: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            req = ProjectIn.model_validate(body)
        except ValidationError as exc:
            raise _validation(exc) from exc
        require_installation(request, "project.create")
        slug = _slug(req.slug)
        try:
            view = api.store.create_project(
                slug, name=req.name.strip() or None, description=req.description
            )
        except ValueError as exc:
            raise ApiError(409, "project_exists", str(exc)) from exc
        actor = request_actor(api, request)
        if actor is not None and actor.user is not None:  # the creator owns it
            from autoontology.store.accounts import Accounts

            Accounts(api.store).add_member(view.id, actor.user.id, "owner")
        if actor is not None:
            from autoontology.web.auth import client_ip

            audit(
                api.store,
                "project.create",
                actor.name,
                actor.user_id,
                actor.via,
                client_ip(request),
                project_id=view.id,
                target=view.slug,
            )
        return project_dict(api, view.project)

    @router.get("/{project}")
    def show_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        return {**project_dict(api, ctx.project), "role": ctx.role, "permissions": ctx.permissions}

    @router.patch("/{project}")
    def patch_project(
        body: dict[str, Any],
        ctx: ProjectContext = Depends(ctx_dep),  # noqa: B008
    ) -> dict[str, Any]:
        ctx.require("project.manage")
        try:
            patch = ProjectPatch.model_validate(body)
        except ValidationError as exc:
            raise _validation(exc) from exc
        before = ctx.slug
        project = rename_project(api, ctx, patch)
        ctx.audit(
            "project.rename",
            target=project.slug,
            detail={k: v for k, v in patch.model_dump().items() if v is not None}
            | ({"old_slug": before} if before != project.slug else {}),
        )
        return project_dict(api, project)

    @router.post("/{project}/archive")
    @allowed_when_archived
    def archive_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        ctx.require("project.manage")
        try:
            project = api.store.archive_project(ctx.id)
        except ValueError as exc:
            raise ApiError(409, "default_project", str(exc)) from exc
        ctx.audit("project.archive", target=ctx.slug)
        return project_dict(api, project)

    @router.post("/{project}/unarchive")
    @allowed_when_archived
    def unarchive_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        ctx.require("project.manage")
        project = api.store.unarchive_project(ctx.id)
        ctx.audit("project.unarchive", target=ctx.slug)
        return project_dict(api, project)

    from autoontology.web.accounts_api import add_member_routes

    add_member_routes(router, api, ctx_dep)
    return router
