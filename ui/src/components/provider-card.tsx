import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Settings, Zap } from "lucide-react"
import { Link } from "react-router"

import { CheckChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import { keys } from "@/hooks/queries"
import { api, ApiError, errorMessage, type ProviderTest, type ServiceStatus } from "@/lib/api"
import { loginCommand, RESTART_COMMAND } from "@/lib/checklist"
import { fmtSeconds, fmtTime } from "@/lib/labels"
import { cn } from "@/lib/utils"
import { useT } from "@/i18n"

/** Plain names on the Home card (the Status table keeps the technical ones). */
const SERVICE_NAME = { backend: "provider.service.backend", postgres: "provider.service.postgres" } as const

export function Code({ children, className }: { children: string; className?: string }) {
  return (
    <code className={cn("inline-block max-w-full rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all",
                        className)}>{children}</code>
  )
}

function TestResult({ result, fresh }: { result: ProviderTest; fresh: boolean }) {
  const t = useT()
  const when = !fresh && result.at ? ` · ${fmtTime(result.at)}` : ""
  if (result.ok) {
    return (
      <p className="text-xs text-success" data-test-result="ok" role="status">
        {result.seconds != null
          ? t(fresh ? "provider.test.passedIn" : "provider.test.lastPassedIn", { time: fmtSeconds(result.seconds) })
          : t(fresh ? "provider.test.passed" : "provider.test.lastPassed")}{when}
      </p>
    )
  }
  return (
    <div className="grid gap-1" data-test-result="error" role="status">
      <p className="text-xs break-words text-destructive">
        {t(fresh ? "provider.test.error" : "provider.test.lastError", {
          error: result.error || t("provider.test.answer", { answer: JSON.stringify(result.answer ?? "") }),
        })}{when}
      </p>
      {result.remedy && <p className="text-xs">{t("common.fix")} <Code>{result.remedy}</Code></p>}
    </div>
  )
}

/** The chat provider (login state, model, remedy, a Test button) and the embedding model.
    *services* also lists the backend and Postgres checks (the Home page has no checks table). */
export function ProviderCard({ status, services = false }: { status: ServiceStatus; services?: boolean }) {
  const t = useT()
  const qc = useQueryClient()
  const test = useMutation({
    mutationFn: api.testProvider,
    onSettled: () => qc.invalidateQueries({ queryKey: keys.status }),
  })
  const p = status.providers
  const chat = p.chat
  const ready = chat ? chat.available && chat.logged_in : true
  const state = t(!chat ? "provider.state.configured" : !chat.available ? "provider.state.unavailable"
    : !chat.logged_in ? "provider.state.notLoggedIn" : "provider.state.ready")
  const login = ready ? null : loginCommand(status)
  const result = test.data ?? null
  // 409 test_in_progress: another tab or the CLI is testing; the status poll brings its result
  const busy = test.error instanceof ApiError && test.error.code === "test_in_progress"
  return (
    <div className="grid min-w-0 gap-3 text-sm" id="provider-card" data-provider={chat?.name ?? p.chat_provider}
         data-ready={ready}>
      {services && (
        <ul className="flex flex-wrap gap-1.5" aria-label={t("provider.services")}>
          {(["backend", "postgres"] as const).map((k) => {
            const c = status.checks?.[k]
            return (
              <li key={k} data-service={k} data-status={c?.status ?? "error"}>
                <CheckChip status={c?.status ?? "error"} label={t(c?.status === "ok" ? "provider.service.ok" : "provider.service.down", { name: t(SERVICE_NAME[k]) })} />
              </li>
            )
          })}
        </ul>
      )}
      {services && (["backend", "postgres"] as const).map((k) => {
        const c = status.checks?.[k]
        return c && c.status !== "ok" && c.remedy
          ? <p key={k} className="text-xs">{t(SERVICE_NAME[k])}: <Code>{c.remedy}</Code></p> : null
      })}
      <dl className="grid min-w-0 gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">{t("provider.chat")}</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <span className="font-medium break-all" id="provider-chat">{chat?.name ?? p.chat_provider} · {chat?.model ?? p.chat_model}</span>
            <CheckChip status={ready ? "ok" : "error"} label={state} />
          </dd>
          {chat?.detail && <dd className="mt-0.5 text-xs break-words text-muted-foreground">{chat.detail}</dd>}
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">{t("provider.embed")}</dt>
          <dd className="font-medium break-all" id="provider-embed">
            {p.embed?.name ?? p.embed_provider} · {p.embed?.model ?? p.embed_model}
          </dd>
        </div>
      </dl>
      {!ready && (
        <div className="grid gap-1 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs" id="provider-fix">
          {login && <p>{login.label}: <Code>{login.command}</Code></p>}
          {chat?.remedy && !login?.command.includes(chat.remedy) && <p>{t("provider.or")} <Code>{chat.remedy}</Code></p>}
          <p>{t("provider.restart")} <Code>{RESTART_COMMAND}</Code></p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" id="btn-provider-test" disabled={test.isPending}
                onClick={() => test.mutate()}>
          {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Zap aria-hidden />}
          {test.isPending ? t("provider.testing") : t("provider.test")}
        </Button>
        <span className="text-xs text-muted-foreground">{t("provider.test.hint")}</span>
        <Button size="sm" variant="link" asChild className="h-auto px-0">
          <Link to="/settings" id="link-provider-settings"><Settings aria-hidden /> {t("settings.link")}</Link>
        </Button>
      </div>
      {test.isError && (busy ? (
        <p className="text-xs text-muted-foreground" role="status" data-test-result="busy">
          {t("provider.test.busy")}
        </p>
      ) : (
        <p className="text-xs break-words text-destructive" role="status" data-test-result="error">
          {t("provider.test.failed", { error: errorMessage(test.error) })}
        </p>
      ))}
      {result ? <TestResult result={result} fresh />
        : (!test.isError || busy) && chat?.last_test ? <TestResult result={chat.last_test} fresh={false} /> : null}
    </div>
  )
}
