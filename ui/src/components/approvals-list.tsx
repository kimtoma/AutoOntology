import { useQuery } from "@tanstack/react-query"
import { ShieldCheck } from "lucide-react"
import { useState } from "react"

import { ActionRunSheet, ApprovalCard } from "@/components/action-run"
import { useRunDecisions } from "@/hooks/use-run-decisions"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, REVIEW_POLL_MS, retryServerErrors } from "@/hooks/queries"
import { fmtNumber, useT } from "@/i18n"
import { api } from "@/lib/api"

const QUERY = { status: "pending_approval", limit: 200 }

/** Review → Approvals: the pending action runs the caller may approve or reject. */
export function ApprovalsList() {
  const t = useT()
  const runs = useQuery({
    queryKey: keys.actionRuns(QUERY), queryFn: () => api.actions.runs(QUERY), retry: retryServerErrors,
    refetchInterval: REVIEW_POLL_MS,
  })
  const decisions = useRunDecisions()
  const [open, setOpen] = useState<string | null>(null)
  if (runs.error) return <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
  if (!runs.data) return <div className="grid gap-3">{[0, 1].map((i) => <Skeleton key={i} className="h-28" />)}</div>
  const mine = runs.data.filter((r) => r.can_approve)
  const others = runs.data.length - mine.length
  return (
    <>
      {!mine.length ? (
        <EmptyState icon={ShieldCheck} title={t("review.approvals.empty")}
                    hint={others ? t("review.approvals.others", { n: fmtNumber(others) }) : undefined} />
      ) : (
        <>
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-3" id="approvals-list">
            {mine.map((r) => <ApprovalCard key={r.id} run={r} onOpen={setOpen} decisions={decisions} />)}
          </ul>
          {others > 0 && <p className="mt-3 text-xs text-muted-foreground" id="approvals-others">{t("review.approvals.others", { n: fmtNumber(others) })}</p>}
        </>
      )}
      <ActionRunSheet runId={open} onClose={() => setOpen(null)} />
    </>
  )
}
