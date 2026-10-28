/* Build alias for `three/webgpu`: three-render-objects imports WebGPURenderer for its opt-in
   WebGPU mode, which AutoOntology never enables. Aliasing it here keeps three.webgpu.js (a second
   copy of three.js, ~700 kB) out of the bundle. */
export class WebGPURenderer {
  constructor() {
    throw new Error("WebGPU rendering is not bundled in AutoOntology")
  }
}
