import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Zap } from "lucide-react"

import { CheckChip } from "@/components/status-chip"
import { Button } from "@/components/ui/button"
import { keys } from "@/hooks/queries"
import { api, errorMessage, type ProviderTest, type ServiceStatus } from "@/lib/api"
import { loginCommand, RESTART_COMMAND } from "@/lib/checklist"
import { CHECK_NAME, fmtSeconds, fmtTime } from "@/lib/labels"
import { cn } from "@/lib/utils"

export function Code({ children, className }: { children: string; className?: string }) {
  return (
    <code className={cn("inline-block max-w-full rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all",
                        className)}>{children}</code>
  )
}

function TestResult({ result, fresh }: { result: ProviderTest; fresh: boolean }) {
  const when = !fresh && result.at ? ` · ${fmtTime(result.at)}` : ""
  if (result.ok) {
    return (
      <p className="text-xs text-success" data-test-result="ok" role="status">
        {fresh ? "Test passed" : "Last test passed"}: the model answered
        {result.seconds != null ? ` in ${fmtSeconds(result.seconds)}` : ""}{when}
      </p>
    )
  }
  return (
    <div className="grid gap-1" data-test-result="error" role="status">
      <p className="text-xs break-words text-destructive">
        {fresh ? "Test failed" : "Last test failed"}: {result.error || `answer ${JSON.stringify(result.answer ?? "")}`}{when}
      </p>
      {result.remedy && <p className="text-xs">Fix: <Code>{result.remedy}</Code></p>}
    </div>
  )
}

/** The chat provider (login state, model, remedy, a Test button) and the embedding model.
    *services* also lists the backend and Postgres checks (the Home page has no checks table). */
export function ProviderCard({ status, services = false }: { status: ServiceStatus; services?: boolean }) {
  const qc = useQueryClient()
  const test = useMutation({
    mutationFn: api.testProvider,
    onSettled: () => qc.invalidateQueries({ queryKey: keys.status }),
  })
  const p = status.providers
  const chat = p.chat
  const ready = chat ? chat.available && chat.logged_in : true
  const state = !chat ? "Configured" : !chat.available ? "Unavailable" : !chat.logged_in ? "Not logged in" : "Ready"
  const login = ready ? null : loginCommand(status)
  const result = test.data ?? null
  return (
    <div className="grid min-w-0 gap-3 text-sm" id="provider-card" data-provider={chat?.name ?? p.chat_provider}
         data-ready={ready}>
      {services && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Services">
          {(["backend", "postgres"] as const).map((k) => {
            const c = status.checks?.[k]
            return (
              <li key={k} data-service={k} data-status={c?.status ?? "error"}>
                <CheckChip status={c?.status ?? "error"} label={`${CHECK_NAME[k]} ${c?.status === "ok" ? "OK" : "down"}`} />
              </li>
            )
          })}
        </ul>
      )}
      {services && (["backend", "postgres"] as const).map((k) => {
        const c = status.checks?.[k]
        return c && c.status !== "ok" && c.remedy
          ? <p key={k} className="text-xs">{CHECK_NAME[k]}: <Code>{c.remedy}</Code></p> : null
      })}
      <dl className="grid min-w-0 gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Chat model (reads and writes the concepts)</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <span className="font-medium break-all" id="provider-chat">{chat?.name ?? p.chat_provider} · {chat?.model ?? p.chat_model}</span>
            <CheckChip status={ready ? "ok" : "error"} label={state} />
          </dd>
          {chat?.detail && <dd className="mt-0.5 text-xs break-words text-muted-foreground">{chat.detail}</dd>}
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">Embedding model (finds similar concepts)</dt>
          <dd className="font-medium break-all" id="provider-embed">
            {p.embed?.name ?? p.embed_provider} · {p.embed?.model ?? p.embed_model}
          </dd>
        </div>
      </dl>
      {!ready && (
        <div className="grid gap-1 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs" id="provider-fix">
          {login && <p>{login.label}: <Code>{login.command}</Code></p>}
          {chat?.remedy && !login?.command.includes(chat.remedy) && <p>Or: <Code>{chat.remedy}</Code></p>}
          <p>then restart: <Code>{RESTART_COMMAND}</Code></p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" id="btn-provider-test" disabled={test.isPending}
                onClick={() => test.mutate()}>
          {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Zap aria-hidden />}
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        <span className="text-xs text-muted-foreground">Sends one short question to the chat model.</span>
      </div>
      {test.isError && (
        <p className="text-xs break-words text-destructive" role="status" data-test-result="error">
          Test failed: {errorMessage(test.error)}
        </p>
      )}
      {result ? <TestResult result={result} fresh />
        : !test.isError && chat?.last_test ? <TestResult result={chat.last_test} fresh={false} /> : null}
    </div>
  )
}
