import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Loader2 } from "lucide-react"
import { ErrorState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { api, errorMessage } from "@/lib/api"
import { fmtTime } from "@/lib/labels"
import { useT } from "@/i18n"
import { useCanUse, useIdentity } from "@/lib/identity"

export function TraceButton({ traceId }: { traceId?: string }) {
  const t = useT(), qc = useQueryClient()
  const canDelete = useCanUse("results.delete")
  const [open, setOpen] = useState(false), [confirm, setConfirm] = useState(false)
  const record = useQuery({ queryKey: ["usage-trace", traceId], queryFn: () => api.playground.trace(traceId!), enabled: open && !!traceId, refetchInterval: q => q.state.data?.status === "running" ? 2000 : false })
  const remove = useMutation({ mutationFn: () => api.playground.forgetTrace(traceId!), onSuccess: () => { qc.invalidateQueries({ queryKey: ["usage-traces"] }); setConfirm(false) } })
  const trace = record.data, data = trace?.data
  if (!traceId) return null
  return <>
    <Button type="button" variant="ghost" size="sm" data-trace-id={traceId} onClick={() => setOpen(true)}>{t("usage.record")}</Button>
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader><SheetTitle>{t("usage.record")}</SheetTitle><SheetDescription>{t("usage.savedHint")}</SheetDescription></SheetHeader>
        <div className="space-y-5 px-4 pb-6 text-sm">
          {record.isPending && <Loader2 aria-label={t("usage.loading")} className="animate-spin" />}
          {record.error && <ErrorState error={record.error} onRetry={() => record.refetch()} />}
          {remove.isSuccess ? <p role="status">{t("usage.deleted")}</p> : trace && data && <>
            <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{trace.kind}</Badge><Badge variant="secondary">{t(`usage.${trace.status}`)}</Badge><span>r{trace.revision}</span></div>
            <p className="text-muted-foreground">{fmtTime(trace.started_at)} · {data.duration_ms == null ? "—" : `${Math.round(data.duration_ms)} ms`}</p>
            <p className="text-xs text-muted-foreground">{t("usage.timingHint")}</p>
            {data.binding.legacy && <p className="text-warning">{t("usage.legacy")}</p>}
            <section><h3 className="font-medium">{t("usage.request")}</h3><p className="mt-2 whitespace-pre-wrap">{data.request.question ?? data.request.query}</p></section>
            <section><h3 className="font-medium">{t("usage.output")}</h3>
              {data.outcome.answer && <p className="mt-2 whitespace-pre-wrap">{data.outcome.answer}</p>}
              {data.outcome.error && <p role="alert" className="mt-2 text-destructive">{data.outcome.error}</p>}
              {!!(data.outcome.result || data.outcome.events) && <pre className="mt-2 max-h-80 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(data.outcome.result ?? data.outcome.events, null, 2)}</pre>}
              {trace.kind === "sql" && trace.status === "succeeded" && <Button asChild variant="outline" size="sm" className="mt-2">
                <a href={`/api/playground/traces/${trace.id}/result.csv`} download="query-result.csv">CSV</a>
              </Button>}
            </section>
            {data.context.data_time === "live_at_execution" && <p className="text-xs text-muted-foreground">{t("usage.liveData")}</p>}
            {!!data.context.hits?.length && <section><h3 className="font-medium">{t("usage.evidence")}</h3><ul className="mt-2 divide-y">{data.context.hits.map((hit, i) => <li key={i} className="py-2"><span className="text-xs text-muted-foreground">[{hit.ref}]</span><p className="mt-1 whitespace-pre-wrap">{hit.text}</p></li>)}</ul></section>}
            <details><summary className="cursor-pointer">{t("usage.details")}</summary><pre className="mt-2 max-h-96 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(trace, null, 2)}</pre></details>
            {trace.status !== "running" && canDelete && <div className="border-t pt-4">
              <p className="mb-2 text-xs text-muted-foreground">{t("usage.deleteHint")}</p>
              {confirm ? <div className="flex gap-2"><Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>{t("usage.confirmDelete")}</Button><Button variant="outline" onClick={() => setConfirm(false)}>{t("common.cancel")}</Button></div> : <Button variant="outline" onClick={() => setConfirm(true)}>{t("usage.delete")}</Button>}
              {remove.error && <p role="alert">{errorMessage(remove.error)}</p>}
            </div>}
          </>}
        </div>
      </SheetContent>
    </Sheet>
  </>
}

export function UsageHistory() {
  const t = useT()
  const identity = useIdentity(), canInspect = useCanUse("identity.admin")
  const [allUsers, setAllUsers] = useState(false)
  const inspect = !!identity && canInspect && allUsers
  const traces = useQuery({ queryKey: ["usage-traces", inspect], queryFn: () => api.playground.traces(inspect), refetchInterval: 5000 })
  return <section>
    {identity && <div className="mb-5 space-y-3 border-y py-4 text-sm"><p>{t(inspect ? "usage.adminScope" : "usage.ownScope")}</p>{canInspect && <label className="flex items-center gap-2"><input type="checkbox" checked={inspect} onChange={event => setAllUsers(event.target.checked)} />{t("usage.inspectAll")}</label>}</div>}
    <p className="mb-4 text-sm text-muted-foreground">{t("usage.savedHint")}</p>
    {traces.error ? <ErrorState error={traces.error} onRetry={() => traces.refetch()} /> : <ul className="divide-y">{traces.data?.items.map(trace => <li key={trace.id} className="flex flex-wrap items-center justify-between gap-3 py-4 text-sm"><div><p>{trace.kind} · r{trace.revision} · {t(`usage.${trace.status}`)}</p>{inspect && <p className="mt-1 text-xs">{trace.owner?.name ?? t("usage.ownerUnknown")}</p>}<p className="mt-1 text-xs text-muted-foreground">{fmtTime(trace.started_at)}</p></div><TraceButton traceId={trace.id} /></li>)}</ul>}
    {!traces.isPending && !traces.error && !traces.data?.items.length && <p className="py-6 text-sm text-muted-foreground">{t("usage.empty")}</p>}
  </section>
}
