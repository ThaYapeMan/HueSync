import { cn } from '@/lib/utils'

interface Props {
  children: React.ReactNode
  className?: string
}

export function SectionLabel({ children, className }: Props) {
  return (
    <p className={cn('text-xs font-semibold uppercase tracking-widest text-muted-foreground', className)}>
      {children}
    </p>
  )
}
