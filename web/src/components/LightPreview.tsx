import { getEffectRgb } from '@/lib/effectColors'

interface Props {
  lowEffectType: string
  highEffectType: string
  mix: number    // 0 = all low, 1 = all high
  energy: number // 0–1 brightness
}

const DOT_COUNT = 8

export function LightPreview({ lowEffectType, highEffectType, mix, energy }: Props) {
  const lowRgb = getEffectRgb(lowEffectType)
  const highRgb = getEffectRgb(highEffectType)
  const brightness = Math.max(0.08, energy)

  return (
    <div className="flex items-center justify-center gap-2 py-3">
      {Array.from({ length: DOT_COUNT }).map((_, i) => {
        // Slight spatial variation — dots at the edges lean more toward their respective effect
        const spatial = (i / (DOT_COUNT - 1) - 0.5) * 0.3
        const dotMix = Math.max(0, Math.min(1, mix + spatial))
        const r = Math.round(lowRgb[0] * (1 - dotMix) + highRgb[0] * dotMix)
        const g = Math.round(lowRgb[1] * (1 - dotMix) + highRgb[1] * dotMix)
        const b = Math.round(lowRgb[2] * (1 - dotMix) + highRgb[2] * dotMix)
        const glowPx = Math.round(brightness * 14)

        return (
          <div
            key={i}
            className="rounded-full w-9 h-9 shrink-0 transition-all duration-75"
            style={{
              backgroundColor: `rgb(${r}, ${g}, ${b})`,
              opacity: brightness,
              boxShadow: `0 0 ${glowPx}px rgba(${r}, ${g}, ${b}, 0.55)`,
            }}
          />
        )
      })}
    </div>
  )
}
