import { request } from "@/lib/api"

export type ComparisonCell = string | number | null | { type: "integer"; value: string }
export interface ComparisonResult { columns: string[]; rows: ComparisonCell[][] }
export interface ComparisonArm {
  id: string; label: string; origin: string; verdict: string; error?: string
  result?: ComparisonResult
  answer: { state: string; sql?: string; reason?: string; citations: string[] }
}
export interface ComparisonReport {
  schema: string; pack_id: string; title: string; sha256: string; pack_sha256: string; attempts_sha256: string
  data_class: string; human_approval: false; baseline_measured: false; model_calls: number
  comparison_status: string; reference_status: string; semantic_evidence_review: string
  engine: { name: string; sqlite_version: string; validator_sha256: string }
  roles: Record<string, string | null>
  evidence: { id: string; title: string; text: string; source_id: string }[]
  cases: { id: string; question: string; kind: string; definition: string; reference_sql: string | null; expected_state: string | null; required_evidence: string[]; reference: ComparisonResult | null; arms: ComparisonArm[] }[]
}
export const comparisonApi = {
  sample: () => request<unknown>("/evaluation/sample"),
  compare: (payload: unknown) => request<ComparisonReport>("/evaluation/compare", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }),
}
