/* The 3D view: react-force-graph-3d (three.js / WebGL). Lazy-loaded with its deps.

   Depth cues: lit standard-material spheres (key + rim light), exponential fog scaled to the
   camera distance, labels that fade with distance, an oblique first framing with a short
   auto-rotation, a faint ground grid and damped orbit controls. */

import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react"
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d"
import SpriteText from "three-spritetext"
import {
  AmbientLight, BufferGeometry, Color, DirectionalLight, Float32BufferAttribute, FogExp2,
  GridHelper, Group, Line, type LineBasicMaterial, LineDashedMaterial, Mesh, MeshStandardMaterial, type Object3D,
  type PerspectiveCamera, SphereGeometry, Vector3,
} from "three"

import { applyForces } from "@/components/graph/forces"
import { labelOpacity, obliqueView, PITCH, YAW } from "@/components/graph/camera"
import { escapeHtml, type GraphCanvasProps, whenPlaced } from "@/components/graph/types"
import { type GLink, type GNode, linkEnds } from "@/lib/graph-data"

type Methods = ForceGraphMethods<GNode, GLink>

interface NodeObj {
  group: Group
  mesh: Mesh<SphereGeometry, MeshStandardMaterial>
  holder: Group
  label: SpriteText
}
interface OrbitLike {
  target: Vector3
  autoRotate?: boolean
  autoRotateSpeed?: number
  enableDamping?: boolean
  dampingFactor?: number
}

const NODE_REL = 4
/** fog density = FOG_K / camera distance to the orbit target (0.0025 at 240 units) */
const FOG_K = 0.6
const INTRO_ROTATE_MS = 3000
const BG = { light: "#ffffff", dark: "#0a0a0a" }

/** Recolour the custom dashed alignment lines (three-forcegraph keeps each link's object on
    `__lineObj`). */
function dimDashed(links: GLink[], active: Set<string> | null, dim: string) {
  for (const l of links) {
    const obj = (l as GLink & { __lineObj?: Line }).__lineObj
    if (l.style !== "dashed" || !obj?.material) continue
    const [s, t] = linkEnds(l)
    const on = !active || (active.has(s) && active.has(t))
    const m = obj.material as LineDashedMaterial
    m.color.set(on ? l.color ?? "#8b5cf6" : dim)
    m.opacity = on ? 0.9 : 0.25
  }
}

/** Colour, size and label state of the node objects (no accessor re-runs, no rebuilds). */
function styleNodes(nodes: GNode[], objects: Map<string, NodeObj>, opts: {
  active: Set<string> | null; dim: string; text: string; hoverId: string | null; selectedId: string | null
}) {
  for (const n of nodes) {
    const obj = objects.get(n.id)
    if (!obj) continue
    const on = !opts.active || opts.active.has(n.id)
    obj.mesh.material.color.set(on ? n.color : opts.dim)
    obj.mesh.material.opacity = on ? 0.95 : 0.35
    const r = Math.cbrt(n.val) * NODE_REL
    obj.mesh.scale.setScalar(r)
    obj.holder.position.set(0, r + 6, 0)
    const forced = n.id === opts.hoverId || n.id === opts.selectedId
    obj.label.userData.forced = forced
    obj.label.visible = forced || (opts.active ? opts.active.has(n.id) : n.labelled)
    obj.label.color = opts.text
  }
}

