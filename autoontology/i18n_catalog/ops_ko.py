# SPDX-License-Identifier: MIT
"""Korean texts of the UI-parity commands (``autoontology/cli_ops.py``)."""

MESSAGES: dict[str, str] = {
    "help.playground.sparql.examples": "플레이그라운드 페이지의 예제 질의를 보여 줍니다.",
    "ops.no_such_dir": "디렉터리가 없습니다: {path}",
    "help.opt.json": "결과를 JSON으로 출력합니다 (웹 API 응답 본문).",
    "help.opt.limit": "최대 행 수.",
    "help.opt.page": "페이지 번호 (1부터).",
    "help.opt.size": "페이지당 행 수.",
    "help.opt.revision": "스냅샷 리비전 (기본: 최신).",
    "help.review.limit": "페이지당 행 수 (--size와 같음).",
    "help.review.size": "페이지당 행 수 (기본: --limit).",
    "help.ontology": "온톨로지 스냅샷: 요약, revisions, diff, concepts, graph.",
    "help.ontology.revisions": "스냅샷 리비전 목록 (최신 순).",
    "help.ontology.diff": "두 리비전 사이의 변경 사항 (기본: 직전 -> 최신).",
    "help.ontology.diff.from": "이전 리비전 (기본: --to 직전).",
    "help.ontology.diff.to": "이후 리비전 (기본: 최신).",
    "help.ontology.concepts": "개념 목록·검색 (Term 클래스와 문서 개념).",
    "help.ontology.concepts.q": "검색어 (이름, 동의어, 정의).",
    "help.ontology.concepts.kind": "이 종류만 (term, entity, process, ...).",
    "help.ontology.concepts.origin": "이 출처만: structured | document | aligned.",
    "help.ontology.concepts.source": "이 소스의 개념만.",
    "help.ontology.concept": (
        "개념 하나: 정의, 동의어, 거절된 별칭, 정렬과 상태, 근거 인용, 검토 결정."
    ),
    "help.ontology.concept.id": "개념 ID (term-<id> 또는 concept-<id>, `ontology concepts` 참고).",
    "help.ontology.graph": "그래프 페이지가 그리는 그래프 (노드와 엣지).",
    "help.ontology.graph.layer": "레이어: all (기본) 또는 structured, documents, aligned.",
    "help.ontology.graph.evidence": "근거 노드도 추가합니다.",
    "help.ontology.graph.out": "그래프 JSON을 이 파일에 씁니다.",
    "help.runs": "최근 실행 목록(기본)과 show, logs, cancel.",
    "help.runs.id": "실행 ID (`uv run autoontology runs` 참고).",
    "help.runs.show": "실행 하나의 단계와 진행 상황 (서비스 필요).",
    "help.runs.logs": (
        "실행 로그를 출력합니다 (비밀 마스킹). --follow는 실행이 끝날 때까지 서비스의 실시간 "
        "이벤트를 받습니다."
    ),
    "help.runs.logs.follow": "실행이 끝날 때까지 서비스에서 새 줄을 받습니다.",
    "help.runs.logs.json": "JSON Lines: 이벤트 한 줄씩.",
    "help.runs.cancel": "서비스가 실행 중인 실행을 취소합니다.",
    "help.sources.freshness": "마지막 빌드 뒤 바뀐 소스와 그 이유.",
    "help.sources.refresh": "UI와의 대응을 위해 받습니다: CLI는 항상 지금 계산합니다.",
    "help.samples": "포함된 샘플 세트: 목록, 등록 (빌드 없이).",
    "help.samples.list": "샘플 세트와 소스, 등록 여부를 보여 줍니다.",
    "help.samples.load": (
        "샘플 세트의 소스를 등록합니다 (빌드는 안 함: 다음에 `uv run autoontology build`)."
    ),
    "help.samples.load.set": "샘플 세트: quickstart | hanbit.",
    "help.samples.load.replace": "같은 이름으로 등록된 소스를 교체합니다.",
    "help.exports": "내보내기 폴더 (exports/r<rev>/): 목록, 파일 보기, 파일·zip 저장.",
    "help.exports.list": "내보내기 폴더와 파일 목록.",
    "help.exports.show": (
        "리비전의 파일 목록, 또는 텍스트 파일 하나 출력 (예: ossie/validation.json)."
    ),
    "help.exports.rev": "리비전: 3 또는 r0003.",
    "help.exports.path": "폴더 안의 파일, 예: report.md 또는 okf/validation.json.",
    "help.exports.get": "내보내기 파일 하나를 저장합니다.",
    "help.exports.out": "저장할 파일 (또는 디렉터리).",
    "help.exports.zip": "내보내기 폴더 전체를 zip 하나로 저장합니다.",
    "help.exports.zip.out": "저장할 .zip 파일 (또는 디렉터리: autoontology-r<rev>.zip).",
    "help.playground": (
        "실행 중인 서비스의 플레이그라운드: chat (인용이 있는 RAG), sql, sparql, index."
    ),
    "help.playground.chat": "온톨로지에 질문합니다: 답변을 스트리밍하고 인용을 보여 줍니다.",
    "help.playground.question": "질문.",
    "help.playground.conversation": "이 대화를 이어 갑니다 (답변 뒤에 ID가 출력됨).",
    "help.playground.top_k": "검색할 요소 수 (1-40).",
    "help.playground.json": "JSON Lines: SSE 이벤트 한 줄씩.",
    "help.playground.sql": (
        "데이터에 자연어로 질문합니다 (업스트림 SQL 에이전트). 규칙·정의 질문은 SQL 없이 "
        "답할 수 있습니다. 한국어 질문에 영어로 답하면 한국어로 옮겨 출력합니다 "
        "(AUTOONTOLOGY_SQL_TRANSLATE=0이면 끔, --json에는 둘 다 있음)."
    ),
    "help.playground.sql.source": "질의할 정형 소스 (기본: 첫 번째).",
    "help.playground.sql.conversation": "이어 갈 대화 UUID.",
    "help.playground.sparql": "최신 스냅샷에 SPARQL 질의를 실행합니다.",
    "help.playground.sparql.query": "질의 (또는 --file).",
    "help.playground.sparql.file": "이 파일에서 질의를 읽습니다.",
    "help.playground.sparql.limit": "최대 행 수.",
    "help.playground.index": "채팅 인덱스 상태; --rebuild는 인덱스를 (다시) 만듭니다.",
    "help.playground.index.rebuild": "최신 스냅샷의 인덱스를 지금 만듭니다.",
    "help.settings": "설정 페이지: 모델 제공자 보기·테스트·저장, 서비스 재시작.",
    "help.settings.show": ".env가 고른 값, 각 값의 출처, 로그인, 벡터 (키는 절대 표시 안 함).",
    "help.settings.test": "후보 설정 테스트(저장 안 함): 채팅 pong과 임베딩 차원 확인.",
    "help.settings.set": (
        "후보 설정을 .env에 저장합니다 (백업, 권한 0600). API 키는 --api-key-stdin으로만."
    ),
    "help.settings.chat_provider": "채팅 제공자: ollama | claude | codex | openai.",
    "help.settings.chat_model": '그 제공자의 채팅 모델 ("" = 기본값).',
    "help.settings.embed_provider": "임베딩 제공자: ollama | openai.",
    "help.settings.embed_model": '그 제공자의 임베딩 모델 ("" = 기본값).',
    "help.settings.base_url": 'OpenAI 호환 기본 URL (""는 삭제).',
    "help.settings.api_key_stdin": (
        "API 키를 표준 입력에서 한 줄로 읽습니다 (터미널에서는 화면에 표시하지 않음)."
    ),
    "help.settings.use_saved_key": "저장된 API 키로 테스트 (기본) 또는 키 없이.",
    "help.settings.confirm_reingest": "임베딩 차원이 저장된 벡터와 달라도 저장합니다.",
    "help.settings.restart": (
        "실행 중인 서비스에 서비스와 백엔드 재시작을 요청합니다 (설정 페이지의 버튼)."
    ),
    "help.settings.restart.wait": "새 서비스가 응답할 때까지 기다리고 결과를 알려 줍니다.",
    "help.settings.restart.timeout": "--wait로 기다릴 초.",
    "ops.service_down": (
        "{url}에서 서비스가 실행 중이 아닙니다: `uv run autoontology up`으로 시작하세요"
    ),
    "ops.no_snapshot": "아직 온톨로지 스냅샷이 없습니다: `uv run autoontology build`를 실행하세요",
    "ops.more": "{count}개 더 (--json은 전부 표시)",
    "ops.no_concepts": "일치하는 개념이 없습니다",
    "ops.page": "{page}/{pages} 페이지 (전체 {total}개, 리비전 {revision}; 더 보려면 --page N)",
    "ops.written": "저장함: {path}",
    "ops.saved": "{path} 저장 ({bytes}바이트)",
    "ops.no_run_log": "실행 {run}의 로그 파일이 없습니다: CLI에서 시작한 실행은 {log}에 기록됩니다",
    "ops.cancel_requested": (
        "{run} 취소를 요청했습니다: 다음 단계에서 멈춥니다 (`uv run autoontology runs show {run}`)"
    ),
    "ops.no_sources": "소스가 없습니다 (`uv run autoontology source add`로 추가)",
    "ops.stale_count": "소스 {total}개 중 {stale}개가 오래됨",
    "ops.sample_replace_hint": (
        "교체하려면 --replace를 추가하세요 (해당 데이터베이스와 추출된 개념이 삭제됩니다)"
    ),
    "ops.samples_next": "다음: `uv run autoontology build`",
    "ops.bad_revision": "리비전은 3 또는 r0003 같은 숫자여야 합니다 (받은 값 {value!r})",
    "ops.no_exports": "아직 내보내기가 없습니다: `uv run autoontology export`를 실행하세요",
    "ops.conversation": "대화 {id} (이어 가려면 --conversation {id})",
    "ops.notice.conversation_store_missing": (
        "업스트림에 대화 기록 저장소(frontend 스키마)가 없어 이전 대화 없이 질문했습니다"
    ),
    "ops.sql.node.react_sql_agent": "데이터를 찾아 SQL 작성 중",
    "ops.sql.node.format_and_respond": "답변 정리 중",
    "ops.sql.node.unconstructable_sql_response": "SQL을 만들 수 없음",
    "ops.sql.translating": "답변을 한국어로 옮기는 중",
    "ops.sql.no_sql": "SQL 없이 답했습니다(규칙·정의 질문).",
    "ops.sql.unconstructable": (
        "찾은 데이터로는 이 질문에 맞는 SQL을 만들 수 없었습니다. "
        "원하는 표나 지표 이름을 넣어 다시 질문해 보세요."
    ),
    "ops.sparql_one_of": "질의를 인수 또는 --file 중 하나로 주세요",
    "ops.no_index": "아직 인덱스가 없습니다: `uv run autoontology playground index --rebuild`",
    "ops.settings_offline": (
        "서비스가 실행 중이 아닙니다: 'running'은 이 셸이 시작할 설정입니다 "
        "(`uv run autoontology up`)"
    ),
    "ops.restart_required": (
        "실행 중인 서비스가 다른 설정을 씁니다: `uv run autoontology settings restart --wait`"
    ),
    "ops.restart_in_progress": "재시작이 진행 중입니다",
    "ops.key_prompt": "API 키 (화면에 표시 안 됨): ",
    "ops.key_empty": "--api-key-stdin이 빈 줄을 읽었습니다: 키가 없습니다",
    "ops.key_and_clear": "--api-key-stdin과 --clear-api-key는 함께 쓸 수 없습니다",
    "ops.settings_nothing": "바꿀 것이 없습니다: 설정을 하나 이상 주세요",
    "ops.settings_written": "{keys}을(를) {path}에 썼습니다",
    "ops.settings_unchanged": "바뀐 것이 없습니다",
    "ops.restart_hint": (
        "적용하려면 재시작하세요: `uv run autoontology settings restart --wait` "
        "(또는 `uv run autoontology restart`)"
    ),
    "ops.dimension_mismatch": (
        "새 임베딩 모델은 {new}차원이지만 저장된 벡터는 {old}차원입니다: 저장하려면 "
        "--confirm-reingest와 모든 소스의 재빌드가 필요합니다"
    ),
    "ops.restart_started": (
        "재시작 {id} 시작: `uv run autoontology status`로 서비스 복귀를 확인하세요"
    ),
    "ops.restart_done": "재시작 {id} 완료: 서비스가 다시 실행 중입니다 (pid {pid})",
    "ops.restart_not_done": "재시작 {id}: {state} (로그: {log})",
    "help.agent": (
        "외부 에이전트(Claude Code, Cursor, 스크립트): MCP 엔드포인트(/mcp)와 "
        "REST API(/agent/v1)용 토큰과 사용량."
    ),
    "help.agent.tokens": "에이전트 토큰: 목록, 만들기, 교체, 폐기.",
    "help.agent.tokens.list": "토큰 목록 (토큰 값은 보이지 않음).",
    "help.agent.tokens.create": "토큰을 만듭니다. 토큰은 한 번만 출력됩니다.",
    "help.agent.tokens.create.name": "에이전트 이름, 예: claude-code.",
    "help.agent.tokens.create.revision": "이 리비전만 제공 (기본: 최신).",
    "help.agent.tokens.create.expires": "30 또는 90일 후 만료 (기본: 없음).",
    "help.agent.tokens.rotate": "토큰 교체: 이전 토큰은 즉시 멈춥니다.",
    "help.agent.tokens.revoke": "토큰 폐기 (호출 기록은 남음).",
    "help.agent.tokens.id": "토큰 id (tok_..., `agent tokens list` 참고).",
    "help.agent.usage": "에이전트 호출: 도구·토큰·날짜별 합계.",
    "help.agent.usage.days": "최근 N일만 (기본: 보관된 전체).",
    "ops.agent_token_once": "이 토큰은 지금 한 번만 보입니다: 바로 복사하세요.",
    "ops.agent_connect_hint": (
        "Claude Code 연결: claude mcp add --transport http autoontology "
        '<origin>/mcp --header "Authorization: Bearer <token>"'
    ),
    "ops.agent_no_tokens": (
        "에이전트 토큰이 없습니다: `uv run autoontology agent tokens create <name>`"
    ),
    "ops.agent_no_calls": "아직 에이전트 호출이 없습니다",
    "ops.agent_revoked": "{id} ({name}) 폐기됨",
    "ops.agent_bad_expiry": "--expires-days는 30 또는 90이어야 합니다 (받은 값 {value})",
    "ops.agent_usage_total": "호출 {total}건, 성공 {ok}건",
}
