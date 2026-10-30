import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ConceptBody } from "@/components/concept-sheet"
import type { ConceptDetail } from "@/lib/api"
import { mockFetch, renderPage } from "@/test/utils"

function concept(available: boolean): ConceptDetail {
  return {
    id: "concept-rule", name: "Approval", kind: "rule", origin: "document", source: "rules",
    definition: "Policy", synonyms: [], merged_concepts: [], folded_into: null, enriched_by: "",
    evidence_count: 1, alignment: { counts: {}, best: null }, revision: 8,
    description: "", definitions: ["Policy"], tables: [], individual_type: null,
    attributes: [], relations: [], alignments: [],
    evidence: [{ quote: "Original quote", verified: true, page: 1, document: "Policy", path: "rules.md",
      original: { status: available ? "archived" : "not_archived", url: available ? "/api/ontology/documents/doc-one/original?revision=8" : null } }],
  }
}

it("downloads the original bound to the displayed revision", () => {
  renderPage(<ConceptBody c={concept(true)} />)
  expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("href", "/api/ontology/documents/doc-one/original?revision=8")
})

it("explains missing historical originals without linking the current locator", () => {
  renderPage(<ConceptBody c={concept(false)} />)
  expect(screen.queryByRole("link", { name: "Download original" })).toBeNull()
  expect(screen.getByText("Original file unavailable for this version.")).toBeInTheDocument()
})

it("recovers only through the displayed document and revision operation", async () => {
  const c = concept(false)
  const url = "/api/ontology/documents/doc-one/archive?revision=8"
  c.evidence[0].original!.recover_url = url
  const { calls } = mockFetch({ "POST /ontology/documents/doc-one/archive": { original: concept(true).evidence[0].original } })
  renderPage(<ConceptBody c={c} />)
  await userEvent.setup().click(screen.getByRole("button", { name: "Recover original" }))
  expect(await screen.findByRole("link", { name: "Download original" })).toHaveAttribute("href", "/api/ontology/documents/doc-one/original?revision=8")
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1)
})
