# SPDX-License-Identifier: MIT
"""``autoontology export``: the latest (or a given) ontology snapshot as Turtle, JSON-LD
and GraphML, an Apache Ossie bundle and an OKF bundle, plus ``report.md`` and the
structured sources' upstream exports.

Steps ``collect -> serialize -> upstream -> ossie -> okf -> report``
(:func:`autoontology.engine.jobs._execute`); ``--format rdf|ossie|okf`` skips the steps the
other formats need (``all``, the default, runs every step).
``collect`` loads the snapshot written by ``autoontology build`` -- the export never
rebuilds the model -- and every format writer serialises that same object; ``serialize``
re-parses the Turtle file and fails the run if the export invariants do not hold. An
export covers one project's snapshot (a plain store: the default project). The files go to
``exports/<project slug>/r<revision:04d>/`` (``--out DIR`` writes ``DIR/r<revision:04d>/``):
``ontology.ttl``, ``ontology.jsonld``, ``ontology.graphml``, ``report.md``, the
``export.json`` marker (project, revision, snapshot run and time, upstream fetch time)
and, per structured source, ``upstream/<source>.model.yaml`` and
``upstream/<source>.ossie.zip`` fetched from the backend, ``ossie/`` (semantic model,
ontology, mapping, ``validation.json``: :mod:`autoontology.export.ossie`, built from those
upstream zips) and ``okf/`` (:mod:`autoontology.export.okf`). Exporting a revision again
replaces its folder, but only when every file in it is one its ``export.json`` lists as
written by this exporter -- a folder with any other file, at any depth, is refused, never
deleted. The new folder is built beside the old one and swapped in (the old one is kept
as a backup until the new one is in place, and restored if the swap fails); the run is
``succeeded`` only once the folder is installed. A failed export leaves the old folder as
it was; a failed Ossie/OKF validation
copies its ``validation.json`` to ``logs/<run>.<format>-validation.json``. A ``--format``
export keeps the other formats' files of the previous export of
the same revision.

Exports written before projects (``exports/r<rev>/`` and their ``.prev-*`` backups) are
moved once into the default project's folder (:func:`migrate_legacy_exports`, at service
start and before an export), recorded by :data:`LAYOUT_MARKER`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
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
from autoontology.export import graphml, okf, ossie, rdf
from autoontology.export import report as export_report
from autoontology.ontology import model as merged
from autoontology.store.alignments import AlignmentStore
from autoontology.store.db import ProjectStore, Run, Snapshot, Source, Store, now_iso, scoped
from autoontology.store.documents import DocumentStore
from autoontology.structured.semantic import database_id_for
from autoontology.unstructured.report import display_path

logger = logging.getLogger("autoontology.jobs")

EXPORT_KIND = "export"
EXPORT_STEPS: tuple[str, ...] = ("collect", "serialize", "upstream", "ossie", "okf", "report")
#: ``export --format``: what each format needs besides ``collect`` and ``report``
EXPORT_FORMATS: dict[str, frozenset[str]] = {
    "all": frozenset({"serialize", "upstream", "ossie", "okf"}),
    "rdf": frozenset({"serialize", "upstream"}),
    "ossie": frozenset({"upstream", "ossie"}),
    "okf": frozenset({"okf"}),
}
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
        ossie.OSSIE_DIR,
        okf.OKF_DIR,
    }
)
REVISION_DIR = re.compile(r"r\d{4,}")
#: the concrete formats behind each ``--format`` value
CONCRETE_FORMATS: dict[str, tuple[str, ...]] = {
    "all": ("rdf", "ossie", "okf"),
    "rdf": ("rdf",),
    "ossie": ("ossie",),
    "okf": ("okf",),
}
#: the top-level entries each concrete format writes (``report.md`` and the marker are
#: always rewritten)
FORMAT_ENTRIES: dict[str, frozenset[str]] = {
    "rdf": frozenset({"ontology.ttl", "ontology.jsonld", "ontology.graphml", UPSTREAM_DIR}),
    "ossie": frozenset({UPSTREAM_DIR, ossie.OSSIE_DIR}),
    "okf": frozenset({okf.OKF_DIR}),
}


def revision_dir_name(revision: int) -> str:
    return f"r{int(revision):04d}"


def _slug_of(project: Any) -> str:
    """The slug of *project* (a :class:`ProjectStore`, a ``Project`` row or a slug)."""
    slug = project if isinstance(project, str) else getattr(project, "slug", None)
    if not isinstance(slug, str) or not slug or "/" in slug or slug.startswith("."):
        raise ValueError(f"not a project slug: {slug!r}")
    return slug


def project_exports_dir(settings: Settings, project: Any) -> Path:
    """``exports/<slug>/``: the revision folders of *project* (a :class:`ProjectStore`, a
    ``Project`` row or its slug)."""
    return Path(settings.exports_dir) / _slug_of(project)


def revision_dir(settings: Settings, project: Any, revision: int) -> Path:
    """``exports/<slug>/r<rev>/``."""
    return project_exports_dir(settings, project) / revision_dir_name(revision)


def export_target(settings: Settings, revision: int, out: Path | None, project: Any = None) -> Path:
    """The folder an export of *revision* of *project* goes to: ``exports/<slug>/r<rev>/``
    or ``<out>/r<rev>/``.

    ``--out`` names a parent directory, never the folder that gets replaced, and may not
    be the filesystem root, the home directory or the repository itself.
    """
    if out is None:
        if project is None:
            raise ValueError("an export into the exports root needs its project")
        return revision_dir(settings, project, revision)
    parent = Path(out).expanduser().resolve()
    if parent in {Path(parent.anchor), Path.home().resolve(), REPO_ROOT.resolve()}:
        raise JobError(f"refusing to export into {parent}: pick a dedicated directory")
    if parent.exists() and not parent.is_dir():
        raise JobError(f"--out {parent} is not a directory")
    return parent / revision_dir_name(revision)


def _listed_files(marker: dict[str, Any] | None) -> set[str] | None:
    """The ``files`` an :data:`EXPORT_MARKER` lists (relative POSIX paths), or None when
    the marker has no valid list (missing, from an older exporter, or tampered with)."""
    names = (marker or {}).get("files")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return None
    listed = set()
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or str(path) != name:
            return None
        listed.add(name)
    return listed | {EXPORT_MARKER}


def _unlisted(folder: Path, listed: set[str]) -> list[str]:
    """Every entry under *folder* (recursively, symlinks not followed) that is not a file
    in *listed* nor a directory holding one; directories end with ``/``."""
    parents = {p.as_posix() for name in listed for p in PurePosixPath(name).parents}
    extra = []
    for dirpath, dirnames, filenames in os.walk(folder):
        base = Path(dirpath)
        rel_dir = base.relative_to(folder).as_posix()
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        for name in dirnames:
            rel = prefix + name
            if (base / name).is_symlink() or rel not in parents:
                extra.append(f"{rel}/" if not (base / name).is_symlink() else rel)
        for name in filenames:
            rel = prefix + name
            path = base / name
            if rel not in listed or path.is_symlink() or not path.is_file():
                extra.append(rel)
    return sorted(extra)


def _changed(folder: Path, listed: set[str], hashes: Any) -> list[str]:
    """The *listed* files whose content no longer matches the sha256 the marker recorded
    (or that have no recorded hash): reused or edited by the user, so not the exporter's.
    A marker without ``file_sha256`` is refused earlier (:func:`check_replaceable`)."""
    if not isinstance(hashes, dict):
        return sorted(listed - {EXPORT_MARKER})
    out = []
    for name in sorted(listed - {EXPORT_MARKER}):
        path = folder / name
        if not path.is_file() or path.is_symlink():
            continue  # gone already (or reported as unlisted)
        if hashes.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
            out.append(name)
    return out


def check_replaceable(settings: Settings, final: Path) -> set[str] | None:
    """Refuse to replace *final* unless every file under it, at any depth, is one its
    :data:`EXPORT_MARKER` lists as written by this exporter, with the content it recorded
    (``file_sha256``) (inside ``upstream/``,
    ``ossie/`` and ``okf/`` too, and under the exports root as well as under ``--out``).
    Returns that list (None when *final* does not exist or is an empty folder)."""
    if not final.exists() and not final.is_symlink():
        return None
    if final.is_symlink() or not final.is_dir():
        raise JobError(f"refusing to replace {final}: not an export folder")
    if not any(final.iterdir()):
        return None
    marker = final / EXPORT_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise JobError(
            f"refusing to replace {final}: it has no {EXPORT_MARKER}, so it was not written "
            "by autoontology export"
        )
    meta = read_export_metadata(final)
    listed = _listed_files(meta)
    if listed is None:
        raise JobError(
            f"refusing to replace {final}: its {EXPORT_MARKER} does not list the files it "
            "wrote (an older or edited export); move or delete the folder, then export again"
        )
    hashes = (meta or {}).get("file_sha256")
    if not isinstance(hashes, dict):
        raise JobError(
            f"refusing to replace {final}: its {EXPORT_MARKER} records no file_sha256 (an "
            "export written before content hashes, or an edited marker), so edited files "
            "cannot be told apart from the exporter's; move or delete the folder (keep what "
            "you changed), then export again"
        )
    foreign = _unlisted(final, listed) + _changed(final, listed, hashes)
    if foreign:
        raise JobError(
            f"refusing to replace {final}: it holds files this exporter did not write "
            f"({', '.join(foreign[:5])}{', ...' if len(foreign) > 5 else ''})"
        )
    return listed


def recover_interrupted_install(final: Path) -> Path | None:
    """Undo a crash between the two renames of :func:`_install`: when *final* is missing
    or an empty folder and a ``<final>.prev-*`` backup holding an :data:`EXPORT_MARKER`
    is left beside it, the newest such backup becomes *final* again. Returns the restored
    backup's old path (None when there was nothing to restore)."""
    if final.is_symlink() or (final.exists() and (not final.is_dir() or any(final.iterdir()))):
        return None
    if not final.parent.is_dir():
        return None
    backups = sorted(
        (
            p
            for p in final.parent.glob(f"{final.name}.prev-*")
            if p.is_dir() and not p.is_symlink() and (p / EXPORT_MARKER).is_file()
        ),
        key=lambda p: p.name,
    )
    if not backups:
        return None
    backup = backups[-1]
    if final.exists():
        final.rmdir()
    backup.rename(final)
    logger.warning(
        "  restored the previous export from %s (an earlier export was interrupted "
        "while swapping folders)",
        backup,
    )
    return backup


