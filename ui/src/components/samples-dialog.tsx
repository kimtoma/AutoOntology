import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, Database, FileText, Loader2, PackageOpen } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, useSamples, useStatus } from "@/hooks/queries"
import { api, errorMessage, type SampleLoad, type SampleSet } from "@/lib/api"
import { expectedTime, LAST_SAMPLE_KEY } from "@/lib/checklist"
import { fmtBytes } from "@/lib/labels"

/** What each set is, in one line (the folder only has the files). */
const SET_BLURB: Record<string, string> = {
  quickstart: "A small set to try every step quickly: a few customer, contract and invoice tables and the policies that define them.",
  hanbit: "The whole fictional company: many related tables, documents in every supported format and scanned images (OCR).",
}

function SetCard({ set, provider, pending, onLoad }: {
  set: SampleSet
  provider?: string
  pending: boolean
  onLoad: () => void
}) {
  const time = expectedTime(set, provider)
  const files = set.sources.reduce((n, s) => n + s.files, 0)
  return (
    <li className="grid gap-2 rounded-lg border p-3" data-sample-set={set.set}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{set.set}</span>
        {set.registered && <Badge variant="outline" className="font-normal"><CheckCircle2 aria-hidden /> Loaded</Badge>}
      </div>
      {SET_BLURB[set.set] && <p className="text-xs text-muted-foreground">{SET_BLURB[set.set]}</p>}
      <ul className="grid gap-1 text-xs">
        {set.sources.map((s) => (
          <li key={s.name} className="flex flex-wrap items-center gap-x-2 gap-y-0.5" data-sample-source={s.name}>
            {s.kind === "documents" ? <FileText className="size-3.5" aria-hidden /> : <Database className="size-3.5" aria-hidden />}
            <span className="font-medium">{s.name}</span>
            <span className="text-muted-foreground">
              {s.files} {s.kind === "documents" ? "document" : "table file"}{s.files === 1 ? "" : "s"} · {fmtBytes(s.bytes)}
            </span>
            {s.formats?.length ? <span className="text-muted-foreground">· {s.formats.join(" ")}</span> : null}
          </li>
        ))}
      </ul>
      <p className="text-xs" data-expected-time>
        {files} files. Expected build time{provider ? ` with ${provider}` : ""}: <b>{time ?? "unknown"}</b>
      </p>
      <div>
        <Button size="sm" onClick={onLoad} disabled={pending} data-load-set={set.set}>
          {pending ? <Loader2 className="animate-spin" aria-hidden /> : <PackageOpen aria-hidden />}
          {set.registered ? "Load again" : "Load"} {set.set}
        </Button>
      </div>
    </li>
  )
}

/** Lists `GET /api/samples` and registers one set (`POST /api/samples/load`), like `demo`. */
export function SamplesDialog({ open, onOpenChange, onLoaded }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onLoaded?: (result: SampleLoad) => void
}) {
  const qc = useQueryClient()
  const samples = useSamples()
  const status = useStatus()
  const provider = status.data?.providers.chat_provider
  const [result, setResult] = useState<SampleLoad | null>(null)
  const load = useMutation({
    mutationFn: (set: string) => api.loadSamples(set),
    onSuccess: (r) => {
      setResult(r)
      try { localStorage.setItem(LAST_SAMPLE_KEY, r.set) } catch { /* private mode */ }
      toast.success(`Loaded the ${r.set} samples`, { description: "Build the ontology next." })
      qc.invalidateQueries({ queryKey: keys.sources })
      qc.invalidateQueries({ queryKey: keys.samples })
      onLoaded?.(r)
    },
  })
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setResult(null); load.reset() } }}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg" id="samples-dialog">
        <DialogHeader>
          <DialogTitle>Load sample data</DialogTitle>
          <DialogDescription>
            Ready-made tables and documents (in Korean) of a fictional company. Loading only registers
            them as sources; nothing is sent to a model until you build.
          </DialogDescription>
        </DialogHeader>
        {samples.error ? <ErrorState error={samples.error} onRetry={() => samples.refetch()} />
          : samples.isPending ? <Skeleton className="h-40" />
          : !samples.data.length ? (
            <p className="text-sm text-muted-foreground">
              No sample sets found next to the service (samples/). Update your checkout with <code>git pull</code>.
            </p>
          ) : (
            <ul className="grid gap-3" id="sample-sets">
              {samples.data.map((s) => (
                <SetCard key={s.set} set={s} provider={provider}
                         pending={load.isPending && load.variables === s.set}
                         onLoad={() => load.mutate(s.set)} />
              ))}
            </ul>
          )}
        {load.isError && <p role="alert" className="text-sm break-words text-destructive">{errorMessage(load.error)}</p>}
        {result && (
          <div className="grid gap-1 rounded-md border border-success/30 bg-success/5 p-3 text-sm" id="samples-result" role="status">
            <p className="font-medium">Sources ready:</p>
            <ul className="grid gap-0.5 text-xs">
              {result.sources.map((s) => (
                <li key={s.name} data-loaded-source={s.name}><b>{s.name}</b> · {s.action}</li>
              ))}
            </ul>
            {result.messages.map((m) => <p key={m} className="text-xs text-muted-foreground break-words">{m}</p>)}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{result ? "Done" : "Close"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
