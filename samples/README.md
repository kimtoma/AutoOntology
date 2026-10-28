# 샘플 데이터

[English](README.en.md)

모든 데이터는 **가상 기업 "한빛상사"**(도소매·제조·건설 고객에게 산업자재를 공급하는 B2B
무역회사)의 자료입니다. 실제 기업·인물과 무관한 생성 값이며, 금액 단위는 원입니다. 세트는
두 가지입니다.

| 세트 | 용도 | 정형 | 문서 | 이미지 | 문서 청크(kordoc 사용 시) |
|---|---|---|---|---|---|
| `quickstart/` | 처음 써 보기, 빠른 빌드 | CSV 3개(155행) | 5개 | - | 28 (kordoc 없이 9) |
| `hanbit/` | 여러 포맷·대량 데이터 확인 | 9개 파일 → 테이블 11개(5,603행) | 15개 | 6개(OCR) | 132 + 이미지 9 (kordoc 없이 42) |

청크 수는 기본 청크 설정으로 이 저장소의 리더(`read_document` + `chunker.chunk`)를 실제로
돌려 센 값입니다. 빌드 시간은 대체로 청크 수에 비례합니다(로컬 `qwen3:8b`이면 청크당 1~2분).

## `quickstart/` — 작은 세트

| 파일 | 형식 | 크기 | 내용 |
|---|---|---|---|
| `structured/customers.csv` | CSV | 40행 | 고객ID(`C001`), 고객명, 업종, 지역, 담당자, 등록일 |
| `structured/contracts.csv` | CSV | 55행 | 계약ID, 고객ID, 계약명, 계약금액, 시작일, 종료일, 상태(진행중·종료·해지) |
| `structured/invoices.csv` | CSV | 60행 | 청구ID, 계약ID, 청구일, 청구금액, 수납금액, 수납상태(완납·부분수납·미수납·연체) |
| `documents/계약관리규정.md` / `.pdf` | Markdown / PDF | PDF 2쪽 | 고객·계약·계약금액·승인권자 정의 |
| `documents/청구수납규정.md` / `.docx` | Markdown / Word | | 청구서·수납상태·독촉장 정의 |
| `documents/사업수행계획서.hwp` | HWP | 17섹션 | rhwp 프로젝트의 HWP 예제(MIT, 아래 라이선스) |

관계: `contracts.고객ID → customers.고객ID`, `invoices.계약ID → contracts.계약ID`.

```bash
uv run autoontology source add samples/quickstart/structured --name quick
uv run autoontology source add samples/quickstart/documents --name quick-docs --kind documents
uv run autoontology build
```

## `hanbit/` — 전체 세트

`scripts/gen_samples.py`가 고정 시드로 만든 세트입니다(아래 "다시 만들기"). 기준일은
2026-09-30이고 날짜는 2019~2026년입니다.

### `structured/` — 정형 데이터

| 파일 | 형식 | 행 | 정의하는 것 |
|---|---|---|---|
| `customers.csv` | CSV | 300 | 고객: 고객ID(`C###`), 고객명, 업종, 지역, 신용등급(A~D), 담당자ID, 등록일 |
| `contracts.csv` | CSV | 600 | 계약: 계약ID(`K2024-015`), 고객ID, 계약명, 계약금액, 시작일, 종료일, 상태, 담당자ID |
| `invoices.csv` | CSV | 1,500 | 청구서: 청구ID(`INV-20250137`), 계약ID, 청구일, 납기일, 청구금액, 수납금액, 수납상태 |
| `products.xlsx` | Excel | 시트 `제품` 120, `카테고리` 12 | 제품코드(`P-####`), 제품명, 카테고리코드(`CAT-##`), 단위, 표준단가, 공급사ID |
| `employees.sqlite` | SQLite | `employees` 80, `departments` 13 | 사원ID(`E###`), 성명, 부서코드(`D##`), 직급 / 부서명, 상위부서코드, 부서장사원ID |
| `suppliers.xls` | Excel 97-2003 | 40 | 공급사ID(`S###`), 공급사명, 지역, 주요품목, 거래시작일, 평가등급(A~C) |
| `payments.jsonl` | JSON Lines | 1,689 | 수납: 수납ID(`PAY-######`), 청구ID, 수납일, 수납금액, 수납방법 |
| `price_history.tsv` | TSV | 349 | 단가 이력: 제품코드, 적용일, 단가, 변경사유 |
| `orders.parquet` | Parquet | 900 | 주문: 주문ID(`O-2025-00042`), 계약ID, 제품코드, 주문일, 수량, 단가, 금액 |

적재하면 테이블 11개가 됩니다(`products_제품`, `products_카테고리`, `employees`, `departments`, 나머지는
파일 이름). 컬럼명은 소문자·`_` 정규화만 되고 한글은 그대로입니다(`고객ID` → `고객id`).

관계(`employees.sqlite`의 두 테이블에만 `REFERENCES`가 선언돼 있고 나머지 파일에는 제약조건이
없습니다. 시맨틱 FK 탐지가 찾아낼 대상이며, 모든 값이 실제로 연결됩니다):

```
customers.고객ID          ◄── contracts.고객ID
contracts.계약ID          ◄── invoices.계약ID, orders.계약ID
invoices.청구ID           ◄── payments.청구ID
products_제품.제품코드     ◄── orders.제품코드, price_history.제품코드
products_카테고리.카테고리코드 ◄── products_제품.카테고리코드
suppliers.공급사ID         ◄── products_제품.공급사ID
employees.사원ID          ◄── customers.담당자ID, contracts.담당자ID, departments.부서장사원ID
departments.부서코드       ◄── employees.부서코드, departments.상위부서코드
```

