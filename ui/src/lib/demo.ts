/** Set only by the authenticated Cloudflare gateway's HTML response. API access is
 * enforced independently by the Worker; this flag only removes unavailable controls. */
export const SHARED_DEMO = typeof document !== "undefined" &&
  document.querySelector('meta[name="autoontology-demo"]')?.getAttribute("content") === "sample"

export const DEMO_PAGES = new Set(["/ontology", "/graph", "/playground", "/exports"])
