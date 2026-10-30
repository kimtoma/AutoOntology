# SPDX-License-Identifier: MIT
"""Private, offline checkpoints for the isolated Mac mini Ops development runtime.

This deliberately refuses the public runtime. Run with the project's Python.
The archive includes secrets and must stay outside git, readable by its owner only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

PG_CTL = Path("/opt/homebrew/opt/postgresql@17/bin/pg_ctl")


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Subprocess output can contain connection settings: never echo it.
        raise RuntimeError(f"checkpoint subprocess failed: {Path(str(args[0])).name}")
    return result.stdout


def hashes(folder: Path) -> dict[str, str]:
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("checkpoint payload must not contain symlinks")
        if path.is_file():
            with path.open("rb") as stream:
                result[path.relative_to(folder).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def verify(checkpoint: Path) -> dict:
    manifest = json.loads((checkpoint / "manifest.json").read_text())
    if manifest["sha256"] != hashes(checkpoint / "payload"):
        raise ValueError("checkpoint integrity check failed")
    database = checkpoint / "payload/workspace/autoontology.db"
    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
    return manifest


def stage(checkpoint: Path, destination: Path) -> dict:
    """Restore into a new directory only; never overwrite a running workspace."""
    manifest = verify(checkpoint)
    if destination.exists():
        raise ValueError("restore destination already exists; use a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(checkpoint / "payload", destination)
    os.chmod(destination, 0o700)
    assert hashes(destination) == manifest["sha256"]
    return manifest


def create(runtime: Path, label: str) -> Path:
    if runtime.name != "ontology-ops-local" or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", label):
        raise ValueError("use the isolated ontology-ops-local runtime and a safe checkpoint label")
    root = runtime.parents[1]
    app = runtime / "app"
    if (root / "data/ontology-ops-local").resolve() != runtime:
        raise ValueError("unexpected runtime location")
    sys.path.insert(0, str(app))
    os.environ["AUTOONTOLOGY_ENV_FILE"] = str(runtime / "local.env")
    from autoontology.config import Settings
    from autoontology.engine.pg import connect

    settings = Settings.from_env()
    if settings.postgres_port != 55433 or settings.port != 7830:
        raise ValueError("refusing to checkpoint a non-isolated service")
    # A physical cluster copy must not include unrelated application databases.
    with connect(settings, "postgres") as conn:
        databases = [
            r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE NOT datistemplate")
        ]
    if any(
        not re.fullmatch(r"postgres|autoontology(?:_src_[a-z0-9_]+)?", name) for name in databases
    ):
        raise ValueError("isolated cluster contains a database outside AutoOntology's scope")
    if command(["git", "status", "--porcelain"], cwd=app).strip():
        raise ValueError("commit local code before making a stage checkpoint")
    revision = command(["git", "rev-parse", "HEAD"], cwd=app).strip()
    destination = runtime / "checkpoints" / label
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    if destination.exists():
        raise ValueError("checkpoint already exists")
    staging = destination.with_name("." + label + ".partial")
    staging.mkdir(mode=0o700)
    payload = staging / "payload"
    payload.mkdir(mode=0o700)
    python = str(root / ".venv/bin/python")
    pg_stopped = False
    service_stopped = False
    try:
        command([python, str(runtime / "control.py"), "stop"], cwd=root)
        service_stopped = True
        command([str(PG_CTL), "-D", str(runtime / "postgres"), "stop", "-m", "fast", "-w"])
        pg_stopped = True
        for name in ("workspace", "exports", "postgres"):
            shutil.copytree(runtime / name, payload / name, symlinks=False)
        for name in ("local.env", "control.py"):
            shutil.copy2(runtime / name, payload / name)
        command(
            ["git", "archive", "--format=tar", "-o", str(payload / "source.tar"), revision], cwd=app
        )
        for path in payload.rglob("*"):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        manifest = {
            "version": 1,
            "commit": revision,
            "label": label,
            "databases": databases,
            "runtime": str(runtime),
            "sha256": hashes(payload),
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))
        os.chmod(staging / "manifest.json", 0o600)
        verify(staging)
        os.replace(staging, destination)
    finally:
        if pg_stopped:
            command(
                [
                    str(PG_CTL),
                    "-D",
                    str(runtime / "postgres"),
                    "-l",
                    str(runtime / "postgres.log"),
                    "start",
                    "-w",
                ]
            )
        if service_stopped:
            command([python, str(runtime / "control.py"), "start"], cwd=root)
    return destination


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--runtime", required=True, type=Path)
    create_parser.add_argument("--label", required=True)
    for name in ("verify", "stage"):
        item = sub.add_parser(name)
        item.add_argument("checkpoint", type=Path)
        if name == "stage":
            item.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.operation == "create":
        print(create(args.runtime.resolve(), args.label))
    else:
        result = (
            verify(args.checkpoint)
            if args.operation == "verify"
            else stage(args.checkpoint, args.destination)
        )
        print(f"Verified checkpoint: {result['label']} / commit {result['commit']}")


if __name__ == "__main__":
    main()
