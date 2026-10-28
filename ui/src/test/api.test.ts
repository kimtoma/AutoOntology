import { api, ApiError } from "@/lib/api"
import { mockFetch } from "@/test/utils"

describe("api client", () => {
  it("returns typed bodies from /api", async () => {
    const { calls } = mockFetch({ "GET /sources": { sources: [{ name: "hanbit", kind: "files" }] } })
    const sources = await api.sources()
    expect(sources.map((s) => s.name)).toEqual(["hanbit"])
    expect(calls[0]).toMatchObject({ method: "GET", path: "/sources" })
  })

  it("raises ApiError with the server's detail and code", async () => {
    mockFetch({
      "POST /runs": () => new Response(JSON.stringify({ error: "run_active", detail: "busy" }),
                                       { status: 409, headers: { "content-type": "application/json" } }),
    })
    const err = await api.startRun("build", "hanbit").catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: "run_active", message: "busy" })
  })

  it("sends JSON bodies and query strings", async () => {
    const { calls } = mockFetch({
      "PUT /providers": (_u: string, init?: RequestInit) => ({ echo: JSON.parse(String(init?.body)) }),
      "DELETE /sources/a b": { removed: "a b", messages: [] },
    })
    await api.saveProviders({ chat_provider: "claude", chat_model: "sonnet" })
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ chat_provider: "claude", chat_model: "sonnet" })
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>
    expect(headers["content-type"]).toBe("application/json")
    const fetchFn = vi.mocked(fetch)
    await api.deleteSource("a b", true).catch(() => undefined)
    expect(String(fetchFn.mock.calls[1][0])).toBe("/api/sources/a%20b?drop_db=true")
  })

  it("maps a network failure to a Korean hint", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch") }))
    await expect(api.status()).rejects.toMatchObject({ status: 0, code: "network" })
  })
})
