import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router"
import { Toaster } from "sonner"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { keys } from "@/hooks/queries"
import {
  api, ApiError, errorMessage, getCurrentProject, RESERVED_SLUG_RE, setCurrentProject, setImplicitProject,
  type Project,
} from "@/lib/api"
import { pagePathname, projectPath, suggestSlug } from "@/lib/project"
import FORMATS from "@/test/fixtures/formats.json"
import { mockFetch } from "@/test/utils"

class FakeEventSource {
  static CLOSED = 2
  readyState = 0
  onopen = null
  onerror = null
  onmessage = null
  url: string
  constructor(url: string) { this.url = url }
  addEventListener() {}
  close() {}
}

const project = (slug: string, extra: Partial<Project> = {}): Project => ({
  id: `id-${slug}`, slug, name: slug[0].toUpperCase() + slug.slice(1), description: "",
  created_at: "2026-11-01T00:00:00Z", archived_at: null, archived: false, default: false,
  latest_revision: null, latest_revision_at: null, sources: 0, last_build: null, last_run: null,
  active: false, api_base: `/api/projects/${slug}`, ...extra,
})

const MAIN = project("main", { name: "Main ontology", default: true, latest_revision: 3,
                               latest_revision_at: "2026-11-20T10:00:00Z", sources: 2,
                               last_build: { id: "r1", kind: "build", status: "succeeded",
                                             started_at: "2026-11-20T09:00:00Z", finished_at: "2026-11-20T10:00:00Z" } })
const OLD = project("old", { name: "Old contracts", archived: true, archived_at: "2026-11-21T00:00:00Z" })
const SIDE = project("side", { name: "Side project" })

const STATUS = {
  status: "ok", version: "0.3.0", kordoc_available: true, ocr_ready: false, vision_model: null,
  formats: FORMATS, service_url: "http://127.0.0.1:7800",
  providers: {
    chat_provider: "claude", chat_model: "claude-sonnet-5-5", embed_provider: "ollama", embed_model: "bge-m3",
    openai_base_url: "",
    chat: { name: "claude", model: "claude-sonnet-5-5", available: true, logged_in: true,
            detail: "logged in", remedy: "", last_test: null },
    embed: { name: "ollama", model: "bge-m3" },
  },
  checks: { backend: { status: "ok", detail: "" }, postgres: { status: "ok", detail: "" } },
  doctor: "uv run onto doctor", upload_max_mb: 200,
}

const SOURCE = { name: "hanbit", kind: "files", locator: "/data/hanbit", database: null, built: true, uploaded: false,
                 created_at: "2026-11-01T00:00:00Z", last_run: null, active_run: null }

const noSnapshot = () => new Response(JSON.stringify({ error: "no_snapshot", detail: "no ontology yet" }),
                                      { status: 404, headers: { "content-type": "application/json" } })

function routes(list: Project[], extra: Record<string, unknown> = {}) {
  return {
    "GET /projects": () => ({ projects: list, default: "main" }),
    "GET /status": STATUS, "GET /sources": { sources: [] }, "GET /ontology": noSnapshot,
    "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": [],
    "GET /review/pending-count": { pending: 0, unapplied: 0, by_category: {} },
    ...extra,
  }
}

function Probe() {
  const { pathname, search, hash } = useLocation()
  return <output data-testid="location">{pathname + search + hash}</output>
}

const where = () => screen.getByTestId("location").textContent

function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TooltipProvider><App /><Probe /><Toaster /></TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...result, client }
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource)
  setCurrentProject(null)
})
afterEach(() => { setCurrentProject(null); setImplicitProject(null) })

