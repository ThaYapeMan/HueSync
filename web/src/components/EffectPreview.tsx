/**
 * Static dot-row visualization of an effect type. Intended as a compact
 * "at a glance" preview — not a real-time renderer of the actual effect.
 * Colors and brightness distributions encode the effect's visual character.
 */

interface Props {
  effectType: string
  energy?: number  // 0–1, controls brightness; defaults to 0.6
  count?: number   // number of dots; defaults to 8
  size?: 'sm' | 'md' | 'lg'
}

type Dot = { rgb: [number, number, number]; brightness: number }

function buildDots(effectType: string, energy: number, count: number): Dot[] {
  const e = Math.max(0.25, energy)

  switch (effectType) {
    case 'spectrum_rgb':
    case 'spectrum_rgb_spatial': {
      const stops: [number, number, number][] = [
        [220, 45, 45],
        [205, 85, 30],
        [60, 180, 40],
        [40, 200, 90],
        [50, 90, 220],
        [30, 55, 200],
      ]
      const brightVar = [0.9, 0.7, 0.85, 0.65, 0.82, 0.75, 0.9, 0.7]
      return Array.from({ length: count }, (_, i) => {
        const t = count === 1 ? 0 : i / (count - 1)
        const fi = t * (stops.length - 1)
        const lo = Math.floor(fi)
        const hi = Math.min(lo + 1, stops.length - 1)
        const a = fi - lo
        const cl = stops[lo], ch = stops[hi]
        return {
          rgb: [
            Math.round(cl[0] * (1 - a) + ch[0] * a),
            Math.round(cl[1] * (1 - a) + ch[1] * a),
            Math.round(cl[2] * (1 - a) + ch[2] * a),
          ] as [number, number, number],
          brightness: e * brightVar[i % brightVar.length],
        }
      })
    }

    case 'mono_pulse':
      return Array.from({ length: count }, () => ({
        rgb: [210, 205, 195] as [number, number, number],
        brightness: e * 0.82,
      }))

    case 'pulses': {
      const brightVar = [1, 0.28, 0.85, 0.1, 0.62, 0.2, 0.95, 0.42]
      return Array.from({ length: count }, (_, i) => ({
        rgb: [56, 189, 248] as [number, number, number],
        brightness: e * brightVar[i % brightVar.length],
      }))
    }

    case 'flashes': {
      const isFlash = [false, true, false, false, false, true, false, false]
      return Array.from({ length: count }, (_, i) => ({
        rgb: isFlash[i % isFlash.length]
          ? ([253, 242, 208] as [number, number, number])
          : ([20, 20, 20] as [number, number, number]),
        brightness: isFlash[i % isFlash.length] ? e : 0.06,
      }))
    }

    case 'splotches': {
      const isBright = [true, false, false, true, false, false, false, true]
      const brVar = [0.9, 1, 0.75, 0.85, 1, 0.8, 0.95, 0.88]
      return Array.from({ length: count }, (_, i) => ({
        rgb: isBright[i % isBright.length]
          ? ([167, 139, 250] as [number, number, number])
          : ([20, 20, 20] as [number, number, number]),
        brightness: isBright[i % isBright.length] ? e * brVar[i % brVar.length] : 0.05,
      }))
    }

    case 'fireworks': {
      return Array.from({ length: count }, (_, i) => {
        const center = (count - 1) / 2
        const dist = Math.abs(i - center) / Math.max(center, 1)
        return {
          rgb: [251, 158, 60] as [number, number, number],
          brightness: e * Math.max(0.08, 1 - dist * 0.65),
        }
      })
    }

    case 'swirl': {
      const colors: [number, number, number][] = [
        [52, 211, 153],
        [20, 200, 183],
        [34, 165, 205],
        [80, 218, 140],
        [40, 195, 173],
        [62, 208, 150],
        [26, 190, 190],
        [72, 207, 162],
      ]
      const brightVar = [0.9, 0.85, 0.75, 0.95, 0.8, 0.9, 0.72, 0.85]
      return Array.from({ length: count }, (_, i) => ({
        rgb: colors[i % colors.length],
        brightness: e * brightVar[i % brightVar.length],
      }))
    }

    case 'wave': {
      const peak = Math.floor(count * 0.38)
      return Array.from({ length: count }, (_, i) => {
        const d = Math.abs(i - peak)
        const br = d === 0 ? e : d === 1 ? e * 0.62 : d === 2 ? e * 0.22 : e * 0.05
        return { rgb: [34, 211, 238] as [number, number, number], brightness: br }
      })
    }

    case 'solid':
      return Array.from({ length: count }, () => ({
        rgb: [148, 163, 184] as [number, number, number],
        brightness: e * 0.72,
      }))

    default: // none
      return Array.from({ length: count }, () => ({
        rgb: [30, 30, 30] as [number, number, number],
        brightness: 0.06,
      }))
  }
}

const SIZE_CLASS: Record<string, string> = {
  sm: 'w-4 h-4',
  md: 'w-7 h-7',
  lg: 'w-10 h-10',
}
const GAP_CLASS: Record<string, string> = {
  sm: 'gap-1',
  md: 'gap-2',
  lg: 'gap-2.5',
}

export function EffectPreview({ effectType, energy = 0.6, count = 8, size = 'md' }: Props) {
  const dots = buildDots(effectType, energy, count)
  const dotClass = SIZE_CLASS[size] ?? SIZE_CLASS.md
  const gapClass = GAP_CLASS[size] ?? GAP_CLASS.md

  return (
    <div
      className={`flex items-center justify-center ${gapClass}`}
      aria-label={`${effectType} preview`}
      data-testid="effect-preview"
    >
      {dots.map((dot, i) => {
        const [r, g, b] = dot.rgb
        const glowPx = size === 'lg' ? Math.round(dot.brightness * 14) : 0
        return (
          <div
            key={i}
            className={`rounded-full shrink-0 ${dotClass}`}
            style={{
              backgroundColor: `rgb(${r},${g},${b})`,
              opacity: dot.brightness,
              ...(glowPx > 0 ? { boxShadow: `0 0 ${glowPx}px rgba(${r},${g},${b},0.5)` } : {}),
            }}
          />
        )
      })}
    </div>
  )
}
