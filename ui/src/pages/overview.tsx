import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle, ArrowRight, BookOpen, CheckCircle2, Download, Hammer, Info, Loader2, XCircle,
} from "lucide-react"
import { useState, type ReactNode } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { ApplyBanner } from "@/components/apply-banner"
import { ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ReviewCard } from "@/components/review-card"
import { RunSheet } from "@/components/run-sheet"
import { StatusChip } from "@/components/status-chip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, REVIEW_POLL_MS, retryServerErrors, useOntology, usePendingCount } from "@/hooks/queries"
import { useRunEnd } from "@/hooks/use-run-events"
import { useBuildChanged, useReviewActions } from "@/hooks/use-review"
import { useNow } from "@/hooks/use-now"
import { fmtDate, fmtNumber, tn, useT, type MessageKey } from "@/i18n"
import { api, errorMessage, WAITING_CATEGORIES, type OntologySummary, type RunRequest } from "@/lib/api"
import { isActiveRun } from "@/lib/checklist"
import { fmtSeconds, RUN_LABEL, runSeconds } from "@/lib/labels"
import { ago, deltaSummary, freshnessText, opsNotices, staleSources, type Notice } from "@/lib/ops"
import { cn } from "@/lib/utils"
import HomePage from "@/pages/home"

const POLL = REVIEW_POLL_MS

function OpsCard({ id, title, action, children, className }: {
  id: string; title: string; action?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <Card id={id} className={cn("min-w-0 gap-4", className)} role="region" aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle id={`${id}-title`} className="text-base">{title}</CardTitle>
        {action && <CardAction>{action}</CardAction>}
      </CardHeader>
      <CardContent className="grid min-w-0 gap-3">{children}</CardContent>
    </Card>
  )
}

function num(v: unknown): number { return typeof v === "number" ? v : 0 }

/** ① Revision, when it was built, the counts and the change since the previous revision. */
function OntologyCard({ o, now, className }: { o: OntologySummary; now: number; className?: string }) {
  const t = useT()
  const diff = useQuery({ queryKey: keys.diff(null, null), queryFn: () => api.diff(), retry: retryServerErrors,
                          refetchInterval: POLL })
  const revisions = useQuery({ queryKey: keys.revisions, queryFn: () => api.revisions(10), refetchInterval: POLL })
  const kind = revisions.data?.find((r) => r.revision === o.revision)?.run_kind
  const stats: [MessageKey, number][] = [
    ["overview.count.classes", num(o.counts.classes)],
    ["overview.count.relations", num(o.counts.object_properties)],
    ["overview.count.evidence", num(o.counts.evidence)],
    ["overview.count.documents", num(o.counts.documents)],
  ]
  const d = diff.data ? deltaSummary(diff.data) : null
  return (
    <OpsCard id="ops-ontology" title={t("overview.ontology")} className={className}
             action={<Button size="sm" variant="ghost" asChild><Link to="/ontology"><BookOpen aria-hidden /> {t("nav.ontology")}</Link></Button>}>
      <div className="flex flex-wrap items-center gap-2 text-sm" id="ops-revision" data-revision={o.revision}>
        <Badge variant="secondary" className="font-normal tabular-nums">{t("overview.ontology.revision", { n: o.revision })}</Badge>
        <span title={fmtDate(o.created_at)}>{t("overview.ontology.built", { ago: ago(o.created_at, now), date: fmtDate(o.created_at) })}</span>
        {kind && <span className="text-xs text-muted-foreground">{kind === "apply" ? t("overview.ontology.byApply") : t("overview.ontology.byBuild")}</span>}
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {stats.map(([k, v]) => (
          <div key={k} className="rounded-lg border px-3 py-2">
            <dt className="text-xs text-muted-foreground">{t(k)}</dt>
            <dd className="text-lg font-semibold tabular-nums">{fmtNumber(v)}</dd>
          </div>
        ))}
      </dl>
      <div id="ops-delta" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
           data-from={diff.data?.from ?? ""} data-to={diff.data?.to ?? ""}>
        {diff.error ? <span className="text-destructive">{errorMessage(diff.error)}</span>
          : !diff.data || !d ? <Skeleton className="h-5 w-48" />
          : diff.data.from == null ? <span className="text-muted-foreground">{t("overview.delta.first")}</span>
          : (
            <>
              {d.added + d.removed + d.changed === 0 ? <span>{t("overview.delta.none", { from: diff.data.from })}</span> : (
                <>
                  <span>{t("overview.delta", { from: diff.data.from })}</span>
                  <span className="font-medium text-success tabular-nums" data-delta="added">{t("overview.delta.added", { n: d.added })}</span>
                  <span className="font-medium text-destructive tabular-nums" data-delta="removed">{t("overview.delta.removed", { n: d.removed })}</span>
                  <span className="font-medium tabular-nums" data-delta="changed">{t("overview.delta.changed", { n: d.changed })}</span>
                </>
              )}
              {d.axioms > 0 && <span className="text-xs text-muted-foreground">{tn("overview.delta.axioms", d.axioms)}</span>}
              {d.merges > 0 && <span className="text-xs text-muted-foreground">{tn("overview.delta.merges", d.merges)}</span>}
              <Button size="sm" variant="link" className="h-auto px-0" asChild>
                <Link to={`/ontology?changed=${o.revision}`} id="btn-view-changes">{t("overview.viewChanges")} <ArrowRight aria-hidden /></Link>
              </Button>
            </>
          )}
      </div>
    </OpsCard>
  )
}

