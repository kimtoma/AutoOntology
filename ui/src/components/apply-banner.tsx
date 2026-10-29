import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCheck, Loader2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { invalidateAfterRun, keys, usePendingCount } from "@/hooks/queries"
import { tn, useT } from "@/i18n"
import { api, ApiError, errorMessage, type Run } from "@/lib/api"
import { isActiveRun } from "@/lib/checklist"
import { humanStep } from "@/lib/labels"
import { cn } from "@/lib/utils"

/** The step an apply run is on, in plain words ("" before the first step). */
function currentStep(run: Run | undefined): string {
  const step = run?.steps.find((s) => s.status === "running") ?? run?.steps.findLast((s) => s.status === "succeeded")
  return step ? humanStep(step.name) : ""
}

/** "N decisions not applied yet → Apply (no model calls, about a minute)": starts an
    `apply` run and follows it to the new revision. Hidden while nothing waits. */
export function ApplyBanner({ className }: { className?: string }) {
  const t = useT()
  const qc = useQueryClient()
  const pending = usePendingCount()
  const [runId, setRunId] = useState<string | null>(null)
  const announced = useRef<string | null>(null)
  const run = useQuery({
    queryKey: keys.run(runId ?? ""),
    queryFn: () => api.run(runId!),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data && !isActiveRun(q.state.data) ? false : 1000),
  })
  const start = useMutation({
    mutationFn: () => api.startRun({ kind: "apply" }),
    onSuccess: ({ run_id }) => {
      setRunId(run_id)
      qc.invalidateQueries({ queryKey: keys.runs })
    },
    onError: (e) => toast.error(t("apply.failed"), {
      description: e instanceof ApiError && e.status === 409 ? t("apply.busy") : errorMessage(e),
    }),
  })
  const done = run.data && !isActiveRun(run.data) ? run.data : undefined
  useEffect(() => {
    if (!done || announced.current === done.id) return
    announced.current = done.id
    if (done.status === "succeeded") {
      const rev = done.params.applied_revision
      // nothing was pending (a decision undone meanwhile): merge and snapshot are skipped
      const noop = done.steps.some((s) => s.name === "snapshot" && s.status === "skipped")
      if (noop) toast.info(typeof rev === "number" ? t("apply.nothing", { n: rev }) : t("apply.doneNoRevision"))
      else toast.success(typeof rev === "number" ? t("apply.done", { n: rev }) : t("apply.doneNoRevision"))
    } else {
      toast.error(t("apply.failed"), { description: done.error?.slice(0, 200) || undefined })
    }
    invalidateAfterRun(qc)
    setRunId(null)
  }, [done, qc, t])

  const n = pending.data?.unapplied ?? 0
  const applying = start.isPending || (!!runId && !done)
  if (!n && !applying) return null
  return (
    <div id="apply-banner" role="region" aria-label={t("run.apply")} data-unapplied={n} data-applying={applying}
         className={cn("flex flex-col gap-3 rounded-xl border border-primary/40 bg-primary/5 p-4 sm:flex-row sm:items-center",
                       className)}>
      <div className="grid min-w-0 flex-1 gap-0.5">
        <p className="font-medium">{n ? tn("apply.banner", n) : t("run.apply")}</p>
        <p className="text-sm text-muted-foreground" aria-live="polite" id="apply-progress">
          {applying ? t("apply.running", { step: currentStep(run.data) }) : t("apply.banner.hint")}
        </p>
      </div>
      <Button id="btn-apply" disabled={applying || !n} onClick={() => start.mutate()} className="shrink-0">
        {applying ? <Loader2 className="animate-spin" aria-hidden /> : <CheckCheck aria-hidden />} {t("apply.button")}
      </Button>
    </div>
  )
}
