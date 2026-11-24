import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactElement } from "react"
import { MemoryRouter, Route, Routes } from "react-router"
import { Toaster } from "sonner"

import { setLang } from "@/i18n/core"
import type {
  ActionInfo, ActionPreview, ActionRun, LogicDef, ObjectDetail, ObjectType, ReviewItem,
} from "@/lib/api"
import { AuthContext, OFF_ME, type Me } from "@/lib/auth"
import { toYaml } from "@/lib/logic"
import { ProjectContext } from "@/lib/project"
import ConnectPage from "@/pages/connect"
import LogicPage from "@/pages/logic"
import ObjectDetailPage from "@/pages/object-detail"
import ObjectsPage from "@/pages/objects"
import ReviewPage from "@/pages/review"
import { mockFetch } from "@/test/utils"

/* --- roles ------------------------------------------------------------------------------------ */

const VIEWER = ["project.read", "exports.read", "playground.ask", "objects.read", "logic.read", "functions.call"]
const REVIEWER = [...VIEWER, "review.decide", "review.apply", "action.approve"]
const EDITOR = [...REVIEWER, "sources.write", "runs.start", "agent.tokens", "playground.manage", "logic.edit",
                "action.run", "workflow.run"]
const OWNER = [...EDITOR, "project.manage", "members.manage", "audit.read", "objects.manage", "logic.activate"]
const PERMS = { viewer: VIEWER, reviewer: REVIEWER, editor: EDITOR, owner: OWNER }
type Role = keyof typeof PERMS

function meAs(role: Role): Me {
  return {
    ...OFF_ME, auth: "accounts", authenticated: true, actor: "kim@example.com", via: "session", is_admin: false,
    user: { id: "u1", email: "kim@example.com", name: "Kim", is_admin: false, created_at: "", disabled_at: null,
            last_login_at: null, has_password: true, active: true },
    memberships: [{ project_id: "id-main", slug: "main", name: "Main", archived: false, role, permissions: PERMS[role] }],
    permissions: { main: PERMS[role] }, installation_permissions: [],
  }
}

/** Render *ui* at *path* (routes for the object detail page), as *role* (accounts mode) or off. */
function renderAt(ui: ReactElement, path: string, role?: Role) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const scope = { slug: "main", archived: false, project: role ? { role, permissions: PERMS[role] } as never : null }
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={role ? meAs(role) : OFF_ME}>
        <ProjectContext.Provider value={scope}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/objects/:type/:key" element={ui} />
              <Route path="*" element={ui} />
            </Routes>
            <Toaster />
          </MemoryRouter>
        </ProjectContext.Provider>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

/** The element *selector* matches, once it is rendered. */
async function found(selector: string): Promise<HTMLElement> {
  return waitFor(() => {
    const el = document.querySelector<HTMLElement>(selector)
    expect(el).not.toBeNull()
    return el!
  })
}

const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body ?? "{}"))
const NOW = "2026-11-25T02:00:00+00:00"

/* --- fixtures (shapes of the service's answers) ------------------------------------------------ */

const INV: ObjectType = {
  id: "ot_inv", name: "term-t-inv", class: "term-t-inv", label: "Invoice", source: "quickstart", schema: "public",
  table: "invoices", key: "id", key_columns: ["id"], key_origin: "inferred", key_checked: true, title_column: "state",
  links: [{ name: "fk-con", label: "contract refers to Contract", direction: "out", column: "contract", target: "term-t-con" }],
  columns: [{ name: "id", type: "text" }, { name: "contract", type: "text" }, { name: "amount", type: "bigint" },
            { name: "state", type: "text" }],
  status: "active", broken_reason: "", validated_revision: 1, decided_by: "local", created_at: NOW, updated_at: NOW,
}
const CUS: ObjectType = { ...INV, id: "ot_cus", name: "term-t-cus", class: "term-t-cus", label: "Customer", table: "customers",
                          key: null, key_columns: [], status: "proposed", links: [] }
const OLD: ObjectType = { ...INV, id: "ot_old", name: "term-t-old", class: "term-t-old", label: "Legacy", table: "legacy",
                          status: "broken", broken_reason: "source: table public.legacy is gone", links: [] }

const ROW = (key: string, extra: Partial<ObjectDetail> = {}) => ({
  type: "term-t-inv", key, title: "unpaid",
  values: { id: key, contract: "K1", amount: 9000, state: "unpaid" },
  source: { id: key, contract: "K1", amount: 9000, state: "unpaid" }, edits: {}, conflicts: [], ...extra,
})

const EDITED = {
  ...ROW("INV-2"),
  values: { id: "INV-2", contract: "K1", amount: 7000, state: "unpaid", stage: 2 },
  edits: {
    amount: { edit_id: 3, value: 7000, source_value: 8000, source_value_at_edit: 9000, conflict: true, operational: false,
              edited_by: "lee", edited_at: NOW, action_run_id: "act_1" },
    stage: { edit_id: 4, value: 2, source_value: null, source_value_at_edit: null, conflict: false, operational: true,
             edited_by: "lee", edited_at: NOW, action_run_id: "act_1" },
  },
  conflicts: ["amount"],
}

