/* Camera math of the 3D view (pure, unit-tested). */

export const YAW = (25 * Math.PI) / 180
export const PITCH = (15 * Math.PI) / 180

/** Label opacity from its distance relative to the orbit distance: full up to 0.9x, gone by
    1.3x (labels on the far side fade out, which also thins the overlap). */
export function labelOpacity(distance: number, orbitDistance: number): number {
  if (!(orbitDistance > 0)) return 1
  const t = distance / orbitDistance
  return Math.min(1, Math.max(0, (1.3 - t) / 0.4))
}

/** Camera position that frames a bounding box from an oblique angle (yaw/pitch). */
export function obliqueView(bbox: { x: number[]; y: number[]; z: number[] }, fovDeg: number,
                            aspect: number) {
  const center = {
    x: (bbox.x[0] + bbox.x[1]) / 2, y: (bbox.y[0] + bbox.y[1]) / 2, z: (bbox.z[0] + bbox.z[1]) / 2,
  }
  const radius = Math.max(20, 0.5 * Math.hypot(bbox.x[1] - bbox.x[0], bbox.y[1] - bbox.y[0],
                                               bbox.z[1] - bbox.z[0]))
  const vfov = (fovDeg * Math.PI) / 180
  const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect)
  const dist = (radius / Math.sin(Math.min(vfov, hfov) / 2)) * 0.9
  return {
    center,
    radius,
    position: {
      x: center.x + dist * Math.cos(PITCH) * Math.sin(YAW),
      y: center.y + dist * Math.sin(PITCH),
      z: center.z + dist * Math.cos(PITCH) * Math.cos(YAW),
    },
  }
}

