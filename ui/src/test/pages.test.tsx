import { act, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"

import BuildPage from "@/pages/build"
import ExportsPage from "@/pages/exports"
import OntologyPage from "@/pages/ontology"
import SourcesPage from "@/pages/sources"
import StatusPage from "@/pages/status"
import { mockFetch, renderPage, renderRouted } from "@/test/utils"

const SOURCES = {
  sources: [
    { name: "hanbit", kind: "files", locator: "/data/structured", database: "autoontology_src_hanbit",
      built: true, uploaded: false, created_at: "2026-10-27T20:29:21Z",
      last_run: { id: "r1", kind: "build", status: "succeeded", finished_at: "2026-10-27T20:30:00Z" },
      active_run: null },
    { name: "hanbit-docs", kind: "documents", locator: "/data/docs", database: null, built: false,
      uploaded: false, created_at: "2026-10-27T18:14:36Z", last_run: null, active_run: null,
      documents: 5, concepts: 85 },
  ],
}

const STATUS = {
  status: "ok", version: "0.1.0", kordoc_available: false, service_url: "http://127.0.0.1:7810",
  providers: { chat_provider: "ollama", chat_model: "openai/qwen3:8b", embed_provider: "ollama",
               embed_model: "bge-m3", openai_base_url: "" },
  checks: { backend: { status: "ok", detail: "http://127.0.0.1:3111" },
            postgres: { status: "error", detail: "OperationalError", remedy: "docker compose up -d" } },
  doctor: "uv run autoontology doctor", upload_max_mb: 1,
}

const ONTOLOGY = {
  revision: 2, created_at: "2026-10-28T11:11:30+00:00", run_id: "b", workspace: "default",
  base_iri: "http://example.com/autoontology#",
  sources: [{ name: "hanbit", kind: "structured" }, { name: "hanbit-docs", kind: "documents" }],
  counts: { classes_structured: 3, classes_document: 58, individuals: 24, object_properties: 69,
            merged_concepts: 3, evidence: 304 },
  alignment: { same: 3 },
}

const ITEM = (id: string, origin: string, extra: object = {}) => ({
  id, name: id === "term-c" ? "Contracts" : "계약상태", kind: id.startsWith("term") ? "term" : "entity",
  origin, source: id.startsWith("term") ? "hanbit" : "hanbit-docs", definition: "정의", synonyms: ["계약"],
  merged_concepts: [], folded_into: null, enriched_by: "", evidence_count: 2,
  alignment: { counts: {}, best: null }, ...extra,
})

const no_snapshot = () => new Response(JSON.stringify({ error: "no_snapshot", detail: "no ontology yet" }),
                                       { status: 404, headers: { "content-type": "application/json" } })

describe("Sources page", () => {
  it("lists both kinds with a kind badge and state", async () => {
    mockFetch({ "GET /sources": SOURCES })
    renderPage(<SourcesPage />)
    const table = await screen.findByRole("table")
    expect(within(table).getByText("hanbit")).toBeInTheDocument()
    expect(within(table).getByText("autoontology_src_hanbit")).toBeInTheDocument()
    expect(table.querySelector("[data-source=hanbit] [data-kind=structured]")).not.toBeNull()
    expect(table.querySelector("[data-source=hanbit-docs] [data-kind=documents]")).not.toBeNull()
    expect(within(table).getAllByText("5 documents · 85 concepts").length).toBeGreaterThan(0)
  })

  it("shows the empty state with the next action", async () => {
    mockFetch({ "GET /sources": { sources: [] } })
    renderPage(<SourcesPage />)
    expect(await screen.findByText("No sources yet")).toBeInTheDocument()
  })

  it("deletes with drop_db only when the switch is on", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "DELETE /sources/hanbit": { removed: "hanbit", messages: [] },
    })
    renderPage(<><SourcesPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "Delete hanbit" }))
    await user.click(await screen.findByRole("switch"))
    await user.click(screen.getByRole("button", { name: "Delete" }))
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true))
    const del = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "DELETE")
    expect(String(del?.[0])).toBe("/api/sources/hanbit?drop_db=true")
  })

  it("picks the kind in the add dialog, with the kordoc hint for documents", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "GET /status": STATUS,
      "POST /sources": (_u: string, init?: RequestInit) => ({ ...SOURCES.sources[1], ...JSON.parse(String(init?.body)) }),
    })
    renderPage(<><SourcesPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "Add source" }))
    await screen.findByText(/up to 1 MB per upload/)
    expect(screen.getByRole("tab", { name: /DB URL/ })).toBeInTheDocument()
    await user.click(screen.getByRole("tab", { name: /Documents/ }))
    expect(screen.queryByRole("tab", { name: /DB URL/ })).toBeNull()
    expect(screen.getByText(/need kordoc/)).toBeInTheDocument()
    await user.click(screen.getByRole("tab", { name: "Folder" }))
    await user.type(screen.getByLabelText("Name"), "docs-2")
    await user.type(screen.getByLabelText("Folder or file path"), "/data/docs")
    await user.click(screen.getByRole("button", { name: "Add" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true))
    const post = calls.find((c) => c.method === "POST")!
    expect(JSON.parse(String(post.init?.body))).toEqual(
      { name: "docs-2", kind: "documents", locator: "/data/docs", recursive: false })
  })

  it("clears the add form on Cancel", async () => {
    const user = userEvent.setup()
    mockFetch({ "GET /sources": SOURCES, "GET /status": STATUS })
    renderPage(<SourcesPage />)
    await user.click(await screen.findByRole("button", { name: "Add source" }))
    await user.click(await screen.findByRole("tab", { name: /Documents/ }))
    await user.type(screen.getByLabelText("Name"), "draft")
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    await user.click(screen.getByRole("button", { name: "Add source" }))
    expect(await screen.findByLabelText("Name")).toHaveValue("")
    expect(screen.getByRole("tab", { name: /Structured/ })).toHaveAttribute("aria-selected", "true")
  })

  it("refuses an upload over the limit before sending it", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({ "GET /sources": SOURCES, "GET /status": STATUS })
    renderPage(<SourcesPage />)
    await user.click(await screen.findByRole("button", { name: "Add source" }))
    expect(await screen.findByText(/up to 1 MB per upload/)).toBeInTheDocument()
    await user.type(screen.getByLabelText("Name"), "big")
    const big = new File([new Uint8Array(2 * 1024 * 1024)], "big.csv", { type: "text/csv" })
    await user.upload(screen.getByLabelText("Files"), big)
    expect(screen.getByText(/2.0 MB total/)).toHaveClass("text-destructive")
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled()
    expect(calls.some((c) => c.method === "POST")).toBe(false)
  })
})