#: Written into the exports root once the legacy ``r<rev>`` folders were moved into the
#: default project's folder (:func:`migrate_legacy_exports`); its presence skips the scan.
LAYOUT_MARKER = ".layout-projects.json"
#: What the migration moves out of the exports root: revision folders, their ``.prev-*``
#: backups and the work folders of an interrupted export (``.r0003.<run>.tmp``).
LEGACY_ENTRY = re.compile(r"r\d{4,}(\.prev-.+)?|\.r\d{4,}\..+\.tmp")


def _legacy_entries(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if LEGACY_ENTRY.fullmatch(p.name))


def _migrate_legacy_exports_held(settings: Settings, store: Store | ProjectStore) -> list[str]:
    """:func:`migrate_legacy_exports` with the build gate already held."""
    from autoontology.store.db import root_store

    root = Path(settings.exports_dir)
    if not root.is_dir() or root.is_symlink():
        return []
    marker = root / LAYOUT_MARKER
    if marker.is_file():
        return []
    default = root_store(store).default_project()
    target = root / _slug_of(default)
    moved: list[str] = []
    conflicts: list[str] = []
    for entry in _legacy_entries(root):
        dest = target / entry.name
        if dest.exists() or dest.is_symlink():
            conflicts.append(entry.name)  # both exist: never overwrite either
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            entry.rename(dest)  # one atomic rename per entry: a crash leaves each whole
        except OSError as exc:  # this entry stays; the migration runs again next time
            logger.warning("exports: could not move %s into %s: %s", entry, target, exc)
            conflicts.append(entry.name)
            continue
        moved.append(entry.name)
    if moved:
        logger.info(
            "exports: moved %d legacy folder(s) into %s: %s", len(moved), target, ", ".join(moved)
        )
    if conflicts:
        logger.warning(
            "exports: %s left in %s (already in %s, or not movable): move or delete one; "
            "the migration runs again at the next start",
            ", ".join(conflicts),
            root,
            target,
        )
        return moved
    record = {
        "layout": "projects-v1",
        "default_project": default.id,
        "slug": default.slug,
        "moved": moved,
        "migrated_at": now_iso(),
    }
    tmp = root / f".{LAYOUT_MARKER}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(marker)
    return moved


