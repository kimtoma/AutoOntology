import { useQuery, type QueryClient } from "@tanstack/react-query"
import { operations } from "@/lib/operations"
export const opsKeys = {
  root: ["ops"] as const,
  overview: ["ops", "overview"] as const,
  changes: ["ops", "changes"] as const,
  freshness: ["ops", "freshness"] as const,
  legacy: (page = 1, status = "pending") => ["ops", "legacy", page, status] as const,
  revisions: ["ops", "revisions"] as const,
}
export const useOperations = (enabled = true) =>
  useQuery({ enabled, queryKey: opsKeys.overview, queryFn: operations.overview, refetchInterval: 5000 })
export const useChanges = () =>
  useQuery({ queryKey: opsKeys.changes, queryFn: operations.changes, refetchInterval: 5000 })
export const useFreshness = () =>
  useQuery({ queryKey: opsKeys.freshness, queryFn: () => operations.freshness(), refetchInterval: 60000 })
export const useLegacy = (page = 1, status = "pending", enabled = true) =>
  useQuery({
    enabled,
    queryKey: opsKeys.legacy(page, status),
    queryFn: () => operations.legacy(page, status),
    refetchInterval: 10000,
  })
export function refreshOperations(qc: QueryClient) {
  for (const key of [
    ["ops"],
    ["ontology"],
    ["concepts"],
    ["concept"],
    ["graph"],
    ["exports"],
    ["sources"],
    ["runs"],
    ["playground-index"],
  ])
    qc.invalidateQueries({ queryKey: key })
}
