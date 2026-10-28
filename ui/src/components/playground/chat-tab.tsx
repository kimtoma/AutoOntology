/* 온톨로지 대화: RAG over the merged ontology, answers with clickable citation chips. */

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

const EXAMPLES = ["계약은 어떤 승인 절차를 거치나요?", "고객과 계약은 어떻게 연결되나요?",
                  "청구서의 수납 상태에는 무엇이 있나요?"]

function Retrieved({ items }: { items: RetrievedElement[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2 text-xs">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
        <ChevronDown className={open ? "size-3.5 rotate-180" : "size-3.5"} aria-hidden />
        검색된 요소 {items.length}개
      </button>
      {open && (
        <ul className="mt-2 flex flex-wrap gap-1.5" data-retrieved={items.length}>
          {items.map((e) => (
            <li key={e.id} className="inline-flex items-center gap-1">
              <ChipLink chip={e} refName={e.ref} />
              <span className="text-muted-foreground tabular-nums">
                {e.score === null ? "이웃" : e.score.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ChatTab({ source }: { source: string }) {
  const [messages, setMessages] = useState<Msg[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()
  const index = useQuery({ queryKey: ["playground-index", source],
                           queryFn: () => api.playground.index(source) })

  // the page mounts one ChatTab per source (key), so another source = a new conversation
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
                           { role: "assistant", content: "", pending: true, phase: "검색 중" }])
    const ctrl = new AbortController()
    abort.current = ctrl
    const onEvent = (ev: SseEvent) => {
      const d = ev.data as Record<string, unknown>
      if (ev.event === "status") patchLast((m) => ({ ...m, phase: "온톨로지 인덱스 확인·검색 중" }))
      else if (ev.event === "retrieved") {
        patchLast((m) => ({ ...m, retrieved: d.elements as RetrievedElement[], phase: "답변 생성 중" }))
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
      await api.playground.chat({ source, question: q, conversation_id: conversationId }, onEvent,
                                ctrl.signal)
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchLast((m) => ({ ...m, error: errorMessage(err) }))
    } finally {
      patchLast((m) => ({ ...m, pending: false }))
      setBusy(false)
      abort.current = null
      qc.invalidateQueries({ queryKey: ["playground-index", source] })
    }
  }

  const idx = index.data?.index
  return (
    <div className="grid gap-4" id="playground-chat">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          범위: {index.data?.scope.join(" + ") ?? source}
          {idx ? ` · 요소 ${idx.elements.toLocaleString()}개 (${idx.model})` : " · 인덱스는 첫 질문에 만들어집니다"}
          {index.data?.stale && idx && " · 온톨로지가 바뀌어 다음 질문에 갱신"}
        </span>
        <Button type="button" size="sm" variant="ghost" disabled={busy || !messages.length}
                onClick={() => { setMessages([]); setConversationId(null) }}>
          <MessageSquarePlus aria-hidden /> 새 대화
        </Button>
      </div>
      {index.data?.warnings.map((w) => <p key={w} className="text-xs text-amber-700 dark:text-amber-300">{w}</p>)}

      <div className="grid gap-3" aria-live="polite" id="chat-messages">
        {!messages.length && (
          <EmptyState title="온톨로지에 질문해 보세요"
                      hint="답은 검색된 온톨로지 요소만 근거로 하며, 근거 칩을 누르면 개념 상세나 그래프로 이동합니다."
                      action={
                        <div className="flex flex-wrap justify-center gap-2">
                          {EXAMPLES.map((e) => (
                            <Button key={e} type="button" size="sm" variant="outline" onClick={() => ask(e)}>{e}</Button>
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
                {m.incomplete && <Badge variant="outline" className="mt-2 font-normal">미완료 답변</Badge>}
                {m.error && <p role="alert" className="mt-2 text-sm text-destructive break-words">{m.error}</p>}
                {!m.pending && m.citations && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" data-citations={Object.keys(m.citations).length}>
                    {Object.keys(m.citations).length ? <span className="text-muted-foreground">근거</span>
                      : <Badge variant="outline" className="font-normal">인용 없음</Badge>}
                    {Object.entries(m.citations).map(([ref, chip]) => <ChipLink key={ref} chip={chip} refName={ref} />)}
                    {!!m.unknown?.length && (
                      <span className="text-amber-700 dark:text-amber-300">없는 인용 {m.unknown.join(", ")}</span>
                    )}
                  </div>
                )}
                {m.retrieved && <Retrieved items={m.retrieved} />}
              </>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
        <Textarea id="chat-input" value={input} onChange={(e) => setInput(e.target.value)} rows={2}
                  placeholder="예: 계약 승인 권한은 금액에 따라 어떻게 달라지나요? (Enter 전송, Shift+Enter 줄바꿈)"
                  aria-label="질문" maxLength={2000}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      ask(input)
                    }
                  }} />
        <div className="flex justify-end gap-2">
          {busy && (
            <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
              <Square aria-hidden /> 중지
            </Button>
          )}
          <Button type="submit" id="chat-send" disabled={busy || !input.trim()}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />} 보내기
          </Button>
        </div>
      </form>
    </div>
  )
}
