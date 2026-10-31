import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TraceButton, UsageHistory } from "@/components/playground/usage-traces"
import { IdentityContext, type IdentitySession } from "@/lib/identity"
import { mockFetch, renderPage } from "@/test/utils"

const ownSession: IdentitySession = { principal: { id: "reader", username: "reader", name: "Reader", kind: "human", status: "active" }, workspace: { workspace_id: "workspace", workspace_name: "Workspace", organization_id: "org" }, credential_id: "session", expires_at: 2000000000, policy_version: 1, grants: [], capabilities: { "results.read": ["*"], "results.delete": ["*"] } }
function renderHistory(admin: boolean) {
  return renderPage(<IdentityContext.Provider value={{ session: { ...ownSession, capabilities: { ...ownSession.capabilities, ...(admin ? { "identity.admin": ["*"] } : {}) } }, signOut: vi.fn(), signingOut: false, logoutFailed: false }}><UsageHistory /></IdentityContext.Provider>)
}

it("shows a reader's own history without offering inspection of other people", async () => {
  const { fn } = mockFetch({ "GET /playground/traces": { items: [] } })
  renderHistory(false)
  expect(await screen.findByText("My saved requests")).toBeVisible()
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  expect(fn.mock.calls.every(([url]) => !String(url).includes("all_users"))).toBe(true)
})

it("requires an explicit admin choice before requesting other owners' history", async () => {
  const { fn } = mockFetch({ "GET /playground/traces": (url: string) => ({ items: url.includes("all_users=true") ? [{ id: "other", kind: "chat", status: "succeeded", revision: 8, started_at: "2026-10-31T01:00:00Z", owner: { name: "Alice", principal_id: "alice", legacy: false } }] : [] }) })
  renderHistory(true)
  await screen.findByText("My saved requests")
  expect(fn.mock.calls.every(([url]) => !String(url).includes("all_users"))).toBe(true)
  await userEvent.click(screen.getByRole("checkbox", { name: "Inspect all users and earlier records" }))
  expect(await screen.findByText("Alice")).toBeVisible()
  expect(fn.mock.calls.some(([url]) => String(url).includes("all_users=true"))).toBe(true)
})

it.each(["succeeded", "failed", "cancelled"])("recovers CSV only from a successful SQL record: %s", async status => {
  const { calls } = mockFetch({"GET /playground/traces/trace1": {
    id: "trace1", kind: "sql", status, revision: 8, data: {
      binding: {legacy: false}, request: {question: "고객 수"}, duration_ms: 100,
      context: {data_time: "live_at_execution"}, outcome: {events: []},
    },
  }})
  renderPage(<TraceButton traceId="trace1" />)
  await userEvent.click(screen.getByRole("button", {name: "Usage record"}))
  await screen.findByText("고객 수")
  if (status === "succeeded") {
    expect(screen.getByRole("link", {name: "CSV"})).toHaveAttribute("href", "/api/playground/traces/trace1/result.csv")
  } else expect(screen.queryByRole("link", {name: "CSV"})).toBeNull()
  expect(calls.every(c => c.method === "GET")).toBe(true)
})

it("shows the saved output and context without starting another query", async () => {
  const user = userEvent.setup()
  const { calls } = mockFetch({ "GET /playground/traces/trace1": {
    id: "trace1", status: "succeeded", kind: "chat", revision: 8, started_at: "2026-10-31T00:00:00Z",
    data: { request: { question: "승인권자는?" }, binding: { legacy: false, publication_id: "pub-1" },
      duration_ms: 1200, context: { hits: [{ ref: "E1", text: "3억 이상은 대표이사 승인" }] },
      outcome: { answer: "대표이사입니다. [E1]" } },
  } })
  renderPage(<TraceButton traceId="trace1" />)
  await user.click(screen.getByRole("button", { name: "Usage record" }))
  expect(await screen.findByText("대표이사입니다. [E1]")).toBeInTheDocument()
  expect(screen.getByText("3억 이상은 대표이사 승인")).toBeInTheDocument()
  expect(calls.every(c => c.method === "GET")).toBe(true)
})

it("requires a second explicit action before deleting a trace", async () => {
  const user = userEvent.setup()
  const { calls } = mockFetch({ "GET /playground/traces/trace1": {
    id: "trace1", status: "failed", kind: "sql", revision: 8, data: {
      binding: { legacy: true }, request: { question: "count?" }, duration_ms: 100,
      context: { data_time: "live_at_execution" }, outcome: { error: "query_failed" },
    },
  }, "DELETE /playground/traces/trace1": { deleted: "trace1" } })
  renderPage(<TraceButton traceId="trace1" />)
  await user.click(screen.getByRole("button", { name: "Usage record" }))
  await user.click(await screen.findByRole("button", { name: "Delete usage record" }))
  expect(calls.some(c => c.method === "DELETE")).toBe(false)
  await user.click(screen.getByRole("button", { name: "Confirm record deletion" }))
  expect(await screen.findByText("Usage record deleted")).toBeInTheDocument()
})
