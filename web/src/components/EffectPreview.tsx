/**
 * Dot-row visualization of an effect type — a conceptual representation,
 * not a real-time renderer of the actual DSP output.
 *
 * Rendering model: each dot is an emissive light source.
 *   - Colour and intensity are kept separate; emissiveRgb() converts them.
 *   - opacity is NOT used as the primary brightness mechanism — that causes
 *     low-opacity saturated colours to composite with the dark background and
 *     produce muddy brick-red / dark-navy instead of vivid lamp colours.
 *   - A dual-layer box-shadow (inner tight glow + outer soft halo) is applied
 *     at all sizes, not only 'lg'.
 *   - Spatial y-offsets are deterministic per effect type so the arrangement
 *     helps explain behaviour visually.
 */

interface Props {
  effectType: string
  energy?: number  // 0–1; defaults to 0.7
  count?: number   // number of dots; defaults to 8
  size?: 'sm' | 'md' | 'lg'
}

type Dot = {
  base: [number, number, number]  // vivid full-intensity RGB
  intensity: number               // 0–1 brightness for this dot
  yFrac?: number                  // fractional y-offset (−1=up, +1=down), scaled by size
}

// ── emissive colour ──────────────────────────────────────────────────────────

/**
 * Convert a vivid base colour + 0–1 intensity to a CSS rgb() value.
 * Uses a mild gamma curve so mid-range intensities stay perceptually saturated,
 * and adds a "bloom" push toward white at high intensity.
 */
function emissiveRgb([R, G, B]: readonly [number, number, number], intensity: number): string {
  if (intensity < 0.04) return 'rgb(8,8,8)'
  const t = Math.pow(intensity, 0.58)           // γ ≈ 0.58 — perceptual brightness
  let r = R * t, g = G * t, b = B * t
  if (t > 0.70) {                               // bloom above ~70% perceived brightness
    const bloom = ((t - 0.70) / 0.30) * 95
    r = Math.min(255, r + bloom)
    g = Math.min(255, g + bloom)
    b = Math.min(255, b + bloom)
  }
  return `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})`
}

// ── glow ─────────────────────────────────────────────────────────────────────

/**
 * Dual-layer box-shadow: tight inner glow + soft outer halo.
 * Applied at all sizes; scale controls overall radius.
 */
function glowStyle(
  [r, g, b]: readonly [number, number, number],
  intensity: number,
  scale: number,
): string {
  if (intensity < 0.05) return 'none'
  const inner = {
    blur:   Math.round(intensity * 6 * scale),
    spread: Math.round(intensity * 2 * scale),
    op:     Math.min(0.90, intensity * 0.95),
  }
  const outer = {
    blur:   Math.round(intensity * 18 * scale),
    spread: Math.round(intensity * 5 * scale),
    op:     Math.min(0.55, intensity * 0.60),
  }
  return [
    `0 0 ${inner.blur}px ${inner.spread}px rgba(${r},${g},${b},${inner.op})`,
    `0 0 ${outer.blur}px ${outer.spread}px rgba(${r},${g},${b},${outer.op})`,
  ].join(', ')
}

// ── vivid base colours ───────────────────────────────────────────────────────
// These are the colours the lamp produces at 100 % intensity.
// emissiveRgb() scales them down for lower intensities.

const RED:      [number,number,number] = [255,  58,  38]
const ORANGE:   [number,number,number] = [255, 145,  15]
const GREEN:    [number,number,number] = [ 38, 238,  58]
const CYAN_G:   [number,number,number] = [ 15, 220, 138]
const BLUE:     [number,number,number] = [ 72, 105, 255]
const INDIGO:   [number,number,number] = [ 42,  58, 248]
const SKY:      [number,number,number] = [ 40, 195, 255]
const CYAN:     [number,number,number] = [ 18, 225, 252]
const AMBER:    [number,number,number] = [255, 160,  38]
const VIOLET:   [number,number,number] = [192, 135, 255]
const WARM_W:   [number,number,number] = [255, 250, 225]
const SLATE_W:  [number,number,number] = [230, 220, 205]
const SLATE:    [number,number,number] = [168, 178, 205]

