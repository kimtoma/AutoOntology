import type { Progress, RunKind, Source } from "@/lib/api"

export const KIND_KO: Record<string, string> = {
  files: "정형 파일", database: "데이터베이스", documents: "문서",
}
export const RUN_KO: Record<string, string> = {
  build: "빌드", extract: "문서 추출", align: "정렬", export: "내보내기", ingest: "인제스트",
}
export const STATUS_KO: Record<string, string> = {
  pending: "대기", running: "실행 중", succeeded: "성공", failed: "실패",
  cancelled: "취소됨", skipped: "건너뜀",
}
export const STEP_KO: Record<string, string> = {
  load: "적재", register_connection: "연결 등록", ingest: "인제스트", compile: "컴파일",
  export: "내보내기", read: "읽기", extract: "추출", consolidate: "병합", report: "보고서",
  terms: "Term 수집", candidates: "후보 검색", judge: "판정", apply: "적용", collect: "수집",
  serialize: "직렬화", upstream: "upstream 파일",
}
export const CKIND_KO: Record<string, string> = {
  entity: "엔터티", metric: "지표", attribute: "속성", process: "프로세스", rule: "규칙",
  other: "기타", term: "Term",
}
export const REL_KO: Record<string, string> = {
  same: "같음", broader: "상위", narrower: "하위", related: "관련", none: "없음", skipped: "건너뜀",
}
export const CHECK_KO: Record<string, string> = { ok: "정상", warn: "주의", error: "오류" }
export const CHECK_NAME_KO: Record<string, string> = {
  backend: "upstream 백엔드", node: "Node.js", kordoc: "kordoc 문서 엔진",
  profile: "모델 프로파일", ollama: "Ollama 별칭",
}
export const PROVIDER_KO: Record<string, string> = {
  ollama: "Ollama (로컬)", claude: "Claude (구독)", codex: "Codex (ChatGPT 구독)",
  nvidia: "NVIDIA API",
}
export const ORIGIN_KO: Record<string, string> = {
  shell: "셸 환경변수", "settings.json": "settings.json(UI)", ".env": ".env",
  AUTOONTOLOGY_MODEL_PROFILE: "모델 프로파일", default: "기본값",
}

export function actionsFor(s: Source): Exclude<RunKind, "ingest">[] {
  return s.kind === "documents" ? ["extract", "align", "export"] : ["build", "export"]
}

export function sourceState(s: Source): string {
  if (s.kind === "documents") {
    return s.concepts ? `문서 ${s.documents}개 · 개념 ${s.concepts}개` : "추출 전"
  }
  return s.built ? "빌드됨" : "빌드 전"
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "-"
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function progressText(progress: Progress | undefined, withFailed = true): string {
  return Object.entries(progress || {})
    .map(([label, counts]) => {
      const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0)
      const done = counts.succeeded || 0
      const failed = withFailed && counts.failed ? `, 실패 ${counts.failed}` : ""
      return `${label === "chunks" ? "청크" : "개념"} ${done}/${total}${failed}`
    })
    .join(" · ")
}
