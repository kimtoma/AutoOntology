declare module "d3-force-3d" {
  interface PositionForce {
    strength(value: number): PositionForce
  }
  export function forceX(x?: number): PositionForce
  export function forceY(y?: number): PositionForce
  export function forceZ(z?: number): PositionForce
}
