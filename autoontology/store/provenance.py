# SPDX-License-Identifier: MIT
"""Content-addressed original bytes and immutable source-input revisions.

Never substitute the file currently found at an old locator for missing history.
The source may have moved, changed or been deleted since an ontology was published.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from autoontology.store.db import Source, Store, now_iso


class ProvenanceError(ValueError):
    def __init__(self, message: str, *, code="provenance_conflict", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class CapturedSource:
    id: str
    files: list[dict]
    paths: dict[Path, Path]


class SourceArchive:
    def __init__(self, store: Store):
        self.store = store
        self.root = store.path.parent / "originals"

    def file_path(self, sha256: str, filename: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or (
            not filename
            or filename in (".", "..")
            or len(filename.encode()) > 255
            or any(c in filename for c in ("/", "\\"))
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        ):
            raise ProvenanceError(
                "invalid original-file identity", code="invalid_original", status=400
            )
        folder = self.root / "blobs" / sha256[:2] / sha256
        for part in (self.root, self.root / "blobs", folder.parent, folder, folder / filename):
            if part.is_symlink():
                raise ProvenanceError(
                    "refusing a symlink in the original archive", code="original_corrupt"
                )
        return folder / filename

    def _capture(self, path: Path, expected: str | None) -> tuple[Path, str, int]:
        sha = expected or file_sha256(path)
        destination = self.file_path(sha, path.name)
        for directory in (
            self.root,
            self.root / "blobs",
            destination.parent.parent,
            destination.parent,
        ):
            created = not directory.exists()
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if created:
                sync_directory(directory)
                sync_directory(directory.parent)
        if destination.exists():
            if file_sha256(destination) != sha:
                raise ProvenanceError(
                    "original archive integrity check failed", code="original_corrupt"
                )
            if file_sha256(path) != sha:
                raise ProvenanceError("source changed during capture; retry the read")
        else:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".capture-", suffix=".partial", dir=self.root
            )
            temporary = Path(temporary)
            try:
                with os.fdopen(descriptor, "wb") as target, path.open("rb") as source:
                    for block in iter(lambda: source.read(1 << 20), b""):
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                if file_sha256(temporary) != sha:
                    raise ProvenanceError("source changed during capture; retry the read")
                temporary.chmod(0o400)
                try:
                    os.link(temporary, destination)  # atomic install; never overwrite history
                    sync_directory(destination.parent)
                except FileExistsError:
                    if file_sha256(destination) != sha:
                        raise ProvenanceError(
                            "original archive integrity check failed", code="original_corrupt"
                        ) from None
            finally:
                temporary.unlink(missing_ok=True)
        size = destination.stat().st_size
        with self.store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO provenance_files VALUES(?,?,?,?)",
                (sha, path.name, size, now_iso()),
            )
        return destination, sha, size

    def capture_files(
        self,
        source: Source,
        files: list[Path],
        *,
        run_id: str,
        root: Path,
        expected: dict[str, str] | None = None,
        purpose: str = "ingestion",
    ) -> CapturedSource:
        entries, paths = [], {}
        root = root.resolve()
        for path in sorted(files, key=lambda p: str(p.resolve())):
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ProvenanceError("source file is outside its input root")
            saved, sha, size = self._capture(
                resolved, expected.get(str(resolved)) if expected else None
            )
            entries.append(
                {
                    "path": resolved.relative_to(root).as_posix(),
                    "filename": resolved.name,
                    "sha256": sha,
                    "bytes": size,
                }
            )
            paths[resolved] = saved
        manifest = {"files": entries}
        if purpose != "ingestion":
            manifest["purpose"] = purpose
        revision = self.record_manifest(source, manifest, run_id=run_id)
        return CapturedSource(revision, entries, paths)

    def record_manifest(self, source: Source, manifest: dict, *, run_id: str) -> str:
        encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        identity = json.dumps(
            [source.id, source.kind, json.loads(encoded)], sort_keys=True, ensure_ascii=False
        )
        revision = hashlib.sha256(identity.encode()).hexdigest()
        timestamp = now_iso()
        with self.store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO source_revisions VALUES(?,?,?,?,?,?)",
                (revision, source.id, source.name, source.kind, encoded, timestamp),
            )
            conn.execute(
                "INSERT INTO source_revision_captures(run_id,source_id,revision_id,captured_at) VALUES(?,?,?,?) "
                "ON CONFLICT(run_id,source_id) DO UPDATE SET revision_id=excluded.revision_id,captured_at=excluded.captured_at,read_at=NULL,summary_json='{}'",
                (run_id, source.id, revision, timestamp),
            )
        return revision

    def mark_read(self, revision: str, run_id: str, *, summary: dict) -> None:
        timestamp = now_iso()
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT source_id FROM source_revision_captures WHERE run_id=? AND revision_id=?",
                (run_id, revision),
            ).fetchone()
            if row is None:
                raise ProvenanceError("source revision capture is missing")
            conn.execute(
                "UPDATE source_revision_captures SET read_at=?,summary_json=? WHERE run_id=? AND source_id=?",
                (timestamp, json.dumps(summary, ensure_ascii=False), run_id, row[0]),
            )
            conn.execute(
                "INSERT INTO source_revision_heads VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET revision_id=excluded.revision_id,read_at=excluded.read_at",
                (row[0], revision, timestamp),
            )

    def reference(self, source_id: str) -> str | None:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT revision_id FROM source_revision_heads WHERE source_id=?", (source_id,)
            ).fetchone()
        return row[0] if row else None

    def capture_catalog(self, source: Source, catalog: dict, *, run_id: str) -> str:
        """Archive the ingested schema, not credentials, row values or transient IDs."""
        schema = {
            name: {
                "columns": dict(item.get("columns", {})),
                "fk_columns": sorted(item.get("fk_columns", [])),
            }
            for name, item in sorted(catalog.items())
        }
        revision = self.record_manifest(source, {"catalog_schema": schema}, run_id=run_id)
        self.mark_read(
            revision, run_id, summary={"tables": len(schema), "kind": "ingested_catalog"}
        )
        return revision

    def revisions(self, source_name: str) -> list[dict]:
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT r.*,count(c.run_id) AS captures,count(c.read_at) AS reads "
                "FROM source_revisions r LEFT JOIN source_revision_captures c ON c.revision_id=r.id "
                "WHERE r.source_name=? GROUP BY r.id ORDER BY r.created_at DESC,r.id DESC",
                (source_name,),
            ).fetchall()
        return [
            {
                **{k: row[k] for k in row.keys() if k != "manifest_json"},
                "manifest": json.loads(row["manifest_json"]),
            }
            for row in rows
        ]

    def original(self, source_name: str, sha256: str, filename: str) -> Path:
        self.file_path(sha256, filename)  # validate the requested name, including aliases
        for row in self._original_records(source_name, sha256, filename):
            path = self.file_path(sha256, row["filename"])
            if not path.is_file():
                continue
            if path.stat().st_size != row["bytes"] or file_sha256(path) != sha256:
                raise ProvenanceError(
                    "original archive integrity check failed", code="original_corrupt"
                )
            return path
        raise ProvenanceError(
            "original file is unavailable; no mutable-locator fallback is permitted",
            code="original_unavailable",
            status=404,
        )

    def _original_records(self, source_name, sha256, filename):
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT f.bytes,f.filename FROM provenance_files f WHERE f.sha256=? AND EXISTS ("
                "SELECT 1 FROM source_revisions r,json_each(r.manifest_json,'$.files') e "
                "WHERE r.source_name=? AND json_extract(e.value,'$.sha256')=f.sha256 "
                "AND json_extract(e.value,'$.filename')=f.filename) ORDER BY (f.filename=?) DESC,f.filename",
                (sha256, source_name, filename),
            ).fetchall()
        return rows

    def status(self, source_name: str, sha256: str, filename: str) -> str:
        """Cheap availability; downloads additionally verify the complete content hash."""
        try:
            self.file_path(sha256, filename)
        except ProvenanceError:
            return "not_archived"
        rows = self._original_records(source_name, sha256, filename)
        if not rows:
            return "not_archived"
        for row in rows:
            try:
                path = self.file_path(sha256, row["filename"])
            except ProvenanceError:
                return "corrupt"
            if path.is_file():
                return "archived" if path.stat().st_size == row["bytes"] else "corrupt"
        return "missing"
