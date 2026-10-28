# SPDX-License-Identifier: MIT
"""Invalidating one database's upstream semantic layer when its schema changed.

Upstream's scoped reset (``dal/reset.py:delete_semantic_layer``) deletes a database's
ColumnAttributes and the Terms *only* its tables represent, but deliberately keeps a Term
shared with another database together with its ``table__term`` links. Compilation selects
only tables without a Term (``semantic/pipeline.py`` ``fetch_all_tables_without_term``),
so this database's tables linked to a shared Term would never be recompiled while their
attributes are gone. :func:`reset_database_semantics` therefore also detaches this
database's tables from the Terms that survived (the Term stays for the other databases)
and deletes Terms left without any table (e.g. those of tables a re-ingest removed).

These are upstream DAL internals (``dal.schema``, ``dal.session``), pinned by
``tests/test_upstream_terms.py``; the statements are built separately so they can be
checked without Postgres.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from autoontology.config import Settings

logger = logging.getLogger("autoontology.jobs")


def database_table_ids(s: Any, database_name: str) -> Any:
    """``SELECT catalog_table.id`` of one database's tables (a subquery)."""
    from sqlalchemy import select

    return (
        select(s.catalog_table.c.id)
        .select_from(
            s.catalog_table.join(
                s.catalog_schema, s.catalog_schema.c.id == s.catalog_table.c.schema_id
            ).join(s.catalog_database, s.catalog_database.c.id == s.catalog_schema.c.database_id)
        )
        .where(s.catalog_database.c.name == database_name)
    )


def detach_statement(s: Any, database_name: str) -> Any:
    """Delete the ``table__term`` links of this database's tables (Terms stay)."""
    from sqlalchemy import delete

    return (
        delete(s.table__term)
        .where(s.table__term.c.table_id.in_(database_table_ids(s, database_name)))
        .returning(s.table__term.c.term_id)
    )


def orphan_terms_statement(s: Any, term_ids: Sequence[str]) -> Any:
    """Delete the given Terms that no table represents any more."""
    from sqlalchemy import delete, exists, select

    linked = exists(select(s.table__term.c.term_id).where(s.table__term.c.term_id == s.term.c.id))
    return delete(s.term).where(s.term.c.id.in_(list(term_ids)), ~linked).returning(s.term.c.id)


def stale_attributes_statement(s: Any, table_ids: Sequence[str]) -> Any:
    """Delete the ColumnAttributes of tables that are no longer in the catalog (their
    ``table_id`` is not a foreign key, so nothing cascades)."""
    from sqlalchemy import delete

    return (
        delete(s.column_attribute)
        .where(s.column_attribute.c.table_id.in_(list(table_ids)))
        .returning(s.column_attribute.c.id)
    )


def snapshot_statement(s: Any, database_name: str) -> Any:
    """Rows ``(table_id, table, column, data_type, term_id)`` of one database's catalog."""
    from sqlalchemy import select

    return (
        select(
            s.catalog_table.c.id.label("table_id"),
            s.catalog_table.c.name.label("table"),
            s.catalog_schema.c.name.label("schema"),
            s.catalog_column.c.name.label("column"),
            s.catalog_column.c.data_type,
            s.table__term.c.term_id,
            s.column__foreign_key.c.target_column_id.label("fk_target"),
        )
        .select_from(
            s.catalog_table.join(
                s.catalog_schema, s.catalog_schema.c.id == s.catalog_table.c.schema_id
            )
            .join(s.catalog_database, s.catalog_database.c.id == s.catalog_schema.c.database_id)
            .outerjoin(s.catalog_column, s.catalog_column.c.table_id == s.catalog_table.c.id)
            .outerjoin(s.table__term, s.table__term.c.table_id == s.catalog_table.c.id)
            .outerjoin(
                s.column__foreign_key,
                s.column__foreign_key.c.source_column_id == s.catalog_column.c.id,
            )
        )
        .where(s.catalog_database.c.name == database_name)
    )


def snapshot_from_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``{schema.table: {"id", "columns": {name: type}, "terms": [ids], "fk_columns":
    [declared FK column names]}}`` (a manifest shape)."""
    out: dict[str, Any] = {}
    for r in rows:
        key = f"{r['schema']}.{r['table']}"
        entry = out.setdefault(
            key, {"id": r["table_id"], "columns": {}, "terms": [], "fk_columns": []}
        )
        if r.get("column"):
            entry["columns"][r["column"]] = r.get("data_type") or ""
            if r.get("fk_target") and r["column"] not in entry["fk_columns"]:
                entry["fk_columns"].append(r["column"])
        if r.get("term_id") and r["term_id"] not in entry["terms"]:
            entry["terms"].append(r["term_id"])
    return out


def _dal(settings: Settings) -> tuple[Any, Any, Any]:
    from autoontology.engine.jobs import prepare_upstream_env

    prepare_upstream_env(settings)
    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store, write_transaction

    return s, store, write_transaction


def catalog_snapshot(settings: Settings, database_name: str) -> dict[str, Any]:
    """This database's ingested catalog (tables, column types, linked Terms)."""
    s, store, _ = _dal(settings)
    return snapshot_from_rows(store().query(snapshot_statement(s, database_name)))


def reset_database_semantics(
    settings: Settings,
    database_name: str,
    *,
    orphan_candidates: Sequence[str] = (),
    stale_table_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Make every table of *database_name* recompile: upstream's scoped reset, then detach
    the tables from the shared Terms that survived it, then delete *orphan_candidates*
    (Term ids) no table links any more and the attributes of *stale_table_ids* (tables a
    re-ingest removed). Never with a blank name (``None`` = every DB)."""
    if not database_name.strip():
        raise ValueError("refusing a semantic reset without a database name")
    s, store, write_transaction = _dal(settings)
    from auto_ontology.dal.reset import delete_semantic_layer

    vectors = delete_semantic_layer(database_name)
    with write_transaction():
        detached = store().query_write(detach_statement(s, database_name))
        candidates = {*orphan_candidates, *(r["term_id"] for r in detached)}
        orphans = (
            store().query_write(orphan_terms_statement(s, sorted(candidates))) if candidates else []
        )
        stale = (
            store().query_write(stale_attributes_statement(s, list(stale_table_ids)))
            if stale_table_ids
            else []
        )
    shared = sorted({r["term_id"] for r in detached})
    if shared:
        logger.info(
            "  detached %s from %d Term(s) shared with other databases", database_name, len(shared)
        )
    return {
        "vectors_removed": vectors,
        "detached_terms": shared,
        "orphan_terms_deleted": [r["id"] for r in orphans],
        "stale_attributes_deleted": len(stale),
    }
