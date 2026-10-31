# SPDX-License-Identifier: MIT
"""Shared read operations for revision-bound originals in the API and CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from autoontology.ontology.model import MergedOntology, ODocument
from autoontology.store.db import Store
from autoontology.store.operations import OperationsError, read_snapshot
from autoontology.store.provenance import ProvenanceError, SourceArchive


def original_metadata(store: Store, document: ODocument, revision: int) -> dict:
    status = SourceArchive(store).status(document.source, document.sha256, Path(document.path).name)
    return {
        "status": status,
        "recover_url": f"/api/ontology/documents/{quote(document.local, safe='')}/archive?revision={revision}"
        if re.fullmatch(r"[0-9a-f]{64}", document.sha256)
        else None,
        "url": f"/api/ontology/documents/{quote(document.local, safe='')}/original?revision={revision}"
        if status == "archived"
        else None,
    }


def document_rows(store: Store, model: MergedOntology, revision: int) -> list[dict]:
    return [
        {
            "id": doc.local,
            "title": doc.title,
            "source": doc.source,
            "filename": Path(doc.path).name,
            "sha256": doc.sha256,
            "original": original_metadata(store, doc, revision),
        }
        for doc in sorted(model.documents.values(), key=lambda d: (d.source, d.title, d.local))
    ]


def document_original(
    store: Store, model: MergedOntology, document_id: str
) -> tuple[ODocument, Path]:
    from autoontology.security.views import verify_view_authority

    verify_view_authority(store, model, "originals.read")
    document = model.documents.get(document_id)
    if document is None:
        raise ProvenanceError(
            "unknown document in the selected ontology revision",
            code="unknown_document",
            status=404,
        )
    if not re.fullmatch(r"[0-9a-f]{64}", document.sha256):
        raise ProvenanceError(
            "the original was not archived for this legacy document",
            code="original_unavailable",
            status=404,
        )
    path = SourceArchive(store).original(document.source, document.sha256, Path(document.path).name)
    verify_view_authority(store, model, "originals.read")
    return document, path


def selected_model(
    store: Store, revision: int | None, *, preview=False
) -> tuple[int, MergedOntology]:
    snapshot = read_snapshot(store, revision, preview=preview)
    if snapshot is None:
        raise OperationsError("no ontology snapshot selected; build, review and publish first")
    from autoontology.security.views import knowledge_view
    from autoontology.store.operations import visible_revision

    view = knowledge_view(
        store,
        snapshot,
        action="originals.read",
        additional_actions=("history.read",)
        if revision is not None and visible_revision(store, revision)
        else (),
    )
    return snapshot.revision, view.model


def preserve_original(store: Store, model: MergedOntology, document_id: str, revision: int) -> dict:
    document = model.documents.get(document_id)
    if document is None:
        raise ProvenanceError(
            "unknown document in the selected ontology revision",
            code="unknown_document",
            status=404,
        )
    archive = SourceArchive(store)
    if archive.status(document.source, document.sha256, Path(document.path).name) == "archived":
        document_original(store, model, document_id)  # verify rather than trust metadata
    else:
        source = store.get_source(document.source)
        if (
            source is None
            or source.kind != "documents"
            or not re.fullmatch(r"[0-9a-f]{64}", document.sha256)
        ):
            raise ProvenanceError(
                "the original cannot be recovered from this registered source",
                code="original_unavailable",
                status=404,
            )
        locator = json.loads(source.locator)
        target = Path(locator["path"]).resolve()
        if not target.exists():
            raise ProvenanceError(
                "the original is no longer present in its registered source",
                code="original_unavailable",
                status=404,
            )
        from autoontology.store.provenance import file_sha256
        from autoontology.unstructured.readers import discover_documents

        # Search only the registered source. A move/rename is recoverable when
        # its bytes match exactly; a historic absolute locator is not authority.
        candidates = discover_documents(target, recursive=bool(locator.get("recursive")))
        candidates.sort(key=lambda p: (p.name != Path(document.path).name, str(p)))
        path = next(
            (
                p
                for p in candidates
                if p.resolve().is_relative_to(target if target.is_dir() else target.parent)
                and file_sha256(p) == document.sha256
            ),
            None,
        )
        if path is None:
            raise ProvenanceError(
                "the registered file no longer matches this version; matching original bytes are required",
                code="original_changed",
            )
        archive.capture_files(
            source,
            [path],
            run_id="preserve-" + uuid.uuid4().hex,
            root=path.parent,
            expected={str(path.resolve()): document.sha256},
            purpose="original_recovery",
        )
    return {
        "revision": revision,
        "document_id": document_id,
        "sha256": document.sha256,
        "original": original_metadata(store, document, revision),
    }


def change_originals(store: Store, items: list[dict]) -> list[dict]:
    """Decorate presentation data without changing content-bound review signatures."""
    models = {}
    references = {}
    for item in items:
        for evidence in item["evidence"]:
            revision, document = evidence.get("snapshot_revision"), evidence.get("document_id")
            if not revision or not document:
                continue
            if revision not in models:
                snapshot = store.get_snapshot(revision)
                models[revision] = MergedOntology.from_dict(snapshot.model) if snapshot else None
            model = models[revision]
            if model is None or document not in model.documents:
                continue
            key = (revision, document)
            if key not in references:
                references[key] = original_metadata(store, model.documents[document], revision)
            evidence["original"] = references[key]
    return items


def save_original(store: Store, model: MergedOntology, document_id: str, destination: Path) -> dict:
    """Copy verified bytes to a new explicit file; never overwrite the user's output."""
    document, original = document_original(store, model, document_id)
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ProvenanceError("output already exists; select a new file", code="output_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".original-", dir=destination.parent)
    temporary = Path(temporary)
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as target, original.open("rb") as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                digest.update(block)
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        if digest.hexdigest() != document.sha256:
            raise ProvenanceError(
                "original archive integrity check failed", code="original_corrupt"
            )
        from autoontology.security.views import verify_view_authority

        verify_view_authority(store, model, "originals.read")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise ProvenanceError(
                "output already exists; select a new file", code="output_exists"
            ) from None
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "document_id": document_id,
        "sha256": document.sha256,
        "file": str(destination),
        "bytes": destination.stat().st_size,
    }
