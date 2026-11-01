# SPDX-License-Identifier: MIT
"""Filesystem spellings must not create a second identity or expose authority files."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from autoontology.security import owner_registry as host
from autoontology.security.context import acting_as, authorize
from autoontology.security.host_files import HostFiles
from autoontology.security.internal import identity_enabled
from autoontology.security.owner_enrollment import enroll
from autoontology.security.policy import AccessError
from autoontology.store.db import Store
from autoontology.store.identity import Identity


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "pins")
    home = tmp_path / "Workspace"
    store = Store.for_home(home)
    blank = tmp_path / "blank.db"
    with sqlite3.connect(store.path) as source, sqlite3.connect(blank) as target:
        source.backup(target)
    enroll(store, "Alias fixture", "owner", "Owner", "synthetic alias owner password")
    return home, store, blank


def alias_of(home):
    alias = home.with_name(home.name.upper())
    if not alias.exists() or not alias.samefile(home):
        pytest.skip("requires a case-insensitive filesystem")
    return alias


def test_case_alias_has_same_pin_and_execution_identity(workspace):
    home, store, _ = workspace
    alias = alias_of(home) / "AUTOONTOLOGY.DB"
    assert host._filename(alias) == host._filename(store.path)
    assert host.read(alias) == host.read(store.path)
    service = Identity(store)
    actor = service.authenticate(
        service.login("owner", "synthetic alias owner password").secret.reveal()
    )
    with acting_as(store, actor), Store(alias)._conn() as conn:
        conn.execute("BEGIN")
        assert authorize(Store(alias), "settings.manage", conn=conn) is not None


def test_case_alias_cannot_hide_surviving_pin_after_blank_rollback(workspace):
    home, store, blank = workspace
    alias = alias_of(home)
    with sqlite3.connect(blank) as source, sqlite3.connect(store.path) as target:
        source.backup(target)
    for path in (home, alias):
        with pytest.raises(AccessError) as error:
            identity_enabled(path)
        assert error.value.code == "identity_configuration_missing"


def test_missing_database_case_alias_never_recreates_pinned_database(workspace):
    home, store, _ = workspace
    alias = alias_of(home) / "AUTOONTOLOGY.DB"
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(store.path) + suffix).unlink(missing_ok=True)
    with pytest.raises(AccessError):
        Store(alias).migrate()
    assert not alias.exists() and not store.path.exists()


def legacy_alias_pin(home, store):
    alias = alias_of(home) / "AUTOONTOLOGY.DB"
    path = next(host.registry_root().glob("*.json"))
    data = json.loads(path.read_text())
    data["database"] = str(alias)
    target = path.with_name(hashlib.sha256(str(alias).encode()).hexdigest() + ".json")
    path.unlink()
    target.write_text(json.dumps(data))
    target.chmod(0o600)
    return target, data


def test_preexisting_alias_pin_remains_readable_and_keeps_original_key(workspace):
    home, store, _ = workspace
    target, data = legacy_alias_pin(home, store)
    assert host.read(store.path) == data
    with host.locked(store.path) as fd:
        host.write(fd, store.path, data)
    assert list(host.registry_root().glob("*.json")) == [target]
    assert (target.parent / (target.name + ".lock")).exists()
    assert Identity(store).status()["configured"]


def test_legacy_case_alias_pin_still_denies_blank_restore(workspace):
    home, store, blank = workspace
    legacy_alias_pin(home, store)
    with sqlite3.connect(blank) as source, sqlite3.connect(store.path) as target:
        source.backup(target)
    with pytest.raises(AccessError):
        identity_enabled(home)


def test_two_aliased_pin_entries_are_not_silently_selected(workspace):
    home, store, _ = workspace
    original = host.read(store.path)
    legacy_alias_pin(home, store)
    path = host.registry_root() / host._filename(store.path)
    path.write_text(json.dumps(original))
    path.chmod(0o600)
    with pytest.raises(AccessError):
        host.read(store.path)


def test_hardlinked_database_is_refused_even_at_original_path(workspace, tmp_path):
    _, store, _ = workspace
    os.link(store.path, tmp_path / "other.db")
    with pytest.raises(AccessError):
        identity_enabled(store.path.parent)


@pytest.mark.parametrize("target", ["database", "security"])
def test_authority_files_case_alias_cannot_be_ingestion_source(workspace, target):
    home, store, _ = workspace
    alias = alias_of(home)
    if target == "security":
        directory = home / "security"
        directory.mkdir(exist_ok=True)
        (directory / "internal-state").write_text("synthetic protected content")
        path = alias / "SECURITY" / "INTERNAL-STATE"
    else:
        path = alias / "AUTOONTOLOGY.DB"
    with pytest.raises(AccessError) as error:
        HostFiles(store).check(path)
    assert error.value.code == "host_secret_path"


def test_header_cache_does_not_cache_selected_identity_payload(workspace):
    _, store, _ = workspace
    assert host.read(store.path)["phase"] == "active"
    path = next(host.registry_root().glob("*.json"))
    data = json.loads(path.read_text())
    data.update(phase="pending", event_sha256=None)
    path.write_text(json.dumps(data))
    assert host.read(store.path)["phase"] == "pending"


def test_missing_status_read_does_not_create_registry_or_database(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "pins")
    missing = tmp_path / "not-created" / "autoontology.db"
    assert host.read(missing) is None
    assert not missing.parent.exists() and not host.registry_root().exists()


def test_unicode_aliases_share_host_pin(tmp_path, monkeypatch):
    import unicodedata

    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "pins")
    home = tmp_path / "지식-é"
    store = Store.for_home(home)
    alias = tmp_path / unicodedata.normalize("NFD", home.name)
    if not alias.exists() or not alias.samefile(home):
        pytest.skip("requires a normalization-insensitive filesystem")
    enroll(store, "Unicode fixture", "owner", "Owner", "synthetic unicode owner password")
    assert host._filename(alias / "autoontology.db") == host._filename(store.path)
    assert host.read(alias / "autoontology.db") == host.read(store.path)


def test_adding_an_alias_marker_invalidates_header_index_even_with_same_directory_time(workspace):
    home, store, _ = workspace
    old = host.read(store.path)
    root = host.registry_root()
    stamp = root.stat()
    alias = alias_of(home) / "AUTOONTOLOGY.DB"
    data = {**old, "database": str(alias)}
    path = root / (hashlib.sha256(str(alias).encode()).hexdigest() + ".json")
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    os.utime(root, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(AccessError):
        host.read(store.path)
    path.unlink()
    os.utime(root, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert host.read(store.path) == old


def test_distinct_existing_case_sensitive_files_do_not_share_markers(tmp_path, monkeypatch):
    home = tmp_path / "Workspace"
    home.mkdir()
    other = tmp_path / "WORKSPACE"
    if other.exists():
        pytest.skip("requires a case-sensitive filesystem")
    monkeypatch.setattr(host, "registry_root", lambda: tmp_path / "pins")
    first = Store.for_home(home)
    second = Store.for_home(other)
    enroll(first, "One", "owner", "Owner", "synthetic first owner password")
    assert host.read(second.path) is None
    enroll(second, "Two", "owner", "Owner", "synthetic second owner password")
    assert host.read(first.path)["workspace_id"] != host.read(second.path)["workspace_id"]


def test_host_root_allow_read_and_revoke_use_native_spelling(workspace):
    home, store, _ = workspace
    alias = alias_of(home)
    folder = home / "Business"
    folder.mkdir()
    file = folder / "policy.md"
    file.write_text("Synthetic business policy")
    actor = Identity(store).authenticate(
        Identity(store).login("owner", "synthetic alias owner password").secret.reveal()
    )
    guard = HostFiles(store)
    with acting_as(store, actor):
        allowed = guard.allow(alias / "BUSINESS", reason="Explicit fixture input")
        assert Path(allowed["path"]) == folder
        assert guard.sha256(alias / "BUSINESS" / "POLICY.MD")
        assert not guard.revoke(alias / "BUSINESS", reason="Fixture revocation")["active"]
        with pytest.raises(AccessError) as error:
            guard.check(file)
        assert error.value.code == "host_root_required"


def test_revoke_clears_preexisting_aliased_host_root_rows(workspace):
    home, store, _ = workspace
    alias = alias_of(home)
    folder = home / "Business"
    folder.mkdir()
    actor = Identity(store).authenticate(
        Identity(store).login("owner", "synthetic alias owner password").secret.reveal()
    )
    guard = HostFiles(store)
    with acting_as(store, actor):
        guard.allow(folder, reason="Fixture root")
        with store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM host_file_roots WHERE path=?", (str(folder),)
            ).fetchone()
            conn.execute(
                "INSERT INTO host_file_roots VALUES(?,1,?,?,?)",
                (str(alias / "BUSINESS"), row["reason"], row["actor"], row["updated_at"]),
            )
        guard.revoke(folder, reason="Revoke all equivalent root rows")
        with store._conn() as conn:
            assert (
                conn.execute("SELECT count(*) FROM host_file_roots WHERE active=1").fetchone()[0]
                == 0
            )


def test_replaced_approved_root_never_grants_its_symlink_destination(workspace):
    home, store, _ = workspace
    folder = home / "Business"
    folder.mkdir()
    other = home / "Other"
    other.mkdir()
    file = other / "policy.md"
    file.write_text("Unapproved destination")
    actor = Identity(store).authenticate(
        Identity(store).login("owner", "synthetic alias owner password").secret.reveal()
    )
    guard = HostFiles(store)
    with acting_as(store, actor):
        guard.allow(folder, reason="Only this original path")
        folder.rmdir()
        folder.symlink_to(other, target_is_directory=True)
        with pytest.raises(AccessError) as error:
            guard.check(file)
        assert error.value.code == "host_root_required"


def test_directory_child_creation_does_not_change_native_directory_identity(tmp_path, monkeypatch):
    import stat

    from autoontology.security import fs_paths

    folder = tmp_path / "directory"
    folder.mkdir()
    original = fs_paths.os.fstat
    changed = False

    def create_child_after_stat(fd):
        nonlocal changed
        info = original(fd)
        if stat.S_ISDIR(info.st_mode) and info.st_ino == folder.stat().st_ino and not changed:
            changed = True
            (folder / "concurrent-child").mkdir()
        return info

    monkeypatch.setattr(fs_paths.os, "fstat", create_child_after_stat)
    assert fs_paths.canonical_path(folder) == folder
    assert changed


def test_file_replacement_during_native_resolution_is_refused(tmp_path, monkeypatch):
    from autoontology.security import fs_paths

    file = tmp_path / "original.db"
    file.write_text("old")
    replacement = tmp_path / "replacement.db"
    replacement.write_text("new")
    original = fs_paths.os.fstat
    changed = False

    def replace_after_stat(fd):
        nonlocal changed
        info = original(fd)
        if info.st_ino == file.stat().st_ino and not changed:
            changed = True
            replacement.replace(file)
        return info

    monkeypatch.setattr(fs_paths.os, "fstat", replace_after_stat)
    with pytest.raises(OSError):
        fs_paths.canonical_path(file, single_link=True)
