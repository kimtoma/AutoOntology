import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import AccessPage from "@/pages/access"
import { IdentityContext, type IdentitySession } from "@/lib/identity"
import { mockFetch, renderPage } from "@/test/utils"

const person = { id: "owner", username: "owner", name: "Owner", kind: "human" as const, status: "active", grants: [{ role: "admin", domain_id: "*" }], delegation: null }
const session: IdentitySession = { principal: person, workspace: { workspace_id: "workspace", workspace_name: "Workspace", organization_id: "org" }, credential_id: "own-session", expires_at: 2000000000, policy_version: 1, grants: person.grants, capabilities: { "identity.admin": ["*"] } }
const management = { workspace_id: "workspace", policy_version: 1, principals: [person], roles: { viewer: ["knowledge.read"], admin: ["identity.admin"] }, delegatable_actions: ["candidate.prepare"], domains: [{ id: "general", slug: "general", name: "General" }], sources: [] }
function renderAccess(admin = true) {
  return renderPage(<IdentityContext.Provider value={{ session: { ...session, capabilities: admin ? session.capabilities : {} }, signOut: vi.fn(), signingOut: false, logoutFailed: false }}><AccessPage /></IdentityContext.Provider>)
}

it("lets a viewer inspect and revoke their own session without loading administration", async () => {
  const { calls } = mockFetch({ "GET /identity/credentials": { items: [{ id: "own-session", principal_id: "owner", username: "owner", kind: "human", status: "active", current: true, created_at: 1800000000, expires_at: 2000000000 }] }, "DELETE /identity/credentials/own-session": { revoked: "own-session" } })
  renderAccess(false)
  expect(await screen.findByText("Current session")).toBeVisible()
  expect(calls.some(c => c.path === "/identity/management")).toBe(false)
  await userEvent.click(screen.getByRole("button", { name: "Revoke session" }))
  expect(calls.some(c => c.method === "DELETE")).toBe(false)
  expect(screen.getByRole("alertdialog")).toHaveTextContent("Sign-in will be required again")
  await userEvent.click(screen.getByRole("button", { name: "Confirm change" }))
  await waitFor(() => expect(calls.some(c => c.method === "DELETE")).toBe(true))
})

it("requires review before adding a person and clears password fields after success", async () => {
  const { calls } = mockFetch({ "GET /identity/management": management, "GET /identity/credentials": { items: [] }, "POST /identity/principals": { id: "new-person" } })
  const user = userEvent.setup()
  renderAccess()
  await user.click(screen.getByRole("button", { name: "People" }))
  await user.click(await screen.findByRole("button", { name: "Add person" }))
  await user.type(screen.getByLabelText("Username"), "analyst")
  await user.type(screen.getByLabelText("Display name"), "Analyst")
  await user.type(screen.getByLabelText("Initial password"), "private fixture password")
  await user.click(screen.getByRole("button", { name: "Review change" }))
  expect(screen.getByRole("alertdialog")).toHaveTextContent("analyst")
  expect(screen.getByRole("alertdialog")).not.toHaveTextContent("private fixture password")
  expect(calls.some(c => c.method === "POST")).toBe(false)
  await user.click(screen.getByRole("button", { name: "Confirm change" }))
  expect(await screen.findByRole("status")).toHaveTextContent("Change confirmed")
  expect(screen.queryByLabelText("Initial password")).not.toBeInTheDocument()
  const body = JSON.parse(String(calls.find(c => c.method === "POST")?.init?.body))
  expect(body.roles).toEqual(["viewer"])
  expect(body.domains).toEqual(["*"])
  expect(localStorage.getItem("password")).toBeNull()
})

it("keeps a failed confirmation reviewable and does not claim that the last admin was removed", async () => {
  mockFetch({ "GET /identity/management": management, "GET /identity/credentials": { items: [] }, "POST /identity/principals/owner/disable": () => new Response(JSON.stringify({ error: "last_administrator", detail: "internal details" }), { status: 409, headers: { "content-type": "application/json" } }) })
  const user = userEvent.setup()
  renderAccess()
  await user.click(screen.getByRole("button", { name: "People" }))
  await user.click(await screen.findByRole("button", { name: "Disable Owner" }))
  await user.click(screen.getByRole("button", { name: "Confirm change" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("Keep at least one active administrator")
  expect(screen.getByRole("alertdialog")).toBeVisible()
  expect(screen.queryByText("internal details")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
})
