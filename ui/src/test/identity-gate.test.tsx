import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IdentityControls, IdentityGate } from "@/components/identity-gate"
import { request } from "@/lib/api"
import { mockFetch, renderPage } from "@/test/utils"
import HomePage from "@/pages/home"

const denied = () => new Response(JSON.stringify({ error: "authentication_required", detail: "Sign in" }), { status: 401, headers: { "content-type": "application/json" } })
const session = { principal: { id: "person", username: "owner", name: "Owner", kind: "human", status: "active" }, workspace: { workspace_id: "workspace", workspace_name: "Workspace", organization_id: "org" }, policy_version: 1, credential_id: "credential", expires_at: 2000000000, grants: [], capabilities: { "knowledge.read": ["*"], "candidate.read": ["*"] } }
const page = <IdentityGate><span>Protected workspace</span><IdentityControls /></IdentityGate>

it("does not flash first-install guidance while the operational workspace is loading", async () => {
  let finish!: (value: Response) => void
  const pending = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal("fetch", vi.fn(() => pending))
  renderPage(<HomePage afterBuild={<span>Existing operations</span>} />)
  expect(await screen.findByRole("status")).toHaveTextContent("Loading operational knowledge")
  expect(screen.queryByRole("heading", { name: "Get started" })).not.toBeInTheDocument()
  finish(new Response(JSON.stringify({ revision: 8 }), { headers: { "content-type": "application/json" } }))
  expect(await screen.findByText("Existing operations")).toBeVisible()
})

it("keeps a legacy local workspace usable without inventing an individual identity", async () => {
  mockFetch({ "GET /identity/status": { configured: false, workspace_id: null } })
  renderPage(page)
  expect(await screen.findByText("Protected workspace")).toBeVisible()
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
})

it("requires login before mounting work and clears it after a confirmed logout", async () => {
  let signedIn = false
  const { calls } = mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace" },
    "GET /identity/me": () => signedIn ? session : denied(),
    "POST /identity/login": () => { signedIn = true; return { principal: session.principal } },
    "POST /identity/logout": () => { signedIn = false; return { signed_out: true } },
  })
  const user = userEvent.setup()
  renderPage(page)
  expect(await screen.findByRole("heading", { name: "Sign in to your workspace" })).toBeVisible()
  expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("Username"), "owner")
  await user.type(screen.getByLabelText("Password"), "fixture password")
  await user.click(screen.getByRole("button", { name: "Sign in" }))
  expect(await screen.findByText("Protected workspace")).toBeVisible()
  expect(JSON.parse(String(calls.find(c => c.path === "/identity/login")?.init?.body))).toEqual({ username: "owner", password: "fixture password", client: "browser" })
  expect(localStorage.getItem("token")).toBeNull()
  await user.click(screen.getByRole("button", { name: "Sign out" }))
  expect(await screen.findByLabelText("Password")).toBeVisible()
  expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument()
})

it("locks mounted work on a revoked HTTP credential without exposing internal errors", async () => {
  mockFetch({ "GET /identity/status": { configured: true, workspace_id: "workspace" }, "GET /identity/me": session, "GET /sensitive": denied() })
  renderPage(page)
  expect(await screen.findByText("Protected workspace")).toBeVisible()
  await request("/sensitive").catch(() => undefined)
  expect(await screen.findByLabelText("Password")).toBeVisible()
  await waitFor(() => expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument())
})

it("shows an actionable cooldown and never renders the password or raw internal detail", async () => {
  mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace" },
    "GET /identity/me": denied(),
    "POST /identity/login": () => new Response(JSON.stringify({ error: "login_throttled", detail: "internal detail must stay hidden", retry_after: 40 }), { status: 429, headers: { "content-type": "application/json" } }),
  })
  const user = userEvent.setup()
  renderPage(page)
  await user.type(await screen.findByLabelText("Username"), "owner")
  await user.type(screen.getByLabelText("Password"), "fixture password")
  await user.click(screen.getByRole("button", { name: "Sign in" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("40")
  expect(screen.getByLabelText("Password")).toHaveValue("")
  expect(screen.queryByText("internal detail must stay hidden")).not.toBeInTheDocument()
})
