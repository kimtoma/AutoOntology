export const title = "Delegation and human confirmation - target design";
export const state = v => ({"assigned": "담당 에이전트에 범위·기한 위임", "preparing": "근거·영향·검증 준비", "awaiting": "사람의 확인 대기", "approved": "정확한 변경에 승인 결합", "executing": "허용된 효과 실행", "blocked": "철회·만료·변경으로 중지", "rejected": "사람이 보류·거절", "succeeded": "승인된 결과 사용 가능", "uncertain": "응답 유실 · 실행 기록 확인"})[v.phase];
export const step = (name) => ({"Prepare": "허용된 지식으로 변경안 준비", "Ready": "근거·영향·품질·복구안을 제출", "HumanApprove": "인증된 사람이 정확한 내용을 확인", "AgentSelfApproval": "에이전트의 자기 승인 차단", "Reject": "사람이 보류·수정·거절", "Revoke": "위임 철회 · 새 효과 중지", "Expire": "승인·위임 기한 만료", "Change": "내용·대상·정책이 바뀌어 재확인 필요", "Execute": "유효한 승인과 권한을 다시 검사", "Complete": "정확히 승인된 효과를 한 번 실행", "LostResponse": "완료 응답을 받지 못함", "InspectReceipt": "실행 기록으로 완료 확인 · 중복 실행 없음", "IdleStep": "현재 상태 유지"})[name] ?? name;
export const group = () => "목표 설계";
export const colors = {"구현된 흐름":"blue","목표 설계":"tan"};
