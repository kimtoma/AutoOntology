import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Route, Routes } from "react-router"
import { Toaster } from "sonner"

import { AppShell } from "@/components/app-shell"
import { NAV } from "@/components/nav"
import { setLang } from "@/i18n/core"
import type { ReviewItem, Run } from "@/lib/api"
import { ago, deltaSummary, freshnessText, opsNotices } from "@/lib/ops"
import OntologyPage from "@/pages/ontology"
import IndexPage from "@/pages/overview"
import ReviewPage from "@/pages/review"
import FORMATS from "@/test/fixtures/formats.json"
import { mockFetch, renderPage } from "@/test/utils"

class FakeEventSource {
  static CLOSED = 2
  readyState = 0
  onopen = null
  onerror = null
  addEventListener() {}
  close() {}
}
beforeEach(() => vi.stubGlobal("EventSource", FakeEventSource))

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })

const STATUS = {
  status: "ok", version: "0.2.0", kordoc_available: true, formats: FORMATS, service_url: "http://127.0.0.1:7880",
  providers: { chat_provider: "claude", chat_model: "m", embed_provider: "ollama", embed_model: "bge-m3", openai_base_url: "",
               chat: { name: "claude", model: "m", available: true, logged_in: true, detail: "", remedy: "", last_test: null } },
  checks: { backend: { status: "ok" }, postgres: { status: "ok" } }, doctor: "", upload_max_mb: 200,
}
const NOW = "2026-10-29T12:00:00Z"
const SUMMARY = {
  revision: 3, created_at: NOW, run_id: "r3", workspace: "ws", base_iri: "https://x/", sources: [],
  counts: { classes: 22, object_properties: 9, evidence: 41, documents: 2 }, alignment: {},
}
const DIFF = {
  from: 2, to: 3,
  classes: { added: [{ id: "concept-a", name: "신규", kind: "term" }], removed: [{ id: "concept-b", name: "계약금액", kind: "attribute" }],
             changed: [{ id: "term-c", name: "Contract", fields: ["definitions"] }], totals: { added: 1, removed: 1, changed: 1 } },
  axioms: { added: [], removed: [], changed: [], totals: { added: 0, removed: 0, changed: 0 } },
  merges: { added: [{ concept: "concept-b", concept_name: "계약금액", term: "term-c", term_name: "Contract" }], removed: [],
            totals: { added: 1, removed: 0 } },
  sources: { added: [], removed: [] }, counts_delta: { classes: -1 },
}
const COUNTS = (axioms: number) => ({ proposals: 0, axioms, unaligned: 1, aliases: 0, stale_sources: 1, folds: 3, total: axioms + 2 })
const item = (n: number): ReviewItem => ({
  id: `ax:c${n}`, category: "axioms",
  concept: { id: `c${n}`, node: `concept-c${n}`, name: `계약ID${n}`, kind: "attribute", source: "quick-docs", definition: "계약 식별자." },
  term: { id: "t1", node: "term-t1", name: "Contract" }, relation: "related", confidence: 0.93,
  rationale: "an identifier of the Contract table", suggested_action: "accept",
  evidence: [{ quote: "계약ID는 연도와 일련번호로", document: "계약관리규정", page: 2 }], decision: null,
})
const LISTING = (items: ReviewItem[], counts = COUNTS(items.length)) => ({
  revision: 3, counts, unapplied: 0, status: "pending", category: "axioms", page: 1, size: 20, total: items.length,
  pages: 1, items,
})
const FRESH = { sources: [
  { name: "quick", kind: "files", state: "fresh", stale: false, reasons: [], last_built_at: NOW },
  { name: "quick-docs", kind: "documents", state: "stale", stale: true, last_built_at: NOW,
    reasons: [{ kind: "files_added", count: 3, examples: [] }, { kind: "files_changed", count: 1, examples: [] }] },
], stale: 1, age_s: 0, cache_s: 60 }
const DECISION = (n: number, relation = "related") => ({
  id: n, kind: "alignment", subject_key: `c${n}`, decision: "accept", relation, payload: {}, note: "",
  decided_by: "local", decided_at: NOW, applied_revision: null,
})

