import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { Loader2, ScanLine } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ErrorState } from "@/components/empty-state"
import { RunSheet } from "@/components/run-sheet"
import { opsKeys, refreshOperations, useFreshness } from "@/hooks/operations"
import { useState } from "react"
import { api, errorMessage } from "@/lib/api"
import { operations } from "@/lib/operations"
import { fmtTime } from "@/lib/labels"
import { useT, type MessageKey } from "@/i18n"

export function SourceFreshness() {
  const t = useT(),
    qc = useQueryClient(),
    fresh = useFreshness()
  const [run, setRun] = useState<string | null>(null)
  const scan = useMutation({
    mutationFn: () => operations.freshness(true),
    onSuccess: (r) => qc.setQueryData(opsKeys.freshness, r),
    onError: (e) => toast.error(errorMessage(e)),
  })
  const analyze = useMutation({
    mutationFn: (names: string[]) => api.startRun({ kind: "build", sources: names, resume: true }),
    onSuccess: (r) => {
      setRun(r.run_id)
      refreshOperations(qc)
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  return (
    <section className="source-freshness" id="source-freshness">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{t("ops.sourceChanges")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("ops.sourceHint")}</p>
        </div>
        <Button variant="outline" size="sm" disabled={scan.isPending} onClick={() => scan.mutate()}>
          <ScanLine aria-hidden />
          {t("ops.scan")}
        </Button>
      </div>
      {fresh.error ? (
        <ErrorState error={fresh.error} onRetry={() => fresh.refetch()} />
      ) : fresh.isPending ? (
        <p className="text-sm text-muted-foreground">{t("shell.status.checking")}</p>
      ) : (
        <ul>
          {fresh.data.sources.map((s) => (
            <li key={s.name} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-medium break-words">{s.name}</p>
                <p className="text-xs text-muted-foreground">
                  {s.last_built_at
                    ? t("ops.lastBuilt", { date: fmtTime(s.last_built_at) })
                    : t("ops.never_built")}
                </p>
                {s.reasons.map((r) => (
                  <p className="mt-1 text-xs text-muted-foreground" key={r.kind}>
                    {t("ops.sourceReason", {
                      kind:
                        t(`ops.reason.${r.kind}` as MessageKey) === `ops.reason.${r.kind}`
                          ? r.kind
                          : t(`ops.reason.${r.kind}` as MessageKey),
                      n: r.count,
                    })}
                    {r.examples.length ? " · " + r.examples.join(", ") : ""}
                  </p>
                ))}
                {s.state === "unknown" && <p className="text-xs text-warning">{s.error}</p>}
              </div>
              <Badge variant="outline">
                {t((s.state === "unknown" ? "ops.unknownState" : `ops.${s.state}`) as MessageKey)}
              </Badge>
              {s.state !== "fresh" && s.state !== "unknown" && (
                <Button size="sm" disabled={analyze.isPending} onClick={() => analyze.mutate([s.name])}>
                  {analyze.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  {t("ops.analyze")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap gap-3">
        <Button asChild size="sm" variant="ghost">
          <Link to="/build">{t("ops.building")}</Link>
        </Button>
        <Button asChild size="sm" variant="ghost">
          <Link to="/review">{t("ops.openReview")}</Link>
        </Button>
      </div>
      <RunSheet
        runId={run}
        onClose={() => {
          setRun(null)
          refreshOperations(qc)
        }}
      />
    </section>
  )
}
