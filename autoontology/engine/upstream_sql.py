# SPDX-License-Identifier: MIT
"""Restore catalog table lineage for upstream's column-free SELECT queries.

The pinned extractor discards tables with no referenced columns, so COUNT(*) is
misclassified as having no known table. Keep SQL unchanged and narrowly recover
one unambiguous catalog table when there are no column references to validate.
"""

from __future__ import annotations

from functools import wraps


def install() -> None:
    from auto_ontology.catalog import sql_parse
    from auto_ontology.catalog.parsers.sqlglot_extractor import TableMatch
    from sqlglot import exp, parse

    original = sql_parse.extract_tables_and_columns
    if getattr(original, "_autoontology_table_lineage", False):
        return

    @wraps(original)
    def extract(*, sql, dialects, all_schemas=None):
        result = original(sql=sql, dialects=dialects, all_schemas=all_schemas)
        if result.tables:
            return result
        statements = parse(sql, read=result.dialect_used or None)
        if len(statements) != 1:
            return result
        statement = statements[0]
        if (
            not isinstance(statement, exp.Select)
            or statement.args.get("with_")
            or statement.find(exp.Column)
            or statement.find(exp.Subquery)
        ):
            return result
        tables = list(statement.find_all(exp.Table))
        if len(tables) != 1:
            return result
        table = tables[0]
        matches = [
            (name, schema)
            for name, schema in (all_schemas or {}).items()
            if (not table.db or name.casefold() == table.db.casefold())
            and schema.table_exists(table.name.lower())
            and (
                not table.catalog
                or schema.get_database_name().casefold() == table.catalog.casefold()
            )
        ]
        if len(matches) == 1:
            name, _schema = matches[0]
            result.tables[f"{name}.{table.name.lower()}"] = TableMatch(schema_name=name)
        return result

    extract._autoontology_table_lineage = True
    sql_parse.extract_tables_and_columns = extract