const SWIRL_CYCLE: [number,number,number][] = [
  [ 38, 228, 145],  // vivid emerald-teal
  [ 18, 205, 192],  // vivid teal
  [ 28, 165, 220],  // vivid cyan-blue
  [ 85, 238, 118],  // vivid lime-green
  [ 28, 205, 182],  // teal variant
  [ 62, 225, 150],  // emerald variant
  [ 18, 192, 212],  // cyan-teal
  [ 72, 218, 158],  // green-teal
]

// ── spatial y-offsets by effect type ────────────────────────────────────────
// Normalised to [−1, +1]; multiplied by size-specific max pixels at render.
// Sliced to `count`, so 6-dot cards use the first 6 values.

const Y: Record<string, number[]> = {
  spectrum_rgb:         [0, 0, 0, 0, 0, 0, 0, 0],          // flat — it's a spatial spectrum
  spectrum_rgb_spatial: [0, 0, 0, 0, 0, 0, 0, 0],
  mono_pulse:           [0, 0, 0, 0, 0, 0, 0, 0],          // all in sync
  solid:                [0, 0, 0, 0, 0, 0, 0, 0],          // steady
  pulses:               [0, -0.25, 0, 0.25, 0, -0.25, 0, 0.25],
  flashes:              [0, -0.55, 0, 0, -0.55, 0, 0, 0],  // bright ones raised
  splotches:            [0.30, -0.45, 0.15, -0.35, 0.45, -0.20, 0.35, -0.45],
  fireworks:            [0.42, 0.12, -0.38, -0.52, -0.38, 0.12, 0.42, 0.55], // V: edges down
  swirl:                [-0.20, 0.15, 0.42, 0.10, -0.38, 0.22, -0.12, 0.42],
  wave:                 [0.05, -0.22, -0.52, -0.42, -0.10, 0.22, 0.42, 0.30], // crest at 2-3
  none:                 [0, 0, 0, 0, 0, 0, 0, 0],
}

// ── dot construction ─────────────────────────────────────────────────────────

function buildDots(effectType: string, energy: number, count: number): Dot[] {
  const e = Math.max(0.22, energy)
  const yArr = Y[effectType] ?? Y.none

  switch (effectType) {
    case 'spectrum_rgb':
    case 'spectrum_rgb_spatial': {
      const stops = [RED, ORANGE, GREEN, CYAN_G, BLUE, INDIGO] as const
      const iVar = [0.92, 0.72, 0.88, 0.68, 0.85, 0.78, 0.92, 0.72]
      return Array.from({ length: count }, (_, i) => {
        const t = count === 1 ? 0 : i / (count - 1)
        const fi = t * (stops.length - 1)
        const lo = Math.floor(fi), hi = Math.min(lo + 1, stops.length - 1)
        const a = fi - lo, cl = stops[lo], ch = stops[hi]
        const base: [number, number, number] = [
          Math.round(cl[0] * (1 - a) + ch[0] * a),
          Math.round(cl[1] * (1 - a) + ch[1] * a),
          Math.round(cl[2] * (1 - a) + ch[2] * a),
        ]
        return { base, intensity: e * iVar[i % iVar.length], yFrac: yArr[i] ?? 0 }
      })
    }

    case 'mono_pulse':
      return Array.from({ length: count }, (_, i) => ({
        base: SLATE_W, intensity: e * 0.84, yFrac: yArr[i] ?? 0,
      }))

    case 'pulses': {
      const iVar = [1, 0.22, 0.88, 0.08, 0.65, 0.16, 0.92, 0.38]
      return Array.from({ length: count }, (_, i) => ({
        base: SKY, intensity: e * iVar[i % iVar.length], yFrac: yArr[i] ?? 0,
      }))
    }

    case 'flashes': {
      const isFlash = [false, true, false, false, false, true, false, false]
      return Array.from({ length: count }, (_, i) => ({
        base: isFlash[i % isFlash.length] ? WARM_W : ([20, 20, 20] as [number, number, number]),
        intensity: isFlash[i % isFlash.length] ? e : 0.05,
        yFrac: yArr[i] ?? 0,
      }))
    }

    case 'splotches': {
      const isBright = [true, false, false, true, false, false, false, true]
      const brVar = [0.92, 1, 0.78, 0.88, 1, 0.82, 0.95, 0.90]
      return Array.from({ length: count }, (_, i) => ({
        base: isBright[i % isBright.length] ? VIOLET : ([18, 18, 18] as [number, number, number]),
        intensity: isBright[i % isBright.length] ? e * brVar[i % brVar.length] : 0.04,
        yFrac: yArr[i] ?? 0,
      }))
    }

    case 'fireworks': {
      return Array.from({ length: count }, (_, i) => {
        const center = (count - 1) / 2
        const dist = Math.abs(i - center) / Math.max(center, 1)
        return {
          base: AMBER,
          intensity: e * Math.max(0.06, 1 - dist * 0.68),
          yFrac: yArr[i] ?? 0,
        }
      })
    }

    case 'swirl': {
      const iVar = [0.92, 0.88, 0.78, 0.96, 0.82, 0.92, 0.74, 0.88]
      return Array.from({ length: count }, (_, i) => ({
        base: SWIRL_CYCLE[i % SWIRL_CYCLE.length],
        intensity: e * iVar[i % iVar.length],
        yFrac: yArr[i] ?? 0,
      }))
    }

    case 'wave': {
      const peak = Math.floor(count * 0.38)
      return Array.from({ length: count }, (_, i) => {
        const d = Math.abs(i - peak)
        const intensity = d === 0 ? e : d === 1 ? e * 0.65 : d === 2 ? e * 0.25 : e * 0.05
        return { base: CYAN, intensity, yFrac: yArr[i] ?? 0 }
      })
    }

    case 'solid':
      return Array.from({ length: count }, () => ({
        base: SLATE, intensity: e * 0.78, yFrac: 0,
      }))

    default: // none
      return Array.from({ length: count }, () => ({
        base: [22, 22, 22] as [number, number, number], intensity: 0.05, yFrac: 0,
      }))
  }
}

