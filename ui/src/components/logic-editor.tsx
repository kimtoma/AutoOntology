import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, FlaskConical, History, Loader2, Plus, Power, PowerOff, Save, X, XCircle } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { KindBadge, StateBadge } from "@/components/logic-badges"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { invalidateAfterLogic, keys } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { useSpecValidation, type SpecValidation } from "@/hooks/use-spec-validation"
import { fmtDate, useT } from "@/i18n"
import { api, ApiError, errorMessage, type FunctionTest, type LogicKind, type SpecError } from "@/lib/api"
import { useCan, useDenied } from "@/lib/auth"
import { fmtValue, templateSpec, toYaml, typeOf } from "@/lib/logic"
import { useArchived } from "@/lib/project"
import { cn } from "@/lib/utils"

export interface EditorTarget { kind: LogicKind; name: string | null }

/** The errors of a spec: where (path, position) and what. */
export function SpecErrors({ errors, id }: { errors: SpecError[]; id?: string }) {
  const t = useT()
  if (!errors.length) return null
  return (
    <ul className="grid gap-1 text-sm" id={id} aria-label={t("logic.editor.errors")}>
      {errors.map((e, i) => (
        <li key={i} className="flex items-start gap-1.5 text-destructive" data-spec-error={e.path}>
          <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">
            <code className="rounded bg-destructive/10 px-1 font-mono text-xs">{e.path || "spec"}</code>
            {e.pos != null && <span className="text-xs"> · {t("logic.editor.pos", { n: e.pos })}</span>}
            {" "}{e.message}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** A plain monospaced YAML editor (no editor dependency). */
export function YamlArea({ value, onChange, readOnly, id, label, rows = 16 }: {
  value: string; onChange?: (v: string) => void; readOnly?: boolean; id: string; label: string; rows?: number
}) {
  return (
    <Textarea id={id} aria-label={label} value={value} readOnly={readOnly} rows={rows} spellCheck={false}
              autoCapitalize="off" autoComplete="off" autoCorrect="off"
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                // Tab indents two spaces instead of leaving the field
                if (e.key !== "Tab" || readOnly || e.shiftKey) return
                e.preventDefault()
                const el = e.currentTarget
                const { selectionStart: a, selectionEnd: b } = el
                const next = `${el.value.slice(0, a)}  ${el.value.slice(b)}`
                onChange?.(next)
                requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = a + 2 })
              }}
              className="min-h-64 font-mono text-xs leading-relaxed whitespace-pre" />
  )
}

export function ValidationLine({ v }: { v: SpecValidation }) {
  const t = useT()
  return (
    <div aria-live="polite" id="spec-validation" data-valid={v.result ? v.result.valid : undefined}>
      {v.pending ? <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" aria-hidden />{t("logic.editor.checking")}</p>
        : v.error ? <p className="text-sm text-destructive">{errorMessage(v.error)}</p>
        : v.result?.valid ? <p className="flex items-center gap-1.5 text-sm text-success"><CheckCircle2 className="size-4" aria-hidden />{t("logic.editor.valid")}</p>
        : v.result ? <SpecErrors errors={v.result.errors} />
        : null}
    </div>
  )
}

function TestPanel({ text, name, dirty, objectType }: { text: string; name: string | null; dirty: boolean; objectType: string }) {
  const t = useT()
  const types = useQuery({ queryKey: keys.objectTypes, queryFn: () => api.objects.types() })
  const type = typeOf(types.data?.types, objectType)
  const [q, setQ] = useState("")
  const debounced = useDebounced(q.trim(), 300)
  const [picked, setPicked] = useState<string[]>([])
  const found = useQuery({
    queryKey: keys.objects(type?.name ?? "", { q: debounced, limit: 8 }),
    queryFn: () => api.objects.list(type!.name, { q: debounced, limit: 8 }),
    enabled: !!type && type.status === "active",
    retry: false,
  })
  const test = useMutation({
    mutationFn: (): Promise<FunctionTest> =>
      api.logic.test(name && !dirty ? { name } : { spec: text }, picked),
  })
  return (
    <section className="grid gap-2 rounded-lg border p-3" id="function-test" aria-labelledby="function-test-title">
      <h3 id="function-test-title" className="flex items-center gap-1.5 text-sm font-medium"><FlaskConical className="size-4" aria-hidden />{t("logic.test.title")}</h3>
      {!type ? <p className="text-xs text-muted-foreground">{t("logic.test.noType", { type: objectType || "—" })}</p> : (
        <>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("logic.test.search", { type: type.label })}
                 aria-label={t("logic.test.search", { type: type.label })} id="test-search" />
          {found.data && found.data.items.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label={t("logic.test.found")}>
              {found.data.items.filter((o) => !picked.includes(o.key)).map((o) => (
                <li key={o.key}>
                  <button type="button" data-pick={o.key} className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs hover:bg-accent"
                          disabled={picked.length >= 20} onClick={() => setPicked([...picked, o.key])}>
                    <Plus className="size-3" aria-hidden />{o.key}{o.title ? ` · ${o.title}` : ""}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {picked.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label={t("logic.test.picked")}>
              {picked.map((k) => (
                <li key={k}>
                  <Badge variant="secondary" className="gap-1 font-normal" data-picked={k}>
                    {k}
                    <button type="button" aria-label={t("logic.test.remove", { key: k })} onClick={() => setPicked(picked.filter((p) => p !== k))}>
                      <X className="size-3" aria-hidden />
                    </button>
                  </Badge>
                </li>
              ))}
            </ul>
          )}
          <Button size="sm" variant="outline" className="justify-self-start" disabled={!picked.length || test.isPending}
                  onClick={() => test.mutate()} id="btn-test-run">
            {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <FlaskConical aria-hidden />} {t("logic.test.run")}
          </Button>
          {test.error && (
            <div role="alert" className="text-sm text-destructive">
              <p>{errorMessage(test.error)}</p>
              {test.error instanceof ApiError && Array.isArray(test.error.body?.errors) && (
                <SpecErrors errors={test.error.body.errors as SpecError[]} />
              )}
            </div>
          )}
          {test.data && (
            <>
              <SpecErrors errors={test.data.errors} />
              <table className="w-full text-sm" id="test-results">
                <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-normal">{t("logic.test.object")}</th><th className="py-1 font-normal">{t("logic.test.value")}</th></tr></thead>
                <tbody>
                  {test.data.results.map((r) => (
                    <tr key={r.key} className="border-t" data-result={r.key}>
                      <td className="py-1">{r.key}{r.title ? <span className="text-muted-foreground"> · {r.title}</span> : null}</td>
                      <td className={cn("py-1 font-medium", r.error && "text-destructive")}>{r.error ? r.error.message : fmtValue(r.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </section>
  )
}

/** A sheet to write, check, save, activate and retire one definition (or a new one). */
export function LogicEditorSheet({ target, onClose, onSaved }: {
  target: EditorTarget | null; onClose: () => void; onSaved?: (kind: LogicKind, name: string) => void
}) {
  return (
    <Sheet open={!!target} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-3xl" id="logic-editor" aria-describedby="logic-editor-desc">
        {target && <EditorBody key={`${target.kind}|${target.name ?? ""}`} target={target} onSaved={onSaved} />}
      </SheetContent>
    </Sheet>
  )
}

function EditorBody({ target, onSaved }: { target: EditorTarget; onSaved?: (kind: LogicKind, name: string) => void }) {
  const t = useT()
  const qc = useQueryClient()
  const archived = useArchived()
  const canEdit = useCan("logic.edit")
  const editDenied = useDenied("logic.edit")
  const canTest = useCan("functions.call")
  const def = useQuery({
    queryKey: keys.logicDef(target.kind, target.name ?? ""),
    queryFn: () => api.logic.get(target.kind, target.name!),
    enabled: !!target.name,
  })
  const loaded = def.data?.definition
  // a function an active action or workflow uses is the owner's to activate or retire
  const usedBy = def.data?.used_by ?? []
  const statusPerm = target.kind === "function" && usedBy.length === 0 ? "logic.edit" : "logic.activate"
  const canStatus = useCan(statusPerm)
  const denied = useDenied(statusPerm)
  const ownerOnly = target.kind === "function" && usedBy.length > 0 && !canStatus
    ? t("logic.editor.usedBy", { names: usedBy.map((u) => u.replace(/^[a-z]+:/, "")).join(", ") })
    : undefined
  const statusDenied = ownerOnly ?? denied
  // null: not edited yet (shows the stored definition once it is loaded)
  const [text, setText] = useState<string | null>(target.name ? null : templateSpec(target.kind))
  const original = loaded ? toYaml(loaded.spec) : null
  const value = text ?? original ?? ""
  const dirty = original === null || value !== original
  const validation = useSpecValidation(value)
  const [viewing, setViewing] = useState<number | null>(null)
  const [confirm, setConfirm] = useState(false)

  const save = useMutation({
    mutationFn: () => api.logic.save(value),
    onSuccess: ({ definition }) => {
      invalidateAfterLogic(qc)
      toast.success(t("logic.editor.saved", { name: definition.label || definition.name, version: definition.version }))
      setText(toYaml(definition.spec))
      onSaved?.(definition.kind, definition.name)
    },
  })
  const status = useMutation({
    mutationFn: (next: "active" | "retired" | "draft") => api.logic.setStatus(target.kind, loaded!.name, next),
    onSuccess: ({ definition }) => {
      invalidateAfterLogic(qc)
      setConfirm(false)
      toast.success(t(`logic.editor.status.${definition.status === "active" ? "activated" : definition.status === "retired" ? "retired" : "drafted"}`,
                      { name: definition.label || definition.name }))
    },
    onError: () => setConfirm(false),
  })
  const ro = archived ? t("projects.readOnly") : undefined
  const saveError = save.error instanceof ApiError && Array.isArray(save.error.body?.errors)
    ? save.error.body.errors as SpecError[] : null
  const history = def.data?.history ?? []
  const shown = viewing !== null ? history[viewing] : null
  // the object type the spec names (a top-level `object_type:` line), for the test panel
  const typeLine = /^object_type:\s*['"]?([^'"\n]+?)['"]?\s*$/m.exec(value)
  const specType = typeLine ? typeLine[1] : String(loaded?.spec.object_type ?? "")
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {loaded ? loaded.label || loaded.name : t("logic.editor.new")}
          <KindBadge kind={target.kind} />
          {loaded && <StateBadge status={loaded.status} title={loaded.broken_reason || undefined} />}
          {loaded && <span className="text-xs font-normal text-muted-foreground">v{loaded.version}</span>}
        </SheetTitle>
        <SheetDescription id="logic-editor-desc">
          {loaded ? t("logic.editor.updated", { who: loaded.created_by ?? "—", date: fmtDate(loaded.updated_at) }) : t(`logic.editor.newHint.${target.kind}`)}
        </SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 p-4">
        {def.error ? <p role="alert" className="text-sm text-destructive">{errorMessage(def.error)}</p>
          : text === null && original === null ? <Skeleton className="h-64" />
          : (
            <>
              {loaded?.broken_reason && (
                <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive" id="broken-reason">
                  {t(loaded.needs_reactivation ? "logic.editor.needsReactivation" : "logic.editor.broken",
                     { reason: loaded.broken_reason })}
                </p>
              )}
              {ownerOnly && <p className="text-xs text-muted-foreground" id="used-by-hint">{ownerOnly}</p>}
              <YamlArea id="spec-text" label={t("logic.editor.spec")} value={value} onChange={setText} readOnly={!canEdit || archived} />
              <ValidationLine v={validation} />
              {saveError && <SpecErrors errors={saveError} id="save-errors" />}
              {save.error && !saveError && <p role="alert" className="text-sm text-destructive">{errorMessage(save.error)}</p>}
              {status.error && <p role="alert" className="text-sm text-destructive" id="status-error">{errorMessage(status.error)}</p>}
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => save.mutate()} id="btn-logic-save"
                        disabled={!canEdit || archived || !dirty || save.isPending || validation.result?.valid === false}
                        title={ro ?? editDenied}>
                  {save.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Save aria-hidden />} {t("logic.editor.save")}
                </Button>
                {loaded && loaded.status !== "active" && (
                  <Button variant="outline" id="btn-logic-activate" disabled={!canStatus || archived || dirty || status.isPending}
                          title={ro ?? statusDenied ?? (dirty ? t("logic.editor.saveFirst") : undefined)} onClick={() => setConfirm(true)}>
                    <Power aria-hidden /> {t("logic.editor.activate")}
                  </Button>
                )}
                {loaded && loaded.status === "active" && (
                  <Button variant="outline" id="btn-logic-retire" disabled={!canStatus || archived || status.isPending}
                          title={ro ?? statusDenied} onClick={() => status.mutate("retired")}>
                    <PowerOff aria-hidden /> {t("logic.editor.retire")}
                  </Button>
                )}
              </div>
              {loaded && loaded.evidence.length > 0 && (
                <section className="grid gap-1.5" aria-label={t("logic.evidence")}>
                  <h3 className="text-sm font-medium">{t("logic.evidence")}</h3>
                  <ul className="grid gap-1.5">
                    {loaded.evidence.map((e, i) => (
                      <li key={i} className="rounded-md border-l-2 border-primary/40 bg-muted/40 px-3 py-1.5 text-sm">
                        “{e.quote}” <span className="text-xs text-muted-foreground">{e.document}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {target.kind === "function" && canTest && <TestPanel text={value} name={loaded?.name ?? null} dirty={dirty} objectType={specType} />}
              {history.length > 0 && (
                <section className="grid gap-2" id="logic-history" aria-labelledby="logic-history-title">
                  <h3 id="logic-history-title" className="flex items-center gap-1.5 text-sm font-medium"><History className="size-4" aria-hidden />{t("logic.editor.history")}</h3>
                  <ul className="grid gap-1 text-sm">
                    {history.map((h, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2" data-history-version={h.version}>
                        <span className="tabular-nums">v{h.version}</span>
                        <StateBadge status={h.status} />
                        <span className="text-xs text-muted-foreground">{h.note} · {h.changed_by ?? "—"} · {fmtDate(h.changed_at)}</span>
                        <Button size="sm" variant="ghost" className="ml-auto h-7" onClick={() => setViewing(viewing === i ? null : i)}
                                aria-expanded={viewing === i}>
                          {viewing === i ? t("logic.editor.hide") : t("logic.editor.view")}
                        </Button>
                      </li>
                    ))}
                  </ul>
                  {shown && (
                    <div className="grid gap-2">
                      <YamlArea id="history-spec" label={t("logic.editor.versionSpec", { n: shown.version })} value={toYaml(shown.spec)} readOnly rows={10} />
                      {canEdit && !archived && (
                        <Button size="sm" variant="outline" className="justify-self-start" onClick={() => { setText(toYaml(shown.spec)); setViewing(null) }}>
                          {t("logic.editor.restore", { n: shown.version })}
                        </Button>
                      )}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
      </div>
      <AlertDialog open={confirm} onOpenChange={(o) => { if (!o && !status.isPending) setConfirm(false) }}>
        <AlertDialogContent id="activate-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("logic.activate.title", { name: loaded?.label || loaded?.name || "" })}</AlertDialogTitle>
            <AlertDialogDescription>{t(`logic.activate.body.${target.kind}`)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={status.isPending}>{t("common.cancel")}</AlertDialogCancel>
            <Button id="btn-activate-confirm" disabled={status.isPending} onClick={() => status.mutate("active")}>
              {status.isPending && <Loader2 className="animate-spin" aria-hidden />} {t("logic.editor.activate")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
