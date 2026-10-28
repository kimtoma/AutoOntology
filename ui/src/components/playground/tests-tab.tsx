/* 검증 테스트: competency questions, judged runs, history with regression highlight. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle, History, Lightbulb, Loader2, Play, Plus, Trash2, TrendingUp,
} from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { CitedText, VerdictBadge } from "@/components/playground/shared"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { api, errorMessage, type SseEvent, type Suggestion, type TestHistory } from "@/lib/api"
import { fmtTime } from "@/lib/labels"

function AddDialog({ source, open, onClose }: { source: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [question, setQuestion] = useState("")
  const [hint, setHint] = useState("")
  const [elements, setElements] = useState("")
  const add = useMutation({
    mutationFn: () => api.playground.addTest({
      source, question, expected_answer_hint: hint,
      expected_elements: elements.split(/[\s,]+/).filter(Boolean),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playground-history", source] })
      setQuestion(""); setHint(""); setElements("")
      onClose()
    },
    onError: (e) => toast.error("추가 실패", { description: errorMessage(e) }),
  })
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>역량 질문 추가</DialogTitle>
          <DialogDescription>온톨로지가 답할 수 있어야 하는 질문과 기대 근거를 적습니다.</DialogDescription>
        </DialogHeader>
        <form id="add-test" className="grid gap-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <div className="grid gap-1.5">
            <Label htmlFor="t-q">질문</Label>
            <Textarea id="t-q" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} required maxLength={1000} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="t-h">기대 답 힌트</Label>
            <Input id="t-h" value={hint} onChange={(e) => setHint(e.target.value)} maxLength={1000} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="t-e">기대 요소 id (쉼표 구분, 선택)</Label>
            <Input id="t-e" value={elements} onChange={(e) => setElements(e.target.value)}
                   placeholder="term-…, concept-…" className="font-mono text-xs" />
          </div>
        </form>
        <DialogFooter>
          <Button type="submit" form="add-test" disabled={!question.trim() || add.isPending}>
            {add.isPending && <Loader2 className="animate-spin" aria-hidden />} 추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SuggestDialog({ source, open, onClose }: { source: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const suggest = useQuery({ queryKey: ["playground-suggest", source], enabled: open, retry: false,
                             staleTime: Infinity, queryFn: () => api.playground.suggest(source) })
  const accept = useMutation({
    mutationFn: async (items: Suggestion[]) => {
      for (const s of items) {
        await api.playground.addTest({ source, question: s.question,
                                       expected_elements: s.expected_elements,
                                       expected_answer_hint: s.expected_answer_hint })
      }
      return items.length
    },
    onSuccess: (n) => {
      toast.success(`역량 질문 ${n}개를 추가했습니다`)
      qc.invalidateQueries({ queryKey: ["playground-history", source] })
      setPicked(new Set())
      onClose()
    },
    onError: (e) => toast.error("추가 실패", { description: errorMessage(e) }),
  })
  const items = suggest.data?.suggestions ?? []
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>역량 질문 제안</DialogTitle>
          <DialogDescription>
            {suggest.data ? `${suggest.data.provider} · ${suggest.data.model}가 온톨로지 요약에서 만든 질문입니다. 고른 질문을 테스트에 추가합니다.`
              : "현재 채팅 공급자가 온톨로지 요약을 보고 질문을 만듭니다 (로컬 모델은 1분 이상 걸릴 수 있습니다)."}
          </DialogDescription>
        </DialogHeader>
        {suggest.isFetching ? (
          <div className="grid gap-2" aria-busy><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : suggest.error ? (
          <ErrorState error={suggest.error} onRetry={() => suggest.refetch()} />
        ) : (
          <ScrollArea className="max-h-[55svh] pr-3">
            <ul className="grid gap-2" id="suggestions">
              {items.map((s, i) => (
                <li key={i} className="rounded-md border p-2 text-sm">
                  <label className="flex cursor-pointer gap-2">
                    <input type="checkbox" className="mt-1 size-4 accent-primary" checked={picked.has(i)}
                           data-suggestion={i}
                           onChange={(e) => {
                             const next = new Set(picked)
                             if (e.target.checked) next.add(i)
                             else next.delete(i)
                             setPicked(next)
                           }} />
                    <span className="grid gap-1">
                      <span className="font-medium">{s.question}</span>
                      {s.expected_answer_hint && <span className="text-xs text-muted-foreground">힌트: {s.expected_answer_hint}</span>}
                      <span className="flex flex-wrap gap-1">
                        {s.expected.map((e) => <Badge key={e.id} variant="secondary" className="font-normal">{e.label}</Badge>)}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </ScrollArea>
        )}
        <DialogFooter className="gap-2">
          <Button type="button" variant="ghost" disabled={suggest.isFetching} onClick={() => suggest.refetch()}>다시 제안</Button>
          <Button type="button" variant="outline" disabled={!items.length}
                  onClick={() => setPicked(new Set(items.map((_, i) => i)))}>모두 선택</Button>
          <Button type="button" id="accept-suggestions" disabled={!picked.size || accept.isPending}
                  onClick={() => accept.mutate(items.filter((_, i) => picked.has(i)))}>
            {accept.isPending && <Loader2 className="animate-spin" aria-hidden />} {picked.size}개 추가
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function HistorySheet({ test, onClose }: { test: TestHistory | null; onClose: () => void }) {
  const [last, prev] = [test?.runs[0], test?.runs[1]]
  return (
    <Sheet open={!!test} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>실행 기록</SheetTitle>
          <SheetDescription className="break-words">{test?.question}</SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1 px-4 pb-4">
          {test && last && prev && (
            <div id="verdict-diff"
                 className={test.regression ? "mb-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"
                   : "mb-3 rounded-md border p-3 text-sm"}>
              <p className="mb-1 font-medium">
                {test.regression ? "회귀: 직전 통과 → 이번 실패/불확실" : test.improved ? "개선: 이번에 통과" : "최근 두 실행 비교"}
              </p>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <VerdictBadge verdict={prev.verdict} /> {prev.score.toFixed(2)}
                <span aria-hidden>→</span>
                <VerdictBadge verdict={last.verdict} /> {last.score.toFixed(2)}
                <span className="text-muted-foreground">({prev.model} → {last.model})</span>
              </div>
            </div>
          )}
          {!test?.runs.length && <EmptyState title="아직 실행하지 않았습니다" />}
          <ol className="grid gap-3">
            {test?.runs.map((r) => (
              <li key={r.id} className="rounded-md border p-3 text-sm" data-run={r.id}>
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <VerdictBadge verdict={r.verdict} />
                  <span className="tabular-nums">{r.score.toFixed(2)}</span>
                  <span className="text-muted-foreground">{fmtTime(r.run_at)} · {r.provider} · {r.model}</span>
                </div>
                {r.rationale && <p className="mb-2 text-xs text-muted-foreground break-words">{r.rationale}</p>}
                {r.answer ? <CitedText text={r.answer} citations={r.citations} />
                  : <p className="text-xs text-muted-foreground">(답 없음)</p>}
              </li>
            ))}
          </ol>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

export function TestsTab({ source }: { source: string }) {
  const qc = useQueryClient()
  const key = ["playground-history", source]
  const history = useQuery({ queryKey: key, queryFn: () => api.playground.history(source) })
  const [adding, setAdding] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [historyId, setHistoryId] = useState<number | null>(null)
  const [running, setRunning] = useState<{ total: number; done: number; current?: number } | null>(null)
  const del = useMutation({
    mutationFn: (id: number) => api.playground.deleteTest(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  })

  const run = async (ids: number[] | null) => {
    setRunning({ total: ids?.length ?? history.data?.length ?? 0, done: 0 })
    const onEvent = (ev: SseEvent) => {
      const d = ev.data as Record<string, unknown>
      if (ev.event === "start") setRunning({ total: Number(d.total), done: 0 })
      else if (ev.event === "test_start") setRunning((r) => r && { ...r, current: Number(d.test_id) })
      else if (ev.event === "test_result") {
        setRunning((r) => r && { ...r, done: r.done + 1 })
        qc.invalidateQueries({ queryKey: key })
      } else if (ev.event === "done") {
        toast.success(`통과 ${d.pass} · 실패 ${d.fail} · 불확실 ${d.unsure}`)
      } else if (ev.event === "error") toast.error("실행 실패", { description: String(d.detail ?? d.error) })
    }
    try {
      await api.playground.runTests({ source, test_ids: ids }, onEvent)
    } catch (err) {
      toast.error("실행 실패", { description: errorMessage(err) })
    } finally {
      setRunning(null)
      qc.invalidateQueries({ queryKey: key })
    }
  }

  const tests = history.data ?? []
  const regressions = tests.filter((t) => t.regression).length
  const selected = tests.find((t) => t.id === historyId) ?? null
  return (
    <div className="grid gap-4" id="playground-tests">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>{tests.length}개 질문</span>
          {["pass", "fail", "unsure"].map((v) => {
            const n = tests.filter((t) => t.last?.verdict === v).length
            return n ? <span key={v} className="inline-flex items-center gap-1"><VerdictBadge verdict={v} />{n}</span> : null
          })}
          {regressions > 0 && (
            <Badge variant="outline" className="border-destructive/40 text-destructive" id="regressions">
              <AlertTriangle aria-hidden /> 회귀 {regressions}
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => setSuggesting(true)} id="open-suggest">
            <Lightbulb aria-hidden /> 질문 제안
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)}>
            <Plus aria-hidden /> 질문 추가
          </Button>
          <Button type="button" size="sm" id="run-all" disabled={!!running || !tests.length} onClick={() => run(null)}>
            {running ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
            {running ? `실행 중 ${running.done}/${running.total}` : "전체 실행"}
          </Button>
        </div>
      </div>

      {history.error ? <ErrorState error={history.error} onRetry={() => history.refetch()} />
        : history.isPending ? <Skeleton className="h-40" />
        : !tests.length ? (
          <EmptyState icon={Lightbulb} title="역량 질문이 없습니다"
                      hint="‘질문 제안’으로 공급자가 온톨로지에서 만든 질문을 받거나 직접 추가하세요."
                      action={<Button type="button" size="sm" onClick={() => setSuggesting(true)}>질문 제안</Button>} />
        ) : (
          <Card className="py-0">
            <CardContent className="px-0">
              <Table id="tests-table">
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">질문</TableHead>
                    <TableHead>최근 판정</TableHead>
                    <TableHead className="hidden sm:table-cell">점수</TableHead>
                    <TableHead className="hidden md:table-cell">기대 요소</TableHead>
                    <TableHead className="pr-4 text-right">작업</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tests.map((t) => (
                    <TableRow key={t.id} data-test-id={t.id} data-regression={t.regression}
                              className={t.regression ? "bg-destructive/5" : undefined}>
                      <TableCell className="pl-4 whitespace-normal">
                        <div className="font-medium break-words">{t.question}</div>
                        {t.expected_answer_hint && <div className="text-xs text-muted-foreground break-words">힌트: {t.expected_answer_hint}</div>}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          {running?.current === t.id ? <Loader2 className="size-4 animate-spin" aria-label="실행 중" />
                            : <VerdictBadge verdict={t.last?.verdict} />}
                          {t.regression && <AlertTriangle className="size-4 text-destructive" aria-label="회귀" />}
                          {t.improved && <TrendingUp className="size-4 text-success" aria-label="개선" />}
                        </div>
                      </TableCell>
                      <TableCell className="hidden tabular-nums sm:table-cell">{t.last ? t.last.score.toFixed(2) : "-"}</TableCell>
                      <TableCell className="hidden text-xs text-muted-foreground md:table-cell">{t.expected_elements.length}</TableCell>
                      <TableCell className="pr-4">
                        <div className="flex justify-end gap-1">
                          <Button type="button" size="icon" variant="ghost" aria-label="이 질문 실행"
                                  disabled={!!running} onClick={() => run([t.id])}><Play aria-hidden /></Button>
                          <Button type="button" size="icon" variant="ghost" aria-label="실행 기록"
                                  onClick={() => setHistoryId(t.id)}><History aria-hidden /></Button>
                          <Button type="button" size="icon" variant="ghost" aria-label="삭제"
                                  disabled={!!running || del.isPending} onClick={() => del.mutate(t.id)}><Trash2 aria-hidden /></Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      <AddDialog source={source} open={adding} onClose={() => setAdding(false)} />
      <SuggestDialog source={source} open={suggesting} onClose={() => setSuggesting(false)} />
      <HistorySheet test={selected} onClose={() => setHistoryId(null)} />
    </div>
  )
}
