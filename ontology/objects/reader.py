# SPDX-License-Identifier: MIT
"""Read rows of a bound object type from its source database -- read only.

Every read runs in one transaction that is ``READ ONLY`` with a ``statement_timeout``
(:func:`open_source`), so even a mistake in this module cannot write to a source. SQL is
composed with :mod:`psycopg.sql`: identifiers (schema, table, columns) are only ever the
bound object type's -- checked against the table's actual columns in the same
transaction (:class:`PgBackend.columns`) -- and quoted with :class:`~psycopg.sql.Identifier`;
values are always parameters. Columns are compared as text (``CAST(col AS text) = %s``),
so a filter value never fails on a type mismatch.

Files sources read their local database (``ontology_src_...``, name-guarded);
database sources their own URL (Postgres only: no other driver is bundled).
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from psycopg import sql

#: Rows one read returns at most, whatever the caller asks for.
MAX_PAGE = 200
DEFAULT_PAGE = 50
#: Per-statement limit inside the read transaction, and the connect timeout.
STATEMENT_TIMEOUT_MS = 5000
CONNECT_TIMEOUT_S = 5
#: Search text and filter values are cut to this many characters.
MAX_VALUE_CHARS = 200
#: Postgres identifiers are at most 63 bytes.
MAX_IDENTIFIER_BYTES = 63


class ObjectError(Exception):
    """A failure the API answers with *status* and *code*."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.extra = extra


class SourceChanged(ObjectError):
    """The bound table or one of its columns is missing from the source now."""

    def __init__(self, reason: str) -> None:
        super().__init__(409, "object_type_broken", reason)
        self.reason = reason


def valid_identifier(name: str) -> bool:
    return (
        bool(name)
        and "\x00" not in name
        and len(name.encode("utf-8")) <= MAX_IDENTIFIER_BYTES
        and name == name.strip()
    )


def jsonable(value: Any) -> Any:
    """A source value as JSON: dates/times ISO text, decimals int or float (str when
    they would lose precision), bytes their length, anything else unknown as text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        if value == value.to_integral_value() and abs(value) < 2**53:
            return int(value)
        as_float = float(value)
        return as_float if decimal.Decimal(repr(as_float)) == value else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


# --- SQL -----------------------------------------------------------------------------


@dataclass
class TableQuery:
    """One SELECT over a bound table. ``equals``: ``(column, value)`` pairs compared as
    text; ``search``: ``(columns, text)`` -- any column contains the text (case-insensitive);
    ``any_of``: ``(column, values)`` -- the column (as text) is one of the values."""

    schema: str
    table: str
    columns: list[str]
    order_by: str = ""
    equals: list[tuple[str, str]] = field(default_factory=list)
    search: tuple[list[str], str] | None = None
    any_of: tuple[str, list[str]] | None = None
    limit: int = DEFAULT_PAGE
    offset: int = 0

    def identifiers(self) -> set[str]:
        names = set(self.columns) | {c for c, _ in self.equals}
        if self.order_by:
            names.add(self.order_by)
        if self.search:
            names |= set(self.search[0])
        if self.any_of:
            names.add(self.any_of[0])
        return names


def _table(q: TableQuery) -> sql.Composable:
    return sql.Identifier(q.schema, q.table) if q.schema else sql.Identifier(q.table)


def _text(column: str) -> sql.Composable:
    return sql.SQL("CAST({} AS text)").format(sql.Identifier(column))


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(q: TableQuery) -> tuple[sql.Composable, list[Any]]:
    parts: list[sql.Composable] = []
    params: list[Any] = []
    for column, value in q.equals:
        parts.append(sql.SQL("{} = %s").format(_text(column)))
        params.append(str(value)[:MAX_VALUE_CHARS])
    if q.search and q.search[1] and q.search[0]:
        ors = [sql.SQL("{} ILIKE %s").format(_text(c)) for c in q.search[0]]
        parts.append(sql.SQL("({})").format(sql.SQL(" OR ").join(ors)))
        params += [_like(q.search[1][:MAX_VALUE_CHARS])] * len(q.search[0])
    if q.any_of is not None:
        parts.append(sql.SQL("{} = ANY(%s)").format(_text(q.any_of[0])))
        params.append([str(v) for v in q.any_of[1]])
    if not parts:
        return sql.SQL(""), params
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def build_select(q: TableQuery) -> tuple[sql.Composed, list[Any]]:
    """``SELECT <columns> FROM <table> [WHERE ...] ORDER BY <order> LIMIT %s OFFSET %s``."""
    where, params = _where(q)
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in q.columns)
    order = (
        sql.SQL(" ORDER BY {} NULLS LAST").format(sql.Identifier(q.order_by))
        if q.order_by
        else sql.SQL("")
    )
    limit = max(1, min(int(q.limit), MAX_PAGE))
    query = sql.SQL("SELECT {} FROM {}{}{} LIMIT %s OFFSET %s").format(
        cols, _table(q), where, order
    )
    return query, [*params, limit, max(0, int(q.offset))]


def build_count(q: TableQuery) -> tuple[sql.Composed, list[Any]]:
    where, params = _where(q)
    return sql.SQL("SELECT count(*) FROM {}{}").format(_table(q), where), params


COLUMNS_SQL = (
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position"
)


def _pk_sql() -> str:
    from ontology.structured.pipeline import _PG_SCHEMA_QUERIES

    return (
        _PG_SCHEMA_QUERIES["pk"][0]
        + " AND kcu.table_schema = %s AND kcu.table_name = %s ORDER BY kcu.ordinal_position"
    )


class PgBackend:
    """Queries over one open read-only transaction (:func:`open_source`)."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def columns(self, schema: str, table: str) -> list[dict[str, str]]:
        rows = self.conn.execute(COLUMNS_SQL, [schema or "public", table]).fetchall()
        return [{"name": str(name), "type": str(kind)} for name, kind in rows]

    def primary_key(self, schema: str, table: str) -> list[str]:
        rows = self.conn.execute(_pk_sql(), [schema or "public", table]).fetchall()
        return [str(r[2]) for r in rows]

    def key_stats(self, schema: str, table: str, column: str) -> dict[str, int]:
        """``{rows, distinct, nulls}`` of *column*: a usable key has ``distinct == rows``
        and no nulls."""
        name = sql.Identifier(schema, table) if schema else sql.Identifier(table)
        query = sql.SQL("SELECT count(*), count(DISTINCT {c}), count(*) - count({c}) FROM {t}")
        row = self.conn.execute(query.format(c=sql.Identifier(column), t=name)).fetchone()
        return {"rows": int(row[0]), "distinct": int(row[1]), "nulls": int(row[2])}

    def select(self, q: TableQuery) -> list[dict[str, Any]]:
        query, params = build_select(q)
        rows = self.conn.execute(query, params).fetchall()
        return [{c: jsonable(v) for c, v in zip(q.columns, row, strict=True)} for row in rows]

    def count(self, q: TableQuery) -> int:
        query, params = build_count(q)
        return int(self.conn.execute(query, params).fetchone()[0])


