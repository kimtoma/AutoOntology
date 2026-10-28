/* Playground: question the unified ontology. Chat (RAG with citations) and SPARQL run on the
   latest snapshot; SQL goes through the upstream NL->SQL agent of one structured source.
   Lazy-loaded from App. */

import { Bot, Database, FlaskConical, Hammer, MessagesSquare, Sparkles } from "lucide-react"
import { useCallback } from "react"
import { Link, useSearchParams } from "react-router"

import { ConceptSheet } from "@/components/concept-sheet"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { ChatTab } from "@/components/playground/chat-tab"
import { ConceptOpener } from "@/components/playground/concept-opener"
import { SparqlTab } from "@/components/playground/sparql-tab"
import { SqlTab } from "@/components/playground/sql-tab"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOntology, useSources, useStatus } from "@/hooks/queries"
import { isNoSnapshot } from "@/lib/api"

const TABS = [
  { key: "chat", label: "Chat", icon: MessagesSquare },
  { key: "sql", label: "SQL", icon: Database },
  { key: "sparql", label: "SPARQL", icon: Sparkles },
] as const
type Tab = (typeof TABS)[number]["key"]
const AUTO = "__auto"

function ProviderBadge() {
  const { data } = useStatus()
  const p = data?.providers
  if (!p) return null
  return (
    <Link to="/status" title="Model providers (read-only, set in .env)" id="playground-provider">
      <Badge variant="outline" className="max-w-full gap-1.5 font-normal">
        <Bot aria-hidden /> <span className="truncate">{p.chat_provider} · {p.chat_model}</span>
        <span className="hidden text-muted-foreground sm:inline">/ embeddings {p.embed_model}</span>
      </Badge>
    </Link>
  )
}

export default function PlaygroundPage() {
  const [params, setParams] = useSearchParams()
  const ontology = useOntology()
  const sources = useSources()
  const structured = (sources.data ?? []).filter((s) => s.kind !== "documents" && s.built)
  const sqlSource = structured.find((s) => s.name === params.get("source"))?.name ?? null
  const tab = (TABS.some((t) => t.key === params.get("tab")) ? params.get("tab") : "chat") as Tab
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
      <PageHeader title="Playground"
                  description="Ask the ontology in plain language, query the structured data, or run SPARQL over the exported Turtle."
                  actions={<ProviderBadge />} />
      {isNoSnapshot(ontology.error) ? (
        <EmptyState icon={FlaskConical} title="No ontology yet"
                    hint="Build the ontology first; the playground works on its latest revision."
                    action={<Button asChild><Link to="/build"><Hammer aria-hidden /> Go to Build</Link></Button>} />
      ) : ontology.error ? <ErrorState error={ontology.error} onRetry={() => ontology.refetch()} />
        : !ontology.data ? <Skeleton className="h-64" />
        : (
          <Tabs value={tab} onValueChange={(v) => set({ tab: v })} id="playground-tabs">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <TabsList className="h-auto flex-wrap justify-start">
                {TABS.map((t) => (
                  <TabsTrigger key={t.key} value={t.key} data-tab={t.key} className="flex-none">
                    <t.icon aria-hidden /> {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              <span className="text-xs text-muted-foreground" id="playground-revision">
                Revision {ontology.data.revision} · {ontology.data.workspace}
              </span>
            </div>
            {/* each tab keeps its state while another one is shown */}
            <TabsContent value="chat" forceMount hidden={tab !== "chat"}><ChatTab /></TabsContent>
            <TabsContent value="sql" forceMount hidden={tab !== "sql"}>
              {structured.length > 1 && (
                <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">Structured source</span>
                  <Select value={sqlSource ?? AUTO} onValueChange={(v) => set({ source: v === AUTO ? null : v })}>
                    <SelectTrigger className="w-full sm:w-56" aria-label="Structured source" id="playground-source">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={AUTO}>Default (first in the snapshot)</SelectItem>
                      {structured.map((s) => <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <SqlTab key={sqlSource ?? AUTO} source={sqlSource} />
            </TabsContent>
            <TabsContent value="sparql" forceMount hidden={tab !== "sparql"}><SparqlTab /></TabsContent>
          </Tabs>
        )}
      <ConceptSheet conceptId={conceptId} onClose={() => set({ concept: null })} onOpen={openConcept} />
    </ConceptOpener.Provider>
  )
}
