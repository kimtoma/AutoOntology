# SPDX-License-Identifier: MIT
"""The committed sample sets (samples/quickstart, samples/hanbit): formats, keys, content.

Unit tests need no services. The kordoc-backed documents are read only when the kordoc
engine resolves (``KORDOC_CLI`` in the shell or ``data/kordoc``), else skipped; OCR of the
images is an integration test (``AUTOONTOLOGY_INTEGRATION=1``, OCR models installed).
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from autoontology import formats
from autoontology.config import REPO_ROOT
from autoontology.unstructured import kordoc, readers

SAMPLES = REPO_ROOT / "samples"
HANBIT = SAMPLES / "hanbit"
QUICKSTART = SAMPLES / "quickstart"
MAX_IMAGE_BYTES = 400 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
#: captured before the autouse fixture scrubs KORDOC_* for unit tests
_KORDOC_ENV = {k: v for k, v in os.environ.items() if k in ("KORDOC_CLI", "PATH", "KORDOC_NODE")}

FOLDER_SUFFIXES = {
    "structured": formats.STRUCTURED_SUFFIXES,
    "documents": formats.DOCUMENT_NATIVE_SUFFIXES | formats.DOCUMENT_KORDOC_SUFFIXES,
    "images": formats.IMAGE_SUFFIXES,
}
NOT_INPUT = {"LICENSE"}

#: document -> terms its extracted text must contain (business concepts and code values
#: the tables use, so alignment can match them)
KEY_TERMS: dict[str, list[str]] = {
    "계약관리규정.md": ["계약ID", "고객ID", "담당자ID", "계약상태", "진행중", "해지", "P-"],
    "계약관리규정.pdf": ["계약ID", "고객ID", "담당자ID", "계약금액", "주문ID", "승인권자"],
    "계약관리규정.hwpx": ["계약ID", "고객ID", "담당자ID", "계약금액", "제품코드"],
    "청구수납규정.md": ["청구ID", "수납상태", "완납", "부분수납", "미수납", "연체", "수납ID"],
    "청구수납규정.docx": ["청구ID", "수납상태", "완납", "부분수납", "미수납", "연체", "납기일"],
    "품질관리절차서.hwpx": ["검사", "불량", "시정조치", "제품코드", "공급사ID", "입고검사"],
    "거래처관리매뉴얼.doc": ["고객ID", "공급사ID", "신용등급", "평가등급", "담당자ID", "업종"],
    "영업전략회의록.pptx": ["연체", "수납상태", "단가표", "공급사", "계약ID", "영업1팀"],
    "분기실적보고.pptx": ["청구금액", "수납률", "영업2팀", "완납", "연체", "고객ID"],
    "조직도.xlsx": ["부서코드", "사원ID", "재무회계팀", "D07", "E001", "직급"],
    "단가표.xls": ["제품코드", "P-1001", "단가", "공급사ID", "적용일", "배관자재"],
    "담당자연락망.csv": ["사원ID", "내선번호", "영업1팀", "담당지역"],
    "고객FAQ.html": ["고객ID", "청구서", "수납상태", "주문ID", "납기일", "신용등급"],
    "공지사항.txt": ["단가표", "제품코드", "청구서"],
    "사업수행계획서.hwp": ["사업수행계획서"],
}
#: image -> terms the OCR text must contain
OCR_TERMS: dict[str, list[str]] = {
    "조직도.png": ["조직도", "재무회계팀", "영업1팀"],
    "매출추이.png": ["청구금액", "수납률"],
    "계약서_스캔.jpg": ["계약서", "계약금액", "P-"],
    "청구서_스캔.png": ["청구서", "INV-", "납기일"],
    "업무흐름도.webp": ["주문", "청구서", "수납"],
    "창고현황판.jpg": ["제품코드", "재고", "P-1"],
}


def _rel(path: Path) -> str:
    return str(path.relative_to(SAMPLES))


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


# --------------------------------------------------------------------------- layout


@pytest.mark.parametrize("root", [QUICKSTART, HANBIT], ids=["quickstart", "hanbit"])
def test_every_file_has_a_supported_suffix_for_its_folder(root):
    files = _files(root)
    assert files
    for path in files:
        folder = path.relative_to(root).parts[0]
        assert folder in FOLDER_SUFFIXES, _rel(path)
        if path.name in NOT_INPUT:
            continue
        assert path.suffix.lower() in FOLDER_SUFFIXES[folder], _rel(path)


def test_hanbit_layout():
    names = {
        folder: sorted(p.name for p in (HANBIT / folder).iterdir()) for folder in FOLDER_SUFFIXES
    }
    assert names["structured"] == sorted(
        [
            "customers.csv", "contracts.csv", "invoices.csv", "products.xlsx", "employees.sqlite",
            "suppliers.xls", "payments.jsonl", "price_history.tsv", "orders.parquet",
        ]
    )  # fmt: skip
    assert len(names["images"]) == 6
    suffixes = {Path(n).suffix for n in names["documents"]}
    for ext in (".pdf", ".pptx", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx", ".csv",
                ".html", ".txt", ".md"):  # fmt: skip
        assert ext in suffixes, ext
    assert set(KEY_TERMS) == set(names["documents"]) - NOT_INPUT
    assert set(OCR_TERMS) == set(names["images"])
    docs = readers.discover_documents(HANBIT / "documents")
    assert [p.name for p in docs] == sorted(set(names["documents"]) - NOT_INPUT)


def test_rhwp_file_and_licence_are_copies():
    for name in ("사업수행계획서.hwp", "LICENSE"):
        assert (HANBIT / "documents" / name).read_bytes() == (
            QUICKSTART / "documents" / name
        ).read_bytes()
    assert "rhwp" in (HANBIT / "documents" / "LICENSE").read_text()


def test_sizes():
    total = sum(p.stat().st_size for p in _files(SAMPLES))
    assert total <= MAX_TOTAL_BYTES, total
    for path in _files(SAMPLES):
        if path.suffix.lower() in formats.IMAGE_SUFFIXES:
            assert path.stat().st_size <= MAX_IMAGE_BYTES, _rel(path)


def test_images_decode():
    pil = pytest.importorskip("PIL.Image")
    for path in _files(HANBIT / "images"):
        with pil.open(path) as img:
            img.load()
            assert img.width >= 600 and img.height >= 400, _rel(path)


# --------------------------------------------------------------------------- tables


def _csv(path: Path, delimiter: str = ",") -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def _sheet(path: Path, sheet: str) -> list[dict]:
    from python_calamine import CalamineWorkbook

    rows = CalamineWorkbook.from_path(str(path)).get_sheet_by_name(sheet).to_python()
    header = [str(h) for h in rows[0]]
    return [dict(zip(header, r, strict=True)) for r in rows[1:]]


def _sqlite(path: Path, table: str) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
    con.close()
    return rows


def _parquet(path: Path) -> list[dict]:
    import duckdb

    con = duckdb.connect()
    rel = con.sql(f"SELECT * FROM read_parquet('{path}')")
    cols = rel.columns
    rows = [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    con.close()
    return rows


@pytest.fixture(scope="module")
def hanbit():
    s = HANBIT / "structured"
    return {
        "customers": _csv(s / "customers.csv"),
        "contracts": _csv(s / "contracts.csv"),
        "invoices": _csv(s / "invoices.csv"),
        "products": _sheet(s / "products.xlsx", "제품"),
        "categories": _sheet(s / "products.xlsx", "카테고리"),
        "employees": _sqlite(s / "employees.sqlite", "employees"),
        "departments": _sqlite(s / "employees.sqlite", "departments"),
        "suppliers": _sheet(s / "suppliers.xls", "suppliers"),
        "payments": [
            json.loads(line) for line in (s / "payments.jsonl").read_text("utf-8").splitlines()
        ],
        "price_history": _csv(s / "price_history.tsv", "\t"),
        "orders": _parquet(s / "orders.parquet"),
    }


def test_hanbit_row_counts(hanbit):
    assert len(hanbit["customers"]) == 300
    assert len(hanbit["contracts"]) == 600
    assert len(hanbit["invoices"]) == 1500
    assert sum(len(rows) for rows in hanbit.values()) >= 4000


PRIMARY_KEYS = {
    "customers": "고객ID", "contracts": "계약ID", "invoices": "청구ID", "products": "제품코드",
    "categories": "카테고리코드", "employees": "사원ID", "departments": "부서코드",
    "suppliers": "공급사ID", "payments": "수납ID", "orders": "주문ID",
}  # fmt: skip
FOREIGN_KEYS = [
    ("contracts", "고객ID", "customers"),
    ("contracts", "담당자ID", "employees"),
    ("customers", "담당자ID", "employees"),
    ("invoices", "계약ID", "contracts"),
    ("orders", "계약ID", "contracts"),
    ("orders", "제품코드", "products"),
    ("products", "카테고리코드", "categories"),
    ("products", "공급사ID", "suppliers"),
    ("payments", "청구ID", "invoices"),
    ("price_history", "제품코드", "products"),
    ("employees", "부서코드", "departments"),
    ("departments", "부서장사원ID", "employees"),
]


@pytest.mark.parametrize("table", sorted(PRIMARY_KEYS))
def test_hanbit_primary_keys_are_unique(hanbit, table):
    keys = [row[PRIMARY_KEYS[table]] for row in hanbit[table]]
    assert all(keys) and len(keys) == len(set(keys))


@pytest.mark.parametrize(("table", "column", "target"), FOREIGN_KEYS)
def test_hanbit_foreign_keys_resolve(hanbit, table, column, target):
    known = {row[PRIMARY_KEYS[target]] for row in hanbit[target]}
    missing = {row[column] for row in hanbit[table]} - known
    assert not missing, f"{table}.{column} -> {target}: {sorted(missing)[:5]}"


def test_hanbit_values_follow_the_documents(hanbit):
    """The code formats and status values the regulations define."""
    assert all(re.fullmatch(r"C\d{3}", r["고객ID"]) for r in hanbit["customers"])
    assert all(re.fullmatch(r"K20(19|2[0-6])-\d{3}", r["계약ID"]) for r in hanbit["contracts"])
    assert all(re.fullmatch(r"INV-20\d{6}", r["청구ID"]) for r in hanbit["invoices"])
    assert all(re.fullmatch(r"P-\d{4}", r["제품코드"]) for r in hanbit["products"])
    assert all(re.fullmatch(r"O-20\d\d-\d{5}", r["주문ID"]) for r in hanbit["orders"])
    assert all(re.fullmatch(r"PAY-\d{6}", r["수납ID"]) for r in hanbit["payments"])
    assert {r["상태"] for r in hanbit["contracts"]} == {"진행중", "종료", "해지"}
    assert {r["수납상태"] for r in hanbit["invoices"]} == {"완납", "부분수납", "미수납", "연체"}
    for inv in hanbit["invoices"]:
        billed, paid = int(inv["청구금액"]), int(inv["수납금액"])
        assert 0 <= paid <= billed
        assert (inv["수납상태"] == "완납") == (paid == billed), inv
        assert "2019-01-01" <= inv["청구일"] <= "2026-12-31"
    paid_by_invoice: dict[str, int] = {}
    for p in hanbit["payments"]:
        paid_by_invoice[p["청구ID"]] = paid_by_invoice.get(p["청구ID"], 0) + p["수납금액"]
    for inv in hanbit["invoices"]:
        assert paid_by_invoice.get(inv["청구ID"], 0) == int(inv["수납금액"]), inv["청구ID"]
    for o in hanbit["orders"]:
        assert o["금액"] == o["수량"] * o["단가"]
    sales = {d["부서코드"] for d in hanbit["departments"] if d["부서명"].startswith("영업")}
    team = {e["사원ID"]: e["부서코드"] for e in hanbit["employees"]}
    assert {team[c["담당자ID"]] for c in hanbit["customers"]} <= sales


def test_quickstart_foreign_keys_resolve():
    s = QUICKSTART / "structured"
    customers = {r["고객ID"] for r in _csv(s / "customers.csv")}
    contracts = _csv(s / "contracts.csv")
    assert {r["고객ID"] for r in contracts} <= customers
    assert {r["계약ID"] for r in _csv(s / "invoices.csv")} <= {r["계약ID"] for r in contracts}


def test_generator_reproduces_the_committed_tables(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "gen_samples", REPO_ROOT / "scripts" / "gen_samples.py"
    )
    gen = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "gen_samples", gen)  # dataclasses need it
    spec.loader.exec_module(gen)
    data = gen.build_data()
    for name in ("customers", "contracts", "invoices"):
        buf = io.StringIO()
        rows = getattr(data, name)
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        assert buf.getvalue().encode() == (HANBIT / "structured" / f"{name}.csv").read_bytes(), name


# --------------------------------------------------------------------------- documents


def _text(doc: readers.Document) -> str:
    return "\n".join(f"{s.path}\n{s.text}" for s in doc.sections)


def _engine() -> kordoc.KordocEngine:
    try:
        return kordoc.resolve_engine(REPO_ROOT / "data", env=_KORDOC_ENV)
    except kordoc.KordocUnavailable as exc:
        pytest.skip(f"kordoc not available: {exc.message}")


def _korean_words(text: str) -> int:
    return sum(1 for w in text.split() if re.search("[가-힣]", w))


@pytest.mark.parametrize("name", sorted(KEY_TERMS))
def test_document_text_contains_key_terms(name):
    path = HANBIT / "documents" / name
    if path.suffix.lower() in formats.DOCUMENT_NATIVE_SUFFIXES:
        doc = readers.read_document(path)
    else:
        doc = readers.read_document(path, engine=_engine())
    text = _text(doc)
    missing = [t for t in KEY_TERMS[name] if t not in text]
    assert not missing, f"{name}: {missing}"
    if name not in ("공지사항.txt", "담당자연락망.csv", "사업수행계획서.hwp"):
        assert _korean_words(text) >= 600, (name, _korean_words(text))


def test_quality_procedure_defines_document_only_concepts(hanbit):
    """검사/불량/시정조치 exist only in the documents, not as tables or columns."""
    columns = {c for rows in hanbit.values() for c in rows[0]}
    tables = set(hanbit)
    for concept in ("검사", "불량", "시정조치"):
        assert not any(concept in name for name in columns | tables)
    text = readers.read_markdown(REPO_ROOT / "scripts" / "sample_sources" / "품질관리절차서.md")
    assert all(concept in _text(text) for concept in ("검사", "불량", "시정조치"))


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(OCR_TERMS))
def test_image_ocr_contains_key_terms(name):
    from autoontology.config import Settings

    home = Settings.from_env().home
    engine = kordoc.resolve_engine(home)
    if not kordoc.ocr_models_status(home, engine=engine)["ready"]:
        pytest.skip("OCR models missing: uv run autoontology tools install-kordoc")
    text = _text(readers.read_document(HANBIT / "images" / name, engine=engine))
    missing = [t for t in OCR_TERMS[name] if t not in text]
    assert not missing, f"{name}: {missing}\n{text[:400]}"
