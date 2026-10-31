export const title = "Publication - current implementation";
export const state = v => ({"empty": "첫 설치 · 운영본 없음", "checking": "모델·데이터 연결 확인", "building": "입력에서 후보 구축", "candidate": "미승인 후보", "held": "근거 확인을 위해 보류", "reviewed": "선택한 변경 검토 완료", "validation_failed": "대표 질문 검증 실패", "validated": "대표 질문 검증 통과", "prepared": "운영 반영 준비 완료", "published": "새 운영본으로 전환", "blocked": "연결·준비 실패"})[v.phase];
export const step = (name) => ({"Start": "환경과 로그인 확인", "Build": "정형·문서 소스 구축 시작", "ConnectionFails": "모델 로그인·연결 실패", "RetryConnection": "원인 해결 후 재확인", "CandidateReady": "후보 생성 완료 · 아직 운영 반영 안 됨", "Hold": "판단이 어려운 변경 보류", "ResumeReview": "근거를 보완하고 검토 재개", "Accept": "포함할 변경을 확인", "CheckPass": "대표 질문과 인용 검사 통과", "CheckFails": "대표 질문 불일치 · 반영 중지", "Correct": "원인 수정 후 다시 검토", "Prepare": "원문·검색·산출물 준비", "ContextChanges": "후보·설정이 바뀌어 재검증 필요", "Confirm": "확인한 운영본으로 함께 전환", "IdleStep": "현재 상태 유지"})[name] ?? name;
export const group = () => "구현된 흐름";
export const colors = {"구현된 흐름":"blue","목표 설계":"tan"};
