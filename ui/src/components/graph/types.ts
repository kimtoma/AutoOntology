import type { Ref } from "react"

import type { GLink, GNode } from "@/lib/graph-data"

export interface GraphHandle {
  /** Move the camera to the node (smooth transition). A node the layout has not placed yet
      is retried for a few seconds. */
  focus(id: string): void
  /** Fit every node in view. */
  reset(): void
}

export interface GraphCanvasProps {
  nodes: GNode[]
  links: GLink[]
  width: number
  height: number
  dark: boolean
  selectedId: string | null
  /** selection + its neighbours; everything else is dimmed. null = nothing selected */
  active: Set<string> | null
  autoRotate: boolean
  /** 3D only: the faint reference grid under the graph */
  showGround: boolean
  onNodeClick: (node: GNode, event: MouseEvent) => void
  onBackgroundClick: () => void
  handleRef: Ref<GraphHandle>
}

/** force-graph renders node/link labels as HTML: escape the (untrusted) document text. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!)
}

/** Calls *fn* with the node once the layout has given it a position (retries every 150 ms,
    up to ~4.5 s): a node revealed by a layer toggle has no coordinates on the first frame. */
export function whenPlaced<T extends { x?: number }>(find: () => T | undefined, fn: (n: T) => void,
                                                     tries = 30): () => void {
  let timer = 0
  const attempt = (left: number) => {
    const n = find()
    if (n && n.x != null && Number.isFinite(n.x)) fn(n)
    else if (left > 0) timer = window.setTimeout(() => attempt(left - 1), 150)
  }
  attempt(tries)
  return () => window.clearTimeout(timer)
}
