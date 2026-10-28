import { useQueryClient } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"

import { ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { CheckChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { keys, useStatus } from "@/hooks/queries"
import { CHECK_KO, CHECK_NAME_KO } from "@/lib/labels"
import { cn } from "@/lib/utils"

export default function StatusPage() {
  const { data, error, isPending, isFetching, refetch } = useStatus()
  const qc = useQueryClient()
  return (
    <>
      <PageHeader title="상태" description="서비스와 백엔드, 모델, 문서 엔진의 간단 점검입니다(15초 캐시)."
                  actions={<Button variant="outline" id="btn-status" disabled={isFetching}
                                   onClick={() => { refetch(); qc.invalidateQueries({ queryKey: keys.providers }) }}>
                    <RefreshCw className={cn(isFetching && "animate-spin")} aria-hidden /> 다시 확인
                  </Button>} />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-64" />
        : (
          <div className="grid gap-6">
            <dl id="status-meta" className="grid gap-x-6 gap-y-3 rounded-xl border p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {[["버전", data.version], ["모델 프로파일", data.model_profile],
                ["채팅", `${data.chat_provider} · ${data.chat_model || "-"}`],
                ["임베딩", `${data.embed_provider} · ${data.embed_model || "-"}`],
                ["서비스", data.service_url]].map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <dt className="text-xs text-muted-foreground">{k}</dt>
                  <dd className="truncate font-medium" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="status-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">항목</TableHead>
                      <TableHead>상태</TableHead>
                      <TableHead className="pr-4">내용 · 조치</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(data.checks).map(([k, c]) => (
                      <TableRow key={k} data-check={k}>
                        <TableCell className="pl-4 font-medium">{CHECK_NAME_KO[k] ?? k}</TableCell>
                        <TableCell><CheckChip status={c.status} label={CHECK_KO[c.status] ?? c.status} /></TableCell>
                        <TableCell className="pr-4 whitespace-normal">
                          <p className="text-xs break-all text-muted-foreground">{c.detail}</p>
                          {c.remedy && <code className="mt-1 inline-block rounded bg-muted px-1.5 py-0.5 text-xs break-all">{c.remedy}</code>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
            <p className="text-sm text-muted-foreground">
              자세한 점검: 터미널에서 <code className="rounded bg-muted px-1">{data.doctor}</code>
              {" "}(모델 호출 생략은 <code className="rounded bg-muted px-1">--skip-models</code>).
              서비스 시작은 <code className="rounded bg-muted px-1">uv run autoontology up</code>.
            </p>
          </div>
        )}
    </>
  )
}
