import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PublicationRecord } from "@/components/publication-record"
import { mockFetch, renderPage } from "@/test/utils"

it("verifies and explains a frozen publication on demand", async () => {
  const user = userEvent.setup()
  mockFetch({ "GET /ops/publications/8": {
    revision: 8, id: "pub-123", status: "committed", verification: { ok: true },
    manifest: { candidate_revision: 7, previous_revision: 6, originals: [{ sha256: "abc" }],
      artifacts: { "ontology.ttl": "def" }, index: { model: "qwen", elements: 21 },
      validation: { questions: [{ ok: true }] } },
  } })
  renderPage(<PublicationRecord revision={8} />)
  await user.click(screen.getByRole("button", { name: "Verify publication" }))
  expect(await screen.findByText("Publication verified")).toBeInTheDocument()
  expect(screen.getByText("1 originals · 1 files · 21 indexed elements · qwen")).toBeInTheDocument()
})

it("does not claim historical proof for a legacy version", async () => {
  const user = userEvent.setup()
  mockFetch({ "GET /ops/publications/6": { revision: 6, legacy: true, manifest: null } })
  renderPage(<PublicationRecord revision={6} />)
  await user.click(screen.getByRole("button", { name: "Verify publication" }))
  expect(await screen.findByText("This version predates publication receipts. Its historical validation and index cannot be verified.")).toBeInTheDocument()
  expect(screen.queryByText("Publication verified")).toBeNull()
})
