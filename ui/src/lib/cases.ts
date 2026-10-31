import type { CaseContextPayload } from "@/lib/ax"
import { request } from "@/lib/api"
import type { KnowledgeChange } from "@/lib/operations"

export interface CaseProposal { id: string; decision: "accept" | "reject" | "hold"; note: string; edits?: Record<string, unknown> }
export interface CaseLink { relation: "depends_on" | "duplicate_of" | "supersedes"; case_id: string }
export interface ReviewPacket { id: string; sha256: string; payload: { allowed_effect?: string; case_context?: {id:string;sha256:string;payload:CaseContextPayload}|null; case_id: string; case_version: number; executor_id: string; expires_at: number; context: { base_revision: number | null; candidate_revision: number | null }; decisions: CaseProposal[]; dependencies: { change_id: string; requires: string; label: string }[]; items: KnowledgeChange[] } }
export interface BusinessCase {case_context?: {id:string;sha256:string;payload:CaseContextPayload}|null; id: string; title: string; reason: string; owner_id: string | null; priority: string; due_at: string | null; status: string; version: number; base_revision: number | null; candidate_revision: number | null; current_publication?: number | null; created_at: string; updated_at: string; changes: CaseProposal[]; links: CaseLink[]; stale?: boolean; packet_stale?: boolean; packet_expired?: boolean; packet?: ReviewPacket | null; receipts?: { id: string }[]; outcome: { publication_revision?: number; reason?: string; usable_at?: string }; history?: { sequence: number; operation: string; created_at: string; actor: { id: string; kind: string }; data: { note: string } }[] }
export interface CaseFields { title: string; reason: string; owner_id: string | null; priority: string; due_at: string | null; changes: CaseProposal[]; links: CaseLink[]; request_key?: string }
export interface CaseMember { id: string; name: string; can_review: boolean }
export interface CaseSummary { active: number; awaiting_review: number; overdue: number; held: number; unassigned: number }
const send = (method: string, value: unknown): RequestInit => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(value) })
const path = (id: string) => `/cases/${encodeURIComponent(id)}`
export const casesApi = {
  list: () => request<{ items: BusinessCase[] }>("/cases"),
  summary: () => request<CaseSummary>("/cases/summary"),
  members: () => request<{ items: CaseMember[] }>("/cases/members"),
  get: (id: string) => request<BusinessCase>(path(id)),
  create: (value: CaseFields) => request<BusinessCase>("/cases", send("POST", value)),
  update: (id: string, version: number, patch: Partial<CaseFields>, status?: string) => request<BusinessCase>(path(id), send("PATCH", { version, patch, ...(status ? { status } : {}) })),
  prepare: (id: string, version: number, executor_id: string) => request<ReviewPacket>(path(id) + "/prepare", send("POST", { version, executor_id })),
  confirm: (packet: ReviewPacket) => request(path(packet.id).replace("/cases/", "/cases/packets/") + "/confirm", send("POST", { sha256: packet.sha256, confirm: true })),
  rebase: (id: string, version: number, reason: string, changes: CaseProposal[]) => request<BusinessCase>(path(id) + "/rebase", send("POST", { version, reason, changes })),
  resolve: (id: string, version: number, reason: string, publication: number | null) => request<BusinessCase>(path(id) + "/resolve", send("POST", { version, reason, publication })),
  reopen: (id: string, version: number, reason: string) => request<BusinessCase>(path(id) + "/reopen", send("POST", { version, reason })),
}
