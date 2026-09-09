import { describe, it, expect } from 'vitest'
import { calcBlendMix } from '@/lib/blend'

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

  it('linearly interpolates within the blend zone', () => {
    expect(calcBlendMix(0.5, 0.3, 0.7)).toBeCloseTo(0.5, 5)
    expect(calcBlendMix(0.4, 0.3, 0.7)).toBeCloseTo(0.25, 5)
    expect(calcBlendMix(0.6, 0.3, 0.7)).toBeCloseTo(0.75, 5)
  })

  it('returns 1 at blend_start when start === end', () => {
    expect(calcBlendMix(0.5, 0.5, 0.5)).toBe(1)
    expect(calcBlendMix(0.4, 0.5, 0.5)).toBe(0)
    expect(calcBlendMix(0.6, 0.5, 0.5)).toBe(1)
  })

  it('handles full-range defaults (0 to 1)', () => {
    expect(calcBlendMix(0, 0, 1)).toBe(0)
    expect(calcBlendMix(0.5, 0, 1)).toBeCloseTo(0.5, 5)
    expect(calcBlendMix(1, 0, 1)).toBe(1)
  })

  it('handles inverted range (end < start): uses end as the only threshold', () => {
    // When end <= start the guard fires: energy >= end → 1, else → 0.
    // The slider prevents this in practice; the guard just avoids NaN/crash.
    expect(calcBlendMix(0.3, 0.7, 0.3)).toBe(1) // 0.3 >= end(0.3) → 1
    expect(calcBlendMix(0.2, 0.7, 0.3)).toBe(0) // 0.2 < end(0.3) → 0
    expect(calcBlendMix(0.7, 0.7, 0.3)).toBe(1) // 0.7 >= end(0.3) → 1
  })
})
