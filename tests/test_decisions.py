# SPDX-License-Identifier: MIT
"""Review decisions (schema v6): the store, the migration from older stores."""

import sqlite3

import pytest

from autoontology.store.alignments import AlignmentRecord, AlignmentStore
from autoontology.store.db import MIGRATIONS, Store
from autoontology.store.decisions import Decision, DecisionStore, alias_key


def _downgrade(path, version):
    """Turn a current store back into a v<version> one (the later migrations' DDL undone)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE review_decisions")
    conn.execute("ALTER TABLE alignments DROP COLUMN judged_json")
    if version < 5:
        conn.execute("ALTER TABLE documents DROP COLUMN reader_fingerprint")
    if version < 4:
        conn.execute("DROP TABLE term_legacy_descriptions")
    conn.execute("DELETE FROM schema_version WHERE version > ?", (version,))
    conn.execute(
        "INSERT INTO alignments (source_id, concept_id, concept_name, term_id, relation, "
        "run_id, created_at) VALUES ('s', 'c1', '계약', 't1', 'same', 'r', 'x')"
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("version", [5, 3])
def test_migration_v6_upgrades_older_stores(tmp_path, version):
    path = tmp_path / "autoontology.db"
    Store(path)
    _downgrade(path, version)
    store = Store(path)
    assert store.schema_version() == MIGRATIONS[-1][0] == 6
    row = AlignmentStore(store).get("c1")
    assert row is not None and row.judged == {} and row.relation == "same"
    decisions = DecisionStore(store)
    assert decisions.list() == []
    d = decisions.put(Decision("concept", "c1", "acknowledge"))
    assert d.id and d.decided_by == "local" and d.applied_revision is None


def test_decisions_upsert_delete_and_applied(tmp_path):
    store = Store(tmp_path / "s.db")
    decisions = DecisionStore(store)
    first = decisions.put(
        Decision(
            "alignment", "c1", "accept", {"relation": "related", "term_id": "t1"}, note="확인"
        )
    )
    assert decisions.mark_applied([first], 3) == 1
    assert decisions.get("alignment", "c1").applied_revision == 3
    # one active decision per subject: a new one replaces it and is pending again
    again = decisions.put(Decision("alignment", "c1", "reject", decided_by="kim@example.test"))
    assert again.id == first.id and again.decision == "reject" and again.relation == "none"
    assert again.applied_revision is None and again.decided_by == "kim@example.test"
    assert [d.subject_key for d in decisions.list(pending=True)] == ["c1"]
    decisions.put(Decision("alias", alias_key("t1", "홍길동"), "accept"))
    assert len(decisions.list()) == 2 and len(decisions.list("alias")) == 1
    assert decisions.delete("alignment", "c1") and not decisions.delete("alignment", "c1")
    assert decisions.get("alignment", "c1") is None
    with pytest.raises(ValueError, match="cannot be"):
        decisions.put(Decision("concept", "c2", "accept"))
    with pytest.raises(ValueError, match="relation"):
        decisions.put(Decision("alignment", "c2", "accept", {"relation": "none", "term_id": "t"}))
    with pytest.raises(ValueError, match="Term"):
        decisions.put(Decision("alignment", "c2", "accept", {"relation": "same"}))


def test_alignment_row_keeps_the_judged_verdict(tmp_path):
    store = Store(tmp_path / "s.db")
    src = store.add_source(name="docs", kind="documents", locator="{}")
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO concepts (id, source_id, name, name_en, kind, definition, "
            "created_at) VALUES ('c1', ?, '계약', '', 'entity', '', 'x')",
            (src.id,),
        )
    aligns = AlignmentStore(store)
    judged = {"relation": "related", "term_id": "t1", "confidence": 0.7}
    aligns.put(
        AlignmentRecord(src.id, "c1", "계약", "same", "r", term_id="t1", accepted=True, judged=judged)
    )
    assert aligns.get("c1").judged == judged
    aligns.put(AlignmentRecord(src.id, "c1", "계약", "related", "r", term_id="t1"))
    assert aligns.get("c1").judged == {}
