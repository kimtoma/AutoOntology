import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RunSheet } from "@/components/run-sheet"
import { mockFetch, renderPage } from "@/test/utils"

const RUN = {
  id: "20261027T202922Z-34f08b", kind: "build", source: "hanbit", status: "succeeded",
  started_at: "2026-10-27T20:29:22Z", finished_at: null, error: null, params: {}, managed: false,
  steps: [{ name: "load", status: "succeeded", started_at: null, finished_at: null, detail: {} }],
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
    expect(await screen.findByText(/실행 정보를 불러오지 못했습니다: boom/)).toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole("button", { name: "다시 시도" }))
    expect(await screen.findByText("적재 · 성공")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("says so when the event stream is refused (CLOSED without end)", async () => {
    mockFetch({ [`GET /runs/${RUN.id}`]: RUN })
    renderPage(<RunSheet runId={RUN.id} onClose={() => {}} />)
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const es = FakeEventSource.instances[0]
    es.readyState = FakeEventSource.CLOSED
    es.onerror?.()
    expect(await screen.findByText(/실시간 로그 스트림을 열지 못했습니다/)).toBeInTheDocument()
  })
})
