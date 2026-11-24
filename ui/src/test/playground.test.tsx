import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { CitedText } from "@/components/playground/shared"
import { setLang } from "@/i18n/core"
import { api, parseSse, type SseEvent } from "@/lib/api"
import { sqlAnswerKind, tabulate, toCsv } from "@/lib/playground"
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
const STATUS = {
  status: "ok", version: "0.1.0", kordoc_available: true, service_url: "", checks: {}, doctor: "",
  providers: { chat_provider: "ollama", chat_model: "openai/qwen3:8b", embed_provider: "ollama",
               embed_model: "bge-m3", openai_base_url: "" },
}
const ONTOLOGY = { revision: 2, created_at: "", run_id: "b", workspace: "default", base_iri: "",
                   sources: [], counts: {}, alignment: {} }
const INDEX = { workspace: "default", scope: ["hanbit", "hanbit-docs"], warnings: [], revision: 2,
                index: null, stale: true }
const CHIP = { type: "term", id: "term-t-c", label: "Contracts", link: "/concepts?concept=abc",
               graph_link: "/graph?focus=term-t-c" }
const EVIDENCE_CHIP = { type: "evidence", id: "ev-1", label: "계약관리규정 p. 2", link: "/concepts?concept=abc",
                        graph_link: "/graph?focus=concept-abc" }

function sse(events: [string, unknown][]) {
  const text = events.map(([e, d]) => `event: ${e}\r\ndata: ${JSON.stringify(d)}\r\n\r\n`).join("")
  return new Response(text, { headers: { "content-type": "text/event-stream" } })
}

describe("playground helpers", () => {
  it("classifies an SQL answer like the server does", () => {
    expect(sqlAnswerKind("unconstructable", "", [])).toBe("unconstructable")
    expect(sqlAnswerKind(undefined, "", ["react_sql_agent", "unconstructable_sql_response"])).toBe("unconstructable")
    expect(sqlAnswerKind(undefined, "SELECT 1", ["react_sql_agent"])).toBe("sql")
    expect(sqlAnswerKind(undefined, "", ["react_sql_agent"])).toBe("information")
  })

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

  it("tabulates upstream's executed-SQL envelope: a list of JSON-records strings", () => {
    // sql_execution stores QueryResponse.result (list[str], one JSON blob of records);
    // sql_columns are semantic-layer column ids, never result headers
    const ID = "5a33d98c-4b46-406c-9111-effd0b1eeb2a"
    const one = { columns: ["customer_count"], rows: [[40]] }
    expect(tabulate(['[{"customer_count":40}]'])).toEqual(one)
    expect(tabulate(['[{"customer_count":40}]'], [ID])).toEqual(one)
    expect(tabulate('["[{\\"customer_count\\":40}]"]')).toEqual(one)
    expect(tabulate(['[{"a":1}]', '[{"a":2,"b":"x"}]'])).toEqual({ columns: ["a", "b"], rows: [[1, undefined], [2, "x"]] })
    expect(tabulate([{ n: 1 }, { n: 2 }], [ID])).toEqual({ columns: ["n"], rows: [[1], [2]] })
    expect(tabulate(["[]"])).toEqual({ columns: [], rows: [] })
    expect(tabulate([[1, 2]], [ID])).toEqual({ columns: ["col1", "col2"], rows: [[1, 2]] })
    expect(tabulate(["plain text"])).toEqual({ columns: ["value"], rows: [["plain text"]] })
    const t = tabulate(['[{"customer_count":40}]'], [ID])!
    expect(toCsv(t.columns, t.rows)).toBe("customer_count\r\n40")
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
    // outside the playground (no concept opener) a chip links to the Ontology page
    const chips = screen.getAllByRole("link", { name: /Contracts/ })
    expect(chips[0]).toHaveAttribute("href", "/ontology?concept=term-t-c")
    expect(screen.getAllByRole("link", { name: "Show Contracts in the graph" })[0])
      .toHaveAttribute("href", "/graph?focus=term-t-c")
    expect(screen.getByText(/<b>약정<\/b>/)).toBeInTheDocument()
    expect(screen.getByText("[E9]")).toBeInTheDocument()
  })

  it("keeps markup in an answer as text", () => {
    const { container } = renderPage(
      <CitedText text={'<script>window.pwned = 1</script><img src=x onerror="window.pwned=1"> [E1]'}
                 citations={{ E1: CHIP }} />)
    expect(container.querySelector("script, img")).toBeNull()
    expect(screen.getByText(/<script>window.pwned = 1<\/script>/)).toBeInTheDocument()
  })

  it("omits the graph link of an element that is not a graph node", () => {
    renderPage(<CitedText text="값 [E1]" citations={{ E1: { ...CHIP, graph_link: "" } }} />)
    expect(screen.getByRole("link", { name: /Contracts/ })).toHaveAttribute("href", "/ontology?concept=term-t-c")
    expect(screen.queryByRole("link", { name: "Show Contracts in the graph" })).toBeNull()
  })

  it("streams a POST SSE endpoint and raises ApiError before the stream", async () => {
    mockFetch({
      "POST /playground/chat": () => sse([["delta", { text: "a" }], ["done", {}]]),
      "POST /playground/sql": () => new Response(JSON.stringify({ error: "playground_busy", detail: "busy" }),
                                                 { status: 409, headers: { "content-type": "application/json" } }),
    })
    const got: string[] = []
    await api.playground.chat({ question: "q" }, (e) => got.push(e.event))
    expect(got).toEqual(["delta", "done"])
    const err = await api.playground.sql({ question: "q" }, () => {}).catch((e) => e)
    expect(err).toMatchObject({ status: 409, code: "playground_busy", message: "busy" })
  })

  it("treats a stream that ends without done/error as truncated, never as success", async () => {
    mockFetch({ "POST /playground/chat": () => sse([["delta", { text: "부분" }]]) })
    const got: string[] = []
    const err = await api.playground.chat({ question: "q" }, (e) => got.push(e.event))
      .catch((e) => e)
    expect(got).toEqual(["delta", "stream_truncated"])
    expect(err).toMatchObject({ status: 502, code: "stream_truncated" })
  })

  it("accepts an error event as a terminal event", async () => {
    mockFetch({ "POST /playground/chat": () => sse([["error", { error: "x", detail: "d" }]]) })
    const got: string[] = []
    await api.playground.chat({ question: "q" }, (e) => got.push(e.event))
    expect(got).toEqual(["error"])
  })
})

