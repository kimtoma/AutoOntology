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
from autoontology.engine.jobs import hold_sources
from autoontology.playground import prompts
from autoontology.playground.elements import elements_from_model
from autoontology.playground.index import build_index
from autoontology.playground.service import EmbedderCache
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import Store, now_iso
from autoontology.store.operations import Operations, OperationsError


def profile(settings: Settings, embedder=None) -> dict:
    return {
        "chat": settings.effective_chat_model,
        "chat_provider": settings.chat_provider,
        "embed": embedder[1] if embedder else settings.effective_embed_model,
        "endpoint": settings.ollama_endpoint
        if settings.embed_provider == "ollama"
        else settings.openai_base_url,
    }


def prepare_index(store, settings, model, scope, revision, embedder=None):
    embed, name = embedder or EmbedderCache(lambda: settings, settings)()
    pg = PlaygroundStore(store)
    # Reuse only content- and model-identical vectors from the published index.
    if not pg.index_rows(scope):
        pub = Operations(store).state()["published_revision"]
        seed = pg.index_rows(f"{model.name}@r{pub}") or pg.index_rows(model.name)
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
        folder = None
        work = None
        try:
            store.start_step(run.id, "prepare")
            new = store.save_snapshot(run.id, model)
            pg = PlaygroundStore(store)
            old = pg.index_rows(validated["scope"])
            pg.replace_index(
                f"{model.name}@r{new}",
                list(old.values()),
                revision=new,
                model=profile(settings, embedder)["embed"],
            )
            index, _ = prepare_index(store, settings, model, f"{model.name}@r{new}", new, embedder)
            if len(index._rows) != len(index.elements):
                raise OperationsError("retrieval index is incomplete")
            folder = settings.exports_dir / f"r{new:04}"
            if folder.exists():
                raise OperationsError("refusing to replace an existing version export")
            work = settings.exports_dir / f".publish-{run.id}"
            if exporter:
                exporter(model, work, new, run)
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
            os.replace(work, folder)
            store.finish_step(
                run.id,
                "prepare",
                status="succeeded",
                detail={
                    "revision": new,
                    "indexed_elements": len(index.elements),
                    "export_dir": str(folder),
                },
            )
            # Re-check decisions/checks after expensive preparation, before the switch.
            if ops.signature(revision, profile(settings, embedder)) != signature:
                raise OperationsError("workspace changed; revalidate")
            store.start_step(run.id, "publish")
            ops.commit(new, previous, items)
            store.finish_step(run.id, "publish", status="succeeded", detail={"revision": new})
            store.update_run(
                run.id, status="succeeded", params={**run.params, "published_revision": new}
            )
            return SimpleNamespace(run_id=run.id, status="succeeded", revision=new)
        except BaseException as exc:
            store.update_run(
                run.id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:250]}"
            )
            if work and work.exists():
                shutil.rmtree(work)
            if folder and folder.exists() and ops.state()["published_revision"] == previous:
                shutil.rmtree(folder)
            raise
