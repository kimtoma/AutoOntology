# SPDX-License-Identifier: MIT
"""HTTP identity boundary with an explicit fail-closed capability inventory.

Unfiltered resources currently require whole-workspace scope. Per-resource domain
projection and long-running stream revocation remain activation gates; this module
must not be advertised as completing organization-wide access control.
"""

import hmac

from starlette.routing import compile_path

from autoontology.security.internal import router_secret
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity
from autoontology.web.identity_api import cookie_name

POLICIES = {}


def _policies(action, method, *paths):
    for path in paths:
        POLICIES[method, path] = action


_policies("source.read", "GET", "/api/sources", "/api/sources/freshness")
_policies("source.edit", "POST", "/api/sources", "/api/samples/load")
_policies("source.edit", "DELETE", "/api/sources/{name}")
_policies("runs.read", "GET", "/api/runs", "/api/runs/{run_id}", "/api/runs/{run_id}/events")
_policies("runs.cancel", "POST", "/api/runs/{run_id}/cancel")
_policies("dynamic-run", "POST", "/api/runs")
_policies(
    "candidate.read",
    "GET",
    "/api/review",
    "/api/review/pending-count",
    "/api/ontology/revisions",
    "/api/ontology/diff",
    "/api/ops",
    "/api/ops/changes",
    "/api/ops/connections",
)
_policies(
    "review.decide",
    "POST",
    "/api/review/{item_id:path}/decision",
    "/api/ops/changes/{item_id}/decision",
    "/api/ops/connections/{item_id:path}/hold",
)
_policies(
    "review.decide",
    "DELETE",
    "/api/review/{item_id:path}/decision",
    "/api/ops/changes/{item_id}/decision",
    "/api/ops/connections/{item_id:path}/hold",
)
_policies(
    "knowledge.read",
    "GET",
    "/api/ontology",
    "/api/ontology/graph",
    "/api/ontology/concepts",
    "/api/ontology/concepts/{concept_id}",
    "/api/playground/index",
    "/api/playground/sparql/examples",
    "/api/samples",
)
_policies(
    "originals.read",
    "GET",
    "/api/ontology/documents",
    "/api/ontology/documents/{document_id}/original",
)
_policies(
    "history.read",
    "GET",
    "/api/sources/{source_name}/revisions",
    "/api/ops/processing",
    "/api/ops/processing/{receipt_id}",
    "/api/ops/publications",
    "/api/ops/publications/{revision}",
)
_policies("source.edit", "POST", "/api/ontology/documents/{document_id}/archive")
_policies("exports.read", "GET", "/api/exports", "/api/exports/{rev}/{file_path:path}")
_policies("exports.read", "GET", "/api/ontology/export", "/api/ontology/export/{format}")
_policies(
    "settings.read", "GET", "/api/status", "/api/settings", "/api/settings/restart/{restart_id}"
)
_policies(
    "settings.manage", "POST", "/api/providers/test", "/api/settings/test", "/api/settings/restart"
)
_policies("settings.manage", "PUT", "/api/settings")
_policies("validation.run", "POST", "/api/ops/validate")
_policies("knowledge.publish", "POST", "/api/ops/publish")
_policies("candidate.prepare", "POST", "/api/ops/restores", "/api/playground/index")
_policies("candidate.read", "GET", "/api/ops/restores", "/api/ops/restores/{proposal}")
_policies("knowledge.restore", "POST", "/api/ops/restores/{proposal}/execute")
_policies("feedback.submit", "POST", "/api/ops/feedback")
_policies("feedback.manage", "POST", "/api/ops/feedback/{item_id}/resolve")
_policies("checks.edit", "POST", "/api/ops/checks")
_policies("checks.edit", "DELETE", "/api/ops/checks/{item_id}")
_policies(
    "results.read",
    "GET",
    "/api/playground/traces",
    "/api/playground/traces/{trace_id}",
    "/api/playground/traces/{trace_id}/result.csv",
    "/api/playground/conversations",
    "/api/playground/conversations/{conversation_id}",
)
_policies(
    "query.run",
    "POST",
    "/api/playground/retrieve",
    "/api/playground/chat",
    "/api/playground/sql",
    "/api/playground/sparql",
)
_policies(
    "results.delete",
    "DELETE",
    "/api/playground/traces/{trace_id}",
    "/api/playground/conversations/{conversation_id}",
)
_policies(None, "GET", "/api/identity/me")
_policies(None, "GET", "/api/identity/credentials")
_policies(None, "POST", "/api/identity/logout")
_policies(None, "DELETE", "/api/identity/credentials/{credential_id}")
_policies(
    "identity.admin",
    "GET",
    "/api/identity/principals",
    "/api/identity/management",
    "/api/identity/domains",
    "/docs",
    "/openapi.json",
    "/redoc",
)
_policies(
    "identity.admin",
    "POST",
    "/api/identity/principals",
    "/api/identity/principals/{principal_id}/disable",
    "/api/identity/principals/{principal_id}/enable",
    "/api/identity/domains",
    "/api/identity/delegations",
)
_policies(
    "identity.admin",
    "PUT",
    "/api/identity/principals/{principal_id}/roles",
    "/api/identity/principals/{principal_id}/password",
    "/api/identity/sources/{source_id}/domain",
)
_policies("identity.admin", "DELETE", "/api/identity/delegations/{principal_id}")
_policies("audit.read", "GET", "/api/identity/audit")

