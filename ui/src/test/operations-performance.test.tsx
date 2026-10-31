import { act, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import OperationsHome from "@/pages/operations"
import { mockFetch, renderPage } from "@/test/utils"
const overview = { published_revision: 6, candidate_revision: null, workspace: "Org", counts: { pending: 1, approved: 0, held: 0, rejected: 0, feedback: 0 }, highlights: [] }
const routes = {
  "GET /ops": overview,
  "GET /ontology": { revision: 6, workspace: "Org", counts: { classes: 4 }, sources: [], alignment: {} },
  "GET /cases/summary": { active: 3, awaiting_review: 1, overdue: 0, unassigned: 0, held: 0 },
  "GET /ops/changes": { ...overview, items: [], feedback: [] },
  "GET /sources/freshness": { sources: [], stale: 0, age_s: 0 },
  "GET /runs": { runs: [{ id: "r", kind: "publish", status: "succeeded", finished_at: "2026-10-30T00:00:00Z" }] },
  "GET /ops/connections": { items: [], counts: { total: 0 }, unapplied: 0, pages: 1, total: 0 },
}
it.each(["/api/ops", "/api/ontology"])("shows available case and activity data while %s is still pending", async blocked => {
  const { fn } = mockFetch(routes)
  vi.stubGlobal("fetch", vi.fn((url: RequestInfo | URL, init?: RequestInit) => String(url) === blocked ? new Promise<Response>(() => {}) : fn(url, init)))
  renderPage(<OperationsHome />)
  const cases = await screen.findByRole("region", { name: "Business cases" })
  expect(await within(cases).findByText("3")).toBeVisible()
  expect(await screen.findByText("Knowledge published")).toBeVisible()
  expect(screen.getByRole("heading", { name: "Ontology operations" })).toBeVisible()
  if (blocked === "/api/ops") expect(await screen.findByText("In use · r6")).toBeVisible()
})
it.each([["/ops", ["ops", "overview"]], ["/ops/changes", ["ops", "changes"]], ["/runs", ["runs"]]] as const)("retains authorized data with a visible refresh error from %s", async (path, key) => {
  let fail = false
  mockFetch({ ...routes, [`GET ${path}`]: () => fail ? new Response(JSON.stringify({ detail: "Temporarily unavailable" }), { status: 503, headers: { "content-type": "application/json" } }) : routes[`GET ${path}` as keyof typeof routes] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><OperationsHome /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("In use · r6")).toBeVisible()
  expect(await screen.findByText("Knowledge published")).toBeVisible()
  expect(await within(screen.getByRole("region", { name: "Business cases" })).findByText("3")).toBeVisible()
  fail = true
  await act(async () => { await client.invalidateQueries({ queryKey: key }) })
  expect(await screen.findByText("Temporarily unavailable")).toBeVisible()
  expect(screen.getByText("In use · r6")).toBeVisible()
  expect(within(screen.getByRole("region", { name: "Business cases" })).getByText("3")).toBeVisible()
})
