/* 데이터 질의: upstream's NL->SQL agent, relayed as SSE (steps, SQL, result table). */

import { CheckCircle2, Loader2, Send, Square } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { CopyButton, ResultTable } from "@/components/playground/shared"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, errorMessage, type SseEvent } from "@/lib/api"
import { tabulate } from "@/lib/playground"

interface Step { node: string; label: string; done: boolean }
interface SqlAnswer {
  response?: string
  sql_code?: string
  sql_columns?: string[]
  sql_response_from_db?: unknown
}

const newConversation = () =>
  (globalThis.crypto?.randomUUID?.() ?? "00000000-0000-4000-8000-" + Date.now().toString(16).padStart(12, "0").slice(-12))

export function SqlTab({ source }: { source: string }) {
  const [question, setQuestion] = useState("")
  const [asked, setAsked] = useState("")
  const [steps, setSteps] = useState<Step[]>([])
  const [sql, setSql] = useState("")
  const [answer, setAnswer] = useState<SqlAnswer | null>(null)
  const [notices, setNotices] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [conversationId] = useState(newConversation)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const run = async (q: string) => {
    if (!q.trim() || busy) return
    setAsked(q.trim())
    setSteps([]); setSql(""); setAnswer(null); setError(null); setNotices([])
    setBusy(true)
    const ctrl = new AbortController()
    abort.current = ctrl
    const onEvent = (ev: SseEvent) => {
      const d = ev.data as Record<string, unknown>
      if (ev.event === "meta") setNotices((d.notices as string[]) ?? [])
      else if (ev.event === "step") {
        const node = String(d.node ?? "")
        const label = String(d.label ?? node)
        setSteps((prev) => {
          const i = prev.findIndex((s) => s.node === node && !s.done)
          if (d.phase === "end" && i >= 0) return prev.map((s, j) => (j === i ? { ...s, done: true } : s))
          if (d.phase === "end") return [...prev, { node, label, done: true }]
          return [...prev, { node, label, done: false }]
        })
      } else if (ev.event === "sql") setSql(String(d.sql ?? ""))
      else if (ev.event === "result") {
        const a = (d.answer ?? {}) as SqlAnswer
        setAnswer(a)
        if (a.sql_code) setSql(a.sql_code)
      } else if (ev.event === "error") setError(String(d.detail ?? d.message ?? d.error ?? "오류"))
    }
    try {
      await api.playground.sql({ source, question: q.trim(), conversation_id: conversationId }, onEvent, ctrl.signal)
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(errorMessage(err))
    } finally {
      setBusy(false)
      abort.current = null
    }
  }

  const table = answer ? tabulate(answer.sql_response_from_db, answer.sql_columns ?? []) : null
  return (
    <div className="grid gap-4" id="playground-sql">
      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); run(question) }}>
        <Textarea id="sql-input" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
                  aria-label="데이터 질문" maxLength={2000}
                  placeholder="예: 고객 수는? / 계약금액이 가장 큰 계약 5건은?"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      run(question)
                    }
                  }} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            업스트림 text-to-SQL 에이전트가 정형 소스의 시맨틱 레이어로 SQL을 만들고 실행합니다. 로컬 모델은 수 분 걸릴 수 있습니다.
          </p>
          <div className="flex gap-2">
            {busy && <Button type="button" variant="outline" onClick={() => abort.current?.abort()}><Square aria-hidden /> 중지</Button>}
            <Button type="submit" id="sql-send" disabled={busy || !question.trim()}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />} 질의
            </Button>
          </div>
        </div>
      </form>
      {notices.map((n) => <p key={n} className="text-xs text-amber-700 dark:text-amber-300">{n}</p>)}
      {(steps.length > 0 || busy) && (
        <Card className="gap-2 py-4">
          <CardHeader><CardTitle className="text-sm">추론 단계 · {asked}</CardTitle></CardHeader>
          <CardContent>
            <ol className="grid gap-1 text-sm" id="sql-steps">
              {steps.map((s, i) => (
                <li key={i} className="flex items-center gap-2" data-done={s.done}>
                  {s.done ? <CheckCircle2 className="size-4 text-success" aria-hidden />
                    : <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
                  {s.label}
                </li>
              ))}
              {busy && !steps.length && <li className="text-muted-foreground">에이전트 시작 중…</li>}
            </ol>
          </CardContent>
        </Card>
      )}
      {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive break-words">{error}</p>}
      {answer?.response && (
        <Card className="gap-2 py-4" id="sql-answer">
          <CardHeader><CardTitle className="text-sm">답변</CardTitle></CardHeader>
          <CardContent><p className="text-sm whitespace-pre-wrap break-words">{answer.response}</p></CardContent>
        </Card>
      )}
      {sql && (
        <Card className="gap-2 py-4" id="sql-code">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">SQL</CardTitle>
            <CopyButton text={sql} label="SQL 복사" />
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs"><code>{sql}</code></pre>
          </CardContent>
        </Card>
      )}
      {table && <ResultTable columns={table.columns} rows={table.rows} csvName="query-result.csv" id="sql-result" />}
      {answer && !table && answer.sql_response_from_db != null && (
        <pre className="overflow-x-auto rounded-md border p-3 text-xs">{String(answer.sql_response_from_db)}</pre>
      )}
    </div>
  )
}
