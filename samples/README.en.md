# Sample data

[한국어](README.md)

Everything here belongs to **a fictional company, "한빛상사" (Hanbit Trading)**, a B2B trader
that supplies industrial materials to wholesale, manufacturing and construction customers.
The values are generated and unrelated to any real company or person; amounts are in KRW.
The content is in Korean. There are two sets.

| Set | Use it for | Structured | Documents | Images | Document chunks (with kordoc) |
|---|---|---|---|---|---|
| `quickstart/` | a first run, a fast build | 3 CSV files (155 rows) | 5 | - | 28 (9 without kordoc) |
| `hanbit/` | many formats, more data | 9 files -> 11 tables (5,603 rows) | 15 | 6 (OCR) | 132 + 9 from images (42 without kordoc) |

The chunk counts come from running this repository's readers (`read_document` +
`chunker.chunk`, default sizes) on the files. Build time grows roughly with the number of
chunks (one to two minutes per chunk with a local `qwen3:8b`).

## `quickstart/` — the small set

| File | Format | Size | Content |
|---|---|---|---|
| `structured/customers.csv` | CSV | 40 rows | 고객ID (`C001`), 고객명, 업종, 지역, 담당자, 등록일 |
| `structured/contracts.csv` | CSV | 55 rows | 계약ID, 고객ID, 계약명, 계약금액, 시작일, 종료일, 상태 (진행중/종료/해지) |
| `structured/invoices.csv` | CSV | 60 rows | 청구ID, 계약ID, 청구일, 청구금액, 수납금액, 수납상태 (완납/부분수납/미수납/연체) |
| `documents/계약관리규정.md` / `.pdf` | Markdown / PDF | PDF 2 pages | contract rules: customer, contract, amount, approver |
| `documents/청구수납규정.md` / `.docx` | Markdown / Word | | billing rules: invoice, payment status, dunning letter |
| `documents/사업수행계획서.hwp` | HWP | 17 sections | the HWP sample of the rhwp project (MIT, see Licences) |

Relations: `contracts.고객ID -> customers.고객ID`, `invoices.계약ID -> contracts.계약ID`.

```bash
uv run autoontology source add samples/quickstart/structured --name quick
uv run autoontology source add samples/quickstart/documents --name quick-docs --kind documents
uv run autoontology build
```

## `hanbit/` — the full set

Generated from a fixed seed by `scripts/gen_samples.py` (see "Regenerating"). The data's
"today" is 2026-09-30; dates run from 2019 to 2026.

### `structured/`

| File | Format | Rows | Defines |
|---|---|---|---|
| `customers.csv` | CSV | 300 | customers: 고객ID (`C###`), name, industry, region, credit grade (A-D), 담당자ID, registration date |
| `contracts.csv` | CSV | 600 | contracts: 계약ID (`K2024-015`), 고객ID, name, amount, start, end, status, 담당자ID |
| `invoices.csv` | CSV | 1,500 | invoices: 청구ID (`INV-20250137`), 계약ID, invoice date, due date, billed, paid, 수납상태 |
| `products.xlsx` | Excel | sheets `제품` 120, `카테고리` 12 | 제품코드 (`P-####`), name, 카테고리코드 (`CAT-##`), unit, list price, 공급사ID |
| `employees.sqlite` | SQLite | `employees` 80, `departments` 13 | 사원ID (`E###`), name, 부서코드 (`D##`), rank / department name, parent, head |
| `suppliers.xls` | Excel 97-2003 | 40 | 공급사ID (`S###`), name, region, main category, since, rating (A-C) |
| `payments.jsonl` | JSON Lines | 1,689 | payments: 수납ID (`PAY-######`), 청구ID, date, amount, method |
| `price_history.tsv` | TSV | 349 | price history: 제품코드, effective date, price, reason |
| `orders.parquet` | Parquet | 900 | orders: 주문ID (`O-2025-00042`), 계약ID, 제품코드, date, quantity, unit price, amount |

Loading produces 11 tables (`products_제품`, `products_카테고리`, `employees`, `departments`,
the rest named after their file). Column names are only lower-cased and `_`-normalised; the
Korean stays (`고객ID` -> `고객id`).

Relations (only the two tables in `employees.sqlite` declare `REFERENCES`; the other files
have no constraints, so these are what semantic foreign-key detection should find; every
value resolves):

```
customers.고객ID          <-- contracts.고객ID
contracts.계약ID          <-- invoices.계약ID, orders.계약ID
invoices.청구ID           <-- payments.청구ID
products_제품.제품코드     <-- orders.제품코드, price_history.제품코드
products_카테고리.카테고리코드 <-- products_제품.카테고리코드
suppliers.공급사ID         <-- products_제품.공급사ID
employees.사원ID          <-- customers.담당자ID, contracts.담당자ID, departments.부서장사원ID
departments.부서코드       <-- employees.부서코드, departments.상위부서코드
```

