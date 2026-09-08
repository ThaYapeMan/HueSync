// Minimum saturation applied to colours that have a detectable hue.
// Colours where max(r,g,b)-min(r,g,b) < ACHROMATIC_THRESHOLD are treated
// as grey and are not boosted (boosting a grey signal picks an arbitrary hue).
export const MIN_SATURATION = 0.80
const ACHROMATIC_THRESHOLD = 0.05

// --- RGB ↔ HSL helpers ---

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  switch (max) {
    case r: h = (g - b) / d + (g < b ? 6 : 0); break
    case g: h = (b - r) / d + 2; break
    default: h = (r - g) / d + 4; break
  }
  return [h / 6, s, l]
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) return [l, l, l]
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const hue = (p: number, q: number, t: number) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return [hue(p, q, h + 1 / 3), hue(p, q, h), hue(p, q, h - 1 / 3)]
}

// sRGB gamma: maps linear light (0–1) to perceptual (0–1).
function gammaEncode(v: number): number {
  return Math.pow(Math.max(v, 0), 1 / 2.2)
}

// Convert linear RGB (0–1) to display-ready [r8, g8, b8]:
//  1. Gamma-encode to perceptual space first.
//  2. Boost saturation to MIN_SATURATION in HSL space (only for chromatic signal).
// Gamma before HSL: the saturation boost operates on display-space lightness
// rather than being partially undone by the subsequent gamma step.
export function toDisplayRgb(
  r: number,
  g: number,
  b: number,
): [number, number, number] {
  const rp = gammaEncode(r)
  const gp = gammaEncode(g)
  const bp = gammaEncode(b)
  const spread = Math.max(rp, gp, bp) - Math.min(rp, gp, bp)
  let [h, s, l] = rgbToHsl(rp, gp, bp)
  if (spread > ACHROMATIC_THRESHOLD) {
    s = Math.max(s, MIN_SATURATION)
  }
  const [br, bg, bb] = hslToRgb(h, s, l)
  return [Math.round(br * 255), Math.round(bg * 255), Math.round(bb * 255)]
}

export function toCssRgb(r: number, g: number, b: number): string {
  const [cr, cg, cb] = toDisplayRgb(r, g, b)
  return `rgb(${cr},${cg},${cb})`
}
