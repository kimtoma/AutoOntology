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
import { CHECK_LABEL, CHECK_NAME } from "@/lib/labels"
import { cn } from "@/lib/utils"

/** The checks of `/api/status` plus kordoc (an optional component, never an error). */
function rows(data: ServiceStatus): [string, StatusCheck][] {
  return [
    ...Object.entries(data.checks),
    ["kordoc", data.kordoc_available
      ? { status: "ok", detail: "HWP, HWPX, PDF, DOCX and PPTX documents can be read" }
      : { status: "warn", detail: "optional: only Markdown, text and HTML documents can be read",
          remedy: "uv run autoontology setup --docs" }],
  ]
}

export default function StatusPage() {
  const { data, error, isPending, isFetching, refetch } = useStatus()
  return (
    <>
      <PageHeader title="Status"
                  description="Read-only health of the service, its backend, Postgres, the models and kordoc (cached for 15 s). Providers are configured in .env."
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
                ["Service", data.service_url],
                ["Upload limit", `${data.upload_max_mb ?? 200} MB`]].map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <dt className="text-xs text-muted-foreground">{k}</dt>
                  <dd className="truncate font-medium" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
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
              {" "}Switch providers by editing <code className="rounded bg-muted px-1">.env</code> (AUTOONTOLOGY_CHAT_PROVIDER, …) and restarting.
            </p>
          </div>
        )}
    </>
  )
}
