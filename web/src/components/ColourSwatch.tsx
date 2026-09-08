import { cn } from '@/lib/utils'
import { toDisplayRgb } from '@/lib/colorUtils'

interface Props {
  r: number
  g: number
  b: number
  onset: boolean
}

export function ColourSwatch({ r, g, b, onset }: Props) {
  const [cr, cg, cb] = toDisplayRgb(r, g, b)
  return (
    <div
      className={cn(
        'w-full h-20 rounded-lg border border-border',
        'transition-[background-color] duration-75',
        onset && 'outline outline-2 outline-white'
      )}
      style={{ backgroundColor: `rgb(${cr}, ${cg}, ${cb})` }}
    />
  )
}
