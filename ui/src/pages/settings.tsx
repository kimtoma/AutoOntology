/* Settings: choose, test and apply the chat and embedding providers (Ollama, the Claude or
   ChatGPT subscription CLIs, an OpenAI-compatible API with a base URL and a key). Saving writes
   .env on the computer running AutoOntology; the engine reads its models at start-up, so the
   service and the engine restart (a detached helper; this page waits for the new service). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, RotateCcw, Save, Zap } from "lucide-react"
import { useId, useMemo, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { ErrorState } from "@/components/empty-state"
import { keys } from "@/hooks/queries"
import { PageHeader } from "@/components/page-header"
import { Code } from "@/components/provider-card"
import { CheckChip } from "@/components/status-chip"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  api, ApiError, errorMessage,
  type EmbedProviderName, type RestartState, type SettingsState, type SettingsTest,
} from "@/lib/api"
import { fmtSeconds } from "@/lib/labels"
import {
  BASE_URL_VAR, CHAT_MODEL_VAR, CHAT_PROVIDERS, changeOf, EMBED_MODEL_VAR, EMBED_PROVIDER_VAR, EMBED_PROVIDERS,
  formFrom, hasChanges, isLocked, KEY_VAR, PROVIDER_VAR, providerState, RestartTimeout, savedKeyElsewhere,
  urlOrigin, usesOpenai,
  waitForRestart, type ProviderState, type SettingsForm,
} from "@/lib/settings"
import { cn } from "@/lib/utils"
import { tx, useT, type MessageKey } from "@/i18n"

const STATE_TONE: Record<ProviderState, "ok" | "warn" | "error"> = {
  ready: "ok", notInstalled: "error", notLoggedIn: "warn", unavailable: "error", needsSetup: "warn",
}

/** A shell-set key: the field is read-only and says why. */
function Locked({ s, name }: { s: SettingsState; name: string }) {
  const t = useT()
  if (!isLocked(s, name)) return null
  return <p className="text-xs text-amber-700 dark:text-amber-300" data-locked={name}>
    {t("settings.locked", { key: name })}
  </p>
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground" id={`${id}-hint`}>{hint}</p>}
    </div>
  )
}

/** A model name: a known/pulled model from the list, or any other name typed in. */
function ModelInput({ id, label, value, options, hint, disabled, onChange, placeholder }: {
  id: string; label: string; value: string; options: string[]; hint?: ReactNode; disabled?: boolean
  onChange: (v: string) => void; placeholder?: string
}) {
  const list = `${id}-options`
  return (
    <Field id={id} label={label} hint={hint}>
      <Input id={id} list={list} value={value} disabled={disabled} placeholder={placeholder} spellCheck={false}
             autoCapitalize="off" autoComplete="off" aria-describedby={hint ? `${id}-hint` : undefined}
             onChange={(e) => onChange(e.target.value)} />
      <datalist id={list}>{options.map((m) => <option key={m} value={m} />)}</datalist>
    </Field>
  )
}

function ProviderChoice({ s, form, setForm }: {
  s: SettingsState; form: SettingsForm; setForm: (f: SettingsForm) => void
}) {
  const t = useT()
  const locked = isLocked(s, PROVIDER_VAR)
  return (
    <div role="radiogroup" aria-label={t("settings.chat.title")} className="grid gap-2 sm:grid-cols-2" id="settings-chat-providers">
      {CHAT_PROVIDERS.map((name) => {
        const state = providerState(s, name, form)
        const checked = form.chatProvider === name
        return (
          <label key={name} data-provider-option={name} data-state={state}
                 className={cn("flex min-w-0 cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors",
                               checked ? "border-primary bg-primary/5" : "hover:bg-muted/50",
                               locked && "cursor-not-allowed opacity-70")}>
            <input type="radio" name="chat-provider" value={name} checked={checked} disabled={locked}
                   className="mt-1 accent-current" onChange={() => setForm({ ...form, chatProvider: name })} />
            <span className="grid min-w-0 gap-1">
              <span className="flex flex-wrap items-center gap-2 font-medium">
                {t(`settings.provider.${name}` as MessageKey)}
                {s.running.chat_provider === name && (
                  <span className="text-xs font-normal text-muted-foreground">({t("settings.current")})</span>
                )}
              </span>
              <span className="text-xs text-muted-foreground">{t(`settings.provider.${name}.desc` as MessageKey)}</span>
              <span><CheckChip status={STATE_TONE[state]} label={t(`settings.state.${state}` as MessageKey)} /></span>
            </span>
          </label>
        )
      })}
    </div>
  )
}

