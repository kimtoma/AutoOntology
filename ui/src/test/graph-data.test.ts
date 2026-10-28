import type { GraphJson } from "@/lib/api"
import {
  buildGraph, degreeVal, isLayerView, layersFor, MAX_VAL, MIN_VAL, neighbours, ORIGIN_COLORS,
  originCounts, reuseNodes, TYPE_COLORS,
} from "@/lib/graph-data"
import { hasWebGL } from "@/lib/webgl"

const node = (id: string, layer: GraphJson["nodes"][number]["layer"], extra: object = {}) => ({
  id, label: id.toUpperCase(), type: "class" as const, source: "docs", kind: "entity",
  origin: "document" as const, layer, ...extra,
})

const G: GraphJson = {
  layer: "all", warnings: [], structured_sources: ["hanbit"], documents_sources: ["docs"],
  counts: { nodes: 7, edges: 6 },
  nodes: [
    node("t1", "structured", { origin: "aligned", kind: "term", source: "hanbit", synonyms: ["계약"] }),
    node("t2", "structured", { origin: "structured", kind: "term", source: "hanbit" }),
    node("c1", "documents"),
    node("c2", "documents", { kind: "metric" }),
    node("f1", "aligned", { type: "concept" }),
    node("r1", "documents", { type: "individual", kind: "rule" }),
    node("d1", "evidence", { type: "document", kind: "document" }),
  ],
  edges: [
    { id: "fk", source: "t1", target: "t2", type: "semantic_fk", label: "fk", layer: "structured" },
    { id: "rel", source: "c1", target: "c2", type: "document", label: "has", layer: "documents" },
    { id: "sub", source: "c2", target: "c1", type: "subclass_of", label: "subClassOf", layer: "documents" },
    { id: "same", source: "f1", target: "t1", type: "aligned_same", label: "same", layer: "aligned", relation: "same" },
    { id: "brd", source: "c1", target: "t2", type: "subclass_of", label: "subClassOf", layer: "aligned", relation: "broader" },
    { id: "ev", source: "c1", target: "d1", type: "evidence", label: "evidence", layer: "evidence", count: 2 },
  ],
}

