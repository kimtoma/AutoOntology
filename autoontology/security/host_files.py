# SPDX-License-Identifier: MIT
"""Explicit host roots for authenticated workspaces and no-follow live input reads."""

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from autoontology.config import REPO_ROOT
from autoontology.security.context import audit_effect, authorize
from autoontology.security.policy import AccessError
from autoontology.store.db import now_iso

SECRET_NAMES = {
    "auth.json",
    "credentials.json",
    "token.json",
    "tokens.json",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "authorized_keys",
    "known_hosts",
}
SECRET_DIRS = {".git", ".ssh", ".aws", ".codex", ".claude", ".gnupg", ".kube"}


def fail(message, code="host_path_denied", status=403):
    raise AccessError(message, code, status)


def safe_name(path):
    path = Path(path)
    if any(part.lower() in SECRET_DIRS for part in path.parts):
        return False
    name = path.name.lower()
    return not (
        name in SECRET_NAMES
        or name.startswith(".env")
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db"))
    )


class HostFiles:
    def __init__(self, store=None):
        self.store = store

    def defaults(self):
        roots = [
            Path(os.environ.get("AUTOONTOLOGY_SAMPLES_DIR") or REPO_ROOT / "samples")
            .expanduser()
            .resolve()
        ]
        if self.store:
            roots.append((self.store.path.parent / "uploads").resolve())
        return roots

    @contextmanager
    def _read(self):
        if self.store is None:
            yield None
            return
        with self.store._conn() as conn:
            conn.execute("BEGIN")
            yield conn

    @staticmethod
    def modern(conn):
        return bool(
            conn
            and conn.execute("SELECT 1 FROM sqlite_master WHERE name='identity_state'").fetchone()
            and conn.execute("SELECT 1 FROM identity_state LIMIT 1").fetchone()
        )

    def check(self, path, *, conn=None):
        original = Path(path).expanduser().absolute()
        # macOS system aliases are normalized before checking user-controlled links.
        for alias in ("/tmp", "/var"):
            if str(original) == alias or str(original).startswith(alias + "/"):
                original = Path("/private" + str(original))
        if any(part.is_symlink() for part in [original, *original.parents]):
            fail("Live input paths cannot contain symbolic links.", "host_unsafe_file")
        if not safe_name(original):
            fail(
                "Authentication, secret and repository-internal paths cannot be ingested.",
                "host_secret_path",
            )
        try:
            resolved = original.resolve()
        except (OSError, RuntimeError):
            fail("The input path cannot be safely resolved.")
        if not safe_name(resolved):
            fail(
                "The resolved path is a protected secret or repository-internal path.",
                "host_secret_path",
            )
        if self.store and (
            resolved == self.store.path.resolve()
            or resolved.is_relative_to((self.store.path.parent / "security").resolve())
        ):
            fail(
                "Runtime identity and workspace database files are not ingestion sources.",
                "host_secret_path",
            )
        if conn is None:
            with self._read() as read:
                return self._checked(resolved, read)
        return self._checked(resolved, conn)

    def _checked(self, path, conn):
        if self.modern(conn):
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='host_file_roots'"
            ).fetchone():
                fail(
                    "Host root policy is missing. Use local-owner recovery.",
                    "host_policy_missing",
                    503,
                )
            roots = [
                *self.defaults(),
                *[
                    Path(row[0])
                    for row in conn.execute("SELECT path FROM host_file_roots WHERE active=1")
                ],
            ]
            if not any(
                path == root or (root.is_dir() or not root.exists()) and path.is_relative_to(root)
                for root in roots
            ):
                fail(
                    "This live input is outside approved host roots. A workspace administrator must approve its folder or file first.",
                    "host_root_required",
                )
        return path

    def listing(self):
        with self._read() as conn:
            decision = authorize(self.store, "identity.admin", conn=conn)
            return {
                "enforced": self.modern(conn),
                "defaults": [str(path) for path in self.defaults()],
                "roots": [
                    dict(row) for row in conn.execute("SELECT * FROM host_file_roots ORDER BY path")
                ],
                "secret_paths_always_excluded": True,
                "actor_verified": decision is not None,
            }

    def allow(self, path, *, reason):
        reason = reason.strip() if isinstance(reason, str) else ""
        if not 1 <= len(reason) <= 2000:
            fail("Provide a bounded host-root reason.", "host_root_input", 400)
        target = Path(path).expanduser().resolve()
        if (
            not target.exists()
            or not (target.is_dir() or target.is_file())
            or target
            in {
                Path("/"),
                Path.home().resolve(),
                Path("/Users"),
                Path("/private"),
                Path("/tmp").resolve(),
            }
            or not safe_name(target)
        ):
            fail(
                "Approve a specific existing business folder or file, not a broad system/home root.",
                "host_root_input",
                400,
            )
        if self.store and (
            target == self.store.path.resolve()
            or target.is_relative_to((self.store.path.parent / "security").resolve())
        ):
            fail("Runtime authority files cannot be approved as inputs.", "host_root_input", 400)
        with self.store._immediate() as conn:
            decision = authorize(self.store, "identity.admin", conn=conn)
            if decision is None:
                fail(
                    "Enroll an individual workspace owner before configuring trusted host roots.",
                    "host_owner_required",
                )
            conn.execute(
                "INSERT INTO host_file_roots VALUES(?,1,?,?,?) ON CONFLICT(path) DO UPDATE SET active=1,reason=excluded.reason,actor=excluded.actor,updated_at=excluded.updated_at",
                (str(target), reason, decision.actor.principal_id, now_iso()),
            )
            audit_effect(
                self.store,
                decision,
                "host-file-roots",
                {"operation": "root_allowed", "path": str(target)},
                conn=conn,
            )
            return dict(
                conn.execute(
                    "SELECT * FROM host_file_roots WHERE path=?", (str(target),)
                ).fetchone()
            )

    def revoke(self, path, *, reason):
        reason = reason.strip() if isinstance(reason, str) else ""
        if not 1 <= len(reason) <= 2000:
            fail("Provide a bounded revocation reason.", "host_root_input", 400)
        # Revoke the recorded path even if the live directory was replaced by a
        # symlink. Resolving first could silently leave the old approval active.
        target = str(Path(path).expanduser().absolute())
        with self.store._immediate() as conn:
            decision = authorize(self.store, "identity.admin", conn=conn)
            if decision is None:
                fail("Use an individual workspace administrator.", "host_owner_required")
            if not conn.execute("SELECT 1 FROM host_file_roots WHERE path=?", (target,)).fetchone():
                target = str(Path(path).expanduser().resolve())
                if not conn.execute(
                    "SELECT 1 FROM host_file_roots WHERE path=?", (target,)
                ).fetchone():
                    fail("The approved host root was not found.", "host_root_missing", 404)
            conn.execute(
                "UPDATE host_file_roots SET active=0,reason=?,actor=?,updated_at=? WHERE path=?",
                (reason, decision.actor.principal_id, now_iso(), target),
            )
            audit_effect(
                self.store,
                decision,
                "host-file-roots",
                {"operation": "root_revoked", "path": target},
                conn=conn,
            )
            return {"path": target, "active": False}

    def _read_check(self, path, source_id):
        with self._read() as conn:
            if self.store:
                authorize(
                    self.store,
                    "source.read",
                    source_ids=[source_id] if source_id else None,
                    conn=conn,
                )
            checked = self.check(path, conn=conn)
            if self.store and source_id:
                import json

                row = conn.execute(
                    "SELECT kind,locator FROM sources WHERE id=?", (source_id,)
                ).fetchone()
                if row and row["kind"] in ("files", "documents"):
                    try:
                        target = json.loads(row["locator"]).get("path")
                    except (ValueError, AttributeError):
                        target = None
                    if target:
                        root = self.check(target, conn=conn)
                        if checked != root and not (root.is_dir() and checked.is_relative_to(root)):
                            fail(
                                "The input is no longer inside its registered source target.",
                                "host_source_changed",
                            )
            return checked

    @contextmanager
    def open(self, path, *, source_id=None):
        resolved = self._read_check(path, source_id)
        directories = []
        descriptor = None
        try:
            parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            directories.append(parent)
            for part in resolved.parts[1:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                directories.append(parent)
            descriptor = os.open(resolved.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                fail(
                    "Live inputs must be regular files without symbolic or hard links.",
                    "host_unsafe_file",
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None

                class Guarded:
                    def read(inner, size=-1):
                        self._read_check(path, source_id)
                        return stream.read(size)

                yield Guarded()
                self._read_check(path, source_id)
        except OSError:
            fail(
                "The live input changed or cannot be opened without following links.",
                "host_unsafe_file",
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            for descriptor in reversed(directories):
                os.close(descriptor)

    def sha256(self, path, *, source_id=None):
        value = hashlib.sha256()
        with self.open(path, source_id=source_id) as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                value.update(block)
        return value.hexdigest()

    def locator(self, kind, locator, *, conn=None):
        if kind not in ("files", "documents"):
            return
        import json

        try:
            data = json.loads(locator)
        except (TypeError, ValueError):
            return  # historical malformed registrations grant no readable path
        if isinstance(data, dict) and isinstance(data.get("path"), str):
            self.check(data["path"], conn=conn)
