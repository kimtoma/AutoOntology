import type { Progress, Run, Source, Step } from "@/lib/api"

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
  ocr: "OCR models", claude: "Claude CLI", codex: "Codex CLI",
}

/** Build steps in plain words (the chip inside a step group). Export steps keep STEP_LABEL. */
export const STEP_HUMAN: Record<string, string> = {
  load: "Load tables",
  register: "Register with the engine",
  ingest: "Ingest rows",
  compile: "Compile business concepts (LLM)",
  read: "Read documents",
  extract: "Extract concepts (LLM)",
  consolidate: "Merge duplicate concepts",
  align: "Match document concepts to table concepts",
  merge: "Build the unified ontology",
  snapshot: "Save revision",
}

/** The whole step in plain words: `structured:hanbit:load` -> "hanbit · Load tables",
    `align:hanbit-docs` -> "hanbit-docs · Match document concepts to table concepts",
    `merge` -> "Build the unified ontology". Unknown names are returned as they are. */
export function humanStep(name: string): string {
  const parts = name.split(":")
  if ((parts[0] === "structured" || parts[0] === "documents") && parts.length >= 3) {
    const short = parts.slice(2).join(":")
    return `${parts[1]} · ${STEP_HUMAN[short] ?? STEP_LABEL[short] ?? short}`
  }
  if (parts[0] === "align" && parts.length >= 2) return `${parts.slice(1).join(":")} · ${STEP_HUMAN.align}`
  return STEP_HUMAN[name] ?? STEP_LABEL[name] ?? name
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
    return { group: "align", title: STEP_HUMAN.align, short: parts.slice(1).join(":") }
  }
  return { group: name, title: STEP_HUMAN[name] ?? STEP_LABEL[name] ?? name, short: name }
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

/** The short label of a step inside its group: plain words for build steps; an align step's
    short name is its documents source; a top step (merge, snapshot) is its own group title,
    so its chip has no label of its own (""). */
export function stepLabel(short: string, group?: string): string {
  if (group === "align") return short
  if (group && group === short) return ""
  return STEP_HUMAN[short] ?? STEP_LABEL[short] ?? short
}

/** Seconds a step took (or has been running, given *now*); null when it has not started. */
export function stepSeconds(step: Pick<Step, "started_at" | "finished_at" | "detail">,
                            now: number = Date.now()): number | null {
  const secs = step.detail?.seconds
  if (typeof secs === "number" && Number.isFinite(secs) && step.finished_at) return secs
  if (!step.started_at) return null
  const start = new Date(step.started_at).getTime()
  const end = step.finished_at ? new Date(step.finished_at).getTime() : now
  const s = (end - start) / 1000
  return Number.isFinite(s) && s >= 0 ? s : null
}

/** Seconds from the run's start to its end (or to *now* while it runs; to the last finished
    step when the run row has no end time). null without a start. */
export function runSeconds(run: Pick<Run, "started_at" | "finished_at">, steps: Step[] = [],
                           now?: number): number | null {
  if (!run.started_at) return null
  const start = new Date(run.started_at).getTime()
  let end: number | null = run.finished_at ? new Date(run.finished_at).getTime() : now ?? null
  if (end == null) {
    const ends = steps.map((s) => (s.finished_at ? new Date(s.finished_at).getTime() : NaN))
      .filter(Number.isFinite)
    end = ends.length ? Math.max(...ends) : null
  }
  if (end == null) return null
  const s = (end - start) / 1000
  return Number.isFinite(s) && s >= 0 ? s : null
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return ""
  if (s < 60) return `${s.toFixed(1)} s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    return `${m} min ${Math.round(s - m * 60)} s`
  }
  const h = Math.floor(s / 3600)
  return `${h} h ${Math.round((s - h * 3600) / 60)} min`
}

/** What a failed step says about itself: the error, a remedy, and the remedies of failed
    files (documents read step), deduplicated. */
export function stepFailure(step: Pick<Step, "status" | "detail">): { error: string; remedies: string[] } | null {
  if (step.status !== "failed") return null
  const d = step.detail ?? {}
  const error = typeof d.error === "string" ? d.error : ""
  const remedies = new Set<string>()
  if (typeof d.remedy === "string" && d.remedy) remedies.add(d.remedy)
  if (Array.isArray(d.failed)) {
    for (const f of d.failed) {
      const r = (f as { remedy?: unknown })?.remedy
      if (typeof r === "string" && r) remedies.add(r)
    }
  }
  return { error, remedies: [...remedies] }
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
