/* The one client for the AutoOntology REST API (/api/*). Errors are
   `{error, detail}` with a matching HTTP status; they surface as ApiError. */

export const API = "/api"

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

export type SourceKind = "files" | "database" | "documents"
export type RunKind = "build" | "extract" | "align" | "export" | "ingest"
export type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled" | "skipped"

export interface RunRef {
  id: string
  kind: RunKind
  status?: RunStatus
  finished_at?: string | null
}

export interface Source {
  name: string
  kind: SourceKind
  locator: string
  database: string | null
  built: boolean
  uploaded: boolean
  created_at: string
  last_run: RunRef | null
  active_run: { id: string; kind: RunKind } | null
  documents?: number
  concepts?: number
}

export interface Step {
  name: string
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  detail: Record<string, unknown>
}

export type Progress = Record<string, Partial<Record<RunStatus, number>>>

export interface Run {
  id: string
  kind: RunKind
  source: string | null
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  error: string | null
  params: Record<string, unknown>
  managed: boolean
  steps: Step[]
  progress: Progress
}

export interface BuildOptions { resume?: boolean; skip_compile?: boolean; skip_export?: boolean }
export interface ExtractOptions { resume?: boolean; workers?: number | null; max_chunks?: number | null }
export interface AlignOptions {
  structured_source?: string | null
  dry_run?: boolean
  resume?: boolean
  threshold?: number | null
  min_conf?: number | null
}
export interface ExportOptions { sources?: string[] | null }
export type RunOptions = BuildOptions | ExtractOptions | AlignOptions | ExportOptions

export interface ConceptItem {
  id: string
  name: string
  name_en: string | null
  kind: string
  source: string | null
  definition: string
  synonyms: string[]
  evidence_count: number
  verified_count: number
  alignment: { relation: string; term: string | null; accepted: boolean; dry_run: boolean } | null
}

export interface ConceptPage {
  items: ConceptItem[]
  total: number
  page: number
  size: number
  pages: number
}

export interface Evidence {
  quote: string
  verified: boolean
  page: number | null
  section: string | null
  document: string | null
  path: string | null
}

export interface Alignment {
  relation: string
  term: string | null
  term_id: string | null
  structured_source: string | null
  confidence: number | null
  similarity: number | null
  accepted: boolean
  dry_run: boolean
  rationale: string | null
}

export interface ConceptRelation {
  subject: string
  predicate: string
  object: string
  label?: string | null
}

export interface ConceptDetail {
  id: string
  name: string
  name_en: string | null
  kind: string
  source: string | null
  definition: string
  synonyms: string[]
  attributes: string[]
  merged_from: unknown
  evidence_count: number
  verified_count: number
  evidence: Evidence[]
  relations: ConceptRelation[]
  alignments: Alignment[]
}

export interface ExportFile { name: string; bytes: number }
export interface ExportEntry {
  source: string
  run_id: string
  kind: RunKind | null
  status: RunStatus | null
  created_at: string
  files: ExportFile[]
}

export type CheckStatus = "ok" | "warn" | "error"
export interface StatusCheck { status: CheckStatus; detail?: string; remedy?: string }
export interface ServiceStatus {
  status: CheckStatus
  version: string
  model_profile: string
  chat_provider: string
  embed_provider: string
  chat_model: string
  embed_model: string
  service_url: string
  checks: Record<string, StatusCheck>
  doctor: string
  /** AUTOONTOLOGY_UPLOAD_MAX_MB: cap per upload request (all files together). */
  upload_max_mb?: number
}

