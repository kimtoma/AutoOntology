import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

beforeEach(() => { vi.resetModules() })
afterEach(() => { document.head.querySelector('meta[name="autoontology-demo"]')?.remove() })

describe("gateway access", () => {
  it("keeps every application feature available in a full remote workspace", async () => {
    document.head.insertAdjacentHTML("beforeend", '<meta name="autoontology-demo" content="full">')
    const mode = await import("@/lib/demo")
    expect(mode.SHARED_DEMO).toBe(false)
    expect(mode.GATEWAY_ACCESS).toBe(true)
  })
  it("retains sample restrictions when explicitly configured", async () => {
    document.head.insertAdjacentHTML("beforeend", '<meta name="autoontology-demo" content="sample">')
    const mode = await import("@/lib/demo")
    expect(mode.SHARED_DEMO).toBe(true)
    expect(mode.GATEWAY_ACCESS).toBe(true)
  })
  it("does not add a remote sign-out form to a local installation", async () => {
    const mode = await import("@/lib/demo")
    expect(mode.SHARED_DEMO).toBe(false)
    expect(mode.GATEWAY_ACCESS).toBe(false)
  })
})
