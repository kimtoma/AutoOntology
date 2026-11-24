import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CalendarClock, FileSearch, Loader2, Play, Plus, Workflow } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { ActionRunSheet } from "@/components/action-run"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { StateBadge, ViaBadge } from "@/components/logic-badges"
import { LogicEditorSheet, type EditorTarget } from "@/components/logic-editor"
import { NativeSelect } from "@/components/native-select"
import { PageHeader } from "@/components/page-header"
import { ProjectLink as Link } from "@/components/project-link"
import { RoleNotice } from "@/components/role-notice"
import { RunSheet } from "@/components/run-sheet"
import { WorkflowRunSheet } from "@/components/workflow-run-sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { invalidateAfterAction, invalidateAfterRun, keys, retryServerErrors } from "@/hooks/queries"
import { fmtDate, useT, type MessageKey } from "@/i18n"
import {
  ACTION_RUN_STATUSES, api, errorMessage, type LogicDef, type LogicKind, type WorkflowInfo,
} from "@/lib/api"
import { useCan, useDenied } from "@/lib/auth"
import { isActiveRun } from "@/lib/checklist"
import { fmtValue, objectPath } from "@/lib/logic"
import { useArchived } from "@/lib/project"

type Tab = "functions" | "actions" | "workflows" | "runs"
const TABS: Tab[] = ["functions", "actions", "workflows", "runs"]
const KIND: Record<Exclude<Tab, "runs">, LogicKind> = { functions: "function", actions: "action", workflows: "workflow" }

/* --- definitions -------------------------------------------------------------------------------- */

function Origin({ def }: { def: LogicDef }) {
  const t = useT()
  return (
    <span className="text-xs text-muted-foreground" data-origin={def.origin}>
      {def.origin === "extracted" ? t("logic.origin.extracted") : t("logic.origin.manual")}
    </span>
  )
}

function DefRow({ def, onOpen, workflow, onRunWorkflow, onOpenWorkflowRun }: {
  def: LogicDef
  onOpen: () => void
  workflow?: WorkflowInfo
  onRunWorkflow?: (name: string) => void
  onOpenWorkflowRun?: (id: string) => void
}) {
  const t = useT()
  const canRun = useCan("workflow.run")
  const runDenied = useDenied("workflow.run")
  const archived = useArchived()
  const quotes = def.evidence.slice(0, 2)
  return (
    <li className="grid min-w-0 gap-1.5 rounded-xl border bg-card p-4" data-def={def.name} data-kind={def.kind} data-status={def.status}>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <button type="button" className="min-w-0 truncate text-left font-medium hover:underline" onClick={onOpen} data-open-def={def.name}>
          {def.label || def.name}
        </button>
        {def.label && def.label !== def.name && <code className="text-xs text-muted-foreground">{def.name}</code>}
        <StateBadge status={def.status} title={def.broken_reason || undefined} />
        <span className="text-xs text-muted-foreground tabular-nums">v{def.version}</span>
        <Origin def={def} />
        <span className="ml-auto text-xs text-muted-foreground">{t("logic.def.updated", { who: def.created_by ?? "—", date: fmtDate(def.updated_at) })}</span>
      </div>
      <p className="text-xs text-muted-foreground">
        {def.object_type && t("logic.def.objectType", { type: def.object_type })}
        {typeof def.spec.description === "string" && def.spec.description ? ` · ${def.spec.description}` : ""}
      </p>
      {def.broken_reason && <p className="text-xs text-destructive" data-reason>{def.broken_reason}</p>}
      {quotes.length > 0 && (
        <ul className="grid gap-1" aria-label={t("logic.evidence")}>
          {quotes.map((e, i) => (
            <li key={i} className="rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-1 text-xs">
              “{e.quote}”{e.document ? <span className="text-muted-foreground"> · {e.document}</span> : null}
            </li>
          ))}
        </ul>
      )}
      {def.kind === "workflow" && workflow && (
        <div className="flex flex-wrap items-center gap-2 border-t pt-2 text-xs text-muted-foreground" data-workflow-info>
          <CalendarClock className="size-3.5" aria-hidden />
          <span>{triggerText(workflow, t)}</span>
          {workflow.next_slot && <span data-next-slot>· {t("logic.workflow.next", { at: workflow.next_slot.replace("T", " ") })}</span>}
          {workflow.last_run ? (
            <button type="button" className="inline-flex items-center gap-1 hover:underline" data-last-run={workflow.last_run.id}
                    onClick={() => onOpenWorkflowRun?.(workflow.last_run!.id)}>
              · {t("logic.workflow.last", { date: fmtDate(workflow.last_run.started_at) })} <StateBadge status={workflow.last_run.status} />
            </button>
          ) : <span>· {t("logic.workflow.neverRan")}</span>}
          <Button size="sm" variant="outline" className="ml-auto" disabled={!canRun || archived} data-action="run-workflow"
                  title={archived ? t("projects.readOnly") : runDenied} onClick={() => onRunWorkflow?.(def.name)}>
            <Play aria-hidden /> {t("logic.workflow.runNow")}
          </Button>
        </div>
      )}
    </li>
  )
}