describe("Build page", () => {
  class FakeEventSource {
    static CLOSED = 2
    readyState = 0
    onopen = null
    onerror = null
    addEventListener() {}
    close() {}
  }

  it("starts a build and an export with the unified bodies", async () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "GET /ontology": ONTOLOGY,
      "GET /runs": { runs: [] },
      "POST /runs": { run_id: "20261028T000000Z-abcdef" },
      "GET /runs/20261028T000000Z-abcdef": {
        id: "20261028T000000Z-abcdef", kind: "build", source: null, status: "running", started_at: null,
        finished_at: null, error: null, params: { sources: null }, managed: true, progress: {},
        steps: [{ name: "structured:hanbit:load", status: "succeeded", started_at: null, finished_at: null, detail: {} },
                { name: "align:hanbit-docs", status: "pending", started_at: null, finished_at: null, detail: {} },
                { name: "merge", status: "pending", started_at: null, finished_at: null, detail: {} }],
      },
    })
    renderPage(<BuildPage />, "/build")
    expect(await screen.findByText("No runs yet")).toBeInTheDocument()
    await user.click(await screen.findByRole("button", { name: /Build ontology/ }))
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1))
    // the run sheet opens with the steps grouped
    const steps = await screen.findByRole("list", { name: "Steps" })
    expect(steps.querySelector("[data-group='structured:hanbit']")).not.toBeNull()
    expect(within(steps).getByText("Structured · hanbit")).toBeInTheDocument()
    expect(steps.querySelector("[data-group=align]")).not.toBeNull()
    expect(steps.querySelector("[data-group=merge]")).not.toBeNull()
    await user.keyboard("{Escape}")
    await user.click(await screen.findByRole("button", { name: /^Export$/ }))
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2))
    expect(calls.filter((c) => c.method === "POST").map((c) => JSON.parse(String(c.init?.body))))
      .toEqual([{ kind: "build" }, { kind: "export" }])
  })
})

