import { useQuery } from "@tanstack/react-query"
import { ArrowRight, Check, CheckCircle2, Loader2, Undo2, X, XCircle } from "lucide-react"
import { useState } from "react"

import { StateBadge, ViaBadge } from "@/components/logic-badges"
import { ProjectLink as Link } from "@/components/project-link"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import { useRunDecisions, type RunDecisions } from "@/hooks/use-run-decisions"
import { fmtDate, useT } from "@/i18n"
import { roleLabel as roleText } from "@/lib/auth"
import { api, errorMessage, type ActionPreview, type ActionRun } from "@/lib/api"
import { useArchived } from "@/lib/project"
import { fmtValue, objectPath } from "@/lib/logic"
import { cn } from "@/lib/utils"

/** What a preview says the action does: preconditions, effects (old → new), approval, mail. */
export function PreviewSummary({ preview, compact }: { preview: ActionPreview; compact?: boolean }) {
  const t = useT()
  const messages = preview.notify.length
  const recipients = preview.notify.reduce((n, m) => n + (m.recipients ?? 0), 0)
  return (
    <div className="grid gap-3 text-sm" data-preview data-ok={preview.ok}>
      {preview.preconditions.length > 0 && !compact && (
        <section aria-label={t("logic.preview.preconditions")} className="grid gap-1">
          <h4 className="text-xs font-medium text-muted-foreground">{t("logic.preview.preconditions")}</h4>
          <ul className="grid gap-1">
            {preview.preconditions.map((p) => (
              <li key={p.index} className={cn("flex items-start gap-2", !p.ok && "text-destructive")}
                  data-precondition={p.ok ? "ok" : "failed"}>
                {p.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-label={t("logic.preview.ok")} />
                  : <XCircle className="mt-0.5 size-4 shrink-0" aria-label={t("logic.preview.failed")} />}
                <span className="min-w-0 break-words">{p.ok ? t("logic.preview.passes", { message: p.message }) : p.error || p.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section aria-label={t("logic.preview.effects")} className="grid gap-1">
        <h4 className="text-xs font-medium text-muted-foreground">{t("logic.preview.effects")}</h4>
        {!preview.effects.length ? <p className="text-muted-foreground">{t("logic.preview.noEffects")}</p> : (
          <ul className="grid gap-1">
            {preview.effects.map((e) => (
              <li key={e.index} className="flex flex-wrap items-center gap-1.5" data-effect={e.property}>
                <span className="font-medium">{e.label || e.property}</span>
                {e.operational && <Badge variant="secondary" className="font-normal">{t("logic.operational")}</Badge>}
                {e.error ? <span className="text-destructive">{e.error}</span> : (
                  <>
                    <span className="text-muted-foreground line-through decoration-muted-foreground/50">{fmtValue(e.old_value)}</span>
                    <ArrowRight className="size-3.5 text-muted-foreground" aria-label={t("logic.preview.becomes")} />
                    <span className="font-medium" data-new-value>{fmtValue(e.new_value)}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span data-approval={preview.approval.required ? preview.approval.role ?? "yes" : "none"}>
          {preview.approval.required
            ? t("logic.preview.approvalNeeded", { role: roleText(preview.approval.role) })
            : t("logic.preview.noApproval")}
        </span>
        {messages > 0 && (
          <span data-notify={messages}>{t("logic.preview.notify", { n: messages, recipients })}</span>
        )}
      </div>
    </div>
  )
}

function ParamList({ params }: { params: Record<string, unknown> }) {
  const entries = Object.entries(params ?? {})
  if (!entries.length) return null
  return (
    <dl className="flex flex-wrap gap-1.5 text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="inline-flex items-center gap-1 rounded-md border px-2 py-0.5" data-param={k}>
          <dt className="text-muted-foreground">{k}</dt><dd className="font-medium">{fmtValue(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

/** One pending action run waiting for the caller's approval (Review → Approvals). */
export function ApprovalCard({ run, onOpen, decisions }: {
  run: ActionRun
  onOpen: (id: string) => void
  decisions: RunDecisions
}) {
  const t = useT()
  const archived = useArchived()
  const [note, setNote] = useState("")
  const [rejecting, setRejecting] = useState(false)
  const busy = decisions.busyId === run.id
  const err = decisions.error?.id === run.id ? decisions.error : null
  const label = run.preview?.label || run.action
  const typeLabel = run.preview?.object_type_label || run.object_type
  return (
    <li className="grid min-w-0 gap-2 rounded-xl border bg-card p-4" data-approval-run={run.id}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="font-medium">{label}</span>
        <Link to={objectPath(run.object_type, run.object_key)} className="text-sm underline-offset-4 hover:underline" data-object-link>
          {typeLabel} · {run.object_key}{run.preview?.title ? ` (${run.preview.title})` : ""}
        </Link>
        <StateBadge status={run.status} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{t("logic.run.requestedBy")}</span>
        <ViaBadge via={run.via} who={run.requested_by} />
        <span title={fmtDate(run.created_at)}>· {fmtDate(run.created_at)}</span>
        {run.approval_role && <span>· {t("logic.run.needsRole", { role: roleText(run.approval_role) })}</span>}
      </div>
      <ParamList params={run.params} />
      {run.preview && <PreviewSummary preview={run.preview} compact />}
      {err && (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
           data-error={err.code}>{err.message}</p>
      )}
      {rejecting && (
        <div className="grid gap-1.5">
          <label htmlFor={`note-${run.id}`} className="text-xs text-muted-foreground">{t("logic.run.rejectNote")}</label>
          <Input id={`note-${run.id}`} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {rejecting ? (
          <>
            <Button size="sm" variant="destructive" disabled={busy || archived} data-action="confirm-reject"
                    onClick={() => decisions.decide({ id: run.id, kind: "reject", note: note.trim() })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <X aria-hidden />} {t("logic.run.reject")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>{t("common.cancel")}</Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy || archived || !run.can_approve} data-action="approve"
                    title={archived ? t("projects.readOnly") : undefined}
                    onClick={() => decisions.decide({ id: run.id, kind: "approve" })}>
              {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("logic.run.approve")}
            </Button>
            <Button size="sm" variant="outline" disabled={busy || archived || !run.can_approve} data-action="reject"
                    onClick={() => setRejecting(true)}>
              <X aria-hidden /> {t("logic.run.reject")}
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onOpen(run.id)} data-action="open-run">
          {t("logic.run.details")}
        </Button>
      </div>
    </li>
  )
}

/** A sheet with one action run: what it asked for, its state, its edits, and the approve /
    reject / revert buttons the caller may use. */
export function ActionRunSheet({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  return (
    <Sheet open={!!runId} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-xl" id="action-run-sheet" aria-describedby="action-run-desc">
        {runId && <ActionRunBody key={runId} runId={runId} />}
      </SheetContent>
    </Sheet>
  )
}

function ActionRunBody({ runId }: { runId: string }) {
  const t = useT()
  const archived = useArchived()
  const q = useQuery({ queryKey: keys.actionRun(runId), queryFn: () => api.actions.run(runId) })
  const decisions = useRunDecisions(() => q.refetch())
  const [note, setNote] = useState("")
  const run = q.data?.run
  const busy = decisions.busyId === runId
  const err = decisions.error?.id === runId ? decisions.error : null
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {run ? run.preview?.label || run.action : t("logic.run.title")}
          {run && <StateBadge status={run.status} />}
        </SheetTitle>
        <SheetDescription id="action-run-desc" className="break-all">{runId}</SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 p-4">
        {q.error ? <p role="alert" className="text-sm text-destructive">{errorMessage(q.error)}</p>
          : !run ? <Skeleton className="h-32" />
          : (
            <>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">{t("logic.run.object")}</dt>
                <dd>
                  <Link to={objectPath(run.object_type, run.object_key)} className="underline-offset-4 hover:underline" data-object-link>
                    {run.preview?.object_type_label || run.object_type} · {run.object_key}
                  </Link>
                </dd>
                <dt className="text-muted-foreground">{t("logic.run.requestedBy")}</dt>
                <dd><ViaBadge via={run.via} who={run.requested_by} /></dd>
                <dt className="text-muted-foreground">{t("logic.run.created")}</dt>
                <dd>{fmtDate(run.created_at)}</dd>
                {run.approval_role && (
                  <>
                    <dt className="text-muted-foreground">{t("logic.run.approval")}</dt>
                    <dd>{roleText(run.approval_role)}{run.approver ? ` · ${t("logic.run.decidedBy", { who: run.approver })}` : ""}</dd>
                  </>
                )}
                {run.applied_at && (
                  <>
                    <dt className="text-muted-foreground">{t("logic.run.applied")}</dt>
                    <dd>{fmtDate(run.applied_at)}</dd>
                  </>
                )}
                {run.reverted_at && (
                  <>
                    <dt className="text-muted-foreground">{t("logic.run.reverted")}</dt>
                    <dd>{fmtDate(run.reverted_at)}</dd>
                  </>
                )}
                {run.workflow_run_id && (
                  <>
                    <dt className="text-muted-foreground">{t("logic.run.workflow")}</dt>
                    <dd className="break-all">{run.workflow_run_id}</dd>
                  </>
                )}
              </dl>
              <ParamList params={run.params} />
              {run.error && <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{run.error}</p>}
              {run.preview && <PreviewSummary preview={run.preview} />}
              {q.data && q.data.edits.length > 0 && (
                <section className="grid gap-1" aria-label={t("logic.run.edits")}>
                  <h4 className="text-xs font-medium text-muted-foreground">{t("logic.run.edits")}</h4>
                  <ul className="grid gap-1 text-sm">
                    {q.data.edits.map((e) => (
                      <li key={e.id} className={cn("flex flex-wrap items-center gap-1.5", e.reverted_at && "text-muted-foreground line-through")}
                          data-edit={e.property}>
                        <span className="font-medium">{e.property}</span>
                        <span>{fmtValue(e.old_value)}</span><ArrowRight className="size-3.5" aria-hidden /><span>{fmtValue(e.value)}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {err && (
                <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                   data-error={err.code}>{err.message}</p>
              )}
              {run.status === "pending_approval" && run.can_approve && (
                <div className="grid gap-2 border-t pt-3">
                  <Input aria-label={t("logic.run.rejectNote")} placeholder={t("logic.run.rejectNote")} value={note}
                         maxLength={500} onChange={(e) => setNote(e.target.value)} />
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" disabled={busy || archived} data-action="approve"
                            onClick={() => decisions.decide({ id: run.id, kind: "approve" })}>
                      {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />} {t("logic.run.approve")}
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy || archived} data-action="reject"
                            onClick={() => decisions.decide({ id: run.id, kind: "reject", note: note.trim() })}>
                      <X aria-hidden /> {t("logic.run.reject")}
                    </Button>
                  </div>
                </div>
              )}
              {run.status === "pending_approval" && !run.can_approve && (
                <p className="text-sm text-muted-foreground" data-waiting>{t("logic.run.waitingFor", { role: roleText(run.approval_role) })}</p>
              )}
              {run.can_revert && (
                <div className="flex flex-wrap gap-2 border-t pt-3">
                  <Button size="sm" variant="outline" disabled={busy || archived} data-action="revert"
                          onClick={() => decisions.decide({ id: run.id, kind: "revert", force: err?.code === "superseded" })}>
                    {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Undo2 aria-hidden />}
                    {err?.code === "superseded" ? t("logic.run.revertForce") : t("logic.run.revert")}
                  </Button>
                </div>
              )}
            </>
          )}
      </div>
    </>
  )
}
