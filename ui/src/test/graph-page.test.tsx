import { screen, waitFor } from "@testing-library/react"

import { mockFetch, renderPage } from "@/test/utils"

const webgl = vi.hoisted(() => ({ value: false }))
vi.mock("@/lib/webgl", () => ({ hasWebGL: () => webgl.value }))
vi.mock("@/components/graph/graph-2d", () => ({
  default: (p: { nodes: unknown[]; links: unknown[] }) =>
    <div data-testid="canvas-2d">{p.nodes.length}/{p.links.length}</div>,
}))
vi.mock("@/components/graph/graph-3d", () => ({
  default: (p: { nodes: unknown[] }) => <div data-testid="canvas-3d">{p.nodes.length}</div>,
}))

const GRAPH = {
  layer: "all", warnings: [], structured_sources: ["hanbit"], documents_sources: ["docs"],
  counts: { nodes: 2, edges: 1, total_nodes: 5 }, truncated: true, max_nodes: 2,
  nodes: [
    { id: "t1", label: "Contract", type: "class", source: "hanbit", kind: "term", origin: "aligned", layer: "structured" },
    { id: "t2", label: "Customer", type: "class", source: "hanbit", kind: "term", origin: "structured", layer: "structured" },
  ],
  edges: [{ id: "fk", source: "t1", target: "t2", type: "semantic_fk", label: "fk", layer: "structured" }],
}

beforeEach(() => {
  // the page sizes the canvas from its box; jsdom has no layout
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800)
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600)
})
afterEach(() => vi.restoreAllMocks())

async function renderGraph(path = "/graph", graph: unknown = GRAPH) {
  const { default: GraphPage } = await import("@/pages/graph")
  const mock = mockFetch({ "GET /ontology/graph": graph })
  renderPage(<GraphPage />, path)
  return mock
}

it("falls back to the 2D canvas with a badge when WebGL is unavailable", async () => {
  webgl.value = false
  await renderGraph()
  expect(await screen.findByTestId("canvas-2d")).toHaveTextContent("2/1")
  expect(screen.getByText("2D fallback")).toBeInTheDocument()
  expect(screen.getByText(/Showing the 2 best-connected of 5 nodes/)).toBeInTheDocument()
  await waitFor(() => expect(window.__autoontologyGraphStats).toMatchObject({
    nodes: 2, links: 1, renderer: "2d", layers: ["structured", "documents", "aligned"],
    origins: { aligned: 1, structured: 1 }, rings: 1,
  }))
  // the whole graph with the evidence layer, filtered client-side
  expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe("/api/ontology/graph?layer=all&evidence=true")
})

it("renders the 3D canvas when WebGL is available", async () => {
  webgl.value = true
  await renderGraph()
  expect(await screen.findByTestId("canvas-3d")).toHaveTextContent("2")
  expect(screen.queryByText("2D fallback")).not.toBeInTheDocument()
})

it("forces the 2D canvas with ?renderer=2d and takes the layer from the URL", async () => {
  webgl.value = true
  await renderGraph("/graph?renderer=2d&layer=structured&evidence=1")
  expect(await screen.findByTestId("canvas-2d")).toHaveTextContent("2/1")
  expect(screen.getByText("2D view")).toBeInTheDocument()
  await waitFor(() => expect(window.__autoontologyGraphStats?.layers).toEqual(["structured", "evidence"]))
  expect(screen.getByRole("radio", { name: "Structured" })).toHaveAttribute("aria-checked", "true")
  // origin colours by default, with a legend entry per origin present
  expect(document.querySelector("[data-legend=aligned]")).not.toBeNull()
  expect(document.querySelector("[data-legend=structured]")).not.toBeNull()
})

it("selects the ?focus node and shows its detail", async () => {
  webgl.value = false
  await renderGraph("/graph?focus=t2")
  const detail = await screen.findByText("Customer", { selector: "h2" })
  expect(detail.closest("#graph-detail")).toHaveAttribute("data-node", "t2")
})

it("links to Build when there is no snapshot", async () => {
  webgl.value = false
  await renderGraph("/graph", () => new Response(JSON.stringify({ error: "no_snapshot", detail: "no" }),
                                                 { status: 404, headers: { "content-type": "application/json" } }))
  expect(await screen.findByText("No ontology yet")).toBeInTheDocument()
})

it("says so when the ?focus node is not in the graph the server sent", async () => {
  webgl.value = false
  await renderGraph("/graph?focus=t9")
  expect(await screen.findByText(/“t9” is not among the 2 best-connected nodes shown/)).toBeInTheDocument()
  expect(document.getElementById("graph-detail")).toBeNull()
})

it("switches to all layers, and says so, when the ?focus node is in another layer", async () => {
  webgl.value = false
  const graph = {
    ...GRAPH, truncated: false, counts: { nodes: 3, edges: 1, total_nodes: 3 },
    nodes: [...GRAPH.nodes, { id: "c1", label: "Payment state", type: "concept", source: "docs",
                              kind: "concept", origin: "document", layer: "documents" }],
  }
  await renderGraph("/graph?layer=structured&focus=c1", graph)
  expect(await screen.findByText(/Switched to all layers to show “Payment state”/)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute("aria-checked", "true"))
  expect((await screen.findByText("Payment state", { selector: "h2" })).closest("#graph-detail"))
    .toHaveAttribute("data-node", "c1")
})
