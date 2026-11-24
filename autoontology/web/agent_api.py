# SPDX-License-Identifier: MIT
"""Access for outside agents (Claude Code, Claude Desktop, Cursor, scripts): read the
ontology and its objects, and -- with a token scope that allows it -- submit actions.

One tool layer (:data:`TOOLS`, :meth:`AgentService.run_tool`) behind two surfaces:

* ``POST /mcp`` -- MCP over Streamable HTTP, hand-rolled JSON-RPC 2.0: JSON answers only
  (no SSE stream, no session), batches, notifications answered ``202`` with no body;
  ``initialize`` / ``ping`` / ``tools/list`` / ``tools/call``. A tool's failure (unknown
  concept, a refused SPARQL query, a pinned revision) is a result with ``isError: true``;
  an unknown method is ``-32601``, unparsable JSON ``-32700``. Other HTTP methods: 405.
* ``/agent/v1/*`` -- the same tools as REST (``GET /openapi.json`` describes them), plus
  ``GET /exports/{rev}.zip``.

Every request needs ``Authorization: Bearer aok_...`` -- from localhost too -- and is
answered 401 without a valid (unrevoked, unexpired) token. A token with a revision pin is
served at that revision only (another explicit revision: ``revision_pinned``). The object
tools (``list_object_types``, ``search_objects``, ``get_object``, ``list_functions``,
``call_function``) read live source rows
through the object types bound to the latest snapshot -- objects have no revisions -- so a
pinned token may use them only while its pin is the latest revision. The action tools
(``list_actions``, ``preview_action``, ``submit_action``, ``get_action_run``) run
:mod:`autoontology.logic.actions` as the token's creator (their current role; accounts
off or no creator: owner) with the token's scopes: ``read`` (every token) refuses
``submit_action`` (``scope_required``); ``propose`` submits runs that always wait for
approval; ``execute`` follows the action's approval rules; ``action:<name>`` entries limit
both to those actions; an action's ``agent`` policy caps the mode (``none`` refuses).
``submit_action`` is the only write tool: audited (``action.submit``, ``via: agent``,
``token_id``) besides ``agent_calls``. Per token
at most :func:`rate_per_min` requests a minute (429 + ``Retry-After``), request bodies up
to :data:`MAX_BODY_BYTES` (413). Every tool call is recorded in ``agent_calls``; the raw
token is never logged or stored.

The token management routes (``/api/agent/*``, the UI's session like every ``/api``
route) are added to the ``/api`` router by :func:`add_management_routes`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoontology import __version__
from autoontology.objects.reader import ObjectError
from autoontology.playground.sparql import DEFAULT_LIMIT, MAX_LIMIT, MAX_QUERY_CHARS, SparqlError
from autoontology.store.agent_tokens import (
    AgentToken,
    AgentTokens,
    TokenError,
    allowed_actions,
    normalize_scopes,
    write_mode,
)
from autoontology.store.db import ProjectNotFound, ProjectStore
from autoontology.web.api import (
    CONCEPT_ID_RE,
    Api,
    ApiError,
    _validation_text,
    concept_body,
    concept_listing,
    export_listing,
    export_zip_response,
    ontology_summary,
)
from autoontology.web.projects import ProjectContext

logger = logging.getLogger("autoontology.web")

PROTOCOL = "2025-06-18"
#: protocol versions a client may ask for in ``initialize`` (echoed back)
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_PATH = "/mcp"
REST_PREFIX = "/agent/v1"
MAX_BODY_BYTES = 1024 * 1024
#: JSON-RPC messages one MCP request may batch
MAX_BATCH = 20
DEFAULT_RATE_PER_MIN = 120
RATE_WINDOW_S = 60.0
RATE_ENV = "AUTOONTOLOGY_AGENT_RATE_PER_MIN"


def rate_per_min() -> int:
    """Requests a token may make per minute (``AUTOONTOLOGY_AGENT_RATE_PER_MIN``)."""
    raw = os.environ.get(RATE_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_RATE_PER_MIN
    except ValueError:
        value = DEFAULT_RATE_PER_MIN
    return max(1, value)


# --- tools -------------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListRevisionsArgs(_Args):
    limit: int = Field(50, ge=1, le=500)


class SummaryArgs(_Args):
    revision: int | None = Field(None, ge=1)


class SearchArgs(_Args):
    query: str = Field("", max_length=200)
    limit: int = Field(10, ge=1, le=50)
    kind: str = Field("", max_length=40)
    revision: int | None = Field(None, ge=1)


class ConceptArgs(_Args):
    id: str | None = Field(None, max_length=120)
    name: str | None = Field(None, min_length=1, max_length=200)
    revision: int | None = Field(None, ge=1)


class SparqlArgs(_Args):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    revision: int | None = Field(None, ge=1)


class ExportsArgs(_Args):
    pass


class ObjectTypesArgs(_Args):
    status: Literal["active", "proposed", "broken", "retired", "all"] = "active"


class SearchObjectsArgs(_Args):
    type: str = Field(min_length=1, max_length=200)
    query: str = Field("", max_length=200)
    filters: dict[str, str] = Field(default_factory=dict, max_length=5)
    limit: int = Field(10, ge=1, le=50)
    offset: int = Field(0, ge=0, le=1_000_000)


class GetObjectArgs(_Args):
    type: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=200)


class ListFunctionsArgs(_Args):
    type: str | None = Field(None, min_length=1, max_length=200)


class CallFunctionArgs(_Args):
    name: str = Field(min_length=1, max_length=200)
    type: str | None = Field(None, min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


class ListActionsArgs(_Args):
    type: str | None = Field(None, min_length=1, max_length=200)


class PreviewActionArgs(_Args):
    name: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, max_length=20)


class SubmitActionArgs(PreviewActionArgs):
    idempotency_key: str | None = Field(None, min_length=1, max_length=200)


class GetActionRunArgs(_Args):
    id: str = Field(min_length=1, max_length=80)


_REVISION = {
    "type": "integer",
    "minimum": 1,
    "description": "Snapshot revision (default: the latest, or the token's pinned one).",
}

_ACTION_PROPS = {
    "name": {"type": "string", "description": "Action name (from list_actions)."},
    "key": {"type": "string", "description": "The object's key value."},
    "params": {
        "type": "object",
        "maxProperties": 20,
        "description": "Parameter values (defaults apply to the ones left out).",
    },
}

#: name, description, input schema and argument model of every tool, in listing order
TOOL_SPECS: list[tuple[str, str, dict[str, Any], type[_Args]]] = [
    (
        "list_revisions",
        "List the ontology snapshot revisions, newest first: time, the run that wrote "
        "each, counts and the sources it includes.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}},
        ListRevisionsArgs,
    ),
    (
        "get_ontology_summary",
        "Summary of one ontology snapshot: workspace, base IRI, sources, counts of "
        "classes/individuals/properties and alignment counts.",
        {"revision": _REVISION},
        SummaryArgs,
    ),
    (
        "search_concepts",
        "Search the concepts (structured Term classes and document concepts) by name, "
        "synonym or definition. An empty query lists them in order.",
        {
            "query": {"type": "string", "maxLength": 200, "description": "Search text."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "kind": {
                "type": "string",
                "description": "Only this kind (term, entity, process, rule, metric, ...).",
            },
            "revision": _REVISION,
        },
        SearchArgs,
    ),
    (
        "get_concept",
        "One concept by id (term-... / concept-..., from search_concepts) or by exact "
        "name or synonym: definition, synonyms, relations, evidence quotes with their "
        "documents, alignments and review decisions (decisions show the current review "
        "state, also for an older revision).",
        {
            "id": {"type": "string", "description": "Concept id, e.g. term-t-1."},
            "name": {"type": "string", "description": "Exact name or synonym (any case)."},
            "revision": _REVISION,
        },
        ConceptArgs,
    ),
    (
        "sparql_query",
        "Run a read-only SPARQL query (SELECT, ASK, CONSTRUCT, DESCRIBE) over the "
        "snapshot's RDF/OWL graph. SERVICE, FROM and LOAD are refused; queries time out "
        "and rows are capped.",
        {
            "query": {"type": "string", "maxLength": MAX_QUERY_CHARS},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
            },
            "revision": _REVISION,
        },
        SparqlArgs,
    ),
    (
        "list_exports",
        "List the export folders (RDF/OWL, GraphML, Ossie, OKF files) per revision. "
        f"The REST API downloads one as a zip: GET {REST_PREFIX}/exports/<rev>.zip.",
        {},
        ExportsArgs,
    ),
    (
        "list_object_types",
        "List the object types: classes bound to a source table, with their key and title "
        "columns, columns and links. Objects are the table's rows; only active types can "
        "be read.",
        {
            "status": {
                "type": "string",
                "enum": ["active", "proposed", "broken", "retired", "all"],
                "default": "active",
            }
        },
        ObjectTypesArgs,
    ),
    (
        "search_objects",
        "Search the objects (source rows) of an object type by key or title text, with "
        "optional exact column filters. Values include edits made by actions; edited "
        "properties are marked (conflict: the source changed after the edit).",
        {
            "type": {
                "type": "string",
                "description": "Object type: its name (term-...), label or table, e.g. invoices.",
            },
            "query": {"type": "string", "maxLength": 200, "description": "Key or title text."},
            "filters": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "maxProperties": 5,
                "description": "Column = value filters (compared as text).",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
        SearchObjectsArgs,
    ),
    (
        "get_object",
        "One object by its key: values (with edits), the source values, edit marks, its "
        "links to other object types, the edit history and the values of the type's "
        "active functions (computed).",
        {
            "type": {"type": "string", "description": "Object type: name, label or table."},
            "key": {"type": "string", "description": "The object's key value."},
        },
        GetObjectArgs,
    ),
    (
        "list_functions",
        "List the active functions: computed values and yes/no checks defined on an object "
        "type (name, description, result type, parameters and the expression).",
        {
            "type": {
                "type": "string",
                "description": "Only this object type (name, label or table).",
            }
        },
        ListFunctionsArgs,
    ),
    (
        "call_function",
        "Evaluate an active function on one object (from list_functions): its value and "
        "type, or an error. Read-only.",
        {
            "name": {"type": "string", "description": "Function name."},
            "type": {"type": "string", "description": "Object type (optional check)."},
            "key": {"type": "string", "description": "The object's key value."},
            "params": {
                "type": "object",
                "maxProperties": 20,
                "description": "Parameter values, when the function has parameters.",
            },
        },
        CallFunctionArgs,
    ),
    (
        "list_actions",
        "List the active actions: operations on objects of a type (parameters, "
        "preconditions, the properties they set, approval and agent policy). Read-only.",
        {
            "type": {
                "type": "string",
                "description": "Only this object type (name, label or table).",
            }
        },
        ListActionsArgs,
    ),
    (
        "preview_action",
        "Preview an action on one object without running it: parameters as coerced, each "
        "precondition's result, the effects (old -> new value), whether approval is needed "
        "and whether this token may submit it. Read-only.",
        _ACTION_PROPS,
        PreviewActionArgs,
    ),
    (
        "submit_action",
        "WRITE: submit an action on one object (needs a token with the propose or execute "
        "scope, and an action whose agent policy allows it). With propose the run always "
        "waits for a person's approval; with execute it follows the action's approval "
        "rules. Values go to AutoOntology's edit layer (sources are never written) and "
        "every run can be reverted. Pass idempotency_key to make retries safe.",
        {
            **_ACTION_PROPS,
            "idempotency_key": {
                "type": "string",
                "maxLength": 200,
                "description": "A retry with the same key returns the first run.",
            },
        },
        SubmitActionArgs,
    ),
    (
        "get_action_run",
        "One action run by id (from submit_action): its status (applied, pending_approval, "
        "rejected, failed, reverted), approval, effects and notifications. Read-only.",
        {"id": {"type": "string", "description": "The run id (act_...)."}},
        GetActionRunArgs,
    ),
]

_REQUIRED = {
    "sparql_query": ["query"],
    "search_concepts": ["query"],
    "search_objects": ["type"],
    "get_object": ["type", "key"],
    "call_function": ["name", "key"],
    "preview_action": ["name", "key"],
    "submit_action": ["name", "key"],
    "get_action_run": ["id"],
}
#: tools that change data (``agent/tools`` lists them; their calls are audited)
WRITE_TOOLS = frozenset({"submit_action"})

TOOLS: list[dict[str, Any]] = [
    {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            **({"required": _REQUIRED[name]} if name in _REQUIRED else {}),
            "additionalProperties": False,
        },
    }
    for name, description, props, _ in TOOL_SPECS
]
_MODELS: dict[str, type[_Args]] = {name: model for name, _, _, model in TOOL_SPECS}


class ToolError(Exception):
    """A tool's domain failure: an ``isError`` result (MCP) or an error status (REST)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.message}


