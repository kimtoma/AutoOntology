import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { CitedText } from "@/components/playground/shared"
import { api, parseSse, type SseEvent } from "@/lib/api"
import { tabulate, toCsv } from "@/lib/playground"
import PlaygroundPage from "@/pages/playground"
import { mockFetch, renderPage } from "@/test/utils"

const SOURCES = {
  sources: [
    { name: "hanbit", kind: "files", locator: "/s", database: "autoontology_src_hanbit", built: true,
      uploaded: false, created_at: "", last_run: null, active_run: null },
    { name: "hanbit-docs", kind: "documents", locator: "/d", database: null, built: false,
      uploaded: false, created_at: "", last_run: null, active_run: null, documents: 5, concepts: 85 },
  ],
}
const PROVIDERS = {
  selection: { chat_provider: "claude", chat_model: "sonnet", embed_provider: "ollama", origin: {} },
  settings_file: {}, choices: { chat_provider: [], embed_provider: [] }, providers: [],
  applied: { chat_provider: "claude", chat_model: "sonnet", embed_provider: "ollama" },
  restart_required: false,
}
const CHIP = { type: "term", id: "term-t-c", label: "Contracts", link: "/concepts?concept=abc",
               graph_link: "/graph?focus=term-t-c" }

function sse(events: [string, unknown][]) {
  const text = events.map(([e, d]) => `event: ${e}\r\ndata: ${JSON.stringify(d)}\r\n\r\n`).join("")
  return new Response(text, { headers: { "content-type": "text/event-stream" } })
}

describe("playground helpers", () => {
  it("parses SSE across chunk boundaries", () => {
    const got: SseEvent[] = []
    let tail = parseSse('event: delta\ndata: {"text":"안', (e) => got.push(e))
    expect(got).toEqual([])
    tail = parseSse(tail + '녕"}\n\nevent: done\r\ndata: {}\r\n\r\n: ping\n\n', (e) => got.push(e))
    expect(got).toEqual([{ event: "delta", data: { text: "안녕" } }, { event: "done", data: {} }])
    expect(tail).toBe("")
  })

  it("tabulates upstream results of any shape and writes CSV", () => {
    expect(tabulate([[3]], ["count"])).toEqual({ columns: ["count"], rows: [[3]] })
    expect(tabulate('[{"a": 1, "b": "x"}]')).toEqual({ columns: ["a", "b"], rows: [[1, "x"]] })
    expect(tabulate("not json")).toBeNull()
    expect(tabulate([], ["a"])).toEqual({ columns: ["a"], rows: [] })
    expect(toCsv(["a", "b"], [["x,y", 'q"'], [null, { k: 1 }]])).toBe('a,b\r\n"x,y","q"""\r\n,"{""k"":1}"')
  })

  it("neutralizes spreadsheet formulas in CSV cells and headers, keeps numbers", () => {
    expect(toCsv(["label"], [["=1+1"]])).toBe("label\r\n'=1+1")
    expect(toCsv(["=HYPERLINK(\"x\")", "b"], [["+cmd", "-2+3"], ["@SUM(A1)", "\tx"]]))
      .toBe('"\'=HYPERLINK(""x"")",b\r\n\'+cmd,\'-2+3\r\n\'@SUM(A1),\'\tx')
    expect(toCsv(["v"], [["\r=1"]])).toBe('v\r\n"\'\r=1"')
    expect(toCsv(["n", "s", "t"], [[-5, "-12.5", "+3"], [1.5e3, "계약 = 약정", "a=b"]]))
      .toBe("n,s,t\r\n-5,-12.5,+3\r\n1500,계약 = 약정,a=b")
  })

  it("renders citation tokens as chips, text escaped", () => {
    renderPage(<CitedText text={'계약은 <b>약정</b> [E1]. 모름 [E1, E9]'} citations={{ E1: CHIP }} />)
    const chips = screen.getAllByRole("link", { name: /Contracts/ })
    expect(chips[0]).toHaveAttribute("href", "/concepts?concept=abc")
    expect(screen.getAllByRole("link", { name: "Contracts 그래프에서 보기" })[0])
      .toHaveAttribute("href", "/graph?focus=term-t-c")
    expect(screen.getByText(/<b>약정<\/b>/)).toBeInTheDocument()
    expect(screen.getByText("[E9]")).toBeInTheDocument()
  })

  it("omits the graph link of an element that is not a graph node", () => {
    renderPage(<CitedText text="값 [E1]" citations={{ E1: { ...CHIP, graph_link: "" } }} />)
    expect(screen.getByRole("link", { name: /Contracts/ })).toHaveAttribute("href", "/concepts?concept=abc")
    expect(screen.queryByRole("link", { name: "Contracts 그래프에서 보기" })).toBeNull()
  })

  it("streams a POST SSE endpoint and raises ApiError before the stream", async () => {
    mockFetch({
      "POST /playground/chat": () => sse([["delta", { text: "a" }], ["done", {}]]),
      "POST /playground/tests/run": () => new Response(JSON.stringify({ error: "playground_busy", detail: "busy" }),
                                                      { status: 409, headers: { "content-type": "application/json" } }),
    })
    const got: string[] = []
    await api.playground.chat({ source: "s", question: "q" }, (e) => got.push(e.event))
    expect(got).toEqual(["delta", "done"])
    const err = await api.playground.runTests({ source: "s" }, () => {}).catch((e) => e)
    expect(err).toMatchObject({ status: 409, code: "playground_busy", message: "busy" })
  })

  it("treats a stream that ends without done/error as truncated, never as success", async () => {
    mockFetch({ "POST /playground/chat": () => sse([["delta", { text: "부분" }]]) })
    const got: string[] = []
    const err = await api.playground.chat({ source: "s", question: "q" }, (e) => got.push(e.event))
      .catch((e) => e)
    expect(got).toEqual(["delta", "stream_truncated"])
    expect(err).toMatchObject({ status: 502, code: "stream_truncated" })
  })

  it("accepts an error event as a terminal event", async () => {
    mockFetch({ "POST /playground/chat": () => sse([["error", { error: "x", detail: "d" }]]) })
    const got: string[] = []
    await api.playground.chat({ source: "s", question: "q" }, (e) => got.push(e.event))
    expect(got).toEqual(["error"])
  })
})

