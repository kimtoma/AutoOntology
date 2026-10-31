# SPDX-License-Identifier: MIT
"""Immutable publication receipts shared by CLI, HTTP and operational readers."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

from autoontology.ontology.model import MergedOntology
from autoontology.playground.elements import elements_from_model
from autoontology.playground.store import PlaygroundStore
from autoontology.store.db import Store, now_iso
from autoontology.store.operations import OperationsError, digest
from autoontology.store.processing import Processing, ProcessingError
from autoontology.store.provenance import ProvenanceError, SourceArchive


class PublicationError(OperationsError):
    """A publication's proof is unavailable or no longer matches its content."""


def export_metadata(marker: dict) -> dict:
    """Bind non-circular export metadata separately from artifact/receipt hashes."""
    return {k: v for k, v in marker.items() if k not in ("files", "file_sha256", "publication_id")}


def index_receipt(store: Store, scope: str, model: MergedOntology) -> dict:
    """Validate complete, finite vectors and hash their canonical float32 bytes."""
    try:
        pg = PlaygroundStore(store)
        rows = pg.index_rows(scope)
        meta = pg.index_meta(scope)
    except (ValueError, OverflowError) as exc:
        raise PublicationError("publication index is corrupt") from exc
    elements = elements_from_model(model)
    if (
        not elements
        or not meta
        or meta["elements"] != len(elements)
        or set(rows) != {e.id for e in elements}
    ):
        raise PublicationError("publication index coverage is incomplete")
    hashes, dimensions, models = [], set(), set()
    for element in sorted(elements, key=lambda e: e.id):
        row = rows[element.id]
        vector = row.embedding
        if (
            row.text_sha != element.text_sha
            or row.element_type != element.type
            or not vector
            or not all(math.isfinite(v) for v in vector)
            or not any(vector)
        ):
            raise PublicationError("publication index differs from approved knowledge")
        dimensions.add(len(vector))
        models.add(row.model)
        if row.model != meta["model"]:
            raise PublicationError("publication index metadata differs from vectors")
        hashes.append(
            [
                element.id,
                row.element_type,
                row.text_sha,
                row.model,
                hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest(),
            ]
        )
    if len(dimensions) != 1 or len(models) != 1:
        raise PublicationError("publication index contains mixed vector profiles")
    return {
        "scope": scope,
        "sha256": digest(hashes),
        "elements": len(elements),
        "dimensions": dimensions.pop(),
        "model": models.pop(),
    }


def artifact_hashes(folder: Path) -> dict[str, str]:
    if folder.is_symlink():
        raise PublicationError("publication artifact directory cannot be a symbolic link")
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise PublicationError("publication artifact cannot be a symbolic link")
        relative = path.relative_to(folder).as_posix()
        if path.is_file() and relative not in ("export.json", "publication.json"):
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise PublicationError("publication artifacts are empty")
    return result


