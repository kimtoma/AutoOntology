import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Focus, X } from "lucide-react"
import { Link } from "react-router"

import { Chips, ConceptBody, OriginBadge, Section } from "@/components/concept-sheet"
import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, retryServerErrors } from "@/hooks/queries"
import { api } from "@/lib/api"
import type { GNode } from "@/lib/graph-data"
import { tn, useT, type MessageKey } from "@/i18n"
import { CKIND_LABEL } from "@/lib/labels"

const TYPE_LABEL: Record<string, MessageKey> = {
  class: "graph.nodeType.class", concept: "graph.nodeType.concept", individual: "graph.nodeType.individual",
  document: "graph.nodeType.document",
}

/** Detail of a graph node: its own fields, then the concept detail (`/api/ontology/concepts/{id}`:
    alignments, relations, evidence quotes) of the node, when it is a concept. */
export function GraphDetail({ node, neighbours, onSelect, onExpand, onClose }: {
  node: GNode
  neighbours: GNode[]
  onSelect: (id: string) => void
  onExpand: (id: string) => void
  onClose?: () => void
}) {
  const t = useT()
  const conceptId = node.type !== "document" && /^(term|concept)-/.test(node.id) ? node.id : null
  const q = useQuery({
    queryKey: keys.concept(conceptId ?? ""),
    queryFn: () => api.concept(conceptId!),
    enabled: !!conceptId,
    retry: retryServerErrors,
  })
  return (
    <div className="grid min-w-0 gap-5" id="graph-detail" data-node={node.id} data-origin={node.origin}>
      <div className="flex items-start gap-2">
        <div className="grid min-w-0 flex-1 gap-1.5">
          <h2 className="text-base leading-tight font-semibold break-words">{node.label}</h2>
          <div className="flex flex-wrap gap-1.5">
            {node.type !== "document" && <OriginBadge origin={node.origin} />}
            <Badge variant="outline" className="font-normal">
              <span className="size-2 rounded-full" style={{ background: node.color }} aria-hidden />
              {TYPE_LABEL[node.type] ? t(TYPE_LABEL[node.type]) : node.type}
              {node.type !== "document" && node.kind ? ` · ${CKIND_LABEL[node.kind] ?? node.kind}` : ""}
            </Badge>
            {node.source && <Badge variant="secondary" className="font-normal">{node.source}</Badge>}
            {node.enriched_by && <Badge variant="secondary" className="font-normal">+ {node.enriched_by}</Badge>}
            <Badge variant="outline" className="font-normal">{t("graph.links", { n: node.degree })}</Badge>
          </div>
        </div>
        {onClose && (
          <Button variant="ghost" size="icon" className="-mt-1 -mr-2 shrink-0" onClick={onClose}
                  aria-label={t("graph.closeDetail")}>
            <X aria-hidden />
          </Button>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onExpand(node.id)} id="graph-expand">
          <Focus aria-hidden /> {t("graph.neighboursOnly")}
        </Button>
        {conceptId && (
          <Button size="sm" variant="outline" asChild>
            <Link to={`/ontology?concept=${encodeURIComponent(conceptId)}`} id="graph-concept-link">
              <ExternalLink aria-hidden /> {t("graph.conceptDetail")}
            </Link>
          </Button>
        )}
      </div>
      {node.type === "document" ? (
        <Section title={t("graph.document")}>
          <p className="text-sm break-all">{node.path || node.label}</p>
          <p className="text-xs text-muted-foreground">{tn("graph.evidenceFor", node.degree)}</p>
        </Section>
      ) : (
        <>
          <Section title={t("concept.definition")} id="definition"><p className="text-sm leading-relaxed">{node.definition || "-"}</p></Section>
          {!!node.synonyms?.length && <Section title={t("concept.synonyms")} id="synonyms"><Chips items={node.synonyms} /></Section>}
          {!!node.tables?.length && <Section title={t("graph.tables")} id="tables"><Chips items={node.tables} /></Section>}
          {!!node.attributes?.length && !conceptId && <Section title={t("concept.attributes")}><Chips items={node.attributes} /></Section>}
        </>
      )}
      {conceptId && (
        q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
          : !q.data ? <div className="grid gap-3"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-20" /></div>
            : <ConceptBody c={q.data} omitBasics onOpen={onSelect} />
      )}
      {neighbours.length > 0 && (
        <Section title={t("graph.neighbours", { n: neighbours.length })}>
          <div className="flex flex-wrap gap-1.5">
            {neighbours.slice(0, 40).map((n) => (
              <button key={n.id} type="button" onClick={() => onSelect(n.id)}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-0.5
                                 text-xs hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none">
                <span className="size-2 shrink-0 rounded-full" style={{ background: n.color }} aria-hidden />
                <span className="truncate">{n.label}</span>
              </button>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
