import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { keys } from "@/hooks/queries"
import { api, errorMessage, type RunKind, type RunOptions, type Source } from "@/lib/api"
import { RUN_KO } from "@/lib/labels"

type Action = Exclude<RunKind, "ingest">

/** Options sent by the one-click buttons (the old UI's behaviour). */
export function defaultOptions(kind: Action, s: Source): RunOptions {
  return kind === "export" && s.kind !== "documents" ? { sources: [s.name] } : {}
}

export function useStartRun(onStarted: (runId: string) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { kind: Action; source: string; options: RunOptions }) =>
      api.startRun(v.kind, v.source, v.options),
    onSuccess: ({ run_id }) => {
      onStarted(run_id)
      qc.invalidateQueries({ queryKey: keys.sources })
      qc.invalidateQueries({ queryKey: keys.runs })
    },
    onError: (e, v) => toast.error(`${RUN_KO[v.kind]} 시작 실패`, { description: errorMessage(e) }),
  })
}

