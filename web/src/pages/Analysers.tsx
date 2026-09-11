import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { SliderField } from '@/components/SliderField'
import { cn } from '@/lib/utils'
import {
  ONSET_METHODS,
  BARS_SOURCE_OPTIONS,
  type Analyser,
  type Coupling,
  getAnalysers,
  getCouplings,
  createAnalyser,
  updateAnalyser,
  deleteAnalyser,
  cloneAnalyser,
} from '@/lib/api'

// ── Analyser defaults ─────────────────────────────────────────────────────────

const ANALYSER_DEFAULTS = {
  bars_source: 'cava' as const,
  onset_method: 'combined' as const,
  bars: 30,
  lower_cutoff_freq: 50,
  higher_cutoff_freq: 12000,
  onset_delta: 0.1,
  use_hpss_separation: false,
  onset_alpha: 0.9,
  superflux_mu: 3,
  superflux_lag: 2,
} as const

// ── Logarithmic frequency mapping ─────────────────────────────────────────────

const LOG_MIN = Math.log10(20)
const LOG_MAX = Math.log10(20000)
const LOG_STEP = (LOG_MAX - LOG_MIN) / 50   // ~50 keyboard steps across full range

export function hzToPercent(hz: number): number {
  return ((Math.log10(Math.max(20, Math.min(20000, hz))) - LOG_MIN) / (LOG_MAX - LOG_MIN)) * 100
}

export function percentToHz(pct: number): number {
  return Math.round(Math.pow(10, (pct / 100) * (LOG_MAX - LOG_MIN) + LOG_MIN))
}

// ── Draft state ───────────────────────────────────────────────────────────────

interface Draft {
  name: string
  bars_source: string
  onset_method: string
  bars: string
  lower_cutoff_freq: string
  higher_cutoff_freq: string
  onset_delta: string
  onset_alpha: string
  superflux_mu: string
  superflux_lag: string
  use_hpss_separation: boolean
}

function defaultDraft(a?: Analyser | null): Draft {
  return {
    name: a?.name ?? '',
    bars_source: a?.bars_source ?? ANALYSER_DEFAULTS.bars_source,
    onset_method: a?.onset_method ?? ANALYSER_DEFAULTS.onset_method,
    bars: String(a?.bars ?? ANALYSER_DEFAULTS.bars),
    lower_cutoff_freq: String(a?.lower_cutoff_freq ?? ANALYSER_DEFAULTS.lower_cutoff_freq),
    higher_cutoff_freq: String(a?.higher_cutoff_freq ?? ANALYSER_DEFAULTS.higher_cutoff_freq),
    onset_delta: String(a?.onset_delta ?? ANALYSER_DEFAULTS.onset_delta),
    onset_alpha: String(a?.onset_alpha ?? ANALYSER_DEFAULTS.onset_alpha),
    superflux_mu: String(a?.superflux_mu ?? ANALYSER_DEFAULTS.superflux_mu),
    superflux_lag: String(a?.superflux_lag ?? ANALYSER_DEFAULTS.superflux_lag),
    use_hpss_separation: a?.use_hpss_separation ?? ANALYSER_DEFAULTS.use_hpss_separation,
  }
}

// ── ConfigSection helper ──────────────────────────────────────────────────────