describe("project paths", () => {
  it("put app paths under a project and read them back", () => {
    expect(projectPath("main", "/")).toBe("/p/main")
    expect(projectPath("main", "/build?x=1")).toBe("/p/main/build?x=1")
    expect(projectPath("main", "/?lang=ko")).toBe("/p/main?lang=ko")
    expect(projectPath("main", "/projects")).toBe("/projects")
    expect(projectPath("main", "/p/other/sources")).toBe("/p/other/sources")
    expect(projectPath(null, "/build")).toBe("/build")
    expect(pagePathname("/p/main/ontology")).toBe("/ontology")
    expect(pagePathname("/p/main")).toBe("/")
    expect(pagePathname("/projects")).toBe("/projects")
    expect(suggestSlug("Contracts 2026")).toBe("contracts-2026")
    expect(suggestSlug("계약 관리")).toBe("")
    // legacy export folder names (r0001) are not project slugs
    expect(["r0001", "r20261"].every((x) => RESERVED_SLUG_RE.test(x))).toBe(true)
    expect(["r12", "rx2026", "main"].some((x) => RESERVED_SLUG_RE.test(x))).toBe(false)
  })
})

describe("API base", () => {
  it("sends project-scoped calls to /api/projects/<slug> and keeps installation-wide ones", async () => {
    const { calls } = mockFetch({
      "GET /sources": { sources: [] }, "GET /status": STATUS,
      "POST /playground/sparql": { columns: [], rows: [], truncated: false, elapsed_ms: 1 },
      "GET /projects": { projects: [MAIN], default: "main" },
    })
    setCurrentProject("contracts")
    await api.sources()
    await api.status()
    await api.playground.sparql({ query: "SELECT * WHERE {}" })
    await api.projects.list()
    expect(calls.map((c) => c.url)).toEqual([
      "/api/projects/contracts/sources", "/api/status", "/api/projects/contracts/playground/sparql", "/api/projects",
    ])
    expect(api.runEventsUrl("run-1")).toBe("/api/projects/contracts/runs/run-1/events")
    expect(api.exportZipUrl("r0003")).toBe("/api/projects/contracts/exports/r0003.zip")
    expect(api.exportFileUrl("r0003", "rdf/계약.ttl")).toBe(
      "/api/projects/contracts/exports/r0003/rdf/%EA%B3%84%EC%95%BD.ttl")
    setCurrentProject(null)
    expect(api.runEventsUrl("run-1")).toBe("/api/runs/run-1/events")
  })

  it("keys project queries by the project slug", () => {
    setCurrentProject("a")
    expect(keys.sources).toEqual(["p", "a", "sources"])
    expect(keys.concept("c1")).toEqual(["p", "a", "concept", "c1"])
    setCurrentProject("b")
    expect(keys.sources).toEqual(["p", "b", "sources"])
    expect(keys.status).toEqual(["status"])
    expect(keys.projects).toEqual(["projects"])
  })

  it("says in the UI language that a project has no structured source for data questions", () => {
    const err = new ApiError("this project has no built structured source", 400, "no_structured_source")
    expect(errorMessage(err)).toMatch(/no built structured source .*Sources page/)
  })

  it("says in the UI language that an archived project is read-only", () => {
    const err = new ApiError("project 'old' is archived: unarchive it to change it", 409, "project_archived")
    expect(errorMessage(err)).toMatch(/archived \(read-only\)/)
  })
})

describe("a gateway that serves only the routes from before projects", () => {
  const refused = () => new Response("Forbidden", { status: 403, headers: { "content-type": "text/plain" } })

  it("falls back to one implicit default project on the /api/... aliases", async () => {
    const { calls } = mockFetch({ "GET /projects": refused, "GET /sources": { sources: [] } })
    const list = await api.projects.list()
    expect(list.implicit).toBe(true)
    expect(list.projects.map((p) => p.slug)).toEqual([list.default])
    setCurrentProject(list.default)
    await api.sources()
    expect(calls.map((c) => c.url)).toEqual(["/api/projects", "/api/sources"])
    expect(api.runEventsUrl("run-1")).toBe("/api/runs/run-1/events")
    expect(api.exportZipUrl("r0003")).toBe("/api/exports/r0003.zip")
  })

  it("opens the pages of the implicit project (404 too), its calls on the aliases", async () => {
    const notFound = () => new Response("Not Found", { status: 404 })
    const { calls } = mockFetch(routes([], { "GET /projects": notFound }))
    renderApp("/sources")
    await waitFor(() => expect(where()).toBe("/p/default/sources"))
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sources")).toBe(true))
    expect(calls.filter((c) => c.url.startsWith("/api/projects/"))).toEqual([])
  })

  it("keeps the real project list (and its canonical routes) when the service answers", async () => {
    mockFetch({ "GET /projects": { projects: [MAIN], default: "main" }, "GET /sources": { sources: [] } })
    setImplicitProject("default")
    expect((await api.projects.list()).implicit).toBeUndefined()
    setCurrentProject("default")
    expect(api.runEventsUrl("run-1")).toBe("/api/projects/default/runs/run-1/events")
  })
})