function ChatDetails({ s, form, setForm }: { s: SettingsState; form: SettingsForm; setForm: (f: SettingsForm) => void }) {
  const t = useT()
  const name = form.chatProvider
  const setModel = (v: string) => setForm({ ...form, chatModels: { ...form.chatModels, [name]: v } })
  const modelVar = CHAT_MODEL_VAR[name]
  if (name === "claude" || name === "codex") {
    const cli = s.cli[name]
    return (
      <div className="grid gap-3" id="settings-chat-details" data-provider={name}>
        {!cli.found && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs" data-hint="install">
            <p>{t("settings.install", { name })}</p>
            {cli.remedy && <p className="mt-1"><Code>{cli.remedy}</Code></p>}
          </div>
        )}
        {cli.found && !cli.logged_in && (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" data-hint="login">
            <p>{t("settings.login")}</p>
            <p className="mt-1"><Code>{cli.login_command}</Code></p>
          </div>
        )}
        {cli.found && cli.logged_in && cli.detail && <p className="text-xs text-muted-foreground">{cli.detail}</p>}
        <ModelInput id="settings-chat-model" label={t("settings.chatModel")} value={form.chatModels[name]}
                    options={cli.known_models} disabled={isLocked(s, modelVar)} onChange={setModel}
                    placeholder={cli.default_model}
                    hint={t("settings.model.hint", { model: cli.default_model })} />
        <Locked s={s} name={modelVar} />
      </div>
    )
  }
  if (name === "ollama") {
    return (
      <div className="grid gap-3" id="settings-chat-details" data-provider={name}>
        {!s.ollama.reachable && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs" data-hint="ollama">
            <p>{t("settings.ollama.start", { endpoint: s.ollama.endpoint })}</p>
            <p className="mt-1"><Code>ollama serve</Code> · <Code>uv run autoontology setup</Code></p>
          </div>
        )}
        <ModelInput id="settings-chat-model" label={t("settings.chatModel")} value={form.chatModels.ollama}
                    options={s.ollama.models} disabled={isLocked(s, modelVar)} onChange={setModel}
                    hint={t("settings.model.ollamaHint", { model: "openai/qwen3:8b" })} />
        <Locked s={s} name={modelVar} />
      </div>
    )
  }
  return (
    <div className="grid gap-3" id="settings-chat-details" data-provider={name}>
      <ModelInput id="settings-chat-model" label={t("settings.chatModel")} value={form.chatModels.openai}
                  options={[]} disabled={isLocked(s, modelVar)} onChange={setModel}
                  placeholder="gpt-4.1-mini" hint={t("settings.openai.chatHint")} />
      <Locked s={s} name={modelVar} />
    </div>
  )
}

