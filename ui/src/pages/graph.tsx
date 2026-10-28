/* 그래프: the merged ontology as a 3D force-directed WebGL graph (2D canvas fallback).
   Lazy-loaded from App, the canvases are lazy too (three.js lives in its own chunk). */

import { useQuery } from "@tanstack/react-query"
import {
  Expand, LocateFixed, Network, RotateCw, Search, Shrink, SlidersHorizontal, X,
} from "lucide-react"
import { useTheme } from "next-themes"
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router"

import { GraphDetail } from "@/components/graph/graph-detail"
import type { GraphHandle } from "@/components/graph/types"
import { EmptyState, ErrorState } from "@/components/empty-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { keys } from "@/hooks/queries"
import { useIsMobile } from "@/hooks/use-mobile"
import { api, type GraphLayer } from "@/lib/api"
import {
  buildGraph, type ColorMode, DEFAULT_LAYERS, type GNode, LAYERS, type LegendItem, neighbours,
  nodeGroup, reuseNodes, searchKeywords, TYPE_LABELS,
} from "@/lib/graph-data"
import { cn } from "@/lib/utils"
import { hasWebGL } from "@/lib/webgl"

const Graph3D = lazy(() => import("@/components/graph/graph-3d"))
const Graph2D = lazy(() => import("@/components/graph/graph-2d"))

declare global {
  interface Window {
    /** node/link counts of what the canvas shows (read by the Playwright test) */
    __autoontologyGraphStats?: { nodes: number; links: number; renderer: "3d" | "2d"; layers: string[] }
  }
}

function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul id="graph-legend" aria-label="범례" className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {items.map((i) => (
        <li key={i.key} className="inline-flex items-center gap-1.5" data-legend={i.key}>
          <span className="size-2.5 rounded-full" style={{ background: i.color }} aria-hidden />
          <span>{i.label}</span>
          <span className="text-muted-foreground tabular-nums">{i.count}</span>
        </li>
      ))}
    </ul>
  )
}

function LinkKey() {
  return (
    <ul className="grid gap-1 text-xs text-muted-foreground" aria-label="링크 범례">
      <li className="flex items-center gap-2"><span className="w-5 border-t border-current" aria-hidden />관계 (객체 속성)</li>
      <li className="flex items-center gap-2"><span className="w-5 border-t border-current" aria-hidden />▸ subClassOf</li>
      <li className="flex items-center gap-2"><span className="w-5 border-t-2 border-dashed border-[#16a34a]" aria-hidden />정렬: 같음 (입자)</li>
      <li className="flex items-center gap-2"><span className="w-5 border-t-2 border-dashed border-[#0ea5e9]" aria-hidden />정렬: 상위·하위</li>
      <li className="flex items-center gap-2"><span className="w-5 border-t-2 border-dashed border-[#8b5cf6]" aria-hidden />정렬: 관련</li>
      <li className="flex items-center gap-2"><span className="w-5 border-t border-[#b45309]" aria-hidden />근거 → 문서</li>
    </ul>
  )
}

function Controls({ layers, toggleLayer, colorMode, setColorMode, legend, ground, setGround }: {
  layers: Set<GraphLayer>
  toggleLayer: (l: GraphLayer, on: boolean) => void
  /** null = no 3D view (2D fallback): no grid switch */
  ground: boolean | null
  setGround: (on: boolean) => void
  colorMode: ColorMode
  setColorMode: (m: ColorMode) => void
  legend: LegendItem[]
}) {
  return (
    <div className="grid gap-4" id="graph-controls">
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-xs font-medium text-muted-foreground">레이어</legend>
        {LAYERS.map((l) => (
          <div key={l.key} className="flex items-center justify-between gap-3">
            <Label htmlFor={`layer-${l.key}`} className="font-normal">{l.label}</Label>
            <Switch id={`layer-${l.key}`} data-layer={l.key} checked={layers.has(l.key)}
                    onCheckedChange={(on) => toggleLayer(l.key, on)} />
          </div>
        ))}
        {ground !== null && (
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="layer-ground" className="font-normal">바닥 격자</Label>
            <Switch id="layer-ground" checked={ground} onCheckedChange={setGround} />
          </div>
        )}
      </fieldset>
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-xs font-medium text-muted-foreground">색상</legend>
        <div className="inline-flex rounded-md border p-0.5" role="radiogroup" aria-label="색상 기준">
          {(["type", "source"] as const).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={colorMode === m}
                    data-color-mode={m} onClick={() => setColorMode(m)}
                    className={cn("flex-1 rounded px-3 py-1 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                                  colorMode === m ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>
              {m === "type" ? "유형" : "소스"}
            </button>
          ))}
        </div>
      </fieldset>
      <Legend items={legend} />
      <LinkKey />
    </div>
  )
}

