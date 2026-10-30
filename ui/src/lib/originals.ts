import type { OriginalReference } from "@/lib/api"

type OriginalEvidence = { document: string; document_id?: string | null; original?: OriginalReference }

/** A document needs one file action even when many quotes support a decision. */
export function isFirstOriginal(items: OriginalEvidence[], index: number) {
  const key = (item: OriginalEvidence) => item.original?.recover_url || item.original?.url || item.document_id || item.document
  return !!items[index].original && items.findIndex((item) => key(item) === key(items[index])) === index
}
