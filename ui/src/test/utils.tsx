import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router"

type Handler = (url: string, init?: RequestInit) => unknown

/** Stub fetch: *routes* maps "METHOD /path" (path without /api) to a JSON body
    or a function returning one; unknown routes answer 404. */
export function mockFetch(routes: Record<string, unknown | Handler>) {
  const calls: { method: string; path: string; init?: RequestInit }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? "GET").toUpperCase()
    const path = url.replace(/^\/api(?=\/)/, "").split("?")[0]
    calls.push({ method, path, init })
    const route = routes[`${method} ${path}`]
    if (route === undefined) {
      return new Response(JSON.stringify({ error: "unknown", detail: `no route ${method} ${path}` }),
                          { status: 404, headers: { "content-type": "application/json" } })
    }
    const body = typeof route === "function" ? (route as Handler)(url, init) : route
    if (body instanceof Response) return body
    return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } })
  })
  vi.stubGlobal("fetch", fn)
  return { fn, calls }
}

export function renderPage(ui: ReactElement, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Like renderPage, but returns the data router so a test can navigate. */
export function renderRouted(ui: ReactElement, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: "*", element: ui }], { initialEntries: [path] })
  const result = render(
    <QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>,
  )
  return { ...result, router }
}