describe("routing", () => {
  it("sends an old /ui/<page> URL to the default project, keeping the query and hash", async () => {
    mockFetch(routes([MAIN, OLD]))
    renderApp("/sources?lang=ko#top")
    await waitFor(() => expect(where()).toBe("/p/main/sources?lang=ko#top"))
  })

  it("opens the default project's home for /ui/ and maps /runs to its build page", async () => {
    mockFetch(routes([MAIN, OLD]))
    const first = renderApp("/")
    await waitFor(() => expect(where()).toBe("/p/main"))
    first.unmount()
    renderApp("/runs?run=r1")
    await waitFor(() => expect(where()).toBe("/p/main/build?run=r1"))
  })

  it("redirects an unknown project to the default project with a notice", async () => {
    mockFetch(routes([MAIN, OLD]))
    renderApp("/p/ghost/review")
    await waitFor(() => expect(where()).toBe("/p/main/review"))
    expect(await screen.findByText(/There is no project "ghost"/)).toBeInTheDocument()
  })

  it("links the sidebar pages under the current project and names it in the breadcrumb", async () => {
    mockFetch(routes([MAIN, OLD]))
    renderApp("/p/main/sources")
    await waitFor(() => expect(document.querySelector("[data-nav=sources]")).toHaveAttribute("href", "/p/main/sources"))
    expect(document.querySelector("[data-nav=overview]")).toHaveAttribute("href", "/p/main")
    expect(document.getElementById("crumb-project")).toHaveTextContent("Main ontology")
    expect(document.getElementById("crumb-project")).toHaveAttribute("href", "/p/main")
  })
})

