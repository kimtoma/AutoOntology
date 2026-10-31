export const title = "SQL and result recovery - current implementation";
export const state = v => v.phase === "cancelled" && !v.accepted ? "전송 전 중지 (기록 없을 수 있음)" : ({"idle": "질문 입력", "catalog": "운영 범위 확인", "planning": "승인된 지식으로 SQL 준비", "checked": "실행 가능한 SELECT", "executing": "제한된 원본 조회", "complete": "완료 기록과 결과", "saved": "CSV 저장 완료", "failed": "실패 · 질문 수정", "cancelled": "취소 기록 확인"})[v.phase];
export const step = (name) => ({"Ask": "질문 전송 · 사용 기록 시작", "StopBeforeSend": "전송 전 중지 · 기록이 없을 수 있음", "VerifyCatalog": "운영본과 대상 소스 확인", "CatalogMissing": "운영 카탈로그·소스 식별 실패", "PlanAllowed": "승인된 테이블·컬럼의 SELECT", "PlanRejected": "미승인 필드·쓰기·불명확한 질문 차단", "Execute": "시간·500행·용량 제한으로 읽기", "ReturnRows": "반환 결과와 종료 기록 저장", "DatabaseFails": "원본 연결·실행·시간 초과 실패", "Stop": "중지 요청 · 실제 종료 기록 확인", "Download": "UI·CLI에서 같은 기록의 CSV 저장", "Reopen": "이력을 다시 열어 파일 복구", "IdleStep": "현재 상태 유지"})[name] ?? name;
export const group = () => "구현된 흐름";
export const colors = {"구현된 흐름":"blue","목표 설계":"tan"};
