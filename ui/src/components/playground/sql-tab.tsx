/* SQL: upstream's NL->SQL agent over a structured source, relayed as SSE (steps, SQL, result table). */

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

/** *source*: the structured source to query; null = the snapshot's first one. */
export function SqlTab({ source }: { source: string | null }) {
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
      } else if (ev.event === "error") setError(String(d.detail ?? d.message ?? d.error ?? "error"))
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
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" id="playground-sql">
      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); run(question) }}>
        <Textarea id="sql-input" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
                  aria-label="Data question" maxLength={2000}
                  placeholder="e.g. How many customers are there? / The 5 largest contracts by amount (계약금액)"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      run(question)
                    }
                  }} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            The upstream text-to-SQL agent writes SQL from the structured source's semantic layer and runs it. Local models can take a few minutes.
          </p>
          <div className="flex gap-2">
            {busy && <Button type="button" variant="outline" onClick={() => abort.current?.abort()}><Square aria-hidden /> Stop</Button>}
            <Button type="submit" id="sql-send" disabled={busy || !question.trim()}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />} Ask
            </Button>
          </div>
        </div>
      </form>
      {notices.map((n) => <p key={n} className="text-xs text-amber-700 dark:text-amber-300">{n}</p>)}
      {(steps.length > 0 || busy) && (
        <Card className="gap-2 py-4">
          <CardHeader><CardTitle className="text-sm break-words">Agent steps · {asked}</CardTitle></CardHeader>
          <CardContent>
            <ol className="grid gap-1 text-sm" id="sql-steps">
              {steps.map((s, i) => (
                <li key={i} className="flex items-center gap-2" data-done={s.done}>
                  {s.done ? <CheckCircle2 className="size-4 text-success" aria-hidden />
                    : <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
                  {s.label}
                </li>
              ))}
              {busy && !steps.length && <li className="text-muted-foreground">Starting the agent…</li>}
            </ol>
          </CardContent>
        </Card>
      )}
      {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive break-words">{error}</p>}
      {answer?.response && (
        <Card className="gap-2 py-4" id="sql-answer">
          <CardHeader><CardTitle className="text-sm">Answer</CardTitle></CardHeader>
          <CardContent><p className="text-sm whitespace-pre-wrap break-words">{answer.response}</p></CardContent>
        </Card>
      )}
      {sql && (
        <Card className="gap-2 py-4" id="sql-code">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">SQL</CardTitle>
            <CopyButton text={sql} label="Copy SQL" />
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