export type ProviderName = "ollama" | "claude" | "codex" | "nvidia"
export interface ProviderRow {
  name: ProviderName
  role: string
  ok: boolean
  available: boolean
  logged_in: boolean
  detail: string
  remedy: string
  models: string[]
  model: string
  last_test: { at: string; ok: boolean; model?: string | null } | null
}
export interface Selection {
  chat_provider: string
  chat_model: string
  embed_provider: string
  upstream_chat_model?: string
  origin: Record<string, string>
}
export interface ProviderUsage {
  calls: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  seconds: number
}
export interface ProvidersOverview {
  selection: Selection
  settings_file: Record<string, string>
  choices: { chat_provider: string[]; embed_provider: string[] }
  providers: ProviderRow[]
  applied: { chat_provider: string; chat_model: string; embed_provider: string }
  restart_required: boolean
  usage?: Record<string, ProviderUsage>
}
export interface ProviderCall {
  ok: boolean
  seconds?: number
  answer?: string
  error?: string
  usage?: Record<string, number>
  cost_usd?: number | null
}
export interface ProviderTestResult {
  at: string
  results: Record<string, {
    ok: boolean | null
    note?: string
    model?: string
    via?: string
    router?: ProviderCall
    chatopenai?: ProviderCall
  }>
}
export interface ApplyResult {
  outcome: "applied" | "rolled_back" | "down"
  detail: string
  backend_pid: number | null
  backend_health: { status: string; detail: string }
  log: string[]
  applied: { chat_provider: string; chat_model: string; embed_provider: string }
  service_restart_required: boolean
  remedy: string
}

export type GraphLayer = "structured" | "documents" | "aligned" | "evidence"
export interface GraphNodeJson {
  id: string
  label: string
  /** class (Term or document class), concept (folded into a Term), individual, document */
  type: "class" | "concept" | "individual" | "document"
  source: string
  /** term | entity | metric | attribute | process | rule | other | document */
  kind: string
  origin: "structured" | "document"
  layer: GraphLayer
  synonyms?: string[]
  definition?: string
  attributes?: string[]
  tables?: string[]
  concept_ids?: string[]
  folded_into?: string
  enriched_by?: string
  individual_type?: string
  evidence_count?: number
  metric?: boolean
  path?: string
}
export interface GraphEdgeJson {
  id: string
  source: string
  target: string
  /** semantic_fk | document | subclass_of | related | aligned_same | evidence */
  type: string
  label: string
  title?: string
  layer: GraphLayer
  confidence?: number | null
  /** alignment links: same | broader | narrower | related */
  relation?: string
  /** evidence links: evidence rows behind the link */
  count?: number
}
export interface GraphJson {
  name?: string
  layer: string
  nodes: GraphNodeJson[]
  edges: GraphEdgeJson[]
  counts: { nodes: number; edges: number; total_nodes?: number }
  truncated?: boolean
  max_nodes?: number
  warnings: string[]
  structured_sources: string[]
  documents_sources: string[]
}

/* --- 플레이그라운드 ---------------------------------------------------------------------- */

export interface ElementChip {
  type: string
  id: string
  label: string
  /** client route of the UI, e.g. /concepts?concept=<id> */
  link: string
  graph_link: string
}
export interface RetrievedElement extends ElementChip {
  ref: string
  score: number | null
  via: "retrieval" | "neighbour"
}
export interface ChatTurn { role: "user" | "assistant"; content: string }
export interface Conversation {
  id: string
  source_id: string
  title: string
  created_at: string
  updated_at: string
  messages: number
}
export interface ConversationMessage {
  id: number
  role: "user" | "assistant"
  content: string
  retrieved: string[]
  citations: Record<string, ElementChip>
  provider: string
  model: string
  /** the provider stream ended without [DONE]: partial text, never graded */
  incomplete: boolean
  created_at: string
}
export interface SparqlExample { id: string; title: string; description: string; query: string }
export interface SparqlResult {
  type: "SELECT" | "ASK" | "CONSTRUCT" | "DESCRIBE"
  columns: string[]
  rows: unknown[][]
  total: number
  truncated: boolean
  boolean?: boolean
  turtle?: string
  export: string
  triples: number
}
export interface CompetencyTest {
  id: number
  question: string
  expected_elements: string[]
  expected_answer_hint: string
  created_at: string
}
export type Verdict = "pass" | "fail" | "unsure"
export interface TestRunRow {
  id: number
  test_id: number
  run_at: string
  answer: string
  citations: Record<string, ElementChip>
  verdict: Verdict
  score: number
  rationale: string
  provider: string
  model: string
}
export interface TestHistory extends CompetencyTest {
  runs: TestRunRow[]
  last: TestRunRow | null
  previous: TestRunRow | null
  regression: boolean
  improved: boolean
}
export interface Suggestion {
  question: string
  expected_elements: string[]
  dropped_elements: string[]
  expected_answer_hint: string
  expected: ElementChip[]
}
export interface IndexStatus {
  source: string
  scope: string[]
  warnings: string[]
  revision: number
  index: { revision: number; model: string; elements: number; built_at: string } | null
  stale: boolean
}

