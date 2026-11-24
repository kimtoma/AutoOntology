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

The permission hook
-------------------
There are no accounts yet. The service listens on 127.0.0.1 or a tailnet, and it may
also be served behind a public, password-gated gateway: in v1 anyone past that gate (or
on the tailnet) can read, change and manage every project, and change the installation's
settings. The dependency is where accounts go: :attr:`ProjectContext.actor` is the
caller's identity today (the tailnet login ``tailscale serve`` adds, else ``"local"``), and
:meth:`ProjectContext.require` is the single check a route will call -- e.g.
``ctx.require("review.decide")`` -- once memberships exist. :func:`resolve` already asks
for ``project.read`` on every request, so read access can be enforced in one place; the
installation-wide routes (settings, provider test, creating a project) call
:func:`require_installation`. In v1 both allow everything;
adding accounts means resolving the actor to a user in :func:`resolve` and making
``require`` consult the user's role in ``ctx.project``, without changing route URLs.
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

#: Permissions a route may ask :meth:`ProjectContext.require` for (documentation; v1
#: grants all of them).
PERMISSIONS = (
    "project.create",
    "project.read",
    "project.manage",
    "sources.write",
    "runs.start",
    "review.decide",
    "exports.read",
    "playground.ask",
    "agent.tokens",
    "settings.manage",
)


@dataclass(frozen=True)
class ProjectContext:
    """The project a request works on, and who asks.

    ``store`` is the project's :class:`ProjectStore` (every read and write limited to
    it); ``actor`` the caller (tailnet login or ``"local"``); ``alias`` True when the
    request came through an ``/api/...`` alias of the default project."""

    store: ProjectStore
    actor: str = "local"
    alias: bool = False

    @property
    def project(self) -> Project:
        return self.store.project

    @property
    def id(self) -> str:
        return self.store.id

    @property
    def slug(self) -> str:
        return self.store.slug

    def require(self, permission: str) -> None:
        """Raise when :attr:`actor` lacks *permission* in this project (the hook for
        accounts; v1 has none and allows everything)."""
        del permission


def require_installation(actor: str, permission: str) -> None:
    """The installation-wide counterpart of :meth:`ProjectContext.require` (creating a
    project; later the settings and user management): v1 allows everything."""
    del actor, permission


def allowed_when_archived(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a non-GET route *handler* as allowed on an archived project (it reads only:
    the archive/unarchive routes, the playground's questions). Put it under the route
    decorator, so the marked function is the one registered."""
    setattr(handler, ARCHIVED_OK_ATTR, True)
    return handler


def is_write(request: Request) -> bool:
    """Whether *request* changes the project (refused on an archived one): every method
    but GET/HEAD/OPTIONS, unless the matched route's handler is
    :func:`allowed_when_archived`."""
    if request.method.upper() in SAFE_METHODS:
        return False
    return not getattr(request.scope.get("endpoint"), ARCHIVED_OK_ATTR, False)


def resolve(api: Api, ref: str | None, request: Request, *, alias: bool = False) -> ProjectContext:
    """The :class:`ProjectContext` of *ref* (a slug or id; None = the default project)
    for *request*: 404 ``unknown_project``, 409 ``project_archived`` for a write."""
    from autoontology.web.api import ApiError

    try:
        view = api.store.project(ref)
    except ProjectNotFound as exc:
        raise ApiError(404, "unknown_project", f"unknown project {str(ref)[:80]!r}") from exc
    if view.project.archived and is_write(request):
        raise ApiError(
            409,
            "project_archived",
            f"project {view.slug!r} is archived: unarchive it to change it",
        )
    ctx = ProjectContext(store=view, actor=api.decided_by(request), alias=alias)
    ctx.require("project.read")  # every request: the place to enforce read access later
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


def build_router(api: Api, prefix: str) -> APIRouter:
    """``/api/projects`` (list, create) and ``/api/projects/{project}`` (show, patch,
    archive, unarchive). No delete in v1: archive keeps everything."""
    from autoontology.web.api import ApiError

    router = APIRouter(prefix=f"{prefix}/projects")
    ctx_dep = path_project(api)

    @router.get("")
    def list_projects(include_archived: bool = True) -> dict[str, Any]:
        projects = api.store.list_projects(include_archived=include_archived)
        return {
            "projects": [project_dict(api, p) for p in projects],
            "default": api.store.default_project().slug,
        }

    @router.post("", status_code=201)
    def create_project(body: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            req = ProjectIn.model_validate(body)
        except ValidationError as exc:
            raise _validation(exc) from exc
        require_installation(api.decided_by(request), "project.create")
        slug = _slug(req.slug)
        try:
            view = api.store.create_project(
                slug, name=req.name.strip() or None, description=req.description
            )
        except ValueError as exc:
            raise ApiError(409, "project_exists", str(exc)) from exc
        return project_dict(api, view.project)

    @router.get("/{project}")
    def show_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        return project_dict(api, ctx.project)

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
        return project_dict(api, rename_project(api, ctx, patch))

    @router.post("/{project}/archive")
    @allowed_when_archived
    def archive_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        ctx.require("project.manage")
        try:
            return project_dict(api, api.store.archive_project(ctx.id))
        except ValueError as exc:
            raise ApiError(409, "default_project", str(exc)) from exc

    @router.post("/{project}/unarchive")
    @allowed_when_archived
    def unarchive_project(ctx: ProjectContext = Depends(ctx_dep)) -> dict[str, Any]:  # noqa: B008
        ctx.require("project.manage")
        return project_dict(api, api.store.unarchive_project(ctx.id))

    return router