describe("Ontology page", () => {
  it("shows the snapshot header and the unified list with origin badges", async () => {
    mockFetch({
      "GET /ontology": ONTOLOGY,
      "GET /ontology/concepts": { revision: 2, total: 2, page: 1, size: 30, pages: 1,
        items: [ITEM("term-c", "aligned", { merged_concepts: ["계약"] }), ITEM("concept-x", "document")] },
    })
    renderPage(<OntologyPage />, "/ontology")
    expect(await screen.findByText("Revision 2")).toBeInTheDocument()
    expect(screen.getByText("Workspace default")).toBeInTheDocument()
    const list = await screen.findByRole("list")
    expect(list.querySelector("[data-concept=term-c][data-origin=aligned]")).not.toBeNull()
    expect(within(list).getByText("Aligned")).toBeInTheDocument()
    expect(within(list).getByText("계약상태")).toBeInTheDocument()
    expect(within(list).getByText("Merged: 계약")).toBeInTheDocument()
  })

  it("filters by origin through the URL and follows a ⌘K search", async () => {
    mockFetch({
      "GET /ontology": ONTOLOGY,
      "GET /ontology/concepts": { revision: 2, total: 0, page: 1, size: 30, pages: 1, items: [] },
    })
    const { router } = renderRouted(<OntologyPage />, "/ontology?origin=structured&q=계약")
    const input = await screen.findByRole("searchbox", { name: "Search concepts" })
    expect(input).toHaveValue("계약")
    await screen.findByText("No matching concepts")
    const url = vi.mocked(fetch).mock.calls.map(([u]) => String(u)).find((u) => u.includes("/concepts?"))!
    expect(new URL(url, "http://x").searchParams.get("origin")).toBe("structured")
    await act(() => router.navigate("/ontology?q=고객"))
    await waitFor(() => expect(input).toHaveValue("고객"))
  })

  it("moves a stale ?page past the end to the last page", async () => {
    mockFetch({
      "GET /ontology": ONTOLOGY,
      "GET /ontology/concepts": (url: string) => {
        const page = Number(new URL(url, "http://x").searchParams.get("page"))
        return { revision: 2, total: 31, page, size: 30, pages: 2,
                 items: page === 2 ? [ITEM("term-c", "aligned")] : [] }
      },
    })
    const { router } = renderRouted(<OntologyPage />, "/ontology?page=5")
    await waitFor(() => expect(router.state.location.search).toBe("?page=2"))
    expect(await screen.findByText("2 / 2")).toBeInTheDocument()
    await waitFor(() => expect(document.querySelector("[data-concept=term-c]")).not.toBeNull())
  })

  it("links to Build when there is no snapshot yet", async () => {
    mockFetch({ "GET /ontology": no_snapshot, "GET /ontology/concepts": no_snapshot })
    renderPage(<OntologyPage />, "/ontology")
    expect(await screen.findByText("No ontology yet")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Go to Build/ })).toHaveAttribute("href", "/build")
  })

  it("opens the concept sheet with evidence and a graph link", async () => {
    mockFetch({
      "GET /ontology": ONTOLOGY,
      "GET /ontology/concepts": { revision: 2, total: 0, page: 1, size: 30, pages: 1, items: [] },
      "GET /ontology/concepts/term-c": {
        ...ITEM("term-c", "aligned", { merged_concepts: ["계약"] }), revision: 2, description: "",
        definitions: ["계약은 승인 후 체결한다."], tables: ["public.contracts"], individual_type: null,
        attributes: [{ id: "a", label: "계약금액", range: "xsd:decimal", source: "structured",
                       column: "계약금액", datatype: "numeric", description: "" }],
        relations: [{ id: "fk", direction: "out", predicate: "semantic_fk", label: "customers",
                      other: "term-d", other_label: "Customers", source: "semantic_fk" }],
        alignments: [{ relation: "same", with: "concept-a", label: "계약", confidence: 0.95, accepted: true }],
        evidence: [{ quote: "계약은 담당자가 작성한다", verified: true, page: 2, document: "계약관리규정", path: "x.md" }],
      },
    })
    renderPage(<OntologyPage />, "/ontology?concept=term-c")
    const sheet = await screen.findByRole("dialog")
    expect(await within(sheet).findByText("“계약은 담당자가 작성한다”")).toBeInTheDocument()
    expect(within(sheet).getByText("public.contracts")).toBeInTheDocument()
    expect(within(sheet).getByText("Same as")).toBeInTheDocument()
    expect(within(sheet).getByRole("link", { name: /Show in graph/ })).toHaveAttribute("href", "/graph?focus=term-c")
  })
})

