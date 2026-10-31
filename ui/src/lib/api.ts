/* The one client for the AutoOntology REST API (/api/*). Errors are
   `{error, detail}` with a matching HTTP status; they surface as ApiError. The payloads
   mirror autoontology/web/api.py, web/concepts.py, web/graph.py and web/playground_api.py. */

import type { Formats } from "@/lib/formats"
import { identityResponse } from "@/lib/auth-events"

export const API = "/api"

export class ApiError extends Error {
  status: number
  code?: string
  /** the whole JSON error body: extra fields such as `conflicts` (409 sample_conflict) */
  body?: Record<string, unknown>
  constructor(message: string, status: number, code?: string, body?: Record<string, unknown>) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.body = body
  }
}

export const NETWORK_HINT = "Cannot reach the AutoOntology service (uv run autoontology up)"

/* --- sources ------------------------------------------------------------------------------ */

/** files / database = structured sources; documents = unstructured. */
export type SourceKind = "files" | "database" | "documents"
export type RunKind = "build" | "export"
export type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled" | "skipped"

export interface RunRef {
  id: string
  kind: string
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
  active_run: { id: string; kind: string } | null
  documents?: number
  concepts?: number
}

/* --- runs --------------------------------------------------------------------------------- */

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
  kind: RunKind | string
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

export type ExportFormat = "all" | "rdf" | "ossie" | "okf"
/** `POST /api/runs` (RunIn in api.py). */
export type RunRequest =
  | { kind: "apply"; force?: boolean }
  | { kind: "build"; sources?: string[] | null; resume?: boolean }
  | { kind: "export"; revision?: number | null; format?: ExportFormat }

/* --- ontology snapshot -------------------------------------------------------------------- */

export type Origin = "structured" | "document" | "aligned"

export interface SnapshotSource {
  name: string
  kind: "structured" | "documents"
  source_kind: SourceKind
  included: boolean
  counts: Record<string, number>
  database?: string
  documents?: number
}

export interface OntologySummary {
  access?: { restricted: boolean; policy_version: number | null; historical_scope_missing?: boolean }
  revision: number
  created_at: string
  run_id: string
  workspace: string
  base_iri: string
  sources: SnapshotSource[]
  counts: Record<string, number | Record<string, unknown>>
  alignment: Record<string, number>
}

export interface AlignmentLink {
  relation: "same" | "broader" | "narrower" | "related" | string
  with: string
  label: string
  confidence: number | null
  accepted: boolean
}

export interface ConceptItem {
  id: string
  name: string
  /** term | entity | metric | attribute | process | rule | other */
  kind: string
  origin: Origin
  source: string
  definition: string
  synonyms: string[]
  /** instance-like names kept off the synonyms (record IDs, numbers/dates, company names) */
  rejected_aliases?: { alias: string; reason: string; concept?: string }[]
  merged_concepts: string[]
  folded_into: { id: string; name: string } | null
  enriched_by: string
  evidence_count: number
  alignment: { counts: Record<string, number>; best: AlignmentLink | null }
}

export interface ConceptPage {
  revision: number
  items: ConceptItem[]
  total: number
  page: number
  size: number
  pages: number
}

export interface OriginalReference {
  status: "archived" | "not_archived" | "missing" | "corrupt"
  url: string | null
  recover_url?: string | null
}

export interface Evidence {
  document_id?: string | null
  original?: OriginalReference
  quote: string
  verified: boolean
  page: number | null
  document: string
  path: string
}

export interface ConceptAttribute {
  id: string
  label: string
  range: string
  source: string
  column: string
  datatype: string
  description: string
}

export interface ConceptRelation {
  id: string
  direction: "out" | "in"
  predicate: string
  label: string
  other: string
  other_label: string
  source: string
}

export interface ConceptDetail extends ConceptItem {
  revision: number
  description: string
  definitions: string[]
  tables: string[]
  individual_type: string | null
  attributes: ConceptAttribute[]
  relations: ConceptRelation[]
  alignments: AlignmentLink[]
  evidence: Evidence[]
}

/* --- graph -------------------------------------------------------------------------------- */

export type GraphLayer = "structured" | "documents" | "aligned" | "evidence"
export interface GraphNodeJson {
  id: string
  label: string
  /** class (Term or document class), concept (folded into a Term), individual, document */
  type: "class" | "concept" | "individual" | "document"
  source: string
  /** term | entity | metric | attribute | process | rule | other | document */
  kind: string
  origin: Origin
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
  access?: { restricted: boolean; policy_version: number | null; historical_scope_missing?: boolean }
  name?: string
  layer: string
  revision?: number
  nodes: GraphNodeJson[]
  edges: GraphEdgeJson[]
  counts: { nodes: number; edges: number; total_nodes?: number; origins?: Record<string, number> }
  truncated?: boolean
  max_nodes?: number
  warnings: string[]
  structured_sources: string[]
  documents_sources: string[]
}