function ConfigSection({
  title,
  children,
  onReset,
  isAtDefault,
  resetTestId,
}: {
  title: string
  children: React.ReactNode
  onReset?: () => void
  isAtDefault?: boolean
  resetTestId?: string
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {title}
        </span>
        {onReset && (
          <button
            type="button"
            onClick={isAtDefault ? undefined : onReset}
            aria-disabled={isAtDefault}
            data-testid={resetTestId}
            className={cn(
              'text-[9px] uppercase tracking-wide transition-colors leading-none py-0.5',
              isAtDefault
                ? 'text-muted-foreground/25 pointer-events-none'
                : 'text-muted-foreground/55 hover:text-muted-foreground cursor-pointer',
            )}
          >
            Reset
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

// ── ColumnHeading helper ──────────────────────────────────────────────────────

function ColumnHeading({ title }: { title: string }) {
  return (
    <div className="pb-1">
      <span className="text-[11px] font-bold uppercase tracking-widest text-foreground/60">
        {title}
      </span>
    </div>
  )
}

// ── FrequencyRangeSlider ──────────────────────────────────────────────────────

function FrequencyRangeSlider({
  lowHz,
  highHz,
  bands,
  onLowChange,
  onHighChange,
}: {
  lowHz: number
  highHz: number
  bands: number
  onLowChange: (hz: number) => void
  onHighChange: (hz: number) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)

  const lowPct = hzToPercent(lowHz)
  const highPct = hzToPercent(highHz)
  const rangeWidth = Math.max(0, highPct - lowPct)

  function getPercentFromEvent(e: MouseEvent): number {
    if (!trackRef.current) return 0
    const rect = trackRef.current.getBoundingClientRect()
    return Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100))
  }

  function startDrag(handle: 'low' | 'high') {
    // Capture current constraint values at drag start — they don't change
    // while a single handle is being dragged.
    const snapHighHz = highHz
    const snapLowHz = lowHz

    return (e: React.MouseEvent) => {
      e.preventDefault()

      function onMove(e: MouseEvent) {
        const pct = getPercentFromEvent(e)
        const rawHz = percentToHz(pct)
        if (handle === 'low') {
          onLowChange(Math.max(20, Math.min(500, Math.min(rawHz, snapHighHz - 1))))
        } else {
          onHighChange(Math.max(1000, Math.min(20000, Math.max(rawHz, snapLowHz + 1))))
        }
      }

      function onUp() {
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
      }

      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    }
  }

  function handleKeyDown(handle: 'low' | 'high') {
    return (e: React.KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      e.preventDefault()
      const dir = e.key === 'ArrowRight' ? 1 : -1
      if (handle === 'low') {
        const newLog = Math.log10(Math.max(20, lowHz)) + dir * LOG_STEP
        const newHz = Math.round(Math.pow(10, newLog))
        onLowChange(Math.max(20, Math.min(500, Math.min(newHz, highHz - 1))))
      } else {
        const newLog = Math.log10(Math.max(1000, highHz)) + dir * LOG_STEP
        const newHz = Math.round(Math.pow(10, newLog))
        onHighChange(Math.max(1000, Math.min(20000, Math.max(newHz, lowHz + 1))))
      }
    }
  }

  const ticks = [
    { hz: 100, label: '100' },
    { hz: 1000, label: '1k' },
    { hz: 5000, label: '5k' },
    { hz: 10000, label: '10k' },
  ]

  const handleClass =
    'absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2.5 h-[22px] ' +
    'bg-zinc-400 dark:bg-zinc-500 rounded-[2px] border border-zinc-500 dark:border-zinc-600 ' +
    'cursor-ew-resize focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-400 z-10'

  return (
    <div data-testid="frequency-range-slider">
      <div className="flex justify-between text-[9px] text-muted-foreground/35 mb-1">
        <span>20 Hz</span>
        <span>20 kHz</span>
      </div>

      <div
        ref={trackRef}
        className="relative h-6 bg-zinc-900/80 rounded-sm border border-zinc-700/30 select-none"
        style={{ overflow: 'visible' }}
        data-testid="slider-track"
      >
        {/* Active range fill */}
        <div
          className="absolute inset-y-0 bg-zinc-500/20"
          style={{ left: `${lowPct}%`, width: `${rangeWidth}%` }}
        />
        {/* Band division lines within active range */}
        {Array.from({ length: Math.max(0, bands - 1) }, (_, i) => {
          const bandX = lowPct + rangeWidth * (i + 1) / bands
          return (
            <div
              key={i}
              className="absolute inset-y-0 w-px bg-zinc-600/25"
              style={{ left: `${bandX}%` }}
            />
          )
        })}

        {/* Low handle */}
        <div
          role="slider"
          aria-label="Low frequency cutoff"
          aria-valuemin={20}
          aria-valuemax={500}
          aria-valuenow={lowHz}
          tabIndex={0}
          data-testid="slider-handle-low"
          className={handleClass}
          style={{ left: `${lowPct}%` }}
          onMouseDown={startDrag('low')}
          onKeyDown={handleKeyDown('low')}
        />

        {/* High handle */}
        <div
          role="slider"
          aria-label="High frequency cutoff"
          aria-valuemin={1000}
          aria-valuemax={20000}
          aria-valuenow={highHz}
          tabIndex={0}
          data-testid="slider-handle-high"
          className={handleClass}
          style={{ left: `${highPct}%` }}
          onMouseDown={startDrag('high')}
          onKeyDown={handleKeyDown('high')}
        />
      </div>

      {/* Frequency tick labels */}
      <div className="relative h-4 mt-0.5">
        {ticks.map(({ hz, label }) => (
          <span
            key={hz}
            className="absolute text-[9px] text-muted-foreground/35 -translate-x-1/2"
            style={{ left: `${hzToPercent(hz)}%` }}
          >
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── AnalyserListItem ──────────────────────────────────────────────────────────

function AnalyserListItem({ analyser, isSelected, onSelect }: {
  analyser: Analyser
  isSelected: boolean
  onSelect: () => void
}) {
  const methodLabel = ONSET_METHODS.find(m => m.value === analyser.onset_method)?.label ?? analyser.onset_method
  const sourceLabel = analyser.bars_source === 'pcm_pipeline' ? 'PCM' : 'cava'

  return (
    <button
      data-testid={`analyser-item-${analyser.id}`}
      className={cn(
        'w-full text-left px-3 py-2.5 rounded-md transition-colors',
        isSelected
          ? 'bg-muted text-foreground'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
      )}
      onClick={onSelect}
    >
      <p className="text-sm font-medium truncate">{analyser.name}</p>
      <p className="text-xs text-muted-foreground/60 truncate">
        {methodLabel} · {analyser.bars} bars · {sourceLabel}
      </p>
    </button>
  )
}

// ── AnalyserWorkspace ─────────────────────────────────────────────────────────

interface WorkspaceProps {
  analyser: Analyser | null
  couplings: Coupling[]
  onSaved: (a: Analyser) => void
  onDeleted: (id: string) => void
  onCloned: (id: string) => void
  onCancelCreate: () => void
}

function AnalyserWorkspace({ analyser, couplings, onSaved, onDeleted, onCloned, onCancelCreate }: WorkspaceProps) {
  const isCreating = analyser === null

  const [draft, setDraft] = useState<Draft>(() => defaultDraft(analyser))
  const [expert, setExpert] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const usedByCount = analyser ? couplings.filter(c => c.analyser_id === analyser.id).length : 0

  // ── isAtDefault checks ────────────────────────────────────────────────────

  const isDefaultBarsSource = draft.bars_source === ANALYSER_DEFAULTS.bars_source
  const isDefaultOnsetMethod = draft.onset_method === ANALYSER_DEFAULTS.onset_method
  const isDefaultFreqRange =
    parseInt(draft.lower_cutoff_freq, 10) === ANALYSER_DEFAULTS.lower_cutoff_freq &&
    parseInt(draft.higher_cutoff_freq, 10) === ANALYSER_DEFAULTS.higher_cutoff_freq
  const isDefaultBars = parseInt(draft.bars, 10) === ANALYSER_DEFAULTS.bars
  const isDefaultOnsetDelta = parseFloat(draft.onset_delta) === ANALYSER_DEFAULTS.onset_delta
  const isDefaultHpss = draft.use_hpss_separation === ANALYSER_DEFAULTS.use_hpss_separation
  const isDefaultOnsetTuning =
    parseFloat(draft.onset_alpha) === ANALYSER_DEFAULTS.onset_alpha &&
    (draft.onset_method !== 'superflux' || (
      parseInt(draft.superflux_mu, 10) === ANALYSER_DEFAULTS.superflux_mu &&
      parseInt(draft.superflux_lag, 10) === ANALYSER_DEFAULTS.superflux_lag
    ))

  // ── Reset actions — operate on local draft only ───────────────────────────

  function resetBarsSource() {
    setDraft(d => ({ ...d, bars_source: ANALYSER_DEFAULTS.bars_source }))
  }
  function resetOnsetMethod() {
    setDraft(d => ({ ...d, onset_method: ANALYSER_DEFAULTS.onset_method }))
  }
  function resetFreqRange() {
    setDraft(d => ({
      ...d,
      lower_cutoff_freq: String(ANALYSER_DEFAULTS.lower_cutoff_freq),
      higher_cutoff_freq: String(ANALYSER_DEFAULTS.higher_cutoff_freq),
    }))
  }
  function resetBars() {
    setDraft(d => ({ ...d, bars: String(ANALYSER_DEFAULTS.bars) }))
  }
  function resetOnsetDelta() {
    setDraft(d => ({ ...d, onset_delta: String(ANALYSER_DEFAULTS.onset_delta) }))
  }
  function resetHpss() {
    setDraft(d => ({ ...d, use_hpss_separation: ANALYSER_DEFAULTS.use_hpss_separation }))
  }
  function resetOnsetTuning() {
    setDraft(d => ({
      ...d,
      onset_alpha: String(ANALYSER_DEFAULTS.onset_alpha),
      superflux_mu: String(ANALYSER_DEFAULTS.superflux_mu),
      superflux_lag: String(ANALYSER_DEFAULTS.superflux_lag),
    }))
  }

  // ── Save ──────────────────────────────────────────────────────────────────

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: draft.name.trim() || 'Unnamed',
        bars_source: draft.bars_source,
        onset_method: draft.onset_method,
        bars: parseInt(draft.bars, 10) || ANALYSER_DEFAULTS.bars,
        lower_cutoff_freq: parseInt(draft.lower_cutoff_freq, 10) || ANALYSER_DEFAULTS.lower_cutoff_freq,
        higher_cutoff_freq: parseInt(draft.higher_cutoff_freq, 10) || ANALYSER_DEFAULTS.higher_cutoff_freq,
        onset_delta: parseFloat(draft.onset_delta) || ANALYSER_DEFAULTS.onset_delta,
        onset_alpha: parseFloat(draft.onset_alpha) || ANALYSER_DEFAULTS.onset_alpha,
        superflux_mu: parseInt(draft.superflux_mu, 10) || ANALYSER_DEFAULTS.superflux_mu,
        superflux_lag: parseInt(draft.superflux_lag, 10) || ANALYSER_DEFAULTS.superflux_lag,
        use_hpss_separation: draft.use_hpss_separation,
      }
      const result = isCreating
        ? await createAnalyser(body)
        : await updateAnalyser(analyser!.id, body)
      onSaved(result)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  function handleClone() {
    if (!analyser) return
    onCloned(analyser.id)
  }

  const lowHz = parseInt(draft.lower_cutoff_freq, 10) || ANALYSER_DEFAULTS.lower_cutoff_freq
  const highHz = parseInt(draft.higher_cutoff_freq, 10) || ANALYSER_DEFAULTS.higher_cutoff_freq
  const barsNum = parseInt(draft.bars, 10) || ANALYSER_DEFAULTS.bars

  const combinedOpt = ONSET_METHODS.find(m => m.value === 'combined')!
  const otherOpts = ONSET_METHODS.filter(m => m.value !== 'combined')

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-5 py-3.5 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <input
            value={draft.name}
            onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
            placeholder={isCreating ? 'Analyser name…' : 'Name…'}
            className="flex-1 min-w-0 bg-transparent text-lg font-semibold outline-none placeholder:text-muted-foreground/40 border-b border-transparent focus:border-border pb-0.5 transition-colors"
            data-testid="analyser-name-input"
          />
          {/* Mode toggle */}
          <div className="flex shrink-0 rounded-md border border-border overflow-hidden">
            <button
              data-testid="mode-standard-btn"
              onClick={() => setExpert(false)}
              className={cn(
                'px-3 py-1 text-xs transition-colors',
                !expert
                  ? 'bg-muted font-semibold text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              )}
            >
              Standard
            </button>
            <button
              data-testid="mode-expert-btn"
              onClick={() => setExpert(true)}
              className={cn(
                'px-3 py-1 text-xs transition-colors border-l border-border',
                expert
                  ? 'bg-muted font-semibold text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              )}
            >
              Expert
            </button>
          </div>
          {!isCreating && (
            <>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs text-muted-foreground hover:text-foreground shrink-0"
                onClick={handleClone}
                data-testid="analyser-clone-btn"
              >
                Clone
              </Button>
              <ConfirmDialog
                trigger={
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs text-destructive/70 hover:text-destructive shrink-0"
                    data-testid="analyser-delete-btn"
                  >
                    Delete
                  </Button>
                }
                title="Delete analyser"
                description={`Delete "${analyser!.name}"? This cannot be undone.`}
                onConfirm={() => onDeleted(analyser!.id)}
              />
            </>
          )}
        </div>
        {!isCreating && usedByCount > 0 && (
          <p className="text-xs text-muted-foreground mt-1.5">
            Used by {usedByCount} coupling{usedByCount === 1 ? '' : 's'}
          </p>
        )}
      </div>

      {/* Two-column configuration workspace */}
      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-2 divide-x divide-border/30 min-h-full">

          {/* ── LEFT: Audio / Spectrum ─────────────────────────────────── */}
          <div className="px-5 py-5 space-y-5">
            <ColumnHeading title="Audio / Spectrum" />

            {/* Audio Source — side-by-side cards */}
            <div data-testid="section-audio-source">
              <ConfigSection
                title="Audio Source"
                onReset={resetBarsSource}
                isAtDefault={isDefaultBarsSource}
                resetTestId="reset-audio-source"
              >
                <div className="grid grid-cols-2 gap-1.5">
                  {BARS_SOURCE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      data-testid={`opt-bars-source-${opt.value}`}
                      onClick={() => setDraft(d => ({ ...d, bars_source: opt.value }))}
                      className={cn(
                        'text-left rounded border p-2.5 text-sm transition-colors',
                        draft.bars_source === opt.value
                          ? 'border-primary bg-primary/10'
                          : 'border-border hover:border-muted-foreground/60',
                      )}
                    >
                      <div className="font-medium leading-tight">{opt.label}</div>
                      <div className="text-xs text-muted-foreground leading-snug mt-0.5">{opt.description}</div>
                    </button>
                  ))}
                </div>
              </ConfigSection>
            </div>

            <div className="h-px bg-border/30" />

            {/* Frequency Range — interactive dual-handle slider */}
            <div data-testid="section-freq-range">
              <ConfigSection
                title="Frequency Range"
                onReset={resetFreqRange}
                isAtDefault={isDefaultFreqRange}
                resetTestId="reset-freq-range"
              >
                <FrequencyRangeSlider
                  lowHz={lowHz}
                  highHz={highHz}
                  bands={barsNum}
                  onLowChange={(hz) => setDraft(d => ({ ...d, lower_cutoff_freq: String(hz) }))}
                  onHighChange={(hz) => setDraft(d => ({ ...d, higher_cutoff_freq: String(hz) }))}
                />
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">Low cut</label>
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={20}
                        max={500}
                        value={draft.lower_cutoff_freq}
                        onChange={e => setDraft(d => ({ ...d, lower_cutoff_freq: e.target.value }))}
                        className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="field-lower-cutoff"
                      />
                      <span className="text-xs text-muted-foreground shrink-0">Hz</span>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">High cut</label>
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={1000}
                        max={20000}
                        value={draft.higher_cutoff_freq}
                        onChange={e => setDraft(d => ({ ...d, higher_cutoff_freq: e.target.value }))}
                        className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                        data-testid="field-higher-cutoff"
                      />
                      <span className="text-xs text-muted-foreground shrink-0">Hz</span>
                    </div>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground/60">
                  Sets the frequency window analysed. 50–12000 Hz covers most music.
                </p>
              </ConfigSection>
            </div>

            <div className="h-px bg-border/30" />

            {/* Spectrum Resolution */}
            <div data-testid="section-spectrum">
              <ConfigSection
                title="Spectrum Resolution"
                onReset={resetBars}
                isAtDefault={isDefaultBars}
                resetTestId="reset-bars"
              >
                <SliderField
                  label="Frequency bands"
                  value={barsNum}
                  min={10}
                  max={60}
                  step={1}
                  format={(v) => `${v} bands`}
                  onChange={(v) => setDraft(d => ({ ...d, bars: String(v) }))}
                  inputHz={barsNum}
                  inputMin={10}
                  inputMax={60}
                  onInputCommit={(v) => setDraft(d => ({ ...d, bars: String(v) }))}
                  inputTestId="field-bars"
                />
                <p className="text-xs text-muted-foreground/60">
                  20–30 bands works well for most rooms. More bands = finer detail.
                </p>
              </ConfigSection>
            </div>
          </div>

          {/* ── RIGHT: Beat Detection ──────────────────────────────────── */}
          <div className="px-5 py-5 space-y-5">
            <ColumnHeading title="Beat Detection" />

            {/* Detection Method — Combined full-width, Multiband + SuperFlux side-by-side */}
            <div data-testid="section-beat-detection">
              <ConfigSection
                title="Detection Method"
                onReset={resetOnsetMethod}
                isAtDefault={isDefaultOnsetMethod}
                resetTestId="reset-onset-method"
              >
                <div className="space-y-1.5">
                  <button
                    type="button"
                    data-testid={`opt-onset-method-${combinedOpt.value}`}
                    onClick={() => setDraft(d => ({ ...d, onset_method: combinedOpt.value }))}
                    className={cn(
                      'w-full text-left rounded border p-2.5 text-sm transition-colors',
                      draft.onset_method === combinedOpt.value
                        ? 'border-primary bg-primary/10'
                        : 'border-border hover:border-muted-foreground/60',
                    )}
                  >
                    <div className="font-medium leading-tight">{combinedOpt.label}</div>
                    <div className="text-xs text-muted-foreground leading-tight mt-0.5">{combinedOpt.description}</div>
                  </button>
                  <div className="grid grid-cols-2 gap-1.5">
                    {otherOpts.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        data-testid={`opt-onset-method-${opt.value}`}
                        onClick={() => setDraft(d => ({ ...d, onset_method: opt.value }))}
                        className={cn(
                          'text-left rounded border p-2.5 text-sm transition-colors',
                          draft.onset_method === opt.value
                            ? 'border-primary bg-primary/10'
                            : 'border-border hover:border-muted-foreground/60',
                        )}
                      >
                        <div className="font-medium leading-tight">{opt.label}</div>
                        <div className="text-xs text-muted-foreground leading-snug mt-0.5">{opt.description}</div>
                      </button>
                    ))}
                  </div>
                </div>
              </ConfigSection>
            </div>

            <div className="h-px bg-border/30" />

            {/* Beat Sensitivity */}
            <div data-testid="section-beat-sensitivity">
              <ConfigSection
                title="Beat Sensitivity"
                onReset={resetOnsetDelta}
                isAtDefault={isDefaultOnsetDelta}
                resetTestId="reset-onset-delta"
              >
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <label className="text-sm" htmlFor="onset-delta-input">Beat sensitivity</label>
                    <span className="font-mono text-sm tabular-nums text-muted-foreground">{draft.onset_delta}</span>
                  </div>
                  <input
                    id="onset-delta-input"
                    type="number"
                    step={0.01}
                    min={0.01}
                    max={1.0}
                    value={draft.onset_delta}
                    onChange={e => setDraft(d => ({ ...d, onset_delta: e.target.value }))}
                    className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="field-onset-delta"
                  />
                  <p className="text-xs text-muted-foreground/60">
                    Lower values catch softer beats; higher values require stronger hits to trigger.
                  </p>
                </div>
              </ConfigSection>
            </div>

            <div className="h-px bg-border/30" />

            {/* Harmonic / Percussive Separation */}
            <div data-testid="section-advanced-processing">
              <ConfigSection
                title="Harmonic / Percussive Separation"
                onReset={resetHpss}
                isAtDefault={isDefaultHpss}
                resetTestId="reset-hpss"
              >
                <div className="flex items-start gap-2.5 rounded border border-border/50 p-3 bg-muted/20">
                  <input
                    id="hpss-check"
                    type="checkbox"
                    checked={draft.use_hpss_separation}
                    onChange={e => setDraft(d => ({ ...d, use_hpss_separation: e.target.checked }))}
                    className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer"
                    data-testid="field-use-hpss"
                  />
                  <div>
                    <label htmlFor="hpss-check" className="text-sm font-medium cursor-pointer">
                      Enable separation
                    </label>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Splits audio into rhythm and melody layers. Beat-driven effects react to percussion;
                      energy-based effects react to melody and harmony.
                    </p>
                  </div>
                </div>
              </ConfigSection>
            </div>

            {/* Expert mode: Onset Tuning — inside Beat Detection column */}
            {expert && (
              <>
                <div className="h-px bg-border/30" />
                <div data-testid="section-onset-tuning">
                  <ConfigSection
                    title="Onset Tuning"
                    onReset={resetOnsetTuning}
                    isAtDefault={isDefaultOnsetTuning}
                    resetTestId="reset-onset-tuning"
                  >
                    <div className="space-y-4">
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <label className="text-sm" htmlFor="onset-alpha-input">Threshold adaptation</label>
                        </div>
                        <input
                          id="onset-alpha-input"
                          type="number"
                          step={0.01}
                          min={0}
                          max={1}
                          value={draft.onset_alpha}
                          onChange={e => setDraft(d => ({ ...d, onset_alpha: e.target.value }))}
                          className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid="field-onset-alpha"
                        />
                        <p className="text-xs text-muted-foreground/60">
                          How quickly the beat threshold adjusts to changing volume levels.
                        </p>
                      </div>

                      {draft.onset_method === 'superflux' && (
                        <>
                          <div className="space-y-1">
                            <label className="text-sm" htmlFor="superflux-mu-input">Vibrato suppression</label>
                            <input
                              id="superflux-mu-input"
                              type="number"
                              min={1}
                              max={20}
                              value={draft.superflux_mu}
                              onChange={e => setDraft(d => ({ ...d, superflux_mu: e.target.value }))}
                              className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                              data-testid="field-superflux-mu"
                            />
                            <p className="text-xs text-muted-foreground/60">
                              Filters out false beats from sustained notes and vocal runs. Higher = more filtering.
                            </p>
                          </div>
                          <div className="space-y-1">
                            <label className="text-sm" htmlFor="superflux-lag-input">Look-back window</label>
                            <input
                              id="superflux-lag-input"
                              type="number"
                              min={1}
                              max={10}
                              value={draft.superflux_lag}
                              onChange={e => setDraft(d => ({ ...d, superflux_lag: e.target.value }))}
                              className="flex h-8 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                              data-testid="field-superflux-lag"
                            />
                            <p className="text-xs text-muted-foreground/60">
                              Number of frames used for vibrato detection. Higher = more context, slightly slower response.
                            </p>
                          </div>
                        </>
                      )}
                    </div>
                  </ConfigSection>
                </div>
              </>
            )}
          </div>

        </div>
      </div>

      {/* Bottom action bar */}
      <div className="px-5 py-3 border-t border-border shrink-0 flex items-center gap-2">
        {saveError && <p className="text-xs text-destructive flex-1">{saveError}</p>}
        {!saveError && <span className="flex-1" />}
        {isCreating && (
          <Button
            size="sm"
            variant="outline"
            onClick={onCancelCreate}
            disabled={saving}
            data-testid="analyser-cancel-btn"
          >
            Cancel
          </Button>
        )}
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving}
          data-testid="analyser-save-btn"
        >
          {saving ? 'Saving…' : isCreating ? 'Create analyser' : 'Save changes'}
        </Button>
      </div>
    </div>
  )
}

// ── Analysers (main) ──────────────────────────────────────────────────────────

export function Analysers() {
  const [analysers, setAnalysers] = useState<Analyser[]>([])
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function loadAll() {
    try {
      const [as, cs] = await Promise.all([getAnalysers(), getCouplings()])
      setAnalysers(as)
      setCouplings(cs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleClone(id: string) {
    setActionError(null)
    try {
      const cloned = await cloneAnalyser(id)
      await loadAll()
      setSelectedId(cloned.id)
      setIsCreating(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Clone failed')
    }
  }

  async function handleDelete(id: string) {
    setActionError(null)
    try {
      await deleteAnalyser(id)
      if (selectedId === id) { setSelectedId(null); setIsCreating(false) }
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  function handleSaved(a: Analyser) {
    setSelectedId(a.id)
    setIsCreating(false)
    loadAll()
  }

  const workspaceKey = isCreating ? '__new__' : (selectedId ?? '__none__')
  const selectedAnalyser = isCreating
    ? null
    : (analysers.find(a => a.id === selectedId) ?? null)
  const showWorkspace = isCreating || selectedId !== null

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          Analysers
        </span>
        <Button size="sm" onClick={() => { setIsCreating(true); setSelectedId(null) }}>
          New analyser
        </Button>
      </div>

      {actionError && (
        <div className="px-4 py-1.5 text-xs text-destructive border-b border-border bg-destructive/5 shrink-0">
          {actionError}
        </div>
      )}

      <div className="flex-1 overflow-hidden flex">
        {/* Compact analyser list */}
        <div className="w-52 shrink-0 border-r border-border overflow-y-auto py-2 px-2">
          {loading ? (
            <p className="text-xs text-muted-foreground px-1 py-2">Loading…</p>
          ) : error ? (
            <p className="text-xs text-destructive px-1 py-2">{error}</p>
          ) : analysers.length === 0 ? (
            <p className="text-xs text-muted-foreground/50 px-1 py-2 leading-relaxed">
              No analysers yet.
            </p>
          ) : (
            analysers.map(a => (
              <AnalyserListItem
                key={a.id}
                analyser={a}
                isSelected={!isCreating && a.id === selectedId}
                onSelect={() => { setSelectedId(a.id); setIsCreating(false) }}
              />
            ))
          )}
        </div>

        {/* Workspace */}
        <div className="flex-1 overflow-hidden flex">
          {showWorkspace ? (
            <AnalyserWorkspace
              key={workspaceKey}
              analyser={selectedAnalyser}
              couplings={couplings}
              onSaved={handleSaved}
              onDeleted={handleDelete}
              onCloned={handleClone}
              onCancelCreate={() => { setIsCreating(false); setSelectedId(null) }}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-muted-foreground/35">
                Select an analyser to configure it
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