def migrate_legacy_exports(settings: Settings, store: Store | ProjectStore) -> list[str]:
    """Move the exports written before projects (``exports/r<rev>``, their ``.prev-*``
    backups and interrupted work folders) into the default project's folder
    ``exports/<slug>/``, one atomic rename per entry, under the global build gate (an
    export running in another process: skipped, retried at the next call). Idempotent:
    an entry already moved is gone from the root; one present on both sides is left in
    place (logged) and the migration is not recorded, so it runs again. Once nothing is
    left, :data:`LAYOUT_MARKER` records it and later calls return at once. Returns the
    names moved by this call."""
    from autoontology.engine.jobs import hold_engine

    root = Path(settings.exports_dir)
    if not root.is_dir() or root.is_symlink() or (root / LAYOUT_MARKER).is_file():
        return []
    try:
        with hold_engine(store, "export-migration"):
            return _migrate_legacy_exports_held(settings, store)
    except JobError as exc:
        logger.warning("exports migration skipped: %s", exc)
        return []


def rename_project_exports(settings: Settings, old_slug: str, new_slug: str) -> bool:
    """After a project's slug changed: rename ``exports/<old>/`` to ``exports/<new>/``
    (False when there is nothing to move or ``<new>`` already exists; then the folder
    stays where it is)."""
    old = Path(settings.exports_dir) / _slug_of(old_slug)
    new = Path(settings.exports_dir) / _slug_of(new_slug)
    if old == new or not old.is_dir() or old.is_symlink() or new.exists() or new.is_symlink():
        return False
    old.rename(new)
    return True