class UnknownTool(Exception):
    pass


# --- service -----------------------------------------------------------------------------


class AgentService:
    """The tools over the API state, plus each token's rate window."""

    def __init__(
        self, api: Api, sparql: Callable[[], Any], mailer: Callable[[], Any] | None = None
    ) -> None:
        self.api = api
        self._sparql = sparql
        self._mailer = mailer or (lambda: None)
        self._hits: dict[str, deque[float]] = {}
        self._hits_lock = threading.Lock()

    @property
    def tokens(self) -> AgentTokens:
        return AgentTokens(self.api.store)

    # --- auth and limits ------------------------------------------------------------

    def authenticate(self, request: Request) -> AgentToken | None:
        header = request.headers.get("authorization") or ""
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            return None
        token = self.tokens.lookup(raw.strip())
        if token is None or not self.creator_allows(token):
            return None
        return token

    def creator_allows(self, token: AgentToken) -> bool:
        """With accounts on, a token lives on its creator's access: refused once the
        creator is disabled, gone or no longer at least a viewer of the token's project.
        Tokens without a creator (from before accounts, or the CLI's) keep working."""
        if not self.api.settings.accounts_enabled or not token.created_by:
            return True
        from autoontology.store.accounts import Accounts
        from autoontology.web.projects import member_role

        user = Accounts(self.api.store).get(token.created_by)
        return member_role(self.api, token.project_id, user) is not None

    def creator_role(self, token: AgentToken) -> str | None:
        """The role a token acts with: its creator's current role in the token's project
        (accounts off, or a token without a creator -- the CLI's -- : owner; None: the
        creator lost access)."""
        if not self.api.settings.accounts_enabled or not token.created_by:
            return "owner"
        from autoontology.store.accounts import Accounts
        from autoontology.web.projects import member_role

        user = Accounts(self.api.store).get(token.created_by)
        return member_role(self.api, token.project_id, user)

    def caller(self, token: AgentToken) -> Any:
        """The :class:`~autoontology.logic.actions.Caller` of an action tool."""
        from autoontology.logic.actions import Caller

        role = self.creator_role(token)
        if role is None:
            raise ToolError(403, "forbidden", "the token's creator has no access any more")
        return Caller(
            name=f"agent:{token.id}",
            role=role,
            four_eyes=bool(self.api.settings.accounts_enabled),
            via="agent",
            token_id=token.id,
            agent_mode=write_mode(token.scopes),
            allowed_actions=allowed_actions(token.scopes),
        )

    def retry_after(self, token_id: str) -> int | None:
        """None when *token_id* may make another request now (it is counted), else the
        seconds until it may."""
        limit, now = rate_per_min(), time.monotonic()
        with self._hits_lock:
            hits = self._hits.setdefault(token_id, deque())
            while hits and now - hits[0] >= RATE_WINDOW_S:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + RATE_WINDOW_S - now))
            hits.append(now)
        return None

    def forget(self, token_id: str) -> None:
        """Drop *token_id*'s rate window (the token was rotated or revoked)."""
        with self._hits_lock:
            self._hits.pop(token_id, None)

    # --- tools ------------------------------------------------------------------------

    def project(self, token: AgentToken) -> ProjectStore:
        """The project *token* belongs to: every tool reads only its snapshots and
        exports, and its ``revision_pin`` is a revision of that project. (A token whose
        project is gone reads as unknown.)"""
        try:
            return self.api.store.project(token.project_id or None)
        except ProjectNotFound as exc:
            raise ToolError(404, "unknown_project", "this token's project is gone") from exc

    def latest_revision(self, token: AgentToken | None = None) -> int | None:
        view = self.project(token) if token is not None else self.api.store.project()
        return view.latest_revision()

    def run_tool(self, token: AgentToken, name: str, arguments: Any) -> dict[str, Any]:
        """*name*'s result (always with ``revision``); :class:`ToolError` for a domain
        failure, :class:`UnknownTool` for a name that is no tool. Blocking."""
        model = _MODELS.get(name)
        if model is None:
            raise UnknownTool(name)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ToolError(400, "invalid_arguments", "arguments must be an object")
        try:
            args = model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(400, "invalid_arguments", _validation_text(exc)) from exc
        try:
            return getattr(self, f"_tool_{name}")(token, args)
        except ApiError as exc:
            raise ToolError(exc.status, exc.error, exc.detail) from exc
        except SparqlError as exc:
            raise ToolError(exc.status, exc.code, exc.message) from exc
        except ObjectError as exc:
            raise ToolError(exc.status, exc.code, exc.message) from exc

    def _revision(self, token: AgentToken, asked: int | None) -> int | None:
        pin = token.revision_pin
        if pin is not None and asked is not None and asked != pin:
            raise ToolError(
                403, "revision_pinned", f"this token is pinned to revision {pin} (asked {asked})"
            )
        return asked if asked is not None else pin

    def _served(self, token: AgentToken) -> int | None:
        return token.revision_pin if token.revision_pin is not None else self.latest_revision(token)

    def _tool_list_revisions(self, token: AgentToken, args: ListRevisionsArgs) -> dict[str, Any]:
        from autoontology.web.revisions import revision_list, revision_row

        view = self.project(token)
        if token.revision_pin is not None:
            snap = view.get_snapshot(token.revision_pin, with_model=False)
            rows = [revision_row(view, snap)] if snap is not None else []
        else:
            rows = revision_list(view, args.limit)
        return {"revision": self._served(token), "revisions": rows}

    def _tool_get_ontology_summary(self, token: AgentToken, args: SummaryArgs) -> dict[str, Any]:
        return ontology_summary(
            self.api, self._revision(token, args.revision), project=self.project(token)
        )

    def _tool_search_concepts(self, token: AgentToken, args: SearchArgs) -> dict[str, Any]:
        found = concept_listing(
            self.api,
            q=args.query,
            kind=args.kind,
            page=1,
            size=args.limit,
            revision=self._revision(token, args.revision),
            project=self.project(token),
        )
        return {k: found[k] for k in ("revision", "items", "total")}

    def _tool_get_concept(self, token: AgentToken, args: ConceptArgs) -> dict[str, Any]:
        revision = self._revision(token, args.revision)
        if args.id:
            if not CONCEPT_ID_RE.fullmatch(args.id):
                raise ToolError(400, "invalid_concept_id", f"invalid concept id {args.id!r}")
            return concept_body(self.api, args.id, revision, project=self.project(token))
        if not args.name:
            raise ToolError(400, "invalid_arguments", "give the concept's id or name")
        _, _, index = self.api.concepts(revision, project=self.project(token))
        needle = args.name.strip().casefold()
        match = next((i for i in index.items if i["name"].casefold() == needle), None) or next(
            (
                i
                for i in index.items
                if any(str(s).casefold() == needle for s in i.get("synonyms") or [])
            ),
            None,
        )
        if match is None:
            raise ToolError(404, "unknown_concept", f"no concept named {args.name[:80]!r}")
        return concept_body(self.api, match["id"], revision, project=self.project(token))

    def _tool_sparql_query(self, token: AgentToken, args: SparqlArgs) -> dict[str, Any]:
        from autoontology.web.playground_api import sparql_answer

        runner = self._sparql()
        if runner is None:
            raise ToolError(503, "sparql_unavailable", "the SPARQL runner is not running")
        return sparql_answer(
            self.api,
            runner,
            args.query,
            limit=args.limit,
            revision=self._revision(token, args.revision),
            project=self.project(token),
        )

    def _tool_list_exports(self, token: AgentToken, args: ExportsArgs) -> dict[str, Any]:
        exports = export_listing(self.api.settings, self.project(token))
        if token.revision_pin is not None:
            exports = [e for e in exports if e["revision"] == token.revision_pin]
        return {"revision": self._served(token), "exports": exports}

    def _objects_view(self, token: AgentToken) -> ProjectStore:
        """The token's project for an object tool: objects are live (bound to the latest
        snapshot), so a pinned token may read them only while its pin is the latest."""
        view = self.project(token)
        latest = view.latest_revision()
        if token.revision_pin is not None and token.revision_pin != latest:
            raise ToolError(
                403,
                "revision_pinned",
                f"this token is pinned to revision {token.revision_pin}; objects are read "
                f"live through the latest revision (r{latest})",
            )
        return view

    def _tool_list_object_types(self, token: AgentToken, args: ObjectTypesArgs) -> dict[str, Any]:
        from autoontology.objects import service as objects

        view = self._objects_view(token)
        status = None if args.status == "all" else args.status
        types = objects.list_types(view, status=status)
        return {"revision": self._served(token), "types": [t.to_dict() for t in types]}

    def _tool_search_objects(self, token: AgentToken, args: SearchObjectsArgs) -> dict[str, Any]:
        from autoontology.objects import service as objects

        found = objects.search(
            self.api.settings,
            self._objects_view(token),
            args.type,
            q=args.query,
            filters=args.filters,
            limit=args.limit,
            offset=args.offset,
        )
        return {"revision": self._served(token), **found}

    def _tool_get_object(self, token: AgentToken, args: GetObjectArgs) -> dict[str, Any]:
        from autoontology.logic import functions
        from autoontology.objects import service as objects

        view = self._objects_view(token)
        found = objects.get_object(self.api.settings, view, args.type, args.key)
        functions.attach_computed(self.api.settings, view, found["type"]["name"], [found])
        return {"revision": self._served(token), **found}

    def _tool_list_functions(self, token: AgentToken, args: ListFunctionsArgs) -> dict[str, Any]:
        from autoontology.logic import functions

        found = functions.list_active(self._objects_view(token), args.type)
        return {"revision": self._served(token), "functions": found}

    def _tool_call_function(self, token: AgentToken, args: CallFunctionArgs) -> dict[str, Any]:
        from autoontology.logic import functions

        found = functions.call(
            self.api.settings,
            self._objects_view(token),
            args.name,
            args.key,
            params=args.params,
            type_name=args.type,
        )
        return {"revision": self._served(token), **found}

    def _tool_list_actions(self, token: AgentToken, args: ListActionsArgs) -> dict[str, Any]:
        from autoontology.logic import actions

        found = actions.list_actions(self._objects_view(token), args.type)
        return {"revision": self._served(token), "actions": found}

    def _tool_preview_action(self, token: AgentToken, args: PreviewActionArgs) -> dict[str, Any]:
        from autoontology.logic import actions

        found = actions.preview(
            self.api.settings,
            self._objects_view(token),
            args.name,
            args.key,
            args.params,
            self.caller(token),
        )
        return {"revision": self._served(token), "preview": found}

    def _tool_submit_action(self, token: AgentToken, args: SubmitActionArgs) -> dict[str, Any]:
        from autoontology.logic import actions

        caller = self.caller(token)
        run, created = actions.submit(
            self.api.settings,
            self._objects_view(token),
            args.name,
            args.key,
            args.params,
            caller,
            idempotency_key=args.idempotency_key,
            mailer=self._mailer(),
        )
        return {
            "revision": self._served(token),
            "run": actions.run_dict(run, caller),
            "created": created,
        }

    def _tool_get_action_run(self, token: AgentToken, args: GetActionRunArgs) -> dict[str, Any]:
        from autoontology.logic import actions

        view = self.project(token)
        run = view.action_runs().get(args.id)
        if run is None:
            raise ToolError(404, "unknown_run", f"no action run {args.id[:80]!r}")
        return {"revision": self._served(token), "run": actions.run_dict(run, self.caller(token))}

    def instructions(self, token: AgentToken) -> str:
        ws = self.project(token).name
        latest = self.latest_revision(token)
        text = f"AutoOntology: the versioned ontology of project '{ws}'. "
        if token.revision_pin is not None:
            text += (
                f"This token is pinned to r{token.revision_pin}: every tool answers from that "
                "revision. "
            )
        elif latest is not None:
            text += f"The latest revision is r{latest}; tools answer from it by default. "
        else:
            text += "No ontology has been built yet. "
        mode = write_mode(token.scopes)
        if mode == "execute":
            scope = (
                "This token has the execute scope: submit_action runs an action under the "
                "action's own approval rules. "
            )
        elif mode == "propose":
            scope = (
                "This token has the propose scope: submit_action creates a run that waits "
                "for a person's approval. "
            )
        else:
            scope = "This token is read-only: submit_action is refused. "
        allowed = allowed_actions(token.scopes)
        if allowed and mode:
            scope += f"It may submit only these actions: {', '.join(allowed)}. "
        return (
            text + "Every tool reads, except submit_action, which writes (audited, revertible; "
            "sources are never written). "
            + scope
            + (
                "Start with get_ontology_summary or search_concepts, then get_concept for "
                "definitions, relations and evidence; sparql_query runs SPARQL over the "
                "RDF/OWL graph. list_object_types, search_objects and get_object read the "
                "business objects (source rows) of bound classes; list_functions and "
                "call_function evaluate their defined functions; list_actions, "
                "preview_action, submit_action and get_action_run run their actions "
                "(always preview first)."
            )
        )


