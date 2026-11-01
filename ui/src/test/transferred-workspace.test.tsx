import { screen } from "@testing-library/react"
import { AccessBoundary, IdentityGate, RestoredWorkspaceNotice } from "@/components/identity-gate"
import { mockFetch, renderPage } from "@/test/utils"

const session = {
  principal: { id: "person", username: "owner", name: "Owner", kind: "human", status: "active" },
  workspace: { workspace_id: "workspace", workspace_name: "Restored", organization_id: "org" },
  policy_version: 2, credential_id: "fresh", expires_at: 2000000000, grants: [],
  capabilities: { "knowledge.read": ["*"], "identity.admin": ["*"] },
}

it("keeps saved knowledge visible while explaining the restored consumer hold", async () => {
  mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace", restoration: { catalog_ready: false, source_authority_retired: true } },
    "GET /identity/me": session,
  })
  renderPage(<IdentityGate><RestoredWorkspaceNotice /><p>Stored knowledge</p><AccessBoundary capability="query.run"><p>Unreviewed query</p></AccessBoundary></IdentityGate>)
  expect(await screen.findByText("Stored knowledge")).toBeVisible()
  expect(screen.getByText("Restored workspace · data connections need review")).toBeVisible()
  expect(screen.getByRole("link", { name: "Review access and transfer history" })).toHaveAttribute("href", "/access")
  expect(screen.getByText("Waiting for restored data connections")).toBeVisible()
  expect(screen.queryByText("Unreviewed query")).not.toBeInTheDocument()
})

it("adds no restored notice to an ordinary local workspace", async () => {
  mockFetch({ "GET /identity/status": { configured: false, workspace_id: null } })
  renderPage(<IdentityGate><RestoredWorkspaceNotice /><p>Ordinary workspace</p></IdentityGate>)
  expect(await screen.findByText("Ordinary workspace")).toBeVisible()
  expect(screen.queryByText(/Restored workspace/)).not.toBeInTheDocument()
})

it("does not grant a held capability just because status says connections are ready", async () => {
  mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace", restoration: { catalog_ready: true, source_authority_retired: true } },
    "GET /identity/me": session,
  })
  renderPage(<IdentityGate><RestoredWorkspaceNotice /><AccessBoundary capability="query.run"><p>Unreviewed query</p></AccessBoundary></IdentityGate>)
  expect(await screen.findByText("This page is outside your current access.")).toBeVisible()
  expect(screen.queryByText("Unreviewed query")).not.toBeInTheDocument()
  expect(screen.getByText("Restored workspace · read connections reviewed")).toBeVisible()
})


it("shows only the reviewed read capability while retaining the effect boundary", async () => {
  mockFetch({
    "GET /identity/status": { configured: true, workspace_id: "workspace", restoration: { catalog_ready: true, source_authority_retired: true } },
    "GET /identity/me": { ...session, capabilities: { ...session.capabilities, "query.run": ["*"] } },
  })
  renderPage(<IdentityGate><RestoredWorkspaceNotice /><AccessBoundary capability="query.run"><p>Reviewed queries</p></AccessBoundary><AccessBoundary capability="candidate.prepare"><p>Unreviewed build</p></AccessBoundary></IdentityGate>)
  expect(await screen.findByText("Reviewed queries")).toBeVisible()
  expect(screen.getByText("Restored workspace · read connections reviewed")).toBeVisible()
  expect(screen.queryByText("Unreviewed build")).not.toBeInTheDocument()
  expect(screen.getByText("This page is outside your current access.")).toBeVisible()
})