export default function Graph3D({
  nodes, links, width, height, dark, selectedId, active, autoRotate, showGround,
  onNodeClick, onBackgroundClick, handleRef,
}: GraphCanvasProps) {
  const fg = useRef<Methods | undefined>(undefined)
  const objects = useRef(new Map<string, NodeObj>())
  const nodesRef = useRef(nodes)
  const orbitDistance = useRef(0)
  const grid = useRef<GridHelper | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [intro, setIntro] = useState(false)
  const [framedAt, setFramedAt] = useState(0)  // bumps when the camera was framed (grid placement)
  const data = useMemo(() => ({ nodes, links }), [nodes, links])
  const bg = dark ? BG.dark : BG.light
  const dim = dark ? "#3f3f46" : "#d4d4d8"
  const linkBase = dark ? "rgba(212,212,216,0.4)" : "rgba(82,82,91,0.4)"
  const textColor = dark ? "#fafafa" : "#18181b"
  useEffect(() => { nodesRef.current = nodes }, [nodes])

  const frame = useCallback((ms: number) => {
    const g = fg.current
    if (!g) return
    const bbox = g.getGraphBbox()
    if (!bbox) return
    const camera = g.camera() as PerspectiveCamera
    const view = obliqueView(bbox, camera.fov ?? 50, width / Math.max(1, height))
    g.cameraPosition(view.position, view.center, ms)
    setFramedAt(Date.now())
  }, [width, height])

  const focusCancel = useRef<() => void>(() => {})
  useImperativeHandle(handleRef, () => ({
    focus(id: string) {
      focusCancel.current()
      focusCancel.current = whenPlaced(() => nodesRef.current.find((x) => x.id === id), (n) => {
        const g = fg.current
        if (!g) return
        const target = { x: n.x ?? 0, y: n.y ?? 0, z: n.z ?? 0 }
        const r = Math.cbrt(n.val) * NODE_REL
        const dist = 140 + r * 4
        g.cameraPosition({
          x: target.x + dist * Math.cos(PITCH) * Math.sin(YAW),
          y: target.y + dist * Math.sin(PITCH),
          z: target.z + dist * Math.cos(PITCH) * Math.cos(YAW),
        }, target, 1200)
      })
    },
    reset() { frame(800) },
  }), [frame])
  useEffect(() => () => focusCancel.current(), [])

  // scene setup: lights, fog (density follows the camera distance every frame), controls
  useEffect(() => {
    const g = fg.current
    if (!g) return
    const key = new DirectionalLight(0xffffff, 0.9 * Math.PI)
    key.position.set(0.7, 1.1, 0.9)
    const rim = new DirectionalLight(0xbcd4ff, 0.45 * Math.PI)
    rim.position.set(-0.8, -0.3, -1)
    g.lights([new AmbientLight(0xffffff, 0.4 * Math.PI), key, rim])
    const scene = g.scene()
    const fog = new FogExp2(BG.light, 0.0025)
    scene.fog = fog
    const controls = g.controls() as OrbitLike
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    scene.onBeforeRender = (_r, _s, camera) => {
      const d = camera.position.distanceTo(controls.target)
      orbitDistance.current = d
      if (d > 0) fog.density = FOG_K / d
    }
    applyForces(g as never, 3)  // keep unconnected nodes near the graph (forces.ts)
    return () => { scene.onBeforeRender = () => {}; scene.fog = null }
  }, [])

  useEffect(() => {
    const fog = fg.current?.scene().fog
    if (fog) fog.color = new Color(bg)
  }, [bg])

  // the first frame: oblique view once the layout settled, then ~3 s of auto-rotation
  const framed = useRef(false)
  const onEngineStop = useCallback(() => {
    if (framed.current) return
    framed.current = true
    frame(900)
    setIntro(true)
    window.setTimeout(() => setIntro(false), 900 + INTRO_ROTATE_MS)
  }, [frame])

  useEffect(() => {
    const controls = fg.current?.controls() as OrbitLike | undefined
    if (controls) { controls.autoRotate = autoRotate || intro; controls.autoRotateSpeed = 1.2 }
  }, [autoRotate, intro])

  // the ground grid, below the graph's lowest node, theme-coloured, fogged with the scene
  useEffect(() => {
    const g = fg.current
    if (!g || !framedAt) return
    const scene = g.scene()
    if (grid.current) {
      scene.remove(grid.current)
      grid.current.geometry.dispose()
      ;(grid.current.material as LineBasicMaterial).dispose()
      grid.current = null
    }
    if (!showGround) return
    const bbox = g.getGraphBbox()
    if (!bbox) return
    const span = Math.max(bbox.x[1] - bbox.x[0], bbox.z[1] - bbox.z[0], 200) * 1.8
    const helper = new GridHelper(span, 24, dark ? 0x52525b : 0xa1a1aa, dark ? 0x3f3f46 : 0xd4d4d8)
    const material = helper.material as LineBasicMaterial
    material.transparent = true
    material.opacity = dark ? 0.35 : 0.5
    material.depthWrite = false
    helper.position.set((bbox.x[0] + bbox.x[1]) / 2, bbox.y[0] - 25, (bbox.z[0] + bbox.z[1]) / 2)
    helper.raycast = () => {}  // never steals hover/click from the nodes
    scene.add(helper)
    grid.current = helper
  }, [showGround, dark, framedAt])

  const styleOpts = useMemo(() => ({ active, dim, text: textColor, hoverId, selectedId }),
                            [active, dim, textColor, hoverId, selectedId])
  const styleRef = useRef(styleOpts)
  useEffect(() => {
    styleRef.current = styleOpts
    styleNodes(nodes, objects.current, styleOpts)
  }, [nodes, styleOpts])

  // one lit sphere + a label per node; objects are registered so styleNodes can update them
  const nodeThreeObject = useCallback((node: GNode): Object3D => {
    const material = new MeshStandardMaterial({
      color: node.color, roughness: 0.5, metalness: 0.1, transparent: true, opacity: 0.95,
    })
    const mesh = new Mesh(new SphereGeometry(1, 20, 14), material)
    const label = new SpriteText(node.label, 7, "#18181b")
    label.fontFace = "Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', system-ui, sans-serif"
    label.backgroundColor = false
    label.material.depthWrite = false
    label.material.transparent = true
    label.material.fog = false  // labels fade by distance themselves (labelOpacity), not into the fog
    // fade (and keep a steadier on-screen size) with the distance to the camera
    const world = new Vector3()
    const holder = new Group()
    label.onBeforeRender = (_r, _s, camera) => {
      label.getWorldPosition(world)
      const d = camera.position.distanceTo(world)
      const ref = orbitDistance.current
      label.material.opacity = label.userData.forced ? 1 : labelOpacity(d, ref)
      if (ref > 0) holder.scale.setScalar(Math.min(1.6, Math.max(0.6, d / ref)))
    }
    holder.add(label)
    const group = new Group()
    group.add(mesh, holder)
    objects.current.set(node.id, { group, mesh, holder, label })
    styleNodes([node], objects.current, styleRef.current)  // created when the node enters
    return group
  }, [])

  // dashed lines for the alignment links; every other link keeps the default line (a falsy
  // object tells force-graph to draw its own)
  const linkThreeObject = useCallback((link: GLink): Object3D => {
    if (link.style !== "dashed") return null as unknown as Object3D
    const geometry = new BufferGeometry()
    geometry.setAttribute("position", new Float32BufferAttribute(new Float32Array(6), 3))
    const material = new LineDashedMaterial({
      color: link.color ?? "#8b5cf6", dashSize: 2.5, gapSize: 1.8, transparent: true, opacity: 0.9,
    })
    return new Line(geometry, material)
  }, [])

  const linkPositionUpdate = useCallback((obj: Object3D, { start, end }: {
    start: { x: number; y: number; z: number }; end: { x: number; y: number; z: number }
  }, link: GLink) => {
    if (link.style !== "dashed") return false
    const line = obj as Line
    const pos = line.geometry.getAttribute("position") as Float32BufferAttribute
    pos.setXYZ(0, start.x, start.y, start.z)
    pos.setXYZ(1, end.x, end.y, end.z)
    pos.needsUpdate = true
    line.geometry.computeBoundingSphere()
    line.computeLineDistances()
    return true
  }, [])

  // dashed materials follow the selection dimming (the accessor only colours default lines)
  useEffect(() => { dimDashed(links, active, dim) }, [links, active, dim])

  // pause rendering while the tab is hidden
  useEffect(() => {
    const onVis = () => {
      if (document.hidden) fg.current?.pauseAnimation()
      else fg.current?.resumeAnimation()
    }
    document.addEventListener("visibilitychange", onVis)
    return () => document.removeEventListener("visibilitychange", onVis)
  }, [])

  // stable accessors: a hover changes none of them, so force-graph re-applies nothing
  const linkOn = useCallback((l: GLink) => {
    if (!active) return true
    const [s, t] = linkEnds(l)
    return active.has(s) && active.has(t)
  }, [active])
  const linkColor = useCallback((l: GLink) => (linkOn(l) ? l.color ?? linkBase : dim), [linkOn, linkBase, dim])
  const linkWidth = useCallback((l: GLink) => (
    active && selectedId && linkOn(l) ? 1.2 : l.style === "evidence" ? 0.3 : 0.6), [active, selectedId, linkOn])
  const arrowColor = useCallback((l: GLink) => (linkOn(l) ? linkBase : dim), [linkOn, linkBase, dim])
  const nodeLabel = useCallback((n: GNode) => escapeHtml(n.label), [])
  const linkLabel = useCallback((l: GLink) => escapeHtml(l.label), [])
  const arrowLength = useCallback((l: GLink) => (l.style === "arrow" ? 4 : 0), [])
  const particles = useCallback((l: GLink) => l.particles, [])
  const particleColor = useCallback((l: GLink) => l.color ?? linkBase, [linkBase])
  const onHover = useCallback((n: GNode | null) => setHoverId(n?.id ?? null), [])
  const onClick = useCallback((n: GNode, e: MouseEvent) => onNodeClick(n, e), [onNodeClick])

  return (
    <ForceGraph3D<GNode, GLink>
      ref={fg}
      graphData={data}
      width={width}
      height={height}
      backgroundColor={bg}
      showNavInfo={false}
      controlType="orbit"
      nodeId="id"
      nodeVal="val"
      nodeRelSize={NODE_REL}
      nodeLabel={nodeLabel}
      nodeThreeObject={nodeThreeObject}
      linkColor={linkColor}
      linkOpacity={0.6}
      linkWidth={linkWidth}
      linkLabel={linkLabel}
      linkThreeObject={linkThreeObject}
      linkThreeObjectExtend={false}
      linkPositionUpdate={linkPositionUpdate as never}
      linkDirectionalArrowLength={arrowLength}
      linkDirectionalArrowRelPos={1}
      linkDirectionalArrowColor={arrowColor}
      linkDirectionalParticles={particles}
      linkDirectionalParticleWidth={1.6}
      linkDirectionalParticleSpeed={0.006}
      linkDirectionalParticleColor={particleColor}
      warmupTicks={60}
      cooldownTicks={200}
      cooldownTime={6000}
      onEngineStop={onEngineStop}
      onNodeClick={onClick}
      onNodeHover={onHover}
      onBackgroundClick={onBackgroundClick}
    />
  )
}
