# SPDX-License-Identifier: MIT
"""Load local tabular files into a per-source Postgres database ``autoontology_src_<name>``.

DuckDB (in-process) sniffs and types every file; rows then stream into Postgres with
``COPY ... FROM STDIN``. One file = one table, except XLSX (one table per sheet) and
SQLite (one table per SQLite table). Tables are replaced on every load, and tables of the
database that the current input set no longer produces are dropped (the database is owned
by the source). The load returns a manifest (table -> input file, sheet, columns and types)
and the schema diff against the previous manifest, so the caller can invalidate semantic
objects built on tables whose schema changed or disappeared before they are rewritten.

Split in two halves so the typing logic is testable without Postgres:
:func:`read_tables` (DuckDB only) and :func:`write_tables` (Postgres).
"""

from __future__ import annotations

import datetime
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from autoontology.config import PG_MAINTENANCE_DB, Settings, assert_source_database

CSV_SUFFIXES = {".csv", ".tsv"}
PARQUET_SUFFIXES = {".parquet", ".pq"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
SUPPORTED_SUFFIXES = (
    CSV_SUFFIXES | PARQUET_SUFFIXES | JSON_SUFFIXES | XLSX_SUFFIXES | SQLITE_SUFFIXES
)

#: Postgres truncates identifiers to NAMEDATALEN-1 = 63 *bytes* (Hangul is 3 bytes each).
PG_IDENTIFIER_MAX_BYTES = 63
COPY_BATCH_ROWS = 10_000

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_UNDERSCORES = re.compile(r"_+")


class LoadError(RuntimeError):
    pass


# --- identifiers -------------------------------------------------------------------


def normalize_identifier(name: str, fallback: str = "col") -> str:
    """Lowercase; spaces/punctuation -> ``_``; keep Hangul and other letters; strip BOM.

    The result is always used quoted, so leading digits and non-ASCII letters are fine.
    """
    text = unicodedata.normalize("NFC", str(name)).replace("﻿", "").strip().lower()
    text = _UNDERSCORES.sub("_", _NON_WORD.sub("_", text)).strip("_")
    if not text:
        text = fallback
    encoded = text.encode("utf-8")
    if len(encoded) > PG_IDENTIFIER_MAX_BYTES:
        text = encoded[:PG_IDENTIFIER_MAX_BYTES].decode("utf-8", errors="ignore").rstrip("_")
    return text


def dedupe_identifiers(names: Iterable[str]) -> list[str]:
    """Make names unique by appending ``_2``, ``_3`` ... (keeping within the byte limit)."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        candidate, n = name, 1
        while candidate in seen:
            n += 1
            suffix = f"_{n}"
            room = PG_IDENTIFIER_MAX_BYTES - len(suffix.encode())
            base = name.encode("utf-8")[:room].decode("utf-8", errors="ignore")
            candidate = base + suffix
        seen.add(candidate)
        out.append(candidate)
    return out


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# --- types -------------------------------------------------------------------------

_SIMPLE_TYPES = {
    "boolean": "boolean",
    "tinyint": "smallint",
    "smallint": "smallint",
    "integer": "integer",
    "bigint": "bigint",
    "utinyint": "smallint",
    "usmallint": "integer",
    "uinteger": "bigint",
    "ubigint": "numeric",
    "hugeint": "numeric",
    "uhugeint": "numeric",
    "float": "real",
    "double": "double precision",
    "varchar": "text",
    "date": "date",
    "time": "time",
    "timestamp": "timestamp",
    "timestamp_s": "timestamp",
    "timestamp_ms": "timestamp",
    "timestamp_ns": "timestamp",
    "timestamp with time zone": "timestamptz",
    "interval": "interval",
    "blob": "bytea",
    "uuid": "uuid",
}
_JSON_TYPES = {"struct", "list", "map", "array", "union", "json"}


def duckdb_to_pg_type(duck_type: Any) -> str:
    """Postgres column type for a DuckDB type (``DuckDBPyType`` or its string)."""
    type_id = getattr(duck_type, "id", None) or str(duck_type).split("(")[0].lower()
    type_id = str(type_id).lower()
    if str(duck_type).rstrip().endswith("]"):  # LIST / fixed ARRAY spelled as "T[]"
        return "jsonb"
    if type_id == "decimal":
        match = re.search(r"\((\d+)\s*,\s*(\d+)\)", str(duck_type))
        return f"numeric({match.group(1)},{match.group(2)})" if match else "numeric"
    if type_id in _JSON_TYPES:
        return "jsonb"
    return _SIMPLE_TYPES.get(type_id, "text")


# --- reading (DuckDB) --------------------------------------------------------------


@dataclass
class ColumnPlan:
    name: str
    original: str
    duck_type: str
    pg_type: str


@dataclass
class TablePlan:
    """A table ready to copy: ``select_sql`` runs on the DuckDB connection."""

    name: str
    source_file: str
    sheet: str | None
    select_sql: str
    columns: list[ColumnPlan] = field(default_factory=list)


@dataclass
class TableReport:
    name: str
    source_file: str
    sheet: str | None
    rows: int
    columns: list[dict[str, str]]


@dataclass
class LoadReport:
    pg_database: str
    created_database: bool = False
    tables: list[TableReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: ``{table: {"source_file", "sheet", "columns": {name: pg type}}}`` of this load
    manifest: dict[str, Any] = field(default_factory=dict)
    #: :func:`diff_manifest` against the previous schema (``added``/``removed``/``changed``)
    schema_changes: dict[str, list[str]] = field(default_factory=dict)
    #: obsolete tables dropped from the database
    dropped_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)


def discover_files(path: Path, *, recursive: bool = False) -> list[Path]:
    """Supported files at *path* (a file, or a directory scanned non-recursively by default)."""
    path = Path(path)
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise LoadError(f"unsupported file type: {path.name}")
        return [path]
    if not path.is_dir():
        raise LoadError(f"no such file or directory: {path}")
    pattern = "**/*" if recursive else "*"
    return sorted(
        p
        for p in path.glob(pattern)
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith(".")
    )


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        import pandas as pd

        return bool(pd.isna(value)) if not isinstance(value, (bytes, str)) else False
    except (TypeError, ValueError):
        return False


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return "\\x" + value.hex()
    return str(value)


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
HUGEINT_MAX = 2**127 - 1
#: pandas' datetime64[ns] range (naive)
PD_MIN = datetime.datetime(1677, 9, 22)
PD_MAX = datetime.datetime(2262, 4, 11)


def _in_pandas_range(value: Any) -> bool:
    try:
        return PD_MIN <= value.replace(tzinfo=None) <= PD_MAX
    except (TypeError, ValueError, AttributeError):
        return False


def typed_column(values: list[Any]) -> tuple[Any, str | None]:
    """A lossless, nullable pandas column for raw cell values (SQLite rows, XLSX cells),
    plus the DuckDB type AutoOntology decided for it when DuckDB must not infer it.

    integers -> ``Int64`` (BIGINT, exact beyond 2**53, NULLs kept); beyond int64 -> HUGEINT
    (Postgres ``numeric``), beyond HUGEINT -> text; numbers -> ``Float64``; booleans ->
    ``boolean``; bytes -> BLOB (``bytea``); datetimes -> datetime64, outside pandas'
    nanosecond range -> TIMESTAMP; dates -> DATE; times -> TIME; text -> VARCHAR; any other
    mix -> text. Values whose type is planned here are passed to DuckDB as exact strings and
    cast explicitly (:func:`typed_frame`): DuckDB infers object columns from a sample of
    rows, so one oversized value past the sample would otherwise fail the read."""
    import datetime as dt

    import pandas as pd

    clean = [None if _missing(v) else v for v in values]
    kinds = {type(v) for v in clean if v is not None}

    def text(fn=str) -> Any:
        return pd.Series([None if v is None else fn(v) for v in clean], dtype=object)

    if not kinds:
        return pd.Series(clean, dtype=object), None
    if kinds == {bool}:
        return pd.array(clean, dtype="boolean"), None
    if kinds == {int}:
        present = [v for v in clean if v is not None]
        if all(INT64_MIN <= v <= INT64_MAX for v in present):
            return pd.array(clean, dtype="Int64"), None
        if all(-HUGEINT_MAX <= v <= HUGEINT_MAX for v in present):
            return text(), "HUGEINT"  # exact as digits, cast by DuckDB
        return text(), "VARCHAR"
    if kinds <= {int, float}:
        return pd.array([None if v is None else float(v) for v in clean], dtype="Float64"), None
    if kinds == {bytes}:
        return pd.Series(clean, dtype=object), "BLOB"
    if kinds <= {dt.datetime, pd.Timestamp}:
        if all(_in_pandas_range(v) for v in clean if v is not None):
            return pd.to_datetime(pd.Series(clean, dtype=object)), None
        # outside pandas' nanosecond range (9999-12-31 sentinels, 1600-01-01): ISO text,
        # cast to TIMESTAMP (Postgres timestamp covers them)
        return text(lambda v: v.isoformat(sep=" ")), "TIMESTAMP"
    if kinds == {dt.date}:
        return text(lambda v: v.isoformat()), "DATE"
    if kinds == {dt.time}:
        return text(lambda v: v.isoformat()), "TIME"
    if kinds == {str}:
        return pd.Series(clean, dtype=object), "VARCHAR"
    return text(_as_text), "VARCHAR"


def internal_column(index: int) -> str:
    """Positional name a frame column gets inside DuckDB (``c0``, ``c1``, ...)."""
    return f"c{index}"


def typed_frame(columns: list[tuple[Any, list[Any]]]) -> tuple[Any, dict[str, str], list[str]]:
    """``(DataFrame, {internal column: DuckDB type}, display labels)`` for *columns*
    (``(label, values)`` in order). Columns are named by position inside DuckDB: labels such
    as ``1`` vs ``"1"`` or ``ID`` vs ``id`` (DuckDB identifiers are case-insensitive) would
    otherwise collapse into one column; the labels come back as display names, which
    :func:`read_tables` normalises and de-duplicates."""
    import pandas as pd

    data, casts, labels = {}, {}, []
    for index, (label, values) in enumerate(columns):
        series, cast = typed_column(values)
        name = internal_column(index)
        data[name] = series
        labels.append(str(label))
        if cast:
            casts[name] = cast
    return pd.DataFrame(data), casts, labels


def register_typed(
    duck: duckdb.DuckDBPyConnection, view: str, frame: Any, casts: dict[str, str]
) -> str:
    """Register *frame* and return a SELECT that applies the planned casts. The pandas
    scan samples every row (``pandas_analyze_sample``), so a BLOB or text column is never
    typed from a prefix either."""
    duck.execute(f"SET pandas_analyze_sample = {max(1000, len(frame))}")
    duck.register(view, frame)
    if not casts:
        return f"SELECT * FROM {view}"
    exprs = [
        f"CAST({quote_ident(c)} AS {casts[c]}) AS {quote_ident(c)}"
        if c in casts
        else quote_ident(c)
        for c in frame.columns
    ]
    return f"SELECT {', '.join(exprs)} FROM {view}"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _relations_for_file(
    duck: duckdb.DuckDBPyConnection, path: Path, warnings: list[str]
) -> Iterator[tuple[str, str | None, str, list[str] | None]]:
    """Yield ``(table_base_name, sheet, duckdb_select_sql, display labels)`` for one file;
    labels are ``None`` when the relation's own column names are the labels."""
    suffix = path.suffix.lower()
    literal = _sql_str(str(path))
    if suffix in CSV_SUFFIXES:
        delim = ", delim='\\t'" if suffix == ".tsv" else ""
        yield (path.stem, None, f"SELECT * FROM read_csv_auto({literal}, header=true{delim})", None)
    elif suffix in PARQUET_SUFFIXES:
        yield path.stem, None, f"SELECT * FROM read_parquet({literal})", None
    elif suffix in JSON_SUFFIXES:
        yield path.stem, None, f"SELECT * FROM read_json_auto({literal})", None
    elif suffix in XLSX_SUFFIXES:
        import pandas as pd

        # dtype=object: the cells as openpyxl read them (a text cell stays a str, so an
        # identifier like "9007199254740993" is never coerced to a double)
        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl", dtype=object)
        for sheet, frame in sheets.items():
            if frame.empty and len(frame.columns) == 0:
                warnings.append(f"{path.name}: sheet {sheet!r} is empty, skipped")
                continue
            view = f"xlsx_{len(duck.execute('SHOW TABLES').fetchall())}"
            typed, casts, labels = typed_frame(  # by position: labels may collide
                [(frame.columns[i], list(frame.iloc[:, i])) for i in range(frame.shape[1])]
            )
            select_sql = register_typed(duck, view, typed, casts)
            base = path.stem if len(sheets) == 1 else f"{path.stem}_{sheet}"
            yield base, str(sheet), select_sql, labels
    elif suffix in SQLITE_SUFFIXES:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in names:
                cur = conn.execute(f"SELECT * FROM {quote_ident(table)}")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                frame, casts, labels = typed_frame(
                    [(c, [r[i] for r in rows]) for i, c in enumerate(cols)]
                )
                view = f"sqlite_{len(duck.execute('SHOW TABLES').fetchall())}"
                yield table, table, register_typed(duck, view, frame, casts), labels
    else:  # pragma: no cover - discover_files filters
        raise LoadError(f"unsupported file type: {path.name}")


def read_tables(
    paths: list[Path], duck: duckdb.DuckDBPyConnection, warnings: list[str]
) -> list[TablePlan]:
    """Sniff every file with DuckDB and plan normalised, de-duplicated tables."""
    plans: list[TablePlan] = []
    used_tables: list[str] = []
    for path in paths:
        try:
            relations = list(_relations_for_file(duck, Path(path), warnings))
        except Exception as exc:  # noqa: BLE001 - one bad file must not hide the others
            raise LoadError(f"{Path(path).name}: {exc}") from exc
        for base, sheet, select_sql, labels in relations:
            try:
                rel = duck.sql(select_sql)
            except duckdb.Error as exc:
                raise LoadError(f"{Path(path).name}: {exc}") from exc
            table = dedupe_identifiers([*used_tables, normalize_identifier(base, "table")])[-1]
            if table != normalize_identifier(base, "table"):
                warnings.append(f"{Path(path).name}: table name {base!r} renamed to {table!r}")
            used_tables.append(table)
            internals = list(rel.columns)  # what the SQL references (unique)
            originals = labels if labels is not None else internals  # display labels
            names = dedupe_identifiers(normalize_identifier(c) for c in originals)
            columns = []
            exprs = []
            for internal, original, name, duck_type in zip(
                internals, originals, names, rel.types, strict=True
            ):
                pg_type = duckdb_to_pg_type(duck_type)
                columns.append(ColumnPlan(name, original, str(duck_type), pg_type))
                ref = quote_ident(internal)
                exprs.append(f"CAST(to_json({ref}) AS VARCHAR)" if pg_type == "jsonb" else ref)
            if not columns:
                warnings.append(f"{Path(path).name}: no columns, skipped")
                continue
            plans.append(
                TablePlan(
                    name=table,
                    source_file=str(path),
                    sheet=sheet,
                    select_sql=f"SELECT {', '.join(exprs)} FROM ({select_sql}) AS src",
                    columns=columns,
                )
            )
    return plans


# --- writing (Postgres) ------------------------------------------------------------


def table_manifest(plans: list[TablePlan]) -> dict[str, Any]:
    """What each planned table is built from and its column types (the schema contract the
    upstream catalog and semantic layer are compiled from)."""
    return {
        p.name: {
            "source_file": p.source_file,
            "sheet": p.sheet,
            "columns": {c.name: c.pg_type for c in p.columns},
        }
        for p in plans
    }


#: ``format_type`` spellings of the types :func:`duckdb_to_pg_type` emits differently.
_PG_TYPE_ALIASES = {
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "time without time zone": "time",
}


def existing_schema(conn: Any) -> dict[str, Any]:
    """The database's current tables as a manifest (no ``source_file``): the fallback when
    the store has no manifest yet (a source loaded before manifests existed)."""
    rows = conn.execute(
        "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0 "
        "AND NOT a.attisdropped ORDER BY c.relname, a.attnum"
    ).fetchall()
    out: dict[str, Any] = {}
    for table, column, pg_type in rows:
        entry = out.setdefault(table, {"source_file": None, "sheet": None, "columns": {}})
        entry["columns"][column] = _PG_TYPE_ALIASES.get(pg_type, pg_type)
    return out


def diff_manifest(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    """Tables ``added``, ``removed``, and ``changed`` (different column names or types)."""
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(
            t
            for t in set(old) & set(new)
            if dict(old[t].get("columns") or {}) != dict(new[t].get("columns") or {})
        ),
    }


def owned_tables(conn: Any) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
    ]


