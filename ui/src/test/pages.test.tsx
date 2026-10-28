import { act, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"

import ConceptsPage from "@/pages/concepts"
import SettingsPage from "@/pages/settings"
import SourcesPage from "@/pages/sources"
import { mockFetch, renderPage } from "@/test/utils"

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

const PROVIDERS = {
  selection: { chat_provider: "ollama", chat_model: "openai/qwen3:8b", embed_provider: "ollama",
               origin: { chat_provider: "settings.json", embed_provider: "shell" } },
  settings_file: { chat_provider: "ollama" },
  choices: { chat_provider: ["ollama", "claude", "codex", "nvidia"], embed_provider: ["ollama", "nvidia"] },
  providers: [
    { name: "ollama", role: "chat+embed", ok: true, available: true, logged_in: true,
      detail: "localhost has qwen3", remedy: "", models: ["qwen3:8b"], model: "openai/qwen3:8b", last_test: null },
    { name: "claude", role: "chat", ok: true, available: true, logged_in: true,
      detail: "logged in (claude.ai, max)", remedy: "", models: ["sonnet", "opus"],
      model: "claude-sonnet-5-5", last_test: { at: "2026-10-27T21:07:01Z", ok: true } },
    { name: "codex", role: "chat", ok: false, available: true, logged_in: false,
      detail: "not logged in", remedy: "codex login", models: [], model: "gpt-6-astra", last_test: null },
    { name: "nvidia", role: "chat+embed", ok: false, available: true, logged_in: false,
      detail: "no NVIDIA API key", remedy: "set DEFAULT_MODELS_API_KEY", models: [], model: "", last_test: null },
  ],
  applied: { chat_provider: "ollama", chat_model: "openai/qwen3:8b", embed_provider: "ollama" },
  restart_required: false,
  usage: {},
}

describe("소스 page", () => {
  it("lists the sources with kind and state", async () => {
    mockFetch({ "GET /sources": SOURCES })
    renderPage(<SourcesPage />)
    const table = await screen.findByRole("table")
    expect(within(table).getByText("hanbit")).toBeInTheDocument()
    expect(within(table).getByText("autoontology_src_hanbit")).toBeInTheDocument()
    expect(within(table).getAllByText("문서").length).toBeGreaterThan(0)
    expect(within(table).getAllByText("문서 5개 · 개념 85개").length).toBeGreaterThan(0)
  })

  it("shows the empty state with the next action", async () => {
    mockFetch({ "GET /sources": { sources: [] } })
    renderPage(<SourcesPage />)
    expect(await screen.findByText("등록된 소스가 없습니다")).toBeInTheDocument()
  })

  it("deletes with drop_db only when the switch is on", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "DELETE /sources/hanbit": { removed: "hanbit", messages: [] },
    })
    renderPage(<><SourcesPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "hanbit 삭제" }))
    await user.click(await screen.findByRole("switch"))
    await user.click(screen.getByRole("button", { name: "삭제" }))
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true))
    const fetchFn = vi.mocked(fetch)
    const del = fetchFn.mock.calls.find(([, init]) => init?.method === "DELETE")
    expect(String(del?.[0])).toBe("/api/sources/hanbit?drop_db=true")
  })

  it("resets the DROP switch for the next delete (regression)", async () => {
    const user = userEvent.setup()
    const files = { ...SOURCES, sources: [SOURCES.sources[0], { ...SOURCES.sources[0], name: "second",
      database: "autoontology_src_second" }] }
    mockFetch({
      "GET /sources": files,
      "DELETE /sources/hanbit": { removed: "hanbit", messages: [] },
    })
    renderPage(<><SourcesPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "hanbit 삭제" }))
    await user.click(await screen.findByRole("switch"))
    expect(screen.getByRole("switch")).toBeChecked()
    await user.click(screen.getByRole("button", { name: "삭제" }))
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    await user.click(screen.getByRole("button", { name: "second 삭제" }))
    const next = await screen.findByRole("switch")
    expect(next).not.toBeChecked()
  })

  it("refuses an upload over the limit before sending it", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /sources": SOURCES,
      "GET /status": { status: "ok", checks: {}, upload_max_mb: 1 },
    })
    renderPage(<SourcesPage />)
    await user.click(await screen.findByRole("button", { name: "새 소스" }))
    expect(await screen.findByText(/한 번에 최대 1 MB/)).toBeInTheDocument()
    await user.type(screen.getByLabelText("이름"), "big")
    const big = new File([new Uint8Array(2 * 1024 * 1024)], "big.csv", { type: "text/csv" })
    await user.upload(screen.getByLabelText("파일"), big)
    expect(screen.getByText(/합계 2.0 MB/)).toHaveClass("text-destructive")
    expect(screen.getByRole("button", { name: "등록" })).toBeDisabled()
    expect(calls.some((c) => c.method === "POST")).toBe(false)
  })
})

