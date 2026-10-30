import { VersionDiff } from "@/components/version-diff"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router"
import { Loader2, CheckCircle2, AlertTriangle } from "lucide-react"
import { toast } from "sonner"
import { PageHeader } from "@/components/page-header"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { RunSheet } from "@/components/run-sheet"
import { refreshOperations, useOperations, useLegacy, opsKeys } from "@/hooks/operations"
import { operations, type Validation } from "@/lib/operations"
import { api, errorMessage } from "@/lib/api"
import { fmtTime } from "@/lib/labels"
import { useT } from "@/i18n"

export default function HistoryPage() {
  const t = useT(),
    qc = useQueryClient(),
    overview = useOperations(),
    legacy = useLegacy(),
    revisions = useQuery({
      queryKey: opsKeys.revisions,
      queryFn: operations.revisions,
      refetchInterval: 5000,
    })
  const [savedValidation, saveValidation] = useState<{ result: Validation; context: string } | null>(null),
    [error, setError] = useState(""),
    [run, setRun] = useState<string | null>(null),
    [question, setQuestion] = useState(""),
    [expected, setExpected] = useState(""),
    [comparison, setComparison] = useState<Record<string, unknown> | null>(null)
  const candidate = overview.data?.candidate_revision,
    published = overview.data?.published_revision,
    approved = overview.data?.counts.approved ?? 0,
    checksKey = JSON.stringify(overview.data?.checks)
  const context = JSON.stringify([candidate, published, approved, checksKey])
  const validation = savedValidation?.context === context ? savedValidation.result : null
  const setValidation = (result: Validation | null) => saveValidation(result ? { result, context } : null)
  const validate = useMutation({
    mutationFn: () => operations.validate(candidate!),
    onSuccess: (r) => {
      setValidation(r)
      setError("")
    },
    onError: (e) => {
      setValidation(null)
      setError(errorMessage(e))
    },
  })
  const publish = useMutation({
    mutationFn: () => operations.publish(candidate!, validation!.token),
    onSuccess: (r) => {
      setRun(r.run_id)
      refreshOperations(qc)
      toast.success(t("ops.publishStarted"))
    },
    onError: (e) => {
      setValidation(null)
      setError(errorMessage(e))
    },
  })
  const apply = useMutation({
    mutationFn: () => api.startRun({ kind: "apply" }),
    onSuccess: (r) => {
      setRun(r.run_id)
      refreshOperations(qc)
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const add = useMutation({
    mutationFn: () => operations.addCheck(question.trim(), expected.trim()),
    onSuccess: () => {
      setQuestion("")
      setExpected("")
      setValidation(null)
      refreshOperations(qc)
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const remove = useMutation({
    mutationFn: operations.deleteCheck,
    onSuccess: () => {
      setValidation(null)
      refreshOperations(qc)
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const compare = useMutation({
    mutationFn: (revision: number) => operations.diff(revision, published!),
    onSuccess: setComparison,
    onError: (e) => setError(errorMessage(e)),
  })
  return (
    <div className="history-page">
      <PageHeader title={t("ops.history")} description={t("ops.historyHint")} />
      {overview.error ? (
        <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
      ) : (
        <>
          <section className="history-publication" id="publication-panel">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">{t("ops.ready")}</h2>
              {published && <Badge variant="outline">{t("ops.published", { n: published })}</Badge>}
            </div>
            <p className="mb-4 text-sm text-muted-foreground">{t("ops.publishHint")}</p>
            {!overview.data?.checks.length && (
              <p className="mb-4 text-sm text-warning" role="status">{t("ops.checksRequired")}</p>
            )}
            {approved > 0 && candidate ? (
              <>
                <Badge variant="secondary">
                  {t("ops.candidate", { n: candidate })} · {approved}
                </Badge>
                <p className="mt-3 text-sm">
                  {t("ops.excluded", {
                    n:
                      (overview.data?.counts.pending ?? 0) +
                      (overview.data?.counts.held ?? 0) +
                      (overview.data?.counts.rejected ?? 0),
                  })}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button
                    disabled={validate.isPending || publish.isPending || !overview.data?.checks.length}
                    onClick={() => validate.mutate()}
                  >
                    {validate.isPending && <Loader2 aria-hidden className="animate-spin" />}
                    {t("ops.prepare")}
                  </Button>
                  {validation?.ok && !!overview.data?.checks.length && (
                    <Button disabled={publish.isPending} onClick={() => publish.mutate()}>
                      {publish.isPending && <Loader2 aria-hidden className="animate-spin" />}
                      {t("ops.publish", { n: validation.approved })}
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <EmptyState
                title={t("ops.noApproved")}
                hint={t("ops.reviewFirst")}
                action={
                  <Button asChild variant="outline">
                    <Link to="/review">{t("ops.openReview")}</Link>
                  </Button>
                }
              />
            )}
            {validate.isPending && (
              <p className="mt-4 text-sm text-muted-foreground" role="status">
                {t("ops.validating")}
              </p>
            )}
            {validation && (
              <div className="mt-5 rounded-lg bg-muted/50 p-4">
                <div className="flex items-center gap-2 text-sm font-medium">
                  {validation.ok ? (
                    <CheckCircle2 className="size-4 text-success" aria-hidden />
                  ) : (
                    <AlertTriangle className="size-4 text-warning" aria-hidden />
                  )}
                  {validation.ok
                    ? t("ops.validationPassed", { n: validation.indexed_elements })
                    : t("ops.validationFailed")}
                </div>
                {!validation.checks_configured && (
                  <p className="mt-2 text-sm text-muted-foreground">{t("ops.noChecks")}</p>
                )}
                <ul className="mt-3 grid gap-3">
                  {validation.questions.map((q) => (
                    <li key={q.id}>
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <Badge variant="outline">{t(q.ok ? "ops.pass" : "ops.fail")}</Badge>
                        {q.question}
                      </div>
                      <p className="mt-2 text-sm whitespace-pre-wrap">{q.answer}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!legacy.data?.unapplied && (
              <div className="mt-5 border-t pt-4">
                <p className="mb-3 text-sm text-muted-foreground">{t("ops.applyHint")}</p>
                <Button variant="outline" disabled={apply.isPending} onClick={() => apply.mutate()}>
                  {t("ops.apply")}
                </Button>
              </div>
            )}
          </section>
          <section className="mt-7" id="quality-checks">
            <h2 className="mb-3 font-semibold">{t("ops.checks")}</h2>
            <form
              className="grid gap-3 rounded-xl border p-4 sm:grid-cols-2"
              onSubmit={(e) => {
                e.preventDefault()
                add.mutate()
              }}
            >
              <label className="grid gap-2 text-sm">
                {t("ops.question")}
                <Input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} />
              </label>
              <label className="grid gap-2 text-sm">
                {t("ops.expected")}
                <Input value={expected} onChange={(e) => setExpected(e.target.value)} maxLength={2000} />
              </label>
              <div className="sm:col-span-2">
                <Button variant="outline" disabled={!question.trim() || !expected.trim() || add.isPending}>
                  {t("ops.addCheck")}
                </Button>
              </div>
            </form>
            <ul className="mt-2 divide-y">
              {overview.data?.checks.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{c.question}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{c.expected}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(c.id)}
                  >
                    {t("ops.removeCheck")}
                  </Button>
                </li>
              ))}
            </ul>
          </section>
          <section className="mt-7">
            <h2 className="mb-3 font-semibold">{t("ops.versions")}</h2>
            {revisions.error ? (
              <ErrorState error={revisions.error} onRetry={() => revisions.refetch()} />
            ) : (
              <ul className="divide-y">
                {revisions.data?.revisions.map((r) => (
                  <li key={r.revision} className="flex flex-wrap items-center justify-between gap-3 py-4">
                    <div>
                      <p className="font-medium">
                        r{r.revision} ·{" "}
                        {r.revision === published
                          ? t("ops.published", { n: r.revision })
                          : r.run_kind === "publish"
                            ? t("ops.history")
                            : r.revision === candidate
                              ? t("ops.candidate", { n: r.revision })
                              : r.run_kind}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {fmtTime(r.created_at)} · {r.sources.map((s) => s.name).join(", ")}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {published && r.revision !== published && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={compare.isPending}
                          onClick={() => compare.mutate(r.revision)}
                        >
                          {t("ops.compare")}
                        </Button>
                      )}
                      {(r.revision === published || r.run_kind === "publish") && (
                        <Button asChild size="sm" variant="ghost">
                          <Link to={`/exports?revision=${r.revision}`}>{t("ops.download")}</Link>
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {comparison && <VersionDiff value={comparison} />}
        </>
      )}
      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-destructive/30 p-3 text-sm text-destructive">
          {error}
        </p>
      )}
      <RunSheet
        runId={run}
        onClose={() => {
          setRun(null)
          refreshOperations(qc)
        }}
      />
    </div>
  )
}
