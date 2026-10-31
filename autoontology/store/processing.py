# SPDX-License-Identifier: MIT
"""Immutable processing inputs/configurations/results, independent of live rows."""

from __future__ import annotations

import hashlib
import json

from autoontology.store.db import Store, now_iso
from autoontology.store.documents import DocumentStore
from autoontology.store.operations import OperationsError, digest


class ProcessingError(OperationsError):
    code = "processing_invalid"
    status = 409


def text_sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def read_chunks(chunks):
    return [
        {
            "id": c.id,
            "index": c.idx,
            "section_path": c.section_path,
            "page": c.page,
            "kind": c.kind,
            "text": c.text,
            "tokens": c.tokens,
        }
        for c in chunks
    ]


class Processing:
    def __init__(self, store: Store):
        self.store = store

    def record(self, source, stage, run_id, **data):
        record = {
            "version": 1,
            "source_id": source.id,
            "source_name": source.name,
            "stage": stage,
            "run_id": run_id,
            **data,
        }
        identity = "proc-" + digest(record)
        with self.store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processing_receipts VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    source.id,
                    source.name,
                    stage,
                    json.dumps(record, ensure_ascii=False),
                    now_iso(),
                ),
            )
        return identity

    def get(self, identity):
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM processing_receipts WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise ProcessingError("processing receipt is missing")
        try:
            record = json.loads(row["record_json"])
            if identity != "proc-" + digest(record) or any(
                record[key] != row[key] for key in ("source_id", "source_name", "stage")
            ):
                raise ValueError("changed receipt")
        except (ValueError, KeyError, TypeError) as exc:
            raise ProcessingError("processing receipt integrity check failed") from exc
        return {"id": identity, "record": record, "created_at": row["created_at"]}

    def listing(self, source_name, limit=100):
        with self.store._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM processing_receipts WHERE source_name=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                    (source_name, max(1, min(1000, limit))),
                )
            ]
        return [self.get(identity) for identity in ids]

    def document_read(self, document_id, identity):
        self.get(identity)
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO processing_document_reads VALUES(?,?) ON CONFLICT(document_id) DO UPDATE SET receipt_id=excluded.receipt_id",
                (document_id, identity),
            )

    def read_for(self, document_id):
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT receipt_id FROM processing_document_reads WHERE document_id=?",
                (document_id,),
            ).fetchone()
        return row[0] if row else None

    def head(self, source_id, identity):
        self.get(identity)
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO processing_source_heads VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET receipt_id=excluded.receipt_id",
                (source_id, identity),
            )

    def binding(self, source_id):
        docs = DocumentStore(self.store)
        identities, unknown = set(), []
        documents = docs.list_documents(source_id)
        for document in documents:
            identity = self.read_for(document.id)
            if identity:
                receipt = self.get(identity)["record"]
                if receipt["input"]["sha256"] != document.sha256 or receipt["output"][
                    "chunks"
                ] != read_chunks(docs.chunks_for_document(document.id)):
                    raise ProcessingError("processing reader input changed")
                identities.add(identity)
            else:
                unknown.append({"stage": "read", "document_id": document.id})
        for chunk in docs.chunks_for_source(source_id):
            identity = chunk.extract_detail.get("processing_receipt")
            if identity and chunk.extraction is not None:
                receipt = self.get(identity)["record"]
                if (
                    receipt["input"]["text_sha256"] != text_sha256(chunk.text)
                    or receipt["output"] != chunk.extraction
                ):
                    raise ProcessingError("processing extraction differs from the active chunk")
                identities.add(identity)
            else:
                unknown.append({"stage": "extract", "chunk_id": chunk.id})
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT receipt_id FROM processing_source_heads WHERE source_id=?", (source_id,)
            ).fetchone()
        if row:
            receipt = self.get(row[0])["record"]
            if receipt["output"]["ontology_sha"] == docs.ontology_identity(source_id):
                identities.add(row[0])
            else:
                unknown.append({"stage": "consolidate", "reason": "materialized output changed"})
        else:
            unknown.append(
                {"stage": "consolidate", "reason": "historical configuration not recorded"}
            )
        return {
            "version": 1,
            "status": "unknown" if unknown else "recorded",
            "receipts": sorted(identities),
            "unknown": unknown,
        }

    def expand(self, bindings):
        identities = {
            identity
            for binding in bindings
            for source in binding["sources"]
            for identity in source.get("processing", {}).get("receipts", [])
        }
        records = {}
        pending = list(identities)
        while pending:
            identity = pending.pop()
            if identity in records:
                continue
            receipt = self.get(identity)
            records[identity] = receipt
            pending.extend(receipt["record"].get("dependencies", []))
        return [records[key] for key in sorted(records)]
