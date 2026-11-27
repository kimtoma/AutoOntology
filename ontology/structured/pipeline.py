# SPDX-License-Identifier: MIT
"""Source registration for the structured pipeline (``onto source add``).

A source is either ``files`` (local files loaded into ``ontology_src_<name>``) or
``database`` (an external DB URL registered with upstream as-is). The upstream catalog
keys everything by the connection's ``database`` value, so that name must be unique
across sources.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from ontology.config import Settings, source_database_name, source_name_max_len
from ontology.store.db import ProjectStore, Source, Store, root_store, scoped
from ontology.structured.loaders import discover_files

SOURCE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
#: Upstream's connection-string factory requires a non-empty password
#: (connectors/connection_string_factory.py:27-31,61). The shared local Postgres uses
#: trust auth, which ignores it; a real POSTGRES_PASSWORD is used when configured.
TRUST_AUTH_PASSWORD = "trust-auth"
_URL_SCHEMES = {"postgres", "postgresql", "mysql", "snowflake"}


class SourceError(ValueError):
    pass


def pg_database_for(name: str, slug: str | None = None) -> str:
    """The local database of a new files source *name*: ``ontology_src_<name>`` in the
    default project (*slug* None), ``ontology_src_<slug_>__<name>`` in another
    (:func:`ontology.config.source_database_name`). An existing source keeps the name
    its row stores (``sources.pg_database``)."""
    return source_database_name(name, slug)


def database_slug(store: Store | ProjectStore) -> str | None:
    """The slug that goes into a new source database name of *store*'s project: None for
    the default project (its databases keep the historical ``ontology_src_<name>``)."""
    view = scoped(store)
    return None if view.is_default else view.slug


def validate_source_name(name: str, slug: str | None = None) -> str:
    """*name* when it can name a structured source of the project with *slug* (None: the
    default project): ``[a-z0-9_]+`` and short enough for its database name to fit
    Postgres' 63-byte identifier limit (:func:`ontology.config.source_name_max_len`)."""
    if not SOURCE_NAME_RE.fullmatch(name or ""):
        raise SourceError(
            f"invalid source name {name!r}: use lowercase letters, digits and _ only "
            "(the local database is named ontology_src_<name>)"
        )
    limit = source_name_max_len(slug)
    if len(name) > limit:
        where = f" in project {slug!r}" if slug else ""
        pattern = (
            f"ontology_src_{slug.replace('-', '_')}__<name>" if slug else ("ontology_src_<name>")
        )
        raise SourceError(
            f"invalid source name {name!r}: at most {limit} characters{where} (the local "
            f"database {pattern} must fit Postgres' 63-byte identifier limit)"
        )
    return name


def source_for_database(store: Store | ProjectStore, database_name: str) -> Source | None:
    """The structured source, in any project, the upstream catalog knows as
    *database_name* (case-insensitive; a database belongs to one project only)."""
    key = (database_name or "").casefold()
    if not key:
        return None
    for s in root_store(store).list_all_sources():
        if s.kind not in ("files", "database"):
            continue
        try:
            if catalog_database_name(s).casefold() == key:
                return s
        except Exception:  # noqa: BLE001 - an unparsable locator names no database
            continue
    return None


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
            raise SourceError(
                f"{scheme} URL needs user, host and database: "
                f"{scheme}://user:password@host:port/database"
            )
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
            raise SourceError(
                "snowflake URL needs user, account, warehouse and database: "
                "snowflake://user:password@account?warehouse=W&database=D"
            )
        return {
            "type": "snowflake",
            "account": parts.hostname,
            "user": user,
            "password": password,
            "warehouse": query["warehouse"],
            "database": database,
        }
    raise SourceError(
        f"unsupported database URL scheme {scheme!r} (supported: {', '.join(sorted(_URL_SCHEMES))})"
    )


#: Seconds to wait for an external database while fingerprinting its schema: the connect
#: and (as ``statement_timeout``) every metadata query.
SCHEMA_CONNECT_TIMEOUT_S = 5