def _recover_folders(root: Path) -> list[Path]:
    """Restore every interrupted install under *root* (a project folder, or the exports
    root for anything the migration left); returns the restored folders."""
    finals = sorted(
        {
            p.name.split(".prev-", 1)[0]
            for p in root.glob("*.prev-*")
            if REVISION_DIR.fullmatch(p.name.split(".prev-", 1)[0])
        }
    )
    restored: list[Path] = []
    for name in finals:
        try:
            if recover_interrupted_install(root / name) is not None:
                restored.append(root / name)
        except Exception as exc:  # noqa: BLE001 - this folder only; the others go on
            logger.warning("startup: could not restore the export %s: %s", root / name, exc)
    return restored


def recover_interrupted_exports(settings: Settings, store: Store | ProjectStore) -> list[Path]:
    """Service start: move the legacy exports into the default project's folder
    (:func:`migrate_legacy_exports`), then :func:`recover_interrupted_install` for every
    revision folder of every project folder (and of the exports root) that has a
    ``.prev-*`` backup beside it, under the global build gate (an export running in
    another process is left alone). Returns the restored folders; each restore is logged.
    (``--out`` targets are recovered by their next export.)"""
    from autoontology.engine.jobs import hold_engine

    root = Path(settings.exports_dir)
    if not root.is_dir() or root.is_symlink():
        return []
    restored: list[Path] = []
    try:
        with hold_engine(store, "export-recovery"):
            _migrate_legacy_exports_held(settings, store)
            parents = [root] + sorted(
                p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
            )
            for parent in parents:
                restored += _recover_folders(parent)
    except JobError as exc:
        logger.warning("startup export recovery skipped: %s", exc)
    if restored:
        logger.info(
            "startup: restored %d interrupted export(s): %s",
            len(restored),
            ", ".join(str(p) for p in restored),
        )
    return restored


