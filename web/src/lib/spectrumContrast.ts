// Contrast is measured against the composited fill (the bars retain their opacity).
// A surface-colour halo cannot contrast with every fill, so choose a neutral halo
// per bar and adjust only the stroke's lightness, preserving the band's hue.
export type RGB = [number, number, number]
export function luminance(rgb: RGB): number {
  const linear = rgb.map(v => { const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4 })
  return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722
}
export function contrast(a: RGB, b: RGB): number {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}
export function outlineColours(band: RGB, surface: RGB, opacity: number) {
  const fill = band.map((v, i) => v * opacity + surface[i] * (1 - opacity)) as RGB
  const black: RGB = [0, 0, 0], white: RGB = [255, 255, 255]
  const halo = contrast(fill, black) >= contrast(fill, white) ? black : white
  const destination = halo === black ? white : black
  let stroke = band
  for (let step = 1; contrast(stroke, halo) < 3.1 && step <= 100; step++) {
    stroke = band.map((v, i) => Math.round(v + (destination[i] - v) * step / 100)) as RGB
  }
  return { halo: `rgb(${halo.join(', ')})`, stroke: `rgb(${stroke.join(', ')})` }
}