/** "Every day at 02:00", "After 독촉단계기록", "Run by hand". */
function triggerText(w: WorkflowInfo, t: ReturnType<typeof useT>): string {
  if (w.trigger.schedule) return t("logic.trigger.schedule.value", { value: w.trigger.schedule })
  if (w.trigger.on_action) return t("logic.trigger.on_action.value", { value: w.trigger.on_action })
  return t("logic.trigger.manual.value")
}

function DefList({ kind, onOpen, onRunWorkflow, onOpenWorkflowRun }: {
  kind: LogicKind
  onOpen: (name: string) => void
  onRunWorkflow: (name: string) => void
  onOpenWorkflowRun: (id: string) => void
}) {
  const t = useT()
  const defs = useQuery({ queryKey: keys.logicDefs(kind), queryFn: () => api.logic.list(kind), retry: retryServerErrors })
  const workflows = useQuery({ queryKey: keys.workflows, queryFn: api.workflows.list, enabled: kind === "workflow" })
  const byName = new Map((workflows.data?.workflows ?? []).map((w) => [w.name, w]))
  if (defs.error) return <ErrorState error={defs.error} onRetry={() => defs.refetch()} />
  if (!defs.data) return <div className="grid gap-3">{[0, 1].map((i) => <Skeleton key={i} className="h-24" />)}</div>
  if (!defs.data.length) {
    return <EmptyState icon={Workflow} title={t(`logic.empty.${kind}`)} hint={t("logic.empty.hint")} />
  }
  const rank: Record<string, number> = { active: 0, draft: 1, broken: 2, retired: 3 }
  const sorted = [...defs.data].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.name.localeCompare(b.name))
  return (
    <>
      {kind === "workflow" && workflows.data?.triggers === "off" && (
        <p className="mb-3 text-sm text-muted-foreground" id="workflow-triggers-off">{t("logic.workflow.triggersOff")}</p>
      )}
      <ul className="grid gap-3" id={`defs-${kind}`}>
        {sorted.map((d) => (
          <DefRow key={d.id} def={d} onOpen={() => onOpen(d.name)} workflow={byName.get(d.name)}
                  onRunWorkflow={onRunWorkflow} onOpenWorkflowRun={onOpenWorkflowRun} />
        ))}
      </ul>
    </>
  )
}

/* --- runs ---------------------------------------------------------------------------------------- */

