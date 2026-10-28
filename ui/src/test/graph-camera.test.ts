import { labelOpacity, obliqueView, PITCH, YAW } from "@/components/graph/camera"
import { whenPlaced } from "@/components/graph/types"

describe("labelOpacity", () => {
  it("is full in front, fades behind the orbit target, gone on the far side", () => {
    expect(labelOpacity(50, 100)).toBe(1)
    expect(labelOpacity(90, 100)).toBe(1)
    expect(labelOpacity(110, 100)).toBeCloseTo(0.5)
    expect(labelOpacity(130, 100)).toBe(0)
    expect(labelOpacity(500, 100)).toBe(0)
    expect(labelOpacity(10, 0)).toBe(1) // before the first frame
  })
})

describe("obliqueView", () => {
  it("looks at the box centre from 25° yaw / 15° pitch, far enough to fit it", () => {
    const v = obliqueView({ x: [-100, 100], y: [-50, 50], z: [-100, 100] }, 40, 16 / 10)
    expect(v.center).toEqual({ x: 0, y: 0, z: 0 })
    const { x, y, z } = v.position
    const dist = Math.hypot(x, y, z)
    expect(Math.asin(y / dist)).toBeCloseTo(PITCH)
    expect(Math.atan2(x, z)).toBeCloseTo(YAW)
    expect(dist).toBeGreaterThan(v.radius * 2) // the whole bounding sphere is in view
  })
})

describe("whenPlaced", () => {
  afterEach(() => vi.useRealTimers())

  it("waits until the layout gave the node a position", () => {
    vi.useFakeTimers()
    const node: { x?: number } = {}
    const fn = vi.fn()
    whenPlaced(() => node, fn)
    expect(fn).not.toHaveBeenCalled()
    node.x = 3
    vi.advanceTimersByTime(150)
    expect(fn).toHaveBeenCalledWith(node)
  })

  it("gives up after its tries and can be cancelled", () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const cancel = whenPlaced(() => undefined, fn, 2)
    vi.advanceTimersByTime(1000)
    expect(fn).not.toHaveBeenCalled()
    const node: { x?: number } = {}
    const cancel2 = whenPlaced(() => node, fn)
    cancel2()
    node.x = 1
    vi.advanceTimersByTime(1000)
    expect(fn).not.toHaveBeenCalled()
    cancel()
  })
})
