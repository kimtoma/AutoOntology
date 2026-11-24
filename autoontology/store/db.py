# SPDX-License-Identifier: MIT
"""SQLite job/source store at ``<home>/autoontology.db`` (stdlib ``sqlite3``, WAL mode).

The schema is a list of numbered migrations applied in order and recorded in
``schema_version`` (v1 is the whole initial schema). Later changes append to
:data:`MIGRATIONS`; never edit a migration that has shipped.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from autoontology.playground.store import PlaygroundStore
    from autoontology.store.actions import ActionRuns
    from autoontology.store.agent_tokens import AgentTokens
    from autoontology.store.decisions import DecisionStore
    from autoontology.store.logic import LogicDefs
    from autoontology.store.objects import ObjectEdits, ObjectTypes
    from autoontology.store.workflows import WorkflowRuns

SourceKind = Literal["files", "database", "documents"]
RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
#: Step statuses are free text in the schema; these are the ones AutoOntology writes.
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
ACTIVE_RUN_STATUSES: tuple[str, ...] = ("pending", "running")

#: A migration is SQL (statements separated by ``;``) or a function run on the migrating
#: connection inside the migration's transaction (``PRAGMA foreign_keys`` is OFF there).
Migration = str | Callable[[sqlite3.Connection], None]

MIGRATIONS: list[tuple[int, Migration]] = [
    (
        1,
        # Schema v1. sources/runs/steps: registered sources and their runs. documents ..
        # evidence: the documents pipeline. term_embeddings/alignments/alignment_previews:
        # alignment of document concepts to upstream Terms (previews = `--dry-run`
        # verdicts, never accepted; proposal_json keeps a `same` verdict the identity
        # policy refused, for review). source_leases: cross-process reservation of a source
        # name. file_manifests: the input -> table manifest of the last load.
        # ontology_revision: bumped whenever a run finishes or a source is removed (cache
        # key of derived views; replaced by snapshots in v2). semantic_baselines: the catalog schema the semantic layer
        # was compiled from plus a pending invalidation. playground_*: the element index,
        # conversations and messages (incomplete = the stream ended without [DONE]).
        # term_alias_additions: synonyms AutoOntology wrote to upstream Terms;
        # term_refresh_pending: Terms whose embedding refresh is still owed.
        """
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK (kind IN ('files', 'database', 'documents')),
            locator TEXT NOT NULL,
            pg_database TEXT,
            catalog_database_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source_id TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            params_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS runs_source_idx ON runs (source_id, started_at);
        CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (run_id, name)
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            mime TEXT NOT NULL,
            engine TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            pages INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (source_id, sha256)
        );
        CREATE INDEX IF NOT EXISTS documents_path_idx ON documents (source_id, path);
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            idx INTEGER NOT NULL,
            section_path TEXT NOT NULL DEFAULT '',
            page INTEGER,
            kind TEXT NOT NULL DEFAULT 'text',
            text TEXT NOT NULL,
            tokens INTEGER NOT NULL DEFAULT 0,
            extraction_json TEXT,
            extract_detail_json TEXT,
            extracted_at TEXT,
            UNIQUE (doc_id, idx)
        );
        CREATE TABLE IF NOT EXISTS concepts (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            name_en TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            definition TEXT NOT NULL DEFAULT '',
            synonyms_json TEXT NOT NULL DEFAULT '[]',
            attributes_json TEXT NOT NULL DEFAULT '[]',
            merged_from_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE (source_id, name)
        );
        CREATE TABLE IF NOT EXISTS relations (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            subject_concept_id TEXT NOT NULL REFERENCES concepts (id) ON DELETE CASCADE,
            predicate TEXT NOT NULL,
            object_concept_id TEXT NOT NULL REFERENCES concepts (id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT '',
            UNIQUE (source_id, subject_concept_id, predicate, object_concept_id)
        );
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY,
            concept_id TEXT REFERENCES concepts (id) ON DELETE CASCADE,
            relation_id TEXT REFERENCES relations (id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
            quote TEXT NOT NULL,
            verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
            CHECK ((concept_id IS NULL) <> (relation_id IS NULL))
        );
        CREATE INDEX IF NOT EXISTS evidence_concept_idx ON evidence (concept_id);
        CREATE INDEX IF NOT EXISTS evidence_relation_idx ON evidence (relation_id);
        CREATE TABLE IF NOT EXISTS term_embeddings (
            term_id TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (term_id, text_sha256, model)
        );
        CREATE TABLE IF NOT EXISTS alignments (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            concept_id TEXT NOT NULL REFERENCES concepts (id) ON DELETE CASCADE,
            concept_name TEXT NOT NULL,
            structured_source TEXT NOT NULL DEFAULT '',
            term_id TEXT NOT NULL DEFAULT '',
            term_name TEXT NOT NULL DEFAULT '',
            relation TEXT NOT NULL CHECK (relation IN
                ('same', 'broader', 'narrower', 'related', 'none', 'skipped')),
            similarity REAL,
            confidence REAL,
            rationale TEXT NOT NULL DEFAULT '',
            accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0, 1)),
            judge_key TEXT NOT NULL DEFAULT '',
            candidates_json TEXT NOT NULL DEFAULT '[]',
            applied_json TEXT NOT NULL DEFAULT '{}',
            run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0, 1)),
            proposal_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (concept_id, term_id)
        );
        CREATE INDEX IF NOT EXISTS alignments_source_idx ON alignments (source_id);
        CREATE TABLE IF NOT EXISTS source_leases (
            source_name TEXT PRIMARY KEY,
            token TEXT NOT NULL,
            operation TEXT NOT NULL,
            pid INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS file_manifests (
            source_id TEXT PRIMARY KEY REFERENCES sources (id) ON DELETE CASCADE,
            manifest_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alignment_previews (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            concept_id TEXT NOT NULL REFERENCES concepts (id) ON DELETE CASCADE,
            concept_name TEXT NOT NULL,
            structured_source TEXT NOT NULL DEFAULT '',
            term_id TEXT NOT NULL DEFAULT '',
            term_name TEXT NOT NULL DEFAULT '',
            relation TEXT NOT NULL CHECK (relation IN
                ('same', 'broader', 'narrower', 'related', 'none', 'skipped')),
            similarity REAL,
            confidence REAL,
            rationale TEXT NOT NULL DEFAULT '',
            accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted = 0),
            dry_run INTEGER NOT NULL DEFAULT 1 CHECK (dry_run = 1),
            judge_key TEXT NOT NULL DEFAULT '',
            candidates_json TEXT NOT NULL DEFAULT '[]',
            applied_json TEXT NOT NULL DEFAULT '{}',
            run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            proposal_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (concept_id)
        );
        CREATE INDEX IF NOT EXISTS alignment_previews_source_idx
            ON alignment_previews (source_id);
        CREATE TABLE IF NOT EXISTS ontology_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            value INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO ontology_revision (id, value) VALUES (1, 0);
        CREATE TABLE IF NOT EXISTS semantic_baselines (
            source_id TEXT PRIMARY KEY REFERENCES sources (id) ON DELETE CASCADE,
            baseline_json TEXT,
            pending_json TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playground_index (
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            element_id TEXT NOT NULL,
            element_type TEXT NOT NULL,
            text_sha TEXT NOT NULL,
            embedding BLOB,
            model TEXT NOT NULL,
            PRIMARY KEY (source_id, element_id)
        );
        CREATE TABLE IF NOT EXISTS playground_index_meta (
            source_id TEXT PRIMARY KEY REFERENCES sources (id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            model TEXT NOT NULL,
            elements INTEGER NOT NULL,
            built_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playground_conversations (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS playground_conversations_source
            ON playground_conversations (source_id, updated_at);
        CREATE TABLE IF NOT EXISTS playground_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL
                REFERENCES playground_conversations (id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            retrieved_json TEXT NOT NULL DEFAULT '[]',
            citations_json TEXT NOT NULL DEFAULT '{}',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            incomplete INTEGER NOT NULL DEFAULT 0 CHECK (incomplete IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS playground_messages_conversation
            ON playground_messages (conversation_id, id);
        CREATE TABLE IF NOT EXISTS term_alias_additions (
            term_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            concepts_json TEXT NOT NULL DEFAULT '[]',
            added_at TEXT NOT NULL,
            removed_at TEXT,
            PRIMARY KEY (term_id, alias)
        );
        CREATE TABLE IF NOT EXISTS term_refresh_pending (
            term_id TEXT PRIMARY KEY,
            reason TEXT NOT NULL DEFAULT '',
            since TEXT NOT NULL
        )
        """,
    ),
    (
        2,
        # Schema v2: the unified ontology. ontology_snapshots: one row per `build` (its
        # `snapshot` step): the merged model as JSON plus its sources and counts; the
        # revision is the cache key of every derived view (graph, playground index, SPARQL
        # graph), replacing ontology_revision. The playground tables are keyed by the
        # workspace scope instead of a source (the playground reads the latest snapshot).
        """
        CREATE TABLE IF NOT EXISTS ontology_snapshots (
            revision INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]',
            counts_json TEXT NOT NULL DEFAULT '{}',
            model_json TEXT NOT NULL
        );
        DROP TABLE IF EXISTS ontology_revision;
        DROP TABLE IF EXISTS playground_messages;
        DROP TABLE IF EXISTS playground_conversations;
        DROP TABLE IF EXISTS playground_index_meta;
        DROP TABLE IF EXISTS playground_index;
        CREATE TABLE playground_index (
            scope TEXT NOT NULL,
            element_id TEXT NOT NULL,
            element_type TEXT NOT NULL,
            text_sha TEXT NOT NULL,
            embedding BLOB,
            model TEXT NOT NULL,
            PRIMARY KEY (scope, element_id)
        );
        CREATE TABLE playground_index_meta (
            scope TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            model TEXT NOT NULL,
            elements INTEGER NOT NULL,
            built_at TEXT NOT NULL
        );
        CREATE TABLE playground_conversations (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX playground_conversations_scope
            ON playground_conversations (scope, updated_at);
        CREATE TABLE playground_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL
                REFERENCES playground_conversations (id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            retrieved_json TEXT NOT NULL DEFAULT '[]',
            citations_json TEXT NOT NULL DEFAULT '{}',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            incomplete INTEGER NOT NULL DEFAULT 0 CHECK (incomplete IN (0, 1))
        );
        CREATE INDEX playground_messages_conversation
            ON playground_messages (conversation_id, id)
        """,
    ),
    (
        3,
        # Schema v3: term_alias_additions.source_id = the documents source whose alignment
        # wrote the synonym (a unified build run has no source of its own, so the run's
        # source_id cannot say it). NULL on older rows: provenance falls back to the run.
        """
        ALTER TABLE term_alias_additions ADD COLUMN source_id TEXT
        """,
    ),
    (
        4,
        # Schema v4: term_legacy_descriptions = Terms whose description an older apply set
        # without recording the text. The judge step confirms each one's baseline (the
        # description its first judge call saw); apply never writes this table, so the
        # state survives an apply that replaces the old record's applied_json. A baseline
        # confirmed under v3 (applied_json.description_baseline, its text) moves over as
        # confirmed; a record without the text is noted unconfirmed.
        """
        CREATE TABLE IF NOT EXISTS term_legacy_descriptions (
            term_id TEXT PRIMARY KEY,
            noted_at TEXT NOT NULL,
            baseline TEXT,
            confirmed_at TEXT
        );
        INSERT INTO term_legacy_descriptions (term_id, noted_at, baseline, confirmed_at)
            SELECT json_extract(applied_json, '$.term_id'), datetime('now'),
                   max(trim(COALESCE(json_extract(applied_json, '$.description'), ''))),
                   datetime('now')
            FROM alignments
            WHERE json_valid(applied_json)
              AND json_extract(applied_json, '$.description_set')
              AND json_extract(applied_json, '$.description_baseline')
              AND json_extract(applied_json, '$.term_id') IS NOT NULL
            GROUP BY json_extract(applied_json, '$.term_id')
        ON CONFLICT (term_id) DO NOTHING;
        INSERT INTO term_legacy_descriptions (term_id, noted_at)
            SELECT DISTINCT json_extract(applied_json, '$.term_id'), datetime('now')
            FROM alignments
            WHERE json_valid(applied_json)
              AND json_extract(applied_json, '$.description_set')
              AND json_extract(applied_json, '$.term_id') IS NOT NULL
              AND trim(COALESCE(json_extract(applied_json, '$.description'), '')) = ''
        ON CONFLICT (term_id) DO NOTHING
        """,
    ),
    (
        5,
        # Schema v5: documents.reader_fingerprint = the reader version and the settings its
        # output depends on (table limits, byte cap, OCR readiness, vision model) when the
        # document was read. A cached read is reused only while it matches; '' on older
        # rows (read again once, kept as is when the chunks come out the same).
        """
        ALTER TABLE documents ADD COLUMN reader_fingerprint TEXT NOT NULL DEFAULT ''
        """,
    ),
    (
        6,
        # Schema v6: review_decisions = a person's decision on a review item (an alignment
        # verdict, a rejected alias, an unaligned concept), one per (kind, subject_key); it
        # overrides the align judge on every later pass and is reflected in the next
        # snapshot by an `apply` run (applied_revision). alignments.judged_json = the
        # judge's verdict a decision replaced on that row, restored when the decision is
        # removed ('{}' on rows no decision touched); alignments_review_idx = the rows the
        # pending review work is read from (decided, or with an upstream write recorded).
        """
        CREATE TABLE IF NOT EXISTS review_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK (kind IN ('alignment', 'alias', 'concept')),
            subject_key TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject', 'acknowledge')),
            payload_json TEXT NOT NULL DEFAULT '{}',
            note TEXT NOT NULL DEFAULT '',
            decided_by TEXT NOT NULL DEFAULT 'local',
            decided_at TEXT NOT NULL,
            applied_revision INTEGER,
            UNIQUE (kind, subject_key)
        );
        ALTER TABLE alignments ADD COLUMN judged_json TEXT NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS alignments_review_idx ON alignments (concept_id)
            WHERE judged_json != '{}' OR applied_json != '{}'
        """,
    ),
    (
        7,
        # Schema v7: outside-agent access (``autoontology/store/agent_tokens.py``).
        # agent_tokens = bearer tokens for MCP and /agent/v1: only the SHA-256 of the token
        # is kept (NULL once revoked: the row stays for the history); revision_pin NULL =
        # follow the latest snapshot. agent_calls = one row per tool call (pruned to the
        # newest rows), surface 'mcp' or 'rest'.
        """
        CREATE TABLE IF NOT EXISTS agent_tokens (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token_hash TEXT UNIQUE,
            revision_pin INTEGER,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            token_id TEXT NOT NULL,
            surface TEXT NOT NULL CHECK (surface IN ('mcp', 'rest')),
            tool TEXT NOT NULL,
            ok INTEGER NOT NULL,
            ms INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS agent_calls_token_idx ON agent_calls (token_id)
        """,
    ),
    # Schema v8: projects (:func:`_migrate_v8`, a Python migration: it rebuilds tables).
    (8, lambda conn: _migrate_v8(conn)),
    # Schema v9: accounts (:func:`_migrate_v9`; ``autoontology/store/accounts.py``).
    (9, lambda conn: _migrate_v9(conn)),
    # Schema v10: the actionable ontology (:func:`_migrate_v10`; ``autoontology/objects/``,
    # ``autoontology/store/objects.py``).
    (10, lambda conn: _migrate_v10(conn)),
]

#: Indexes a store may lack although it reached their schema version (added to a migration
#: before it was released): created on open, ``(min version, statement)``.
ENSURED_INDEXES: list[tuple[int, str]] = [
    (
        6,
        "CREATE INDEX IF NOT EXISTS alignments_review_idx ON alignments (concept_id) "
        "WHERE judged_json != '{}' OR applied_json != '{}'",
    ),
]


#: The project every pre-v8 row belongs to (its slug starts as ``default`` and becomes the
#: workspace's slug the first time the app opens the store, see :meth:`Store.__init__`).
DEFAULT_PROJECT_ID = "prj_default"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
#: Slugs a project may not take: ``r<4+ digits>`` is the name of a legacy (pre-projects)
#: revision folder in the exports root (``export_job.LEGACY_ENTRY``).
RESERVED_SLUG_RE = re.compile(r"r\d{4,}")
SLUG_MAX = 24
#: store_meta keys: the default project's id; set while the default project still waits for
#: its workspace slug (removed once it got one).
META_DEFAULT_PROJECT = "default_project_id"
META_SLUG_PENDING = "default_project_slug_pending"


def project_slug_from_workspace(workspace: str) -> str:
    """A valid project slug for a workspace name: lower-cased, runs of other characters
    folded to ``-``, ``p-`` in front of a leading digit, cut to 24 characters; ``default``
    when nothing usable is left."""
    slug = re.sub(r"[^a-z0-9]+", "-", (workspace or "").strip().lower()).strip("-")
    if slug and not slug[0].isalpha():
        slug = "p-" + slug
    if RESERVED_SLUG_RE.fullmatch(slug):
        slug = "p-" + slug
    slug = slug[:SLUG_MAX].rstrip("-")
    return slug if slug_allowed(slug) else "default"


def slug_allowed(slug: str) -> bool:
    """True for a valid, unreserved project slug."""
    return bool(SLUG_RE.fullmatch(slug)) and not RESERVED_SLUG_RE.fullmatch(slug)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _sequence(conn: sqlite3.Connection, table: str) -> int | None:
    """The AUTOINCREMENT sequence of *table* (None: no AUTOINCREMENT table has one yet)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if not exists:
        return None
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
    return int(row[0]) if row else None


def _rebuild(
    conn: sqlite3.Connection,
    table: str,
    create: str,
    *,
    project_expr: str,
    extra: dict[str, str] | None = None,
    indexes: tuple[str, ...] = (),
) -> None:
    """Rebuild *table* as *create* (``{t}`` = the table name): create ``<table>__v8``, copy
    every column the two shapes share (``project_id`` from the old row when it has one,
    else *project_expr*; *extra* = other new columns' expressions when the old row lacks
    them), drop the old table, rename the copy, recreate *indexes*. Row counts are checked.
    Child tables keep referencing the table by name, so their foreign keys stay valid; the
    caller runs with ``PRAGMA foreign_keys = OFF`` so the drop cascades nothing."""
    tmp = f"{table}__v8"
    old = _columns(conn, table)
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(create.replace("{t}", tmp))
    new = _columns(conn, tmp)
    exprs = dict(extra or {})
    exprs["project_id"] = (
        f"COALESCE(project_id, {project_expr})" if "project_id" in old else project_expr
    )
    cols, values = [], []
    for col in new:
        if col in old and col != "project_id":
            cols.append(col)
            values.append(col)
        elif col in exprs:
            cols.append(col)
            values.append(exprs[col])
    before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    conn.execute(f"INSERT INTO {tmp} ({', '.join(cols)}) SELECT {', '.join(values)} FROM {table}")
    after = conn.execute(f"SELECT count(*) FROM {tmp}").fetchone()[0]
    if after != before:
        raise RuntimeError(f"migration v8: {table} kept {after} of {before} rows")
    # AUTOINCREMENT: never hand out an id again that the old table already used (its
    # highest row may have been deleted, so max(id) of the copy can be below its sequence)
    old_seq = _sequence(conn, table)
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    if old_seq is not None and old_seq > (_sequence(conn, table) or 0):
        updated = conn.execute(
            "UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (old_seq, table)
        ).rowcount
        if not updated:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, old_seq))
    for statement in indexes:
        conn.execute(statement)


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """Schema v8: projects.

    ``projects`` holds the projects (slug ``^[a-z][a-z0-9-]{1,23}$``, checked in Python);
    ``store_meta`` names the default project (:data:`DEFAULT_PROJECT_ID`), which every
    earlier row is backfilled to. Its slug starts as ``default`` with a pending mark that
    :meth:`Store.__init__` resolves to the workspace's slug the first time the store is
    opened with one (the migration itself has no settings).

    ``project_id`` (NOT NULL, references ``projects``) is added by rebuilding: ``sources``
    (UNIQUE ``(project_id, name)``), ``runs`` (a run of a source takes the source's
    project), ``review_decisions`` (UNIQUE ``(project_id, kind, subject_key)``),
    ``agent_tokens``, ``source_leases`` (key ``(project_id, source_name)``) and the three
    playground tables, which keep their ``scope`` column as a key inside the project.
    ``ontology_snapshots`` gets a store-wide surrogate ``id`` and a per-project
    ``revision`` (UNIQUE ``(project_id, revision)``); existing rows keep ``id = revision =``
    their old revision, so every stored revision number (``review_decisions.applied_revision``,
    ``agent_tokens.revision_pin``, ``playground_index_meta.revision``, run params, export
    folders) keeps meaning the same snapshot, now read as (the row's project, revision).

    Safe to re-run over tables that already have the v8 shape (a downgraded
    ``schema_version``): columns present are copied, never reset."""
    default = repr(DEFAULT_PROJECT_ID)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, "
        "name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
        "archived_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    inserted = conn.execute(
        "INSERT OR IGNORE INTO projects (id, slug, name, description, created_at) "
        "VALUES (?, 'default', 'default', '', ?)",
        (DEFAULT_PROJECT_ID, now_iso()),
    ).rowcount
    conn.execute(
        "INSERT OR IGNORE INTO store_meta (key, value) VALUES (?, ?)",
        (META_DEFAULT_PROJECT, DEFAULT_PROJECT_ID),
    )
    if inserted:
        conn.execute(
            "INSERT OR IGNORE INTO store_meta (key, value) VALUES (?, '1')", (META_SLUG_PENDING,)
        )
    ref = "project_id TEXT NOT NULL REFERENCES projects (id)"
    _rebuild(
        conn,
        "sources",
        f"""CREATE TABLE {{t}} (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('files', 'database', 'documents')),
            locator TEXT NOT NULL,
            pg_database TEXT,
            catalog_database_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            {ref},
            UNIQUE (project_id, name)
        )""",
        project_expr=default,
    )
    _rebuild(
        conn,
        "runs",
        f"""CREATE TABLE {{t}} (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source_id TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            params_json TEXT NOT NULL DEFAULT '{{}}',
            {ref}
        )""",
        project_expr=(
            f"COALESCE((SELECT s.project_id FROM sources s WHERE s.id = runs.source_id), {default})"
        ),
        indexes=(
            "CREATE INDEX runs_source_idx ON runs (source_id, started_at)",
            "CREATE INDEX runs_project_idx ON runs (project_id, kind, started_at)",
        ),
    )
    _rebuild(
        conn,
        "review_decisions",
        f"""CREATE TABLE {{t}} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK (kind IN ('alignment', 'alias', 'concept')),
            subject_key TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject', 'acknowledge')),
            payload_json TEXT NOT NULL DEFAULT '{{}}',
            note TEXT NOT NULL DEFAULT '',
            decided_by TEXT NOT NULL DEFAULT 'local',
            decided_at TEXT NOT NULL,
            applied_revision INTEGER,
            {ref},
            UNIQUE (project_id, kind, subject_key)
        )""",
        project_expr=default,
    )
    _rebuild(
        conn,
        "agent_tokens",
        f"""CREATE TABLE {{t}} (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token_hash TEXT UNIQUE,
            revision_pin INTEGER,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT,
            {ref}
        )""",
        project_expr=default,
        indexes=("CREATE INDEX agent_tokens_project_idx ON agent_tokens (project_id)",),
    )
    _rebuild(
        conn,
        "source_leases",
        f"""CREATE TABLE {{t}} (
            {ref},
            source_name TEXT NOT NULL,
            token TEXT NOT NULL,
            operation TEXT NOT NULL,
            pid INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at REAL NOT NULL,
            PRIMARY KEY (project_id, source_name)
        )""",
        project_expr=default,
    )
    _rebuild(
        conn,
        "playground_index",
        f"""CREATE TABLE {{t}} (
            {ref},
            scope TEXT NOT NULL,
            element_id TEXT NOT NULL,
            element_type TEXT NOT NULL,
            text_sha TEXT NOT NULL,
            embedding BLOB,
            model TEXT NOT NULL,
            PRIMARY KEY (project_id, scope, element_id)
        )""",
        project_expr=default,
    )
    _rebuild(
        conn,
        "playground_index_meta",
        f"""CREATE TABLE {{t}} (
            {ref},
            scope TEXT NOT NULL,
            revision INTEGER NOT NULL,
            model TEXT NOT NULL,
            elements INTEGER NOT NULL,
            built_at TEXT NOT NULL,
            PRIMARY KEY (project_id, scope)
        )""",
        project_expr=default,
    )
    _rebuild(
        conn,
        "playground_conversations",
        f"""CREATE TABLE {{t}} (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            {ref}
        )""",
        project_expr=default,
        indexes=(
            "CREATE INDEX playground_conversations_scope "
            "ON playground_conversations (project_id, scope, updated_at)",
        ),
    )
    _rebuild(
        conn,
        "ontology_snapshots",
        f"""CREATE TABLE {{t}} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {ref},
            revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]',
            counts_json TEXT NOT NULL DEFAULT '{{}}',
            model_json TEXT NOT NULL,
            UNIQUE (project_id, revision)
        )""",
        project_expr=default,
        extra={"id": "revision"},  # pre-v8 rows: surrogate id = the old global revision
    )
    rebuilt = (
        "sources",
        "runs",
        "review_decisions",
        "agent_tokens",
        "source_leases",
        "playground_index",
        "playground_index_meta",
        "playground_conversations",
        "playground_messages",
        "ontology_snapshots",
    )
    for table in rebuilt:
        bad = conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
        if bad:
            raise RuntimeError(f"migration v8: {table} has dangling references: {bad[:3]}")


#: Schema v9 tables (every statement is idempotent: a downgraded ``schema_version`` re-runs
#: the migration without touching rows).
_V9_STATEMENTS = (
    # users: email is unique without regard to case (stored lower-cased); pw_hash NULL =
    # no password yet (invited, or magic links only); disabled_at set = refused everywhere.
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL DEFAULT '',
        pw_hash TEXT,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        disabled_at TEXT,
        last_login_at TEXT
    )""",
    # sessions: the cookie holds a random id, the row only its SHA-256 (hex).
    """CREATE TABLE IF NOT EXISTS sessions (
        id_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at)",
    # login_tokens: single-use links (magic login, invitation, password reset), SHA-256 only.
    """CREATE TABLE IF NOT EXISTS login_tokens (
        id_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK (purpose IN ('magic', 'invite', 'reset')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS login_tokens_user_idx ON login_tokens (user_id, purpose)",
    """CREATE TABLE IF NOT EXISTS memberships (
        project_id TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'reviewer', 'viewer')),
        created_at TEXT NOT NULL,
        PRIMARY KEY (project_id, user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS memberships_user_idx ON memberships (user_id)",
    # audit_log: no foreign keys -- the history outlives the users and projects it names.
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        user_id TEXT,
        project_id TEXT,
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}',
        ip TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS audit_log_project_idx ON audit_log (project_id, id)",
    "CREATE INDEX IF NOT EXISTS audit_log_user_idx ON audit_log (user_id, id)",
)


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """Schema v9: accounts. ``users``, ``sessions``, ``login_tokens``, ``memberships``
    (a user's role in a project), ``audit_log``, and ``agent_tokens.created_by`` (the user
    who issued a token; NULL for the tokens from before accounts). Existing rows are not
    touched; safe to re-run."""
    for statement in _V9_STATEMENTS:
        conn.execute(statement)
    if "created_by" not in _columns(conn, "agent_tokens"):
        conn.execute("ALTER TABLE agent_tokens ADD COLUMN created_by TEXT")


#: Schema v10 tables: the actionable ontology. Every table is per project and every
#: statement is idempotent (a downgraded ``schema_version`` re-runs the migration).
_V10_STATEMENTS = (
    # object_types: a class of the snapshot bound to one source table (key and title
    # columns, links from foreign-key properties). ``proposed`` (inferred, waiting in the
    # review inbox) -> ``active`` (accepted) | ``retired`` (rejected); an active type whose
    # class, table or columns are gone is ``broken`` (broken_reason) until they are back.
    # columns_json = the table's columns [{name, type}] as last read; key_origin = pk |
    # inferred | manual; key_checked = the key was verified unique and non-null on the
    # source; validated_revision = the snapshot it was last checked against.
    """CREATE TABLE IF NOT EXISTS object_types (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id TEXT PRIMARY KEY,
        class_local TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        source_name TEXT NOT NULL DEFAULT '',
        schema_name TEXT NOT NULL DEFAULT '',
        table_name TEXT NOT NULL DEFAULT '',
        key_columns_json TEXT NOT NULL DEFAULT '[]',
        title_column TEXT NOT NULL DEFAULT '',
        links_json TEXT NOT NULL DEFAULT '[]',
        columns_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'active', 'broken', 'retired')),
        broken_reason TEXT NOT NULL DEFAULT '',
        key_origin TEXT NOT NULL DEFAULT 'inferred'
            CHECK (key_origin IN ('pk', 'inferred', 'manual')),
        key_checked INTEGER NOT NULL DEFAULT 0,
        validated_revision INTEGER,
        decided_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, class_local)
    )""",
    # logic_defs: function / action / workflow definitions (spec_json), versioned
    # (logic_def_history keeps every version), governed draft -> active -> retired, and
    # broken while a reference of the spec is missing from the ontology.
    """CREATE TABLE IF NOT EXISTS logic_defs (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('function', 'action', 'workflow')),
        name TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'active', 'retired', 'broken')),
        spec_json TEXT NOT NULL DEFAULT '{}',
        origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'extracted')),
        evidence_json TEXT NOT NULL DEFAULT '[]',
        broken_reason TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, kind, name)
    )""",
    """CREATE TABLE IF NOT EXISTS logic_def_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects (id),
        def_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        spec_json TEXT NOT NULL,
        status TEXT NOT NULL,
        changed_by TEXT NOT NULL DEFAULT '',
        changed_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS logic_def_history_def_idx ON logic_def_history (def_id, version)",
    # logic_candidates: definitions a `logic` run extracted from documents (errors_json:
    # what failed validation; evidence_json: the quotes they rest on).
    """CREATE TABLE IF NOT EXISTS logic_candidates (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL CHECK (kind IN ('function', 'action', 'workflow')),
        name TEXT NOT NULL,
        spec_json TEXT NOT NULL DEFAULT '{}',
        errors_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL,
        source_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS logic_candidates_project_idx "
    "ON logic_candidates (project_id, kind, name)",
    # action_runs: one action invocation, from preview to apply / revert.
    """CREATE TABLE IF NOT EXISTS action_runs (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id TEXT PRIMARY KEY,
        action_name TEXT NOT NULL,
        action_version INTEGER NOT NULL DEFAULT 1,
        object_type TEXT NOT NULL DEFAULT '',
        object_key TEXT NOT NULL DEFAULT '',
        params_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK (status IN ('previewed', 'pending_approval', 'approved',
            'rejected', 'applied', 'failed', 'reverted')),
        requested_by TEXT NOT NULL DEFAULT '',
        via TEXT NOT NULL DEFAULT 'ui' CHECK (via IN ('ui', 'cli', 'agent', 'workflow')),
        token_id TEXT,
        workflow_run_id TEXT,
        approver TEXT,
        approval_role TEXT,
        preview_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        idempotency_key TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        applied_at TEXT,
        reverted_at TEXT,
        UNIQUE (project_id, idempotency_key)
    )""",
    "CREATE INDEX IF NOT EXISTS action_runs_project_idx "
    "ON action_runs (project_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS action_runs_object_idx "
    "ON action_runs (project_id, object_type, object_key)",
    # object_edits: the edit layer over source rows (sources are never written).
    # source_value_json = the source's value when the edit was made (a different current
    # value is a conflict); operational = a property that is no source column.
    """CREATE TABLE IF NOT EXISTS object_edits (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        object_type TEXT NOT NULL,
        object_key TEXT NOT NULL,
        property TEXT NOT NULL,
        old_value_json TEXT NOT NULL DEFAULT 'null',
        new_value_json TEXT NOT NULL DEFAULT 'null',
        source_value_json TEXT NOT NULL DEFAULT 'null',
        operational INTEGER NOT NULL DEFAULT 0,
        action_run_id TEXT,
        edited_by TEXT NOT NULL DEFAULT '',
        edited_at TEXT NOT NULL,
        reverted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS object_edits_object_idx "
    "ON object_edits (project_id, object_type, object_key, property, id)",
    "CREATE INDEX IF NOT EXISTS object_edits_run_idx ON object_edits (action_run_id)",
    # workflow_runs: one firing of a workflow; (workflow, fire_slot) unique so a schedule
    # slot fires once however many ticks see it.
    """CREATE TABLE IF NOT EXISTS workflow_runs (
        project_id TEXT NOT NULL REFERENCES projects (id),
        id TEXT PRIMARY KEY,
        workflow_name TEXT NOT NULL,
        workflow_version INTEGER NOT NULL DEFAULT 1,
        trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'on_action', 'manual')),
        fire_slot TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('running', 'paused', 'succeeded', 'failed', 'cancelled')),
        started_by TEXT NOT NULL DEFAULT '',
        steps_json TEXT NOT NULL DEFAULT '[]',
        summary_json TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        UNIQUE (project_id, workflow_name, fire_slot)
    )""",
)

#: review_decisions as of v10: the kinds ``object`` (an object type proposal) and
#: ``logic`` (an extracted logic candidate) join the ontology kinds.
_V10_REVIEW_DECISIONS = """CREATE TABLE {t} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL
        CHECK (kind IN ('alignment', 'alias', 'concept', 'object', 'logic')),
    subject_key TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject', 'acknowledge')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL DEFAULT 'local',
    decided_at TEXT NOT NULL,
    applied_revision INTEGER,
    project_id TEXT NOT NULL REFERENCES projects (id),
    UNIQUE (project_id, kind, subject_key)
)"""


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """Schema v10: the actionable ontology. ``object_types``, ``logic_defs`` (+
    ``logic_def_history``), ``logic_candidates``, ``action_runs``, ``object_edits``,
    ``workflow_runs``; ``agent_tokens.scopes`` (a JSON list, ``["read"]`` for every
    existing token); ``review_decisions`` rebuilt with the kinds ``object`` and ``logic``
    (rows kept). Safe to re-run."""
    for statement in _V10_STATEMENTS:
        conn.execute(statement)
    if "scopes" not in _columns(conn, "agent_tokens"):
        conn.execute(
            "ALTER TABLE agent_tokens ADD COLUMN scopes TEXT NOT NULL DEFAULT '[\"read\"]'"
        )
    current = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review_decisions'"
    ).fetchone()
    if current is None or "'object'" not in (current[0] or ""):
        _rebuild(
            conn,
            "review_decisions",
            _V10_REVIEW_DECISIONS,
            project_expr=repr(DEFAULT_PROJECT_ID),
        )


def _ensure_indexes(conn: sqlite3.Connection, version: int) -> None:
    for since, statement in ENSURED_INDEXES:
        if version >= since:
            conn.execute(statement)


#: Seconds a lease stays valid without a heartbeat (the holder refreshes it every third).
LEASE_TTL_S = 120.0


class SourceBusy(RuntimeError):
    """Another operation holds the source's lease (an HTTP 409 for the API)."""

    def __init__(self, name: str, operation: str, pid: int) -> None:
        super().__init__(
            f"source {name!r} already has an active {operation} (pid {pid}); wait for it to finish"
        )
        self.name, self.operation, self.pid = name, operation, pid