export default function GraphPage() {
  const q = useQuery({ queryKey: keys.graph, queryFn: api.graph })
  const { resolvedTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  const isMobile = useIsMobile()
  const webgl = useMemo(() => hasWebGL(), [])
  const [layers, setLayers] = useState<Set<GraphLayer>>(() => new Set(DEFAULT_LAYERS))
  const [colorMode, setColorMode] = useState<ColorMode>("type")
  const [expanded, setExpanded] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [autoRotate, setAutoRotate] = useState(false)
  const [showGround, setShowGround] = useState(true)
  const [searchOpen, setSearchOpen] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [canvasRef, size] = useSize<HTMLDivElement>()
  const handle = useRef<GraphHandle>(null)
  const [cache] = useState(() => new Map<string, GNode>())  // node objects kept across rebuilds
  const lastClick = useRef<{ id: string; t: number } | null>(null)

  const built = useMemo(() => {
    if (!q.data) return null
    const g = buildGraph(q.data, { layers, colorMode, expanded })
    return { ...g, nodes: reuseNodes(cache, g.nodes) }
  }, [q.data, layers, colorMode, expanded, cache])

  const nodeById = useMemo(() => new Map((built?.nodes ?? []).map((n) => [n.id, n])), [built])
  const selected = selectedId ? nodeById.get(selectedId) ?? null : null
  const active = useMemo(() => {
    if (!selected || !built) return null
    return new Set([selected.id, ...neighbours(built.links, selected.id)])
  }, [selected, built])
  const selectedNeighbours = useMemo(
    () => (active && selected ? [...active].filter((id) => id !== selected.id)
      .map((id) => nodeById.get(id)!).filter(Boolean).sort((a, b) => b.degree - a.degree) : []),
    [active, selected, nodeById])

  useEffect(() => {
    if (!built) return
    window.__autoontologyGraphStats = {
      nodes: built.stats.nodes, links: built.stats.links, renderer: webgl ? "3d" : "2d",
      layers: [...layers],
    }
  }, [built, webgl, layers])
  useEffect(() => () => { delete window.__autoontologyGraphStats }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName ?? "") && !target?.isContentEditable) {
        e.preventDefault()
        setSearchOpen(true)
      } else if (e.key === "Escape" && fullscreen && !searchOpen) {
        setFullscreen(false)
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [fullscreen, searchOpen])

  // fullscreen: the browser's fullscreen on the whole document (so dialogs and sheets, which
  // portal to <body>, stay visible) + the canvas pinned over the app shell
  useEffect(() => {
    const onChange = () => { if (!document.fullscreenElement) setFullscreen(false) }
    document.addEventListener("fullscreenchange", onChange)
    return () => {
      document.removeEventListener("fullscreenchange", onChange)
      // leaving the page (route change) must not leave the browser in fullscreen
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
    }
  }, [])
  const toggleFullscreen = () => {
    const next = !fullscreen
    setFullscreen(next)
    if (next) document.documentElement.requestFullscreen?.().catch(() => {})
    else if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
  }

  const toggleLayer = (l: GraphLayer, on: boolean) => {
    setLayers((prev) => {
      const next = new Set(prev)
      if (on) next.add(l)
      else next.delete(l)
      return next
    })
  }

  const expand = useCallback((id: string) => {
    setExpanded((prev) => (prev.includes(id) ? prev : [...prev, id]))
    setSelectedId(id)
  }, [])

  const select = useCallback((id: string, focus = true) => {
    setSelectedId(id)
    if (focus) handle.current?.focus(id)
  }, [])

  const onNodeClick = useCallback((node: GNode) => {
    const now = Date.now()
    const last = lastClick.current
    lastClick.current = { id: node.id, t: now }
    if (last && last.id === node.id && now - last.t < 350) {
      lastClick.current = null
      expand(node.id)
      return
    }
    setSelectedId(node.id)
  }, [expand])

  const pickSearch = (id: string) => {
    setSearchOpen(false)
    // the node may be hidden by the layer / neighbour filters: show it first
    if (!nodeById.has(id)) {
      setExpanded([])
      const n = q.data?.nodes.find((x) => x.id === id)
      if (n) setLayers((prev) => new Set([...prev, n.layer]))
      setSelectedId(id)
      // the canvas retries until the layout has placed the newly shown node
      window.setTimeout(() => handle.current?.focus(id), 0)
      return
    }
    select(id)
  }

  // ?focus=<node id> (citation chips of the 플레이그라운드): select and fly to that node once
  // the data and the canvas are there
  const [params] = useSearchParams()
  const focusParam = params.get("focus")
  const focusedParam = useRef<string | null>(null)
  useEffect(() => {
    if (!focusParam || !q.data || size.width === 0 || focusedParam.current === focusParam) return
    if (!q.data.nodes.some((n) => n.id === focusParam)) return
    focusedParam.current = focusParam
    pickSearch(focusParam)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusParam, q.data, size.width])

  const Canvas = webgl ? Graph3D : Graph2D
  const legend = built?.legend ?? []
  const controls = (
    <Controls layers={layers} toggleLayer={toggleLayer} colorMode={colorMode}
              setColorMode={setColorMode} legend={legend}
              ground={webgl ? showGround : null} setGround={setShowGround} />
  )
  // the mobile sheet brings its own close button
  const detail = selected && (
    <GraphDetail node={selected} neighbours={selectedNeighbours} onSelect={(id) => select(id)}
                 onExpand={expand} onClose={isMobile ? undefined : () => setSelectedId(null)} />
  )

  return (
    <div className={cn("relative h-[calc(100svh-3.5rem)] w-full overflow-hidden bg-background",
                       fullscreen && "fixed inset-0 z-40 h-svh")}
         id="graph-page" data-renderer={webgl ? "3d" : "2d"}>
      <h1 className="sr-only">그래프</h1>
      <div ref={canvasRef} className="absolute inset-0" id="graph-canvas">
        {q.error ? (
          <div className="p-6"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
        ) : !built ? (
          <div className="grid h-full place-items-center"><Skeleton className="size-48 rounded-full" /></div>
        ) : !q.data!.nodes.length ? (
          <div className="p-6">
            <EmptyState icon={Network} title="보여줄 그래프가 없습니다"
                        hint="소스를 빌드하거나 문서를 추출한 뒤 다시 열어 보세요." />
          </div>
        ) : size.width > 0 && (
          <Suspense fallback={<div className="grid h-full place-items-center"><Skeleton className="size-48 rounded-full" /></div>}>
            <Canvas nodes={built.nodes} links={built.links} width={size.width} height={size.height}
                    dark={dark} selectedId={selectedId} active={active} showGround={showGround}
                    autoRotate={autoRotate} onNodeClick={onNodeClick}
                    onBackgroundClick={() => setSelectedId(null)} handleRef={handle} />
          </Suspense>
        )}
      </div>

      {/* top bar: search, view buttons, counts */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex flex-wrap items-start gap-2 p-3">
        <div className="pointer-events-auto flex flex-wrap items-center gap-1.5 rounded-lg border bg-background/90 p-1 shadow-sm backdrop-blur">
          <Button variant="ghost" size="sm" onClick={() => setSearchOpen(true)} id="graph-search-open">
            <Search aria-hidden /> <span className="hidden sm:inline">노드 검색</span>
            <kbd className="hidden rounded border px-1 text-[10px] text-muted-foreground sm:inline">/</kbd>
          </Button>
          {isMobile && (
            <Button variant="ghost" size="sm" onClick={() => setControlsOpen(true)} id="graph-controls-open">
              <SlidersHorizontal aria-hidden /> 보기
            </Button>
          )}
          {webgl && (
            <Button variant={autoRotate ? "secondary" : "ghost"} size="icon" aria-pressed={autoRotate}
                    onClick={() => setAutoRotate((v) => !v)} aria-label="자동 회전" title="자동 회전" id="graph-rotate">
              <RotateCw aria-hidden />
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={() => handle.current?.reset()}
                  aria-label="카메라 초기화" title="카메라 초기화" id="graph-reset">
            <LocateFixed aria-hidden />
          </Button>
          <Button variant="ghost" size="icon" onClick={toggleFullscreen} aria-pressed={fullscreen}
                  aria-label={fullscreen ? "전체 화면 끝내기" : "전체 화면"} title="전체 화면" id="graph-fullscreen">
            {fullscreen ? <Shrink aria-hidden /> : <Expand aria-hidden />}
          </Button>
        </div>
        <div className="pointer-events-auto flex flex-wrap items-center gap-1.5" id="graph-badges" aria-live="polite">
          {built && (
            <Badge variant="secondary" className="font-normal tabular-nums" id="graph-stats">
              노드 {built.stats.nodes} · 링크 {built.stats.links}
            </Badge>
          )}
          {!webgl && <Badge variant="outline" className="border-warning/40 bg-warning/10" id="graph-2d-badge">2D 폴백</Badge>}
          {q.data?.truncated && (
            <Badge variant="outline" className="border-warning/40 bg-warning/10 font-normal" id="graph-truncated">
              노드 {q.data.counts.total_nodes}개 중 연결 많은 {q.data.counts.nodes}개만 표시
            </Badge>
          )}
          {expanded.length > 0 && (
            <Badge variant="outline" className="gap-1 pr-1 font-normal" id="graph-expanded">
              이웃만 보기 · {expanded.map((id) => cache.get(id)?.label ?? id).join(", ")}
              <button type="button" onClick={() => setExpanded([])} aria-label="전체 보기"
                      className="rounded p-0.5 hover:bg-accent"><X className="size-3" aria-hidden /></button>
            </Badge>
          )}
          {q.data?.warnings.map((w) => (
            <Badge key={w} variant="outline" className="max-w-xs truncate border-warning/40 font-normal" title={w}>{w}</Badge>
          ))}
        </div>
      </div>

      {/* desktop: controls panel at the left, detail panel at the right */}
      {!isMobile && built && (
        <div className="absolute top-16 left-3 w-60 rounded-lg border bg-background/90 p-3 shadow-sm backdrop-blur">
          {controls}
        </div>
      )}
      {!isMobile && detail && (
        <aside aria-label="노드 상세"
               className="absolute top-3 right-3 bottom-3 flex w-[min(24rem,calc(100%-17rem))] flex-col rounded-lg border bg-background/95 shadow-sm backdrop-blur">
          <ScrollArea className="min-h-0 flex-1"><div className="p-4">{detail}</div></ScrollArea>
        </aside>
      )}

      {/* mobile: both are bottom sheets */}
      {isMobile && (
        <>
          <Sheet open={controlsOpen} onOpenChange={setControlsOpen}>
            <SheetContent side="bottom" className="max-h-[75svh]">
              <SheetHeader>
                <SheetTitle>그래프 보기</SheetTitle>
                <SheetDescription>레이어, 색상, 범례</SheetDescription>
              </SheetHeader>
              <ScrollArea className="min-h-0 flex-1"><div className="px-4 pb-4">{controls}</div></ScrollArea>
            </SheetContent>
          </Sheet>
          <Sheet open={!!detail} onOpenChange={(o) => { if (!o) setSelectedId(null) }}>
            <SheetContent side="bottom" className="max-h-[70svh] gap-0" onOpenAutoFocus={(e) => e.preventDefault()}>
              <SheetHeader className="sr-only">
                <SheetTitle>{selected?.label ?? "노드 상세"}</SheetTitle>
                <SheetDescription>{selected ? TYPE_LABELS[selected.group] : ""}</SheetDescription>
              </SheetHeader>
              <ScrollArea className="min-h-0 flex-1"><div className="p-4 pr-10">{detail}</div></ScrollArea>
            </SheetContent>
          </Sheet>
          {built && !selected && (
            <div className="pointer-events-none absolute inset-x-0 bottom-0 p-3">
              <div className="rounded-lg border bg-background/90 px-3 py-2 backdrop-blur"><Legend items={legend} /></div>
            </div>
          )}
        </>
      )}

      <CommandDialog open={searchOpen} onOpenChange={setSearchOpen} title="노드 검색"
                     description="이름, 동의어, 테이블로 노드를 찾고 카메라를 옮깁니다">
        <CommandInput placeholder="노드 이름, 동의어, 테이블…" id="graph-search-input" />
        <CommandList>
          <CommandEmpty>결과 없음</CommandEmpty>
          <CommandGroup heading="노드">
            {(q.data?.nodes ?? []).map((n) => (
              <CommandItem key={n.id} value={`${n.label} ${n.id}`} keywords={searchKeywords(n)}
                           data-node-id={n.id} onSelect={() => pickSearch(n.id)}>
                <span className="size-2 shrink-0 rounded-full"
                      style={{ background: cache.get(n.id)?.color ?? "#94a3b8" }} aria-hidden />
                <span className="truncate">{n.label}</span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {TYPE_LABELS[nodeGroup(n)] ?? n.kind}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  )
}
