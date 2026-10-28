import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { BookOpen, ChevronLeft, ChevronRight, Search } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"

import { ConceptSheet, RelationChip } from "@/components/concept-sheet"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, useSources } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { api } from "@/lib/api"
import { CKIND_KO } from "@/lib/labels"

const ALL = "__all"
const KINDS = ["entity", "metric", "attribute", "process", "rule", "other"]
const SIZE = 30

export default function ConceptsPage() {
  const [params, setParams] = useSearchParams()
  const urlQ = params.get("q") ?? ""
  const [q, setQ] = useState(urlQ)
  // the URL can change under us (⌘K search, back/forward): follow it
  const [seenUrlQ, setSeenUrlQ] = useState(urlQ)
  if (urlQ !== seenUrlQ) {
    setSeenUrlQ(urlQ)
    if (urlQ !== q.trim()) setQ(urlQ)
  }
  const kind = params.get("kind") ?? ""
  const source = params.get("source") ?? ""
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1)
  const conceptId = params.get("concept")
  const debounced = useDebounced(q.trim(), 300)

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    setParams(next, { replace: true })
  }
  // the search box writes the URL after the debounce (and resets the page)
  useEffect(() => {
    if ((params.get("q") ?? "") !== debounced) update({ q: debounced || null, page: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  const query = { q: params.get("q") ?? "", kind, source, page, size: SIZE }
  const concepts = useQuery({
    queryKey: keys.concepts(query),
    queryFn: () => api.concepts(query),
    placeholderData: keepPreviousData,
  })
  const sources = useSources()
  const docSources = (sources.data ?? []).filter((s) => s.kind === "documents")

  return (
    <>
      <PageHeader title="개념" description="문서에서 추출한 개념을 이름·동의어·정의로 찾고 근거와 정렬 결과를 봅니다." />
      <form role="search" id="concept-search" onSubmit={(e) => { e.preventDefault(); update({ q: q.trim() || null, page: null }) }}
            className="mb-4 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input id="concept-q" type="search" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="이름·동의어·정의로 검색" aria-label="개념 검색" className="pl-8" />
        </div>
        <Select value={kind || ALL} onValueChange={(v) => update({ kind: v === ALL ? null : v, page: null })}>
          <SelectTrigger aria-label="종류" className="w-full sm:w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>모든 종류</SelectItem>
            {KINDS.map((k) => <SelectItem key={k} value={k}>{CKIND_KO[k]}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={source || ALL} onValueChange={(v) => update({ source: v === ALL ? null : v, page: null })}>
          <SelectTrigger aria-label="소스" className="w-full sm:w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>모든 문서 소스</SelectItem>
            {docSources.map((s) => <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </form>

      {concepts.error ? <ErrorState error={concepts.error} onRetry={() => concepts.refetch()} />
        : concepts.isPending ? (
          <div className="grid gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20" />)}</div>
        ) : (
          <>
            <p id="concept-summary" className="mb-3 text-sm text-muted-foreground" aria-live="polite">
              {concepts.data.total}개 개념{query.q ? ` · '${query.q}' 검색` : ""}
            </p>
            {!concepts.data.items.length ? (
              <EmptyState icon={BookOpen} title="결과가 없습니다"
                          hint={query.q || kind || source ? "검색어나 필터를 바꿔 보세요."
                            : <>문서 소스를 등록하고 <Link to="/runs" className="underline underline-offset-4">실행</Link>에서 문서 추출을 실행하세요.</>} />
            ) : (
              <ul id="concept-list" className="grid grid-cols-[minmax(0,1fr)] gap-2">
                {concepts.data.items.map((c) => (
                  <li key={c.id} className="min-w-0">
                    <button type="button" data-concept={c.id} onClick={() => update({ concept: c.id })}
                            className="w-full rounded-xl border bg-card px-4 py-3 text-left text-card-foreground
                                       shadow-xs transition-colors outline-none hover:bg-accent/50
                                       focus-visible:ring-[3px] focus-visible:ring-ring/50">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{c.name}</span>
                          {c.name_en && <span className="text-xs text-muted-foreground">{c.name_en}</span>}
                          <Badge variant="outline" className="font-normal">{CKIND_KO[c.kind] ?? c.kind}</Badge>
                          {c.source && <Badge variant="secondary" className="font-normal">{c.source}</Badge>}
                          {c.alignment && <RelationChip relation={c.alignment.relation} term={c.alignment.term} />}
                          <span className="text-xs text-muted-foreground">근거 {c.evidence_count}</span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{c.definition}</p>
                        {c.synonyms.length > 0 && (
                          <p className="mt-1 truncate text-xs text-muted-foreground">동의어: {c.synonyms.join(", ")}</p>
                        )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {concepts.data.pages > 1 && (
              <nav aria-label="페이지" className="mt-4 flex items-center justify-center gap-3">
                <Button variant="outline" size="sm" disabled={page <= 1}
                        onClick={() => update({ page: String(page - 1) })}>
                  <ChevronLeft aria-hidden /> 이전
                </Button>
                <span className="text-sm text-muted-foreground">{page} / {concepts.data.pages}</span>
                <Button variant="outline" size="sm" disabled={page >= concepts.data.pages}
                        onClick={() => update({ page: String(page + 1) })}>
                  다음 <ChevronRight aria-hidden />
                </Button>
              </nav>
            )}
          </>
        )}
      <ConceptSheet conceptId={conceptId} onClose={() => update({ concept: null })} />
    </>
  )
}
