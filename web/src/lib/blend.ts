export function calcBlendMix(energy: number, start: number, end: number): number {
  if (end <= start) return energy >= end ? 1 : 0
  if (energy <= start) return 0
  if (energy >= end) return 1
  return (energy - start) / (end - start)
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
