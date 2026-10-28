/* API graph JSON (GET /api/ontology/graph?layer=all&evidence=true) -> force-graph data.
   Pure, so the 3D view, the 2D fallback and the tests share it. */

import type { GraphEdgeJson, GraphJson, GraphLayer, GraphNodeJson } from "@/lib/api"
import { CKIND_LABEL } from "@/lib/labels"

export type ColorMode = "origin" | "type" | "source"
export type LinkStyle = "solid" | "dashed" | "arrow" | "evidence"

/** The layer picker: `all` or one layer of the API (`/api/ontology/graph?layer=`). */
export type LayerView = "all" | "structured" | "documents" | "aligned"
export const LAYER_VIEWS: { key: LayerView; label: string }[] = [
  { key: "all", label: "All" },
  { key: "structured", label: "Structured" },
  { key: "documents", label: "Documents" },
  { key: "aligned", label: "Aligned" },
]
export const isLayerView = (v: string | null): v is LayerView =>
  LAYER_VIEWS.some((l) => l.key === v)

/** The API layers a view shows; *evidence* adds the evidence layer (document nodes). */
export function layersFor(view: LayerView, evidence: boolean): GraphLayer[] {
  const base: GraphLayer[] = view === "all" ? ["structured", "documents", "aligned"] : [view]
  return evidence ? [...base, "evidence"] : base
}

/** Colour per origin (default mode): structured Term, document concept, aligned Term. The
    aligned Terms also get a ring in every mode (GNode.ring). */
export const ORIGIN_COLORS: Record<string, string> = {
  structured: "#2563eb",
  document: "#9333ea",
  aligned: "#d97706",
  source_document: "#78716c",
}
export const ORIGIN_LABELS: Record<string, string> = {
  structured: "Structured (Term)",
  document: "Document concept",
  aligned: "Aligned Term",
  source_document: "Source document",
}
export const RING_COLOR = "#f59e0b"

/** Colour per node group in type mode (fixed, so the legend never shifts). */
export const TYPE_COLORS: Record<string, string> = {
  term: "#2563eb",
  entity: "#16a34a",
  metric: "#d97706",
  attribute: "#0891b2",
  process: "#9333ea",
  rule: "#dc2626",
  other: "#64748b",
  folded: "#65a30d",
  document: "#78716c",
}
export const TYPE_LABELS: Record<string, string> = {
  ...CKIND_LABEL, term: "Term (structured)", folded: "Folded document concept", document: "Source document",
}
/** source mode: assigned in sorted source order. */
export const SOURCE_PALETTE = [
  "#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#db2777", "#4d7c0f",
  "#7c3aed", "#ea580c",
]
export const RELATION_COLORS: Record<string, string> = {
  same: "#16a34a", broader: "#0ea5e9", narrower: "#0ea5e9", related: "#8b5cf6",
}
export const EVIDENCE_COLOR = "#b45309"

export const MIN_VAL = 1
export const MAX_VAL = 16

export interface GNode extends GraphNodeJson {
  group: string
  /** legend key in origin mode: structured | document | aligned | source_document */
  originGroup: string
  color: string
  /** an aligned Term: drawn with a ring */
  ring: boolean
  degree: number
  /** force-graph nodeVal (sphere volume): grows with degree, clamped to [MIN_VAL, MAX_VAL] */
  val: number
  /** shown without hover: the best-connected nodes */
  labelled: boolean
  x?: number
  y?: number
  z?: number
}

export interface GLink {
  id: string
  source: string | GNode
  target: string | GNode
  type: string
  label: string
  layer: GraphLayer
  relation?: string
  style: LinkStyle
  /** null = the theme's default link colour */
  color: string | null
  particles: number
  count?: number
}

export interface LegendItem { key: string; label: string; color: string; count: number }

export interface BuildOptions {
  layers: Iterable<GraphLayer>
  colorMode: ColorMode
  /** "neighbours only": show these nodes and their direct neighbours */
  expanded?: Iterable<string>
}

export interface BuiltGraph {
  nodes: GNode[]
  links: GLink[]
  legend: LegendItem[]
  stats: { nodes: number; links: number }
}

export function nodeGroup(n: GraphNodeJson): string {
  if (n.type === "document") return "document"
  if (n.type === "concept") return "folded"
  if (n.origin === "structured") return "term"
  return TYPE_COLORS[n.kind] ? n.kind : "other"
}

export function originGroup(n: GraphNodeJson): string {
  if (n.type === "document") return "source_document"
  return ORIGIN_COLORS[n.origin] ? n.origin : "document"
}

export function degreeVal(degree: number): number {
  return Math.min(MAX_VAL, Math.max(MIN_VAL, 1 + degree * 1.5))
}

export function linkStyle(e: GraphEdgeJson): LinkStyle {
  if (e.type === "evidence") return "evidence"
  if (e.layer === "aligned") return "dashed"
  if (e.type === "subclass_of") return "arrow"
  return "solid"
}

const endId = (end: string | GNode) => (typeof end === "string" ? end : end.id)
export const linkEnds = (l: GLink): [string, string] => [endId(l.source), endId(l.target)]