데이터 규칙: 청구서의 수납금액은 그 청구ID의 수납 합계와 같고, 수납상태는 청구수납규정
제8조(완납·부분수납·미수납·연체)를 따릅니다. 주문 단가는 주문일에 유효한 `price_history`
단가이고 금액 = 수량 × 단가입니다. 고객과 계약의 담당자는 영업1·2·3팀 직원입니다.

### `documents/` — 문서

| 파일 | 형식 | 분량 | 청크 | 정의하는 것 |
|---|---|---|---|---|
| `계약관리규정.md` `.pdf` `.hwpx` | Markdown / PDF / HWPX | 19조, PDF 4쪽 | 9 / 11 / 10 | 고객, 계약, 계약ID·고객ID·담당자ID, 계약상태, 주문, 제품코드, 승인권자 |
| `청구수납규정.md` `.docx` | Markdown / Word | 21조 | 10 / 10 | 청구서, 청구ID, 납기일, 수납, 수납ID·수납방법, 수납상태 4종, 독촉장 |
| `품질관리절차서.hwpx` | HWPX | 13절 | 10 | **표에 없는 개념**: 검사, 로트, 불량, 부적합보고서, 시정조치, 예방조치 |
| `거래처관리매뉴얼.doc` | Word 97-2003 | 11절 | 10 | 고객·공급사 등록, 신용등급, 평가등급, 업종, 지역, 담당 영업팀 |
| `영업전략회의록.pptx` | PowerPoint | 9슬라이드 | 13 | 영업팀별 목표, 주력 카테고리, 연체 관리, 계약 갱신, 공급사 단가 협상 |
| `분기실적보고.pptx` | PowerPoint | 9슬라이드 | 14 | 분기별 청구금액·수납률, 영업팀·업종·카테고리별 실적(표 데이터에서 계산) |
| `조직도.xlsx` | Excel | 시트 3개 | 6 | 부서코드, 부서, 사원ID, 직급, 부서별 업무 |
| `단가표.xls` | Excel 97-2003 | 시트 2개 | 6 | 제품코드별 단가·적용일·공급사ID, 단가 적용 기준 |
| `담당자연락망.csv` | CSV | 59행 | 2 | 사원ID, 부서명, 직급, 내선번호, 담당지역 |
| `고객FAQ.html` | HTML | 24문항 | 10 | 고객이 보는 주문·청구·수납·반품 안내 |
| `공지사항.txt` | 텍스트 | 4건 | 1 | 단가표 개정, 출고 일정, 수납 계좌 변경 |
| `사업수행계획서.hwp` | HWP | 17섹션 | 10 | rhwp HWP 예제(`quickstart`와 같은 파일) |

`.md`·`.txt`·`.html`·`.csv`·`.doc`는 Node 없이 읽고, 나머지는 kordoc이 필요합니다
(`uv run autoontology tools install-kordoc`).

### `images/` — 이미지(OCR)

| 파일 | 크기 | 청크 | 내용 |
|---|---|---|---|
| `조직도.png` | 1900×800 | 1 | 부서 상자와 부서코드·부서장 이름 |
| `매출추이.png` | 1100×616 | 1 | 연도별 청구금액(막대)과 수납률(선) 차트 |
| `계약서_스캔.jpg` | 1278×1781 | 2 | 기울고 잡음이 있는 계약서 스캔(계약ID, 고객ID, 제품코드, 단가) |
| `청구서_스캔.png` | 1284×1785 | 2 | 같은 계약의 청구서 스캔(청구ID, 납기일, 청구 내역) |
| `업무흐름도.webp` | 1600×820 | 2 | 고객 등록 → 계약 → 주문 → 출하검사 → 청구 → 수납 → 연체 관리 |
| `창고현황판.jpg` | 1500×1050 | 1 | 사진처럼 찍힌 화이트보드 재고표(제품코드, 재고, 안전재고) |

이미지는 kordoc OCR 모델이 있어야 읽습니다. 없으면 빌드가 이미지를 건너뛰고 경고를 냅니다.
OCR 결과 예: `물류센터창고현황판 2026-09-30기준 | 제품코드 | 제품명 | 재고 | ...`.

### 등록

```bash
uv run autoontology source add samples/hanbit/structured --name hanbit
uv run autoontology source add samples/hanbit/documents --name hanbit-docs --kind documents
uv run autoontology source add samples/hanbit/images --name hanbit-images --kind documents
uv run autoontology build
```

정형 데이터는 Postgres DB `autoontology_src_hanbit`에 들어갑니다.

### 다시 만들기

```bash
uv run --group dev python scripts/gen_samples.py
```

macOS(`/usr/bin/textutil`, AppleGothic 글꼴)와 kordoc(`.hwpx` 생성)이 필요합니다. 몇 번을
돌려도 같은 바이트가 나오며(시각 고정, 시드 고정 잡음), 끝에 파일별 바이트·행/쪽 수를
출력합니다. 직접 쓴 원문은 모두 `scripts/sample_sources/`에 있으며(`documents/*.md`도 여기서 복사), 빈
`samples/hanbit`에서도 전체를 다시 만들 수 있습니다.
`--quickstart`를 주면 `quickstart`의 PDF/DOCX도 Markdown에서 다시 만듭니다(커밋된 바이트와는
달라집니다).

## 라이선스

- 이 폴더의 생성 데이터와 문서는 저장소의 MIT 라이선스(`LICENSE`)를 따릅니다.
- `사업수행계획서.hwp`는 [rhwp](https://github.com/edwardkim/rhwp) 프로젝트의 예제 파일로 MIT
  라이선스이며, 원문 고지는 같은 폴더의 `LICENSE`에 있습니다.
- PDF와 스캔 이미지는 macOS의 AppleGothic 글꼴로 조판했고, PDF에는 사용한 글자만 담은
  부분 글꼴이 들어 있습니다.
