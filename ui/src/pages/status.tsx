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
import { t, tx, useT } from "@/i18n"
import { cn } from "@/lib/utils"

/** The checks of `/api/status` plus kordoc and its OCR models (optional components, never an
    error). The extension lists come from `/api/status.formats`. */
function rows(data: ServiceStatus): [string, StatusCheck][] {
  const by = extensionsByEngine(data.formats, "documents")
  const kordocExts = by.kordoc.length ? extLabel(by.kordoc) : t("status.kordoc.defaultExts")
  const nativeExts = by.native.length ? extLabel(by.native) : t("status.kordoc.nativeExts")
  const images = by["kordoc-ocr"].length ? extLabel(by["kordoc-ocr"]) : t("status.ocr.defaultImages")
  return [
    ...Object.entries(data.checks),
    ["kordoc", data.kordoc_available
      ? { status: "ok", detail: t("status.kordoc.ok", { exts: kordocExts }) }
      : { status: "warn", detail: t("status.kordoc.warn", { exts: nativeExts }),
          remedy: KORDOC_INSTALL }],
    ["ocr", !data.kordoc_available
      ? { status: "warn", detail: t("status.ocr.noKordoc", { images }),
          remedy: KORDOC_INSTALL }
      : data.ocr_ready
        ? { status: "ok", detail: t("status.ocr.ok", { images }) }
        : { status: "warn", detail: t("status.ocr.missing", { images }),
            remedy: KORDOC_INSTALL }],
  ]
}

export default function StatusPage() {
  const t = useT()
  const { data, error, isPending, isFetching, refetch } = useStatus()
  return (
    <>
      <PageHeader title={t("status.title")}
                  description={t("status.description")}
                  actions={<Button variant="outline" id="btn-status" disabled={isFetching} onClick={() => refetch()}>
                    <RefreshCw className={cn(isFetching && "animate-spin")} aria-hidden /> {t("status.checkAgain")}
                  </Button>} />
      {error ? <ErrorState error={error} onRetry={() => refetch()} />
        : isPending ? <Skeleton className="h-64" />
        : (
          <div className="grid gap-6">
            <dl id="status-meta" className="grid gap-x-6 gap-y-3 rounded-xl border p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {[[t("status.meta.version"), data.version],
                [t("status.meta.chat"), `${data.providers.chat_provider} · ${data.providers.chat_model || "-"}`],
                [t("status.meta.embeddings"), `${data.providers.embed_provider} · ${data.providers.embed_model || "-"}`],
                ...(data.providers.openai_base_url ? [[t("status.meta.openaiEndpoint"), data.providers.openai_base_url]] : []),
                [t("status.meta.imageCaptions"), data.vision_model ? t("status.meta.captionsOn", { model: data.vision_model })
                  : t("status.meta.captionsOff")],
                [t("status.meta.service"), data.service_url],
                [t("status.meta.uploadLimit"), `${data.upload_max_mb ?? 200} MB`]].map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <dt className="text-xs text-muted-foreground">{k}</dt>
                  <dd className="truncate font-medium" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
            <Card className="gap-3" id="status-providers">
              <CardHeader><CardTitle className="text-base">{t("status.models")}</CardTitle></CardHeader>
              <CardContent><ProviderCard status={data} /></CardContent>
            </Card>
            <Card className="py-0">
              <CardContent className="px-0">
                <Table id="status-table" className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[35%] pl-4 sm:w-48">{t("status.col.component")}</TableHead>
                      <TableHead className="w-28">{t("status.col.status")}</TableHead>
                      <TableHead className="pr-4">{t("status.col.detail")}</TableHead>
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
            <p className="text-sm break-keep text-muted-foreground">
              {tx("status.footer", {
                doctor: <code key="doctor" className="rounded bg-muted px-1">{data.doctor}</code>,
                up: <code key="up" className="rounded bg-muted px-1">uv run autoontology up</code>,
                use: <code key="use" className="rounded bg-muted px-1">uv run autoontology provider use &lt;name&gt;</code>,
                restart: <code key="restart" className="rounded bg-muted px-1">uv run autoontology down && uv run autoontology up</code>,
              })}
            </p>
          </div>
        )}
    </>
  )
}
