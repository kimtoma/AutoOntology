import { fmtDate, fmtSecs, labelTable, t } from "@/i18n/core"
import type { Progress, Run, Source, Step } from "@/lib/api"

export const KIND_LABEL = labelTable({
  files: "kind.files", database: "kind.database", documents: "kind.documents",
})
/** The two source families of the Sources page. */
export const FAMILY_LABEL = labelTable({ structured: "family.structured", documents: "family.documents" })
export const familyOf = (kind: string) => (kind === "documents" ? "documents" : "structured")

export const RUN_LABEL = labelTable({ build: "run.build", export: "run.export", apply: "ops.apply", publish: "ops.history" })
export const STATUS_LABEL = labelTable({
  pending: "status.pending", running: "status.running", succeeded: "status.succeeded",
  failed: "status.failed", cancelled: "status.cancelled", skipped: "status.skipped",
})
export const STEP_LABEL = labelTable({
  load: "step.load", register: "step.register", ingest: "step.ingest", compile: "step.compile",
  read: "step.read", extract: "step.extract", consolidate: "step.consolidate",
  align: "step.align", merge: "step.merge", snapshot: "step.snapshot",
  collect: "step.collect", serialize: "step.serialize", upstream: "step.upstream", ossie: "step.ossie",
  okf: "step.okf", report: "step.report",
})
export const CKIND_LABEL = labelTable({
  term: "ckind.term", entity: "ckind.entity", metric: "ckind.metric", attribute: "ckind.attribute",
  process: "ckind.process", rule: "ckind.rule", other: "ckind.other",
})
export const ORIGIN_LABEL = labelTable({
  structured: "origin.structured", document: "origin.document", aligned: "origin.aligned",
})
export const REL_LABEL = labelTable({
  same: "rel.same", broader: "rel.broader", narrower: "rel.narrower", related: "rel.related", none: "rel.none",
})
export const CHECK_LABEL = labelTable({ ok: "check.ok", warn: "check.warn", error: "check.error" })
export const CHECK_NAME = labelTable({
  backend: "checkName.backend", postgres: "checkName.postgres", ollama: "checkName.ollama",
  kordoc: "checkName.kordoc", ocr: "checkName.ocr", claude: "checkName.claude", codex: "checkName.codex",
})

/** Build steps in plain words (the chip inside a step group). Export steps keep STEP_LABEL. */
export const STEP_HUMAN = labelTable({
  load: "human.load",
  register: "human.register",
  ingest: "human.ingest",
  compile: "human.compile",
  read: "human.read",
  extract: "human.extract",
  consolidate: "human.consolidate",
  align: "human.align",
  merge: "human.merge",
  snapshot: "human.snapshot",
})

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
  return fmtSecs(s)
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
    return s.concepts ? t("source.state.docs", { documents: s.documents, concepts: s.concepts })
      : t("source.state.notExtracted")
  }
  return s.built ? t("source.state.built") : t("source.state.notBuilt")
}

export function fmtTime(iso: string | null | undefined): string {
  return fmtDate(iso)
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
  return fmtSecs(s)
}

const PROGRESS_LABEL = labelTable({
  chunks: "progress.chunks", concepts: "progress.concepts", documents: "progress.documents", files: "progress.files",
})
/** A progress counter's name ("chunks") in the UI language; unknown names as they are. */
const progressLabel = (label: string) => PROGRESS_LABEL[label] ?? label

export function progressText(progress: Progress | undefined, withFailed = true): string {
  return Object.entries(progress || {})
    .map(([label, counts]) => {
      const total = Object.values(counts).reduce((a, b) => a + (b || 0), 0)
      const done = counts.succeeded || 0
      const failed = withFailed && counts.failed ? t("progress.failed", { n: counts.failed }) : ""
      return `${progressLabel(label)} ${done}/${total}${failed}`
    })
    .join(" · ")
}
