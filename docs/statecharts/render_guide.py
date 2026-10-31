# SPDX-License-Identifier: MIT
import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
from pathlib import Path

os.umask(0o077)
scratch = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(
    description="Render the reviewed statechart guide without rerunning TLC or mutating the app."
)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--fonts-dir", type=Path, default=scratch.parents[1] / "ui/public/fonts")
parser.add_argument(
    "--generated-at", help="Preserve the evidence snapshot timestamp for reproducible output."
)
args = parser.parse_args()
runtime = args.output.absolute().parent
runtime.mkdir(parents=True, exist_ok=True)
out = runtime / "statecharts"
out.mkdir(mode=0o700, exist_ok=True)
models = json.loads((scratch / "models/models.json").read_text())
meta = {
    "Publication": {
        "name": "구축에서 첫 운영 반영까지",
        "mode": "current",
        "about": "후보 생성은 운영 반영이 아닙니다. 원문과 변경 내용을 검토하고 대표 질문을 통과한 뒤, 확인한 지식·검색·파일을 함께 운영본으로 전환합니다.",
        "ui": "소스 → 구축 → 검토함 → 변경 이력",
        "cli": 'autoontology build\nautoontology ops list\nautoontology ops decide CHANGE_ID --revision N --decision accept --note "Original checked"\nautoontology ops check "승인권자는?" --expected "대표이사"\nautoontology ops validate --revision N\nautoontology ops publish --revision N --token VALIDATION_TOKEN',
        "limit": "확인 동작은 구현됐지만, 확인자가 인증된 개인인지는 아직 보장하지 않습니다. 검증 토큰은 콘텐츠 일치용이며 로그인 자격 증명이 아닙니다.",
        "path": [
            "empty",
            "checking",
            "building",
            "candidate",
            "reviewed",
            "validated",
            "prepared",
            "published",
        ],
    },
    "ContinuousChange": {
        "name": "변경을 검토하는 동안 운영은 계속",
        "mode": "current",
        "about": "원문이나 표가 바뀌어도 기존 운영본을 사용합니다. 새 후보에서 필요한 변경만 검토하며 보류·제외·실패는 기존 운영본을 바꾸지 않습니다.",
        "ui": "운영 홈 → 지식 소스 → 후보 구축 → 검토함",
        "cli": 'autoontology source list\nautoontology build\nautoontology ops list\nautoontology ops decide CHANGE_ID --revision N --decision hold --note "Need evidence"\nautoontology ops processing SOURCE',
        "limit": "현재 변경 확인·구축은 수동 흐름입니다. 폴더·DB 상시 감시, 담당자 배정·기한·알림은 후속 구현 대상입니다.",
        "path": [
            "operating",
            "changed",
            "reading",
            "candidate",
            "held",
            "candidate",
            "reviewed",
            "ready",
            "published",
        ],
    },
    "SqlQuery": {
        "name": "데이터 질문과 결과 파일 복구",
        "mode": "current",
        "about": "운영본의 승인된 테이블·컬럼으로 SQL을 검사하고 제한된 읽기를 실행합니다. 완료된 사용 기록에서 결과를 다시 받으며, 파일 복구는 모델·DB 조회를 반복하지 않습니다.",
        "ui": "질문·활용 → SQL → 사용 기록 → CSV",
        "cli": 'autoontology ops sql "등록된 고객 수는?" --source quick --output customers.csv\nautoontology ops trace TRACE_ID\nautoontology ops sql-result TRACE_ID --output recovered.csv',
        "limit": "행 데이터는 조회 시점의 원본입니다. 과거 원본 DB 행 전체를 재현하는 기능은 아닙니다. 최대 500행·4 MiB, DB 실행 10초·작업자 20초 제한을 적용하며, 실패·취소에는 성공 CSV를 주지 않습니다.",
        "path": [
            "idle",
            "catalog",
            "planning",
            "checked",
            "executing",
            "complete",
            "saved",
            "complete",
            "saved",
        ],
    },
    "Restoration": {
        "name": "이전 지식을 새 운영 버전으로 복원",
        "mode": "current",
        "about": "과거 스냅샷을 덮어쓰지 않습니다. 차이·원문·영향·질문 검증을 담은 복원안을 준비하고, 정확한 확인 뒤 새 운영 버전으로 반영합니다.",
        "ui": "변경 이력 → 복원 내용 보기 → 운영 반영 내용 확인 → 복원",
        "cli": 'autoontology ops restore-prepare --revision N --reason "Recover approved knowledge"\nautoontology ops restoration PROPOSAL_ID\nautoontology ops restore --proposal PROPOSAL_ID --confirm EXACT_CONFIRMATION\nautoontology ops publication --revision NEW_REVISION --verify',
        "limit": "내용·대상·검증이 바뀌거나 복원안이 만료되면 다시 준비해야 합니다. 같은 완료 복원 재시도는 기존 결과를 반환합니다. 인증된 개인의 역할 검사는 아직 후속 작업입니다.",
        "path": [
            "operating",
            "preparing",
            "ready",
            "executing",
            "committed",
            "uncertain",
            "committed",
        ],
    },
    "AgentTarget": {
        "name": "에이전트는 준비하고, 사람은 근거로 확인",
        "mode": "planned",
        "about": "위임 범위와 기한 안에서 에이전트가 근거·변경·영향·검증·복구안을 준비합니다. 인증된 사람이 정확한 효과를 확인하며 에이전트의 자기 승인은 차단합니다.",
        "ui": "목표 설계: 담당 업무 → 준비 패킷 → 사람 확인 → 실행 기록",
        "cli": "현재 제공되는 완성된 위임·인증 CLI는 없습니다.\n기존 ops list / validate / publish / restore 명령은 부분 기반이며\n인증된 사람·에이전트 분리 및 동일 권한 검사를 연결하는 중입니다.",
        "limit": "이 차트는 목표 설계의 추상 모델입니다. 현재 제품의 권한 보증이나 외부 업무 실행 기능을 뜻하지 않습니다. 다중 서비스 부분 실패·보상과 소비 계약은 별도 P3 설계·검증이 필요합니다.",
        "path": [
            "assigned",
            "preparing",
            "awaiting",
            "approved",
            "executing",
            "succeeded",
            "uncertain",
            "succeeded",
        ],
    },
}
meta["KnowledgeQuery"] = {
    "name": "근거를 묻고 운영 그래프를 활용",
    "mode": "current",
    "about": "RAG 질문은 선택한 운영본의 개념·규칙·원문 맥락을 검색해 답변과 인용을 저장합니다. SPARQL은 같은 운영본의 그래프에 읽기 전용 질의를 실행합니다. 둘을 원본 DB의 SQL 조회와 구분합니다.",
    "ui": "질문·활용 → 채팅·인용·사용 기록 / SPARQL",
    "cli": 'autoontology ops ask "3억원 이상 계약은 누가 승인하나요?"\nautoontology ops retrieve "계약 승인 기준"\nautoontology ops trace TRACE_ID\nautoontology ops sparql "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"',
    "limit": "이 모델은 전송 완료·실패·잘림·취소·원문 해시를 다룹니다. 전체 응답 완료가 업무 정답이나 인용의 의미적 지지를 보증하지 않습니다. 근거 부족·부정·예외의 엄밀한 평가와 사용자별 접근 통제는 후속 작업입니다.",
    "path": ["idle", "scope", "retrieving", "streaming", "answer", "evidence"],
}
scenarios = [
    (
        "로컬 환경 설치와 역할 분리",
        "partial",
        "Publication",
        "Codex 로그인과 Ollama·Postgres를 확인하고 작업 공간을 준비한다.",
        "처음 설치하는 운영 담당자",
        "설치 안내·상태 검사",
        "scripts/bootstrap.sh --yes --provider codex\nautoontology doctor",
        "환경 검사 → 소스 연결 → 후보 구축",
        "첫 운영 반영 전에는 검토·질문 검증이 필요하다. 개인 역할별 로그인은 후속 작업이다.",
        "모델 로그인·백엔드 실패를 해결한 뒤 같은 입력으로 재시도한다.",
        "빈 환경에서 첫 승인된 운영본까지 걸린 총시간",
    ),
    (
        "최초 구축 안내에서 운영 홈으로",
        "current",
        "Publication",
        "운영본이 없을 때는 구축 안내, 생긴 뒤에는 검토할 변경과 운영 상태를 본다.",
        "운영 담당자",
        "운영 홈",
        "autoontology ontology\nautoontology ops list",
        "운영본 없음 → 후보 → 검증 → 운영 홈",
        "후보가 생겼다는 이유로 운영본으로 취급하지 않는다.",
        "검증 실패·미승인 후보를 운영 조회로 대체하지 않는다.",
        "설치 후 첫 정상 운영 화면까지 걸린 시간",
    ),
    (
        "질문 모델과 임베딩 모델 확인",
        "current",
        "Publication",
        "질문 모델은 Codex, 임베딩은 Ollama 설정과 실제 아티팩트 식별을 확인한다.",
        "운영 담당자",
        "설정·상태",
        "autoontology provider status\nautoontology doctor\nautoontology ops model-identity",
        "연결 확인 → 실제 모델 검사 → 구축",
        "모델 이름이 같아도 내용이 바뀌면 재검증한다.",
        "로그인·연결·식별 불일치 원인을 확인하고 다시 검증한다.",
        "모델 장애 진단·정상 재개까지 걸린 시간",
    ),
    (
        "파일 또는 DB로 정형 데이터 연결",
        "current",
        "Publication",
        "고객·계약·청구 CSV나 읽기 가능한 DB를 소스로 등록한다.",
        "데이터 담당자",
        "지식 소스 → 소스 추가",
        "autoontology source add /absolute/structured --name quick --kind structured\nautoontology source list",
        "연결 → 분석 → 후보",
        "등록·구축과 운영 반영은 별개의 작업이다.",
        "연결·파일 형식·스키마 문제를 고친 뒤 원본을 보존하며 재처리한다.",
        "원천 데이터 연결부터 검토 가능한 후보까지 걸린 시간",
    ),
    (
        "규정·계획서·업무 문서 추가",
        "current",
        "Publication",
        "한국어 HWP·PDF·Office·Markdown 문서를 읽고 개념과 근거를 만든다.",
        "문서 담당자",
        "지식 소스 → 문서 추가",
        "autoontology source add /absolute/documents --name policies --kind documents\nautoontology build\nautoontology ops processing policies",
        "원문 보존 → 읽기·추출 → 후보",
        "원문·처리 이력·인용을 확인한 뒤 변경을 검토한다.",
        "추출 실패 청크는 미완료로 남기고 이전 결과는 보존한다.",
        "문서 도착부터 근거 있는 후보 생성까지 걸린 시간",
    ),
    (
        "소스 현황과 신선도 확인",
        "partial",
        "ContinuousChange",
        "어떤 소스가 운영본에 포함되고 어떤 입력이 바뀌었는지 확인한다.",
        "운영 담당자",
        "지식 소스·운영 홈",
        "autoontology source list\nautoontology runs\nautoontology ops list",
        "운영 유지 → 변경 확인 → 처리",
        "소스별 담당자·권한·상시 감시를 완성한 상태는 아니다.",
        "미기록 과거 상태를 오늘의 값으로 추정하지 않는다.",
        "변경 발생부터 담당자가 확인하기까지의 지연",
    ),
    (
        "소스 분석·의미 연결·통합 구축",
        "partial",
        "Publication",
        "정형 카탈로그와 비정형 개념을 통합한 후보를 만든다.",
        "구축 담당자",
        "구축 → 실행 이력",
        "autoontology build\nautoontology ops execution RUN_ID --watch\nautoontology ops execution RUN_ID --cancel",
        "구축 → 후보 → 검토",
        "문서 처리 이력은 보존한다. 정형 엔진 처리 계보의 완전한 고정은 남아 있다.",
        "부분 실패·중단을 이력에서 확인하고 재개한다.",
        "실패와 재작업을 포함한 후보 준비시간",
    ),
    (
        "같은 업무 개념인지 연결 검토",
        "current",
        "ContinuousChange",
        "단어가 같다는 이유로 병합하지 않고 원문과 표의 의미를 확인한다.",
        "도메인 검토자",
        "검토함 → 개념 연결",
        "autoontology review list\nautoontology review accept ITEM_ID\nautoontology apply",
        "연결 제안 → 근거 확인 → 후보 재생성",
        "관계가 애매하면 보류·거절하고 과도한 동일시를 피한다.",
        "연결 판단 변경 후 필요한 재구축·재검증을 수행한다.",
        "연결 후보 하나를 확인하는 사람의 실제 작업시간",
    ),
    (
        "개념 목록과 그래프로 구조 확인",
        "partial",
        "ContinuousChange",
        "업무 개념·관계·근거를 목록과 그래프에서 탐색한다.",
        "업무 사용자",
        "온톨로지·그래프",
        'autoontology ontology\nautoontology ops sparql "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"',
        "운영본 선택 → 개념·근거 탐색",
        "CLI 요약·SPARQL은 제공된다. UI의 모든 그래프 탐색 동작과의 완전한 동등성은 남아 있다.",
        "명시적인 후보 미리보기와 일반 운영 조회를 구분한다.",
        "업무 개념과 근거를 찾는 데 걸린 시간",
    ),
    (
        "계약 승인 기준을 근거로 질문",
        "current",
        "KnowledgeQuery",
        "“3억원 이상 계약은 누가 승인하나요?”를 운영 지식에 묻고 인용을 확인한다.",
        "업무 사용자",
        "질문·활용 → 채팅",
        'autoontology ops ask "3억원 이상 계약은 누가 승인하나요?"\nautoontology ops trace TRACE_ID',
        "검색 → 질문 → 인용·원문 확인",
        "RAG 답변은 운영 버전과 검색 맥락에 결합된다. SQL 실행·원본 행 조회와 구분한다.",
        "부정·예외·인용의 업무 지지 여부는 사람 검토와 후속 평가가 필요하다.",
        "답변을 믿고 사용할 수 있는 근거 확인까지의 시간",
    ),
    (
        "고객 수와 계약금액을 조회",
        "current",
        "SqlQuery",
        "고객 수 40명과 한국어 계약금액 조인·집계를 승인된 카탈로그로 조회한다.",
        "분석 사용자",
        "질문·활용 → SQL",
        'autoontology ops sql "고객 수는?" --source quick --output customers.csv\nautoontology ops sql-result TRACE_ID --output recovered.csv',
        "질문 → 승인 범위 검사 → SELECT → 결과·CSV",
        "미승인 필드와 쓰기는 차단한다. 행은 실행 시점 기준이고 결과 제한이 표시된다.",
        "실패·취소와 완료를 구분하고, 저장 실패는 기존 결과 파일만 다시 받는다.",
        "질문부터 재사용 가능한 CSV까지의 총시간",
    ),
    (
        "SPARQL·내보내기로 지식 재사용",
        "partial",
        "KnowledgeQuery",
        "온톨로지 관계를 질의하고 RDF·GraphML·Ossie·OKF를 다른 도구에서 사용한다.",
        "분석·연계 담당자",
        "질문·활용 → SPARQL / 다운로드",
        'autoontology ops sparql "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"\nautoontology export --format okf --out /absolute/export-copy',
        "같은 운영본 → 질의·산출물 → 재사용",
        "SPARQL은 그래프 질의다. SQL 원본 데이터 조회와 실행기를 구분한다. 산출물은 운영 버전에 연결한다. SPARQL 결과 파일의 완전한 CLI 동등성은 남아 있다.",
        "깨진·변조된 산출물이나 검증되지 않은 후보를 운영 파일로 제공하지 않는다.",
        "연계 가능한 검증된 산출물을 얻기까지 걸린 시간",
    ),
    (
        "새 변경을 오늘 확인할 일로 모으기",
        "partial",
        "ContinuousChange",
        "바뀐 소스와 검토할 지식·답변 개선 요청을 운영 홈과 검토함에서 확인한다.",
        "운영 담당자",
        "운영 홈·검토함",
        "autoontology ops list",
        "변경 확인 → 후보 → 검토 대기",
        "현재 화면 기반은 있다. 자동 수집 큐·담당자·기한·우선순위·알림 연결은 후속 작업이다.",
        "미할당·오류·보류가 완료처럼 보이지 않게 해야 한다.",
        "변경 도착부터 검토자가 일을 시작하기까지의 시간",
    ),
    (
        "승인 기준 3억원 → 2억원 변경",
        "partial",
        "ContinuousChange",
        "규정 개정의 실제 근거와 영향받는 질문을 보고 변경을 반영한다.",
        "계약·청구 책임자",
        "지식 소스 → 검토함 → 변경 이력",
        'autoontology build\nautoontology ops list\nautoontology ops decide CHANGE_ID --revision N --decision accept --note "Updated original checked"',
        "새 원문 → 후보 → 근거 검토 → 질문 검증 → 운영 전환",
        "시행일·금액·연산자·예외·권한을 구조화한 결정적 규칙 검증은 아직 완성되지 않았다.",
        "충돌하거나 근거가 모자라면 보류하고 기존 승인 기준을 유지한다.",
        "개정 접수부터 승인된 새 기준을 사용할 때까지의 총시간",
    ),
    (
        "대표 질문으로 변경 영향 검증",
        "partial",
        "Publication",
        "새 규칙이 중요한 질문에 맞는 답과 인용을 제공하는지 확인한다.",
        "검토·품질 담당자",
        "변경 이력 → 대표 질문",
        'autoontology ops check "승인권자는?" --expected "대표이사"\nautoontology ops validate --revision N',
        "검토 완료 → 대표 질문 → 통과/실패",
        "현재 검사는 기대 문구·인용 존재 기반이다. 의미적 정답·부정·예외·권한별 회귀와 버전 평가 모음은 남아 있다.",
        "실패한 후보는 반영하지 않고 원인을 수정해 다시 검토한다.",
        "최초 검증 통과율과 검증·재작업 시간",
    ),
    (
        "버전과 판단 기록·복원",
        "current",
        "Restoration",
        "언제 어떤 지식을 확인해 반영했고 어떻게 복원할 수 있는지 확인한다.",
        "운영 책임자",
        "변경 이력 → 게시 기록 검증·복원",
        'autoontology ops publications\nautoontology ops publication --revision N --verify\nautoontology ops restore-prepare --revision N --reason "Recover approved version"',
        "과거 버전 확인 → 복원안 → 정확한 확인 → 새 버전",
        "기존 과거 버전을 덮어쓰지 않는다. 복원도 검증한 새 운영 이벤트다.",
        "만료·내용 변경은 재준비하고, 응답 유실은 동일 결과를 먼저 조회한다.",
        "문제 인지부터 검증된 운영 상태 회복까지의 시간",
    ),
    (
        "답변의 부족함을 개선 요청으로",
        "partial",
        "ContinuousChange",
        "질문·답변 맥락을 남겨 지식 개선을 요청한다.",
        "업무 사용자·담당자",
        "질문 답변 → 지식 개선 요청 → 검토함",
        "autoontology ops trace TRACE_ID\n# Feedback creation/assignment/resolution CLI is pending.",
        "답변 발견 → 개선 요청 → 담당 검토",
        "UI 요청은 있다. 담당자·변경 업무·평가·해결 운영본까지의 연결과 완전한 CLI는 남아 있다.",
        "해결되지 않은 요청을 단순 확인만으로 해결 완료 처리하지 않는다.",
        "개선 요청부터 해결 운영본까지의 총시간",
    ),
    (
        "업무 변화에 맞춘 자동 운영 루프",
        "planned",
        "AgentTarget",
        "정해진 범위에서 변경을 모으고 준비하며 사람의 확인으로 운영한다.",
        "운영 책임자·수집 에이전트",
        "목표: 수집 큐·업무·검토·실행 기록",
        "아직 완성된 일정·수집 큐 CLI 없음",
        "변경 감지 → 중복 제거 → 준비 → 사람 확인 → 결과",
        "자동 수집은 운영 반영 권한을 뜻하지 않는다. 작업 재시도·만료·철회·알림이 필요하다.",
        "중단 후 재개와 중복 방지·실패 담당자 연결을 구현해야 한다.",
        "수집부터 확인 준비까지의 시간·대기·복구 부담",
    ),
    (
        "실패·중지·재시작에서도 운영 보호",
        "current",
        "Restoration",
        "실행 중단과 결과 불확실성을 구분하고 기존 운영 지식을 지킨다.",
        "운영 담당자",
        "실행 이력·사용 기록·변경 이력",
        "autoontology ops execution RUN_ID --watch\nautoontology ops traces\nautoontology ops restoration PROPOSAL_ID",
        "진행 → 실패/취소/완료 → 기록 확인 → 복구",
        "전환 전 실패는 이전 운영본을 유지한다. 복원·백업 훈련은 별도 환경에서 수행한다.",
        "완료 여부가 불명확하면 실행 기록부터 확인하고 무작정 작업을 반복하지 않는다.",
        "실패율·수동 복구 작업시간·정상 재개까지의 시간",
    ),
    (
        "조직의 도메인 판단 기준으로 확장",
        "planned",
        "AgentTarget",
        "계약·청구·HR·보안 도메인과 책임자·공통 개념·권한을 관리한다.",
        "조직 지식 책임자",
        "목표: 도메인 카탈로그·업무 계약",
        "아직 완성된 도메인·소비 계약 CLI 없음",
        "도메인 범위 → 지식 계약 → 소비 영향 확인",
        "현재 한 작업 공간 기반이다. 인증·도메인 범위·책임 모델은 구현 중이며 아직 조직 전체 권한 보증이 아니다.",
        "다른 도메인의 근거·원문·검색·SQL이 우회 노출되지 않게 해야 한다.",
        "조직이 승인된 기준을 찾고 재사용하는 데 걸린 시간",
    ),
    (
        "에이전트 준비와 사람 확인·업무 실행",
        "planned",
        "AgentTarget",
        "계약 승인 초안 등 업무 제안을 근거·영향·복구안과 함께 준비한다.",
        "에이전트·확인자·실행자",
        "목표: 준비 패킷 → 정확한 승인 → 실행 기록",
        "현재 완성된 위임·업무 실행 CLI 없음",
        "범위 위임 → 준비 → 인증된 사람 확인 → 제한된 실행",
        "초안 준비와 발송·외부 변경은 다르다. 별도 승인·권한·중복 방지·부분 실패 보상이 필요하다.",
        "철회·만료·내용 변경은 중지하고, 불확실한 실행은 기록·보상 절차로 넘긴다.",
        "승인된 결과까지의 총시간·사람 실작업 시간·재작업 부담",
    ),
]
status = {"current": "현재 제공", "partial": "일부 구현", "planned": "목표 설계"}
scenario_data = []
for i, (title, coverage, model, goal, actor, ui, cli, path, confirm, recovery, metric) in enumerate(
    scenarios, 1
):
    scenario_data.append(
        dict(
            id=i,
            title=title,
            coverage=coverage,
            model=model,
            goal=goal,
            actor=actor,
            ui=ui,
            cli=cli,
            path=path,
            confirm=confirm,
            recovery=recovery,
            metric=metric,
        )
    )
