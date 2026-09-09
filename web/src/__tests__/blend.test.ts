import { describe, it, expect } from 'vitest'
import { calcBlendMix } from '@/lib/blend'

function smoothstep(x: number, lo: number, hi: number): number {
  const t = Math.max(0, Math.min(1, (x - lo) / Math.max(hi - lo, 1e-9)))
  return t * t * (3 - 2 * t)
}

describe('calcBlendMix', () => {
  it('returns 0 at or below blend_start', () => {
    expect(calcBlendMix(0.0, 0.3, 0.7)).toBe(0)
    expect(calcBlendMix(0.3, 0.3, 0.7)).toBe(0)
    expect(calcBlendMix(0.1, 0.3, 0.7)).toBe(0)
  })

  it('returns 1 at or above blend_end', () => {
    expect(calcBlendMix(1.0, 0.3, 0.7)).toBe(1)
    expect(calcBlendMix(0.7, 0.3, 0.7)).toBe(1)
    expect(calcBlendMix(0.9, 0.3, 0.7)).toBe(1)
  })

  it('applies cubic smoothstep within the blend zone', () => {
    // midpoint: t=0.5 → 0.5*0.5*(3-1) = 0.5 (same as linear at this point)
    expect(calcBlendMix(0.5, 0.3, 0.7)).toBeCloseTo(0.5, 5)
    // quarter-point: t=0.25 → 0.25*0.25*(3-0.5) = 0.15625 (not 0.25 linear)
    expect(calcBlendMix(0.4, 0.3, 0.7)).toBeCloseTo(0.15625, 5)
    // three-quarter-point: t=0.75 → 0.75*0.75*(3-1.5) = 0.84375 (not 0.75 linear)
    expect(calcBlendMix(0.6, 0.3, 0.7)).toBeCloseTo(0.84375, 5)
  })

  it('matches inline smoothstep formula at 0%/25%/50%/75%/100% of zone', () => {
    const lo = 0.3
    const hi = 0.7
    const points = [lo, lo + 0.25*(hi-lo), lo + 0.5*(hi-lo), lo + 0.75*(hi-lo), hi]
    for (const energy of points) {
      expect(calcBlendMix(energy, lo, hi)).toBeCloseTo(smoothstep(energy, lo, hi), 10)
    }
  })

  it('handles full-range defaults (0 to 1)', () => {
    expect(calcBlendMix(0, 0, 1)).toBe(0)
    expect(calcBlendMix(0.5, 0, 1)).toBeCloseTo(0.5, 5)
    expect(calcBlendMix(1, 0, 1)).toBe(1)
  })

  it('handles inverted range (end < start): uses start as threshold via 1e-9 guard', () => {
    // max(end-start, 1e-9) collapses to 1e-9; energy > start → t >> 1 → 1, else → 0
    expect(calcBlendMix(0.8, 0.7, 0.3)).toBe(1) // energy > start → 1
    expect(calcBlendMix(0.7, 0.7, 0.3)).toBe(0) // energy == start → t=0 → 0
    expect(calcBlendMix(0.3, 0.7, 0.3)).toBe(0) // energy < start → 0
    expect(calcBlendMix(0.2, 0.7, 0.3)).toBe(0) // energy < start → 0
  })
})
