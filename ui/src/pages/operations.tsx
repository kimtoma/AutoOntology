import { CollectionSummary } from "@/components/collection-panel"
import { AXDashboard } from "@/components/ax-dashboard"
import { CaseSummaryRow } from "@/components/case-board"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  Database,
  FileText,
  GitCommitHorizontal,
  GitPullRequest,
  Network,
} from "lucide-react"
import { Link } from "react-router"
import { useState } from "react"
import { PageHeader } from "@/components/page-header"
import { ErrorState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useOperations, useChanges, useLegacy, useFreshness } from "@/hooks/operations"
import { keys, useOntology } from "@/hooks/queries"
import { api } from "@/lib/api"
import { knowledgeText } from "@/lib/operations"
import { fmtTime, STATUS_LABEL } from "@/lib/labels"
import { fmtNumber, useT } from "@/i18n"

export default function OperationsHome() {
  const t = useT(),
    ontology = useOntology(),
    overview = useOperations(),
    changes = useChanges(),
    fresh = useFreshness(),
    legacy = useLegacy()
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(6), refetchInterval: 5000 })
  const [showAX, setShowAX] = useState(false)
  const o = overview.data,
    model = ontology.data
  const unknown = !!fresh.error || fresh.data?.sources.some((s) => s.state === "unknown")
  const pending = o ? o.counts.pending + o.counts.held + o.counts.feedback + (legacy.data?.counts.total ?? 0) : "…"
  const queue = (changes.data?.items ?? o?.highlights ?? []).filter((i) => !i.decision).slice(0, 6)
  const counts = (model?.counts ?? {}) as Record<string, number>
  return (
    <div className="ops-overview">
      <PageHeader
        title={t("ops.title")}
        description={t("ops.description")}
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/sources">{t("ops.scan")}</Link>
            </Button>
            <Button asChild>
              <Link to="/review">
                {t("ops.openReview")}
                <ArrowRight aria-hidden />
              </Link>
            </Button>
          </>
        }
      />
      <CaseSummaryRow />
      <CollectionSummary />
      {!!overview.error && <ErrorState error={overview.error} onRetry={() => overview.refetch()} />}
      {!!ontology.error && <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />}
      <div className="ops-metrics" id="operations-counts">
        {[
          [t("ops.pending"), pending, "/review", t("console.pendingHint")],
          [
            t("ops.ready"),
            o ? o.counts.approved + (legacy.data?.unapplied ?? 0) : "…",
            "/history",
            t("console.readyHint"),
          ],
          [
            t("ops.changedSources"),
            unknown ? "—" : (fresh.data?.stale ?? "…"),
            "/sources",
            t("console.sourceHint"),
          ],
        ].map(([label, value, path, hint]) => (
          <Link key={String(label)} to={String(path)} className="ops-metric">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {label}
              <ArrowUpRight className="ml-auto size-3.5" aria-hidden />
            </div>
            <div className="ops-metric-value">{value}</div>
            <div className="text-xs text-muted-foreground">{hint}</div>
          </Link>
        ))}
      </div>
      {unknown && (
        <div className="ops-notice" role="status">
          <span className="status-dot bg-warning" aria-hidden />
          <div>
            <strong>{t("ops.unknown")}</strong>
            <p>{t("ops.unknownHint")}</p>
          </div>
        </div>
      )}
      <div className="ops-columns">
        <div className="min-w-0">
          <section className="ops-panel" aria-labelledby="queue-title">
            <div className="ops-panel-heading">
              <h2 id="queue-title">{t("console.queue")}</h2>
              <span className="console-counter">{o?.counts.pending ?? "…"}</span>
              <Link to="/review?tab=changes" className="ops-text-link ml-auto">
                {t("console.viewAll")}
                <ArrowUpRight aria-hidden />
              </Link>
            </div>
            {!!changes.error && !!changes.data && <ErrorState error={changes.error} onRetry={() => changes.refetch()} />}
            {changes.error && !changes.data ? (
              <ErrorState error={changes.error} onRetry={() => changes.refetch()} />
            ) : !changes.data && !o ? (
              <Skeleton className="my-4 h-28" aria-label={t("ops.loading")} />
            ) : queue.length ? (
              <div className="ops-queue">
                <div className="ops-queue-labels" aria-hidden>
                  <span>{t("console.change")}</span>
                  <span>{t("ops.evidence")}</span>
                  <span>{t("console.type")}</span>
                </div>
                {queue.map((item) => (
                  <Link to={`/review?tab=changes&item=${item.id}`} key={item.id} className="ops-queue-row">
                    <span className="ops-queue-main">
                      <GitPullRequest className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="min-w-0">
                        <strong>{item.label}</strong>
                        <span className="ops-queue-description">
                          {item.after_text || knowledgeText(item.after)}
                        </span>
                      </span>
                    </span>
                    <span className="ops-queue-evidence">
                      {item.evidence.find((e) => e.version === "candidate")?.document || "—"}
                    </span>
                    <span className="ops-queue-kind">
                      <span className="status-dot bg-foreground/40" aria-hidden />
                      {t(`ops.${item.kind}`)}
                      <ChevronRight className="ml-auto size-3.5" aria-hidden />
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="ops-quiet-state">
                <Check className="size-4 text-muted-foreground" aria-hidden />
                <div>
                  <p>{t("ops.noChanges")}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t("ops.noChangesHint")}</p>
                </div>
              </div>
            )}
            <div className="ops-queue-footer">
              <Link to="/review?tab=connections">
                <Network className="size-3.5" aria-hidden />
                {t("ops.alignments")}
                <span className="font-mono">{legacy.data?.counts.total ?? "—"}</span>
              </Link>
              <Link to="/review?tab=feedback">
                <FileText className="size-3.5" aria-hidden />
                {t("ops.feedback")}
                <span className="font-mono">{o?.counts.feedback ?? "…"}</span>
              </Link>
            </div>
          </section>
          <section className="ops-panel mt-8" aria-labelledby="activity-title">
            <div className="ops-panel-heading">
              <h2 id="activity-title">{t("ops.activity")}</h2>
              <Link to="/history" className="ops-text-link ml-auto">
                {t("nav.history")}
                <ArrowUpRight aria-hidden />
              </Link>
            </div>
            {!!runs.error && !!runs.data && <ErrorState error={runs.error} onRetry={() => runs.refetch()} />}
            {runs.error && !runs.data ? (
              <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
            ) : !runs.data ? (
              <Skeleton className="my-4 h-20" aria-label={t("ops.loading")} />
            ) : (
              <div className="ops-activity-list">
                {runs.data?.map((r) => (
                  <Link key={r.id} to="/history" className="ops-activity-row">
                    <span
                      className={
                        r.status === "succeeded"
                          ? "status-dot bg-success"
                          : r.status === "failed"
                            ? "status-dot bg-destructive"
                            : "status-dot bg-warning"
                      }
                      aria-hidden
                    />
                    <span className="ops-activity-name">
                      {r.kind === "publish"
                        ? t("console.publishedActivity")
                        : r.kind === "build"
                          ? t("ops.sourceUpdate")
                          : r.kind === "apply"
                            ? t("ops.apply")
                            : t("ops.exported")}
                    </span>
                    <span className="ops-activity-status">{STATUS_LABEL[r.status]}</span>
                    <time>{fmtTime(r.finished_at || r.started_at || "")}</time>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </div>
        <aside className="ops-aside">
          {model ? <section className="ops-published" aria-labelledby="published-title">
            <div className="flex items-center gap-2">
              <span className="status-dot bg-success" aria-hidden />
              <h2 id="published-title">{t("console.publishedModel")}</h2>
              <span className="ml-auto font-mono text-xs">r{o?.published_revision ?? model.revision}</span>
            </div>
            <p className="mt-3 truncate text-sm font-medium" title={o?.workspace ?? model.workspace}>
              {o?.workspace ?? model.workspace}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("ops.published", { n: o?.published_revision ?? model.revision })}
            </p>
            <dl className="ops-model-counts">
              <div>
                <dt>{t("console.concepts")}</dt>
                <dd>{fmtNumber((counts.classes || 0) + (counts.individuals || 0))}</dd>
              </div>
              <div>
                <dt>{t("console.documents")}</dt>
                <dd>{fmtNumber(counts.documents || 0)}</dd>
              </div>
              <div>
                <dt>{t("console.evidenceCount")}</dt>
                <dd>{fmtNumber(counts.evidence || 0)}</dd>
              </div>
            </dl>
            <p className="text-xs leading-relaxed text-muted-foreground">{t("console.publishedHint")}</p>
            <div className="mt-5 flex gap-2">
              <Button asChild size="sm" variant="outline">
                <Link to="/graph">
                  {t("ops.graph")}
                  <ArrowUpRight aria-hidden />
                </Link>
              </Button>
              <Button asChild size="sm" variant="ghost">
                <Link to="/exports">{t("ops.exports")}</Link>
              </Button>
            </div>
            {(o?.candidate_revision ?? changes.data?.candidate_revision) && (
              <Link to="/history" className="ops-candidate-link">
                <GitCommitHorizontal className="size-3.5" aria-hidden />
                {t("ops.candidate", { n: o?.candidate_revision ?? changes.data?.candidate_revision ?? "—" })}
                <ChevronRight className="ml-auto size-3" aria-hidden />
              </Link>
            )}
          </section> : <Skeleton className="h-60" aria-label={t("ops.loading")} />}
          <section className="ops-panel mt-7" aria-labelledby="sources-title">
            <div className="ops-panel-heading">
              <h2 id="sources-title">{t("nav.sources")}</h2>
              <Link to="/sources" className="ops-text-link ml-auto" aria-label={t("nav.sources")}>
                <ArrowUpRight aria-hidden />
              </Link>
            </div>
            <ul className="ops-source-list">
              {fresh.data?.sources.map((s) => (
                <li key={s.name}>
                  <Link to="/sources">
                    <span className="flex min-w-0 items-center gap-2">
                      {s.kind === "documents" ? (
                        <FileText className="size-3.5 shrink-0" aria-hidden />
                      ) : (
                        <Database className="size-3.5 shrink-0" aria-hidden />
                      )}
                      <span className="truncate">{s.name}</span>
                    </span>
                    <span className="ops-source-state">
                      <span
                        className={
                          s.state === "fresh"
                            ? "status-dot bg-success"
                            : s.state === "unknown"
                              ? "status-dot bg-muted-foreground"
                              : "status-dot bg-warning"
                        }
                        aria-hidden
                      />
                      {t(s.state === "unknown" ? "ops.unknownState" : `ops.${s.state}`)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {!fresh.data && (
              <p className="py-4 text-xs text-muted-foreground">{t("shell.status.checking")}</p>
            )}
          </section>
        </aside>
      </div>
      <details className="mt-8 border-t py-4" id="ax-measurements" onToggle={event => setShowAX(event.currentTarget.open)}>
        <summary className="cursor-pointer text-sm font-medium">{t("ax.title")}</summary>
        {showAX && <AXDashboard />}
      </details>
    </div>
  )
}
