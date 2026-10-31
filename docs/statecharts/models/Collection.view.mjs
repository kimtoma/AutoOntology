export const title="지속 수집 · 배정·중단·재시도·완료";
export const state=v=>({queued:"변경 입력 대기",working:v.writes?"후보 준비 완료 · 기록 대기":"내 작업으로 처리 중",retry:"작업자 응답 만료 · 재시도 대기",done:"완료 기록 보존",blocked:"권한·범위 확인 필요",cancelled:"사람이 중지",failed:"실패 · 담당자 인계"})[v.phase];
export const step=n=>({Claim:"배정과 현재 권한으로 실행",Prepare:"현재 실행 세대에서 후보 준비",LoseWorker:"작업자 중단 · 이전 세대 폐기",Reclaim:"새 작업자가 배정 작업 재개",Finish:"기존 준비 결과의 완료 기록",Revoke:"만료·철회로 이후 저장 차단",HumanResume:"사람이 범위를 확인해 재개",Cancel:"사람이 중지",Fail:"한도·입력 실패를 담당자에게",LateCompletion:"이전 실행의 늦은 결과 차단",Idle:"기록과 현재 상태 유지"})[n]??n;
export const group=v=>["blocked","failed"].includes(v.phase)?"사람 확인":"허용된 수집";
export const colors={"사람 확인":"orange","허용된 수집":"blue"};
