import { cn } from '@/lib/utils'

interface Props {
  r: number
  g: number
  b: number
  onset: boolean
}

export function ColourSwatch({ r, g, b, onset }: Props) {
  // sRGB gamma: matches the perceptual brightness of the physical Hue lights.
  const to255 = (v: number) => Math.round(Math.pow(Math.max(v, 0), 1 / 2.2) * 255)
  return (
    <div
      className={cn(
        'w-full h-20 rounded-lg border border-border',
        'transition-[background-color] duration-75',
        onset && 'outline outline-2 outline-white'
      )}
      style={{ backgroundColor: `rgb(${to255(r)}, ${to255(g)}, ${to255(b)})` }}
    />
  )
}
