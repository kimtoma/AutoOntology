import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CheckCircle2, FlaskConical, Info, Loader2, Lock, RotateCw, Save, TriangleAlert,
} from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { CheckChip } from "@/components/status-chip"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { keys } from "@/hooks/queries"
import {
  api, ApiError, errorMessage, type ApplyResult, type ProviderRow, type ProvidersOverview,
} from "@/lib/api"
import { fmtTime, ORIGIN_KO, PROVIDER_KO } from "@/lib/labels"
import { cn } from "@/lib/utils"

const DEFAULT_MODEL = "__default"
/** Providers whose chat model the selection can set (ollama uses its alias). */
const MODEL_SELECTABLE = new Set(["claude", "codex", "nvidia"])

function OriginBadge({ origin }: { origin?: string }) {
  if (!origin) return null
  return (
    <Badge variant="outline" className="gap-1 font-normal" title="이 값이 정해진 곳">
      {origin === "shell" && <Lock aria-hidden />}
      {ORIGIN_KO[origin] ?? origin}
    </Badge>
  )
}

function useSaveProviders() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.saveProviders,
    onSuccess: (data) => {
      qc.setQueryData(keys.providers, (old: ProvidersOverview | undefined) => ({ ...old, ...data }))
      toast.success("저장했습니다", {
        description: data.restart_required ? "적용(백엔드 재시작)을 눌러야 반영됩니다" : undefined,
      })
    },
    onError: (e) => toast.error("저장 실패", { description: errorMessage(e) }),
  })
}

function ProviderCard({ row, data }: { row: ProviderRow; data: ProvidersOverview }) {
  const sel = data.selection
  const selected = sel.chat_provider === row.name
  const shell = sel.origin.chat_provider === "shell" || sel.origin.chat_model === "shell"
  // keyed by selection + model in the parent: a saved change re-initialises this
  const [model, setModel] = useState(selected && MODEL_SELECTABLE.has(row.name) ? row.model : "")
  const save = useSaveProviders()
  const qc = useQueryClient()
  const test = useMutation({
    mutationFn: () => api.testProvider(row.name),
    onSuccess: (res) => {
      const r = res.results[row.name]
      qc.invalidateQueries({ queryKey: keys.providers })
      if (!r || r.ok === null) {
        toast(`${PROVIDER_KO[row.name]}: /v1 경유 테스트 대상이 아닙니다`, { description: r?.note })
        return
      }
      const call = r.router
      const desc = [r.model, call?.seconds != null ? `${call.seconds.toFixed(1)}초` : "",
        call?.answer ? `응답 "${call.answer}"` : "", call?.error ?? r.chatopenai?.error ?? ""]
        .filter(Boolean).join(" · ")
      if (r.ok) toast.success(`${PROVIDER_KO[row.name]} 테스트 성공`, { description: desc })
      else toast.error(`${PROVIDER_KO[row.name]} 테스트 실패`, { description: desc })
    },
    onError: (e) => toast.error("테스트 실패", { description: errorMessage(e) }),
  })
  const usage = data.usage?.[row.name]
  const models = row.models.filter((m) => !/embed|bge/i.test(m))
  const ready = row.available && row.logged_in

  return (
    <Card data-provider={row.name} className={cn("gap-4", selected && "ring-2 ring-primary/40")}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {PROVIDER_KO[row.name] ?? row.name}
          {selected && <Badge><CheckCircle2 aria-hidden /> 채팅에 사용 중</Badge>}
        </CardTitle>
        <CardDescription>{row.role === "chat+embed" ? "채팅 + 임베딩" : "채팅 전용"}</CardDescription>
        <CardAction>
          <CheckChip status={ready ? "ok" : row.available ? "warn" : "error"}
                     label={ready ? (row.name === "ollama" ? "연결됨" : row.name === "nvidia" ? "키 있음" : "로그인됨")
                       : row.available ? (row.name === "nvidia" ? "키 없음" : "로그인 필요") : "사용 불가"} />
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <p className="text-xs break-words text-muted-foreground">{row.detail}</p>
        {row.remedy && <code className="rounded bg-muted px-1.5 py-1 text-xs break-all">{row.remedy}</code>}
        <div className="grid gap-1.5">
          <Label htmlFor={`model-${row.name}`}>모델</Label>
          {row.name === "ollama" ? (
            <Input id={`model-${row.name}`} value={row.model} readOnly disabled
                   title="AUTOONTOLOGY_OLLAMA_CHAT_MODEL (.env)로 바꿉니다" />
          ) : models.length ? (
            <Select value={model || DEFAULT_MODEL} onValueChange={(v) => setModel(v === DEFAULT_MODEL ? "" : v)}
                    disabled={shell}>
              <SelectTrigger id={`model-${row.name}`} className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_MODEL}>기본값 ({row.model})</SelectItem>
                {models.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              </SelectContent>
            </Select>
          ) : (
            <Input id={`model-${row.name}`} value={model} placeholder={row.model || "기본값"}
                   onChange={(e) => setModel(e.target.value)} disabled={shell} autoComplete="off" />
          )}
        </div>
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <div>
            <dt className="text-muted-foreground">마지막 테스트</dt>
            <dd>{row.last_test ? `${row.last_test.ok ? "성공" : "실패"} · ${fmtTime(row.last_test.at)}` : "-"}</dd>
          </div>
          {usage && (
            <div>
              <dt className="text-muted-foreground">이번 실행 사용량</dt>
              <dd>
                호출 {usage.calls}{usage.errors ? ` (오류 ${usage.errors})` : ""} · 토큰
                {" "}{(usage.prompt_tokens + usage.completion_tokens).toLocaleString("ko-KR")}
                {usage.cost_usd ? ` · $${usage.cost_usd.toFixed(3)}` : ""}
              </dd>
            </div>
          )}
        </dl>
      </CardContent>
      <CardFooter className="mt-auto flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => test.mutate()} disabled={test.isPending}
                aria-label={`${PROVIDER_KO[row.name]} 테스트`}>
          {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <FlaskConical aria-hidden />}
          {test.isPending ? "테스트 중…" : "테스트"}
        </Button>
        <Button size="sm" disabled={shell || save.isPending}
                title={shell ? "셸 환경변수로 정해진 값은 UI에서 바꿀 수 없습니다" : undefined}
                onClick={() => save.mutate(row.name === "ollama"
                  ? { chat_provider: row.name }
                  : { chat_provider: row.name, chat_model: model })}
                aria-label={`${PROVIDER_KO[row.name]} 저장`}>
          {save.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Save aria-hidden />} 저장
        </Button>
      </CardFooter>
    </Card>
  )
}

