"""``autoontology export``: the latest (or a given) ontology snapshot as Turtle, JSON-LD
and GraphML, plus ``report.md`` and the structured sources' upstream exports.

Steps ``collect -> serialize -> upstream -> report`` (:func:`autoontology.engine.jobs._execute`).
``collect`` loads the snapshot written by ``autoontology build`` -- the export never
rebuilds the model -- and every format writer serialises that same object; ``serialize``
re-parses the Turtle file and fails the run if the export invariants do not hold. The
files go to ``exports/r<revision:04d>/`` (``--out DIR`` writes ``DIR/r<revision:04d>/``):
``ontology.ttl``, ``ontology.jsonld``, ``ontology.graphml``, ``report.md``, the
``export.json`` marker (workspace, revision, snapshot run and time) and, per structured
source, ``upstream/<source>.model.yaml`` and ``upstream/<source>.ossie.zip`` fetched from
the backend. Exporting a revision again replaces its folder -- but only a folder that holds
nothing except what this exporter writes (:data:`EXPORT_ENTRIES`): a directory with any
other file is refused, never deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoontology.config import REPO_ROOT, Settings
from autoontology.engine.client import BackendClient
from autoontology.engine.jobs import (
    JobError,
    RunResult,
    _execute,
    hold_sources,
    setup_job_logging,
)
from autoontology.export import graphml, rdf
from autoontology.export import report as export_report
from autoontology.ontology import model as merged
from autoontology.store.alignments import AlignmentStore
from autoontology.store.db import Run, Snapshot, Source, Store
from autoontology.store.documents import DocumentStore
from autoontology.structured.semantic import database_id_for
from autoontology.unstructured.report import display_path

logger = logging.getLogger("autoontology.jobs")

EXPORT_KIND = "export"
EXPORT_STEPS: tuple[str, ...] = ("collect", "serialize", "upstream", "report")
UPSTREAM_DIR = "upstream"
#: per structured source: (file suffix, upstream export format)
UPSTREAM_FILES: tuple[tuple[str, str], ...] = (
    ("model.yaml", "auto_ontology"),
    ("ossie.zip", "ossie"),
)


#: the metadata file every export writes; also marks a folder as written by this exporter
EXPORT_MARKER = "export.json"
#: every top-level name an export folder may hold (``ossie/`` and ``okf/`` are reserved
#: for the Ossie / OKF exporters); replacing a folder removes these and nothing else
EXPORT_ENTRIES = frozenset(
    {
        "ontology.ttl",
        "ontology.jsonld",
        "ontology.graphml",
        "report.md",
        EXPORT_MARKER,
        UPSTREAM_DIR,
        "ossie",
        "okf",
    }
)
REVISION_DIR = re.compile(r"r\d{4,}")


def revision_dir_name(revision: int) -> str:
    return f"r{int(revision):04d}"


def export_target(settings: Settings, revision: int, out: Path | None) -> Path:
    """The folder an export of *revision* goes to: ``exports/r<rev>/`` or ``<out>/r<rev>/``.

    ``--out`` names a parent directory, never the folder that gets replaced, and may not
    be the filesystem root, the home directory or the repository itself.
    """
    if out is None:
        return settings.exports_dir / revision_dir_name(revision)
    parent = Path(out).expanduser().resolve()
    if parent in {Path(parent.anchor), Path.home().resolve(), REPO_ROOT.resolve()}:
        raise JobError(f"refusing to export into {parent}: pick a dedicated directory")
    if parent.exists() and not parent.is_dir():
        raise JobError(f"--out {parent} is not a directory")
    return parent / revision_dir_name(revision)


def _owned(settings: Settings, final: Path) -> bool:
    """A ``r<NNNN>`` folder directly under the configured exports root belongs to us."""
    return bool(REVISION_DIR.fullmatch(final.name)) and (
        final.parent.resolve() == settings.exports_dir.resolve()
    )


def check_replaceable(settings: Settings, final: Path) -> None:
    """Refuse to replace *final* unless it is a revision folder under the exports root or
    holds only files this exporter writes."""
    if not final.exists() and not final.is_symlink():
        return
    if final.is_symlink() or not final.is_dir():
        raise JobError(f"refusing to replace {final}: not an export folder")
    if _owned(settings, final):
        return
    foreign = sorted(p.name for p in final.iterdir() if p.name not in EXPORT_ENTRIES)
    if foreign:
        raise JobError(
            f"refusing to replace {final}: it holds files this exporter did not write "
            f"({', '.join(foreign[:5])}{', ...' if len(foreign) > 5 else ''})"
        )


def _clear_export(settings: Settings, final: Path) -> None:
    """Remove *final*: all of it under the exports root, elsewhere only the exporter's own
    entries and then the (by then empty) folder."""
    if _owned(settings, final):
        shutil.rmtree(final)
        return
    for name in EXPORT_ENTRIES:
        path = final / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    final.rmdir()


@dataclass
class _ExportCtx:
    settings: Settings
    store: Store
    run: Run
    snapshot: Snapshot
    structured: list[Source]
    client: BackendClient
    out_dir: Path
    model: merged.MergedOntology | None = None
    files: list[Path] = field(default_factory=list)
    upstream: list[dict[str, Any]] = field(default_factory=list)


def _file_entry(path: Path, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "name": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def documents_inputs(store: Store, documents: list[Source]) -> list[merged.DocumentsInput]:
    """The store side of the merged model, per documents source."""
    docs, aligns = DocumentStore(store), AlignmentStore(store)
    inputs = []
    for s in documents:
        rows = docs.list_documents(s.id)
        shown = [type(d)(**{**d.__dict__, "path": display_path(d.path)}) for d in rows]
        inputs.append(
            merged.DocumentsInput(
                source=s.name,
                documents=shown,
                concepts=docs.list_concepts(s.id),
                relations=docs.list_relations(s.id),
                evidence=docs.evidence_rows(s.id),
                alignments=aligns.list(s.id),
                previews=aligns.list(s.id, preview=True),
            )
        )
    return inputs


def _step_collect(ctx: _ExportCtx) -> dict[str, Any]:
    assert ctx.snapshot.model is not None
    ctx.model = merged.MergedOntology.from_dict(ctx.snapshot.model)
    for name in ctx.model.without_evidence:
        logger.warning(
            "  concept %s has no evidence row: exported without prov:wasDerivedFrom", name
        )
    counts = ctx.model.counts()
    logger.info(
        "  revision %d: %d class(es) (%d Term, %d document), %d object / %d datatype "
        "propert(ies), %d individual(s), %d document(s), %d evidence node(s); %d concept(s) "
        "folded into Terms",
        ctx.snapshot.revision,
        counts["classes"],
        counts["classes_structured"],
        counts["classes_document"],
        counts["object_properties"],
        counts["datatype_properties"],
        counts["individuals"],
        counts["documents"],
        counts["evidence"],
        counts["merged_concepts"],
    )
    return {
        "revision": ctx.snapshot.revision,
        "snapshot_run": ctx.snapshot.run_id,
        "counts": counts,
        "alignment": ctx.model.alignment_counts,
        "structured": ctx.model.structured_sources,
        "documents": ctx.model.documents_sources,
    }


def _step_serialize(ctx: _ExportCtx) -> dict[str, Any]:
    assert ctx.model is not None
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    paths = rdf.write(ctx.model, ctx.out_dir)
    ctx.files = [paths["ttl"], paths["jsonld"], graphml.write(ctx.model, ctx.out_dir)]
    check = rdf.roundtrip(paths["ttl"], ctx.model, expected_classes=ctx.model.expected_classes)
    logger.info(
        "  Turtle round-trip: %s triples, %s classes (expected %s), %s properties; %s",
        check["triples"],
        check["classes"],
        check["expected_classes"],
        check["properties"],
        "ok" if check["ok"] else "FAILED",
    )
    if not check["ok"]:
        raise RuntimeError(f"Turtle round-trip check failed: {check}")
    return {"files": [p.name for p in ctx.files], "roundtrip": check}


def _step_upstream(ctx: _ExportCtx) -> dict[str, Any]:
    folder = ctx.out_dir / UPSTREAM_DIR
    for source in ctx.structured:
        db_id, _ = database_id_for(ctx.client, source)
        for suffix, fmt in UPSTREAM_FILES:
            data = (
                ctx.client.export_model([db_id], fmt, include_ontology=True)
                if fmt == "ossie"
                else ctx.client.export_model([db_id], fmt)
            )
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{source.name}.{suffix}"
            target.write_bytes(data)
            ctx.files.append(target)
            ctx.upstream.append(
                {
                    "name": target.relative_to(ctx.out_dir).as_posix(),
                    "origin": f"upstream {fmt} export of {source.name}",
                    "source": source.name,
                }
            )
            logger.info("  %s: %d bytes", target.relative_to(ctx.out_dir).as_posix(), len(data))
    return {"upstream": ctx.upstream}


def export_metadata(ctx: _ExportCtx) -> dict[str, Any]:
    """What :data:`EXPORT_MARKER` records: which snapshot of which workspace this is."""
    return {
        "workspace": ctx.settings.workspace,
        "revision": ctx.snapshot.revision,
        "snapshot_run": ctx.snapshot.run_id,
        "snapshot_created_at": ctx.snapshot.created_at,
        "export_run": ctx.run.id,
    }


def read_export_metadata(folder: Path) -> dict[str, Any] | None:
    """The :data:`EXPORT_MARKER` of an export folder (None when missing or unreadable)."""
    try:
        data = json.loads((folder / EXPORT_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _step_report(ctx: _ExportCtx) -> dict[str, Any]:
    assert ctx.model is not None
    entries = [_file_entry(p, ctx.out_dir) for p in ctx.files]
    path = export_report.write(
        ctx.model,
        ctx.out_dir,
        run_id=ctx.run.id,
        files=entries,
        upstream=ctx.upstream,
        revision=ctx.snapshot.revision,
    )
    marker = ctx.out_dir / EXPORT_MARKER
    marker.write_text(json.dumps(export_metadata(ctx), indent=2) + "\n", encoding="utf-8")
    return {
        "report": str(path),
        "files": [*entries, _file_entry(path, ctx.out_dir), _file_entry(marker, ctx.out_dir)],
    }


EXPORT_STEP_FUNCS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "collect": _step_collect,
    "serialize": _step_serialize,
    "upstream": _step_upstream,
    "report": _step_report,
}


def run_export(
    store: Store,
    settings: Settings,
    *,
    revision: int | None = None,
    out: Path | None = None,
    client: BackendClient | None = None,
) -> RunResult:
    """Export snapshot *revision* (default: the latest) to ``exports/r<rev>/`` or *out*."""
    from autoontology.engine.build import WORKSPACE_LEASE, refuse_if_active

    log_path = setup_job_logging(settings)
    head = (
        store.latest_snapshot(with_model=False)
        if revision is None
        else store.get_snapshot(revision, with_model=False)
    )
    if head is None:
        raise JobError(
            "no ontology snapshot yet: run `uv run autoontology build` first"
            if revision is None
            else f"unknown revision {revision} (see `uv run autoontology ontology`)",
            log_path=log_path,
        )
    names = [s["name"] for s in head.sources if s.get("kind") == "structured" and s.get("included")]
    structured = [s for s in (store.get_source(n) for n in names) if s is not None]
    gone = sorted(set(names) - {s.name for s in structured})
    with hold_sources(
        store, [WORKSPACE_LEASE, *(s.name for s in structured)], EXPORT_KIND, log_path=log_path
    ):
        refuse_if_active(store, log_path=log_path)
        snapshot = store.get_snapshot(head.revision)
        assert snapshot is not None
        return _run_export_held(
            settings=settings,
            store=store,
            snapshot=snapshot,
            structured=structured,
            gone=gone,
            out=out,
            client=client,
        )


def _run_export_held(
    *,
    settings: Settings,
    store: Store,
    snapshot: Snapshot,
    structured: list[Source],
    gone: list[str],
    out: Path | None,
    client: BackendClient | None,
) -> RunResult:
    final = export_target(settings, snapshot.revision, out)
    check_replaceable(settings, final)
    client = client or BackendClient(settings.backend_url)
    if structured:
        client.health()  # BackendUnavailable -> "run autoontology up"
    run = store.create_run(
        kind=EXPORT_KIND,
        source_id=None,
        params={
            "revision": snapshot.revision,
            "out": str(out) if out else None,
            "pid": os.getpid(),
        },
    )
    # write next to the final folder, then swap: a failed export never leaves half a folder
    work = final.parent / f".{final.name}.{run.id}.tmp"
    shutil.rmtree(work, ignore_errors=True)
    logger.info("export run %s: revision %d -> %s", run.id, snapshot.revision, final)
    for name in gone:
        logger.warning(
            "  structured source %s was removed since the build: no upstream files", name
        )
    ctx = _ExportCtx(
        settings=settings,
        store=store,
        run=run,
        snapshot=snapshot,
        structured=structured,
        client=client,
        out_dir=work,
    )
    try:
        result = _execute(ctx, list(EXPORT_STEPS), skip={}, already=set(), steps=EXPORT_STEP_FUNCS)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    try:
        check_replaceable(settings, final)  # again: it may have changed meanwhile
        if final.exists():
            _clear_export(settings, final)
    except BaseException as exc:
        shutil.rmtree(work, ignore_errors=True)
        store.update_run(run.id, status="failed", error=str(exc))
        raise
    final.parent.mkdir(parents=True, exist_ok=True)
    work.rename(final)
    result.export_dir = final
    return result
