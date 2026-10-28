import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"

export const keys = {
  status: ["status"] as const,
  sources: ["sources"] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["run", id] as const,
  concepts: (p: object) => ["concepts", p] as const,
  concept: (id: string) => ["concept", id] as const,
  exports: ["exports"] as const,
  graph: ["graph"] as const,
  providers: ["providers"] as const,
}

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: 60_000 })

export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources })
