# SPDX-License-Identifier: MIT
"""Read-only offline checks; never signal a PID or execute archived configuration."""

import os
import re
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from autoontology.recovery import profile, staging
from autoontology.security.fs_paths import canonical_path
from autoontology.security.policy import AccessError

PG_CONTROLDATA = Path("/opt/homebrew/opt/postgresql@17/bin/pg_controldata")


def busy():
    return AccessError(
        "Stop the original app, backend, PostgreSQL and CLI jobs before transfer; no process was stopped automatically.",
        "restore_source_busy",
        409,
    )


def cluster(path):
    if not path.exists():
        return {"state": "missing"}
    if (path / "postmaster.pid").exists():
        raise busy()
    result = subprocess.run(
        [str(PG_CONTROLDATA), "-D", str(path)],
        env={**os.environ, "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise busy()
    info = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)
    state = info.get("Database cluster state", "").strip()
    identifier = info.get("Database system identifier", "").strip()
    if state != "shut down" or not re.fullmatch(r"[0-9]+", identifier):
        raise busy()
    return {"state": state, "system_identifier": identifier}


def check(source_database, destination, ports):
    home = Path(source_database).parent
    if home.name != "workspace":
        raise busy()
    runtime = home.parent
    env = profile.read_env(runtime / "local.env")
    if (
        canonical_path(env.get("AUTOONTOLOGY_HOME", "")) != canonical_path(home)
        or env.get("POSTGRES_HOST") != "127.0.0.1"
    ):
        raise busy()
    try:
        old = {
            "ui": int(env["AUTOONTOLOGY_PORT"]),
            "backend": int(env["BACKEND_PORT"]),
            "postgres": int(env["POSTGRES_PORT"]),
        }
    except (ValueError, KeyError):
        raise busy() from None
    staging._ports(old, {})
    staging._ports(ports, env)
    for path in (home / "run").glob("*.pid"):
        try:
            pid = int(path.read_text().strip())
        except ValueError:
            raise busy() from None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            raise busy() from None
        raise busy()
    source_file = Path(source_database)
    if source_file.exists():
        try:
            with closing(sqlite3.connect(source_file.as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_leases'"
                ).fetchone():
                    for (pid,) in conn.execute("SELECT pid FROM source_leases"):
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            continue
                        except PermissionError:
                            raise busy() from None
                        raise busy()
        except sqlite3.DatabaseError:
            # The original may be corrupt. It never supplies replacement grants;
            # the independent pin and verified backup are the authority inputs.
            pass
    source = cluster(runtime / "postgres")
    target = cluster(destination / "postgres")
    if target["state"] != "shut down":
        raise busy()
    return {"source": source, "target": target, "source_ports": old, "selected_ports": ports}
