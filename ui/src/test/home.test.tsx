import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"

import { RunSheet } from "@/components/run-sheet"
import HomePage from "@/pages/home"
import SourcesPage from "@/pages/sources"
import FORMATS from "@/test/fixtures/formats.json"
import { mockFetch, renderPage } from "@/test/utils"

class FakeEventSource {
  static CLOSED = 2
  readyState = 0
  onopen = null
  onerror = null
  addEventListener() {}
  close() {}
}

const STATUS = (loggedIn: boolean) => ({
  status: loggedIn ? "ok" : "error", version: "0.2.0", kordoc_available: true, ocr_ready: false,
  vision_model: null, formats: FORMATS, service_url: "http://127.0.0.1:7860",
  providers: {
    chat_provider: "claude", chat_model: "openai/claude:claude-sonnet-5-5", embed_provider: "ollama",
    embed_model: "bge-m3", openai_base_url: "",
    chat: { name: "claude", model: "openai/claude:claude-sonnet-5-5", available: true, logged_in: loggedIn,
            detail: loggedIn ? "logged in (claude.ai, max)" : "not logged in",
            remedy: loggedIn ? "" : "run `claude auth login` (or `claude` then /login) as this user",
            last_test: null },
    embed: { name: "ollama", model: "bge-m3" },
  },
  checks: { backend: { status: "ok", detail: "http://127.0.0.1:3161" },
            postgres: { status: "ok", detail: "localhost:5432/autoontology" } },
  doctor: "uv run autoontology doctor", upload_max_mb: 200,
})

const SAMPLES = [
  { set: "quickstart", path: "/repo/samples/quickstart", registered: false,
    sources: [{ name: "quick", kind: "structured", path: "/repo/samples/quickstart/structured", files: 3, bytes: 6000, formats: [".csv"] },
              { name: "quick-docs", kind: "documents", path: "/repo/samples/quickstart/documents", files: 5, bytes: 90000,
                formats: [".docx", ".hwp", ".md", ".pdf"] }],
    durations: { claude: { text: "~8 min", measured: true }, ollama: { text: "20-50 min", measured: false } } },
  { set: "hanbit", path: "/repo/samples/hanbit", registered: false,
    sources: [{ name: "hanbit", kind: "structured", path: "/repo/samples/hanbit/structured", files: 9, bytes: 400000, formats: [".csv", ".xls"] }],
    durations: { claude: { text: "~20-30 min", measured: false } } },
]

const no_snapshot = () => new Response(JSON.stringify({ error: "no_snapshot", detail: "no ontology yet" }),
                                       { status: 404, headers: { "content-type": "application/json" } })

beforeEach(() => vi.stubGlobal("EventSource", FakeEventSource))

