import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, Hammer, History, Loader2, Play, SlidersHorizontal } from "lucide-react"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { RunSheet } from "@/components/run-sheet"
import { StatusChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { keys, useOntology, useSources } from "@/hooks/queries"
import { api, errorMessage, isNoSnapshot, type RunRequest, type Source } from "@/lib/api"
import { fmtSeconds, fmtTime, groupSteps, progressText, RUN_LABEL, runSeconds, sourceState } from "@/lib/labels"
import { KindBadge } from "@/pages/sources"

function useStartRun(onStarted: (runId: string) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RunRequest) => api.startRun(body),
    onSuccess: ({ run_id }) => {
      onStarted(run_id)
      qc.invalidateQueries({ queryKey: keys.sources })
      qc.invalidateQueries({ queryKey: keys.runs })
    },
    onError: (e, body) =>
      toast.error(`${RUN_LABEL[body.kind]} did not start`, { description: errorMessage(e) }),
  })
}

/** Build options: a subset of the sources (align, merge and snapshot still cover all of
    them) and resume (continue the latest build run). */
function BuildOptionsDialog({ open, sources, pending, onClose, onStart }: {
  open: boolean
  sources: Source[]
  pending: boolean
  onClose: () => void
  onStart: (body: RunRequest) => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [resume, setResume] = useState(false)
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <form className="grid min-w-0 gap-4" id="form-build"
              onSubmit={(e) => {
                e.preventDefault()
                onStart({ kind: "build", sources: picked.length ? picked : null, resume })
              }}>
          <DialogHeader>
            <DialogTitle>Build options</DialogTitle>
            <DialogDescription>
              The same options as <code className="rounded bg-muted px-1">uv run autoontology build</code>.
            </DialogDescription>
          </DialogHeader>
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">Rebuild only these sources (--source)</legend>
            <p className="text-xs text-muted-foreground">
              None picked: every source. The other sources keep their last results; align, merge and
              snapshot always cover all of them.
            </p>
            {sources.map((s) => (
              <div key={s.name} className="flex items-center gap-3">
                <Switch id={`opt-src-${s.name}`} data-source={s.name} checked={picked.includes(s.name)}
                        onCheckedChange={(on) => setPicked((cur) =>
                          on ? [...cur, s.name] : cur.filter((n) => n !== s.name))} />
                <Label htmlFor={`opt-src-${s.name}`} className="flex min-w-0 flex-wrap items-center gap-2 font-normal">
                  <span className="break-all">{s.name}</span> <KindBadge kind={s.kind} />
                </Label>
              </div>
            ))}
          </fieldset>
          <div className="flex items-start gap-3">
            <Switch id="opt-resume" checked={resume} onCheckedChange={setResume} />
            <Label htmlFor="opt-resume" className="grid gap-0.5 leading-snug">
              <span>Resume (--resume)</span>
              <span className="text-xs font-normal text-muted-foreground">
                Continue the latest build: steps that succeeded and whose inputs did not change are kept
              </span>
            </Label>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Close</Button>
            <Button type="submit" disabled={pending}>
              {pending ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
              Build
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Pipeline({ sources, onStarted, busy }: {
  sources: Source[]
  onStarted: (id: string) => void
  busy: boolean
}) {
  const start = useStartRun(onStarted)
  const [options, setOptions] = useState(false)
  const ontology = useOntology()
  const structured = sources.filter((s) => s.kind !== "documents").length
  const documents = sources.length - structured
  return (
    <div className="grid gap-4 md:grid-cols-2" id="pipeline">
      <Card className="gap-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><Hammer className="size-4" aria-hidden /> Build ontology</CardTitle>
          <CardDescription>
            {structured} structured and {documents} document source{documents === 1 ? "" : "s"}.
            {" "}Tables: load, ingest rows, compile business concepts. Documents: read, extract concepts,
            merge duplicates. Then match document concepts to table concepts, build the unified
            ontology and save a revision.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-wrap gap-2">
          <Button id="btn-build" disabled={busy || start.isPending}
                  onClick={() => start.mutate({ kind: "build" })}>
            {start.isPending && start.variables?.kind === "build"
              ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />} Build ontology
          </Button>
          <Button variant="outline" id="btn-build-options" disabled={busy} onClick={() => setOptions(true)}>
            <SlidersHorizontal aria-hidden /> Options
          </Button>
        </CardFooter>
      </Card>
      <Card className="gap-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><Download className="size-4" aria-hidden /> Export</CardTitle>
          <CardDescription>
            {ontology.data
              ? <>Writes revision {ontology.data.revision} as Turtle, JSON-LD, GraphML, an Apache Ossie bundle and an OKF bundle.</>
              : isNoSnapshot(ontology.error) ? "Nothing to export yet: build the ontology first."
                : "Writes the latest revision as Turtle, JSON-LD, GraphML, Ossie and OKF."}
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex flex-wrap gap-2">
          <Button variant="secondary" id="btn-export" disabled={busy || start.isPending || !ontology.data}
                  onClick={() => start.mutate({ kind: "export" })}>
            {start.isPending && start.variables?.kind === "export"
              ? <Loader2 className="animate-spin" aria-hidden /> : <Download aria-hidden />} Export
          </Button>
          <Button variant="ghost" asChild><Link to="/exports">Open exports</Link></Button>
        </CardFooter>
      </Card>
      <BuildOptionsDialog key={String(options)} open={options} sources={sources} pending={start.isPending}
                          onClose={() => setOptions(false)}
                          onStart={(body) => start.mutate(body, { onSuccess: () => setOptions(false) })} />
    </div>
  )
}

export default function BuildPage() {
  const [params, setParams] = useSearchParams()
  const runId = params.get("run")
  const open = (id: string | null) => setParams(id ? { run: id } : {}, { replace: !id })
  const sources = useSources()
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(40), refetchInterval: 10_000 })
  const busy = (runs.data ?? []).some((r) => r.status === "running" || r.status === "pending")

  return (
    <>
      <PageHeader title="Build"
                  description="One pipeline turns every registered source into the unified ontology; export writes it out." />
      <section aria-labelledby="h-actions" className="mb-8">
        <h2 id="h-actions" className="sr-only">Start</h2>
        {sources.error ? <ErrorState error={sources.error} onRetry={() => sources.refetch()} />
          : sources.isPending ? <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>
          : !sources.data.length ? (
            <EmptyState title="Nothing to build yet"
                        hint={<>Add a <Link to="/sources" className="underline underline-offset-4">source</Link> first.</>} />
          ) : (
            <>
              <Pipeline sources={sources.data} onStarted={open} busy={busy} />
              <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Sources">
                {sources.data.map((s) => (
                  <li key={s.name} className="inline-flex items-center gap-1.5">
                    <span className="font-medium text-foreground">{s.name}</span> {sourceState(s)}
                  </li>
                ))}
              </ul>
            </>
          )}
      </section>

      <section aria-labelledby="h-history">
        <h2 id="h-history" className="mb-3 text-lg font-semibold">Runs</h2>
        {runs.error ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
          : runs.isPending ? <Skeleton className="h-64" />
          : !runs.data.length ? <EmptyState icon={History} title="No runs yet"
                                            hint="Start a build above." />
          : (
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="runs-table" className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[45%] pl-4 sm:w-[32%]">Run</TableHead>
                      <TableHead className="w-28">Status</TableHead>
                      <TableHead className="hidden w-40 sm:table-cell">Started</TableHead>
                      <TableHead className="hidden pr-4 md:table-cell">Steps</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.data.map((r) => (
                      <TableRow key={r.id} data-run-id={r.id} data-kind={r.kind} tabIndex={0}
                                className="cursor-pointer"
                                aria-label={`Open run ${r.id}`}
                                onClick={() => open(r.id)}
                                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(r.id) } }}>
                        <TableCell className="pl-4 whitespace-normal">
                          <div className="font-medium">{RUN_LABEL[r.kind] ?? r.kind}</div>
                          <code className="block truncate text-xs text-muted-foreground" title={r.id}>{r.id}</code>
                        </TableCell>
                        <TableCell className="whitespace-normal">
                          <StatusChip status={r.status} />
                          {runSeconds(r, r.steps) != null && (
                            <span className="mt-1 block text-xs text-muted-foreground" data-took>
                              {fmtSeconds(runSeconds(r, r.steps))}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="hidden text-xs whitespace-normal sm:table-cell">{fmtTime(r.started_at)}</TableCell>
                        <TableCell className="hidden pr-4 whitespace-normal md:table-cell">
                          <div className="flex flex-wrap gap-1">
                            {groupSteps(r.steps).map((g) => {
                              const failed = g.steps.some((s) => s.status === "failed")
                              const running = g.steps.some((s) => s.status === "running")
                              const done = g.steps.every((s) => s.status === "succeeded" || s.status === "skipped")
                              return (
                                <StatusChip key={g.key}
                                            status={failed ? "failed" : running ? "running" : done ? "succeeded" : "pending"}
                                            label={g.title} className="max-w-full truncate" />
                              )
                            })}
                            {Object.keys(r.progress || {}).length > 0 && (
                              <span className="text-xs text-muted-foreground">{progressText(r.progress, false)}</span>
                            )}
                          </div>
                          {r.error && <p className="mt-1 line-clamp-2 text-xs break-all text-destructive">{r.error.slice(0, 200)}</p>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
      </section>
      <RunSheet runId={runId} onClose={() => open(null)} />
    </>
  )
}
