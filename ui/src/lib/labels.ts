import type { Progress, Source, Step } from "@/lib/api"

export const KIND_LABEL: Record<string, string> = {
  files: "Structured files", database: "Database", documents: "Documents",
}
/** The two source families of the Sources page. */
export const FAMILY_LABEL: Record<string, string> = { structured: "Structured", documents: "Documents" }
export const familyOf = (kind: string) => (kind === "documents" ? "documents" : "structured")

export const RUN_LABEL: Record<string, string> = { build: "Build", export: "Export" }
export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending", running: "Running", succeeded: "Succeeded", failed: "Failed",
  cancelled: "Cancelled", skipped: "Skipped",
}
export const STEP_LABEL: Record<string, string> = {
  load: "Load", register: "Register", ingest: "Ingest", compile: "Compile",
  read: "Read", extract: "Extract", consolidate: "Consolidate",
  align: "Align", merge: "Merge", snapshot: "Snapshot",
  collect: "Collect", serialize: "Serialize", upstream: "Upstream files", ossie: "Ossie",
  okf: "OKF", report: "Report",
}
export const CKIND_LABEL: Record<string, string> = {
  term: "Term", entity: "Entity", metric: "Metric", attribute: "Attribute", process: "Process",
  rule: "Rule", other: "Other",
}
export const ORIGIN_LABEL: Record<string, string> = {
  structured: "Structured", document: "Document", aligned: "Aligned",
}
export const REL_LABEL: Record<string, string> = {
  same: "Same as", broader: "Broader", narrower: "Narrower", related: "Related", none: "None",
}
export const CHECK_LABEL: Record<string, string> = { ok: "OK", warn: "Warning", error: "Error" }
export const CHECK_NAME: Record<string, string> = {
  backend: "Upstream backend", postgres: "Postgres", ollama: "Ollama models", kordoc: "kordoc",
}

/** One step group of a build run: `structured:<name>`, `documents:<name>`, `align`, `merge`,
    `snapshot` (export runs: one group per step). */
export interface StepGroup { key: string; title: string; steps: (Step & { short: string })[] }

/** `structured:hanbit:load` -> group `structured:hanbit`, short name `load`;
    `align:hanbit-docs` -> group `align`, short `hanbit-docs`. */
export function splitStep(name: string): { group: string; title: string; short: string } {
  const parts = name.split(":")
  if ((parts[0] === "structured" || parts[0] === "documents") && parts.length >= 3) {
    return {
      group: `${parts[0]}:${parts[1]}`,
      title: `${FAMILY_LABEL[parts[0]]} · ${parts[1]}`,
      short: parts.slice(2).join(":"),
    }
  }
  if (parts[0] === "align" && parts.length >= 2) {
    return { group: "align", title: STEP_LABEL.align, short: parts.slice(1).join(":") }
  }
  return { group: name, title: STEP_LABEL[name] ?? name, short: name }
}

export function groupSteps(steps: Step[]): StepGroup[] {
  const groups: StepGroup[] = []
  const byKey = new Map<string, StepGroup>()
  for (const s of steps) {
    const { group, title, short } = splitStep(s.name)
    let g = byKey.get(group)
    if (!g) {
      g = { key: group, title, steps: [] }
      byKey.set(group, g)
      groups.push(g)
    }
    g.steps.push({ ...s, short })
  }
  return groups
}

/** The short label of a step inside its group. */
export function stepLabel(short: string): string {
  return STEP_LABEL[short] ?? short
}

export function sourceState(s: Source): string {
  if (s.kind === "documents") {
    return s.concepts ? `${s.documents} documents · ${s.concepts} concepts` : "Not extracted yet"
  }
  return s.built ? "Built" : "Not built yet"
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "-"
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function fmtDuration(start: string | null, end: string | null): string {
  if (!start || !end) return ""
  const s = (new Date(end).getTime() - new Date(start).getTime()) / 1000
  if (!Number.isFinite(s) || s < 0) return ""
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}

export function progressText(progress: Progress | undefined, withFailed = true): string {
  return Object.entries(progress || {})
    .map(([label, counts]) => {
      const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0)
      const done = counts.succeeded || 0
      const failed = withFailed && counts.failed ? `, ${counts.failed} failed` : ""
      return `${label} ${done}/${total}${failed}`
    })
    .join(" · ")
}
