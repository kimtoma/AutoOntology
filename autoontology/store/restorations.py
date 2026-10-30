# SPDX-License-Identifier: MIT
"""Durable restoration packets; execution never edits a ready packet in place."""

from __future__ import annotations

import json
import uuid

from autoontology.store.db import Store, now_iso, pid_alive
from autoontology.store.operations import OperationsError, digest


class RestoreError(OperationsError):
    def __init__(self, message: str, code="restore_changed", status=409):
        super().__init__(message)
        self.code, self.status = code, status


class Restorations:
    def __init__(self, store: Store):
        self.store = store

    def create(self, run_id: str, packet: dict) -> str:
        proposal = uuid.uuid4().hex
        with self.store._conn() as conn:
            conn.execute(
                "INSERT INTO restore_proposals VALUES(?,'preparing',?,NULL,NULL,?,?,?)",
                (
                    proposal,
                    run_id,
                    json.dumps(packet, ensure_ascii=False),
                    digest(packet),
                    now_iso(),
                ),
            )
        return proposal

    def prepared(self, proposal: str, packet: dict, *, ok: bool) -> dict:
        with self.store._conn() as conn:
            changed = conn.execute(
                "UPDATE restore_proposals SET packet_json=?,sha256=?,status=? WHERE id=? AND status='preparing'",
                (
                    json.dumps(packet, ensure_ascii=False),
                    digest(packet),
                    "ready" if ok else "blocked",
                    proposal,
                ),
            ).rowcount
            if changed != 1:
                raise RestoreError("restoration preparation changed")
        return self.get(proposal)

    def get(self, proposal: str) -> dict:
        with self.store._conn() as conn:
            row = conn.execute("SELECT * FROM restore_proposals WHERE id=?", (proposal,)).fetchone()
        if row is None:
            raise RestoreError("unknown restoration proposal", "restore_missing", 404)
        try:
            packet = json.loads(row["packet_json"])
        except ValueError as exc:
            raise RestoreError("restoration packet integrity check failed") from exc
        if digest(packet) != row["sha256"]:
            raise RestoreError("restoration packet integrity check failed")
        confirmation = f"restore r{packet['target_revision']} from r{packet['base_revision']} {row['sha256'][:12]}"
        return {
            **{k: row[k] for k in row.keys() if k != "packet_json"},
            "packet": packet,
            "confirmation": confirmation,
        }

    def reap(self) -> None:
        with self.store._conn() as conn:
            rows = conn.execute(
                "SELECT id,prepare_run_id,execution_run_id,status FROM restore_proposals WHERE status IN ('preparing','ready')"
            ).fetchall()
        for row in rows:
            run_id = (
                row["prepare_run_id"] if row["status"] == "preparing" else row["execution_run_id"]
            )
            run = self.store.get_run(run_id) if run_id else None
            if (
                run
                and run.status in ("running", "pending")
                and not pid_alive(int(run.params.get("pid") or 0))
            ):
                self.store.update_run(
                    run.id,
                    status="failed",
                    error="Restoration interrupted before commit; inspect the packet before retrying.",
                )
            if (
                row["status"] == "preparing"
                and run
                and self.store.get_run(run.id).status in ("failed", "cancelled")
            ):
                with self.store._conn() as conn:
                    conn.execute(
                        "UPDATE restore_proposals SET status='failed' WHERE id=? AND status='preparing'",
                        (row["id"],),
                    )

    def listing(self, limit=50) -> list[dict]:
        self.reap()
        with self.store._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM restore_proposals ORDER BY created_at DESC,rowid DESC LIMIT ?",
                    (max(1, min(500, limit)),),
                )
            ]
        return [self.get(proposal) for proposal in ids]