DOMAIN_READS = {
    ("GET", path)
    for path in (
        "/api/ontology",
        "/api/ontology/graph",
        "/api/ontology/concepts",
        "/api/ontology/concepts/{concept_id}",
        "/api/ontology/documents",
        "/api/ontology/documents/{document_id}/original",
        "/api/ontology/export",
        "/api/ontology/export/{format}",
    )
}
RULES = [
    (method, compile_path(path)[0], action, path) for (method, path), action in POLICIES.items()
]


def public_path(path, method):
    return (
        path in {"/", "/ui", "/health", "/health/live"}
        or path.startswith("/ui/")
        or (method, path) in {("GET", "/api/identity/status"), ("POST", "/api/identity/login")}
    )


async def authenticate_request(api, request):
    service = Identity(api.store)
    state = service.status()
    if not state["configured"] or public_path(request.url.path, request.method):
        return None
    authorization = request.headers.get("authorization", "")
    if (
        len([value for key, value in request.scope["headers"] if key.lower() == b"authorization"])
        > 1
    ):
        raise AccessError("Use one credential header.", "authentication_required", 401)
    if request.url.path.startswith("/v1/"):
        expected = "Bearer " + router_secret(api.settings.home).reveal()
        if (
            request.client
            and request.client.host in {"127.0.0.1", "::1"}
            and (request.method, request.url.path)
            in {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
            and hmac.compare_digest(authorization.encode(), expected.encode())
        ):
            return None
        raise AccessError(
            "Only the local managed model client may use this endpoint.",
            "internal_credential_required",
            401,
        )
    if authorization:
        if not authorization.startswith("Bearer "):
            raise AccessError("Use a bearer credential.", "authentication_required", 401)
        raw = authorization[7:]
    else:
        raw = request.cookies.get(cookie_name(api))
    actor = service.authenticate(raw)
    matched = next(
        (
            (action, path)
            for method, pattern, action, path in RULES
            if method == request.method and pattern.fullmatch(request.url.path)
        ),
        None,
    )
    if matched is None:
        raise AccessError(
            "No permission is defined for this endpoint.", "permission_not_defined", 403
        )
    action = matched[0]
    if action == "dynamic-run":
        try:
            body = await request.json()
            action = {
                "build": "candidate.prepare",
                "apply": "candidate.prepare",
                "export": "exports.read",
            }.get(body.get("kind"))
        except (ValueError, AttributeError, TypeError):
            action = None
        if action is None:
            raise AccessError("Choose a supported execution kind.", "invalid_request", 400)
    if action:
        if (request.method, matched[1]) in DOMAIN_READS:
            service.require_any(actor, action)
        else:
            service.require(actor, action)
    if request.url.path.startswith("/api/ontology") and any(
        k in request.query_params for k in ("revision", "from", "to")
    ):
        from autoontology.store.operations import visible_revision

        for parameter in ("revision", "from", "to"):
            try:
                selected = int(request.query_params[parameter])
            except (KeyError, ValueError):
                continue
            selected_action = (
                "history.read" if visible_revision(api.store, selected) else "candidate.read"
            )
            if (request.method, matched[1]) in DOMAIN_READS and selected_action == "history.read":
                service.require_any(actor, selected_action)
            else:
                service.require(actor, selected_action)
    return actor