function dashboardRoutes(over: Record<string, unknown> = {}) {
  return {
    "GET /status": STATUS, "GET /ontology": SUMMARY, "GET /ontology/diff": DIFF,
    "GET /ontology/revisions": { revisions: [{ revision: 3, run_kind: "apply", created_at: NOW, run_id: "r3", counts: {}, sources: [] }] },
    "GET /review": LISTING([item(1), item(2)]),
    "GET /review/pending-count": { pending: 4, counts: COUNTS(2), unapplied: 2 },
    "GET /sources/freshness": FRESH,
    "GET /runs": { runs: [{ id: "r3", kind: "apply", status: "succeeded", started_at: NOW, finished_at: NOW, error: null,
                            params: { applied_revision: 3 }, managed: true, steps: [], progress: {}, source: null }] },
    "GET /exports": { exports: [{ revision: 2, dir: "r0002", created_at: NOW, files: [], dirs: [] }] },
    "GET /sources": { sources: [] }, "GET /samples": [],
    ...over,
  }
}

describe("Overview (/)", () => {
  it("shows the first-run checklist while there is no snapshot", async () => {
    mockFetch(dashboardRoutes({ "GET /ontology": () => json({ error: "no_snapshot", detail: "none" }, 404) }))
    renderPage(<IndexPage />)
    expect(await screen.findByRole("heading", { name: "Get started", level: 1 })).toBeInTheDocument()
    expect(document.querySelectorAll("#checklist > li[data-step]")).toHaveLength(5)
    expect(document.getElementById("overview")).toBeNull()
  })

  it("shows the dashboard once a snapshot exists", async () => {
    mockFetch(dashboardRoutes())
    renderPage(<IndexPage />)
    expect(await screen.findByRole("heading", { name: "Overview", level: 1 })).toBeInTheDocument()
    expect(document.getElementById("checklist")).toBeNull()
    const ontology = await screen.findByRole("region", { name: "Ontology" })
    expect(within(ontology).getByText("Revision 3")).toBeInTheDocument()
    expect(within(ontology).getByText("by applying decisions")).toBeInTheDocument()
    expect(within(ontology).getByText("22")).toBeInTheDocument()
    await waitFor(() => expect(document.querySelector("[data-delta=added]")).toHaveTextContent("+1 added"))
    expect(document.querySelector("[data-delta=removed]")).toHaveTextContent("−1 removed")
    expect(document.querySelector("[data-delta=changed]")).toHaveTextContent("~1 changed")
    expect(screen.getByRole("link", { name: /View changes/ })).toHaveAttribute("href", "/ontology?changed=3")
    // needs confirmation: counts and the top items with their actions
    const review = screen.getByRole("region", { name: "Needs your confirmation" })
    expect(await within(review).findByText("계약ID1")).toBeInTheDocument()
    expect(within(review).getAllByRole("button", { name: "Accept" })).toHaveLength(2)
    expect(review.querySelector("[data-category=axioms]")).toHaveAttribute("data-count", "2")
    // freshness in plain words, and the resume build
    const sources = screen.getByRole("region", { name: "Sources" })
    expect(await within(sources).findByText("3 files added, 1 changed")).toBeInTheDocument()
    expect(within(sources).getByRole("button", { name: /Build changed sources/ })).toBeInTheDocument()
    // activity with the apply label, notices with the apply banner and the export notice
    expect(await screen.findByText("Apply decisions", { selector: "#ops-activity-list span" })).toBeInTheDocument()
    expect(await screen.findByText("2 decisions not applied yet")).toBeInTheDocument()
    expect(screen.getByText("The latest export is revision 2, the ontology is at revision 3.")).toBeInTheDocument()
  })

  it("builds only the changed sources with resume", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(dashboardRoutes({ "POST /runs": { run_id: "r4" }, "GET /runs/r4": {
      id: "r4", kind: "build", status: "running", started_at: NOW, finished_at: null, error: null, params: {},
      managed: true, steps: [], progress: {}, source: null } }))
    renderPage(<IndexPage />)
    const sources = await screen.findByRole("region", { name: "Sources" })
    await user.click(await within(sources).findByRole("button", { name: /Build changed sources/ }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/runs")).toBe(true))
    const body = JSON.parse(String(calls.find((c) => c.method === "POST" && c.path === "/runs")!.init?.body))
    expect(body).toEqual({ kind: "build", resume: true, sources: ["quick-docs"] })
  })
})

describe("Review page", () => {
  it("accepts with the chosen relation, shows the decision and undoes it", async () => {
    const user = userEvent.setup()
    let pending = 2
    const { calls } = mockFetch(dashboardRoutes({
      "GET /review/pending-count": () => ({ pending, counts: COUNTS(pending), unapplied: 2 - pending }),
      "POST /review/ax:c1/decision": (_u: string, init?: RequestInit) => {
        pending = 1
        const body = JSON.parse(String(init?.body))
        return { item: { ...item(1), decision: DECISION(1, body.relation) }, decision: DECISION(1, body.relation) }
      },
      "DELETE /review/ax:c1/decision": () => { pending = 2; return { removed: "ax:c1" } },
    }))
    renderPage(<><ReviewPage /><Toaster /></>, "/review?category=axioms")
    const card = (await screen.findByText("계약ID1")).closest<HTMLElement>("[data-review-item]")!
    expect(within(card).getByText("“계약ID는 연도와 일련번호로”")).toBeInTheDocument()
    expect(within(card).getByText(/계약관리규정 · p\. 2/)).toBeInTheDocument()
    expect(document.querySelector("[data-tab=axioms] [data-count]")).toHaveAttribute("data-count", "2")
    // choose "broader than", then accept
    await user.click(within(card).getByRole("combobox", { name: "Accept as" }))
    await user.click(await screen.findByRole("option", { name: "broader than" }))
    await user.click(within(card).getByRole("button", { name: "Accept" }))
    await waitFor(() => expect(card).toHaveAttribute("data-decided", "accept"))
    const post = calls.find((c) => c.method === "POST")!
    expect(post.path).toBe("/review/ax:c1/decision")
    expect(JSON.parse(String(post.init?.body))).toEqual({ decision: "accept", relation: "broader" })
    expect(within(card).getByText(/Accepted · broader than/)).toBeInTheDocument()
    expect(document.getElementById("review-live")).toHaveTextContent("Accepted 계약ID1")
    // the tab count follows the pending count
    await waitFor(() => expect(document.querySelector("[data-tab=axioms] [data-count]")).toHaveAttribute("data-count", "1"))
    expect(await screen.findByText("1 decision not applied yet")).toBeInTheDocument()
    await user.click(within(card).getByRole("button", { name: "Undo" }))
    await waitFor(() => expect(card).not.toHaveAttribute("data-decided"))
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/review/ax:c1/decision")).toBe(true)
    await waitFor(() => expect(document.querySelector("[data-tab=axioms] [data-count]")).toHaveAttribute("data-count", "2"))
  })

  it("moves with j/k and rejects with r", async () => {
    const user = userEvent.setup()
    const { calls } = mockFetch(dashboardRoutes({
      "POST /review/ax:c2/decision": () => ({ item: item(2), decision: { ...DECISION(2), decision: "reject", relation: "none" } }),
    }))
    renderPage(<ReviewPage />, "/review?category=axioms")
    await screen.findByText("계약ID2")
    await user.keyboard("j")
    expect(document.querySelector("[data-review-item='ax:c2']")).toHaveAttribute("aria-current", "true")
    await user.keyboard("r")
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true))
    const post = calls.find((c) => c.method === "POST")!
    expect(post.path).toBe("/review/ax:c2/decision")
    expect(JSON.parse(String(post.init?.body))).toEqual({ decision: "reject" })
    await waitFor(() => expect(document.getElementById("review-live")).toHaveTextContent("Rejected 계약ID2"))
  })

  it("applies the decisions and follows the run to the new revision", async () => {
    const user = userEvent.setup()
    let polls = 0
    const { calls } = mockFetch(dashboardRoutes({
      "GET /review/pending-count": { pending: 1, counts: COUNTS(1), unapplied: 1 },
      "POST /runs": { run_id: "ap1" },
      "GET /runs/ap1": () => {
        polls += 1
        const done = polls > 1
        return { id: "ap1", kind: "apply", status: done ? "succeeded" : "running", started_at: NOW,
                 finished_at: done ? NOW : null, error: null, params: done ? { applied_revision: 4 } : {},
                 managed: true, progress: {}, source: null,
                 steps: [{ name: "decisions", status: "succeeded", started_at: NOW, finished_at: NOW, detail: {} },
                         { name: "merge", status: done ? "succeeded" : "running", started_at: NOW, finished_at: null, detail: {} }] }
      },
    }))
    renderPage(<><ReviewPage /><Toaster /></>, "/review?category=axioms")
    const banner = await screen.findByRole("region", { name: "Apply decisions" })
    expect(banner).toHaveTextContent("No model calls, about a minute")
    await user.click(within(banner).getByRole("button", { name: "Apply" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/runs")).toBe(true))
    expect(JSON.parse(String(calls.find((c) => c.path === "/runs" && c.method === "POST")!.init?.body))).toEqual({ kind: "apply" })
    expect(await screen.findByText("Revision 4 written", {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it("lists automatic merges in a sixth tab and says when there was nothing to apply", async () => {
    const user = userEvent.setup()
    const fold: ReviewItem = { ...item(9), id: "fold:c9", category: "folds", relation: "same" }
    let polls = 0
    mockFetch(dashboardRoutes({
      "GET /review": (url: string) => (url.includes("category=folds") ? LISTING([fold]) : LISTING([item(1)])),
      "GET /review/pending-count": { pending: 3, counts: COUNTS(1), unapplied: 1 },
      "POST /runs": { run_id: "ap2" },
      "GET /runs/ap2": () => {
        polls += 1
        return { id: "ap2", kind: "apply", status: "succeeded", started_at: NOW, finished_at: NOW, error: null,
                 params: { applied_revision: 3 }, managed: true, progress: {}, source: null,
                 steps: [{ name: "decisions", status: "succeeded", started_at: NOW, finished_at: NOW, detail: {} },
                         { name: "merge", status: "skipped", started_at: null, finished_at: null, detail: {} },
                         { name: "snapshot", status: "skipped", started_at: null, finished_at: null, detail: {} }] }
      },
    }))
    renderPage(<><ReviewPage /><Toaster /></>, "/review")
    const tabs = await screen.findAllByRole("tab")
    expect(tabs.map((x) => x.getAttribute("data-tab"))).toEqual(
      ["proposals", "axioms", "unaligned", "aliases", "stale_sources", "folds"])
    await waitFor(() => expect(screen.getByRole("tab", { name: /Merged/ })).toHaveTextContent("3"))
    // no category in the URL: the first waiting one with items, never the merges
    expect(screen.getByRole("tab", { name: /Relations/ })).toHaveAttribute("aria-selected", "true")
    await user.click(screen.getByRole("tab", { name: /Merged/ }))
    expect(await screen.findByText("계약ID9")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Apply" }))
    expect(await screen.findByText("Nothing to apply: revision 3 is current", {}, { timeout: 4000 })).toBeInTheDocument()
    expect(polls).toBeGreaterThan(0)
  })

  it("shows the empty state with the last-checked time", async () => {
    mockFetch(dashboardRoutes({
      "GET /review": LISTING([], { proposals: 0, axioms: 0, unaligned: 0, aliases: 0, stale_sources: 0, folds: 0, total: 0 }),
      "GET /review/pending-count": { pending: 0, counts: { proposals: 0, axioms: 0, unaligned: 0, aliases: 0, stale_sources: 0, folds: 0, total: 0 }, unapplied: 0 },
    }))
    renderPage(<ReviewPage />, "/review")
    expect(await screen.findByText("Nothing to review")).toBeInTheDocument()
    expect(screen.getByText(/^Last checked /)).toBeInTheDocument()
    expect(document.getElementById("apply-banner")).toBeNull()
  })
})

describe("Navigation", () => {
  it("lists Overview and Review first and Settings last; Get started is gone", () => {
    expect(NAV.map((n) => n.id)).toEqual(
      ["overview", "review", "sources", "build", "ontology", "graph", "playground", "exports", "status", "settings"])
    setLang("ko", false)
    expect(NAV.map((n) => n.label)).toEqual(
      ["개요", "검토", "소스", "빌드", "온톨로지", "그래프", "플레이그라운드", "내보내기", "상태", "설정"])
  })

  it("badges Review with the pending count", async () => {
    mockFetch(dashboardRoutes())
    renderPage(<Routes><Route element={<AppShell />}><Route index element={<p>x</p>} /></Route></Routes>)
    const badge = await screen.findByText("4", { selector: "#review-badge" })
    expect(badge).toHaveAttribute("data-count", "4")
    expect(screen.getByRole("link", { name: /Review\s*\(4 to review\)/ })).toHaveAttribute("href", "/review")
    expect(document.querySelector("a[data-nav=home]")).toBeNull()
  })
})

describe("Ontology page: revisions and changes", () => {
  const REVISIONS = { revisions: [
    { revision: 3, run_kind: "apply", created_at: NOW, run_id: "r3", counts: {}, sources: [] },
    { revision: 2, run_kind: "build", created_at: NOW, run_id: "r2", counts: {}, sources: [] },
  ] }

  it("lists what changed in the latest revision", async () => {
    const { calls } = mockFetch({ "GET /ontology": SUMMARY, "GET /ontology/revisions": REVISIONS, "GET /ontology/diff": DIFF })
    renderPage(<OntologyPage />, "/ontology?changed=1")
    const changes = await screen.findByText("Changes from revision 2 to 3")
    expect(changes).toBeInTheDocument()
    expect(document.querySelector("[data-changes=added] [data-concept=concept-a]")).toHaveTextContent("신규")
    expect(document.querySelector("[data-changes=changed]")).toHaveTextContent("changed: definitions")
    expect(document.querySelector("[data-changes=removed]")).toHaveTextContent("계약금액")
    expect(document.querySelector("[data-changes=merges]")).toHaveTextContent("Same as → Contract")
    expect(screen.getByRole("button", { name: /Changed in r3/ })).toHaveAttribute("aria-pressed", "true")
    expect(calls.some((c) => c.path === "/ontology/concepts")).toBe(false)
  })

  it("shows a person's decision in the concept sheet", async () => {
    mockFetch({
      "GET /ontology": SUMMARY, "GET /ontology/revisions": REVISIONS,
      "GET /ontology/concepts": { revision: 3, total: 0, page: 1, size: 30, pages: 1, items: [] },
      "GET /ontology/concepts/concept-c1": {
        id: "concept-c1", name: "계약ID", kind: "attribute", origin: "document", source: "quick-docs", definition: "정의",
        synonyms: [], merged_concepts: [], folded_into: null, enriched_by: "", evidence_count: 1,
        alignment: { counts: {}, best: null }, revision: 3, description: "", definitions: ["정의"], tables: [],
        individual_type: null, attributes: [], relations: [], evidence: [],
        alignments: [{ relation: "related", with: "term-t1", label: "Contract", confidence: 0.93, accepted: true }],
        decisions: [{ kind: "alignment", decision: "accept", relation: "related", alias: null,
                      term: { id: "t1", name: "Contract" }, note: "", decided_by: "kim@example.com",
                      decided_at: NOW, applied_revision: 3, item_id: "ax:c1" }],
      },
    })
    renderPage(<OntologyPage />, "/ontology?concept=concept-c1")
    const sheet = await screen.findByRole("dialog")
    const decisions = await within(sheet).findByText("Accepted by a person")
    expect(decisions).toBeInTheDocument()
    expect(within(sheet).getByText(/kim@example.com/)).toHaveTextContent("applied in r3")
    expect(within(sheet).getByRole("link", { name: "Open in Review" })).toHaveAttribute("href", "/review?category=axioms&status=decided")
  })
})

describe("ops helpers", () => {
  it("say what changed in a source in plain words", () => {
    expect(freshnessText(FRESH.sources[1])).toBe("3 files added, 1 changed")
    expect(freshnessText({ name: "db", kind: "database", state: "stale", stale: true,
                           reasons: [{ kind: "schema_changed", count: 0, examples: [] }] })).toBe("the database schema changed")
    expect(freshnessText({ name: "x", kind: "files", state: "unknown", stale: null, reasons: [], error: "OSError" }))
      .toBe("could not check (OSError)")
    setLang("ko", false)
    expect(freshnessText(FRESH.sources[1])).toBe("파일 3개 추가, 1개 변경")
  })

  it("compute the notices", () => {
    const now = new Date(NOW).getTime() + 8 * 86_400_000
    const old: Run = { id: "b", kind: "build", status: "succeeded", started_at: NOW, finished_at: NOW, error: null,
                  params: {}, managed: true, steps: [], progress: {}, source: null }
    const notices = opsNotices({ ontology: SUMMARY, runs: [old], exports: [], now,
                                 pending: { pending: 0, counts: COUNTS(0), unapplied: 3 }, freshness: FRESH.sources })
    expect(notices.map((n) => n.id)).toEqual(["unapplied", "stale", "old-build", "export"])
    expect(notices[2].text).toBe("The last build was 8 days ago. Sources may have changed since.")
    expect(opsNotices({ ontology: SUMMARY, runs: [], exports: [{ revision: 3, dir: "r0003", created_at: NOW, files: [], dirs: [] }],
                        pending: { pending: 0, counts: COUNTS(0), unapplied: 0 }, freshness: [], now: new Date(NOW).getTime() }))
      .toEqual([])
    expect(opsNotices({ runs: [{ ...old, status: "failed" }] })[0]).toMatchObject({ id: "failed", tone: "error", runId: "b" })
  })

  it("summarise a diff and a relative time", () => {
    expect(deltaSummary(DIFF as never)).toEqual({ added: 1, removed: 1, changed: 1, axioms: 0, merges: 1 })
    const t0 = new Date(NOW).getTime()
    expect(ago(NOW, t0 + 30_000)).toBe("just now")
    expect(ago(NOW, t0 + 5 * 60_000)).toBe("5 min ago")
    expect(ago(NOW, t0 + 3 * 86_400_000)).toBe("3 days ago")
  })
})