describe("Home page", () => {
  it("shows five steps and the first reflects /api/status (login command when logged out)", async () => {
    mockFetch({
      "GET /status": STATUS(false), "GET /sources": { sources: [] }, "GET /ontology": no_snapshot,
      "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": SAMPLES,
    })
    renderPage(<HomePage />)
    await screen.findByText("Models connected")
    const cards = document.querySelectorAll("#checklist > li[data-step]")
    expect([...cards].map((c) => c.getAttribute("data-step"))).toEqual(["models", "sources", "build", "explore", "export"])
    await waitFor(() => expect(document.querySelector("[data-step=models]")?.getAttribute("data-state")).toBe("active"))
    const models = document.querySelector<HTMLElement>("[data-step=models]")!
    expect(within(models).getByText("claude auth login")).toBeInTheDocument()
    expect(within(models).getByText("uv run autoontology down && uv run autoontology up")).toBeInTheDocument()
    expect(within(models).getByText("Not logged in")).toBeInTheDocument()
    expect(document.querySelector("[data-step=sources]")?.getAttribute("data-state")).toBe("pending")
    expect(screen.getByText("0 of 5 done")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /step-by-step guide/ })).toHaveAttribute(
      "href", "https://github.com/kimtoma/AutoOntology/blob/main/docs/GUIDE.md")
  })

  it("tests the provider and explains a step in a popover", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /status": STATUS(true), "GET /sources": { sources: [] }, "GET /ontology": no_snapshot,
      "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": SAMPLES,
      "POST /providers/test": { ok: true, seconds: 3.07, answer: "pong", provider: "claude" },
    })
    renderPage(<HomePage />)
    await waitFor(() => expect(document.querySelector("[data-step=models]")?.getAttribute("data-state")).toBe("done"))
    expect(document.querySelector("[data-step=sources]")?.getAttribute("data-state")).toBe("active")
    await user.click(screen.getByRole("button", { name: "Test" }))
    expect(await screen.findByText(/Test passed: the model answered in 3.1 s/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === "POST" && c.path === "/providers/test")).toBe(true)
    const help = document.querySelector<HTMLElement>("[data-help=build]")!
    await user.click(help)
    expect(await screen.findByText(/Building finds the business concepts/)).toBeInTheDocument()
  })

  it("lists the sample sets with formats and expected time, and loads one", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /status": STATUS(true), "GET /sources": { sources: [] }, "GET /ontology": no_snapshot,
      "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": SAMPLES,
      "POST /samples/load": {
        set: "quickstart", messages: [],
        sources: [{ name: "quick", kind: "files", action: "created" }, { name: "quick-docs", kind: "documents", action: "created" }],
      },
    })
    renderPage(<><HomePage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: /Load sample data/ }))
    const dialog = await screen.findByRole("dialog")
    const quick = await within(dialog).findByText("quickstart")
    const card = quick.closest<HTMLElement>("[data-sample-set]")!
    expect(within(card).getByText(/\.docx \.hwp \.md \.pdf/)).toBeInTheDocument()
    expect(within(card).getByText(/5 documents/)).toBeInTheDocument()
    expect(within(card).getByText("~8 min")).toBeInTheDocument()
    expect(dialog.querySelector("[data-sample-set=hanbit]")).not.toBeNull()
    await user.click(within(card).getByRole("button", { name: /Load quickstart/ }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/samples/load")).toBe(true))
    const post = calls.find((c) => c.path === "/samples/load")!
    expect(JSON.parse(String(post.init?.body))).toEqual({ set: "quickstart" })
    expect(await within(dialog).findByText("Sources ready:")).toBeInTheDocument()
    expect(dialog.querySelector("[data-loaded-source=quick-docs]")).not.toBeNull()
  })

  it("shows the counts of a built ontology and the expected time of a sample build", async () => {
    mockFetch({
      "GET /status": STATUS(true),
      "GET /sources": { sources: [{ name: "quick", kind: "files" }, { name: "quick-docs", kind: "documents" }] },
      "GET /ontology": { revision: 1, created_at: "2026-10-29T00:00:00Z", counts: {
        classes_structured: 3, classes_document: 56, merged_concepts: 3, object_properties: 143 } },
      "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": SAMPLES,
    })
    renderPage(<HomePage />)
    await waitFor(() => expect(document.querySelector("[data-step=export]")?.getAttribute("data-state")).toBe("active"))
    const counts = document.getElementById("explore-counts")!
    expect(counts).toHaveTextContent("3 business concepts found in your tables")
    expect(counts).toHaveTextContent("56 concepts found in your documents")
    expect(screen.getByText("Nothing exported yet.")).toBeInTheDocument()
  })
})

