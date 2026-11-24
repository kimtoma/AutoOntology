import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router"
import { Toaster } from "sonner"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import { setCurrentProject, setImplicitProject, type Project } from "@/lib/api"
import {
  detailSummary, passwordStrength, readInviteHash, safeNext, type AuditEntry, type Me, type Member, type UserRow,
} from "@/lib/auth"
import { setLang } from "@/i18n/core"
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

const PERMS = {
  viewer: ["project.read", "exports.read", "playground.ask"],
  reviewer: ["project.read", "exports.read", "playground.ask", "review.decide", "review.apply"],
  editor: ["project.read", "exports.read", "playground.ask", "review.decide", "review.apply", "sources.write",
           "runs.start", "agent.tokens", "playground.manage"],
  owner: ["project.read", "exports.read", "playground.ask", "review.decide", "review.apply", "sources.write",
          "runs.start", "agent.tokens", "playground.manage", "project.manage", "members.manage", "audit.read"],
}
type RoleName = keyof typeof PERMS | "admin"

const project = (slug: string, role: RoleName, extra: Partial<Project> = {}): Project => ({
  id: `id-${slug}`, slug, name: slug[0].toUpperCase() + slug.slice(1), description: "",
  created_at: "2026-11-01T00:00:00Z", archived_at: null, archived: false, default: slug === "main",
  latest_revision: null, latest_revision_at: null, sources: 0, last_build: null, last_run: null,
  active: false, api_base: `/api/projects/${slug}`,
  role: role === "admin" ? "owner" : role, permissions: PERMS[role === "admin" ? "owner" : role], ...extra,
})

const USER = {
  id: "u1", email: "kim@example.com", name: "Kim", is_admin: false, created_at: "2026-11-01T00:00:00Z",
  disabled_at: null, last_login_at: "2026-11-24T00:00:00Z", has_password: true, active: true,
}

function me(role: RoleName | null, extra: Partial<Me> = {}): Me {
  if (role === null) {
    return {
      auth: "accounts", authenticated: false, actor: null, via: null, user: null, is_admin: false, memberships: [],
      permissions: {}, installation_permissions: [], magic_link_enabled: false, session_expires_at: null, ...extra,
    }
  }
  const admin = role === "admin"
  const r = admin ? "owner" : role
  return {
    auth: "accounts", authenticated: true, actor: USER.email, via: "session", user: { ...USER, is_admin: admin },
    is_admin: admin,
    memberships: admin ? [] : [{ project_id: "id-main", slug: "main", name: "Main", archived: false, role: r, permissions: PERMS[r] }],
    permissions: { main: PERMS[r] },
    installation_permissions: admin ? ["project.create", "settings.manage", "users.manage", "audit.read_all"] : [],
    magic_link_enabled: false, session_expires_at: "2026-11-25T00:00:00Z", ...extra,
  }
}

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
  doctor: "uv run autoontology doctor", upload_max_mb: 200,
}

const SOURCE = { name: "hanbit", kind: "files", locator: "/data/hanbit", database: null, built: true, uploaded: false,
                 created_at: "2026-11-01T00:00:00Z", last_run: null, active_run: null }

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } })
const noSnapshot = () => json(404, { error: "no_snapshot", detail: "no ontology yet" })

function routes(role: RoleName | null, extra: Record<string, unknown> = {}, projects?: Project[]) {
  const list = projects ?? [project("main", role ?? "viewer")]
  return {
    "GET /auth/me": me(role),
    "GET /projects": () => ({ projects: list, default: "main" }),
    "GET /status": STATUS, "GET /sources": { sources: [] }, "GET /ontology": noSnapshot,
    "GET /runs": { runs: [] }, "GET /exports": { exports: [] }, "GET /samples": [],
    "GET /review/pending-count": { pending: 0, unapplied: 0, by_category: {} },
    "GET /ontology/revisions": { revisions: [] },
    "GET /agent/tools": { tools: [], protocol: "2025-06-18", mcp_path: "/mcp", rest_base: "/agent/v1", rate_per_min: 60, max_body_bytes: 1 },
    "GET /agent/tokens": { tokens: [] },
    "GET /agent/usage": { days: 30, total: 0, by_token: [], by_tool: [], by_day: [] },
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
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TooltipProvider><App /><Probe /><Toaster /></TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource)
  setCurrentProject(null)
})
afterEach(() => { setCurrentProject(null); setImplicitProject(null) })