function RunsTab({ onOpenActionRun, onOpenWorkflowRun }: { onOpenActionRun: (id: string) => void; onOpenWorkflowRun: (id: string) => void }) {
  const t = useT()
  const [status, setStatus] = useState("")
  const [action, setAction] = useState("")
  const actions = useQuery({ queryKey: keys.logicDefs("action"), queryFn: () => api.logic.list("action") })
  const query = { status: status || null, action: action || null, limit: 100 }
  const runs = useQuery({ queryKey: keys.actionRuns(query), queryFn: () => api.actions.runs(query), retry: retryServerErrors })
  const wfRuns = useQuery({ queryKey: keys.workflowRuns(""), queryFn: () => api.workflows.runs(null, 30), retry: retryServerErrors })
  return (
    <div className="grid gap-6">
      <section className="grid gap-3" aria-labelledby="action-runs-title">
        <div className="flex flex-wrap items-end gap-2">
          <h2 id="action-runs-title" className="mr-auto text-base font-medium">{t("logic.runs.actions")}</h2>
          <NativeSelect className="w-44" aria-label={t("logic.runs.status")} value={status} id="runs-status"
                        onChange={(e) => setStatus(e.target.value)}>
            <option value="">{t("logic.runs.allStatuses")}</option>
            {ACTION_RUN_STATUSES.map((s) => <option key={s} value={s}>{t(`logic.state.${s}`)}</option>)}
          </NativeSelect>
          <NativeSelect className="w-44" aria-label={t("logic.runs.action")} value={action} id="runs-action"
                        onChange={(e) => setAction(e.target.value)}>
            <option value="">{t("logic.runs.allActions")}</option>
            {(actions.data ?? []).map((a) => <option key={a.name} value={a.name}>{a.label || a.name}</option>)}
          </NativeSelect>
        </div>
        {runs.error ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
          : !runs.data ? <Skeleton className="h-32" />
          : !runs.data.length ? <p className="text-sm text-muted-foreground" id="action-runs-empty">{t("logic.runs.none")}</p>
          : (
            <ul className="grid gap-2" id="action-runs">
              {runs.data.map((r) => (
                <li key={r.id} className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm" data-action-run={r.id}>
                  <button type="button" className="font-medium hover:underline" onClick={() => onOpenActionRun(r.id)}>{r.preview?.label || r.action}</button>
                  <Link to={objectPath(r.object_type, r.object_key)} className="text-muted-foreground hover:underline">
                    {r.preview?.object_type_label || r.object_type} · {r.object_key}
                  </Link>
                  <span className="text-xs text-muted-foreground">{Object.entries(r.params).map(([k, v]) => `${k}=${fmtValue(v)}`).join(", ")}</span>
                  <ViaBadge via={r.via} who={r.requested_by} />
                  <span className="text-xs text-muted-foreground">{fmtDate(r.created_at)}</span>
                  <StateBadge status={r.status} className="ml-auto" />
                  {(r.can_approve || r.can_revert) && (
                    <Button size="sm" variant="outline" className="h-7" onClick={() => onOpenActionRun(r.id)} data-action={r.can_approve ? "review-approval" : "open-revert"}>
                      {r.can_approve ? t("logic.runs.decide") : t("logic.run.revert")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
      </section>
      <section className="grid gap-3" aria-labelledby="workflow-runs-title">
        <h2 id="workflow-runs-title" className="text-base font-medium">{t("logic.runs.workflows")}</h2>
        {wfRuns.error ? <ErrorState error={wfRuns.error} onRetry={() => wfRuns.refetch()} />
          : !wfRuns.data ? <Skeleton className="h-24" />
          : !wfRuns.data.length ? <p className="text-sm text-muted-foreground">{t("logic.runs.noneWorkflows")}</p>
          : (
            <ul className="grid gap-2" id="workflow-runs">
              {wfRuns.data.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm" data-workflow-run={r.id}>
                  <button type="button" className="font-medium hover:underline" onClick={() => onOpenWorkflowRun(r.id)}>{r.workflow}</button>
                  <span className="text-xs text-muted-foreground">{t(`logic.trigger.${r.trigger}` as MessageKey)} · {fmtDate(r.started_at)}</span>
                  <span className="text-xs text-muted-foreground">
                    {t("logic.runs.summary", { matched: r.summary.matched ?? 0, applied: r.summary.actions_applied ?? 0, pending: r.summary.pending_approval ?? 0 })}
                  </span>
                  <StateBadge status={r.status} className="ml-auto" />
                </li>
              ))}
            </ul>
          )}
      </section>
    </div>
  )
}

/* --- extraction ---------------------------------------------------------------------------------- */

/** "Extract from documents": a `logic` run, followed until it ends, then a link to Review. */
function useExtraction() {
  const t = useT()
  const qc = useQueryClient()
  const [runId, setRunId] = useState<string | null>(null)
  const [watch, setWatch] = useState<string | null>(null)
  const start = useMutation({
    mutationFn: () => api.startRun({ kind: "logic" }),
    onSuccess: ({ run_id }) => { setRunId(run_id); setWatch(run_id); qc.invalidateQueries({ queryKey: keys.runs }) },
    onError: (e) => toast.error(t("logic.extract.failed"), { description: errorMessage(e) }),
  })
  const run = useQuery({
    queryKey: keys.run(watch ?? ""),
    queryFn: () => api.run(watch!),
    enabled: !!watch,
    refetchInterval: (q) => (q.state.data && !isActiveRun(q.state.data) ? false : 1500),
  })
  const done = run.data && !isActiveRun(run.data) ? run.data : null
  const announced = useRef<string | null>(null)
  useEffect(() => {
    if (!done || announced.current === done.id) return
    announced.current = done.id
    invalidateAfterRun(qc)
    if (done.status === "succeeded") toast.success(t("logic.extract.done"), { description: t("logic.extract.doneHint") })
  }, [done, qc, t])
  return {
    start, runId, setRunId,
    finished: done?.status === "succeeded" ? done : null,
    running: start.isPending || (!!watch && !done),
  }
}

/* --- page ---------------------------------------------------------------------------------------- */

export default function LogicPage() {
  const t = useT()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const tab: Tab = TABS.includes(params.get("tab") as Tab) ? params.get("tab") as Tab : "functions"
  const canEdit = useCan("logic.edit")
  const editDenied = useDenied("logic.edit")
  const archived = useArchived()
  const ro = archived ? t("projects.readOnly") : undefined
  const defParam = params.get("def")
  const [editor, setEditor] = useState<EditorTarget | null>(
    defParam && tab !== "runs" ? { kind: KIND[tab], name: defParam } : null)
  const [actionRun, setActionRun] = useState<string | null>(null)
  const [workflowRun, setWorkflowRun] = useState<string | null>(null)
  const extraction = useExtraction()
  const extracted = !!extraction.finished
  const runWorkflow = useMutation({
    mutationFn: (name: string) => api.workflows.run(name),
    onSuccess: ({ run }) => {
      setWorkflowRun(run.id)
      invalidateAfterAction(qc)
    },
    onError: (e) => toast.error(t("logic.workflow.runFailed"), { description: errorMessage(e) }),
  })
  const setTab = (next: string) => {
    const sp = new URLSearchParams(params)
    sp.set("tab", next)
    sp.delete("def")
    setParams(sp, { replace: true })
  }
  return (
    <>
      <PageHeader title={t("logic.title")} description={t("logic.description")}
                  actions={(
                    <>
                      <Button variant="outline" disabled={!canEdit || archived || extraction.running} title={ro ?? editDenied}
                              id="btn-logic-extract" onClick={() => extraction.start.mutate()}>
                        {extraction.running ? <Loader2 className="animate-spin" aria-hidden /> : <FileSearch aria-hidden />}
                        {t("logic.extract.button")}
                      </Button>
                      {tab !== "runs" && (
                        <Button disabled={!canEdit || archived} title={ro ?? editDenied} id="btn-logic-new"
                                onClick={() => setEditor({ kind: KIND[tab], name: null })}>
                          <Plus aria-hidden /> {t(`logic.new.${KIND[tab]}`)}
                        </Button>
                      )}
                    </>
                  )} />
      <RoleNotice permission="logic.edit" message="auth.notice.logic" />
      {extracted && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-primary/40 bg-primary/5 p-3 text-sm" id="extract-done">
          <span className="flex-1">{t("logic.extract.doneHint")}</span>
          <Button size="sm" asChild><Link to="/review?category=logic" id="btn-extract-review">{t("logic.extract.toReview")}</Link></Button>
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab} className="mb-4">
        <TabsList aria-label={t("logic.tabs")} id="logic-tabs">
          {TABS.map((tb) => <TabsTrigger key={tb} value={tb} data-tab={tb}>{t(`logic.tab.${tb}`)}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      <p className="mb-3 text-sm text-muted-foreground" id="logic-help">{t(`logic.help.${tab}`)}</p>
      {tab === "runs" ? <RunsTab onOpenActionRun={setActionRun} onOpenWorkflowRun={setWorkflowRun} />
        : <DefList key={tab} kind={KIND[tab]} onOpen={(name) => setEditor({ kind: KIND[tab], name })}
                   onRunWorkflow={(name) => runWorkflow.mutate(name)} onOpenWorkflowRun={setWorkflowRun} />}
      {runWorkflow.isPending && <Badge variant="secondary" className="mt-2"><Loader2 className="animate-spin" aria-hidden /> {t("logic.workflow.starting")}</Badge>}
      <LogicEditorSheet target={editor} onClose={() => setEditor(null)}
                        onSaved={(kind, name) => setEditor({ kind, name })} />
      <ActionRunSheet runId={actionRun} onClose={() => setActionRun(null)} />
      <WorkflowRunSheet runId={workflowRun} onClose={() => setWorkflowRun(null)} onOpenActionRun={(id) => { setWorkflowRun(null); setActionRun(id) }} />
      <RunSheet runId={extraction.runId} onClose={() => extraction.setRunId(null)} />
    </>
  )
}
