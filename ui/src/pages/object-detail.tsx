import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, ArrowLeft, ArrowRight, Boxes, Play, Sigma } from "lucide-react"
import { useState } from "react"
import { useParams } from "react-router"

import { ActionDialog, type ActionTarget } from "@/components/action-dialog"
import { ActionRunSheet } from "@/components/action-run"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { ProjectLink as Link } from "@/components/project-link"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { keys, retryServerErrors } from "@/hooks/queries"
import { fmtDate, fmtNumber, useT } from "@/i18n"
import { api, type ObjectDetail, type ObjectLink } from "@/lib/api"
import { useCan } from "@/lib/auth"
import { fmtValue, objectPath } from "@/lib/logic"
import { useArchived } from "@/lib/project"
import { cn } from "@/lib/utils"

function ValuesCard({ obj }: { obj: ObjectDetail }) {
  const t = useT()
  const operational = Object.entries(obj.edits ?? {}).filter(([, e]) => e.operational).map(([k]) => k)
  const sourceProps = Object.keys(obj.values).filter((k) => !operational.includes(k))
  const row = (prop: string) => {
    const edit = obj.edits?.[prop]
    return (
      <div key={prop} className="grid gap-0.5 border-t py-2 first:border-t-0 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-3"
           data-prop={prop} data-edited={edit ? true : undefined} data-conflict={edit?.conflict || undefined}>
        <dt className="text-sm text-muted-foreground">{prop}</dt>
        <dd className="grid min-w-0 gap-1">
          <span className="flex flex-wrap items-center gap-1.5 break-words">
            <span className="font-medium">{fmtValue(obj.values[prop])}</span>
            {edit && !edit.operational && (
              <Badge variant="outline" className="border-primary/40 bg-primary/5 font-normal text-primary" data-mark="edited"
                     title={t("objects.edited.title", { who: edit.edited_by })}>{t("objects.edited")}</Badge>
            )}
          </span>
          {edit && !edit.operational && !edit.conflict && (
            <span className="text-xs text-muted-foreground" data-source-value>
              {t("objects.sourceWas", { value: fmtValue(edit.source_value_at_edit ?? edit.source_value) })}
              {" · "}{t("objects.editedBy", { who: edit.edited_by, date: fmtDate(edit.edited_at) })}
            </span>
          )}
          {edit?.conflict && (
            <span role="note" className="flex items-start gap-1 text-xs text-destructive" data-mark="conflict">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {t("objects.conflict.detail", { source: fmtValue(edit.source_value), was: fmtValue(edit.source_value_at_edit) })}
            </span>
          )}
          {edit?.operational && (
            <span className="text-xs text-muted-foreground">{t("objects.editedBy", { who: edit.edited_by, date: fmtDate(edit.edited_at) })}</span>
          )}
        </dd>
      </div>
    )
  }
  return (
    <Card className="gap-2 py-4" id="object-values">
      <CardHeader className="px-4"><CardTitle className="text-base">{t("objects.values")}</CardTitle></CardHeader>
      <CardContent className="px-4">
        <dl>{sourceProps.map(row)}</dl>
        {operational.length > 0 && (
          <section className="mt-3 grid gap-1" id="object-operational" aria-labelledby="operational-title">
            <h3 id="operational-title" className="text-sm font-medium">{t("objects.operational")}</h3>
            <p className="text-xs text-muted-foreground">{t("objects.operational.hint")}</p>
            <dl>{operational.map(row)}</dl>
          </section>
        )}
      </CardContent>
    </Card>
  )
}

