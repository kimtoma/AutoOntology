/** Set by the authenticated Cloudflare gateway; access is enforced by the Worker. */
const mode = typeof document !== "undefined"
  ? document.querySelector('meta[name="autoontology-demo"]')?.getAttribute("content") : null

export const SHARED_DEMO = mode === "sample"
export const GATEWAY_ACCESS = SHARED_DEMO || mode === "full"
export const DEMO_PAGES = new Set(["/ontology", "/graph", "/playground", "/exports"])