def connect_source(settings: Any, source: Any) -> Any:
    """A psycopg connection (not autocommit) to *source*'s database."""
    import psycopg

    from ontology.config import validate_pg_database_name
    from ontology.structured.pipeline import catalog_database_name, parse_database_url

    if source.kind == "files":
        dbname = validate_pg_database_name(catalog_database_name(source))
        params: dict[str, Any] = {
            "host": settings.postgres_host,
            "port": int(settings.postgres_port),
            "user": settings.postgres_user,
            "password": settings.postgres_password or None,
            "dbname": dbname,
        }
    elif source.kind == "database":
        conn = parse_database_url(source.locator)
        if conn["type"] != "postgres":
            raise ObjectError(
                409,
                "unsupported_source",
                f"source {source.name!r} is a {conn['type']} database: objects are read from "
                "Postgres sources only",
            )
        params = {
            "host": conn["host"],
            "port": int(conn.get("port") or 5432),
            "user": conn["user"],
            "password": conn.get("password") or None,
            "dbname": conn["database"],
        }
    else:
        raise ObjectError(409, "unsupported_source", f"source {source.name!r} has no tables")
    return psycopg.connect(**params, connect_timeout=CONNECT_TIMEOUT_S, autocommit=False)


@contextmanager
def open_source(settings: Any, source: Any) -> Iterator[PgBackend]:
    """A :class:`PgBackend` inside a ``READ ONLY`` transaction with a statement timeout,
    always rolled back. An unreachable source is 503 ``source_unreachable``."""
    import psycopg

    try:
        conn = connect_source(settings, source)
    except ObjectError:
        raise
    except (psycopg.Error, OSError, ValueError) as exc:
        raise ObjectError(
            503,
            "source_unreachable",
            f"cannot read source {source.name!r}: {type(exc).__name__}",
        ) from exc
    try:
        # the first statement of the transaction: everything after it is read only
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(
            sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT_MS))
        )
        try:
            yield PgBackend(conn)
        except psycopg.errors.QueryCanceled as exc:
            raise ObjectError(504, "source_timeout", "the source took too long") from exc
        except psycopg.errors.UndefinedTable as exc:
            raise SourceChanged("source: the table is missing") from exc
        except psycopg.errors.UndefinedColumn as exc:
            raise SourceChanged("source: a column is missing") from exc
        except psycopg.Error as exc:
            raise ObjectError(
                502, "source_error", f"the source refused the read: {type(exc).__name__}"
            ) from exc
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
