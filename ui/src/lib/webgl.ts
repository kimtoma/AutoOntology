/** True when the browser can create a WebGL 2 context (the 3D view needs one; the page falls
    back to the 2D canvas otherwise). WebGL 2, not 1: three.js >= r163 renders with WebGL 2
    only. `?renderer=2d` forces the fallback. */
export function hasWebGL(doc: Document = document): boolean {
  try {
    if (new URLSearchParams(doc.defaultView?.location.search ?? "").get("renderer") === "2d") return false
    const canvas = doc.createElement("canvas")
    return !!canvas.getContext("webgl2")
  } catch {
    return false
  }
}
