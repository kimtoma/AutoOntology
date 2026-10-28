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
import { useStatus } from "@/hooks/queries"
import type { ServiceStatus, StatusCheck } from "@/lib/api"
import { extensionsByEngine, extLabel, KORDOC_INSTALL } from "@/lib/formats"
import { CHECK_LABEL, CHECK_NAME } from "@/lib/labels"
import { ProviderCard } from "@/components/provider-card"
import { CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

/** The checks of `/api/status` plus kordoc and its OCR models (optional components, never an
    error). The extension lists come from `/api/status.formats`. */
function rows(data: ServiceStatus): [string, StatusCheck][] {
  const by = extensionsByEngine(data.formats, "documents")
  const kordocExts = by.kordoc.length ? extLabel(by.kordoc) : "HWP, HWPX, PDF, DOCX and PPTX"
  const nativeExts = by.native.length ? extLabel(by.native) : "Markdown, text and HTML"
  const images = by["kordoc-ocr"].length ? extLabel(by["kordoc-ocr"]) : "image"
  return [
    ...Object.entries(data.checks),
    ["kordoc", data.kordoc_available
      ? { status: "ok", detail: `${kordocExts} documents can be read` }
      : { status: "warn", detail: `optional: only ${nativeExts} documents can be read`,
          remedy: KORDOC_INSTALL }],
    ["ocr", !data.kordoc_available
      ? { status: "warn", detail: `optional: ${images} files and scanned PDFs need kordoc and its OCR models`,
          remedy: KORDOC_INSTALL }
      : data.ocr_ready
        ? { status: "ok", detail: `${images} files and scanned PDF pages are read by OCR (PP-OCRv5 korean)` }
        : { status: "warn", detail: `optional: ${images} files and scanned PDFs are skipped until the OCR models are downloaded`,
            remedy: KORDOC_INSTALL }],
  ]
}

export default function StatusPage() {
  const { data, error, isPending, isFetching, refetch } = useStatus()
  return (
    <>
      <PageHeader title="Status"
                  description="Read-only health of the service, its backend, Postgres, the models and kordoc (cached for 15 s). Switch providers with the command below."
                  actions={<Button variant="outline" id="btn-status" disabled={isFetching} onClick={() => refetch()}>
                    <RefreshCw className={cn(isFetching && "animate-spin")} aria-hidden /> Check again
                  </Button>} />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-64" />
        : (
          <div className="grid gap-6">
            <dl id="status-meta" className="grid gap-x-6 gap-y-3 rounded-xl border p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {[["Version", data.version],
                ["Chat", `${data.providers.chat_provider} · ${data.providers.chat_model || "-"}`],
                ["Embeddings", `${data.providers.embed_provider} · ${data.providers.embed_model || "-"}`],
                ...(data.providers.openai_base_url ? [["OpenAI-compatible endpoint", data.providers.openai_base_url]] : []),
                ["Image captions", data.vision_model ? `on · ${data.vision_model}` : "off (OCR text only)"],
                ["Service", data.service_url],
                ["Upload limit", `${data.upload_max_mb ?? 200} MB`]].map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <dt className="text-xs text-muted-foreground">{k}</dt>
                  <dd className="truncate font-medium" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
            <Card className="gap-3" id="status-providers">
              <CardHeader><CardTitle className="text-base">Models</CardTitle></CardHeader>
              <CardContent><ProviderCard status={data} /></CardContent>
            </Card>
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="status-table" className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[35%] pl-4 sm:w-48">Component</TableHead>
                      <TableHead className="w-28">Status</TableHead>
                      <TableHead className="pr-4">Detail</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows(data).map(([k, c]) => (
                      <TableRow key={k} data-check={k} data-status={c.status}>
                        <TableCell className="pl-4 font-medium whitespace-normal">{CHECK_NAME[k] ?? k}</TableCell>
                        <TableCell><CheckChip status={c.status} label={CHECK_LABEL[c.status] ?? c.status} /></TableCell>
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
              Full check from a terminal: <code className="rounded bg-muted px-1">{data.doctor}</code>.
              {" "}Start the service with <code className="rounded bg-muted px-1">uv run autoontology up</code>.
              {" "}Switch the chat model with <code className="rounded bg-muted px-1">uv run autoontology provider use &lt;name&gt;</code>
              {" "}(ollama, claude, codex or openai), then restart with <code className="rounded bg-muted px-1">uv run autoontology down && uv run autoontology up</code>.
            </p>
          </div>
        )}
    </>
  )
}
