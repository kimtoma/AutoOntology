import { useQuery, type QueryClient } from "@tanstack/react-query"

import { api, ApiError } from "@/lib/api"

export const keys = {
  status: ["status"] as const,
  sources: ["sources"] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["run", id] as const,
  ontology: ["ontology"] as const,
  concepts: (p: object) => ["concepts", p] as const,
  concept: (id: string) => ["concept", id] as const,
  exports: ["exports"] as const,
  graph: ["graph"] as const,
  samples: ["samples"] as const,
  settings: ["settings"] as const,
  review: ["review"] as const,
  reviewPage: (p: object) => ["review", p] as const,
  pendingCount: ["review-pending"] as const,
  revisions: ["revisions"] as const,
  diff: (from: number | null | undefined, to: number | null | undefined) => ["diff", from ?? null, to ?? null] as const,
  freshness: ["freshness"] as const,
}

/** The review inbox and its badge poll this often (and refresh after decisions and runs). */
export const REVIEW_POLL_MS = 30_000

/** Everything a finished run (build, export, apply) may have changed. */
export function invalidateAfterRun(qc: QueryClient) {
  for (const key of [keys.runs, keys.sources, keys.exports, keys.ontology, keys.graph, ["concepts"],
                     ["concept"], keys.review, keys.pendingCount, keys.revisions, ["diff"], keys.freshness]) {
    qc.invalidateQueries({ queryKey: key })
  }
}

/** After a decision: the inbox, its badge and the concept details that show decisions. */
export function invalidateAfterDecision(qc: QueryClient) {
  for (const key of [keys.pendingCount, ["concept"]]) qc.invalidateQueries({ queryKey: key })
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