/* --- exports ------------------------------------------------------------------------------ */

export interface ExportFile { name: string; bytes: number }
export interface ExportDir { name: string; files: string[]; bytes: number }
export interface ExportEntry {
  revision: number
  /** r0002 */
  dir: string
  created_at: string
  files: ExportFile[]
  /** the sub-folders (upstream/, ossie/, okf/) with their files, relative to the folder */
  dirs: ExportDir[]
}

/* --- status ------------------------------------------------------------------------------- */

export type CheckStatus = "ok" | "warn" | "error"
export interface StatusCheck { status: CheckStatus; detail?: string; remedy?: string }

/** `POST /api/providers/test` (web/providers/probe.py): one structured "pong" call. */
export interface ProviderTest {
  at?: string
  provider?: string
  model?: string
  via?: string
  ok: boolean
  seconds?: number
  status?: number
  answer?: string
  error?: string
  remedy?: string
}

/** `/api/status.providers.chat`: the active chat provider (CLI login, endpoint). */
export interface ChatProviderStatus {
  name: string
  model: string
  available: boolean
  logged_in: boolean
  detail: string
  remedy: string
  last_test: ProviderTest | null
}

export interface ServiceStatus {
  status: CheckStatus
  version: string
  providers: {
    chat_provider: string
    chat_model: string
    embed_provider: string
    embed_model: string
    openai_base_url: string
    chat?: ChatProviderStatus
    embed?: { name: string; model: string }
  }
  kordoc_available: boolean
  /** kordoc's OCR models are downloaded (images and scanned PDFs can be read) */
  ocr_ready?: boolean
  /** AUTOONTOLOGY_VISION_MODEL (image captions), null when unset */
  vision_model?: string | null
  /** every readable extension, by source family (autoontology/formats.py::describe) */
  formats?: Formats
  service_url: string
  checks: Record<string, StatusCheck>
  doctor: string
  /** AUTOONTOLOGY_UPLOAD_MAX_MB: cap per upload request (all files together). */
  upload_max_mb?: number
}

/* --- settings (GET/PUT /api/settings, web/settings_api.py) --------------------------------- */

export type ChatProviderName = "ollama" | "openai" | "claude" | "codex"
export type EmbedProviderName = "ollama" | "openai"
export type SettingOrigin = "shell" | ".env" | "default"

export interface CliProviderInfo {
  found: boolean
  logged_in: boolean
  detail: string
  remedy: string
  model: string
  default_model: string
  known_models: string[]
  login_command: string
}

/** `GET /api/settings`: what `.env` (and the shell) select now. Never a key: `api_key_set`. */
export interface SettingsState {
  chat: { provider: ChatProviderName; model: string; origin: SettingOrigin }
  embed: { provider: EmbedProviderName; model: string; origin: SettingOrigin }
  running: { chat_provider: string; chat_model: string; embed_provider: string; embed_model: string }
  openai: { base_url: string; api_key_set: boolean; chat_model: string; embed_model: string }
  cli: { claude: CliProviderInfo; codex: CliProviderInfo }
  ollama: { endpoint: string; reachable: boolean; models: string[]; chat_model: string; embed_model: string }
  origins: Record<string, SettingOrigin>
  /** `.env` keys the service's shell environment sets (they win over `.env`: not editable here) */
  locked_keys: string[]
  vectors: { rows: number | null; dimension: number | null; error?: string }
  restart_required: boolean
  restart_in_progress: boolean
  service_pid: number
  /** started by `autoontology up` (only then can it restart itself) */
  supervised: boolean
  restart_command: string
}

/** The body of `POST /api/settings/test` and `PUT /api/settings`: only what changes. */
export interface SettingsChange {
  chat_provider?: ChatProviderName
  chat_model?: string
  embed_provider?: EmbedProviderName
  embed_model?: string
  openai_base_url?: string
  /** a new key; never sent back by the server */
  api_key?: string
  use_saved_key?: boolean
  clear_api_key?: boolean
  confirm_reingest?: boolean
}

export interface SettingsTest {
  chat: { ok: boolean; provider: string; model: string; seconds?: number; answer?: string | null;
          error?: string | null; remedy?: string | null }
  embed: { ok: boolean; provider: string; model: string; seconds?: number; dimension?: number;
           error?: string; mismatch_with_store: boolean; stored_dimension: number | null }
}

export interface SettingsSaved { written: string[]; backup: string | null; restart_required: boolean }

export interface RestartState {
  id: string
  state: "running" | "done" | "failed"
  started_at?: string
  finished_at?: string | null
  error?: string | null
  previous_pid?: number | null
  new_pid?: number | null
  log_tail?: string
  service_pid?: number
}

