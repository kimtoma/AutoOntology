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
import { Code } from "@/components/provider-card"
import { useSamples, useSources, useStatus } from "@/hooks/queries"
import { useNow } from "@/hooks/use-now"
import { expectedTime, sampleSetFor } from "@/lib/checklist"
import {
  fmtSeconds, fmtTime, groupSteps, humanStep, progressText, RUN_LABEL, runSeconds, STATUS_LABEL,
  stepFailure, stepLabel, stepSeconds,
} from "@/lib/labels"
import { t, useT } from "@/i18n"

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
  /** the error of the latest `status` event (the run's own `error` is the snapshot before the stream) */
  error: string | null
  ended: boolean
}

/** The JSON payload of an SSE event; null (the update is dropped) when a frame is malformed. */
function payload<T>(e: Event): T | null {
  try {
    return JSON.parse((e as MessageEvent<string>).data) as T
  } catch {
    return null
  }
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
    let loggedError = ""  // a reconnect re-sends the status event: log its error once
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
      setLive({ run, steps: run.steps, progress: run.progress, status: run.status, error: run.error, ended: false })
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
        const step = payload<Step>(e)
        if (!step) return
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
        const progress = payload<Progress>(e)
        if (!progress) return
        setLive((cur) => cur && { ...cur, progress })
      })
      es.addEventListener("status", (e) => {
        const s = payload<{ status: RunStatus; error?: string | null }>(e)
        if (!s) return
        setLive((cur) => cur && { ...cur, status: s.status, error: s.error || cur.error })
        if (s.error && s.error !== loggedError) {
          loggedError = s.error
          appendLine(`error: ${s.error}`)
        }
      })
      es.addEventListener("end", () => {
        ended = true
        es?.close()
        if (!lastLogId) {
          appendLine(t("runSheet.noLog"))
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
          setStreamError(t("runSheet.streamError"))
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
    return `${p.revision ? t("runSheet.scope.revision", { revision: String(p.revision) }) : ""}${p.format ? ` · ${p.format === "all" ? t("runSheet.scope.allFormats") : p.format}` : ""}`
  }
  const sources = Array.isArray(p.sources) && p.sources.length ? ` · ${p.sources.join(", ")}` : t("runSheet.scope.all")
  return `${sources}${p.resume ? t("runSheet.scope.resume") : ""}`
}

/** The run's steps grouped as the build runs them: `structured:<name>`, `documents:<name>`,
    `align`, `merge`, `snapshot`, in plain words, with the time each took. A step still
    pending in a finished run did not run; a failed one shows its error and remedy. */
export function StepList({ steps, ended }: { steps: Step[]; ended: boolean }) {
  const t = useT()
  const now = useNow(!ended && steps.some((s) => s.status === "running"))
  return (
    <ol id="live-steps" aria-label={t("runSheet.steps")} className="grid gap-2">
      {groupSteps(steps).map((g) => (
        <li key={g.key} data-group={g.key} className="grid gap-1 rounded-md border px-3 py-2">
          <span className="flex flex-wrap items-baseline gap-x-2 text-xs font-medium">
            <span className="break-words">{g.title}</span>
            <code className="font-mono text-[11px] font-normal break-all text-muted-foreground" data-step-id={g.key}>{g.key}</code>
          </span>
          <div className="flex flex-wrap gap-1.5">
            {g.steps.map((s) => {
              const notRun = ended && s.status === "pending"
              const took = notRun ? "" : fmtSeconds(stepSeconds(s, now))
              const own = stepLabel(s.short, g.key)
              const label = [own, notRun ? t("runSheet.notRun") : STATUS_LABEL[s.status] ?? s.status, took]
                .filter(Boolean).join(" · ")
              // the step's own id next to its plain label (a top step's id is the group's)
              const id = g.key === "align" || s.short === g.key ? null : s.short
              return (
                <span key={s.name} data-step={s.name} title={`${humanStep(s.name)} (${s.name})`} className="max-w-full">
                  <StatusChip status={notRun ? "skipped" : s.status}
                              label={<>{label}{id && <code className="font-mono text-[10px] opacity-60" data-step-id={s.name}>{id}</code>}</>}
                              className="max-w-full whitespace-normal" />
                </span>
              )
            })}
          </div>
          {g.steps.map((s) => {
            const f = stepFailure(s)
            return f && (
              <div key={`${s.name}-error`} className="grid gap-1 text-xs" data-step-error={s.name}>
                {f.error && <p className="break-words text-destructive">{humanStep(s.name)}: {f.error}</p>}
                {f.remedies.map((r) => <p key={r}>{t("common.fix")} <Code>{r}</Code></p>)}
              </div>
            )
          })}
        </li>
      ))}
    </ol>
  )
}

/** "Expected: ~5 min with claude (estimate), quickstart sample set" when the run covers only
    the sources of one sample set. */
function EtaNote({ run }: { run: Run }) {
  const t = useT()
  const samples = useSamples()
  const sources = useSources()
  const status = useStatus()
  if (run.kind !== "build" || !samples.data || !sources.data) return null
  const picked = Array.isArray(run.params.sources) && run.params.sources.length
    ? (run.params.sources as string[]) : sources.data.map((s) => s.name)
  const set = sampleSetFor(picked, samples.data)
  const provider = status.data?.providers.chat_provider
  const time = expectedTime(set, provider)
  if (!set || !time) return null
  return (
    <p className="text-xs text-muted-foreground" id="live-eta">
      {t("runSheet.eta", { set: set.set, provider, time })}
    </p>
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

function RunTimes({ live }: { live: Live }) {
  const running = !live.ended && ["pending", "running"].includes(live.status)
  const now = useNow(running)
  const total = runSeconds(live.run, live.steps, running ? now : undefined)
  const t = useT()
  return (
    <p className="text-xs text-muted-foreground" id="live-total">
      {t("runSheet.started", { date: fmtTime(live.run.started_at) })}
      {total != null && t(running ? "runSheet.runningFor" : "runSheet.took", { time: fmtSeconds(total) })}
    </p>
  )
}

function RunSheetBody({ runId }: { runId: string }) {
  const t = useT()
  const [logEl, setLogEl] = useState<HTMLPreElement | null>(null)
  const { live, error, streamError, retry } = useRunStream(runId, logEl)
  const [cancelling, setCancelling] = useState(false)
  const announced = useRef(false)

  useEffect(() => {
    if (!live?.ended || announced.current) return
    announced.current = true
    if (live.status === "failed") {
      toast.error(t("runSheet.failed", { kind: RUN_LABEL[live.run.kind] ?? live.run.kind }), { description: live.error?.slice(0, 200) || undefined })
    }
  }, [live, t])

  const cancel = async () => {
    setCancelling(true)
    try {
      await api.cancelRun(runId)
      if (logEl) appendLog(logEl, `${t("runSheet.cancelLog")}\n`)
      toast(t("runSheet.cancelRequested"), { description: t("runSheet.cancelRequested.hint") })
    } catch (err) {
      toast.error(t("runSheet.cancelFailed"), { description: errorMessage(err) })
    } finally {
      setCancelling(false)
    }
  }

  const canCancel = !!live && live.run.managed && live.status === "running" && !live.ended
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {live ? t("runSheet.title", { kind: RUN_LABEL[live.run.kind] ?? live.run.kind }) : t("runSheet.titleBare")}
          {live && <StatusChip status={live.status} />}
        </SheetTitle>
        <SheetDescription id="live-desc" className="break-all">
          {live ? `${live.run.id}${runScope(live.run)}` : runId}
        </SheetDescription>
        {live && <RunTimes live={live} />}
        {live && <EtaNote run={live.run} />}
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        {(error || streamError) && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/30
                                       bg-destructive/5 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 break-words text-destructive">
              {error ? t("runSheet.loadError", { error }) : streamError}
            </span>
            <Button size="sm" variant="outline" onClick={retry}>
              <RotateCw aria-hidden /> {t("common.tryAgain")}
            </Button>
          </div>
        )}
        {!live && !error && <Skeleton className="h-6 w-2/3" />}
        {live && (
          <>
            <StepList steps={live.steps} ended={live.ended || !["pending", "running"].includes(live.status)} />
            <p id="live-progress" className="text-xs text-muted-foreground" role="status" aria-live="polite">
              {progressText(live.progress)}
            </p>
            <span id="live-status" className="sr-only" data-status={live.status} data-ended={live.ended}
                  role="status" aria-live="polite">
              {STATUS_LABEL[live.status] ?? live.status}
            </span>
          </>
        )}
        <pre ref={setLogEl} id="live-log" role="log" aria-live="off" aria-label={t("runSheet.log")}
             tabIndex={0}
             className="min-h-48 flex-1 overflow-auto rounded-md border bg-muted/40 p-3 font-mono
                        text-xs leading-relaxed whitespace-pre-wrap break-all outline-none
                        focus-visible:ring-[3px] focus-visible:ring-ring/50" />
      </div>
      {canCancel && (
        <SheetFooter className="border-t">
          <Button variant="outline" onClick={cancel} disabled={cancelling} id="btn-cancel">
            {cancelling ? <Loader2 className="animate-spin" aria-hidden /> : <Square aria-hidden />}
            {t("runSheet.cancel")}
          </Button>
        </SheetFooter>
      )}
    </>
  )
}
