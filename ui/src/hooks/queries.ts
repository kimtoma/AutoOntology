import { useQuery } from "@tanstack/react-query"

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
}

/** No retry for answers that will not change by retrying (404 no_snapshot, 400 ...). */
const retryServerErrors = (count: number, err: unknown) =>
  count < 1 && !(err instanceof ApiError && err.status >= 400 && err.status < 500)

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 60_000 })

export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources })

/** `/api/ontology`: revision, workspace and counts of the latest snapshot. */
export const useOntology = () =>
  useQuery({ queryKey: keys.ontology, queryFn: api.ontology, retry: retryServerErrors })

export const useSamples = () => useQuery({ queryKey: keys.samples, queryFn: api.samples })

export { retryServerErrors }
