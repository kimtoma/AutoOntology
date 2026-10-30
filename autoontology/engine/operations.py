# SPDX-License-Identifier: MIT
"""Validate and publish approved knowledge with an atomic published-head switch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

from autoontology.config import Settings
from autoontology.engine.build import WORKSPACE_LEASE
from autoontology.engine.jobs import RunCancelled, hold_sources
from autoontology.playground import prompts
from autoontology.playground.elements import elements_from_model
from autoontology.playground.index import build_index
from autoontology.playground.service import EmbedderCache, embed_triplet
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import Store, now_iso
from autoontology.store.operations import Operations, OperationsError
from autoontology.store.provenance import sync_directory
from autoontology.store.publications import PublicationError, Publications, index_receipt


def profile(settings: Settings, embedder=None) -> dict:
    from autoontology.engine.model_artifacts import embedding_identity

    return {
        "chat": settings.effective_chat_model,
        "chat_provider": settings.chat_provider,
        "embed": embedder[1] if embedder else settings.effective_embed_model,
        "endpoint": embed_triplet(settings)[0],
        "embedding_artifact": embedding_identity(settings, embedder),
    }


def prepare_index(store, settings, model, scope, revision, embedder=None):
    from autoontology.engine.model_artifacts import embedding_identity, reusable_artifact

    embed, name = embedder or EmbedderCache(lambda: settings, settings)()
    pg = PlaygroundStore(store)
    # Reuse only content- and model-identical vectors from the published index.
    if not pg.index_rows(scope):
        pub = Operations(store).state()["published_revision"]
        receipt = Publications(store).get(pub) if pub is not None else None
        seed = {}
        if receipt and reusable_artifact(
            receipt["manifest"]["profile"].get("embedding_artifact"),
            embedding_identity(settings, (embed, name)),
        ):
            # Verify the immutable receipt before copying its vectors.
            Publications(store).verify(pub, settings.exports_dir)
            seed = pg.index_rows(receipt["manifest"]["index"]["scope"])
        if seed:
            pg.replace_index(
                scope, [r for r in seed.values() if r.model == name], revision=revision, model=name
            )
    return build_index(
        pg, scope, elements_from_model(model), revision=revision, model=name, embed=embed
    ), embed


def validate(store: Store, settings: Settings, *, revision: int, embedder=None, llm=None) -> dict:
    from autoontology.engine.apply import pending_work

    ops = Operations(store)
    if pending_work(store)["total"]:
        raise OperationsError(
            "generate a candidate from pending connection decisions before validation"
        )
    model, items = ops.compose(revision)
    signature = ops.signature(revision, profile(settings, embedder))
    scope = f"{model.name}@proposal-{signature}"
    cases = ops.checks()
    index = None
    if cases:
        index, embed = prepare_index(store, settings, model, scope, revision, embedder)
        if len(index._rows) != len(index.elements):
            raise OperationsError("some knowledge has no retrieval vector")
    questions = []
    for case in cases:
        vector = embed([case["question"]], "query")[0]
        hits = index.retrieve(vector or [], top_k=12)

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
        ok = case["expected"].casefold() in text.casefold() and bool(cited) and not unknown
        questions.append({**case, "answer": text, "citations": cited, "ok": ok})
    if ops.signature(revision, profile(settings, embedder)) != signature:
        raise OperationsError("workspace or model identity changed; revalidate")
    result = {
        "ok": bool(questions) and all(q["ok"] for q in questions),
        "revision": revision,
        "published_revision": ops.state()["published_revision"],
        "approved": len(items),
        "excluded": len(ops.changes()) - len(items),
        "indexed_elements": len(index.elements) if index else 0,
        "questions": questions,
        "checks_configured": bool(questions),
        "blocking_reasons": []
        if questions
        else [
            "Configure at least one representative question and expected answer before publication."
        ],
        "token": signature,
        "scope": scope,
        "validated_at": now_iso(),
        "index": index_receipt(store, scope, model) if index else None,
    }
    with store._conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ops_validations VALUES(?,?,?,?)",
            (signature, signature, json.dumps(result, ensure_ascii=False), now_iso()),
        )
    return result


def write_exports(model, folder: Path, revision: int, run, upstream=()) -> None:
    from autoontology.export import graphml, okf, ossie, rdf, report

    folder.mkdir(parents=True, exist_ok=False)
    paths = rdf.write(model, folder)
    graphml.write(model, folder)
    check = rdf.roundtrip(paths["ttl"], model)
    if not check["ok"]:
        raise OperationsError("RDF integrity validation failed")
    upstream_paths = []
    for name, payload in upstream:
        target = folder / "upstream" / f"{name}.ossie.zip"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(payload)
        upstream_paths.append((name, target))
    ossie.write(
        model, folder, revision=revision, created_at=run.started_at, upstream=upstream_paths
    )
    okf.write(model, folder, revision=revision, created_at=run.started_at)
    report.write(model, folder, run_id=run.id, revision=revision, files=[], upstream=[])
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    marker = {
        "workspace": model.name,
        "revision": revision,
        "snapshot_run": run.id,
        "snapshot_created_at": run.started_at,
        "export_run": run.id,
        "format": "all",
        "formats": {
            k: {"export_run": run.id, "exported_at": run.started_at}
            for k in ("rdf", "ossie", "okf")
        },
        "upstream_fetched_at": now_iso() if upstream_paths else None,
        "files": [*names, "export.json"],
        "file_sha256": {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in names},
    }
    (folder / "export.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2))


def run_publish(
    store: Store,
    settings: Settings,
    *,
    revision: int,
    token: str,
    embedder=None,
    exporter=None,
    log=None,
):
    ops = Operations(store)
    with hold_sources(store, [WORKSPACE_LEASE], "publish"):
        signature = ops.signature(revision, profile(settings, embedder))
        with store._conn() as conn:
            row = conn.execute(
                "SELECT result_json FROM ops_validations WHERE token=?", (token,)
            ).fetchone()
        validated = json.loads(row[0]) if row else None
        if (
            token != signature
            or not validated
            or not validated["ok"]
            or not validated.get("checks_configured")
            or not validated.get("questions")
        ):
            raise OperationsError("validation changed or failed; revalidate before publication")
        model, items = ops.compose(revision)
        try:
            current_index = index_receipt(store, validated["scope"], model)
        except PublicationError as exc:
            raise PublicationError("validation index is incomplete or changed; revalidate") from exc
        if current_index != validated.get("index"):
            raise PublicationError("validation index changed; revalidate")
        publications = Publications(store)
        publications.originals(model)
        previous = ops.state()["published_revision"]
        run = store.create_run(
            kind="publish",
            source_id=None,
            params={
                "revision": revision,
                "previous_revision": previous,
                "approved": len(items),
                "pid": os.getpid(),
            },
        )

        def recheck():
            if ops.signature(revision, profile(settings, embedder)) != signature:
                raise OperationsError("workspace changed; revalidate")

        return install_publication(
            store,
            settings,
            model=model,
            items=items,
            run=run,
            revision=revision,
            validated=validated,
            previous=previous,
            embedder=embedder,
            exporter=exporter,
            recheck=recheck,
        )


def install_publication(
    store,
    settings,
    *,
    model,
    items,
    run,
    revision,
    validated,
    previous,
    recheck,
    embedder=None,
    exporter=None,
    frozen_upstream=None,
    restoration=None,
    sources=None,
    source_bindings=None,
):
    """Prepare everything, then atomically commit one operational publication.

    Callers hold the workspace lease and supply a content-bound final guard.
    Restorations use frozen mappings; they never ask a mutable backend for old truth.
    """
    ops, publications = Operations(store), Publications(store)
    folder = None
    work = None
    try:
        store.start_step(run.id, "prepare")
        new = store.save_snapshot(run.id, model, sources=sources)
        pg = PlaygroundStore(store)
        old = pg.index_rows(validated["scope"])
        frozen_scope = f"{model.name}@publication-r{new}"
        pg.replace_index(
            frozen_scope,
            list(old.values()),
            revision=new,
            model=profile(settings, embedder)["embed"],
        )
        frozen_index = index_receipt(store, frozen_scope, model)
        if {**frozen_index, "scope": validated["scope"]} != validated["index"]:
            raise PublicationError("validation index changed during preparation; revalidate")
        folder = settings.exports_dir / f"r{new:04}"
        if folder.exists():
            raise OperationsError("refusing to replace an existing version export")
        work = settings.exports_dir / f".publish-{run.id}"
        if exporter:
            exporter(model, work, new, run)
        elif frozen_upstream is not None:
            write_exports(model, work, new, run, upstream=frozen_upstream)
        else:
            from autoontology.engine.client import BackendClient

            upstream = []
            with BackendClient(settings.backend_url) as client:
                for name in model.structured_sources:
                    source = store.get_source(name)
                    represented = any(
                        c.source == "structured" and c.structured_source == name
                        for c in model.classes.values()
                    )
                    if source and source.catalog_database_id and represented:
                        upstream.append(
                            (
                                source.name,
                                client.export_model(
                                    [source.catalog_database_id], "ossie", include_ontology=True
                                ),
                            )
                        )
            write_exports(model, work, new, run, upstream=upstream)
        if restoration and frozen_upstream:
            marker_path = work / "export.json"
            marker = json.loads(marker_path.read_text())
            marker["upstream_fetched_at"] = restoration.get("upstream_fetched_at")
            marker["upstream_origin"] = {
                "restored_from_revision": restoration["target_revision"],
                "publication_id": restoration.get("target_publication_id"),
            }
            marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2))
        publication = publications.prepare(
            new,
            candidate=revision,
            previous=previous,
            validation=validated,
            items=items,
            profile=profile(settings, embedder),
            scope=frozen_scope,
            folder=work,
            restoration=restoration,
            source_bindings=source_bindings,
        )
        for path in work.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        for path in sorted(
            (p for p in work.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            sync_directory(path)
        sync_directory(work)
        os.replace(work, folder)
        sync_directory(folder.parent)
        publications.verify(new, settings.exports_dir, prepared=True)
        store.finish_step(
            run.id,
            "prepare",
            status="succeeded",
            detail={
                "revision": new,
                "indexed_elements": frozen_index["elements"],
                "export_dir": str(folder),
            },
        )
        # Re-check decisions/checks after expensive preparation, before the switch.
        recheck()
        store.start_step(run.id, "publish")
        ops.commit(
            new,
            previous,
            items,
            publication_id=publication["id"],
            run_id=run.id,
            restore_proposal_id=restoration["proposal_id"] if restoration else None,
        )
        return SimpleNamespace(run_id=run.id, status="succeeded", revision=new)
    except BaseException as exc:
        committed = store.get_run(run.id).status == "succeeded"
        if not committed:
            store.update_run(
                run.id,
                status="cancelled"
                if isinstance(exc, (RunCancelled, KeyboardInterrupt))
                else "failed",
                error=f"{type(exc).__name__}: {str(exc)[:250]}",
            )
        if work and work.exists():
            shutil.rmtree(work)
        if (
            not committed
            and folder
            and folder.exists()
            and ops.state()["published_revision"] == previous
        ):
            shutil.rmtree(folder)
        raise
