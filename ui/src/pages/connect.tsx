import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Check, Copy, FolderOpen, KeyRound, Loader2, Plus, Plug, RotateCw, Trash2 } from "lucide-react"
import { useState, type CSSProperties, type FormEvent, type ReactNode } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { keys } from "@/hooks/queries"
import { fmtDate, fmtNumber, t, type MessageKey } from "@/i18n"
import {
  api, errorMessage, type AgentToken, type AgentTokenIssued, type AgentTool, type AgentUsage,
} from "@/lib/api"
import {
  endpoints, snippet, SNIPPETS, TOKEN_PLACEHOLDER, toolParams, type Endpoints,
} from "@/lib/connect"
import { projectName, useArchived, useProject } from "@/lib/project"
import { cn } from "@/lib/utils"

const USAGE_DAYS = 30
/** Fixed token-table columns, so the header and every row line up (a row without actions too). */
const TOKEN_COLS = {
  "--token-cols": "minmax(0,1.4fr) 5.5rem minmax(0,1.2fr) minmax(0,1.1fr) minmax(0,1.1fr) 3.5rem 12.5rem",
} as CSSProperties

/* --- copy --------------------------------------------------------------------------------- */

function CopyButton({ text, what, className }: { text: string; what: string; className?: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setDone(true)
      window.setTimeout(() => setDone(false), 1500)
    } catch {
      toast.error(t("connect.copyFailed"))
    }
  }
  return (
    <Button type="button" size="sm" variant="outline" className={cn("shrink-0", className)}
            aria-label={t("connect.copyLabel", { what })} onClick={copy}>
      {done ? <Check aria-hidden /> : <Copy aria-hidden />}
      <span aria-live="polite">{done ? t("connect.copied") : t("connect.copy")}</span>
    </Button>
  )
}

/** A labelled one-line value (URL, token) with a copy button; long values wrap. */
function CopyField({ label, value, note, id }: { label: string; value: string; note?: string; id?: string }) {
  return (
    <div className="grid gap-1" data-field={id}>
      <p className="text-xs font-medium text-muted-foreground">
        {label}{note && <span className="font-normal"> · {note}</span>}
      </p>
      <div className="flex min-w-0 items-start gap-2">
        <code className="min-w-0 flex-1 rounded-md border bg-muted/40 px-2 py-1.5 font-mono text-xs break-all"
              id={id}>{value}</code>
        <CopyButton text={value} what={label} />
      </div>
    </div>
  )
}

/* --- snippets ----------------------------------------------------------------------------- */

function SnippetTabs({ ep, token, idPrefix }: { ep: Endpoints; token: string; idPrefix: string }) {
  const query = t("connect.snippet.sampleQuery")
  return (
    <Tabs defaultValue="claudeCode" className="min-w-0 gap-2">
      <TabsList className="h-auto! max-w-full flex-wrap justify-start">
        {SNIPPETS.map((id) => (
          <TabsTrigger key={id} value={id} className="h-8 flex-none">{t(`connect.snippet.${id}` as MessageKey)}</TabsTrigger>
        ))}
      </TabsList>
      {SNIPPETS.map((id) => {
        const text = snippet(id, ep, token, query)
        const title = t(`connect.snippet.${id}` as MessageKey)
        return (
          <TabsContent key={id} value={id} className="grid min-w-0 gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">{t(`connect.snippet.${id}.hint` as MessageKey)}</p>
              <CopyButton text={text} what={title} />
            </div>
            <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed
                            whitespace-pre-wrap break-all" data-snippet={id} id={`${idPrefix}-${id}`}>{text}</pre>
          </TabsContent>
        )
      })}
    </Tabs>
  )
}

/* --- tokens ------------------------------------------------------------------------------- */

const STATUS_TONE: Record<string, string> = {
  active: "border-success/30 bg-success/10 text-success",
  revoked: "border-destructive/30 bg-destructive/10 text-destructive",
  expired: "border-warning/40 bg-warning/10 text-amber-700 dark:text-amber-300",
}

function TokenStatus({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={cn("font-normal", STATUS_TONE[status])} data-status={status}>
      {t(`connect.status.${status}` as MessageKey)}
    </Badge>
  )
}

const revisionLabel = (pin: number | null) =>
  pin == null ? t("connect.revision.latest") : t("connect.revision.pinned", { n: pin })

type Confirm = { kind: "rotate" | "revoke"; token: AgentToken } | null

