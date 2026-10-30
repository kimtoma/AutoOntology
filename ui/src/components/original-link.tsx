import type { OriginalReference } from "@/lib/api"
import { errorMessage } from "@/lib/api"
import { useT } from "@/i18n"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

export function OriginalLink({ original }: { original?: OriginalReference }) {
  const t = useT()
  const qc = useQueryClient()
  const [saved, setSaved] = useState<{ url: string; original: OriginalReference; from?: OriginalReference } | null>(null)
  const recover = useMutation({
    mutationFn: async (url: string) => {
      const response = await fetch(url, { method: "POST" })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || data.error)
      return data.original as OriginalReference
    },
    onSuccess: (result, url) => {
      setSaved({ url, original: result, from: original })
      qc.invalidateQueries({ queryKey: ["concept"] })
      qc.invalidateQueries({ queryKey: ["ops"] })
    },
  })
  if (!original) return null
  const current = saved && saved.url === original.recover_url && saved.from === original ? saved.original : original
  return current.status === "archived" && current.url
    ? <a className="mt-2 inline-block text-xs underline underline-offset-4" href={current.url} download>{t("concept.original")}</a>
    : <div className="mt-2 text-xs text-muted-foreground">
        <p>{t("concept.originalUnavailable")}</p>
        {original.recover_url && <button type="button" className="mt-1 underline underline-offset-4" disabled={recover.isPending} onClick={() => recover.mutate(original.recover_url!)}>{t("concept.recoverOriginal")}</button>}
        {recover.error && recover.variables === original.recover_url && <p className="mt-1 text-destructive" role="alert">{errorMessage(recover.error)}</p>}
      </div>
}
