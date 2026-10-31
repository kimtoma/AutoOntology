import { FeedbackCaseButton } from "@/components/feedback-case"
import { RuleFields, RuleSummary } from "@/components/rule-fields"
import { emptyRule, type MonetaryRule } from "@/lib/rules"
import { CaseBoard } from "@/components/case-board"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"
import { Loader2 } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { OriginalLink } from "@/components/original-link"
import { isFirstOriginal } from "@/lib/originals"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { RunSheet } from "@/components/run-sheet"
import { refreshOperations, useChanges, useLegacy } from "@/hooks/operations"
import { api, errorMessage } from "@/lib/api"
import { operations, type Evidence, type KnowledgeChange, type LegacyReview } from "@/lib/operations"
import { useT, type MessageKey } from "@/i18n"
import { knowledgeText } from "@/lib/operations"

function EvidenceList({ items }: { items: Evidence[] }) {
  const t = useT()
  return (
    <section className="review-evidence">
      <h3 className="mb-2 font-medium">{t("ops.evidence")}</h3>
      {items.length ? (
        <ul className="grid gap-3">
          {items.map((e, i) => (
            <li key={i}>
              <blockquote className="border-l-2 border-primary/40 pl-3 text-sm">{e.quote}</blockquote>
              <p className="mt-1 text-xs text-muted-foreground">
                {e.document}
                {e.page !== null ? ` · p.${e.page}` : ""}
                {e.version ? " · " + t(e.version === "candidate" ? "ops.after" : "ops.before") : ""}
              </p>
              {isFirstOriginal(items, i) && <OriginalLink original={e.original} />}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-warning">{t("ops.noEvidence")}</p>
      )}
    </section>
  )
}
function ChangeDetail({ item }: { item: KnowledgeChange }) {
  const t = useT(),
    qc = useQueryClient()
  const [note, setNote] = useState(""),
    [error, setError] = useState("")
  const [editing, setEditing] = useState(false),
    [editedLabel, setEditedLabel] = useState(String(item.after?.label || item.label)),
    [editedText, setEditedText] = useState(knowledgeText(item.after))
  const displayedRule = item.decision?.edits?.rule ?? item.after?.rule
  const ruleCandidate = item.collection === "individuals" && item.after?.type === "BusinessRule"
  const [structuredRule, setStructuredRule] = useState<MonetaryRule>({...emptyRule, ...(item.after?.rule as Partial<MonetaryRule> ?? {})})
  const [ruleEnabled, setRuleEnabled] = useState(!!item.after?.rule && Object.keys(item.after.rule as object).length > 0)
  const editable = !!item.after && ["classes", "individuals", "properties"].includes(item.collection)
  const save = useMutation({
    mutationFn: (decision: "accept" | "reject" | "hold") =>
      operations.decide(
        item,
        decision,
        note,
        editing && decision === "accept"
          ? {
              label: editedLabel.trim(),
              ...(ruleCandidate ? { rule: ruleEnabled ? structuredRule : {} } : {}),
              ...(item.collection === "classes"
                ? {
                    definitions: editedText
                      .split("\n")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  }
                : { comment: editedText.trim() }),
            }
          : {},
      ),
    onSuccess: () => {
      refreshOperations(qc)
      setEditing(false)
      toast.success(t("ops.saved"))
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const undo = useMutation({
    mutationFn: () => operations.undo(item.id),
    onSuccess: () => refreshOperations(qc),
    onError: (e) => setError(errorMessage(e)),
  })
  const needsEvidence =
    !!item.after &&
    ["classes", "individuals"].includes(item.collection) &&
    item.after.source !== "structured" &&
    !item.evidence.some((e) => e.version === "candidate")
  const decide = (d: "accept" | "reject" | "hold") => {
    setError("")
    if (d === "reject" && !note.trim()) {
      setError(t("ops.noteRequired"))
      return
    }
    if (editing && d === "accept" && !note.trim()) {
      setError(t("ops.editReason"))
      return
    }
    save.mutate(d)
  }
  return (
    <article className="review-detail" id="review-detail">
      <div className="review-detail-heading">
        <span className="review-kind">{t(`ops.${item.kind}`)}</span>
        <h2>{item.label}</h2>
      </div>
      {!!displayedRule && Object.keys(displayedRule as object).length>0 && <section className="mb-4 border-y py-3"><h3 className="text-sm font-medium">{t("quality.ruleFields")}</h3><RuleSummary value={displayedRule as Record<string,unknown>}/></section>}
      <div className="review-compare">
        {[
          ["ops.before", item.before],
          ["ops.after", item.after ? { ...item.after, ...item.decision?.edits } : null],
        ].map(([label, value]) => (
          <div
            key={String(label)}
            className="review-diff-cell"
            data-side={label === "ops.before" ? "before" : "after"}
          >
            <p className="review-diff-label">
              <span aria-hidden>{label === "ops.before" ? "−" : "+"}</span>
              {t(label as MessageKey)}
            </p>
            <p className="break-words text-sm whitespace-pre-wrap">
              {label === "ops.before"
                ? item.before_text || knowledgeText(value as Record<string, unknown> | null)
                : !Object.keys(item.decision?.edits || {}).length && item.after_text
                  ? item.after_text
                  : knowledgeText(value as Record<string, unknown> | null)}
            </p>
          </div>
        ))}
      </div>
      {editing && (
        <div className="my-4 grid gap-3 rounded-lg border p-4">
          <label htmlFor="proposal-label" className="grid gap-2 text-sm">
            {t("ops.field.label")}
            <input
              id="proposal-label"
              className="rounded-md border bg-background p-2"
              value={editedLabel}
              onChange={(e) => setEditedLabel(e.target.value)}
              maxLength={5000}
            />
          </label>
          <div className="grid gap-2 text-sm">
            <label htmlFor="proposal-definition">{t("ops.field.definitions")}</label>
            <Textarea
              id="proposal-definition"
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              maxLength={5000}
            />
          </div>
{editing && ruleCandidate && <section className="my-4 space-y-3"><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={ruleEnabled} onChange={event=>setRuleEnabled(event.target.checked)}/>{t("quality.structureRule")}</label>{ruleEnabled && <RuleFields value={structuredRule} onChange={setStructuredRule}/>}</section>}
          <p className="text-xs text-muted-foreground">{t("ops.editHint")}</p>
        </div>
      )}
      <EvidenceList items={item.evidence} />
      <div className="review-reason">
        <label htmlFor="review-note" className="text-sm font-medium">
          {t("ops.note")}
        </label>
        <Textarea
          id="review-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          placeholder={t("ops.noteHint")}
          className="mt-2"
        />
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}
      {item.decision ? (
        <div className="mt-4 grid gap-3">
          <Badge variant="secondary" className="w-fit">
            {t(
              ({ accept: "ops.accepted", hold: "ops.held", reject: "ops.rejected" } as const)[
                item.decision.decision
              ],
            )}
          </Badge>
          <p className="text-sm text-muted-foreground">
            {item.decision.note} · {item.decision.actor}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={undo.isPending} onClick={() => undo.mutate()}>
              {t("ops.undo")}
            </Button>
            {item.decision.decision === "accept" && (
              <Button asChild>
                <Link to="/history">{t("ops.prepare")}</Link>
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="review-actions">
          <Button disabled={save.isPending || needsEvidence} onClick={() => decide("accept")}>
            {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {t(editing ? "ops.acceptEdited" : "ops.accept")}
          </Button>

          {editable && (
            <Button variant="outline" disabled={save.isPending} onClick={() => setEditing(!editing)}>
              {t(editing ? "common.cancel" : "ops.edit")}
            </Button>
          )}
          <Button variant="outline" disabled={save.isPending} onClick={() => decide("hold")}>
            {t("ops.hold")}
          </Button>
          <Button variant="ghost" disabled={save.isPending} onClick={() => decide("reject")}>
            {t("ops.reject")}
          </Button>
        </div>
      )}
      <p className="mt-3 text-xs text-muted-foreground">{t("ops.currentUnchanged")}</p>
    </article>
  )
}
function ConnectionDetail({ item }: { item: LegacyReview }) {
  const t = useT(),
    qc = useQueryClient(),
    [note, setNote] = useState(""),
    [error, setError] = useState(""),
    [relation, setRelation] = useState(item.relation || "related"),
    [term, setTerm] = useState(item.term?.id || "")
  const save = useMutation({
    mutationFn: (decision: string) =>
      operations.legacyDecision(
        item.id,
        decision,
        note,
        decision === "accept" ? relation : undefined,
        decision === "accept" ? term : undefined,
      ),
    onSuccess: () => {
      refreshOperations(qc)
      toast.success(t("ops.saved"))
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const undo = useMutation({
    mutationFn: () => operations.undoLegacy(item.id),
    onSuccess: () => refreshOperations(qc),
    onError: (e) => setError(errorMessage(e)),
  })
  const hold = useMutation({
    mutationFn: () => operations.holdConnection(item.id, note),
    onSuccess: () => refreshOperations(qc),
    onError: (e) => setError(errorMessage(e)),
  })
  const unhold = useMutation({
    mutationFn: () => operations.unholdConnection(item.id),
    onSuccess: () => refreshOperations(qc),
    onError: (e) => setError(errorMessage(e)),
  })
  const decide = (decision: string) => {
    setError("")
    if (decision === "reject" && !note.trim()) {
      setError(t("ops.noteRequired"))
      return
    }
    save.mutate(decision)
  }
  return (
    <article className="min-w-0">
      <Badge variant="outline">{item.category}</Badge>
      <h2 className="my-3 text-lg font-semibold">{item.concept?.name || item.id}</h2>
      <p className="text-sm">{item.concept?.definition}</p>
      {item.term && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-xs">
            {t("ops.term")}
            <select
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              className="min-w-0 rounded-md border bg-background p-2 text-sm"
            >
              {(item.candidates?.length ? item.candidates : [item.term]).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-xs">
            {t("ops.relation")}
            <select
              value={relation}
              onChange={(e) => setRelation(e.target.value)}
              className="min-w-0 rounded-md border bg-background p-2 text-sm"
            >
              {["same", "related", "broader", "narrower"].map((r) => (
                <option key={r} value={r}>
                  {t(`ops.${r}` as MessageKey)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {item.rationale && (
        <div className="mt-4 rounded-lg bg-muted/60 p-3 text-sm">
          <p className="mb-1 text-xs text-muted-foreground">{t("ops.reason")}</p>
          {item.rationale}
        </div>
      )}
      <EvidenceList items={item.evidence || []} />
      <label className="mt-5 grid gap-2 text-sm">
        {t("ops.note")}
        <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </label>
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {item.hold ? (
          <>
            <Badge variant="secondary">{t("ops.held")}</Badge>
            <Button variant="outline" disabled={unhold.isPending} onClick={() => unhold.mutate()}>
              {t("ops.undo")}
            </Button>
          </>
        ) : item.decision ? (
          <Button variant="outline" disabled={undo.isPending} onClick={() => undo.mutate()}>
            {t("ops.undo")}
          </Button>
        ) : (
          <>
            <Button
              disabled={save.isPending}
              onClick={() =>
                decide(item.category === "unaligned" ? "acknowledge" : item.suggested_action || "accept")
              }
            >
              {t(item.category === "unaligned" ? "ops.acknowledge" : "ops.accept")}
            </Button>
            <Button variant="outline" disabled={hold.isPending} onClick={() => hold.mutate()}>
              {t("ops.hold")}
            </Button>
            {item.category !== "unaligned" && (
              <Button variant="outline" disabled={save.isPending} onClick={() => decide("reject")}>
                {t("ops.reject")}
              </Button>
            )}
          </>
        )}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{t("ops.applyHint")}</p>
    </article>
  )
}
export default function ReviewPage() {
  const t = useT(),
    qc = useQueryClient(),
    [params, setParams] = useSearchParams(),
    changes = useChanges()
  const [page, setPage] = useState(1),
    [filter, setFilter] = useState("all")
  const legacy = useLegacy(
    page,
    filter === "hold" ? "held" : filter === "all" ? "all" : filter === "decided" ? "decided" : "pending",
  )
  const [chosenTab, setTab] = useState(params.get("tab") || ""),
    [selected, setSelected] = useState(params.get("item") || ""),
    [run, setRun] = useState<string | null>(null)
  const tab = params.get("tab") || chosenTab || (params.has("item") ? "changes" : "cases")
  const apply = useMutation({
    mutationFn: () => api.startRun({ kind: "apply" }),
    onSuccess: (r) => {
      setRun(r.run_id)
      refreshOperations(qc)
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  const items =
    changes.data?.items.filter((i) =>
      filter === "all" || filter === "pending"
        ? !i.decision || filter === "all"
        : filter === "hold"
          ? i.decision?.decision === "hold"
          : !!i.decision,
    ) || []
  const selectedIndex = items.findIndex((i) => i.id === selected || i.key === params.get("subject"))
  const changePage =
    selectedIndex >= 0
      ? Math.floor(selectedIndex / 10) + 1
      : Math.min(page, Math.max(1, Math.ceil(items.length / 10)))
  const visibleItems = items.slice((changePage - 1) * 10, changePage * 10)
  const item = items.find((i) => i.id === selected || i.key === params.get("subject")) || visibleItems[0],
    connection = legacy.data?.items.find((i) => i.id === selected) || legacy.data?.items[0]
  const choose = (id: string) => {
    setSelected(id)
    setParams({ tab, item: id }, { replace: true })
  }
  return (
    <>
      <PageHeader
        title={t("ops.review")}
        description={t("ops.reviewHint")}
        actions={
          <Button asChild variant="outline">
            <Link to="/history">{t("ops.history")}</Link>
          </Button>
        }
      />
      <div className="review-tabs" role="tablist" aria-label={t("ops.review")}>
        {[
          ["cases", "cases.title"],
          ["changes", "ops.changes"],
          ["connections", "ops.alignments"],
          ["feedback", "ops.feedback"],
        ].map(([id, label]) => (
          <Button
            role="tab"
            aria-selected={tab === id}
            key={id}
            variant={tab === id ? "default" : "outline"}
            onClick={() => {
              setTab(id)
              setPage(1)
              setSelected("")
              setParams({ tab: id }, { replace: true })
            }}
          >
            {t(label as MessageKey)}
            <span className="console-counter">
              {id === "changes" && changes.data
                ? changes.data.items.length
                : id === "connections" && legacy.data
                  ? legacy.data.total
                  : id === "feedback" && changes.data
                    ? changes.data.feedback.length
                    : "—"}
            </span>
          </Button>
        ))}
      </div>
      {tab !== "feedback" && tab !== "cases" && (
        <div className="review-filters">
          {[
            ["all", "ops.all"],
            ["pending", "ops.onlyPending"],
            ["hold", "ops.onlyHeld"],
            ["decided", "ops.onlyDecided"],
          ].map(([id, label]) => (
            <Button
              key={id}
              size="sm"
              variant={filter === id ? "secondary" : "ghost"}
              aria-pressed={filter === id}
              onClick={() => {
                setFilter(id)
                setPage(1)
                setSelected("")
              }}
            >
              {t(label as MessageKey)}
            </Button>
          ))}
        </div>
      )}
      {tab === "cases" ? <CaseBoard /> : (tab === "changes" ? changes.error : tab === "connections" ? legacy.error : changes.error) ? (
        <ErrorState
          error={changes.error || legacy.error}
          onRetry={() => {
            changes.refetch()
            legacy.refetch()
          }}
        />
      ) : tab === "feedback" ? (
        <div className="grid gap-4">
          {changes.data?.feedback.length ? (
            changes.data.feedback.map((f) => (
              <article key={f.id} className="rounded-xl border bg-card p-5">
                <h2 className="font-semibold">{f.question}</h2>
                <p className="my-3 text-sm text-muted-foreground whitespace-pre-wrap">{f.answer}</p>
                <p className="mb-4 text-sm">{f.note}</p>
                <div className="mb-3"><FeedbackCaseButton id={f.id} question={f.question}/></div>
                <div className="flex flex-wrap gap-2">
                  <Button asChild variant="outline">
                    <Link to="/sources">{t("nav.sources")}</Link>
                  </Button>

                </div>
              </article>
            ))
          ) : (
            <EmptyState title={t("ops.noReview")} hint={t("ops.noReviewHint")} />
          )}
        </div>
      ) : (tab === "changes" ? items.length : legacy.data?.items.length) ? (
        <div className="review-workbench">
          <div className="review-list" aria-label={t("ops.review")}>
            {tab === "changes"
              ? visibleItems.map((i) => (
                  <button
                    type="button"
                    key={i.id}
                    onClick={() => choose(i.id)}
                    aria-pressed={item?.id === i.id}
                    className="review-list-item"
                  >
                    <p className="mb-1 text-xs text-muted-foreground">
                      {t(`ops.${i.kind}`)}
                      {i.decision
                        ? " · " +
                          t(
                            ({ accept: "ops.accepted", hold: "ops.held", reject: "ops.rejected" } as const)[
                              i.decision.decision
                            ],
                          )
                        : ""}
                    </p>
                    <p className="text-sm font-medium">{i.label}</p>
                  </button>
                ))
              : legacy.data?.items.map((i) => (
                  <button
                    type="button"
                    key={i.id}
                    onClick={() => choose(i.id)}
                    aria-pressed={connection?.id === i.id}
                    className="review-list-item"
                  >
                    <p className="mb-1 text-xs text-muted-foreground">{i.concept?.source}</p>
                    <p className="text-sm font-medium">{i.concept?.name || i.id}</p>
                    <p className="text-xs text-muted-foreground">{i.term?.name}</p>
                  </button>
                ))}
          </div>
          {tab === "changes" && item ? (
            <ChangeDetail key={item.id} item={item} />
          ) : connection ? (
            <ConnectionDetail key={connection.id} item={connection} />
          ) : null}
        </div>
      ) : (
        <EmptyState title={t("ops.noReview")} hint={t("ops.noReviewHint")} />
      )}
      {tab === "changes" && items.length > 10 && (
        <div className="mt-6 flex items-center gap-3">
          <Button
            variant="outline"
            disabled={changePage === 1}
            onClick={() => {
              setSelected("")
              setPage(changePage - 1)
            }}
          >
            {t("ops.previous")}
          </Button>
          <span className="text-sm tabular-nums">
            {changePage} / {Math.ceil(items.length / 10)}
          </span>
          <Button
            variant="outline"
            disabled={changePage >= Math.ceil(items.length / 10)}
            onClick={() => {
              setSelected("")
              setPage(changePage + 1)
            }}
          >
            {t("ops.next")}
          </Button>
        </div>
      )}
      {tab === "connections" && (
        <div className="mt-6 grid gap-3">
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={page === 1}
              onClick={() => {
                setPage(page - 1)
                setSelected("")
              }}
            >
              {t("ops.previous")}
            </Button>
            <Button
              variant="outline"
              disabled={!legacy.data || page >= legacy.data.pages}
              onClick={() => {
                setPage(page + 1)
                setSelected("")
              }}
            >
              {t("ops.next")}
            </Button>
          </div>
          {!!legacy.data?.unapplied && (
            <div className="rounded-lg border p-4">
              <p className="mb-3 text-sm text-muted-foreground">{t("ops.applyHint")}</p>
              <Button disabled={apply.isPending} onClick={() => apply.mutate()}>
                {t("ops.apply")}
              </Button>
            </div>
          )}
        </div>
      )}
      <RunSheet
        runId={run}
        onClose={() => {
          setRun(null)
          refreshOperations(qc)
        }}
      />
    </>
  )
}