const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body ?? "{}"))

describe("helpers", () => {
  it("reads an invitation fragment, keeps next paths inside the app and rates passwords", () => {
    expect(readInviteHash("#token=abc&purpose=reset")).toEqual({ token: "abc", purpose: "reset" })
    expect(readInviteHash("#token=abc")).toEqual({ token: "abc", purpose: "invite" })
    expect(readInviteHash("#purpose=invite")).toBeNull()
    expect(safeNext("/p/main/sources?x=1")).toBe("/p/main/sources?x=1")
    for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil", "/login?next=/x"]) {
      expect(safeNext(bad)).toBe("/")
    }
    expect(passwordStrength("short")).toBe(0)
    expect(passwordStrength("abcdefghijkl")).toBe(1)
    expect(passwordStrength("Abcdefghijk1!xyz")).toBe(3)
    expect(detailSummary({ role: "editor", from: null, n: 2 })).toBe("role: editor · n: 2")
  })
})

describe("accounts off", () => {
  it("shows the app as before: no login page, no user menu, only GET /auth/me", async () => {
    const { calls } = mockFetch({ ...routes("admin"), "GET /auth/me": { ...me(null), auth: "off", authenticated: true } })
    renderApp("/sources")
    await waitFor(() => expect(where()).toBe("/p/main/sources"))
    expect(await screen.findByRole("heading", { name: "Sources" })).toBeInTheDocument()
    expect(document.getElementById("user-menu")).toBeNull()
    expect(document.getElementById("btn-new-source")).toBeEnabled()
    expect(calls.filter((c) => c.path.startsWith("/auth/")).map((c) => `${c.method} ${c.path}`)).toEqual(["GET /auth/me"])
    // Settings stays in the sidebar, Users does not appear
    expect(document.querySelector("a[data-nav=settings]")).not.toBeNull()
    expect(document.querySelector("a[data-nav=users]")).toBeNull()
  })

  it("treats a service without /api/auth/me (404) as accounts off", async () => {
    const r: Record<string, unknown> = routes("admin")
    delete r["GET /auth/me"]
    mockFetch(r)
    renderApp("/login")
    await waitFor(() => expect(where()).toBe("/p/main"))
    expect(document.getElementById("login-page")).toBeNull()
  })
})

