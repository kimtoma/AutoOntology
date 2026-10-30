import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query"
import {
  BookOpen, CheckCircle2, CircleDashed, Download, ExternalLink, FlaskConical, HelpCircle, Loader2,
  Network, PackageOpen, Play, Plus,
} from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"
import type { QueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { ErrorState } from "@/components/empty-state"
import { ProviderCard } from "@/components/provider-card"
import { RunSheet } from "@/components/run-sheet"
import { SamplesDialog } from "@/components/samples-dialog"
import { StatusChip } from "@/components/status-chip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, retryServerErrors, useSamples } from "@/hooks/queries"
import { useNow } from "@/hooks/use-now"
import {
  api, ApiError, errorMessage, isNoSnapshot, type ExportEntry, type OntologySummary, type Run, type RunRequest,
  type ServiceStatus, type Source,
} from "@/lib/api"
import { guideUrl, stepCopy } from "@/lib/home-copy"
import { fmtNumber, tx, useT, type MessageKey } from "@/i18n"
import {
  allDone, checklist, expectedTime, isActiveRun, latestExport, latestRun, sampleSetFor, sampleSetsTouched,
  STEP_ORDER, type StepId,
  type StepState,
} from "@/lib/checklist"
import { fmtSeconds, fmtTime, progressText, RUN_LABEL, runSeconds } from "@/lib/labels"
import { cn } from "@/lib/utils"
import { NewSourceDialog } from "@/pages/sources"

/** Poll while a step is incomplete (the runs' SSE stream also refreshes on every step). */
const POLL_MS = 5000

/** The checklist from what the query cache holds now (the poll interval asks it). */
function cachedStates(qc: QueryClient) {
  const ontology = qc.getQueryState(keys.ontology)
  return checklist({
    status: qc.getQueryData<ServiceStatus>(keys.status),
    sources: qc.getQueryData<Source[]>(keys.sources),
    ontology: ontology?.data as OntologySummary | undefined
      ?? (ontology?.error instanceof ApiError && isNoSnapshot(ontology.error) ? null : undefined),
    runs: qc.getQueryData<Run[]>(keys.runs),
    exports: qc.getQueryData<ExportEntry[]>(keys.exports),
  })
}


function StateBadge({ state }: { state: StepState }) {
  const t = useT()
  const Icon = state === "done" ? CheckCircle2 : state === "checking" ? Loader2 : CircleDashed
  return (
    <Badge variant="outline" data-state={state}
           className={cn("font-normal", state === "done" && "border-success/30 bg-success/10 text-success",
                         state === "active" && "border-primary/40 bg-primary/10 text-foreground")}>
      <Icon className={cn(state === "checking" && "animate-spin")} aria-hidden /> {t(`home.state.${state}`)}
    </Badge>
  )
}

function StepCard({ id, n, state, children }: { id: StepId; n: number; state: StepState; children?: ReactNode }) {
  const t = useT()
  const copy = stepCopy(id)
  return (
    <li data-step={id} data-state={state}
        className={cn("grid min-w-0 gap-3 rounded-xl border bg-card p-4 sm:p-5",
                      state === "active" && "border-primary/50 shadow-sm ring-1 ring-primary/20",
                      state === "pending" && "opacity-80")}>
      <div className="flex min-w-0 items-start gap-3">
        <span aria-hidden className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border text-sm font-semibold",
                                        state === "done" ? "border-success bg-success text-white" : "bg-muted")}>
          {state === "done" ? "✓" : n}
        </span>
        <div className="grid min-w-0 flex-1 gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold">{copy.title}</h2>
            <StateBadge state={state} />
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground"
                        data-help={id} aria-label={t("home.help.aria", { step: copy.title })}>
                  <HelpCircle aria-hidden /> {t("home.help")}
                </Button>
              </PopoverTrigger>
              <PopoverContent className="grid gap-2 text-sm" data-help-content={id}>
                <p>{copy.help[0]}</p>
                <p className="text-muted-foreground">{copy.help[1]}</p>
              </PopoverContent>
            </Popover>
          </div>
          <p className="text-sm text-muted-foreground text-pretty">{copy.description}</p>
        </div>
      </div>
      {children && <div className="min-w-0 sm:pl-10">{children}</div>}
    </li>
  )
}

/** Refresh the checklist on every event of an active run (the poll covers the rest). */
function useRunEvents(runId: string | undefined) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return
    const es = new EventSource(api.runEventsUrl(runId))
    const refresh = () => {
      qc.invalidateQueries({ queryKey: keys.runs })
    }
    const end = () => {
      es.close()
      for (const key of [keys.runs, keys.sources, keys.ontology, keys.exports, keys.graph]) {
        qc.invalidateQueries({ queryKey: key })
      }
    }
    es.addEventListener("step", refresh)
    es.addEventListener("end", end)
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) es.close() }
    return () => es.close()
  }, [runId, qc])
}

