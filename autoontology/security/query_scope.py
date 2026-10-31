# SPDX-License-Identifier: MIT
"""Durable knowledge dependencies for queries and messages, independent of ownership."""

from contextlib import nullcontext

from autoontology.security.context import authorize, authorize_any, current_actor
from autoontology.security.policy import AccessError
from autoontology.security.views import knowledge_view, verify_view_authority
from autoontology.store.identity import Identity
from autoontology.store.operations import digest


def resources(model):
    """Knowledge dependencies, including unowned legacy diagnostics in whole views."""
    data = model.to_dict()
    result = {}
    for collection in ("classes", "properties", "individuals", "documents", "evidence"):
        result.update(
            {collection + ":" + key: digest(value) for key, value in data[collection].items()}
        )
    for collection in ("axioms", "merges"):
        result.update(
            {collection + ":" + digest(value): digest(value) for value in data[collection]}
        )
    collections = {
        "classes",
        "properties",
        "individuals",
        "documents",
        "evidence",
        "axioms",
        "merges",
    }
    metadata = {"format", "name", "base", "structured_sources", "documents_sources"}
    result.update(
        {
            "metadata:" + key: digest(value)
            for key, value in data.items()
            if key not in collections | metadata and value
        }
    )
    return result


def capture_scope(store, revision, model):
    decision = authorize_any(store, "query.run")
    if decision is None:
        return None
    verify_view_authority(store, model, "query.run")
    base = {
        "version": 1,
        "workspace_id": decision.workspace_id,
        "policy_version": decision.policy_version,
        "revision": revision,
        "status": "unknown",
    }
    snapshot = store.get_snapshot(revision)
    if snapshot is not None:
        selected = knowledge_view(store, snapshot, action="query.run")
        if digest(selected.model.to_dict()) == digest(model.to_dict()):
            return {
                **base,
                "policy_version": selected.policy_version,
                "status": "recorded",
                "snapshot_sha256": digest(snapshot.model),
                "view_sha256": digest(model.to_dict()),
                "source_names": sorted(
                    set(model.structured_sources) | set(model.documents_sources)
                ),
                "resources": resources(model),
            }
    # Trusted whole-workspace fixtures/custom callers may keep an unknown record,
    # but it must never turn into a domain-authorized historical result later.
    authorize(store, "query.run")
    return base


def require_scope(store, binding, action, *, conn=None, cache=None, expected_policy=None):
    service = Identity(store)
    with nullcontext(conn) if conn is not None else service._read() as conn:
        actor = current_actor(store, conn=conn)
        if actor is None:
            return
        state = service._state(conn)
        if expected_policy is not None and state["policy_version"] != expected_policy:
            raise AccessError(
                "Authority changed while preparing this request.", "authority_changed", 409
            )
        try:
            service.require(actor, action, conn=conn)
            return
        except AccessError as error:
            if error.code != "forbidden":
                raise
        service.require_any(actor, action, conn=conn)
        if (
            not binding
            or binding.get("version") != 1
            or binding.get("status") != "recorded"
            or binding.get("workspace_id") != state["workspace_id"]
        ):
            raise AccessError(
                "This saved result has no verified accessible knowledge scope.",
                "result_scope_unknown",
                404,
            )
        cache = cache if cache is not None else {}
        key = (binding["revision"], action)
        if key not in cache:
            snapshot = store.get_snapshot(binding["revision"])
            if snapshot is None:
                raise AccessError("Saved knowledge is unavailable.", "result_scope_changed", 404)
            view = knowledge_view(store, snapshot, action=action, conn=conn)
            cache[key] = (
                digest(snapshot.model),
                set(view.model.structured_sources) | set(view.model.documents_sources),
                resources(view.model),
            )
        snapshot_sha, names, available = cache[key]
        if (
            snapshot_sha != binding["snapshot_sha256"]
            or not set(binding["source_names"]) <= names
            or any(available.get(key) != sha for key, sha in binding["resources"].items())
        ):
            raise AccessError(
                "Saved knowledge is outside current access.", "result_scope_changed", 404
            )


def message_scope(store, conversation_id, action, *, conn, cache=None):
    """Every message has its own immutable scope copy; deleting a trace cannot erase it."""
    actor = current_actor(store, conn=conn)
    if actor is None:
        return
    rows = conn.execute(
        "SELECT m.*,s.scope_json AS access_json,s.sha256 AS access_sha FROM playground_messages m LEFT JOIN playground_message_scopes s ON s.message_id=m.id WHERE m.conversation_id=? ORDER BY m.id",
        (conversation_id,),
    ).fetchall()
    for row in rows:
        if row["access_json"] is None:
            authorize(store, action, conn=conn)
            continue
        import json

        try:
            binding = json.loads(row["access_json"])
            content = {
                key: row[key] for key in row.keys() if key not in {"access_json", "access_sha"}
            }
            if digest([content, binding]) != row["access_sha"]:
                raise ValueError
        except (TypeError, ValueError):
            raise AccessError(
                "Saved message scope failed its integrity check.", "result_scope_invalid", 409
            ) from None
        require_scope(store, binding, action, conn=conn, cache=cache)