/** Indeterminate-looking progress while the backend restarts (~3 s): eases towards 95 %. */
function useApplyProgress(active: boolean) {
  const [ticks, setTicks] = useState(0)
  const [wasActive, setWasActive] = useState(active)
  if (active !== wasActive) { setWasActive(active); setTicks(0) }
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setTicks((n) => n + 1), 200)
    return () => clearInterval(t)
  }, [active])
  return active ? Math.round(95 * (1 - Math.exp(-(ticks + 1) / 12))) : 0
}

function SelectionCard({ data }: { data: ProvidersOverview }) {
  const qc = useQueryClient()
  const sel = data.selection
  const save = useSaveProviders()
  const [forceAsk, setForceAsk] = useState<string | null>(null)
  const [result, setResult] = useState<ApplyResult | null>(null)
  const apply = useMutation({
    mutationFn: (force: boolean) => api.applyProviders(force),
    onSuccess: (r) => {
      setResult(r)
      qc.invalidateQueries({ queryKey: keys.providers })
      qc.invalidateQueries({ queryKey: keys.status })
      if (r.outcome === "applied") {
        toast.success("적용했습니다", { description: `백엔드 ${r.backend_health.status === "ok" ? "정상" : r.backend_health.detail}` })
      } else {
        toast.error(r.outcome === "rolled_back" ? "적용 실패: 이전 설정으로 되돌렸습니다" : "적용 실패: 백엔드가 내려갔습니다",
                    { description: r.remedy || r.detail })
      }
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "runs_active") setForceAsk(e.message)
      else toast.error("적용 실패", { description: errorMessage(e) })
    },
  })
  const progress = useApplyProgress(apply.isPending)
  const embedShell = sel.origin.embed_provider === "shell"

  return (
    <Card id="provider-selection">
      <CardHeader>
        <CardTitle>현재 선택</CardTitle>
        <CardDescription>저장한 값은 백엔드를 다시 시작해야 실제 실행에 쓰입니다.</CardDescription>
        <CardAction>
          {data.restart_required
            ? <Badge variant="outline" className="border-warning/50 text-amber-700 dark:text-amber-300"><TriangleAlert aria-hidden /> 재시작 필요</Badge>
            : <Badge variant="outline" className="border-success/40 text-success"><CheckCircle2 aria-hidden /> 적용됨</Badge>}
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">채팅 프로바이더</dt>
            <dd className="flex flex-wrap items-center gap-1.5 font-medium">
              {sel.chat_provider} <OriginBadge origin={sel.origin.chat_provider} />
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">채팅 모델</dt>
            <dd className="flex flex-wrap items-center gap-1.5 font-medium break-all">
              {sel.chat_model || "-"} <OriginBadge origin={sel.origin.chat_model} />
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">실행 중인 백엔드</dt>
            <dd className="font-medium break-all">{data.applied.chat_provider} · {data.applied.chat_model}</dd>
          </div>
        </dl>
        <div className="grid gap-1.5 sm:max-w-xs">
          <Label htmlFor="embed-provider">임베딩 프로바이더</Label>
          <div className="flex items-center gap-2">
            <Select value={sel.embed_provider} disabled={embedShell || save.isPending}
                    onValueChange={(v) => save.mutate({ embed_provider: v })}>
              <SelectTrigger id="embed-provider" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {data.choices.embed_provider.map((p) => <SelectItem key={p} value={p}>{PROVIDER_KO[p] ?? p}</SelectItem>)}
              </SelectContent>
            </Select>
            <OriginBadge origin={sel.origin.embed_provider} />
          </div>
        </div>
        {apply.isPending && (
          <div className="grid gap-1.5" role="status" aria-live="polite">
            <Progress value={progress} aria-label="백엔드 재시작 진행" />
            <p className="text-xs text-muted-foreground">백엔드를 다시 시작하고 상태를 확인하는 중…</p>
          </div>
        )}
        {result && !apply.isPending && (
          <div role="status" className={cn("rounded-md border px-3 py-2 text-sm",
            result.outcome === "applied" ? "border-success/30 bg-success/5" : "border-destructive/30 bg-destructive/5")}>
            <p className="font-medium">
              {result.outcome === "applied" ? "적용됨" : result.outcome === "rolled_back" ? "되돌림 (이전 설정 유지)" : "백엔드 중지됨"}
              {" · "}백엔드 {result.backend_health.status === "ok" ? "정상" : `오류 (${result.backend_health.detail})`}
            </p>
            {result.detail && <p className="mt-1 text-xs break-words text-muted-foreground">{result.detail}</p>}
            {result.remedy && <code className="mt-1 block text-xs break-all">{result.remedy}</code>}
          </div>
        )}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button onClick={() => apply.mutate(false)} disabled={apply.isPending} id="btn-apply"
                variant={data.restart_required ? "default" : "outline"}>
          {apply.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCw aria-hidden />}
          적용(백엔드 재시작)
        </Button>
      </CardFooter>
      <AlertDialog open={!!forceAsk} onOpenChange={(o) => { if (!o) setForceAsk(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>실행 중인 작업이 있습니다</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              {forceAsk} 지금 재시작하면 그 작업은 백엔드를 잃고 실패할 수 있습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90"
                               onClick={() => apply.mutate(true)}>그래도 재시작</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

export default function SettingsPage() {
  const q = useQuery({ queryKey: keys.providers, queryFn: api.providers })
  return (
    <>
      <PageHeader title="설정" description="모델 프로바이더: 구독(Claude·Codex) 또는 로컬 Ollama, NVIDIA API 중 무엇으로 채팅할지 고릅니다." />
      <h2 className="mb-3 text-lg font-semibold">모델 프로바이더</h2>
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} />
        : q.isPending ? (
          <div className="grid gap-4"><Skeleton className="h-48" />
            <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div>
          </div>
        ) : (
          <div className="grid gap-6">
            <SelectionCard data={q.data} />
            <div className="flex gap-2 rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" id="precedence-note">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
              <p>
                우선순위: <b>셸 환경변수 &gt; settings.json(이 화면) &gt; .env &gt; 기본값</b>.
                셸에서 정한 값(<Lock className="inline size-3" aria-label="잠김" />)은 이 화면에서 바꿀 수 없습니다.
                {" "}저장 위치: <code>&lt;home&gt;/settings.json</code>
              </p>
            </div>
            <div id="provider-cards" className="grid gap-4 md:grid-cols-2">
              {q.data.providers.map((row) => (
                <ProviderCard key={`${row.name}:${q.data.selection.chat_provider}:${row.model}`}
                              row={row} data={q.data} />
              ))}
            </div>
          </div>
        )}
    </>
  )
}
