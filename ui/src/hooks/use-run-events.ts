import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { invalidateAfterRun, keys } from "@/hooks/queries"
import { api } from "@/lib/api"

/** While *runId* runs: refresh the runs on every step and every view the run may change at
    its end (the pages' 30 s poll covers the rest). */
export function useRunEnd(runId: string | undefined) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return
    const es = new EventSource(api.runEventsUrl(runId))
    const step = () => { qc.invalidateQueries({ queryKey: keys.runs }) }
    const end = () => { es.close(); invalidateAfterRun(qc) }
    es.addEventListener("step", step)
    es.addEventListener("end", end)
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) es.close() }
    return () => es.close()
  }, [runId, qc])
}
