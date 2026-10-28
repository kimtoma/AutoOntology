import { useQuery } from "@tanstack/react-query"

import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { api, type ConceptDetail } from "@/lib/api"
import { CKIND_KO, REL_KO } from "@/lib/labels"
import { cn } from "@/lib/utils"

const REL_TONE: Record<string, string> = {
  same: "border-success/30 bg-success/10 text-success",
  broader: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  narrower: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  related: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300",
}

export function RelationChip({ relation, term }: { relation: string; term?: string | null }) {
  return (
    <Badge variant="outline" className={cn("font-normal", REL_TONE[relation])}>
      {REL_KO[relation] ?? relation}{term ? ` → ${term}` : ""}
    </Badge>
  )
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

export function Chips({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((s) => <Badge key={s} variant="secondary" className="font-normal">{s}</Badge>)}
    </div>
  )
}

/** The concept detail sections (개념 sheet, graph detail panel). *omitBasics* drops 정의 /
    동의어 / 속성 when the caller already shows them (a Term node). */
export function ConceptBody({ c, omitBasics = false }: { c: ConceptDetail; omitBasics?: boolean }) {
  const aligns = c.alignments.filter((a) => a.relation !== "skipped")
  return (
    <div className="grid gap-5">
      {!omitBasics && <Section title="정의"><p className="text-sm leading-relaxed">{c.definition || "-"}</p></Section>}
      {!omitBasics && c.synonyms.length > 0 && <Section title="동의어"><Chips items={c.synonyms} /></Section>}
      {!omitBasics && c.attributes.length > 0 && <Section title="속성"><Chips items={c.attributes} /></Section>}
      <Section title="정렬">
        {aligns.length ? aligns.map((a, i) => (
          <div key={i} className="grid gap-1 text-sm">
            <div className="flex flex-wrap items-center gap-1.5">
              <RelationChip relation={a.relation} />
              <b>{a.term || "-"}</b>
              <span className="text-xs text-muted-foreground">
                {a.structured_source}
                {a.confidence != null && ` · 확신도 ${a.confidence.toFixed(2)}`}
                {a.accepted ? " · 적용됨" : a.dry_run ? " · 시험 실행" : ""}
              </span>
            </div>
            {a.rationale && <p className="text-xs text-muted-foreground">{a.rationale}</p>}
          </div>
        )) : <p className="text-sm text-muted-foreground">정렬 결과 없음</p>}
      </Section>
      {c.relations.length > 0 && (
        <Section title="관계">
          <ul className="grid gap-1 text-sm">
            {c.relations.map((r, i) => (
              <li key={i}>
                {r.subject} <code className="rounded bg-muted px-1 text-xs">{r.predicate}</code> {r.object}
                {r.label && r.label !== r.predicate && <span className="text-muted-foreground"> — {r.label}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Separator />
      <Section title={`근거 (${c.evidence.length})`}>
        {c.evidence.length ? (
          <ul className="grid gap-2">
            {c.evidence.map((e, i) => (
              <li key={i} className="quote rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-2">
                <blockquote className="text-sm leading-relaxed">“{e.quote}”</blockquote>
                <p className="mt-1 text-xs text-muted-foreground">
                  {e.document}{e.page ? ` · ${e.page}쪽` : ""}{e.section ? ` · ${e.section}` : ""}
                  {!e.verified && <span className="text-destructive"> · 원문 대조 실패</span>}
                </p>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">근거 없음</p>}
      </Section>
    </div>
  )
}

export function ConceptSheet({ conceptId, onClose }: { conceptId: string | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: keys.concept(conceptId ?? ""),
    queryFn: () => api.concept(conceptId!),
    enabled: !!conceptId,
  })
  const c = q.data
  return (
    <Sheet open={!!conceptId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 sm:max-w-lg" id="drawer"
                    onOpenAutoFocus={(e) => e.preventDefault()}>
        <SheetHeader className="border-b">
          <SheetTitle>{c?.name ?? "개념 상세"}</SheetTitle>
          <SheetDescription asChild>
            <div className="flex flex-wrap gap-1.5">
              {c && (
                <>
                  <Badge variant="outline" className="font-normal">{CKIND_KO[c.kind] ?? c.kind}</Badge>
                  {c.source && <Badge variant="secondary" className="font-normal">{c.source}</Badge>}
                  {c.name_en && <Badge variant="outline" className="font-normal">{c.name_en}</Badge>}
                </>
              )}
            </div>
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-4">
            {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
              : !c ? <div className="grid gap-3"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-20" /><Skeleton className="h-32" /></div>
              : <ConceptBody c={c} />}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
