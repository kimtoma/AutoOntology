import { screen, waitFor } from "@testing-library/react"
import { useEffect } from "react"
import userEvent from "@testing-library/user-event"
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { IdentityControls, IdentityGate } from "@/components/identity-gate"
import { request, streamSse } from "@/lib/api"
import { mockFetch, renderPage } from "@/test/utils"
import HomePage from "@/pages/home"
import { AUTHORITY_CHANGED, IDENTITY_UNAVAILABLE } from "@/lib/auth-events"
import { useCanUse } from "@/lib/identity"

const denied = () => new Response(JSON.stringify({ error: "authentication_required", detail: "Sign in" }), { status: 401, headers: { "content-type": "application/json" } })
const session = { principal: { id: "person", username: "owner", name: "Owner", kind: "human", status: "active" }, workspace: { workspace_id: "workspace", workspace_name: "Workspace", organization_id: "org" }, policy_version: 1, credential_id: "credential", expires_at: 2000000000, grants: [], capabilities: { "knowledge.read": ["*"], "candidate.read": ["*"] } }
const page = <IdentityGate><span>Protected workspace</span><IdentityControls /></IdentityGate>

it("discards protected data immediately on lost enrollment and rechecks before retry", async () => {
  let text = "PRIVATE_BEFORE_LOSS"
  let cache: QueryClient | undefined
  const { calls } = mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace" },
    "GET /identity/me": session,
    "GET /knowledge": () => ({ text }),
    "GET /probe": () => new Response(JSON.stringify({ error: "identity_configuration_missing" }), { status: 503, headers: { "content-type": "application/json" } }),
  })
  function Knowledge() {
    const client = useQueryClient()
    useEffect(() => { cache = client }, [client])
    const query = useQuery({ queryKey: ["protected-knowledge"], queryFn: () => request<{ text: string }>("/knowledge") })
    return <span>{query.data?.text}</span>
  }
  renderPage(<IdentityGate><Knowledge /></IdentityGate>)
  expect(await screen.findByText(text)).toBeVisible()
  await expect(request("/probe")).rejects.toBeInstanceOf(Error)
  expect(await screen.findByRole("alert")).toHaveTextContent("Workspace access could not be checked")
  expect(screen.queryByText(text)).not.toBeInTheDocument()
  expect(cache?.getQueryData(["protected-knowledge"])).toBeUndefined()
  text = "Recovered current knowledge"
  await userEvent.setup().click(screen.getByRole("button", { name: "Retry connection" }))
  expect(await screen.findByText(text)).toBeVisible()
  expect(calls.filter(call => call.path === "/knowledge")).toHaveLength(2)
})

it("does not downgrade a previously managed session into legacy authority after retry", async () => {
  let configured = true
  mockFetch({
    "GET /identity/status": () => ({ configured, workspace_id: configured ? "workspace" : null }),
    "GET /identity/me": session,
    "GET /probe": () => new Response(JSON.stringify({ error: "identity_configuration_missing" }), { status: 503, headers: { "content-type": "application/json" } }),
  })
  renderPage(page)
  expect(await screen.findByText("Protected workspace")).toBeVisible()
  configured = false
  await expect(request("/probe")).rejects.toBeInstanceOf(Error)
  expect(await screen.findByRole("alert")).toHaveTextContent("Workspace access could not be checked")
  await userEvent.setup().click(screen.getByRole("button", { name: "Retry connection" }))
  expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument()
  expect(screen.getByRole("alert")).toBeVisible()
})

it("reports lost enrollment from an already running stream", async () => {
  const unavailable = vi.fn()
  window.addEventListener(IDENTITY_UNAVAILABLE, unavailable)
  mockFetch({ "POST /playground/chat": () => new Response('event: error\ndata: {"status":503,"error":"identity_configuration_missing"}\n\n', { headers: { "content-type": "text/event-stream" } }) })
  await streamSse("/playground/chat", {}, vi.fn())
  expect(unavailable).toHaveBeenCalledOnce()
  window.removeEventListener(IDENTITY_UNAVAILABLE, unavailable)
})

it("discards cached knowledge when the live policy changes", async () => {
  let version = 1
  const { calls } = mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace" },
    "GET /identity/me": () => ({ ...session, policy_version: version }),
    "GET /domain-knowledge": () => ({ text: version === 1 ? "PRIVATE_OLD" : "Allowed current data" }),
  })
  function Knowledge() {
    const data = useQuery({ queryKey: ["knowledge"], queryFn: () => request<{ text: string }>("/domain-knowledge") })
    return <span>{data.data?.text}</span>
  }
  renderPage(<IdentityGate><Knowledge /></IdentityGate>)
  expect(await screen.findByText("PRIVATE_OLD")).toBeVisible()
  version = 2
  window.dispatchEvent(new Event(AUTHORITY_CHANGED))
  expect(await screen.findByText("Allowed current data")).toBeVisible()
  expect(screen.getByRole("status")).toHaveTextContent("Access changed")
  expect(screen.queryByText("PRIVATE_OLD")).not.toBeInTheDocument()
  expect(calls.filter(call => call.path === "/domain-knowledge")).toHaveLength(2)
})

it("permits projected domain reads and queries", async () => {
  mockFetch({ "GET /identity/status": { configured: true, workspace_id: "workspace" }, "GET /identity/me": { ...session, capabilities: { "knowledge.read": ["finance"], "query.run": ["finance"] } } })
  function Authority() {
    return <span>{useCanUse("knowledge.read") ? "Scoped graph allowed" : "Graph denied"} / {useCanUse("query.run") ? "Query allowed" : "Query gate retained"}</span>
  }
  renderPage(<IdentityGate><Authority /></IdentityGate>)
  expect(await screen.findByText("Scoped graph allowed / Query allowed")).toBeVisible()
})

it("refreshes current authority after a stream reports policy drift", async () => {
  const changed = vi.fn()
  window.addEventListener(AUTHORITY_CHANGED, changed)
  mockFetch({ "POST /playground/chat": () => new Response('event: error\ndata: {"status":409,"error":"view_authority_changed"}\n\n', { headers: { "content-type": "text/event-stream" } }) })
  await streamSse("/playground/chat", {}, vi.fn())
  expect(changed).toHaveBeenCalledOnce()
  window.removeEventListener(AUTHORITY_CHANGED, changed)
})

it("locks the workspace when a running SSE response reports credential revocation", async () => {
  mockFetch({ "GET /identity/status": { configured: true, workspace_id: "workspace" }, "GET /identity/me": session, "POST /playground/chat": () => new Response('event: error\ndata: {"status":401,"error":"credential_invalid"}\n\n', { headers: { "content-type": "text/event-stream" } }) })
  renderPage(page)
  expect(await screen.findByText("Protected workspace")).toBeVisible()
  await streamSse("/playground/chat", { question: "test" }, vi.fn())
  expect(await screen.findByLabelText("Password")).toBeVisible()
  expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument()
})

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