function TokenRow({ token, calls, onAsk }: {
  token: AgentToken; calls: number; onAsk: (c: NonNullable<Confirm>) => void
}) {
  const active = token.status === "active"
  const archived = useArchived()
  const ro = archived ? t("projects.readOnly") : undefined
  return (
    <li className="grid gap-2 rounded-lg border p-3 lg:grid-cols-[var(--token-cols)]
                   lg:items-center lg:gap-3 lg:rounded-none lg:border-0 lg:border-t lg:px-1 lg:py-2"
        data-token={token.id}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium" title={token.name}>{token.name}</span>
        <span className="lg:hidden"><TokenStatus status={token.status} /></span>
      </div>
      <span className="hidden lg:block"><TokenStatus status={token.status} /></span>
      <dl className="contents text-sm">
        <Cell label={t("connect.col.revision")}>{revisionLabel(token.revision_pin)}</Cell>
        <Cell label={t("connect.col.expires")}>{token.expires_at ? fmtDate(token.expires_at) : t("connect.never")}</Cell>
        <Cell label={t("connect.col.lastUsed")}>{token.last_used_at ? fmtDate(token.last_used_at) : t("connect.notUsed")}</Cell>
        <Cell label={t("connect.col.calls")} right><span data-calls>{fmtNumber(calls)}</span></Cell>
      </dl>
      <div className="flex gap-1.5 lg:justify-end">
        {active && (
          <>
            <Button size="sm" variant="outline" aria-label={t("connect.rotateLabel", { name: token.name })}
                    data-action="rotate" disabled={archived} title={ro} onClick={() => onAsk({ kind: "rotate", token })}>
              <RotateCw aria-hidden /> {t("connect.rotate")}
            </Button>
            <Button size="sm" variant="outline" className="text-destructive hover:text-destructive"
                    aria-label={t("connect.revokeLabel", { name: token.name })}
                    data-action="revoke" disabled={archived} title={ro} onClick={() => onAsk({ kind: "revoke", token })}>
              <Trash2 aria-hidden /> {t("connect.revoke")}
            </Button>
          </>
        )}
      </div>
    </li>
  )
}

/** A value with its column name shown on narrow screens (the header row shows it on wide ones). */
function Cell({ label, children, right }: { label: string; children: ReactNode; right?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 lg:block">
      <dt className="text-xs text-muted-foreground lg:sr-only">{label}</dt>
      <dd className={cn("min-w-0 truncate text-right", right ? "lg:text-right" : "lg:text-left")}>{children}</dd>
    </div>
  )
}

