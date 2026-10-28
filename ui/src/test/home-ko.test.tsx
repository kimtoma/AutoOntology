import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Route, Routes } from "react-router"

import { AppShell } from "@/components/app-shell"
import { LangProvider } from "@/i18n"
import { initLang, LANG_STORAGE_KEY } from "@/i18n/core"
import HomePage from "@/pages/home"
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

const STATUS = {
  status: "error", version: "0.2.0", kordoc_available: true, ocr_ready: false, vision_model: null,
  formats: FORMATS, service_url: "http://127.0.0.1:7860",
  providers: {
    chat_provider: "claude", chat_model: "claude-sonnet-5-5", embed_provider: "ollama", embed_model: "bge-m3",
    openai_base_url: "",
    chat: { name: "claude", model: "claude-sonnet-5-5", available: true, logged_in: false,
            detail: "not logged in", remedy: "run `claude auth login` as this user", last_test: null },
    embed: { name: "ollama", model: "bge-m3" },
  },
  checks: { backend: { status: "ok", detail: "" }, postgres: { status: "ok", detail: "" } },
  doctor: "uv run autoontology doctor", upload_max_mb: 200,
}

const ROUTES = {
  "GET /status": STATUS, "GET /sources": { sources: [] },
  "GET /ontology": () => new Response(JSON.stringify({ error: "no_snapshot", detail: "no ontology yet" }),
                                      { status: 404, headers: { "content-type": "application/json" } }),
  "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": [],
}

const shell = () => (
  <LangProvider>
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
      </Route>
    </Routes>
  </LangProvider>
)

beforeEach(() => vi.stubGlobal("EventSource", FakeEventSource))

describe("Home in Korean", () => {
  it("renders Korean under ?lang=ko and keeps it after a reload without the parameter", async () => {
    mockFetch(ROUTES)
    window.history.replaceState(null, "", "/ui/?lang=ko")
    initLang()
    const first = renderPage(shell())
    expect(await screen.findByRole("heading", { level: 1, name: "시작하기" })).toBeInTheDocument()
    const models = document.querySelector<HTMLElement>("[data-step=models]")!
    expect(within(models).getByText("모델 연결")).toBeInTheDocument()
    await waitFor(() => expect(within(models).getByText("로그인 필요")).toBeInTheDocument())
    // the command stays verbatim
    expect(within(models).getByText("claude auth login")).toBeInTheDocument()
    expect(screen.getByText("5단계 중 0단계 완료")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /단계별 가이드/ })).toHaveAttribute(
      "href", "https://github.com/kimtoma/AutoOntology/blob/main/docs/GUIDE.ko.md")
    expect(document.documentElement.lang).toBe("ko")
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe("ko")
    first.unmount()

    // a reload: the URL has no ?lang= any more, the stored choice wins
    window.history.replaceState(null, "", "/ui/")
    initLang()
    renderPage(shell())
    expect(await screen.findByRole("heading", { level: 1, name: "시작하기" })).toBeInTheDocument()
  })

  it("switches the language from the header without a reload", async () => {
    const user = userEvent.setup()
    mockFetch(ROUTES)
    renderPage(shell())
    expect(await screen.findByRole("heading", { level: 1, name: "Get started" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Language: English" }))
    await user.click(await screen.findByRole("menuitemradio", { name: "한국어" }))
    expect(await screen.findByRole("heading", { level: 1, name: "시작하기" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /소스/ })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe("ko")
    expect(localStorage.getItem(LANG_STORAGE_KEY)).toBe("ko")
    // the switch keeps the focus for keyboard users
    await waitFor(() => expect(document.activeElement?.id).toBe("lang-toggle"))
    await user.click(screen.getByRole("button", { name: "언어: 한국어" }))
    await user.click(await screen.findByRole("menuitemradio", { name: "English" }))
    expect(await screen.findByRole("heading", { level: 1, name: "Get started" })).toBeInTheDocument()
  })
})
