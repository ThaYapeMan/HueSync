import { useState, useId, type ReactNode } from 'react'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import { ENERGY_SOURCE_OPTIONS } from '@/lib/api'
import { Label } from '@/components/ui/label'

interface Props {
  blendStart: number
  blendEnd: number
  blendResponse: number
  onChangeBlendStart: (v: number) => void
  onChangeBlendEnd: (v: number) => void
  onChangeBlendResponse: (v: number) => void
  liveEnergy?: number
}

function settleTime(response: number): string {
  const secs = 3 / (response * 30)
  return secs >= 10 ? `~${Math.round(secs)} s` : `~${secs.toFixed(1)} s`
}

export function EnergyBlendEditor({
  blendStart,
  blendEnd,
  blendResponse,
  onChangeBlendStart,
  onChangeBlendEnd,
  onChangeBlendResponse,
  liveEnergy,
}: Props) {
  const [previewEnergy, setPreviewEnergy] = useState(0.5)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const displayEnergy = liveEnergy !== undefined ? liveEnergy : previewEnergy

  function handleRangeChange(values: number[]) {
    const [start, end] = values
    if (start !== blendStart) onChangeBlendStart(Math.round(start * 100) / 100)
    if (end !== blendEnd) onChangeBlendEnd(Math.round(end * 100) / 100)
  }

  const startPct = blendStart * 100
  const endPct = blendEnd * 100
  const markerPct = displayEnergy * 100

  // EMA response slider: map blend_response [0.01..0.5] → slider [0..1]
  const responseSliderValue = (blendResponse - 0.01) / (0.5 - 0.01)

  function handleResponseSlider(values: number[]) {
    const mapped = 0.01 + values[0] * (0.5 - 0.01)
    onChangeBlendResponse(Math.round(mapped * 1000) / 1000)
  }

  return (
    <div className="space-y-4">
      {/* Blend zone visualisation + dual-handle slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground mb-1">
          <span>LOW ENERGY</span>
          <span>HIGH ENERGY</span>
        </div>

        {/* Colour zones behind the slider */}
        <div className="relative h-2 rounded-full overflow-hidden mb-3">
          {/* Low energy zone */}
          <div
            className="absolute inset-y-0 left-0 bg-blue-500/40"
            style={{ right: `${100 - startPct}%` }}
          />
          {/* Blend zone */}
          <div
            className="absolute inset-y-0 bg-amber-400/40"
            style={{ left: `${startPct}%`, right: `${100 - endPct}%` }}
          />
          {/* High energy zone */}
          <div
            className="absolute inset-y-0 right-0 bg-orange-500/40"
            style={{ left: `${endPct}%` }}
          />
        </div>

        <Slider
          min={0}
          max={1}
          step={0.01}
          value={[blendStart, blendEnd]}
          onValueChange={handleRangeChange}
        />

        {/* Zone labels */}
        <div className="relative h-4 text-xs text-muted-foreground font-mono mt-0.5">
          <span className="absolute left-0">LOW 100%</span>
          <span
            className="absolute -translate-x-1/2 text-amber-500"
            style={{ left: `${(startPct + endPct) / 2}%` }}
          >
            AUTO BLEND
          </span>
          <span className="absolute right-0">HIGH 100%</span>
        </div>

        {/* Energy marker */}
        <div className="relative h-5 mt-1">
          <div
            className="absolute flex flex-col items-center -translate-x-1/2"
            style={{ left: `${markerPct}%` }}
          >
            <div className={`w-0.5 h-3 ${liveEnergy !== undefined ? 'bg-green-400' : 'bg-muted-foreground/50'}`} />
            <span className={`text-[9px] whitespace-nowrap ${liveEnergy !== undefined ? 'text-green-400' : 'text-muted-foreground/60'}`}>
              {liveEnergy !== undefined ? `live ${Math.round(markerPct)}%` : `preview ${Math.round(markerPct)}%`}
            </span>
          </div>
        </div>
      </div>

      {/* Response slider */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs">
          <span className="text-muted-foreground">Response</span>
          <span className="text-muted-foreground font-mono">{settleTime(blendResponse)}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-12">Smooth</span>
          <Slider
            min={0}
            max={1}
            step={0.001}
            value={[responseSliderValue]}
            onValueChange={handleResponseSlider}
            className="flex-1"
          />
          <span className="text-xs text-muted-foreground w-8 text-right">Fast</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Blend settles over {settleTime(blendResponse)} after a sustained energy change
        </p>
      </div>

      {/* Preview simulation (only when not live) */}
      {liveEnergy === undefined && (
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Drag to simulate music energy</Label>
          <Slider
            min={0}
            max={1}
            step={0.01}
            value={[previewEnergy]}
            onValueChange={(v) => setPreviewEnergy(v[0])}
          />
          <div className="flex justify-between text-xs text-muted-foreground font-mono">
            <span>0%</span>
            <span>{Math.round(previewEnergy * 100)}%</span>
            <span>100%</span>
          </div>
        </div>
      )}

      {/* Advanced (numeric inputs) */}
      <div>
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setAdvancedOpen((o) => !o)}
        >
          <span>{advancedOpen ? '▾' : '▸'}</span>
          <span>Advanced</span>
        </button>

        {advancedOpen && (
          <div className="mt-2 space-y-2 pl-3 border-l border-border">
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">Blend start</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={blendStart}
                  onChange={(e) => onChangeBlendStart(parseFloat(e.target.value) || 0)}
                  className="h-7 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Blend end</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={blendEnd}
                  onChange={(e) => onChangeBlendEnd(parseFloat(e.target.value) || 0)}
                  className="h-7 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Response</Label>
                <Input
                  type="number"
                  step={0.01}
                  min={0.01}
                  max={0.5}
                  value={blendResponse}
                  onChange={(e) => onChangeBlendResponse(parseFloat(e.target.value) || 0.1)}
                  className="h-7 text-xs"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


export type EnergySetting = 'energy_source' | 'lufs_floor' | 'lufs_ceiling' | 'adaptation_tau_s'

export function validateEnergySettings(floor: string, ceiling: string, tau: string): string | null {
  if ([floor, ceiling, tau].some(v => !v.trim() || !Number.isFinite(Number(v)))) return 'Energy source settings must be finite'
  if (Number(floor) >= Number(ceiling)) return 'lufs_floor must be below lufs_ceiling'
  if (Number(tau) <= 0) return 'adaptation_tau_s must be positive'
  return null
}

// Shared labels, fields and validation; the profile workspace retains its cards.
export function EnergySourceControls({ source, floor, ceiling, tau, onChange, compact = false, disabled = false, pending = false, onCommit, onStep, header }: {
  source: string; floor: string; ceiling: string; tau: string
  onChange: (field: EnergySetting, value: string) => void
  compact?: boolean; disabled?: boolean; pending?: boolean; onCommit?: () => void
  onStep?: (field: EnergySetting, value: string) => void; header?: ReactNode
}) {
  const error = validateEnergySettings(floor, ceiling, tau)
  const fields = source === 'loudness_fixed'
    ? [{ field: 'lufs_floor' as const, label: 'Floor', unit: 'LUFS', value: floor },
       { field: 'lufs_ceiling' as const, label: 'Ceiling', unit: 'LUFS', value: ceiling }]
    : source === 'loudness_adaptive'
      ? [{ field: 'adaptation_tau_s' as const, label: 'Adaptation', unit: 's', value: tau }] : []
  return <section className={compact ? 'space-y-2' : 'space-y-3'} aria-label="Energy source settings">
    {!compact && <Label>Energy source</Label>}
    <div className={compact ? 'flex flex-wrap items-center justify-between gap-2' : undefined}>
    {compact && header}
    <div role={compact ? 'radiogroup' : undefined} aria-label={compact ? 'Energy source' : undefined}
      className={compact ? 'inline-grid w-max grid-cols-3 rounded-md border-[0.5px] border-border bg-background/40 p-0.5' : 'grid grid-cols-1 gap-1.5 sm:grid-cols-3'}>
      {ENERGY_SOURCE_OPTIONS.map((opt, index) => <button key={opt.value} type="button"
        role={compact ? 'radio' : undefined} aria-checked={compact ? source === opt.value : undefined}
        tabIndex={compact ? (source === opt.value ? 0 : -1) : undefined}
        aria-pressed={compact ? undefined : source === opt.value} disabled={disabled} aria-disabled={disabled || pending}
        onClick={() => { if (!pending) onChange('energy_source', opt.value) }}
        onKeyDown={event => {
          if (pending || !compact || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2
            : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? 2 : 1)) % 3
          ;(event.currentTarget.parentElement!.children[next] as HTMLButtonElement).focus()
          onChange('energy_source', ENERGY_SOURCE_OPTIONS[next].value)
        }}
        className={compact
          ? `h-5 whitespace-nowrap rounded px-3 text-xs disabled:opacity-50 ${source === opt.value ? 'bg-secondary text-foreground font-medium shadow-sm ring-[0.5px] ring-border' : 'text-muted-foreground'}`
          : `rounded border p-2.5 text-left text-sm ${source === opt.value ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/60'}`}>
        <div className={compact ? undefined : "font-medium"}>{opt.label}</div>
        {!compact && <div className="mt-0.5 text-xs text-muted-foreground">{opt.description}</div>}
      </button>)}
    </div>
    </div>
    {(compact || fields.length > 0) && <div data-testid={compact ? 'energy-parameters' : undefined}
      className={compact ? 'flex h-7 items-center gap-5' : fields.length === 1 ? 'block' : 'grid grid-cols-2 gap-3'}>
      {compact && (disabled || fields.length === 0) ? <span className="text-xs text-muted-foreground">
        {disabled ? 'No active coupling' : 'No parameters for this source'}
      </span> : fields.map(({field, label, unit, value}) => compact ? <CompactEnergyNumber key={field}
        label={label} unit={unit} value={value} step={field === 'adaptation_tau_s' ? 5 : 1}
        disabled={disabled || pending} invalid={!!error} onChange={v => onChange(field, v)}
        onCommit={onCommit} onStep={v => onStep?.(field, v)} /> : <label key={field} className="space-y-1 text-xs">
        {field === 'adaptation_tau_s' ? 'Adaptation time (seconds)' : `${label} (LUFS)`}
        <span className="block">
          <Input type="number" step="0.1" min={field === 'adaptation_tau_s' ? '0.1' : undefined}
            disabled={disabled || pending} value={value} aria-invalid={!!error} className="tabular-nums"
            onChange={e => onChange(field, e.target.value)} onBlur={onCommit}
            onKeyDown={e => { if (e.key === 'Enter' && onCommit) { e.preventDefault(); e.currentTarget.blur() } }} />
        </span>
      </label>)}
    </div>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </section>
}

// The text field and attached arrow pair share a single border. Keyboard edits
// commit on blur/Enter; pointer steps use the parent's short PATCH debounce.
function CompactEnergyNumber({ label, unit, value, step, disabled, invalid, onChange, onCommit, onStep }: {
  label: string; unit: string; value: string; step: number; disabled: boolean; invalid: boolean
  onChange: (value: string) => void; onCommit?: () => void; onStep: (value: string) => void
}) {
  const id = useId()
  function stepped(direction: number, multiplier = 1) {
    return String((Number.isFinite(Number(value)) ? Number(value) : 0) + direction * step * multiplier)
  }
  return <div className="flex items-center gap-1.5 text-xs">
    <label htmlFor={id}>{label}</label>
      <span className="flex h-6 overflow-hidden rounded border border-input bg-background focus-within:ring-1 focus-within:ring-ring">
        <input id={id} type="number" value={value} step={step} disabled={disabled} aria-invalid={invalid}
          className="h-full w-11 min-w-0 appearance-none bg-transparent px-1 text-right tabular-nums outline-none disabled:opacity-50 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
          onChange={e => onChange(e.target.value)} onBlur={onCommit}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur() }
            if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
              e.preventDefault(); onChange(stepped(e.key === 'ArrowUp' ? 1 : -1, e.shiftKey ? 5 : 1))
            }
          }} />
        <span className="grid w-4 grid-rows-2 border-l border-input">
          {[1, -1].map(direction => <button key={direction} type="button" disabled={disabled}
            aria-label={`${direction === 1 ? 'Increase' : 'Decrease'} ${label}`}
            className="flex items-center justify-center bg-secondary text-[8px] leading-none hover:bg-muted disabled:opacity-50 first:border-b first:border-input"
            onMouseDown={e => e.preventDefault()}
            onClick={() => onStep(stepped(direction))}>{direction === 1 ? '▴' : '▾'}</button>)}
        </span>
      </span>
    <span aria-hidden="true" className="text-muted-foreground">{unit}</span>
  </div>
}
