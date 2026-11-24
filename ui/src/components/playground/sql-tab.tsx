/* SQL: upstream's NL->SQL agent over a structured source, relayed as SSE (steps, SQL, result table).
   Engine 1.3 runs one long ReAct step (`react_sql_agent`) that streams nothing until it is done,
   so the running step shows the elapsed time. A rule/definition answer has no SQL
   (answer_kind "information"); an unconstructable one explains why. An English answer to a
   Korean question gets a `translation` event (answer.response_ko) after the result: shown as the
   answer, with the original collapsible. */

import { AlertTriangle, CheckCircle2, Loader2, Send, Square } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { CopyButton, ResultTable } from "@/components/playground/shared"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, errorMessage, type SseEvent } from "@/lib/api"
import { useT, type MessageKey } from "@/i18n"
import { cell, SQL_NODE_LABEL, sqlAnswerKind, tabulate, type SqlAnswerKind } from "@/lib/playground"

interface Step { node: string; label: string; done: boolean; startedAt: number }
interface SqlAnswer {
  response?: string
  sql_code?: string
  sql_columns?: string[]  // semantic-layer column ids, not result headers
  sql_response_from_db?: unknown
  response_ko?: string  // ours: the Korean translation (a `translation` event)
}

const stepLabel = (node: string, label: string) =>
  (Object.hasOwn(SQL_NODE_LABEL, node) ? SQL_NODE_LABEL[node] : label || node)

/** Seconds since *since*, ticking every second while *active*. */
function useElapsed(since: number | null, active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active || since == null) return  // a stale `now` reads 0 until the first tick
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [since, active])
  return since == null ? 0 : Math.max(0, Math.floor((now - since) / 1000))
}

const newConversation = () =>
  (globalThis.crypto?.randomUUID?.() ?? "00000000-0000-4000-8000-" + Date.now().toString(16).padStart(12, "0").slice(-12))

/** Known server notice codes (meta `notice_codes`, parallel to `notices`) in the UI language;
    an unknown code keeps the server's English text. */
const NOTICE_KEYS: Record<string, MessageKey> = {
  conversation_store_missing: "playground.sql.notice.conversationStoreMissing",
}

function localizeNotices(t: ReturnType<typeof useT>, d: Record<string, unknown>): string[] {
  const texts = Array.isArray(d.notices) ? d.notices.map(String) : []
  const codes = Array.isArray(d.notice_codes) ? d.notice_codes.map(String) : []
  return texts.map((text, i) => (Object.hasOwn(NOTICE_KEYS, codes[i] ?? "") ? t(NOTICE_KEYS[codes[i]]) : text))
}

