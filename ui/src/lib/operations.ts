import type { ModelChecks } from "@/components/model-check-results"
/** Operational APIs keep the published ontology separate from build candidates. */
import { ApiError, type OriginalReference } from "@/lib/api"

export interface Evidence {
  document_id?: string | null
  original?: OriginalReference
  quote: string
  page: number | null
  verified: boolean
  document: string
  version?: string
}
export interface ChangeDecision {
  decision: "accept" | "reject" | "hold"
  note: string
  actor: string
  created_at: string
  applied_revision: number | null
  edits?: Record<string, unknown>
}
export interface KnowledgeChange {
  id: string
  collection: string
  key: string
  label: string
  kind: "added" | "changed" | "removed"
  before_text?: string
  after_text?: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  evidence: Evidence[]
  decision: ChangeDecision | null
  revision: number
}
export interface Feedback {
  id: number
  question: string
  answer: string
  note: string
  actor: string
  created_at: string
}
export interface QualityCheck {
  suite_id?: string
  id: number | string
  question: string
  expected: string
}
export interface Overview {
  initial_approval_required?: boolean
  publication_policy?: { minimum_questions: number }
  published_revision: number | null
  candidate_revision: number | null
  workspace: string
  counts: { pending: number; held: number; approved: number; rejected: number; feedback: number }
  highlights: KnowledgeChange[]
  published_at: string | null
  checks: QualityCheck[]
}
export interface Changes {
  published_revision: number | null
  candidate_revision: number | null
  items: KnowledgeChange[]
  feedback: Feedback[]
}
export interface FreshSource {
  name: string
  kind: string
  state: "fresh" | "stale" | "never_built" | "unknown"
  stale: boolean
  last_built_at: string | null
  reasons: { kind: string; count: number; examples: string[] }[]
  error?: string
}
export interface Freshness {
  sources: FreshSource[]
  stale: number
  age_s: number
  cache_s: number
}
export interface LegacyReview {
  id: string
  category: string
  concept?: { name: string; definition: string; source: string }
  term?: { id: string; name: string }
  relation?: string
  candidates?: { id: string; name: string }[]
  evidence: Evidence[]
  rationale?: string
  decision: Record<string, unknown> | null
  suggested_action: string
  hold?: { note: string; actor: string } | null
}
export interface LegacyInbox {
  items: LegacyReview[]
  counts: Record<string, number>
  unapplied: number
  pages: number
  page: number
  total: number
}
export interface Revision {
  run_status?: string | null
  operational?: boolean
  revision: number
  created_at: string
  run_kind: string | null
  counts: Record<string, number>
  sources: { name: string }[]
}
export interface Validation {
  model_checks?: ModelChecks;
  ok: boolean
  token: string
  revision: number
  published_revision: number | null
  approved: number
  excluded: number
  indexed_elements: number
  checks_configured: boolean
  blocking_reasons?: string[]
  questions: (QualityCheck & { answer: string; ok: boolean; citations: Record<string, unknown> })[]
  validated_at: string
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok)
    throw new ApiError(
      String(data.detail || data.error || response.statusText),
      response.status,
      data.error,
      data,
    )
  return data
}
const enc = encodeURIComponent
export interface Publication {
  id?: string
  revision: number
  status?: string
  legacy?: boolean
  verification?: { ok: boolean }
  manifest: null | {
    candidate_revision: number
    previous_revision: number | null
    originals: { sha256: string }[]
    artifacts: Record<string, string>
    index: { model: string; elements: number }
    source_bindings?: { sources: { kind?: string; included?: boolean; processing?: { status: string } }[] }[]
    processing_receipts?: { id: string; record: { stage: string; status: string; configuration?: { model?: string; mode?: string } } }[]
  }
}
export interface Restoration {
  id: string
  status: "preparing" | "ready" | "blocked" | "failed" | "committed"
  confirmation: string
  sha256: string
  result_revision: number | null
  packet: {
    target_revision: number
    base_revision: number
    reason: string
    expires_at?: string
    diff?: Record<string, unknown>
    changes?: {collection:string;key:string;label:string;kind:string;before_text:string;after_text:string;fields:{name:string;before:unknown;after:unknown}[]}[]
    originals?: {document_id:string;title:string;sha256:string;revision:number;versions:string[];original:OriginalReference}[]
    validation?: { ok: boolean; checks_configured: boolean; indexed_elements: number; questions: { id:number; question:string; answer:string; ok:boolean }[] }
  }
}
export function operationalRevision(revision: Revision, published: number | null | undefined): boolean {
  if (revision.revision === published) return true
  if (revision.operational !== undefined) return revision.operational
  return ["publish", "restore"].includes(revision.run_kind ?? "") && revision.run_status === "succeeded"
}