function ComputedCard({ obj }: { obj: ObjectDetail }) {
  const t = useT()
  const entries = Object.entries(obj.computed ?? {})
  if (!entries.length) return null
  return (
    <Card className="gap-2 py-4" id="object-computed">
      <CardHeader className="px-4"><CardTitle className="flex items-center gap-2 text-base"><Sigma className="size-4" aria-hidden />{t("objects.computed")}</CardTitle></CardHeader>
      <CardContent className="px-4">
        <dl className="grid gap-1.5">
          {entries.map(([name, v]) => (
            <div key={name} className="flex flex-wrap items-baseline justify-between gap-2" data-computed={name}>
              <dt className="text-sm text-muted-foreground">
                <Link to={`/logic?tab=functions&def=${encodeURIComponent(name)}`} className="hover:underline">{name}()</Link>
              </dt>
              <dd className={cn("text-sm font-medium", v.error && "text-destructive")}>
                {v.error ? (typeof v.error === "string" ? v.error : v.error.message) : fmtValue(v.value)}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

function LinkGroup({ obj, link }: { obj: ObjectDetail; link: ObjectLink }) {
  const t = useT()
  const q = useQuery({
    queryKey: keys.objectLinks(obj.type.name, obj.key, link.name),
    queryFn: () => api.objects.follow(obj.type.name, obj.key, link.name, 10),
    enabled: link.target_status !== "proposed" && link.target_status !== "broken",
    retry: false,
  })
  return (
    <li className="grid gap-1" data-link={link.name} data-direction={link.direction}>
      <p className="flex items-center gap-1.5 text-sm">
        {link.direction === "in" ? <ArrowLeft className="size-3.5 text-muted-foreground" aria-label={t("objects.link.in")} />
          : <ArrowRight className="size-3.5 text-muted-foreground" aria-label={t("objects.link.out")} />}
        <span className="font-medium">{link.target_label || link.target}</span>
        <span className="text-xs text-muted-foreground">{link.label}</span>
      </p>
      {q.error ? <p className="text-xs text-muted-foreground">{t("objects.link.unavailable")}</p>
        : !q.data ? (q.isFetching ? <Skeleton className="h-5 w-40" /> : <p className="text-xs text-muted-foreground">{t("objects.link.unavailable")}</p>)
        : !q.data.items.length ? <p className="text-xs text-muted-foreground">{t("objects.link.none")}</p>
        : (
          <ul className="flex flex-wrap gap-1.5">
            {q.data.items.map((o) => (
              <li key={o.key}>
                <Link to={objectPath(o.type || link.target, o.key)} data-linked={o.key}
                      className="inline-flex rounded-full border px-2.5 py-0.5 text-xs hover:bg-accent">
                  {o.key}{o.title ? ` · ${o.title}` : ""}
                </Link>
              </li>
            ))}
            {q.data.total > q.data.items.length && (
              <li className="text-xs text-muted-foreground">{t("objects.link.more", { n: fmtNumber(q.data.total - q.data.items.length) })}</li>
            )}
          </ul>
        )}
    </li>
  )
}

function LinksCard({ obj }: { obj: ObjectDetail }) {
  const t = useT()
  // the detail lists its out-links; in-links come with the type
  const links = [...obj.links, ...obj.type.links.filter((l) => l.direction === "in" && !obj.links.some((o) => o.name === l.name))]
  if (!links.length) return null
  return (
    <Card className="gap-2 py-4" id="object-links">
      <CardHeader className="px-4"><CardTitle className="text-base">{t("objects.links")}</CardTitle></CardHeader>
      <CardContent className="px-4"><ul className="grid gap-3">{links.map((l) => <LinkGroup key={l.name} obj={obj} link={l} />)}</ul></CardContent>
    </Card>
  )
}

function HistoryCard({ obj, onOpenRun }: { obj: ObjectDetail; onOpenRun: (id: string) => void }) {
  const t = useT()
  if (!obj.history.length) return null
  return (
    <Card className="gap-2 py-4" id="object-history">
      <CardHeader className="px-4"><CardTitle className="text-base">{t("objects.history")}</CardTitle></CardHeader>
      <CardContent className="px-4">
        <ul className="grid gap-2 text-sm">
          {obj.history.slice(0, 20).map((e) => (
            <li key={e.id} className={cn("grid gap-0.5 border-t pt-2 first:border-t-0 first:pt-0", e.reverted_at && "text-muted-foreground")}
                data-history={e.id} data-reverted={e.reverted_at ? true : undefined}>
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{e.property}</span>
                <span className={cn(e.reverted_at && "line-through")}>{fmtValue(e.old_value)}</span>
                <ArrowRight className="size-3.5" aria-hidden />
                <span className={cn(e.reverted_at && "line-through")}>{fmtValue(e.value)}</span>
                {e.reverted_at && <Badge variant="outline" className="font-normal">{t("logic.state.reverted")}</Badge>}
              </span>
              <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                {t("objects.editedBy", { who: e.edited_by, date: fmtDate(e.edited_at) })}
                {e.action_run_id && (
                  <button type="button" className="underline underline-offset-4" data-run-link={e.action_run_id}
                          onClick={() => onOpenRun(e.action_run_id!)}>{t("objects.history.run")}</button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function ActionsCard({ obj, onRun }: { obj: ObjectDetail; onRun: (a: ActionTarget) => void }) {
  const t = useT()
  const archived = useArchived()
  if (!obj.actions?.length) return null
  return (
    <Card className="gap-2 py-4" id="object-actions">
      <CardHeader className="px-4"><CardTitle className="flex items-center gap-2 text-base"><Play className="size-4" aria-hidden />{t("objects.actions")}</CardTitle></CardHeader>
      <CardContent className="grid gap-2 px-4">
        {obj.actions.map((a) => {
          const reason = archived ? t("projects.readOnly") : a.available ? undefined : a.reason || t("objects.action.unavailable")
          return (
            <div key={a.name} className="grid gap-1" data-action-item={a.name} data-available={a.available}>
              <Button variant={a.available ? "default" : "outline"} className="justify-start" disabled={!!reason}
                      title={reason} data-action-button={a.name}
                      onClick={() => onRun({ type: obj.type.name, key: obj.key, title: obj.title, name: a.name, label: a.label })}>
                <Play aria-hidden /> {a.label || a.name}
              </Button>
              {reason && !archived && <p className="text-xs text-muted-foreground" data-reason>{reason}</p>}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

export default function ObjectDetailPage() {
  const t = useT()
  const { type = "", key = "" } = useParams()
  const canRead = useCan("objects.read")
  const q = useQuery({ queryKey: keys.object(type, key), queryFn: () => api.objects.get(type, key), retry: retryServerErrors, enabled: canRead })
  const [action, setAction] = useState<ActionTarget | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const obj = q.data
  return (
    <>
      <div className="mb-4">
        <Link to={`/objects?type=${encodeURIComponent(type)}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              id="object-back">
          <ArrowLeft className="size-4" aria-hidden /> {obj ? obj.type.label : t("objects.title")}
        </Link>
      </div>
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
        : !obj ? <Skeleton className="h-64" />
        : (
          <>
            <div className="mb-6 flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight" id="object-title">{obj.title || obj.key}</h1>
              <Badge variant="secondary" className="font-normal">{obj.type.label}</Badge>
              <code className="text-sm text-muted-foreground">{obj.type.key} = {obj.key}</code>
              {obj.conflicts?.length > 0 && (
                <Badge variant="outline" className="border-destructive/30 bg-destructive/10 font-normal text-destructive" id="object-conflicts">
                  <AlertTriangle aria-hidden /> {t("objects.conflicts", { n: obj.conflicts.length })}
                </Badge>
              )}
            </div>
            <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
              <div className="grid min-w-0 content-start gap-4">
                <ValuesCard obj={obj} />
                <LinksCard obj={obj} />
                <HistoryCard obj={obj} onOpenRun={setRunId} />
              </div>
              <div className="grid min-w-0 content-start gap-4">
                <ActionsCard obj={obj} onRun={setAction} />
                <ComputedCard obj={obj} />
                {!obj.actions?.length && !Object.keys(obj.computed ?? {}).length && (
                  <EmptyState icon={Boxes} title={t("objects.noLogic")} hint={t("objects.noLogic.hint")} />
                )}
              </div>
            </div>
          </>
        )}
      <ActionDialog target={action} onClose={() => setAction(null)} onOpenRun={setRunId} />
      <ActionRunSheet runId={runId} onClose={() => setRunId(null)} />
    </>
  )
}