describe("project switcher", () => {
  it("lists the projects (archived ones apart) and opens the same page in another", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes([MAIN, OLD, SIDE]))
    renderApp("/p/main/sources")
    await waitFor(() => expect(document.getElementById("project-switcher-name")).toHaveTextContent("Main ontology"))
    await waitFor(() => expect(calls.some((c) => c.path === "/sources" && c.project === "main")).toBe(true))
    await user.click(document.getElementById("project-switcher")!)
    const menu = await screen.findByRole("menu")
    const items = within(menu).getAllByRole("menuitem").map((i) => i.getAttribute("data-project") ?? i.id)
    expect(items).toEqual(["main", "side", "old", "project-menu-new", "project-menu-all"])
    expect(within(menu).getByText("Archived", { selector: "[data-slot=dropdown-menu-label]" })).toBeInTheDocument()
    await user.click(within(menu).getByText("Side project"))
    await waitFor(() => expect(where()).toBe("/p/side/sources"))
    // a new query key: the other project's sources are fetched from its own base
    await waitFor(() => expect(calls.some((c) => c.path === "/sources" && c.project === "side")).toBe(true))
    expect(getCurrentProject()).toBe("side")
  })

  it("creates a project from the dialog and opens it", async () => {
    const user = userEvent.setup()
    const list = [MAIN, OLD]
    const { calls } = mockFetch(routes(list, {
      "POST /projects": (_url: string, init?: RequestInit) => {
        const body = JSON.parse(String(init!.body))
        const created = project(body.slug, { name: body.name, description: body.description })
        list.push(created)
        return new Response(JSON.stringify(created), { status: 201, headers: { "content-type": "application/json" } })
      },
    }))
    renderApp("/p/main/sources")
    await user.click(await screen.findByRole("button", { name: /Project: Main ontology/ }))
    await user.click(await screen.findByRole("menuitem", { name: /New project/ }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Name"), "Contracts")
    expect(within(dialog).getByLabelText("Slug")).toHaveValue("contracts")
    // the slug is checked as it is typed
    await user.clear(within(dialog).getByLabelText("Slug"))
    await user.type(within(dialog).getByLabelText("Slug"), "Main")
    expect(within(dialog).getByText(/already has this slug/)).toBeInTheDocument()
    await user.clear(within(dialog).getByLabelText("Slug"))
    await user.type(within(dialog).getByLabelText("Slug"), "9x")
    expect(within(dialog).getByText(/a lowercase letter first/)).toBeInTheDocument()
    await user.clear(within(dialog).getByLabelText("Slug"))
    await user.type(within(dialog).getByLabelText("Slug"), "contracts")
    await user.type(within(dialog).getByLabelText(/Description/), "Supplier contracts")
    await user.click(within(dialog).getByRole("button", { name: "Create project" }))
    await waitFor(() => expect(where()).toBe("/p/contracts"))
    const post = calls.find((c) => c.method === "POST" && c.path === "/projects")!
    expect(JSON.parse(String(post.init!.body))).toEqual({ name: "Contracts", slug: "contracts", description: "Supplier contracts" })
    // the empty project's home is the first-run checklist, asked of its own base
    await waitFor(() => expect(calls.some((c) => c.path === "/ontology" && c.project === "contracts")).toBe(true))
  })
})

describe("archived project", () => {
  it("shows the read-only banner and disables writes, but keeps pages readable", async () => {
    mockFetch(routes([MAIN, OLD], { "GET /sources": { sources: [SOURCE] } }))
    renderApp("/p/old/sources")
    expect(await screen.findByText("Old contracts is archived.")).toBeInTheDocument()
    expect(await screen.findByRole("button", { name: /Add source/ })).toBeDisabled()
    await waitFor(() => expect(document.querySelector("[data-delete=hanbit]")).toBeDisabled())
  })

  it("disables the build buttons of an archived project", async () => {
    mockFetch(routes([MAIN, OLD], { "GET /sources": { sources: [SOURCE] } }))
    renderApp("/p/old/build")
    await screen.findByText("Old contracts is archived.")
    await waitFor(() => expect(document.getElementById("btn-build")).toBeDisabled())
  })

  it("has no banner in an active project", async () => {
    mockFetch(routes([MAIN, OLD], { "GET /sources": { sources: [SOURCE] } }))
    renderApp("/p/main/build")
    await waitFor(() => expect(document.getElementById("btn-build")).toBeInTheDocument())
    expect(document.getElementById("archived-banner")).toBeNull()
  })
})