/** *source*: the structured source to query; null = the snapshot's first one. */
export function SqlTab({ source }: { source: string | null }) {
  const t = useT()
  const [question, setQuestion] = useState("")
  const [asked, setAsked] = useState("")
  const [steps, setSteps] = useState<Step[]>([])
  const [sql, setSql] = useState("")
  const [answer, setAnswer] = useState<SqlAnswer | null>(null)
  const [kind, setKind] = useState<SqlAnswerKind>("sql")
  const [translating, setTranslating] = useState(false)
  const [notices, setNotices] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [conversationId] = useState(newConversation)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const run = async (q: string) => {
    if (!q.trim() || busy) return
    setAsked(q.trim())
    setSteps([]); setSql(""); setAnswer(null); setError(null); setNotices([]); setTranslating(false)
    const nodes: string[] = []
    setBusy(true)
    const ctrl = new AbortController()
    abort.current = ctrl
    const onEvent = (ev: SseEvent) => {
      const d = ev.data as Record<string, unknown>
      if (ev.event === "meta") setNotices(localizeNotices(t, d))
      else if (ev.event === "step") {
        const node = String(d.node ?? "")
        const label = stepLabel(node, String(d.label ?? ""))
        nodes.push(node)
        const startedAt = Date.now()
        setSteps((prev) => {
          const i = prev.findIndex((s) => s.node === node && !s.done)
          if (d.phase === "end" && i >= 0) return prev.map((s, j) => (j === i ? { ...s, done: true } : s))
          if (d.phase === "end") return [...prev, { node, label, done: true, startedAt }]
          return [...prev, { node, label, done: false, startedAt }]
        })
      } else if (ev.event === "sql") setSql(String(d.sql ?? ""))
      else if (ev.event === "result") {
        const a = (d.answer ?? {}) as SqlAnswer
        setAnswer(a)
        setKind(sqlAnswerKind(d.answer_kind, a.sql_code, nodes))
        if (a.sql_code) setSql(a.sql_code)
      } else if (ev.event === "translation") {
        setTranslating(d.phase === "start")
        const ko = (d.answer as { response_ko?: unknown } | undefined)?.response_ko
        if (typeof ko === "string" && ko.trim()) setAnswer((prev) => (prev ? { ...prev, response_ko: ko } : prev))
      } else if (ev.event === "error") setError(String(d.detail ?? d.message ?? d.error ?? "error"))
    }
    try {
      await api.playground.sql({ source, question: q.trim(), conversation_id: conversationId }, onEvent, ctrl.signal)
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(errorMessage(err))
    } finally {
      setBusy(false)
      setTranslating(false)
      abort.current = null
    }
  }

  const table = answer ? tabulate(answer.sql_response_from_db) : null
  const running = steps.find((s) => !s.done) ?? null
  const elapsed = useElapsed(running?.startedAt ?? null, busy && running != null)
  const translated = answer?.response_ko?.trim() ? answer.response_ko : ""
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" id="playground-sql">
      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); run(question) }}>
        <Textarea id="sql-input" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
                  aria-label={t("playground.sql.label")} maxLength={2000}
                  placeholder={t("playground.sql.placeholder")}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      run(question)
                    }
                  }} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{t("playground.sql.hint")}</p>
          <div className="flex gap-2">
            {busy && <Button type="button" variant="outline" onClick={() => abort.current?.abort()}><Square aria-hidden /> {t("playground.stop")}</Button>}
            <Button type="submit" id="sql-send" disabled={busy || !question.trim()}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />} {t("playground.sql.ask")}
            </Button>
          </div>
        </div>
      </form>
      {notices.map((n) => <p key={n} className="text-xs text-amber-700 dark:text-amber-300">{n}</p>)}
      {(steps.length > 0 || busy) && (
        <Card className="gap-2 py-4">
          <CardHeader><CardTitle className="text-sm break-words">{t("playground.sql.steps", { q: asked })}</CardTitle></CardHeader>
          <CardContent>
            <ol className="grid gap-1 text-sm" id="sql-steps">
              {steps.map((s, i) => (
                <li key={i} className="flex items-center gap-2" data-done={s.done}>
                  {s.done ? <CheckCircle2 className="size-4 text-success" aria-hidden />
                    : <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
                  {s.label}
                  {busy && s === running && (
                    <span className="text-xs tabular-nums text-muted-foreground" id="sql-elapsed">{t("playground.sql.elapsed", { s: elapsed })}</span>
                  )}
                </li>
              ))}
              {busy && !steps.length && <li className="text-muted-foreground">{t("playground.sql.starting")}</li>}
              {translating && (
                <li className="flex items-center gap-2 text-muted-foreground" id="sql-translating">
                  <Loader2 className="size-4 animate-spin" aria-hidden />{t("playground.sql.translating")}
                </li>
              )}
            </ol>
            {busy && running?.node === "react_sql_agent" && (
              <p className="mt-2 text-xs text-muted-foreground">{t("playground.sql.longStep")}</p>
            )}
          </CardContent>
        </Card>
      )}
      {error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive break-words">{error}</p>}
      {answer?.response && (
        <Card className={kind === "unconstructable" ? "gap-2 border-amber-500/40 py-4" : "gap-2 py-4"} id="sql-answer" data-kind={kind}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              {kind === "unconstructable" && <AlertTriangle className="size-4 text-amber-600 dark:text-amber-400" aria-hidden />}
              {kind === "unconstructable" ? t("playground.sql.unconstructable") : t("playground.sql.answer")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2">
            <p className="text-sm whitespace-pre-wrap break-words">{translated || answer.response}</p>
            {translated && (
              <details className="text-xs text-muted-foreground" id="sql-answer-original">
                <summary className="cursor-pointer select-none">{t("playground.sql.original")}</summary>
                <p className="mt-1 whitespace-pre-wrap break-words">{answer.response}</p>
              </details>
            )}
            {kind === "information" && <p className="text-xs text-muted-foreground">{t("playground.sql.noSql")}</p>}
            {kind === "unconstructable" && <p className="text-xs text-muted-foreground">{t("playground.sql.unconstructableHint")}</p>}
          </CardContent>
        </Card>
      )}
      {sql && (
        <Card className="gap-2 py-4" id="sql-code">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">SQL</CardTitle>
            <CopyButton text={sql} label={t("playground.sql.copy")} />
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs"><code>{sql}</code></pre>
          </CardContent>
        </Card>
      )}
      {table && <ResultTable columns={table.columns} rows={table.rows} csvName="query-result.csv" id="sql-result" />}
      {answer && !table && answer.sql_response_from_db != null && (
        <pre className="overflow-x-auto rounded-md border p-3 text-xs">{cell(answer.sql_response_from_db)}</pre>
      )}
    </div>
  )
}
