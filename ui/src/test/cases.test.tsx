import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { CaseBoard } from "@/components/case-board"
import { IdentityContext, type IdentitySession } from "@/lib/identity"
import { mockFetch, renderPage } from "@/test/utils"

const session: IdentitySession = { principal: { id: "human", username: "reviewer", name: "Reviewer", kind: "human", status: "active" }, workspace: { workspace_id: "org", workspace_name: "Org", organization_id: "org" }, credential_id: "credential", expires_at: 2000000000, policy_version: 1, grants: [], capabilities: { "candidate.read": ["*"], "review.suggest": ["*"], "review.decide": ["*"] } }
const entry = { id: "case1", title: "Contract policy amendment", reason: "Changed approval threshold", owner_id: "human", priority: "high", due_at: "2026-11-01T00:00:00+00:00", status: "awaiting_review", version: 2, base_revision: 8, candidate_revision: 9, changes: [], links: [], created_at: "2026-10-31T00:00:00+00:00", updated_at: "2026-10-31T00:00:00+00:00", outcome: {}, receipts: [], history: [], stale: false }
const packet = { id: "packet1", sha256: "a".repeat(64), payload: { case_id: "case1", case_version: 2, executor_id: "human", expires_at: 2000000000, context: { base_revision: 8, candidate_revision: 9 }, decisions: [{ id: "change1", decision: "accept", note: "Evidence checked", edits: {} }], dependencies: [{ label: "Contract", requires: "change1", change_id: "change2" }], items: [{ id: "change1", label: "Approval authority", before_text: "Manager", after_text: "CEO", evidence: [{ quote: "CEO approval above threshold", document: "Policy", page: 2 }] }] } }

it("requires explicit confirmation of the prepared business packet", async () => {
  let applied = false
  const { calls } = mockFetch({ "GET /cases": { items: [entry] }, "GET /cases/members": { items: [{ id: "human", name: "Reviewer", can_review: true }] }, "GET /cases/case1": () => ({ ...entry, packet, status: applied ? "reviewed" : "awaiting_review", receipts: applied ? [{ id: "receipt" }] : [] }), "GET /ops/changes": { items: [], feedback: [] }, "POST /cases/packets/packet1/confirm": () => { applied = true; return { id: "receipt", status: "reviewed" } } })
  renderPage(<IdentityContext.Provider value={{ session, signOut: vi.fn(), signingOut: false, logoutFailed: false }}><CaseBoard /></IdentityContext.Provider>, "/review?tab=cases&case=case1")
  expect(await screen.findByText("CEO approval above threshold")).toBeVisible()
  expect(screen.getByText("Changed approval threshold")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "Confirm review packet" }))
  expect(screen.getByRole("button", { name: "Apply these review decisions" })).toBeDisabled()
  await userEvent.click(screen.getByRole("checkbox", { name: /I reviewed the changes/ }))
  await userEvent.click(screen.getByRole("button", { name: "Apply these review decisions" }))
  expect((await screen.findAllByText("Review applied"))[0]).toBeVisible()
  const call = calls.find(call => call.path === "/cases/packets/packet1/confirm")
  expect(JSON.parse(String(call?.init?.body))).toEqual({ sha256: "a".repeat(64), confirm: true })
})
