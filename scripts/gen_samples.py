# SPDX-License-Identifier: MIT
"""Generate the sample data sets under samples/.

    uv run --group dev python scripts/gen_samples.py              # samples/hanbit
    uv run --group dev python scripts/gen_samples.py --quickstart # also re-render the
                                                                  # quickstart PDF/DOCX

``samples/hanbit`` is a fictional Korean trading company (한빛상사) built from a fixed seed:
nine related tables in seven file formats, documents in eleven formats that define the
same business concepts, and six images for OCR. Every foreign key resolves.

Needs macOS (``/usr/bin/textutil`` for ``.doc``, the AppleGothic font), the dev dependency
group (pillow, matplotlib, python-pptx, python-docx, openpyxl, xlwt, pymupdf) and kordoc
for ``.hwpx`` (``KORDOC_CLI`` or ``<AUTOONTOLOGY_HOME>/kordoc``). The run is idempotent:
timestamps inside the generated files are pinned and a file is only rewritten when its
bytes change. It prints a manifest (path, bytes, rows/pages) at the end.

``samples/quickstart`` is committed as is; ``--quickstart`` re-renders its PDF and DOCX
from the Markdown sources (the bytes then differ from the committed ones).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
HANBIT = SAMPLES / "hanbit"
QUICKSTART = SAMPLES / "quickstart"
SOURCES = Path(__file__).resolve().parent / "sample_sources"
FONT = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
FONT_SD = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
SEED = 20260101
AS_OF = dt.date(2026, 9, 30)  # the data's "today"
PINNED = dt.datetime(2026, 10, 1, 9, 0, 0)
IMAGE_MAX_BYTES = 400 * 1024

# --------------------------------------------------------------------------- word lists

# fmt: off
SURNAMES = list(
    "김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허남심노하곽성차주우구민유나진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
)
GIVEN = [
    "민준", "서연", "도윤", "하은", "시우", "지우", "서준", "하윤", "예준", "수아", "주원", "지민", "하준", "채원", "지호",
    "다은", "준서", "윤서", "현우", "서아", "건우", "지유", "우진", "은서", "선우", "예은", "유준", "소윤", "연우", "가은",
    "정우", "민서", "승현", "수빈", "태윤", "나연", "재원", "하린", "동현", "유나",
]
COMPANY_CORE = [
    "가람", "푸른", "한결", "다온", "새봄", "미래", "동해", "서해", "대성", "한마음", "금강", "백두", "태백", "청솔", "늘봄",
    "은하", "우리", "세진", "삼우", "동진", "새솔", "신일", "여울", "해솔", "온빛", "누리", "하늘", "보람", "참빛", "한울",
]
COMPANY_KIND = [
    "산업", "물산", "건설", "테크", "정밀", "유통", "기계", "철강", "전기", "화학", "공업", "물류", "개발",
]
SUPPLIER_CORE = [
    "대양", "성원", "동방", "솔빛", "가온", "중앙", "다솜", "제일", "우성", "광명", "신화", "새벽", "경동", "화신", "진흥",
    "남양", "동녘", "세원", "영진", "태광",
]
SUPPLIER_KIND = ["정공", "산업", "화학", "금속", "전자", "케미칼", "상사", "기공"]
INDUSTRIES = ["도소매", "제조", "건설", "물류", "공공기관"]
INDUSTRY_WEIGHTS = [30, 35, 20, 10, 5]
REGIONS = {
    "D07": ["서울", "경기", "인천", "강원"],
    "D08": ["부산", "대구", "울산", "경북", "경남"],
    "D09": ["광주", "대전", "충북", "충남", "전북", "전남", "제주"],
}
REGION_WEIGHTS = {
    "서울": 14,
    "경기": 14,
    "인천": 5,
    "강원": 2,
    "부산": 7,
    "대구": 5,
    "울산": 4,
    "경북": 4,
    "경남": 6,
    "광주": 4,
    "대전": 4,
    "충북": 3,
    "충남": 4,
    "전북": 3,
    "전남": 3,
    "제주": 1,
}
CONTRACT_KINDS = ["정기 납품", "자재 공급", "일시 납품", "연간 단가 계약", "현장 자재 공급"]
CATEGORIES = [
    (
        "CAT-01",
        "배관자재",
        "배관, 이음관, 밸브 등 유체 이송 자재",
        ["PVC 배관", "스테인리스 배관", "엘보 이음관", "볼밸브", "플랜지"],
        "m",
    ),
    (
        "CAT-02",
        "공구류",
        "전동 공구와 수공구",
        ["전동 드릴", "임팩트 렌치", "앵글 그라인더", "토크 렌치", "공구 세트"],
        "개",
    ),
    (
        "CAT-03",
        "안전용품",
        "개인 보호구와 현장 안전 장비",
        ["안전모", "안전화", "보안경", "방진 마스크", "안전대"],
        "개",
    ),
    (
        "CAT-04",
        "체결부품",
        "볼트, 너트 등 체결용 부품",
        ["육각 볼트", "육각 너트", "평 와셔", "앵커 볼트", "블라인드 리벳"],
        "박스",
    ),
    (
        "CAT-05",
        "전기자재",
        "배선과 배전 자재",
        ["전선 케이블", "누전 차단기", "배선 덕트", "분전반", "접지봉"],
        "개",
    ),
    (
        "CAT-06",
        "공압유압",
        "공압, 유압 기기와 부품",
        ["에어 호스", "공압 실린더", "솔레노이드 밸브", "유압 펌프", "원터치 커플러"],
        "개",
    ),
    (
        "CAT-07",
        "동력전달",
        "베어링, 체인, 벨트 등 동력 전달 부품",
        ["볼 베어링", "롤러 체인", "V벨트", "기어 감속기", "축 커플링"],
        "개",
    ),
    (
        "CAT-08",
        "포장자재",
        "출하와 보관을 위한 포장 자재",
        ["스트레치 필름", "골판지 상자", "플라스틱 팔레트", "에어 완충재", "결속 밴드"],
        "롤",
    ),
    (
        "CAT-09",
        "용접자재",
        "용접 소모품과 보호구",
        ["피복 용접봉", "용접 와이어", "자동 용접 마스크", "용접 토치", "용접 장갑"],
        "박스",
    ),
    (
        "CAT-10",
        "윤활제",
        "윤활유와 방청제",
        ["다목적 그리스", "수용성 절삭유", "유압 작동유", "방청 윤활제", "기어 오일"],
        "통",
    ),
    (
        "CAT-11",
        "측정기기",
        "치수, 압력 등 측정 기기",
        ["버니어 캘리퍼스", "외측 마이크로미터", "레이저 거리계", "디지털 수평계", "압력 게이지"],
        "개",
    ),
    (
        "CAT-12",
        "청소위생",
        "산업 현장 청소와 위생 용품",
        ["산업용 와이퍼", "유흡착포", "중성 세척제", "집진기 필터", "폐기물 봉투"],
        "박스",
    ),
]
GRADES = ["표준형", "고급형"]
# (code, name, parent, location, headcount, head rank)
DEPARTMENTS = [
    ("D01", "대표이사실", "", "본사 5층", 2, "대표이사"),
    ("D02", "경영지원본부", "D01", "본사 4층", 1, "본부장"),
    ("D03", "영업본부", "D01", "본사 3층", 1, "본부장"),
    ("D04", "운영본부", "D01", "물류센터 2층", 1, "본부장"),
    ("D05", "재무회계팀", "D02", "본사 4층", 8, "부장"),
    ("D06", "인사총무팀", "D02", "본사 4층", 5, "차장"),
    ("D07", "영업1팀", "D03", "본사 3층", 10, "부장"),
    ("D08", "영업2팀", "D03", "부산지점", 9, "부장"),
    ("D09", "영업3팀", "D03", "대전지점", 8, "차장"),
    ("D10", "구매팀", "D04", "물류센터 2층", 7, "부장"),
    ("D11", "품질관리팀", "D04", "물류센터 1층", 9, "차장"),
    ("D12", "물류팀", "D04", "물류센터 1층", 11, "부장"),
    ("D13", "고객지원팀", "D03", "본사 2층", 8, "차장"),
]
RANKS = ["사원", "대리", "과장", "차장"]
RANK_WEIGHTS = [35, 30, 22, 13]
SALES_TEAMS = ("D07", "D08", "D09")
# fmt: on


# --------------------------------------------------------------------------- data model


def _date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randrange((end - start).days + 1))


def _add_months(day: dt.date, months: int) -> dt.date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    last = (dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(year, month, min(day.day, last))


def _round(value: float, unit: int) -> int:
    return max(unit, int(round(value / unit)) * unit)


def _iso(day: dt.date) -> str:
    return day.isoformat()


@dataclass
class Data:
    departments: list[dict]
    employees: list[dict]
    categories: list[dict]
    suppliers: list[dict]
    products: list[dict]
    price_history: list[dict]
    customers: list[dict]
    contracts: list[dict]
    invoices: list[dict]
    payments: list[dict]
    orders: list[dict]

    def by(self, table: str, key: str) -> dict:
        return {row[key]: row for row in getattr(self, table)}


def _people(rng: random.Random, count: int) -> list[str]:
    names = sorted({s + g for s in SURNAMES[:30] for g in GIVEN})
    return rng.sample(names, count)


def build_data() -> Data:
    rng = random.Random(SEED)

    # departments and employees
    names = iter(_people(rng, 80))
    employees: list[dict] = []
    departments: list[dict] = []
    for code, name, parent, location, headcount, head_rank in DEPARTMENTS:
        first = len(employees) + 1
        for i in range(headcount):
            rank = head_rank if i == 0 else rng.choices(RANKS, RANK_WEIGHTS)[0]
            hired = (
                _date(rng, dt.date(2008, 1, 1), dt.date(2018, 12, 31))
                if i == 0
                else _date(rng, dt.date(2012, 1, 1), dt.date(2026, 3, 31))
            )
            employees.append(
                {
                    "사원ID": f"E{len(employees) + 1:03d}",
                    "성명": next(names),
                    "부서코드": code,
                    "직급": rank,
                    "입사일": _iso(hired),
                    "내선번호": f"{int(code[1:]) * 100 + 1000 + i}",
                }
            )
        departments.append(
            {
                "부서코드": code,
                "부서명": name,
                "상위부서코드": parent or None,
                "부서장사원ID": f"E{first:03d}",
                "위치": location,
            }
        )
    # the team head (first member) approves; the other members are account managers
    reps = {
        team: [e["사원ID"] for e in employees if e["부서코드"] == team][1:] for team in SALES_TEAMS
    }

    # categories, suppliers, products, price history
    categories = [
        {"카테고리코드": code, "카테고리명": name, "설명": desc}
        for code, name, desc, _, _ in CATEGORIES
    ]
    supplier_names = rng.sample(sorted({c + k for c in SUPPLIER_CORE for k in SUPPLIER_KIND}), 40)
    suppliers: list[dict] = []
    by_category: dict[str, list[str]] = defaultdict(list)
    for i, name in enumerate(supplier_names):
        code, cat_name = CATEGORIES[i % len(CATEGORIES)][:2]
        sid = f"S{i + 1:03d}"
        suppliers.append(
            {
                "공급사ID": sid,
                "공급사명": f"{name}(주)" if i % 3 else f"주식회사 {name}",
                "지역": rng.choices(list(REGION_WEIGHTS), list(REGION_WEIGHTS.values()))[0],
                "주요품목": cat_name,
                "거래시작일": _iso(_date(rng, dt.date(2012, 1, 1), dt.date(2022, 12, 31))),
                "평가등급": rng.choices(["A", "B", "C"], [45, 40, 15])[0],
            }
        )
        by_category[code].append(sid)
    products: list[dict] = []
    price_history: list[dict] = []
    change_days = [
        dt.date(2021, 1, 1),
        dt.date(2023, 4, 1),
        dt.date(2025, 1, 1),
        dt.date(2026, 10, 1),
    ]
    reasons = ["원자재 가격 상승", "공급사 단가 조정", "환율 변동", "물량 할인 반영"]
    for code, _, _, items, unit in CATEGORIES:
        for item in items:
            base = math.exp(rng.uniform(math.log(3_000), math.log(600_000)))
            for g, grade in enumerate(GRADES):
                pcode = f"P-{1001 + len(products)}"
                price = _round(base * (1.0 if g == 0 else rng.uniform(1.4, 1.9)), 100)
                price_history.append(
                    {
                        "제품코드": pcode,
                        "적용일": "2019-01-01",
                        "단가": price,
                        "변경사유": "최초 등록",
                    }
                )
                for day in sorted(rng.sample(change_days, rng.randint(1, 3))):
                    reason = rng.choice(reasons)
                    factor = (
                        rng.uniform(0.92, 0.98)
                        if reason == "물량 할인 반영"
                        else rng.uniform(1.03, 1.12)
                    )
                    price = _round(price * factor, 100)
                    price_history.append(
                        {"제품코드": pcode, "적용일": _iso(day), "단가": price, "변경사유": reason}
                    )
                products.append(
                    {
                        "제품코드": pcode,
                        "제품명": f"{item} {grade}",
                        "카테고리코드": code,
                        "단위": unit,
                        "표준단가": price,
                        "공급사ID": rng.choice(by_category[code]),
                    }
                )
    prices: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in price_history:
        prices[row["제품코드"]].append((row["적용일"], row["단가"]))

    def price_on(pcode: str, day: dt.date) -> int:
        current = prices[pcode][0][1]
        for start, value in prices[pcode]:
            if start <= day.isoformat():
                current = value
        return current

    # customers
    customer_names = rng.sample(sorted({c + k for c in COMPANY_CORE for k in COMPANY_KIND}), 300)
    region_team = {r: team for team, regions in REGIONS.items() for r in regions}
    customers: list[dict] = []
    for i, name in enumerate(customer_names):
        region = rng.choices(list(REGION_WEIGHTS), list(REGION_WEIGHTS.values()))[0]
        customers.append(
            {
                "고객ID": f"C{i + 1:03d}",
                "고객명": f"주식회사 {name}" if i % 2 else f"{name}(주)",
                "업종": rng.choices(INDUSTRIES, INDUSTRY_WEIGHTS)[0],
                "지역": region,
                "신용등급": rng.choices(["A", "B", "C", "D"], [25, 40, 25, 10])[0],
                "담당자ID": rng.choice(reps[region_team[region]]),
                "등록일": _iso(_date(rng, dt.date(2019, 1, 2), dt.date(2025, 12, 31))),
            }
        )

    # contracts: every customer has one, 300 more go to random customers
    owners = [c for c in customers] + rng.choices(customers, k=300)
    drafts = []
    for n, cust in enumerate(owners):
        registered = dt.date.fromisoformat(cust["등록일"])
        if n < len(customers):  # the first contract follows registration closely
            start = registered + dt.timedelta(days=rng.randrange(120))
        else:
            start = _date(rng, registered, max(registered, dt.date(2026, 8, 31)))
        months = rng.choice([6, 12, 12, 12, 24, 24, 36])
        end = _add_months(start, months) - dt.timedelta(days=1)
        drafts.append((start, cust, end))
    drafts.sort(key=lambda d: (d[0], d[1]["고객ID"]))
    contracts: list[dict] = []
    seq: dict[int, int] = defaultdict(int)
    for start, cust, end in drafts:
        seq[start.year] += 1
        team = region_team[cust["지역"]]
        manager = cust["담당자ID"] if rng.random() < 0.9 else rng.choice(reps[team])
        if end < AS_OF:
            status = "해지" if rng.random() < 0.06 else "종료"
        else:
            status = "해지" if rng.random() < 0.03 and start < dt.date(2025, 6, 1) else "진행중"
        core = re.sub(r"^주식회사 |\(주\)$", "", cust["고객명"])
        contracts.append(
            {
                "계약ID": f"K{start.year}-{seq[start.year]:03d}",
                "고객ID": cust["고객ID"],
                "계약명": f"{core} {rng.choice(CONTRACT_KINDS)}",
                "계약금액": _round(math.exp(rng.uniform(math.log(1e7), math.log(6e8))), 1_000_000),
                "시작일": _iso(start),
                "종료일": _iso(end),
                "상태": status,
                "담당자ID": manager,
            }
        )

    # invoices: 1,500 over the contracts' billed periods
    counts = [1] * len(contracts)
    weights = []
    for c in contracts:
        start = dt.date.fromisoformat(c["시작일"])
        last = min(dt.date.fromisoformat(c["종료일"]), AS_OF)
        weights.append(max(1, (last - start).days))
    for idx in rng.choices(range(len(contracts)), weights, k=1500 - len(contracts)):
        counts[idx] += 1
    drafts_inv = []
    for c, n in zip(contracts, counts, strict=True):
        start = dt.date.fromisoformat(c["시작일"])
        last = min(dt.date.fromisoformat(c["종료일"]), AS_OF)
        if c["상태"] == "해지":
            last = min(last, start + (last - start) / 2)
        days = sorted(
            _date(rng, start + dt.timedelta(days=min(14, (last - start).days)), last)
            for _ in range(n)
        )
        share = rng.uniform(0.55, 0.95) * c["계약금액"]
        parts = [rng.uniform(0.6, 1.4) for _ in range(n)]
        total = sum(parts)
        for day, part in zip(days, parts, strict=True):
            drafts_inv.append((day, c, _round(share * part / total, 10_000)))
    drafts_inv.sort(key=lambda d: (d[0], d[1]["계약ID"]))
    customers_by_id = {c["고객ID"]: c for c in customers}
    invoices: list[dict] = []
    payments_draft: list[tuple[dt.date, str, int, str]] = []
    seq = defaultdict(int)
    for day, c, amount in drafts_inv:
        seq[day.year] += 1
        due = day + dt.timedelta(days=30)
        late = (AS_OF - due).days
        grade = customers_by_id[c["고객ID"]]["신용등급"]
        r = rng.random()
        if c["상태"] == "해지" and late > 30:
            paid = rng.choice([0, _round(amount * rng.uniform(0.2, 0.7), 10_000)])
        elif late > 30:
            p_full = {"A": 0.97, "B": 0.93, "C": 0.86, "D": 0.7}[grade]
            paid = (
                amount
                if r < p_full
                else rng.choice([0, _round(amount * rng.uniform(0.2, 0.8), 10_000)])
            )
        elif late >= 0:
            paid = amount if r < 0.7 else _round(amount * rng.uniform(0.2, 0.8), 10_000)
        else:
            paid = (
                amount
                if r < 0.35
                else (0 if r < 0.75 else _round(amount * rng.uniform(0.2, 0.8), 10_000))
            )
        paid = min(paid, amount)
        if paid == amount:
            status = "완납"
        elif late > 30:
            status = "연체"
        elif paid > 0:
            status = "부분수납"
        else:
            status = "미수납"
            if late >= 0:  # past due with nothing paid yet: partial within 30 days
                paid = _round(amount * 0.3, 10_000)
                status = "부분수납"
        iid = f"INV-{day.year}{seq[day.year]:04d}"
        invoices.append(
            {
                "청구ID": iid,
                "계약ID": c["계약ID"],
                "청구일": _iso(day),
                "납기일": _iso(due),
                "청구금액": amount,
                "수납금액": paid,
                "수납상태": status,
            }
        )
        if paid:
            n = 2 if (paid < amount or rng.random() < 0.15) and paid >= 20_000 else 1
            first = _round(paid * rng.uniform(0.3, 0.7), 10_000) if n == 2 else paid
            first = min(first, paid - 10_000) if n == 2 else first
            last_day = min(AS_OF, due + dt.timedelta(days=25))
            pay_days = sorted(
                _date(rng, min(day + dt.timedelta(days=3), last_day), last_day) for _ in range(n)
            )
            methods = ["계좌이체", "계좌이체", "계좌이체", "법인카드"] + (
                ["전자어음"] * 2 if grade in ("A", "B") else []
            )
            for pay_day, value in zip(pay_days, [first, paid - first][:n], strict=True):
                payments_draft.append((pay_day, iid, value, rng.choice(methods)))
    payments_draft.sort()
    payments = [
        {
            "수납ID": f"PAY-{i + 1:06d}",
            "청구ID": iid,
            "수납일": _iso(day),
            "수납금액": value,
            "수납방법": method,
        }
        for i, (day, iid, value, method) in enumerate(payments_draft)
    ]

    # orders: 900 lines, each on a product the contract covers, priced on the order day
    order_counts = [1] * len(contracts)
    for idx in rng.choices(range(len(contracts)), weights, k=900 - len(contracts)):
        order_counts[idx] += 1
    product_codes = [p["제품코드"] for p in products]
    drafts_ord = []
    for c, n in zip(contracts, order_counts, strict=True):
        start = dt.date.fromisoformat(c["시작일"])
        last = min(dt.date.fromisoformat(c["종료일"]), AS_OF)
        covered = rng.sample(product_codes, rng.randint(2, 4))
        for _ in range(n):
            drafts_ord.append((_date(rng, start, last), c["계약ID"], rng.choice(covered)))
    drafts_ord.sort()
    orders: list[dict] = []
    seq = defaultdict(int)
    for day, cid, pcode in drafts_ord:
        seq[day.year] += 1
        unit_price = price_on(pcode, day)
        qty = max(1, int(rng.uniform(2e5, 2e7) / unit_price))
        qty = _round(qty, 10) if qty > 50 else qty
        orders.append(
            {
                "주문ID": f"O-{day.year}-{seq[day.year]:05d}",
                "계약ID": cid,
                "제품코드": pcode,
                "주문일": _iso(day),
                "수량": qty,
                "단가": unit_price,
                "금액": qty * unit_price,
            }
        )

    return Data(
        departments,
        employees,
        categories,
        suppliers,
        products,
        price_history,
        customers,
        contracts,
        invoices,
        payments,
        orders,
    )


# --------------------------------------------------------------------------- file helpers

WRITTEN: list[Path] = []


def write_bytes(path: Path, data: bytes) -> None:
    """Write *data* unless the file already holds exactly these bytes (idempotent runs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)
    WRITTEN.append(path)


