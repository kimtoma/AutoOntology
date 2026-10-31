# SPDX-License-Identifier: MIT
"""A checkpoint cannot restore corrupt payloads or overwrite existing runtime data."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "checkpoint", Path(__file__).resolve().parents[1] / "scripts/local_ops_checkpoint.py"
)
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


def fixture(tmp_path):
    root = tmp_path / "checkpoint"
    payload = root / "payload/workspace"
    payload.mkdir(parents=True)
    with sqlite3.connect(payload / "autoontology.db") as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE knowledge (value TEXT)")
        conn.execute("INSERT INTO knowledge VALUES ('preserved')")
    conn.close()
    (root / "payload/local.env").write_text("PRIVATE_SETTING=local\n")
    (root / "manifest.json").write_text(
        json.dumps(
            {"label": "baseline", "commit": "abc", "sha256": checkpoint.hashes(root / "payload")}
        )
    )
    return root


def test_checkpoint_roundtrip_into_new_directory(tmp_path):
    source = fixture(tmp_path)
    restored = tmp_path / "restore"
    result = checkpoint.stage(source, restored)
    # Verification is repeatable and must not create SQLite WAL sidecars.
    assert checkpoint.verify(source) == result
    assert not (source / "payload/workspace/autoontology.db-wal").exists()
    assert result["label"] == "baseline"
    with sqlite3.connect(restored / "workspace/autoontology.db") as conn:
        assert conn.execute("SELECT value FROM knowledge").fetchone()[0] == "preserved"
    assert restored.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError, match="already exists"):
        checkpoint.stage(source, restored)


def test_corrupt_checkpoint_does_not_create_restore_destination(tmp_path):
    source = fixture(tmp_path)
    (source / "payload/local.env").write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        checkpoint.stage(source, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_symlink_checkpoint_is_rejected(tmp_path):
    source = fixture(tmp_path)
    (source / "payload/link").symlink_to(source / "payload/local.env")
    with pytest.raises(ValueError, match="symlinks"):
        checkpoint.verify(source)


@pytest.mark.skipif(sys.platform != "darwin", reason="APFS clone is a macOS operation")
def test_native_clone_preserves_bytes_modes_and_isolates_later_writes(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "clone"
    source.write_bytes(b"immutable backup\n" * 2048)
    source.chmod(0o400)
    checkpoint.copy_file(source, destination, mode="clone")
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o400
    destination.chmod(0o600)
    destination.write_bytes(b"independent restored workspace")
    assert source.read_bytes() == b"immutable backup\n" * 2048


def test_required_clone_never_silently_falls_back_to_full_copy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    source = tmp_path / "source"
    source.write_bytes(b"preserved")
    monkeypatch.setattr(checkpoint.sys, "platform", "darwin")
    monkeypatch.setattr(checkpoint.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.setattr(
        checkpoint.shutil, "copy2", lambda *a, **k: pytest.fail("full copy forbidden")
    )
    with pytest.raises(RuntimeError, match="clone"):
        checkpoint.copy_file(source, tmp_path / "clone", mode="clone")
    assert source.read_bytes() == b"preserved"


def test_auto_mode_retains_copy_fallback_on_unsupported_filesystems(tmp_path, monkeypatch):
    from types import SimpleNamespace

    source, destination = tmp_path / "source", tmp_path / "copy"
    source.write_bytes(b"preserved")
    monkeypatch.setattr(checkpoint.sys, "platform", "darwin")
    monkeypatch.setattr(checkpoint.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    checkpoint.copy_file(source, destination, mode="auto")
    assert destination.read_bytes() == b"preserved"


def test_explicit_copy_does_not_invoke_native_clone(tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "copy"
    source.write_bytes(b"preserved")
    monkeypatch.setattr(
        checkpoint.subprocess, "run", lambda *a, **k: pytest.fail("unexpected clone")
    )
    checkpoint.copy_file(source, destination, mode="copy")
    assert destination.read_bytes() == b"preserved"


def test_clone_refuses_existing_targets_and_source_symlinks(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source")
    destination.write_bytes(b"keep destination")
    with pytest.raises(FileExistsError):
        checkpoint.copy_file(source, destination, mode="clone")
    assert destination.read_bytes() == b"keep destination"
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlinks"):
        checkpoint.copy_file(link, tmp_path / "new", mode="clone")


def test_copy_mode_must_be_explicitly_supported(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"source")
    with pytest.raises(ValueError, match="mode"):
        checkpoint.copy_file(source, tmp_path / "new", mode="invalid")


def test_workspace_snapshot_excludes_only_named_ephemeral_tool_homes(tmp_path):
    home = tmp_path / "workspace"
    assert checkpoint.workspace_ignored(home, home, ["kordoc", "originals", "autoontology.db"]) == {
        "kordoc",
        "autoontology.db",
    }
    assert checkpoint.workspace_ignored(home, home / "run", ["codex-home", "profile.json"]) == {
        "codex-home"
    }
    assert not checkpoint.workspace_ignored(home, home / "originals", ["kordoc", "codex-home"])


def test_private_auth_and_tool_cache_symlinks_are_not_followed_into_snapshot(tmp_path):
    from functools import partial

    home = tmp_path / "workspace"
    (home / "run/codex-home").mkdir(parents=True)
    private = tmp_path / "private-login"
    private.write_text("synthetic-not-for-backup")
    (home / "run/codex-home/auth.json").symlink_to(private)
    (home / "kordoc").symlink_to(tmp_path, target_is_directory=True)
    (home / "run/profile.json").write_text("preserved profile")
    target = tmp_path / "snapshot"
    checkpoint.shutil.copytree(
        home,
        target,
        ignore=partial(checkpoint.workspace_ignored, home),
        copy_function=partial(checkpoint.copy_file, mode="copy"),
    )
    assert not (target / "run/codex-home").exists() and not (target / "kordoc").exists()
    assert (target / "run/profile.json").read_text() == "preserved profile"
    assert private.read_text() == "synthetic-not-for-backup"
