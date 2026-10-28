import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"

import { setLang } from "@/i18n/core"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  // every test starts in English with no remembered choice
  setLang("en", false)
  try { localStorage.clear() } catch { /* jsdom without storage */ }
  window.history.replaceState(null, "", "/")
})

// jsdom lacks these; Radix/sonner touch them
window.matchMedia ??= ((query: string) => ({
  matches: false, media: query, onchange: null,
  addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver
Element.prototype.scrollIntoView ??= function () {}
Element.prototype.hasPointerCapture ??= () => false