describe("Sources dialog preview", () => {
  it("derives accept from the formats API and classifies .doc and .xyz before upload", async () => {
    const user = userEvent.setup({ applyAccept: false })
    const { calls } = mockFetch({ "GET /sources": { sources: [] }, "GET /status": STATUS(true) })
    renderPage(<SourcesPage />)
    await user.click((await screen.findAllByRole("button", { name: "Add source" }))[0])
    await user.click(await screen.findByRole("tab", { name: /Documents/ }))
    const input = screen.getByLabelText("Files")
    expect(input.getAttribute("accept")).toBe(Object.keys(FORMATS.documents).sort().join(","))
    expect(input.getAttribute("accept")).toContain(".doc,")
    await user.upload(input, [
      new File(["x"], "거래처관리매뉴얼.doc"), new File(["x"], "data.xyz"), new File(["x"], "scan.png"),
    ])
    const doc = document.querySelector("[data-file='거래처관리매뉴얼.doc']")!
    expect(doc.getAttribute("data-state")).toBe("supported")
    expect(doc.getAttribute("data-engine")).toBe("native")
    const xyz = document.querySelector<HTMLElement>("[data-file='data.xyz']")!
    expect(xyz.getAttribute("data-state")).toBe("unsupported")
    expect(within(xyz).getByText(/cannot be read/)).toBeInTheDocument()
    expect(document.querySelector("[data-file='scan.png']")?.getAttribute("data-state")).toBe("needs_ocr")
    expect(screen.getByText(/OCR models are not downloaded/)).toBeInTheDocument()
    await user.type(screen.getByLabelText("Name"), "mine")
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: /Remove 1 unsupported/ }))
    expect(document.querySelector("[data-file='data.xyz']")).toBeNull()
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled()
    expect(calls.some((c) => c.method === "POST")).toBe(false)
  })

  it("lists .xls for tables and no SQLite URL wording", async () => {
    const user = userEvent.setup()
    mockFetch({ "GET /sources": { sources: [] }, "GET /status": STATUS(true) })
    renderPage(<SourcesPage />)
    await user.click((await screen.findAllByRole("button", { name: "Add source" }))[0])
    const hint = await screen.findByText(/Tables:/)
    expect(hint.textContent).toContain("XLS,")
    expect(hint.textContent).not.toMatch(/SQLite database URL|SQLite URL/)
    expect(screen.getByLabelText("Files").getAttribute("accept")).toContain(".xls")
  })
})

describe("Run sheet of a failed build", () => {
  it("shows plain step names, times and the remedy of the failed step", async () => {
    vi.stubGlobal("EventSource", FakeEventSource)
    mockFetch({
      "GET /runs/r1": {
        id: "r1", kind: "build", source: null, status: "failed", started_at: "2026-10-29T00:00:00Z",
        finished_at: "2026-10-29T00:02:00Z", error: "documents:quick-docs:read failed", params: {}, managed: false,
        progress: {},
        steps: [
          { name: "structured:quick:compile", status: "succeeded", started_at: "2026-10-29T00:00:00Z",
            finished_at: "2026-10-29T00:00:24Z", detail: { seconds: 24.4 } },
          { name: "documents:quick-docs:read", status: "failed", started_at: "2026-10-29T00:00:24Z",
            finished_at: "2026-10-29T00:00:25Z",
            detail: { error: "1 document failed", failed: [{ path: "a.doc", code: "DOC_PARSE_FAILED",
                                                           remedy: "Save the file as .docx and add it again" }] } },
          { name: "merge", status: "pending", started_at: null, finished_at: null, detail: {} },
        ],
      },
    })
    renderPage(<RunSheet runId="r1" onClose={() => {}} />)
    expect(await screen.findByText("Compile business concepts (LLM) · Succeeded · 24.4 s")).toBeInTheDocument()
    expect(screen.getByText("quick-docs · Read documents: 1 document failed")).toBeInTheDocument()
    expect(screen.getByText("Save the file as .docx and add it again")).toBeInTheDocument()
    expect(screen.getByText("Build the unified ontology")).toBeInTheDocument()
    expect(screen.getByText("not run")).toBeInTheDocument()
    expect(document.getElementById("live-total")).toHaveTextContent("took 2 min 0 s")
  })
})
