import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Square } from "lucide-react"
import { useEffect, useRef } from "react"
import { toast } from "sonner"

import { StateBadge } from "@/components/logic-badges"
import { ProjectLink as Link } from "@/components/project-link"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { invalidateAfterAction, keys } from "@/hooks/queries"
import { fmtDate, fmtNumber, useT, type MessageKey } from "@/i18n"
import { api, errorMessage, type WorkflowRun } from "@/lib/api"
import { useCan } from "@/lib/auth"
import { fmtValue, objectPath } from "@/lib/logic"
import { useArchived } from "@/lib/project"

/** The summary counters a run shows, in this order. */
const COUNTERS = [
  "matched", "completed", "actions_applied", "pending_approval", "already_pending", "skipped", "failed", "notified",
  "where_errors", "scanned",
] as const

const isOpen = (run: WorkflowRun | undefined) => !!run && (run.status === "running" || run.status === "paused")

/** One workflow run, polled while it runs: its counters, each object's result and the action
    runs it started (open one with *onOpenActionRun*); cancel while it is open. */
export function WorkflowRunSheet({ runId, onClose, onOpenActionRun }: {
  runId: string | null; onClose: () => void; onOpenActionRun: (id: string) => void
}) {
  return (
    <Sheet open={!!runId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-2xl" id="workflow-run-sheet" aria-describedby="workflow-run-desc">
        {runId && <Body key={runId} runId={runId} onOpenActionRun={onOpenActionRun} />}
      </SheetContent>
    </Sheet>
  )
}

function Body({ runId, onOpenActionRun }: { runId: string; onOpenActionRun: (id: string) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const canRun = useCan("workflow.run")
  const archived = useArchived()
  const q = useQuery({
    queryKey: keys.workflowRun(runId),
    queryFn: () => api.workflows.getRun(runId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1500 : false),
  })
  const run = q.data
  // a run that stopped running changed objects and action runs
  const seen = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (seen.current === "running" && run && run.status !== "running") invalidateAfterAction(qc)
    seen.current = run?.status
  }, [run, qc])
  const cancel = useMutation({
    mutationFn: () => api.workflows.cancel(runId),
    onSuccess: () => {
      toast.success(t("logic.workflow.cancelled"))
      invalidateAfterAction(qc)
      q.refetch()
    },
    onError: (e) => toast.error(t("logic.workflow.cancelFailed"), { description: errorMessage(e) }),
  })
  const s = run?.summary ?? {}
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {run ? run.workflow : t("logic.workflow.run")}
          {run && <StateBadge status={run.status} />}
        </SheetTitle>
        <SheetDescription id="workflow-run-desc" className="break-all">
          {runId}{run ? ` · ${t(`logic.trigger.${run.trigger}` as MessageKey)} · ${fmtDate(run.started_at)}` : ""}
        </SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 p-4">
        {q.error ? <p role="alert" className="text-sm text-destructive">{errorMessage(q.error)}</p>
          : !run ? <Skeleton className="h-40" />
          : (
            <>
              <p className="sr-only" role="status" aria-live="polite" id="workflow-run-status" data-status={run.status}>{run.status}</p>
              {run.error && <p role="alert" className="text-sm text-destructive">{run.error}</p>}
              {run.status === "paused" && <p className="text-sm text-muted-foreground" id="workflow-paused">{t("logic.workflow.paused")}</p>}
              <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5" id="workflow-summary">
                {COUNTERS.filter((c) => typeof s[c] === "number").map((c) => (
                  <div key={c} className="rounded-lg border p-2" data-counter={c}>
                    <dt className="text-xs text-muted-foreground">{t(`logic.workflow.count.${c}`)}</dt>
                    <dd className="text-lg font-semibold tabular-nums">{fmtNumber(Number(s[c]))}</dd>
                  </div>
                ))}
              </dl>
              {s.truncated && <p className="text-sm text-amber-700 dark:text-amber-300" id="workflow-truncated">{t("logic.workflow.truncated")}</p>}
              {(run.objects ?? []).length > 0 && (
                <section className="grid gap-1.5" aria-labelledby="wf-objects">
                  <h3 id="wf-objects" className="text-sm font-medium">{t("logic.workflow.objects")}</h3>
                  <ul className="grid gap-1.5 text-sm">
                    {run.objects!.map((o) => {
                      const acts = o.steps.filter((st) => st.kind === "action" || st.kind === "notify" || st.error)
                      return (
                        <li key={o.key} className="grid gap-1 rounded-md border px-3 py-2" data-wf-object={o.key} data-status={o.status}>
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{o.key}</span>
                            {o.title && <span className="text-muted-foreground">{o.title}</span>}
                            <StateBadge status={o.status === "done" ? "succeeded" : o.status} className="ml-auto" />
                          </span>
                          {o.error && <span className="text-xs text-destructive">{o.error}</span>}
                          {acts.length === 0 && !o.error && <span className="text-xs text-muted-foreground">{t("logic.workflow.noStep")}</span>}
                          {acts.map((st) => (
                            <span key={st.path} className="flex flex-wrap items-center gap-1.5 text-xs">
                              <span>{st.kind === "notify" ? t("logic.workflow.notify") : st.action}</span>
                              {st.params && <span className="text-muted-foreground">{Object.entries(st.params).map(([k, v]) => `${k}=${fmtValue(v)}`).join(", ")}</span>}
                              {st.result && <StateBadge status={st.result} />}
                              {st.error && <span className="text-destructive">{st.error}</span>}
                              {st.action_run_id && (
                                <button type="button" className="underline underline-offset-4" data-open-action-run={st.action_run_id}
                                        onClick={() => onOpenActionRun(st.action_run_id!)}>{t("logic.run.view")}</button>
                              )}
                            </span>
                          ))}
                        </li>
                      )
                    })}
                  </ul>
                </section>
              )}
              {(run.action_runs ?? []).length > 0 && (
                <section className="grid gap-1.5" aria-labelledby="wf-actions">
                  <h3 id="wf-actions" className="text-sm font-medium">{t("logic.workflow.actionRuns")}</h3>
                  <ul className="grid gap-1 text-sm">
                    {run.action_runs!.map((a) => (
                      <li key={a.id} className="flex flex-wrap items-center gap-2" data-wf-action-run={a.id}>
                        <button type="button" className="font-medium hover:underline" onClick={() => onOpenActionRun(a.id)}>{a.preview?.label || a.action}</button>
                        <Link to={objectPath(a.object_type, a.object_key)} className="text-muted-foreground hover:underline">{a.object_key}</Link>
                        <StateBadge status={a.status} className="ml-auto" />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
      </div>
      {isOpen(run) && canRun && (
        <SheetFooter className="border-t">
          <Button variant="outline" disabled={cancel.isPending || archived} onClick={() => cancel.mutate()} id="btn-workflow-cancel">
            {cancel.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Square aria-hidden />} {t("logic.workflow.cancel")}
          </Button>
        </SheetFooter>
      )}
    </>
  )
}
