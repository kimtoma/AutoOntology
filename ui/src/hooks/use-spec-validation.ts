import { useQuery } from "@tanstack/react-query"

import { keys } from "@/hooks/queries"
import { useDebounced } from "@/hooks/use-debounced"
import { api } from "@/lib/api"

/** Validate *text* as the user types (debounced): null while unknown or empty. */
export function useSpecValidation(text: string, enabled = true) {
  const debounced = useDebounced(text, 400)
  const q = useQuery({
    queryKey: [...keys.logicAll, "validate", debounced],
    queryFn: () => api.logic.validate(debounced),
    enabled: enabled && !!debounced.trim(),
    retry: false,
    staleTime: 60_000,
  })
  const settled = debounced === text && !q.isFetching
  return { result: settled ? q.data ?? null : null, error: q.error, pending: !settled }
}

export type SpecValidation = ReturnType<typeof useSpecValidation>