describe("playground page", () => {
  it("shows the provider, asks the ontology and renders the cited answer", async () => {
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "GET /providers": PROVIDERS,
      "GET /playground/index": { source: "hanbit-docs", scope: ["hanbit", "hanbit-docs"], warnings: [],
                                 revision: 3, index: null, stale: true },
      "POST /playground/chat": () => sse([
        ["status", { phase: "index", provider: "claude", model: "sonnet", scope: [] }],
        ["retrieved", { elements: [{ ref: "E1", score: 0.81, via: "retrieval", ...CHIP }], index: {} }],
        ["delta", { text: "계약은 고객과의 " }], ["delta", { text: "약정입니다 [E1]." }],
        ["citations", { citations: { E1: CHIP }, unknown: [] }],
        ["done", { conversation_id: "c".repeat(32), message_id: 2 }],
      ]),
    })
    renderPage(<PlaygroundPage />, "/playground")
    expect(await screen.findByText(/claude · sonnet/)).toBeInTheDocument()
    expect(await screen.findByText(/범위: hanbit \+ hanbit-docs/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("질문"), "계약이란?")
    await userEvent.click(screen.getByRole("button", { name: /보내기/ }))
    const answer = await screen.findByText(/약정입니다/)
    const bubble = answer.closest("[data-role=assistant]") as HTMLElement
    await waitFor(() => expect(within(bubble).getAllByRole("link", { name: /Contracts/ }).length).toBeGreaterThan(0))
    const body = JSON.parse(String(calls.find((c) => c.path === "/playground/chat")!.init!.body))
    expect(body).toMatchObject({ source: "hanbit-docs", question: "계약이란?", conversation_id: null })
  })

  it("marks a truncated answer as incomplete and shows the error", async () => {
    mockFetch({
      "GET /sources": SOURCES,
      "GET /providers": PROVIDERS,
      "GET /playground/index": { source: "hanbit-docs", scope: ["hanbit-docs"], warnings: [],
                                 revision: 3, index: null, stale: true },
      "POST /playground/chat": () => sse([["delta", { text: "계약은 고객과" }]]),
    })
    renderPage(<PlaygroundPage />, "/playground")
    await userEvent.type(await screen.findByLabelText("질문"), "계약이란?")
    await userEvent.click(screen.getByRole("button", { name: /보내기/ }))
    expect(await screen.findByText("미완료 답변")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent(/끊겼습니다/)
  })

  it("runs an example SPARQL query and shows the rows", async () => {
    mockFetch({
      "GET /sources": SOURCES,
      "GET /providers": PROVIDERS,
      "GET /playground/sparql/examples": { examples: [
        { id: "classes", title: "클래스 목록", description: "모든 클래스", query: "SELECT ?c WHERE { ?c a owl:Class }" }] },
      "POST /playground/sparql": { type: "SELECT", columns: ["c"], rows: [["os:term-1"], ["os:term-2"]],
                                   total: 2, truncated: false, export: "20261028T000000Z-abcdef", triples: 10 },
    })
    renderPage(<PlaygroundPage />, "/playground?tab=sparql&source=hanbit-docs")
    await userEvent.click(await screen.findByRole("button", { name: /실행/ }))
    expect(await screen.findByText("os:term-2")).toBeInTheDocument()
    expect(screen.getByText(/20261028T000000Z-abcdef/)).toBeInTheDocument()
  })
})
