import { Check, CheckCircle2, Loader2, Pencil, ShieldCheck, ShieldQuestion, Undo2, X } from "lucide-react"
import { forwardRef, useState } from "react"

import { KindBadge, StateBadge } from "@/components/logic-badges"
import { SpecErrors, ValidationLine, YamlArea } from "@/components/logic-editor"
import { useSpecValidation } from "@/hooks/use-spec-validation"
import { ProjectLink as Link } from "@/components/project-link"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { itemName } from "@/hooks/use-review"
import { fmtDate, useT } from "@/i18n"
import type { Decision } from "@/lib/api"
import { useCan, useDenied } from "@/lib/auth"
import { ago } from "@/lib/ops"
import { useArchived } from "@/lib/project"
import { cn } from "@/lib/utils"
import type { ReviewCardProps } from "@/components/review-card"

/** The decision on an object type or logic item (no apply step: they take effect at once). */
function LogicDecision({ decision, kind }: { decision: Decision; kind: "objects" | "logic" }) {
  const t = useT()
  const tone = decision.decision === "reject" ? "border-destructive/30 bg-destructive/10 text-destructive"
    : "border-success/30 bg-success/10 text-success"
  const version = decision.payload?.version
  return (
    <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" data-decision={decision.decision}>
      <Badge variant="outline" className={cn("font-normal", tone)}>
        {decision.decision === "reject" ? t("review.decided.reject") : t("review.decided.accept")}
      </Badge>
      <span title={fmtDate(decision.decided_at)}>{t("review.decided.by", { who: decision.decided_by, ago: ago(decision.decided_at) })}</span>
      {decision.decision === "accept" && kind === "logic" && typeof version === "number" && (
        <span>· {t(decision.payload?.edited ? "review.logic.savedEdited" : "review.logic.saved", { n: version })}</span>
      )}
      {decision.decision === "accept" && kind === "objects" && <span>· {t("review.objects.bound")}</span>}
    </span>
  )
}

function Evidence({ item }: { item: ReviewCardProps["item"] }) {
  const t = useT()
  const evidence = (item.evidence ?? []).slice(0, 3)
  if (!evidence.length) return null
  return (
    <ul className="grid gap-1.5" aria-label={t("review.evidence")}>
      {evidence.map((e, i) => (
        <li key={i} className="rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-1.5" data-verified={e.verified ? true : undefined}>
          <blockquote className="text-sm">“{e.quote.replace(/^["“”]+|["“”]+$/g, "")}”</blockquote>
          <p className="flex flex-wrap items-center gap-1 text-xs break-all text-muted-foreground">
            {e.verified ? <ShieldCheck className="size-3.5 text-success" aria-hidden /> : <ShieldQuestion className="size-3.5 text-amber-600" aria-hidden />}
            <span>{e.verified ? t("review.logic.verified") : t("review.logic.unverified")}</span>
            <span>· {e.document || e.path}{e.page ? ` · ${t("review.page.n", { n: e.page })}` : ""}</span>
          </p>
        </li>
      ))}
    </ul>
  )
}

/** A logic candidate: what it is, why (evidence), whether it can be used as it is, its YAML,
    and Accept / Edit & accept / Reject. */
export const LogicReviewCard = forwardRef<HTMLLIElement, ReviewCardProps>(function LogicReviewCard(
  { item, onDecide, onUndo, busy, compact, selected }, ref,
) {
  const t = useT()
  const can = useCan("logic.edit")
  const denied = useDenied("logic.edit")
  const archived = useArchived()
  const readOnly = archived || !can
  const title = archived ? t("projects.readOnly") : denied
  const logic = item.logic!
  const decided = item.decision
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(logic.spec_yaml)
  const [showYaml, setShowYaml] = useState(false)
  const validation = useSpecValidation(text, editing)
  return (
    <li ref={ref} tabIndex={-1} data-review-item={item.id} data-category="logic" data-ready={logic.ready}
        data-decided={decided ? decided.decision : undefined} aria-current={selected || undefined}
        className={cn("grid min-w-0 gap-2 rounded-xl border bg-card p-4 outline-none", selected && "ring-2 ring-primary/50", decided && "bg-muted/30")}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <KindBadge kind={logic.kind} />
        <span className="font-medium break-words">{itemName(item)}</span>
        {logic.label && logic.label !== logic.name && <code className="text-xs text-muted-foreground">{logic.name}</code>}
        {logic.ready ? (
          <Badge variant="outline" className="border-success/30 bg-success/10 font-normal text-success" data-ready-badge="ready">
            <CheckCircle2 aria-hidden /> {t("review.logic.ready")}
          </Badge>
        ) : (
          <Badge variant="outline" className="border-warning/40 bg-warning/10 font-normal text-amber-700 dark:text-amber-300" data-ready-badge="edit">
            <Pencil aria-hidden /> {t("review.logic.needsEdit")}
          </Badge>
        )}
        {logic.object_type && <Badge variant="secondary" className="font-normal">{logic.object_type}</Badge>}
        {item.confidence != null && (
          <span className="text-xs text-muted-foreground tabular-nums">{t("review.confidence", { n: item.confidence.toFixed(2) })}</span>
        )}
      </div>
      {logic.existing && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground" data-existing>
          {t("review.logic.existing", { n: logic.existing.version })} <StateBadge status={logic.existing.status} />
        </p>
      )}
      {!compact && item.rationale && <p className="text-sm text-pretty">{item.rationale}</p>}
      {!compact && !editing && <SpecErrors errors={logic.errors} />}
      {!compact && <Evidence item={item} />}
      {!compact && !editing && (
        <div>
          <button type="button" className="text-xs text-muted-foreground underline underline-offset-4" aria-expanded={showYaml}
                  data-action="toggle-yaml" onClick={() => setShowYaml(!showYaml)}>
            {showYaml ? t("review.logic.hideYaml") : t("review.logic.showYaml")}
          </button>
          {showYaml && (
            <pre className="mt-1.5 max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs whitespace-pre" data-yaml>
              {logic.spec_yaml}
            </pre>
          )}
        </div>
      )}
      {editing && !decided && (
        <div className="grid gap-2" data-editing>
          <YamlArea id={`edit-${logic.candidate_id}`} label={t("review.logic.editLabel", { name: itemName(item) })}
                    value={text} onChange={setText} rows={12} />
          <ValidationLine v={validation} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {decided ? (
          <>
            <LogicDecision decision={decided} kind="logic" />
            <Button size="sm" variant="ghost" disabled={busy || readOnly} title={title} data-action="undo" onClick={() => onUndo(item)}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Undo2 aria-hidden />} {t("review.undo")}
            </Button>
          </>
        ) : editing ? (
          <>
            <Button size="sm" disabled={busy || readOnly || !validation.result?.valid} data-action="accept-edited"
                    onClick={() => onDecide({ item, decision: "accept", spec: text })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("review.logic.acceptEdited")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setText(logic.spec_yaml) }}>{t("common.cancel")}</Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy || readOnly || !logic.ready} data-action="accept"
                    title={!logic.ready ? t("review.logic.fixFirst") : title}
                    onClick={() => onDecide({ item, decision: "accept" })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("review.accept")}
            </Button>
            {!compact && (
              <Button size="sm" variant="outline" disabled={busy || readOnly} title={title} data-action="edit"
                      onClick={() => setEditing(true)}>
                <Pencil aria-hidden /> {t("review.logic.edit")}
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={busy || readOnly} title={title} data-action="reject"
                    onClick={() => onDecide({ item, decision: "reject" })}>
              <X aria-hidden /> {t("review.reject")}
            </Button>
          </>
        )}
        {decided?.decision === "accept" && !compact && (
          <Link to={`/logic?tab=${logic.kind}s&def=${encodeURIComponent(String(decided.payload?.def_name ?? logic.name))}`}
                className="ml-auto text-xs text-muted-foreground underline-offset-4 hover:underline" data-open-logic>
            {t("review.logic.open")}
          </Link>
        )}
      </div>
    </li>
  )
})

