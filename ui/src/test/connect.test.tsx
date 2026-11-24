import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Toaster } from "sonner"

import { NAV } from "@/components/nav"
import { setLang } from "@/i18n/core"
import type { AgentToken } from "@/lib/api"
import { endpoints, snippet, TOKEN_PLACEHOLDER, toolParams } from "@/lib/connect"
import ConnectPage from "@/pages/connect"
import { mockFetch, renderPage } from "@/test/utils"

const RAW = "aok_test_once_1234567890abcdef"
const ROTATED = "aok_test_rotated_0987654321fedcba"

const record = (over: Partial<AgentToken> = {}): AgentToken => ({
  id: "tok_aaaaaaaaaaaa", name: "claude-code", status: "active", revision_pin: null,
  expires_at: "2027-02-22T00:00:00+00:00", created_at: "2026-11-24T00:00:00+00:00",
  last_used_at: "2026-11-24T03:00:00+00:00", revoked_at: null, ...over,
})

const TOOLS = {
  tools: [
    { name: "search_concepts", description: "Search the concepts.",
      inputSchema: { type: "object", properties: { query: {}, limit: {}, kind: {}, revision: {} }, required: ["query"] } },
    { name: "list_exports", description: "List the export folders.", inputSchema: { type: "object", properties: {} } },
    { name: "future_tool", description: "A tool the UI has no translation for.", inputSchema: { type: "object" } },
  ],
  protocol: "2025-06-18", mcp_path: "/mcp", rest_base: "/agent/v1", rate_per_min: 120, max_body_bytes: 1048576,
}

const USAGE = {
  days: 30, total: 12, ok: 11,
  by_tool: [{ tool: "search_concepts", calls: 9, ok: 9, avg_ms: 41 }, { tool: "get_concept", calls: 3, ok: 2, avg_ms: 80 }],
  by_day: [{ day: "2026-11-23", calls: 5 }, { day: "2026-11-24", calls: 7 }],
  by_token: [{ token_id: "tok_aaaaaaaaaaaa", name: "claude-code", calls: 12, ok: 11,
               by_surface: { mcp: 10, rest: 2 }, last_call_at: "2026-11-24T03:00:00+00:00" }],
}

const REVISIONS = {
  revisions: [
    { revision: 3, created_at: "2026-11-20T00:00:00+00:00", run_id: "r3", run_kind: "build", counts: {}, sources: [] },
    { revision: 2, created_at: "2026-11-10T00:00:00+00:00", run_id: "r2", run_kind: "build", counts: {}, sources: [] },
  ],
}

function routes(tokens: AgentToken[] = [record()], extra: Record<string, unknown> = {}) {
  return {
    "GET /agent/tools": TOOLS,
    "GET /agent/tokens": () => ({ tokens }),
    "GET /agent/usage": USAGE,
    "GET /ontology/revisions": REVISIONS,
    ...extra,
  }
}

const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body ?? "{}"))
const origin = window.location.origin

describe("connect helpers", () => {
  it("builds the endpoints and snippets from the origin and the token", () => {
    const ep = endpoints("http://127.0.0.1:7800/", TOOLS)
    expect(ep).toEqual({ mcp: "http://127.0.0.1:7800/mcp", rest: "http://127.0.0.1:7800/agent/v1",
                         openapi: "http://127.0.0.1:7800/agent/v1/openapi.json", server: "autoontology" })
    expect(snippet("claudeCode", ep, RAW)).toBe(
      `claude mcp add --transport http autoontology http://127.0.0.1:7800/mcp --header "Authorization: Bearer ${RAW}"`)
    expect(JSON.parse(snippet("desktop", ep, RAW))).toEqual({
      mcpServers: { autoontology: { type: "http", url: "http://127.0.0.1:7800/mcp",
                                    headers: { Authorization: `Bearer ${RAW}` } } },
    })
    const curl = snippet("curl", ep, TOKEN_PLACEHOLDER, "q1")
    expect(curl).toContain(`curl -H "Authorization: Bearer <TOKEN>" "http://127.0.0.1:7800/agent/v1/concepts?q=q1&limit=5"`)
    expect(curl).toContain(`curl -X POST "http://127.0.0.1:7800/agent/v1/sparql"`)
    expect(toolParams(TOOLS.tools[0].inputSchema).map((p) => p.name)).toEqual(["query", "limit", "kind", "revision"])
    // one MCP server per project: autoontology-<slug>
    const scoped = endpoints("http://127.0.0.1:7800", TOOLS, "contracts")
    expect(scoped.mcp).toBe("http://127.0.0.1:7800/mcp")
    expect(snippet("claudeCode", scoped, RAW)).toContain("--transport http autoontology-contracts http://127.0.0.1:7800/mcp")
    expect(Object.keys(JSON.parse(snippet("desktop", scoped, RAW)).mcpServers)).toEqual(["autoontology-contracts"])
  })
})

