import { screen } from "@testing-library/react"
import ExportsPage from "@/pages/exports"
import { IdentityContext, type IdentitySession } from "@/lib/identity"
import { mockFetch, renderPage } from "@/test/utils"
import { AppShell } from "@/components/app-shell"
import { Route, Routes } from "react-router"

it("downloads the current scoped knowledge without requesting the full archived bundle", async () => {
  const session: IdentitySession = { principal: { id: "finance", username: "finance", name: "Finance", kind: "human", status: "active" }, workspace: { workspace_id: "org", workspace_name: "Org", organization_id: "org" }, credential_id: "session", expires_at: 2000000000, policy_version: 3, grants: [], capabilities: { "exports.read": ["finance"] } }
  const { calls } = mockFetch({ "GET /ontology/export": { revision: 5, counts: { classes: 2, documents: 1 }, access: { restricted: true, policy_version: 3 }, formats: ["ttl", "jsonld", "graphml"] } })
  renderPage(<IdentityContext.Provider value={{ session, signOut: vi.fn(), signingOut: false, logoutFailed: false }}><ExportsPage /></IdentityContext.Provider>)
  expect(await screen.findByText("Knowledge within your access")).toBeVisible()
  for (const format of ["Turtle", "JSON-LD", "GraphML"]) {
    expect(screen.getByRole("link", { name: format })).toHaveAttribute("href", expect.stringContaining("/ontology/export/"))
  }
  expect(calls.some(call => call.path === "/exports")).toBe(false)
})

it("keeps downloads reachable and removes unavailable question tabs for a domain reader", () => {
  mockFetch({})
  const session: IdentitySession = { principal: { id: "finance", username: "finance", name: "Finance", kind: "human", status: "active" }, workspace: { workspace_id: "org", workspace_name: "Org", organization_id: "org" }, credential_id: "session", expires_at: 2000000000, policy_version: 3, grants: [], capabilities: { "knowledge.read": ["finance"], "query.run": ["finance"], "exports.read": ["finance"] } }
  renderPage(<IdentityContext.Provider value={{ session, signOut: vi.fn(), signingOut: false, logoutFailed: false }}><Routes><Route element={<AppShell />}><Route path="*" element={<p>Scoped workspace</p>} /></Route></Routes></IdentityContext.Provider>, "/exports")
  const downloadLinks = screen.getAllByRole("link", { name: "Downloads" })
  expect(downloadLinks).toHaveLength(2)
  expect(downloadLinks[0]).toHaveAttribute("href", "/exports")
  expect(downloadLinks[0]).toHaveAttribute("aria-current", "page")
  expect(document.querySelector('a[href="/playground"]')).toBeNull()
})
