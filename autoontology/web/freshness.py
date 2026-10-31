# SPDX-License-Identifier: MIT
"""Per-source freshness (``GET /api/sources/freshness``): has a source's input changed
since the last build that read it?

The answer mirrors ``build --resume``: the source's :func:`input_fingerprint` now against
the ``inputs_sha`` its steps recorded in the newest build run that ran them. When they
differ, the reasons say what moved: files added / changed / removed (documents sources:
against the stored documents' paths and hashes; files sources: against the load
manifest, with a file modified after the last build counted as changed), the live
schema of a database source, or -- nothing else moved -- the reader settings (kordoc,
OCR, the vision model, table limits). Never raises: an unreadable input or an
unreachable database is state ``unknown`` with the error class.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from autoontology.config import Settings
from autoontology.store.db import Source, Store

#: build runs searched for a source's last recorded fingerprint
RUN_LOOKBACK = 50
EXAMPLES = 3


def _last_built(store: Store, source: Source) -> tuple[str, str] | None:
    """``(inputs_sha, finished_at)`` of the newest succeeded step of *source* that recorded
    its fingerprint, over the recent build runs (None: never built)."""
    from autoontology.engine.build import BUILD_KIND, INPUTS_KEY

    group = "documents" if source.kind == "documents" else "structured"
    prefix = f"{group}:{source.name}:"
    for run in store.list_runs(None, limit=RUN_LOOKBACK):
        if run.kind != BUILD_KIND:
            continue
        steps = [
            s
            for s in store.get_steps(run.id)
            if s.name.startswith(prefix) and s.status == "succeeded" and INPUTS_KEY in s.detail
        ]
        if steps:
            last = max(steps, key=lambda s: s.finished_at or "")
            return str(last.detail[INPUTS_KEY]), last.finished_at or ""
    return None


def _reason(kind: str, items: list[str]) -> dict[str, Any]:
    return {"kind": kind, "count": len(items), "examples": items[:EXAMPLES]}


def _shown(path: Path | str) -> str:
    from autoontology.unstructured.report import display_path

    return display_path(str(path))


def _document_reasons(store: Store, source: Source) -> list[dict[str, Any]]:
    from autoontology.engine import extract as extract_job
    from autoontology.security.host_files import HostFiles
    from autoontology.store.documents import DocumentStore

    guard = HostFiles(store)
    target, recursive = extract_job.documents_target(source)
    guard.check(target)
    files = extract_job.readers.discover_documents(target, recursive=recursive)
    current = {str(p.resolve()): guard.sha256(p, source_id=source.id) for p in files}
    stored = {d.path: d.sha256 for d in DocumentStore(store).list_documents(source.id)}
    shas = set(stored.values())
    added = [p for p, sha in current.items() if p not in stored and sha not in shas]
    changed = [p for p, sha in current.items() if p in stored and stored[p] != sha]
    now = set(current.values())
    removed = [p for p, sha in stored.items() if p not in current and sha not in now]
    out = []
    for kind, paths in (
        ("files_added", added),
        ("files_changed", changed),
        ("files_removed", removed),
    ):
        if paths:
            out.append(_reason(kind, [_shown(p) for p in sorted(paths)]))
    return out


def _files_reasons(store: Store, source: Source, built_at: str) -> list[dict[str, Any]]:
    from autoontology.structured import pipeline

    files = {
        str(p.resolve()): p
        for p in pipeline.files_locator(source).files(store=store, source_id=source.id)
    }
    manifest = store.get_file_manifest(source.id) or {}
    loaded = {
        str(Path(t["source_file"]).resolve())
        for t in manifest.values()
        if isinstance(t, dict) and t.get("source_file")
    }
    # no manifest (a source loaded before manifests existed): only the modification times
    added = sorted(p for p in files if p not in loaded) if loaded else []
    removed = sorted(p for p in loaded if p not in files)
    changed = []
    try:
        since = datetime.fromisoformat(built_at).timestamp() if built_at else None
    except ValueError:
        since = None
    if since is not None:
        changed = sorted(
            p
            for p, path in files.items()
            if (p in loaded or not loaded) and path.stat().st_mtime > since
        )
    out = []
    for kind, paths in (
        ("files_added", added),
        ("files_changed", changed),
        ("files_removed", removed),
    ):
        if paths:
            out.append(_reason(kind, [_shown(p) for p in paths]))
    return out


def source_freshness(store: Store, settings: Settings, source: Source) -> dict[str, Any]:
    """``{name, kind, state: fresh|stale|never_built|unknown, stale, reasons, last_built_at,
    last_inputs_sha, error?}`` of *source*."""
    from autoontology.engine import extract as extract_job
    from autoontology.engine.build import input_fingerprint

    out: dict[str, Any] = {
        "name": source.name,
        "kind": source.kind,
        "state": "fresh",
        "stale": False,
        "reasons": [],
        "last_built_at": None,
        "last_inputs_sha": None,
    }
    try:
        last = _last_built(store, source)
        if last is None:
            out.update(state="never_built", stale=True, reasons=[_reason("never_built", [])])
            return out
        out["last_inputs_sha"], out["last_built_at"] = last
        # a database is read once: its error class, else the schema for the fingerprint
        schema = _schema(source) if source.kind == "database" else None
        current = input_fingerprint(
            source,
            settings.home,
            settings.vision_model,
            reader=extract_job.pipeline_settings(settings),
            schema=schema,
            store=store,
        )
        if not current:  # it swallows errors: read the inputs again to name the error
            _diagnose(source, store)
            raise InputsUnreadable("the inputs could not be read")
        if current == last[0]:
            return out
        if source.kind == "database":
            reasons = [_reason("schema_changed", [])]
        elif source.kind == "documents":
            reasons = _document_reasons(store, source)
        else:
            reasons = _files_reasons(store, source, last[1])
        if not reasons:
            kind = "reader_changed" if source.kind == "documents" else "inputs_changed"
            reasons = [_reason(kind, [])]
        out.update(state="stale", stale=True, reasons=reasons)
    except Exception as exc:  # noqa: BLE001 - never raise: report it
        out.update(state="unknown", stale=None, error=type(exc).__name__, reasons=[])
    return out


class InputsUnreadable(RuntimeError):
    """The fingerprint could not be taken, and reading the inputs did not say why."""


def _schema(source: Source) -> str:
    """The live schema fingerprint of a database source; raises when it cannot be read."""
    from autoontology.structured import pipeline

    conn = pipeline.parse_database_url(source.locator)
    introspect = pipeline.SCHEMA_INTROSPECTORS.get(conn["type"])
    if introspect is None:
        raise InputsUnreadable(f"no schema introspector for {conn['type']!r}")
    return pipeline.schema_rows_fingerprint(introspect(conn))


def _diagnose(source: Source, store: Store) -> None:
    """Read *source*'s inputs the way the fingerprint does, raising what it swallowed."""
    from autoontology.engine import extract as extract_job
    from autoontology.security.host_files import HostFiles
    from autoontology.structured import pipeline

    guard = HostFiles(store)
    if source.kind == "database":
        return  # read (and raised) before the fingerprint
    if source.kind == "documents":
        target, recursive = extract_job.documents_target(source)
        guard.check(target)
        files = extract_job.readers.discover_documents(target, recursive=recursive)
    else:
        files = pipeline.files_locator(source).files(store=store, source_id=source.id)
    for path in files:
        guard.sha256(path, source_id=source.id)


def freshness(store: Store, settings: Settings) -> list[dict[str, Any]]:
    from autoontology.engine.build import registration_order

    return [source_freshness(store, settings, s) for s in registration_order(store)]
