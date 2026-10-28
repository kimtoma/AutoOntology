import { useQueryClient } from "@tanstack/react-query"
import { Loader2, RotateCw, Square } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { StatusChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import {
  Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { api, errorMessage, type Progress, type Run, type RunStatus, type Step } from "@/lib/api"
import { fmtDuration, fmtTime, groupSteps, progressText, RUN_LABEL, STATUS_LABEL, stepLabel } from "@/lib/labels"

/** Append *text* to the log, keeping it scrolled to the bottom when it already was. */
function appendLog(el: HTMLPreElement, text: string) {
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8
  el.appendChild(document.createTextNode(text))
  if (atBottom) el.scrollTop = el.scrollHeight
}

function clearLog(el: HTMLPreElement) {
  el.replaceChildren()
}

interface Live {
  run: Run
  steps: Step[]
  progress: Progress
  status: RunStatus
  ended: boolean
}

/**
 * Live view of one run: steps, progress and the log from `GET /runs/{id}/events` (SSE).
 * Each log line's id is a byte offset: on a reconnect EventSource sends Last-Event-ID and
 * the server resumes there; with no line seen yet the server replays from the start, so the
 * log is cleared on (re)open in that case. Lines are batched into one text node per frame.
 */
function useRunStream(runId: string, logEl: HTMLPreElement | null) {
  const qc = useQueryClient()
  const [live, setLive] = useState<Live | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const retry = () => {
    setLive(null)
    setError(null)
    setStreamError(null)
    if (logEl) clearLog(logEl)
    setAttempt((n) => n + 1)
  }

  useEffect(() => {
    if (!logEl) return
    let ended = false
    let closed = false
    let es: EventSource | null = null
    let lastLogId = ""
    let pending: string[] = []
    let frame = 0

    const flush = () => {
      frame = 0
      appendLog(logEl, pending.join(""))
      pending = []
    }
    const appendLine = (text: string) => {
      pending.push(text + "\n")
      if (!frame) frame = requestAnimationFrame(flush)
    }

    api.run(runId).then((run) => {
      if (closed) return
      setLive({ run, steps: run.steps, progress: run.progress, status: run.status, ended: false })
      es = new EventSource(api.runEventsUrl(runId))
      es.onopen = () => {
        if (!lastLogId) { clearLog(logEl); pending = [] }
      }
      es.addEventListener("log", (e) => {
        const ev = e as MessageEvent<string>
        if (ev.lastEventId) lastLogId = ev.lastEventId
        appendLine(ev.data)
      })
      es.addEventListener("step", (e) => {
        const step = JSON.parse((e as MessageEvent<string>).data) as Step
        setLive((cur) => {
          if (!cur) return cur
          const steps = [...cur.steps]
          const i = steps.findIndex((s) => s.name === step.name)
          if (i >= 0) steps[i] = step
          else steps.push(step)
          return { ...cur, steps }
        })
      })
      es.addEventListener("progress", (e) => {
        const progress = JSON.parse((e as MessageEvent<string>).data) as Progress
        setLive((cur) => cur && { ...cur, progress })
      })
      es.addEventListener("status", (e) => {
        const s = JSON.parse((e as MessageEvent<string>).data) as { status: RunStatus; error?: string }
        setLive((cur) => cur && { ...cur, status: s.status })
        if (s.error) appendLine(`error: ${s.error}`)
      })
      es.addEventListener("end", () => {
        ended = true
        es?.close()
        if (!lastLogId) {
          appendLine("This run has no log file (started from the CLI). Full log: data/logs/jobs.log")
        }
        setLive((cur) => cur && { ...cur, ended: true })
        qc.invalidateQueries({ queryKey: keys.runs })
        qc.invalidateQueries({ queryKey: keys.sources })
        qc.invalidateQueries({ queryKey: keys.exports })
        // a build saves a new snapshot: every ontology view follows it
        qc.invalidateQueries({ queryKey: keys.ontology })
        qc.invalidateQueries({ queryKey: keys.graph })
        qc.invalidateQueries({ queryKey: ["concepts"] })
      })
      // EventSource reconnects by itself (sending Last-Event-ID); the end event closes it.
      // CLOSED without an end event = the server refused the stream (404/500): no retries.
      es.onerror = () => {
        if (!closed && !ended && es?.readyState === EventSource.CLOSED) {
          setStreamError("Could not open the live log stream (server error). Try again or read data/logs/jobs.log.")
        }
      }
    }).catch((err) => {
      if (!closed) setError(errorMessage(err))
    })

    return () => {
      closed = true
      es?.close()
      if (frame) cancelAnimationFrame(frame)
    }
  }, [runId, logEl, qc, attempt])

  return { live, error, streamError, retry }
}

/** " · sources a, b · resume" from the run params (build) or " · revision 2 · all" (export). */
function runScope(run: Run): string {
  const p = run.params
  if (run.kind === "export") {
    return `${p.revision ? ` · revision ${p.revision}` : ""}${p.format ? ` · ${p.format}` : ""}`
  }
  const sources = Array.isArray(p.sources) && p.sources.length ? ` · ${p.sources.join(", ")}` : " · all sources"
  return `${sources}${p.resume ? " · resume" : ""}`
}

/** The run's steps grouped as the build runs them: `structured:<name>`, `documents:<name>`,
    `align`, `merge`, `snapshot`. A step still pending in a finished run did not run. */
export function StepList({ steps, ended }: { steps: Step[]; ended: boolean }) {
  return (
    <ol id="live-steps" aria-label="Steps" className="grid gap-2">
      {groupSteps(steps).map((g) => (
        <li key={g.key} data-group={g.key} className="grid gap-1 rounded-md border px-3 py-2">
          <span className="text-xs font-medium break-all">{g.title}</span>
          <div className="flex flex-wrap gap-1.5">
            {g.steps.map((s) => {
              const notRun = ended && s.status === "pending"
              const took = fmtDuration(s.started_at, s.finished_at)
              return (
                <StatusChip key={s.name} status={notRun ? "skipped" : s.status}
                            label={`${stepLabel(s.short)} · ${notRun ? "not run" : STATUS_LABEL[s.status] ?? s.status}${took ? ` · ${took}` : ""}`} />
              )
            })}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function RunSheet({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  return (
    <Sheet open={!!runId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 sm:max-w-xl" id="live" aria-describedby="live-desc"
                    focusSelf>
        {runId && <RunSheetBody key={runId} runId={runId} />}
      </SheetContent>
    </Sheet>
  )
}

function RunSheetBody({ runId }: { runId: string }) {
  const [logEl, setLogEl] = useState<HTMLPreElement | null>(null)
  const { live, error, streamError, retry } = useRunStream(runId, logEl)
  const [cancelling, setCancelling] = useState(false)
  const announced = useRef(false)

  useEffect(() => {
    if (!live?.ended || announced.current) return
    announced.current = true
    if (live.status === "failed") {
      toast.error(`${RUN_LABEL[live.run.kind] ?? live.run.kind} failed`, { description: live.run.error?.slice(0, 200) ?? undefined })
    }
  }, [live])

  const cancel = async () => {
    setCancelling(true)
    try {
      await api.cancelRun(runId)
      if (logEl) appendLog(logEl, "cancel requested: stops after the current step\n")
      toast("Cancel requested", { description: "The run stops after the current step" })
    } catch (err) {
      toast.error("Cancel failed", { description: errorMessage(err) })
    } finally {
      setCancelling(false)
    }
  }

  const canCancel = !!live && live.run.managed && live.status === "running" && !live.ended
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {live ? `${RUN_LABEL[live.run.kind] ?? live.run.kind} run` : "Run"}
          {live && <StatusChip status={live.status} />}
        </SheetTitle>
        <SheetDescription id="live-desc" className="break-all">
          {live ? `${live.run.id}${runScope(live.run)}` : runId}
        </SheetDescription>
        {live && <p className="text-xs text-muted-foreground">Started {fmtTime(live.run.started_at)}</p>}
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        {(error || streamError) && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/30
                                       bg-destructive/5 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 break-words text-destructive">
              {error ? `Could not load the run: ${error}` : streamError}
            </span>
            <Button size="sm" variant="outline" onClick={retry}>
              <RotateCw aria-hidden /> Try again
            </Button>
          </div>
        )}
        {!live && !error && <Skeleton className="h-6 w-2/3" />}
        {live && (
          <>
            <StepList steps={live.steps} ended={live.ended || live.status !== "running"} />
            <p id="live-progress" className="text-xs text-muted-foreground" role="status" aria-live="polite">
              {progressText(live.progress)}
            </p>
            <span id="live-status" className="sr-only" data-status={live.status} data-ended={live.ended}
                  role="status" aria-live="polite">
              {STATUS_LABEL[live.status] ?? live.status}
            </span>
          </>
        )}
        <pre ref={setLogEl} id="live-log" role="log" aria-live="off" aria-label="Run log"
             tabIndex={0}
             className="min-h-48 flex-1 overflow-auto rounded-md border bg-muted/40 p-3 font-mono
                        text-xs leading-relaxed whitespace-pre-wrap break-all outline-none
                        focus-visible:ring-[3px] focus-visible:ring-ring/50" />
      </div>
      {canCancel && (
        <SheetFooter className="border-t">
          <Button variant="outline" onClick={cancel} disabled={cancelling} id="btn-cancel">
            {cancelling ? <Loader2 className="animate-spin" aria-hidden /> : <Square aria-hidden />}
            Cancel
          </Button>
        </SheetFooter>
      )}
    </>
  )
}
