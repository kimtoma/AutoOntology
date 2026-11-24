import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Play } from "lucide-react"
import { useMemo, useState, type FormEvent } from "react"
import { toast } from "sonner"

import { PreviewSummary } from "@/components/action-run"
import { NativeSelect } from "@/components/native-select"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { invalidateAfterAction, keys } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { useT } from "@/i18n"
import { api, ApiError, errorMessage, type ActionParam, type ActionRun } from "@/lib/api"
import { roleLabel } from "@/lib/auth"
import { initialParams, missingParams, newIdempotencyKey, paramPayload, type ParamValues } from "@/lib/logic"

export interface ActionTarget { type: string; key: string; title?: string | null; name: string; label: string }

/** Run an action on one object: a form for its parameters, a live preview of what it would
    do, then submit (one idempotency key per opened dialog). */
export function ActionDialog({ target, onClose, onOpenRun }: {
  target: ActionTarget | null
  onClose: () => void
  /** show a run (the toast's "View run") */
  onOpenRun?: (id: string) => void
}) {
  return (
    <Dialog open={!!target} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl" id="action-dialog">
        {target && <ActionForm key={`${target.name}|${target.key}`} target={target} onClose={onClose} onOpenRun={onOpenRun} />}
      </DialogContent>
    </Dialog>
  )
}