const DETAIL: ObjectDetail = {
  ...EDITED, type: INV,
  links: [{ ...INV.links[0], target_label: "Contract", target_status: "active" }],
  history: [{ id: 4, object_type: "term-t-inv", object_key: "INV-2", property: "stage", value: 2, old_value: null,
              source_value: null, operational: true, action_run_id: "act_1", edited_by: "lee", edited_at: NOW, reverted_at: null }],
  computed: { overdue_days: { value: 55, type: "number" }, ratio: { value: null, type: "number", error: { code: "division_by_zero", message: "division by zero" } } },
  actions: [{ name: "remind", label: "Record reminder", available: true },
            { name: "close", label: "Close contract", available: false, reason: "Already paid." }],
}

const ACTION: ActionInfo = {
  name: "remind", label: "Record reminder", description: "Record a reminder stage.", object_type: "term-t-inv",
  object_type_label: "Invoice",
  params: [{ name: "stage", type: "number", required: true, enum: [1, 2, 3, 4], default: 1, label: "Stage" },
           { name: "memo", type: "text", required: false, label: "" }],
  preconditions: ["Already paid."], effects: [{ set: "stage", operational: true }], notifies: 1,
  permission: { min_role: "editor" }, approval: { role: "owner", when: "param.stage >= 4" }, agent: "propose",
}

const preview = (stage: number): ActionPreview => ({
  action: "remind", label: "Record reminder", description: "", version: 1, object_type: "term-t-inv",
  object_type_label: "Invoice", key: "INV-2", title: "unpaid", params: { stage },
  preconditions: [{ index: 0, message: "Already paid.", ok: true }],
  effects: [{ index: 0, property: "stage", label: "stage", operational: true, old_value: 2, new_value: stage }],
  approval: stage >= 4 ? { required: true, role: "owner", reason: "when" } : { required: false, role: null, reason: "" },
  notify: [{ subject: "s", body: "b", to: ["role:editor"], recipients: 2, invalid: 0 }],
  permission: { min_role: "editor", allowed: true }, ok: true, can_submit: true,
})

const RUN = (over: Partial<ActionRun> = {}): ActionRun => ({
  id: "act_9", action: "remind", action_version: 1, object_type: "term-t-inv", object_key: "INV-3", params: { stage: 4 },
  status: "pending_approval", requested_by: "workflow:dunning", via: "workflow", token_id: null, workflow_run_id: "wfr_1",
  approver: null, approval_role: "owner", preview: { ...preview(4), key: "INV-3" }, result: {}, error: null,
  idempotency_key: null, created_at: NOW, decided_at: null, applied_at: null, reverted_at: null,
  can_approve: true, can_revert: false, ...over,
})

const DEF = (over: Partial<LogicDef> = {}): LogicDef => ({
  id: "ld_1", kind: "function", name: "overdue_days", label: "Overdue days", object_type: "Invoice", version: 2,
  status: "draft", origin: "extracted",
  spec: { kind: "function", name: "overdue_days", label: "Overdue days", object_type: "Invoice", returns: "number", expr: "today() - due" },
  evidence: [{ quote: "Overdue days count from the day after the due date.", document: "Billing rules" }],
  broken_reason: "", created_by: "lee", created_at: NOW, updated_at: NOW, ...over,
})

const COUNTS = { proposals: 0, axioms: 0, unaligned: 0, aliases: 0, objects: 1, logic: 2, stale_sources: 0, folds: 0, total: 3 }
const PENDING = { pending: 3, counts: COUNTS, unapplied: 0, approvals: 1 }
const LISTING = (items: ReviewItem[], category: string) => ({
  revision: 1, counts: COUNTS, unapplied: 0, status: "pending", category, page: 1, size: 20, total: items.length, pages: 1, items,
})

const LOGIC_ITEM = (ready: boolean): ReviewItem => ({
  id: ready ? "lg:lc_ok" : "lg:lc_bad", category: "logic", concept: null, term: null, relation: null, confidence: 0.8,
  rationale: "From article 6.", suggested_action: ready ? "accept" : "reject",
  evidence: [{ quote: "Overdue days count from the day after the due date.", document: "Billing rules", path: "rules.md", page: null, verified: ready }],
  logic: {
    candidate_id: ready ? "lc_ok" : "lc_bad", kind: "function", name: ready ? "overdue_days" : "past_due", label: ready ? "Overdue days" : "Past due",
    object_type: "Invoice", spec: {}, spec_yaml: ready ? "kind: function\nname: overdue_days\n" : "kind: function\nname: past_due\nexpr: due_date\n",
    errors: ready ? [] : [{ path: "expr", code: "unknown_field", message: "no field due_date", pos: 0 }],
    ready, evidence_verified: ready, document: "Billing rules", run_id: "r1", created_at: NOW, existing: null,
  },
  decision: null,
})

const OB_ITEM: ReviewItem = {
  id: "ob:term-t-cus", category: "objects", concept: null, term: null, relation: null, confidence: null, rationale: "",
  evidence: [], suggested_action: "accept", decision: null, object_type: { ...CUS, key: "cid", key_columns: ["cid"] },
}

