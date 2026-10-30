# SPDX-License-Identifier: MIT
"""A checkpoint cannot restore corrupt payloads or overwrite existing runtime data."""

import importlib.util
import json
import sqlite3
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
        conn.execute("CREATE TABLE knowledge (value TEXT)")
        conn.execute("INSERT INTO knowledge VALUES ('preserved')")
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