function TokensCard({ tokens, usage, onCreate, onAsk }: {
  tokens: AgentToken[]; usage: AgentUsage | undefined; onCreate: () => void; onAsk: (c: NonNullable<Confirm>) => void
}) {
  const calls = new Map((usage?.by_token ?? []).map((u) => [u.token_id, u.calls]))
  const archived = useArchived()
  const ro = archived ? t("projects.readOnly") : undefined
  return (
    <Card className="gap-3 py-4" id="connect-tokens">
      <CardHeader className="flex flex-wrap items-start justify-between gap-2 px-4">
        <div className="grid gap-1">
          <CardTitle className="flex items-center gap-2 text-base"><KeyRound className="size-4" aria-hidden /> {t("connect.tokens")}</CardTitle>
          <CardDescription>{t("connect.tokens.hint")}</CardDescription>
        </div>
        {tokens.length > 0 && (
          <Button size="sm" onClick={onCreate} id="btn-token-create" disabled={archived} title={ro}><Plus aria-hidden /> {t("connect.create")}</Button>
        )}
      </CardHeader>
      <CardContent className="px-4">
        {!tokens.length ? (
          <EmptyState icon={KeyRound} title={t("connect.empty.title")} hint={t("connect.empty.hint")}
                      action={<Button onClick={onCreate} id="btn-token-create" disabled={archived} title={ro}><Plus aria-hidden /> {t("connect.create")}</Button>} />
        ) : (
          <div style={TOKEN_COLS}>
            <div aria-hidden className="hidden gap-3 px-1 pb-2 text-xs text-muted-foreground
                                        lg:grid lg:grid-cols-[var(--token-cols)]">
              <span>{t("connect.col.name")}</span>
              <span>{t("connect.col.status")}</span>
              <span>{t("connect.col.revision")}</span>
              <span>{t("connect.col.expires")}</span>
              <span>{t("connect.col.lastUsed")}</span>
              <span className="text-right">{t("connect.col.calls")}</span>
              <span className="text-right">{t("connect.col.actions")}</span>
            </div>
            <ul className="grid gap-2 lg:gap-0" aria-label={t("connect.tokens")}>
              {tokens.map((tok) => (
                <TokenRow key={tok.id} token={tok} calls={calls.get(tok.id) ?? 0} onAsk={onAsk} />
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/* --- create / one-time dialog ------------------------------------------------------------- */

type Issued = AgentTokenIssued & { rotated: boolean }
type Expiry = "30" | "90" | "never"

function CreateForm({ onIssued, onCancel }: { onIssued: (i: Issued) => void; onCancel: () => void }) {
  const qc = useQueryClient()
  const revisions = useQuery({ queryKey: keys.revisions, queryFn: () => api.revisions() })
  const [name, setName] = useState("")
  const [revision, setRevision] = useState("latest")
  const [expiry, setExpiry] = useState<Expiry>("90")
  const create = useMutation({
    mutationFn: () => api.agent.create({
      name: name.trim(),
      revision_pin: revision === "latest" ? null : Number(revision),
      expires_days: expiry === "never" ? null : (Number(expiry) as 30 | 90),
    }),
    onSuccess: (issued) => {
      qc.invalidateQueries({ queryKey: keys.agentTokens })
      onIssued({ ...issued, rotated: false })
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (name.trim() && !create.isPending) create.mutate()
  }
  return (
    <form onSubmit={submit} className="grid gap-4" id="token-form">
      <DialogHeader>
        <DialogTitle>{t("connect.dialog.title")}</DialogTitle>
        <DialogDescription>{t("connect.dialog.description")}</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label htmlFor="token-name">{t("connect.field.name")}</Label>
        <Input id="token-name" value={name} maxLength={80} autoComplete="off" required
               placeholder={t("connect.field.namePlaceholder")} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="token-revision">{t("connect.field.revision")}</Label>
        <Select value={revision} onValueChange={setRevision}>
          <SelectTrigger id="token-revision" className="w-full" aria-describedby="token-revision-hint">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="latest">{t("connect.field.revision.latest")}</SelectItem>
            {(revisions.data ?? []).map((r) => (
              <SelectItem key={r.revision} value={String(r.revision)}>
                {t("connect.field.revision.pin", { n: r.revision, date: fmtDate(r.created_at) })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground" id="token-revision-hint">{t("connect.field.revision.hint")}</p>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="token-expiry">{t("connect.field.expiry")}</Label>
        <Select value={expiry} onValueChange={(v) => setExpiry(v as Expiry)}>
          <SelectTrigger id="token-expiry" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="30">{t("connect.expiry.30")}</SelectItem>
            <SelectItem value="90">{t("connect.expiry.90")}</SelectItem>
            <SelectItem value="never">{t("connect.expiry.never")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {create.isError && (
        <p role="alert" className="text-sm break-words text-destructive">
          {t("connect.failed", { error: errorMessage(create.error) })}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={!name.trim() || create.isPending} id="btn-token-submit">
          {create.isPending && <Loader2 className="animate-spin" aria-hidden />}
          {create.isPending ? t("connect.creating") : t("connect.create")}
        </Button>
      </DialogFooter>
    </form>
  )
}

function OneTime({ issued, ep, onDone }: { issued: Issued; ep: Endpoints | null; onDone: () => void }) {
  const name = issued.record.name
  return (
    <div className="grid min-w-0 gap-4" id="token-once">
      <DialogHeader>
        <DialogTitle>{issued.rotated ? t("connect.once.rotatedTitle", { name }) : t("connect.once.title", { name })}</DialogTitle>
        <DialogDescription>{t("connect.once.description")}</DialogDescription>
      </DialogHeader>
      <p role="alert" className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
        {t("connect.once.warning")}
      </p>
      <CopyField label={t("connect.once.token")} value={issued.token} id="token-raw" />
      {ep && (
        <div className="grid min-w-0 gap-2">
          <p className="text-sm font-medium">{t("connect.once.snippets")}</p>
          <SnippetTabs ep={ep} token={issued.token} idPrefix="once" />
        </div>
      )}
      <DialogFooter>
        <Button onClick={onDone} id="btn-token-done">{t("common.done")}</Button>
      </DialogFooter>
    </div>
  )
}

/* --- tools and usage ---------------------------------------------------------------------- */

function toolDescription(tool: AgentTool): string {
  const key = `connect.tool.${tool.name}` as MessageKey
  const text = t(key)
  return text === key ? tool.description : text
}

function ToolsCard({ tools, protocol }: { tools: AgentTool[]; protocol: string }) {
  return (
    <Card className="gap-3 py-4" id="connect-tools">
      <CardHeader className="px-4">
        <CardTitle className="text-base">{t("connect.tools")}</CardTitle>
        <CardDescription>{t("connect.tools.hint", { protocol })}</CardDescription>
      </CardHeader>
      <CardContent className="px-4">
        <ul className="grid gap-3">
          {tools.map((tool) => {
            const params = toolParams(tool.inputSchema)
            return (
              <li key={tool.name} className="grid gap-1 border-t pt-3 first:border-t-0 first:pt-0" data-tool={tool.name}>
                <code className="font-mono text-sm font-semibold break-all">{tool.name}</code>
                <p className="text-sm text-muted-foreground">{toolDescription(tool)}</p>
                <p className="flex flex-wrap items-center gap-1 text-xs">
                  <span className="text-muted-foreground">{t("connect.tools.params")}:</span>
                  {params.length ? params.map((p) => (
                    <Badge key={p.name} variant="outline" className="font-mono font-normal" data-param={p.name}>
                      {p.name}{p.required && <span className="font-sans text-muted-foreground"> · {t("connect.tools.required")}</span>}
                    </Badge>
                  )) : <span className="text-muted-foreground">{t("connect.tools.noParams")}</span>}
                </p>
              </li>
            )
          })}
        </ul>
      </CardContent>
    </Card>
  )
}

function UsageCard({ usage }: { usage: AgentUsage }) {
  const maxTool = Math.max(1, ...usage.by_tool.map((r) => r.calls))
  const maxDay = Math.max(1, ...usage.by_day.map((r) => r.calls))
  return (
    <Card className="gap-3 py-4" id="connect-usage">
      <CardHeader className="px-4">
        <CardTitle className="text-base">{t("connect.usage")}</CardTitle>
        <CardDescription>{t("connect.usage.hint", { days: usage.days ?? USAGE_DAYS })}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 px-4">
        <dl className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border p-3">
            <dt className="text-xs text-muted-foreground">{t("connect.usage.total")}</dt>
            <dd className="text-2xl font-semibold tabular-nums" id="usage-total">{fmtNumber(usage.total)}</dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className="text-xs text-muted-foreground">{t("connect.usage.ok")}</dt>
            <dd className="text-2xl font-semibold tabular-nums" id="usage-ok">{fmtNumber(usage.ok)}</dd>
          </div>
        </dl>
        {!usage.total ? <p className="text-sm text-muted-foreground">{t("connect.usage.none")}</p> : (
          <div className="grid gap-4 md:grid-cols-2">
            <section className="grid content-start gap-2" aria-labelledby="usage-by-tool">
              <h3 className="text-sm font-medium" id="usage-by-tool">{t("connect.usage.byTool")}</h3>
              <ul className="grid gap-2">
                {usage.by_tool.map((r) => (
                  <li key={r.tool} className="grid gap-1" data-usage-tool={r.tool}>
                    <div className="flex min-w-0 items-baseline justify-between gap-2 text-xs">
                      <code className="truncate font-mono">{r.tool}</code>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {fmtNumber(r.calls)}{r.avg_ms != null && ` · ${t("connect.usage.avg", { ms: fmtNumber(r.avg_ms) })}`}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted" aria-hidden>
                      <div className="h-full rounded-full bg-primary" style={{ width: `${(r.calls / maxTool) * 100}%` }} />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <section className="grid content-start gap-2" aria-labelledby="usage-by-day">
              <h3 className="text-sm font-medium" id="usage-by-day">{t("connect.usage.byDay")}</h3>
              <ul className="grid gap-1">
                {usage.by_day.map((r) => (
                  <li key={r.day} className="grid grid-cols-[5.5rem_minmax(0,1fr)_3rem] items-center gap-2 text-xs"
                      aria-label={t("connect.usage.dayCalls", { day: r.day, n: r.calls })}>
                    <span className="tabular-nums text-muted-foreground" aria-hidden>{r.day}</span>
                    <div className="h-2 rounded-sm bg-muted" aria-hidden>
                      <div className="h-full rounded-sm bg-primary/70" style={{ width: `${(r.calls / maxDay) * 100}%` }} />
                    </div>
                    <span className="text-right tabular-nums" aria-hidden>{fmtNumber(r.calls)}</span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/* --- page --------------------------------------------------------------------------------- */

export default function ConnectPage() {
  const qc = useQueryClient()
  const tools = useQuery({ queryKey: keys.agentTools, queryFn: api.agent.tools, staleTime: Infinity })
  const tokens = useQuery({ queryKey: keys.agentTokens, queryFn: api.agent.tokens })
  const usage = useQuery({ queryKey: keys.agentUsage, queryFn: () => api.agent.usage(USAGE_DAYS) })
  const [dialog, setDialog] = useState<"closed" | "form" | Issued>("closed")
  const [confirm, setConfirm] = useState<Confirm>(null)

  const scope = useProject()
  const ep = tools.data ? endpoints(window.location.origin, tools.data, scope?.slug) : null

  const act = useMutation({
    mutationFn: async (c: NonNullable<Confirm>) =>
      c.kind === "rotate" ? { kind: c.kind, issued: await api.agent.rotate(c.token.id) }
        : { kind: c.kind, record: await api.agent.revoke(c.token.id) },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: keys.agentTokens })
      setConfirm(null)
      if (res.issued) setDialog({ ...res.issued, rotated: true })
      else if (res.record) toast.success(t("connect.revoked", { name: res.record.name }))
    },
    onError: (err) => toast.error(t("connect.failed", { error: errorMessage(err) })),
  })

  const closeDialog = () => setDialog("closed")
  const issued = typeof dialog === "object" ? dialog : null

  return (
    <>
      <PageHeader title={t("connect.title")} description={t("connect.description")} />
      <div className="grid min-w-0 gap-4">
        <Card className="gap-3 py-4" id="connect-endpoints">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-2 text-base"><Plug className="size-4" aria-hidden /> {t("connect.endpoints")}</CardTitle>
            <CardDescription className="break-words">{t("connect.endpoints.hint")}</CardDescription>
            {scope && (
              <p className="flex items-center gap-1.5 text-sm font-medium" id="connect-project-scope">
                <FolderOpen className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                {t("connect.project.scope", { name: projectName(scope.project, scope.slug) })}
              </p>
            )}
          </CardHeader>
          <CardContent className="grid gap-3 px-4">
            {tools.error ? <ErrorState error={tools.error} onRetry={() => tools.refetch()} />
              : !ep || !tools.data ? <><Skeleton className="h-12" /><Skeleton className="h-12" /></>
              : (
                <>
                  <CopyField label={t("connect.endpoint.mcp")} value={ep.mcp} id="endpoint-mcp" />
                  <CopyField label={t("connect.endpoint.rest")} value={ep.rest} id="endpoint-rest" />
                  <CopyField label={t("connect.endpoint.openapi")} value={ep.openapi}
                             note={t("connect.endpoint.openapiNote")} id="endpoint-openapi" />
                  <p className="text-xs text-muted-foreground" id="endpoint-rate">
                    {t("connect.rate", { n: fmtNumber(tools.data.rate_per_min) })}
                  </p>
                </>
              )}
          </CardContent>
        </Card>

        {tokens.error ? <ErrorState error={tokens.error} onRetry={() => tokens.refetch()} />
          : tokens.isPending ? <Skeleton className="h-40" />
          : <TokensCard tokens={tokens.data} usage={usage.data} onCreate={() => setDialog("form")} onAsk={setConfirm} />}

        {ep && (
          <Card className="gap-3 py-4" id="connect-howto">
            <CardHeader className="px-4">
              <CardTitle className="text-base">{t("connect.howto")}</CardTitle>
              <CardDescription>{t("connect.howto.hint")}</CardDescription>
            </CardHeader>
            <CardContent className="min-w-0 px-4">
              <SnippetTabs ep={ep} token={TOKEN_PLACEHOLDER} idPrefix="howto" />
            </CardContent>
          </Card>
        )}

        {tools.data && <ToolsCard tools={tools.data.tools} protocol={tools.data.protocol} />}
        {usage.data && <UsageCard usage={usage.data} />}
      </div>

      <Dialog open={dialog !== "closed"} onOpenChange={(o) => { if (!o) closeDialog() }}>
        <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl"
                       onInteractOutside={(e) => { if (issued) e.preventDefault() }}>
          {issued ? <OneTime issued={issued} ep={ep} onDone={closeDialog} />
            : dialog === "form" ? <CreateForm onIssued={setDialog} onCancel={closeDialog} /> : null}
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!confirm} onOpenChange={(o) => { if (!o && !act.isPending) setConfirm(null) }}>
        <AlertDialogContent id="token-confirm" data-kind={confirm?.kind}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirm && t(confirm.kind === "rotate" ? "connect.rotate.title" : "connect.revoke.title", { name: confirm.token.name })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirm && t(confirm.kind === "rotate" ? "connect.rotate.body" : "connect.revoke.body")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={act.isPending}>{t("common.cancel")}</AlertDialogCancel>
            <Button variant={confirm?.kind === "revoke" ? "destructive" : "default"} disabled={act.isPending}
                    id="btn-token-confirm" onClick={() => confirm && act.mutate(confirm)}>
              {act.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {confirm?.kind === "rotate" ? t("connect.rotate.go") : t("connect.revoke.go")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