def create_table_sql(plan: TablePlan) -> str:
    cols = ", ".join(f"{quote_ident(c.name)} {c.pg_type}" for c in plan.columns)
    return f"CREATE TABLE {quote_ident(plan.name)} ({cols})"


def ensure_database(settings: Settings, pg_database: str) -> bool:
    """Create *pg_database* if missing. Returns True when it was created."""
    from autoontology.engine import pg

    assert_source_database(settings, pg_database)
    if pg.database_exists(settings, pg_database):
        return False
    with pg.connect(settings, PG_MAINTENANCE_DB) as conn:
        conn.execute(f"CREATE DATABASE {quote_ident(pg_database)}")
    return True


def write_tables(
    plans: list[TablePlan], duck: duckdb.DuckDBPyConnection, conn: Any
) -> list[TableReport]:
    """Replace each planned table in the Postgres connection and COPY its rows."""
    reports = []
    for plan in plans:
        cols = ", ".join(quote_ident(c.name) for c in plan.columns)
        rows = 0
        with conn.transaction():
            conn.execute(f"DROP TABLE IF EXISTS {quote_ident(plan.name)}")
            conn.execute(create_table_sql(plan))
            result = duck.execute(plan.select_sql)
            with conn.cursor() as cur:
                with cur.copy(f"COPY {quote_ident(plan.name)} ({cols}) FROM STDIN") as copy:
                    while batch := result.fetchmany(COPY_BATCH_ROWS):
                        for row in batch:
                            copy.write_row(row)
                        rows += len(batch)
        reports.append(
            TableReport(
                name=plan.name,
                source_file=plan.source_file,
                sheet=plan.sheet,
                rows=rows,
                columns=[
                    {"name": c.name, "original": c.original, "type": c.pg_type}
                    for c in plan.columns
                ],
            )
        )
    return reports


