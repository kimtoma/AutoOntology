# SPDX-License-Identifier: MIT
"""Korean CLI messages (same keys as ``en.py``; commands and codes stay in English)."""

MESSAGES: dict[str, str] = {
    "lang.hint": "English: AUTOONTOLOGY_LANG=en",
    "word.yes": "예",
    "word.no": "아니요",
    # --- help texts -------------------------------------------------------------------
    "help.doctor": "Postgres, pgvector, 마이그레이션, 모델 엔드포인트, 포트를 점검합니다.",
    "help.doctor.skip_models": "채팅/임베딩 호출을 건너뜁니다.",
    "help.provider_app": "채팅 제공자를 확인, 테스트, 전환합니다.",
    "help.init": (
        "Postgres, 채팅 제공자, 임베딩, 문서 엔진, .env를 차례로 설정하고 `setup`까지 "
        "실행합니다. 여러 번 실행해도 안전합니다."
    ),
    "help.init.provider": "채팅 제공자: ollama, claude, codex, openai 중 하나 (기본값: 자동 감지).",
    "help.init.chat_model": "해당 제공자의 채팅 모델.",
    "help.init.docs": "kordoc 문서 엔진과 OCR 모델 설치 (기본값: Node.js 20+가 있으면 설치).",
    "help.init.vision_model": "이미지 설명용 Ollama 비전 모델 (선택).",
    "help.init.yes": "묻지 않고 감지된 기본값으로 진행 (스크립트, CI용).",
    "help.init.env": "작성할 .env 파일 (기본값: <repo>/.env).",
    "help.demo": (
        "샘플 세트로 전체 파이프라인을 실행합니다: 서비스 시작, 샘플 소스 등록, 빌드, "
        "내보내기, 웹 UI 열기."
    ),
    "help.demo.set": "샘플 세트: quickstart (소형) | hanbit (가상 회사 전체).",
    "help.demo.no_open": "웹 UI를 열지 않음.",
    "help.demo.resume": "가장 최근 빌드를 이어서 실행 (성공한 단계는 유지).",
    "help.demo.replace": (
        "같은 이름의 다른 소스를 묻지 않고 교체합니다. 그 데이터베이스와 추출된 개념은 삭제됩니다."
    ),
    "help.provider.status": "모든 채팅 제공자의 사용 가능 여부, 로그인, 모델, 최근 테스트, 조치.",
    "help.provider.test": '채팅 라우터를 거쳐 구조화된 테스트 호출("pong")을 1회 보냅니다.',
    "help.provider.test.provider": "ollama | openai | claude | codex (기본값: 현재 제공자).",
    "help.provider.use": ".env의 채팅 제공자를 전환합니다 (주석과 다른 줄은 그대로 유지).",
    "help.provider.use.chat_model": "해당 제공자의 채팅 모델.",
    # --- setup ------------------------------------------------------------------------
    "setup.postgres_failed": "postgres: {error}\n먼저 Postgres를 시작하세요. 예: `docker compose up -d`",
    "setup.migrations": "migrations: 데이터베이스 리비전 {current} (최신 {head})",
    "setup.models_skipped": "models: 건너뜀 (--skip-models)",
    "setup.roles_openai": "채팅과 임베딩 모두 openai 제공자 사용",
    "setup.roles_mixed": "채팅은 {provider} 제공자, 임베딩은 openai 제공자 사용",
    "setup.models_skipped_roles": (
        "models: 건너뜀 ({roles}; 엔드포인트 확인은 `uv run autoontology doctor`)"
    ),
    "setup.models_present": "models: {models} 준비됨",
    "setup.models_pulled": " (내려받음: {models})",
    "setup.models_alias": (
        "models: {alias} 응답 {answer}, completion_tokens {tokens} (thinking 꺼짐); 임베딩 {embed}"
    ),
    "setup.vision_present": "vision model: {model} 준비됨",
    "setup.vision_pulled": " (내려받음)",
    "setup.vision_skipped": "vision model: {model} 건너뜀 (--skip-models)",
    "setup.kordoc_skipped_init": (
        "kordoc: 건너뜀 (문서 지원에 '예'로 답하거나 나중에 "
        "`uv run autoontology tools install-kordoc` 실행)"
    ),
    "setup.kordoc_skipped": "kordoc: 건너뜀 (HWP/PDF/DOCX/PPTX 문서는 --docs 지정)",
    "setup.next": "다음: uv run autoontology up",
    # --- source add / rm ----------------------------------------------------------------
    "source.no_such_path": "source add 실패: 파일이나 디렉터리가 없습니다: {path}",
    "source.exists": (
        "source add 실패: 소스 {name!r}이(가) 이미 있습니다 (`source rm {name}`로 제거)"
    ),
    "source.add_failed": "source add 실패: {error}",
    "source.added_documents": "소스 {name} 추가됨 (documents)",
    "source.added": "소스 {name} 추가됨 ({kind}) -> {database}",
    "source.locator": "  위치: {locator}",
    "source.files": "  파일 ({count}개): {names}",
    "source.unknown_kind": "알 수 없는 --kind {kind!r} (structured | documents)",
    "source.next_build": "다음: uv run autoontology build",
    "source.unknown": "알 수 없는 소스 {name!r}",
    "notice.removed_source": "소스 {name} 제거됨",
    "notice.removed_documents_source": ("문서 소스 {name} 제거됨 (문서, 청크, 개념, 관계, 근거)"),
    "notice.dropped_database": "데이터베이스 {name} 삭제됨",
    "notice.graph_deleted": "{name}의 수집 그래프 삭제됨",
    "notice.no_connection": "업스트림 연결 {name} 없음 (이미 삭제됨)",
    "notice.connection_deleted": (
        "업스트림 연결 {name} 삭제됨. 서비스가 수집 그래프를 지우는 중 ..."
    ),
    "notice.drop_db_ignored": "--drop-db 무시됨: 로컬 데이터베이스는 files 소스만 가집니다",
    # --- doctor -------------------------------------------------------------------------
    "doctor.header.check": "점검",
    "doctor.header.status": "상태",
    "doctor.header.detail": "상세",
    "doctor.header.remedy": "조치",
    "remedy.openai_base_url": ".env에 AUTOONTOLOGY_OPENAI_BASE_URL을 설정하세요",
    "remedy.claude_login": "이 사용자로 `claude auth login` 실행 (또는 `claude` 실행 후 /login)",
    "remedy.codex_login": "이 사용자로 `codex login` 실행 (ChatGPT 계정)",
    "remedy.install_cli": "{bin} CLI를 설치하고 로그인하세요 ({login})",
    "remedy.env_missing": "uv run autoontology init (.env 작성; 또는 cp .env.example .env)",
    "remedy.start_postgres": "Postgres 시작. 예: `docker compose up -d`",
    "remedy.pgvector": (
        "uv run autoontology setup (서버에 pgvector 필요. 예: docker-compose.yml 이미지)"
    ),
    "remedy.port": "해당 프로세스를 중지하거나 .env에서 포트 변경",
    "remedy.start_postgres_row": "Postgres 시작 (`docker compose up -d`)",
    "remedy.see_postgres_row": "postgres 행 참고",
    "remedy.prune_unowned": (
        "다른 체크아웃에서 쓰지 않는다면: uv run autoontology tools prune-connections "
        "--include-unowned"
    ),
    "remedy.node": "Node.js 20+ 설치 (HWP/PDF/DOCX/PPTX/XLSX 문서에만 필요)",
    "remedy.ocr": "`uv run autoontology tools install-kordoc` 실행",
    "remedy.start_ollama": "Ollama 시작 (`ollama serve`)",
    "remedy.vision": "Ollama 시작 (`ollama serve`) 후 .env의 AUTOONTOLOGY_VISION_MODEL 확인",
    "remedy.profile_values": (
        ".env에 AUTOONTOLOGY_OPENAI_* / _CLAUDE_* / _CODEX_* 값 설정 (.env.example 참고)"
    ),
    "remedy.profile_alias": (
        "AUTOONTOLOGY_OLLAMA_CHAT_MODEL을 openai/<name> 형식의 Ollama 별칭으로 설정 "
        "(`uv run autoontology setup`이 openai/qwen3:8b 생성)"
    ),
    "remedy.profile_pick": "목록에 있는 모델 선택 (.env.example 참고)",
    "remedy.embed_provider": (
        ".env의 AUTOONTOLOGY_EMBED_PROVIDER와 해당 모델 확인 (.env.example 참고)"
    ),
    "remedy.no_think": "AUTOONTOLOGY_OLLAMA_NO_THINK 켜 두기 / Ollama 업그레이드 확인",
    "remedy.embedder_changed": (
        "수집 후 임베더가 바뀜: 이전 EMBED 모델로 되돌리거나, 모든 소스를 "
        "`autoontology source rm`으로 제거하고 vdb 테이블 비우기"
    ),
    "remedy.none_needed": "조치 불필요: 다음 빌드가 빈 테이블을 다시 만듦",
    "remedy.check_embed_pg": "EMBED_* 설정과 Postgres 확인",
    "remedy.openai_env": ".env의 AUTOONTOLOGY_OPENAI_BASE_URL/API_KEY/CHAT_MODEL 확인",
    "remedy.cli_chat": "uv run autoontology doctor (chat provider 행)",
    "remedy.check_embed": ".env의 EMBED_* 확인",
    "remedy.check_embed_up": ".env의 EMBED_* 확인 / 모델 서버 실행 여부 확인",
    # --- init ---------------------------------------------------------------------------
    "init.step.postgres": "Postgres",
    "init.step.provider": "채팅 제공자",
    "init.step.embeddings": "임베딩",
    "init.step.documents": "문서 (kordoc: HWP/PDF/DOCX/PPTX/XLSX 및 이미지 OCR)",
    "init.step.env": "{path} 작성",
    "init.step.setup": "설정 (데이터베이스, 마이그레이션, 모델)",
    "init.step.setup_docs": "설정 (데이터베이스, 마이그레이션, 모델, kordoc)",
    "init.step.summary": "요약",
    "init.bad_provider": "--provider는 {choices} 중 하나여야 합니다 (입력값: {value!r})",
    "init.bad_model": "{flag} {value!r}은(는) 올바른 모델 이름이 아닙니다",
    "init.pg_fix_docker": "  조치: 이 폴더에서 동봉된 Postgres 시작: `docker compose up -d`",
    "init.pg_fix_install": (
        "  조치: Docker 설치 (https://docs.docker.com/get-docker/) 후 이 폴더에서 "
        "`docker compose up -d` 실행"
    ),
    "init.pg_fix_own": (
        "        또는 {env_path}에 POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / "
        "POSTGRES_PASSWORD를 pgvector가 있는 자체 Postgres 17로 설정"
    ),
    "init.pg_retry": (
        "  {where}의 Postgres에 연결할 수 없습니다. 시작(docker compose up -d)한 뒤 Enter를 "
        "누르면 다시 시도하고, n을 누르면 중단합니다"
    ),
    "init.pg_stopped": (
        "init 중단: Postgres에 연결할 수 없습니다. 위의 조치를 적용한 뒤 "
        "`uv run autoontology init`을 다시 실행하세요."
    ),
    "init.not_ready": "준비 안 됨",
    "init.fix": "  조치: {remedy}",
    "init.login_stopped_yes": (
        "init 중단: {name} CLI에 로그인되어 있지 않습니다. 터미널에서 `{command}`을(를) "
        "실행한 뒤 init을 다시 실행하세요 (또는 --provider로 다른 제공자 선택)."
    ),
    "init.login_retry": "  `{command}` 실행 후 다시 시도할까요?",
    "init.still_not_ready": "  아직 준비 안 됨: {note}",
    "init.login_stopped": (
        "init 중단: {name}에 로그인되어 있지 않습니다. `{command}` 실행 후 "
        "`uv run autoontology init`을 다시 실행하세요 (또는 다른 제공자 선택)."
    ),
    "init.col.provider": "제공자",
    "init.col.found": "설치됨",
    "init.col.logged_in": "로그인",
    "init.col.note": "비고",
    "init.current_vs_recommended": "  note  현재: {current}; 권장: {recommended}",
    "init.keeping": "  ok    현재 제공자 {current} 유지",
    "init.keeping_switch": " (권장: {recommended}; 전환하려면 --provider {recommended})",
    "init.prompt_provider": "  채팅 제공자",
    "init.prompt_provider_current": "  채팅 제공자 (현재: {current}; 권장: {recommended})",
    "init.ollama_missing": (
        "  warn  Ollama가 응답하지 않습니다 ({note}): https://ollama.com에서 설치하고 "
        "시작하세요 (`ollama serve`). 모델은 setup이 내려받습니다"
    ),
    "init.prompt_base_url": "  Base URL (/v1 포함)",
    "init.openai_base_needed": (
        "init 중단: openai 제공자에는 https://api.openai.com/v1 같은 base URL이 필요합니다 "
        "(.env에 AUTOONTOLOGY_OPENAI_BASE_URL을 설정하거나 --yes 없이 init 실행)"
    ),
    "init.prompt_chat_model": "  채팅 모델{hint}",
    "init.model_needed": (
        "init 중단: {provider} 제공자에는 채팅 모델 이름이 필요합니다 "
        "(--chat-model 지정 또는 --yes 없이 init 실행)"
    ),
    "init.chat_ok": "  ok    채팅: {provider} {model}",
    "init.openai_key_note": (
        "  note  AUTOONTOLOGY_OPENAI_API_KEY=… 는 직접 .env에 추가하세요 "
        "({env_path}; 키를 채팅이나 기록이 남는 셸에 붙여 넣지 마세요)"
    ),
    "init.prompt_embed_provider": "  임베딩 제공자",
    "init.embed_kept": "  ok    임베딩: openai {model} (현재 설정 유지)",
    "init.model_not_set": "(모델 미설정)",
    "init.prompt_embed_model": "  임베딩 모델",
    "init.embed_model_needed": (
        "init 중단: openai 임베딩에는 모델 이름이 필요합니다 "
        "(.env에 AUTOONTOLOGY_OPENAI_EMBED_MODEL을 설정하거나 --yes 없이 init 실행)"
    ),
    "init.embed_openai": "  ok    임베딩: openai {model} ({base})",
    "init.embed_present": "준비됨",
    "init.embed_pulled_by_setup": "setup이 내려받음",
    "init.embed_needs_ollama": "Ollama 실행 필요 (https://ollama.com, `ollama serve`)",
    "init.embed_ollama": "  ok    임베딩: ollama {model} ({state})",
    "init.docs_need_node": "  warn  --docs에는 Node.js 20 이상이 필요합니다",
    "init.node_not_found": "Node.js 20+를 찾을 수 없음",
    "init.docs_skip": (
        "  skip  {detail}. Markdown, 텍스트, HTML, CSV, .doc 문서는 그대로 처리됩니다. 나머지 "
        "형식은 Node.js 20+ (https://nodejs.org) 설치 후 "
        "`uv run autoontology tools install-kordoc` 실행"
    ),
    "init.prompt_docs": "  kordoc과 OCR 모델(약 18 MB)을 지금 설치할까요?",
    "init.docs_yes": "  ok    문서: setup에서 kordoc + OCR 모델 설치",
    "init.docs_no": "  ok    문서: 건너뜀 (나중에: `uv run autoontology tools install-kordoc`)",
    "init.env_created": "  ok    .env.example에서 {path} 생성",
    "init.env_set": "  set   {change}",
    "init.env_unchanged": "  ok    변경 없음 (이미 설정됨)",
    "init.env_backup": "  ok    이전 파일 백업: {path}",
    "init.warn": "  warn  {warning}",
    "init.shell_override": (
        "셸에 {key}={value}이(가) 설정되어 있어 .env보다 우선합니다: `unset {key}` 실행 "
        "(셸 프로필에서도 제거)"
    ),
    "init.setup_stopped": (
        "setup 단계에서 init 중단 (위 줄 참고): 문제를 해결한 뒤 "
        "`uv run autoontology init`을 다시 실행하세요 (반복 실행해도 안전합니다)"
    ),
    "init.col.item": "항목",
    "init.col.value": "값",
    "init.sum.documents_kordoc": "kordoc + OCR",
    "init.sum.documents_native": "기본 형식만",
    "init.sum.vision_off": "꺼짐 (이미지: OCR 텍스트만)",
    "init.next": "\n다음: {command}",
    # --- demo ---------------------------------------------------------------------------
    "demo.bad_set": "demo: {error}",
    "demo.step.services": "서비스",
    "demo.step.samples": "샘플 소스 ({set})",
    "demo.step.build": "빌드",
    "demo.step.export": "내보내기",
    "demo.review_required": "첫 구축 결과는 변경안입니다. 웹에서 변경을 검토하고 대표 질문을 추가한 뒤 검증·게시하면 운영 다운로드를 사용할 수 있습니다.",
    "demo.step.ui": "웹 UI",
    "demo.live_connections": (
        "  note  이 체크아웃에 소스가 없는 업스트림 연결 (다른 체크아웃이 쓸 수 있어 유지): {names}"
    ),
    "demo.pruning": (
        "  note  데이터베이스가 사라진 업스트림 연결 정리 (남겨 두면 모든 수집이 시간 "
        "초과됨): {names}"
    ),
    "demo.prune_backend": (
        "  warn  업스트림 연결을 확인하지 못했습니다 ({error}); 빌드가 시간 초과되면 "
        "`{command}` 실행"
    ),
    "demo.prune_postgres": (
        "  warn  업스트림 연결을 확인하지 못했습니다: Postgres에 연결할 수 없음 "
        "({error}); 시작(`docker compose up -d`) 후 `uv run autoontology doctor` 실행"
    ),
    "demo.prune_other": (
        "  warn  업스트림 연결을 확인하지 못했습니다 ({error}); `uv run autoontology doctor` 참고"
    ),
    "demo.conflict": "  {name}: 등록된 위치 {registered} ({kind}), 샘플 위치 {sample}",
    "demo.confirm_replace": (
        "  소스 {name}을(를) 교체할까요 (해당 데이터베이스와 추출된 개념이 삭제됨)?"
    ),
    "demo.kept_stop": (
        "demo 중단: 소스 {name!r}을(를) 유지했습니다. 교체하려면 --replace로 다시 "
        "실행하거나 직접 제거하세요 (`uv run autoontology source list`)"
    ),
    "demo.register_failed": (
        "demo: 샘플 소스를 등록하지 못했습니다: {error}\n"
        "`uv run autoontology source list`를 확인하고, 충돌하는 소스는 "
        "`uv run autoontology source rm <name>`로 제거하세요"
    ),
    "demo.expected": "  {set} 첫 빌드 예상 시간 (이후 빌드는 결과를 재사용):",
    "demo.col.provider": "제공자",
    "demo.col.time": "시간",
    "demo.col.note": "비고",
    "demo.col.step": "단계",
    "demo.col.status": "상태",
    "demo.chat_provider": (
        "  채팅 제공자: {provider} {model} (전환: `uv run autoontology provider use <name>`)"
    ),
    "demo.resume": "demo 중단: `uv run autoontology demo --resume`으로 이어서 실행",
    "demo.build_total": "  빌드 합계: {seconds:.1f}초",
    "demo.finished": "\ndemo 완료: {seconds:.1f}초",
    "demo.look_next": "다음에 볼 곳: 검토함 → 변경 이력 → 온톨로지 → 그래프 → 질문·활용",
    # --- provider -----------------------------------------------------------------------
    "provider.col.provider": "제공자",
    "provider.col.available": "사용 가능",
    "provider.col.logged_in": "로그인",
    "provider.col.model": "모델",
    "provider.col.last_test": "최근 테스트",
    "provider.col.remedy": "조치",
    "provider.last_ok": "성공",
    "provider.seconds": "{seconds}초",
    "provider.last_failed": "실패",
    "provider.active": "* = 현재 채팅 제공자 ({provider}, 출처: {origin})",
    "provider.test_ok": "{name} 성공: {model}이(가) {seconds}초 만에 {answer!r} 응답 (경로: {via})",
    "provider.test_failed": "{name} 실패: {error} ({seconds}초)\n조치: {remedy}",
    "provider.no_answer": "응답 없음",
    "provider.see_status": "`uv run autoontology provider status` 참고",
    "provider.unknown": "알 수 없는 제공자 {name!r}: {choices} 중에서 선택",
    "provider.set": "{path}에 {change} 설정",
    "provider.unchanged": "{path}에서 이미 {name}을(를) 선택 중입니다. 변경 없음",
    "provider.backup": "백업: {path}",
    "provider.warning": "경고: {warning}",
    "provider.openai_warning": (
        "경고: {path}에 AUTOONTOLOGY_OPENAI_BASE_URL, AUTOONTOLOGY_OPENAI_CHAT_MODEL, "
        "AUTOONTOLOGY_OPENAI_API_KEY를 설정하세요 (키는 직접 입력)"
    ),
    "provider.not_ready": "경고: {name} 준비 안 됨 ({note}): {remedy}",
    "provider.restart": "재시작: {command}",
    "help.provider.use.embed": "임베딩 제공자: ollama | openai.",
    "help.provider.use.embed_model": "해당 임베딩 제공자의 임베딩 모델.",
    "help.provider.use.base_url": (
        "OpenAI 호환 엔드포인트의 기본 URL (AUTOONTOLOGY_OPENAI_BASE_URL)."
    ),
    "help.provider.use.openai_chat_model": (
        "OpenAI 호환 엔드포인트의 채팅 모델 (AUTOONTOLOGY_OPENAI_CHAT_MODEL)."
    ),
    "help.restart": (
        "서비스와 백엔드를 재시작해 둘 다 현재 .env를 읽게 합니다 (제공자를 바꾼 뒤). "
        "down + up과 같아서 진행 중인 빌드를 기다리지 않습니다."
    ),
    "help.restart.only": "이 프로세스만 재시작합니다: backend.",
    "provider.unknown_embed": "알 수 없는 임베딩 제공자 {name!r}: {choices} 중에서 고르세요",
    "provider.bad_url": "{flag} {value!r}은(는) http:// 또는 https:// URL이어야 합니다",
    "provider.key_hint": (
        "API 키는 명령줄로 받지 않습니다: 설정 페이지({url})에서 입력하거나 {path}에 "
        "AUTOONTOLOGY_OPENAI_API_KEY를 직접 입력하세요"
    ),
    "provider.restart_short": "또는: `uv run autoontology restart`",
    "restart.failed": "재시작 실패 (종료 코드 {code}): 위 출력을 보세요 ({log}에도 있음)",
    "restart.bad_only": "--only는 {choices} 중 하나여야 합니다 (받은 값 {value!r})",
}