summary = []
raw = {}
for s in scenario_data:
    s["model_path"] = (
        ["idle", "scope", "evaluating", "rows"] if s["id"] == 12 else meta[s["model"]]["path"]
    )
for model in models:
    n = model["id"]
    src = scratch / (
        "SqlQuery-final-v2"
        if n == "SqlQuery"
        else "KnowledgeQuery-final-v2"
        if n == "KnowledgeQuery"
        else n + "-final"
    )
    dest = out / n
    dest.mkdir(mode=0o700, exist_ok=True)
    for p in [
        scratch / "models" / f"{n}.tla",
        scratch / "models" / f"{n}.cfg",
        scratch / "models" / f"{n}.view.mjs",
        src / "chart.mmd",
        src / "chart.svg",
        src / "report.md",
        src / "tlc.out",
    ]:
        if p.exists():
            shutil.copy2(p, dest / p.name)
    report = (src / "report.md").read_text()
    stats = re.search(r"TLC reached (\d+) states and (\d+) steps", report)
    numbers = list(map(int, stats.groups()))
    rules = re.search(r"## Rules that hold\n([^\n]+)", report).group(1).split(", ")
    mmd = (src / "chart.mmd").read_text()
    svg = (src / "chart.svg").read_text()
    labels = {
        a: re.sub(r"#(\d+);", lambda x: chr(int(x[1])), b.strip())
        for a, b in re.findall(r"^\s*(s\d+): (.+)$", mmd, re.M)
    }
    view = (scratch / "models" / f"{n}.view.mjs").read_text()
    phase_labels = json.loads(re.search(r"\((\{.+?\})\)\[v.phase\]", view).group(1))
    summary.append(
        {
            "id": n,
            "states": numbers[0],
            "steps": numbers[1],
            "holds": rules,
            "breaks": ["VerifiedHumanApproval"] if n == "Publication" else [],
            "source_sha256": hashlib.sha256(
                (scratch / "models" / f"{n}.tla").read_bytes()
            ).hexdigest(),
        }
    )
    raw[n] = {
        **meta[n],
        "mmd": mmd,
        "svg": svg,
        "labels": labels,
        "phase_labels": phase_labels,
        "report": report,
        "rules": model["rules"],
        "stats": summary[-1],
    }