export interface SseEvent { event: string; data: unknown }

/** Parse a text/event-stream body chunk by chunk; returns the unconsumed tail. */
export function parseSse(buffer: string, emit: (ev: SseEvent) => void): string {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n")
  const tail = blocks.pop() ?? ""
  for (const block of blocks) {
    let event = "message"
    const data: string[] = []
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim()
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""))
    }
    if (!data.length) continue
    const raw = data.join("\n")
    let parsed: unknown = raw
    try { parsed = JSON.parse(raw) } catch { /* keep the text */ }
    emit({ event, data: parsed })
  }
  return tail
}

/** Events that end every playground SSE stream; EOF before one of them = truncated. */
export const TERMINAL_EVENTS = new Set(["done", "error"])

/** POST a JSON body to an SSE endpoint and call *onEvent* for every event. Errors before
    the stream starts (4xx/5xx JSON) throw ApiError like `request`. A stream that ends
    without a terminal event (`done`/`error`) emits a synthetic `stream_truncated` event and
    throws ApiError(code "stream_truncated"): it is never a successful answer. */
export async function streamSse(path: string, body: unknown, onEvent: (ev: SseEvent) => void,
                                signal?: AbortSignal): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(API + path, { ...json("POST", body), signal })
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err
    throw new ApiError("서비스에 연결할 수 없습니다 (uv run autoontology up)", 0, "network")
  }
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    let code: string | undefined
    try {
      const obj = await resp.json() as { detail?: string; error?: string }
      if (typeof obj.detail === "string") detail = obj.detail
      code = obj.error
    } catch { /* not JSON */ }
    throw new ApiError(detail, resp.status, code)
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ""
  let terminal = false
  const emit = (ev: SseEvent) => {
    if (TERMINAL_EVENTS.has(ev.event)) terminal = true
    onEvent(ev)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer = parseSse(buffer + value, emit)
  }
  parseSse(buffer + "\n\n", emit)
  if (!terminal) {
    const detail = "응답 스트림이 완료 표시 없이 끊겼습니다 (부분 답변은 미완료)"
    onEvent({ event: "stream_truncated", data: { detail } })
    throw new ApiError(detail, 502, "stream_truncated")
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(API + path, init)
  } catch {
    throw new ApiError("서비스에 연결할 수 없습니다 (uv run autoontology up)", 0, "network")
  }
  const type = resp.headers.get("content-type") || ""
  const body: unknown = type.includes("json") ? await resp.json() : await resp.text()
  if (!resp.ok) {
    const obj = (body && typeof body === "object" ? body : {}) as { detail?: unknown; error?: string }
    const detail = typeof obj.detail === "string" ? obj.detail : `HTTP ${resp.status}`
    throw new ApiError(detail, resp.status, obj.error)
  }
  return body as T
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
})

const enc = encodeURIComponent

