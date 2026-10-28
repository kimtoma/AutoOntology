import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"

import { ConceptSheet } from "@/components/concept-sheet"
import { RunSheet } from "@/components/run-sheet"
import { mockFetch, renderPage } from "@/test/utils"

vi.mock("@/lib/webgl", () => ({ hasWebGL: () => false }))
vi.mock("@/components/graph/graph-2d", () => ({ default: () => <div data-testid="canvas-2d" /> }))

const RUN = {
  id: "r1", kind: "build", source: null, status: "running", started_at: null, finished_at: null,
  error: null, params: {}, managed: false, steps: [], progress: {},
}
const CONCEPT = {
  id: "term-c", name: "Contract", kind: "entity", origin: "structured", source: "hanbit", definition: "",
  synonyms: [], merged_concepts: [], folded_into: null, enriched_by: "", evidence_count: 0,
  alignment: { best: null, candidates: 0 }, revision: 2, description: "", attributes: [], relations: [],
  alignments: [], evidence: [], definitions: [], tables: [], individual_type: null,
}

class FakeEventSource {
  static CLOSED = 2
  readyState = 0
  onopen = null
  onerror = null
  addEventListener() {}
  close() {}
}

/** A button that opens *sheet* with an id, the way the pages do (state, not SheetTrigger). */
function Opener({ sheet }: { sheet: (id: string | null, close: () => void) => React.ReactNode }) {
  const [id, setId] = useState<string | null>(null)
  return (
    <>
      <button type="button" onClick={() => setId("x")}>Open</button>
      {sheet(id, () => setId(null))}
    </>
  )
}

async function expectFocusInsideThenBack(user: ReturnType<typeof userEvent.setup>) {
  const opener = screen.getByRole("button", { name: "Open" })
  await user.click(opener)
  const dialog = await screen.findByRole("dialog")
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
  // the first Tab stays inside the sheet
  await user.tab()
  expect(dialog.contains(document.activeElement)).toBe(true)
  await user.keyboard("{Escape}")
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  await waitFor(() => expect(opener).toHaveFocus())
}

describe("sheets take the focus", () => {
  it("run sheet", async () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    mockFetch({ "GET /runs/x": { ...RUN, id: "x" } })
    const user = userEvent.setup()
    renderPage(<Opener sheet={(id, close) => <RunSheet runId={id} onClose={close} />} />)
    await expectFocusInsideThenBack(user)
  })

  it("concept sheet", async () => {
    mockFetch({ "GET /ontology/concepts/x": { ...CONCEPT, id: "x" } })
    const user = userEvent.setup()
    renderPage(<Opener sheet={(id, close) => <ConceptSheet conceptId={id} onClose={close} />} />)
    await expectFocusInsideThenBack(user)
  })

  it("mobile graph node sheet", async () => {
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(375)
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(700)
    const width = window.innerWidth
    window.innerWidth = 375
    try {
      mockFetch({
        "GET /ontology/graph": {
          layer: "all", warnings: [], structured_sources: [], documents_sources: [],
          counts: { nodes: 1, edges: 0, total_nodes: 1 }, truncated: false, max_nodes: 1500,
          nodes: [{ id: "n1", label: "Customer", type: "individual", source: "s", kind: "term", origin: "structured", layer: "structured" }],
          edges: [],
        },
      })
      const { default: GraphPage } = await import("@/pages/graph")
      renderPage(<GraphPage />, "/graph?focus=n1")
      const dialog = await screen.findByRole("dialog")
      expect(dialog).toHaveTextContent("Customer")
      await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
    } finally {
      window.innerWidth = width
      vi.restoreAllMocks()
    }
  })
})
