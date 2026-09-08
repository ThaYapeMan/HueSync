// Saturation boost factor: widens the spread between R/G/B channels
// relative to their average. Applied in linear space before gamma.
// Increase to make colours more vivid; 1.0 = no change.
export const SATURATION_BOOST = 1.5

// Apply saturation boost then convert to an 8-bit CSS integer.
// Pipeline: linear RGB (0–1) → saturate → sRGB gamma → 0–255.
export function toDisplayRgb(
  r: number,
  g: number,
  b: number,
): [number, number, number] {
  const avg = (r + g + b) / 3
  const clamp = (v: number) => Math.max(0, Math.min(1, v))
  const sr = clamp(avg + (r - avg) * SATURATION_BOOST)
  const sg = clamp(avg + (g - avg) * SATURATION_BOOST)
  const sb = clamp(avg + (b - avg) * SATURATION_BOOST)
  // sRGB gamma (v^1/2.2) matches the perceptual brightness of Hue lights.
  const g22 = (v: number) => Math.round(Math.pow(v, 1 / 2.2) * 255)
  return [g22(sr), g22(sg), g22(sb)]
}

export function toCssRgb(r: number, g: number, b: number): string {
  const [cr, cg, cb] = toDisplayRgb(r, g, b)
  return `rgb(${cr},${cg},${cb})`
}
