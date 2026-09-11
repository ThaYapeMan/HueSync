import { useEffect, useState } from 'react'
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
    bars_source: a?.bars_source ?? 'cava',
    onset_method: a?.onset_method ?? 'combined',
    bars: String(a?.bars ?? 30),
    lower_cutoff_freq: String(a?.lower_cutoff_freq ?? 50),
    higher_cutoff_freq: String(a?.higher_cutoff_freq ?? 12000),
    onset_delta: String(a?.onset_delta ?? 0.1),
    onset_alpha: String(a?.onset_alpha ?? 0.9),
    superflux_mu: String(a?.superflux_mu ?? 3),
    superflux_lag: String(a?.superflux_lag ?? 2),
    use_hpss_separation: a?.use_hpss_separation ?? false,
  }
}

// ── ConfigSection helper ──────────────────────────────────────────────────────

function ConfigSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div>
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {title}
        </span>
      </div>
      {children}
    </div>
  )
}

// ── FrequencyRangeBar ─────────────────────────────────────────────────────────

function FrequencyRangeBar({ lowHz, highHz, bands }: { lowHz: number; highHz: number; bands: number }) {
  const LOG_MIN = Math.log10(20)
  const LOG_MAX = Math.log10(20000)
  const toX = (hz: number) =>
    ((Math.log10(Math.max(20, Math.min(20000, hz))) - LOG_MIN) / (LOG_MAX - LOG_MIN)) * 100

  const leftX = toX(lowHz)
  const rightX = toX(highHz)
  const rangeWidth = rightX - leftX

  const landmarks = [
    { hz: 100, label: '100' },
    { hz: 1000, label: '1k' },
    { hz: 5000, label: '5k' },
    { hz: 10000, label: '10k' },
  ]

  return (
    <div data-testid="frequency-range-bar">
      <div className="relative h-6 bg-muted/40 rounded-sm border border-border/40 overflow-hidden">
        {/* Active range background */}
        <div
          className="absolute inset-y-0 bg-primary/15"
          style={{ left: `${leftX}%`, width: `${rangeWidth}%` }}
        />
        {/* Band division lines within active range */}
        {Array.from({ length: Math.max(0, bands - 1) }, (_, i) => {
          const bandX = leftX + rangeWidth * (i + 1) / bands
          return (
            <div
              key={i}
              className="absolute inset-y-0 w-px bg-primary/15"
              style={{ left: `${bandX}%` }}
            />
          )
        })}
        {/* Range boundary markers */}
        <div className="absolute inset-y-0 w-0.5 bg-primary/50 rounded-r" style={{ left: `${leftX}%` }} />
        <div className="absolute inset-y-0 w-0.5 bg-primary/50 rounded-l" style={{ left: `${rightX - 0.2}%` }} />
      </div>
      {/* Landmark labels */}
      <div className="relative h-4 mt-0.5">
        {landmarks.map(({ hz, label }) => {
          const x = toX(hz)
          return (
            <span
              key={hz}
              className="absolute text-[9px] text-muted-foreground/40 -translate-x-1/2"
              style={{ left: `${x}%` }}
            >
              {label}
            </span>
          )
        })}
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

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: draft.name.trim() || 'Unnamed',
        bars_source: draft.bars_source,
        onset_method: draft.onset_method,
        bars: parseInt(draft.bars, 10) || 30,
        lower_cutoff_freq: parseInt(draft.lower_cutoff_freq, 10) || 50,
        higher_cutoff_freq: parseInt(draft.higher_cutoff_freq, 10) || 12000,
        onset_delta: parseFloat(draft.onset_delta) || 0.1,
        onset_alpha: parseFloat(draft.onset_alpha) || 0.9,
        superflux_mu: parseInt(draft.superflux_mu, 10) || 3,
        superflux_lag: parseInt(draft.superflux_lag, 10) || 2,
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

  const lowHz = parseInt(draft.lower_cutoff_freq, 10) || 50
  const highHz = parseInt(draft.higher_cutoff_freq, 10) || 12000
  const barsNum = parseInt(draft.bars, 10) || 30

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

      {/* Scrollable config body */}
      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
        {/* 1. Audio Source */}
        <div data-testid="section-audio-source">
          <ConfigSection title="Audio Source">
            <div className="space-y-1.5">
              {BARS_SOURCE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  data-testid={`opt-bars-source-${opt.value}`}
                  onClick={() => setDraft(d => ({ ...d, bars_source: opt.value }))}
                  className={cn(
                    'w-full text-left rounded border p-2.5 text-sm transition-colors',
                    draft.bars_source === opt.value
                      ? 'border-primary bg-primary/10'
                      : 'border-border hover:border-muted-foreground/60',
                  )}
                >
                  <div className="font-medium leading-tight">{opt.label}</div>
                  <div className="text-xs text-muted-foreground leading-tight mt-0.5">{opt.description}</div>
                </button>
              ))}
            </div>
          </ConfigSection>
        </div>

        <div className="h-px bg-border/30" />

        {/* 2. Beat Detection */}
        <div data-testid="section-beat-detection">
          <ConfigSection title="Beat Detection">
            <div className="space-y-1.5">
              {ONSET_METHODS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  data-testid={`opt-onset-method-${opt.value}`}
                  onClick={() => setDraft(d => ({ ...d, onset_method: opt.value }))}
                  className={cn(
                    'w-full text-left rounded border p-2.5 text-sm transition-colors',
                    draft.onset_method === opt.value
                      ? 'border-primary bg-primary/10'
                      : 'border-border hover:border-muted-foreground/60',
                  )}
                >
                  <div className="font-medium leading-tight">{opt.label}</div>
                  <div className="text-xs text-muted-foreground leading-tight mt-0.5">{opt.description}</div>
                </button>
              ))}
            </div>
          </ConfigSection>
        </div>

        <div className="h-px bg-border/30" />

        {/* 3. Frequency Range */}
        <div data-testid="section-freq-range">
          <ConfigSection title="Frequency Range">
            <FrequencyRangeBar lowHz={lowHz} highHz={highHz} bands={barsNum} />
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

        {/* 4. Spectrum Resolution */}
        <div data-testid="section-spectrum">
          <ConfigSection title="Spectrum Resolution">
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

        <div className="h-px bg-border/30" />

        {/* 5. Beat Sensitivity */}
        <div data-testid="section-beat-sensitivity">
          <ConfigSection title="Beat Sensitivity">
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

        {/* 6. Advanced Processing */}
        <div data-testid="section-advanced-processing">
          <ConfigSection title="Advanced Processing">
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
                  Harmonic / percussive separation
                </label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Splits music into rhythm and melody layers for more targeted effects.
                  High-energy effects react to beats; low-energy effects react to melody.
                </p>
              </div>
            </div>
          </ConfigSection>
        </div>

        {/* Expert mode: Onset Tuning */}
        {expert && (
          <>
            <div className="h-px bg-border/30" />
            <div data-testid="section-onset-tuning">
              <ConfigSection title="Onset Tuning">
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
