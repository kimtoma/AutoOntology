import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"

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
  listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>()
  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }
  emit(type: string, data: unknown, lastEventId = "") {
    const raw = typeof data === "string" ? data : JSON.stringify(data)
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data: raw, lastEventId }))
  }
  close() { this.readyState = FakeEventSource.CLOSED }
}

async function openStream(run: object) {
  mockFetch({ [`GET /runs/${RUN.id}`]: run })
  renderPage(<RunSheet runId={RUN.id} onClose={() => {}} />)
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
  return FakeEventSource.instances[0]
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

  it("shows the steps of a pending run as pending, not as not run", async () => {
    await openStream({ ...RUN, status: "pending" })
    expect(await screen.findByText("Extract · Pending")).toBeInTheDocument()
    expect(screen.queryByText(/not run/)).toBeNull()
  })

  it("toasts the error of a run that fails while the sheet is open, logging it once", async () => {
    const failed = vi.spyOn(toast, "error")
    const es = await openStream({ ...RUN, status: "running" })
    await screen.findByText("Load · Succeeded · 2.0 s")
    act(() => {
      es.emit("log", "step 1", "10")
      es.emit("step", "{not json")  // a malformed frame is dropped, the stream goes on
      es.emit("status", { status: "failed", error: "align: model timed out" })
      es.emit("status", { status: "failed", error: "align: model timed out" })  // after a reconnect
      es.emit("end", "")
    })
    await waitFor(() => expect(failed).toHaveBeenCalledWith("Build failed", { description: "align: model timed out" }))
    await waitFor(() => expect(document.getElementById("live-log")!.textContent)
      .toBe("step 1\nerror: align: model timed out\n"))
    expect(document.getElementById("live-status")).toHaveAttribute("data-status", "failed")
  })
})
