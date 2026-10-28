import { useQuery } from "@tanstack/react-query"
import { Download, FileCode2, FolderOpen, Loader2 } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { StatusChip } from "@/components/status-chip"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { api, errorMessage, type ExportEntry } from "@/lib/api"
import { fmtBytes, fmtTime, RUN_KO } from "@/lib/labels"

const TTL = "ontology.ttl"
const PREVIEW_MAX = 200_000

export default function ExportsPage() {
  const list = useQuery({ queryKey: keys.exports, queryFn: api.exports })
  const [preview, setPreview] = useState<{ entry: ExportEntry; text: string } | null>(null)
  const [loading, setLoading] = useState<string | null>(null)

  const openTtl = async (entry: ExportEntry | undefined) => {
    if (!entry) { toast.error("Turtle 파일이 있는 내보내기가 없습니다"); return }
    setLoading(entry.run_id)
    try {
      const text = await api.exportText(entry.run_id, TTL)
      setPreview({ entry, text: text.length > PREVIEW_MAX
        ? text.slice(0, PREVIEW_MAX) + "\n… (앞부분 200,000자만 표시)" : text })
    } catch (err) {
      toast.error("Turtle을 불러오지 못했습니다", { description: errorMessage(err) })
    } finally {
      setLoading(null)
    }
  }
  const latestTtl = list.data?.find((e) => e.files.some((f) => f.name === TTL))

  return (
    <>
      <PageHeader title="내보내기"
                  description="실행이 만든 파일(Turtle·JSON-LD·GraphML·보고서)을 내려받습니다."
                  actions={<Button variant="outline" id="btn-ttl" disabled={!latestTtl || !!loading}
                                   onClick={() => openTtl(latestTtl)}>
                    {loading && loading === latestTtl?.run_id ? <Loader2 className="animate-spin" aria-hidden />
                      : <FileCode2 aria-hidden />} 최신 Turtle 보기
                  </Button>} />
      {list.error ? <ErrorState error={list.error} onRetry={() => list.refetch()} />
        : list.isPending ? <div className="grid gap-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
        : !list.data.length ? (
          <EmptyState icon={FolderOpen} title="내보내기 결과가 없습니다"
                      hint={<><Link to="/runs" className="underline underline-offset-4">실행</Link>에서 내보내기를 실행하세요.</>} />
        ) : (
          <div id="exports-list" className="grid gap-3">
            {list.data.slice(0, 40).map((e) => (
              <Card key={e.run_id} className="gap-3 py-4">
                <CardHeader className="px-4">
                  <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-semibold">{e.source}</span>
                    <code className="text-xs font-normal text-muted-foreground">{e.run_id}</code>
                    {e.kind && <Badge variant="secondary" className="font-normal">{RUN_KO[e.kind] ?? e.kind}</Badge>}
                    {e.status && <StatusChip status={e.status} />}
                    <span className="text-xs font-normal text-muted-foreground">{fmtTime(e.created_at)}</span>
                    {e.files.some((f) => f.name === TTL) && (
                      <Button variant="ghost" size="sm" className="ml-auto h-7" onClick={() => openTtl(e)}
                              disabled={!!loading}>
                        {loading === e.run_id ? <Loader2 className="animate-spin" aria-hidden /> : <FileCode2 aria-hidden />}
                        Turtle 보기
                      </Button>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2 px-4">
                  {e.files.map((f) => (
                    <a key={f.name} href={api.exportFileUrl(e.run_id, f.name)} download
                       className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs
                                  outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50">
                      <Download className="size-3.5" aria-hidden />
                      <span className="font-mono">{f.name}</span>
                      <span className="text-muted-foreground">{fmtBytes(f.bytes)}</span>
                    </a>
                  ))}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      <Dialog open={!!preview} onOpenChange={(o) => { if (!o) setPreview(null) }}>
        <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Turtle 미리보기</DialogTitle>
            <DialogDescription className="break-all">{preview?.entry.source} / {preview?.entry.run_id} / {TTL}</DialogDescription>
          </DialogHeader>
          <ScrollArea className="min-h-0 flex-1 rounded-md border bg-muted/40">
            <pre id="ttl-preview" className="p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
              {preview?.text}
            </pre>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </>
  )
}