/** ② Counts per review category and the first five items, decided in place. */
function ConfirmCard({ onBuild, className }: { onBuild: (sources: string[]) => void; className?: string }) {
  const t = useT()
  const pending = usePendingCount()
  const top = useQuery({ queryKey: keys.reviewPage({ overview: true }), queryFn: () => api.review({ size: 5 }),
                         retry: retryServerErrors, refetchInterval: POLL })
  const { decide, undo, busyId, announcement } = useReviewActions()
  const counts = pending.data?.counts
  const total = counts?.total ?? 0
  return (
    <OpsCard id="ops-review" title={t("overview.confirm")} className={className}
             action={<Button size="sm" variant="ghost" asChild><Link to="/review" id="btn-open-review">{t("overview.confirm.open")} <ArrowRight aria-hidden /></Link></Button>}>
      {counts && (
        <ul className="flex flex-wrap gap-1.5" id="ops-review-counts">
          {WAITING_CATEGORIES.map((c) => (
            <li key={c}>
              <Link to={`/review?category=${c}`} data-category={c} data-count={counts[c]}
                    className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs hover:bg-accent",
                                  counts[c] === 0 && "text-muted-foreground")}>
                {t(`review.tab.${c}`)} <b className="tabular-nums">{fmtNumber(counts[c])}</b>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="sr-only" aria-live="polite">{announcement}</p>
      {top.error ? <ErrorState error={top.error} onRetry={() => top.refetch()} />
        : !top.data ? <Skeleton className="h-24" />
        : !top.data.items.length ? <p className="text-sm text-muted-foreground" id="ops-review-none">{t("overview.confirm.none")}</p>
        : (
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-2" id="ops-review-top">
            {top.data.items.map((item) => (
              <ReviewCard key={item.id} item={item} compact busy={busyId === item.id}
                          onDecide={decide} onUndo={undo} onBuild={onBuild} />
            ))}
          </ul>
        )}
      {top.data && total > top.data.items.length && (
        <p className="text-xs text-muted-foreground">{t("overview.confirm.more", { n: total - top.data.items.length })}</p>
      )}
    </OpsCard>
  )
}

/** ③ Each source's freshness, and a resume build of the changed ones. */
function SourcesCard({ onBuild, building, now, className }: {
  onBuild: (s: string[]) => void; building: boolean; now: number; className?: string
}) {
  const t = useT()
  const fresh = useQuery({ queryKey: keys.freshness, queryFn: () => api.freshness(), refetchInterval: POLL })
  const stale = staleSources(fresh.data?.sources)
  return (
    <OpsCard id="ops-sources" title={t("overview.sources")} className={className}
             action={<Button size="sm" variant="ghost" asChild><Link to="/sources">{t("overview.sources.manage")}</Link></Button>}>
      {fresh.error ? <ErrorState error={fresh.error} onRetry={() => fresh.refetch()} />
        : !fresh.data ? <Skeleton className="h-20" />
        : !fresh.data.sources.length ? <p className="text-sm text-muted-foreground">{t("overview.sources.none")}</p>
        : (
          <>
            <ul className="grid gap-1.5" id="ops-freshness">
              {fresh.data.sources.map((s) => (
                <li key={s.name} data-source={s.name} data-state={s.state}
                    className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                  {s.state === "fresh" ? <CheckCircle2 className="size-4 text-success" aria-hidden />
                    : s.state === "unknown" ? <Info className="size-4 text-muted-foreground" aria-hidden />
                    : <AlertTriangle className="size-4 text-warning" aria-hidden />}
                  <span className="font-medium break-all">{s.name}</span>
                  <span className="text-muted-foreground">{t(`fresh.state.${s.state}` as MessageKey)}</span>
                  {s.state !== "fresh" && freshnessText(s) && <span className="text-xs" data-reasons>{freshnessText(s)}</span>}
                  {s.last_built_at && <span className="text-xs text-muted-foreground" title={fmtDate(s.last_built_at)}>· {t("fresh.lastBuilt", { ago: ago(s.last_built_at, now) })}</span>}
                </li>
              ))}
            </ul>
            {stale.length ? (
              <div>
                <Button size="sm" id="btn-build-changed" disabled={building} onClick={() => onBuild(stale)}>
                  {building ? <Loader2 className="animate-spin" aria-hidden /> : <Hammer aria-hidden />}
                  {building ? t("overview.sources.building") : t("overview.sources.build")}
                </Button>
              </div>
            ) : <p className="text-xs text-muted-foreground">{t("overview.sources.fresh")}</p>}
          </>
        )}
    </OpsCard>
  )
}

/** ④ The last five runs (builds, exports, applies) in plain words. */
function ActivityCard({ onOpen, now, className }: { onOpen: (id: string) => void; now: number; className?: string }) {
  const t = useT()
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(40), refetchInterval: POLL })
  const last = (runs.data ?? []).slice(0, 5)
  return (
    <OpsCard id="ops-activity" title={t("overview.activity")} className={className}
             action={<Button size="sm" variant="ghost" asChild><Link to="/build">{t("overview.activity.all")}</Link></Button>}>
      {runs.error ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        : !runs.data ? <Skeleton className="h-24" />
        : !last.length ? <p className="text-sm text-muted-foreground">{t("overview.activity.none")}</p>
        : (
          <ul className="grid gap-1" id="ops-activity-list">
            {last.map((r) => (
              <li key={r.id} data-run-kind={r.kind}>
                <button type="button" onClick={() => onOpen(r.id)}
                        className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent">
                  <span className="font-medium">{RUN_LABEL[r.kind] ?? r.kind}</span>
                  <StatusChip status={r.status} />
                  <span className="text-xs text-muted-foreground" title={fmtDate(r.started_at)}>{ago(r.started_at, now)}</span>
                  {runSeconds(r, r.steps) != null && <span className="text-xs text-muted-foreground">· {fmtSeconds(runSeconds(r, r.steps))}</span>}
                  {r.kind === "apply" && typeof r.params.applied_revision === "number" && (
                    <span className="text-xs text-muted-foreground">· r{r.params.applied_revision}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
    </OpsCard>
  )
}

const NOTICE_ICON = { info: Info, warn: AlertTriangle, error: XCircle }

/** ⑤ What needs doing: a failed run, unapplied decisions, changed sources, an old build, an
    export behind the latest revision. */
function NoticesCard({ notices, onAction, busy, className }: {
  notices: Notice[]
  onAction: (n: Notice) => void
  busy: boolean
  className?: string
}) {
  const t = useT()
  const actionLabel: Record<string, string> = {
    build: t("overview.sources.build"), export: t("overview.export"), "open-run": t("overview.openRun"),
  }
  // unapplied decisions get the apply banner (with its progress) instead of a plain row
  const rows = notices.filter((n) => n.id !== "unapplied")
  if (!notices.length) {
    // nothing to do: one quiet line, not a card
    return (
      <div id="ops-notices" data-empty className={className}>
        <ApplyBanner />
        <p className="flex items-center gap-2 text-sm text-muted-foreground" id="ops-notices-none">
          <CheckCircle2 className="size-4 text-success" aria-hidden /> {t("overview.notices.none")}
        </p>
      </div>
    )
  }
  return (
    <OpsCard id="ops-notices" title={t("overview.notices")} className={cn("gap-3", className)}>
      <ApplyBanner />
      {rows.length > 0 && (
        <ul className="grid gap-2" id="ops-notices-list">
          {rows.map((n) => {
            const Icon = NOTICE_ICON[n.tone]
            return (
              <li key={n.id} data-notice={n.id} data-tone={n.tone}
                  className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm">
                <Icon className={cn("size-4 shrink-0", n.tone === "error" ? "text-destructive" : n.tone === "warn" ? "text-warning" : "text-muted-foreground")} aria-hidden />
                <span className="min-w-0 flex-1">{n.text}</span>
                {n.action === "open-build" ? (
                  <Button size="sm" variant="outline" asChild data-notice-action={n.action}>
                    <Link to="/build"><Hammer aria-hidden /> {t("overview.openBuild")}</Link>
                  </Button>
                ) : n.action && n.action !== "apply" && (
                  <Button size="sm" variant="outline" disabled={busy && n.action !== "open-run"} data-notice-action={n.action}
                          onClick={() => onAction(n)}>
                    {n.action === "export" ? <Download aria-hidden /> : n.action === "build" ? <Hammer aria-hidden /> : null}
                    {actionLabel[n.action]}
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </OpsCard>
  )
}

/** The dashboard of a built ontology (`/` once a snapshot exists). */
export function OverviewDashboard({ ontology }: { ontology: OntologySummary }) {
  const t = useT()
  const qc = useQueryClient()
  const now = useNow(true, 30_000)
  const [runId, setRunId] = useState<string | null>(null)
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(40), refetchInterval: POLL })
  const exports = useQuery({ queryKey: keys.exports, queryFn: api.exports, refetchInterval: POLL })
  const fresh = useQuery({ queryKey: keys.freshness, queryFn: () => api.freshness(), refetchInterval: POLL })
  const pending = usePendingCount()
  // the snapshot header itself (a build or an apply from the CLI moves it too)
  useQuery({ queryKey: keys.ontology, queryFn: () => api.ontology(), retry: retryServerErrors, refetchInterval: POLL })
  const active = (runs.data ?? []).find(isActiveRun)
  useRunEnd(active?.id)
  const build = useBuildChanged(setRunId)
  const start = useMutation({
    mutationFn: (body: RunRequest) => api.startRun(body),
    onSuccess: ({ run_id }) => { setRunId(run_id); qc.invalidateQueries({ queryKey: keys.runs }) },
    onError: (e, body) => toast.error(t("home.runFailed", { kind: RUN_LABEL[body.kind] }), { description: errorMessage(e) }),
  })
  const notices = opsNotices({ ontology, runs: runs.data, exports: exports.data, pending: pending.data,
                               freshness: fresh.data?.sources, now })
  const busy = !!active || build.isPending || start.isPending
  const onAction = (n: Notice) => {
    if (n.action === "export") start.mutate({ kind: "export" })
    else if (n.action === "build") build.mutate(staleSources(fresh.data?.sources))
    else if (n.action === "open-run" && n.runId) setRunId(n.runId)
  }
  return (
    <>
      <PageHeader title={t("overview.title")} description={t("overview.description")} />
      {/* DOM order = phone order: what to do, the ontology, what needs a person, sources,
          activity. On wide screens the left column stacks Ontology, Sources and Activity and
          the confirmation card spans those rows on the right (no stretched cards). */}
      <div id="overview"
           className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2 lg:grid-rows-[auto_auto_auto_1fr] lg:items-start">
        <NoticesCard notices={notices} onAction={onAction} busy={busy} className="lg:col-span-2 lg:row-start-1" />
        <OntologyCard o={ontology} now={now} className="lg:col-start-1 lg:row-start-2" />
        <ConfirmCard onBuild={(s) => build.mutate(s)} className="lg:col-start-2 lg:row-span-3 lg:row-start-2" />
        <SourcesCard onBuild={(s) => build.mutate(s)} building={build.isPending || active?.kind === "build"} now={now}
                     className="lg:col-start-1 lg:row-start-3" />
        <ActivityCard onOpen={setRunId} now={now} className="lg:col-start-1 lg:row-start-4" />
      </div>
      <RunSheet runId={runId} onClose={() => setRunId(null)} />
    </>
  )
}

/** `/`: the first-run checklist while nothing is built, the operations dashboard after.
    A refetch without data goes back to "pending" (the checklist's own queries refetch the
    snapshot on mount): the last settled answer decides meanwhile, so the page never flips
    to a skeleton and back. */
export default function IndexPage() {
  const ontology = useOntology()
  const settled = ontology.data ? "overview" : ontology.isError ? "setup" : null
  const [mode, setMode] = useState<"overview" | "setup" | null>(settled)
  if (settled && settled !== mode) setMode(settled)
  const shown = settled ?? mode
  if (shown === "overview" && ontology.data) return <OverviewDashboard ontology={ontology.data} />
  // no snapshot yet, or the service is unreachable: the checklist explains what to do
  if (shown === "setup") return <HomePage />
  return <Skeleton className="h-[60svh]" id="index-loading" />
}