function Counts({ ontology }: { ontology: OntologySummary }) {
  useT()
  const c = ontology.counts as Record<string, number>
  const items: [MessageKey, number | undefined][] = [
    ["home.counts.structured", c.classes_structured],
    ["home.counts.document", c.classes_document],
    ["home.counts.merged", c.merged_concepts],
    ["home.counts.links", c.object_properties],
  ]
  return (
    <ul className="grid gap-1 text-sm sm:grid-cols-2" id="explore-counts">
      {items.filter(([, v]) => typeof v === "number").map(([label, v]) => (
        <li key={label}>{tx(label, { n: <b key="n" className="tabular-nums">{fmtNumber(v!)}</b> })}</li>
      ))}
    </ul>
  )
}

/** One observer owns the empty-to-built transition; guidance never refetches on mount. */
export default function HomePage({ afterBuild }: { afterBuild?: ReactNode } = {}) {
  const ontology = useQuery({
    queryKey: keys.ontology,
    queryFn: api.ontology,
    retry: retryServerErrors,
    refetchInterval: (query) => query.state.data ? false : POLL_MS,
  })
  if (afterBuild && ontology.data) return afterBuild
  if (afterBuild && ontology.error && !isNoSnapshot(ontology.error)) {
    return <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />
  }
  return <OnboardingPage ontology={ontology} />
}