_PG_USER_SCHEMAS = "NOT IN ('pg_catalog', 'information_schema')"
#: What upstream's Postgres connector ingests and the semantic step consumes
#: (``connectors/postgres.py`` ``get_columns`` / ``get_pks`` / ``get_fks``), one row kind
#: each, so a column, primary-key or foreign-key change all change the fingerprint (the
#: foreign keys: :data:`_PG_FK_SQL`).
_PG_SCHEMA_QUERIES: dict[str, tuple[str, str]] = {
    "column": (
        "SELECT table_schema, table_name, column_name, data_type "
        "FROM information_schema.columns WHERE table_schema " + _PG_USER_SCHEMAS,
        "table_schema",
    ),
    "pk": (
        "SELECT kcu.table_schema, kcu.table_name, kcu.column_name, kcu.ordinal_position "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema " + _PG_USER_SCHEMAS,
        "tc.table_schema",
    ),
}


#: Foreign keys from ``pg_constraint`` (``information_schema.constraint_column_usage``
#: cannot pair columns and joins the referenced table on the referencing schema): both
#: ends resolved by ``conrelid``/``confrelid`` to schema-qualified names, and the
#: ``conkey``/``confkey`` arrays, paired position by position (:data:`_PG_FK_ATTR_SQL`).
_PG_FK_SQL = (
    "SELECT con.conrelid::bigint, con.confrelid::bigint, ns.nspname, con.conname, "
    "cn.nspname, cl.relname, fn.nspname, fcl.relname, con.conkey, con.confkey "
    "FROM pg_constraint con "
    "JOIN pg_namespace ns ON ns.oid = con.connamespace "
    "JOIN pg_class cl ON cl.oid = con.conrelid "
    "JOIN pg_namespace cn ON cn.oid = cl.relnamespace "
    "JOIN pg_class fcl ON fcl.oid = con.confrelid "
    "JOIN pg_namespace fn ON fn.oid = fcl.relnamespace "
    "WHERE con.contype = 'f' AND cn.nspname " + _PG_USER_SCHEMAS
)
#: Column names of every relation on either end of a foreign key, by attribute number.
_PG_FK_ATTR_SQL = (
    "SELECT a.attrelid::bigint, a.attnum::int, a.attname FROM pg_attribute a "
    "WHERE a.attnum > 0 AND a.attrelid IN (SELECT conrelid FROM pg_constraint WHERE "
    "contype = 'f' UNION SELECT confrelid FROM pg_constraint WHERE contype = 'f')"
)


def _pg_foreign_keys(pg_conn: Any, schemas: list[str] | None) -> list[tuple[str, ...]]:
    """``fk`` rows (constraint schema/name, schema, table, column, referenced
    schema/table/column, position): one per column pair, ``conkey[i]`` with ``confkey[i]``."""
    sql, args = _PG_FK_SQL, []
    if schemas:
        sql += " AND cn.nspname = ANY(%s)"
        args.append(schemas)
    constraints = pg_conn.execute(sql, args).fetchall()
    if not constraints:
        return []
    names = {
        (int(rel), int(num)): str(name)
        for rel, num, name in pg_conn.execute(_PG_FK_ATTR_SQL, []).fetchall()
    }
    rows: list[tuple[str, ...]] = []
    for (
        rel,
        ref,
        con_schema,
        con_name,
        schema,
        table,
        ref_schema,
        ref_table,
        keys,
        refs,
    ) in constraints:
        for position, (key, ref_key) in enumerate(
            zip(keys or [], refs or [], strict=True), start=1
        ):
            rows.append(
                (
                    "fk",
                    str(con_schema),
                    str(con_name),
                    str(schema),
                    str(table),
                    names.get((int(rel), int(key)), f"#{key}"),
                    str(ref_schema),
                    str(ref_table),
                    names.get((int(ref), int(ref_key)), f"#{ref_key}"),
                    str(position),
                )
            )
    return rows