function ParamField({ param, value, onChange }: {
  param: ActionParam; value: string | boolean; onChange: (v: string | boolean) => void
}) {
  const t = useT()
  const id = `param-${param.name}`
  const label = param.label || param.name
  const enumValues = Array.isArray(param.enum) && param.enum.length ? param.enum : null
  const head = (
    <Label htmlFor={id} className="gap-1">
      {label}
      {param.required && param.type !== "bool" && <span className="text-destructive" aria-hidden>*</span>}
      {param.required && param.type !== "bool" && <span className="sr-only">({t("logic.param.required")})</span>}
    </Label>
  )
  if (param.type === "bool") {
    return (
      <div className="flex items-center justify-between gap-3" data-param-field={param.name}>
        {head}
        <Switch id={id} checked={value === true} onCheckedChange={(v) => onChange(v)} />
      </div>
    )
  }
  return (
    <div className="grid gap-1.5" data-param-field={param.name}>
      {head}
      {enumValues ? (
        <NativeSelect id={id} value={String(value)} onChange={(e) => onChange(e.target.value)} required={param.required}>
          {!param.required && <option value="">{t("logic.param.none")}</option>}
          {param.required && value === "" && <option value="" disabled>{t("logic.param.choose")}</option>}
          {enumValues.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
        </NativeSelect>
      ) : (
        <Input id={id} value={String(value)} required={param.required}
               type={param.type === "number" ? "number" : param.type === "date" ? "date" : "text"}
               step={param.type === "number" ? "any" : undefined}
               onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  )
}

function ActionForm({ target, onClose, onOpenRun }: {
  target: ActionTarget; onClose: () => void; onOpenRun?: (id: string) => void
}) {
  const t = useT()
  const qc = useQueryClient()
  // one key per opened dialog: a double submit or a retry after a lost answer runs once
  const [idempotencyKey] = useState(newIdempotencyKey)
  const actions = useQuery({ queryKey: keys.actions, queryFn: () => api.actions.list() })
  const info = actions.data?.find((a) => a.name === target.name)
  const params = useMemo(() => info?.params ?? [], [info])
  const [values, setValues] = useState<ParamValues | null>(null)
  const current = values ?? initialParams(params)
  const payload = useMemo(() => paramPayload(params, current), [params, current])
  const missing = missingParams(params, current)
  const debounced = useDebounced(JSON.stringify(payload), 300)
  const preview = useQuery({
    queryKey: [...keys.actions, "preview", target.name, target.key, debounced],
    queryFn: () => api.actions.preview(target.name, target.key, JSON.parse(debounced)),
    enabled: !!info && !missing.length,
    placeholderData: keepPreviousData,
    retry: false,
  })
  const submit = useMutation({
    mutationFn: () => api.actions.submit(target.name, target.key, payload, idempotencyKey),
    onSuccess: ({ run }) => {
      invalidateAfterAction(qc)
      announce(run)
      onClose()
    },
  })
  const announce = (run: ActionRun) => {
    const action = onOpenRun ? { label: t("logic.run.view"), onClick: () => onOpenRun(run.id) } : undefined
    if (run.status === "pending_approval") {
      toast.info(t("logic.action.pendingToast", { role: roleLabel(run.approval_role) }), { action, id: run.id })
    } else if (run.status === "applied") {
      toast.success(t("logic.action.appliedToast", { action: target.label, key: target.key }), { action, id: run.id })
    } else {
      toast(t("logic.action.statusToast", { status: run.status }), { action, id: run.id })
    }
  }
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) submit.mutate()
  }
  const stale = preview.isPlaceholderData || debounced !== JSON.stringify(payload)
  const canSubmit = !!preview.data?.can_submit && !preview.error && !stale && !missing.length && !submit.isPending
  const submitError = submit.error
  const failures = submitError instanceof ApiError && submitError.code === "precondition_failed"
    ? (submitError.body?.failures as { message: string }[] | undefined) ?? [] : []
  return (
    <form onSubmit={onSubmit} className="grid gap-4" id="action-form" data-action-name={target.name}>
      <DialogHeader>
        <DialogTitle>{target.label || target.name}</DialogTitle>
        <DialogDescription>
          {t("logic.action.on", { key: target.key, title: target.title ? ` (${target.title})` : "" })}
          {info?.description ? ` · ${info.description}` : ""}
        </DialogDescription>
      </DialogHeader>
      {actions.error ? <p role="alert" className="text-sm text-destructive">{errorMessage(actions.error)}</p>
        : !actions.data ? <Skeleton className="h-20" />
        : !info ? <p role="alert" className="text-sm text-destructive">{t("logic.action.gone")}</p>
        : (
          <>
            {params.length > 0 ? (
              <fieldset className="grid gap-3" aria-label={t("logic.action.params")}>
                {params.map((p) => (
                  <ParamField key={p.name} param={p} value={current[p.name] ?? ""}
                              onChange={(v) => setValues({ ...current, [p.name]: v })} />
                ))}
              </fieldset>
            ) : <p className="text-sm text-muted-foreground">{t("logic.action.noParams")}</p>}
            <section className="grid gap-2 rounded-lg border bg-muted/30 p-3" aria-label={t("logic.preview.title")} id="action-preview"
                     aria-busy={preview.isFetching || stale}>
              <h3 className="text-sm font-medium">{t("logic.preview.title")}</h3>
              {missing.length ? <p className="text-sm text-muted-foreground" data-missing>{t("logic.param.missing", { list: missing.join(", ") })}</p>
                : preview.error ? <p role="alert" className="text-sm text-destructive" data-preview-error>{errorMessage(preview.error)}</p>
                : !preview.data ? <Skeleton className="h-16" />
                : (
                  <>
                    <PreviewSummary preview={preview.data} />
                    {!preview.data.permission.allowed && (
                      <p className="text-sm text-destructive" data-denied>
                        {t("logic.action.minRole", { role: roleLabel(preview.data.permission.min_role) })}
                      </p>
                    )}
                  </>
                )}
            </section>
          </>
        )}
      {submitError && (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
             id="action-submit-error">
          <p>{errorMessage(submitError)}</p>
          {failures.length > 1 && <ul className="mt-1 list-disc pl-5">{failures.map((f, i) => <li key={i}>{f.message}</li>)}</ul>}
        </div>
      )}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={!canSubmit} id="btn-action-submit">
          {submit.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
          {preview.data?.approval.required ? t("logic.action.request") : t("logic.action.run")}
        </Button>
      </DialogFooter>
    </form>
  )
}
