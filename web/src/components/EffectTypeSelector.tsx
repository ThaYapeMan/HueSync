import { useState } from 'react'
import { EFFECTS } from '@/lib/api'
import { EffectPreview } from '@/components/EffectPreview'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  onChange: (type: string) => void
}

export function EffectTypeSelector({ value, onChange }: Props) {
  const [picking, setPicking] = useState(false)
  const current = EFFECTS.find((e) => e.id === value)

  if (picking) {
    return (
      <div className="space-y-1.5" data-testid="effect-type-picker">
        <div className="max-h-64 overflow-y-auto space-y-0.5 pr-0.5">
          {EFFECTS.map((e) => (
            <button
              key={e.id}
              type="button"
              onClick={() => { onChange(e.id); setPicking(false) }}
              className={cn(
                'w-full text-left rounded-md px-2 py-2 transition-colors flex items-center gap-3',
                e.id === value
                  ? 'bg-primary/10 text-foreground'
                  : 'hover:bg-muted text-muted-foreground hover:text-foreground',
              )}
              data-testid={`type-option-${e.id}`}
            >
              <EffectPreview effectType={e.id} energy={0.72} count={6} size="sm" />
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium leading-tight">{e.label}</div>
                <div className="text-[10px] text-muted-foreground/70 leading-tight mt-0.5 line-clamp-1">
                  {e.description}
                </div>
              </div>
            </button>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="text-xs h-6 px-2"
          onClick={() => setPicking(false)}
        >
          Cancel
        </Button>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-3" data-testid="effect-type-display">
      <EffectPreview effectType={value} energy={0.75} count={6} size="sm" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium leading-tight">{current?.label ?? value}</div>
        <div className="text-xs text-muted-foreground/70 leading-tight mt-0.5 line-clamp-2">
          {current?.description}
        </div>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="text-xs h-7 shrink-0"
        onClick={() => setPicking(true)}
        data-testid="effect-type-change"
      >
        Change
      </Button>
    </div>
  )
}