describe("projects page", () => {
  it("lists every project with its revision, sources and state", async () => {
    mockFetch(routes([MAIN, OLD, SIDE]))
    renderApp("/projects")
    const row = await screen.findByText("Main ontology", { selector: "a" })
    const main = row.closest<HTMLElement>("[data-project-row]")!
    expect(within(main).getByText("r3")).toBeInTheDocument()
    expect(within(main).getByText("Default")).toBeInTheDocument()
    expect(within(main).getByRole("link", { name: "Open Main ontology" })).toHaveAttribute("href", "/p/main")
    // the default project cannot be archived
    expect(within(main).getByRole("button", { name: "Archive Main ontology" })).toBeDisabled()
    const old = document.querySelector<HTMLElement>("[data-project-row=old]")!
    expect(within(old).getByText("Archived", { selector: "[data-badge]" })).toBeInTheDocument()
    expect(within(old).getByRole("button", { name: "Unarchive Old contracts" })).toBeEnabled()
    expect(screen.getByRole("heading", { level: 1, name: "Projects" })).toBeInTheDocument()
  })

  it("renames a project's slug and opens it at the new address", async () => {
    const user = userEvent.setup()
    const list = [MAIN, SIDE]
    const { calls } = mockFetch(routes(list, {
      "PATCH /projects/side": (_url: string, init?: RequestInit) => {
        const body = JSON.parse(String(init!.body))
        list[1] = { ...SIDE, ...body }
        return list[1]
      },
    }))
    renderApp("/projects")
    await user.click(await screen.findByRole("button", { name: "Edit Side project" }))
    const dialog = await screen.findByRole("dialog")
    await user.clear(within(dialog).getByLabelText("Slug"))
    await user.type(within(dialog).getByLabelText("Slug"), "side-two")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(where()).toBe("/p/side-two"))
    const patch = calls.find((c) => c.method === "PATCH")!
    expect(JSON.parse(String(patch.init!.body))).toEqual({ slug: "side-two" })
  })

  it("explains a refused rename (a run is active)", async () => {
    const user = userEvent.setup()
    mockFetch(routes([MAIN, SIDE], {
      "PATCH /projects/side": () => new Response(JSON.stringify({ error: "run_active", detail: "busy" }),
                                                  { status: 409, headers: { "content-type": "application/json" } }),
    }))
    renderApp("/projects")
    await user.click(await screen.findByRole("button", { name: "Edit Side project" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Slug"), "x")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(await screen.findByText(/A run of this project is active/)).toBeInTheDocument()
    expect(where()).toBe("/projects")
  })

  it("archives a project after confirming", async () => {
    const user = userEvent.setup()
    const list = [MAIN, SIDE]
    const { calls } = mockFetch(routes(list, {
      "POST /projects/side/archive": () => { list[1] = { ...SIDE, archived: true }; return list[1] },
    }))
    renderApp("/projects")
    await user.click(await screen.findByRole("button", { name: "Archive Side project" }))
    const confirm = await screen.findByRole("alertdialog")
    expect(within(confirm).getByText(/becomes read-only/)).toBeInTheDocument()
    await user.click(within(confirm).getByRole("button", { name: "Archive" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/projects/side/archive")).toBe(true))
    expect(await screen.findByRole("button", { name: "Unarchive Side project" })).toBeInTheDocument()
  })
})

describe("command menu", () => {
  it("offers to switch to the other projects", async () => {
    const user = userEvent.setup()
    mockFetch(routes([MAIN, OLD, SIDE]))
    renderApp("/p/main/ontology")
    await screen.findByRole("link", { name: "Sources" })
    await act(async () => { await user.keyboard("{Meta>}k{/Meta}") })
    const dialog = await screen.findByRole("dialog")
    await user.click(await within(dialog).findByText("Switch to Side project"))
    await waitFor(() => expect(where()).toBe("/p/side/ontology"))
  })
})

describe("connect page in a project", () => {
  it("names the project the tokens read and suggests one MCP server per project", async () => {
    const tools = {
      tools: [], protocol: "2025-06-18", mcp_path: "/mcp", rest_base: "/agent/v1", rate_per_min: 120,
      max_body_bytes: 1048576,
    }
    const { calls } = mockFetch(routes([MAIN, OLD, SIDE], {
      "GET /agent/tools": tools, "GET /agent/tokens": { tokens: [] },
      "GET /agent/usage": { days: 30, total: 0, ok: 0, by_tool: [], by_day: [], by_token: [] },
    }))
    renderApp("/p/side/connect")
    expect(await screen.findByText('These tokens only read the "Side project" project.')).toBeInTheDocument()
    await waitFor(() => expect(document.getElementById("howto-claudeCode")).toHaveTextContent(
      "claude mcp add --transport http ontology-side"))
    // the agent endpoint stays /mcp (the token picks the project)
    expect(document.getElementById("howto-claudeCode")).toHaveTextContent("/mcp --header")
    expect(calls.find((c) => c.path === "/agent/tokens")!.url).toBe("/api/projects/side/agent/tokens")
  })
})
