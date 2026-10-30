import { act, screen, waitFor } from "@testing-library/react"
import App from "@/App"
import { mockFetch, renderPage } from "@/test/utils"

function workspace(publicationError = false, readiness: "ready" | "logged_out" | "backend_down" = "ready") {
  let built = false
  const result = mockFetch({
    "GET /status": {
      status: "ok",
      checks: { backend: { status: readiness === "backend_down" ? "error" : "ok" }, postgres: { status: "ok" } },
      providers: {
        chat_provider: "codex",
        chat_model: "test",
        embed_provider: "ollama",
        embed_model: "test",
        chat: { name: "codex", model: "test", available: true, logged_in: readiness !== "logged_out", detail: "ready", remedy: "" },
      },
    },
    "GET /ontology": () =>
      built
        ? { revision: 1, workspace: "org", counts: {}, sources: [], created_at: "", alignment: {} }
        : new Response(
            JSON.stringify(
              publicationError
                ? { error: "publication_unavailable", detail: "Restore the workspace" }
                : { error: "no_snapshot", detail: "Build first" },
            ),
            { status: publicationError ? 409 : 404, headers: { "Content-Type": "application/json" } },
          ),
    "GET /sources": { sources: [] },
    "GET /runs": { runs: [] },
    "GET /exports": { exports: [] },
    "GET /samples": [],
    "GET /ops": {
      published_revision: null,
      candidate_revision: null,
      workspace: "org",
      counts: { pending: 0, held: 0, approved: 0, rejected: 0, feedback: 0 },
      highlights: [],
      checks: [],
    },
    "GET /ops/changes": { items: [], feedback: [] },
    "GET /ops/connections": { items: [], counts: { total: 0 }, unapplied: 0, pages: 1, total: 0 },
    "GET /sources/freshness": { sources: [], stale: 0 },
  })
  return {
    ...result,
    finishBuild: () => {
      built = true
    },
  }
}

it("keeps first-use guidance mounted without a remount/refetch loop at the root route", async () => {
  const { calls } = workspace()
  renderPage(<App />)
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100))
  })
  expect(calls.filter((c) => c.path === "/ontology")).toHaveLength(1)
  expect(document.querySelector("#checklist")).toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "Ontology operations" })).toBeNull()
})

it("does not mistake an unavailable published snapshot for a new workspace", async () => {
  workspace(true)
  renderPage(<App />)
  expect(await screen.findByRole("alert")).toHaveTextContent("Restore the workspace")
  expect(document.querySelector("#checklist")).toBeNull()
})

it("keeps root onboarding actionable when the chat CLI is logged out", async () => {
  const { calls } = workspace(false, "logged_out")
  renderPage(<App />)
  expect(await screen.findByText("codex login")).toBeInTheDocument()
  expect(document.querySelector("[data-step=models]")).toHaveAttribute("data-state", "active")
  expect(calls.filter((c) => c.path === "/ontology")).toHaveLength(1)
})

it("keeps the model step incomplete while the backend is unavailable", async () => {
  const { calls } = workspace(false, "backend_down")
  renderPage(<App />)
  await screen.findByText("Models connected")
  await waitFor(() => expect(document.querySelector("[data-step=models]")).toHaveAttribute("data-state", "active"))
  expect(document.querySelector("[data-step=build]")).toHaveAttribute("data-state", "pending")
  expect(calls.filter((c) => c.path === "/ontology")).toHaveLength(1)
})

it("moves from guidance to operations after the first build becomes available", async () => {
  vi.useFakeTimers()
  try {
    const { calls, finishBuild } = workspace()
    await act(async () => {
      renderPage(<App />)
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(document.querySelector("#checklist")).toBeInTheDocument()
    finishBuild()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5050)
    })
    expect(screen.getByRole("heading", { name: "Ontology operations" })).toBeInTheDocument()
    expect(document.querySelector("#checklist")).toBeNull()
    expect(calls.filter((c) => c.path === "/ontology").length).toBeLessThanOrEqual(3)
  } finally {
    vi.useRealTimers()
  }
})
