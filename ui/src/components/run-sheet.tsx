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
import { fmtTime, progressText, RUN_KO, STATUS_KO, STEP_KO } from "@/lib/labels"

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
        if (s.error) appendLine(`오류: ${s.error}`)
      })
      es.addEventListener("end", () => {
        ended = true
        es?.close()
        if (!lastLogId) {
          appendLine("이 실행의 로그 파일이 없습니다(CLI에서 시작한 실행). 전체 로그: data/logs/jobs.log")
        }
        setLive((cur) => cur && { ...cur, ended: true })
        qc.invalidateQueries({ queryKey: keys.runs })
        qc.invalidateQueries({ queryKey: keys.sources })
        qc.invalidateQueries({ queryKey: keys.exports })
      })
      // EventSource reconnects by itself (sending Last-Event-ID); the end event closes it.
      // CLOSED without an end event = the server refused the stream (404/500): no retries.
      es.onerror = () => {
        if (!closed && !ended && es?.readyState === EventSource.CLOSED) {
          setStreamError("실시간 로그 스트림을 열지 못했습니다(서버 오류). 다시 시도하거나 data/logs/jobs.log를 보세요.")
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

export function RunSheet({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  return (
    <Sheet open={!!runId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 sm:max-w-xl" id="live" aria-describedby="live-desc"
                    onOpenAutoFocus={(e) => e.preventDefault()}>
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
      toast.error(`${RUN_KO[live.run.kind] ?? live.run.kind} 실패`, { description: live.run.source ?? undefined })
    }
  }, [live])

  const cancel = async () => {
    setCancelling(true)
    try {
      await api.cancelRun(runId)
      if (logEl) appendLog(logEl, "취소 요청됨: 현재 단계가 끝나면 멈춥니다\n")
      toast("취소를 요청했습니다", { description: "현재 단계가 끝나면 멈춥니다" })
    } catch (err) {
      toast.error("취소 실패", { description: errorMessage(err) })
    } finally {
      setCancelling(false)
    }
  }

  const canCancel = !!live && live.run.managed && live.status === "running" && !live.ended
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          진행 상황
          {live && <StatusChip status={live.status} />}
        </SheetTitle>
        <SheetDescription id="live-desc" className="break-all">
          {live ? `${RUN_KO[live.run.kind] ?? live.run.kind} · ${live.run.source ?? "-"} · ${live.run.id}`
            : runId}
        </SheetDescription>
        {live && <p className="text-xs text-muted-foreground">시작 {fmtTime(live.run.started_at)}</p>}
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        {(error || streamError) && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/30
                                       bg-destructive/5 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 break-words text-destructive">
              {error ? `실행 정보를 불러오지 못했습니다: ${error}` : streamError}
            </span>
            <Button size="sm" variant="outline" onClick={retry}>
              <RotateCw aria-hidden /> 다시 시도
            </Button>
          </div>
        )}
        {!live && !error && <Skeleton className="h-6 w-2/3" />}
        {live && (
          <>
            <div id="live-steps" className="flex flex-wrap gap-1.5" aria-label="단계">
              {live.steps.map((s) => (
                <StatusChip key={s.name} status={s.status}
                            label={`${STEP_KO[s.name] ?? s.name} · ${STATUS_KO[s.status] ?? s.status}`} />
              ))}
            </div>
            <p id="live-progress" className="text-xs text-muted-foreground" role="status" aria-live="polite">
              {progressText(live.progress)}
            </p>
            <span id="live-status" className="sr-only" data-status={live.status} data-ended={live.ended}
                  role="status" aria-live="polite">
              {STATUS_KO[live.status] ?? live.status}
            </span>
          </>
        )}
        <pre ref={setLogEl} id="live-log" role="log" aria-live="off" aria-label="실행 로그"
             tabIndex={0}
             className="min-h-48 flex-1 overflow-auto rounded-md border bg-muted/40 p-3 font-mono
                        text-xs leading-relaxed whitespace-pre-wrap break-all outline-none
                        focus-visible:ring-[3px] focus-visible:ring-ring/50" />
      </div>
      {canCancel && (
        <SheetFooter className="border-t">
          <Button variant="outline" onClick={cancel} disabled={cancelling} id="btn-cancel">
            {cancelling ? <Loader2 className="animate-spin" aria-hidden /> : <Square aria-hidden />}
            취소
          </Button>
        </SheetFooter>
      )}
    </>
  )
}
