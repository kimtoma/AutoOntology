# SPDX-License-Identifier: MIT
"""Revalidate historical knowledge and restore it as a new atomic publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from autoontology.engine.build import WORKSPACE_LEASE
from autoontology.engine.jobs import hold_sources
from autoontology.engine.operations import install_publication, profile
from autoontology.ontology.model import MergedOntology
from autoontology.playground import prompts
from autoontology.playground.elements import elements_from_model
from autoontology.playground.index import build_index
from autoontology.playground.service import EmbedderCache
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import now_iso
from autoontology.store.operations import Operations, digest, visible_revision
from autoontology.store.publications import PublicationError, Publications, index_receipt
from autoontology.store.restorations import Restorations, RestoreError
from autoontology.web.revisions import diff


def utc_now():
    return datetime.now(timezone.utc)


def target_material(store, settings, revision):
    if not visible_revision(store, revision):
        raise RestoreError("only a published version can be restored")
    snapshot = store.get_snapshot(revision)
    if snapshot is None or snapshot.model is None:
        raise RestoreError("the published restoration target is missing")
    model = MergedOntology.from_dict(snapshot.model)
    receipts = Publications(store)
    try:
        publication = receipts.get(revision)
        if publication:
            receipts.verify(revision, settings.exports_dir)
        originals = receipts.originals(model)
    except PublicationError as exc:
        raise RestoreError(str(exc), "restore_target_invalid") from exc
    folder = settings.exports_dir / f"r{revision:04}"
    represented = [
        name
        for name in model.structured_sources
        if any(
            c.source == "structured" and c.structured_source == name for c in model.classes.values()
        )
    ]
    upstream, upstream_hashes, marker = [], {}, {}
    if represented:
        try:
            marker = json.loads((folder / "export.json").read_text())
            if (
                marker.get("snapshot_run") != snapshot.run_id
                or marker.get("revision") != revision
                or marker.get("workspace") != model.name
            ):
                raise RestoreError("frozen structured mappings do not belong to this target")
            for name in represented:
                relative = f"upstream/{name}.ossie.zip"
                path = folder / relative
                if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                    raise RestoreError("unsafe frozen structured mapping")
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                if marker.get("file_sha256", {}).get(relative) != sha:
                    raise RestoreError("frozen structured mapping integrity check failed")
                upstream.append((name, raw))
                upstream_hashes[name] = sha
        except (OSError, ValueError) as exc:
            raise RestoreError(
                "this version has no verified frozen structured mapping; prepare a reviewed candidate instead",
                "restore_mapping_missing",
            ) from exc
    descriptor = {
        "model_sha256": receipts.model_digest(revision),
        "publication_id": publication["id"] if publication else None,
        "originals": originals,
        "upstream_sha256": upstream_hashes,
        "sources": [
            {
                k: s[k]
                for k in (
                    "name",
                    "kind",
                    "included",
                    "source_id",
                    "source_revision",
                    "processing",
                    "sql_binding",
                )
                if k in s
            }
            for s in snapshot.sources
        ],
    }
    return snapshot, model, descriptor, upstream, marker


def signature(store, settings, revision, reason, descriptor, embedder):
    return digest(
        {
            "operation": "restore-v1",
            "state": Operations(store).signature(revision, profile(settings, embedder)),
            "target": descriptor,
            "reason": reason,
        }
    )


def impact_details(store, before, after, base_revision, target_revision):
    from autoontology.provenance import original_metadata

    old, new = Operations.records(before), Operations.records(after)
    changes = []
    for collection in old:
        for key in sorted(set(old[collection]) | set(new[collection])):
            a, b = old[collection].get(key), new[collection].get(key)
            sa, sb = Operations.semantic(a, before), Operations.semantic(b, after)
            if sa == sb:
                continue
            fields = [
                {"name": field, "before": (sa or {}).get(field), "after": (sb or {}).get(field)}
                for field in sorted(set(sa or {}) | set(sb or {}))
                if (sa or {}).get(field) != (sb or {}).get(field)
            ]
            changes.append(
                {
                    "collection": collection,
                    "key": key,
                    "label": (b or a).get("label") or (b or a).get("concept_name") or key,
                    "kind": "added" if a is None else "removed" if b is None else "changed",
                    "before_text": Operations.describe(a, before),
                    "after_text": Operations.describe(b, after),
                    "fields": fields,
                }
            )
    originals = {}
    for model, revision, version in [
        (after, target_revision, "target"),
        (before, base_revision, "current"),
    ]:
        for document in model.documents.values():
            key = (document.sha256, Path(document.path).name)
            if key in originals:
                originals[key]["versions"].append(version)
                continue
            reference = original_metadata(store, document, revision)
            reference["recover_url"] = None
            originals[key] = {
                "document_id": document.local,
                "title": document.title,
                "sha256": document.sha256,
                "revision": revision,
                "versions": [version],
                "original": reference,
            }
    return changes, list(originals.values())


def prepare_restore(store, settings, *, revision: int, reason: str, embedder=None, llm=None):
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise RestoreError(
            "a restoration reason is required and must be at most 2000 characters",
            "restore_reason_required",
        )
    with hold_sources(store, [WORKSPACE_LEASE], "restore_prepare"):
        ops = Operations(store)
        base = ops.state()["published_revision"]
        if base is None or base == revision:
            raise RestoreError("choose a previous published version to restore")
        run = store.create_run(
            kind="restore_prepare",
            source_id=None,
            params={"revision": revision, "previous_revision": base, "pid": os.getpid()},
        )
        restorations = Restorations(store)
        proposal = restorations.create(
            run.id, {"target_revision": revision, "base_revision": base, "reason": reason}
        )
        try:
            snapshot, model, descriptor, _, _ = target_material(store, settings, revision)
            initial_signature = signature(store, settings, revision, reason, descriptor, embedder)
            scope = f"{model.name}@restore-{proposal}"
            cases = ops.checks()
            questions, index = [], None
            if cases:
                from autoontology.engine.model_artifacts import (
                    embedding_identity,
                    reusable_artifact,
                )

                selected = embedder or EmbedderCache(lambda: settings, settings)()
                target = Publications(store).get(revision)
                if (
                    target
                    and target["manifest"]["profile"]["embed"] == selected[1]
                    and target["manifest"]["profile"]["endpoint_sha256"]
                    == digest(profile(settings, embedder)["endpoint"])
                    and reusable_artifact(
                        target["manifest"]["profile"].get("embedding_artifact"),
                        embedding_identity(settings, selected),
                    )
                ):
                    pg = PlaygroundStore(store)
                    pg.replace_index(
                        scope,
                        list(pg.index_rows(target["manifest"]["index"]["scope"]).values()),
                        revision=revision,
                        model=selected[1],
                    )
                embed, name = selected
                index = build_index(
                    PlaygroundStore(store),
                    scope,
                    elements_from_model(model),
                    revision=revision,
                    model=name,
                    embed=embed,
                )
                index_receipt(store, scope, model)
                for case in cases:
                    vector = embed([case["question"]], "query")[0]
                    if not vector or not any(vector) or not all(math.isfinite(v) for v in vector):
                        raise RestoreError(
                            "restoration query index could not be validated",
                            "restore_index_invalid",
                        )
                    hits = index.retrieve(vector, top_k=12)

                    async def answer():
                        from autoontology.playground.llm import llm_for

                        parts = []
                        async for part in (llm or llm_for(settings)).stream(
                            prompts.chat_messages(case["question"], hits)
                        ):
                            parts.append(part)
                        return "".join(parts)

                    text = asyncio.run(answer())
                    cited, unknown = prompts.parse_citations(text, prompts.context_tokens(hits))
                    questions.append(
                        {
                            **case,
                            "answer": text,
                            "citations": cited,
                            "ok": case["expected"].casefold() in text.casefold()
                            and bool(cited)
                            and not unknown,
                        }
                    )
            # Preparation never switches the head; also reject a raced target/change.
            _, _, current_descriptor, _, _ = target_material(store, settings, revision)
            if (
                signature(store, settings, revision, reason, current_descriptor, embedder)
                != initial_signature
            ):
                raise RestoreError("workspace or restoration target changed; prepare again")
            validation = {
                "ok": bool(questions) and all(q["ok"] for q in questions),
                "revision": revision,
                "published_revision": base,
                "scope": scope,
                "questions": questions,
                "checks_configured": bool(questions),
                "indexed_elements": len(index.elements) if index else 0,
                "index": index_receipt(store, scope, model) if index else None,
                "token": initial_signature,
                "blocking_reasons": []
                if questions
                else ["Configure at least one representative question before restoration."],
                "validated_at": now_iso(),
            }
            old = ops.model(base)
            changes, originals = impact_details(store, old, model, base, revision)
            packet = {
                "version": 1,
                "changes": changes,
                "originals": originals,
                "target_revision": revision,
                "base_revision": base,
                "reason": reason,
                "target": descriptor,
                "signature": initial_signature,
                "validation": validation,
                "diff": diff(
                    old,
                    model,
                    old_sources=store.get_snapshot(base).sources,
                    new_sources=snapshot.sources,
                ),
                "sql_data": "live_at_execution; database rows are not restored",
                "pending_changes_preserved": len(ops.changes()),
                "review_effect": "decision history is retained; candidate reviews are recalculated against the restored base",
                "candidate_revision": ops.state()["candidate_revision"],
                "actor": {"id": "local-unverified", "verified": False},
                "expires_at": (utc_now() + timedelta(minutes=30)).isoformat(),
            }
            result = restorations.prepared(proposal, packet, ok=validation["ok"])
            store.update_run(
                run.id,
                status="succeeded",
                params={**run.params, "proposal_id": proposal, "ready": validation["ok"]},
            )
            return result
        except BaseException as exc:
            with store._conn() as conn:
                conn.execute(
                    "UPDATE restore_proposals SET status='failed' WHERE id=? AND status='preparing'",
                    (proposal,),
                )
            store.update_run(
                run.id,
                status="failed",
                error="Restoration preparation failed; inspect the target, representative checks and provider status.",
            )
            if isinstance(exc, Exception) and not isinstance(exc, RestoreError):
                raise RestoreError(
                    "Restoration checks could not complete. Check provider login and embedding availability, then prepare again.",
                    "restore_validation_failed",
                    503,
                ) from exc
            raise


def run_restore(
    store, settings, *, proposal: str, confirmation: str, embedder=None, exporter=None, log=None
):
    with hold_sources(store, [WORKSPACE_LEASE], "restore"):
        restorations = Restorations(store)
        result = restorations.get(proposal)
        packet = result["packet"]
        if confirmation != result["confirmation"]:
            raise RestoreError(
                "explicit confirmation must match the exact prepared restoration packet",
                "restore_confirmation_required",
            )
        if result["status"] == "committed":
            if Publications(store).check_model(result["result_revision"]) is None:
                raise RestoreError(
                    "the committed restoration publication is missing; recover from a verified checkpoint",
                    "restore_receipt_invalid",
                )
            return SimpleNamespace(
                run_id=result["execution_run_id"],
                status="succeeded",
                revision=result["result_revision"],
                already_committed=True,
            )
        if result["status"] != "ready" or not packet["validation"]["ok"]:
            raise RestoreError(
                "restoration is blocked or incomplete; prepare a valid packet first",
                "restore_blocked",
            )
        if utc_now() >= datetime.fromisoformat(packet["expires_at"]):
            raise RestoreError("restoration packet expired; prepare again", "restore_expired")
        snapshot, model, descriptor, upstream, marker = target_material(
            store, settings, packet["target_revision"]
        )

        def recheck():
            if utc_now() >= datetime.fromisoformat(packet["expires_at"]):
                raise RestoreError("restoration packet expired; prepare again", "restore_expired")
            _, _, latest, _, _ = target_material(store, settings, packet["target_revision"])
            if (
                signature(
                    store, settings, packet["target_revision"], packet["reason"], latest, embedder
                )
                != packet["signature"]
            ):
                raise RestoreError("workspace, checks, profile or target changed; prepare again")
            try:
                current = index_receipt(store, packet["validation"]["scope"], model)
            except PublicationError as exc:
                raise RestoreError("restoration validation index changed; prepare again") from exc
            if current != packet["validation"]["index"]:
                raise RestoreError("restoration validation index changed; prepare again")

        recheck()
        run = store.create_run(
            kind="restore",
            source_id=None,
            params={
                "proposal_id": proposal,
                "revision": snapshot.revision,
                "previous_revision": packet["base_revision"],
                "pid": os.getpid(),
            },
        )
        with store._conn() as conn:
            conn.execute(
                "UPDATE restore_proposals SET execution_run_id=? WHERE id=? AND status='ready'",
                (run.id, proposal),
            )
        target = Publications(store).get(snapshot.revision)
        bindings = (
            target["manifest"]["source_bindings"]
            if target
            else [{"revision": snapshot.revision, "sources": descriptor["sources"]}]
        )
        restoration = {
            "proposal_id": proposal,
            "packet_sha256": result["sha256"],
            "target_revision": snapshot.revision,
            "target_publication_id": descriptor["publication_id"],
            "reason": packet["reason"],
            "confirmation": confirmation,
            "actor": packet["actor"],
            "confirmed_at": now_iso(),
            "upstream_fetched_at": marker.get("upstream_fetched_at"),
        }
        return install_publication(
            store,
            settings,
            model=model,
            items=[],
            run=run,
            revision=snapshot.revision,
            validated=packet["validation"],
            previous=packet["base_revision"],
            recheck=recheck,
            embedder=embedder,
            exporter=exporter,
            frozen_upstream=upstream,
            restoration=restoration,
            sources=snapshot.sources,
            source_bindings=bindings,
        )
