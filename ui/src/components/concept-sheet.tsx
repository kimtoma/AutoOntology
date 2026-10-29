import { useQuery } from "@tanstack/react-query"
import { ArrowLeft, ArrowRight, Network } from "lucide-react"
import type { ReactNode } from "react"
import { Link } from "react-router"

import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { keys, retryServerErrors } from "@/hooks/queries"
import { api, type ConceptDetail, type Origin } from "@/lib/api"
import { type MessageKey, t, tx, useT } from "@/i18n"
import { CKIND_LABEL, ORIGIN_LABEL, REL_LABEL } from "@/lib/labels"
import { cn } from "@/lib/utils"

const REL_TONE: Record<string, string> = {
  same: "border-success/30 bg-success/10 text-success",
  broader: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  narrower: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  related: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300",
}

/** Same hues as the graph's origin colours (graph-data ORIGIN_COLORS). */
const ORIGIN_TONE: Record<string, string> = {
  structured: "border-blue-600/30 bg-blue-600/10 text-blue-700 dark:text-blue-300",
  document: "border-violet-600/30 bg-violet-600/10 text-violet-700 dark:text-violet-300",
  aligned: "border-amber-500/50 bg-amber-500/15 text-amber-800 ring-1 ring-amber-500/40 dark:text-amber-300",
}

export function OriginBadge({ origin }: { origin: Origin | string }) {
  return (
    <Badge variant="outline" data-origin={origin} className={cn("font-normal", ORIGIN_TONE[origin])}>
      {ORIGIN_LABEL[origin] ?? origin}
    </Badge>
  )
}

export function RelationChip({ relation, term }: { relation: string; term?: string | null }) {
  return (
    <Badge variant="outline" className={cn("max-w-full font-normal", REL_TONE[relation])}>
      <span className="truncate">{REL_LABEL[relation] ?? relation}{term ? ` → ${term}` : ""}</span>
    </Badge>
  )
}

