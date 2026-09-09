import { useState } from 'react'
import { EFFECTS, type Effect } from '@/lib/api'
import { getEffectSwatchClass } from '@/lib/effectColors'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface Props {
  role: 'low' | 'high'
  effectId: string
  allEffects: Effect[]
  onChange: (id: string) => void
}

export function EffectCard({ role, effectId, allEffects, onChange }: Props) {
  const [picking, setPicking] = useState(false)

  const effect = allEffects.find((e) => e.id === effectId)
  const effectTypeMeta = EFFECTS.find((e) => e.id === effect?.effect_type)
  const swatchClass = getEffectSwatchClass(effect?.effect_type ?? '')
  const isHigh = role === 'high'

  return (
    <div
      className={cn(
        'border rounded-xl p-4 flex flex-col gap-3 min-h-[160px]',
        isHigh
          ? 'border-orange-500/25 bg-orange-500/5'
          : 'border-blue-500/25 bg-blue-500/5',
      )}
    >
      {/* Role label */}
      <div
        className={cn(
          'text-xs font-semibold uppercase tracking-widest',
          isHigh ? 'text-orange-400' : 'text-blue-400',
        )}
      >
        {isHigh ? 'High energy' : 'Low energy'}
      </div>

      {/* Effect info */}
      {picking ? (
        /* Inline effect picker */
        <div className="flex-1 flex flex-col gap-1">
          <div className="text-xs text-muted-foreground font-medium mb-1">Select effect:</div>
          {role === 'low' && (
            <button
              type="button"
              onClick={() => { onChange(''); setPicking(false) }}
              className={cn(
                'w-full text-left px-2 py-1.5 rounded text-xs transition-colors',
                effectId === ''
                  ? 'bg-primary/15 text-foreground font-medium'
                  : 'hover:bg-muted text-muted-foreground',
              )}
            >
              Same as high-energy
            </button>
          )}
          <div className="max-h-36 overflow-y-auto space-y-0.5 pr-0.5">
            {allEffects.map((e) => (
              <button
                key={e.id}
                type="button"
                onClick={() => { onChange(e.id); setPicking(false) }}
                className={cn(
                  'w-full text-left px-2 py-1.5 rounded text-xs transition-colors flex items-center gap-2',
                  effectId === e.id
                    ? 'bg-primary/15 text-foreground font-medium'
                    : 'hover:bg-muted text-muted-foreground',
                )}
              >
                <div className={cn('w-3 h-3 rounded-sm shrink-0', getEffectSwatchClass(e.effect_type))} />
                <span className="flex-1 truncate">{e.name}</span>
                <span className="text-muted-foreground/50 text-[10px] shrink-0">
                  {EFFECTS.find((t) => t.id === e.effect_type)?.label}
                </span>
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant="ghost"
            className="text-xs h-6 px-2 self-start mt-1"
            onClick={() => setPicking(false)}
          >
            Cancel
          </Button>
        </div>
      ) : (
        /* Effect display */
        <>
          <div className="flex items-start gap-3 flex-1">
            <div className={cn('w-12 h-12 rounded-lg shrink-0', swatchClass)} />
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-sm leading-tight truncate">
                {effect?.name ?? (role === 'low' ? 'Same as high-energy' : 'Not set')}
              </div>
              {effectTypeMeta && (
                <div className="text-xs text-muted-foreground mt-0.5">
                  {effectTypeMeta.label}
                </div>
              )}
              {effectTypeMeta?.description && (
                <div className="text-xs text-muted-foreground/60 mt-1 leading-tight line-clamp-2">
                  {effectTypeMeta.description}
                </div>
              )}
              {!effect && role === 'low' && (
                <div className="text-xs text-muted-foreground/60 mt-1">
                  Uses the high-energy effect
                </div>
              )}
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="self-start text-xs h-7"
            onClick={() => setPicking(true)}
          >
            Change…
          </Button>
        </>
      )}
    </div>
  )
}
