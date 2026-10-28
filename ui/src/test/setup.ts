import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
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
