export const title = "Managed restoration - current implementation";
export const state = v => ({"operating": "현재 운영본 사용 중", "preparing": "과거 지식·차이·근거 준비", "ready": "복원 내용 확인 대기", "executing": "확인한 복원안 실행", "stale": "내용 변경 · 새 복원안 필요", "expired": "만료 · 새 복원안 필요", "wrong": "확인 내용 불일치", "failed": "실패 · 현재 운영 유지", "committed": "새 버전으로 복원 완료", "uncertain": "응답 유실 · 결과 확인"})[v.phase];
export const step = (name) => ({"Prepare": "목표 버전·복원 사유 지정", "Ready": "원문·차이·대표 질문 검증 완료", "PreparationFails": "원문·검증·매핑 준비 실패", "WrongConfirmation": "다른 확인값으로 실행 요청", "ReadCorrectPacket": "준비된 복원안을 다시 확인", "Expire": "복원안 유효기간 종료", "ContextChanged": "운영본·설정·목표·검증 변경", "Confirm": "이 내용으로 복원 확인", "FailureBeforeCommit": "전환 전 실패 · 기존 운영 유지", "Commit": "새 운영 버전·검색·파일 함께 전환", "LostResponse": "서버 완료 후 응답 유실", "RetrySame": "동일 확인 재시도 · 기존 결과 반환", "IdleStep": "현재 상태 유지"})[name] ?? name;
export const group = () => "구현된 흐름";
export const colors = {"구현된 흐름":"blue","목표 설계":"tan"};
