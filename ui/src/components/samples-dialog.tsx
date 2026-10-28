import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, Database, FileText, Loader2, PackageOpen, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, useSamples, useSources, useStatus } from "@/hooks/queries"
import {
  api, ApiError, errorMessage, type SampleConflict, type SampleLoad, type SampleSet, type Source,
} from "@/lib/api"
import { expectedTime, LAST_SAMPLE_KEY, otherSources } from "@/lib/checklist"
import { fmtBytes } from "@/lib/labels"

/** What each set is, in one line (the folder only has the files). */
const SET_BLURB: Record<string, string> = {
  quickstart: "A small set to try every step quickly: a few customer, contract and invoice tables and the policies that define them.",
  hanbit: "The whole fictional company: many related tables, documents in every supported format and scanned images (OCR).",
}

const KIND_WORD: Record<string, string> = { files: "tables", structured: "tables", database: "database", documents: "documents" }

function SetCard({ set, provider, pending, others, otherSamples, onLoad, onRemoveAndLoad }: {
  set: SampleSet
  provider?: string
  pending: boolean
  /** registered sources outside this set (built together with it) */
  others: Source[]
  /** the part of *others* that belongs to another sample set */
  otherSamples: Source[]
  onLoad: () => void
  onRemoveAndLoad: () => void
}) {
  const time = expectedTime(set, provider)
  const files = set.sources.reduce((n, s) => n + s.files, 0)
  const names = (list: Source[]) => list.map((s) => s.name).join(", ")
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
        {others.length > 0 && time && " (for this set alone)"}
      </p>
      {others.length > 0 && !set.registered && (
        <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs" data-other-sources={set.set}>
          Your other source{others.length === 1 ? "" : "s"} ({names(others)}) stay registered and will be built
          together with this set, so the build takes longer than the time above.
          {otherSamples.length > 0 && <> To build only {set.set}, remove the other sample sources first.</>}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {otherSamples.length > 0 && !set.registered ? (
          <>
            <Button size="sm" onClick={onRemoveAndLoad} disabled={pending} data-remove-load={set.set}>
              {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Trash2 aria-hidden />}
              Remove the other sample sources first
            </Button>
            <Button size="sm" variant="outline" onClick={onLoad} disabled={pending} data-load-set={set.set}>
              <PackageOpen aria-hidden /> Keep both
            </Button>
          </>
        ) : (
          <Button size="sm" variant={set.registered ? "outline" : "default"} onClick={onLoad} disabled={pending}
                  data-load-set={set.set}>
            {pending ? <Loader2 className="animate-spin" aria-hidden /> : <PackageOpen aria-hidden />}
            {set.registered ? `Check ${set.set} again` : `Load ${set.set}`}
          </Button>
        )}
      </div>
    </li>
  )
}

/** The 409 `sample_conflict` question: same-named sources with other data. */
function ConflictPanel({ set, conflicts, pending, onReplace, onCancel }: {
  set: string
  conflicts: SampleConflict[]
  pending: boolean
  onReplace: () => void
  onCancel: () => void
}) {
  return (
    <div role="alertdialog" aria-labelledby="sample-conflict-title" id="sample-conflict"
         className="grid gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
      <p id="sample-conflict-title" className="font-medium">These sources already exist and point elsewhere:</p>
      <ul className="grid gap-0.5 text-xs">
        {conflicts.map((c) => (
          <li key={c.name} data-conflict={c.name}><b>{c.name}</b>{c.kind ? ` (${KIND_WORD[c.kind] ?? c.kind})` : ""}</li>
        ))}
      </ul>
      <p className="text-xs">
        Loading the {set} samples replaces them: replacing deletes their database and the concepts
        extracted from them. Your files on disk are not touched. Replace?
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="destructive" onClick={onReplace} disabled={pending} id="btn-sample-replace">
          {pending && <Loader2 className="animate-spin" aria-hidden />} Replace and load
        </Button>
        <Button size="sm" variant="outline" onClick={onCancel} disabled={pending}>Cancel</Button>
      </div>
    </div>
  )
}

/** Lists `GET /api/samples` and registers one set (`POST /api/samples/load`), like `demo`.
    A 409 `sample_conflict` asks before re-posting with `replace: true`. */
export function SamplesDialog({ open, onOpenChange, onLoaded }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onLoaded?: (result: SampleLoad) => void
}) {
  const qc = useQueryClient()
  const samples = useSamples()
  const sources = useSources()
  const status = useStatus()
  const provider = status.data?.providers.chat_provider
  const [result, setResult] = useState<SampleLoad | null>(null)
  const [conflict, setConflict] = useState<{ set: string; conflicts: SampleConflict[] } | null>(null)
  const load = useMutation({
    mutationFn: async ({ set, replace = false, remove = [] }: { set: string; replace?: boolean; remove?: Source[] }) => {
      for (const s of remove) await api.deleteSource(s.name, s.kind === "files")
      return api.loadSamples(set, replace)
    },
    onSuccess: (r) => {
      setResult(r)
      setConflict(null)
      try { localStorage.setItem(LAST_SAMPLE_KEY, r.set) } catch { /* private mode */ }
      toast.success(`Loaded the ${r.set} samples`, { description: "Build the ontology next." })
      onLoaded?.(r)
    },
    onError: (e, { set }) => {
      if (e instanceof ApiError && e.code === "sample_conflict") {
        const list = Array.isArray(e.body?.conflicts) ? (e.body.conflicts as SampleConflict[]) : []
        setConflict({ set, conflicts: list.filter((c) => c && typeof c.name === "string") })
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: keys.sources })
      qc.invalidateQueries({ queryKey: keys.samples })
    },
  })
  const showError = load.isError && !(load.error instanceof ApiError && load.error.code === "sample_conflict")
  return (
    <Dialog open={open} onOpenChange={(o) => {
      onOpenChange(o)
      if (!o) { setResult(null); setConflict(null); load.reset() }
    }}>
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
              {samples.data.map((s) => {
                const { others, otherSamples } = otherSources(s, sources.data, samples.data)
                return (
                  <SetCard key={s.set} set={s} provider={provider} others={others} otherSamples={otherSamples}
                           pending={load.isPending && load.variables?.set === s.set}
                           onLoad={() => load.mutate({ set: s.set })}
                           onRemoveAndLoad={() => load.mutate({ set: s.set, remove: otherSamples })} />
                )
              })}
            </ul>
          )}
        {conflict && (
          <ConflictPanel set={conflict.set} conflicts={conflict.conflicts} pending={load.isPending}
                         onReplace={() => load.mutate({ set: conflict.set, replace: true })}
                         onCancel={() => { setConflict(null); load.reset() }} />
        )}
        {showError && <p role="alert" className="text-sm break-words text-destructive">{errorMessage(load.error)}</p>}
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
