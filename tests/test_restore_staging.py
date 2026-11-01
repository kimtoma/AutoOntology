# SPDX-License-Identifier: MIT
"""Inactive restored runtimes cannot gain a second workspace authority."""

import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from autoontology.cli import app
from autoontology.recovery import staging
from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as
from autoontology.security.internal import identity_enabled
from autoontology.security.owner_enrollment import enroll
from autoontology.security.owner_recovery import local_status
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity
from tests.test_recovery_checkpoint import bundle, rehash  # noqa: F401

PASSWORD = "synthetic staged restore owner password"


@pytest.fixture
def ready(bundle, monkeypatch):  # noqa: F811 - pytest imports this shared fixture
    root, store = bundle
    payload = root / "payload"
    env = """AUTOONTOLOGY_HOME=/original/workspace
AUTOONTOLOGY_PORT=7830
BACKEND_PORT=3131
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=55433
POSTGRES_USER=macmini
POSTGRES_PASSWORD=synthetic-archived-secret
AUTOONTOLOGY_CHAT_PROVIDER=codex
AUTOONTOLOGY_CODEX_MODEL=gpt-6-astra
AUTOONTOLOGY_CODEX_BIN=/archived/untrusted-codex
AUTOONTOLOGY_EMBED_PROVIDER=ollama
AUTOONTOLOGY_OLLAMA_ENDPOINT=http://127.0.0.1:11434/v1
AUTOONTOLOGY_OLLAMA_EMBED_MODEL=qwen3-embedding:4b
AUTOONTOLOGY_PUBLIC_HOSTS=example.invalid
AUTOONTOLOGY_DEMO_SECRETS=/original/secrets
"""
    (payload / "local.env").write_text(env)
    for name in ["run/worker.pid", "security/cli-session.json", "settings.json"]:
        path = payload / "workspace" / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text("synthetic archived execution state")
        path.chmod(0o600)
    enrolled = enroll(store, "Korean knowledge OS", "owner", "Owner", PASSWORD)
    marker = host.read(store.path)
    (payload / "host-owner.json").write_text(json.dumps(marker))
    (payload / "host-owner.json").chmod(0o600)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(host_pin_present=True, host_database=marker["database"])
    path.write_text(json.dumps(manifest))
    rehash(root)
    monkeypatch.setattr(staging, "current_codex_binary", lambda: str(Path(sys.executable)))
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = dict(zip(("ui", "backend", "postgres"), [s.getsockname()[1] for s in sockets]))
    finally:
        for sock in sockets:
            sock.close()
    return root, store, enrolled, ports


def prepare(ready, destination, **kwargs):
    root, _, enrolled, ports = ready
    return staging.stage_restore(
        root,
        destination,
        workspace_id=enrolled["workspace_id"],
        request_id="stable-local-restore",
        ports=ports,
        copy_mode="copy",
        **kwargs,
    )


def test_stage_retains_backup_and_identity_without_starting_or_reusing_execution_state(
    ready, tmp_path
):
    root, store, enrolled, ports = ready
    before = (root / "manifest.json").read_bytes()
    database_before = store.path.read_bytes()
    source_pin = host.read(store.path)
    destination = tmp_path / "새 복원 폴더"
    result = prepare(ready, destination)
    assert result["phase"] == "staged" and result["integrity_verified"]
    assert result["activation_supported"] is False and result["source_authority_retired"] is False
    assert result["workspace_id"] == enrolled["workspace_id"]
    assert host.read(store.path) == source_pin
    assert (
        root / "manifest.json"
    ).read_bytes() == before and store.path.read_bytes() == database_before
    assert (destination / "workspace/autoontology.db").read_bytes() == database_before
    assert (destination / "recovery/input/local.env").read_bytes() == (
        root / "payload/local.env"
    ).read_bytes()
    assert not list((destination / "workspace/run").iterdir())
    assert not list((destination / "workspace/security").iterdir())
    assert not (destination / "workspace/settings.json").exists()
    env = dotenv_values(destination / "local.env", interpolate=False)
    assert env["AUTOONTOLOGY_HOME"] == str(destination / "workspace")
    assert env["AUTOONTOLOGY_EXPORTS_DIR"] == str(destination / "exports")
    assert env["POSTGRES_PORT"] == str(ports["postgres"])
    assert env["POSTGRES_HOST"] == "127.0.0.1" and not env["POSTGRES_PASSWORD"]
    assert env["AUTOONTOLOGY_CODEX_BIN"] == str(Path(sys.executable))
    assert env["AUTOONTOLOGY_CHAT_PROVIDER"] == "codex"
    assert env["AUTOONTOLOGY_OLLAMA_EMBED_MODEL"] == "qwen3-embedding:4b"
    assert "AUTOONTOLOGY_PUBLIC_HOSTS" not in env and "AUTOONTOLOGY_DEMO_SECRETS" not in env
    fresh = (destination / "postgres/postgresql.conf").read_text()
    assert "127.0.0.1" in fresh and str(ports["postgres"]) in fresh
    assert "include" not in fresh and "data_directory" not in fresh
    assert (destination / "postgres/postgresql.auto.conf").read_text() == ""
    assert not (destination / "postgres/postmaster.opts").exists()
    assert "synthetic-archived-secret" not in json.dumps(result)
    assert prepare(ready, destination, resume=True) == result
    assert staging.restore_status(destination) == result