// ── sizes ─────────────────────────────────────────────────────────────────────

const DOT_CLASS: Record<string, string> = {
  sm: 'w-4 h-4',
  md: 'w-7 h-7',
  lg: 'w-10 h-10',
}
const GAP_CLASS: Record<string, string> = {
  sm: 'gap-1.5',
  md: 'gap-2',
  lg: 'gap-2.5',
}
// Max y-offset in px per size
const MAX_Y: Record<string, number> = { sm: 3.5, md: 6, lg: 10 }
// Glow scale per size
const GLOW_SCALE: Record<string, number> = { sm: 0.32, md: 0.62, lg: 1.0 }

// ── component ─────────────────────────────────────────────────────────────────

export function EffectPreview({ effectType, energy = 0.7, count = 8, size = 'md' }: Props) {
  const dots = buildDots(effectType, energy, count)
  const dotClass = DOT_CLASS[size] ?? DOT_CLASS.md
  const gapClass = GAP_CLASS[size] ?? GAP_CLASS.md
  const maxY = MAX_Y[size] ?? MAX_Y.md
  const glowScale = GLOW_SCALE[size] ?? GLOW_SCALE.md

  // Vertical clearance needed for offsets + outer glow radius
  const clearancePx = size === 'lg' ? 16 : size === 'md' ? 10 : 6

  return (
    <div
      className={`flex items-center justify-center ${gapClass}`}
      style={{ paddingTop: clearancePx, paddingBottom: clearancePx }}
      aria-label={`${effectType} preview`}
      data-testid="effect-preview"
    >
      {dots.map((dot, i) => {
        const yPx = (dot.yFrac ?? 0) * maxY
        const bg = emissiveRgb(dot.base, dot.intensity)
        const shadow = glowStyle(dot.base, dot.intensity, glowScale)
        return (
          <div
            key={i}
            className={`rounded-full shrink-0 ${dotClass}`}
            style={{
              backgroundColor: bg,
              boxShadow: shadow,
              transform: yPx !== 0 ? `translateY(${yPx.toFixed(1)}px)` : undefined,
            }}
          />
        )
      })}
    </div>
  )
}