export const api = {
  status: () => request<ServiceStatus>("/status"),
  sources: () => request<{ sources: Source[] }>("/sources").then((r) => r.sources),
  addSourceUpload: (name: string, kind: "files" | "documents", files: File[]) => {
    const fd = new FormData()
    fd.append("name", name)
    fd.append("kind", kind)
    for (const f of files) fd.append("files", f, f.name)
    return request<Source>("/sources", { method: "POST", body: fd })
  },
  addSourceLocator: (body: { name: string; kind: SourceKind; locator: string; recursive: boolean }) =>
    request<Source>("/sources", json("POST", body)),
  deleteSource: (name: string, dropDb: boolean) =>
    request<{ removed: string; messages: string[] }>(
      `/sources/${enc(name)}${dropDb ? "?drop_db=true" : ""}`, { method: "DELETE" }),
  runs: (limit = 40) => request<{ runs: Run[] }>(`/runs?limit=${limit}`).then((r) => r.runs),
  run: (id: string) => request<Run>(`/runs/${enc(id)}`),
  startRun: (kind: Exclude<RunKind, "ingest">, source: string, options: RunOptions = {}) =>
    request<{ run_id: string }>("/runs", json("POST", { kind, source, options })),
  cancelRun: (id: string) =>
    request<{ run_id: string; cancel_requested: boolean }>(`/runs/${enc(id)}/cancel`, { method: "POST" }),
  runEventsUrl: (id: string) => `${API}/runs/${enc(id)}/events`,
  concepts: (params: { q?: string; kind?: string; source?: string; page?: number; size?: number }) => {
    const sp = new URLSearchParams({ page: String(params.page ?? 1), size: String(params.size ?? 30) })
    if (params.q) sp.set("q", params.q)
    if (params.kind) sp.set("kind", params.kind)
    if (params.source) sp.set("source", params.source)
    return request<ConceptPage>(`/concepts?${sp}`)
  },
  concept: (id: string) => request<ConceptDetail>(`/concepts/${enc(id)}`),
  /** The whole merged graph with the 근거 layer; the page filters layers client-side. */
  graph: () => request<GraphJson>("/graph?layer=all&evidence=true"),
  exports: () => request<{ exports: ExportEntry[] }>("/exports").then((r) => r.exports),
  exportFileUrl: (runId: string, file: string) => `${API}/exports/${enc(runId)}/${enc(file)}`,
  exportText: (runId: string, file: string) =>
    request<string>(`/exports/${enc(runId)}/${enc(file)}?inline=true`),
  providers: () => request<ProvidersOverview>("/providers"),
  saveProviders: (body: { chat_provider?: string; chat_model?: string; embed_provider?: string }) =>
    request<ProvidersOverview>("/providers", json("PUT", body)),
  testProvider: (provider: string) =>
    request<ProviderTestResult>("/providers/test", json("POST", { provider })),
  applyProviders: (force = false) =>
    request<ApplyResult>("/providers/apply", json("POST", { force })),
  playground: {
    index: (source: string) => request<IndexStatus>(`/playground/index?source=${enc(source)}`),
    conversations: (source: string) =>
      request<{ conversations: Conversation[] }>(`/playground/conversations?source=${enc(source)}`)
        .then((r) => r.conversations),
    conversation: (id: string) =>
      request<Conversation & { messages: ConversationMessage[] }>(`/playground/conversations/${enc(id)}`),
    chat: (body: { source: string; question: string; history?: ChatTurn[]; top_k?: number;
                   conversation_id?: string | null },
           onEvent: (ev: SseEvent) => void, signal?: AbortSignal) =>
      streamSse("/playground/chat", body, onEvent, signal),
    sql: (body: { source?: string | null; question: string; conversation_id?: string },
          onEvent: (ev: SseEvent) => void, signal?: AbortSignal) =>
      streamSse("/playground/sql", body, onEvent, signal),
    sparqlExamples: () =>
      request<{ examples: SparqlExample[] }>("/playground/sparql/examples").then((r) => r.examples),
    sparql: (body: { source: string; query: string; limit?: number }) =>
      request<SparqlResult>("/playground/sparql", json("POST", body)),
    tests: (source: string) =>
      request<{ tests: CompetencyTest[] }>(`/playground/tests?source=${enc(source)}`).then((r) => r.tests),
    addTest: (body: { source: string; question: string; expected_elements: string[];
                      expected_answer_hint: string }) =>
      request<CompetencyTest>("/playground/tests", json("POST", body)),
    deleteTest: (id: number) =>
      request<{ deleted: number }>(`/playground/tests/${id}`, { method: "DELETE" }),
    history: (source: string) =>
      request<{ tests: TestHistory[] }>(`/playground/tests/history?source=${enc(source)}`)
        .then((r) => r.tests),
    runTests: (body: { source: string; test_ids?: number[] | null },
               onEvent: (ev: SseEvent) => void, signal?: AbortSignal) =>
      streamSse("/playground/tests/run", body, onEvent, signal),
    suggest: (source: string) =>
      request<{ suggestions: Suggestion[]; provider: string; model: string }>(
        "/playground/tests/suggest", json("POST", { source })),
  },
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}