describe("Exports page", () => {
  it("lists the revision folder with ossie/ and okf/ groups and validation badges", async () => {
    const user = userEvent.setup()
    mockFetch({
      "GET /exports": { exports: [{
        revision: 2, dir: "r0002", created_at: "2026-10-28T12:06:24+00:00",
        files: [{ name: "ontology.ttl", bytes: 100 }, { name: "export.json", bytes: 10 },
                { name: "ossie/ontology.yaml", bytes: 50 }, { name: "ossie/validation.json", bytes: 5 },
                { name: "okf/index.md", bytes: 5 }, { name: "okf/concepts/계약상태.md", bytes: 7 },
                { name: "okf/validation.json", bytes: 5 }],
        dirs: [{ name: "okf", files: ["concepts/계약상태.md", "index.md", "validation.json"], bytes: 17 },
               { name: "ossie", files: ["ontology.yaml", "validation.json"], bytes: 55 }],
      }] },
      "GET /exports/r0002/ossie/validation.json": () => new Response(JSON.stringify(
        { semantic_model: "ok", roundtrip: "ok", ontology: "ok", mapping: "ok", warnings: ["w"] }),
        { headers: { "content-type": "text/plain" } }),
      "GET /exports/r0002/okf/validation.json": () => new Response(JSON.stringify(
        { okf_version: "0.2", ok: true, files: 95, errors: [], warnings: [] }),
        { headers: { "content-type": "text/plain" } }),
      [`GET /exports/r0002/okf/concepts/${encodeURIComponent("계약상태.md")}`]: () =>
        new Response("---\ntype: Business Concept\n---\n# 계약상태", { headers: { "content-type": "text/plain" } }),
    })
    renderPage(<ExportsPage />)
    const card = await screen.findByText("exports/r0002/")
    const root = card.closest("[data-export]") as HTMLElement
    expect(root.querySelector("[data-dir=ossie]")).not.toBeNull()
    expect(root.querySelector("[data-dir=okf]")).not.toBeNull()
    await waitFor(() => expect(root.querySelectorAll("[data-validation=ossie][data-ok=true]").length).toBeGreaterThan(0))
    await waitFor(() => expect(root.querySelectorAll("[data-validation=okf][data-ok=true]").length).toBeGreaterThan(0))
    expect(within(root).getAllByText("Ossie ok · 1 warning").length).toBeGreaterThan(0)
    await user.click(within(root).getByRole("button", { name: "Preview okf/concepts/계약상태.md" }))
    expect(await screen.findByText(/type: Business Concept/)).toBeInTheDocument()
  })

  it("shows 20 export folders, then more on request", async () => {
    const user = userEvent.setup()
    const entry = (n: number) => ({ revision: n, dir: `r${String(n).padStart(4, "0")}`,
                                    created_at: "2026-10-28T12:06:24+00:00", files: [], dirs: [] })
    mockFetch({ "GET /exports": { exports: Array.from({ length: 23 }, (_, i) => entry(23 - i)) } })
    renderPage(<ExportsPage />)
    await screen.findByText("exports/r0023/")
    expect(document.querySelectorAll("[data-export]")).toHaveLength(20)
    await user.click(screen.getByRole("button", { name: "Show more (3 older)" }))
    expect(document.querySelectorAll("[data-export]")).toHaveLength(23)
    expect(screen.getByText("exports/r0001/")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Show more/ })).toBeNull()
  })
})

describe("Status page", () => {
  it("is read-only: checks, providers and the kordoc hint", async () => {
    mockFetch({ "GET /status": STATUS })
    renderPage(<StatusPage />)
    const table = await screen.findByRole("table")
    expect(table.querySelector("[data-check=postgres][data-status=error]")).not.toBeNull()
    expect(within(table).getByText("docker compose up -d")).toBeInTheDocument()
    expect(table.querySelector("[data-check=kordoc][data-status=warn]")).not.toBeNull()
    expect(screen.getByText("ollama · openai/qwen3:8b")).toBeInTheDocument()
    expect(screen.queryByRole("combobox")).toBeNull()
  })
})
