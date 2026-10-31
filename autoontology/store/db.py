# SPDX-License-Identifier: MIT
"""SQLite job/source store at ``<home>/autoontology.db`` (stdlib ``sqlite3``, WAL mode).

The schema is a list of numbered migrations applied in order and recorded in
``schema_version`` (v1 is the whole initial schema). Later changes append to
:data:`MIGRATIONS`; never edit a migration that has shipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SourceKind = Literal["files", "database", "documents"]
RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
#: Step statuses are free text in the schema; these are the ones AutoOntology writes.
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
ACTIVE_RUN_STATUSES: tuple[str, ...] = ("pending", "running")

MIGRATIONS: list[tuple[int, str]] = [
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
        # Original bytes and input revisions outlive mutable ingestion/source rows.
        # Source IDs/names are provenance, not cascading foreign keys to live sources.
        """
        CREATE TABLE IF NOT EXISTS provenance_files (
            sha256 TEXT NOT NULL, filename TEXT NOT NULL, bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY (sha256, filename)
        );
        CREATE TABLE IF NOT EXISTS source_revisions (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
            kind TEXT NOT NULL, manifest_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS source_revisions_source ON source_revisions (source_name, created_at);
        CREATE TABLE IF NOT EXISTS source_revision_captures (
            run_id TEXT NOT NULL, source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL REFERENCES source_revisions(id),
            captured_at TEXT NOT NULL, read_at TEXT, summary_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS source_revision_heads (
            source_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL REFERENCES source_revisions(id),
            read_at TEXT NOT NULL
        )
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS publications (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL UNIQUE,
            manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('prepared','committed')),
            created_at TEXT NOT NULL, committed_at TEXT
        )
        """,
    ),
    (
        9,
        """
        CREATE TABLE IF NOT EXISTS usage_traces (
            id TEXT PRIMARY KEY, workspace TEXT NOT NULL, kind TEXT NOT NULL,
            revision INTEGER NOT NULL, publication_id TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','cancelled','interrupted')),
            conversation_id TEXT REFERENCES playground_conversations(id) ON DELETE CASCADE,
            owner_pid INTEGER NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            data_json TEXT NOT NULL, sha256 TEXT
        );
        CREATE INDEX IF NOT EXISTS usage_traces_time ON usage_traces(workspace, started_at DESC);
        ALTER TABLE playground_messages ADD COLUMN trace_id TEXT
        """,
    ),
    (
        10,
        """
        CREATE TABLE IF NOT EXISTS restore_proposals (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK(status IN ('preparing','ready','blocked','failed','committed')),
            prepare_run_id TEXT NOT NULL, execution_run_id TEXT,
            result_revision INTEGER, packet_json TEXT NOT NULL,
            sha256 TEXT NOT NULL, created_at TEXT NOT NULL
        )
        """,
    ),
    (
        11,
        """
        CREATE TABLE IF NOT EXISTS processing_receipts (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_name TEXT NOT NULL,
            stage TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS processing_receipts_source ON processing_receipts(source_name, created_at);
        CREATE TABLE IF NOT EXISTS processing_document_reads (
            document_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES processing_receipts(id)
        );
        CREATE TABLE IF NOT EXISTS processing_source_heads (
            source_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES processing_receipts(id)
        )
        """,
    ),
    (
        12,
        """
        CREATE TABLE identity_state (
            id INTEGER PRIMARY KEY CHECK(id=1), organization_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL UNIQUE, workspace_name TEXT NOT NULL,
            policy_version INTEGER NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE identity_principals (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('human','agent')),
            status TEXT NOT NULL CHECK(status IN ('active','disabled')),
            password_hash TEXT, epoch INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
        );
        CREATE TABLE identity_domains (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE identity_grants (
            id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES identity_principals(id),
            workspace_id TEXT NOT NULL, domain_id TEXT NOT NULL, role TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL
        );
        CREATE INDEX identity_grants_principal ON identity_grants(principal_id,revoked_at);
        CREATE TABLE identity_credentials (
            id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES identity_principals(id),
            secret_sha256 TEXT NOT NULL UNIQUE, epoch INTEGER NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
        );
        CREATE TABLE identity_delegations (
            principal_id TEXT PRIMARY KEY REFERENCES identity_principals(id),
            delegator_id TEXT NOT NULL REFERENCES identity_principals(id),
            workspace_id TEXT NOT NULL, actions_json TEXT NOT NULL, domains_json TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
        );
        CREATE TABLE identity_source_domains (
            source_id TEXT NOT NULL, version INTEGER NOT NULL, domain_id TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(source_id,version)
        );
        CREATE TABLE identity_login_limits (
            key TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE identity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            actor_id TEXT, credential_id TEXT, action TEXT NOT NULL,
            target TEXT NOT NULL, detail_json TEXT NOT NULL, previous_sha256 TEXT NOT NULL,
            sha256 TEXT NOT NULL
        );
        CREATE TRIGGER identity_events_no_update BEFORE UPDATE ON identity_events
        BEGIN SELECT RAISE(ABORT,'identity audit is append-only'); END;
        CREATE TRIGGER identity_events_no_delete BEFORE DELETE ON identity_events
        BEGIN SELECT RAISE(ABORT,'identity audit is append-only'); END
        """,
    ),
    (
        13,
        """
        ALTER TABLE playground_conversations ADD COLUMN principal_id TEXT;
        ALTER TABLE playground_conversations ADD COLUMN identity_workspace TEXT;
        ALTER TABLE usage_traces ADD COLUMN principal_id TEXT;
        ALTER TABLE usage_traces ADD COLUMN identity_workspace TEXT;
        UPDATE usage_traces SET
            principal_id=json_extract(data_json,'$.actor.id'),
            identity_workspace=json_extract(data_json,'$.actor.workspace_id')
        WHERE CASE WHEN json_valid(data_json) THEN
            json_extract(data_json,'$.actor.verified')=1
            AND json_extract(data_json,'$.actor.workspace_id') IS NOT NULL ELSE 0 END;
        CREATE INDEX playground_conversation_owner ON playground_conversations
            (identity_workspace,principal_id,scope,updated_at);
        CREATE INDEX usage_trace_owner ON usage_traces
            (identity_workspace,principal_id,workspace,started_at);
        CREATE TRIGGER conversation_owner_immutable
        BEFORE UPDATE OF principal_id,identity_workspace ON playground_conversations
        BEGIN SELECT RAISE(ABORT,'conversation owner is immutable'); END;
        CREATE TRIGGER usage_trace_owner_immutable
        BEFORE UPDATE OF principal_id,identity_workspace ON usage_traces
        BEGIN SELECT RAISE(ABORT,'trace owner is immutable'); END
        """,
    ),
    (
        14,
        """
        CREATE TABLE playground_message_scopes (
            message_id INTEGER PRIMARY KEY REFERENCES playground_messages(id) ON DELETE CASCADE,
            scope_json TEXT NOT NULL, sha256 TEXT NOT NULL
        );
        CREATE TRIGGER playground_message_scope_immutable
        BEFORE UPDATE ON playground_message_scopes
        BEGIN SELECT RAISE(ABORT,'message knowledge scope is immutable'); END
        """,
    ),
    (
        15,
        """
        CREATE TABLE ops_cases (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, reason TEXT NOT NULL,
            owner_id TEXT, priority TEXT NOT NULL, due_at TEXT,
            base_revision INTEGER, candidate_revision INTEGER, base_publication_id TEXT,
            status TEXT NOT NULL, version INTEGER NOT NULL,
            changes_json TEXT NOT NULL, links_json TEXT NOT NULL, outcome_json TEXT NOT NULL DEFAULT '{}',
            request_key TEXT NOT NULL UNIQUE, request_sha TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE ops_case_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES ops_cases(id),
            sequence INTEGER NOT NULL, operation TEXT NOT NULL, actor_json TEXT NOT NULL,
            data_json TEXT NOT NULL, previous_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(case_id,sequence));
        CREATE TABLE ops_case_packets (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES ops_cases(id),
            payload_json TEXT NOT NULL, sha256 TEXT NOT NULL,
            expires_at REAL NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE ops_case_receipts (
            packet_id TEXT PRIMARY KEY REFERENCES ops_case_packets(id),
            case_id TEXT NOT NULL REFERENCES ops_cases(id), record_json TEXT NOT NULL,
            sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE ops_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, change_id TEXT NOT NULL,
            operation TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TRIGGER case_events_no_update BEFORE UPDATE ON ops_case_events BEGIN SELECT RAISE(ABORT,'immutable case event'); END;
        CREATE TRIGGER case_events_no_delete BEFORE DELETE ON ops_case_events BEGIN SELECT RAISE(ABORT,'immutable case event'); END;
        CREATE TRIGGER case_packets_no_update BEFORE UPDATE ON ops_case_packets BEGIN SELECT RAISE(ABORT,'immutable review packet'); END;
        CREATE TRIGGER case_packets_no_delete BEFORE DELETE ON ops_case_packets BEGIN SELECT RAISE(ABORT,'immutable review packet'); END;
        CREATE TRIGGER case_receipts_no_update BEFORE UPDATE ON ops_case_receipts BEGIN SELECT RAISE(ABORT,'immutable review receipt'); END;
        CREATE TRIGGER case_receipts_no_delete BEFORE DELETE ON ops_case_receipts BEGIN SELECT RAISE(ABORT,'immutable review receipt'); END;
        CREATE TRIGGER review_events_no_update BEFORE UPDATE ON ops_review_events BEGIN SELECT RAISE(ABORT,'immutable review event'); END;
        CREATE TRIGGER review_events_no_delete BEFORE DELETE ON ops_review_events BEGIN SELECT RAISE(ABORT,'immutable review event'); END;
    """,
    ),
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


def _ensure_indexes(conn: sqlite3.Connection, version: int) -> None:
    for since, statement in ENSURED_INDEXES:
        if version >= since:
            conn.execute(statement)


def _migration_statements(sql: str) -> Iterator[str]:
    """Keep trigger bodies and quoted semicolons inside one atomic statement."""
    start = 0
    for pos, char in enumerate(sql):
        if char == ";" and sqlite3.complete_statement(sql[start : pos + 1]):
            yield sql[start : pos + 1].strip()
            start = pos + 1
    remainder = sql[start:].strip()
    if remainder:
        if not sqlite3.complete_statement(remainder + ";"):
            raise ValueError("incomplete migration statement")
        yield remainder


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


def _snapshot(row: sqlite3.Row, model: dict[str, Any] | None) -> Snapshot:
    return Snapshot(
        revision=int(row["revision"]),
        created_at=row["created_at"],
        run_id=row["run_id"],
        sources=json.loads(row["sources_json"] or "[]"),
        counts=json.loads(row["counts_json"] or "{}"),
        model=model,
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

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()
        # The store can hold external DB credentials (database sources): owner-only.
        os.chmod(self.path, 0o600)

    @classmethod
    def for_home(cls, home: Path) -> Store:
        return cls(Path(home) / "autoontology.db")

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
                    for statement in _migration_statements(sql):
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

    @contextmanager
    def _source_effect(self, action, source_id, detail, *, expected_policy=None):
        from autoontology.security.context import audit_effect, authorize

        with self._immediate() as conn:
            decision = authorize(self, action, expected_policy=expected_policy, conn=conn)
            yield conn
            audit_effect(self, decision, source_id, detail, conn=conn)

    def add_source(
        self,
        *,
        name: str,
        kind: SourceKind,
        locator: str,
        pg_database: str | None = None,
        expected_policy: int | None = None,
    ) -> Source:
        ts = now_iso()
        source_id = uuid.uuid4().hex
        try:
            with self._source_effect(
                "source.edit",
                source_id,
                {"operation": "register", "name": name, "kind": kind},
                expected_policy=expected_policy,
            ) as conn:
                conn.execute(
                    "INSERT INTO sources (id, name, kind, locator, pg_database, "
                    "catalog_database_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                    (source_id, name, kind, locator, pg_database, ts, ts),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
            raise ValueError(f"source {name!r} already exists") from exc
        return self.get_source(name)  # type: ignore[return-value]

    def get_source(self, name: str) -> Source | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()
        return _source(row) if row else None

    def get_source_by_id(self, source_id: str) -> Source | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source(row) if row else None

    def list_sources(self) -> list[Source]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
        return [_source(r) for r in rows]

    def set_locator(self, source_id: str, locator: str, *, expected_policy=None) -> None:
        with self._source_effect(
            "source.edit",
            source_id,
            {"operation": "change_locator"},
            expected_policy=expected_policy,
        ) as conn:
            conn.execute(
                "UPDATE sources SET locator = ?, updated_at = ? WHERE id = ?",
                (locator, now_iso(), source_id),
            )

    def set_catalog_database_id(self, source_id: str, catalog_database_id: str | None) -> None:
        with self._source_effect(
            "candidate.prepare",
            source_id,
            {"operation": "bind_catalog", "catalog_bound": catalog_database_id is not None},
        ) as conn:
            conn.execute(
                "UPDATE sources SET catalog_database_id = ?, updated_at = ? WHERE id = ?",
                (catalog_database_id, now_iso(), source_id),
            )

    def delete_source(self, source_id: str, *, expected_policy=None) -> None:
        """Delete a source together with its runs and their steps (documents, chunks,
        concepts, relations and evidence go with it through ON DELETE CASCADE)."""
        with self._source_effect(
            "source.edit", source_id, {"operation": "remove"}, expected_policy=expected_policy
        ) as conn:
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
    ) -> Run:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (id, kind, source_id, status, started_at, params_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    kind,
                    source_id,
                    status,
                    now_iso(),
                    json.dumps(params or {}, ensure_ascii=False, default=str),
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
        self, run_id: str, model: Any, *, sources: list[dict[str, Any]] | None = None
    ) -> int:
        """Persist *model* (a :class:`~autoontology.ontology.model.MergedOntology`) as the
        next revision and return it. *sources* defaults to the model's source names."""
        data = model.to_dict()
        counts = model.counts()
        if sources is None:
            sources = [
                *({"name": n, "kind": "structured"} for n in model.structured_sources),
                *({"name": n, "kind": "documents"} for n in model.documents_sources),
            ]
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO ontology_snapshots (created_at, run_id, sources_json, "
                "counts_json, model_json) VALUES (?, ?, ?, ?, ?)",
                (
                    now_iso(),
                    run_id,
                    json.dumps(sources, ensure_ascii=False, default=str),
                    json.dumps(counts, ensure_ascii=False, default=str),
                    json.dumps(data, ensure_ascii=False, default=str),
                ),
            )
        return int(cur.lastrowid)

    def latest_snapshot(self, *, with_model: bool = True) -> Snapshot | None:
        return self._snapshot(None, with_model)

    def get_snapshot(self, revision: int, *, with_model: bool = True) -> Snapshot | None:
        return self._snapshot(int(revision), with_model)

    def latest_revision(self) -> int | None:
        """Revision of the newest snapshot (the cache key of derived views)."""
        with self._conn() as conn:
            row = conn.execute("SELECT max(revision) FROM ontology_snapshots").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def previous_revision(self, revision: int) -> int | None:
        """The newest revision older than *revision* (None: there is none)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT max(revision) FROM ontology_snapshots WHERE revision < ?", (revision,)
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def list_snapshots(self, limit: int = 50) -> list[Snapshot]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT revision, created_at, run_id, sources_json, counts_json "
                "FROM ontology_snapshots ORDER BY revision DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_snapshot(r, None) for r in rows]

    def _snapshot(self, revision: int | None, with_model: bool) -> Snapshot | None:
        cols = "revision, created_at, run_id, sources_json, counts_json" + (
            ", model_json" if with_model else ""
        )
        sql = f"SELECT {cols} FROM ontology_snapshots "
        sql += "WHERE revision = ?" if revision is not None else "ORDER BY revision DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, (revision,) if revision is not None else ()).fetchone()
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

    def active_runs(self, source_id: str | None = None) -> list[Run]:
        sql = "SELECT * FROM runs WHERE status IN ('pending', 'running')"
        args: list[Any] = []
        if source_id is not None:
            sql += " AND source_id = ?"
            args.append(source_id)
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY started_at", args).fetchall()
        return [_run(r) for r in rows]

    def latest_workspace_run(self, kind: str) -> Run | None:
        """The newest workspace-wide run (no source) of *kind*, whatever its status."""
        sql = "SELECT * FROM runs WHERE kind = ? AND source_id IS NULL"
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, (kind,)).fetchone()
        return _run(row) if row is not None else None

    def list_runs(self, source_id: str | None = None, limit: int = 50) -> list[Run]:
        sql = "SELECT * FROM runs"
        args: list[Any] = []
        if source_id is not None:
            sql += " WHERE source_id = ?"
            args.append(source_id)
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

    def succeeded_steps(self, run_id: str) -> set[str]:
        return {s.name for s in self.get_steps(run_id) if s.status == "succeeded"}

    # --- source leases --------------------------------------------------------

    def acquire_leases(self, names: list[str], operation: str, *, ttl: float = LEASE_TTL_S) -> str:
        """Reserve every source in *names* for *operation*, all or nothing; return the
        lease token. Raises :class:`SourceBusy` while a lease's holder pid is alive (also
        past its expiry: a suspended or slow holder keeps exclusivity). Only a lease whose
        pid is gone is reclaimed."""
        token = uuid.uuid4().hex
        now = time.time()
        wanted = sorted(set(names))
        with self._immediate() as conn:
            for name in wanted:
                row = conn.execute(
                    "SELECT * FROM source_leases WHERE source_name = ?", (name,)
                ).fetchone()
                if row is not None and pid_alive(row["pid"]):
                    raise SourceBusy(name, row["operation"], row["pid"])
            for name in wanted:
                conn.execute(
                    "INSERT INTO source_leases (source_name, token, operation, pid, "
                    "acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_name) DO UPDATE SET token = excluded.token, "
                    "operation = excluded.operation, pid = excluded.pid, "
                    "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
                    (name, token, operation, os.getpid(), now_iso(), now + ttl),
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

    def lease_holder(self, name: str) -> dict[str, Any] | None:
        """The live lease on *name* (``operation``, ``pid``, ...), if any."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM source_leases WHERE source_name = ?", (name,)
            ).fetchone()
        if row is None or not pid_alive(row["pid"]):
            return None
        return dict(row)

    @contextmanager
    def lease(
        self, names: list[str], operation: str, *, ttl: float = LEASE_TTL_S
    ) -> Iterator[Lease]:
        """Hold the sources' lease for the ``with`` body, refreshed by a heartbeat thread.
        A failed heartbeat (the row is no longer ours) marks the :class:`Lease` lost; the
        holder checks it (:meth:`Lease.check`) before each step and stops."""
        token = self.acquire_leases(names, operation, ttl=ttl)
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
