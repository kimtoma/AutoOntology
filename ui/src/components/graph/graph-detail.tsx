import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Focus, X } from "lucide-react"
import { Link } from "react-router"

import { Chips, ConceptBody, RelationChip, Section } from "@/components/concept-sheet"
import { ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { api } from "@/lib/api"
import { type GNode, TYPE_LABELS } from "@/lib/graph-data"

const TYPE_KO: Record<string, string> = {
  class: "클래스", concept: "정렬된 개념", individual: "개체", document: "문서",
}

/** Detail of a graph node: the node's own fields, then the concept detail (alignment chips,
    relations, evidence quotes) of the document concept behind it, if any. */
export function GraphDetail({ node, neighbours, onSelect, onExpand, onClose }: {
  node: GNode
  neighbours: GNode[]
  onSelect: (id: string) => void
  onExpand: (id: string) => void
  onClose?: () => void
}) {
  const conceptId = node.concept_ids?.[0] ?? null
  const q = useQuery({
    queryKey: keys.concept(conceptId ?? ""),
    queryFn: () => api.concept(conceptId!),
    enabled: !!conceptId,
  })
  const term = node.origin === "structured"
  return (
    <div className="grid gap-5" id="graph-detail" data-node={node.id}>
      <div className="flex items-start gap-2">
        <div className="grid min-w-0 flex-1 gap-1.5">
          <h2 className="text-base leading-tight font-semibold break-words">{node.label}</h2>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="font-normal">
              <span className="size-2 rounded-full" style={{ background: node.color }} aria-hidden />
              {TYPE_LABELS[node.group] ?? node.group}
            </Badge>
            <Badge variant="outline" className="font-normal">{TYPE_KO[node.type] ?? node.type}</Badge>
            {node.source && <Badge variant="secondary" className="font-normal">{node.source}</Badge>}
            {node.enriched_by && <Badge variant="secondary" className="font-normal">+ {node.enriched_by}</Badge>}
            <Badge variant="outline" className="font-normal">연결 {node.degree}</Badge>
          </div>
        </div>
        {onClose && (
          <Button variant="ghost" size="icon" className="-mt-1 -mr-2 shrink-0" onClick={onClose}
                  aria-label="상세 닫기">
            <X aria-hidden />
          </Button>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onExpand(node.id)} id="graph-expand">
          <Focus aria-hidden /> 이웃만 보기
        </Button>
        {conceptId && (
          <Button size="sm" variant="outline" asChild>
            <Link to={`/concepts?concept=${encodeURIComponent(conceptId)}`} id="graph-concept-link">
              <ExternalLink aria-hidden /> 개념 상세
            </Link>
          </Button>
        )}
      </div>
      {node.type === "document" ? (
        <Section title="문서">
          <p className="text-sm break-all">{node.path || node.label}</p>
          <p className="text-xs text-muted-foreground">근거로 연결된 노드 {node.degree}개</p>
        </Section>
      ) : (
        (term || !conceptId) && (
          <>
            <Section title="정의"><p className="text-sm leading-relaxed">{node.definition || "-"}</p></Section>
            {!!node.synonyms?.length && <Section title="동의어"><Chips items={node.synonyms} /></Section>}
            {!!node.attributes?.length && <Section title="속성"><Chips items={node.attributes} /></Section>}
            {!!node.tables?.length && <Section title="테이블"><Chips items={node.tables} /></Section>}
          </>
        )
      )}
      {node.folded_into && (
        <Section title="정렬">
          <div><RelationChip relation="same" term={neighbours.find((n) => n.id === node.folded_into)?.label} /></div>
        </Section>
      )}
      {conceptId && (
        q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
          : !q.data ? <div className="grid gap-3"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-20" /></div>
            : <ConceptBody c={q.data} omitBasics={term} />
      )}
      {neighbours.length > 0 && (
        <Section title={`이웃 (${neighbours.length})`}>
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