export function buildGraph(g: GraphJson, opts: BuildOptions): BuiltGraph {
  const layers = new Set(opts.layers)
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  // nodes of the enabled layers, plus both endpoints of every enabled alignment link (an
  // alignment link is the point of that layer even when the documents layer is off)
  const keep = new Set(g.nodes.filter((n) => layers.has(n.layer)).map((n) => n.id))
  const edges = g.edges.filter((e) => layers.has(e.layer) && byId.has(e.source) && byId.has(e.target))
  for (const e of edges) {
    if (e.layer === "aligned") { keep.add(e.source); keep.add(e.target) }
  }
  let kept = edges.filter((e) => keep.has(e.source) && keep.has(e.target))
  const expanded = [...(opts.expanded ?? [])].filter((id) => keep.has(id))
  if (expanded.length) {
    const focus = new Set(expanded)
    const show = new Set(expanded)
    for (const e of kept) {
      if (focus.has(e.source)) show.add(e.target)
      if (focus.has(e.target)) show.add(e.source)
    }
    for (const id of [...keep]) if (!show.has(id)) keep.delete(id)
    kept = kept.filter((e) => keep.has(e.source) && keep.has(e.target))
  }
  const degree = new Map<string, number>()
  for (const e of kept) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
  }
  const shown = g.nodes.filter((n) => keep.has(n.id))
  const sources = [...new Set(shown.map((n) => n.source || "-"))].sort()
  const sourceColor = new Map(sources.map((s, i) => [s, SOURCE_PALETTE[i % SOURCE_PALETTE.length]]))
  // label the top ~12% by degree (at least the hubs with 3+ links), never more than 40
  const ranked = shown.map((n) => degree.get(n.id) ?? 0).sort((a, b) => b - a)
  const cut = Math.max(3, ranked[Math.min(ranked.length - 1, Math.min(39, Math.floor(ranked.length * 0.12)))] ?? 0)
  const nodes: GNode[] = shown.map((n) => {
    const d = degree.get(n.id) ?? 0
    const group = nodeGroup(n)
    const og = originGroup(n)
    return {
      ...n, group, originGroup: og, degree: d, val: degreeVal(d), labelled: d >= cut || og === "aligned",
      ring: n.origin === "aligned",
      color: opts.colorMode === "origin" ? ORIGIN_COLORS[og]
        : opts.colorMode === "type" ? TYPE_COLORS[group] : sourceColor.get(n.source || "-")!,
    }
  })
  const links: GLink[] = kept.map((e) => {
    const style = linkStyle(e)
    return {
      id: e.id, source: e.source, target: e.target, type: e.type, label: e.title || e.label,
      layer: e.layer, relation: e.relation, style, count: e.count,
      color: style === "evidence" ? EVIDENCE_COLOR
        : style === "dashed" ? RELATION_COLORS[e.relation ?? "related"] ?? RELATION_COLORS.related
          : null,
      particles: e.relation === "same" ? 2 : 0,
    }
  })
  const counts = new Map<string, number>()
  for (const n of nodes) {
    const key = opts.colorMode === "origin" ? n.originGroup : opts.colorMode === "type" ? n.group : n.source || "-"
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const fixed = (colors: Record<string, string>, labels: Record<string, string>) =>
    Object.keys(colors).filter((k) => counts.has(k)).map((k) => ({
      key: k, label: labels[k] ?? k, color: colors[k], count: counts.get(k)!,
    }))
  const legend: LegendItem[] = opts.colorMode === "origin" ? fixed(ORIGIN_COLORS, ORIGIN_LABELS)
    : opts.colorMode === "type" ? fixed(TYPE_COLORS, TYPE_LABELS)
      : sources.map((s) => ({ key: s, label: s, color: sourceColor.get(s)!, count: counts.get(s)! }))
  return { nodes, links, legend, stats: { nodes: nodes.length, links: links.length } }
}

/** Ids of the nodes linked to *id* (any direction). */
export function neighbours(links: GLink[], id: string): Set<string> {
  const out = new Set<string>()
  for (const l of links) {
    const [s, t] = linkEnds(l)
    if (s === id) out.add(t)
    if (t === id) out.add(s)
  }
  return out
}

/** Keeps node objects (and the positions the simulation wrote on them) across rebuilds, so
    toggling a layer does not restart the layout from scratch. */
export function reuseNodes(cache: Map<string, GNode>, nodes: GNode[]): GNode[] {
  return nodes.map((n) => {
    const prev = cache.get(n.id)
    if (!prev) { cache.set(n.id, n); return n }
    return Object.assign(prev, n, { x: prev.x, y: prev.y, z: prev.z })
  })
}

/** Origin counts of the nodes (not the source documents): what the legend badges say. */
export function originCounts(nodes: GNode[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const n of nodes) if (n.type !== "document") out[n.origin] = (out[n.origin] ?? 0) + 1
  return out
}

/** Text the node search matches on: label, synonyms, tables. */
export function searchKeywords(n: GraphNodeJson): string[] {
  return [...(n.synonyms ?? []), ...(n.tables ?? []), ...(n.attributes ?? []).slice(0, 5)]
}