/* --- sample sets (GET /api/samples, POST /api/samples/load) -------------------------------- */

export interface SampleSource {
  name: string
  /** structured | documents */
  kind: string
  path: string
  files: number
  bytes: number
  formats?: string[]
}
export interface SampleSet {
  set: string
  path: string
  sources: SampleSource[]
  registered: boolean
  /** expected build time per chat provider (`demo` prints the same table) */
  durations?: Record<string, { text: string; measured: boolean }>
}
/** One entry of a 409 `sample_conflict`: a source with a sample source's name and other data. */
export interface SampleConflict { name: string; kind?: string; registered?: string; sample?: string }

export interface SampleLoad {
  set: string
  sources: (Source & { action: "created" | "kept" | "replaced" | string })[]
  messages: string[]
}

/* --- playground --------------------------------------------------------------------------- */

export interface ElementChip {
  type: string
  id: string
  label: string
  /** client route of the UI, e.g. /concepts?concept=<store id> (legacy) or /graph?focus=… */
  link: string
  graph_link: string
}
export interface RetrievedElement extends ElementChip {
  ref: string
  score: number | null
  via: "retrieval" | "neighbour"
}
export interface ChatTurn { role: "user" | "assistant"; content: string }
export interface SparqlExample { id: string; title: string; description: string; query: string }
export interface SparqlResult {
  trace_id?: string
  type: "SELECT" | "ASK" | "CONSTRUCT" | "DESCRIBE"
  columns: string[]
  rows: unknown[][]
  total: number
  truncated: boolean
  boolean?: boolean
  turtle?: string
  triples: number
  revision: number
  /** the Turtle file queried (the export's ontology.ttl or a cache file) */
  ttl: string
}
export interface IndexStatus {
  workspace: string
  scope: string[]
  warnings: string[]
  revision: number
  index: { revision: number; model: string; elements: number; built_at: string } | null
  stale: boolean
}
export interface UsageSummary {
  id: string
  kind: string
  status: "running" | "succeeded" | "failed" | "cancelled" | "interrupted"
  revision: number
  started_at: string
  owner?: { principal_id: string | null; name: string | null; legacy: boolean }
}
export interface UsageTrace extends UsageSummary {
  data: {
    request: { question?: string; query?: string }
    binding: { legacy: boolean; publication_id?: string | null }
    duration_ms: number | null
    context: { hits?: { ref: string; text: string }[]; data_time?: string }
    outcome: { answer?: string; error?: string; result?: unknown; events?: unknown }
  }
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
    throw new ApiError(NETWORK_HINT, 0, "network")
  }
  if (!resp.ok || !resp.body) {
    identityResponse(resp.status)
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
    if (ev.event === "error" && typeof ev.data === "object" && ev.data !== null && "status" in ev.data && typeof ev.data.status === "number") identityResponse(ev.data.status)
    onEvent(ev)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer = parseSse(buffer + value, emit)
  }
  parseSse(buffer + "\n\n", emit)
  if (!terminal) {
    const detail = "The answer stream ended without a completion marker (partial answer, incomplete)"
    onEvent({ event: "stream_truncated", data: { detail } })
    throw new ApiError(detail, 502, "stream_truncated")
  }
}

async function send(path: string, init: RequestInit, asText: boolean): Promise<unknown> {
  let resp: Response
  try {
    resp = await fetch(API + path, init)
  } catch {
    throw new ApiError(NETWORK_HINT, 0, "network")
  }
  const type = resp.headers.get("content-type") || ""
  const body: unknown = type.includes("json") && !(asText && resp.ok) ? await resp.json() : await resp.text()
  if (!resp.ok) {
    identityResponse(resp.status)
    const obj = (body && typeof body === "object" ? body : {}) as { detail?: unknown; error?: string }
    const detail = typeof obj.detail === "string" ? obj.detail : `HTTP ${resp.status}`
    throw new ApiError(detail, resp.status, obj.error, obj as Record<string, unknown>)
  }
  return body
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await send(path, init, false)) as T
}

/** Like `request`, but a successful body is always the raw text, whatever its content type
    (an export file is text even if the server ever labels a .json file as JSON). */
export async function requestText(path: string, init: RequestInit = {}): Promise<string> {
  return (await send(path, init, true)) as string
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
})

const enc = encodeURIComponent
/** An export file path keeps its "/" separators; every segment is encoded (Hangul names). */
const encPath = (p: string) => p.split("/").map(enc).join("/")

export interface ConceptQuery {
  q?: string
  kind?: string
  origin?: string
  source?: string
  page?: number
  size?: number
  revision?: number | null
}

