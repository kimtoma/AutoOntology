# 샘플 데이터

모든 데이터는 **가상 기업 "한빛상사"** 의 거래 데이터로, 실제 기업·인물과 무관한 무작위 생성 값입니다(UTF-8 CSV).

## `structured/` — 정형 데이터 (고객 → 계약 → 청구)

| 파일 | 행 수 | 컬럼 | 설명 |
|---|---|---|---|
| `customers.csv` | 40 | 고객ID, 고객명, 업종, 지역, 담당자, 등록일 | 한빛상사의 거래처. `고객ID`는 `C001` 형식 |
| `contracts.csv` | 55 | 계약ID, 고객ID, 계약명, 계약금액, 시작일, 종료일, 상태 | 거래처와 맺은 계약. 상태는 진행중·종료·해지, 금액 단위는 원 |
| `invoices.csv` | 60 | 청구ID, 계약ID, 청구일, 청구금액, 수납금액, 수납상태 | 계약별 청구서. 수납상태는 완납·부분수납·미수납·연체 |

관계(외래키에 해당, 파일에는 제약조건 없음 — 시맨틱 FK 탐지가 찾아낼 대상):

- `contracts.고객ID` → `customers.고객ID`
- `invoices.계약ID` → `contracts.계약ID`

모든 `contracts.고객ID`는 `customers`에, 모든 `invoices.계약ID`는 `contracts`에 존재합니다.

## 사용법

```bash
uv run autoontology up
uv run autoontology source add samples/structured --name hanbit
uv run autoontology build --source hanbit
```

적재 시 컬럼명은 소문자·`_` 정규화만 되고 한글은 그대로 유지됩니다(예: `고객ID` → `고객id`). 데이터는 Postgres DB `autoontology_src_hanbit`에 테이블 `customers`·`contracts`·`invoices`로 들어갑니다.