export function Section({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section className="grid min-w-0 gap-2" data-section={id}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

export function Chips({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((s) => <Badge key={s} variant="secondary" className="max-w-full font-normal"><span className="truncate">{s}</span></Badge>)}
    </div>
  )
}

const isConcept = (id: string) => /^(term|concept)-/.test(id)

/** The concept detail sections (Ontology sheet, graph detail panel). *omitBasics* drops the
    definition / synonyms when the caller already shows them. *onOpen* makes linked concepts
    (relations, alignments, the Term a concept folds into) clickable. */
export function ConceptBody({ c, omitBasics = false, onOpen }: {
  c: ConceptDetail
  omitBasics?: boolean
  onOpen?: (id: string) => void
}) {
  const t = useT()
  const other = (id: string, label: string) => (onOpen && isConcept(id)
    ? <button type="button" onClick={() => onOpen(id)} data-open={id}
              className="text-left font-medium underline-offset-4 hover:underline">{label || id}</button>
    : <b>{label || id}</b>)
  const definitions = c.definitions.length ? c.definitions : [c.description || c.definition].filter(Boolean)
  return (
    <div className="grid min-w-0 gap-5">
      {!omitBasics && (
        <Section title={t("concept.definition")} id="definition">
          {definitions.length
            ? definitions.map((d, i) => <p key={i} className="text-sm leading-relaxed">{d}</p>)
            : <p className="text-sm text-muted-foreground">-</p>}
        </Section>
      )}
      {!omitBasics && (c.synonyms.length > 0 || !!c.rejected_aliases?.length) && (
        <Section title={t("concept.synonyms")} id="synonyms">
          {c.synonyms.length > 0 && <Chips items={c.synonyms} />}
          {!!c.rejected_aliases?.length && (
            <p className="text-xs text-muted-foreground" data-testid="rejected-aliases">
              {t("concept.rejected", {
                list: c.rejected_aliases.map((r) => `${r.alias} (${t(`concept.rejected.${r.reason}` as MessageKey)})`).join(", "),
              })}
            </p>
          )}
        </Section>
      )}
      <Section title={t("concept.source")} id="source">
        <p className="text-sm">
          {c.source || "-"}
          {c.enriched_by && <span className="text-muted-foreground"> · {t("concept.enrichedBy", { name: c.enriched_by })}</span>}
          {c.individual_type && <span className="text-muted-foreground"> · {t("concept.instanceOf", { name: c.individual_type })}</span>}
        </p>
      </Section>
      {(c.tables.length > 0 || c.attributes.length > 0) && (
        <Section title={c.kind === "term" ? t("concept.tableColumns") : t("concept.attributes")} id="columns">
          {c.tables.length > 0 && <Chips items={c.tables} />}
          {c.attributes.length > 0 && (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("concept.attribute")}</TableHead>
                    <TableHead>{c.kind === "term" ? t("concept.column") : t("concept.range")}</TableHead>
                    <TableHead>{t("concept.type")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {c.attributes.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="text-xs whitespace-normal">{a.label}</TableCell>
                      <TableCell className="font-mono text-xs whitespace-normal break-all">{a.column || a.range}</TableCell>
                      <TableCell className="font-mono text-xs">{a.datatype || a.range.replace(/^xsd:/, "")}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </Section>
      )}
      {(c.merged_concepts.length > 0 || c.folded_into) && (
        <Section title={t("concept.merged")} id="merged">
          {c.folded_into && (
            <p className="text-sm">{tx("concept.foldedInto", { term: other(c.folded_into.id, c.folded_into.name) })}</p>
          )}
          {c.merged_concepts.length > 0 && (
            <>
              <p className="text-xs text-muted-foreground">{t("concept.foldedHere")}</p>
              <Chips items={c.merged_concepts} />
            </>
          )}
        </Section>
      )}
      <Section title={t("concept.alignments", { n: c.alignments.length })} id="alignments">
        {c.alignments.length ? (
          <ul className="grid gap-1.5">
            {c.alignments.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-1.5 text-sm">
                <RelationChip relation={a.relation} />
                {other(a.with, a.label)}
                <span className="text-xs text-muted-foreground">
                  {a.confidence != null && t("concept.confidence", { n: a.confidence.toFixed(2) })}
                  {" · "}{a.accepted ? t("concept.accepted") : t("concept.proposal")}
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">{t("concept.noAlignment")}</p>}
      </Section>
      {c.relations.length > 0 && (
        <Section title={t("concept.relations", { n: c.relations.length })} id="relations">
          <ul className="grid gap-1 text-sm">
            {c.relations.map((r, i) => (
              <li key={`${r.id}-${i}`} className="flex flex-wrap items-center gap-1.5">
                {r.direction === "out" ? <ArrowRight className="size-3.5 text-muted-foreground" aria-label={t("concept.outgoing")} />
                  : <ArrowLeft className="size-3.5 text-muted-foreground" aria-label={t("concept.incoming")} />}
                <code className="rounded bg-muted px-1 text-xs">{r.predicate}</code>
                {other(r.other, r.other_label)}
                {r.label && r.label !== r.predicate && <span className="text-xs text-muted-foreground">{r.label}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Separator />
      <Section title={t("concept.evidence", { n: c.evidence.length })} id="evidence">
        {c.evidence.length ? (
          <ul className="grid gap-2" id="evidence-list">
            {c.evidence.map((e, i) => (
              <li key={i} className="quote rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-2">
                <blockquote className="text-sm leading-relaxed">“{e.quote}”</blockquote>
                <p className="mt-1 text-xs break-all text-muted-foreground">
                  {e.document || e.path}{e.page ? ` · ${t("concept.page", { n: e.page })}` : ""}
                  {!e.verified && <span className="text-destructive"> · {t("concept.notVerbatim")}</span>}
                </p>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">{c.kind === "term" ? t("concept.noEvidenceTerm") : t("concept.noEvidence")}</p>}
      </Section>
    </div>
  )
}

/** The concept detail sheet of `/api/ontology/concepts/{id}`. */
export function ConceptSheet({ conceptId, onClose, onOpen }: {
  conceptId: string | null
  onClose: () => void
  /** open another concept (relations, alignments); default: not clickable */
  onOpen?: (id: string) => void
}) {
  const q = useQuery({
    queryKey: keys.concept(conceptId ?? ""),
    queryFn: () => api.concept(conceptId!),
    enabled: !!conceptId,
    retry: retryServerErrors,
  })
  const c = q.data
  return (
    <Sheet open={!!conceptId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 sm:max-w-lg" id="drawer"
                    focusSelf>
        <SheetHeader className="border-b pr-12">
          <SheetTitle className="break-words">{c?.name ?? t("concept.title")}</SheetTitle>
          <SheetDescription asChild>
            <div className="flex flex-wrap items-center gap-1.5">
              {c && (
                <>
                  <OriginBadge origin={c.origin} />
                  <Badge variant="outline" className="font-normal">{CKIND_LABEL[c.kind] ?? c.kind}</Badge>
                  <span className="text-xs">{t("concept.evidenceCount", { n: c.evidence_count })}</span>
                </>
              )}
            </div>
          </SheetDescription>
          {conceptId && (
            <div className="pt-1">
              <Button size="sm" variant="outline" asChild>
                <Link to={`/graph?focus=${encodeURIComponent(conceptId)}`} id="concept-graph-link">
                  <Network aria-hidden /> {t("concept.showInGraph")}
                </Link>
              </Button>
            </div>
          )}
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-4">
            {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
              : !c ? <div className="grid gap-3"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-20" /><Skeleton className="h-32" /></div>
              : <ConceptBody c={c} onOpen={onOpen} />}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
