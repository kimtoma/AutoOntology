# SPDX-License-Identifier: MIT
"""Complete offline backup verification is not restored-home activation."""

import hashlib
import io
import json
import os
import sqlite3
import tarfile

import pytest
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.recovery.checkpoint import verify_bundle
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as
from autoontology.security.owner_enrollment import enroll
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from tests.test_local_checkpoint import checkpoint

COMMIT = "a" * 40


def archive(path, *, member="README.md", link=None, commit=COMMIT):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as out:
        item = tarfile.TarInfo(member)
        if link:
            item.type, item.linkname = tarfile.SYMTYPE, link
            out.addfile(item)
        else:
            raw = b"preserved source\n"
            item.size = len(raw)
            out.addfile(item, io.BytesIO(raw))


def rehash(root):
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["sha256"] = checkpoint.hashes(root / "payload")
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@pytest.fixture
def bundle(isolated_env, tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "private-host/pins")
    root = tmp_path / "complete"
    payload = root / "payload"
    for name in ("workspace", "exports", "postgres/global", "postgres/base", "postgres/pg_wal"):
        (payload / name).mkdir(parents=True, mode=0o700)
    store = Store(payload / "workspace/autoontology.db")
    # These are controlled structural PG fixtures, never a runnable cluster.
    for name, value in {
        "PG_VERSION": "17\n",
        "global/pg_control": "controlled PG header",
        "postgresql.conf": "port = 55433\n",
        "postgresql.auto.conf": "",
        "pg_hba.conf": "local all all trust\n",
        "pg_ident.conf": "",
    }.items():
        (payload / "postgres" / name).write_text(value)
    (payload / "local.env").write_text("PRIVATE_VALUE=synthetic-not-for-output\n")
    (payload / "control.py").write_text("raise RuntimeError('must never execute')\n")
    archive(payload / "source.tar")
    data = {
        "version": 1,
        "commit": COMMIT,
        "label": "controlled-full-backup",
        "databases": ["postgres", "autoontology"],
        "runtime": "/original/runtime",
        "copy_mode": "copy",
        "excluded_runtime_caches": ["workspace/kordoc", "workspace/run/codex-home"],
        "host_database": str(store.path),
        "host_pin_present": False,
        "sha256": checkpoint.hashes(payload),
    }
    (root / "manifest.json").write_text(json.dumps(data))
    for path in [root, *root.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    return root, store


def test_verified_full_bundle_is_offline_repeatable_and_cannot_activate(bundle, monkeypatch):
    root, store = bundle
    before = checkpoint.hashes(root / "payload")
    # Verification must neither execute control/source nor load the original env.
    monkeypatch.setenv("PRIVATE_VALUE", "current-shell")
    report = verify_bundle(root)
    assert report == verify_bundle(root)
    assert report["verified"] and report["integrity_only"]
    assert report["activation_supported"] is False and report["runtime_started"] is False
    assert report["host_authority_checked"] is False
    assert report["identity"]["configured"] is False
    assert report["sqlite_schema_version"] == store.schema_version()
    assert report["postgres_major"] == 17 and report["source_files"] == 1
    assert report["payload_files"] == len(before)
    assert (
        report["manifest_sha256"]
        == hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    )
    assert "synthetic-not-for-output" not in json.dumps(report)
    assert os.environ["PRIVATE_VALUE"] == "current-shell"
    assert checkpoint.hashes(root / "payload") == before
    assert not (store.path.parent / "autoontology.db-wal").exists()
    assert not host.registry_root().exists()


def test_full_verifier_rejects_partial_checkpoint_without_changing_legacy_drills(tmp_path):
    from tests.test_local_checkpoint import fixture

    root = fixture(tmp_path)
    assert checkpoint.verify(root)["label"] == "baseline"
    with pytest.raises(AccessError, match="complete offline checkpoint"):
        verify_bundle(root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("commit", "not-a-commit"),
        ("host_pin_present", 0),
        ("databases", ["postgres", "unrelated_app"]),
        ("databases", ["autoontology", "autoontology"]),
        ("runtime", "relative"),
        ("host_database", "/original/../runtime/db"),
        ("copy_mode", "unsafe"),
    ],
)
def test_manifest_types_scope_and_paths_are_strict(bundle, field, value):
    root, _ = bundle
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(AccessError):
        verify_bundle(root)


@pytest.mark.parametrize(
    "name",
    [
        "source.tar",
        "local.env",
        "control.py",
        "workspace/autoontology.db",
        "postgres/global/pg_control",
    ],
)
def test_missing_full_artifact_rejected_even_with_matching_hashes(bundle, name):
    root, _ = bundle
    (root / "payload" / name).unlink()
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)


@pytest.mark.parametrize(
    "name",
    [
        "workspace/autoontology.db-wal",
        "workspace/autoontology.db-shm",
        "postgres/postmaster.pid",
        "postgres/standby.signal",
        "postgres/recovery.signal",
    ],
)
def test_online_or_recovery_cluster_is_not_a_consolidated_backup(bundle, name):
    root, _ = bundle
    path = root / "payload" / name
    path.write_bytes(b"not offline")
    path.chmod(0o600)
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)