const DECISION = (decision: "accept" | "reject", payload: Record<string, unknown> = {}) => ({
  id: 1, kind: "logic", subject_key: "lc_ok", decision, relation: null, payload, note: "", decided_by: "kim",
  decided_at: NOW, applied_revision: null,
})

/* --- Objects ---------------------------------------------------------------------------------- */

describe("Objects page", () => {
  it("lists types (active first, proposed and broken with their reason) and a type's objects", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /objects/types": { revision: 1, types: [OLD, CUS, INV] },
      "GET /objects/term-t-inv": (url: string) => ({
        type: INV, total: 2, limit: 25, offset: 0,
        items: url.includes("computed=1")
          ? [{ ...ROW("INV-1"), computed: { overdue_days: { value: 0, type: "number" } } },
             { ...EDITED, computed: { overdue_days: { value: 55, type: "number" } } }]
          : [ROW("INV-1"), EDITED],
      }),
    })
    renderAt(<ObjectsPage />, "/objects", "owner")
    const types = await screen.findByRole("list", { name: "Object types" })
    expect([...types.querySelectorAll("[data-type]")].map((e) => e.getAttribute("data-status"))).toEqual(["active", "proposed", "broken"])
    expect(types.querySelector("[data-type=term-t-old] [data-reason]")).toHaveTextContent("table public.legacy is gone")
    expect(types.querySelector("[data-type=term-t-cus]")).toHaveTextContent("No key yet")
    // the first active type's table: an edited cell with a conflict
    await waitFor(() => expect(document.querySelector("[data-object=INV-2]")).not.toBeNull())
    const cell = document.querySelector("[data-object=INV-2] [data-cell=amount]")!
    expect(cell).toHaveAttribute("data-edited", "true")
    expect(cell).toHaveAttribute("data-conflict", "true")
    expect(cell).toHaveTextContent("Conflict")
    expect(document.querySelector("[data-computed-col]")).toBeNull()
    await user.click(screen.getByRole("switch", { name: "Computed values" }))
    await waitFor(() => expect(document.querySelector("[data-object=INV-2] [data-computed=overdue_days]")).toHaveTextContent("55"))
    expect(calls.some((c) => c.url.includes("computed=1"))).toBe(true)
    await user.type(screen.getByLabelText("Search by key or title"), "INV-2")
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=INV-2"))).toBe(true))
  })

  it("binds a type's key and title (owners only)", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /objects/types": { revision: 1, types: [INV, CUS] },
      "GET /objects/term-t-inv": { type: INV, items: [], total: 0, limit: 25, offset: 0 },
      "PUT /objects/types/term-t-cus": (_u: string, init?: RequestInit) => ({ type: { ...CUS, ...bodyOf(init), status: "active" } }),
    })
    const { unmount } = renderAt(<ObjectsPage />, "/objects", "owner")
    const row = await screen.findByText("Customer")
    await user.click(within(row.closest("li")!).getByRole("button", { name: "Key and title" }))
    const dialog = await screen.findByRole("dialog", { name: "Bind Customer" })
    await user.selectOptions(within(dialog).getByLabelText("Key column"), "contract")
    await user.click(within(dialog).getByRole("button", { name: "Bind" }))
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true))
    expect(bodyOf(calls.find((c) => c.method === "PUT")!.init)).toEqual({ key: "contract", title: "state" })
    unmount()
    renderAt(<ObjectsPage />, "/objects", "editor")
    await screen.findByText("Customer")
    expect(screen.queryByRole("button", { name: "Key and title" })).toBeNull()
    expect(document.getElementById("role-notice")).toHaveTextContent("not bind object types")
  })
})