def _postgres_schema(conn: dict[str, Any]) -> list[tuple[str, ...]]:
    """Rows ``(kind, ...)`` of a Postgres database's user schema: ``column`` (schema,
    table, column, type), ``pk`` (schema, table, column, position) and ``fk`` (constraint
    schema/name, schema, table, column, referenced schema/table/column, position:
    :func:`_pg_foreign_keys`), every query under ``statement_timeout``."""
    import psycopg

    timeout_ms = int(SCHEMA_CONNECT_TIMEOUT_S * 1000)
    rows: list[tuple[str, ...]] = []
    with psycopg.connect(
        host=conn["host"],
        port=int(conn.get("port") or 5432),
        user=conn["user"],
        password=conn.get("password") or None,
        dbname=conn["database"],
        connect_timeout=SCHEMA_CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={timeout_ms}",
    ) as pg_conn:
        for kind, (sql, schema_col) in _PG_SCHEMA_QUERIES.items():
            args: list[Any] = []
            if conn.get("schemas"):
                sql += f" AND {schema_col} = ANY(%s)"
                args.append(list(conn["schemas"]))
            rows += [
                (kind, *(str(v) for v in row)) for row in pg_conn.execute(sql, args).fetchall()
            ]
        rows += _pg_foreign_keys(pg_conn, list(conn["schemas"]) if conn.get("schemas") else None)
    return rows


#: Schema introspection per connection ``type``; a type without one (mysql, snowflake: no
#: driver is bundled) has no fingerprint, so ``build --resume`` never reuses its steps.
SCHEMA_INTROSPECTORS: dict[str, Callable[[dict[str, Any]], list[tuple[str, ...]]]] = {
    "postgres": _postgres_schema,
}


def schema_fingerprint(source: Source) -> str:
    """A hash of the sorted schema rows (columns and types, primary and foreign keys) of a
    database source, or ""
    when it cannot be read (unreachable, or no introspector for its type)."""
    try:
        conn = parse_database_url(source.locator)
        introspect = SCHEMA_INTROSPECTORS.get(conn["type"])
        if introspect is None:
            return ""
        rows = introspect(conn)
    except Exception:  # noqa: BLE001 - unreachable/unreadable: do not reuse
        return ""
    return schema_rows_fingerprint(rows)


def schema_rows_fingerprint(rows: list[Any]) -> str:
    """:func:`schema_fingerprint` of schema rows already read."""
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode()).hexdigest()


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
    store: Store | ProjectStore,
    target: str,
    *,
    name: str,
    kind: str | None = None,
    recursive: bool = False,
) -> Source:
    """Validate and record a source in *store*'s project (a plain store: the default
    project). No data is moved until ``onto build``. A database another source
    already uses is refused -- in this project, and in any other (an upstream catalog
    database, with its Terms, belongs to one project only)."""
    view = scoped(store)
    slug = database_slug(view)
    validate_source_name(name, slug)
    kind = kind or ("database" if looks_like_url(target) else "files")
    if view.get_source(name):
        raise SourceError(f"source {name!r} already exists (remove it with `source rm {name}`)")
    if kind == "files":
        path = Path(target).expanduser().resolve()
        files = discover_files(path, recursive=recursive)  # raises LoadError
        if not files:
            raise SourceError(f"no supported files in {path}")
        locator = json.dumps({"path": str(path), "recursive": recursive}, ensure_ascii=False)
        pg_database: str | None = pg_database_for(name, slug)
        db_name = pg_database
    elif kind == "database":
        if not looks_like_url(target):
            raise SourceError(f"--kind database needs a database URL, got {target!r}")
        db_name = str(parse_database_url(target)["database"])
        locator, pg_database = target, None
    else:
        raise SourceError(f"unsupported kind {kind!r} (files | database)")
    other = source_for_database(view, db_name)
    if other is not None:
        if other.project_id == view.id:
            raise SourceError(f"source {other.name!r} already uses database {db_name!r}")
        owner = view.root.get_project(other.project_id)
        raise SourceError(
            f"database {db_name!r} is already registered by source {other.name!r} of project "
            f"{owner.slug if owner else other.project_id!r}: a database can belong to one "
            "project only"
        )
    return view.add_source(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        locator=locator,
        pg_database=pg_database,
    )
