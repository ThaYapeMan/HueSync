export function calcBlendMix(energy: number, start: number, end: number): number {
  const t = Math.max(0, Math.min(1, (energy - start) / Math.max(end - start, 1e-9)))
  return t * t * (3 - 2 * t)
}

export function settleTime(response: number): string {
  const secs = 3 / (response * 30)
  return secs >= 10 ? `~${Math.round(secs)} s` : `~${secs.toFixed(1)} s`
}

export function responseToSlider(response: number): number {
  return (response - 0.01) / (0.5 - 0.01)
}

export function sliderToResponse(slider: number): number {
  return Math.round((0.01 + slider * (0.5 - 0.01)) * 1000) / 1000
}