Data rules: an invoice's paid amount equals the sum of its payments, and its 수납상태
follows article 8 of the billing rules (완납 paid, 부분수납 partly paid, 미수납 unpaid and
not due, 연체 more than 30 days past due). An order's unit price is the `price_history`
price valid on the order date, and amount = quantity x unit price. Account managers of
customers and contracts are members of the three sales teams.

### `documents/`

| File | Format | Size | Chunks | Defines |
|---|---|---|---|---|
| `계약관리규정.md` `.pdf` `.hwpx` | Markdown / PDF / HWPX | 19 articles, PDF 4 pages | 9 / 11 / 10 | customer, contract, 계약ID/고객ID/담당자ID, contract status, order, product code, approver |
| `청구수납규정.md` `.docx` | Markdown / Word | 21 articles | 10 / 10 | invoice, 청구ID, due date, payment, 수납ID/method, the four payment statuses, dunning |
| `품질관리절차서.hwpx` | HWPX | 13 sections | 10 | **concepts no table has**: inspection, lot, defect, non-conformance report, corrective and preventive action |
| `거래처관리매뉴얼.doc` | Word 97-2003 | 11 sections | 10 | customer and supplier registration, credit grade, supplier rating, industry, region, sales team |
| `영업전략회의록.pptx` | PowerPoint | 9 slides | 13 | sales targets, focus categories, overdue accounts, renewals, supplier price talks |
| `분기실적보고.pptx` | PowerPoint | 9 slides | 14 | quarterly billed/paid and collection rate by team, industry, category (computed from the tables) |
| `조직도.xlsx` | Excel | 3 sheets | 6 | department codes, departments, employee IDs, ranks, what each department does |
| `단가표.xls` | Excel 97-2003 | 2 sheets | 6 | price per product code, effective date, supplier, pricing rules |
| `담당자연락망.csv` | CSV | 59 rows | 2 | employee ID, department, rank, extension, sales region |
| `고객FAQ.html` | HTML | 24 questions | 10 | customer-facing answers on orders, invoices, payments, returns |
| `공지사항.txt` | text | 4 notices | 1 | price list change, shipping schedule, account change |
| `사업수행계획서.hwp` | HWP | 17 sections | 10 | the rhwp HWP sample (same file as in `quickstart`) |

`.md`, `.txt`, `.html`, `.csv` and `.doc` are read without Node; the rest need kordoc
(`uv run autoontology tools install-kordoc`).

### `images/` (OCR)

| File | Size | Chunks | Content |
|---|---|---|---|
| `조직도.png` | 1900x800 | 1 | org chart: department boxes with codes and heads |
| `매출추이.png` | 1100x616 | 1 | chart: billed amount per year (bars) and collection rate (line) |
| `계약서_스캔.jpg` | 1278x1781 | 2 | tilted, noisy scan of a contract (계약ID, 고객ID, product codes, prices) |
| `청구서_스캔.png` | 1284x1785 | 2 | scan of an invoice of the same contract (청구ID, due date, lines) |
| `업무흐름도.webp` | 1600x820 | 2 | flow: register customer -> contract -> order -> outbound inspection -> invoice -> payment -> overdue |
| `창고현황판.jpg` | 1500x1050 | 1 | photographed whiteboard stock table (product code, stock, safety stock) |

Images need kordoc's OCR models; without them the build skips the images with a warning.
Example OCR output: `물류센터창고현황판 2026-09-30기준 | 제품코드 | 제품명 | 재고 | ...`.

### Registering

```bash
uv run autoontology source add samples/hanbit/structured --name hanbit
uv run autoontology source add samples/hanbit/documents --name hanbit-docs --kind documents
uv run autoontology source add samples/hanbit/images --name hanbit-images --kind documents
uv run autoontology build
```

The structured data goes into the Postgres database `autoontology_src_hanbit`.

### Regenerating

```bash
uv run --group dev python scripts/gen_samples.py
```

Needs macOS (`/usr/bin/textutil`, the AppleGothic font) and kordoc (to write `.hwpx`).
Re-runs produce the same bytes (pinned timestamps, seeded noise) and end with a manifest of
bytes and rows/pages per file. All hand-written texts live in
`scripts/sample_sources/` (`documents/*.md` are copied from there), so the whole set can be
rebuilt from an empty `samples/hanbit`. `--quickstart` also re-renders the quickstart PDF/DOCX from their
Markdown (the bytes then differ from the committed ones).

## Licences

- The generated data and documents in this folder are under the repository's MIT licence
  (`LICENSE`).
- `사업수행계획서.hwp` is the sample file of the [rhwp](https://github.com/edwardkim/rhwp)
  project, MIT-licensed; its notice is in the `LICENSE` file next to it.
- The PDFs and scanned images are typeset with macOS's AppleGothic font; the PDFs embed a
  subset holding only the glyphs used.
