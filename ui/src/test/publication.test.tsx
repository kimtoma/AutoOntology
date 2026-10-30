import { screen } from "@testing-library/react"
import HistoryPage from "@/pages/history"
import HomePage from "@/pages/home"
import { mockFetch, renderPage } from "@/test/utils"

const overview = {
  published_revision: null,
  candidate_revision: 1,
  workspace: "org",
  counts: { pending: 1, approved: 0, held: 0, rejected: 0, feedback: 0 },
  checks: [],
  highlights: [],
  initial_approval_required: true,
  publication_policy: { minimum_questions: 1 },
}

it("takes a first-build candidate to explicit review rather than repeating setup", async () => {
  mockFetch({
    "GET /ontology": () => new Response(JSON.stringify({ error: "no_snapshot", detail: "Not published" }), { status: 404, headers: { "Content-Type": "application/json" } }),
    "GET /ops": overview,
    "GET /sources": [],
    "GET /runs": [],
    "GET /exports": [],
    "GET /samples": [],
  })
  renderPage(<HomePage afterBuild={<div>Operational dashboard</div>} />)
  expect(await screen.findByRole("heading", { name: "Approve the first operational version" })).toBeInTheDocument()
  expect(screen.getByRole("link", { name: "Review the first candidate" })).toHaveAttribute("href", "/review")
  expect(screen.getByRole("link", { name: "Questions and publication" })).toHaveAttribute("href", "/history")
  expect(screen.queryByText("Operational dashboard")).toBeNull()
  expect(document.querySelector("#checklist")).toBeNull()
})

it("requires representative questions before validating approved knowledge", async () => {
  const { calls } = mockFetch({
    "GET /ops": { ...overview, counts: { ...overview.counts, approved: 1 } },
    "GET /ops/connections": { items: [], counts: { total: 0 }, unapplied: 0 },
    "GET /ontology/revisions": { revisions: [] },
  })
  renderPage(<HistoryPage />)
  const validate = await screen.findByRole("button", { name: "Validate approved changes" })
  expect(validate).toBeDisabled()
  expect(screen.getByText(/Add at least one representative question/)).toBeInTheDocument()
  expect(calls.some((c) => c.path === "/ops/publish")).toBe(false)
})