def _remove_listed(folder: Path, listed: set[str]) -> bool:
    """Remove the *listed* files of *folder*, then the directories they leave empty, then
    *folder* itself. Anything else is kept (then *folder* stays and False is returned)."""
    for name in listed:
        path = folder / name
        if path.is_file() and not path.is_symlink():
            path.unlink()
    dirs = {folder / p for name in listed for p in PurePosixPath(name).parents if str(p) != "."}
    for path in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        return False
    return True


@dataclass
class _ExportCtx:
    settings: Settings
    store: ProjectStore
    run: Run
    snapshot: Snapshot
    structured: list[Source]
    client: BackendClient
    out_dir: Path
    model: merged.MergedOntology | None = None
    files: list[Path] = field(default_factory=list)
    upstream: list[dict[str, Any]] = field(default_factory=list)
    #: when the upstream files were fetched (None: no structured source)
    upstream_fetched_at: str | None = None
    #: ``upstream/<source>.ossie.zip`` per structured source (the Ossie step's input)
    ossie_zips: list[tuple[str, Path]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    format: str = "all"
    #: files carried over from the previous export of this revision (formats not chosen)
    kept: list[Path] = field(default_factory=list)
    #: ``export.json`` ``formats`` of the carried-over formats
    kept_formats: dict[str, Any] = field(default_factory=dict)


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
    """The structured sources' upstream exports, fetched *now*: they reflect the backend
    at export time, not at the time the snapshot was built (said in ``report.md``)."""
    folder = ctx.out_dir / UPSTREAM_DIR
    if ctx.structured:
        ctx.upstream_fetched_at = now_iso()
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
            if fmt == "ossie":
                ctx.ossie_zips.append((source.name, target))
            ctx.upstream.append(
                {
                    "name": target.relative_to(ctx.out_dir).as_posix(),
                    "origin": f"upstream {fmt} export of {source.name}",
                    "source": source.name,
                }
            )
            logger.info("  %s: %d bytes", target.relative_to(ctx.out_dir).as_posix(), len(data))
    return {"upstream": ctx.upstream, "upstream_fetched_at": ctx.upstream_fetched_at}


def _step_ossie(ctx: _ExportCtx) -> dict[str, Any]:
    """``ossie/``: the merged semantic model of the upstream zips, the unified ontology and
    the mapping, each validated against the vendored Ossie schemas (any error fails)."""
    assert ctx.model is not None
    try:
        result = ossie.write(
            ctx.model,
            ctx.out_dir,
            revision=ctx.snapshot.revision,
            created_at=ctx.snapshot.created_at,
            upstream=ctx.ossie_zips,
        )
    except ossie.OssieError as exc:
        _keep_validation(ctx, exc, ctx.out_dir / ossie.OSSIE_DIR / ossie.VALIDATION, "ossie")
    ctx.files += result["files"]
    ctx.validation["ossie"] = result["validation"]
    record = result["validation"]
    logger.info(
        "  ossie: semantic_model %s, ontology %s, mapping %s, round-trip %s; %d warning(s)",
        *(_short(record[k]) for k in ("semantic_model", "ontology", "mapping", "roundtrip")),
        len(record["warnings"]),
    )
    for warning in record["warnings"]:
        logger.warning("  ossie: %s", warning)
    return {"validation": record}


def _step_okf(ctx: _ExportCtx) -> dict[str, Any]:
    """``okf/``: an OKF v0.2 bundle, validated with :func:`autoontology.export.okf.validate`."""
    assert ctx.model is not None
    try:
        result = okf.write(
            ctx.model,
            ctx.out_dir,
            revision=ctx.snapshot.revision,
            created_at=ctx.snapshot.created_at,
        )
    except okf.OkfError as exc:
        _keep_validation(ctx, exc, ctx.out_dir / okf.OKF_DIR / okf.VALIDATION, "okf")
    ctx.files += result["files"]
    ctx.validation["okf"] = result["validation"]
    record = result["validation"]
    logger.info(
        "  okf: %d markdown file(s) conformant, %d warning(s)",
        record["files"],
        len(record["warnings"]),
    )
    for warning in record["warnings"][:20]:
        logger.warning("  okf: %s", warning)
    return {"validation": {k: v for k, v in record.items() if k != "warnings"}}


def _keep_validation(ctx: _ExportCtx, exc: Exception, record: Path, fmt: str) -> None:
    """A failed export removes its work folder, so copy the full ``validation.json`` to
    ``logs/<run>.<fmt>-validation.json`` and name it in the error."""
    if not record.is_file():
        raise exc
    ctx.settings.logs_dir.mkdir(parents=True, exist_ok=True)
    kept = ctx.settings.logs_dir / f"{ctx.run.id}.{fmt}-validation.json"
    shutil.copyfile(record, kept)
    # the path first: the run's error keeps only the first 600 characters
    raise type(exc)(f"[full record: {kept}] {exc}") from exc


def _short(value: Any) -> str:
    return value if isinstance(value, str) else f"{len(value)} error(s)"


def export_metadata(ctx: _ExportCtx) -> dict[str, Any]:
    """What :data:`EXPORT_MARKER` records: which snapshot of which project this is, and
    per concrete format (``rdf``, ``ossie``, ``okf``) which export run wrote it and when.
    ``workspace`` (the project's name) is kept for readers of older markers."""
    written = {"export_run": ctx.run.id, "exported_at": ctx.run.started_at}
    formats = {**ctx.kept_formats, **dict.fromkeys(CONCRETE_FORMATS[ctx.format], written)}
    return {
        "project": ctx.store.id,
        "project_slug": ctx.store.slug,
        "workspace": ctx.store.name,
        "revision": ctx.snapshot.revision,
        "snapshot_run": ctx.snapshot.run_id,
        "snapshot_created_at": ctx.snapshot.created_at,
        "export_run": ctx.run.id,
        "format": ctx.format,
        "formats": {k: formats[k] for k in sorted(formats)},
        "upstream_fetched_at": ctx.upstream_fetched_at,
    }


def _previous_formats(marker: dict[str, Any]) -> dict[str, Any]:
    """The ``formats`` record of an older marker (one written before ``formats`` existed
    names only its ``format``)."""
    formats = marker.get("formats")
    if isinstance(formats, dict):
        return {k: v for k, v in formats.items() if k in FORMAT_ENTRIES}
    previous = {"export_run": marker.get("export_run"), "exported_at": None}
    return dict.fromkeys(CONCRETE_FORMATS.get(str(marker.get("format", "all")), ()), previous)


def marker_matches_project(marker: dict[str, Any], project: ProjectStore) -> bool:
    """Whether an :data:`EXPORT_MARKER` was written for *project*: its ``project`` id, or
    -- a marker written before projects, which has none -- *project* is the default one."""
    if "project" in marker:
        return marker.get("project") == project.id
    return project.is_default


def _upstream_source(name: str) -> str:
    """``hanbit.ossie.zip`` -> ``hanbit``."""
    for suffix, _ in UPSTREAM_FILES:
        if name.endswith(f".{suffix}"):
            return name[: -len(suffix) - 1]
    return name


def _carry_over(ctx: _ExportCtx, final: Path) -> None:
    """Copy into the work folder the entries of the formats this export does not write,
    from the previous export of the same revision, so ``--format ossie`` after a full
    export keeps the Turtle files and ``okf/``. Only entries of this exporter
    (:data:`EXPORT_ENTRIES`) are copied, never symlinks, and only when the previous
    marker names this workspace and revision."""
    if ctx.format == "all" or not final.is_dir() or final.is_symlink():
        return
    marker = read_export_metadata(final)
    if (
        marker is None
        or not marker_matches_project(marker, ctx.store)
        or marker.get("revision") != ctx.snapshot.revision
    ):
        return
    written = set().union(*(FORMAT_ENTRIES[f] for f in CONCRETE_FORMATS[ctx.format]))
    previous = _previous_formats(marker)
    kept_formats = {f: v for f, v in previous.items() if f not in CONCRETE_FORMATS[ctx.format]}
    carried = set().union(*(FORMAT_ENTRIES[f] for f in kept_formats), set()) - written
    for name in sorted(carried):
        source = final / name
        if source.is_symlink() or not source.exists():
            continue
        target = ctx.out_dir / name
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
            files = sorted(p for p in target.rglob("*") if p.is_file() and not p.is_symlink())
        else:
            shutil.copy2(source, target)
            files = [target]
        ctx.kept += files
        if name == UPSTREAM_DIR:
            ctx.upstream_fetched_at = marker.get("upstream_fetched_at")
            ctx.upstream += [
                {
                    "name": p.relative_to(ctx.out_dir).as_posix(),
                    "origin": f"kept from export run {marker.get('export_run')}",
                    "source": _upstream_source(p.name),
                }
                for p in files
            ]
        logger.info("  kept %s from the previous export of this revision", name)
    ctx.kept_formats = kept_formats


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
    kept = [_file_entry(p, ctx.out_dir) for p in ctx.kept]
    path = export_report.write(
        ctx.model,
        ctx.out_dir,
        run_id=ctx.run.id,
        files=[*entries, *kept],
        upstream=ctx.upstream,
        revision=ctx.snapshot.revision,
        upstream_fetched_at=ctx.upstream_fetched_at,
    )
    marker = ctx.out_dir / EXPORT_MARKER
    meta = export_metadata(ctx)
    # every file of the folder: a later export removes these and refuses anything else
    meta["files"] = sorted(
        {
            p.relative_to(ctx.out_dir).as_posix()
            for p in ctx.out_dir.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        | {EXPORT_MARKER}
    )
    # ... and what each held: a listed path whose content changed is the user's
    meta["file_sha256"] = {
        name: hashlib.sha256((ctx.out_dir / name).read_bytes()).hexdigest()
        for name in meta["files"]
        if name != EXPORT_MARKER
    }
    marker.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {
        "report": str(path),
        "files": [*entries, _file_entry(path, ctx.out_dir), _file_entry(marker, ctx.out_dir)],
        #: top-level entries carried over from the previous export of this revision
        "kept": sorted({e["name"].split("/", 1)[0] for e in kept}),
    }


EXPORT_STEP_FUNCS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "collect": _step_collect,
    "serialize": _step_serialize,
    "upstream": _step_upstream,
    "ossie": _step_ossie,
    "okf": _step_okf,
    "report": _step_report,
}


def run_export(
    store: Store,
    settings: Settings,
    *,
    revision: int | None = None,
    out: Path | None = None,
    client: BackendClient | None = None,
    format: str = "all",
    project: str | None = None,
) -> RunResult:
    """Export the project's snapshot *revision* (default: its latest) to
    ``exports/<slug>/r<rev>/`` or *out*; *store* is the project's view, or a plain store
    with *project* (an id or slug; None = the default project). *format* (``all`` |
    ``rdf`` | ``ossie`` | ``okf``) picks the formats written. The revision folder is
    replaced, but the files of the formats not chosen are carried over from its previous
    export of the same revision (``export.json`` ``formats`` says which run wrote each
    format)."""
    from autoontology.engine.build import WORKSPACE_LEASE, refuse_if_active
    from autoontology.engine.jobs import hold_engine

    log_path = setup_job_logging(settings)
    store = scoped(store, project)
    if format not in EXPORT_FORMATS:
        raise JobError(
            f"unknown export format {format!r} (choose {'|'.join(EXPORT_FORMATS)})",
            log_path=log_path,
        )
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
    with (
        hold_engine(store, EXPORT_KIND, log_path=log_path),
        hold_sources(
            store, [WORKSPACE_LEASE, *(s.name for s in structured)], EXPORT_KIND, log_path=log_path
        ),
    ):
        refuse_if_active(store, log_path=log_path)
        if out is None:
            _migrate_legacy_exports_held(settings, store)
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
            format=format,
        )