def load_files_to_postgres(
    paths: list[Path],
    pg_database: str,
    settings: Settings,
    *,
    previous: dict[str, Any] | None = None,
    on_changes: Callable[[dict[str, list[str]]], Any] | None = None,
) -> LoadReport:
    """Load every file into *pg_database* (created if missing); tables are replaced and
    tables no longer produced by *paths* are dropped.

    *previous* is the manifest of the last load (default: the database's current schema).
    When tables were removed or changed, *on_changes(diff)* runs before anything is
    written, so the caller can invalidate what was built on them (raising aborts the load
    with the database untouched).
    """
    from autoontology.engine import pg

    if not paths:
        raise LoadError("no supported files to load")
    report = LoadReport(pg_database=assert_source_database(settings, pg_database))
    duck = duckdb.connect()
    try:
        plans = read_tables(paths, duck, report.warnings)
        report.manifest = table_manifest(plans)
        report.created_database = ensure_database(settings, pg_database)
        assert_source_database(settings, pg_database)  # never clean the catalog's tables
        with pg.connect(settings, pg_database) as conn:
            old = previous if previous is not None else existing_schema(conn)
            report.schema_changes = diff_manifest(old, report.manifest)
            if on_changes is not None and (
                report.schema_changes["removed"] or report.schema_changes["changed"]
            ):
                on_changes(report.schema_changes)
            report.tables = write_tables(plans, duck, conn)
            obsolete = [t for t in owned_tables(conn) if t not in report.manifest]
            with conn.transaction():
                for table in obsolete:
                    conn.execute(f"DROP TABLE IF EXISTS {quote_ident(table)}")
            report.dropped_tables = obsolete
    finally:
        duck.close()
    return report