describe("signing in", () => {
  it("sends a signed-out visitor to the login page and back to the page after signing in", async () => {
    let signedIn = false
    const { calls } = mockFetch(routes("editor", {
      "GET /auth/me": () => (signedIn ? me("editor") : me(null)),
      "POST /auth/login": () => { signedIn = true; return me("editor") },
    }))
    renderApp("/p/main/sources")
    await waitFor(() => expect(where()).toBe("/login?next=%2Fp%2Fmain%2Fsources"))
    expect(calls.some((c) => c.path === "/projects")).toBe(false)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText("Email"), "kim@example.com")
    await user.type(screen.getByLabelText("Password"), "correct horse battery")
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username")
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password")
    await user.click(screen.getByRole("button", { name: "Sign in" }))
    await waitFor(() => expect(where()).toBe("/p/main/sources"))
    const login = calls.find((c) => c.path === "/auth/login")!
    expect(bodyOf(login.init)).toEqual({ email: "kim@example.com", password: "correct horse battery" })
    expect(await screen.findByRole("button", { name: "Account: Kim" })).toBeInTheDocument()
  })

  it("says wrong credentials generically and keeps the email", async () => {
    mockFetch(routes(null, { "POST /auth/login": () => json(401, { error: "invalid_credentials", detail: "wrong email or password" }) }))
    renderApp("/login")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Email"), "kim@example.com")
    await user.type(screen.getByLabelText("Password"), "wrong password!")
    await user.click(screen.getByRole("button", { name: "Sign in" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.")
    expect(screen.getByLabelText("Email")).toHaveValue("kim@example.com")
    expect(screen.getByLabelText("Password")).toHaveValue("")
    expect(where()).toBe("/login")
  })

  it("names the wait after too many attempts (429 Retry-After)", async () => {
    mockFetch(routes(null, {
      "POST /auth/login": () => json(429, { error: "too_many_attempts", detail: "too many" }, { "retry-after": "30" }),
    }))
    renderApp("/login")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Email"), "kim@example.com")
    await user.type(screen.getByLabelText("Password"), "whatever password")
    await user.click(screen.getByRole("button", { name: "Sign in" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 30 seconds.")
  })

  it("asks for an empty field in Korean too", async () => {
    mockFetch(routes(null))
    setLang("ko", false)
    renderApp("/login")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "로그인" }))
    expect(screen.getByRole("alert")).toHaveTextContent("이메일과 비밀번호를 입력하세요.")
  })

  it("offers a sign-in link only with mail set up and always says to check the mail", async () => {
    mockFetch(routes(null))
    const first = renderApp("/login")
    await screen.findByLabelText("Email")
    expect(screen.queryByRole("button", { name: "Email me a sign-in link" })).toBeNull()
    first.unmount()

    const { calls } = mockFetch(routes(null, {
      "GET /auth/me": me(null, { magic_link_enabled: true }),
      "POST /auth/magic-link": () => json(202, { ok: true }),
    }))
    renderApp("/login")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Email me a sign-in link" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your email above")
    expect(calls.some((c) => c.path === "/auth/magic-link")).toBe(false)
    await user.type(screen.getByLabelText("Email"), "who@example.com")
    await user.click(screen.getByRole("button", { name: "Email me a sign-in link" }))
    expect(await screen.findByText("Check your email")).toBeInTheDocument()
    expect(screen.getByText(/If who@example.com has an account/)).toBeInTheDocument()
    expect(bodyOf(calls.find((c) => c.path === "/auth/magic-link")!.init)).toEqual({ email: "who@example.com" })
  })

  it("explains a bad sign-in link (?error=link)", async () => {
    mockFetch(routes(null))
    renderApp("/login?error=link")
    expect(await screen.findByText(/sign-in link is invalid, used or expired/)).toBeInTheDocument()
  })

  it("goes back to the login page when the session ends (401 login_required)", async () => {
    mockFetch(routes("editor", { "GET /sources": () => json(401, { error: "login_required", detail: "sign in first" }) }))
    renderApp("/p/main/sources")
    await waitFor(() => expect(where()).toBe("/login?next=%2Fp%2Fmain%2Fsources"))
    expect(await screen.findByRole("heading", { name: "Sign in to AutoOntology" })).toBeInTheDocument()
  })
})

describe("invitation page", () => {
  it("reads the token from the fragment, drops it from the URL and signs in with the new password", async () => {
    const { calls } = mockFetch(routes(null, { "POST /auth/accept-invite": () => me("viewer") }))
    renderApp("/invite#token=tok-123&purpose=invite")
    expect(await screen.findByRole("heading", { name: "Choose your password" })).toBeInTheDocument()
    await waitFor(() => expect(where()).toBe("/invite"))
    const user = userEvent.setup()
    const pw = screen.getByLabelText("New password")
    expect(pw).toHaveAttribute("autocomplete", "new-password")
    await user.type(pw, "short")
    await user.click(screen.getByRole("button", { name: "Set password and sign in" }))
    expect(calls.some((c) => c.path === "/auth/accept-invite")).toBe(false)
    expect(pw).toHaveAttribute("aria-invalid", "true")
    await user.clear(pw)
    await user.type(pw, "a much longer password")
    await user.type(screen.getByLabelText("Confirm the password"), "a much longer passwort")
    expect(screen.getByText("The two passwords differ.")).toBeInTheDocument()
    await user.clear(screen.getByLabelText("Confirm the password"))
    await user.type(screen.getByLabelText("Confirm the password"), "a much longer password")
    await user.click(screen.getByRole("button", { name: "Set password and sign in" }))
    await waitFor(() => expect(where()).toBe("/p/main"))
    expect(bodyOf(calls.find((c) => c.path === "/auth/accept-invite")!.init))
      .toEqual({ token: "tok-123", password: "a much longer password" })
  })

  it("titles a reset link and explains a link without a token", async () => {
    mockFetch(routes(null))
    const first = renderApp("/invite#token=t&purpose=reset")
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument()
    first.unmount()
    renderApp("/invite")
    expect(await screen.findByText(/needs the link from your invitation/)).toBeInTheDocument()
  })

  it("shows an expired link's error", async () => {
    mockFetch(routes(null, { "POST /auth/accept-invite": () => json(400, { error: "invalid_token", detail: "x" }) }))
    renderApp("/invite#token=old")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("New password"), "a much longer password")
    await user.type(screen.getByLabelText("Confirm the password"), "a much longer password")
    await user.click(screen.getByRole("button", { name: "Set password and sign in" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid, used or expired.")
  })
})

describe("user menu", () => {
  it("shows who is signed in with the role, changes the password and signs out", async () => {
    let signedIn = true
    const { calls } = mockFetch(routes("reviewer", {
      "GET /auth/me": () => (signedIn ? me("reviewer") : me(null)),
      "POST /auth/password": { ok: true, revoked_sessions: 1 },
      "POST /auth/logout": () => { signedIn = false; return { ok: true } },
    }))
    renderApp("/p/main/sources")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Account: Kim" }))
    const menu = await screen.findByRole("menu")
    expect(within(menu).getByText("kim@example.com")).toBeInTheDocument()
    expect(within(menu).getByText("Role here: Reviewer")).toBeInTheDocument()
    expect(within(menu).queryByText("Admin")).toBeNull()
    await user.click(within(menu).getByRole("menuitem", { name: "Change password" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Current password"), "old password 123")
    await user.type(within(dialog).getByLabelText("New password"), "new password 4567")
    await user.type(within(dialog).getByLabelText("Confirm the new password"), "new password 4567")
    await user.click(within(dialog).getByRole("button", { name: "Change password" }))
    expect(await screen.findByText("Password changed")).toBeInTheDocument()
    expect(bodyOf(calls.find((c) => c.path === "/auth/password")!.init))
      .toEqual({ current: "old password 123", new: "new password 4567" })

    await user.click(screen.getByRole("button", { name: "Account: Kim" }))
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }))
    await waitFor(() => expect(where()).toBe("/login"))
    expect(calls.some((c) => c.method === "POST" && c.path === "/auth/logout")).toBe(true)
  })

  it("marks an admin and links the users page", async () => {
    mockFetch(routes("admin", { "GET /users": { users: [] } }))
    renderApp("/p/main/sources")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Account: Kim" }))
    const menu = await screen.findByRole("menu")
    expect(within(menu).getByText("Admin")).toBeInTheDocument()
    await user.click(within(menu).getByRole("menuitem", { name: "Users" }))
    await waitFor(() => expect(where()).toBe("/users"))
  })
})

describe("role-aware pages", () => {
  it("lets a viewer look at sources but not change them", async () => {
    mockFetch(routes("viewer"))
    renderApp("/p/main/sources")
    expect(await screen.findByRole("note")).toHaveTextContent("Your role (Viewer) can look at the sources")
    expect(document.getElementById("btn-new-source")).toBeDisabled()
    // no settings for non-admins, no users page, no new project
    expect(document.querySelector("a[data-nav=settings]")).toBeNull()
    expect(document.querySelector("a[data-nav=users]")).toBeNull()
  })

  it("keeps a reviewer from builds and an editor from members", async () => {
    mockFetch(routes("reviewer", { "GET /sources": { sources: [SOURCE] } }))
    const first = renderApp("/p/main/build")
    await screen.findByRole("heading", { name: "Build" })
    await waitFor(() => expect(document.getElementById("btn-build")).toBeDisabled())
    expect(screen.getByRole("note")).toHaveTextContent("Reviewer")
    first.unmount()

    mockFetch(routes("editor"))
    renderApp("/p/main/sources")
    await screen.findByRole("heading", { name: "Sources" })
    expect(document.getElementById("btn-new-source")).toBeEnabled()
    expect(screen.queryByRole("note")).toBeNull()
    const user = userEvent.setup()
    await user.click(document.getElementById("project-switcher")!)
    await screen.findByRole("menu")
    expect(document.getElementById("project-menu-members")).toBeNull()
    expect(document.getElementById("project-menu-new")).toBeNull()
  })

  it("shows owners the members and audit pages in the switcher", async () => {
    mockFetch(routes("owner", { "GET /members": { members: [] } }))
    renderApp("/p/main/sources")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: /Project: Main/ }))
    expect(await screen.findByRole("menuitem", { name: "Audit log" })).toBeInTheDocument()
    await user.click(screen.getByRole("menuitem", { name: "Members" }))
    await waitFor(() => expect(where()).toBe("/p/main/members"))
  })

  it("gives admins settings, users and new projects", async () => {
    mockFetch(routes("admin"))
    renderApp("/p/main/sources")
    await screen.findByRole("heading", { name: "Sources" })
    expect(document.querySelector("a[data-nav=settings]")).not.toBeNull()
    expect(document.querySelector("a[data-nav=users]")).toHaveAttribute("href", "/users")
    const user = userEvent.setup()
    await user.click(document.getElementById("project-switcher")!)
    expect(await screen.findByRole("menuitem", { name: "New project" })).toBeInTheDocument()
  })

  it("explains agent tokens to a viewer without asking for them", async () => {
    const { calls } = mockFetch(routes("viewer"))
    renderApp("/p/main/connect")
    expect(await screen.findByText(/Managing agent tokens needs the editor role/)).toBeInTheDocument()
    expect(calls.some((c) => c.path === "/agent/tokens" || c.path === "/agent/usage")).toBe(false)
    expect(calls.some((c) => c.path === "/agent/tools")).toBe(true)
  })

  it("keeps the settings page and the provider test from non-admins", async () => {
    const { calls } = mockFetch(routes("owner"))
    const first = renderApp("/p/main/settings")
    expect(await screen.findByText("Only administrators can change the settings.")).toBeInTheDocument()
    expect(calls.some((c) => c.path.startsWith("/settings"))).toBe(false)
    first.unmount()
    renderApp("/p/main/status")
    await waitFor(() => expect(document.getElementById("provider-card")).not.toBeNull())
    expect(document.getElementById("btn-provider-test")).toBeNull()
  })

  it("lists only the caller's projects with their role on the projects page", async () => {
    const list = [project("main", "viewer"), project("side", "owner")]
    mockFetch(routes("viewer", { "GET /auth/me": me("viewer", { permissions: { main: PERMS.viewer, side: PERMS.owner } }) }, list))
    renderApp("/projects")
    const side = await screen.findByText("Side", { selector: "[data-project-row=side] a" })
    const row = side.closest("[data-project-row]") as HTMLElement
    expect(within(row).getByText("Owner")).toBeInTheDocument()
    expect(within(row).getByRole("link", { name: "Members of Side" })).toHaveAttribute("href", "/p/side/members")
    const mainRow = document.querySelector("[data-project-row=main]") as HTMLElement
    expect(within(mainRow).getByText("Viewer")).toBeInTheDocument()
    expect(within(mainRow).queryByRole("button", { name: /Edit/ })).toBeNull()
    expect(within(row).getByRole("button", { name: "Edit Side" })).toBeInTheDocument()
    expect(document.getElementById("btn-new-project")).toBeNull()
    // the switcher lists the memberships only
    const user = userEvent.setup()
    await user.click(document.getElementById("project-switcher")!)
    const menu = await screen.findByRole("menu")
    expect([...menu.querySelectorAll("[data-project]")].map((e) => e.getAttribute("data-project"))).toEqual(["main", "side"])
  })

  it("opens the user's own project when the default one is not theirs", async () => {
    const list = [project("side", "editor")]
    mockFetch(routes("editor", { "GET /auth/me": me("editor", { permissions: { side: PERMS.editor } }) }, list))
    renderApp("/")
    await waitFor(() => expect(where()).toBe("/p/side"))
  })

  it("says so when the user is in no project yet", async () => {
    mockFetch(routes("viewer", { "GET /auth/me": me("viewer", { permissions: {}, memberships: [] }) }, []))
    renderApp("/")
    await waitFor(() => expect(where()).toBe("/projects"))
    expect(await screen.findByText("You are not in any project yet")).toBeInTheDocument()
  })

  it("shows a toast naming the permission on 403 forbidden", async () => {
    mockFetch(routes("viewer", {
      "GET /sources": () => json(403, { error: "forbidden", detail: "this needs the 'project.read' permission", permission: "project.read" }),
    }))
    renderApp("/p/main/sources")
    expect(await screen.findByText("You do not have permission (project.read)", { selector: "[data-title]" })).toBeInTheDocument()
  })
})