function OpenaiEndpoint({ s, form, setForm }: { s: SettingsState; form: SettingsForm; setForm: (f: SettingsForm) => void }) {
  const t = useT()
  const keyHint = useId()
  const elsewhere = savedKeyElsewhere(s, form)
  const keySaved = s.openai.api_key_set && !form.clearKey && !elsewhere
  return (
    <Card className="gap-4" id="settings-openai">
      <CardHeader>
        <CardTitle className="text-base">{t("settings.openai.title")}</CardTitle>
        <CardDescription>{t("settings.openai.hint")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Field id="settings-base-url" label={t("settings.baseUrl")}>
          <Input id="settings-base-url" type="url" inputMode="url" value={form.baseUrl} spellCheck={false}
                 autoCapitalize="off" autoComplete="off" placeholder="https://api.openai.com/v1"
                 disabled={isLocked(s, BASE_URL_VAR)} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} />
        </Field>
        <Locked s={s} name={BASE_URL_VAR} />
        <div className="grid gap-1.5">
          <Label htmlFor="settings-api-key" className="flex flex-wrap items-center gap-2">
            {t("settings.apiKey")}
            <span className={cn("text-xs font-normal", keySaved ? "text-success" : "text-muted-foreground")}
                  id="settings-key-state" data-key-saved={keySaved}>
              {keySaved ? t("settings.apiKey.saved") : form.clearKey ? t("settings.apiKey.willClear") : t("settings.apiKey.none")}
            </span>
          </Label>
          <Input id="settings-api-key" type="password" autoComplete="off" spellCheck={false} autoCapitalize="off"
                 value={form.apiKey} aria-describedby={keyHint} disabled={isLocked(s, KEY_VAR)}
                 placeholder={keySaved ? "••••••••" : ""}
                 onChange={(e) => setForm({ ...form, apiKey: e.target.value, clearKey: false })} />
          {elsewhere && (
            <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs" id="settings-key-elsewhere" role="alert">
              {t("settings.apiKey.otherEndpoint", { origin: urlOrigin(s.openai.base_url).replace(/:(80|443)$/, "") || "?" })}
            </p>
          )}
          <p className="text-xs text-muted-foreground" id={keyHint}>{t("settings.apiKey.hint")}</p>
          {s.openai.api_key_set && !isLocked(s, KEY_VAR) && (
            <div>
              <Button type="button" size="sm" variant="ghost" id="btn-clear-key"
                      onClick={() => setForm({ ...form, apiKey: "", clearKey: !form.clearKey })}>
                {form.clearKey ? t("settings.apiKey.clearUndo") : t("settings.apiKey.clear")}
              </Button>
            </div>
          )}
          <Locked s={s} name={KEY_VAR} />
        </div>
      </CardContent>
    </Card>
  )
}