function OnboardingPage({ ontology }: { ontology: UseQueryResult<OntologySummary, unknown> }) {
  const t = useT()
  const qc = useQueryClient()
  const interval = () => (allDone(cachedStates(qc)) ? false : POLL_MS)
  const status = useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: interval })
  const sources = useQuery({ queryKey: keys.sources, queryFn: api.sources, refetchInterval: interval })
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(40), refetchInterval: interval })
  const exports = useQuery({ queryKey: keys.exports, queryFn: api.exports, refetchInterval: interval })
  const samples = useSamples()
  const [samplesOpen, setSamplesOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [runId, setRunId] = useState<string | null>(null)

  const ontologyValue = ontology.data ?? (isNoSnapshot(ontology.error) ? null : undefined)
  const states = checklist({
    status: status.data, sources: sources.data, ontology: ontologyValue, runs: runs.data,
    exports: exports.data,
  })
  const doneCount = STEP_ORDER.filter((s) => states[s] === "done").length

  const build = latestRun(runs.data, "build")
  const active = (runs.data ?? []).find(isActiveRun)
  useRunEvents(active?.id)
  const now = useNow(!!build && isActiveRun(build))
  const buildSecs = build ? runSeconds(build, build.steps, isActiveRun(build) ? now : undefined) : null

  const start = useMutation({
    mutationFn: (body: RunRequest) => api.startRun(body),
    onSuccess: ({ run_id }) => {
      setRunId(run_id)
      qc.invalidateQueries({ queryKey: keys.runs })
      qc.invalidateQueries({ queryKey: keys.sources })
    },
    onError: (e, body) => toast.error(t("home.runFailed", { kind: RUN_LABEL[body.kind] }), { description: errorMessage(e) }),
  })

  const names = (sources.data ?? []).map((s) => s.name)
  const set = sampleSetFor(names, samples.data)
  const eta = expectedTime(set, status.data?.providers.chat_provider)
  const touched = sampleSetsTouched(names, samples.data)
  const modelsOk = states.models === "done"
  const latest = latestExport(exports.data)
  const busy = !!active || start.isPending

  return (
    <>
      <PageHeader title={t("home.title")} description={t("home.description")} />
      <p className="mb-4 text-sm font-medium" id="checklist-progress" data-done={doneCount}>
        {t("home.progress", { done: doneCount })}
      </p>
      <ol className="grid gap-4" id="checklist">
        <StepCard id="models" n={1} state={states.models}>
          {status.data ? <ProviderCard status={status.data} services />
            : status.error ? <p className="text-sm text-destructive" role="alert">{errorMessage(status.error)}</p>
            : <Skeleton className="h-20" />}
        </StepCard>

        <StepCard id="sources" n={2} state={states.sources}>
          <div className="grid gap-3">
            {sources.data && (
              <p className="text-sm" id="sources-count">
                {sources.data.length ? tx(`home.sources.count.${sources.data.length === 1 ? "one" : "other"}`,
                                          { n: <b key="n">{sources.data.length}</b>, names: names.join(", ") })
                  : t("home.sources.none")}
                {sources.data.length > 0 && <> · <Link to="/sources" className="underline underline-offset-4">{t("home.sources.manage")}</Link></>}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant={states.sources === "active" ? "default" : "outline"} id="btn-load-samples"
                      onClick={() => setSamplesOpen(true)}>
                <PackageOpen aria-hidden /> {t("home.sources.loadSamples")}
              </Button>
              <Button variant="outline" id="btn-add-own" onClick={() => setAdding(true)}>
                <Plus aria-hidden /> {t("home.sources.addOwn")}
              </Button>
            </div>
          </div>
        </StepCard>

        <StepCard id="build" n={3} state={states.build}>
          <div className="grid gap-3">
            {build && (
              <div className="flex flex-wrap items-center gap-2 text-sm" id="last-build" data-status={build.status}>
                <span>{t("home.build.last")}</span>
                <StatusChip status={build.status} />
                {buildSecs != null && (
                  <span className="text-muted-foreground" id="last-build-elapsed">
                    {isActiveRun(build) ? t("home.build.runningFor", { time: fmtSeconds(buildSecs) }) : fmtSeconds(buildSecs)}
                  </span>
                )}
                <span className="text-muted-foreground">{fmtTime(build.started_at)}</span>
                {Object.keys(build.progress || {}).length > 0 && (
                  <span className="text-xs text-muted-foreground">{progressText(build.progress)}</span>
                )}
              </div>
            )}
            {build?.status === "failed" && build.error && (
              <p className="text-xs break-words text-destructive">{build.error.slice(0, 300)}</p>
            )}
            {ontologyValue && (
              <p className="text-sm">{tx("home.build.revision", { revision: <b key="r">{ontologyValue.revision}</b>, date: fmtTime(ontologyValue.created_at) })}</p>
            )}
            {eta && !ontologyValue && (
              <p className="text-xs text-muted-foreground" id="build-eta">
                {t("home.build.eta", { set: set?.set ?? "", provider: status.data?.providers.chat_provider ?? "", time: eta })}
              </p>
            )}
            {!set && touched.length > 1 && !ontologyValue && (
              <p className="text-xs text-muted-foreground" id="build-eta">
                {t("home.build.noEta", { sets: touched.map((x) => x.set).join(", ") })}
              </p>
            )}
            {!modelsOk && names.length > 0 && (
              <p className="text-xs text-muted-foreground" id="build-needs-models">
                {t("home.build.needsModels")}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <span title={!modelsOk ? t("home.build.needsModels.short") : !names.length ? t("home.build.needsSource") : undefined}
                    className="inline-flex">
              <Button id="btn-home-build" disabled={busy || !names.length || !modelsOk}
                      variant={states.build === "active" ? "default" : "outline"}
                      onClick={() => start.mutate({ kind: "build" })}>
                {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
                {active?.kind === "build" ? t("home.build.building") : ontologyValue ? t("home.build.again") : t("home.build.start")}
              </Button>
              </span>
              {(active ?? build) && (
                <Button variant="ghost" id="btn-live-run" onClick={() => setRunId((active ?? build)!.id)}>
                  {t("home.build.openLive")}
                </Button>
              )}
              <Button variant="ghost" asChild><Link to="/build">{t("home.build.page")}</Link></Button>
            </div>
          </div>
        </StepCard>

        <StepCard id="explore" n={4} state={states.explore}>
          <div className="grid gap-3">
            {ontologyValue && <Counts ontology={ontologyValue} />}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild><Link to="/ontology"><BookOpen aria-hidden /> {t("nav.ontology")}</Link></Button>
              <Button variant="outline" asChild><Link to="/graph"><Network aria-hidden /> {t("nav.graph")}</Link></Button>
              <Button variant="outline" asChild><Link to="/playground"><FlaskConical aria-hidden /> {t("nav.playground")}</Link></Button>
            </div>
          </div>
        </StepCard>

        <StepCard id="export" n={5} state={states.export}>
          <div className="grid gap-3">
            <p className="text-sm" id="latest-export">
              {latest ? tx("home.export.latest", { revision: <b key="r">{latest.revision}</b>, dir: latest.dir,
                                                   date: fmtTime(latest.created_at) })
                : t("home.export.none")}
              {latest && ontologyValue && latest.revision < ontologyValue.revision &&
                t("home.export.stale", { revision: ontologyValue.revision })}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button id="btn-home-export" variant={states.export === "active" ? "default" : "outline"}
                      disabled={busy || !ontologyValue} onClick={() => start.mutate({ kind: "export" })}>
                {active?.kind === "export" ? <Loader2 className="animate-spin" aria-hidden /> : <Download aria-hidden />} {t("home.export.button")}
              </Button>
              <Button variant="ghost" asChild><Link to="/exports">{t("home.export.open")}</Link></Button>
            </div>
          </div>
        </StepCard>
      </ol>
      <p className="mt-6 text-sm text-muted-foreground">
        {tx("home.guide", {
          link: (
            <a key="guide" href={guideUrl()} target="_blank" rel="noreferrer" id="guide-link"
               className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4">
              {t("home.guide.link")} <ExternalLink className="size-3.5" aria-hidden />
            </a>
          ),
        })}
      </p>
      <SamplesDialog open={samplesOpen} onOpenChange={setSamplesOpen} />
      <NewSourceDialog open={adding} onOpenChange={setAdding} />
      <RunSheet runId={runId} onClose={() => setRunId(null)} />
    </>
  )
}
