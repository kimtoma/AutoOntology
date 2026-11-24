import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { invalidateAfterDecision, keys } from "@/hooks/queries"
import { t } from "@/i18n"
import {
  api, DECISION_WORD, errorMessage, type AcceptRelation, type Decision, type DecisionValue, type ReviewItem, type ReviewListing,
} from "@/lib/api"

/** The display name of a review item (concept, alias or source). */
export function itemName(item: ReviewItem): string {
  if (item.category === "aliases") return item.alias ?? item.id
  if (item.category === "objects") return item.object_type?.label || item.id.replace(/^ob:/, "")
  if (item.category === "logic") return item.logic?.label || item.logic?.name || item.id.replace(/^lg:/, "")
  if (item.category === "stale_sources") return item.source?.name ?? item.id.replace(/^src:/, "")
  return item.concept?.name ?? item.id
}

/** Put *decision* on the item *id* in every cached review page (the card keeps its place
    and shows its badge and Undo until the list is fetched again). */
function patchItem(qc: QueryClient, id: string, decision: Decision | null, item?: ReviewItem) {
  qc.setQueriesData<ReviewListing>({ queryKey: keys.review }, (page) =>
    page && { ...page, items: page.items.map((i) => (i.id === id ? { ...i, ...(item ?? {}), decision } : i)) })
}

export const RELATIONS: AcceptRelation[] = ["same", "related", "narrower", "broader"]

export const isAlignment = (item: ReviewItem) =>
  item.category === "proposals" || item.category === "axioms" || item.category === "folds"

/** The relation a proposal or relation item is accepted with by default: the suggested one. */
export function suggestedRelation(item: ReviewItem): AcceptRelation {
  const r = item.relation as AcceptRelation
  return RELATIONS.includes(r) ? r : "same"
}

/** The decision a/r (keyboard) and the primary button take for *item*, or null. */
export function primaryDecision(item: ReviewItem, key: "a" | "r"): DecideArgs["decision"] | null {
  if (item.category === "stale_sources") return null
  if (item.category === "unaligned") return key === "a" ? "acknowledge" : null
  // a logic candidate that needs an edit, or an object type without a key, cannot be accepted as it is
  if (key === "a" && item.category === "logic" && !item.logic?.ready) return null
  if (key === "a" && item.category === "objects" && !item.object_type?.key) return null
  return key === "a" ? "accept" : "reject"
}

export interface DecideArgs {
  item: ReviewItem
  decision: DecisionValue
  relation?: AcceptRelation
  /** logic accept: the edited spec (YAML) */
  spec?: string
}

/** Decide, undo and announce (aria-live) review items; the nav badge and the apply banner
    refresh after each change. */
export function useReviewActions() {
  const qc = useQueryClient()
  const [announcement, setAnnouncement] = useState("")
  const decide = useMutation({
    mutationFn: ({ item, decision, relation, spec }: DecideArgs) =>
      api.decide(item.id, relation && isAlignment(item) ? { decision, relation }
        : spec !== undefined && item.category === "logic" ? { decision, spec } : { decision }),
    onSuccess: (res, { item, decision }) => {
      patchItem(qc, item.id, res.decision, res.item)
      invalidateAfterDecision(qc)
      setAnnouncement(t(`review.announce.${DECISION_WORD[decision]}`, { name: itemName(item) }))
    },
    onError: (e) => toast.error(t("review.failed"), { description: errorMessage(e) }),
  })
  const undo = useMutation({
    mutationFn: (item: ReviewItem) => api.undoDecision(item.id),
    onSuccess: (_res, item) => {
      patchItem(qc, item.id, null)
      invalidateAfterDecision(qc)
      setAnnouncement(t("review.announce.undo", { name: itemName(item) }))
    },
    onError: (e) => toast.error(t("review.failed"), { description: errorMessage(e) }),
  })
  const busyId = decide.isPending ? decide.variables?.item.id : undo.isPending ? undo.variables?.id : undefined
  return { decide: decide.mutate, undo: undo.mutate, busyId, announcement }
}

/** `POST /api/runs {kind: build, resume: true, sources}`: build only what changed. */
export function useBuildChanged(onStarted?: (runId: string) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sources: string[]) =>
      api.startRun({ kind: "build", resume: true, sources: sources.length ? sources : null }),
    onSuccess: ({ run_id }) => {
      qc.invalidateQueries({ queryKey: keys.runs })
      onStarted?.(run_id)
    },
    onError: (e) => toast.error(t("home.runFailed", { kind: t("run.build") }), { description: errorMessage(e) }),
  })
}