_CORE_DATE = re.compile(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)")


def pinned_zip(data: bytes) -> bytes:
    """Rewrite an OOXML/HWPX zip with fixed entry times and pinned core-property dates."""
    stamp = PINNED.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = _CORE_DATE.sub(lambda m: m.group(1) + stamp + m.group(3), body)
            entry = zipfile.ZipInfo(info.filename, date_time=PINNED.timetuple()[:6])
            entry.compress_type = info.compress_type
            entry.external_attr = 0o644 << 16
            dst.writestr(entry, body)
    return out.getvalue()


def write_csv(path: Path, rows: list[dict], *, delimiter: str = ",") -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_bytes(path, buf.getvalue().encode("utf-8"))


def _tmp(suffix: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(name)


def write_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for i, column in enumerate(zip(*rows, strict=False)):
            width = max(len(str(v)) for v in column if v is not None)
            ws.column_dimensions[openpyxl.utils.get_column_letter(i + 1)].width = min(
                60, 4 + width * 1.6
            )
    wb.properties.creator = "AutoOntology samples"
    wb.properties.created = wb.properties.modified = PINNED
    buf = io.BytesIO()
    wb.save(buf)
    write_bytes(path, pinned_zip(buf.getvalue()))


def write_xls(path: Path, sheets: dict[str, list[list]]) -> None:
    import xlwt

    wb = xlwt.Workbook(encoding="utf-8")
    bold = xlwt.easyxf("font: bold on")
    for title, rows in sheets.items():
        ws = wb.add_sheet(title)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if value is not None:
                    ws.write(r, c, value, bold if r == 0 else xlwt.Style.default_style)
    buf = io.BytesIO()
    wb.save(buf)
    write_bytes(path, buf.getvalue())


def table(rows: list[dict]) -> list[list]:
    return [list(rows[0])] + [list(r.values()) for r in rows]


# --------------------------------------------------------------------------- structured


def write_structured(data: Data, out: Path) -> None:
    write_csv(out / "customers.csv", data.customers)
    write_csv(out / "contracts.csv", data.contracts)
    write_csv(out / "invoices.csv", data.invoices)
    write_csv(out / "price_history.tsv", data.price_history, delimiter="\t")
    write_xlsx(
        out / "products.xlsx", {"제품": table(data.products), "카테고리": table(data.categories)}
    )
    write_xls(out / "suppliers.xls", {"suppliers": table(data.suppliers)})
    lines = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in data.payments)
    write_bytes(out / "payments.jsonl", lines.encode("utf-8"))

    tmp = _tmp(".sqlite")
    tmp.unlink()
    con = sqlite3.connect(tmp)
    con.execute(
        "CREATE TABLE departments (부서코드 TEXT PRIMARY KEY, 부서명 TEXT NOT NULL, "
        "상위부서코드 TEXT REFERENCES departments(부서코드), 부서장사원ID TEXT, 위치 TEXT)"
    )
    con.execute(
        "CREATE TABLE employees (사원ID TEXT PRIMARY KEY, 성명 TEXT NOT NULL, "
        "부서코드 TEXT NOT NULL REFERENCES departments(부서코드), 직급 TEXT, 입사일 TEXT, "
        "내선번호 TEXT)"
    )
    con.executemany(
        "INSERT INTO departments VALUES (?,?,?,?,?)", [tuple(d.values()) for d in data.departments]
    )
    con.executemany(
        "INSERT INTO employees VALUES (?,?,?,?,?,?)", [tuple(e.values()) for e in data.employees]
    )
    con.commit()
    con.close()
    write_bytes(out / "employees.sqlite", tmp.read_bytes())
    tmp.unlink()

    import duckdb

    tmp = _tmp(".parquet")
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE orders (주문ID VARCHAR, 계약ID VARCHAR, 제품코드 VARCHAR, 주문일 DATE, "
        "수량 INTEGER, 단가 BIGINT, 금액 BIGINT)"
    )
    con.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?)", [tuple(o.values()) for o in data.orders]
    )
    con.execute(f"COPY orders TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
    con.close()
    write_bytes(out / "orders.parquet", tmp.read_bytes())
    tmp.unlink()


# --------------------------------------------------------------------------- documents


def korean_words(text: str) -> int:
    """Whitespace-separated tokens that contain Hangul (the "≥ 600 words" rule)."""
    return sum(1 for w in text.split() if re.search("[가-힣]", w))


def md_html(text: str) -> str:
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark").enable("table").render(text)


def pdf_bytes(text: str, title: str, *, font_pt: float = 10.5, extra_css: str = "") -> bytes:
    """Markdown -> A4 PDF (pymupdf Story, AppleGothic subset), with pinned metadata."""
    import pymupdf

    css = (
        "@font-face {font-family: kr; src: url(%s);} "
        "* {font-family: kr; font-size: %spt; line-height: 1.5;} "
        "h1 {font-size: 18pt;} h2 {font-size: 14pt;} h3 {font-size: 12pt;} "
        "table {border-collapse: collapse;} td, th {border: 1px solid #444; padding: 3px;}"
        % (FONT.name, font_pt)
    ) + extra_css
    story = pymupdf.Story(
        html=md_html(text), user_css=css, archive=pymupdf.Archive(str(FONT.parent))
    )
    raw = io.BytesIO()
    writer = pymupdf.DocumentWriter(raw)
    page = pymupdf.paper_rect("a4")
    where = page + (56, 56, -56, -56)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    doc = pymupdf.open("pdf", raw.getvalue())
    stamp = PINNED.strftime("D:%Y%m%d%H%M%S")
    doc.set_metadata(
        {
            "title": title,
            "author": "AutoOntology samples",
            "creationDate": stamp,
            "modDate": stamp,
            "producer": "AutoOntology samples",
            "creator": "AutoOntology samples",
        }
    )
    doc.subset_fonts()  # the full AppleGothic face is ~15 MB
    data = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return data


_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def docx_bytes(text: str, title: str) -> bytes:
    """Markdown (headings, paragraphs, lists, GFM tables) -> DOCX with pinned properties."""
    import docx

    document = docx.Document()
    props = document.core_properties
    props.title, props.author, props.last_modified_by = title, "AutoOntology samples", ""
    props.created = props.modified = PINNED
    props.revision = 1
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            document.add_heading(heading.group(2), level=len(heading.group(1)))
        elif line.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            header = _cells(line)
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            grid = document.add_table(rows=1 + len(rows), cols=len(header))
            grid.style = "Table Grid"
            for r, values in enumerate([header, *rows]):
                for c, value in enumerate(values):
                    grid.cell(r, c).text = value
            continue
        elif line.startswith("- "):
            document.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            document.add_paragraph(line)
        i += 1
    buf = io.BytesIO()
    document.save(buf)
    return pinned_zip(buf.getvalue())


def kordoc_cli() -> Path:
    cli = os.environ.get("KORDOC_CLI")
    env_file = ROOT / ".env"
    if not cli and env_file.is_file():
        found = re.search(r"^KORDOC_CLI=(.+)$", env_file.read_text("utf-8"), re.M)
        cli = found.group(1).strip() if found else None
    candidates = [Path(cli)] if cli else []
    home = Path(os.environ.get("AUTOONTOLOGY_HOME", ROOT / "data"))
    candidates.append(home / "kordoc" / "node_modules" / "kordoc" / "dist" / "cli.js")
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit(
        "kordoc not found: set KORDOC_CLI or run `uv run autoontology tools "
        "install-kordoc` (needed for .hwpx)"
    )


def hwpx_bytes(text: str) -> bytes:
    src, out = _tmp(".md"), _tmp(".hwpx")
    try:
        src.write_text(text, encoding="utf-8")
        subprocess.run(
            ["node", str(kordoc_cli()), "generate", str(src), "-o", str(out)],
            check=True,
            capture_output=True,
        )
        return pinned_zip(out.read_bytes())
    finally:
        src.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


def doc_bytes(docx_data: bytes) -> bytes:
    """DOCX -> Word 97-2003 .doc with macOS textutil."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.docx"
        src.write_bytes(docx_data)
        subprocess.run(
            [
                "/usr/bin/textutil",
                "-convert",
                "doc",
                "-output",
                str(Path(tmp) / "out.doc"),
                str(src),
            ],
            check=True,
            capture_output=True,
        )
        return (Path(tmp) / "out.doc").read_bytes()


def parse_slides(text: str) -> tuple[str, str, list[dict]]:
    """``# title`` / subtitle line / ``## slide`` sections with ``- bullets`` and one table."""
    title, subtitle, slides = "", "", []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("# "):
            title = line[2:]
        elif line.startswith("## "):
            slides.append({"title": line[3:], "bullets": [], "table": []})
        elif line.startswith("- ") and slides:
            slides[-1]["bullets"].append(line[2:])
        elif line.startswith("|") and slides:
            if not _TABLE_SEP.match(line):
                slides[-1]["table"].append(_cells(line))
        elif line.strip() and not slides:
            subtitle = line.strip()
        i += 1
    return title, subtitle, slides


def pptx_bytes(text: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Emu, Pt

    title, subtitle, slides = parse_slides(text)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = title
    first.placeholders[1].text = subtitle
    for spec in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        slide.shapes.title.text = spec["title"]
        slide.shapes.title.text_frame.paragraphs[0].font.size = Pt(30)
        box = slide.shapes.add_textbox(Emu(600000), Emu(1500000), Emu(6400000), Emu(4800000))
        frame = box.text_frame
        frame.word_wrap = True
        for n, bullet in enumerate(spec["bullets"]):
            para = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
            para.text = "• " + bullet
            para.font.size = Pt(15)
            para.space_after = Pt(8)
        rows = spec["table"]
        if rows:
            shape = slide.shapes.add_table(
                len(rows),
                len(rows[0]),
                Emu(7200000),
                Emu(1600000),
                Emu(4500000),
                Emu(380000 * len(rows)),
            )
            for r, values in enumerate(rows):
                for c, value in enumerate(values):
                    cell = shape.table.cell(r, c)
                    cell.text = value
                    cell.text_frame.paragraphs[0].font.size = Pt(12)
    props = prs.core_properties
    props.title, props.author, props.last_modified_by = title, "AutoOntology samples", ""
    props.created = props.modified = PINNED
    props.revision = 1
    buf = io.BytesIO()
    prs.save(buf)
    return pinned_zip(buf.getvalue())


def faq_html(text: str) -> bytes:
    title = text.splitlines()[0].lstrip("# ").strip()
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>body {{font-family: sans-serif; max-width: 48rem; margin: 2rem auto; line-height: 1.6}}
h3 {{margin-bottom: .2rem}}</style>
<script>document.addEventListener("DOMContentLoaded", () => console.log("faq"));</script>
</head>
<body>
<nav><a href="#">홈</a> | <a href="#">제품 안내</a> | <a href="#">고객지원</a></nav>
<main>
{md_html(text)}</main>
<footer>한빛상사 고객지원팀 · 이 페이지는 AutoOntology 예제용 가상 회사의 안내문입니다.</footer>
</body>
</html>
"""
    return page.encode("utf-8")


def won(value: float) -> str:
    return f"{value / 1e8:,.1f}억원"


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def quarter(day: str) -> str:
    return f"{day[:4]}년 {(int(day[5:7]) - 1) // 3 + 1}분기"


def report_text(data: Data) -> str:
    """분기실적보고: the numbers come from the generated tables."""
    contracts = data.by("contracts", "계약ID")
    customers = data.by("customers", "고객ID")
    employees = data.by("employees", "사원ID")
    departments = data.by("departments", "부서코드")
    products = data.by("products", "제품코드")
    categories = data.by("categories", "카테고리코드")

    by_q: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for inv in data.invoices:
        if inv["청구일"] >= "2025-01-01":
            agg = by_q[quarter(inv["청구일"])]
            agg[0] += 1
            agg[1] += inv["청구금액"]
            agg[2] += inv["수납금액"]
    this_year = [i for i in data.invoices if i["청구일"].startswith("2026")]

    def total(rows, key):
        return sum(r[key] for r in rows)

    billed, paid = total(this_year, "청구금액"), total(this_year, "수납금액")
    q3, q2 = by_q["2026년 3분기"], by_q["2026년 2분기"]

    by_team: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    by_industry: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for inv in this_year:
        c = contracts[inv["계약ID"]]
        team = departments[employees[c["담당자ID"]]["부서코드"]]["부서명"]
        for agg in (by_team[team], by_industry[customers[c["고객ID"]]["업종"]]):
            agg[0] += 1
            agg[1] += inv["청구금액"]
            agg[2] += inv["수납금액"]
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in data.orders:
        if o["주문일"].startswith("2026"):
            agg = by_cat[categories[products[o["제품코드"]]["카테고리코드"]]["카테고리명"]]
            agg[0] += 1
            agg[1] += o["금액"]
    statuses: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for inv in this_year:
        statuses[inv["수납상태"]][0] += 1
        statuses[inv["수납상태"]][1] += inv["청구금액"] - inv["수납금액"]
    overdue: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for inv in data.invoices:
        if inv["수납상태"] == "연체":
            cid = contracts[inv["계약ID"]]["고객ID"]
            overdue[cid][0] += 1
            overdue[cid][1] += inv["청구금액"] - inv["수납금액"]
    top_overdue = sorted(overdue.items(), key=lambda kv: (-kv[1][1], kv[0]))[:5]
    teams = sorted(by_team.items(), key=lambda kv: kv[0])
    best_team = max(teams, key=lambda kv: kv[1][1])[0]
    cats = sorted(by_cat.items(), key=lambda kv: (-kv[1][1], kv[0]))[:6]
    industries = sorted(by_industry.items(), key=lambda kv: (-kv[1][1], kv[0]))
    active = sum(1 for c in data.contracts if c["상태"] == "진행중")
    new_2026 = sum(1 for c in data.contracts if c["시작일"].startswith("2026"))
    overdue_total = sum(v[1] for v in overdue.values())

    out = [
        "# 2026년 3분기 실적 보고",
        "",
        f"기준일 {AS_OF.isoformat()} · 작성 경영지원본부 재무회계팀 · 보고 대상 대표이사",
        "",
        "## 요약",
        f"- 2026년 1월부터 9월까지 청구서 {len(this_year):,}건, 청구금액 {won(billed)}을 발행했고 "
        f"수납금액은 {won(paid)}으로 수납률은 {pct(paid, billed)}이다",
        f"- 3분기 청구금액은 {won(q3[1])}으로 2분기 {won(q2[1])}와 비교하여 "
        f"{'늘었다' if q3[1] >= q2[1] else '줄었다'}",
        f"- 기준일 현재 진행중인 계약은 {active}건이며 올해 새로 시작한 계약은 {new_2026}건이다",
        f"- 연체 청구서의 미수금 합계는 {won(overdue_total)}으로 연체 고객 관리가 계속 필요하다",
        "- 이 보고서의 금액은 모두 부가가치세를 제외한 원 단위 금액을 억원으로 줄여 표시하였다",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 청구서 건수 | {len(this_year):,}건 |",
        f"| 청구금액 | {won(billed)} |",
        f"| 수납금액 | {won(paid)} |",
        f"| 수납률 | {pct(paid, billed)} |",
        "",
        "## 분기별 청구와 수납 추이",
        "- 분기별 청구금액은 해당 분기에 발행한 청구서의 청구금액을 합한 값이다",
        "- 수납금액은 그 청구서들에 기준일까지 입금된 금액의 합계이므로 최근 분기일수록 수납률이 낮다",
        "- 납기일은 청구일부터 30일이므로 3분기 말에 발행한 청구서는 아직 납기일이 지나지 않았다",
        "- 정기 납품 계약은 매월 청구하므로 분기별 청구 건수의 변화는 주문 실적의 변화와 함께 움직인다",
        "- 2025년 같은 분기와 비교하면 계약 갱신이 늘어난 분기에 청구금액도 함께 늘었다",
        "",
        "| 분기 | 청구 건수 | 청구금액 | 수납금액 | 수납률 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for q in sorted(by_q):
        n, b, p = by_q[q]
        out.append(f"| {q} | {n}건 | {won(b)} | {won(p)} | {pct(p, b)} |")
    out += [
        "",
        "## 영업팀별 실적",
        "- 영업팀별 실적은 계약의 담당자ID가 속한 부서를 기준으로 집계하였다",
        f"- 올해 청구금액이 가장 많은 팀은 {best_team}이며, 대형 건설 계약의 정기 청구가 큰 몫을 차지했다",
        "- 영업1팀은 서울·경기·인천·강원, 영업2팀은 영남, 영업3팀은 호남·충청·제주 지역의 고객을 맡는다",
        "- 수납률이 낮은 팀은 연체 고객 주간 보고를 바탕으로 담당자별 방문 계획을 세운다",
        "",
        "| 영업팀 | 청구 건수 | 청구금액 | 수납률 |",
        "| --- | --- | --- | --- |",
    ]
    for team, (n, b, p) in teams:
        out.append(f"| {team} | {n}건 | {won(b)} | {pct(p, b)} |")
    out += [
        "",
        "## 업종별 고객 실적",
        "- 업종은 고객 등록 때 정한 도소매, 제조, 건설, 물류, 공공기관 다섯 가지 분류를 따른다",
        "- 제조 업종 고객이 고객 수와 청구금액 모두에서 가장 큰 비중을 차지한다",
        "- 공공기관 고객은 건수는 적지만 연체가 거의 없어 수납률이 높다",
        "- 건설 업종은 현장 일정에 따라 주문이 몰리므로 분기마다 청구금액의 변동이 크다",
        "- 도소매 업종은 주문 한 건의 금액은 작지만 주문 건수가 많아 출고 업무의 부담이 크다",
        "",
        "| 업종 | 청구 건수 | 청구금액 | 수납률 |",
        "| --- | --- | --- | --- |",
    ]
    for name, (n, b, p) in industries:
        out.append(f"| {name} | {n}건 | {won(b)} | {pct(p, b)} |")
    out += [
        "",
        "## 카테고리별 주문 실적",
        "- 주문 실적은 올해 접수한 주문의 금액을 제품 카테고리별로 합한 값이다",
        "- 주문 금액은 주문일에 유효한 단가표의 단가에 수량을 곱하여 계산한다",
        "- 하반기 주력 제품으로 정한 배관자재, 공구류, 안전용품의 주문 추이를 다음 분기에도 계속 살핀다",
        "- 10월 1일 단가표 개정 이후의 주문은 4분기 보고에서 새 단가로 집계한다",
        "- 주문 건수가 줄어든 카테고리는 구매팀과 함께 공급사 단가와 재고 수준을 다시 점검한다",
        "",
        "| 카테고리 | 주문 건수 | 주문 금액 |",
        "| --- | --- | --- |",
    ]
    for name, (n, b) in cats:
        out.append(f"| {name} | {n}건 | {won(b)} |")
    out += [
        "",
        "## 수납상태 현황",
        "- 올해 발행한 청구서를 기준일의 수납상태별로 나누어 건수와 미수금을 집계하였다",
        "- 완납은 청구금액을 모두 받은 청구서이고 부분수납은 일부만 받은 청구서이다",
        "- 미수납은 아직 입금이 없지만 납기일이 지나지 않은 청구서이다",
        "- 연체는 납기일이 30일 넘게 지났는데 대금을 다 받지 못한 청구서로, 독촉장을 발송하였다",
        "",
        "| 수납상태 | 청구 건수 | 미수금 |",
        "| --- | --- | --- |",
    ]
    for name in ("완납", "부분수납", "미수납", "연체"):
        n, rest = statuses.get(name, [0, 0])
        out.append(f"| {name} | {n}건 | {won(rest)} |")
    out += [
        "",
        "## 연체 상위 고객",
        "- 연체 미수금이 많은 고객 다섯 곳을 고객ID와 함께 정리하였다",
        "- 담당자는 이 고객들을 2주 안에 방문하여 지급 계획을 받고 상담 기록에 남긴다",
        "- 90일 이상 연체가 계속되는 고객은 계약관리규정에 따라 계약 해지와 신용등급 조정을 검토한다",
        "- 연체가 해소된 고객은 다음 보고에서 목록에서 빼고 그 사실을 따로 적는다",
        "",
        "| 고객ID | 고객명 | 연체 건수 | 미수금 |",
        "| --- | --- | --- | --- |",
    ]
    for cid, (n, rest) in top_overdue:
        out.append(f"| {cid} | {customers[cid]['고객명']} | {n}건 | {won(rest)} |")
    out += [
        "",
        "## 4분기 과제",
        "- 연체 미수금을 줄이기 위해 연체 고객 주간 보고를 계속하고 분할 수납 승인을 적극 활용한다",
        "- 10월 1일 단가표 개정 내용을 모든 진행중인 계약의 고객에게 다시 안내한다",
        "- 종료일이 다가오는 계약은 60일 전에 만료 예정 안내를 보내고 갱신 협의를 시작한다",
        "- 품질 클레임이 많았던 제품은 품질관리팀과 함께 출하검사 기준을 다시 점검한다",
        "- 영업팀별 목표 대비 실적은 다음 영업전략회의에서 다시 보고한다",
        "- 재무회계팀은 수납상태별 청구서 현황을 매월 10일까지 경영지원본부장에게 보고한다",
        "",
        "| 과제 | 담당 | 기한 |",
        "| --- | --- | --- |",
        "| 연체 고객 방문 | 각 영업팀 | 10월 31일 |",
        "| 단가표 개정 재안내 | 고객지원팀 | 10월 15일 |",
        "| 갱신 협의 시작 | 영업 담당자 | 11월 30일 |",
    ]
    return "\n".join(out) + "\n"


def org_sheets(data: Data) -> dict[str, list[list]]:
    names = data.by("employees", "사원ID")
    depts = data.by("departments", "부서코드")
    heads = defaultdict(int)
    for e in data.employees:
        heads[e["부서코드"]] += 1
    dept_rows = [["부서코드", "부서명", "상위부서", "부서장", "위치", "인원"]] + [
        [
            d["부서코드"],
            d["부서명"],
            depts[d["상위부서코드"]]["부서명"] if d["상위부서코드"] else "",
            names[d["부서장사원ID"]]["성명"],
            d["위치"],
            heads[d["부서코드"]],
        ]
        for d in data.departments
    ]
    staff = [["사원ID", "성명", "부서코드", "부서명", "직급", "입사일"]] + [
        [
            e["사원ID"],
            e["성명"],
            e["부서코드"],
            depts[e["부서코드"]]["부서명"],
            e["직급"],
            e["입사일"],
        ]
        for e in data.employees
    ]
    guide = [[line] for line in (SOURCES / "조직도_안내.txt").read_text("utf-8").splitlines()]
    return {"부서": dept_rows, "직원": staff, "안내": guide}


def price_sheets(data: Data) -> dict[str, list[list]]:
    cats = data.by("categories", "카테고리코드")
    since: dict[str, str] = {}
    for row in data.price_history:
        since[row["제품코드"]] = row["적용일"]
    rows = [["제품코드", "제품명", "카테고리", "단위", "단가", "공급사ID", "적용일"]] + [
        [
            p["제품코드"],
            p["제품명"],
            cats[p["카테고리코드"]]["카테고리명"],
            p["단위"],
            p["표준단가"],
            p["공급사ID"],
            since[p["제품코드"]],
        ]
        for p in data.products
    ]
    guide = [[line] for line in (SOURCES / "단가표_적용기준.txt").read_text("utf-8").splitlines()]
    return {"단가표": rows, "적용기준": guide}


def contacts_rows(data: Data) -> list[dict]:
    depts = data.by("departments", "부서코드")
    areas = {team: "·".join(regions) for team, regions in REGIONS.items()}
    return [
        {
            "사원ID": e["사원ID"],
            "성명": e["성명"],
            "부서명": depts[e["부서코드"]]["부서명"],
            "직급": e["직급"],
            "내선번호": e["내선번호"],
            "담당지역": areas.get(e["부서코드"], ""),
        }
        for e in data.employees
        if e["부서코드"] in SALES_TEAMS + ("D05", "D10", "D11", "D13")
    ]


def write_documents(data: Data, out: Path) -> dict[str, str]:
    """Write samples/hanbit/documents; returns the text of each document for the word check."""
    texts: dict[str, str] = {}
    contract = (out / "계약관리규정.md").read_text("utf-8")
    billing = (out / "청구수납규정.md").read_text("utf-8")
    texts["계약관리규정"] = contract
    texts["청구수납규정"] = billing
    WRITTEN.extend([out / "계약관리규정.md", out / "청구수납규정.md"])  # hand-written sources
    write_bytes(out / "계약관리규정.pdf", pdf_bytes(contract, "계약관리규정"))
    write_bytes(out / "계약관리규정.hwpx", hwpx_bytes(contract))
    write_bytes(out / "청구수납규정.docx", docx_bytes(billing, "청구수납규정"))

    quality = (SOURCES / "품질관리절차서.md").read_text("utf-8")
    texts["품질관리절차서"] = quality
    write_bytes(out / "품질관리절차서.hwpx", hwpx_bytes(quality))

    manual = (SOURCES / "거래처관리매뉴얼.md").read_text("utf-8")
    texts["거래처관리매뉴얼"] = manual
    write_bytes(out / "거래처관리매뉴얼.doc", doc_bytes(docx_bytes(manual, "거래처관리매뉴얼")))

    minutes = (SOURCES / "영업전략회의록.md").read_text("utf-8")
    texts["영업전략회의록"] = minutes
    write_bytes(out / "영업전략회의록.pptx", pptx_bytes(minutes))
    report = report_text(data)
    texts["분기실적보고"] = report
    write_bytes(out / "분기실적보고.pptx", pptx_bytes(report))

    org = org_sheets(data)
    texts["조직도"] = "\n".join(" ".join(str(v) for v in row) for s in org.values() for row in s)
    write_xlsx(out / "조직도.xlsx", org)
    prices = price_sheets(data)
    texts["단가표"] = "\n".join(" ".join(str(v) for v in row) for s in prices.values() for row in s)
    write_xls(out / "단가표.xls", prices)
    write_csv(out / "담당자연락망.csv", contacts_rows(data))

    faq = (SOURCES / "고객FAQ.md").read_text("utf-8")
    texts["고객FAQ"] = faq
    write_bytes(out / "고객FAQ.html", faq_html(faq))
    write_bytes(out / "공지사항.txt", (SOURCES / "공지사항.txt").read_bytes())

    for name in ("사업수행계획서.hwp", "LICENSE"):
        write_bytes(out / name, (QUICKSTART / "documents" / name).read_bytes())
    return texts


# --------------------------------------------------------------------------- images


def font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    if bold and FONT_SD.is_file():
        return ImageFont.truetype(str(FONT_SD), size, index=6)  # AppleSDGothicNeo Bold
    return ImageFont.truetype(str(FONT), size)


def encode_image(img, fmt: str, **options) -> bytes:
    """Encode, downscaling by 10% steps until the file fits in IMAGE_MAX_BYTES."""
    from PIL import Image

    while True:
        buf = io.BytesIO()
        img.save(buf, fmt, **options)
        if buf.tell() <= IMAGE_MAX_BYTES:
            return buf.getvalue()
        img = img.resize((int(img.width * 0.9), int(img.height * 0.9)), Image.LANCZOS)


def _centered(draw, box, lines, fonts, fill="#1d2733"):
    x0, y0, x1, y1 = box
    heights = [draw.textbbox((0, 0), t, font=f)[3] for t, f in zip(lines, fonts, strict=True)]
    y = (y0 + y1 - sum(heights) - 6 * (len(lines) - 1)) / 2
    for text, f, h in zip(lines, fonts, heights, strict=True):
        w = draw.textlength(text, font=f)
        draw.text(((x0 + x1 - w) / 2, y), text, font=f, fill=fill)
        y += h + 6


def _arrow(draw, start, end, fill="#44515e", width=4):
    draw.line([start, end], fill=fill, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 14
    left = (end[0] - size * math.cos(angle - 0.45), end[1] - size * math.sin(angle - 0.45))
    right = (end[0] - size * math.cos(angle + 0.45), end[1] - size * math.sin(angle + 0.45))
    draw.polygon([end, left, right], fill=fill)


def org_chart(data: Data) -> bytes:
    from PIL import Image, ImageDraw

    names = data.by("employees", "사원ID")
    depts = data.by("departments", "부서코드")
    children: dict[str, list[str]] = defaultdict(list)
    for d in data.departments:
        if d["상위부서코드"]:
            children[d["상위부서코드"]].append(d["부서코드"])
    width, height = 1800, 900
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    title = font(40, bold=True)
    draw.text((60, 40), "한빛상사 조직도", font=title, fill="#0f1b2d")
    draw.text(
        (60, 96), f"기준일 {AS_OF.isoformat()} · 부서코드와 부서장", font=font(22), fill="#56606b"
    )
    big, small = font(26, bold=True), font(20)
    box_w, box_h = 200, 92
    leaves = [c for top in children["D01"] for c in children[top]]
    gap = (width - 120 - box_w * len(leaves)) / max(1, len(leaves) - 1)
    pos: dict[str, tuple[float, float]] = {}
    for i, code in enumerate(leaves):
        pos[code] = (60 + i * (box_w + gap), 640)
    for top in children["D01"]:
        xs = [pos[c][0] for c in children[top]]
        pos[top] = ((min(xs) + max(xs)) / 2, 400)
    pos["D01"] = ((width - box_w) / 2, 170)
    colours = {"D01": "#dbe7f5", "D02": "#e3f1e6", "D03": "#fbeedd", "D04": "#efe5f6"}
    for parent, kids in children.items():
        px, py = pos[parent]
        for kid in kids:
            kx, ky = pos[kid]
            mid = (py + box_h + ky) / 2
            draw.line(
                [
                    (px + box_w / 2, py + box_h),
                    (px + box_w / 2, mid),
                    (kx + box_w / 2, mid),
                    (kx + box_w / 2, ky),
                ],
                fill="#7a8794",
                width=3,
            )
    for code, (x, y) in pos.items():
        d = depts[code]
        top = code if code in colours else d["상위부서코드"]
        draw.rounded_rectangle(
            [x, y, x + box_w, y + box_h],
            12,
            fill=colours.get(top, "#eeeeee"),
            outline="#33414f",
            width=3,
        )
        _centered(
            draw,
            (x, y, x + box_w, y + box_h),
            [d["부서명"], f"{code} · {names[d['부서장사원ID']]['성명']}"],
            [big, small],
        )
    return encode_image(img, "PNG", optimize=True)


def flow_chart() -> bytes:
    from PIL import Image, ImageDraw

    steps = [
        ("고객 등록", "영업 담당자 · 고객ID"),
        ("계약 체결", "승인권자 승인 · 계약ID"),
        ("주문 접수", "제품코드 · 주문ID"),
        ("출하검사", "품질관리팀 · 불량 격리"),
        ("출고", "물류팀 · 납품 확인"),
        ("청구서 발행", "재무회계팀 · 청구ID"),
        ("수납 확인", "수납ID · 수납상태"),
        ("연체 관리", "독촉장 · 신용등급 조정"),
    ]
    width, height = 1600, 820
    img = Image.new("RGB", (width, height), "#fbfaf7")
    draw = ImageDraw.Draw(img)
    draw.text(
        (60, 40), "한빛상사 주문-청구-수납 업무흐름도", font=font(40, bold=True), fill="#13202e"
    )
    big, small = font(28, bold=True), font(20)
    box_w, box_h = 300, 120
    xs = [80, 460, 840, 1220]
    rows = [230, 520]
    boxes = []
    for i, (name, note) in enumerate(steps):
        row = i // 4
        col = i % 4 if row == 0 else 3 - i % 4
        x, y = xs[col], rows[row]
        fill = "#fde2d2" if name == "연체 관리" else "#d9ecf7" if row == 0 else "#e2f3e1"
        draw.rounded_rectangle(
            [x, y, x + box_w, y + box_h], 16, fill=fill, outline="#2e3c4a", width=3
        )
        _centered(draw, (x, y, x + box_w, y + box_h), [f"{i + 1}. {name}", note], [big, small])
        boxes.append((x, y))
    for i in range(len(boxes) - 1):
        (x0, y0), (x1, y1) = boxes[i], boxes[i + 1]
        if y0 == y1 and x1 > x0:
            _arrow(draw, (x0 + box_w + 6, y0 + box_h / 2), (x1 - 8, y1 + box_h / 2))
        elif y0 == y1:
            _arrow(draw, (x0 - 6, y0 + box_h / 2), (x1 + box_w + 8, y1 + box_h / 2))
        else:
            _arrow(draw, (x0 + box_w / 2, y0 + box_h + 6), (x1 + box_w / 2, y1 - 8))
    draw.text(
        (80, 720),
        "납기일은 청구일부터 30일 · 납기일이 30일 넘게 지나면 연체로 바꾼다",
        font=font(24),
        fill="#3c4650",
    )
    return encode_image(img, "WEBP", quality=88, method=6)


def sales_chart(data: Data) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt

    font_manager.fontManager.addfont(str(FONT))
    family = font_manager.FontProperties(fname=str(FONT)).get_name()
    plt.rcParams.update(
        {"font.family": family, "axes.unicode_minus": False, "svg.hashsalt": "samples"}
    )
    billed: dict[str, int] = defaultdict(int)
    paid: dict[str, int] = defaultdict(int)
    for inv in data.invoices:
        billed[inv["청구일"][:4]] += inv["청구금액"]
        paid[inv["청구일"][:4]] += inv["수납금액"]
    years = sorted(billed)
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=110)
    bars = ax.bar(years, [billed[y] / 1e8 for y in years], color="#4c7cb8", label="청구금액(억원)")
    ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=10)
    ax.set_ylabel("청구금액(억원)")
    ax.set_xlabel("청구 연도")
    ax2 = ax.twinx()
    ax2.plot(
        years,
        [100 * paid[y] / billed[y] for y in years],
        color="#d9603b",
        marker="o",
        linewidth=2.5,
        label="수납률(%)",
    )
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("수납률(%)")
    ax.set_title("한빛상사 연도별 청구금액과 수납률 (2026년은 9월까지)", fontsize=15, pad=14)
    handles = ax.get_legend_handles_labels()
    handles2 = ax2.get_legend_handles_labels()
    ax.legend(handles[0] + handles2[0], handles[1] + handles2[1], loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", metadata={"Software": None})
    plt.close(fig)
    from PIL import Image

    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return encode_image(img.quantize(colors=128), "PNG", optimize=True)


WIDE = " table {width: 100%;}"


def _noise(size: tuple[int, int], sigma: float, *, seed: int):
    """Seeded gaussian noise around 128 (PIL's effect_noise is not seedable)."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    values = rng.normal(128, sigma, (size[1], size[0])).clip(0, 255).astype("uint8")
    return Image.fromarray(values, "L")


def _scan(pdf: bytes, *, angle: float, fmt: str, **options) -> bytes:
    """Render page 1 at 150 dpi and make it look scanned: grey paper, tilt, noise."""
    import pymupdf
    from PIL import Image, ImageChops, ImageFilter

    doc = pymupdf.open("pdf", pdf)
    pix = doc[0].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    page = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    doc.close()
    paper = Image.new("L", page.size, 236)
    page = ImageChops.multiply(page, paper.point(lambda v: v))
    page = ImageChops.darker(page, Image.new("L", page.size, 238))
    page = page.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=150)
    noise = _noise(page.size, 6 if fmt == "JPEG" else 3.6, seed=SEED + round(abs(angle) * 100))
    page = ImageChops.add(page, noise, scale=1.0, offset=-128)
    page = page.filter(ImageFilter.GaussianBlur(0.6))
    if fmt == "PNG":  # a few grey levels keep a noisy scan small enough as PNG
        page = page.point(lambda v: min(255, v // 32 * 32 + 16))
    return encode_image(page, fmt, **options)


def scan_pair(data: Data) -> tuple[dict, dict]:
    """A recent large contract and its invoice with the most orders before it (for the scans)."""
    orders: dict[str, list[str]] = defaultdict(list)
    for o in data.orders:
        orders[o["계약ID"]].append(o["주문일"])
    best = None
    for c in data.contracts:
        if c["상태"] != "진행중" or c["계약금액"] < 100_000_000 or c["시작일"] < "2025-01-01":
            continue
        for inv in data.invoices:
            if inv["계약ID"] == c["계약ID"]:
                key = (sum(d <= inv["청구일"] for d in orders[c["계약ID"]]), inv["청구ID"])
                if best is None or key > best[0]:
                    best = (key, c, inv)
    return best[1], best[2]


def contract_scan_text(data: Data) -> tuple[str, str]:
    customers = data.by("customers", "고객ID")
    employees = data.by("employees", "사원ID")
    products = data.by("products", "제품코드")
    contract, _ = scan_pair(data)
    cust = customers[contract["고객ID"]]
    lines = [o for o in data.orders if o["계약ID"] == contract["계약ID"]]
    codes = sorted({o["제품코드"] for o in lines})[:3] or [data.products[0]["제품코드"]]
    rows = "\n".join(
        f"| {code} | {products[code]['제품명']} | {products[code]['단위']} | "
        f"{products[code]['표준단가']:,}원 |"
        for code in codes
    )
    text = f"""# 물품 공급 계약서

계약ID: {contract["계약ID"]}  
고객ID: {cust["고객ID"]}  
고객명: {cust["고객명"]}  
계약명: {contract["계약명"]}  
계약금액: {contract["계약금액"]:,}원 (부가가치세 별도)  
계약기간: {contract["시작일"]} ~ {contract["종료일"]}  
담당자: {employees[contract["담당자ID"]]["성명"]} ({contract["담당자ID"]})

## 공급 제품

| 제품코드 | 제품명 | 단위 | 단가 |
| --- | --- | --- | --- |
{rows}

## 주요 조건

제1조 한빛상사는 고객의 주문에 따라 위 제품을 공급하고, 고객은 청구서의 납기일까지 대금을 지급한다.

제2조 주문의 단가는 주문일에 유효한 단가표의 단가를 적용한다.

제3조 납기일은 청구일부터 30일로 하며, 납기일이 30일 넘게 지나면 연체로 본다.

제4조 공급 제품은 출하검사를 거쳐 출고하며, 불량이 발견되면 교환한다.

{contract["시작일"][:4]}년 {int(contract["시작일"][5:7])}월 {int(contract["시작일"][8:])}일

한빛상사 대표이사 (인)  
{cust["고객명"]} 대표이사 (인)
"""
    return contract["계약ID"], text


def invoice_scan_text(data: Data, contract_id: str) -> str:
    contracts = data.by("contracts", "계약ID")
    customers = data.by("customers", "고객ID")
    products = data.by("products", "제품코드")
    _, inv = scan_pair(data)
    c = contracts[contract_id]
    cust = customers[c["고객ID"]]
    lines, billed = [], 0
    for o in data.orders:
        if o["계약ID"] == contract_id and o["주문일"] <= inv["청구일"] and len(lines) < 4:
            if billed + o["금액"] <= inv["청구금액"]:
                lines.append(o)
                billed += o["금액"]
    rows = "\n".join(
        f"| {o['제품코드']} | {products[o['제품코드']]['제품명']} | {o['수량']:,} | "
        f"{o['단가']:,}원 | {o['금액']:,}원 |"
        for o in lines
    )
    if billed < inv["청구금액"]:
        rows += f"\n| - | 그 밖의 정기 납품분 | - | - | {inv['청구금액'] - billed:,}원 |"
    rows = rows.lstrip("\n")
    return f"""# 청구서

청구ID: {inv["청구ID"]}  
계약ID: {contract_id}  
고객명: {cust["고객명"]} (고객ID {cust["고객ID"]})  
청구일: {inv["청구일"]}  
납기일: {inv["납기일"]}  
청구금액: {inv["청구금액"]:,}원 (부가가치세 {inv["청구금액"] // 10:,}원 별도)

## 청구 내역

| 제품코드 | 제품명 | 수량 | 단가 | 금액 |
| --- | --- | --- | --- | --- |
{rows}

## 입금 안내

수납방법: 계좌이체, 전자어음(신용등급 A·B 고객), 법인카드

납기일까지 입금해 주십시오. 일부만 입금하시면 수납상태가 부분수납으로 표시되고, 납기일이 30일 넘게 지나면 연체로 바뀌어 독촉장이 발송됩니다.

한빛상사 재무회계팀
"""


def whiteboard(data: Data) -> bytes:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    rng = random.Random(SEED + 7)
    picks = rng.sample(data.products, 8)
    board_w, board_h = 1400, 900
    board = Image.new("RGB", (board_w, board_h), "#f7f7f2")
    draw = ImageDraw.Draw(board)
    marker = "#1f3f8f"
    draw.text((50, 36), "물류센터 창고 현황판", font=font(52, bold=True), fill=marker)
    draw.text((900, 56), f"{AS_OF.isoformat()} 기준", font=font(32), fill="#b3261e")
    cols = [("제품코드", 50), ("제품명", 260), ("재고", 760), ("안전재고", 930), ("위치", 1150)]
    y = 150
    for label, x in cols:
        draw.text((x, y), label, font=font(34, bold=True), fill=marker)
    draw.line([(40, y + 52), (board_w - 40, y + 52)], fill=marker, width=4)
    hand = font(32)
    for i, p in enumerate(picks):
        y = 220 + i * 78
        stock = rng.randint(0, 400)
        safety = rng.choice([50, 80, 100, 150])
        values = [
            p["제품코드"],
            p["제품명"],
            f"{stock}{p['단위']}",
            f"{safety}",
            f"{'ABC'[i % 3]}-{i + 1:02d}",
        ]
        for (label, x), value in zip(cols, values, strict=True):
            colour = "#b3261e" if label == "재고" and stock < safety else "#222831"
            draw.text(
                (x + rng.randint(-3, 3), y + rng.randint(-3, 3)), value, font=hand, fill=colour
            )
        draw.line([(40, y + 60), (board_w - 40, y + 60)], fill="#c9ccd1", width=2)
    draw.rectangle([0, 0, board_w - 1, board_h - 1], outline="#9aa0a6", width=18)

    # photograph it: perspective skew onto a wall, uneven light, sensor noise, blur
    out_w, out_h = 1500, 1050
    shift = (60, 40, -50, 30)  # how far each corner moves
    src = [(0, 0), (board_w, 0), (board_w, board_h), (0, board_h)]
    dst = [
        (80 + shift[0], 70),
        (out_w - 90, 70 + shift[1]),
        (out_w - 70 + shift[2], out_h - 80),
        (60, out_h - 90 + shift[3]),
    ]
    coeffs = _perspective(dst, src)
    wall = Image.new("RGB", (out_w, out_h), "#8a8f93")
    warped = board.transform(
        (out_w, out_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(0, 0, 0)
    )
    mask = Image.new("L", board.size, 255).transform(
        (out_w, out_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=0
    )
    wall.paste(warped, (0, 0), mask)
    gradient = Image.linear_gradient("L").rotate(-35, expand=False).resize((out_w, out_h))
    light = gradient.point(lambda v: 190 + v * 65 // 255)
    photo = ImageChops.multiply(wall, Image.merge("RGB", [light] * 3))
    noise = _noise((out_w, out_h), 5.5, seed=SEED)
    photo = ImageChops.add(photo, Image.merge("RGB", [noise] * 3), offset=-128)
    photo = photo.filter(ImageFilter.GaussianBlur(0.8))
    return encode_image(photo, "JPEG", quality=70)


def _perspective(dst, src):
    """Coefficients for PIL's PERSPECTIVE transform mapping *dst* corners to *src* corners."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    return _solve(rows, rhs)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def write_images(data: Data, out: Path) -> None:
    write_bytes(out / "조직도.png", org_chart(data))
    write_bytes(out / "매출추이.png", sales_chart(data))
    contract_id, contract = contract_scan_text(data)
    write_bytes(
        out / "계약서_스캔.jpg",
        _scan(
            pdf_bytes(contract, "계약서", font_pt=12, extra_css=WIDE),
            angle=1.2,
            fmt="JPEG",
            quality=70,
        ),
    )
    invoice = invoice_scan_text(data, contract_id)
    write_bytes(
        out / "청구서_스캔.png",
        _scan(
            pdf_bytes(invoice, "청구서", font_pt=12, extra_css=WIDE),
            angle=-1.4,
            fmt="PNG",
            optimize=True,
        ),
    )
    write_bytes(out / "업무흐름도.webp", flow_chart())
    write_bytes(out / "창고현황판.jpg", whiteboard(data))


# --------------------------------------------------------------------------- manifest


def describe(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".csv", ".tsv"):
            return f"{len(path.read_text('utf-8').splitlines()) - 1} rows"
        if ext == ".jsonl":
            return f"{len(path.read_text('utf-8').splitlines())} rows"
        if ext == ".parquet":
            import duckdb

            n = duckdb.sql(f"SELECT count(*) FROM read_parquet('{path}')").fetchall()[0][0]
            return f"{n} rows"
        if ext in (".sqlite",):
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            names = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            parts = [f"{n} {con.execute(f'SELECT count(*) FROM {n}').fetchone()[0]}" for n in names]
            con.close()
            return "tables " + ", ".join(parts)
        if ext in (".xlsx", ".xls"):
            from python_calamine import CalamineWorkbook

            wb = CalamineWorkbook.from_path(str(path))
            return "sheets " + ", ".join(
                f"{n} {len(wb.get_sheet_by_name(n).to_python()) - 1}" for n in wb.sheet_names
            )
        if ext == ".pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                return f"{doc.page_count} pages"
        if ext == ".pptx":
            with zipfile.ZipFile(path) as z:
                slides = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
            return f"{len(slides)} slides"
        if ext in (".png", ".jpg", ".webp"):
            from PIL import Image

            with Image.open(path) as img:
                return f"{img.width}x{img.height}"
    except Exception as exc:  # the manifest is informational
        return f"? ({exc})"
    return ""


def manifest(paths: list[Path]) -> int:
    total = 0
    for path in sorted(set(paths)):
        size = path.stat().st_size
        total += size
        print(f"{path.relative_to(ROOT)}\t{size}\t{describe(path)}")
    print(f"total\t{total}\t{len(set(paths))} files")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--quickstart",
        action="store_true",
        help="also re-render samples/quickstart PDF/DOCX from their Markdown",
    )
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or not FONT.is_file():
        print(f"needs macOS with {FONT} and /usr/bin/textutil", file=sys.stderr)
        return 1
    data = build_data()
    write_structured(data, HANBIT / "structured")
    texts = write_documents(data, HANBIT / "documents")
    short = {k: korean_words(v) for k, v in texts.items() if korean_words(v) < 600}
    if short:
        print(f"documents under 600 Korean words: {short}", file=sys.stderr)
        return 1
    write_images(data, HANBIT / "images")
    if args.quickstart:
        docs = QUICKSTART / "documents"
        write_bytes(
            docs / "계약관리규정.pdf",
            pdf_bytes((docs / "계약관리규정.md").read_text("utf-8"), "계약관리규정"),
        )
        write_bytes(
            docs / "청구수납규정.docx",
            docx_bytes((docs / "청구수납규정.md").read_text("utf-8"), "청구수납규정"),
        )
    every = [p for p in HANBIT.rglob("*") if p.is_file()]
    total = manifest(every)
    stray = sorted(set(every) - set(WRITTEN))
    if stray:
        print(f"files the generator does not write: {[str(p) for p in stray]}", file=sys.stderr)
    whole = sum(p.stat().st_size for p in SAMPLES.rglob("*") if p.is_file())
    print(f"samples/ total {whole} bytes (hanbit {total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