# --- HTTP helpers ------------------------------------------------------------------------


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized", "detail": "a valid bearer token is required"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="autoontology"'},
    )


def _error(status: int, code: str, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)


def _too_large() -> JSONResponse:
    return _error(413, "too_large", f"request body exceeds {MAX_BODY_BYTES // 1024} KB")


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None when it exceeds :data:`MAX_BODY_BYTES`."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BODY_BYTES:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            return None
    return bytes(buf)


def _rpc_error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _ints(params: dict[str, str], *names: str) -> dict[str, Any]:
    out: dict[str, Any] = dict(params)
    for name in names:
        if name in out:
            try:
                out[name] = int(out[name])
            except ValueError as exc:
                raise ToolError(400, "invalid_arguments", f"{name}: must be an integer") from exc
    return out


# --- routes ------------------------------------------------------------------------------


def build_router(service: AgentService) -> APIRouter:
    router = APIRouter()

    async def gate(request: Request) -> AgentToken | Response:
        """The caller's token, or the 401/429 answer."""
        token = await anyio.to_thread.run_sync(service.authenticate, request)
        if token is None:
            return _unauthorized()
        wait = service.retry_after(token.id)
        if wait is not None:
            return _error(
                429,
                "rate_limited",
                f"at most {rate_per_min()} requests a minute per token",
                **{"Retry-After": str(wait)},
            )
        await anyio.to_thread.run_sync(service.tokens.touch, token.id)
        return token

    async def call(token: AgentToken, surface: str, tool: str, arguments: Any) -> dict[str, Any]:
        """Run *tool* in a worker thread and record the call (domain errors included)."""
        start, ok = time.monotonic(), False
        try:
            data = await anyio.to_thread.run_sync(service.run_tool, token, tool, arguments)
            ok = True
            return data
        finally:
            ms = int((time.monotonic() - start) * 1000)
            await anyio.to_thread.run_sync(
                service.tokens.record_call, token.id, surface, tool, ok, ms
            )

    # --- MCP ------------------------------------------------------------------------

    @router.post(MCP_PATH, include_in_schema=False)
    async def mcp(request: Request) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        raw = await _read_body(request)
        if raw is None:
            return _too_large()
        try:
            msg = json.loads(raw)
        except (ValueError, RecursionError):  # RecursionError: absurdly deep nesting
            return JSONResponse(_rpc_error(None, -32700, "parse error"))
        if isinstance(msg, list):
            if not msg:
                return JSONResponse(_rpc_error(None, -32600, "empty batch"))
            if len(msg) > MAX_BATCH:
                return JSONResponse(
                    _rpc_error(None, -32600, f"batch too large (at most {MAX_BATCH} messages)")
                )
            # gate() charged the request once, which covers its first tool call; every
            # further tool call of the batch is charged on its own
            charged = [False]
            out = []
            for m in msg:
                if await request.is_disconnected():
                    break  # nobody reads the answer: run nothing more
                reply = await rpc(token, m, charged)
                if reply is not None:
                    out.append(reply)
            return JSONResponse(out) if out else Response(status_code=202)
        reply = await rpc(token, msg, [False])
        return JSONResponse(reply) if reply is not None else Response(status_code=202)

    async def rpc(token: AgentToken, m: Any, charged: list[bool]) -> dict[str, Any] | None:
        if not isinstance(m, dict) or not isinstance(m.get("method"), str):
            mid = m.get("id") if isinstance(m, dict) else None
            return _rpc_error(mid, -32600, "invalid request")
        if "id" not in m:  # a notification: no answer
            return None
        mid, method = m["id"], m["method"]
        params = m.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(mid, -32602, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            res: Any = {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "autoontology", "version": __version__},
                "instructions": await anyio.to_thread.run_sync(service.instructions, token),
            }
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in _MODELS:
                return _rpc_error(mid, -32602, f"unknown tool: {str(name)[:80]}")
            try:
                if charged[0] and service.retry_after(token.id) is not None:
                    raise ToolError(
                        429, "rate_limited", f"at most {rate_per_min()} requests a minute per token"
                    )
                charged[0] = True
                data = await call(token, "mcp", name, params.get("arguments"))
            except ToolError as exc:
                text = json.dumps(exc.body(), ensure_ascii=False)
                res = {"content": [{"type": "text", "text": text}], "isError": True}
            except Exception:  # noqa: BLE001 - answered as a JSON-RPC error
                logger.exception("agent tool %s failed", name)
                return _rpc_error(mid, -32603, "internal error (see service.log)")
            else:
                res = {
                    "content": [
                        {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}
                    ],
                    "structuredContent": data,
                    "isError": False,
                }
        else:
            return _rpc_error(mid, -32601, f"method not found: {method[:80]}")
        return {"jsonrpc": "2.0", "id": mid, "result": res}

    @router.api_route(MCP_PATH, methods=["GET", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def mcp_other() -> Response:
        return JSONResponse(
            {"error": "method_not_allowed", "detail": "MCP takes POST only (no SSE stream)"},
            status_code=405,
            headers={"Allow": "POST"},
        )

    # --- REST -----------------------------------------------------------------------

    async def rest(request: Request, tool: str, args: Callable[[], dict[str, Any]]) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        try:
            data = await call(token, "rest", tool, args())
        except ToolError as exc:
            return _error(exc.status, exc.code, exc.message)
        return JSONResponse(data)

    def query(request: Request, *ints: str, rename: dict[str, str] | None = None):
        def build() -> dict[str, Any]:
            params = {(rename or {}).get(k, k): v for k, v in request.query_params.items()}
            return _ints(params, *ints)

        return build

    @router.get(f"{REST_PREFIX}/revisions", include_in_schema=False)
    async def rest_revisions(request: Request) -> Response:
        return await rest(request, "list_revisions", query(request, "limit"))

    @router.get(f"{REST_PREFIX}/ontology", include_in_schema=False)
    async def rest_ontology(request: Request) -> Response:
        return await rest(request, "get_ontology_summary", query(request, "revision"))

    @router.get(f"{REST_PREFIX}/concepts", include_in_schema=False)
    async def rest_concepts(request: Request) -> Response:
        return await rest(
            request,
            "search_concepts",
            query(request, "limit", "revision", rename={"q": "query"}),
        )

    @router.get(f"{REST_PREFIX}/concepts/{{concept_id}}", include_in_schema=False)
    async def rest_concept(request: Request, concept_id: str) -> Response:
        build = query(request, "revision")
        return await rest(request, "get_concept", lambda: {**build(), "id": concept_id})

    @router.post(f"{REST_PREFIX}/sparql", include_in_schema=False)
    async def rest_sparql(request: Request) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        raw = await _read_body(request)
        if raw is None:
            return _too_large()
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            return _error(400, "invalid_arguments", "the body must be a JSON object")
        try:
            data = await call(token, "rest", "sparql_query", body)
        except ToolError as exc:
            return _error(exc.status, exc.code, exc.message)
        return JSONResponse(data)

    @router.get(f"{REST_PREFIX}/objects/types", include_in_schema=False)
    async def rest_object_types(request: Request) -> Response:
        return await rest(request, "list_object_types", query(request))

    @router.get(f"{REST_PREFIX}/objects/{{type_name}}", include_in_schema=False)
    async def rest_objects(request: Request, type_name: str) -> Response:
        def build() -> dict[str, Any]:
            params: dict[str, Any] = {"type": type_name}
            filters: dict[str, str] = {}
            for name, value in request.query_params.multi_items():
                if name == "filter":
                    column, _, wanted = value.partition("=")
                    filters[column] = wanted
                elif name == "q":
                    params["query"] = value
                else:
                    params[name] = value
            if filters:
                params["filters"] = filters
            return _ints(params, "limit", "offset")

        return await rest(request, "search_objects", build)

    @router.get(f"{REST_PREFIX}/objects/{{type_name}}/{{key}}", include_in_schema=False)
    async def rest_object(request: Request, type_name: str, key: str) -> Response:
        return await rest(request, "get_object", lambda: {"type": type_name, "key": key})

    @router.get(f"{REST_PREFIX}/functions", include_in_schema=False)
    async def rest_functions(request: Request) -> Response:
        return await rest(request, "list_functions", query(request))

    @router.post(f"{REST_PREFIX}/functions/{{name}}/call", include_in_schema=False)
    async def rest_call_function(request: Request, name: str) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        raw = await _read_body(request)
        if raw is None:
            return _too_large()
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            return _error(400, "invalid_arguments", "the body must be a JSON object")
        if not isinstance(body, dict):
            return _error(400, "invalid_arguments", "the body must be a JSON object")
        try:
            data = await call(token, "rest", "call_function", {**body, "name": name})
        except ToolError as exc:
            return _error(exc.status, exc.code, exc.message)
        return JSONResponse(data)

    async def rest_body(request: Request, tool: str, extra: dict[str, Any]) -> Response:
        """A POST tool whose JSON body holds its arguments (plus *extra* from the path)."""
        token = await gate(request)
        if isinstance(token, Response):
            return token
        raw = await _read_body(request)
        if raw is None:
            return _too_large()
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            return _error(400, "invalid_arguments", "the body must be a JSON object")
        if not isinstance(body, dict):
            return _error(400, "invalid_arguments", "the body must be a JSON object")
        try:
            data = await call(token, "rest", tool, {**body, **extra})
        except ToolError as exc:
            return _error(exc.status, exc.code, exc.message)
        return JSONResponse(data)

    @router.get(f"{REST_PREFIX}/actions", include_in_schema=False)
    async def rest_actions(request: Request) -> Response:
        return await rest(request, "list_actions", query(request))

    @router.get(f"{REST_PREFIX}/actions/runs/{{run_id}}", include_in_schema=False)
    async def rest_action_run(request: Request, run_id: str) -> Response:
        return await rest(request, "get_action_run", lambda: {"id": run_id})

    @router.post(f"{REST_PREFIX}/actions/{{name}}/preview", include_in_schema=False)
    async def rest_preview_action(request: Request, name: str) -> Response:
        return await rest_body(request, "preview_action", {"name": name})

    @router.post(f"{REST_PREFIX}/actions/{{name}}/submit", include_in_schema=False)
    async def rest_submit_action(request: Request, name: str) -> Response:
        return await rest_body(request, "submit_action", {"name": name})

    @router.get(f"{REST_PREFIX}/exports", include_in_schema=False)
    async def rest_exports(request: Request) -> Response:
        return await rest(request, "list_exports", query(request))

    @router.get(f"{REST_PREFIX}/exports/{{rev}}.zip", include_in_schema=False)
    async def rest_export_zip(request: Request, rev: str) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        start, ok = time.monotonic(), False
        try:
            if token.revision_pin is not None:
                number = rev[1:] if rev[:1].lower() == "r" else rev
                if not number.isdigit() or int(number) != token.revision_pin:
                    return _error(
                        403,
                        "revision_pinned",
                        f"this token is pinned to revision {token.revision_pin}",
                    )
            try:
                resp = await anyio.to_thread.run_sync(
                    export_zip_response, service.api.settings, rev, service.project(token)
                )
            except ApiError as exc:
                return _error(exc.status, exc.error, exc.detail)
            except ToolError as exc:
                return _error(exc.status, exc.code, exc.message)
            ok = True
            return resp
        finally:
            ms = int((time.monotonic() - start) * 1000)
            await anyio.to_thread.run_sync(
                service.tokens.record_call, token.id, "rest", "download_export", ok, ms
            )

    @router.get(f"{REST_PREFIX}/openapi.json", include_in_schema=False)
    async def rest_openapi(request: Request) -> Response:
        token = await gate(request)
        if isinstance(token, Response):
            return token
        return JSONResponse(openapi_schema())

    return router


def openapi_schema() -> dict[str, Any]:
    """OpenAPI 3.1 description of the ``/agent/v1`` routes (only those)."""

    def param(name: str, where: str, schema: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return {"name": name, "in": where, "schema": schema, **kw}

    revision = param("revision", "query", {"type": "integer", "minimum": 1})
    errors = {
        "401": {"description": "Missing, invalid, revoked or expired token."},
        "403": {
            "description": "revision_pinned: the token serves another revision; scope_required, "
            "agent_not_allowed, action_not_allowed, forbidden: the action tools' refusals."
        },
        "404": {"description": "Unknown revision or concept, or no snapshot yet."},
        "429": {"description": "Rate limit; see Retry-After."},
    }

    def op(summary: str, params: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
        return {
            "summary": summary,
            "parameters": params or [],
            "responses": {"200": {"description": "OK (JSON with `revision`)"}, **errors},
            **extra,
        }

    tools = {t["name"]: t for t in TOOLS}

    def action_body(tool: str) -> dict[str, Any]:
        props = tools[tool]["inputSchema"]["properties"]
        return {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {k: v for k, v in props.items() if k != "name"},
                        "required": ["key"],
                    }
                }
            },
        }

    paths = {
        f"{REST_PREFIX}/revisions": {
            "get": op(
                tools["list_revisions"]["description"],
                [param("limit", "query", {"type": "integer", "minimum": 1, "maximum": 500})],
            )
        },
        f"{REST_PREFIX}/ontology": {
            "get": op(tools["get_ontology_summary"]["description"], [revision])
        },
        f"{REST_PREFIX}/concepts": {
            "get": op(
                tools["search_concepts"]["description"],
                [
                    param("q", "query", {"type": "string", "maxLength": 200}),
                    param("limit", "query", {"type": "integer", "minimum": 1, "maximum": 50}),
                    param("kind", "query", {"type": "string"}),
                    revision,
                ],
            )
        },
        f"{REST_PREFIX}/concepts/{{concept_id}}": {
            "get": op(
                tools["get_concept"]["description"],
                [param("concept_id", "path", {"type": "string"}, required=True), revision],
            )
        },
        f"{REST_PREFIX}/sparql": {
            "post": op(
                tools["sparql_query"]["description"],
                requestBody={
                    "required": True,
                    "content": {
                        "application/json": {"schema": tools["sparql_query"]["inputSchema"]}
                    },
                },
            )
        },
        f"{REST_PREFIX}/exports": {"get": op(tools["list_exports"]["description"])},
        f"{REST_PREFIX}/objects/types": {
            "get": op(
                tools["list_object_types"]["description"],
                [
                    param(
                        "status",
                        "query",
                        tools["list_object_types"]["inputSchema"]["properties"]["status"],
                    )
                ],
            )
        },
        f"{REST_PREFIX}/objects/{{type}}": {
            "get": op(
                tools["search_objects"]["description"],
                [
                    param("type", "path", {"type": "string"}, required=True),
                    param("q", "query", {"type": "string", "maxLength": 200}),
                    param(
                        "filter",
                        "query",
                        {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        description="column=value (repeatable)",
                    ),
                    param("limit", "query", {"type": "integer", "minimum": 1, "maximum": 50}),
                    param("offset", "query", {"type": "integer", "minimum": 0}),
                ],
            )
        },
        f"{REST_PREFIX}/objects/{{type}}/{{key}}": {
            "get": op(
                tools["get_object"]["description"],
                [
                    param("type", "path", {"type": "string"}, required=True),
                    param("key", "path", {"type": "string"}, required=True),
                ],
            )
        },
        f"{REST_PREFIX}/functions": {
            "get": op(
                tools["list_functions"]["description"],
                [param("type", "query", {"type": "string"})],
            )
        },
        f"{REST_PREFIX}/functions/{{name}}/call": {
            "post": op(
                tools["call_function"]["description"],
                [param("name", "path", {"type": "string"}, required=True)],
                requestBody={
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    k: v
                                    for k, v in tools["call_function"]["inputSchema"][
                                        "properties"
                                    ].items()
                                    if k != "name"
                                },
                                "required": ["key"],
                            }
                        }
                    },
                },
            )
        },
        f"{REST_PREFIX}/actions": {
            "get": op(
                tools["list_actions"]["description"],
                [param("type", "query", {"type": "string"})],
            )
        },
        f"{REST_PREFIX}/actions/{{name}}/preview": {
            "post": op(
                tools["preview_action"]["description"],
                [param("name", "path", {"type": "string"}, required=True)],
                requestBody=action_body("preview_action"),
            )
        },
        f"{REST_PREFIX}/actions/{{name}}/submit": {
            "post": op(
                tools["submit_action"]["description"],
                [param("name", "path", {"type": "string"}, required=True)],
                requestBody=action_body("submit_action"),
            )
        },
        f"{REST_PREFIX}/actions/runs/{{id}}": {
            "get": op(
                tools["get_action_run"]["description"],
                [param("id", "path", {"type": "string"}, required=True)],
            )
        },
        f"{REST_PREFIX}/exports/{{rev}}.zip": {
            "get": {
                **op(
                    "One export revision folder as a zip (rev: 3 or r0003).",
                    [param("rev", "path", {"type": "string"}, required=True)],
                ),
                "responses": {
                    "200": {
                        "description": "The zip",
                        "content": {"application/zip": {}},
                    },
                    **errors,
                },
            }
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AutoOntology agent API",
            "version": __version__,
            "description": (
                "Access to the built ontology and its objects. Every operation reads, except "
                "POST /actions/{name}/submit (a token with the propose or execute scope). "
                "The same tools are served over MCP (Streamable HTTP, JSON-RPC 2.0) at "
                f"POST {MCP_PATH}."
            ),
        },
        "paths": paths,
        "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
        "security": [{"bearer": []}],
    }


