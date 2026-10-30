import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import OperationsHome from "@/pages/operations"
import ReviewPage from "@/pages/review"
import { mockFetch, renderPage } from "@/test/utils"

const item = {
  id: "abc",
  collection: "classes",
  key: "concept-approval",
  label: "승인 기준",
  kind: "changed",
  before: { label: "승인 기준", definitions: ["3억원 이상"] },
  after: { label: "승인 기준", definitions: ["2억원 이상"] },
  evidence: [
    {
      quote: "2억원 이상은 대표이사 승인",
      document: "계약관리규정",
      page: 1,
      verified: true,
      version: "candidate",
    },
  ],
  decision: null,
  revision: 7,
}
const overview = {
  published_revision: 6,
  candidate_revision: 7,
  workspace: "org",
  published_at: "2026-10-30T00:00:00Z",
  counts: { pending: 1, approved: 0, held: 0, rejected: 0, feedback: 0 },
  highlights: [item],
  checks: [],
}
const ontology = { revision: 6, workspace: "org", counts: {}, sources: [], created_at: "", alignment: {} }
const routes = {
  "GET /ops": overview,
  "GET /ontology": ontology,
  "GET /sources/freshness": { sources: [], stale: 0, age_s: 0 },
  "GET /runs": [],
  "GET /ops/connections": { items: [], counts: { total: 0 }, unapplied: 0, pages: 1, total: 0 },
  "GET /ops/changes": { ...overview, items: [item], feedback: [] },
}

describe("Ontology Ops", () => {
  it("opens the operations dashboard after a snapshot exists, with no onboarding checklist", async () => {
    mockFetch(routes)
    renderPage(<OperationsHome />)
    expect(await screen.findByRole("heading", { name: "Ontology operations" })).toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "Get started" })).toBeNull()
    expect(screen.getByText("승인 기준")).toBeInTheDocument()
    expect(screen.getByText("In use · r6")).toBeInTheDocument()
  })
  it("shows source-check failures as unknown rather than fresh", async () => {
    mockFetch({
      ...routes,
      "GET /sources/freshness": {
        sources: [{ name: "rules", state: "unknown", reasons: [], stale: false, error: "unreachable" }],
        stale: 0,
        age_s: 0,
      },
    })
    renderPage(<OperationsHome />)
    expect(await screen.findByText("Source status unknown")).toBeInTheDocument()
  })
  it("saves a review decision without calling publication", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({ ...routes, "POST /ops/changes/abc/decision": { decision: "accept" } })
    renderPage(<ReviewPage />)
    await user.click(await screen.findByRole("button", { name: "Approve change" }))
    await waitFor(() => expect(calls.some((c) => c.path === "/ops/changes/abc/decision")).toBe(true))
    expect(calls.some((c) => c.path === "/ops/publish")).toBe(false)
  })
  it("requires a reason for rejection", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes)
    renderPage(<ReviewPage />)
    await user.click(await screen.findByRole("button", { name: "Exclude proposal" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reason")
    expect(calls.some((c) => c.method === "POST")).toBe(false)
  })
  it("edits a proposal with named inputs and requires an explanation", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({ ...routes, "POST /ops/changes/abc/decision": { decision: "accept" } })
    renderPage(<ReviewPage />)
    await user.click(await screen.findByRole("button", { name: "Edit proposal" }))
    const definition = screen.getByLabelText("Definition", { exact: true })
    await user.clear(definition)
    await user.type(definition, "Revised definition")
    await user.click(screen.getByRole("button", { name: "Approve edited proposal" }))
    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(calls.some((c) => c.method === "POST")).toBe(false)
    await user.type(screen.getByLabelText("Decision reason"), "Clarified the source wording")
    await user.click(screen.getByRole("button", { name: "Approve edited proposal" }))
    await waitFor(() => expect(calls.some((c) => c.path === "/ops/changes/abc/decision")).toBe(true))
  })
})
