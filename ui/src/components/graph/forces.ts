/* Layout forces shared by the 3D view and the 2D fallback. */

import { forceX, forceY, forceZ } from "d3-force-3d"

interface Sim {
  d3Force(name: string): unknown
  d3Force(name: string, force: unknown): unknown
  d3ReheatSimulation(): unknown
}

/** Bounded repulsion plus a weak pull to the origin, so unconnected nodes and small
    components stay near the main graph instead of drifting off (and the zoom-to-fit stays
    readable). */
export function applyForces(fg: Sim | undefined, dims: 2 | 3) {
  if (!fg) return
  const charge = fg.d3Force("charge") as { distanceMax?: (d: number) => void } | undefined
  charge?.distanceMax?.(160)
  fg.d3Force("x", forceX(0).strength(0.06))
  fg.d3Force("y", forceY(0).strength(0.06))
  if (dims === 3) fg.d3Force("z", forceZ(0).strength(0.06))
  fg.d3ReheatSimulation()
}