def _run_export_held(
    *,
    settings: Settings,
    store: ProjectStore,
    snapshot: Snapshot,
    structured: list[Source],
    gone: list[str],
    out: Path | None,
    client: BackendClient | None,
    format: str = "all",
) -> RunResult:
    final = export_target(settings, snapshot.revision, out, store)
    recover_interrupted_install(final)
    check_replaceable(settings, final)
    wanted = EXPORT_FORMATS[format]
    skip = {
        name: f"--format {format}"
        for name in EXPORT_STEPS
        if name not in ("collect", "report") and name not in wanted
    }
    client = client or BackendClient(settings.backend_url)
    if structured and "upstream" in wanted:
        client.health()  # BackendUnavailable -> "run autoontology up"
    run = store.create_run(
        kind=EXPORT_KIND,
        source_id=None,
        params={
            "revision": snapshot.revision,
            "out": str(out) if out else None,
            "format": format,
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
        format=format,
    )
    try:
        _carry_over(ctx, final)
        result = _execute(
            ctx,
            list(EXPORT_STEPS),
            skip=skip,
            already=set(),
            steps=EXPORT_STEP_FUNCS,
            finish=False,
        )
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    _install(store, run, work, settings, final)
    store.update_run(run.id, status="succeeded")  # only once the folder is in place
    result.status = "succeeded"
    result.export_dir = final
    return result


def _install(store: Store, run: Run, work: Path, settings: Settings, final: Path) -> None:
    """Swap *work* in as *final*: the previous folder is first renamed to a backup
    (``<final>.prev-<ts>``), restored on any failure, and deleted only once *work* is in
    place. On failure the run is marked failed and :class:`JobError` is raised."""
    backup: Path | None = None
    listed: set[str] | None = None
    try:
        listed = check_replaceable(settings, final)  # again: it may have changed meanwhile
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            stamp = time.strftime("%Y%m%dT%H%M%S")
            backup = final.with_name(f"{final.name}.prev-{stamp}-{run.id}")
            final.rename(backup)
        work.rename(final)
    except BaseException as exc:
        restored = ""
        if backup is not None and backup.exists() and not final.exists():
            try:
                backup.rename(final)
            except OSError as undo:
                restored = f"; the previous export is kept at {backup} ({undo})"
        shutil.rmtree(work, ignore_errors=True)
        message = f"install: {exc}{restored}"
        interrupted = not isinstance(exc, Exception)
        store.update_run(run.id, status="cancelled" if interrupted else "failed", error=message)
        if interrupted or isinstance(exc, JobError):
            raise
        raise JobError(
            f"could not install the export at {final}: {exc}{restored}", run_id=run.id
        ) from exc
    if backup is None:
        return
    # the new folder is in place: the run succeeded whatever the cleanup does
    try:
        removed = _remove_listed(backup, listed or set())
    except OSError as exc:
        logger.warning(
            "  could not remove the previous export: %s is left (%s); delete it by hand",
            backup,
            exc,
        )
        return
    if not removed:
        logger.warning("  could not fully remove the previous export: %s is left", backup)
