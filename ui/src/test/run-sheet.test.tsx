import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RunSheet } from "@/components/run-sheet"
import { mockFetch, renderPage } from "@/test/utils"

const RUN = {
  id: "20261027T202922Z-34f08b", kind: "build", source: null, status: "succeeded",
  started_at: "2026-10-27T20:29:22Z", finished_at: null, error: null, params: {}, managed: false,
  steps: [
    { name: "structured:hanbit:load", status: "succeeded", started_at: "2026-10-27T20:29:22Z",
      finished_at: "2026-10-27T20:29:24Z", detail: {} },
    { name: "documents:hanbit-docs:extract", status: "pending", started_at: null, finished_at: null, detail: {} },
    { name: "align:hanbit-docs", status: "succeeded", started_at: null, finished_at: null, detail: {} },
    { name: "snapshot", status: "succeeded", started_at: null, finished_at: null, detail: {} },
  ],
  progress: {},
}

class FakeEventSource {
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener() {}
  close() { this.readyState = FakeEventSource.CLOSED }
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal("EventSource", FakeEventSource)
})

describe("run sheet", () => {
  it("shows an error with a retry when the run cannot be loaded", async () => {
    const user = userEvent.setup()
    let fail = true
    mockFetch({
      [`GET /runs/${RUN.id}`]: () => fail
        ? new Response(JSON.stringify({ error: "internal_error", detail: "boom" }),
                       { status: 500, headers: { "content-type": "application/json" } })
        : RUN,
    })
    renderPage(<RunSheet runId={RUN.id} onClose={() => {}} />)
    expect(await screen.findByText(/Could not load the run: boom/)).toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByText("Load · Succeeded · 2.0 s")).toBeInTheDocument()
    // grouped as the build runs them; a step still pending in a finished run did not run
    expect(screen.getByText("Structured · hanbit")).toBeInTheDocument()
    expect(screen.getByText("Documents · hanbit-docs")).toBeInTheDocument()
    expect(screen.getByText("Extract · not run")).toBeInTheDocument()
    expect(screen.getByText("hanbit-docs · Succeeded")).toBeInTheDocument()
    expect(screen.getByText(/all sources/)).toBeInTheDocument()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("says so when the event stream is refused (CLOSED without end)", async () => {
    mockFetch({ [`GET /runs/${RUN.id}`]: RUN })
    renderPage(<RunSheet runId={RUN.id} onClose={() => {}} />)
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const es = FakeEventSource.instances[0]
    es.readyState = FakeEventSource.CLOSED
    es.onerror?.()
    expect(await screen.findByText(/Could not open the live log stream/)).toBeInTheDocument()
  })
})