@pytest.mark.parametrize(
    "boundary",
    ["after_reserve", "after_input", "after_workspace", "before_finalize", "after_finalize"],
)
def test_each_interruption_resumes_same_reserved_destination(
    ready, tmp_path, monkeypatch, boundary
):
    destination = tmp_path / "interrupted"

    def crash(name):
        if name == boundary:
            raise RuntimeError("controlled stage interruption")

    monkeypatch.setattr(staging, "_boundary", crash)
    with pytest.raises(RuntimeError):
        prepare(ready, destination)
    marker = host.read(destination / "workspace/autoontology.db")
    assert marker["version"] == 3 and marker["kind"] == "restore"
    with pytest.raises(AccessError) as error:
        Store.for_home(destination / "workspace")
    assert error.value.code == "restore_activation_required"
    with pytest.raises(AccessError):
        identity_enabled(destination / "workspace")
    if marker["phase"] == "reserved":
        with pytest.raises(AccessError):
            prepare(ready, destination)
    monkeypatch.setattr(staging, "_boundary", lambda _: None)
    result = prepare(ready, destination, resume=True)
    assert result["phase"] == "staged"
    assert (
        host.read(destination / "workspace/autoontology.db")["operation_id"]
        == marker["operation_id"]
    )


def test_staged_pin_denies_native_start_enrollment_and_old_protocol_readers_without_db_sidecars(
    ready, tmp_path, monkeypatch
):
    destination = tmp_path / "inactive"
    prepare(ready, destination)
    before = staging.snapshot_hashes(destination)
    monkeypatch.setenv("AUTOONTOLOGY_HOME", str(destination / "workspace"))
    runner = CliRunner()
    password_file = tmp_path / "owner-password"
    password_file.write_text(PASSWORD)
    password_file.chmod(0o600)
    for args in [
        ["up"],
        [
            "auth",
            "enroll",
            "newowner",
            "--workspace",
            "Other",
            "--name",
            "Other",
            "--confirm-local-owner",
            "--password-file",
            str(password_file),
        ],
        [
            "auth",
            "owner-adopt",
            "owner",
            "--password-file",
            str(password_file),
            "--confirm-local-owner",
        ],
        ["auth", "login", "owner", "--password-file", str(password_file)],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert json.loads(result.stdout)["error"] == "restore_activation_required", result.output
    assert local_status(destination / "workspace/autoontology.db")["restore_phase"] == "staged"
    assert staging.snapshot_hashes(destination) == before
    assert not (destination / "workspace/autoontology.db-wal").exists()
    control = subprocess.run(
        [sys.executable, str(destination / "control.py"), "start"], capture_output=True, text=True
    )
    assert control.returncode != 0 and "restore_activation_required" in control.stdout
    # Actual previous parser is exercised by the native acceptance harness;
    # the stable protocol discriminator must be rejected by version-1/2 readers.
    marker = host.read(destination / "workspace/autoontology.db")
    assert marker["version"] not in {1, 2} and marker["kind"] not in {"enroll", "adopt"}


@pytest.mark.parametrize("change", ["request", "workspace", "ports", "copy_mode"])
def test_resume_cannot_change_request_binding(ready, tmp_path, change):
    root, _, enrolled, ports = ready
    destination = tmp_path / "bound"
    prepare(ready, destination)
    before = staging.snapshot_hashes(destination)
    options = dict(
        workspace_id=enrolled["workspace_id"],
        request_id="stable-local-restore",
        ports=ports,
        copy_mode="copy",
        resume=True,
    )
    if change == "request":
        options["request_id"] = "another-request"
    elif change == "workspace":
        options["workspace_id"] = "0" * 32
    elif change == "ports":
        options["ports"] = {**ports, "ui": ports["ui"] + 1}
    else:
        options["copy_mode"] = "clone"
    with pytest.raises(AccessError):
        staging.stage_restore(root, destination, **options)
    assert staging.snapshot_hashes(destination) == before


def test_existing_unrelated_destination_is_never_modified_or_reserved(ready, tmp_path):
    destination = tmp_path / "customer-files"
    destination.mkdir(mode=0o700)
    kept = destination / "retain.txt"
    kept.write_text("keep")
    with pytest.raises(AccessError):
        prepare(ready, destination)
    assert (
        kept.read_text() == "keep" and host.read(destination / "workspace/autoontology.db") is None
    )


def test_corrupted_prepared_file_is_preserved_and_blocks_resume(ready, tmp_path):
    destination = tmp_path / "corrupted"
    prepare(ready, destination)
    target = destination / "workspace/autoontology.db"
    target.write_bytes(b"controlled damage")
    with pytest.raises(AccessError):
        prepare(ready, destination, resume=True)
    with pytest.raises(AccessError):
        staging.restore_status(destination)
    assert target.read_bytes() == b"controlled damage"


@pytest.mark.parametrize("port", [0, 80, True, "1234", 65536])
def test_invalid_ports_do_not_create_any_destination(ready, tmp_path, port):
    root, _, enrolled, ports = ready
    destination = tmp_path / "invalid-port"
    with pytest.raises(AccessError):
        staging.stage_restore(
            root,
            destination,
            workspace_id=enrolled["workspace_id"],
            request_id="ports",
            ports={**ports, "ui": port},
            copy_mode="copy",
        )
    assert not destination.exists() and host.read(destination / "workspace/autoontology.db") is None


def test_busy_port_is_not_replaced_with_another_or_original_port(ready, tmp_path):
    root, _, enrolled, ports = ready
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        with pytest.raises(AccessError) as error:
            staging.stage_restore(
                root,
                tmp_path / "busy",
                workspace_id=enrolled["workspace_id"],
                request_id="busy",
                ports={**ports, "ui": sock.getsockname()[1]},
                copy_mode="copy",
            )
    assert error.value.code == "restore_port_unavailable"
    assert not (tmp_path / "busy").exists()


def test_unpinned_or_wrong_current_source_cannot_reserve_a_destination(
    ready, tmp_path, monkeypatch
):
    root, store, _, _ = ready
    real = host.read
    monkeypatch.setattr(host, "read", lambda p: None if Path(p) == store.path else real(p))
    with pytest.raises(AccessError):
        prepare(ready, tmp_path / "untrusted")
    assert not (tmp_path / "untrusted").exists()


def test_application_execution_and_credential_flag_cannot_stage(ready, tmp_path):
    root, store, _, ports = ready
    identity = Identity(store)
    actor = identity.authenticate(identity.login("owner", PASSWORD).secret.reveal())
    with acting_as(store, actor), pytest.raises(AccessError) as error:
        prepare(ready, tmp_path / "agent")
    assert error.value.code == "local_owner_required"
    result = CliRunner().invoke(
        app,
        [
            "--credential-file",
            str(tmp_path / "unused"),
            "auth",
            "restore-stage",
            str(root),
            str(tmp_path / "cli"),
            "--workspace-id",
            actor.principal_id,
            "--request-id",
            "test",
            "--ui-port",
            str(ports["ui"]),
            "--backend-port",
            str(ports["backend"]),
            "--postgres-port",
            str(ports["postgres"]),
            "--confirm-local-owner",
        ],
    )
    assert result.exit_code != 0 and json.loads(result.stdout)["error"] == "local_owner_required"
    assert not (tmp_path / "cli").exists()


def test_interrupted_file_copy_resumes_only_its_owned_partial_file(ready, tmp_path, monkeypatch):
    from autoontology.recovery import files

    destination = tmp_path / "partial-copy"
    original = files.shutil.copy2

    def interrupt(source, target):
        assert Path(target).stat().st_mode & 0o777 == 0o600
        Path(target).write_bytes(b"partial bytes")
        raise RuntimeError("controlled process interruption during copy")

    monkeypatch.setattr(files.shutil, "copy2", interrupt)
    with pytest.raises(RuntimeError):
        prepare(ready, destination)
    assert list(destination.rglob(".ao-copy-*"))
    marker = host.read(destination / "workspace/autoontology.db")
    monkeypatch.setattr(files.shutil, "copy2", original)
    result = prepare(ready, destination, resume=True)
    assert result["phase"] == "staged" and result["operation_id"] == marker["operation_id"]
    assert not list(destination.rglob(".ao-copy-*"))


def test_unknown_file_in_interrupted_destination_is_not_blessed_or_overwritten(
    ready, tmp_path, monkeypatch
):
    destination = tmp_path / "unknown-file"
    monkeypatch.setattr(
        staging,
        "_boundary",
        lambda name: (_ for _ in ()).throw(RuntimeError()) if name == "after_input" else None,
    )
    with pytest.raises(RuntimeError):
        prepare(ready, destination)
    unknown = destination / "retain-customer.txt"
    unknown.write_text("retain these bytes")
    unknown.chmod(0o600)
    monkeypatch.setattr(staging, "_boundary", lambda _: None)
    with pytest.raises(AccessError):
        prepare(ready, destination, resume=True)
    assert unknown.read_text() == "retain these bytes"
    assert host.read(destination / "workspace/autoontology.db")["phase"] == "reserved"


def test_empty_database_rollback_cannot_reenroll_or_migrate_reserved_destination(ready, tmp_path):
    import sqlite3

    destination = tmp_path / "blank-rollback"
    prepare(ready, destination)
    path = destination / "workspace/autoontology.db"
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    with pytest.raises(AccessError):
        Store(path)
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE incomplete(value TEXT)")
    before = path.read_bytes()
    rogue = Store.__new__(Store)
    rogue.path = path
    for operation in (
        Store,
        lambda p: rogue._conn().__enter__(),
        lambda p: rogue._immediate().__enter__(),
    ):
        with pytest.raises(AccessError):
            operation(path)
    assert path.read_bytes() == before and hashlib.sha256(before).hexdigest() != original_sha
    assert not (path.parent / "autoontology.db-wal").exists()


@pytest.mark.parametrize("name", ["workspace", "exports", "postgres", "app"])
def test_destination_cannot_be_inside_original_working_data(ready, name):
    root, store, _, _ = ready
    parent = store.path.parent if name == "workspace" else store.path.parent.parent / name
    parent.mkdir(mode=0o700, exist_ok=True)
    with pytest.raises(AccessError):
        prepare(ready, parent / "must-not-exist")
    assert not (parent / "must-not-exist").exists()


def test_archived_pg_commands_and_settings_never_enter_working_profile(ready, tmp_path):
    root, *_ = ready
    for filename in ("postgresql.conf", "postgresql.auto.conf", "postmaster.opts"):
        path = root / "payload/postgres" / filename
        path.write_text(
            "include='/original/unsafe'\narchive_command='do-not-execute'\ndata_directory='/original/pg'\n"
        )
        path.chmod(0o600)
    rehash(root)
    destination = tmp_path / "fresh-config"
    prepare(ready, destination)
    for path in (destination / "postgres").glob("*.conf"):
        text = path.read_text()
        assert "do-not-execute" not in text and "/original" not in text
    assert (
        "do-not-execute"
        in (destination / "recovery/input/postgres/postgresql.auto.conf").read_text()
    )


def test_staged_empty_directory_removal_is_detected_by_receipt(ready, tmp_path):
    destination = tmp_path / "directory-check"
    prepare(ready, destination)
    (destination / "workspace/security").rmdir()
    with pytest.raises(AccessError):
        staging.restore_status(destination)


def test_source_pin_change_is_visible_but_cannot_silently_change_resume_binding(ready, tmp_path):
    _, store, _, _ = ready
    destination = tmp_path / "source-changed"
    prepare(ready, destination)
    with host.locked(store.path) as directory:
        marker = host.read(store.path)
        host.write(directory, store.path, {**marker, "controlled_note": "new source ceremony"})
    report = staging.restore_status(destination)
    assert report["integrity_verified"] and report["source_pin_current"] is False
    with pytest.raises(AccessError):
        prepare(ready, destination, resume=True)


def test_two_inactive_destinations_never_change_source_authority(ready, tmp_path):
    _, store, enrolled, _ = ready
    source = host.read(store.path)
    first = prepare(ready, tmp_path / "first")
    root, _, enrolled, ports = ready
    second = staging.stage_restore(
        root,
        tmp_path / "second",
        workspace_id=enrolled["workspace_id"],
        request_id="another-inactive-destination",
        ports=ports,
        copy_mode="copy",
    )
    assert first["phase"] == second["phase"] == "staged"
    assert host.read(store.path) == source
    identity = Identity(store)
    assert identity.status()["workspace_id"] == enrolled["workspace_id"]
    assert identity.authenticate(identity.login("owner", PASSWORD).secret.reveal())
    for name in ("first", "second"):
        with pytest.raises(AccessError):
            Store.for_home(tmp_path / name / "workspace")


def test_same_workspace_request_cannot_be_rebound_to_another_destination(ready, tmp_path):
    first = tmp_path / "first-binding"
    prepare(ready, first)
    marker = host.read(first / "workspace/autoontology.db")
    second = tmp_path / "rebound"
    with pytest.raises(AccessError):
        prepare(ready, second)
    assert not second.exists()
    assert host.read(second / "workspace/autoontology.db") is None
    assert host.read(first / "workspace/autoontology.db") == marker


def test_request_binding_survives_loss_of_all_destination_files(ready, tmp_path):
    import shutil

    first = tmp_path / "lost-binding"
    prepare(ready, first)
    shutil.rmtree(first)  # Controlled loss; independent host reservation survives.
    with pytest.raises(AccessError):
        prepare(ready, tmp_path / "different-path")
    with pytest.raises(AccessError):
        Store.for_home(first / "workspace")
    assert not first.exists()