class LeaseLost(RuntimeError):
    """The operation's lease is no longer its own: it must stop before mutating more."""


@dataclass
class Lease:
    store: Store
    token: str
    names: list[str]
    operation: str
    lost: threading.Event

    def owned(self) -> bool:
        """True while every reserved name still carries this lease's token."""
        if self.lost.is_set():
            return False
        with self.store._conn() as conn:
            n = conn.execute(
                "SELECT count(*) FROM source_leases WHERE token = ?", (self.token,)
            ).fetchone()[0]
        if n != len(self.names):
            self.lost.set()
        return not self.lost.is_set()

    def check(self) -> None:
        if not self.owned():
            raise LeaseLost(
                f"lease lost: the {self.operation} reservation of "
                f"{', '.join(self.names)} was taken over; stopping"
            )


def pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    locator: str
    pg_database: str | None
    catalog_database_id: str | None
    created_at: str
    updated_at: str
    project_id: str = DEFAULT_PROJECT_ID


@dataclass(frozen=True)
class Run:
    id: str
    kind: str
    source_id: str | None
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    params: dict[str, Any]
    project_id: str = DEFAULT_PROJECT_ID


@dataclass(frozen=True)
class Step:
    run_id: str
    name: str
    status: str
    started_at: str | None
    finished_at: str | None
    detail: dict[str, Any]