/** An object type the build proposed: its table, key and title columns; owners accept or reject. */
export const ObjectReviewCard = forwardRef<HTMLLIElement, ReviewCardProps>(function ObjectReviewCard(
  { item, onDecide, onUndo, busy, compact, selected }, ref,
) {
  const t = useT()
  const can = useCan("objects.manage")
  const denied = useDenied("objects.manage")
  const archived = useArchived()
  const readOnly = archived || !can
  const title = archived ? t("projects.readOnly") : denied
  const ot = item.object_type!
  const decided = item.decision
  return (
    <li ref={ref} tabIndex={-1} data-review-item={item.id} data-category="objects"
        data-decided={decided ? decided.decision : undefined} aria-current={selected || undefined}
        className={cn("grid min-w-0 gap-2 rounded-xl border bg-card p-4 outline-none", selected && "ring-2 ring-primary/50", decided && "bg-muted/30")}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="font-medium break-words">{itemName(item)}</span>
        <Badge variant="secondary" className="max-w-full font-normal"><span className="truncate">{ot.source} · {ot.schema}.{ot.table}</span></Badge>
        <StateBadge status={ot.status} />
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_minmax(0,1fr)]">
        <dt className="text-muted-foreground">{t("review.objects.key")}</dt>
        <dd data-key-proposal>
          {ot.key ? (
            <>
              <code className="font-mono">{ot.key_columns.join(", ") || ot.key}</code>
              <span className="text-xs text-muted-foreground"> · {ot.key_checked ? t("review.objects.unique") : t("review.objects.unchecked")}
                {ot.key_origin ? ` · ${t(ot.key_origin === "pk" ? "review.objects.fromPk" : ot.key_origin === "manual" ? "review.objects.manual" : "review.objects.inferred")}` : ""}</span>
            </>
          ) : <span className="text-destructive">{t("review.objects.noKey")}</span>}
        </dd>
        <dt className="text-muted-foreground">{t("review.objects.titleColumn")}</dt>
        <dd>{ot.title_column ? <code className="font-mono">{ot.title_column}</code> : "—"}</dd>
        {!compact && (
          <>
            <dt className="text-muted-foreground">{t("review.objects.columns")}</dt>
            <dd className="text-xs text-muted-foreground">{ot.columns.map((c) => c.name).join(", ")}</dd>
          </>
        )}
      </dl>
      {!ot.key && !decided && (
        <p className="text-xs text-muted-foreground">
          {t("review.objects.bindHint")}{" "}
          <Link to="/objects" className="underline underline-offset-4">{t("nav.objects")}</Link>
        </p>
      )}
      {ot.broken_reason && <p className="text-xs text-destructive">{ot.broken_reason}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {decided ? (
          <>
            <LogicDecision decision={decided} kind="objects" />
            <Button size="sm" variant="ghost" disabled={busy || readOnly} title={title} data-action="undo" onClick={() => onUndo(item)}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Undo2 aria-hidden />} {t("review.undo")}
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy || readOnly || !ot.key} data-action="accept" title={title}
                    onClick={() => onDecide({ item, decision: "accept" })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("review.accept")}
            </Button>
            <Button size="sm" variant="outline" disabled={busy || readOnly} data-action="reject" title={title}
                    onClick={() => onDecide({ item, decision: "reject" })}>
              <X aria-hidden /> {t("review.reject")}
            </Button>
          </>
        )}
      </div>
    </li>
  )
})
