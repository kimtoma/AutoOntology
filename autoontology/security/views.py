# SPDX-License-Identifier: MIT
"""Domain-bound read models shared by HTTP, native commands and model consumers."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field, replace

from autoontology.ontology.model import MergedOntology
from autoontology.ontology.projection import project
from autoontology.security.context import current_actor
from autoontology.security.policy import AccessError
from autoontology.store.identity import Identity


@dataclass(frozen=True)
class KnowledgeView:
    snapshot: object
    model: MergedOntology
    restricted: bool
    policy_version: int | None
    authority: dict | None = field(default=None, repr=False)
    historical_scope_missing: bool = False

    def __post_init__(self):
        # This is request-local metadata, never part of the immutable model JSON.
        self.model._access_view = {
            "restricted": self.restricted,
            "policy_version": self.policy_version,
            "historical_scope_missing": self.historical_scope_missing,
        }
        if self.authority is not None:
            from autoontology.store.operations import digest

            self.model._view_authority = {
                **self.authority,
                "model_sha256": digest(self.model.to_dict()),
            }


def _whole(service, actor, action, conn):
    try:
        return service.require(actor, action, conn=conn)
    except AccessError as error:
        if error.code != "forbidden":
            raise
        return None


def knowledge_view(store, snapshot, *, action="knowledge.read", additional_actions=(), conn=None):
    """Recheck live policy and project copies; never cache by revision alone.

    Historical identities must come from captured source metadata, not today's
    same-named registry entry. Until a publication pins a domain, all recorded
    classifications are required. Missing bindings cannot authorize a named domain.
    """
    from autoontology.store.publications import Publications

    model = MergedOntology.from_dict(snapshot.model)
    service = Identity(store)
    publication = Publications(store).check_model(snapshot.revision)
    pinned = (publication or {}).get("manifest", {}).get("source_authority")
    with nullcontext(conn) if conn is not None else service._read() as conn:
        actor = current_actor(store, conn=conn)
        if actor is None:
            return KnowledgeView(snapshot, model, False, None)
        state = service._state(conn)
        authority = {
            "principal_id": actor.principal_id,
            "credential_id": actor.credential_id,
            "workspace_id": state["workspace_id"],
            "policy_version": state["policy_version"],
            "action": action,
        }
        actions = (action, *additional_actions)
        whole = _whole(service, actor, action, conn)
        if whole and any(
            _whole(service, actor, extra, conn) is None for extra in additional_actions
        ):
            whole = None
        if whole:
            return KnowledgeView(snapshot, model, False, whole.policy_version, authority)
        for selected_action in actions:
            service.require_any(actor, selected_action, conn=conn)
        metadata = {}
        for source in snapshot.sources:
            metadata.setdefault(source["name"], []).append(source)
        allowed = set()
        unknown = False
        for name in (*model.structured_sources, *model.documents_sources):
            sources = metadata.get(name, [])
            source_ids = sorted({s["source_id"] for s in sources if s.get("source_id")})
            binding = (pinned or {}).get("sources", {}).get(name)
            if pinned is not None and (
                not binding or pinned.get("workspace_id") != service._state(conn)["workspace_id"]
            ):
                unknown = True
                continue
            try:
                if binding and pinned.get("workspace_id") == service._state(conn)["workspace_id"]:
                    domains = set(binding["domains"])
                    if not binding["source_ids"] or not domains:
                        unknown = True
                        continue
                    for identity in binding["source_ids"]:
                        row = conn.execute(
                            "SELECT domain_id FROM identity_source_domains WHERE source_id=? ORDER BY version DESC LIMIT 1",
                            (identity,),
                        ).fetchone()
                        if not row:
                            raise AccessError("A historical source has no current classification.")
                        domains.add(row[0])
                    for selected_action in actions:
                        service.require(actor, selected_action, domains=sorted(domains), conn=conn)
                elif source_ids and all(s.get("source_id") for s in sources):
                    for selected_action in actions:
                        service.source_domains(actor, source_ids, action=selected_action, conn=conn)
                else:
                    unknown = True
                    continue
            except AccessError as error:
                if error.code not in {"forbidden", "source_scope_missing"}:
                    raise
                continue
            allowed.add(name)
        result = project(model, allowed)
        by_source = result.counts().get("by_source", {})
        projected = replace(
            snapshot,
            sources=[
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "source_kind": s.get("source_kind", s["kind"]),
                    "included": True,
                    "counts": by_source.get(s["name"], {}),
                }
                for s in snapshot.sources
                if s["name"] in allowed
            ],
            counts=result.counts(),
            model=result.to_dict(),
        )
        return KnowledgeView(projected, result, True, state["policy_version"], authority, unknown)


def verify_view_authority(store, model, action, *, check_model=True):
    """Recheck a selected view before a byte/file effect; reject stale/mutated views."""
    from autoontology.store.operations import digest

    service = Identity(store)
    with service._read() as conn:
        actor = current_actor(store, conn=conn)
        if actor is None:
            return
        bound = getattr(model, "_view_authority", None)
        if bound is None:
            # Trusted whole-workspace callers can use an unprojected model.
            service.require(actor, action, conn=conn)
            return
        state = service._state(conn)
        if (
            bound["principal_id"],
            bound["credential_id"],
            bound["workspace_id"],
            bound["action"],
        ) != (actor.principal_id, actor.credential_id, state["workspace_id"], action):
            raise AccessError(
                "The selected view belongs to a different execution.", "view_authority_changed", 409
            )
        if bound["policy_version"] != state["policy_version"] or (
            check_model and bound["model_sha256"] != digest(model.to_dict())
        ):
            raise AccessError(
                "The selected knowledge or policy changed. Refresh before continuing.",
                "view_authority_changed",
                409,
            )
        service.require_any(actor, action, conn=conn)


def publication_authority(store, model, source_bindings):
    """Pin current classifications for historically identified publication inputs.

    A matching live source name does not prove an old input's identity. Unknown
    bindings stay unknown; publishing cannot manufacture their historical lineage.
    The caller's final publication guard also binds the current policy version.
    """
    service = Identity(store)
    with service._read() as conn:
        actor = current_actor(store, conn=conn)
        if actor is None:
            return None
        service.require(actor, "knowledge.publish", conn=conn)
        sources = {}
        for name in sorted(set(model.structured_sources) | set(model.documents_sources)):
            entries = [
                s
                for binding in source_bindings
                for s in binding["sources"]
                if s.get("name") == name
            ]
            identities = sorted({s["source_id"] for s in entries if s.get("source_id")})
            domains, versions = set(), []
            complete = bool(identities) and all(s.get("source_id") for s in entries)
            for identity in identities:
                row = conn.execute(
                    "SELECT version,domain_id FROM identity_source_domains WHERE source_id=? ORDER BY version DESC LIMIT 1",
                    (identity,),
                ).fetchone()
                if row is None:
                    complete = False
                else:
                    domains.add(row["domain_id"])
                    versions.append(
                        {
                            "source_id": identity,
                            "version": row["version"],
                            "domain_id": row["domain_id"],
                        }
                    )
            sources[name] = {
                "source_ids": identities,
                "domains": sorted(domains) if complete else [],
                "classifications": versions,
                "status": "recorded" if complete else "unknown",
            }
        state = service._state(conn)
        return {
            "version": 1,
            "workspace_id": state["workspace_id"],
            "policy_version": state["policy_version"],
            "sources": sources,
        }