describe("Object detail", () => {
  function routes(extra: Record<string, unknown> = {}) {
    return {
      "GET /objects/term-t-inv/INV-2": DETAIL,
      "GET /objects/term-t-inv/INV-2/links/fk-con": { type: { name: "term-t-con" }, link: {}, total: 1, limit: 10, offset: 0,
                                                      items: [{ type: "term-t-con", key: "K1", title: "Logistics", values: {}, source: {}, edits: {}, conflicts: [] }] },
      "GET /actions": { actions: [ACTION] },
      "POST /actions/remind/preview": (_u: string, init?: RequestInit) => ({ preview: preview(Number(bodyOf(init).params.stage ?? 1)) }),
      ...extra,
    }
  }

  it("shows edited and conflicting values, operational properties, computed values, links and history", async () => {
    mockFetch(routes())
    renderAt(<ObjectDetailPage />, "/objects/term-t-inv/INV-2", "editor")
    expect(await screen.findByRole("heading", { name: "unpaid" })).toBeInTheDocument()
    const amount = document.querySelector("#object-values [data-prop=amount]")!
    expect(amount).toHaveAttribute("data-conflict", "true")
    expect(amount.querySelector("[data-mark=edited]")).toHaveTextContent("Edited")
    expect(amount.querySelector("[data-mark=conflict]")).toHaveTextContent("it now says 8,000 (it said 9,000)")
    expect(document.querySelector("#object-operational [data-prop=stage]")).toHaveTextContent("2")
    expect(document.querySelector("[data-computed=overdue_days]")).toHaveTextContent("55")
    expect(document.querySelector("[data-computed=ratio]")).toHaveTextContent("division by zero")
    expect(await screen.findByText("K1 · Logistics")).toHaveAttribute("href", "/p/main/objects/term-t-con/K1")
    expect(document.querySelector("[data-history='4']")).toHaveTextContent("stage")
    expect(document.querySelector("[data-run-link=act_1]")).toBeInTheDocument()
    const close = screen.getByRole("button", { name: "Close contract" })
    expect(close).toBeDisabled()
    expect(close).toHaveAttribute("title", "Already paid.")
  })

  it("runs an action: parameter form, live preview, submit with an idempotency key, applied toast", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes({
      "POST /actions/remind/submit": (_u: string, init?: RequestInit) =>
        ({ run: RUN({ id: "act_10", object_key: "INV-2", status: "applied", params: bodyOf(init).params }), created: true }),
    }))
    renderAt(<ObjectDetailPage />, "/objects/term-t-inv/INV-2", "editor")
    await user.click(await screen.findByRole("button", { name: "Record reminder" }))
    const dialog = await screen.findByRole("dialog", { name: "Record reminder" })
    const stage = within(dialog).getByLabelText(/Stage/)
    expect(stage).toHaveValue("1")
    await waitFor(() => expect(dialog.querySelector("[data-effect=stage] [data-new-value]")).toHaveTextContent("1"))
    expect(within(dialog).getByText("Applies at once, no approval needed")).toBeInTheDocument()
    expect(within(dialog).getByText("Mails: 1 message(s), 2 recipient(s)")).toBeInTheDocument()
    await user.selectOptions(stage, "3")
    await waitFor(() => expect(dialog.querySelector("[data-effect=stage] [data-new-value]")).toHaveTextContent("3"))
    await user.click(within(dialog).getByRole("button", { name: "Run" }))
    expect(await screen.findByText("Record reminder applied to INV-2")).toBeInTheDocument()
    const submit = calls.find((c) => c.method === "POST" && c.path === "/actions/remind/submit")!
    const body = bodyOf(submit.init)
    expect(body.params).toEqual({ stage: 3 })
    expect(body.key).toBe("INV-2")
    expect(body.idempotency_key).toMatch(/^ui-/)
  })

  it("says when an action waits for approval", async () => {
    const user = userEvent.setup()
    mockFetch(routes({ "POST /actions/remind/submit": { run: RUN({ object_key: "INV-2", via: "ui" }), created: true } }))
    renderAt(<ObjectDetailPage />, "/objects/term-t-inv/INV-2", "editor")
    await user.click(await screen.findByRole("button", { name: "Record reminder" }))
    const dialog = await screen.findByRole("dialog", { name: "Record reminder" })
    await user.selectOptions(within(dialog).getByLabelText(/Stage/), "4")
    await waitFor(() => expect(within(dialog).getByText("Needs approval by a Owner")).toBeInTheDocument())
    await user.click(within(dialog).getByRole("button", { name: "Ask for approval" }))
    expect(await screen.findByText("Submitted: waiting for approval by a Owner")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "View run" }).length).toBeGreaterThan(0)
  })
})

/* --- Review ------------------------------------------------------------------------------------- */

