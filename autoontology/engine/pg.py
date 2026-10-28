"""Postgres helpers (psycopg). Never imports ``auto_ontology``."""

from __future__ import annotations

import psycopg
from psycopg import sql

from autoontology.config import (
    PG_MAINTENANCE_DB,
    Settings,
    validate_pg_database_name,
)

CONNECT_TIMEOUT_S = 5
#: Extensions the engine needs in the catalog database.
REQUIRED_EXTENSIONS: tuple[str, ...] = ("vector", "pg_trgm")


def connect(settings: Settings, database: str | None = None) -> psycopg.Connection:
    """Connect to an AutoOntology DB (name-guarded) or the read-only ``postgres`` maintenance DB."""
    dbname = database or settings.postgres_database
    if dbname != PG_MAINTENANCE_DB:
        validate_pg_database_name(dbname)
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password or None,
        dbname=dbname,
        connect_timeout=CONNECT_TIMEOUT_S,
        autocommit=True,
    )


def database_exists(settings: Settings, name: str | None = None) -> bool:
    with connect(settings, PG_MAINTENANCE_DB) as conn:
        row = conn.execute(
            "select 1 from pg_database where datname = %s", (name or settings.postgres_database,)
        ).fetchone()
    return row is not None


def extension_version(settings: Settings, extension: str) -> str | None:
    with connect(settings) as conn:
        row = conn.execute(
            "select extversion from pg_extension where extname = %s", (extension,)
        ).fetchone()
    return row[0] if row else None


class SetupError(RuntimeError):
    pass


def setup_database(settings: Settings) -> str:
    """Idempotent: create the catalog database if it is missing (name-guarded, via the
    ``postgres`` maintenance DB) and ``create extension if not exists`` for ``vector``
    and ``pg_trgm``. Returns a one-line status."""
    db = validate_pg_database_name(settings.postgres_database)
    where = f"{settings.postgres_host}:{settings.postgres_port} as {settings.postgres_user}"
    try:
        with connect(settings, PG_MAINTENANCE_DB) as conn:
            exists = (
                conn.execute("select 1 from pg_database where datname = %s", (db,)).fetchone()
                is not None
            )
            if not exists:
                try:
                    conn.execute(sql.SQL("create database {}").format(sql.Identifier(db)))
                except psycopg.errors.DuplicateDatabase:
                    # a parallel setup (or the compose image's first boot) created it
                    # between the check and the create
                    exists = True
    except psycopg.Error as exc:
        raise SetupError(
            f"cannot prepare database {db} on {where}: "
            f"{type(exc).__name__}: {str(exc).strip()[:300]}"
        ) from exc
    created = "exists" if exists else "created"
    try:
        with connect(settings) as conn:
            conn.execute("set client_min_messages = warning")
            for ext in REQUIRED_EXTENSIONS:
                conn.execute(
                    sql.SQL("create extension if not exists {}").format(sql.Identifier(ext))
                )
            rows = conn.execute(
                "select extname, extversion from pg_extension where extname = any(%s) "
                "order by extname",
                (list(REQUIRED_EXTENSIONS),),
            ).fetchall()
    except psycopg.Error as exc:
        hint = (
            " (the server needs pgvector, e.g. the pgvector/pgvector:pg17 image from "
            "docker-compose.yml)"
            if "vector" in str(exc)
            else ""
        )
        raise SetupError(
            f"could not create extensions in {db}: {str(exc).strip()[:300]}{hint}"
        ) from exc
    versions = ", ".join(f"{name} {version}" for name, version in rows)
    return f"database {db} ({created}) on {where}; extensions: {versions}"
