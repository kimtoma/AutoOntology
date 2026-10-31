export const title = "Knowledge change - current implementation";
export const state = v => ({"operating": "기존 운영본 사용 중", "changed": "입력 변경 확인", "reading": "원문·개정 읽기", "candidate": "새 후보 · 기존 운영 유지", "held": "보류 · 운영본 그대로", "reviewed": "반영할 변경 선택", "failed": "질문 검증 실패", "ready": "운영 반영 확인 대기", "published": "검증한 새 운영본", "rejected": "제외 · 기존 운영 유지"})[v.phase];
export const step = (name) => ({"Detect": "신선도 확인으로 변경 발견 [수동]", "Read": "새 입력 읽기 · 원문은 보존", "Build": "문서·정형 지식으로 후보 생성", "Hold": "충돌·불충분한 근거 보류", "Resume": "근거 보완 후 검토", "Reject": "불필요한 변경 제외", "Accept": "반영할 변경만 확인", "Pass": "대표 질문 검증 통과", "Fail": "답변·인용이 검증 기준에 미달", "Correct": "수정 후 재검토", "ChangeAgain": "검토 중 입력이 다시 바뀜", "Publish": "검증한 변경만 운영 반영", "IdleStep": "현재 상태 유지"})[name] ?? name;
export const group = () => "구현된 흐름";
export const colors = {"구현된 흐름":"blue","목표 설계":"tan"};