@pytest.mark.parametrize(
    "key",
    ["../escape", "/escape", "workspace//db", "workspace/./db", "workspace/../db", "workspace\\db"],
)
def test_noncanonical_manifest_paths_rejected(bundle, key):
    root, _ = bundle
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["sha256"][key] = "a" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(AccessError):
        verify_bundle(root)


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "world-readable", "fifo"])
def test_unsafe_payload_entries_rejected_without_following_them(bundle, mode, tmp_path):
    root, _ = bundle
    target = root / "payload" / "unsafe"
    if mode == "symlink":
        target.symlink_to(root / "payload/local.env")
    elif mode == "hardlink":
        os.link(root / "payload/local.env", target)
    elif mode == "world-readable":
        target.write_bytes(b"private")
        target.chmod(0o644)
    else:
        os.mkfifo(target, 0o600)
    with pytest.raises(AccessError):
        verify_bundle(root)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    "member,link,commit",
    [
        ("../escape", None, COMMIT),
        ("/escape", None, COMMIT),
        ("safe", "/escape", COMMIT),
        ("safe", None, "b" * 40),
    ],
)
def test_source_archive_is_inspected_without_extracting_or_executing(bundle, member, link, commit):
    root, _ = bundle
    archive(root / "payload/source.tar", member=member, link=link, commit=commit)
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)


def test_pin_pair_verification_does_not_touch_original_host_paths(bundle, monkeypatch):
    root, store = bundle
    enroll(store, "Knowledge OS", "owner", "Owner", "synthetic complete backup password")
    raw = host.read(store.path)
    (root / "payload/host-owner.json").write_text(json.dumps(raw))
    (root / "payload/host-owner.json").chmod(0o600)
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["host_pin_present"] = True
    path.write_text(json.dumps(data))
    rehash(root)
    # The source path may now be gone, corrupt, linked, or on a disconnected disk.
    monkeypatch.setattr(host, "database_path", lambda *a: pytest.fail("consulted live original"))
    monkeypatch.setattr(host, "read", lambda *a: pytest.fail("consulted live host registry"))
    report = verify_bundle(root)
    assert report["identity"]["configured"] and report["identity"]["pin_phase"] == "active"
    assert report["identity"]["workspace_id"] == raw["workspace_id"]
    # Legacy private verify also treats the pin as a frozen literal, without IO.
    assert checkpoint.verify(root)["host_pin_present"]


def test_damaged_identity_audit_is_not_hidden_by_recomputed_file_hashes(bundle):
    root, store = bundle
    enroll(store, "Knowledge OS", "owner", "Owner", "synthetic complete backup password")
    raw = host.read(store.path)
    (root / "payload/host-owner.json").write_text(json.dumps(raw))
    (root / "payload/host-owner.json").chmod(0o600)
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["host_pin_present"] = True
    path.write_text(json.dumps(data))
    with sqlite3.connect(store.path) as conn:
        # Controlled file-owner corruption bypasses the normal append-only API.
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='identity_events'"
        ).fetchall():
            conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        conn.execute(
            "UPDATE identity_events SET detail_json='{}' WHERE action='identity.bootstrap'"
        )
    conn.close()
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)


def test_os_owner_verification_rejects_application_execution_context(bundle):
    root, store = bundle
    enroll(store, "Knowledge OS", "owner", "Owner", "synthetic complete backup password")
    identity = Identity(store)
    actor = identity.authenticate(
        identity.login("owner", "synthetic complete backup password").secret.reveal()
    )
    with acting_as(store, actor), pytest.raises(AccessError) as error:
        verify_bundle(root)
    assert error.value.code == "local_owner_required"


def test_cli_verifies_without_opening_current_workspace_or_exposing_configuration(
    bundle, monkeypatch, tmp_path
):
    root, _ = bundle
    missing = tmp_path / "must-not-create"
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(missing))
    runner = CliRunner()
    denied = runner.invoke(app, ["auth", "backup-verify", str(root)])
    assert denied.exit_code == 1
    assert json.loads(denied.stdout)["error"] == "local_owner_confirmation_required"
    args = ["auth", "backup-verify", str(root), "--confirm-local-owner"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["activation_supported"] is False
    assert not missing.exists()
    assert "synthetic-not-for-output" not in result.output
    result = runner.invoke(app, ["--credential-file", str(tmp_path / "not-read.json"), *args])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "local_owner_required"


def test_offline_pin_header_is_exact_literal_not_case_folded(bundle):
    root, store = bundle
    enroll(store, "Knowledge OS", "owner", "Owner", "synthetic complete backup password")
    raw = host.read(store.path)
    assert host.decode_snapshot(json.dumps(raw), raw["database"]) == raw
    with pytest.raises(AccessError):
        host.decode_snapshot(json.dumps(raw), raw["database"].upper())
    # Frozen parsing does not weaken ordinary live authority decoding.
    with pytest.raises(AccessError):
        host._decode(json.dumps(raw), str(store.path) + ".other")


def test_duplicate_json_keys_and_oversize_manifests_are_rejected(bundle):
    root, _ = bundle
    path = root / "manifest.json"
    raw = path.read_text()
    path.write_text(raw[:-1] + ', "version": 1}')
    with pytest.raises(AccessError):
        verify_bundle(root)
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(AccessError):
        verify_bundle(root)


def test_backup_changed_during_inspection_is_not_reported_as_verified(bundle, monkeypatch):
    from autoontology.recovery import checkpoint as core

    root, _ = bundle
    original = core._source

    def mutate(path, commit):
        result = original(path, commit)
        (path.parent / "local.env").write_bytes(b"changed after hashing")
        return result

    monkeypatch.setattr(core, "_source", mutate)
    with pytest.raises(AccessError):
        verify_bundle(root)


def test_postgres_major_is_explicit_and_no_cluster_is_started(bundle):
    root, _ = bundle
    (root / "payload/postgres/PG_VERSION").write_text("18\n")
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)


def test_directory_only_source_archive_is_not_source_code(bundle):
    root, _ = bundle
    with tarfile.open(
        root / "payload/source.tar", "w", format=tarfile.PAX_FORMAT, pax_headers={"comment": COMMIT}
    ) as out:
        item = tarfile.TarInfo("empty")
        item.type = tarfile.DIRTYPE
        out.addfile(item)
    rehash(root)
    with pytest.raises(AccessError):
        verify_bundle(root)
