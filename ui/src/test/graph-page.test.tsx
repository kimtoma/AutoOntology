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
    { id: "t1", label: "Contract", type: "class", source: "hanbit", kind: "term", origin: "structured", layer: "structured" },
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

async function renderGraph() {
  const { default: GraphPage } = await import("@/pages/graph")
  mockFetch({ "GET /graph": GRAPH })
  renderPage(<GraphPage />)
}

it("falls back to the 2D canvas with a badge when WebGL is unavailable", async () => {
  webgl.value = false
  await renderGraph()
  expect(await screen.findByTestId("canvas-2d")).toHaveTextContent("2/1")
  expect(screen.getByText("2D 폴백")).toBeInTheDocument()
  expect(screen.getByText(/노드 5개 중 연결 많은 2개만 표시/)).toBeInTheDocument()
  await waitFor(() => expect(window.__autoontologyGraphStats).toMatchObject({ nodes: 2, links: 1, renderer: "2d" }))
})

it("renders the 3D canvas when WebGL is available", async () => {
  webgl.value = true
  await renderGraph()
  expect(await screen.findByTestId("canvas-3d")).toHaveTextContent("2")
  expect(screen.queryByText("2D 폴백")).not.toBeInTheDocument()
})
