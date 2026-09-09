import { useState } from 'react'

interface Props {
  children: React.ReactNode
  defaultOpen?: boolean
}

export function AdvancedSection({ children, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div>
      <button
        type="button"
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors w-full"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="advanced-toggle"
      >
        <span className="text-[10px]">{open ? '▾' : '▸'}</span>
        <span className="font-semibold uppercase tracking-widest">Advanced</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3 pl-3 border-l border-border" data-testid="advanced-content">
          {children}
        </div>
      )}
    </div>
  )
}
