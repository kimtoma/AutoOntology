import { proxiedOrigin, rewriteDevOrigin, SERVICE } from "../../dev-proxy"

function fakeProxy() {
  let handler: ((req: { getHeader(n: string): unknown; setHeader(n: string, v: string): void }) => void) | null = null
  return {
    on: (_e: "proxyReq", h: typeof handler) => { handler = h },
    send(headers: Record<string, string>) {
      const out = { ...headers }
      handler!({ getHeader: (n) => out[n], setHeader: (n, v) => { out[n] = v } })
      return out
    },
  }
}

describe("dev proxy Origin rewrite", () => {
  it("rewrites only the dev server's own origin", () => {
    expect(proxiedOrigin("http://localhost:5173")).toBe(SERVICE)
    expect(proxiedOrigin("http://127.0.0.1:5173")).toBe(SERVICE)
    expect(proxiedOrigin(undefined)).toBeUndefined()
    for (const foreign of ["https://evil.example", "http://localhost:5174", "null", "http://localhost:5173.evil.example"]) {
      expect(proxiedOrigin(foreign)).toBe(foreign)
    }
  })

  it("forwards a cross-site write with its Origin and Sec-Fetch-Site untouched", () => {
    const proxy = fakeProxy()
    rewriteDevOrigin(proxy as never)
    expect(proxy.send({ origin: "https://evil.example", "sec-fetch-site": "cross-site" }))
      .toEqual({ origin: "https://evil.example", "sec-fetch-site": "cross-site" })
    expect(proxy.send({ origin: "http://localhost:5173", "sec-fetch-site": "same-origin" }))
      .toEqual({ origin: SERVICE, "sec-fetch-site": "same-origin" })
    expect(proxy.send({})).toEqual({})
  })
})

describe("dev proxy target", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.resetModules() })

  it("defaults to 127.0.0.1:7800 and follows the SERVICE env", async () => {
    vi.stubEnv("SERVICE", "")
    vi.resetModules()
    expect((await import("../../dev-proxy")).SERVICE).toBe("http://127.0.0.1:7800")
    vi.stubEnv("SERVICE", "http://127.0.0.1:7810/")
    vi.resetModules()
    expect((await import("../../dev-proxy")).SERVICE).toBe("http://127.0.0.1:7810")
  })
})
