import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"

import { NAV } from "@/components/nav"
import type { SettingsState } from "@/lib/api"
import { changeOf, formFrom, providerState, RestartTimeout, waitForRestart } from "@/lib/settings"
import SettingsPage from "@/pages/settings"
import { setLang } from "@/i18n/core"
import { mockFetch, renderPage } from "@/test/utils"

const cli = (model: string, extra: object = {}) => ({
  found: true, logged_in: true, detail: "logged in", remedy: "", model, default_model: model,
  known_models: [model, "other-model"], login_command: "claude auth login", ...extra,
})

function settings(over: Partial<SettingsState> = {}): SettingsState {
  return {
    chat: { provider: "claude", model: "claude-sonnet-5-5", origin: ".env" },
    embed: { provider: "ollama", model: "bge-m3", origin: "default" },
    running: { chat_provider: "claude", chat_model: "claude-sonnet-5-5", embed_provider: "ollama", embed_model: "bge-m3" },
    openai: { base_url: "", api_key_set: false, chat_model: "", embed_model: "" },
    cli: {
      claude: cli("claude-sonnet-5-5"),
      codex: cli("gpt-6-astra", { logged_in: false, login_command: "codex login" }),
    },
    ollama: { endpoint: "http://localhost:11434/v1", reachable: true, models: ["bge-m3", "openai/qwen3:8b"],
              chat_model: "openai/qwen3:8b", embed_model: "bge-m3" },
    origins: {},
    locked_keys: [],
    vectors: { rows: 120, dimension: 1024 },
    restart_required: false,
    restart_in_progress: false,
    service_pid: 100,
    supervised: true,
    restart_command: "uv run autoontology restart",
    ...over,
  }
}

const TEST_OK = {
  chat: { ok: true, provider: "codex", model: "gpt-6-astra", seconds: 3.2, answer: "pong", error: null, remedy: null },
  embed: { ok: true, provider: "ollama", model: "bge-m3", seconds: 0.1, dimension: 1024,
           mismatch_with_store: false, stored_dimension: 1024 },
}

const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body ?? "{}"))

describe("settings form model", () => {
  it("sends only what changed, and the key only when typed", () => {
    const s = settings({ openai: { base_url: "https://api.test/v1", api_key_set: true, chat_model: "m", embed_model: "" } })
    const f = formFrom(s)
    expect(changeOf(s, f)).toEqual({})
    expect(changeOf(s, { ...f, chatProvider: "codex" })).toEqual({ chat_provider: "codex" })
    expect(changeOf(s, { ...f, chatProvider: "codex", chatModels: { ...f.chatModels, codex: "gpt-5.5" } }))
      .toEqual({ chat_provider: "codex", chat_model: "gpt-5.5" })
    expect(changeOf(s, { ...f, baseUrl: "https://api.test/v1/" })).toEqual({})
    expect(changeOf(s, { ...f, apiKey: "  sk-new  " })).toEqual({ api_key: "sk-new" })
    expect(changeOf(s, { ...f, clearKey: true })).toEqual({ clear_api_key: true })
    expect(providerState(s, "openai")).toBe("ready")
    expect(providerState(s, "openai", { ...f, clearKey: true })).toBe("needsSetup")
    expect(providerState(s, "codex")).toBe("notLoggedIn")
  })

  it("waits for a done record served by a new process", async () => {
    let pid = 100
    let state = "running"
    mockFetch({
      "GET /health/live": { status: "ok" },
      "GET /settings/restart/abc": () => ({ id: "abc", state, service_pid: pid }),
    })
    let ticks = 0
    const sleep = async () => {
      ticks += 1
      if (ticks === 2) { pid = 200 }        // the new service answers, still running
      if (ticks === 3) { state = "done" }   // the helper has finished
    }
    const st = await waitForRestart("abc", 100, { sleep })
    expect(st.state).toBe("done")
    expect(st.service_pid).toBe(200)
    expect(ticks).toBe(3)
  })

  it("reports a failed record and times out", async () => {
    mockFetch({ "GET /health/live": { status: "ok" },
                "GET /settings/restart/abc": { id: "abc", state: "failed", error: "port busy", service_pid: 100 } })
    expect((await waitForRestart("abc", 100, { sleep: async () => {} })).error).toBe("port busy")
    mockFetch({})  // the service never answers
    let t = 0
    await expect(waitForRestart("abc", 100, { sleep: async () => {}, now: () => (t += 1000), timeoutMs: 5000 }))
      .rejects.toBeInstanceOf(RestartTimeout)
  })
})

