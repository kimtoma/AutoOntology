import { knowledgeText } from "@/lib/operations"
import { useQuery } from "@tanstack/react-query"
import { ArrowRight, Inbox, ScanLine } from "lucide-react"
import { Link } from "react-router"
import { PageHeader } from "@/components/page-header"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useOperations, useLegacy, useFreshness } from "@/hooks/operations"
import { keys, useOntology } from "@/hooks/queries"
import { api, isNoSnapshot } from "@/lib/api"
import { fmtTime, STATUS_LABEL } from "@/lib/labels"
import { useT } from "@/i18n"
import HomePage from "@/pages/home"

export default function OperationsHome() {
  const t = useT(),
    ontology = useOntology(),
    overview = useOperations(),
    fresh = useFreshness(),
    legacy = useLegacy()
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(8), refetchInterval: 5000 })
  if (isNoSnapshot(ontology.error)) return <HomePage />
  if (ontology.error) return <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />
  if (overview.error) return <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
  if (!ontology.data || !overview.data) return <Skeleton className="h-80" />
  const o = overview.data,
    unknown = !!fresh.error || fresh.data?.sources.some((s) => s.state === "unknown"),
    pending = o.counts.pending + o.counts.held + o.counts.feedback + (legacy.data?.counts.total ?? 0)
  const first = o.highlights.find((i) => !i.decision) || o.highlights[0]
  return (
    <>
      <PageHeader
        title={t("ops.title")}
        description={t("ops.description")}
        actions={
          <Button asChild variant="outline">
            <Link to="/sources">
              <ScanLine aria-hidden />
              {t("ops.scan")}
            </Link>
          </Button>
        }
      />
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="outline">
          {t("ops.published", { n: o.published_revision ?? ontology.data.revision })}
        </Badge>
        <span className="text-muted-foreground">{o.workspace}</span>
        {o.candidate_revision && (
          <Badge variant="secondary">{t("ops.candidate", { n: o.candidate_revision })}</Badge>
        )}
      </div>
      <div className="mb-6 grid gap-3 sm:grid-cols-3" id="operations-counts">
        {[
          [t("ops.pending"), pending, "/review"],
          [t("ops.changedSources"), unknown ? "—" : (fresh.data?.stale ?? "…"), "/sources"],
          [t("ops.ready"), o.counts.approved + (legacy.data?.unapplied ?? 0), "/history"],
        ].map(([label, value, path]) => (
          <Link
            to={String(path)}
            key={String(label)}
            className="rounded-xl border bg-card p-5 transition-colors hover:bg-accent"
          >
            <div className="text-sm text-muted-foreground">{label}</div>
            <div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div>
          </Link>
        ))}
      </div>
      {unknown && (
        <div className="mb-6 rounded-xl border border-warning/40 bg-warning/5 p-5" role="status">
          <h2 className="font-semibold">{t("ops.unknown")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("ops.unknownHint")}</p>
        </div>
      )}
      {first ? (
        <section className="mb-7 rounded-xl border border-l-4 border-l-warning bg-card p-5">
          <Badge variant="outline">{t(`ops.${first.kind}`)}</Badge>
          <h2 className="my-3 text-lg font-semibold">{first.label}</h2>
          <div className="grid gap-3 text-sm sm:grid-cols-[1fr_auto_1fr]">
            <p className="text-muted-foreground">{knowledgeText(first.before)}</p>
            <ArrowRight className="hidden size-4 self-center sm:block" aria-hidden />
            <p>{knowledgeText(first.after)}</p>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">{t("ops.currentUnchanged")}</p>
            <Button asChild>
              <Link to={`/review?item=${first.id}`}>
                {t("ops.inspect")}
                <ArrowRight aria-hidden />
              </Link>
            </Button>
          </div>
        </section>
      ) : pending ? (
        <section className="mb-7 rounded-xl border bg-card p-5">
          <h2 className="font-semibold">{t("ops.pending")}</h2>
          <p className="my-2 text-sm text-muted-foreground">{t("ops.reviewHint")}</p>
          <Button asChild>
            <Link to="/review?tab=connections">
              <Inbox aria-hidden />
              {t("ops.openReview")}
            </Link>
          </Button>
        </section>
      ) : (
        <EmptyState title={t("ops.noChanges")} hint={t("ops.noChangesHint")} />
      )}
      <section className="mb-7">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t("ops.newKnowledge")}</h2>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/sources">
              {t("nav.sources")}
              <ArrowRight aria-hidden />
            </Link>
          </Button>
        </div>
        <ul className="divide-y">
          {fresh.data?.sources.map((s) => (
            <li key={s.name} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <span className="font-medium">{s.name}</span>
              <Badge variant="outline">
                {t(s.state === "unknown" ? "ops.unknownState" : `ops.${s.state}`)}
              </Badge>
            </li>
          ))}
        </ul>
        {!fresh.data && <p className="text-sm text-muted-foreground">{t("shell.status.checking")}</p>}
      </section>
      <section>
        <h2 className="mb-3 font-semibold">{t("ops.activity")}</h2>
        {runs.error ? (
          <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        ) : (
          <ul className="divide-y">
            {runs.data?.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div>
                  <Link to="/history" className="font-medium hover:underline">
                    {r.kind === "publish"
                      ? t("ops.history")
                      : r.kind === "build"
                        ? t("ops.sourceUpdate")
                        : r.kind === "apply"
                          ? t("ops.apply")
                          : t("ops.exported")}
                  </Link>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {fmtTime(r.finished_at || r.started_at || "")}
                  </p>
                </div>
                <Badge variant="outline">{STATUS_LABEL[r.status]}</Badge>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}