describe("Review: approvals, logic proposals and object types", () => {
  it("approves a run and shows the four-eyes refusal in words", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /review/pending-count": PENDING,
      "GET /actions/runs": { runs: [RUN(), RUN({ id: "act_8", object_key: "INV-4", requested_by: "kim@example.com", via: "ui" }),
                                    RUN({ id: "act_7", can_approve: false })] },
      "POST /actions/runs/act_9/approve": { run: RUN({ status: "applied", can_approve: false }) },
      "POST /actions/runs/act_8/approve": () => new Response(JSON.stringify({
        error: "self_approval", detail: "you requested this run: someone else must approve it" }),
        { status: 403, headers: { "content-type": "application/json" } }),
    })
    renderAt(<ReviewPage />, "/review?category=approvals", "owner")
    expect(await screen.findByRole("tab", { name: /Approvals/ })).toHaveAttribute("aria-selected", "true")
    await waitFor(() => expect(screen.getByRole("tab", { name: /Approvals/ }).querySelector("[data-count]")).toHaveAttribute("data-count", "1"))
    const card = await found("[data-approval-run=act_9]")
    expect(card.querySelector("[data-via=workflow]")).toHaveTextContent("Workflow")
    expect(card.querySelector("[data-object-link]")).toHaveAttribute("href", "/p/main/objects/term-t-inv/INV-3")
    expect(card.querySelector("[data-param=stage]")).toHaveTextContent("4")
    expect(document.querySelector("[data-approval-run=act_7]")).toBeNull()
    expect(document.getElementById("approvals-others")).toHaveTextContent("1 more wait")
    await user.click(within(card).getByRole("button", { name: "Approve" }))
    expect(await screen.findByText("Approved: Record reminder on INV-3")).toBeInTheDocument()
    expect(calls.some((c) => c.method === "POST" && c.path === "/actions/runs/act_9/approve")).toBe(true)
    const mine = document.querySelector<HTMLElement>("[data-approval-run=act_8]")!
    await user.click(within(mine).getByRole("button", { name: "Approve" }))
    expect(await within(mine).findByRole("alert")).toHaveTextContent("someone else must approve it (four-eyes rule)")
  })

  it("rejects a run with a note", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /review/pending-count": PENDING,
      "GET /actions/runs": { runs: [RUN()] },
      "POST /actions/runs/act_9/reject": { run: RUN({ status: "rejected", can_approve: false }) },
    })
    renderAt(<ReviewPage />, "/review?category=approvals", "owner")
    const card = await found("[data-approval-run=act_9]")
    await user.click(within(card).getByRole("button", { name: "Reject" }))
    await user.type(within(card).getByLabelText("Note (optional)"), "not yet")
    await user.click(within(card).getByRole("button", { name: "Reject" }))
    await waitFor(() => expect(calls.some((c) => c.path === "/actions/runs/act_9/reject")).toBe(true))
    expect(bodyOf(calls.find((c) => c.path === "/actions/runs/act_9/reject")!.init)).toEqual({ note: "not yet" })
  })

  it("accepts, edits and accepts, and rejects logic proposals", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /review/pending-count": PENDING,
      "GET /review": LISTING([LOGIC_ITEM(true), LOGIC_ITEM(false)], "logic"),
      "POST /review/lg:lc_ok/decision": { item: LOGIC_ITEM(true), decision: DECISION("accept", { version: 1, def_name: "overdue_days" }) },
      "POST /review/lg:lc_bad/decision": (_u: string, init?: RequestInit) => ({
        item: LOGIC_ITEM(false), decision: DECISION(bodyOf(init).decision, { version: 1, edited: true }) }),
      "POST /logic/validate": (_u: string, init?: RequestInit) => (String(bodyOf(init).spec).includes("due_date")
        ? { valid: false, kind: "function", name: "past_due", errors: [{ path: "expr", code: "unknown_field", message: "no field due_date", pos: 0 }] }
        : { valid: true, kind: "function", name: "past_due", errors: [] }),
    })
    renderAt(<ReviewPage />, "/review?category=logic", "editor")
    const ok = await found("[data-review-item='lg:lc_ok']")
    const bad = document.querySelector<HTMLElement>("[data-review-item='lg:lc_bad']")!
    expect(ok.querySelector("[data-ready-badge]")).toHaveTextContent("Ready to use")
    expect(ok.querySelector("[data-verified]")).toHaveTextContent("quote found in the document")
    expect(bad.querySelector("[data-ready-badge]")).toHaveTextContent("Needs an edit")
    expect(bad.querySelector("[data-spec-error=expr]")).toHaveTextContent("no field due_date")
    expect(within(bad).getByRole("button", { name: "Accept" })).toBeDisabled()
    await user.click(within(ok).getByRole("button", { name: "Show the definition" }))
    expect(ok.querySelector("[data-yaml]")).toHaveTextContent("name: overdue_days")
    await user.click(within(ok).getByRole("button", { name: "Accept" }))
    await waitFor(() => expect(ok).toHaveAttribute("data-decided", "accept"))
    expect(ok).toHaveTextContent("draft v1 saved")
    expect(bodyOf(calls.find((c) => c.path === "/review/lg:lc_ok/decision")!.init)).toEqual({ decision: "accept" })

    await user.click(within(bad).getByRole("button", { name: "Edit and accept" }))
    const area = within(bad).getByLabelText("Definition of Past due")
    await waitFor(() => expect(bad.querySelector("[data-spec-error=expr]")).not.toBeNull())
    expect(within(bad).getByRole("button", { name: "Accept edited" })).toBeDisabled()
    await user.clear(area)
    await user.type(area, "kind: function{enter}name: past_due{enter}expr: amount")
    await waitFor(() => expect(within(bad).getByRole("button", { name: "Accept edited" })).toBeEnabled(), { timeout: 3000 })
    await user.click(within(bad).getByRole("button", { name: "Accept edited" }))
    await waitFor(() => expect(bad).toHaveAttribute("data-decided", "accept"))
    const sent = bodyOf(calls.find((c) => c.path === "/review/lg:lc_bad/decision")!.init)
    expect(sent).toEqual({ decision: "accept", spec: "kind: function\nname: past_due\nexpr: amount" })
    expect(bad).toHaveTextContent("edited draft v1 saved")
  })

  it("rejects a logic proposal and lists object type proposals by name for owners", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch({
      "GET /review/pending-count": PENDING,
      "GET /review": (url: string) => (url.includes("category=objects") ? LISTING([OB_ITEM], "objects") : LISTING([LOGIC_ITEM(false)], "logic")),
      "POST /review/lg:lc_bad/decision": { item: LOGIC_ITEM(false), decision: DECISION("reject") },
      "POST /review/ob:term-t-cus/decision": { item: OB_ITEM, decision: { ...DECISION("accept"), kind: "object" } },
    })
    const { unmount } = renderAt(<ReviewPage />, "/review?category=logic", "owner")
    const bad = await found("[data-review-item='lg:lc_bad']")
    await user.click(within(bad).getByRole("button", { name: "Reject" }))
    await waitFor(() => expect(bad).toHaveAttribute("data-decided", "reject"))
    expect(bodyOf(calls.find((c) => c.path === "/review/lg:lc_bad/decision")!.init)).toEqual({ decision: "reject" })
    unmount()
    renderAt(<ReviewPage />, "/review?category=objects", "owner")
    const ob = await found("[data-review-item='ob:term-t-cus']")
    // the type's label, never the raw item id
    expect(ob).toHaveTextContent("Customer")
    expect(ob).not.toHaveTextContent("ob:term-t-cus")
    expect(ob.querySelector("[data-key-proposal]")).toHaveTextContent("cid")
    await user.click(within(ob).getByRole("button", { name: "Accept" }))
    await waitFor(() => expect(calls.some((c) => c.path === "/review/ob:term-t-cus/decision")).toBe(true))
  })

  it("keeps object type decisions for owners", async () => {
    mockFetch({ "GET /review/pending-count": PENDING, "GET /review": LISTING([OB_ITEM], "objects") })
    renderAt(<ReviewPage />, "/review?category=objects", "editor")
    const ob = await found("[data-review-item='ob:term-t-cus']")
    expect(within(ob).getByRole("button", { name: "Accept" })).toBeDisabled()
  })
})