class Publications:
    def __init__(self, store: Store):
        self.store = store

    def get(self, revision: int) -> dict | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM publications WHERE revision=?", (revision,)
            ).fetchone()
        if row is None:
            return None
        manifest = json.loads(row["manifest_json"])
        if digest(manifest) != row["manifest_sha256"] or row["id"] != "pub-" + digest(manifest):
            raise PublicationError("publication manifest integrity check failed")
        return {
            "id": row["id"],
            "revision": revision,
            "status": row["status"],
            "created_at": row["created_at"],
            "committed_at": row["committed_at"],
            "manifest": manifest,
        }

    def listing(self) -> list[dict]:
        with self.store._conn() as conn:
            revisions = [
                r[0]
                for r in conn.execute(
                    "SELECT revision FROM publications WHERE status='committed' ORDER BY revision DESC"
                )
            ]
        return [self.get(r) for r in revisions]

    def model_digest(self, revision: int) -> str:
        snapshot = self.store.get_snapshot(revision)
        if snapshot is None or snapshot.model is None:
            raise PublicationError("publication model is missing")
        return digest(snapshot.model)

    def originals(self, model: MergedOntology) -> list[dict]:
        archive = SourceArchive(self.store)
        result = []
        for document in sorted(model.documents.values(), key=lambda d: d.local):
            try:
                path = archive.original(document.source, document.sha256, Path(document.path).name)
            except ProvenanceError as exc:
                raise PublicationError(
                    "publication original is unavailable or corrupt; preserve it before publication"
                ) from exc
            revisions = [
                r["id"]
                for r in archive.revisions(document.source)
                if any(f.get("sha256") == document.sha256 for f in r["manifest"].get("files", []))
            ]
            result.append(
                {
                    "document_id": document.local,
                    "source": document.source,
                    "sha256": document.sha256,
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "source_revisions": sorted(revisions),
                }
            )
        return result

    def bindings(self, revisions):
        bound = {}
        for revision in revisions:
            if revision is None:
                continue
            publication = self.check_model(revision)
            if publication:
                entries = publication["manifest"]["source_bindings"]
            else:
                entries = [
                    {
                        "revision": revision,
                        "sources": [
                            {
                                k: source[k]
                                for k in (
                                    "source_id",
                                    "source_revision",
                                    "name",
                                    "kind",
                                    "included",
                                    "processing",
                                    "sql_binding",
                                )
                                if k in source
                            }
                            for source in self.store.get_snapshot(revision).sources
                        ],
                    }
                ]
            for entry in entries:
                bound[digest(entry)] = entry
        return list(bound.values())

    def prepare(
        self,
        revision: int,
        *,
        candidate: int,
        previous: int | None,
        validation: dict,
        items: list[dict],
        profile: dict,
        scope: str,
        folder: Path,
        restoration: dict | None = None,
        source_bindings: list[dict] | None = None,
    ) -> dict:
        snapshot = self.store.get_snapshot(revision)
        model = MergedOntology.from_dict(snapshot.model)
        # The endpoint may contain credentials. Bind it by hash; never persist it.
        safe_profile = {k: v for k, v in profile.items() if k != "endpoint"}
        safe_profile["endpoint_sha256"] = digest(profile.get("endpoint"))
        manifest = {
            "version": 2,
            "revision": revision,
            "run_id": snapshot.run_id,
            "candidate_revision": candidate,
            "previous_revision": previous,
            "previous_publication_id": (self.get(previous) or {}).get("id")
            if previous is not None
            else None,
            "model_sha256": self.model_digest(revision),
            "validation": validation,
            "profile": safe_profile,
            "decisions": [{"id": i["id"], "decision": i.get("decision")} for i in items],
            "source_bindings": source_bindings
            if source_bindings is not None
            else self.bindings((previous, candidate)),
            "originals": self.originals(model),
            "index": index_receipt(self.store, scope, model),
            "artifacts": artifact_hashes(folder),
            "export_metadata_sha256": digest(
                export_metadata(json.loads((folder / "export.json").read_text()))
            ),
            "structured_rows": "live_at_execution; historical rows are not archived",
        }
        source_ids = {
            s.get("source_revision") for b in manifest["source_bindings"] for s in b["sources"]
        }
        source_ids.update(r for o in manifest["originals"] for r in o["source_revisions"])
        manifest["source_inputs"] = self.source_inputs(source_ids - {None})
        try:
            manifest["processing_receipts"] = Processing(self.store).expand(
                manifest["source_bindings"]
            )
        except ProcessingError as exc:
            raise PublicationError(str(exc)) from exc
        # Matching bytes in today's mutable document row do not prove who read
        # the historical input. Only snapshot-bound, immutable read records do.
        manifest["readers"] = []
        for document in model.documents.values():
            captured = [
                r
                for r in manifest["processing_receipts"]
                if r["record"]["stage"] == "read"
                and r["record"]["source_name"] == document.source
                and r["record"]["input"].get("sha256") == document.sha256
            ]
            manifest["readers"].append(
                {
                    "document_id": document.local,
                    "fingerprints": sorted(
                        {
                            r["record"]["configuration"]["fingerprint"]
                            for r in captured
                            if r["record"]["configuration"].get("fingerprint")
                        }
                    ),
                    "processing_receipts": sorted(r["id"] for r in captured),
                    "status": "recorded" if captured else "unknown",
                }
            )
        manifest["extraction_profile"] = (
            "Processing receipts contain captured configurations and outputs where recorded. Missing historical processing bindings remain unknown; current settings are not historical proof."
        )
        if restoration:
            manifest["restoration"] = restoration
        publication_id = "pub-" + digest(manifest)
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO publications VALUES(?,?,?,?,?,?,NULL)",
                (
                    publication_id,
                    revision,
                    json.dumps(manifest, ensure_ascii=False),
                    digest(manifest),
                    "prepared",
                    now_iso(),
                ),
            )
        envelope = {"id": publication_id, "manifest": manifest}
        target = folder / "publication.json"
        target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2))
        marker_path = folder / "export.json"
        marker = json.loads(marker_path.read_text())
        marker["publication_id"] = publication_id
        marker["files"] = sorted(set([*marker["files"], "publication.json"]))
        marker["file_sha256"]["publication.json"] = hashlib.sha256(target.read_bytes()).hexdigest()
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2))
        return self.get(revision)

    def source_inputs(self, revisions) -> list[dict]:
        result = []
        with self.store._conn() as conn:
            for revision in sorted(revisions):
                row = conn.execute(
                    "SELECT * FROM source_revisions WHERE id=?", (revision,)
                ).fetchone()
                if not row:
                    raise PublicationError("publication source input revision is missing")
                manifest = json.loads(row["manifest_json"])
                if digest([row["source_id"], row["kind"], manifest]) != revision:
                    raise PublicationError("publication source input integrity check failed")
                result.append(
                    {
                        "id": revision,
                        "source_id": row["source_id"],
                        "source_name": row["source_name"],
                        "kind": row["kind"],
                        "manifest_sha256": digest(manifest),
                    }
                )
        return result

    def check_model(self, revision: int) -> dict | None:
        publication = self.get(revision)
        if publication:
            if publication["status"] != "committed":
                raise PublicationError("publication is not committed")
            if self.model_digest(revision) != publication["manifest"]["model_sha256"]:
                raise PublicationError("publication model integrity check failed")
        return publication

    def verify(self, revision: int, exports: Path, *, prepared: bool = False) -> dict:
        publication = self.get(revision) if prepared else self.check_model(revision)
        if publication is None:
            return {
                "ok": False,
                "revision": revision,
                "legacy": True,
                "reason": "No manifest was recorded for this legacy revision.",
            }
        manifest = publication["manifest"]
        if manifest.get("previous_publication_id"):
            parent = self.get(manifest["previous_revision"])
            if not parent or parent["id"] != manifest["previous_publication_id"]:
                raise PublicationError("publication parent receipt is missing or changed")
        if self.model_digest(revision) != manifest["model_sha256"]:
            raise PublicationError("publication model integrity check failed")
        snapshot = self.store.get_snapshot(revision)
        model = MergedOntology.from_dict(snapshot.model)
        if index_receipt(self.store, manifest["index"]["scope"], model) != manifest["index"]:
            raise PublicationError("publication index integrity check failed")
        self.originals(model)
        if "processing_receipts" in manifest:
            try:
                if (
                    Processing(self.store).expand(manifest["source_bindings"])
                    != manifest["processing_receipts"]
                ):
                    raise ProcessingError("publication processing receipts changed")
            except ProcessingError as exc:
                raise PublicationError(str(exc)) from exc
        if (
            self.source_inputs(r["id"] for r in manifest["source_inputs"])
            != manifest["source_inputs"]
        ):
            raise PublicationError("publication source inputs changed")
        folder = exports / f"r{revision:04}"
        if artifact_hashes(folder) != manifest["artifacts"]:
            raise PublicationError("publication artifact integrity check failed")
        try:
            envelope = json.loads((folder / "publication.json").read_text())
            marker = json.loads((folder / "export.json").read_text())
        except (ValueError, OSError) as exc:
            raise PublicationError("publication artifact receipt is missing or corrupt") from exc
        expected_files = set(manifest["artifacts"]) | {"publication.json", "export.json"}
        expected_hashes = {
            **manifest["artifacts"],
            "publication.json": hashlib.sha256(
                (folder / "publication.json").read_bytes()
            ).hexdigest(),
        }
        if (
            envelope != {"id": publication["id"], "manifest": manifest}
            or marker.get("publication_id") != publication["id"]
            or set(marker.get("files", [])) != expected_files
            or marker.get("file_sha256") != expected_hashes
            or (
                "export_metadata_sha256" in manifest
                and digest(export_metadata(marker)) != manifest["export_metadata_sha256"]
            )
        ):
            raise PublicationError("publication artifact receipt differs from manifest")
        return {
            "ok": True,
            "id": publication["id"],
            "revision": revision,
            "originals": len(manifest["originals"]),
            "artifacts": len(manifest["artifacts"]),
        }