export const api = {
  status: () => request<ServiceStatus>("/status"),
  testProvider: () => request<ProviderTest>("/providers/test", { method: "POST" }),
  settings: () => request<SettingsState>("/settings"),
  testSettings: (body: SettingsChange) => request<SettingsTest>("/settings/test", json("POST", body)),
  saveSettings: (body: SettingsChange) => request<SettingsSaved>("/settings", json("PUT", body)),
  restart: () => request<{ restart_id: string; previous_pid: number }>("/settings/restart", { method: "POST" }),
  restartState: (id: string) => request<RestartState>(`/settings/restart/${enc(id)}`),
  samples: () => request<SampleSet[]>("/samples"),
  /** *replace*: consent to replacing same-named sources that point elsewhere (409 sample_conflict). */
  loadSamples: (set: string, replace = false) =>
    request<SampleLoad>("/samples/load", json("POST", replace ? { set, replace: true } : { set })),
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
  startRun: (body: RunRequest) => request<{ run_id: string }>("/runs", json("POST", body)),
  cancelRun: (id: string) =>
    request<{ run_id: string; cancel_requested: boolean }>(`/runs/${enc(id)}/cancel`, { method: "POST" }),
  runEventsUrl: (id: string) => `${API}/runs/${enc(id)}/events`,
  ontology: () => request<OntologySummary>("/ontology"),
  concepts: (params: ConceptQuery) => {
    const sp = new URLSearchParams({ page: String(params.page ?? 1), size: String(params.size ?? 30) })
    for (const k of ["q", "kind", "origin", "source"] as const) {
      if (params[k]) sp.set(k, params[k]!)
    }
    if (params.revision) sp.set("revision", String(params.revision))
    return request<ConceptPage>(`/ontology/concepts?${sp}`)
  },
  concept: (id: string, revision?: number | null) =>
    request<ConceptDetail>(`/ontology/concepts/${enc(id)}${revision ? `?revision=${revision}` : ""}`),
  /** The whole unified graph with the evidence layer; the page filters layers client-side. */
  graph: () => request<GraphJson>("/ontology/graph?layer=all&evidence=true"),
  exports: () => request<{ exports: ExportEntry[] }>("/exports").then((r) => r.exports),
  exportFileUrl: (dir: string, file: string) => `${API}/exports/${enc(dir)}/${encPath(file)}`,
  viewExports: () => request<{ revision: number; counts: Record<string, number>; access: { restricted: boolean }; formats: string[] }>("/ontology/export"),
  viewExportUrl: (format: string, revision?: number) => `${API}/ontology/export/${enc(format)}${revision ? `?revision=${revision}` : ""}`,
  exportText: (dir: string, file: string) =>
    requestText(`/exports/${enc(dir)}/${encPath(file)}?inline=true`),
  playground: {
    traces: (allUsers = false) => request<{ items: UsageSummary[] }>("/playground/traces" + (allUsers ? "?all_users=true" : "")),
    trace: (id: string) => request<UsageTrace>(`/playground/traces/${enc(id)}`),
    forgetTrace: (id: string) => request(`/playground/traces/${enc(id)}`, json("DELETE", {})),
    index: () => request<IndexStatus>("/playground/index"),
    chat: (body: { question: string; history?: ChatTurn[]; top_k?: number; conversation_id?: string | null },
           onEvent: (ev: SseEvent) => void, signal?: AbortSignal) =>
      streamSse("/playground/chat", body, onEvent, signal),
    sql: (body: { source?: string | null; question: string; conversation_id?: string },
          onEvent: (ev: SseEvent) => void, signal?: AbortSignal) =>
      streamSse("/playground/sql", body, onEvent, signal),
    sparqlExamples: () =>
      request<{ examples: SparqlExample[] }>("/playground/sparql/examples").then((r) => r.examples),
    sparql: (body: { query: string; limit?: number }) =>
      request<SparqlResult>("/playground/sparql", json("POST", body)),
  },
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** 404 `no_snapshot`: nothing built yet (the pages show "Build first" instead of an error). */
export function isNoSnapshot(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.code === "no_snapshot"
}

const CONCEPT_ID = /^(term|concept)-[a-z0-9_-]{1,100}$/

/** The `/api/ontology/concepts/{id}` id behind a playground citation chip, or null when the
    element is not a concept (a relation without an owner, a literal). Chip ids of Terms,
    document classes and individuals are the model locals already; relations and evidence
    point at their concept through `link` (`/concepts?concept=<store id>`) or `graph_link`. */
export function conceptIdOfChip(chip: ElementChip): string | null {
  if (CONCEPT_ID.test(chip.id)) return chip.id
  const query = (link: string) => new URLSearchParams(link.split("?")[1] ?? "")
  const legacy = query(chip.link).get("concept")
  if (legacy) {
    const id = CONCEPT_ID.test(legacy) ? legacy : `concept-${legacy}`
    if (CONCEPT_ID.test(id)) return id
  }
  const focus = query(chip.graph_link).get("focus")
  return focus && CONCEPT_ID.test(focus) ? focus : null
}