shutil.copy2(scratch / "publication-replay.json", out / "publication-replay.json")
licenses = (scratch / "licenses/Pele-LICENSE").read_text()
font_css = ""
for family, name in [("Geist", "geist-sans.woff2"), ("Pretendard", "pretendard.woff2")]:
    font = base64.b64encode((args.fonts_dir / name).read_bytes()).decode()
    font_css += f'@font-face{{font-family:{family};src:url(data:font/woff2;base64,{font}) format("woff2");font-weight:100 900;font-display:swap}}'
font_license = (
    (args.fonts_dir / "geist-OFL.txt").read_text()
    + "\n\n"
    + (args.fonts_dir / "pretendard-OFL.txt").read_text()
)
provenance = {
    "generated_at": args.generated_at
    or json.loads((scratch / "generation.json").read_text())["generated_at"],
    "application_reference_commit": "6896be41504f97f484b42b906ed8425daee0bc18",
    "skill": "pstack:statechart",
    "skill_version": "0.15.36",
    "skill_source": "https://github.com/cursor/plugins/tree/main/pstack/skills/statechart",
    "checker": "TLC 1.7.4",
    "tlc_jar_sha256": "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88",
    "renderer": "pele 1.9.0",
    "models": summary,
    "scenarios": 21,
    "counterexample_replay": "publication-replay.json",
    "states": sum(s["states"] for s in summary),
    "transitions": sum(s["steps"] for s in summary),
    "passed_rules": sum(len(s["holds"]) for s in summary),
    "known_failed_rules": 1,
    "scope": "Bounded abstract models. Current implementation and planned delegation are separate. No application or public service mutation. Uncommitted identity work is excluded.",
}
(out / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
(out / "scenarios.json").write_text(json.dumps(scenario_data, ensure_ascii=False, indent=2))
E = html.escape
workflow = []
for n, w in raw.items():
    verdict = (
        '<span class="badge gap">인증된 사람 확인 경계 미구현</span>'
        if n == "Publication"
        else '<span class="badge design">목표 모델 검사 통과 · 구현 완료 아님</span>'
        if n == "AgentTarget"
        else '<span class="badge good">선택한 모델 규칙 통과</span>'
    )
    rules = "".join(
        "<li><code>" + E(r["id"]) + "</code><span>" + E(r["meaning"]) + "</span></li>"
        for r in w["rules"]
    )
    workflow.append(
        f'''<section class="workflow" id="{n}" data-model="{n}"><div class="section-top"><p class="eyebrow">{"TARGET DESIGN" if w["mode"] == "planned" else "CURRENT FLOW"}</p>{verdict}</div><h2>{E(w["name"])}</h2><p class="lead">{E(w["about"])}</p><div class="note {"target" if w["mode"] == "planned" else ""}">{E(w["limit"])}</div><div class="diagram-toolbar"><span>상태를 선택해 설명 보기</span><div><button data-zoom="out" aria-label="{n} 축소">−</button><button data-zoom="fit">맞춤</button><button data-zoom="in" aria-label="{n} 확대">＋</button><button data-save="svg">SVG 저장</button></div></div><div class="diagram" tabindex="0" aria-label="{E(w["name"])} 상태차트">{w["svg"]}</div><div class="inspector" aria-live="polite"><div><p class="eyebrow">선택한 상태</p><h3 class="state-title">상자를 선택하세요</h3><p class="state-help">실제 앱 상태를 바꾸지 않는 설명용 탐색입니다. 표와 시나리오에서 다음 작업·확인·복구 경로를 함께 읽으세요.</p></div><div><span class="small-label">UI 동선</span><p>{E(w["ui"])}</p></div></div><div class="details-grid"><details><summary>CLI로 같은 흐름 보기</summary><pre>{E(w["cli"])}</pre></details><details><summary>상태 모델의 규칙과 검사 결과</summary><ul class="rules">{rules}</ul><pre>{E(w["report"])}</pre></details><details><summary>Mermaid 원문 보기</summary><pre class="mmd-source">{E(w["mmd"])}</pre><button data-save="mmd">Mermaid 저장</button></details></div></section>'''
    )
scards = []
for s in scenario_data:
    scards.append(
        f'<button class="scenario-choice" data-scenario="{s["id"]}" data-coverage="{s["coverage"]}"><span class="scenario-number">{s["id"]:02}</span><span>{E(s["title"])}</span><span class="badge {s["coverage"]}">{status[s["coverage"]]}</span></button>'
    )
css = """*{box-sizing:border-box}html{color-scheme:light;scroll-padding-top:90px}body{margin:0;background:#fff;color:#171717;font:14px/1.8 Geist,Pretendard,-apple-system,sans-serif;word-break:keep-all;overflow-wrap:anywhere}button,input,select{font:inherit}button,a,input,select{outline-offset:3px}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #1765a5}button{cursor:pointer;border:1px solid #d9d9d9;border-radius:5px;background:#fff;color:#333;padding:6px 10px;font-size:12px}button:hover{background:#f5f5f5}a{color:#145b9a;text-underline-offset:3px}.topbar{height:64px;position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #e7e7e7;display:flex;align-items:center;gap:16px;padding:0 24px}.topbar strong{font-weight:600;font-size:14px}.topbar .meta{margin-left:auto}.shell{display:grid;grid-template-columns:224px minmax(0,1fr)}.sidebar{position:sticky;top:64px;height:calc(100vh - 64px);border-right:1px solid #e8e8e8;padding:24px 14px;overflow:auto;background:#fff}.sidebar a{display:block;padding:9px 12px;border-radius:4px;text-decoration:none;color:#555;font-size:12px}.sidebar a:hover{background:#f5f5f5;color:#111}.sidebar .nav-title{font-size:10px;letter-spacing:1px;color:#888;padding:0 12px;margin:18px 0 10px}.sidebar a.active{background:#f4f4f4;color:#171717;font-weight:600}.sidebar .pending-link{color:#86692e}.collapsed .shell{grid-template-columns:0 minmax(0,1fr)}.collapsed .sidebar{visibility:hidden;padding:0;border:0}main{min-width:0;max-width:1290px;width:100%;padding:34px 38px 68px;margin:auto}h1{font-weight:550;font-size:29px;line-height:1.45;letter-spacing:-.9px;margin:8px 0 14px}h2{font-weight:550;font-size:23px;line-height:1.5;letter-spacing:-.6px;margin:8px 0 16px}h3{font-size:16px;font-weight:550;line-height:1.5;margin:7px 0}p{color:#444;margin:10px 0}.eyebrow{font:10px/1.5 Geist,sans-serif;letter-spacing:1px;color:#777;text-transform:uppercase;margin:0}.lead{max-width:850px;color:#555}.meta{font-size:11px;color:#777}.stats{display:flex;gap:22px;flex-wrap:wrap;border-block:1px solid #e6e6e6;margin:25px 0;padding:14px 0;color:#666;font-size:12px}.stats strong{font-weight:550;color:#222}.badge{display:inline-flex;align-items:center;gap:4px;font-size:10px;line-height:1.5;border:1px solid #ddd;padding:3px 7px;border-radius:4px;white-space:nowrap;color:#555}.badge.current,.badge.good{background:#f5f8f6;border-color:#d9e4dc;color:#366248}.badge.partial,.badge.design,.badge.planned{background:#fcfaf5;border-color:#e7deca;color:#826127}.badge.gap{background:#fff5f5;border-color:#ecd0d3;color:#9a3b45}.note{font-size:12px;line-height:1.8;background:#fafafa;border-left:2px solid #c7c7c7;padding:12px 15px;margin:20px 0;color:#555}.note.target{border-color:#bfa668;background:#fcfaf5}.quick{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #e5e5e5;border-radius:5px;margin:25px 0}.quick>div{padding:18px;border-right:1px solid #e5e5e5}.quick>div:last-child{border:0}.quick p{font-size:12px;margin:6px 0}.quick b{font-size:13px;font-weight:550}.workflow,#scenarios,#limits,#outcomes,#terms{border-top:1px solid #e5e5e5;padding-top:32px;margin-top:45px}.section-top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.diagram-toolbar{display:flex;gap:12px;justify-content:space-between;align-items:center;margin:18px 0 0;border:1px solid #e5e5e5;border-bottom:0;border-radius:5px 5px 0 0;padding:8px 12px;background:#fafafa;font-size:11px;color:#666}.diagram-toolbar>div{display:flex;gap:5px}.diagram-toolbar button{font-size:10px;padding:3px 8px}.diagram{border:1px solid #e5e5e5;padding:12px;overflow:auto;max-height:900px;background:#fff;border-radius:0 0 5px 5px}.diagram svg{display:block;width:100%;height:auto;max-width:none;margin:auto}.pele .pele-node[data-id^="s"]{cursor:pointer}.pele .pele-node.selected rect{stroke:#1466b3!important;stroke-width:3px!important;filter:drop-shadow(0 1px 2px #1c66aa22)}.inspector{display:grid;grid-template-columns:1.4fr 1fr;gap:24px;padding:18px 0;border-bottom:1px solid #eee;min-height:118px}.inspector p{font-size:12px}.small-label{font-size:10px;color:#888}.details-grid details{padding:13px 0;border-bottom:1px solid #eee}summary{cursor:pointer;font-size:12px;color:#444}pre{overflow:auto;padding:16px;border:1px solid #e5e5e5;border-radius:4px;background:#fafafa;font:11px/1.9 ui-monospace,SFMono-Regular,monospace;white-space:pre;word-break:normal}code{font:11px/1.6 ui-monospace,monospace;background:#f4f4f4;padding:2px 4px;border-radius:3px}.rules{padding-left:18px;font-size:12px}.rules li{padding:5px 0}.rules span{display:block;margin-top:4px;color:#555}.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:20px 0}.filters input{border:1px solid #d6d6d6;border-radius:4px;padding:7px 10px;min-width:220px;font-size:12px}.filters select{border:1px solid #d6d6d6;border-radius:4px;padding:6px 8px;font-size:12px;background:#fff}.scenario-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);border:1px solid #e5e5e5;border-radius:5px;overflow:hidden}.scenario-list{max-height:690px;overflow:auto;background:#fafafa;padding:0}.scenario-choice{display:grid;grid-template-columns:25px minmax(0,1fr) auto;gap:9px;align-items:center;text-align:left;width:100%;font-size:11px;line-height:1.7;background:transparent;border:0;border-bottom:1px solid #e5e5e5;padding:13px 14px;border-radius:0}.scenario-choice.selected{background:#edf3f9;box-shadow:inset 2px 0 #286da7}.scenario-number{color:#888;font:10px Geist,sans-serif}.scenario-detail{border-left:1px solid #e5e5e5;padding:22px;min-width:0}.scenario-detail dl{margin:16px 0}.scenario-detail dt{font-size:10px;color:#888;margin-top:13px}.scenario-detail dd{font-size:12px;color:#444;margin:4px 0}.scenario-detail .actions{display:flex;gap:8px;margin-top:18px;align-items:center;flex-wrap:wrap}.scenario-detail pre{font-size:10px}.scenario-path{font-size:11px;padding:12px;border:1px solid #e5e5e5;border-radius:4px;color:#555;line-height:1.9}.stepper{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0}.step-label{font-size:12px;color:#444}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.9}th,td{padding:12px;border-bottom:1px solid #e5e5e5;vertical-align:top;text-align:left}th{font-weight:500;background:#fafafa;color:#666}footer{border-top:1px solid #e5e5e5;margin-top:45px;padding-top:20px;font-size:11px;color:#777}[hidden]{display:none!important}.trace{padding:16px;border:1px solid #e7d5d8;background:#fffafa;border-radius:5px;font-size:12px}.trace ol{padding-left:20px}.trace li{padding:4px 0}.evidence-links{display:flex;flex-wrap:wrap;gap:15px;margin:12px 0;font-size:12px}@media(max-width:1050px){.shell{grid-template-columns:190px minmax(0,1fr)}main{padding:28px 24px}.scenario-grid{grid-template-columns:1fr}.scenario-list{max-height:330px}.scenario-detail{border-left:0;border-top:1px solid #e5e5e5}.quick{grid-template-columns:1fr}.quick>div{border-right:0;border-bottom:1px solid #e5e5e5}}@media(max-width:700px){.shell{grid-template-columns:0 minmax(0,1fr)}.sidebar{visibility:hidden;padding:0}.topbar{padding:0 14px;gap:10px}.topbar strong{font-size:12px}.topbar .meta{display:none}main{padding:26px 16px}h1{font-size:25px}h2{font-size:21px}.inspector{grid-template-columns:1fr;gap:5px}.stats{gap:12px;font-size:11px}.diagram{padding:4px}.diagram-toolbar{flex-wrap:wrap}.scenario-choice{grid-template-columns:23px minmax(0,1fr)}.scenario-choice .badge{grid-column:2;justify-self:start}.filters input{min-width:0;flex:1}.table-wrap table{min-width:620px}.topbar button{font-size:11px}}@media print{.sidebar,.topbar,.filters,.diagram-toolbar,.scenario-list,.actions,.stepper{display:none!important}.shell{display:block}main{padding:0;max-width:none}.diagram{overflow:visible;max-height:none;border:0}.diagram svg{width:100%!important}.workflow{break-before:page}.details-grid details{display:block}.scenario-grid{display:block}.scenario-detail{border:0}.note{break-inside:avoid}h2{break-after:avoid}p,td,li{font-size:10px}}"""
css += "@media(max-width:700px){body.nav-open .sidebar{visibility:visible;padding:24px 14px;position:fixed;top:64px;left:0;width:260px;height:calc(100vh - 64px);z-index:12;border-right:1px solid #ddd;box-shadow:4px 0 24px #00000018}}"
nav = "".join(
    f'<a href="#{n}" class="{"pending-link" if n == "AgentTarget" else ""}">{i:02} {E(w["name"])}</a>'
    for i, (n, w) in enumerate(raw.items(), 1)
)
trace = [
    "첫 설치 · 운영본 없음",
    "환경·모델 연결 확인",
    "입력에서 후보 구축",
    "미승인 후보 생성",
    "포함할 변경 검토 (actor=local)",
    "대표 질문 검증 통과",
    "운영 반영 준비",
    "새 운영본 전환 — 개인 인증은 요구되지 않음",
]
trace_html = '<ol start="0">' + "".join("<li>" + E(x) + "</li>" for x in trace) + "</ol>"
source_tests = """현재 코드 기준은 커밋 6896be4입니다. 로컬 기본 작업 공간은 운영 r8·후보 r7이며, 이전 실제 수용 시험에서는 분리한 복사본의 r12 반영·r13 복원과 SQL 고객 수 40명, 한국어 계약금액 집계·CSV 재복구를 확인했습니다. 이번 문서 생성은 앱·DB·Cloudflare를 변경하지 않았습니다. 진행 중인 인증 계층 초안은 현재 구현으로 포함하지 않았습니다."""
body = f"""<header class="topbar"><button id="nav-toggle" aria-label="목차 접기·펼치기" aria-expanded="true">☰</button><strong>AutoOntology <span style="color:#888;font-weight:400">/ 상태로 읽는 사용 설명서</span></strong><span class="meta">LOCAL · 6896be4 · 2026.10.31</span><button id="print-guide">인쇄 / PDF</button></header><div class="shell"><nav class="sidebar" aria-label="설명서 목차"><p class="nav-title">GET STARTED</p><a href="#overview">읽는 방법과 빠른 시작</a><a href="#scenarios">21개 사용자 시나리오</a><p class="nav-title">STATECHARTS</p>{nav}<p class="nav-title">OPERATIONS</p><a href="#outcomes">Enterprise AX 시간 목표</a><a href="#limits">검사 결과와 남은 경계</a><a href="#terms">용어·재현 방법</a></nav><main><section id="overview"><p class="eyebrow">HUMAN FIRST / CLI PARITY / AGENT PREPARATION</p><h1>한 번 구축한 지식을,<br>매일 운영하는 조직의 기준으로</h1><p class="lead">어떤 상태에서 무엇을 하고, 언제 확인하며, 실패하면 어디로 돌아가는지 읽는 설명서입니다. 현재 구현과 목표 설계를 구분해 같은 흐름을 UI와 CLI에서 살펴봅니다.</p><div class="stats"><span><strong>{len(models)}개</strong> 상태차트</span><span><strong>21개</strong> 사용자 시나리오</span><span><strong>{provenance["states"]}개</strong> 추상 상태 탐색</span><span><strong>{provenance["passed_rules"]}개</strong> 모델 규칙 통과 · <strong>1개</strong> 미구현 경계 확인</span></div><div class="quick"><div><b>1. 먼저 시나리오를 고르세요</b><p>설치·데이터 연결·질문·개정·복원 중 본인의 일을 찾습니다.</p><a href="#scenarios">시나리오 선택 →</a></div><div><b>2. 상태와 확인 조건을 읽으세요</b><p>차트 상자를 선택하면 현재 단계가 강조됩니다. 아래 CLI·검사 규칙도 펼쳐볼 수 있습니다.</p></div><div><b>3. 결과와 복구 경로를 확인하세요</b><p>완료·보류·실패·취소·응답 유실을 구분합니다. 에이전트 위임은 목표 설계로 표시했습니다.</p></div></div><p class="meta">이 파일의 버튼은 설명·탐색용입니다. 앱 작업을 실행하거나 실제 데이터를 변경하지 않습니다. 외부 연결 없이 열 수 있습니다.</p></section><section id="scenarios"><div class="section-top"><p class="eyebrow">USER SCENARIOS</p><span class="meta" id="scenario-count">21개 시나리오</span></div><h2>사용자가 하려는 일에서 시작하기</h2><div class="filters"><input id="scenario-search" type="search" aria-label="시나리오 검색" placeholder="예: 계약, 복원, SQL"><select id="scenario-filter" aria-label="구현 상태 필터"><option value="all">전체</option><option value="current">현재 제공</option><option value="partial">일부 구현</option><option value="planned">목표 설계</option></select></div><div class="scenario-grid"><div class="scenario-list">{"".join(scards)}</div><article class="scenario-detail" id="scenario-detail" aria-live="polite"></article></div></section>{"".join(workflow)}<section id="outcomes"><p class="eyebrow">ENTERPRISE AX</p><h2>목표는 승인된 결과까지의 시간 단축</h2><p>에이전트 수나 자동화율보다, 요청부터 검토를 거쳐 쓸 수 있는 결과까지의 총시간과 사람의 실제 작업시간을 줄입니다. 빠르지만 틀린 결과는 성공이 아닙니다.</p><div class="table-wrap"><table><thead><tr><th>측정할 것</th><th>시작 → 끝</th><th>반드시 함께 볼 것</th></tr></thead><tbody><tr><td>총 리드타임 중앙값·p90</td><td>요청·변경 발견 → 승인된 결과 사용 가능</td><td>대기·실패·재작업도 포함</td></tr><tr><td>사람의 실작업 시간</td><td>명시적인 검토·수정·복구 작업 구간 합계</td><td>배정부터 승인까지의 경과 시간으로 추정하지 않음</td></tr><tr><td>확인 준비시간과 대기</td><td>에이전트 준비 완료 / 사람 검토 시작 / 확인 완료</td><td>담당자 부재·불충분한 패킷·승인 병목</td></tr><tr><td>품질과 재작업</td><td>최초 통과율·수정 횟수·복구 부담</td><td>잘못된 근거·권한 위반·운영 장애</td></tr></tbody></table></div><div class="note">계약·청구 규정 개정 파일럿의 수동 기준선을 먼저 측정하고, 비슷한 복잡도의 보조 작업과 비교해 조직 책임자가 수치 목표를 정합니다. 아직 사람 시간 기준선이나 절감률을 측정한 것으로 표시하지 않습니다. 현재 SQL 파일 재다운로드가 모델·DB 작업을 반복하지 않는 점은 검증했습니다.</div></section><section id="limits"><p class="eyebrow">MODEL CHECK / EVIDENCE / LIMITS</p><h2>차트가 증명한 범위와 아직 없는 경계</h2><p>{E(source_tests)}</p><div class="trace"><strong>알려진 미구현 경계: 인증된 사람의 운영 반영</strong><p>현재 모델의 <code>VerifiedHumanApproval</code> 규칙은 아래 최단 7단계 경로에서 깨집니다. 내용 확인을 하는 절차와 확인자의 개인 인증을 같은 것으로 표시하지 않았습니다.</p>{trace_html}<p>같은 경로를 기준 커밋의 격리된 저장소에서 재현했습니다. 제어된 임베딩·질문 모델로 검토와 검증을 통과한 후 actor=local만으로 운영 반영이 가능했습니다. 실제 Codex 호출 시험으로 꾸미지 않았으며, 인증된 Principal·에이전트 분리는 진행 중인 후속 구현입니다.</p></div><div class="table-wrap"><table><thead><tr><th>모델</th><th>추상 상태 / 전이</th><th>검사 결과</th></tr></thead><tbody>{"".join("<tr><td>" + E(raw[s["id"]]["name"]) + "</td><td>" + str(s["states"]) + " / " + str(s["steps"]) + "</td><td>" + ("인증된 사람 확인 규칙 1개 위반" if s["breaks"] else "선택한 규칙 통과" + (" — 목표 설계 모델만" if s["id"] == "AgentTarget" else "")) + "</td></tr>" for s in summary)}</tbody></table></div><p class="meta">TLC는 코드 전체가 아닌 유한한 추상 모델을 검사했습니다. 한 후보·한 SQL 요청·한 복원·한 위임의 선택된 상태를 모델링했습니다. 실제 사용자별 권한, DB 실행 계정의 전체 권한, 동시 부하, 원천 DB의 과거 행, 모델 답변의 의미적 정답, 다중 서비스의 부분 실패·보상은 이 모델 검사로 검증되지 않습니다. 상태가 변하지 않는 완료 유지 단계는 그림에서 생략되며, 같은 표시 상태로 합쳐지는 내부 전이는 각 원문 보고서에 개수를 기록했습니다. 모델 규칙을 녹색으로 만들려고 약화하지 않았습니다.</p></section><section id="terms"><p class="eyebrow">REFERENCE</p><h2>용어와 다시 확인하는 방법</h2><div class="table-wrap"><table><tbody><tr><th>후보</th><td>입력에서 새로 만든 미승인 지식. 운영본을 자동으로 대체하지 않습니다.</td></tr><tr><th>운영본</th><td>검토·검증을 거쳐 일반 조회가 읽는 버전. 그래프·검색·다운로드의 기준입니다.</td></tr><tr><th>검증 토큰 / 복원 확인값</th><td>준비한 내용과 맥락의 일치값. 사람의 로그인·역할 증명은 별도입니다.</td></tr><tr><th>사용 기록</th><td>그 요청의 버전·맥락·질의·결과와 완료/실패/취소를 보존한 기록입니다.</td></tr><tr><th>Principal / 위임</th><td>검증된 사람·에이전트 신원과 허용 범위·유효기간. 현재 완성된 제품 보증이 아닌 구현 중인 영역입니다.</td></tr></tbody></table></div><details class="note"><summary>이 로컬 앱에서 CLI 실행하기</summary><pre>cd /Users/macmini/Projects/AutoOntology/data/ontology-ops-local/app\nexport AUTOONTOLOGY_ENV_FILE=/Users/macmini/Projects/AutoOntology/data/ontology-ops-local/local.env\n.venv/bin/python -m autoontology ops list</pre><p>시나리오에 표시한 명령은 이 환경에서 <code>autoontology</code> 대신 <code>.venv/bin/python -m autoontology</code>로 실행할 수 있습니다. 설치·소스·변경 명령은 실제 효과가 있으므로 이 문서에서는 실행하지 않았습니다.</p></details><details class="note"><summary>상태차트를 다시 생성하는 명령</summary><pre>JAVA=/opt/homebrew/opt/openjdk@25/bin/java \\\nTLA2TOOLS=/private/tmp/autoontology-statechart-20261031/runtime/tla2tools.jar \\\nJAVA_TOOL_OPTIONS=-Xmx512m \\\n/Users/macmini/.codex/plugins/cache/cursor-plugins/pstack/0.15.36/skills/statechart/scripts/statechart \\\n  /private/tmp/autoontology-statechart-20261031/models/Publication.tla \\\n  --view /private/tmp/autoontology-statechart-20261031/models/Publication.view.mjs \\\n  --out /private/tmp/autoontology-statechart-NEW-OUTPUT</pre><p>출력 폴더는 새 경로여야 합니다. Publication의 종료 코드 1은 설명한 인증 경계 위반이며, 나머지 다섯 모델은 0입니다. TLA+·설정·뷰·SVG·Mermaid·TLC 원문·코드 재현 기록은 별도 근거 폴더에도 보존합니다.</p></details><div class="evidence-links"><a href="https://github.com/cursor/plugins/tree/main/pstack/skills/statechart" target="_blank" rel="noopener">pstack statechart 스킬</a><a href="https://pele.run/api" target="_blank" rel="noopener">Pele 렌더러</a></div></section><footer>pstack:statechart 0.15.36 · TLC 1.7.4 · Pele 1.9.0 · 기준 코드 6896be4 · 로컬 전용<br>실행 중인 앱 화면 캡처와 검증 기록은 별도의 단계별 변경 기록에 보존합니다. 이 문서는 상태 모델과 설명용 탐색 화면입니다.<details><summary>라이선스</summary><pre>{E(licenses + chr(10) + font_license)}</pre></details></footer></main></div>"""
data = {
    "workflows": {n: {k: v for k, v in w.items() if k != "svg"} for n, w in raw.items()},
    "scenarios": scenario_data,
    "status": status,
    "provenance": provenance,
}
js = """const DATA=JSON.parse(document.getElementById('guide-data').textContent);const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let selected=1;let stepIndex=0;function choose(id){selected=id;stepIndex=0;const s=DATA.scenarios.find(s=>s.id===id),w=DATA.workflows[s.model];document.querySelectorAll('.scenario-choice').forEach(b=>b.classList.toggle('selected',Number(b.dataset.scenario)===id));document.getElementById('scenario-detail').innerHTML=`<div class="section-top"><p class="eyebrow">SCENARIO ${String(s.id).padStart(2,'0')}</p><span class="badge ${s.coverage}">${esc(DATA.status[s.coverage])}</span></div><h3>${esc(s.title)}</h3><p>${esc(s.goal)}</p><dl><dt>누가 / 어디서</dt><dd>${esc(s.actor)} · ${esc(s.ui)}</dd><dt>진행 순서</dt><dd class="scenario-path">${esc(s.path)}</dd><dt>사람의 확인과 구현 경계</dt><dd>${esc(s.confirm)}</dd><dt>실패·복구</dt><dd>${esc(s.recovery)}</dd><dt>이 사례에서 측정할 시간</dt><dd>${esc(s.metric)}</dd></dl><details><summary>CLI 대응과 남은 범위</summary><pre>${esc(s.cli)}</pre></details><div class="actions"><button id="show-model">상태차트에서 보기</button><span class="meta">설명용 탐색 · 앱 실행 없음</span></div><div class="stepper"><button id="prev-state">이전 상태</button><button id="next-state">다음 상태</button><span class="step-label" id="step-label"></span></div>`;document.getElementById('show-model').onclick=()=>document.getElementById(s.model).scrollIntoView({behavior:'smooth'});document.getElementById('prev-state').onclick=()=>move(-1);document.getElementById('next-state').onclick=()=>move(1);showStep();}function showStep(){const s=DATA.scenarios.find(s=>s.id===selected),w=DATA.workflows[s.model];const path=s.model_path;const phase=path[stepIndex];const label=w.phase_labels[phase];document.getElementById('step-label').textContent=`${stepIndex+1}/${path.length} · ${label}`;document.getElementById('prev-state').disabled=stepIndex===0;document.getElementById('next-state').disabled=stepIndex===path.length-1;const alias=Object.keys(w.labels).find(k=>w.labels[k]===label);if(alias)selectNode(s.model,alias);}function move(delta){const s=DATA.scenarios.find(s=>s.id===selected),w=DATA.workflows[s.model];stepIndex=Math.max(0,Math.min(s.model_path.length-1,stepIndex+delta));showStep();}function selectNode(model,alias){const section=document.getElementById(model),w=DATA.workflows[model];section.querySelectorAll('.pele-node').forEach(n=>n.classList.toggle('selected',n.dataset.id===alias));section.querySelector('.state-title').textContent=w.labels[alias]||'상태';section.querySelector('.state-help').textContent=w.limit;}document.querySelectorAll('.scenario-choice').forEach(b=>b.onclick=()=>choose(Number(b.dataset.scenario)));function filter(){const q=document.getElementById('scenario-search').value.toLowerCase(),mode=document.getElementById('scenario-filter').value;let count=0;document.querySelectorAll('.scenario-choice').forEach(b=>{const s=DATA.scenarios.find(s=>s.id===Number(b.dataset.scenario));b.hidden=!((mode==='all'||s.coverage===mode)&&(s.title+' '+s.goal+' '+s.ui+' '+s.cli).toLowerCase().includes(q));if(!b.hidden)count++;});document.getElementById('scenario-count').textContent=`${count}개 / 21개 시나리오`;}document.getElementById('scenario-search').oninput=filter;document.getElementById('scenario-filter').onchange=filter;document.querySelectorAll('.workflow').forEach(section=>{const model=section.dataset.model,w=DATA.workflows[model];section.querySelectorAll('.pele-node[data-id^="s"]').forEach(node=>{node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',w.labels[node.dataset.id]||'상태');node.onclick=()=>selectNode(model,node.dataset.id);node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectNode(model,node.dataset.id);}};});let zoom=100;section.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>{zoom=b.dataset.zoom==='fit'?100:Math.max(70,Math.min(250,zoom+(b.dataset.zoom==='in'?25:-25)));section.querySelector('svg').style.width=zoom+'%';});section.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>{const svg=b.dataset.save==='svg';const text=svg?section.querySelector('svg').outerHTML:w.mmd;const url=URL.createObjectURL(new Blob([text],{type:svg?'image/svg+xml':'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=model+(svg?'.svg':'.mmd');a.click();setTimeout(()=>URL.revokeObjectURL(url),500);});});document.getElementById('nav-toggle').onclick=()=>{if(innerWidth<=700){const open=document.body.classList.toggle('nav-open');document.getElementById('nav-toggle').setAttribute('aria-expanded',String(open));}else{const collapsed=document.body.classList.toggle('collapsed');document.getElementById('nav-toggle').setAttribute('aria-expanded',String(!collapsed));}};if(innerWidth<=700)document.getElementById('nav-toggle').setAttribute('aria-expanded','false');document.querySelectorAll('.sidebar a').forEach(a=>a.addEventListener('click',()=>{if(innerWidth<=700){document.body.classList.remove('nav-open');document.getElementById('nav-toggle').setAttribute('aria-expanded','false');}}));document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.body.classList.remove('nav-open');if(innerWidth<=700)document.getElementById('nav-toggle').setAttribute('aria-expanded','false');}});document.getElementById('print-guide').onclick=()=>window.print();choose(1);"""
text = (
    '<!doctype html>\n<!-- SPDX-License-Identifier: MIT -->\n<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>AutoOntology — 상태차트 설명서와 21개 사용자 시나리오</title><style>'
    + font_css
    + css
    + "</style></head><body>"
    + body
    + '<script type="application/json" id="guide-data">'
    + json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    + "</script><script>"
    + js
    + "</script></body></html>"
)
target = args.output.absolute()
target.write_text(text)
(out / "guide.html").write_text(text)
print(
    json.dumps(
        {
            "html": str(target),
            "bytes": target.stat().st_size,
            "models": len(models),
            "scenarios": len(scenarios),
            "states": provenance["states"],
            "passed_rules": provenance["passed_rules"],
            "known_failed_rules": 1,
        }
    )
)
