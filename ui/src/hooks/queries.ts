import { useQuery, type QueryClient } from "@tanstack/react-query"

import { api, ApiError, getCurrentProject } from "@/lib/api"

/** The query-key prefix of the current project: caches of two projects never mix, and
    switching projects refetches. */
const scope = () => ["p", getCurrentProject() ?? ""] as const

export const keys = {
  // installation-wide
  status: ["status"] as const,
  settings: ["settings"] as const,
  projects: ["projects"] as const,
  me: ["auth", "me"] as const,
  users: ["users"] as const,
  members: (slug: string) => ["members", slug] as const,
  audit: (q: object) => ["audit", q] as const,
  // the current project's (getters: read at use, after the project route set the project)
  get scope() { return scope() },
  get sources() { return [...scope(), "sources"] as const },
  get runs() { return [...scope(), "runs"] as const },
  run: (id: string) => [...scope(), "run", id] as const,
  get ontology() { return [...scope(), "ontology"] as const },
  get conceptsAll() { return [...scope(), "concepts"] as const },
  concepts: (p: object) => [...scope(), "concepts", p] as const,
  get conceptAll() { return [...scope(), "concept"] as const },
  concept: (id: string) => [...scope(), "concept", id] as const,
  get exports() { return [...scope(), "exports"] as const },
  get graph() { return [...scope(), "graph"] as const },
  get samples() { return [...scope(), "samples"] as const },
  get review() { return [...scope(), "review"] as const },
  reviewPage: (p: object) => [...scope(), "review", p] as const,
  get pendingCount() { return [...scope(), "review-pending"] as const },
  get revisions() { return [...scope(), "revisions"] as const },
  get diffAll() { return [...scope(), "diff"] as const },
  diff: (from: number | null | undefined, to: number | null | undefined) =>
    [...scope(), "diff", from ?? null, to ?? null] as const,
  get freshness() { return [...scope(), "freshness"] as const },
  get agentTokens() { return [...scope(), "agent-tokens"] as const },
  get agentUsage() { return [...scope(), "agent-usage"] as const },
  get agentTools() { return [...scope(), "agent-tools"] as const },
  get playgroundIndex() { return [...scope(), "playground-index"] as const },
  get sparqlExamples() { return [...scope(), "sparql-examples"] as const },
}

/** The review inbox and its badge poll this often (and refresh after decisions and runs). */
export const REVIEW_POLL_MS = 30_000

/** Everything a finished run (build, export, apply) may have changed. */
export function invalidateAfterRun(qc: QueryClient) {
  for (const key of [keys.runs, keys.sources, keys.exports, keys.ontology, keys.graph, keys.conceptsAll,
                     keys.conceptAll, keys.review, keys.pendingCount, keys.revisions, keys.diffAll, keys.freshness,
                     keys.projects]) {
    qc.invalidateQueries({ queryKey: key })
  }
}

/** After a decision: the inbox, its badge and the concept details that show decisions. */
export function invalidateAfterDecision(qc: QueryClient) {
  for (const key of [keys.pendingCount, keys.conceptAll]) qc.invalidateQueries({ queryKey: key })
}

/** No retry for answers that will not change by retrying (404 no_snapshot, 400 ...). */
const retryServerErrors = (count: number, err: unknown) =>
  count < 1 && !(err instanceof ApiError && err.status >= 400 && err.status < 500)

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 60_000 })

export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources })

/** `/api/ontology`: revision, workspace and counts of the latest snapshot. */
export const useOntology = () =>
  useQuery({ queryKey: keys.ontology, queryFn: () => api.ontology(), retry: retryServerErrors })

/** `/api/review/pending-count`: the nav badge and the apply banner. */
export const usePendingCount = () =>
  useQuery({ queryKey: keys.pendingCount, queryFn: api.pendingCount, retry: retryServerErrors,
             refetchInterval: REVIEW_POLL_MS })

export const useSamples = () => useQuery({ queryKey: keys.samples, queryFn: api.samples })

export { retryServerErrors }

/** `GET /api/projects` (all of them, archived too) and the default project's slug. */
export const useProjects = () =>
  useQuery({ queryKey: keys.projects, queryFn: api.projects.list, staleTime: 10_000 })
