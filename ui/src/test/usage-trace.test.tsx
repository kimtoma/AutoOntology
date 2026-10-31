import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TraceButton } from "@/components/playground/usage-traces"
import { mockFetch, renderPage } from "@/test/utils"

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
