import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight, Inbox } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router"

import { ApplyBanner } from "@/components/apply-banner"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ReviewCard } from "@/components/review-card"
import { RunSheet } from "@/components/run-sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { keys, retryServerErrors, usePendingCount } from "@/hooks/queries"
import { primaryDecision, suggestedRelation, useBuildChanged, useReviewActions } from "@/hooks/use-review"
import { fmtDate, fmtNumber, useT } from "@/i18n"
import {
  api, REVIEW_CATEGORIES, type AcceptRelation, type ReviewCategory, type ReviewStatus,
} from "@/lib/api"

export const PAGE_SIZE = 20
const STATUSES: ReviewStatus[] = ["pending", "decided", "all"]

const isCategory = (v: string | null): v is ReviewCategory => !!v && (REVIEW_CATEGORIES as string[]).includes(v)
const isStatus = (v: string | null): v is ReviewStatus => !!v && (STATUSES as string[]).includes(v)

/** Typing in a field, or a shortcut with a modifier: not ours. */
function ignoreKey(e: KeyboardEvent): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return true
  const el = e.target as HTMLElement | null
  if (!el) return false
  return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable || el.getAttribute("role") === "combobox"
    || !!el.closest("[role=listbox],[role=dialog]")
}

export default function ReviewPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const pending = usePendingCount()
  const counts = pending.data?.counts
  const status: ReviewStatus = isStatus(params.get("status")) ? params.get("status") as ReviewStatus : "pending"
  // no category in the URL: the first one with something to review
  const urlCategory = params.get("category")
  const category: ReviewCategory = isCategory(urlCategory) ? urlCategory
    : REVIEW_CATEGORIES.find((c) => (counts?.[c] ?? 0) > 0) ?? "proposals"
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1)
  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setParams(next, { replace: true })
  }

  const query = { category, status, page, size: PAGE_SIZE }
  const list = useQuery({
    queryKey: keys.reviewPage(query),
    queryFn: () => api.review(query),
    placeholderData: keepPreviousData,
    retry: retryServerErrors,
    // the list is not polled: a decided card keeps its place (badge, Undo) until the
    // category, page or filter changes; the counts poll on their own
    enabled: isCategory(urlCategory) || !!pending.data || pending.isError,
  })
  const { decide, undo, busyId, announcement } = useReviewActions()
  const [runId, setRunId] = useState<string | null>(null)
  const build = useBuildChanged(setRunId)
  const [relations, setRelations] = useState<Record<string, AcceptRelation>>({})
  const [selected, setSelected] = useState(0)
  const cards = useRef<(HTMLLIElement | null)[]>([])
  const items = useMemo(() => list.data?.items ?? [], [list.data])

  // a new list: start at its first card
  const listKey = `${category}|${status}|${page}`
  const [seenKey, setSeenKey] = useState(listKey)
  if (seenKey !== listKey) {
    setSeenKey(listKey)
    setSelected(0)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (ignoreKey(e) || !items.length) return
      const key = e.key.toLowerCase()
      if (key === "j" || key === "k") {
        e.preventDefault()
        const next = Math.min(items.length - 1, Math.max(0, selected + (key === "j" ? 1 : -1)))
        setSelected(next)
        cards.current[next]?.focus()
        cards.current[next]?.scrollIntoView({ block: "nearest" })
        return
      }
      if (key !== "a" && key !== "r") return
      const item = items[selected]
      if (!item || item.decision || busyId) return
      const decision = primaryDecision(item, key)
      if (!decision) return
      e.preventDefault()
      decide({ item, decision, relation: decision === "accept" ? relations[item.id] ?? suggestedRelation(item) : undefined })
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [items, selected, relations, busyId, decide])

  const checkedAt = list.dataUpdatedAt ? new Date(list.dataUpdatedAt).toISOString() : null
  return (
    <>
      <PageHeader title={t("review.title")} description={t("review.description")} />
      <ApplyBanner className="mb-4" />
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1 overflow-x-auto">
          <Tabs value={category} onValueChange={(v) => update({ category: v, page: null })}>
            <TabsList id="review-tabs" aria-label={t("review.tabs")}>
              {REVIEW_CATEGORIES.map((c) => (
                <TabsTrigger key={c} value={c} data-tab={c} className="gap-1.5">
                  {t(`review.tab.${c}`)}
                  <Badge variant="secondary" className="h-5 min-w-5 px-1 font-normal tabular-nums" data-count={counts?.[c] ?? 0}>
                    {fmtNumber(counts?.[c] ?? 0)}
                  </Badge>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        <Select value={status} onValueChange={(v) => update({ status: v === "pending" ? null : v, page: null })}>
          <SelectTrigger id="review-status" aria-label={t("review.status")} className="w-full sm:w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            {STATUSES.map((s) => <SelectItem key={s} value={s} data-status={s}>{t(`review.status.${s}`)}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      <p className="mb-3 text-sm text-muted-foreground" id="review-help">{t(`review.help.${category}`)}</p>
      <p className="sr-only" aria-live="polite" id="review-live">{announcement}</p>

      {list.error ? <ErrorState error={list.error} onRetry={() => list.refetch()} />
        : !list.data ? <div className="grid gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}</div>
        : !items.length ? (
          <EmptyState icon={Inbox} title={status === "decided" ? t("review.empty.decided") : t("review.empty")}
                      hint={checkedAt ? t("review.empty.hint", { time: fmtDate(checkedAt) }) : undefined} />
        ) : (
          <>
            <p className="mb-2 hidden text-xs text-muted-foreground sm:block">{t("review.keys")}</p>
            <ul id="review-list" className="grid grid-cols-[minmax(0,1fr)] gap-3">
              {items.map((item, i) => (
                <ReviewCard key={item.id} ref={(el) => { cards.current[i] = el }} item={item}
                            selected={i === selected} busy={busyId === item.id}
                            relation={relations[item.id]}
                            onRelation={(r) => setRelations((cur) => ({ ...cur, [item.id]: r }))}
                            onDecide={(args) => { setSelected(i); decide(args) }}
                            onUndo={(it) => { setSelected(i); undo(it) }}
                            onBuild={(sources) => build.mutate(sources)} />
              ))}
            </ul>
            {list.data.pages > 1 && (
              <nav aria-label={t("ontology.pages")} className="mt-4 flex items-center justify-center gap-3" id="review-pages">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => update({ page: page > 2 ? String(page - 1) : null })}>
                  <ChevronLeft aria-hidden /> {t("review.previous")}
                </Button>
                <span className="text-sm text-muted-foreground">{t("review.page", { page, pages: list.data.pages })}</span>
                <Button variant="outline" size="sm" disabled={page >= list.data.pages} onClick={() => update({ page: String(page + 1) })}>
                  {t("review.next")} <ChevronRight aria-hidden />
                </Button>
              </nav>
            )}
          </>
        )}
      <RunSheet runId={runId} onClose={() => setRunId(null)} />
    </>
  )
}