describe("playground page", () => {
  const BASE = {
    "GET /sources": SOURCES,
    "GET /status": STATUS,
    "GET /ontology": ONTOLOGY,
    "GET /playground/index": INDEX,
  }

  it("has exactly the Chat, SQL and SPARQL tabs", async () => {
    mockFetch(BASE)
    renderPage(<PlaygroundPage />, "/playground")
    const tabs = await screen.findAllByRole("tab")
    expect(tabs.map((t) => t.textContent?.trim())).toEqual(["Chat", "SQL", "SPARQL"])
    expect(await screen.findByText(/ollama · openai\/qwen3:8b/)).toBeInTheDocument()
  })

  it("asks the ontology, renders the cited answer, and a citation opens the concept sheet", async () => {
    const { calls } = mockFetch({
      ...BASE,
      "POST /playground/chat": () => sse([
        ["status", { phase: "index", provider: "ollama", model: "qwen3", scope: [] }],
        ["retrieved", { elements: [{ ref: "E1", score: 0.81, via: "retrieval", ...CHIP }], index: {} }],
        ["delta", { text: "계약은 고객과의 " }], ["delta", { text: "약정입니다 [E1][E2]." }],
        ["citations", { citations: { E1: CHIP, E2: EVIDENCE_CHIP }, unknown: [] }],
        ["done", { conversation_id: "c".repeat(32), message_id: 2 }],
      ]),
      "GET /ontology/concepts/concept-abc": {
        id: "concept-abc", name: "계약", kind: "entity", origin: "document", source: "hanbit-docs",
        definition: "", synonyms: [], merged_concepts: [], folded_into: { id: "term-t-c", name: "Contracts" },
        enriched_by: "", evidence_count: 1, alignment: { counts: {}, best: null }, revision: 2,
        description: "", definitions: [], tables: [], individual_type: null, attributes: [], relations: [],
        alignments: [], evidence: [{ quote: "계약은 승인 후 체결", verified: true, page: 2, document: "규정", path: "" }],
      },
    })
    renderPage(<PlaygroundPage />, "/playground")
    expect(await screen.findByText(/Scope: revision 2 · hanbit \+ hanbit-docs/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Question"), "계약이란?")
    await userEvent.click(screen.getByRole("button", { name: /Send/ }))
    const answer = await screen.findByText(/약정입니다/)
    const bubble = answer.closest("[data-role=assistant]") as HTMLElement
    await waitFor(() => expect(within(bubble).getAllByRole("button", { name: /계약관리규정/ }).length).toBeGreaterThan(0))
    const body = JSON.parse(String(calls.find((c) => c.path === "/playground/chat")!.init!.body))
    expect(body).toEqual({ question: "계약이란?", conversation_id: null })
    await userEvent.click(within(bubble).getAllByRole("button", { name: /계약관리규정/ })[0])
    const sheet = await screen.findByRole("dialog")
    expect(await within(sheet).findByText("“계약은 승인 후 체결”")).toBeInTheDocument()
    expect(within(sheet).getByText(/Folded into the Term/)).toBeInTheDocument()
  })

  it("marks a truncated answer as incomplete and shows the error", async () => {
    mockFetch({ ...BASE, "POST /playground/chat": () => sse([["delta", { text: "계약은 고객과" }]]) })
    renderPage(<PlaygroundPage />, "/playground")
    await userEvent.type(await screen.findByLabelText("Question"), "계약이란?")
    await userEvent.click(screen.getByRole("button", { name: /Send/ }))
    expect(await screen.findByText("Incomplete answer")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent(/ended without a completion marker/)
  })

  it("runs a SPARQL query on the snapshot and shows the rows", async () => {
    const { calls } = mockFetch({
      ...BASE,
      "GET /playground/sparql/examples": { examples: [
        { id: "classes", title: "Classes", description: "All classes", query: "SELECT ?c WHERE { ?c a owl:Class }" }] },
      "POST /playground/sparql": { type: "SELECT", columns: ["c"], rows: [["ao:term-1"], ["ao:term-2"]],
                                   total: 2, truncated: false, triples: 10, revision: 2, ttl: "ontology.ttl" },
    })
    renderPage(<PlaygroundPage />, "/playground?tab=sparql")
    await userEvent.click(await screen.findByRole("button", { name: /Run/ }))
    expect(await screen.findByText("ao:term-2")).toBeInTheDocument()
    expect(screen.getByText(/ontology.ttl/)).toBeInTheDocument()
    const body = JSON.parse(String(calls.find((c) => c.path === "/playground/sparql")!.init!.body))
    expect(Object.keys(body).sort()).toEqual(["limit", "query"])
  })

  it("renders an SQL result from upstream's envelope with its own headers and a localized notice", async () => {
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => sse([
        ["meta", { target_db: "db1", notices: ["the upstream conversation store (frontend schema) is missing: asking without the earlier turns"],
                   notice_codes: ["conversation_store_missing"], stateless: true }],
        ["result", { type: "result", answer: {
          response: "Counts customers.", sql_code: "SELECT COUNT(*) AS customer_count FROM customers",
          sql_columns: ["5a33d98c-4b46-406c-9111-effd0b1eeb2a"],
          sql_response_from_db: ['[{"customer_count":40}]'] } }],
        ["done", {}],
      ]),
    })
    renderPage(<PlaygroundPage />, "/playground?tab=sql")
    await userEvent.type(await screen.findByLabelText("Data question"), "고객은 몇 명인가요?")
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }))
    const result = await waitFor(() => {
      const el = document.getElementById("sql-result")
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(within(result).getByRole("columnheader")).toHaveTextContent("customer_count")
    expect(within(result).getByRole("cell")).toHaveTextContent(/^40$/)
    expect(result).not.toHaveTextContent("5a33d98c")
    expect(screen.getByText(/conversation history is not stored upstream/)).toBeInTheDocument()
  })

  const NO_SQL = { sql_code: "", sql_columns: [], custom_analyses_used: [], sql_response_from_db: null }
  const askSql = async (label: string, button: RegExp, q: string) => {
    renderPage(<PlaygroundPage />, "/playground?tab=sql")
    await userEvent.type(await screen.findByLabelText(label), q)
    await userEvent.click(screen.getByRole("button", { name: button }))
  }

  it("shows an informational answer without SQL sections, with Korean step labels", async () => {
    setLang("ko", false)
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => sse([
        ["step", { type: "step", phase: "start", node: "react_sql_agent", label: "Searching the data and writing SQL" }],
        ["step", { type: "step", phase: "end", node: "react_sql_agent", label: "Searching the data and writing SQL", thought: "Answering an information question." }],
        ["result", { type: "result", answer_kind: "information", answer: { ...NO_SQL, response: "연체는 납기 30일 후입니다." } }],
        ["done", {}],
      ]),
    })
    await askSql("데이터 질문", /질문하기/, "연체 규칙은?")
    const card = await waitFor(() => {
      const el = document.getElementById("sql-answer")
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(card).toHaveTextContent("연체는 납기 30일 후입니다.")
    expect(card).toHaveTextContent("SQL 없이 답했습니다(규칙·정의 질문).")
    expect(screen.getByText("데이터를 찾아 SQL 작성 중")).toBeInTheDocument()
    expect(screen.queryByText("Searching the data and writing SQL")).toBeNull()
    expect(document.getElementById("sql-code")).toBeNull()
    expect(document.getElementById("sql-result")).toBeNull()
  })

  it("shows the elapsed time while the long ReAct step runs", async () => {
    let finish: () => void = () => {}
    const enc = new TextEncoder()
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => new Response(new ReadableStream({
        start(ctrl) {
          ctrl.enqueue(enc.encode(`event: step\ndata: ${JSON.stringify({ type: "step", phase: "start", node: "react_sql_agent", label: "x" })}\n\n`))
          finish = () => {
            ctrl.enqueue(enc.encode("event: done\ndata: {}\n\n"))
            ctrl.close()
          }
        },
      }), { headers: { "content-type": "text/event-stream" } }),
    })
    await askSql("Data question", /Ask/, "q")
    expect(await screen.findByText("Searching the data and writing SQL")).toBeInTheDocument()
    expect(document.getElementById("sql-elapsed")).toHaveTextContent(/^\d+s$/)
    expect(screen.getByText(/searches the catalog, tries queries/)).toBeInTheDocument()
    finish()
    await waitFor(() => expect(document.getElementById("sql-elapsed")).toBeNull())
  })

  it("explains an unconstructable answer", async () => {
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => sse([
        ["step", { type: "step", phase: "start", node: "unconstructable_sql_response", label: "SQL could not be constructed" }],
        ["step", { type: "step", phase: "end", node: "unconstructable_sql_response" }],
        ["result", { type: "result", answer_kind: "unconstructable", answer: { ...NO_SQL, response: "I couldn't write a query." } }],
        ["done", {}],
      ]),
    })
    await askSql("Data question", /Ask/, "날씨는?")
    const card = await waitFor(() => {
      const el = document.getElementById("sql-answer")
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(card).toHaveAttribute("data-kind", "unconstructable")
    expect(within(card).getByText("No SQL for this question")).toBeInTheDocument()
    expect(card).toHaveTextContent("I couldn't write a query.")
    expect(card).toHaveTextContent(/name the table or measure/)
    expect(document.getElementById("sql-code")).toBeNull()
  })

  it("shows the Korean translation with the English original collapsed", async () => {
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => sse([
        ["result", { type: "result", answer_kind: "sql", answer: {
          response: "There are 40 customers.", sql_code: "SELECT COUNT(*) AS n FROM customers",
          sql_columns: [], sql_response_from_db: ['[{"n":40}]'] } }],
        ["translation", { type: "translation", phase: "start", target: "ko" }],
        ["translation", { type: "translation", phase: "end", answer: { response_ko: "고객은 40명입니다." } }],
        ["done", {}],
      ]),
    })
    await askSql("Data question", /Ask/, "고객은 몇 명인가요?")
    const card = await waitFor(() => {
      const el = document.getElementById("sql-answer")
      expect(el).toHaveTextContent("고객은 40명입니다.")
      return el as HTMLElement
    })
    const original = card.querySelector("details") as HTMLDetailsElement
    expect(original).not.toBeNull()
    expect(original.open).toBe(false)
    expect(within(original).getByText("Original (English)")).toBeInTheDocument()
    expect(original).toHaveTextContent("There are 40 customers.")
    expect(card).not.toHaveTextContent("Answered without SQL")
    expect(document.getElementById("sql-code")).toHaveTextContent("SELECT COUNT(*)")
    expect(screen.queryByText("Translating the answer into Korean…")).toBeNull()
  })

  it("keeps the English answer when the translation is skipped", async () => {
    mockFetch({
      ...BASE,
      "POST /playground/sql": () => sse([
        ["result", { type: "result", answer: { ...NO_SQL, response: "Late after 30 days." } }],
        ["translation", { type: "translation", phase: "start", target: "ko" }],
        ["translation", { type: "translation", phase: "end", skipped: true }],
        ["done", {}],
      ]),
    })
    await askSql("Data question", /Ask/, "연체 규칙은?")
    const card = await waitFor(() => {
      const el = document.getElementById("sql-answer")
      expect(el).toHaveTextContent("Late after 30 days.")
      return el as HTMLElement
    })
    expect(card.querySelector("details")).toBeNull()
    expect(card).toHaveTextContent("Answered without SQL (a rule or definition question).")
  })

  it("sends to Build when there is no snapshot", async () => {
    mockFetch({ ...BASE, "GET /ontology": () => new Response(
      JSON.stringify({ error: "no_snapshot", detail: "no" }),
      { status: 404, headers: { "content-type": "application/json" } }) })
    renderPage(<PlaygroundPage />, "/playground")
    expect(await screen.findByText("No ontology yet")).toBeInTheDocument()
  })
})