describe("개념 page", () => {
  const PAGE = (source: string | null) => ({
    items: [{ id: "a".repeat(32), name: "계약", name_en: null, kind: "entity", source,
              definition: "정의", synonyms: [], evidence_count: 1, verified_count: 1, alignment: null }],
    total: 1, page: 1, size: 30, pages: 1,
  })

  it("follows the URL q (⌘K search while on the page)", async () => {
    mockFetch({ "GET /concepts": PAGE("docs"), "GET /sources": { sources: [] } })
    const { router } = await import("@/test/utils").then((m) => m.renderRouted(<ConceptsPage />, "/concepts?q=계약"))
    const input = await screen.findByRole("searchbox", { name: "개념 검색" })
    expect(input).toHaveValue("계약")
    await act(() => router.navigate("/concepts?q=고객"))
    await waitFor(() => expect(input).toHaveValue("고객"))
  })

  it("renders no source badge for a concept without a source", async () => {
    mockFetch({ "GET /concepts": PAGE(null), "GET /sources": { sources: [] } })
    renderPage(<ConceptsPage />, "/concepts")
    const item = await screen.findByRole("button", { name: /계약/ })
    expect(item.querySelectorAll("[data-slot=badge]")).toHaveLength(1) // the kind only
  })
})

describe("설정 page", () => {
  it("renders a card per provider and the precedence note", async () => {
    mockFetch({ "GET /providers": PROVIDERS })
    renderPage(<SettingsPage />)
    expect(await screen.findByText("Claude (구독)")).toBeInTheDocument()
    for (const name of ["ollama", "claude", "codex", "nvidia"]) {
      expect(document.querySelector(`[data-provider="${name}"]`)).not.toBeNull()
    }
    expect(screen.getByText(/셸 환경변수 > settings.json/)).toBeInTheDocument()
    // embed provider comes from the shell: its control is locked
    expect(screen.getByRole("combobox", { name: "임베딩 프로바이더" })).toBeDisabled()
  })

  it("tests a provider and saves a selection", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /providers": PROVIDERS,
      "POST /providers/test": { at: "now", results: { claude: { ok: true, model: "openai/claude:sonnet",
        router: { ok: true, seconds: 2.5, answer: "pong" }, chatopenai: { ok: true } } } },
      "PUT /providers": { ...PROVIDERS, restart_required: true,
        selection: { ...PROVIDERS.selection, chat_provider: "claude", chat_model: "claude-sonnet-5-5" } },
    })
    renderPage(<><SettingsPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "Claude (구독) 테스트" }))
    expect(await screen.findByText("Claude (구독) 테스트 성공")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Claude (구독) 저장" }))
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true))
    const put = calls.find((c) => c.method === "PUT")
    expect(JSON.parse(String(put?.init?.body))).toEqual({ chat_provider: "claude", chat_model: "" })
    expect(await screen.findByText("재시작 필요")).toBeInTheDocument()
  })
})
