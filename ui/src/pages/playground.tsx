/* Playground: question the unified ontology. Chat (RAG with citations) and SPARQL run on the
   published snapshot; SQL goes through the upstream NL->SQL agent of one structured source.
   Lazy-loaded from App. */

import { Bot, Database, FlaskConical, Hammer, MessagesSquare, Sparkles } from "lucide-react"
import { useCallback } from "react"
import { Link, useSearchParams } from "react-router"

import { ConceptSheet } from "@/components/concept-sheet"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ScopeNotice } from "@/components/scope-notice"
import { ChatTab } from "@/components/playground/chat-tab"
import { ConceptOpener } from "@/components/playground/concept-opener"
import { SparqlTab } from "@/components/playground/sparql-tab"
import { UsageHistory } from "@/components/playground/usage-traces"
import { SqlTab } from "@/components/playground/sql-tab"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOntology, useStatus } from "@/hooks/queries"
import { t, useT } from "@/i18n"
import { isNoSnapshot } from "@/lib/api"
import { SHARED_DEMO } from "@/lib/demo"
import { useCanUse } from "@/lib/identity"

const TABS = [
  { key: "chat", label: () => t("playground.tab.chat"), icon: MessagesSquare },
  { key: "sql", label: () => "SQL", icon: Database },
  { key: "sparql", label: () => "SPARQL", icon: Sparkles },
  { key: "usage", label: () => t("usage.history"), icon: MessagesSquare },
] as const
type Tab = (typeof TABS)[number]["key"]
const AUTO = "__auto"

function ProviderBadge() {
  const { data } = useStatus()
  const p = data?.providers
  if (!p) return null
  return (
    <Link to="/status" title={t("playground.providerTitle")} id="playground-provider">
      <Badge variant="outline" className="max-w-full gap-1.5 font-normal">
        <Bot aria-hidden /> <span className="truncate">{p.chat_provider} · {p.chat_model}</span>
        <span className="hidden text-muted-foreground sm:inline">{t("playground.embeddings", { model: p.embed_model })}</span>
      </Badge>
    </Link>
  )
}

export default function PlaygroundPage() {
  const t = useT()
  const canReadResults = useCanUse("results.read")
  const canReadSettings = useCanUse("settings.read")
  const tabs = TABS.filter(tab => tab.key !== "usage" || canReadResults)
  const [params, setParams] = useSearchParams()
  const ontology = useOntology()
  const structured = (ontology.data?.sources ?? []).filter((s) => s.kind !== "documents" && s.included !== false)
  const sqlSource = structured.find((s) => s.name === params.get("source"))?.name ?? null
  const tab = (tabs.some((x) => x.key === params.get("tab")) ? params.get("tab") : "chat") as Tab
  const conceptId = params.get("concept")
  const set = useCallback((patch: Record<string, string | null>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v)
        else next.delete(k)
      }
      return next
    }, { replace: true })
  }, [setParams])
  const openConcept = useCallback((id: string) => set({ concept: id }), [set])

  return (
    <ConceptOpener.Provider value={openConcept}>
      <PageHeader title={t("playground.title")}
                  description={t("playground.description")}
                  actions={SHARED_DEMO || !canReadSettings ? undefined : <ProviderBadge />} />
      <ScopeNotice access={ontology.data?.access} />
      {isNoSnapshot(ontology.error) ? (
        <EmptyState icon={FlaskConical} title={t("ontology.empty.title")}
                    hint={t("playground.empty.hint")}
                    action={<Button asChild><Link to="/build"><Hammer aria-hidden /> {t("ontology.goBuild")}</Link></Button>} />
      ) : ontology.error ? <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />
        : !ontology.data ? <Skeleton className="h-64" />
        : (
          <Tabs value={tab} onValueChange={(v) => set({ tab: v })} id="playground-tabs">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <TabsList className="h-auto flex-wrap justify-start">
                {tabs.map((tab) => (
                  <TabsTrigger key={tab.key} value={tab.key} data-tab={tab.key} className="flex-none">
                    <tab.icon aria-hidden /> {tab.label()}
                  </TabsTrigger>
                ))}
              </TabsList>
              <span className="text-xs text-muted-foreground" id="playground-revision">
                {t("playground.revision", { n: ontology.data.revision, workspace: ontology.data.workspace })}
              </span>
            </div>
            {/* each tab keeps its state while another one is shown */}
            <TabsContent value="chat" forceMount hidden={tab !== "chat"}><ChatTab /></TabsContent>
            <TabsContent value="sql" forceMount hidden={tab !== "sql"}>
              {structured.length > 1 && (
                <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">{t("playground.structuredSource")}</span>
                  <Select value={sqlSource ?? AUTO} onValueChange={(v) => set({ source: v === AUTO ? null : v })}>
                    <SelectTrigger className="w-full sm:w-56" aria-label={t("playground.structuredSource")} id="playground-source">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={AUTO}>{t("playground.defaultSource")}</SelectItem>
                      {structured.map((s) => <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <SqlTab key={sqlSource ?? AUTO} source={sqlSource} />
            </TabsContent>
            <TabsContent value="sparql" forceMount hidden={tab !== "sparql"}><SparqlTab /></TabsContent>
            {canReadResults && <TabsContent value="usage">{tab === "usage" && <UsageHistory />}</TabsContent>}
          </Tabs>
        )}
      <ConceptSheet conceptId={conceptId} onClose={() => set({ concept: null })} onOpen={openConcept} />
    </ConceptOpener.Provider>
  )
}
