import { Check, Hammer, Loader2, Undo2, X } from "lucide-react"
import { forwardRef, useState } from "react"
import { Link } from "react-router"

import { RelationChip } from "@/components/concept-sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { isAlignment, itemName, RELATIONS, suggestedRelation, type DecideArgs } from "@/hooks/use-review"
import { fmtDate, type MessageKey, useT } from "@/i18n"
import type { AcceptRelation, Decision, ReviewItem } from "@/lib/api"
import { CKIND_LABEL } from "@/lib/labels"
import { ago, freshnessText } from "@/lib/ops"
import { cn } from "@/lib/utils"

export function DecisionBadge({ decision }: { decision: Decision }) {
  const t = useT()
  const tone = decision.decision === "reject" ? "border-destructive/30 bg-destructive/10 text-destructive"
    : "border-success/30 bg-success/10 text-success"
  return (
    <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" data-decision={decision.decision}>
      <Badge variant="outline" className={cn("font-normal", tone)}>
        {t(`review.decided.${decision.decision}`)}
        {decision.decision === "accept" && decision.relation ? ` · ${t(`review.rel.${decision.relation}` as MessageKey)}` : ""}
      </Badge>
      <span title={fmtDate(decision.decided_at)}>{t("review.decided.by", { who: decision.decided_by, ago: ago(decision.decided_at) })}</span>
      <span>· {decision.applied_revision ? t("review.decided.applied", { n: decision.applied_revision }) : t("review.decided.pending")}</span>
    </span>
  )
}

export interface ReviewCardProps {
  item: ReviewItem
  onDecide: (args: DecideArgs) => void
  onUndo: (item: ReviewItem) => void
  onBuild?: (sources: string[]) => void
  busy?: boolean
  /** the Overview's short form: no rationale nor evidence */
  compact?: boolean
  selected?: boolean
  relation?: AcceptRelation
  onRelation?: (r: AcceptRelation) => void
}

/** One review item: what it is, why it waits, the evidence, and its actions. */
export const ReviewCard = forwardRef<HTMLLIElement, ReviewCardProps>(function ReviewCard(
  { item, onDecide, onUndo, onBuild, busy, compact, selected, relation: relationProp, onRelation }, ref,
) {
  const t = useT()
  const [ownRelation, setOwnRelation] = useState<AcceptRelation>(suggestedRelation(item))
  const relation = relationProp ?? ownRelation
  const setRelation = onRelation ?? setOwnRelation
  const name = itemName(item)
  const decided = item.decision
  const evidence = (item.evidence ?? []).slice(0, 3)
  return (
    <li ref={ref} tabIndex={-1} data-review-item={item.id} data-category={item.category}
        data-decided={decided ? decided.decision : undefined} aria-current={selected || undefined}
        className={cn("grid min-w-0 gap-2 rounded-xl border bg-card p-4 outline-none",
                      selected && "ring-2 ring-primary/50", decided && "bg-muted/30")}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="font-medium break-words">
          {item.category === "aliases" && item.term ? t("review.alias", { alias: item.alias ?? "", term: item.term.name }) : name}
        </span>
        {item.concept && item.category !== "aliases" && (
          <Badge variant="outline" className="font-normal">{CKIND_LABEL[item.concept.kind] ?? item.concept.kind}</Badge>
        )}
        {item.concept?.source && item.category !== "aliases" && (
          <Badge variant="secondary" className="max-w-full font-normal"><span className="truncate">{item.concept.source}</span></Badge>
        )}
        {item.new && <Badge className="font-normal" data-new>{t("review.new")}</Badge>}
        {isAlignment(item) && item.relation && <RelationChip relation={item.relation} term={item.term?.name} />}
        {item.confidence != null && (
          <span className="text-xs text-muted-foreground tabular-nums">{t("review.confidence", { n: item.confidence.toFixed(2) })}</span>
        )}
      </div>
      {item.concept?.definition && item.category !== "aliases" && (
        <p className={cn("text-sm text-muted-foreground", compact && "line-clamp-2")}>{item.concept.definition}</p>
      )}
      {item.category === "stale_sources" && item.source && (
        <p className="text-sm" data-reasons>{t("review.source.reasons", { list: freshnessText(item.source) })}</p>
      )}
      {!compact && item.reason && item.category !== "stale_sources" && (
        <p className="text-xs text-muted-foreground">
          {t("review.reason", { reason: item.reason === "column_name" ? t("review.alias.reason.column_name") : item.reason })}
        </p>
      )}
      {!compact && item.rationale && <p className="text-sm text-pretty">{item.rationale}</p>}
      {!compact && evidence.length > 0 && (
        <ul className="grid gap-1.5" aria-label={t("review.evidence")}>
          {evidence.map((e, i) => (
            <li key={i} className="rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-1.5">
              <blockquote className="text-sm">“{e.quote.replace(/^["“”]+|["“”]+$/g, "")}”</blockquote>
              <p className="text-xs break-all text-muted-foreground">
                {e.document || e.path}{e.page ? ` · ${t("review.page.n", { n: e.page })}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {decided ? (
          <>
            <DecisionBadge decision={decided} />
            <Button size="sm" variant="ghost" disabled={busy} data-action="undo" onClick={() => onUndo(item)}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Undo2 aria-hidden />} {t("review.undo")}
            </Button>
          </>
        ) : item.category === "stale_sources" ? (
          onBuild && (
            <Button size="sm" variant="outline" data-action="build" onClick={() => onBuild([name])}>
              <Hammer aria-hidden /> {t("review.buildSource")}
            </Button>
          )
        ) : item.category === "unaligned" ? (
          <Button size="sm" variant="outline" disabled={busy} data-action="acknowledge"
                  onClick={() => onDecide({ item, decision: "acknowledge" })}>
            <Check aria-hidden /> {t("review.acknowledge")}
          </Button>
        ) : (
          <>
            {isAlignment(item) && !compact && (
              <Select value={relation} onValueChange={(v) => setRelation(v as AcceptRelation)}>
                <SelectTrigger size="sm" className="w-36" aria-label={t("review.acceptAs")} data-relation-select>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RELATIONS.map((r) => <SelectItem key={r} value={r} data-relation={r}>{t(`review.rel.${r}`)}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
            <Button size="sm" disabled={busy} data-action="accept"
                    onClick={() => onDecide({ item, decision: "accept", relation: isAlignment(item) ? relation : undefined })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("review.accept")}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} data-action="reject"
                    onClick={() => onDecide({ item, decision: "reject" })}>
              <X aria-hidden /> {t("review.reject")}
            </Button>
          </>
        )}
        {item.concept?.node && !compact && (
          <Link to={`/ontology?concept=${encodeURIComponent(item.concept.node)}`}
                className="ml-auto text-xs text-muted-foreground underline-offset-4 hover:underline">
            {t("nav.ontology")}
          </Link>
        )}
      </div>
    </li>
  )
})