export const operations = {
  prepareRestore: (revision:number, reason:string) => request<Restoration>("/ops/restores", "POST", {revision,reason}),
  restorations: () => request<{items:Restoration[]}>("/ops/restores"),
  restore: (proposal:string, confirmation:string) => request<{run_id:string}>(`/ops/restores/${enc(proposal)}/execute`, "POST", {confirmation}),
  publication: (revision: number, verify = false) => request<Publication>(`/ops/publications/${revision}?verify=${verify}`),
  overview: () => request<Overview>("/ops"),
  changes: () => request<Changes>("/ops/changes"),
  decide: (
    item: KnowledgeChange,
    decision: "accept" | "reject" | "hold",
    note: string,
    edits: Record<string, unknown> = {},
  ) =>
    request<ChangeDecision>(`/ops/changes/${enc(item.id)}/decision`, "POST", {
      candidate: item.revision,
      decision,
      note,
      edits,
    }),
  undo: (id: string) => request(`/ops/changes/${enc(id)}/decision`, "DELETE"),
  freshness: (refresh = false) => request<Freshness>(`/sources/freshness${refresh ? "?refresh=true" : ""}`),
  legacy: (page = 1, status = "pending") =>
    request<LegacyInbox>(`/ops/connections?page=${page}&status=${status}`),
  legacyDecision: (id: string, decision: string, note: string, relation?: string, term_id?: string) =>
    request(`/review/${enc(id)}/decision`, "POST", {
      decision,
      note,
      ...(relation ? { relation } : {}),
      ...(term_id ? { term_id } : {}),
    }),
  holdConnection: (id: string, note: string) => request(`/ops/connections/${enc(id)}/hold`, "POST", { note }),
  unholdConnection: (id: string) => request(`/ops/connections/${enc(id)}/hold`, "DELETE"),
  undoLegacy: (id: string) => request(`/review/${enc(id)}/decision`, "DELETE"),
  revisions: () => request<{ revisions: Revision[] }>("/ontology/revisions"),
  diff: (from: number, to: number) =>
    request<Record<string, unknown>>(`/ontology/diff?from=${from}&to=${to}`),
  validate: (revision: number) => request<Validation>("/ops/validate", "POST", { revision }),
  publish: (revision: number, token: string) =>
    request<{ run_id: string }>("/ops/publish", "POST", { revision, token }),
  feedback: (question: string, answer: string, note: string) =>
    request<Feedback>("/ops/feedback", "POST", { question, answer, note }),
  resolve: (id: number) => request(`/ops/feedback/${id}/resolve`, "POST"),
  addCheck: (question: string, expected: string) =>
    request<QualityCheck>("/ops/checks", "POST", { question, expected }),
  deleteCheck: (id: number) => request(`/ops/checks/${id}`, "DELETE"),
}

export function knowledgeText(value: Record<string, unknown> | null): string {
  if (!value) return "—"
  const values = [
    value.definitions,
    value.comment,
    value.label,
    value.concept_name,
    value.rationale,
    value.kind,
  ]
  for (const v of values) {
    if (Array.isArray(v) && v.length) return v.join(" / ")
    if (typeof v === "string" && v.trim()) return v
  }
  return JSON.stringify(value)
}
