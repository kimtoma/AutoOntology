/* The words of the Home page's five steps, kept apart so the tests can check them. The
   texts live in the dictionaries (i18n/en.ts, ko.ts: `home.<step>.*`). */

import { getLang, t, type MessageKey } from "@/i18n/core"
import type { StepId } from "@/lib/checklist"

export const GUIDE_URL = "https://github.com/kimtoma/AutoOntology/blob/main/docs/GUIDE.md"
export const GUIDE_URL_KO = "https://github.com/kimtoma/AutoOntology/blob/main/docs/GUIDE.ko.md"

/** The guide in the UI language. */
export const guideUrl = () => (getLang() === "ko" ? GUIDE_URL_KO : GUIDE_URL)

interface StepCopy { title: string; description: string; help: [string, string] }

const key = (id: StepId, part: string) => `home.${id}.${part}` as MessageKey

/** Plain words: the reader is a domain expert, not an ontology engineer. */
export function stepCopy(id: StepId): StepCopy {
  return {
    title: t(key(id, "title")),
    description: t(key(id, "description")),
    help: [t(key(id, "help1")), t(key(id, "help2"))],
  }
}
