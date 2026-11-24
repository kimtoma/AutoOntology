import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { invalidateAfterAction, keys } from "@/hooks/queries"
import { useT } from "@/i18n"
import { api, ApiError, errorMessage, type ActionRun } from "@/lib/api"

/** Approve, reject (with a note) and revert action runs; errors such as four-eyes
    (`self_approval`) come back as a message for the card that asked. */
export function useRunDecisions(onDone?: (run: ActionRun) => void) {
  const t = useT()
  const qc = useQueryClient()
  const [error, setError] = useState<{ id: string; message: string; code?: string } | null>(null)
  const mutation = useMutation({
    mutationFn: async (a: { id: string; kind: "approve" | "reject" | "revert"; note?: string; force?: boolean }) => {
      if (a.kind === "approve") return (await api.actions.approve(a.id)).run
      if (a.kind === "reject") return (await api.actions.reject(a.id, a.note ?? "")).run
      return (await api.actions.revert(a.id, a.force)).run
    },
    onMutate: () => setError(null),
    onSuccess: (run, a) => {
      invalidateAfterAction(qc)
      qc.setQueryData(keys.actionRun(run.id), (old: { run: ActionRun } | undefined) => old && { ...old, run })
      const label = run.preview?.label || run.action
      if (a.kind === "approve") {
        if (run.status === "failed") toast.error(t("logic.run.approvedButFailed"), { description: run.error ?? undefined })
        else toast.success(t("logic.run.approvedToast", { action: label, key: run.object_key }))
      } else if (a.kind === "reject") toast.success(t("logic.run.rejectedToast", { action: label, key: run.object_key }))
      else toast.success(t("logic.run.revertedToast", { action: label, key: run.object_key }))
      onDone?.(run)
    },
    onError: (e, a) => setError({ id: a.id, message: errorMessage(e), code: e instanceof ApiError ? e.code : undefined }),
  })
  return { decide: mutation.mutate, busyId: mutation.isPending ? mutation.variables?.id : undefined, error }
}

export type RunDecisions = ReturnType<typeof useRunDecisions>
