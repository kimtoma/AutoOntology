# SPDX-License-Identifier: MIT
"""Parse and qualify one read query against a frozen, approved column projection."""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

MAX_SQL_CHARS = 16000
MAX_ROWS = 500
MAX_RESULT_BYTES = 4 * 1024 * 1024
STATEMENT_SECONDS = 10
WORKER_SECONDS = 20


class SqlError(Exception):
    def __init__(self, message, code="sql_not_allowed", status=409):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


# Deliberately excludes filesystem/network/system/session functions and UDFs.
FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "COUNT",
        "SUM",
        "MIN",
        "MAX",
        "COALESCE",
        "NULLIF",
        "CAST",
        "TRY_CAST",
        "ROUND",
        "CEIL",
        "FLOOR",
        "POWER",
        "SQRT",
        "MOD",
        "LOWER",
        "UPPER",
        "LENGTH",
        "CHAR_LENGTH",
        "TRIM",
        "LTRIM",
        "RTRIM",
        "SUBSTRING",
        "CONCAT",
        "CONCAT_WS",
        "REPLACE",
        "LEFT",
        "RIGHT",
        "DATE",
        "DATE_TRUNC",
        "TIMESTAMP_TRUNC",
        "EXTRACT",
        "YEAR",
        "MONTH",
        "DAY",
        "DATE_ADD",
        "DATE_SUB",
        "DATEDIFF",
        "DATE_DIFF",
        "TO_CHAR",
        "TO_DATE",
        "TO_TIMESTAMP",
        "CURRENT_DATE",
        "CURRENT_TIMESTAMP",
        "ROW_NUMBER",
        "RANK",
        "DENSE_RANK",
        "LAG",
        "LEAD",
        "FIRST_VALUE",
        "LAST_VALUE",
        "NTILE",
        "PERCENTILE_CONT",
        "PERCENTILE_DISC",
        "STDDEV",
        "STDDEV_POP",
        "STDDEV_SAMP",
        "VARIANCE",
        "STRING_AGG",
        "GROUP_CONCAT",
        "ARRAY_AGG",
        "IF",
        "CASE",
        "GREATEST",
        "LEAST",
        "IFNULL",
    }
)
BLOCKED = tuple(
    getattr(exp, name)
    for name in (
        "DML",
        "DDL",
        "Command",
        "Into",
        "Lock",
        "Transaction",
        "Parameter",
        "Placeholder",
        "SessionParameter",
        "Hint",
        "TableSample",
        "Use",
        "Pragma",
        "Copy",
        "Execute",
        "Operator",
        "ObjectIdentifier",
    )
    if hasattr(exp, name)
)


def validate_sql(sql: str, catalog: dict) -> str:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise SqlError("Provide one SQL query within the supported size limit.", "sql_invalid", 400)
    dialect = catalog["dialect"]
    if dialect not in ("postgres", "mysql", "snowflake"):
        raise SqlError(
            "This source dialect has no managed query adapter.", "sql_dialect_unavailable"
        )
    try:
        statements = parse(sql, read=dialect)
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            raise ValueError("not one read query")
        tree = statements[0]
        for node in tree.walk():
            if isinstance(node, BLOCKED):
                raise ValueError("side-effect or session expression")
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise ValueError("recursive query")
            if any(comment.lstrip().startswith("+") for comment in node.comments or []):
                raise ValueError("optimizer/session hint")
            if isinstance(node, exp.DataType) and str(node.this).endswith("USERDEFINED"):
                raise ValueError("custom type")
            if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
                raise ValueError("qualified function")
            if isinstance(node, exp.Func):
                name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                if name.upper() not in FUNCTIONS:
                    raise ValueError("unsupported function")
        default_schema = (
            next(iter({t["schema"] for t in catalog["tables"]}))
            if len({t["schema"] for t in catalog["tables"]}) == 1
            else None
        )
        schemas = {}
        for table in catalog["tables"]:
            schemas.setdefault(table["schema"], {})[table["name"]] = {
                name: "TEXT" for name in table["columns"]
            }
        database = catalog["database"]
        for table in tree.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise ValueError("table function")
            if table.catalog:
                if table.catalog != database:
                    raise ValueError("different database")
                if dialect != "snowflake":
                    table.set("catalog", None)
        mapping = {database: schemas} if dialect == "snowflake" else schemas
        tree = qualify(
            tree,
            dialect=dialect,
            db=default_schema,
            catalog=database if dialect == "snowflake" else None,
            schema=MappingSchema(mapping, dialect=dialect, normalize=False),
            allow_partial_qualification=False,
            validate_qualify_columns=True,
            quote_identifiers=True,
            identify=True,
        )
        if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
            raise ValueError("unresolved wildcard")
        allowed = {(t["schema"], t["name"]) for t in catalog["tables"]}
        physical = 0
        for scope in traverse_scope(tree):
            for _, source in scope.sources.items():
                if isinstance(source, Scope):
                    continue
                if not isinstance(source, exp.Table) or (source.db, source.name) not in allowed:
                    raise ValueError("unpublished table")
                physical += 1
        if not physical:
            raise ValueError("no published table")
        return tree.sql(dialect=dialect, comments=False)
    except SqlError:
        raise
    except Exception:
        raise SqlError(
            "The query uses unsupported SQL or a table, column, function or type outside this publication. Refine the question or review the source first."
        ) from None
