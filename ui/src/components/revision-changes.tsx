import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { ErrorState } from "@/components/empty-state"
import { RelationChip } from "@/components/concept-sheet"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, retryServerErrors } from "@/hooks/queries"
import { useT } from "@/i18n"
import { api, type DiffNode } from "@/lib/api"
import { CKIND_LABEL } from "@/lib/labels"

const isConcept = (id: string) => /^(term|concept)-/.test(id)

function Group({ id, title, total, shown, children }: {
  id: string; title: string; total: number; shown: number; children: ReactNode
}) {
  const t = useT()
  if (!total) return null
  return (
    <section className="grid min-w-0 gap-2" data-changes={id}>
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        {title} <Badge variant="secondary" className="font-normal tabular-nums">{total}</Badge>
      </h3>
      <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">{children}</ul>
      {total > shown && <p className="text-xs text-muted-foreground">{t("ontology.changed.more", { n: total - shown })}</p>}
    </section>
  )
}

/** What changed in revision *to* against *from* (the diff API), each concept clickable. */
export function RevisionChanges({ from, to, onOpen }: {
  from: number | null
  to: number
  onOpen: (id: string) => void
}) {
  const t = useT()
  const diff = useQuery({ queryKey: keys.diff(from, to), queryFn: () => api.diff(from, to), retry: retryServerErrors,
                          enabled: from !== undefined })
  if (diff.error) return <ErrorState error={diff.error} onRetry={() => diff.refetch()} />
  if (!diff.data) return <div className="grid gap-3"><Skeleton className="h-6 w-64" /><Skeleton className="h-32" /></div>
  const d = diff.data
  const node = (n: DiffNode, clickable: boolean, extra?: ReactNode) => (
    <li key={n.id} className="min-w-0">
      {clickable && isConcept(n.id) ? (
        <button type="button" onClick={() => onOpen(n.id)} data-concept={n.id}
                className="flex w-full min-w-0 flex-wrap items-center gap-1.5 rounded-lg border bg-card px-3 py-2 text-left text-sm hover:bg-accent/50">
          <span className="font-medium break-words">{n.name}</span>
          {n.kind && <Badge variant="outline" className="font-normal">{CKIND_LABEL[n.kind] ?? n.kind}</Badge>}
          {extra}
        </button>
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
          <span className="font-medium break-words line-through">{n.name}</span>
          {n.kind && <Badge variant="outline" className="font-normal">{CKIND_LABEL[n.kind] ?? n.kind}</Badge>}
        </div>
      )}
    </li>
  )
  const axioms = [
    ...d.axioms.added.map((a) => ({ ...a, how: "+", word: t("ontology.changed.how.added") })),
    ...d.axioms.changed.map((a) => ({ ...a, how: "~", word: t("ontology.changed.how.changed") })),
    ...d.axioms.removed.map((a) => ({ ...a, how: "−", word: t("ontology.changed.how.removed") })),
  ]
  const axTotal = d.axioms.totals.added + d.axioms.totals.removed + (d.axioms.totals.changed ?? 0)
  const merges = d.merges.added
  const splits = d.merges.removed
  const nothing = !d.classes.totals.added && !d.classes.totals.removed && !d.classes.totals.changed && !axTotal
    && !d.merges.totals.added && !d.merges.totals.removed
  return (
    <div className="grid gap-5" id="revision-changes" data-from={d.from ?? ""} data-to={d.to}>
      <p className="text-sm text-muted-foreground" id="changes-summary">
        {d.from == null ? t("ontology.changed.first", { n: d.to }) : t("ontology.changed.from", { from: d.from, to: d.to })}
      </p>
      {nothing && <p className="text-sm">{t("ontology.changed.none")}</p>}
      <Group id="added" title={t("ontology.changed.added")} total={d.classes.totals.added} shown={d.classes.added.length}>
        {d.classes.added.map((n) => node(n, true))}
      </Group>
      <Group id="changed" title={t("ontology.changed.changed")} total={d.classes.totals.changed ?? 0} shown={d.classes.changed.length}>
        {d.classes.changed.map((n) => node(n, true, n.fields?.length
          ? <span className="text-xs text-muted-foreground">{t("ontology.changed.fields", { list: n.fields.join(", ") })}</span> : null))}
      </Group>
      <Group id="removed" title={t("ontology.changed.removed")} total={d.classes.totals.removed} shown={d.classes.removed.length}>
        {d.classes.removed.map((n) => node(n, false))}
      </Group>
      <Group id="merges" title={t("ontology.changed.merges")} total={d.merges.totals.added} shown={merges.length}>
        {merges.map((m) => (
          <li key={`${m.concept}-${m.term}`} className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
            <span className="font-medium">{m.concept_name}</span>
            <RelationChip relation="same" term={m.term_name || m.term} />
          </li>
        ))}
      </Group>
      <Group id="splits" title={t("ontology.changed.splits")} total={d.merges.totals.removed} shown={splits.length}>
        {splits.map((m) => (
          <li key={`${m.concept}-${m.term}`} className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
            <button type="button" className="font-medium underline-offset-4 hover:underline" onClick={() => onOpen(m.concept)}>
              {m.concept_name}
            </button>
            <span className="text-xs text-muted-foreground">{t("ontology.changed.splitFrom", { term: m.term_name || m.term })}</span>
          </li>
        ))}
      </Group>
      <Group id="axioms" title={t("ontology.changed.axioms")} total={axTotal} shown={axioms.length}>
        {axioms.map((a) => (
          <li key={`${a.how}${a.subject}-${a.kind}-${a.object}-${a.source}`} data-how={a.how}
              className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
            <span aria-hidden className="w-3 text-center font-mono text-muted-foreground">{a.how}</span>
            <span className="sr-only">{a.word}: </span>
            {isConcept(a.subject)
              ? <button type="button" className="font-medium underline-offset-4 hover:underline" onClick={() => onOpen(a.subject)}>{a.subject_name || a.subject}</button>
              : <span className="font-medium">{a.subject_name || a.subject}</span>}
            <RelationChip relation={a.kind} term={a.object_name || a.object} />
            {a.accepted && <Badge variant="outline" className="border-success/30 bg-success/10 font-normal text-success">{t("ontology.changed.accepted")}</Badge>}
          </li>
        ))}
      </Group>
    </div>
  )
}
