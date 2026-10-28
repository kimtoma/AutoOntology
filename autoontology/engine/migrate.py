# SPDX-License-Identifier: MIT
"""Alembic migrations for the upstream catalog schema (run inside the submodule)."""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg

from autoontology.config import VENDOR_DIR, Settings, postgres_env
from autoontology.engine.pg import connect


class MigrationError(RuntimeError):
    pass


def migration_env(settings: Settings) -> dict[str, str]:
    return {**os.environ, **postgres_env(settings), "PYTHONUNBUFFERED": "1"}


def run_migrations(settings: Settings) -> None:
    """``alembic upgrade head`` with cwd = vendor checkout and Postgres env injected."""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=VENDOR_DIR,
        env=migration_env(settings),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MigrationError(
            f"alembic upgrade head failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )


def head_revision() -> str | None:
    """The single head of the vendored migrations (``None`` if branched/unreadable)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(VENDOR_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(VENDOR_DIR / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    return heads[0] if len(heads) == 1 else None


def current_revision(settings: Settings) -> str | None:
    """Applied revision of the catalog DB, or ``None`` if never migrated."""
    with connect(settings) as conn:
        try:
            row = conn.execute("select version_num from public.alembic_version").fetchone()
        except psycopg.errors.UndefinedTable:
            return None
    return row[0] if row else None
