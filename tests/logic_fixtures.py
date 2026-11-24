# SPDX-License-Identifier: MIT
"""The quickstart sample (columns as in ``samples/quickstart/structured``) as a snapshot
and as source tables, for the logic tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from autoontology.config import Settings
from autoontology.objects import binding, reader, service
from autoontology.ontology import model as merged
from autoontology.store.db import Store
from autoontology.structured.semantic import SemanticFK, StructuredSnapshot
from autoontology.unstructured.align import TermInfo
from tests.test_objects import FakeBackend

CONTRACTS = [
    {
        "계약ID": "K2019-001",
        "고객ID": "C035",
        "계약명": "온빛테크 ERP 유지보수",
        "계약금액": 18000000,
        "시작일": "2019-03-06",
        "종료일": "2020-02-29",
        "상태": "종료",
    },
    {
        "계약ID": "K2024-003",
        "고객ID": "C001",
        "계약명": "가람물산 물류 시스템",
        "계약금액": 52000000,
        "시작일": "2024-01-10",
        "종료일": "2027-01-09",
        "상태": "진행",
    },
    {
        "계약ID": "K2025-009",
        "고객ID": None,
        "계약명": "고객 없는 계약",
        "계약금액": None,
        "시작일": "2025-05-01",
        "종료일": None,
        "상태": "진행",
    },
]
CUSTOMERS = [
    {"고객ID": "C001", "고객명": "주식회사 가람물산", "지역": "부산", "담당자": "최하은"},
    {"고객ID": "C035", "고객명": "온빛테크", "지역": "서울", "담당자": "윤서아"},
]
INVOICES = [
    {
        "청구ID": "INV-1",
        "계약ID": "K2019-001",
        "청구일": "2019-11-25",
        "청구금액": 4500000,
        "수납금액": 4500000,
        "수납상태": "완납",
    },
    {
        "청구ID": "INV-2",
        "계약ID": "K2024-003",
        "청구일": "2026-09-01",
        "청구금액": 9000000,
        "수납금액": 3000000,
        "수납상태": "미납",
    },
    {
        "청구ID": "INV-3",
        "계약ID": "K2024-003",
        "청구일": "2026-11-10",
        "청구금액": 2000000,
        "수납금액": None,
        "수납상태": "미납",
    },
]
TYPES = {
    "계약금액": "bigint",
    "청구금액": "bigint",
    "수납금액": "bigint",
    "시작일": "date",
    "종료일": "date",
    "청구일": "date",
}
#: property labels that differ from the column (resolved by label in expressions)
LABELS = {"지역": "소재지"}
CON, CUS, INV = "term-t-con", "term-t-cus", "term-t-inv"


def _columns(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": c, "type": TYPES.get(c, "text")} for c in rows[0]]


def _attrs(table: str, rows: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[dict]:
    return [
        {
            "id": f"{table}-{n}",
            "name": LABELS.get(c, c),
            "datatype": TYPES.get(c, "text"),
            "column": c,
            "schema": "public",
            "table": table,
        }
        for n, c in enumerate(rows[0])
        if c not in skip
    ]


#: the class labels of a build that named its classes in English
ENGLISH = {"계약": "Contract", "고객": "Customer", "청구": "Invoice"}


def snapshot(labels: dict[str, str] | None = None) -> StructuredSnapshot:
    """The quickstart snapshot (*labels*: class label renames, e.g. :data:`ENGLISH`)."""
    lb = labels or {}
    terms = [
        TermInfo(
            "t-con",
            lb.get("계약", "계약"),
            "계약 정보",
            attributes=_attrs("contracts", CONTRACTS, skip=("고객ID",)),
            tables=[{"id": "tb-con", "name": "contracts", "schema": "public"}],
        ),
        TermInfo(
            "t-cus",
            lb.get("고객", "고객"),
            "고객",
            attributes=_attrs("customers", CUSTOMERS),
            tables=[{"id": "tb-cus", "name": "customers", "schema": "public"}],
        ),
        TermInfo(
            "t-inv",
            lb.get("청구", "청구"),
            "청구서",
            attributes=_attrs("invoices", INVOICES, skip=("계약ID",)),
            tables=[{"id": "tb-inv", "name": "invoices", "schema": "public"}],
        ),
    ]
    fks = [
        SemanticFK("t-con", "t-cus", "fk-a", "고객ID", "public.contracts.고객ID"),
        SemanticFK("t-inv", "t-con", "fk-b", "계약ID", "public.invoices.계약ID"),
    ]
    return StructuredSnapshot("quickstart", "db1", "autoontology_src_quickstart", terms, fks)


def model(labels: dict[str, str] | None = None) -> merged.MergedOntology:
    return merged.build("default", structured=[snapshot(labels)])


def tables() -> dict[str, dict[str, Any]]:
    return {
        "public.contracts": {"columns": _columns(CONTRACTS), "rows": [dict(r) for r in CONTRACTS]},
        "public.customers": {"columns": _columns(CUSTOMERS), "rows": [dict(r) for r in CUSTOMERS]},
        "public.invoices": {"columns": _columns(INVOICES), "rows": [dict(r) for r in INVOICES]},
    }


def install_backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend(tables())

    @contextmanager
    def open_source(settings, source):
        yield fake

    monkeypatch.setattr(reader, "open_source", open_source)
    return fake


def seed(store: Store, m: merged.MergedOntology | None = None, *, activate=(CON, CUS, INV)):
    """The quickstart source, a snapshot and the object types *activate* bound."""
    view = store.project()
    if view.get_source("quickstart") is None:
        view.add_source(
            name="quickstart",
            kind="files",
            locator=json.dumps({"path": "/nonexistent/quickstart"}),
            pg_database="autoontology_src_quickstart",
        )
    run = view.create_run(kind="build", source_id=None)
    view.save_snapshot(run.id, m or model())
    binding.ensure_synced(view)
    for name in activate:
        service.bind(Settings.from_env(), view, name)
    return view
