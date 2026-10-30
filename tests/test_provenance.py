# SPDX-License-Identifier: MIT
"""Original bytes and source revisions survive overwrites, deletions and retries."""

import hashlib
import json

import pytest

from autoontology.store.db import Store
from autoontology.store.provenance import ProvenanceError, SourceArchive


def source(store, root):
    return store.add_source(
        name="rules", kind="documents", locator=json.dumps({"path": str(root), "recursive": False})
    )


def test_originals_survive_source_overwrite_delete_and_source_removal(tmp_path):
    store = Store.for_home(tmp_path / "home")
    path = tmp_path / "규정.md"
    path.write_text("# 규정\n3억원 이상은 대표이사 승인")
    src = source(store, path)
    archive = SourceArchive(store)
    first = archive.capture_files(src, [path], run_id="read-1", root=tmp_path)
    original = path.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    assert first.files[0]["sha256"] == sha
    assert first.paths[path.resolve()].read_bytes() == original
    archive.mark_read(first.id, "read-1", summary={"documents": 1})
    path.write_text("# 규정\n2억원 이상은 대표이사 승인")
    second = archive.capture_files(src, [path], run_id="read-2", root=tmp_path)
    archive.mark_read(second.id, "read-2", summary={"documents": 1})
    assert first.id != second.id
    path.unlink()
    store.delete_source(src.id)
    assert archive.original("rules", sha, "규정.md").read_bytes() == original
    assert len(archive.revisions("rules")) == 2
    assert archive.reference(src.id) == second.id
    assert archive.original("rules", sha, "규정.md").stat().st_mode & 0o777 == 0o400


def test_repeat_input_is_deduplicated_but_read_attempts_are_distinct(tmp_path):
    store = Store.for_home(tmp_path / "home")
    path = tmp_path / "rules.md"
    path.write_text("Original")
    src = source(store, path)
    archive = SourceArchive(store)
    first = archive.capture_files(src, [path], run_id="first", root=tmp_path)
    again = archive.capture_files(src, [path], run_id="again", root=tmp_path)
    assert first.id == again.id
    assert archive.reference(src.id) is None  # captured is not read/processed
    archive.mark_read(again.id, "again", summary={"documents": 1})
    item = archive.revisions("rules")[0]
    assert item["captures"] == 2 and item["reads"] == 1
    assert len(list((store.path.parent / "originals/blobs").rglob("rules.md"))) == 1


def test_changed_input_hash_aborts_without_claiming_a_source_revision(tmp_path):
    store = Store.for_home(tmp_path / "home")
    path = tmp_path / "rules.md"
    path.write_text("Changed")
    archive = SourceArchive(store)
    with pytest.raises(ProvenanceError, match="changed"):
        archive.capture_files(
            source(store, path),
            [path],
            run_id="race",
            root=tmp_path,
            expected={str(path.resolve()): hashlib.sha256(b"Old").hexdigest()},
        )
    assert archive.revisions("rules") == []
    assert not list((store.path.parent / "originals").rglob("*.partial"))


def test_corrupt_or_missing_archive_never_falls_back_to_mutable_original(tmp_path):
    store = Store.for_home(tmp_path / "home")
    path = tmp_path / "rules.md"
    path.write_text("Original")
    archive = SourceArchive(store)
    captured = archive.capture_files(source(store, path), [path], run_id="one", root=tmp_path)
    sha = captured.files[0]["sha256"]
    saved = captured.paths[path.resolve()]
    saved.chmod(0o600)
    saved.write_text("Tampered")
    with pytest.raises(ProvenanceError, match="integrity"):
        archive.original("rules", sha, path.name)
    with pytest.raises(ProvenanceError, match="integrity"):
        archive.capture_files(store.get_source("rules"), [path], run_id="retry", root=tmp_path)
    saved.unlink()
    with pytest.raises(ProvenanceError, match="unavailable"):
        archive.original("rules", sha, path.name)


def test_archive_rejects_traversal_and_symlink_targets(tmp_path):
    store = Store.for_home(tmp_path / "home")
    archive = SourceArchive(store)
    for filename in ("../secret", "a/b.md", "a\\b.md", "..", "bad\nname.md"):
        with pytest.raises(ProvenanceError):
            archive.file_path("0" * 64, filename)
    with pytest.raises(ProvenanceError):
        archive.file_path("../bad", "file.md")
    archive.root.mkdir(parents=True, exist_ok=True)
    (archive.root / "blobs").symlink_to(tmp_path, target_is_directory=True)
    path = tmp_path / "rules.md"
    path.write_text("Original")
    with pytest.raises(ProvenanceError, match="symlink"):
        archive.capture_files(source(store, path), [path], run_id="one", root=tmp_path)


def test_source_revision_records_deleted_files_and_exposes_no_absolute_locator(tmp_path):
    store = Store.for_home(tmp_path / "home")
    path = tmp_path / "rules.md"
    path.write_text("Original")
    src = source(store, path)
    archive = SourceArchive(store)
    archive.capture_files(src, [path], run_id="one", root=tmp_path)
    removed = archive.capture_files(src, [], run_id="two", root=tmp_path)
    assert removed.files == []
    assert str(tmp_path) not in json.dumps(archive.revisions("rules"))


def test_database_catalog_revision_ignores_transient_term_ids_and_connection_secrets(tmp_path):
    store = Store.for_home(tmp_path)
    src = store.add_source(
        name="billing", kind="database", locator="postgres://user:private-value@host/db"
    )
    archive = SourceArchive(store)
    schema = {
        "public.contracts": {
            "id": "table-1",
            "columns": {"amount": "bigint"},
            "terms": ["term-1"],
            "fk_columns": ["customer_id"],
        }
    }
    first = archive.capture_catalog(src, schema, run_id="one")
    schema["public.contracts"]["terms"] = ["different-term"]
    schema["public.contracts"]["id"] = "different-id"
    assert archive.capture_catalog(src, schema, run_id="two") == first
    serialized = json.dumps(archive.revisions("billing"))
    assert "private-value" not in serialized and "postgres://" not in serialized
    schema["public.contracts"]["columns"]["amount"] = "numeric"
    assert archive.capture_catalog(src, schema, run_id="three") != first


def test_v7_additive_migration_preserves_legacy_sources_and_snapshots(tmp_path, monkeypatch):
    from autoontology.store import db
    from tests.test_operations import example

    path = tmp_path / "autoontology.db"
    with monkeypatch.context() as version:
        version.setattr(db, "MIGRATIONS", [(v, sql) for v, sql in db.MIGRATIONS if v < 7])
        old = Store(path)
        src = source(old, tmp_path)
        run = old.create_run(kind="build", source_id=None)
        revision = old.save_snapshot(run.id, example())
        old.update_run(run.id, status="succeeded")
        snapshot = old.get_snapshot(revision)
        assert old.schema_version() == 6
    current = Store(path)
    assert current.schema_version() == db.MIGRATIONS[-1][0]
    assert current.get_source("rules") == src
    assert current.get_snapshot(revision) == snapshot
    assert SourceArchive(current).revisions("rules") == []