describe("buildGraph", () => {
  it("keeps the enabled layers and the endpoints of alignment links", () => {
    const all = buildGraph(G, { layers: ["structured", "documents", "aligned", "evidence"], colorMode: "type" })
    expect(all.stats).toEqual({ nodes: 7, links: 6 })
    const noDocs = buildGraph(G, { layers: ["structured", "aligned"], colorMode: "type" })
    // c1 stays: it is an endpoint of the broader alignment link; c2 and r1 go
    expect(noDocs.nodes.map((n) => n.id).sort()).toEqual(["c1", "f1", "t1", "t2"])
    expect(noDocs.links.map((l) => l.id).sort()).toEqual(["brd", "fk", "same"])
    const structured = buildGraph(G, { layers: ["structured"], colorMode: "type" })
    expect(structured.stats).toEqual({ nodes: 2, links: 1 })
    // the 근거 layer adds document nodes and evidence links only when on
    expect(buildGraph(G, { layers: ["structured", "documents", "aligned"], colorMode: "type" })
      .nodes.some((n) => n.type === "document")).toBe(false)
  })

  it("styles links: arrows for subClassOf, dashed alignment with particles for same", () => {
    const { links } = buildGraph(G, { layers: ["structured", "documents", "aligned", "evidence"], colorMode: "type" })
    const by = Object.fromEntries(links.map((l) => [l.id, l]))
    expect(by.fk.style).toBe("solid")
    expect(by.sub.style).toBe("arrow")
    expect(by.same).toMatchObject({ style: "dashed", particles: 2 })
    expect(by.brd).toMatchObject({ style: "dashed", particles: 0, color: "#0ea5e9" })
    expect(by.ev.style).toBe("evidence")
  })

  it("sizes nodes by degree, clamped", () => {
    expect(degreeVal(0)).toBe(MIN_VAL)
    expect(degreeVal(2)).toBe(4)
    expect(degreeVal(1000)).toBe(MAX_VAL)
    const { nodes } = buildGraph(G, { layers: ["structured", "documents", "aligned", "evidence"], colorMode: "type" })
    const c1 = nodes.find((n) => n.id === "c1")!
    expect(c1.degree).toBe(4)
    expect(c1.val).toBe(degreeVal(4))
    expect(c1.labelled).toBe(true) // the hub
    expect(nodes.find((n) => n.id === "r1")!.val).toBe(MIN_VAL)
  })

  it("builds a legend per colour mode", () => {
    const byType = buildGraph(G, { layers: ["structured", "documents", "aligned", "evidence"], colorMode: "type" })
    expect(byType.legend.map((l) => [l.key, l.count])).toEqual([
      ["term", 2], ["entity", 1], ["metric", 1], ["rule", 1], ["folded", 1], ["document", 1],
    ])
    expect(byType.nodes.find((n) => n.id === "t1")!.color).toBe(TYPE_COLORS.term)
    const bySource = buildGraph(G, { layers: ["structured", "documents"], colorMode: "source" })
    expect(bySource.legend.map((l) => [l.label, l.count])).toEqual([["docs", 3], ["hanbit", 2]])
    const colors = new Set(bySource.nodes.map((n) => n.color))
    expect(colors.size).toBe(2)
  })

  it("colours by origin by default, rings the aligned Terms", () => {
    const g = buildGraph(G, { layers: layersFor("all", true), colorMode: "origin" })
    expect(g.legend.map((l) => [l.key, l.count])).toEqual([
      ["structured", 1], ["document", 4], ["aligned", 1], ["source_document", 1],
    ])
    const by = Object.fromEntries(g.nodes.map((n) => [n.id, n]))
    expect(by.t1).toMatchObject({ color: ORIGIN_COLORS.aligned, ring: true, labelled: true })
    expect(by.t2).toMatchObject({ color: ORIGIN_COLORS.structured, ring: false })
    expect(by.f1.color).toBe(ORIGIN_COLORS.document)  // a folded concept stays a document concept
    expect(by.d1.color).toBe(ORIGIN_COLORS.source_document)
    expect(originCounts(g.nodes)).toEqual({ aligned: 1, structured: 1, document: 4 })
  })

  it("maps the layer picker to API layers", () => {
    expect(layersFor("all", false)).toEqual(["structured", "documents", "aligned"])
    expect(layersFor("aligned", true)).toEqual(["aligned", "evidence"])
    expect(isLayerView("documents")).toBe(true)
    expect(isLayerView("evidence")).toBe(false)
    expect(isLayerView(null)).toBe(false)
    // aligned only: the folded concept and both ends of every alignment link
    const aligned = buildGraph(G, { layers: layersFor("aligned", false), colorMode: "origin" })
    expect(aligned.nodes.map((n) => n.id).sort()).toEqual(["c1", "f1", "t1", "t2"])
    expect(aligned.links.map((l) => l.id).sort()).toEqual(["brd", "same"])
  })

  it("shows only the expanded nodes and their neighbours", () => {
    const g = buildGraph(G, { layers: ["structured", "documents", "aligned"], colorMode: "type", expanded: ["t1"] })
    expect(g.nodes.map((n) => n.id).sort()).toEqual(["f1", "t1", "t2"])
    expect(neighbours(g.links, "t1")).toEqual(new Set(["t2", "f1"]))
  })

  it("keeps node objects (and positions) across rebuilds", () => {
    const cache = new Map()
    const first = reuseNodes(cache, buildGraph(G, { layers: ["structured"], colorMode: "type" }).nodes)
    first[0].x = 12
    const again = reuseNodes(cache, buildGraph(G, { layers: ["structured"], colorMode: "source" }).nodes)
    expect(again[0]).toBe(first[0])
    expect(again[0].x).toBe(12)
  })
})

describe("hasWebGL", () => {
  afterEach(() => vi.restoreAllMocks())

  it("is false when no WebGL 2 context can be created (jsdom) -> 2D fallback", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
    expect(hasWebGL()).toBe(false)
  })

  it("is true when the canvas returns a WebGL 2 context", () => {
    const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockImplementation(((kind: string) => (kind === "webgl2" ? {} : null)) as never)
    expect(hasWebGL()).toBe(true)
    expect(spy).toHaveBeenCalledWith("webgl2")
  })

  it("is false when getContext throws", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => { throw new Error("x") })
    expect(hasWebGL()).toBe(false)
  })
})