/* --- Logic ------------------------------------------------------------------------------------- */

describe("Logic page", () => {
  const ACT_DEF = DEF({ id: "ld_2", kind: "action", name: "remind", label: "Record reminder", status: "draft", origin: "manual",
                        evidence: [], spec: { kind: "action", name: "remind", label: "Record reminder", object_type: "Invoice" } })
  function routes(extra: Record<string, unknown> = {}) {
    return {
      "GET /logic": (url: string) => ({ definitions: url.includes("kind=action") ? [ACT_DEF] : url.includes("kind=workflow") ? [] : [DEF()] }),
      "GET /logic/function/overdue_days": { definition: DEF(), history: [
        { version: 2, status: "draft", spec: DEF().spec, note: "created", changed_by: "lee", changed_at: NOW },
        { version: 1, status: "retired", spec: { ...DEF().spec, expr: "0" }, note: "status retired", changed_by: "lee", changed_at: NOW }] },
      "GET /logic/action/remind": { definition: ACT_DEF, history: [] },
      "POST /logic/validate": (_u: string, init?: RequestInit) => (String(bodyOf(init).spec).includes("nope")
        ? { valid: false, kind: "function", name: "overdue_days", errors: [{ path: "expr", code: "syntax_error", message: "expected ')'", pos: 9 }] }
        : { valid: true, kind: "function", name: "overdue_days", errors: [] }),
      "GET /objects/types": { revision: 1, types: [INV] },
      "GET /objects/term-t-inv": { type: INV, items: [ROW("INV-1"), ROW("INV-2")], total: 2, limit: 8, offset: 0 },
      ...extra,
    }
  }

  it("lists definitions with status, version, origin and evidence; the editor validates, saves and activates", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes({
      "POST /logic": (_u: string, init?: RequestInit) => ({ definition: DEF({ version: 3, spec: { ...DEF().spec, expr: "1 + 1" } }), _sent: bodyOf(init) }),
      "POST /logic/function/overdue_days/status": { definition: DEF({ status: "active" }) },
      "POST /logic/test": { function: "overdue_days", type: "number", errors: [],
                            results: [{ key: "INV-2", title: "unpaid", value: 55, type: "number" }] },
    }))
    renderAt(<LogicPage />, "/logic", "editor")
    const row = await found("[data-def=overdue_days]")
    expect(row.querySelector("[data-state]")).toHaveTextContent("Draft")
    expect(row).toHaveTextContent("v2")
    expect(row.querySelector("[data-origin]")).toHaveTextContent("From documents")
    expect(row).toHaveTextContent("Overdue days count from the day after the due date.")
    await user.click(within(row).getByRole("button", { name: "Overdue days" }))
    const editor = await screen.findByRole("dialog", { name: /Overdue days/ })
    const area = await within(editor).findByLabelText("Definition (YAML)")
    expect((area as HTMLTextAreaElement).value).toBe(toYaml(DEF().spec))
    expect(await within(editor).findByText("Valid")).toBeInTheDocument()
    // an error with its path and position
    await user.clear(area)
    await user.type(area, "kind: function{enter}expr: nope(")
    expect(await within(editor).findByText("expected ')'", { exact: false }, { timeout: 3000 })).toBeInTheDocument()
    expect(editor.querySelector("[data-spec-error=expr]")).toHaveTextContent("position 9")
    expect(within(editor).getByRole("button", { name: "Save draft" })).toBeDisabled()
    // a version from the history back in the editor, then save
    await user.click(within(editor).getAllByRole("button", { name: "View" })[1])
    await user.click(within(editor).getByRole("button", { name: "Load version 1 into the editor" }))
    await waitFor(() => expect(within(editor).getByRole("button", { name: "Save draft" })).toBeEnabled(), { timeout: 3000 })
    await user.click(within(editor).getByRole("button", { name: "Save draft" }))
    expect(await screen.findByText("Overdue days saved as draft v3")).toBeInTheDocument()
    expect(bodyOf(calls.find((c) => c.method === "POST" && c.path === "/logic")!.init).spec).toContain("expr: '0'")
    // try the function on objects
    await user.click(await within(editor).findByRole("button", { name: /INV-2/ }))
    await user.click(within(editor).getByRole("button", { name: "Evaluate" }))
    expect(await within(editor).findByText("55")).toBeInTheDocument()
  })

  it("splits activation: editors activate functions, only owners activate actions", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes({ "POST /logic/function/overdue_days/status": { definition: DEF({ status: "active" }) } }))
    const { unmount } = renderAt(<LogicPage />, "/logic?tab=functions&def=overdue_days", "editor")
    const editor = await screen.findByRole("dialog", { name: /Overdue days/ })
    const activate = await within(editor).findByRole("button", { name: "Activate" })
    await waitFor(() => expect(activate).toBeEnabled())
    await user.click(activate)
    const confirm = await screen.findByRole("alertdialog", { name: "Activate Overdue days?" })
    await user.click(within(confirm).getByRole("button", { name: "Activate" }))
    await waitFor(() => expect(calls.some((c) => c.path === "/logic/function/overdue_days/status")).toBe(true))
    expect(bodyOf(calls.find((c) => c.path === "/logic/function/overdue_days/status")!.init)).toEqual({ status: "active" })
    unmount()
    renderAt(<LogicPage />, "/logic?tab=actions&def=remind", "editor")
    const actionEditor = await screen.findByRole("dialog", { name: /Record reminder/ })
    const act = await within(actionEditor).findByRole("button", { name: "Activate" })
    expect(act).toBeDisabled()
    expect(act).toHaveAttribute("title", "Your role (Editor) cannot do this")
  })

  it("runs a workflow now and follows its run", async () => {
    const user = userEvent.setup()
    const WF = DEF({ id: "ld_3", kind: "workflow", name: "dunning", label: "Dunning", status: "active", origin: "manual", evidence: [],
                     spec: { kind: "workflow", name: "dunning", label: "Dunning" } })
    let polls = 0
    const running = { id: "wfr_2", workflow: "dunning", workflow_version: 1, trigger: "manual", fire_slot: "m", status: "running",
                      started_by: "kim", summary: { matched: 0 }, error: null, started_at: NOW, finished_at: null }
    const { calls } = mockFetch(routes({
      "GET /logic": (url: string) => ({ definitions: url.includes("kind=workflow") ? [WF] : [] }),
      "GET /workflows": { triggers: "on", workflows: [{ name: "dunning", label: "Dunning", description: "", version: 1,
        trigger: { schedule: "daily 02:00" }, object_type: "term-t-inv", object_type_label: "Invoice", max_objects: 500,
        next_slot: "2026-11-26T02:00", activated_by: "kim", last_run: null }] },
      "POST /workflows/dunning/run": { run: running },
      "GET /workflows/runs/wfr_2": () => {
        polls += 1
        return polls < 2 ? { run: { ...running, objects: [], action_runs: [] } } : { run: {
          ...running, status: "paused",
          summary: { matched: 3, completed: 2, actions_applied: 1, pending_approval: 1, already_pending: 1, truncated: true },
          objects: [{ key: "INV-6", title: "unpaid", status: "paused", steps: [
            { path: "steps[0]", kind: "action", action: "remind", params: { stage: 4 }, action_run_id: "act_9", result: "pending_approval" }] }],
          action_runs: [RUN()],
        } }
      },
    }))
    renderAt(<LogicPage />, "/logic?tab=workflows", "editor")
    const row = await found("[data-def=dunning]")
    await waitFor(() => expect(row.querySelector("[data-workflow-info]")).toHaveTextContent("Scheduled: daily 02:00"))
    expect(row.querySelector("[data-next-slot]")).toHaveTextContent("2026-11-26 02:00")
    await user.click(within(row).getByRole("button", { name: "Run now" }))
    const sheet = await screen.findByRole("dialog", { name: /dunning/ })
    await waitFor(() => expect(sheet.querySelector("#workflow-run-status")).toHaveAttribute("data-status", "paused"), { timeout: 4000 })
    expect(sheet.querySelector("[data-counter=already_pending]")).toHaveTextContent("1")
    expect(sheet.querySelector("#workflow-truncated")).toBeInTheDocument()
    expect(sheet.querySelector("[data-wf-object=INV-6]")).toHaveTextContent("Waiting for approval")
    expect(sheet.querySelector("[data-wf-action-run=act_9]")).toBeInTheDocument()
    expect(within(sheet).getByRole("button", { name: "Cancel run" })).toBeEnabled()
    expect(calls.filter((c) => c.method === "POST" && c.path === "/workflows/dunning/run")).toHaveLength(1)
  })

  it("lists action runs with filters and starts an extraction", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes({
      "GET /actions/runs": { runs: [RUN(), RUN({ id: "act_10", status: "applied", can_approve: false, can_revert: true, via: "agent", requested_by: "token:cursor" })] },
      "GET /workflows/runs": { runs: [] },
      "POST /runs": { run_id: "lr1" },
      "GET /runs/lr1": { id: "lr1", kind: "logic", status: "running", started_at: NOW, finished_at: null, error: null, params: {},
                         managed: true, steps: [], progress: {}, source: null },
    }))
    renderAt(<LogicPage />, "/logic?tab=runs", "editor")
    const list = await found("#action-runs")
    expect(list.querySelector("[data-action-run=act_10] [data-via=agent]")).toHaveTextContent("Agent")
    expect(within(list.querySelector<HTMLElement>("[data-action-run=act_10]")!).getByRole("button", { name: "Revert" })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText("Status"), "applied")
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=applied"))).toBe(true))
    vi.stubGlobal("EventSource", class { close() {} addEventListener() {} })
    await user.click(screen.getByRole("button", { name: "Extract from documents" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/runs")).toBe(true))
    expect(bodyOf(calls.find((c) => c.method === "POST" && c.path === "/runs")!.init)).toEqual({ kind: "logic" })
  })

  it("reads in Korean", async () => {
    setLang("ko", false)
    mockFetch(routes())
    renderAt(<LogicPage />, "/logic", "viewer")
    expect(await screen.findByRole("heading", { name: "로직" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "펑션" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "문서에서 추출" })).toBeDisabled()
    expect(await screen.findByText("초안")).toBeInTheDocument()
  })
})