function Embeddings({ s, form, setForm, dimensionWarning }: {
  s: SettingsState; form: SettingsForm; setForm: (f: SettingsForm) => void; dimensionWarning: string | null
}) {
  const t = useT()
  const locked = isLocked(s, EMBED_PROVIDER_VAR)
  const name = form.embedProvider
  const modelVar = EMBED_MODEL_VAR[name]
  return (
    <Card className="gap-4" id="settings-embed">
      <CardHeader>
        <CardTitle className="text-base">{t("settings.embed.title")}</CardTitle>
        <CardDescription>{t("settings.embed.hint")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div role="radiogroup" aria-label={t("settings.embed.title")} className="flex flex-wrap gap-2">
          {EMBED_PROVIDERS.map((p: EmbedProviderName) => (
            <label key={p} data-embed-option={p}
                   className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm",
                                 form.embedProvider === p ? "border-primary bg-primary/5" : "hover:bg-muted/50",
                                 locked && "cursor-not-allowed opacity-70")}>
              <input type="radio" name="embed-provider" value={p} checked={form.embedProvider === p} disabled={locked}
                     onChange={() => setForm({ ...form, embedProvider: p })} />
              {t(`settings.embed.${p}` as MessageKey)}
            </label>
          ))}
        </div>
        <Locked s={s} name={EMBED_PROVIDER_VAR} />
        <ModelInput id="settings-embed-model" label={t("settings.embedModel")} value={form.embedModels[name]}
                    options={name === "ollama" ? s.ollama.models : []} disabled={isLocked(s, modelVar)}
                    placeholder={name === "ollama" ? "bge-m3" : "text-embedding-3-small"}
                    onChange={(v) => setForm({ ...form, embedModels: { ...form.embedModels, [name]: v } })}
                    hint={name === "ollama" ? t("settings.model.ollamaHint", { model: "bge-m3" }) : undefined} />
        <Locked s={s} name={modelVar} />
        <p className="text-xs text-muted-foreground" id="settings-vectors">
          {s.vectors.rows ? t("settings.vectors", { rows: s.vectors.rows, dimension: s.vectors.dimension ?? "?" })
            : t("settings.vectors.none")}
        </p>
        {dimensionWarning && (
          <p className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" id="settings-dimension-warning" role="alert">
            {dimensionWarning}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function TestResults({ result }: { result: SettingsTest }) {
  const t = useT()
  const { chat, embed } = result
  return (
    <div className="grid gap-1 text-xs" id="settings-test-result" role="status">
      <p className={chat.ok ? "text-success" : "break-words text-destructive"} data-test-chat={chat.ok ? "ok" : "error"}>
        {chat.ok
          ? t("settings.test.chatOk", { provider: chat.provider, model: chat.model, time: fmtSeconds(chat.seconds) })
          : t("settings.test.chatFailed", { provider: chat.provider, model: chat.model,
                                            error: chat.error || chat.answer || "?" })}
      </p>
      {!chat.ok && chat.remedy && <p>{t("common.fix")} <Code>{chat.remedy}</Code></p>}
      <p className={embed.ok ? "text-success" : "break-words text-destructive"} data-test-embed={embed.ok ? "ok" : "error"}>
        {embed.ok
          ? t("settings.test.embedOk", { model: embed.model, dimension: embed.dimension, time: fmtSeconds(embed.seconds) })
          : t("settings.test.embedFailed", { model: embed.model, error: embed.error || "?" })}
      </p>
    </div>
  )
}

type Phase =
  | { kind: "idle" }
  | { kind: "confirm" }
  | { kind: "saving" }
  | { kind: "restarting"; id: string; elapsed: number }
  | { kind: "failed"; error: string; log?: string }

export default function SettingsPage() {
  const t = useT()
  const qc = useQueryClient()
  const query = useQuery({ queryKey: keys.settings, queryFn: api.settings })
  const s = query.data
  // null = untouched: the form shows the saved settings (fresh after a restart)
  const [edited, setForm] = useState<SettingsForm | null>(null)
  const form = edited ?? (s ? formFrom(s) : null)
  const [phase, setPhase] = useState<Phase>({ kind: "idle" })
  const [confirmReingest, setConfirmReingest] = useState(false)
  const [dimension, setDimension] = useState<{ new: number; old: number } | null>(null)
  const change = useMemo(() => (s && form ? changeOf(s, form) : {}), [s, form])
  const dirty = hasChanges(change)

  const test = useMutation({ mutationFn: () => api.testSettings(change) })
  const testDims = test.data?.embed
  const mismatch = testDims?.mismatch_with_store && testDims.dimension != null && testDims.stored_dimension != null
    ? { new: testDims.dimension, old: testDims.stored_dimension } : null
  const dims = dimension ?? mismatch
  const dimensionWarning = dims ? t("settings.dimension.warning", { new: dims.new, old: dims.old }) : null

  const finish = (st: RestartState) => {
    if (st.state === "failed") {
      setPhase({ kind: "failed", error: st.error || "?", log: st.log_tail })
      return
    }
    setPhase({ kind: "idle" })
    setForm(null)
    setDimension(null)
    setConfirmReingest(false)
    test.reset()
    qc.invalidateQueries()
    qc.fetchQuery({ queryKey: keys.settings, queryFn: api.settings }).then((fresh) => {
      toast.success(t("settings.restart.done", { chat: fresh.running.chat_provider, model: fresh.running.chat_model }))
    }).catch(() => toast.success(t("settings.restart.doneShort")))
  }

  const restart = async () => {
    const started = await api.restart()
    setPhase({ kind: "restarting", id: started.restart_id, elapsed: 0 })
    try {
      const st = await waitForRestart(started.restart_id, started.previous_pid, {
        onTick: (ms) => setPhase({ kind: "restarting", id: started.restart_id, elapsed: ms / 1000 }),
      })
      finish(st)
    } catch (err) {
      setPhase({ kind: "failed",
                 error: err instanceof RestartTimeout ? t("settings.restart.timeout") : errorMessage(err),
                 log: err instanceof RestartTimeout ? err.last?.log_tail : undefined })
    }
  }

  const save = async () => {
    if (!s) return
    setPhase({ kind: "saving" })
    try {
      const body = { ...change, ...(confirmReingest ? { confirm_reingest: true } : {}) }
      if (hasChanges(change)) {
        await api.saveSettings(body)
        // saved: the typed key leaves the browser's memory, the form shows the new .env
        setForm(null)
        await qc.invalidateQueries({ queryKey: keys.settings })
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "embedding_dimension_changed") {
        setDimension({ new: Number(err.body?.dimension), old: Number(err.body?.stored_dimension) })
        setPhase({ kind: "confirm" })
        return
      }
      setPhase({ kind: "idle" })
      toast.error(t("settings.saveFailed", { error: errorMessage(err) }))
      return
    }
    if (!s.supervised) {
      setPhase({ kind: "idle" })
      toast.message(t("settings.saved.manual"), { description: s.restart_command })
      return
    }
    try {
      await restart()
    } catch (err) {
      setPhase({ kind: "failed", error: errorMessage(err) })
    }
  }

  if (query.error) return <><PageHeader title={t("settings.title")} /><ErrorState error={query.error} onRetry={() => query.refetch()} /></>
  if (!s || !form) return <><PageHeader title={t("settings.title")} /><Skeleton className="h-96" /></>

  const testBusy = test.error instanceof ApiError && test.error.code === "test_in_progress"
  const pending = s.restart_required && !dirty
  const needsReingestConsent = !!dimension && !confirmReingest
  const busy = phase.kind === "saving" || phase.kind === "restarting"
  return (
    <>
      <PageHeader title={t("settings.title")} description={t("settings.description")} />
      <div className="grid max-w-3xl gap-6" id="settings-page" data-dirty={dirty}>
        <p className="text-sm text-muted-foreground" id="settings-running">
          {t("settings.running", { chat: s.running.chat_provider, model: s.running.chat_model || "-",
                                   embed: s.running.embed_provider, embedModel: s.running.embed_model || "-" })}
        </p>
        {pending && (
          <div className="grid gap-2 rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm" id="settings-pending" role="status">
            <p className="font-medium">{t("settings.pending.title")}</p>
            <p className="text-xs">{t("settings.pending.body", { chat: s.chat.provider, model: s.chat.model || "-" })}</p>
            {s.supervised ? (
              <div><Button size="sm" id="btn-restart-now" disabled={busy || s.restart_in_progress}
                           onClick={() => restart().catch((err) => setPhase({ kind: "failed", error: errorMessage(err) }))}>
                <RotateCcw aria-hidden /> {t("settings.pending.restart")}
              </Button></div>
            ) : <p className="text-xs">{t("settings.restart.manual")} <Code>{s.restart_command}</Code></p>}
          </div>
        )}
        {!s.supervised && (
          <p className="text-xs text-muted-foreground" id="settings-unsupervised">
            {tx("settings.unsupervised", { up: <Code key="up">uv run autoontology up</Code> })}
          </p>
        )}
        <Card className="gap-4" id="settings-chat">
          <CardHeader>
            <CardTitle className="text-base">{t("settings.chat.title")}</CardTitle>
            <CardDescription>{t("settings.chat.hint")}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <ProviderChoice s={s} form={form} setForm={setForm} />
            <Locked s={s} name={PROVIDER_VAR} />
            <ChatDetails s={s} form={form} setForm={setForm} />
          </CardContent>
        </Card>
        {usesOpenai(form) && <OpenaiEndpoint s={s} form={form} setForm={setForm} />}
        <Embeddings s={s} form={form} setForm={setForm} dimensionWarning={dimensionWarning} />
        <div className="grid gap-3 rounded-xl border p-4" id="settings-actions">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" id="btn-settings-test" disabled={test.isPending || busy} onClick={() => test.mutate()}>
              {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Zap aria-hidden />}
              {test.isPending ? t("settings.testing") : t("settings.test")}
            </Button>
            <Button id="btn-settings-save" disabled={!dirty || busy || s.restart_in_progress}
                    onClick={() => setPhase({ kind: "confirm" })}>
              <Save aria-hidden /> {dirty ? t("settings.save") : t("settings.noChanges")}
            </Button>
            {dirty && (
              <Button variant="ghost" id="btn-settings-reset" disabled={busy}
                      onClick={() => { setForm(null); setDimension(null); test.reset() }}>
                {t("settings.reset")}
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{t("settings.test.hint")}</p>
          {test.isError && (
            <p className="text-xs break-words text-destructive" role="status" data-test-result={testBusy ? "busy" : "error"}>
              {testBusy ? t("settings.test.busy") : t("settings.test.failed", { error: errorMessage(test.error) })}
            </p>
          )}
          {test.data && <TestResults result={test.data} />}
        </div>
      </div>
      <AlertDialog open={phase.kind !== "idle"} onOpenChange={(open) => { if (!open && !busy) setPhase({ kind: "idle" }) }}>
        <AlertDialogContent id="settings-dialog" data-phase={phase.kind}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {phase.kind === "failed" ? t("settings.restart.failedTitle")
                : phase.kind === "restarting" ? t("settings.restarting.title") : t("settings.confirm.title")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {phase.kind === "restarting" ? t("settings.restarting.hint")
                : phase.kind === "failed" ? t("settings.restart.failed", { error: phase.error })
                : s.supervised ? t("settings.confirm.body") : t("settings.confirm.manual")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {phase.kind === "restarting" && (
            <p className="flex items-center gap-2 text-sm" role="status" id="settings-restarting">
              <Loader2 className="animate-spin" aria-hidden /> {t("settings.restarting", { time: fmtSeconds(Math.round(phase.elapsed)) })}
            </p>
          )}
          {phase.kind === "failed" && (
            <div className="grid gap-2 text-xs">
              {phase.log && <pre className="max-h-48 overflow-auto rounded bg-muted p-2 whitespace-pre-wrap">{phase.log}</pre>}
              <p>{t("settings.restart.manual")} <Code>{s.restart_command}</Code></p>
            </div>
          )}
          {(phase.kind === "confirm" || phase.kind === "saving") && dimension && (
            <div className="grid gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
              <p role="alert">{dimensionWarning}</p>
              <div className="flex items-center gap-2">
                <Switch id="confirm-reingest" checked={confirmReingest} onCheckedChange={setConfirmReingest} />
                <Label htmlFor="confirm-reingest" className="text-xs font-normal">{t("settings.dimension.confirm")}</Label>
              </div>
            </div>
          )}
          <AlertDialogFooter>
            {phase.kind === "failed" ? (
              <AlertDialogCancel>{t("common.close")}</AlertDialogCancel>
            ) : (
              <>
                <AlertDialogCancel disabled={busy}>{t("common.cancel")}</AlertDialogCancel>
                <Button id="btn-settings-confirm" disabled={busy || needsReingestConsent} onClick={() => save()}>
                  {busy && <Loader2 className="animate-spin" aria-hidden />}
                  {phase.kind === "saving" ? t("settings.saving")
                    : s.supervised ? t("settings.confirm.go") : t("settings.confirm.saveOnly")}
                </Button>
              </>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

