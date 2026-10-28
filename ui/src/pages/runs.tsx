import { useQuery } from "@tanstack/react-query"
import { History, Play, SlidersHorizontal } from "lucide-react"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { RunOptionsDialog } from "@/components/run-options"
import { RunSheet } from "@/components/run-sheet"
import { StatusChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { keys, useSources } from "@/hooks/queries"
import { defaultOptions, useStartRun } from "@/hooks/use-start-run"
import { api, type Source } from "@/lib/api"
import { actionsFor, fmtTime, progressText, RUN_KO, sourceState, STEP_KO } from "@/lib/labels"
import { KindBadge } from "@/pages/sources"

function RunActions({ sources, onStarted }: { sources: Source[]; onStarted: (id: string) => void }) {
  const start = useStartRun(onStarted)
  const [optionsFor, setOptionsFor] = useState<Source | null>(null)
  return (
    <div id="run-actions" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {sources.map((s) => (
        <Card key={s.name} className="gap-3" data-source={s.name}>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2 text-base">
              {s.name} <KindBadge kind={s.kind} />
            </CardTitle>
            <CardDescription>{sourceState(s)}</CardDescription>
          </CardHeader>
          <CardFooter className="flex flex-wrap gap-2">
            {actionsFor(s).map((kind) => (
              <Button key={kind} size="sm" variant="secondary" data-run={kind}
                      disabled={!!s.active_run || start.isPending}
                      onClick={() => start.mutate({ kind, source: s.name, options: defaultOptions(kind, s) })}>
                <Play aria-hidden /> {RUN_KO[kind]}
              </Button>
            ))}
            <Button size="sm" variant="ghost" aria-label={`${s.name} 옵션과 함께 실행`}
                    disabled={!!s.active_run} onClick={() => setOptionsFor(s)}>
              <SlidersHorizontal aria-hidden /> 옵션
            </Button>
          </CardFooter>
        </Card>
      ))}
      <RunOptionsDialog source={optionsFor} sources={sources} pending={start.isPending}
                        onClose={() => setOptionsFor(null)}
                        onStart={(kind, options) => {
                          if (!optionsFor) return
                          start.mutate({ kind, source: optionsFor.name, options },
                                       { onSuccess: () => setOptionsFor(null) })
                        }} />
    </div>
  )
}

export default function RunsPage() {
  const [params, setParams] = useSearchParams()
  const runId = params.get("run")
  const open = (id: string | null) => setParams(id ? { run: id } : {}, { replace: !id })
  const sources = useSources()
  const runs = useQuery({ queryKey: keys.runs, queryFn: () => api.runs(40), refetchInterval: 10_000 })

  return (
    <>
      <PageHeader title="실행"
                  description="소스마다 빌드·문서 추출·정렬·내보내기를 시작하고 진행 상황과 로그를 봅니다." />
      <section aria-labelledby="h-actions" className="mb-8">
        <h2 id="h-actions" className="sr-only">실행 시작</h2>
        {sources.error ? <ErrorState error={sources.error} onRetry={() => sources.refetch()} />
          : sources.isPending ? <div className="grid gap-4 sm:grid-cols-2"><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
          : !sources.data.length ? (
            <EmptyState title="실행할 소스가 없습니다"
                        hint={<>먼저 <Link to="/sources" className="underline underline-offset-4">소스</Link>를 등록하세요.</>} />
          ) : <RunActions sources={sources.data} onStarted={open} />}
      </section>

      <section aria-labelledby="h-history">
        <h2 id="h-history" className="mb-3 text-lg font-semibold">실행 기록</h2>
        {runs.error ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
          : runs.isPending ? <Skeleton className="h-64" />
          : !runs.data.length ? <EmptyState icon={History} title="실행 기록이 없습니다"
                                            hint="위의 소스 카드에서 실행을 시작하세요." />
          : (
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="runs-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">실행</TableHead>
                      <TableHead>상태</TableHead>
                      <TableHead className="hidden sm:table-cell">시작</TableHead>
                      <TableHead className="hidden md:table-cell pr-4">단계</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.data.map((r) => (
                      <TableRow key={r.id} data-run-id={r.id} tabIndex={0}
                                className="cursor-pointer"
                                aria-label={`실행 ${r.id} 상세 보기`}
                                onClick={() => open(r.id)}
                                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(r.id) } }}>
                        <TableCell className="pl-4 whitespace-normal">
                          <div className="font-medium">{RUN_KO[r.kind] ?? r.kind} · {r.source ?? "-"}</div>
                          <code className="text-xs text-muted-foreground">{r.id}</code>
                        </TableCell>
                        <TableCell><StatusChip status={r.status} /></TableCell>
                        <TableCell className="hidden text-xs sm:table-cell">{fmtTime(r.started_at)}</TableCell>
                        <TableCell className="hidden max-w-md pr-4 whitespace-normal md:table-cell">
                          <div className="flex flex-wrap gap-1">
                            {r.steps.map((s) => (
                              <StatusChip key={s.name} status={s.status} label={STEP_KO[s.name] ?? s.name} />
                            ))}
                            {Object.keys(r.progress || {}).length > 0 && (
                              <span className="text-xs text-muted-foreground">{progressText(r.progress, false)}</span>
                            )}
                          </div>
                          {r.error && <p className="mt-1 line-clamp-2 text-xs text-destructive">{r.error.slice(0, 200)}</p>}
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