/* --- Connect: token scopes ---------------------------------------------------------------------- */

describe("Connect: token scopes", () => {
  const TOOLS = {
    tools: [{ name: "search_objects", description: "Search.", inputSchema: { type: "object" } },
            { name: "submit_action", description: "WRITE: submit.", inputSchema: { type: "object" } }],
    write_tools: ["submit_action"], scopes: ["read", "propose", "execute"],
    protocol: "2025-06-18", mcp_path: "/mcp", rest_base: "/agent/v1", rate_per_min: 60, max_body_bytes: 1,
  }
  const TOKEN = { id: "tok_1", name: "bot", status: "active", revision_pin: null, expires_at: null, created_at: NOW,
                  last_used_at: null, revoked_at: null, scopes: ["read", "execute", "action:remind"] }
  const routes = (extra: Record<string, unknown> = {}) => ({
    "GET /agent/tools": TOOLS, "GET /agent/tokens": { tokens: [TOKEN] },
    "GET /agent/usage": { days: 30, total: 0, ok: 0, by_tool: [], by_day: [], by_token: [] },
    "GET /ontology/revisions": { revisions: [] }, "GET /actions": { actions: [ACTION] }, ...extra,
  })

  it("shows scopes, marks write tools and keeps execute for owners", async () => {
    const user = userEvent.setup()
    mockFetch(routes())
    renderAt(<ConnectPage />, "/connect", "editor")
    const row = await found("[data-token=tok_1]")
    expect(row.querySelector("[data-scope]")).toHaveAttribute("data-scope", "execute")
    expect(row.querySelector("[data-actions]")).toHaveTextContent("only: remind")
    expect(within(row).getByRole("button", { name: /Rotate/ })).toBeDisabled()
    expect(document.querySelector("[data-tool=submit_action] [data-write-tool]")).toHaveTextContent("Writes")
    expect(document.querySelector("[data-tool=search_objects] [data-write-tool]")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Create token" }))
    const dialog = await screen.findByRole("dialog", { name: "Create a token" })
    expect(within(dialog).getByRole("radio", { name: /Execute/ })).toBeDisabled()
    expect(within(dialog).getByRole("radio", { name: /Read/ })).toBeChecked()
    expect(within(dialog).getByRole("radio", { name: /Propose/ })).toBeEnabled()
  })

  it("creates a propose token limited to chosen actions; owners may pick execute", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(routes({
      "POST /agent/tokens": (_u: string, init?: RequestInit) => ({ token: "aok_x", record: { ...TOKEN, name: bodyOf(init).name }, note: "once" }),
    }))
    renderAt(<ConnectPage />, "/connect", "owner")
    await user.click(await screen.findByRole("button", { name: "Create token" }))
    const dialog = await screen.findByRole("dialog", { name: "Create a token" })
    expect(within(dialog).getByRole("radio", { name: /Execute/ })).toBeEnabled()
    await user.type(within(dialog).getByLabelText("Name"), "agent")
    await user.click(within(dialog).getByRole("radio", { name: /Propose/ }))
    await user.click(await within(dialog).findByRole("checkbox", { name: /Record reminder/ }))
    await user.click(within(dialog).getByRole("button", { name: "Create token" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/agent/tokens")).toBe(true))
    const body = bodyOf(calls.find((c) => c.method === "POST" && c.path === "/agent/tokens")!.init)
    expect(body.scopes).toEqual(["propose"])
    expect(body.actions).toEqual(["remind"])
  })
})