const member = (email: string, role: string, extra: Partial<Member> = {}): Member => ({
  ...USER, id: `id-${email.split("@")[0]}`, email, name: email.split("@")[0], role, member_since: "2026-11-02T00:00:00Z", ...extra,
})

describe("members page", () => {
  it("lists members, adds one with an invitation link, changes a role and reports the last owner", async () => {
    let members = [member("kim@example.com", "owner", { id: "u1" }), member("lee@example.com", "viewer")]
    const { calls } = mockFetch(routes("owner", {
      "GET /members": () => ({ members }),
      "POST /members": (_u: string, init?: RequestInit) => {
        const b = bodyOf(init)
        const m = member(b.email, b.role, { has_password: false })
        members = [...members, m]
        return json(201, { member: m, created: true, mail_sent: false, invite_link: "https://ao.example/ui/invite#token=xyz&purpose=invite", expires_at: "2026-12-01T00:00:00Z" })
      },
      "PATCH /members/id-lee": (_u: string, init?: RequestInit) => {
        members = members.map((m) => (m.email === "lee@example.com" ? { ...m, role: bodyOf(init).role } : m))
        return members[1]
      },
      "DELETE /members/u1": () => json(409, { error: "last_owner", detail: "a project keeps at least one owner" }),
    }))
    renderApp("/p/main/members")
    const list = await screen.findByRole("list", { name: "Members" })
    await within(list).findByText("lee@example.com")
    expect(within(list).getByText("you")).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText("Email"), "new@example.com")
    await user.selectOptions(screen.getByLabelText("Role"), "editor")
    await user.click(screen.getByRole("button", { name: "Add" }))
    expect(await screen.findByText("Invitation link for new@example.com")).toBeInTheDocument()
    expect(document.getElementById("member-invite-link")).toHaveTextContent("https://ao.example/ui/invite#token=xyz&purpose=invite")
    expect(screen.getByRole("button", { name: /Copy Invitation link/ })).toBeInTheDocument()
    const add = calls.find((c) => c.method === "POST" && c.path === "/members")!
    expect(add.project).toBe("main")
    expect(bodyOf(add.init)).toEqual({ email: "new@example.com", role: "editor" })

    await user.selectOptions(screen.getByLabelText("Role of lee@example.com"), "reviewer")
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true))
    expect(bodyOf(calls.find((c) => c.method === "PATCH")!.init)).toEqual({ role: "reviewer" })
    expect(await screen.findByText("lee@example.com is now Reviewer")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Remove kim@example.com" }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: "Remove" }))
    expect(await screen.findByText(/keeps at least one owner/)).toBeInTheDocument()
  })

  it("is closed to non-owners", async () => {
    const { calls } = mockFetch(routes("editor"))
    renderApp("/p/main/members")
    expect(await screen.findByText("Only the project's owners can see and manage its members.")).toBeInTheDocument()
    expect(calls.some((c) => c.path === "/members")).toBe(false)
  })
})

