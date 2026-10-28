import { api, ApiError, conceptIdOfChip, isNoSnapshot } from "@/lib/api"
import { legacyTarget } from "@/lib/routes"
import { mockFetch } from "@/test/utils"

describe("api client", () => {
  it("returns typed bodies from /api", async () => {
    const { calls } = mockFetch({ "GET /sources": { sources: [{ name: "hanbit", kind: "files" }] } })
    const sources = await api.sources()
    expect(sources.map((s) => s.name)).toEqual(["hanbit"])
    expect(calls[0]).toMatchObject({ method: "GET", path: "/sources" })
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe("/api/sources")
  })

  it("raises ApiError with the server's detail and code", async () => {
    mockFetch({
      "POST /runs": () => new Response(JSON.stringify({ error: "run_active", detail: "busy" }),
                                       { status: 409, headers: { "content-type": "application/json" } }),
    })
    const err = await api.startRun({ kind: "build" }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: "run_active", message: "busy" })
  })

  it("posts the unified run bodies", async () => {
    const { calls } = mockFetch({ "POST /runs": { run_id: "r" } })
    await api.startRun({ kind: "build", sources: ["hanbit"], resume: true })
    await api.startRun({ kind: "export" })
    expect(calls.map((c) => JSON.parse(String(c.init?.body)))).toEqual([
      { kind: "build", sources: ["hanbit"], resume: true }, { kind: "export" },
    ])
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>
    expect(headers["content-type"]).toBe("application/json")
  })

  it("builds the ontology query strings and export paths", async () => {
    mockFetch({
      "GET /ontology/concepts": { items: [], total: 0, page: 1, size: 30, pages: 1, revision: 2 },
      "GET /ontology/concepts/term-a": { id: "term-a" },
      "DELETE /sources/a b": { removed: "a b", messages: [] },
    })
    await api.concepts({ q: "계약", origin: "aligned", kind: "", page: 2, revision: 2 })
    await api.concept("term-a", 2)
    await api.deleteSource("a b", true).catch(() => undefined)
    const urls = vi.mocked(fetch).mock.calls.map(([u]) => String(u))
    expect(urls[0]).toBe(`/api/ontology/concepts?page=2&size=30&q=${encodeURIComponent("계약")}&origin=aligned&revision=2`)
    expect(urls[1]).toBe("/api/ontology/concepts/term-a?revision=2")
    expect(urls[2]).toBe("/api/sources/a%20b?drop_db=true")
    // every segment encoded, the separators kept (Hangul OKF file names)
    expect(api.exportFileUrl("r0002", "okf/concepts/계약상태.md"))
      .toBe(`/api/exports/r0002/okf/concepts/${encodeURIComponent("계약상태.md")}`)
  })

  it("maps a network failure to the start-the-service hint", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch") }))
    const err = await api.status().catch((e) => e)
    expect(err).toMatchObject({ status: 0, code: "network" })
    expect(err.message).toMatch(/uv run autoontology up/)
  })

  it("recognizes 404 no_snapshot", () => {
    expect(isNoSnapshot(new ApiError("x", 404, "no_snapshot"))).toBe(true)
    expect(isNoSnapshot(new ApiError("x", 404, "unknown_revision"))).toBe(false)
    expect(isNoSnapshot(new Error("x"))).toBe(false)
  })
})

describe("concept ids of playground chips", () => {
  const chip = (id: string, link = "", graph_link = "") => ({ type: "term", id, label: "x", link, graph_link })
  it("uses the element id, then the legacy concept link, then the graph focus", () => {
    expect(conceptIdOfChip(chip("term-301fd719-fd5e"))).toBe("term-301fd719-fd5e")
    expect(conceptIdOfChip(chip("ev-1", "/concepts?concept=0c587bcc"))).toBe("concept-0c587bcc")
    expect(conceptIdOfChip(chip("rel-1", "/graph?focus=concept-ab", "/graph?focus=concept-ab"))).toBe("concept-ab")
    expect(conceptIdOfChip(chip("rel-2", "/graph", ""))).toBeNull()
  })
})

describe("legacy routes", () => {
  it("send old links to the new pages", () => {
    expect(legacyTarget("/runs", "?run=x")).toBe("/build?run=x")
    expect(legacyTarget("/settings", "")).toBe("/status")
    expect(legacyTarget("/concepts", "?concept=abc")).toBe("/ontology?concept=concept-abc")
    expect(legacyTarget("/concepts", "?concept=term-a&q=x")).toBe("/ontology?concept=term-a&q=x")
    expect(legacyTarget("/concepts", "")).toBe("/ontology")
    expect(legacyTarget("/graph", "")).toBeNull()
  })
})
