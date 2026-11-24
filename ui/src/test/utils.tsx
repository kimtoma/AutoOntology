import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router"

type Handler = (url: string, init?: RequestInit) => unknown

/** Stub fetch: *routes* maps "METHOD /path" (path without /api) to a JSON body
    or a function returning one; unknown routes answer 404. */
export function mockFetch(routes: Record<string, unknown | Handler>) {
  const calls: { method: string; path: string; init?: RequestInit; url: string; project: string | null }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? "GET").toUpperCase()
    // project-scoped calls (`/api/projects/<slug>/sources`) match the same routes as the
    // default project's alias (`/api/sources`); `project` records which one was asked
    const scoped = /^\/api\/projects\/([^/?]+)(\/.*)$/.exec(url)
    const scopedPath = scoped && !/^\/(archive|unarchive)$/.test(scoped[2].split("?")[0]) ? scoped[2] : null
    const project = scopedPath ? decodeURIComponent(scoped![1]) : null
    const path = (scopedPath ?? url.replace(/^\/api(?=\/)/, "")).split("?")[0]
    calls.push({ method, path, init, url, project })
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