describe("Settings page", () => {
  it("shows the providers, login hints and the API key fields without the key", async () => {
    const user = userEvent.setup()
    mockFetch({ "GET /settings": settings({ openai: { base_url: "https://api.test/v1", api_key_set: true,
                                                         chat_model: "gpt-x", embed_model: "" } }) })
    renderPage(<SettingsPage />)
    const group = await screen.findByRole("radiogroup", { name: "Chat model" })
    expect(within(group).getAllByRole("radio")).toHaveLength(4)
    expect(within(group).getByRole("radio", { name: /Claude subscription/ })).toBeChecked()
    expect(document.querySelector("[data-provider-option=codex][data-state=notLoggedIn]")).not.toBeNull()
    expect(screen.getByRole("combobox", { name: "Chat model" })).toHaveValue("claude-sonnet-5-5")
    await user.click(within(group).getByRole("radio", { name: /Codex/ }))
    expect(screen.getByText("codex login")).toBeInTheDocument()
    expect(document.getElementById("settings-openai")).toBeNull()
    await user.click(within(group).getByRole("radio", { name: /OpenAI-compatible/ }))
    const key = screen.getByLabelText(/^API key/, { selector: "input" })
    expect(key).toHaveAttribute("type", "password")
    expect(key).toHaveAttribute("autocomplete", "off")
    expect(key).toHaveValue("")
    expect(screen.getByText(/Saved ✓/)).toBeInTheDocument()
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.test/v1")
    expect(screen.getByText("Stored vectors: 120 (1024 dimensions)")).toBeInTheDocument()
    // every input has a label
    for (const input of document.querySelectorAll("#settings-page input")) {
      const id = input.getAttribute("id")
      const labelled = (id && document.querySelector(`label[for="${id}"]`)) || input.closest("label")
      expect(labelled, input.outerHTML).toBeTruthy()
    }
  })

  it("locks the keys the shell sets", async () => {
    mockFetch({ "GET /settings": settings({ locked_keys: ["AUTOONTOLOGY_CHAT_PROVIDER"] }) })
    renderPage(<SettingsPage />)
    const radios = await screen.findAllByRole("radio", { name: /subscription|Ollama|OpenAI/ })
    expect(radios.filter((r) => r.closest("#settings-chat-providers")).every((r) => (r as HTMLInputElement).disabled)).toBe(true)
    expect(document.querySelector("[data-locked=AUTOONTOLOGY_CHAT_PROVIDER]")).not.toBeNull()
  })

  it("tests the candidate without saving it", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({ "GET /settings": settings(), "POST /settings/test": TEST_OK })
    renderPage(<SettingsPage />)
    await user.click(await screen.findByRole("radio", { name: /Codex/ }))
    await user.click(screen.getByRole("button", { name: "Test" }))
    expect(await screen.findByText("Chat: codex · gpt-6-astra answered in 3.2 s")).toBeInTheDocument()
    const post = calls.find((c) => c.method === "POST")!
    expect(bodyOf(post.init)).toEqual({ chat_provider: "codex" })
    expect(calls.some((c) => c.method === "PUT")).toBe(false)
  })

  it("warns when the test finds another embedding dimension", async () => {
    const user = userEvent.setup()
    mockFetch({
      "GET /settings": settings(),
      "POST /settings/test": { ...TEST_OK, embed: { ...TEST_OK.embed, dimension: 768, mismatch_with_store: true } },
    })
    renderPage(<SettingsPage />)
    await user.type(await screen.findByRole("combobox", { name: "Embedding model" }), "-v2")
    await user.click(screen.getByRole("button", { name: "Test" }))
    expect(await screen.findByText(/has 768 dimensions but the stored vectors have 1024/)).toBeInTheDocument()
  })

  it("saves, restarts and waits for the new service", async () => {
    const user = userEvent.setup()
    let pid = 100
    const { calls } = mockFetch({
      "GET /settings": () => settings(pid === 200
        ? { chat: { provider: "codex", model: "gpt-6-astra", origin: ".env" }, service_pid: 200,
            running: { chat_provider: "codex", chat_model: "gpt-6-astra", embed_provider: "ollama", embed_model: "bge-m3" } }
        : {}),
      "PUT /settings": { written: ["AUTOONTOLOGY_CHAT_PROVIDER"], backup: ".env.bak-1", restart_required: true },
      "POST /settings/restart": () => { pid = 200; return { restart_id: "0123456789ab", previous_pid: 100 } },
      "GET /health/live": { status: "ok" },
      "GET /settings/restart/0123456789ab": () => ({ id: "0123456789ab", state: "done", service_pid: pid }),
    })
    renderPage(<><SettingsPage /><Toaster /></>)
    await user.click(await screen.findByRole("radio", { name: /Codex/ }))
    await user.click(screen.getByRole("button", { name: "Save and restart" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(/restart \(usually under a minute\)/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Save and restart" }))
    expect(await screen.findByText("Restarted: now using codex · gpt-6-astra", {}, { timeout: 5000 })).toBeInTheDocument()
    const put = calls.find((c) => c.method === "PUT")!
    expect(bodyOf(put.init)).toEqual({ chat_provider: "codex" })
    const order = calls.map((c) => `${c.method} ${c.path}`).filter((c) => !c.startsWith("GET /settings"))
    expect(order.slice(0, 3)).toEqual(["PUT /settings", "POST /settings/restart", "GET /health/live"])
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
  })

  it("needs consent for an embedding dimension change", async () => {
    const user = userEvent.setup()
    let puts = 0
    const { calls } = mockFetch({
      "GET /settings": settings({ supervised: false }),
      "PUT /settings": () => {
        puts += 1
        if (puts === 1) {
          return new Response(JSON.stringify({ error: "embedding_dimension_changed", detail: "x", dimension: 768,
                                               stored_dimension: 1024, rows: 120 }),
                              { status: 409, headers: { "content-type": "application/json" } })
        }
        return { written: ["AUTOONTOLOGY_OLLAMA_EMBED_MODEL"], backup: null, restart_required: true }
      },
    })
    renderPage(<><SettingsPage /><Toaster /></>)
    await user.type(await screen.findByRole("combobox", { name: "Embedding model" }), "-v2")
    await user.click(screen.getByRole("button", { name: "Save and restart" }))
    let dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(/cannot restart itself/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Save" }))
    dialog = await screen.findByRole("alertdialog")
    expect(await within(dialog).findByText(/has 768 dimensions but the stored vectors have 1024/)).toBeInTheDocument()
    const go = within(dialog).getByRole("button", { name: "Save" })
    expect(go).toBeDisabled()
    await user.click(within(dialog).getByRole("switch"))
    await user.click(go)
    expect(await screen.findByText("Saved. Restart AutoOntology to apply it:")).toBeInTheDocument()
    const bodies = calls.filter((c) => c.method === "PUT").map((c) => bodyOf(c.init))
    expect(bodies).toEqual([{ embed_model: "bge-m3-v2" }, { embed_model: "bge-m3-v2", confirm_reingest: true }])
    expect(calls.some((c) => c.path === "/settings/restart")).toBe(false)
  })

  it("asks for the key again when the endpoint changes", async () => {
    const user = userEvent.setup()
    mockFetch({ "GET /settings": settings({ chat: { provider: "openai", model: "gpt-x", origin: ".env" },
                                              openai: { base_url: "https://api.test/v1", api_key_set: true,
                                                        chat_model: "gpt-x", embed_model: "" } }) })
    renderPage(<SettingsPage />)
    const url = await screen.findByLabelText("Base URL")
    expect(document.getElementById("settings-key-elsewhere")).toBeNull()
    await user.clear(url)
    await user.type(url, "https://other.test/v1")
    expect(screen.getByRole("alert")).toHaveTextContent("The saved key belongs to https://api.test")
    expect(document.querySelector("[data-provider-option=openai][data-state=needsSetup]")).not.toBeNull()
    await user.type(screen.getByLabelText(/^API key/, { selector: "input" }), "sk-other")
    expect(document.getElementById("settings-key-elsewhere")).toBeNull()
  })

  it.each([
    ["saved, restart refused", { supervised: true, restartFails: true }],
    ["saved without a restart", { supervised: false, restartFails: false }],
  ])("forgets the typed key once it is saved (%s)", async (_name, opts) => {
    const user = userEvent.setup()
    let saved = false
    const openai = { base_url: "https://api.test/v1", api_key_set: false, chat_model: "gpt-x", embed_model: "" }
    mockFetch({
      "GET /settings": () => settings({ supervised: opts.supervised,
                                        chat: { provider: "openai", model: "gpt-x", origin: ".env" },
                                        openai: { ...openai, api_key_set: saved }, restart_required: saved }),
      "PUT /settings": () => { saved = true; return { written: ["AUTOONTOLOGY_OPENAI_API_KEY"], backup: null, restart_required: true } },
      "POST /settings/restart": () => new Response(JSON.stringify({ error: "run_active", detail: "a build is running" }),
                                                   { status: 409, headers: { "content-type": "application/json" } }),
    })
    renderPage(<><SettingsPage /><Toaster /></>)
    const key = await screen.findByLabelText(/^API key/, { selector: "input" })
    await user.type(key, "sk-typed-secret")
    await user.click(screen.getByRole("button", { name: "Save and restart" }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: opts.supervised ? "Save and restart" : "Save" }))
    if (opts.restartFails) expect(await screen.findByText(/a build is running/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText(/^API key/, { selector: "input" })).toHaveValue(""))
    expect(await screen.findByText(/Saved ✓/)).toBeInTheDocument()
  })

  it("offers a restart for settings saved but not applied", async () => {
    mockFetch({ "GET /settings": settings({ restart_required: true, chat: { provider: "codex", model: "gpt-6-astra", origin: ".env" } }) })
    renderPage(<SettingsPage />)
    expect(await screen.findByText("Saved settings are not applied yet")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Restart now" })).toBeEnabled()
  })

  it("speaks Korean", async () => {
    setLang("ko", false)
    mockFetch({ "GET /settings": settings() })
    renderPage(<SettingsPage />)
    expect(await screen.findByRole("radiogroup", { name: "채팅 모델" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "테스트" })).toBeInTheDocument()
  })

  it("has a nav entry after Status", () => {
    const paths = NAV.map((n) => n.path)
    expect(paths.slice(-2)).toEqual(["/status", "/settings"])
  })
})
