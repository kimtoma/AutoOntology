# SPDX-License-Identifier: MIT
"""The concept detail lists the review decisions about the concept (the concept sheet of the
UI shows who decided what and when)."""

from tests.test_build import env  # noqa: F401 - the build fixture
from tests.test_decisions import OpsJudge, _no_models, _rows, ops  # noqa: F401
from tests.test_ops_api import API, _client


def test_concept_detail_lists_its_decisions(ops):  # noqa: F811
    c, _ = _client(ops)
    rows = _rows(ops)
    cid = rows["수납"].concept_id
    item = f"al:{cid}"
    detail = c.get(f"{API}/ontology/concepts/concept-{cid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["decisions"] == []
    resp = c.post(f"{API}/review/{item}/decision", json={"decision": "reject", "note": "아님"})
    assert resp.status_code == 200, resp.text
    got = c.get(f"{API}/ontology/concepts/concept-{cid}").json()["decisions"]
    assert len(got) == 1
    d = got[0]
    assert (d["kind"], d["decision"], d["decided_by"], d["note"], d["item_id"]) == (
        "alignment",
        "reject",
        "local",
        "아님",
        item,
    )
    assert d["decided_at"] and d["applied_revision"] is None
    assert d["term"] == {"id": "t-customer", "name": "Customer"}
    # a Term lists the alias decisions on its labels (none here) and never a concept's
    term = c.get(f"{API}/ontology/concepts/term-t-customer").json()
    assert term["decisions"] == []
