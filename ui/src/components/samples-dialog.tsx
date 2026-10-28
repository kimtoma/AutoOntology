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
import { t, tn, tx, useT, type MessageKey } from "@/i18n"

/** What each set is, in one line (the folder only has the files). */
const SET_BLURB: Record<string, MessageKey> = {
  quickstart: "samples.blurb.quickstart",
  hanbit: "samples.blurb.hanbit",
}

const KIND_WORD: Record<string, MessageKey> = {
  files: "samples.kind.tables", structured: "samples.kind.tables", database: "samples.kind.database",
  documents: "samples.kind.documents",
}
const kindWord = (kind: string) => (KIND_WORD[kind] ? t(KIND_WORD[kind]) : kind)
const ACTION: Record<string, MessageKey> = {
  created: "samples.action.created", kept: "samples.action.kept", replaced: "samples.action.replaced",
}

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
  const t = useT()
  const time = expectedTime(set, provider)
  const files = set.sources.reduce((n, s) => n + s.files, 0)
  const names = (list: Source[]) => list.map((s) => s.name).join(", ")
  return (
    <li className="grid gap-2 rounded-lg border p-3" data-sample-set={set.set}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{set.set}</span>
        {set.registered && <Badge variant="outline" className="font-normal"><CheckCircle2 aria-hidden /> {t("samples.loaded")}</Badge>}
      </div>
      {SET_BLURB[set.set] && <p className="text-xs text-muted-foreground">{t(SET_BLURB[set.set])}</p>}
      <ul className="grid gap-1 text-xs">
        {set.sources.map((s) => (
          <li key={s.name} className="flex flex-wrap items-center gap-x-2 gap-y-0.5" data-sample-source={s.name}>
            {s.kind === "documents" ? <FileText className="size-3.5" aria-hidden /> : <Database className="size-3.5" aria-hidden />}
            <span className="font-medium">{s.name}</span>
            <span className="text-muted-foreground">
              {tn(s.kind === "documents" ? "samples.docs" : "samples.tables", s.files)} · {fmtBytes(s.bytes)}
            </span>
            {s.formats?.length ? <span className="text-muted-foreground">· {s.formats.join(" ")}</span> : null}
          </li>
        ))}
      </ul>
      <p className="text-xs" data-expected-time>
        {tx(provider ? "samples.expectedWith" : "samples.expected",
            { files, provider: provider ?? "", time: <b key="time">{time ?? t("samples.unknown")}</b> })}
        {others.length > 0 && time && t("samples.alone")}
      </p>
      {others.length > 0 && !set.registered && (
        <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs" data-other-sources={set.set}>
          {tn("samples.others", others.length, { names: names(others) })}
          {otherSamples.length > 0 && t("samples.others.removeHint", { set: set.set })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {otherSamples.length > 0 && !set.registered ? (
          <>
            <Button size="sm" onClick={onRemoveAndLoad} disabled={pending} data-remove-load={set.set}>
              {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Trash2 aria-hidden />}
              {t("samples.removeFirst")}
            </Button>
            <Button size="sm" variant="outline" onClick={onLoad} disabled={pending} data-load-set={set.set}>
              <PackageOpen aria-hidden /> {t("samples.keepBoth")}
            </Button>
          </>
        ) : (
          <Button size="sm" variant={set.registered ? "outline" : "default"} onClick={onLoad} disabled={pending}
                  data-load-set={set.set}>
            {pending ? <Loader2 className="animate-spin" aria-hidden /> : <PackageOpen aria-hidden />}
            {t(set.registered ? "samples.checkAgain" : "samples.load", { set: set.set })}
          </Button>
        )}
      </div>
    </li>
  )
}

/** Confirms deleting the other sample set's sources before loading *set*. */
function RemovePanel({ set, remove, pending, onConfirm, onCancel }: {
  set: string
  remove: Source[]
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const t = useT()
  return (
    <div role="alertdialog" aria-labelledby="sample-remove-title" id="sample-remove"
         className="grid gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
      <p id="sample-remove-title" className="font-medium">
        {t("samples.remove.title", { names: remove.map((s) => s.name).join(", ") })}
      </p>
      <ul className="grid gap-0.5 text-xs">
        {remove.map((s) => (
          <li key={s.name} data-remove-source={s.name}><b>{s.name}</b> ({kindWord(s.kind)})</li>
        ))}
      </ul>
      <p className="text-xs">{t("samples.remove.body", { set })}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="destructive" onClick={onConfirm} disabled={pending} id="btn-sample-remove">
          {pending && <Loader2 className="animate-spin" aria-hidden /> } {t("samples.remove.confirm")}
        </Button>
        <Button size="sm" variant="outline" onClick={onCancel} disabled={pending}>{t("common.cancel")}</Button>
      </div>
    </div>
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
  const t = useT()
  return (
    <div role="alertdialog" aria-labelledby="sample-conflict-title" id="sample-conflict"
         className="grid gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
      <p id="sample-conflict-title" className="font-medium">{t("samples.conflict.title")}</p>
      <ul className="grid gap-0.5 text-xs">
        {conflicts.map((c) => (
          <li key={c.name} data-conflict={c.name}><b>{c.name}</b>{c.kind ? ` (${kindWord(c.kind)})` : ""}</li>
        ))}
      </ul>
      <p className="text-xs">
        {t("samples.conflict.body", { set })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="destructive" onClick={onReplace} disabled={pending} id="btn-sample-replace">
          {pending && <Loader2 className="animate-spin" aria-hidden />} {t("samples.conflict.confirm")}
        </Button>
        <Button size="sm" variant="outline" onClick={onCancel} disabled={pending}>{t("common.cancel")}</Button>
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
  const t = useT()
  const qc = useQueryClient()
  const samples = useSamples()
  const sources = useSources()
  const status = useStatus()
  const provider = status.data?.providers.chat_provider
  const [result, setResult] = useState<SampleLoad | null>(null)
  const [conflict, setConflict] = useState<{ set: string; conflicts: SampleConflict[] } | null>(null)
  const [removal, setRemoval] = useState<{ set: string; remove: Source[] } | null>(null)
  const load = useMutation({
    mutationFn: async ({ set, replace = false, remove = [] }: { set: string; replace?: boolean; remove?: Source[] }) => {
      for (const s of remove) await api.deleteSource(s.name, s.kind === "files")
      return api.loadSamples(set, replace)
    },
    onSuccess: (r) => {
      setResult(r)
      setConflict(null)
      setRemoval(null)
      try { localStorage.setItem(LAST_SAMPLE_KEY, r.set) } catch { /* private mode */ }
      toast.success(t("samples.toast", { set: r.set }), { description: t("samples.toast.next") })
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
      if (!o) { setResult(null); setConflict(null); setRemoval(null); load.reset() }
    }}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg" id="samples-dialog">
        <DialogHeader>
          <DialogTitle>{t("samples.title")}</DialogTitle>
          <DialogDescription>
            {t("samples.description")}
          </DialogDescription>
        </DialogHeader>
        {samples.error ? <ErrorState error={samples.error} onRetry={() => samples.refetch()} />
          : samples.isPending ? <Skeleton className="h-40" />
          : !samples.data.length ? (
            <p className="text-sm text-muted-foreground">
              {tx("samples.none", { cmd: <code key="cmd">git pull</code> })}
            </p>
          ) : (
            <ul className="grid gap-3" id="sample-sets">
              {samples.data.map((s) => {
                const { others, otherSamples } = otherSources(s, sources.data, samples.data)
                return (
                  <SetCard key={s.set} set={s} provider={provider} others={others} otherSamples={otherSamples}
                           pending={load.isPending && load.variables?.set === s.set}
                           onLoad={() => load.mutate({ set: s.set })}
                           onRemoveAndLoad={() => { setConflict(null); setRemoval({ set: s.set, remove: otherSamples }) }} />
                )
              })}
            </ul>
          )}
        {removal && (
          <RemovePanel set={removal.set} remove={removal.remove} pending={load.isPending}
                       onConfirm={() => load.mutate({ set: removal.set, remove: removal.remove })}
                       onCancel={() => setRemoval(null)} />
        )}
        {conflict && (
          <ConflictPanel set={conflict.set} conflicts={conflict.conflicts} pending={load.isPending}
                         onReplace={() => load.mutate({ set: conflict.set, replace: true })}
                         onCancel={() => { setConflict(null); load.reset() }} />
        )}
        {showError && <p role="alert" className="text-sm break-words text-destructive">{errorMessage(load.error)}</p>}
        {result && (
          <div className="grid gap-1 rounded-md border border-success/30 bg-success/5 p-3 text-sm" id="samples-result" role="status">
            <p className="font-medium">{t("samples.result")}</p>
            <ul className="grid gap-0.5 text-xs">
              {result.sources.map((s) => (
                <li key={s.name} data-loaded-source={s.name}><b>{s.name}</b> · {ACTION[s.action] ? t(ACTION[s.action]) : s.action}</li>
              ))}
            </ul>
            {result.messages.map((m) => <p key={m} className="text-xs text-muted-foreground break-words">{m}</p>)}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{result ? t("common.done") : t("common.close")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