def install(app: FastAPI, api: Api) -> AgentService:
    """Mount ``POST /mcp`` and ``/agent/v1/*`` on *app*."""
    service = AgentService(
        api,
        lambda: getattr(app.state, "sparql", None),
        lambda: getattr(getattr(app.state, "auth", None), "mailer", None),
    )
    app.state.agent = service
    app.include_router(build_router(service))
    return service


# --- token management (/api/agent/*) ----------------------------------------------------


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    #: serve only this snapshot revision (None: follow the latest)
    revision_pin: int | None = Field(None, ge=1)
    #: days until the token expires (None: never)
    expires_days: Literal[30, 90] | None = None
    #: read (always) | propose | execute (owners only) | action:<name> (allow-list)
    scopes: list[str] = Field(default_factory=lambda: ["read"], max_length=30)
    #: action allow-list (each becomes an ``action:<name>`` scope)
    actions: list[str] = Field(default_factory=list, max_length=29)


ONCE_NOTE = "This token is shown only once: copy it now."


def add_management_routes(router: APIRouter, api: Api, ctx_dep: Any = None) -> None:
    """``/api/agent/*`` on a project router (the UI and :class:`~autoontology.cli_ops.
    LocalApi` share it): the tokens and usage of the request's project (*ctx_dep*: the
    :class:`~autoontology.web.projects.ProjectContext` dependency; None = the default
    project). Another project's token id reads as unknown (404)."""
    from autoontology.web.projects import default_project

    Ctx = Depends(ctx_dep or default_project(api))  # noqa: N806

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except TokenError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc

    def require_execute(ctx: ProjectContext, scopes: tuple[str, ...] | list[str]) -> None:
        """An ``execute`` token runs actions without a person approving each one: only
        owners create or rotate one (editors: read and propose)."""
        if "execute" in scopes and not (ctx.is_admin or ctx.role == "owner"):
            raise ApiError(
                403,
                "forbidden",
                "a token with the execute scope needs the owner role",
                permission="logic.activate",
            )

    @router.get("/agent/tokens")
    def list_agent_tokens(ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("agent.tokens")
        return {"tokens": [t.to_dict() for t in ctx.store.agent_tokens().list_tokens()]}

    @router.post("/agent/tokens", status_code=201)
    def create_agent_token(body: dict[str, Any], ctx: ProjectContext = Ctx) -> dict[str, Any]:
        ctx.require("agent.tokens")
        try:
            req = TokenIn.model_validate(body)
        except ValidationError as exc:
            raise ApiError(400, "invalid_request", _validation_text(exc)) from exc
        name = " ".join(req.name.split())
        if not name:
            raise ApiError(400, "invalid_request", "name: must not be blank")
        if (
            req.revision_pin is not None
            and ctx.store.get_snapshot(req.revision_pin, with_model=False) is None
        ):
            raise ApiError(404, "unknown_revision", f"unknown revision {req.revision_pin}")
        try:
            scopes = normalize_scopes(req.scopes, req.actions)
        except ValueError as exc:
            raise ApiError(400, "invalid_scope", str(exc)) from exc
        require_execute(ctx, scopes)
        raw, record = ctx.store.agent_tokens().create(
            name,
            revision_pin=req.revision_pin,
            expires_days=req.expires_days,
            created_by=ctx.user_id,
            scopes=scopes,
        )
        ctx.audit(
            "token.create",
            target=record.id,
            detail={
                "name": name,
                "revision_pin": req.revision_pin,
                "expires_days": req.expires_days,
                "scopes": list(scopes),
            },
        )
        return {"token": raw, "record": record.to_dict(), "note": ONCE_NOTE}

    def forget(request: Request, token_id: str) -> None:
        service = getattr(request.app.state, "agent", None)
        if service is not None:  # the CLI's in-process app has no agent surface
            service.forget(token_id)

    @router.post("/agent/tokens/{token_id}/rotate")
    def rotate_agent_token(
        token_id: str, request: Request, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("agent.tokens")
        current = guard(lambda: ctx.store.agent_tokens()._require(token_id))
        require_execute(ctx, current.scopes)
        raw, record = guard(
            lambda: ctx.store.agent_tokens().rotate(token_id, created_by=ctx.user_id)
        )
        forget(request, token_id)
        ctx.audit("token.rotate", target=token_id)
        return {"token": raw, "record": record.to_dict(), "note": ONCE_NOTE}

    @router.delete("/agent/tokens/{token_id}")
    def revoke_agent_token(
        token_id: str, request: Request, ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("agent.tokens")
        record = guard(lambda: ctx.store.agent_tokens().revoke(token_id)).to_dict()
        forget(request, token_id)
        ctx.audit("token.revoke", target=token_id)
        return record

    @router.get("/agent/usage")
    def agent_usage(
        days: int | None = Query(None, ge=1, le=365), ctx: ProjectContext = Ctx
    ) -> dict[str, Any]:
        ctx.require("agent.tokens")
        return ctx.store.agent_tokens().usage(days)

    @router.get("/agent/tools")
    def agent_tools(ctx: ProjectContext = Ctx) -> dict[str, Any]:  # noqa: ARG001 - access
        return {
            "tools": TOOLS,
            "write_tools": sorted(WRITE_TOOLS),
            "scopes": ["read", "propose", "execute"],
            "protocol": PROTOCOL,
            "mcp_path": MCP_PATH,
            "rest_base": REST_PREFIX,
            "rate_per_min": rate_per_min(),
            "max_body_bytes": MAX_BODY_BYTES,
        }