const userRow = (email: string, extra: Partial<UserRow> = {}): UserRow => ({
  ...USER, id: `id-${email.split("@")[0]}`, email, name: "", memberships: [], ...extra,
})

describe("users page", () => {
  it("lists users, invites one with a link, and reports the last admin", async () => {
    let users = [userRow("kim@example.com", { id: "u1", is_admin: true, name: "Kim" }),
                 userRow("lee@example.com", { memberships: [{ project_id: "id-main", slug: "main", role: "viewer" }] })]
    const { calls } = mockFetch(routes("admin", {
      "GET /users": () => ({ users }),
      "POST /users": (_u: string, init?: RequestInit) => {
        const b = bodyOf(init)
        const u = userRow(b.email, { has_password: false, is_admin: !!b.is_admin })
        users = [...users, u]
        return json(201, { user: u, invite_link: "https://ao.example/ui/invite#token=inv&purpose=invite", expires_at: "2026-12-01T00:00:00Z", mail_sent: false })
      },
      "PATCH /users/u1": () => json(409, { error: "last_admin", detail: "keeps one admin" }),
      "POST /users/id-lee/link": (_u: string, init?: RequestInit) => ({
        email: "lee@example.com", purpose: bodyOf(init).purpose, link: "https://ao.example/auth/magic/m1",
        expires_at: "2026-11-24T12:15:00Z", mail_sent: false,
      }),
    }))
    renderApp("/users")
    const list = await screen.findByRole("list", { name: "Users" })
    const lee = await within(list).findByText("lee@example.com")
    expect(within(lee.closest("li")!).getByText("main")).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Invite user" }))
    const dialog = await screen.findByRole("dialog")
    await user.type(within(dialog).getByLabelText("Email"), "park@example.com")
    await user.click(within(dialog).getByLabelText(/Administrator/))
    await user.click(within(dialog).getByRole("button", { name: "Create invitation" }))
    expect(await within(dialog).findByText(/Mail is not set up/)).toBeInTheDocument()
    expect(document.getElementById("invite-link")).toHaveTextContent("token=inv")
    expect(bodyOf(calls.find((c) => c.method === "POST" && c.path === "/users")!.init))
      .toEqual({ email: "park@example.com", is_admin: true })
    await user.click(within(dialog).getByRole("button", { name: "Done" }))

    await user.click(screen.getByRole("button", { name: "Actions for kim@example.com" }))
    await user.click(await screen.findByRole("menuitem", { name: "Remove admin" }))
    expect(await screen.findByText("The installation keeps at least one active admin.")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Actions for lee@example.com" }))
    await user.click(await screen.findByRole("menuitem", { name: "Create sign-in link" }))
    const linkDialog = await screen.findByRole("dialog")
    await user.selectOptions(within(linkDialog).getByLabelText("Kind of link"), "reset")
    await user.click(within(linkDialog).getByRole("button", { name: "Create link" }))
    expect(await within(linkDialog).findByText("https://ao.example/auth/magic/m1")).toBeInTheDocument()
    expect(bodyOf(calls.find((c) => c.path.endsWith("/link"))!.init)).toEqual({ purpose: "reset", send: false })
  })

  it("disables and enables a user", async () => {
    let active = true
    const { calls } = mockFetch(routes("admin", {
      "GET /users": () => ({ users: [userRow("lee@example.com", { active, disabled_at: active ? null : "x" })] }),
      "PATCH /users/id-lee": (_u: string, init?: RequestInit) => {
        active = !bodyOf(init).disabled
        return userRow("lee@example.com", { active })
      },
    }))
    renderApp("/users")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Actions for lee@example.com" }))
    await user.click(await screen.findByRole("menuitem", { name: "Disable" }))
    await waitFor(() => expect(document.querySelector("[data-user='lee@example.com']")).toHaveAttribute("data-active", "false"))
    expect(bodyOf(calls.find((c) => c.method === "PATCH")!.init)).toEqual({ disabled: true })
  })

  it("is for admins only", async () => {
    const { calls } = mockFetch(routes("owner"))
    renderApp("/users")
    expect(await screen.findByText("Only administrators can manage users.")).toBeInTheDocument()
    expect(calls.some((c) => c.path === "/users")).toBe(false)
  })
})

const entry = (id: number, action = "member.add"): AuditEntry => ({
  id, at: "2026-11-24T10:00:00Z", user_id: "u1", actor: "kim@example.com", project_id: "id-main", project: "main",
  action, target: `t${id}@example.com`, detail: { role: "viewer" }, ip: "127.0.0.1",
})

describe("audit log", () => {
  it("pages a project's log with before_id and filters by action", async () => {
    const { calls } = mockFetch(routes("owner", {
      "GET /audit": (url: string) => {
        const sp = new URL(url, "http://x").searchParams
        if (sp.get("action")) return { entries: [entry(3, sp.get("action")!)], next_before_id: null }
        return sp.get("before_id") === "4"
          ? { entries: [entry(3), entry(2)], next_before_id: null }
          : { entries: [entry(5), entry(4)], next_before_id: 4 }
      },
    }))
    renderApp("/p/main/audit")
    const list = await screen.findByRole("list", { name: "Audit log" })
    expect(await within(list).findByText("t5@example.com")).toBeInTheDocument()
    expect(within(list).getAllByText("role: viewer")).toHaveLength(2)
    const first = new URL(calls.find((c) => c.path === "/audit")!.url, "http://x").searchParams
    expect(first.get("project")).toBe("main")
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Load more" }))
    expect(await within(list).findByText("t2@example.com")).toBeInTheDocument()
    expect(screen.getByText("That is everything.")).toBeInTheDocument()
    expect(new URL(calls.filter((c) => c.path === "/audit")[1].url, "http://x").searchParams.get("before_id")).toBe("4")

    await user.selectOptions(screen.getByLabelText("Action"), "token.create")
    expect(await screen.findByText("token.create", { selector: "[data-audit] *" })).toBeInTheDocument()
  })

  it("shows admins the whole log with a project column", async () => {
    const { calls } = mockFetch(routes("admin", { "GET /audit": { entries: [entry(1)], next_before_id: null } }))
    renderApp("/users/audit")
    expect(await screen.findByText("t1@example.com")).toBeInTheDocument()
    expect(new URL(calls.find((c) => c.path === "/audit")!.url, "http://x").searchParams.get("project")).toBeNull()
    expect(screen.getByRole("link", { name: "Audit log" })).toHaveAttribute("aria-current", "page")
  })

  it("is closed to non-owners", async () => {
    mockFetch(routes("editor"))
    renderApp("/p/main/audit")
    expect(await screen.findByText("Only the project's owners can read its audit log.")).toBeInTheDocument()
  })
})