@dataclass(frozen=True)
class Snapshot:
    """One persisted ontology (``ontology_snapshots`` row); ``model`` is the JSON of
    :meth:`autoontology.ontology.model.MergedOntology.to_dict` (None when not loaded)."""

    revision: int
    created_at: str
    run_id: str
    sources: list[dict[str, Any]]
    counts: dict[str, Any]
    model: dict[str, Any] | None = None
    project_id: str = DEFAULT_PROJECT_ID


@dataclass(frozen=True)
class Project:
    """One project (schema v8): its own sources, runs, revisions, review decisions, agent
    tokens, playground and leases. ``archived_at`` set = hidden from default listings."""

    id: str
    slug: str
    name: str
    description: str
    created_at: str
    archived_at: str | None

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)


class ProjectNotFound(LookupError):
    """No project with that id or slug."""


def validate_slug(slug: str) -> str:
    if isinstance(slug, str) and SLUG_RE.fullmatch(slug) and RESERVED_SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"project slug {slug!r} is reserved: 'r' followed by digits names a legacy "
            "export folder; choose another slug"
        )
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"invalid project slug {str(slug)[:40]!r}: 2-24 characters, lower-case letters, "
            "digits and '-', starting with a letter"
        )
    return slug


def _project(row: sqlite3.Row) -> Project:
    return Project(**{k: row[k] for k in row.keys()})


