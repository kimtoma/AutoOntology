/* The living-ontology operations helpers of the Overview and Review pages: relative time,
   freshness reasons in plain words, the diff summary and the operations notices. Pure: the
   pages feed them the query results, the tests feed them fixtures. */

import { t, tn, type MessageKey } from "@/i18n/core"
import type {
  ExportEntry, FreshnessReason, OntologyDiff, OntologySummary, PendingCount, RevisionEntry, Run,
  SourceFreshness,
} from "@/lib/api"
import { isActiveRun, latestExport } from "@/lib/checklist"
import { RUN_LABEL } from "@/lib/labels"

const DAY_MS = 86_400_000
/** A build older than this is worth a notice. */
export const OLD_BUILD_DAYS = 7

/** "just now", "5 min ago", "3 h ago", "9 days ago" ("" for a missing or bad date). */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return ""
  const then = new Date(iso).getTime()
  if (!Number.isFinite(then)) return ""
  const s = Math.max(0, (now - then) / 1000)
  if (s < 60) return t("ago.now")
  if (s < 3600) return t("ago.m", { n: Math.floor(s / 60) })
  if (s < 86400) return t("ago.h", { n: Math.floor(s / 3600) })
  return t("ago.d", { n: Math.floor(s / 86400) })
}

/** One freshness reason in plain words: "3 files added", "the database schema changed". */
export function reasonText(r: FreshnessReason): string {
  if (r.kind === "files_added" || r.kind === "files_changed" || r.kind === "files_removed") {
    return tn(`fresh.${r.kind}`, r.count)
  }
  const key = `fresh.${r.kind}` as MessageKey
  const text = t(key)
  return text === key ? r.kind : text
}

/** Every reason of a source: "3 files added, 1 changed"; an unknown state names the error. */
export function freshnessText(row: SourceFreshness): string {
  if (row.state === "unknown") return t("fresh.unknown", { error: row.error ?? "?" })
  return row.reasons.map(reasonText).join(", ")
}

/** Sources a resume build should cover (stale or never built; unknown ones are left out). */
export const staleSources = (rows: SourceFreshness[] | undefined) =>
  (rows ?? []).filter((r) => r.stale === true).map((r) => r.name)

export interface DeltaSummary { added: number; removed: number; changed: number; axioms: number; merges: number }

/** The class / relation / merge totals of a diff ("+2 −1 ~3"). */
export function deltaSummary(diff: OntologyDiff): DeltaSummary {
  const ax = diff.axioms?.totals ?? { added: 0, removed: 0, changed: 0 }
  const mg = diff.merges?.totals ?? { added: 0, removed: 0 }
  return {
    added: diff.classes.totals.added,
    removed: diff.classes.totals.removed,
    changed: diff.classes.totals.changed ?? 0,
    axioms: ax.added + ax.removed + (ax.changed ?? 0),
    merges: mg.added + mg.removed,
  }
}

/** The revision before *revision* in the list (newest first), or null. */
export function previousRevision(revisions: RevisionEntry[] | undefined, revision: number): number | null {
  const older = (revisions ?? []).map((r) => r.revision).filter((r) => r < revision)
  return older.length ? Math.max(...older) : null
}

export type NoticeAction = "apply" | "export" | "build" | "open-run"
export interface Notice {
  id: "old-build" | "unapplied" | "export" | "stale" | "failed"
  tone: "info" | "warn" | "error"
  text: string
  action?: NoticeAction
  runId?: string
}

export interface NoticeInput {
  ontology?: OntologySummary | null
  runs?: Run[]
  exports?: ExportEntry[]
  pending?: PendingCount
  freshness?: SourceFreshness[]
  now?: number
}

/** What an operator should look at: a failed last run, unapplied decisions, changed sources,
    an old build, an export behind the latest revision. Data still loading adds nothing. */
export function opsNotices(input: NoticeInput): Notice[] {
  const now = input.now ?? Date.now()
  const out: Notice[] = []
  const runs = input.runs ?? []
  const last = runs.find((r) => !isActiveRun(r))
  if (last?.status === "failed") {
    out.push({ id: "failed", tone: "error", runId: last.id, action: "open-run",
               text: t("overview.notice.failed", { kind: RUN_LABEL[last.kind] ?? last.kind }) })
  }
  const unapplied = input.pending?.unapplied ?? 0
  if (unapplied > 0) {
    out.push({ id: "unapplied", tone: "warn", action: "apply", text: tn("overview.notice.unapplied", unapplied) })
  }
  const stale = staleSources(input.freshness).length
  if (stale > 0) out.push({ id: "stale", tone: "warn", action: "build", text: tn("overview.notice.stale", stale) })
  const lastBuild = runs.find((r) => r.kind === "build" && r.status === "succeeded")
  const builtAt = lastBuild?.finished_at ?? lastBuild?.started_at ?? input.ontology?.created_at
  if (builtAt) {
    const days = Math.floor((now - new Date(builtAt).getTime()) / DAY_MS)
    if (days >= OLD_BUILD_DAYS) out.push({ id: "old-build", tone: "info", action: "build", text: t("overview.notice.oldBuild", { days }) })
  }
  if (input.ontology && input.exports) {
    const latest = latestExport(input.exports)
    if (!latest) {
      out.push({ id: "export", tone: "info", action: "export",
                 text: t("overview.notice.noExport", { revision: input.ontology.revision }) })
    } else if (latest.revision < input.ontology.revision) {
      out.push({ id: "export", tone: "info", action: "export",
                 text: t("overview.notice.export", { export: latest.revision, revision: input.ontology.revision }) })
    }
  }
  return out
}
