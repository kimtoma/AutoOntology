import { useState } from "react"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ComparisonPanel } from "@/components/comparison-panel"
import { mockFetch, renderPage } from "@/test/utils"
import { IdentityContext, type IdentitySession } from "@/lib/identity"

const report = {
  schema: "knowledge-comparison-report/v1", pack_id: "sample", title: "Controlled comparison", sha256: "a".repeat(64),
  pack_sha256: "b".repeat(64), attempts_sha256: "c".repeat(64), human_approval: false, baseline_measured: false,
  comparison_status: "constructed_examples", engine: { name: "frozen-sqlite-v1", sqlite_version: "test", validator_sha256: "d".repeat(64) },
  evidence: [], cases: [{ id: "one", question: "Contract total?", kind: "sql", definition: "One contract per row", reference_sql: "SELECT 10", reference: { columns: ["amount"], rows: [[10]] },
    arms: [{ id: "A", label: "Example", verdict: "different", result: { columns: ["amount"], rows: [[20]] }, answer: { state: "sql", sql: "SELECT 20", citations: [] } }] }],
}

it("keeps the previous report visibly tied to its input after edits and invalid retries", async () => {
  const user = userEvent.setup()
  const { calls } = mockFetch({ "GET /evaluation/sample": { pack: { title: "Input draft", tables: [{ rows: [[1],[2]] }], cases: [{question:"Preview question"}] }, attempts: [] }, "POST /evaluation/compare": report })
  renderPage(<ComparisonPanel />)
  expect(calls).toHaveLength(0)
  await user.click(screen.getByRole("button", { name: "Load Hanbit sample" }))
  const input = screen.getByRole("textbox", { name: "Inspect or edit input JSON and SQL", hidden: true })
  expect(await screen.findByRole("button", { name: "Compare inputs" })).toBeEnabled()
  expect(screen.getByText("1 frozen tables · 2 rows · 1 questions")).toBeVisible()
  expect(screen.getByText("Preview question")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "Compare inputs" }))
  expect(await screen.findByText("Controlled comparison")).toBeVisible()
  expect(screen.getByText("Different result")).toBeVisible()
  expect(screen.getByText(/Unapproved reference hypothesis/)).toBeVisible()
  await user.click(screen.getByText("Inspect or edit input JSON and SQL"))
  await user.clear(input)
  await user.type(input, "invalid")
  expect(screen.getByText(/Input changed/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "Compare inputs" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("Use valid comparison JSON")
  expect(calls.filter(c => c.method === "POST")).toHaveLength(1)
  expect(screen.getByText("Controlled comparison")).toBeVisible()
})

it("drops inputs and reports when the authenticated credential changes", async () => {
  const user = userEvent.setup()
  mockFetch({ "GET /evaluation/sample": { pack: {}, attempts: [] }, "POST /evaluation/compare": report })
  const session = (credential: string): IdentitySession => ({ principal: { id: "owner", username: "owner", name: "Owner", kind: "human", status: "active" }, workspace: { workspace_id: "workspace", workspace_name: "Local", organization_id: "org" }, credential_id: credential, policy_version: 1, expires_at: 9999999999, grants: [], capabilities: { "validation.run": ["*"] } })
  const value = (id: string) => ({ session: session(id), signOut: () => {}, signingOut: false, logoutFailed: false })
  function Fixture() {
    const [credential, setCredential] = useState("first")
    return <><button onClick={() => setCredential("second")}>Switch session</button><IdentityContext.Provider value={value(credential)}><ComparisonPanel /></IdentityContext.Provider></>
  }
  renderPage(<Fixture />)
  await user.click(screen.getByRole("button", { name: "Load Hanbit sample" }))
  await user.click(await screen.findByRole("button", { name: "Compare inputs" }))
  expect(await screen.findByText("Controlled comparison")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "Switch session" }))
  expect(screen.queryByText("Controlled comparison")).not.toBeInTheDocument()
})
