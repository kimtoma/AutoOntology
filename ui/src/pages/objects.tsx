import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Boxes, ChevronLeft, ChevronRight, Loader2, Pencil, Search, X } from "lucide-react"
import { useState, type FormEvent } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { StateBadge } from "@/components/logic-badges"
import { NativeSelect } from "@/components/native-select"
import { PageHeader } from "@/components/page-header"
import { ProjectLink as Link } from "@/components/project-link"
import { RoleNotice } from "@/components/role-notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { keys, retryServerErrors } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { fmtNumber, useT } from "@/i18n"
import { api, errorMessage, isNoSnapshot, type ObjectRow, type ObjectType } from "@/lib/api"
import { useCan, useDenied } from "@/lib/auth"
import { fmtValue, objectPath, sortTypes } from "@/lib/logic"
import { useArchived, useProjectNavigate } from "@/lib/project"
import { cn } from "@/lib/utils"

export const OBJECT_PAGE = 25

/* --- bind dialog ------------------------------------------------------------------------------ */

function BindDialog({ type, onClose }: { type: ObjectType | null; onClose: () => void }) {
  return (
    <Dialog open={!!type} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-md" id="bind-dialog">
        {type && <BindForm key={type.name} type={type} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  )
}

function BindForm({ type, onClose }: { type: ObjectType; onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [key, setKey] = useState(type.key ?? type.columns[0]?.name ?? "")
  const [title, setTitle] = useState(type.title_column ?? "")
  const bind = useMutation({
    mutationFn: () => api.objects.bind(type.name, { key, title }),
    onSuccess: ({ type: bound }) => {
      qc.invalidateQueries({ queryKey: keys.objectsAll })
      qc.invalidateQueries({ queryKey: keys.pendingCount })
      toast.success(t("objects.bind.done", { type: bound.label }))
      onClose()
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (key && !bind.isPending) bind.mutate()
  }
  return (
    <form className="grid gap-4" onSubmit={submit} id="bind-form">
      <DialogHeader>
        <DialogTitle>{t("objects.bind.title", { type: type.label })}</DialogTitle>
        <DialogDescription>{t("objects.bind.description", { table: `${type.schema}.${type.table}` })}</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label htmlFor="bind-key">{t("objects.bind.key")}</Label>
        <NativeSelect id="bind-key" value={key} onChange={(e) => setKey(e.target.value)} required>
          {type.columns.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
        </NativeSelect>
        <p className="text-xs text-muted-foreground">{t("objects.bind.keyHint")}</p>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="bind-title">{t("objects.bind.titleColumn")}</Label>
        <NativeSelect id="bind-title" value={title} onChange={(e) => setTitle(e.target.value)}>
          <option value="">{t("objects.bind.noTitle")}</option>
          {type.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </NativeSelect>
      </div>
      {bind.isError && <p role="alert" className="text-sm break-words text-destructive">{errorMessage(bind.error)}</p>}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={!key || bind.isPending} id="btn-bind-submit">
          {bind.isPending && <Loader2 className="animate-spin" aria-hidden />} {t("objects.bind.save")}
        </Button>
      </DialogFooter>
    </form>
  )
}

/* --- types ------------------------------------------------------------------------------------- */

function TypeList({ types, selected, onSelect, onBind }: {
  types: ObjectType[]; selected: string | null; onSelect: (name: string) => void; onBind: (t: ObjectType) => void
}) {
  const t = useT()
  const canManage = useCan("objects.manage")
  const denied = useDenied("objects.manage")
  const archived = useArchived()
  return (
    <ul className="grid content-start gap-2" id="object-types" aria-label={t("objects.types")}>
      {types.map((ot) => {
        const active = ot.status === "active"
        return (
          <li key={ot.name} data-type={ot.name} data-status={ot.status}
              className={cn("grid gap-1 rounded-lg border p-3", selected === ot.name && "border-primary ring-1 ring-primary/40",
                            !active && "bg-muted/30")}>
            <div className="flex min-w-0 items-center gap-2">
              {active ? (
                <button type="button" className="min-w-0 truncate text-left font-medium hover:underline"
                        aria-current={selected === ot.name || undefined} onClick={() => onSelect(ot.name)}>
                  {ot.label}
                </button>
              ) : <span className="min-w-0 truncate font-medium">{ot.label}</span>}
              <StateBadge status={ot.status} className="ml-auto shrink-0" />
            </div>
            <p className="truncate text-xs text-muted-foreground">{ot.source} · {ot.table}</p>
            <p className="text-xs text-muted-foreground">
              {ot.key ? t("objects.type.key", { key: ot.key_columns.join(", ") || ot.key }) : t("objects.type.noKey")}
              {ot.title_column ? ` · ${t("objects.type.title", { title: ot.title_column })}` : ""}
            </p>
            {ot.broken_reason && (
              <p className="flex items-start gap-1 text-xs text-destructive" data-reason>
                <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />{ot.broken_reason}
              </p>
            )}
            {ot.status === "proposed" && (
              <p className="text-xs text-muted-foreground">
                {t("objects.type.proposed")}{" "}
                <Link to="/review?category=objects" className="underline underline-offset-4">{t("objects.type.toReview")}</Link>
              </p>
            )}
            {canManage && ot.status !== "retired" && (
              <Button size="sm" variant="ghost" className="justify-self-start" disabled={archived}
                      title={archived ? t("projects.readOnly") : denied} data-action="bind" onClick={() => onBind(ot)}>
                <Pencil aria-hidden /> {t("objects.bind.button")}
              </Button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/* --- table ------------------------------------------------------------------------------------- */

/** A value cell: the edited mark and the conflict warning beside the value. */
export function ValueCell({ row, column }: { row: ObjectRow; column: string }) {
  const t = useT()
  const edit = row.edits?.[column]
  return (
    <span className="inline-flex items-center gap-1" data-cell={column} data-edited={edit ? true : undefined}
          data-conflict={edit?.conflict || undefined}>
      <span className="truncate">{fmtValue(row.values[column])}</span>
      {edit && (
        <span title={edit.conflict ? t("objects.conflict.title", { source: fmtValue(edit.source_value) }) : t("objects.edited.title", { who: edit.edited_by })}
              className={cn("size-1.5 shrink-0 rounded-full", edit.conflict ? "bg-destructive" : "bg-primary")} aria-hidden />
      )}
      {edit && <span className="sr-only">{edit.conflict ? t("objects.conflict") : t("objects.edited")}</span>}
    </span>
  )
}

function ObjectTable({ type }: { type: ObjectType }) {
  const t = useT()
  const navigate = useProjectNavigate()
  const canCompute = useCan("functions.call")
  const [q, setQ] = useState("")
  const [filterCol, setFilterCol] = useState("")
  const [filterVal, setFilterVal] = useState("")
  const [offset, setOffset] = useState(0)
  const [computed, setComputed] = useState(false)
  const dq = useDebounced(q.trim(), 300)
  const dv = useDebounced(filterVal.trim(), 300)
  const query = {
    q: dq,
    filter: filterCol && dv ? [`${filterCol}=${dv}`] : [],
    limit: OBJECT_PAGE, offset, computed: computed && canCompute,
  }
  const list = useQuery({
    queryKey: keys.objects(type.name, query),
    queryFn: () => api.objects.list(type.name, query),
    placeholderData: keepPreviousData,
    retry: retryServerErrors,
  })
  const items = list.data?.items ?? []
  const columns = type.columns.map((c) => c.name).filter((c) => c !== type.key)
  const computedNames = [...new Set(items.flatMap((i) => Object.keys(i.computed ?? {})))].sort()
  const total = list.data?.total ?? 0
  const reset = () => setOffset(0)
  return (
    <section className="grid min-w-0 content-start gap-3" aria-label={type.label} id="object-table" data-type={type.name}>
      <div className="flex flex-wrap items-end gap-2">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input id="object-search" value={q} className="pl-8" placeholder={t("objects.search")} aria-label={t("objects.search")}
                 onChange={(e) => { setQ(e.target.value); reset() }} />
        </div>
        <NativeSelect className="w-40" aria-label={t("objects.filter.column")} value={filterCol} id="object-filter-col"
                      onChange={(e) => { setFilterCol(e.target.value); reset() }}>
          <option value="">{t("objects.filter.none")}</option>
          {type.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </NativeSelect>
        {filterCol && (
          <div className="flex items-center gap-1">
            <Input className="w-40" aria-label={t("objects.filter.value", { column: filterCol })} value={filterVal} id="object-filter-val"
                   placeholder={t("objects.filter.equals")} onChange={(e) => { setFilterVal(e.target.value); reset() }} />
            <Button size="icon" variant="ghost" aria-label={t("objects.filter.clear")}
                    onClick={() => { setFilterCol(""); setFilterVal(""); reset() }}><X aria-hidden /></Button>
          </div>
        )}
        {canCompute && (
          <label className="flex items-center gap-2 text-sm">
            <Switch id="object-computed" checked={computed} onCheckedChange={setComputed} />
            {t("objects.showComputed")}
          </label>
        )}
      </div>
      {list.error ? <ErrorState error={list.error} onRetry={() => list.refetch()} />
        : !list.data ? <Skeleton className="h-48" />
        : !items.length ? <EmptyState icon={Search} title={t("objects.none")} />
        : (
          <div className="min-w-0 overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{type.key}</TableHead>
                  {columns.map((c) => <TableHead key={c}>{c}</TableHead>)}
                  {computedNames.map((c) => <TableHead key={`fn-${c}`} className="text-primary" data-computed-col={c}>{c}()</TableHead>)}
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((row) => (
                  <TableRow key={row.key} data-object={row.key} className="cursor-pointer"
                            onClick={() => navigate(objectPath(type.name, row.key))}>
                    <TableCell className="font-medium">
                      <Link to={objectPath(type.name, row.key)} onClick={(e) => e.stopPropagation()} className="hover:underline">{row.key}</Link>
                      {row.conflicts?.length > 0 && <AlertTriangle className="ml-1 inline size-3.5 text-destructive" aria-label={t("objects.conflict")} />}
                    </TableCell>
                    {columns.map((c) => <TableCell key={c} className="max-w-56"><ValueCell row={row} column={c} /></TableCell>)}
                    {computedNames.map((c) => {
                      const v = row.computed?.[c]
                      return (
                        <TableCell key={`fn-${c}`} data-computed={c} className={cn(v?.error && "text-destructive")}
                                   title={v?.error ? (typeof v.error === "string" ? v.error : v.error.message) : undefined}>
                          {v?.error ? t("objects.computed.error") : fmtValue(v?.value)}
                        </TableCell>
                      )
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      {list.data && total > OBJECT_PAGE && (
        <nav className="flex items-center justify-center gap-3" aria-label={t("objects.pages")} id="object-pages">
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - OBJECT_PAGE))}>
            <ChevronLeft aria-hidden /> {t("review.previous")}
          </Button>
          <span className="text-sm text-muted-foreground">
            {t("objects.range", { from: fmtNumber(offset + 1), to: fmtNumber(Math.min(total, offset + OBJECT_PAGE)), total: fmtNumber(total) })}
          </span>
          <Button size="sm" variant="outline" disabled={offset + OBJECT_PAGE >= total} onClick={() => setOffset(offset + OBJECT_PAGE)}>
            {t("review.next")} <ChevronRight aria-hidden />
          </Button>
        </nav>
      )}
      {list.data && total <= OBJECT_PAGE && items.length > 0 && (
        <p className="text-xs text-muted-foreground">{t("objects.count", { n: fmtNumber(total) })}</p>
      )}
    </section>
  )
}

/* --- page -------------------------------------------------------------------------------------- */

export default function ObjectsPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const types = useQuery({ queryKey: keys.objectTypes, queryFn: () => api.objects.types(), retry: retryServerErrors })
  const [binding, setBinding] = useState<ObjectType | null>(null)
  const sorted = sortTypes(types.data?.types ?? [])
  const firstActive = sorted.find((ot) => ot.status === "active")
  const selectedName = params.get("type") ?? firstActive?.name ?? null
  const selected = sorted.find((ot) => ot.name === selectedName && ot.status === "active") ?? null
  const select = (name: string) => {
    const next = new URLSearchParams(params)
    next.set("type", name)
    setParams(next, { replace: true })
  }
  return (
    <>
      <PageHeader title={t("objects.title")} description={t("objects.description")} />
      <RoleNotice permission="objects.manage" message="auth.notice.objects" />
      {types.error ? (isNoSnapshot(types.error)
        ? <EmptyState icon={Boxes} title={t("objects.noSnapshot")} hint={t("objects.noSnapshot.hint")} />
        : <ErrorState error={types.error} onRetry={() => types.refetch()} />)
        : !types.data ? <Skeleton className="h-64" />
        : !sorted.length ? <EmptyState icon={Boxes} title={t("objects.empty")} hint={t("objects.empty.hint")} />
        : (
          <div className="grid min-w-0 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
            <TypeList types={sorted} selected={selected?.name ?? null} onSelect={select} onBind={setBinding} />
            {selected ? <ObjectTable key={selected.name} type={selected} />
              : <EmptyState icon={Boxes} title={t("objects.noActive")} hint={t("objects.noActive.hint")} />}
          </div>
        )}
      {types.data && sorted.some((ot) => ot.status === "proposed") && (
        <p className="mt-3 text-xs text-muted-foreground">
          <Badge variant="outline" className="mr-1 font-normal">{fmtNumber(sorted.filter((ot) => ot.status === "proposed").length)}</Badge>
          {t("objects.proposedNote")}
        </p>
      )}
      <BindDialog type={binding} onClose={() => setBinding(null)} />
    </>
  )
}
