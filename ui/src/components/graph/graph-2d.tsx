/* The 2D fallback (no WebGL): react-force-graph-2d on a plain canvas, same data. */

import { useEffect, useImperativeHandle, useMemo, useRef, useState } from "react"
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d"

import { applyForces } from "@/components/graph/forces"
import { escapeHtml, type GraphCanvasProps, whenPlaced } from "@/components/graph/types"
import { type GLink, type GNode, linkEnds, RING_COLOR } from "@/lib/graph-data"

type Methods = ForceGraphMethods<GNode, GLink>

export default function Graph2D({
  nodes, links, width, height, dark, selectedId, active,
  onNodeClick, onBackgroundClick, handleRef,
}: GraphCanvasProps) {
  const [hoverId, setHoverId] = useState<string | null>(null)
  const nodesRef = useRef(nodes)
  useEffect(() => { nodesRef.current = nodes }, [nodes])
  const focusCancel = useRef<() => void>(() => {})
  const fg = useRef<Methods | undefined>(undefined)
  const data = useMemo(() => ({ nodes, links }), [nodes, links])
  const dim = dark ? "#3f3f46" : "#d4d4d8"
  const linkBase = dark ? "rgba(212,212,216,0.45)" : "rgba(82,82,91,0.45)"
  const text = dark ? "#fafafa" : "#18181b"
  const isOn = (id: string) => !active || active.has(id)
  const linkOn = (l: GLink) => { const [s, t] = linkEnds(l); return isOn(s) && isOn(t) }

  useImperativeHandle(handleRef, () => ({
    focus(id: string) {
      focusCancel.current()
      focusCancel.current = whenPlaced(() => nodesRef.current.find((x) => x.id === id), (n) => {
        fg.current?.centerAt(n.x, n.y, 800)
        fg.current?.zoom(3, 800)
      })
    },
    reset() { fg.current?.zoomToFit(800, 40) },
  }), [])
  useEffect(() => () => focusCancel.current(), [])

  const framed = useRef(false)
  useEffect(() => { applyForces(fg.current as never, 2) }, [])

  useEffect(() => {
    const onVis = () => {
      if (document.hidden) fg.current?.pauseAnimation()
      else fg.current?.resumeAnimation()
    }
    document.addEventListener("visibilitychange", onVis)
    return () => document.removeEventListener("visibilitychange", onVis)
  }, [])

  return (
    <ForceGraph2D<GNode, GLink>
      ref={fg}
      graphData={data}
      width={width}
      height={height}
      backgroundColor={dark ? "#0a0a0a" : "#ffffff"}
      nodeId="id"
      nodeVal="val"
      nodeRelSize={3}
      nodeLabel={(n) => escapeHtml(n.label)}
      nodeColor={(n) => (isOn(n.id) ? n.color : dim)}
      nodeCanvasObjectMode={() => "after"}
      nodeCanvasObject={(n, ctx, scale) => {
        if (n.ring && n.x != null && n.y != null) {  // aligned Term: a ring around the dot
          ctx.beginPath()
          ctx.arc(n.x, n.y, Math.sqrt(n.val) * 3 + 2.5, 0, 2 * Math.PI)
          ctx.strokeStyle = RING_COLOR
          ctx.lineWidth = 1.6
          ctx.globalAlpha = isOn(n.id) ? 1 : 0.35
          ctx.stroke()
          ctx.globalAlpha = 1
        }
        const show = n.id === hoverId || n.id === selectedId || (active ? active.has(n.id) : n.labelled)
        if (!show || n.x == null || n.y == null) return
        const size = 11 / scale
        ctx.font = `${size}px Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', system-ui, sans-serif`
        ctx.textAlign = "center"
        ctx.textBaseline = "top"
        ctx.fillStyle = text
        ctx.fillText(n.label, n.x, n.y + Math.sqrt(n.val) * 3 + 2 / scale)
      }}
      linkColor={(l) => (linkOn(l) ? l.color ?? linkBase : dim)}
      linkWidth={(l) => (selectedId && active && linkOn(l) ? 1.6 : 0.8)}
      linkLineDash={(l) => (l.style === "dashed" ? [4, 3] : l.style === "evidence" ? [1, 2] : null)}
      linkLabel={(l) => escapeHtml(l.label)}
      linkDirectionalArrowLength={(l) => (l.style === "arrow" ? 4 : 0)}
      linkDirectionalArrowRelPos={1}
      linkDirectionalParticles={(l) => l.particles}
      linkDirectionalParticleWidth={2}
      linkDirectionalParticleColor={(l) => l.color ?? linkBase}
      warmupTicks={60}
      cooldownTicks={200}
      cooldownTime={6000}
      onEngineStop={() => { if (!framed.current) { framed.current = true; fg.current?.zoomToFit(600, 30) } }}
      onNodeClick={(n, e) => onNodeClick(n, e)}
      onNodeHover={(n) => setHoverId(n?.id ?? null)}
      onBackgroundClick={onBackgroundClick}
    />
  )
}