def _snapshot(row: sqlite3.Row, model: dict[str, Any] | None) -> Snapshot:
    return Snapshot(
        revision=int(row["revision"]),
        created_at=row["created_at"],
        run_id=row["run_id"],
        sources=json.loads(row["sources_json"] or "[]"),
        counts=json.loads(row["counts_json"] or "{}"),
        model=model,
        project_id=row["project_id"],
    )


def _source(row: sqlite3.Row) -> Source:
    return Source(**{k: row[k] for k in row.keys()})


def _run(row: sqlite3.Row) -> Run:
    data = {k: row[k] for k in row.keys() if k != "params_json"}
    return Run(**data, params=json.loads(row["params_json"] or "{}"))


def _step(row: sqlite3.Row) -> Step:
    return Step(
        run_id=row["run_id"],
        name=row["name"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        detail=json.loads(row["detail_json"] or "{}"),
    )


#: Busy timeout of every store connection (ms) and the bounded retry for lock errors
#: SQLite does not route through the busy handler (switching a new database to WAL).
BUSY_TIMEOUT_MS = 30_000
LOCK_RETRIES = 60
#: Total wait for a store lock across all attempts (``AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S``).
DEFAULT_LOCK_TIMEOUT_S = 120.0


class StoreLocked(sqlite3.OperationalError):
    """The store stayed locked past ``AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S``."""


def store_lock_timeout_s() -> float:
    raw = os.environ.get("AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_LOCK_TIMEOUT_S
    except ValueError:
        return DEFAULT_LOCK_TIMEOUT_S
    return value if value > 0 else DEFAULT_LOCK_TIMEOUT_S


def _locked(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _retry_locked(fn: Any, conn: sqlite3.Connection, what: str) -> Any:
    """Run *fn*, retrying "database is locked" with capped exponential backoff, all within
    one monotonic deadline (``AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S``): each attempt's
    busy_timeout is capped to the time left, so a lock that never clears fails after the
    deadline (:class:`StoreLocked`) instead of after every attempt's full busy timeout."""
    timeout = store_lock_timeout_s()
    deadline = time.monotonic() + timeout
    last: sqlite3.OperationalError | None = None
    for attempt in range(LOCK_RETRIES):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        conn.execute(f"PRAGMA busy_timeout={max(1, min(BUSY_TIMEOUT_MS, int(left * 1000)))}")
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _locked(exc):
                raise
            last = exc
        time.sleep(max(0.0, min(0.5, 0.005 * 2**attempt, deadline - time.monotonic())))
    raise StoreLocked(
        f"the AutoOntology store is locked ({what}) for more than {timeout:.0f}s: another "
        "process holds a write transaction on autoontology.db (see `autoontology status` / running "
        "jobs); retry later or raise AUTOONTOLOGY_STORE_LOCK_TIMEOUT_S"
    ) from last


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """WAL mode, set once per database. Changing the journal mode needs an exclusive lock
    and fails with "database is locked" *without* waiting on the busy handler when another
    connection is opening the same fresh database, so it is retried."""

    def apply() -> None:
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")

    _retry_locked(apply, conn, "switching to WAL")


class Store:
    """Thin typed wrapper. Opens a short-lived connection per call (safe across threads)."""

    def __init__(self, path: Path, *, default_project_slug: str | None = None) -> None:
        """Open (and migrate) the store. *default_project_slug* = the workspace name
        (``Settings.workspace``): the first open that passes it gives the default project
        that slug (slugified, :func:`project_slug_from_workspace`) and the workspace name
        as its display name, once; later opens and user renames are never overridden."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()
        # The store can hold external DB credentials (database sources): owner-only.
        os.chmod(self.path, 0o600)
        self.default_project_id = self._read_default_project_id()
        if default_project_slug:
            self._settle_default_slug(default_project_slug)

    @classmethod
    def for_home(cls, home: Path, *, default_project_slug: str | None = None) -> Store:
        return cls(Path(home) / "autoontology.db", default_project_slug=default_project_slug)

    def _read_default_project_id(self) -> str:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT value FROM store_meta WHERE key = ?", (META_DEFAULT_PROJECT,)
                ).fetchone()
        except sqlite3.OperationalError:  # a store held below v8 (tests)
            return DEFAULT_PROJECT_ID
        return str(row[0]) if row else DEFAULT_PROJECT_ID

    def _settle_default_slug(self, workspace: str) -> None:
        slug = project_slug_from_workspace(workspace)
        with self._immediate() as conn:
            if not conn.execute(
                "SELECT 1 FROM store_meta WHERE key = ?", (META_SLUG_PENDING,)
            ).fetchone():
                return
            taken = conn.execute(
                "SELECT 1 FROM projects WHERE slug = ? AND id != ?",
                (slug, self.default_project_id),
            ).fetchone()
            if not taken:
                conn.execute(
                    "UPDATE projects SET slug = ?, name = ? WHERE id = ?",
                    (slug, workspace.strip()[:200] or slug, self.default_project_id),
                )
            conn.execute("DELETE FROM store_meta WHERE key = ?", (META_SLUG_PENDING,))

    def _pid(self, project_id: str | None) -> str:
        return project_id or self.default_project_id

    # --- projects -------------------------------------------------------------

    def project(self, ref: str | None = None) -> ProjectStore:
        """The project-scoped view of *ref* (an id or a slug; None = the default project).
        Raises :class:`ProjectNotFound`. Archived projects are still returned."""
        found = self.get_project(ref) if ref else self.default_project()
        if found is None:
            raise ProjectNotFound(f"unknown project {str(ref)[:80]!r}")
        return ProjectStore(self, found)

    def default_project(self) -> Project:
        found = self.get_project(self.default_project_id)
        assert found is not None, "the default project row is missing"
        return found

    def get_project(self, ref: str) -> Project | None:
        """The project whose id or slug is *ref*."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ? OR slug = ? ORDER BY id = ? DESC LIMIT 1",
                (ref, ref, ref),
            ).fetchone()
        return _project(row) if row else None

    def get_project_by_slug(self, slug: str) -> Project | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return _project(row) if row else None

    def list_projects(self, *, include_archived: bool = False) -> list[Project]:
        """Projects, the default one first, then by creation."""
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY id != ?, created_at, rowid"
        with self._conn() as conn:
            rows = conn.execute(sql, (self.default_project_id,)).fetchall()
        return [_project(r) for r in rows]

    def create_project(
        self, slug: str, *, name: str | None = None, description: str = ""
    ) -> ProjectStore:
        validate_slug(slug)
        project_id = "prj_" + uuid.uuid4().hex[:12]
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO projects (id, slug, name, description, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (project_id, slug, (name or slug).strip() or slug, description, now_iso()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"project {slug!r} already exists") from exc
        return self.project(project_id)

    def _require_project(self, ref: str) -> Project:
        found = self.get_project(ref)
        if found is None:
            raise ProjectNotFound(f"unknown project {str(ref)[:80]!r}")
        return found

    def rename_project(
        self,
        ref: str,
        *,
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
    ) -> Project:
        """Change a project's display name, slug and/or description."""
        current = self._require_project(ref)
        if slug is not None:
            validate_slug(slug)
        if name is not None and not name.strip():
            raise ValueError("a project name cannot be empty")
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE projects SET slug = ?, name = ?, description = ? WHERE id = ?",
                    (
                        slug if slug is not None else current.slug,
                        name.strip() if name is not None else current.name,
                        description if description is not None else current.description,
                        current.id,
                    ),
                )
                if slug is not None and current.id == self.default_project_id:
                    conn.execute("DELETE FROM store_meta WHERE key = ?", (META_SLUG_PENDING,))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"project {slug!r} already exists") from exc
        return self._require_project(current.id)

    def archive_project(self, ref: str) -> Project:
        """Hide a project from default listings (its data stays). The default project
        cannot be archived."""
        current = self._require_project(ref)
        if current.id == self.default_project_id:
            raise ValueError("the default project cannot be archived")
        if not current.archived_at:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE projects SET archived_at = ? WHERE id = ?", (now_iso(), current.id)
                )
        return self._require_project(current.id)

    def unarchive_project(self, ref: str) -> Project:
        current = self._require_project(ref)
        with self._conn() as conn:
            conn.execute("UPDATE projects SET archived_at = NULL WHERE id = ?", (current.id,))
        return self._require_project(current.id)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            _ensure_wal(conn)
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:  # one transaction per call
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """A connection inside ``BEGIN IMMEDIATE`` (write lock up front): check-then-write
        sequences in it are atomic across threads and processes."""
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            _retry_locked(lambda: conn.execute("BEGIN IMMEDIATE"), conn, "write transaction")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    # --- schema ---------------------------------------------------------------

    def schema_version(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
        return int(row[0] or 0)

    def migrate(self) -> int:
        """Apply the pending :data:`MIGRATIONS`, each in its own explicit transaction.

        ``sqlite3``'s implicit transactions do not cover DDL, so a crash between a
        migration's statements and its ``schema_version`` row used to leave a half-applied
        schema that the retry could not re-apply (``duplicate column``). Here every
        migration runs between ``BEGIN IMMEDIATE`` and ``COMMIT`` (SQLite DDL is
        transactional) and the write lock taken by ``BEGIN IMMEDIATE`` serialises
        concurrent migrators, which re-read the version inside the transaction.
        """
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            # table rebuilds (v8) must not cascade or check references mid-copy; the
            # pragma is a no-op inside a transaction, so it is set before any BEGIN
            conn.execute("PRAGMA foreign_keys=OFF")
            _ensure_wal(conn)
            current = 0
            while True:
                _retry_locked(lambda: conn.execute("BEGIN IMMEDIATE"), conn, "migration")
                try:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS schema_version "
                        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                    )
                    current = (
                        conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] or 0
                    )
                    pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
                    if not pending:
                        _ensure_indexes(conn, current)
                        conn.execute("COMMIT")
                        return current
                    version, sql = pending[0]
                    if callable(sql):
                        sql(conn)
                    else:
                        for statement in (s.strip() for s in sql.split(";")):
                            if statement:
                                conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, now_iso()),
                    )
                    conn.execute("COMMIT")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
        finally:
            conn.close()

    # --- sources --------------------------------------------------------------

    # Methods with a ``project_id`` keyword: None means the default project. Callers that
    # leave it out are deprecated (they predate projects); use :meth:`project` instead.

    def add_source(
        self,
        *,
        name: str,
        kind: SourceKind,
        locator: str,
        pg_database: str | None = None,
        project_id: str | None = None,
    ) -> Source:
        """Register a source in the project (names are unique per project)."""
        ts = now_iso()
        source_id = uuid.uuid4().hex
        pid = self._pid(project_id)
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO sources (id, name, kind, locator, pg_database, "
                    "catalog_database_id, created_at, updated_at, project_id) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (source_id, name, kind, locator, pg_database, ts, ts, pid),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
            raise ValueError(f"source {name!r} already exists") from exc
        return self.get_source(name, project_id=pid)  # type: ignore[return-value]

    def get_source(self, name: str, *, project_id: str | None = None) -> Source | None:
        """The source named *name* in the project (deprecated without *project_id*)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE project_id = ? AND name = ?",
                (self._pid(project_id), name),
            ).fetchone()
        return _source(row) if row else None

    def get_source_by_id(self, source_id: str) -> Source | None:
        """The source with id *source_id* in any project (ids are store-wide); check its
        ``project_id``, or use :meth:`ProjectStore.get_source_by_id`."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source(row) if row else None

    def list_sources(self, *, project_id: str | None = None) -> list[Source]:
        """The project's sources by name (deprecated without *project_id*)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sources WHERE project_id = ? ORDER BY name",
                (self._pid(project_id),),
            ).fetchall()
        return [_source(r) for r in rows]

    def list_all_sources(self) -> list[Source]:
        """Every project's sources (administration only)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY project_id, name").fetchall()
        return [_source(r) for r in rows]

    def set_locator(self, source_id: str, locator: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET locator = ?, updated_at = ? WHERE id = ?",
                (locator, now_iso(), source_id),
            )

    def set_catalog_database_id(self, source_id: str, catalog_database_id: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET catalog_database_id = ?, updated_at = ? WHERE id = ?",
                (catalog_database_id, now_iso(), source_id),
            )

    def delete_source(self, source_id: str) -> None:
        """Delete a source together with its runs and their steps (documents, chunks,
        concepts, relations and evidence go with it through ON DELETE CASCADE)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM steps WHERE run_id IN (SELECT id FROM runs WHERE source_id = ?)",
                (source_id,),
            )
            conn.execute("DELETE FROM runs WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def get_file_manifest(self, source_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM file_manifests WHERE source_id = ?", (source_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def set_file_manifest(self, source_id: str, manifest: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO file_manifests (source_id, manifest_json, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT (source_id) DO UPDATE SET "
                "manifest_json = excluded.manifest_json, updated_at = excluded.updated_at",
                (source_id, json.dumps(manifest, ensure_ascii=False), now_iso()),
            )

    def _semantic_row(self, source_id: str) -> sqlite3.Row | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM semantic_baselines WHERE source_id = ?", (source_id,)
            ).fetchone()

    def get_semantic_baseline(self, source_id: str) -> dict[str, Any] | None:
        row = self._semantic_row(source_id)
        return json.loads(row["baseline_json"]) if row and row["baseline_json"] else None

    def get_pending_invalidation(self, source_id: str) -> dict[str, Any] | None:
        row = self._semantic_row(source_id)
        return json.loads(row["pending_json"]) if row and row["pending_json"] else None

    def _set_semantic(self, source_id: str, column: str, value: Any) -> None:
        payload = None if value is None else json.dumps(value, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO semantic_baselines (source_id, {column}, updated_at) "
                f"VALUES (?, ?, ?) ON CONFLICT (source_id) DO UPDATE SET "
                f"{column} = excluded.{column}, updated_at = excluded.updated_at",
                (source_id, payload, now_iso()),
            )

    def set_semantic_baseline(self, source_id: str, baseline: dict[str, Any]) -> None:
        self._set_semantic(source_id, "baseline_json", baseline)

    def set_pending_invalidation(self, source_id: str, pending: dict[str, Any] | None) -> None:
        self._set_semantic(source_id, "pending_json", pending)

    # --- runs -----------------------------------------------------------------

    def create_run(
        self,
        *,
        kind: str,
        source_id: str | None,
        params: dict[str, Any] | None = None,
        status: RunStatus = "running",
        project_id: str | None = None,
    ) -> Run:
        """A new run. A run of a source belongs to the source's project (*project_id*, when
        given, must match it); a project-wide run to *project_id* (deprecated: None = the
        default project)."""
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        pid = self._pid(project_id)
        if source_id is not None:
            source = self.get_source_by_id(source_id)
            if source is not None:
                if project_id is not None and source.project_id != project_id:
                    raise ValueError(f"source {source.name!r} belongs to another project")
                pid = source.project_id
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (id, kind, source_id, status, started_at, params_json, "
                "project_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    kind,
                    source_id,
                    status,
                    now_iso(),
                    json.dumps(params or {}, ensure_ascii=False, default=str),
                    pid,
                ),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> Run | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        finished = None if status in ACTIVE_RUN_STATUSES else now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, finished, run_id),
            )
            if params is not None:
                conn.execute(
                    "UPDATE runs SET params_json = ? WHERE id = ?",
                    (json.dumps(params, ensure_ascii=False, default=str), run_id),
                )

    # --- ontology snapshots --------------------------------------------------------

    def save_snapshot(
        self,
        run_id: str,
        model: Any,
        *,
        sources: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
    ) -> int:
        """Persist *model* (a :class:`~autoontology.ontology.model.MergedOntology`) as the
        project's next revision (``max + 1`` within the project, under the write lock) and
        return it. *sources* defaults to the model's source names."""
        data = model.to_dict()
        counts = model.counts()
        if sources is None:
            sources = [
                *({"name": n, "kind": "structured"} for n in model.structured_sources),
                *({"name": n, "kind": "documents"} for n in model.documents_sources),
            ]
        pid = self._pid(project_id)
        with self._immediate() as conn:
            revision = int(
                conn.execute(
                    "SELECT coalesce(max(revision), 0) + 1 FROM ontology_snapshots "
                    "WHERE project_id = ?",
                    (pid,),
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO ontology_snapshots (project_id, revision, created_at, run_id, "
                "sources_json, counts_json, model_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    revision,
                    now_iso(),
                    run_id,
                    json.dumps(sources, ensure_ascii=False, default=str),
                    json.dumps(counts, ensure_ascii=False, default=str),
                    json.dumps(data, ensure_ascii=False, default=str),
                ),
            )
        return revision

    def latest_snapshot(
        self, *, with_model: bool = True, project_id: str | None = None
    ) -> Snapshot | None:
        return self._snapshot(None, with_model, self._pid(project_id))

    def get_snapshot(
        self, revision: int, *, with_model: bool = True, project_id: str | None = None
    ) -> Snapshot | None:
        """The project's snapshot *revision* (deprecated without *project_id*)."""
        return self._snapshot(int(revision), with_model, self._pid(project_id))

    def latest_revision(self, *, project_id: str | None = None) -> int | None:
        """Revision of the project's newest snapshot (the cache key of derived views)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT max(revision) FROM ontology_snapshots WHERE project_id = ?",
                (self._pid(project_id),),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def previous_revision(self, revision: int, *, project_id: str | None = None) -> int | None:
        """The project's newest revision older than *revision* (None: there is none)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT max(revision) FROM ontology_snapshots "
                "WHERE project_id = ? AND revision < ?",
                (self._pid(project_id), revision),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def list_snapshots(self, limit: int = 50, *, project_id: str | None = None) -> list[Snapshot]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT project_id, revision, created_at, run_id, sources_json, counts_json "
                "FROM ontology_snapshots WHERE project_id = ? ORDER BY revision DESC LIMIT ?",
                (self._pid(project_id), limit),
            ).fetchall()
        return [_snapshot(r, None) for r in rows]

    def _snapshot(self, revision: int | None, with_model: bool, pid: str) -> Snapshot | None:
        cols = "project_id, revision, created_at, run_id, sources_json, counts_json" + (
            ", model_json" if with_model else ""
        )
        sql = f"SELECT {cols} FROM ontology_snapshots WHERE project_id = ? "
        sql += "AND revision = ?" if revision is not None else "ORDER BY revision DESC LIMIT 1"
        args: tuple[Any, ...] = (pid, revision) if revision is not None else (pid,)
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        if row is None:
            return None
        return _snapshot(row, json.loads(row["model_json"]) if with_model else None)

    def latest_run(self, source_id: str, kind: str | None = None) -> Run | None:
        sql = "SELECT * FROM runs WHERE source_id = ?"
        args: list[Any] = [source_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        return _run(row) if row else None

    def active_runs(
        self, source_id: str | None = None, *, project_id: str | None = None
    ) -> list[Run]:
        """Pending/running runs (of *source_id*); *project_id* None = every project."""
        sql = "SELECT * FROM runs WHERE status IN ('pending', 'running')"
        args: list[Any] = []
        if source_id is not None:
            sql += " AND source_id = ?"
            args.append(source_id)
        if project_id is not None:
            sql += " AND project_id = ?"
            args.append(project_id)
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY started_at", args).fetchall()
        return [_run(r) for r in rows]

    def latest_project_run(self, kind: str, *, project_id: str | None = None) -> Run | None:
        """The project's newest project-wide run (no source) of *kind*, whatever its
        status."""
        sql = "SELECT * FROM runs WHERE project_id = ? AND kind = ? AND source_id IS NULL"
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, (self._pid(project_id), kind)).fetchone()
        return _run(row) if row is not None else None

    def latest_workspace_run(self, kind: str) -> Run | None:
        """Deprecated: :meth:`latest_project_run` of the default project."""
        return self.latest_project_run(kind)

    def list_runs(
        self, source_id: str | None = None, limit: int = 50, *, project_id: str | None = None
    ) -> list[Run]:
        """Newest runs first (of *source_id*); *project_id* None = every project."""
        sql = "SELECT * FROM runs WHERE 1 = 1"
        args: list[Any] = []
        if source_id is not None:
            sql += " AND source_id = ?"
            args.append(source_id)
        if project_id is not None:
            sql += " AND project_id = ?"
            args.append(project_id)
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_run(r) for r in rows]

    # --- steps ----------------------------------------------------------------

    def start_step(self, run_id: str, name: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO steps (run_id, name, status, started_at, finished_at, detail_json) "
                "VALUES (?, ?, 'running', ?, NULL, '{}') "
                "ON CONFLICT (run_id, name) DO UPDATE SET status = 'running', "
                "started_at = excluded.started_at, finished_at = NULL, detail_json = '{}'",
                (run_id, name, now_iso()),
            )

    def finish_step(
        self, run_id: str, name: str, *, status: StepStatus, detail: dict[str, Any] | None = None
    ) -> None:
        ts = now_iso()
        payload = json.dumps(detail or {}, ensure_ascii=False, default=str)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO steps (run_id, name, status, started_at, finished_at, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id, name) DO UPDATE SET status = excluded.status, "
                "finished_at = excluded.finished_at, detail_json = excluded.detail_json",
                (run_id, name, status, ts, ts, payload),
            )

    def reset_steps(self, run_id: str, names: list[str]) -> None:
        """Mark *names* pending again (a resumed run invalidated them)."""
        with self._conn() as conn:
            conn.executemany(
                "UPDATE steps SET status = 'pending', started_at = NULL, finished_at = NULL "
                "WHERE run_id = ? AND name = ?",
                [(run_id, n) for n in names],
            )

    def get_steps(self, run_id: str) -> list[Step]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [_step(r) for r in rows]

    def last_succeeded_step(
        self, run_kind: str, prefix: str, detail_key: str, *, project_id: str | None = None
    ) -> Step | None:
        """The succeeded step named ``prefix*`` whose detail carries *detail_key*, from the
        project's newest run of *run_kind* that has one (the newest finished of that run's
        steps). No run-count cutoff: unrelated runs never hide it."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.* FROM steps s JOIN runs r ON r.id = s.run_id "
                "WHERE r.project_id = ? AND r.kind = ? AND s.status = 'succeeded' "
                "AND substr(s.name, 1, ?) = ? "
                "AND json_extract(s.detail_json, '$.' || ?) IS NOT NULL "
                "ORDER BY r.started_at DESC, r.rowid DESC, s.finished_at DESC, s.id DESC "
                "LIMIT 1",
                (self._pid(project_id), run_kind, len(prefix), prefix, detail_key),
            ).fetchone()
        return _step(row) if row is not None else None

    def succeeded_steps(self, run_id: str) -> set[str]:
        return {s.name for s in self.get_steps(run_id) if s.status == "succeeded"}

    # --- source leases --------------------------------------------------------

    def acquire_leases(
        self,
        names: list[str],
        operation: str,
        *,
        ttl: float = LEASE_TTL_S,
        project_id: str | None = None,
    ) -> str:
        """Reserve every source in *names* (of the project) for *operation*, all or nothing;
        return the lease token. Raises :class:`SourceBusy` while a lease's holder pid is
        alive (also past its expiry: a suspended or slow holder keeps exclusivity). Only a
        lease whose pid is gone is reclaimed."""
        token = uuid.uuid4().hex
        now = time.time()
        wanted = sorted(set(names))
        pid = self._pid(project_id)
        with self._immediate() as conn:
            for name in wanted:
                row = conn.execute(
                    "SELECT * FROM source_leases WHERE project_id = ? AND source_name = ?",
                    (pid, name),
                ).fetchone()
                if row is not None and pid_alive(row["pid"]):
                    raise SourceBusy(name, row["operation"], row["pid"])
            for name in wanted:
                conn.execute(
                    "INSERT INTO source_leases (project_id, source_name, token, operation, pid, "
                    "acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (project_id, source_name) DO UPDATE SET "
                    "token = excluded.token, operation = excluded.operation, "
                    "pid = excluded.pid, acquired_at = excluded.acquired_at, "
                    "expires_at = excluded.expires_at",
                    (pid, name, token, operation, os.getpid(), now_iso(), now + ttl),
                )
        return token

    def heartbeat_lease(self, token: str, *, ttl: float = LEASE_TTL_S) -> bool:
        """Extend the lease; False when it was lost (reclaimed by someone else)."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE source_leases SET expires_at = ? WHERE token = ?",
                (time.time() + ttl, token),
            )
        return cur.rowcount > 0

    def release_lease(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM source_leases WHERE token = ?", (token,))

    def lease_holder(self, name: str, *, project_id: str | None = None) -> dict[str, Any] | None:
        """The live lease on the project's source *name* (``operation``, ``pid``, ...)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM source_leases WHERE project_id = ? AND source_name = ?",
                (self._pid(project_id), name),
            ).fetchone()
        if row is None or not pid_alive(row["pid"]):
            return None
        return dict(row)

    @contextmanager
    def lease(
        self,
        names: list[str],
        operation: str,
        *,
        ttl: float = LEASE_TTL_S,
        project_id: str | None = None,
    ) -> Iterator[Lease]:
        """Hold the project's sources' lease for the ``with`` body, refreshed by a heartbeat
        thread. A failed heartbeat (the row is no longer ours) marks the :class:`Lease`
        lost; the holder checks it (:meth:`Lease.check`) before each step and stops."""
        token = self.acquire_leases(names, operation, ttl=ttl, project_id=project_id)
        stop = threading.Event()
        held = Lease(self, token, sorted(set(names)), operation, threading.Event())

        def beat() -> None:
            while not stop.wait(ttl / 3):
                try:
                    if not self.heartbeat_lease(token, ttl=ttl):
                        held.lost.set()
                except sqlite3.Error:  # busy store: the next beat retries
                    pass

        thread = threading.Thread(target=beat, name=f"autoontology-lease-{operation}", daemon=True)
        thread.start()
        try:
            yield held
        finally:
            stop.set()
            thread.join(timeout=5)
            self.release_lease(token)


@functools.cache
def _project_scoped_methods(cls: type) -> frozenset[str]:
    """The public methods of *cls* (a :class:`Store` class) that take ``project_id``."""
    out = set()
    for name, member in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("_"):
            continue
        if "project_id" in inspect.signature(member).parameters:
            out.add(name)
    return frozenset(out)


class ProjectStore:
    """A :class:`Store` seen through one project: every read and write is limited to it.

    Objects looked up by a store-wide id (a source, a run) come back only when they belong
    to the project; another project's id reads as missing. Calls go through the store's
    public methods with ``project_id``, so a subclass's hooks (``TrackingStore``) still
    see them.

    It stands in for a :class:`Store` wherever the engine takes one (build, export, apply,
    align, extract, the review/alignment/document stores): any other attribute is the
    store's own, and a store method with a ``project_id`` keyword gets this project's id
    (so a deprecated default-project call can never leak through). Store-wide id methods
    (steps, manifests, ``update_run``) pass through unchanged; ``default_project_id`` is
    this project's id, so the per-project tables (``DecisionStore(view)``,
    ``AgentTokens(view)``, ``PlaygroundStore(view)``) are scoped to it too."""

    def __init__(self, store: Store, project: Project) -> None:
        self.store = store
        self.project = project
        self.id = project.id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name in ("store", "project", "id"):
            raise AttributeError(name)
        attr = getattr(self.store, name)
        if name in _project_scoped_methods(type(self.store)) and callable(attr):
            return functools.partial(attr, project_id=self.id)
        return attr

    def __repr__(self) -> str:
        return f"ProjectStore({self.project.slug!r}, {self.store.path})"

    @property
    def slug(self) -> str:
        return self.project.slug

    @property
    def name(self) -> str:
        """The project's display name (the ontology's name)."""
        return self.project.name

    @property
    def default_project_id(self) -> str:
        """This project's id (see the class docstring)."""
        return self.id

    @property
    def root(self) -> Store:
        """The whole store (administration and store-wide gates only)."""
        return self.store

    @property
    def is_default(self) -> bool:
        return self.id == self.store.default_project_id

    def latest_workspace_run(self, kind: str) -> Run | None:
        """Deprecated alias of :meth:`latest_project_run`."""
        return self.latest_project_run(kind)

    def delete_source(self, source_id: str) -> None:
        """Delete one of this project's sources (another project's id raises)."""
        if self.get_source_by_id(source_id) is None:
            raise ValueError("the source does not belong to this project")
        self.store.delete_source(source_id)

    def refresh(self) -> ProjectStore:
        """The same view with the project row read again (after a rename)."""
        return self.store.project(self.id)

    # --- sources ----------------------------------------------------------------------

    def add_source(
        self, *, name: str, kind: SourceKind, locator: str, pg_database: str | None = None
    ) -> Source:
        return self.store.add_source(
            name=name, kind=kind, locator=locator, pg_database=pg_database, project_id=self.id
        )

    def get_source(self, name: str) -> Source | None:
        return self.store.get_source(name, project_id=self.id)

    def get_source_by_id(self, source_id: str) -> Source | None:
        found = self.store.get_source_by_id(source_id)
        return found if found is not None and found.project_id == self.id else None

    def list_sources(self) -> list[Source]:
        return self.store.list_sources(project_id=self.id)

    # --- runs -------------------------------------------------------------------------

    def create_run(
        self,
        *,
        kind: str,
        source_id: str | None,
        params: dict[str, Any] | None = None,
        status: RunStatus = "running",
    ) -> Run:
        """A run in this project (a source of another project raises ValueError)."""
        return self.store.create_run(
            kind=kind, source_id=source_id, params=params, status=status, project_id=self.id
        )

    def get_run(self, run_id: str) -> Run | None:
        found = self.store.get_run(run_id)
        return found if found is not None and found.project_id == self.id else None

    def list_runs(self, source_id: str | None = None, limit: int = 50) -> list[Run]:
        return self.store.list_runs(source_id, limit, project_id=self.id)

    def active_runs(self, source_id: str | None = None) -> list[Run]:
        return self.store.active_runs(source_id, project_id=self.id)

    def latest_project_run(self, kind: str) -> Run | None:
        return self.store.latest_project_run(kind, project_id=self.id)

    def last_succeeded_step(self, run_kind: str, prefix: str, detail_key: str) -> Step | None:
        return self.store.last_succeeded_step(run_kind, prefix, detail_key, project_id=self.id)

    # --- snapshots --------------------------------------------------------------------

    def save_snapshot(
        self, run_id: str, model: Any, *, sources: list[dict[str, Any]] | None = None
    ) -> int:
        return self.store.save_snapshot(run_id, model, sources=sources, project_id=self.id)

    def latest_snapshot(self, *, with_model: bool = True) -> Snapshot | None:
        return self.store.latest_snapshot(with_model=with_model, project_id=self.id)

    def get_snapshot(self, revision: int, *, with_model: bool = True) -> Snapshot | None:
        return self.store.get_snapshot(revision, with_model=with_model, project_id=self.id)

    def latest_revision(self) -> int | None:
        return self.store.latest_revision(project_id=self.id)

    def previous_revision(self, revision: int) -> int | None:
        return self.store.previous_revision(revision, project_id=self.id)

    def list_snapshots(self, limit: int = 50) -> list[Snapshot]:
        return self.store.list_snapshots(limit, project_id=self.id)

    # --- leases -----------------------------------------------------------------------

    def acquire_leases(self, names: list[str], operation: str, *, ttl: float = LEASE_TTL_S) -> str:
        return self.store.acquire_leases(names, operation, ttl=ttl, project_id=self.id)

    def lease_holder(self, name: str) -> dict[str, Any] | None:
        return self.store.lease_holder(name, project_id=self.id)

    def lease(
        self, names: list[str], operation: str, *, ttl: float = LEASE_TTL_S
    ) -> contextlib.AbstractContextManager[Lease]:
        return self.store.lease(names, operation, ttl=ttl, project_id=self.id)

    # --- per-project tables kept in their own modules ---------------------------------

    def decisions(self) -> DecisionStore:
        """The project's :class:`~autoontology.store.decisions.DecisionStore`."""
        from autoontology.store.decisions import DecisionStore

        return DecisionStore(self.store, project_id=self.id)

    def agent_tokens(self) -> AgentTokens:
        """The project's :class:`~autoontology.store.agent_tokens.AgentTokens`."""
        from autoontology.store.agent_tokens import AgentTokens

        return AgentTokens(self.store, project_id=self.id)

    def object_types(self) -> ObjectTypes:
        """The project's :class:`~autoontology.store.objects.ObjectTypes`."""
        from autoontology.store.objects import ObjectTypes

        return ObjectTypes(self.store, project_id=self.id)

    def object_edits(self) -> ObjectEdits:
        """The project's :class:`~autoontology.store.objects.ObjectEdits`."""
        from autoontology.store.objects import ObjectEdits

        return ObjectEdits(self.store, project_id=self.id)

    def action_runs(self) -> ActionRuns:
        """The project's :class:`~autoontology.store.actions.ActionRuns`."""
        from autoontology.store.actions import ActionRuns

        return ActionRuns(self.store, project_id=self.id)

    def workflow_runs(self) -> WorkflowRuns:
        """The project's :class:`~autoontology.store.workflows.WorkflowRuns`."""
        from autoontology.store.workflows import WorkflowRuns

        return WorkflowRuns(self.store, project_id=self.id)

    def logic_defs(self) -> LogicDefs:
        """The project's :class:`~autoontology.store.logic.LogicDefs`."""
        from autoontology.store.logic import LogicDefs

        return LogicDefs(self.store, project_id=self.id)

    def playground(self) -> PlaygroundStore:
        """The project's :class:`~autoontology.playground.store.PlaygroundStore`."""
        from autoontology.playground.store import PlaygroundStore

        return PlaygroundStore(self.store, project_id=self.id)


def scoped(store: Store | ProjectStore, project: str | None = None) -> ProjectStore:
    """*store* as a :class:`ProjectStore`: a view stays itself (a *project* naming another
    project raises ValueError), a plain store gives the view of *project* (an id or a slug;
    None = the default project)."""
    if isinstance(store, ProjectStore):
        if project is not None and project not in (store.id, store.slug):
            raise ValueError(f"this view is of project {store.slug!r}, not {project!r}")
        return store
    return store.project(project)


def root_store(store: Store | ProjectStore) -> Store:
    """The whole :class:`Store` behind *store* (itself when it is one)."""
    return store.store if isinstance(store, ProjectStore) else store