describe("Connect page", () => {
  it("shows the endpoints from the page's origin, tokens, tools and usage", async () => {
    mockFetch(routes([record(), record({ id: "tok_bbbbbbbbbbbb", name: "old-script", status: "revoked",
                                          revision_pin: 2, expires_at: null, last_used_at: null })]))
    renderPage(<ConnectPage />)
    expect(await screen.findByText(`${origin}/mcp`)).toBeInTheDocument()
    expect(screen.getByText(`${origin}/agent/v1`)).toBeInTheDocument()
    expect(screen.getByText(`${origin}/agent/v1/openapi.json`)).toBeInTheDocument()
    expect(screen.getByText("Up to 120 requests a minute per token.")).toBeInTheDocument()

    const active = await screen.findByText("claude-code", { selector: "[data-token] span" })
    const row = active.closest("li")!
    expect(within(row).getByText("Follows the latest")).toBeInTheDocument()
    expect(within(row).getByText("12", { selector: "[data-calls]" })).toBeInTheDocument()
    const revoked = document.querySelector("[data-token=tok_bbbbbbbbbbbb]") as HTMLElement
    expect(within(revoked).getByText("Pinned to revision 2")).toBeInTheDocument()
    expect(within(revoked).getAllByText("Revoked").length).toBeGreaterThan(0)
    expect(within(revoked).queryByRole("button", { name: /Rotate/ })).toBeNull()

    // the how-to tabs carry the placeholder, never a real token
    expect(document.querySelector("#howto-claudeCode")?.textContent).toContain("Bearer <TOKEN>")

    const tool = document.querySelector("[data-tool=search_concepts]") as HTMLElement
    expect(within(tool).getByText(/Search concepts \(table terms/)).toBeInTheDocument()
    expect(within(tool).getByText("query")).toBeInTheDocument()
    // no translation: the backend's description
    expect(screen.getByText("A tool the UI has no translation for.")).toBeInTheDocument()
    expect(document.querySelector("#usage-total")?.textContent).toBe("12")
    expect(document.querySelector("#usage-ok")?.textContent).toBe("11")
    expect(document.querySelector("[data-usage-tool=get_concept]")).not.toBeNull()
  })

  it("offers to create the first token when there is none", async () => {
    mockFetch(routes([]))
    renderPage(<ConnectPage />)
    expect(await screen.findByText("No tokens yet")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Create token" })).toBeInTheDocument()
  })

  it("creates a token, shows it once with filled-in snippets, and forgets it after closing", async () => {
    const user = userEvent.setup()
    const tokens: AgentToken[] = []
    const { calls } = mockFetch(routes(tokens, {
      "POST /agent/tokens": (_u: string, init?: RequestInit) => {
        const body = bodyOf(init)
        const rec = record({ name: body.name, revision_pin: body.revision_pin, last_used_at: null })
        tokens.push(rec)
        return { token: RAW, record: rec, note: "This token is shown only once: copy it now." }
      },
    }))
    renderPage(<ConnectPage />)
    await user.click(await screen.findByRole("button", { name: "Create token" }))
    const dialog = await screen.findByRole("dialog", { name: "Create a token" })
    await user.type(within(dialog).getByLabelText("Name"), "cursor")
    await user.click(within(dialog).getByRole("combobox", { name: "Revision" }))
    await user.click(await screen.findByRole("option", { name: /Revision 3/ }))
    await user.click(within(dialog).getByRole("combobox", { name: "Expires" }))
    await user.click(await screen.findByRole("option", { name: "Never" }))
    await user.click(within(dialog).getByRole("button", { name: "Create token" }))

    const once = await screen.findByRole("dialog", { name: "Token for “cursor”" })
    const post = calls.find((c) => c.method === "POST" && c.path === "/agent/tokens")!
    expect(bodyOf(post.init)).toEqual({ name: "cursor", revision_pin: 3, expires_days: null })
    expect(within(once).getByText(RAW)).toBeInTheDocument()
    expect(within(once).getByText(/will not be shown again/)).toBeInTheDocument()
    expect(once.querySelector("#once-claudeCode")?.textContent).toBe(
      `claude mcp add --transport http autoontology ${origin}/mcp --header "Authorization: Bearer ${RAW}"`)
    await user.click(within(once).getByRole("tab", { name: "Claude Desktop / Cursor" }))
    expect(once.querySelector("#once-desktop")?.textContent).toContain(`"Authorization": "Bearer ${RAW}"`)
    await user.click(within(once).getByRole("tab", { name: "REST (curl)" }))
    expect(once.querySelector("#once-curl")?.textContent).toContain(`Bearer ${RAW}`)

    await user.click(within(once).getByRole("button", { name: "Copy Token" }))
    expect(await navigator.clipboard.readText()).toBe(RAW)

    await user.click(within(once).getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(await screen.findByText("cursor", { selector: "[data-token] span" })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(RAW)
  })

  it("rotates after a confirmation and shows the new token once", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes([record()], {
      "POST /agent/tokens/tok_aaaaaaaaaaaa/rotate": { token: ROTATED, record: record(), note: "once" },
    }))
    renderPage(<ConnectPage />)
    await user.click(await screen.findByRole("button", { name: "Rotate token claude-code" }))
    const confirm = await screen.findByRole("alertdialog", { name: "Rotate “claude-code”?" })
    expect(within(confirm).getByText(/old one stops working immediately/)).toBeInTheDocument()
    expect(calls.some((c) => c.path.endsWith("/rotate"))).toBe(false)
    await user.click(within(confirm).getByRole("button", { name: "Rotate token" }))
    const once = await screen.findByRole("dialog", { name: "New token for “claude-code”" })
    expect(within(once).getByText(ROTATED)).toBeInTheDocument()
    expect(once.querySelector("#once-claudeCode")?.textContent).toContain(`Bearer ${ROTATED}`)
    expect(calls.filter((c) => c.method === "POST" && c.path === "/agent/tokens/tok_aaaaaaaaaaaa/rotate")).toHaveLength(1)
  })

  it("revokes after a confirmation", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes([record()], {
      "DELETE /agent/tokens/tok_aaaaaaaaaaaa": record({ status: "revoked", revoked_at: "2026-11-24T04:00:00+00:00" }),
    }))
    renderPage(<><ConnectPage /><Toaster /></>)
    await user.click(await screen.findByRole("button", { name: "Revoke token claude-code" }))
    const confirm = await screen.findByRole("alertdialog", { name: "Revoke “claude-code”?" })
    await user.click(within(confirm).getByRole("button", { name: "Revoke token" }))
    expect(await screen.findByText("Token “claude-code” revoked")).toBeInTheDocument()
    expect(calls.filter((c) => c.method === "DELETE" && c.path === "/agent/tokens/tok_aaaaaaaaaaaa")).toHaveLength(1)
  })

  it("reads in Korean", async () => {
    const user = userEvent.setup()
    setLang("ko", false)
    mockFetch(routes([record({ revision_pin: 3 })]))
    renderPage(<ConnectPage />)
    expect(await screen.findByRole("heading", { name: "연결" })).toBeInTheDocument()
    expect(await screen.findByText("리비전 3 고정")).toBeInTheDocument()
    await user.click(screen.getByRole("tab", { name: "REST (curl)" }))
    expect(document.querySelector("#howto-curl")?.textContent).toContain("/agent/v1/concepts?q=계약&limit=5")
  })
})

describe("Connect in the navigation", () => {
  it("comes right after Exports in both languages", () => {
    const ids = NAV.map((n) => n.id)
    expect(ids[ids.indexOf("exports") + 1]).toBe("connect")
    expect(NAV.find((n) => n.id === "connect")?.path).toBe("/connect")
    expect(NAV.find((n) => n.id === "connect")?.label).toBe("Connect")
    setLang("ko", false)
    expect(NAV.find((n) => n.id === "connect")?.label).toBe("연결")
    expect(NAV.find((n) => n.id === "connect")?.hint).toBe("외부 에이전트가 MCP·REST로 온톨로지를 조회")
  })
})
