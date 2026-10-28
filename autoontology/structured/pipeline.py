"""Source registration for the structured pipeline (``autoontology source add``).

A source is either ``files`` (local files loaded into ``autoontology_src_<name>``) or
``database`` (an external DB URL registered with upstream as-is). The upstream catalog
keys everything by the connection's ``database`` value, so that name must be unique
across sources.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from autoontology.config import SOURCE_NAME_MAX_LEN, Settings, validate_pg_database_name
from autoontology.store.db import Source, Store
from autoontology.structured.loaders import discover_files

SOURCE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
#: Upstream's connection-string factory requires a non-empty password
#: (connectors/connection_string_factory.py:27-31,61). The shared local Postgres uses
#: trust auth, which ignores it; a real POSTGRES_PASSWORD is used when configured.
TRUST_AUTH_PASSWORD = "trust-auth"
_URL_SCHEMES = {"postgres", "postgresql", "mysql", "snowflake"}


class SourceError(ValueError):
    pass


def pg_database_for(name: str) -> str:
    return validate_pg_database_name(f"autoontology_src_{name}")


def validate_source_name(name: str) -> str:
    if not SOURCE_NAME_RE.fullmatch(name or ""):
        raise SourceError(
            f"invalid source name {name!r}: use lowercase letters, digits and _ only "
            "(the local database is named autoontology_src_<name>)"
        )
    if len(name) > SOURCE_NAME_MAX_LEN:
        raise SourceError(
            f"invalid source name {name!r}: at most {SOURCE_NAME_MAX_LEN} characters (the local "
            "database autoontology_src_<name> must fit Postgres' 63-byte identifier limit)"
        )
    return name


def looks_like_url(target: str) -> bool:
    return "://" in target and target.split("://", 1)[0].lower() in _URL_SCHEMES


def parse_database_url(url: str) -> dict[str, Any]:
    """Upstream connection dict for a postgres/mysql/snowflake URL."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    if scheme in ("postgres", "postgresql", "mysql"):
        database = unquote(parts.path.lstrip("/"))
        if not (parts.hostname and user and database):
            raise SourceError(f"{scheme} URL needs user, host and database: "
                              f"{scheme}://user:password@host:port/database")
        conn: dict[str, Any] = {
            "type": "postgres" if scheme.startswith("postgres") else "mysql",
            "host": parts.hostname,
            "user": user,
            "password": password,
            "database": database,
        }
        if parts.port:
            conn["port"] = str(parts.port)
        if "schema" in query:
            conn["schemas"] = [s for s in query["schema"].split(",") if s]
        return conn
    if scheme == "snowflake":
        database = query.get("database", "")
        if not (parts.hostname and user and database and query.get("warehouse")):
            raise SourceError("snowflake URL needs user, account, warehouse and database: "
                              "snowflake://user:password@account?warehouse=W&database=D")
        return {"type": "snowflake", "account": parts.hostname, "user": user,
                "password": password, "warehouse": query["warehouse"], "database": database}
    raise SourceError(f"unsupported database URL scheme {scheme!r} "
                      f"(supported: {', '.join(sorted(_URL_SCHEMES))})")


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return parts._replace(netloc=netloc).geturl()


@dataclass(frozen=True)
class FilesLocator:
    path: str
    recursive: bool = False

    def files(self) -> list[Path]:
        return discover_files(Path(self.path), recursive=self.recursive)


def files_locator(source: Source) -> FilesLocator:
    data = json.loads(source.locator)
    return FilesLocator(path=data["path"], recursive=bool(data.get("recursive")))


def catalog_database_name(source: Source) -> str:
    """The ``database`` the upstream catalog knows this source by."""
    if source.kind == "files":
        return source.pg_database or pg_database_for(source.name)
    if source.kind == "database":
        return str(parse_database_url(source.locator)["database"])
    raise SourceError(f"source {source.name!r} has kind {source.kind!r}, not structured")


def connection_for(source: Source, settings: Settings) -> dict[str, Any]:
    """The connection dict registered with ``POST /api/connections``."""
    if source.kind == "database":
        return parse_database_url(source.locator)
    return {
        "type": "postgres",
        "host": settings.postgres_host,
        "port": str(settings.postgres_port),
        "user": settings.postgres_user,
        "password": settings.postgres_password or TRUST_AUTH_PASSWORD,
        "database": catalog_database_name(source),
    }


def display_locator(source: Source) -> str:
    if source.kind == "database":
        return redact_url(source.locator)
    if source.kind in ("files", "documents"):
        loc = files_locator(source)
        return loc.path + (" (recursive)" if loc.recursive else "")
    return source.locator


def add_source(
    store: Store, target: str, *, name: str, kind: str | None = None, recursive: bool = False
) -> Source:
    """Validate and record a source. No data is moved until ``autoontology build``."""
    validate_source_name(name)
    kind = kind or ("database" if looks_like_url(target) else "files")
    if store.get_source(name):
        raise SourceError(f"source {name!r} already exists (remove it with `source rm {name}`)")
    if kind == "files":
        path = Path(target).expanduser().resolve()
        files = discover_files(path, recursive=recursive)  # raises LoadError
        if not files:
            raise SourceError(f"no supported files in {path}")
        locator = json.dumps({"path": str(path), "recursive": recursive}, ensure_ascii=False)
        pg_database: str | None = pg_database_for(name)
        db_name = pg_database
    elif kind == "database":
        if not looks_like_url(target):
            raise SourceError(f"--kind database needs a database URL, got {target!r}")
        db_name = str(parse_database_url(target)["database"])
        locator, pg_database = target, None
    else:
        raise SourceError(f"unsupported kind {kind!r} (files | database)")
    for other in store.list_sources():
        if other.kind in ("files", "database") and \
                catalog_database_name(other).casefold() == db_name.casefold():
            raise SourceError(f"source {other.name!r} already uses database {db_name!r}")
    return store.add_source(name=name, kind=kind, locator=locator,  # type: ignore[arg-type]
                            pg_database=pg_database)
