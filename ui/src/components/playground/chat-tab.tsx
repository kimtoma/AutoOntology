import { KnowledgeFeedback } from "@/components/knowledge-feedback"
/* Chat: RAG over the published ontology snapshot, with clickable citation chips. */

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Loader2, MessageSquarePlus, Send, Square } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { EmptyState } from "@/components/empty-state"
import { ChipLink, CitedText } from "@/components/playground/shared"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  api, errorMessage, type ElementChip, type RetrievedElement, type SseEvent,
} from "@/lib/api"
import { fmtNumber, t, tn, useT } from "@/i18n"
import { SHARED_DEMO } from "@/lib/demo"

interface Msg {
  role: "user" | "assistant"
  content: string
  citations?: Record<string, ElementChip>
  retrieved?: RetrievedElement[]
  unknown?: string[]
  error?: string
  pending?: boolean
  phase?: string
  /** truncated stream: the text is partial and was not cited or graded */
  incomplete?: boolean
}

const examples = () => [t("playground.chat.example1"), t("playground.chat.example2"), t("playground.chat.example3")]

function Retrieved({ items }: { items: RetrievedElement[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2 text-xs">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
        <ChevronDown className={open ? "size-3.5 rotate-180" : "size-3.5"} aria-hidden />
        {tn("playground.chat.retrieved", items.length)}
      </button>
      {open && (
        <ul className="mt-2 flex flex-wrap gap-1.5" data-retrieved={items.length}>
          {items.map((e) => (
            <li key={e.id} className="inline-flex items-center gap-1">
              <ChipLink chip={e} refName={e.ref} />
              <span className="text-muted-foreground tabular-nums">
                {e.score === null ? t("playground.chat.neighbour") : e.score.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ChatTab() {
  const t = useT()
  const [messages, setMessages] = useState<Msg[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()
  const index = useQuery({ queryKey: ["playground-index"], queryFn: api.playground.index })

  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "end" }) }, [messages])

  const patchLast = (patch: (m: Msg) => Msg) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? patch(m) : m)))

  const ask = async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setInput("")
    setBusy(true)
    setMessages((prev) => [...prev, { role: "user", content: q },
                           { role: "assistant", content: "", pending: true, phase: t("playground.chat.phase.searching") }])
    const ctrl = new AbortController()
    abort.current = ctrl
    const onEvent = (ev: SseEvent) => {
      const d = ev.data as Record<string, unknown>
      if (ev.event === "status") patchLast((m) => ({ ...m, phase: t("playground.chat.phase.checking") }))
      else if (ev.event === "retrieved") {
        patchLast((m) => ({ ...m, retrieved: d.elements as RetrievedElement[], phase: t("playground.chat.phase.writing") }))
      } else if (ev.event === "delta") patchLast((m) => ({ ...m, content: m.content + String(d.text ?? "") }))
      else if (ev.event === "citations") {
        patchLast((m) => ({ ...m, citations: d.citations as Record<string, ElementChip>,
                            unknown: d.unknown as string[] }))
      } else if (ev.event === "done") {
        if (d.conversation_id) setConversationId(String(d.conversation_id))
        patchLast((m) => ({ ...m, pending: false }))
      } else if (ev.event === "stream_truncated") patchLast((m) => ({ ...m, incomplete: true }))
      else if (ev.event === "error") patchLast((m) => ({ ...m, pending: false, error: String(d.detail ?? d.error) }))
    }
    try {
      await api.playground.chat({ question: q, ...(SHARED_DEMO
        ? { history: messages.filter((m) => !m.pending && !m.error).slice(-40).map(({ role, content }) => ({ role, content: content.slice(0, 8000) })) }
        : { conversation_id: conversationId }) }, onEvent,
                                ctrl.signal)
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchLast((m) => ({ ...m, error: errorMessage(err) }))
    } finally {
      patchLast((m) => ({ ...m, pending: false }))
      setBusy(false)
      abort.current = null
      qc.invalidateQueries({ queryKey: ["playground-index"] })
    }
  }

  const idx = index.data?.index
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4" id="playground-chat">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {t("playground.chat.scope", { scope: index.data
            ? t("playground.chat.scopeRevision", { n: index.data.revision, scope: index.data.scope.join(" + ") })
            : t("playground.chat.scopeLatest") })}
          {" · "}{idx ? t("playground.chat.indexInfo", { n: fmtNumber(idx.elements), model: idx.model })
            : t("playground.chat.indexLater")}
          {index.data?.stale && idx && ` · ${t("playground.chat.stale")}`}
        </span>
        <Button type="button" size="sm" variant="ghost" disabled={busy || !messages.length}
                onClick={() => { setMessages([]); setConversationId(null) }}>
          <MessageSquarePlus aria-hidden /> {t("playground.chat.newChat")}
        </Button>
      </div>
      {index.data?.warnings.map((w) => <p key={w} className="text-xs text-amber-700 dark:text-amber-300">{w}</p>)}

      <div className="grid gap-3" aria-live="polite" id="chat-messages">
        {!messages.length && (
          <EmptyState title={t("playground.chat.askTitle")}
                      hint={t("playground.chat.askHint")}
                      action={
                        <div className="flex flex-wrap justify-center gap-2">
                          {examples().map((e) => (
                            <Button key={e} type="button" size="sm" variant="outline" onClick={() => ask(e)}
                                    className="h-auto max-w-full py-1.5 whitespace-normal">{e}</Button>
                          ))}
                        </div>
                      } />
        )}
        {messages.map((m, i) => (
          <div key={i} data-role={m.role}
               className={m.role === "user"
                 ? "ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground whitespace-pre-wrap break-words"
                 : "max-w-full rounded-lg border bg-card px-3 py-2 text-sm"}>
            {m.role === "user" ? m.content : (
              <>
                {m.content ? <CitedText text={m.content} citations={m.citations ?? {}} /> : m.pending && (
                  <span className="inline-flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" aria-hidden /> {m.phase}
                  </span>
                )}
                {m.incomplete && <Badge variant="outline" className="mt-2 font-normal">{t("playground.chat.incomplete")}</Badge>}
                {m.error && <p role="alert" className="mt-2 text-sm text-destructive break-words">{m.error}</p>}
                {!m.pending && m.citations && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" data-citations={Object.keys(m.citations).length}>
                    {Object.keys(m.citations).length ? <span className="text-muted-foreground">{t("playground.chat.sources")}</span>
                      : <Badge variant="outline" className="font-normal">{t("playground.chat.noCitations")}</Badge>}
                    {Object.entries(m.citations).map(([ref, chip]) => <ChipLink key={ref} chip={chip} refName={ref} />)}
                    {!!m.unknown?.length && (
                      <span className="text-amber-700 dark:text-amber-300">{t("playground.chat.unknownCitations", { list: m.unknown.join(", ") })}</span>
                    )}
                  </div>
                )}
                {!SHARED_DEMO && !m.pending && m.content && <KnowledgeFeedback question={messages.slice(0,i).filter(x=>x.role==="user").at(-1)?.content || ""} answer={m.content} />}
                {m.retrieved && <Retrieved items={m.retrieved} />}
              </>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
        <Textarea id="chat-input" value={input} onChange={(e) => setInput(e.target.value)} rows={2}
                  placeholder={t("playground.chat.placeholder")}
                  aria-label={t("playground.chat.question")} maxLength={2000}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      ask(input)
                    }
                  }} />
        <div className="flex justify-end gap-2">
          {busy && (
            <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
              <Square aria-hidden /> {t("playground.stop")}
            </Button>
          )}
          <Button type="submit" id="chat-send" disabled={busy || !input.trim()}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />} {t("playground.chat.send")}
          </Button>
        </div>
      </form>
    </div>
  )
}
