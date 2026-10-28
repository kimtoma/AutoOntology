/* 플레이그라운드: validate and test the built ontology conversationally. Four tabs scoped to
   one source (a documents source brings its aligned structured source along). Lazy-loaded
   from App. */

import { useQuery } from "@tanstack/react-query"
import { Bot, Database, FlaskConical, ListChecks, MessagesSquare, Sparkles } from "lucide-react"
import { Link, useSearchParams } from "react-router"

import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ChatTab } from "@/components/playground/chat-tab"
import { SparqlTab } from "@/components/playground/sparql-tab"
import { SqlTab } from "@/components/playground/sql-tab"
import { TestsTab } from "@/components/playground/tests-tab"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { keys, useSources } from "@/hooks/queries"
import { api } from "@/lib/api"

const TABS = [
  { key: "chat", label: "온톨로지 대화", icon: MessagesSquare },
  { key: "sql", label: "데이터 질의", icon: Database },
  { key: "sparql", label: "SPARQL", icon: Sparkles },
  { key: "tests", label: "검증 테스트", icon: ListChecks },
] as const
type Tab = (typeof TABS)[number]["key"]

function ProviderBadge() {
  const providers = useQuery({ queryKey: keys.providers, queryFn: api.providers, staleTime: 30_000 })
  const sel = providers.data?.selection
  if (!sel) return null
  return (
    <Link to="/settings" title="채팅 공급자 바꾸기 (설정)" id="playground-provider">
      <Badge variant="outline" className="gap-1.5 font-normal">
        <Bot aria-hidden /> {sel.chat_provider} · {sel.chat_model}
        <span className="text-muted-foreground">/ 임베딩 {sel.embed_provider}</span>
      </Badge>
    </Link>
  )
}

export default function PlaygroundPage() {
  const [params, setParams] = useSearchParams()
  const sources = useSources()
  const usable = (sources.data ?? []).filter((s) => s.kind === "documents" || s.built)
  // default: the documents source with the most concepts (it carries its aligned Terms)
  const preferred = [...usable].sort((a, b) =>
    Number(b.kind === "documents") - Number(a.kind === "documents") || (b.concepts ?? 0) - (a.concepts ?? 0))[0]
  const source = usable.find((s) => s.name === params.get("source"))?.name ?? preferred?.name ?? ""
  const tab = (TABS.some((t) => t.key === params.get("tab")) ? params.get("tab") : "chat") as Tab
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) next.set(k, v)
    setParams(next, { replace: true })
  }

  return (
    <>
      <PageHeader title="플레이그라운드"
                  description="만든 온톨로지에 질문하고, 데이터 질의·SPARQL·역량 질문 테스트로 검증합니다."
                  actions={
                    <div className="flex flex-wrap items-center gap-2">
                      <ProviderBadge />
                      {usable.length > 0 && (
                        <Select value={source} onValueChange={(v) => set({ source: v })}>
                          <SelectTrigger className="w-full sm:w-56" aria-label="소스" id="playground-source">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {usable.map((s) => (
                              <SelectItem key={s.name} value={s.name}>
                                {s.name} <span className="text-muted-foreground">({s.kind === "documents" ? "문서" : "정형"})</span>
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>
                  } />
      {sources.error ? <ErrorState error={sources.error} onRetry={() => sources.refetch()} />
        : sources.isPending ? <Skeleton className="h-64" />
        : !source ? (
          <EmptyState icon={FlaskConical} title="검증할 온톨로지가 없습니다"
                      hint={<>먼저 <Link to="/runs" className="underline underline-offset-4">실행</Link>에서 소스를 빌드하거나 문서를 추출하세요.</>} />
        ) : (
          <Tabs value={tab} onValueChange={(v) => set({ tab: v })} id="playground-tabs">
            <TabsList className="mb-4 h-auto w-full flex-wrap justify-start sm:w-auto">
              {TABS.map((t) => (
                <TabsTrigger key={t.key} value={t.key} data-tab={t.key} className="flex-none">
                  <t.icon aria-hidden /> {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
            {/* each tab keeps its state while another one is shown */}
            <TabsContent value="chat" forceMount hidden={tab !== "chat"}><ChatTab key={source} source={source} /></TabsContent>
            <TabsContent value="sql" forceMount hidden={tab !== "sql"}><SqlTab key={source} source={source} /></TabsContent>
            <TabsContent value="sparql" forceMount hidden={tab !== "sparql"}><SparqlTab key={source} source={source} /></TabsContent>
            <TabsContent value="tests" forceMount hidden={tab !== "tests"}><TestsTab key={source} source={source} /></TabsContent>
          </Tabs>
        )}
    </>
  )
}
