import { useMutation } from "@tanstack/react-query"
import { CheckCircle2, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { operations } from "@/lib/operations"
import { errorMessage } from "@/lib/api"
import { useT } from "@/i18n"

export function PublicationRecord({ revision }: { revision: number }) {
  const t = useT()
  const receipt = useMutation({ mutationFn: () => operations.publication(revision, true) })
  const manifest = receipt.data?.manifest
  return <div className="w-full text-sm">
    <Button variant="ghost" size="sm" disabled={receipt.isPending} onClick={() => receipt.mutate()}>
      {receipt.isPending && <Loader2 aria-hidden className="animate-spin" />}
      {t("ops.verifyPublication")}
    </Button>
    {receipt.error && <p role="alert" className="mt-2 text-destructive">{errorMessage(receipt.error)}</p>}
    {receipt.data?.legacy && <p className="mt-2 text-muted-foreground">{t("ops.legacyPublication")}</p>}
    {manifest && <div className="mt-2 border-l-2 pl-3">
      {receipt.data?.verification?.ok && <p className="flex items-center gap-2 font-medium"><CheckCircle2 aria-hidden className="size-4 text-success" />{t("ops.publicationVerified")}</p>}
      <p className="mt-2 text-muted-foreground">{t("ops.publicationSummary", { originals: manifest.originals.length, files: Object.keys(manifest.artifacts).length, elements: manifest.index.elements, model: manifest.index.model })}</p>
      <p className="mt-1 text-muted-foreground">{t("ops.publicationVersions", { candidate: manifest.candidate_revision, previous: manifest.previous_revision ?? "—" })}</p>
      <details className="mt-2">
        <summary className="cursor-pointer">{t("ops.publicationDetails")}</summary>
        <pre className="mt-2 max-h-80 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(receipt.data, null, 2)}</pre>
      </details>
    </div>}
  </div>
}
