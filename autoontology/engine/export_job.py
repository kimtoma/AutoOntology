"""``autoontology export``: the merged ontology (structured Terms + document concepts joined
through the ``align`` verdicts) as Turtle, JSON-LD and GraphML, plus ``report.md`` and the
structured sources' upstream exports (``model.yaml``, ``ossie.zip``).

Steps ``collect -> serialize -> upstream -> report`` (:func:`autoontology.engine.jobs._execute`).
The model is built once in ``collect`` (:func:`autoontology.export.merged.build`) and every
format writer serialises that same object; ``serialize`` re-parses the Turtle file and
fails the run if the export invariants do not hold.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import Settings
from autoontology.engine.client import BackendClient
from autoontology.engine.jobs import (
    BUILD_KIND,
    JobError,
    RunResult,
    _execute,
    hold_sources,
    setup_job_logging,
    verify_sources,
)
from autoontology.export import graphml, merged, rdf
from autoontology.export import report as export_report
from autoontology.store.alignments import AlignmentStore
from autoontology.store.db import Run, Source, Store
from autoontology.store.documents import DocumentStore
from autoontology.structured.semantic import StructuredSnapshot, database_id_for, fetch_snapshot
from autoontology.unstructured.report import display_path

logger = logging.getLogger("autoontology.jobs")

EXPORT_KIND = "export"
EXPORT_STEPS: tuple[str, ...] = ("collect", "serialize", "upstream", "report")
UPSTREAM_FILES: tuple[str, ...] = ("model.yaml", "ossie.zip")


@dataclass
class _ExportCtx:
    settings: Settings
    store: Store
    run: Run
    name: str
    structured: list[Source]
    documents: list[Source]
    client: BackendClient
    out_dir: Path
    model: merged.MergedOntology | None = None
    snapshots: list[StructuredSnapshot] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    upstream: list[dict[str, Any]] = field(default_factory=list)
    expected_classes: int | None = None


def _file_entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def documents_inputs(store: Store, documents: list[Source]) -> list[merged.DocumentsInput]:
    """The store side of the merged model, per documents source."""
    docs, aligns = DocumentStore(store), AlignmentStore(store)
    inputs = []
    for s in documents:
        rows = docs.list_documents(s.id)
        shown = [type(d)(**{**d.__dict__, "path": display_path(d.path)}) for d in rows]
        inputs.append(merged.DocumentsInput(
            source=s.name, documents=shown, concepts=docs.list_concepts(s.id),
            relations=docs.list_relations(s.id), evidence=docs.evidence_rows(s.id),
            alignments=aligns.list(s.id), previews=aligns.list(s.id, preview=True)))
    return inputs


def collect_model(name: str, store: Store, client: BackendClient, structured: list[Source],
                  documents: list[Source]
                  ) -> tuple[merged.MergedOntology, list[StructuredSnapshot],
                             list[merged.DocumentsInput]]:
    """Build the merged model exactly as ``export`` does (the web graph uses this too)."""
    snapshots = [fetch_snapshot(client, s) for s in structured]
    inputs = documents_inputs(store, documents)
    return merged.build(name, structured=snapshots, documents=inputs), snapshots, inputs


def _step_collect(ctx: _ExportCtx) -> dict[str, Any]:
    ctx.model, ctx.snapshots, inputs = collect_model(ctx.name, ctx.store, ctx.client,
                                                     ctx.structured, ctx.documents)
    ctx.expected_classes = merged.expected_class_count(ctx.snapshots, inputs)
    for name in ctx.model.without_evidence:
        logger.warning("  concept %s has no evidence row: exported without "
                       "prov:wasDerivedFrom", name)
    counts = ctx.model.counts()
    logger.info("  %d class(es) (%d Term, %d document), %d object / %d datatype propert(ies), "
                "%d individual(s), %d document(s), %d evidence node(s); %d concept(s) folded "
                "into Terms", counts["classes"], counts["classes_structured"],
                counts["classes_document"], counts["object_properties"],
                counts["datatype_properties"], counts["individuals"], counts["documents"],
                counts["evidence"], counts["merged_concepts"])
    return {"counts": counts, "alignment": ctx.model.alignment_counts,
            "structured": [s.name for s in ctx.structured],
            "documents": [s.name for s in ctx.documents]}


def _step_serialize(ctx: _ExportCtx) -> dict[str, Any]:
    assert ctx.model is not None
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    paths = rdf.write(ctx.model, ctx.out_dir)
    ctx.files = [paths["ttl"], paths["jsonld"], graphml.write(ctx.model, ctx.out_dir)]
    check = rdf.roundtrip(paths["ttl"], ctx.model, expected_classes=ctx.expected_classes)
    logger.info("  Turtle round-trip: %s triples, %s classes (expected %s), %s properties; "
                "%s", check["triples"], check["classes"], check["expected_classes"],
                check["properties"], "ok" if check["ok"] else "FAILED")
    if not check["ok"]:
        raise RuntimeError(f"Turtle round-trip check failed: {check}")
    return {"files": [p.name for p in ctx.files], "roundtrip": check}


def _latest_upstream_export(ctx: _ExportCtx, source: Source) -> Path | None:
    for run in ctx.store.list_runs(source.id, limit=50):
        if run.kind != BUILD_KIND:
            continue
        folder = ctx.settings.exports_dir / source.name / run.id
        if all((folder / f).is_file() for f in UPSTREAM_FILES):
            return folder
    return None


def _step_upstream(ctx: _ExportCtx) -> dict[str, Any]:
    single = len(ctx.structured) == 1
    for source in ctx.structured:
        folder = _latest_upstream_export(ctx, source)
        for name in UPSTREAM_FILES:
            target = ctx.out_dir / (name if single else f"{source.name}-{name}")
            if folder is not None:
                shutil.copyfile(folder / name, target)
                origin = f"copied from {display_path(str(folder / name))}"
            else:
                db_id, _ = database_id_for(ctx.client, source)
                data = (ctx.client.export_model([db_id], "auto_ontology") if name ==
                        "model.yaml" else ctx.client.export_model([db_id], "ossie",
                                                                 include_ontology=True))
                target.write_bytes(data)
                origin = "exported now (no earlier build export)"
            ctx.files.append(target)
            ctx.upstream.append({"name": target.name, "origin": origin, "source": source.name})
            logger.info("  %s: %s", target.name, origin)
    return {"upstream": ctx.upstream}


def _step_report(ctx: _ExportCtx) -> dict[str, Any]:
    assert ctx.model is not None
    entries = [_file_entry(p) for p in ctx.files]
    path = export_report.write(ctx.model, ctx.out_dir, run_id=ctx.run.id,
                               files=entries, upstream=ctx.upstream)
    return {"report": str(path), "files": entries}


EXPORT_STEP_FUNCS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "collect": _step_collect,
    "serialize": _step_serialize,
    "upstream": _step_upstream,
    "report": _step_report,
}


def resolve_sources(store: Store, name: str, sources: list[str] | None
                    ) -> tuple[list[Source], list[Source]]:
    """``--sources`` (default: *name*). A documents source also brings the structured
    sources its alignments point at, unless ``--sources`` already names structured ones."""
    names = [s.strip() for s in (sources or [name]) if s.strip()]
    found: list[Source] = []
    for n in dict.fromkeys(names):
        source = store.get_source(n)
        if source is None:
            raise JobError(f"unknown source {n!r} (see `autoontology source list`)")
        found.append(source)
    documents = [s for s in found if s.kind == "documents"]
    structured = [s for s in found if s.kind in ("files", "database")]
    if not structured:
        aligns = AlignmentStore(store)
        linked = sorted({a.structured_source for d in documents for a in aligns.list(d.id)
                         if a.structured_source})
        for n in linked:
            source = store.get_source(n)
            if source is not None:
                structured.append(source)
    return structured, documents


def run_export(name: str, *, settings: Settings, store: Store,
               sources: list[str] | None = None, out: Path | None = None,
               client: BackendClient | None = None) -> RunResult:
    log_path = setup_job_logging(settings)
    if not merged.slug(name) == name.lower():
        raise JobError(f"invalid export name {name!r}: use letters, digits, _ and -")
    structured, documents = resolve_sources(store, name, sources)
    if not structured and not documents:
        raise JobError("nothing to export")
    # an export reads every included source: none may be mid-build/extract/align/removal
    with hold_sources(store, [s.name for s in structured + documents], EXPORT_KIND,
                      log_path=log_path):
        return _run_export_held(name, settings=settings, store=store, structured=structured,
                                documents=documents, out=out, client=client)


def _run_export_held(name: str, *, settings: Settings, store: Store, structured: list[Source],
                     documents: list[Source], out: Path | None,
                     client: BackendClient | None) -> RunResult:
    structured = verify_sources(store, structured)
    documents = verify_sources(store, documents)
    client = client or BackendClient(settings.backend_url)
    if structured:
        client.health()  # BackendUnavailable -> "run autoontology up"
    owner = store.get_source(name)
    run = store.create_run(kind=EXPORT_KIND, source_id=owner.id if owner else None, params={
        "name": name, "structured": [s.name for s in structured],
        "documents": [s.name for s in documents], "out": str(out) if out else None,
        "pid": os.getpid()})
    out_dir = Path(out) if out else settings.exports_dir / name / run.id
    logger.info("export run %s: %s (structured: %s; documents: %s) -> %s", run.id, name,
                ", ".join(s.name for s in structured) or "-",
                ", ".join(s.name for s in documents) or "-", out_dir)
    ctx = _ExportCtx(settings=settings, store=store, run=run, name=name,
                     structured=structured, documents=documents, client=client,
                     out_dir=out_dir)
    result = _execute(ctx, list(EXPORT_STEPS), skip={}, already=set(),
                      steps=EXPORT_STEP_FUNCS)
    result.export_dir = out_dir
    return result
