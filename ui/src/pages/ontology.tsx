import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { BookOpen, ChevronLeft, ChevronRight, Hammer, Search } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"

import { ConceptSheet, OriginBadge, RelationChip } from "@/components/concept-sheet"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ScopeNotice } from "@/components/scope-notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, retryServerErrors, useOntology } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { api, isNoSnapshot, type OntologySummary } from "@/lib/api"
import { fmtDate, fmtNumber, t, tn, useT } from "@/i18n"
import { CKIND_LABEL, ORIGIN_LABEL } from "@/lib/labels"

const ALL = "__all"
const KINDS = ["term", "entity", "metric", "attribute", "process", "rule", "other"]
const ORIGINS = ["structured", "document", "aligned"]
const SIZE = 30

function num(v: unknown): number {
  return typeof v === "number" ? v : 0
}

function Summary({ o }: { o: OntologySummary }) {
  const c = o.counts
  const stats: [string, number][] = [
    [t("ontology.stat.terms"), num(c.classes_structured)],
    [t("ontology.stat.documentClasses"), num(c.classes_document)],
    [t("ontology.stat.individuals"), num(c.individuals)],
    [t("ontology.stat.relations"), num(c.object_properties)],
    [t("ontology.stat.merged"), num(c.merged_concepts)],
    [t("ontology.stat.evidence"), num(c.evidence)],
  ]
  return (
    <div className="mb-6 grid gap-3" id="ontology-summary" data-revision={o.revision}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="secondary" className="font-normal tabular-nums">{t("ontology.revision", { n: o.revision })}</Badge>
        <Badge variant="outline" className="font-normal">{t("ontology.workspace", { name: o.workspace })}</Badge>
        <span className="text-xs text-muted-foreground">{t("ontology.built", { date: fmtDate(o.created_at) })}</span>
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground" title={o.base_iri}>{o.base_iri}</span>
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {stats.map(([k, v]) => (
          <div key={k} className="rounded-lg border px-3 py-2">
            <dt className="text-xs text-muted-foreground">{k}</dt>
            <dd className="text-lg font-semibold tabular-nums">{fmtNumber(v)}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        {t("ontology.sources", { list: o.sources.map((s) => `${s.name} (${s.kind})`).join(", ") || "-" })}
      </p>
    </div>
  )
}

export default function OntologyPage() {
  const t = useT()
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
  const origin = params.get("origin") ?? ""
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

  const ontology = useOntology()
  const query = { q: params.get("q") ?? "", kind, origin, page, size: SIZE }
  const concepts = useQuery({
    queryKey: keys.concepts(query),
    queryFn: () => api.concepts(query),
    placeholderData: keepPreviousData,
    retry: retryServerErrors,
  })
  const noSnapshot = isNoSnapshot(ontology.error) || isNoSnapshot(concepts.error)
  // a stale ?page past the end (the list shrank after a rebuild): go to the last page
  const lastPage = concepts.data && !concepts.isPlaceholderData ? Math.max(1, concepts.data.pages) : null
  useEffect(() => {
    if (lastPage !== null && page > lastPage) update({ page: lastPage > 1 ? String(lastPage) : null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastPage, page])

  return (
    <>
      <PageHeader title={t("ontology.title")} description={t("ontology.description")} />
      {noSnapshot ? (
        <EmptyState icon={Hammer} title={t("ontology.empty.title")}
                    hint={t("ontology.empty.hint")}
                    action={<Button asChild id="btn-go-build"><Link to="/build"><Hammer aria-hidden /> {t("ontology.goBuild")}</Link></Button>} />
      ) : (
        <>
          {ontology.error ? <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />
            : ontology.data ? <><ScopeNotice access={ontology.data.access} /><Summary o={ontology.data} /></>
            : <Skeleton className="mb-6 h-28" />}
          <form role="search" id="concept-search" onSubmit={(e) => { e.preventDefault(); update({ q: q.trim() || null, page: null }) }}
                className="mb-4 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <Input id="concept-q" type="search" value={q} onChange={(e) => setQ(e.target.value)}
                     placeholder={t("ontology.search.placeholder")} aria-label={t("ontology.search.label")} className="pl-8" />
            </div>
            <Select value={origin || ALL} onValueChange={(v) => update({ origin: v === ALL ? null : v, page: null })}>
              <SelectTrigger aria-label={t("ontology.filter.origin")} id="filter-origin" className="w-full sm:w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("ontology.filter.allOrigins")}</SelectItem>
                {ORIGINS.map((o) => <SelectItem key={o} value={o} data-origin={o}>{ORIGIN_LABEL[o]}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={kind || ALL} onValueChange={(v) => update({ kind: v === ALL ? null : v, page: null })}>
              <SelectTrigger aria-label={t("ontology.filter.kind")} id="filter-kind" className="w-full sm:w-36"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("ontology.filter.allKinds")}</SelectItem>
                {KINDS.map((k) => <SelectItem key={k} value={k}>{CKIND_LABEL[k]}</SelectItem>)}
              </SelectContent>
            </Select>
          </form>

          {concepts.error ? <ErrorState error={concepts.error} onRetry={() => concepts.refetch()} />
            : !concepts.data ? (
              <div className="grid gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20" />)}</div>
            ) : (
              <>
                <p id="concept-summary" className="mb-3 text-sm text-muted-foreground" aria-live="polite">
                  {tn("ontology.count", concepts.data.total)}
                  {query.q ? ` ${t("ontology.matching", { q: query.q })}` : ""}
                  {origin ? ` · ${ORIGIN_LABEL[origin] ?? origin}` : ""}
                </p>
                {!concepts.data.items.length ? (
                  <EmptyState icon={BookOpen} title={t("ontology.noMatch.title")}
                              hint={t("ontology.noMatch.hint")} />
                ) : (
                  <ul id="concept-list" className="grid grid-cols-[minmax(0,1fr)] gap-2">
                    {concepts.data.items.map((c) => (
                      <li key={c.id} className="min-w-0">
                        <button type="button" data-concept={c.id} data-origin={c.origin}
                                onClick={() => update({ concept: c.id })}
                                className="w-full rounded-xl border bg-card px-4 py-3 text-left text-card-foreground
                                           shadow-xs transition-colors outline-none hover:bg-accent/50
                                           focus-visible:ring-[3px] focus-visible:ring-ring/50">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-medium">{c.name}</span>
                            <OriginBadge origin={c.origin} />
                            <Badge variant="outline" className="font-normal">{CKIND_LABEL[c.kind] ?? c.kind}</Badge>
                            {c.source && <Badge variant="secondary" className="max-w-full font-normal"><span className="truncate">{c.source}</span></Badge>}
                            {c.folded_into && <RelationChip relation="same" term={c.folded_into.name} />}
                            {!c.folded_into && c.alignment.best && c.alignment.best.accepted && (
                              <RelationChip relation={c.alignment.best.relation} term={c.alignment.best.label} />
                            )}
                            <span className="text-xs text-muted-foreground">{t("ontology.evidence", { n: c.evidence_count })}</span>
                          </div>
                          {c.definition && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{c.definition}</p>}
                          {c.merged_concepts.length > 0 && (
                            <p className="mt-1 truncate text-xs text-muted-foreground">{t("ontology.mergedList", { list: c.merged_concepts.join(", ") })}</p>
                          )}
                          {c.synonyms.length > 0 && (
                            <p className="mt-1 truncate text-xs text-muted-foreground">{t("ontology.synonymsList", { list: c.synonyms.join(", ") })}</p>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {concepts.data.pages > 1 && (
                  <nav aria-label={t("ontology.pages")} className="mt-4 flex items-center justify-center gap-3">
                    <Button variant="outline" size="sm" disabled={page <= 1}
                            onClick={() => update({ page: String(page - 1) })}>
                      <ChevronLeft aria-hidden /> {t("ontology.previous")}
                    </Button>
                    <span className="text-sm text-muted-foreground">{page} / {concepts.data.pages}</span>
                    <Button variant="outline" size="sm" disabled={page >= concepts.data.pages}
                            onClick={() => update({ page: String(page + 1) })}>
                      {t("ontology.next")} <ChevronRight aria-hidden />
                    </Button>
                  </nav>
                )}
              </>
            )}
        </>
      )}
      <ConceptSheet conceptId={conceptId} onClose={() => update({ concept: null })}
                    onOpen={(id) => update({ concept: id })} />
    </>
  )
}
