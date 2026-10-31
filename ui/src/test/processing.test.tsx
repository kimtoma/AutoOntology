import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PublicationRecord } from "@/components/publication-record"
import { mockFetch, renderPage } from "@/test/utils"

it("shows captured extraction settings while keeping historical unknowns explicit", async () => {
  mockFetch({ "GET /ops/publications/2": {
    revision: 2, verification: { ok: true }, manifest: {
      candidate_revision: 1, previous_revision: null, originals: [], artifacts: {}, index: { model: "test", elements: 1 },
      source_bindings: [{ sources: [{ kind: "documents", included: true, processing: { status: "unknown" } }] }],
      processing_receipts: [{ id: "proc-1", record: { stage: "extract", status: "succeeded", configuration: { model: "selected-model", mode: "json" } } }],
    },
  } })
  renderPage(<PublicationRecord revision={2} />)
  await userEvent.click(screen.getByRole("button", { name: "Verify publication" }))
  expect(await screen.findByText("Processing history")).toBeInTheDocument()
  await userEvent.click(screen.getByText("Processing history"))
  expect(within(screen.getByRole("list", { name: "Captured extraction selections" })).getByText(/selected-model/)).toBeInTheDocument()
  expect(screen.getByText(/Historical processing settings are unknown/)).toBeInTheDocument()
})
