import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EffectCard } from '@/components/EffectCard'
import { EffectPreview } from '@/components/EffectPreview'
import { EditorPageHeader } from '@/components/editor/EditorPageHeader'
import { SectionLabel } from '@/components/editor/SectionLabel'
import { usePreviewSocket } from '@/hooks/usePreviewSocket'
import { calcBlendMix, settleTime, responseToSlider, sliderToResponse } from '@/lib/blend'
import { cn } from '@/lib/utils'
import {
  type EnergyProfile,
  type Effect,
  getEnergyProfiles,
  createEnergyProfile,
  updateEnergyProfile,
  deleteEnergyProfile,
  getEffects,
} from '@/lib/api'

// Semantic energy zone colors — purely for UI navigation, not actual effect/light colors.
const E_LOW   = '#22D3EE'  // cyan-400
const E_TRANS = '#8B5CF6'  // violet-500
const E_HIGH  = '#FB7185'  // rose-400

const ENERGY_PROFILE_DEFAULTS = {
  blend_start:    0.3,
  blend_end:      0.7,
  blend_response: 0.1,
} as const

// ── Form state ────────────────────────────────────────────────────────────────

interface FormState {
  name: string
  high_energy_effect_id: string
  low_energy_effect_id: string
  blend_start: string
  blend_end: string
  blend_response: string
}

function defaultForm(ep?: EnergyProfile): FormState {
  return {
    name: ep?.name ?? '',
    high_energy_effect_id: ep?.high_energy_effect_id ?? '',
    low_energy_effect_id:  ep?.low_energy_effect_id  ?? '',
    blend_start:    String(ep?.blend_start    ?? ENERGY_PROFILE_DEFAULTS.blend_start),
    blend_end:      String(ep?.blend_end      ?? ENERGY_PROFILE_DEFAULTS.blend_end),
    blend_response: String(ep?.blend_response ?? ENERGY_PROFILE_DEFAULTS.blend_response),
  }
}

// ── Energy position marker color ─────────────────────────────────────────────
// Interpolates cyan→violet→coral based on simulated energy position.
// Matches the semantic zone colors without covering the full track.

function energyMarkerColor(v: number): string {
  const [r1, g1, b1] = v <= 0.5
    ? [34,  211, 238] : [139, 92, 246]  // cyan or violet (left half)
  const [r2, g2, b2] = v <= 0.5
    ? [139, 92,  246] : [251, 113, 133] // violet or coral (right half)
  const t = v <= 0.5 ? v * 2 : (v - 0.5) * 2
  return `rgb(${Math.round(r1 + t*(r2-r1))},${Math.round(g1 + t*(g2-g1))},${Math.round(b1 + t*(b2-b1))})`
}

// ── Simulated energy position slider ─────────────────────────────────────────
// Neutral track; marker color interpolates through energy semantic zones.
// Avoids repeating the full Energy Blend gradient on a position control.

function EnergyPositionSlider({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const trackRef = useRef<HTMLDivElement>(null)

  function posFromMouse(clientX: number): number {
    if (!trackRef.current) return value
    const rect = trackRef.current.getBoundingClientRect()
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
  }

  function handleMouseDown(e: React.MouseEvent) {
    e.preventDefault()
    onChange(posFromMouse(e.clientX))
    const onMove = (ev: MouseEvent) => onChange(posFromMouse(ev.clientX))
    const onUp = () => document.removeEventListener('mousemove', onMove)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp, { once: true })
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if      (e.key === 'ArrowRight') { e.preventDefault(); onChange(Math.min(1, value + 0.01)) }
    else if (e.key === 'ArrowLeft')  { e.preventDefault(); onChange(Math.max(0, value - 0.01)) }
  }

  const color = energyMarkerColor(value)

  return (
    <div
      ref={trackRef}
      data-testid="energy-slider"
      className="relative h-5 touch-none select-none cursor-pointer"
      onMouseDown={handleMouseDown}
    >
      <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-1.5 rounded-full bg-white/10" />
      <div
        role="slider"
        tabIndex={0}
        aria-label="Simulated energy"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-4 h-4 rounded-full ring-1 ring-white/25 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
        style={{ left: `${value * 100}%`, backgroundColor: color, boxShadow: `0 0 8px ${color}99` }}
        onKeyDown={handleKeyDown}
      />
    </div>
  )
}

// ── Conceptual output preview ─────────────────────────────────────────────────
// Uses EffectPreview semantics — vivid per-effect colors, not energy-role colors.
// When effects differ, splits proportionally: left = low, right = high (by mix).
// This ensures Swirl and Spectrum RGB look visually distinct, not both teal.

function ConceptualPreview({ lowEffectType, highEffectType, mix, energy }: {
  lowEffectType: string
  highEffectType: string
  mix: number
  energy: number
}) {
  const e = Math.max(0.40, energy)  // ensure preview remains visible at low simulated energy
  const lowFrac  = 1 - mix
  const highFrac = mix
  const sameFx = lowEffectType === highEffectType

  if (sameFx || lowFrac < 0.03) {
    return (
      <div data-testid="conceptual-preview">
        <EffectPreview effectType={highEffectType} energy={e} count={8} size="md" />
      </div>
    )
  }
  if (highFrac < 0.03) {
    return (
      <div data-testid="conceptual-preview">
        <EffectPreview effectType={lowEffectType} energy={e} count={8} size="md" />
      </div>
    )
  }

  return (
    <div className="flex" data-testid="conceptual-preview">
      <div className="overflow-hidden min-w-0" style={{ flex: lowFrac }}>
        <EffectPreview effectType={lowEffectType} energy={e} count={8} size="md" />
      </div>
      <div className="w-px bg-border/25 shrink-0 self-stretch my-2" />
      <div className="overflow-hidden min-w-0" style={{ flex: highFrac }}>
        <EffectPreview effectType={highEffectType} energy={e} count={8} size="md" />
      </div>
    </div>
  )
}

// ── Live channel preview ──────────────────────────────────────────────────────
// Renders per-channel colours from the WebSocket feed as emissive light dots.
// Mirrors the emissive model in EffectPreview (γ≈0.58, bloom above 70%).

function channelDotBg(r: number, g: number, b: number): string {
  const lum = r * 0.2126 + g * 0.7152 + b * 0.0722
  if (lum < 0.04) return 'rgb(8,8,8)'
  const t = Math.pow(lum, 0.58)
  const scale = t / lum
  let rr = r * 255 * scale, gg = g * 255 * scale, bb = b * 255 * scale
  if (t > 0.70) {
    const bloom = ((t - 0.70) / 0.30) * 95
    rr = Math.min(255, rr + bloom)
    gg = Math.min(255, gg + bloom)
    bb = Math.min(255, bb + bloom)
  }
  return `rgb(${Math.round(rr)},${Math.round(gg)},${Math.round(bb)})`
}

function channelGlow(r: number, g: number, b: number): string {
  const lum = r * 0.2126 + g * 0.7152 + b * 0.0722
  if (lum < 0.04) return 'none'
  const R = Math.round(r * 255), G = Math.round(g * 255), B = Math.round(b * 255)
  const inner = Math.round(lum * 6)
  const outer = Math.round(lum * 18)
  return [
    `0 0 ${inner}px rgba(${R},${G},${B},${Math.min(0.90, lum * 0.95).toFixed(2)})`,
    `0 0 ${outer}px rgba(${R},${G},${B},${Math.min(0.55, lum * 0.60).toFixed(2)})`,
  ].join(', ')
}

function LiveChannelPreview({ channels }: { channels: Array<{ r: number; g: number; b: number }> }) {
  return (
    <div
      className="flex items-center justify-center gap-2 py-3 flex-wrap"
      data-testid="live-channel-dots"
    >
      {channels.slice(0, 12).map((ch, i) => (
        <div
          key={i}
          data-testid={`channel-dot-${i}`}
          className="rounded-full w-9 h-9 shrink-0 transition-all duration-75"
          style={{
            backgroundColor: channelDotBg(ch.r, ch.g, ch.b),
            boxShadow: channelGlow(ch.r, ch.g, ch.b),
          }}
        />
      ))}
    </div>
  )
}

// ── Section with reset button ─────────────────────────────────────────────────

interface SectionWithResetProps {
  title: React.ReactNode
  isAtDefault: boolean
  onReset: () => void
  resetTestId?: string
  children: React.ReactNode
}

function SectionWithReset({ title, isAtDefault, onReset, resetTestId, children }: SectionWithResetProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <SectionLabel>{title}</SectionLabel>
        <button
          type="button"
          onClick={isAtDefault ? undefined : onReset}
          aria-disabled={isAtDefault}
          data-testid={resetTestId}
          className={cn(
            'text-[9px] uppercase tracking-wide transition-colors leading-none py-0.5',
            isAtDefault
              ? 'text-muted-foreground/25 pointer-events-none'
              : 'text-foreground cursor-pointer',
          )}
        >
          Reset
        </button>
      </div>
      {children}
    </div>
  )
}

// ── Mode toggle ───────────────────────────────────────────────────────────────

function ModeToggle({ expertMode, onToggle }: { expertMode: boolean; onToggle: (v: boolean) => void }) {
  return (
    <div className="flex shrink-0 rounded-md border border-border overflow-hidden">
      <button
        type="button"
        data-testid="mode-standard-btn"
        onClick={() => onToggle(false)}
        className={cn(
          'px-3 py-1 text-xs transition-colors',
          !expertMode
            ? 'bg-muted font-semibold text-foreground'
            : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
        )}
      >
        Standard
      </button>
      <button
        type="button"
        data-testid="mode-expert-btn"
        onClick={() => onToggle(true)}
        className={cn(
          'px-3 py-1 text-xs transition-colors border-l border-border',
          expertMode
            ? 'bg-muted font-semibold text-foreground'
            : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
        )}
      >
        Expert
      </button>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function EnergyProfiles() {
  const [energyProfiles, setEnergyProfiles] = useState<EnergyProfile[]>([])
  const [effects, setEffects] = useState<Effect[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [simulatedEnergy, setSimulatedEnergy] = useState(0.5)
  const [expertMode, setExpertMode] = useState(false)

  const preview = usePreviewSocket()

  // --- Derived values ---
  const blendStart    = parseFloat(form.blend_start)    || 0
  const blendEnd      = parseFloat(form.blend_end)      || 1
  const blendResponse = parseFloat(form.blend_response) || 0.1
  const startPct = blendStart * 100
  const endPct   = blendEnd   * 100

  const blendAtDefault    = Math.abs(blendStart    - ENERGY_PROFILE_DEFAULTS.blend_start)    < 0.001
                         && Math.abs(blendEnd      - ENERGY_PROFILE_DEFAULTS.blend_end)      < 0.001
  const responseAtDefault = Math.abs(blendResponse - ENERGY_PROFILE_DEFAULTS.blend_response) < 0.001

  const isLive = editingId !== null && editingId !== 'new'
    && preview.status?.active_energy_profile_id === editingId

  const displayEnergy = isLive ? preview.energy : simulatedEnergy
  const instantMix    = calcBlendMix(displayEnergy, blendStart, blendEnd)
  const displayMix    = isLive ? preview.mix : instantMix

  const highEffect    = effects.find((e) => e.id === form.high_energy_effect_id)
  const lowEffect     = effects.find((e) => e.id === form.low_energy_effect_id)
  const lowEffectType = (lowEffect ?? highEffect)?.effect_type ?? 'mono_pulse'
  const highEffectType = highEffect?.effect_type ?? 'mono_pulse'

  const lowPct  = Math.round((1 - displayMix) * 100)
  const highPct = Math.round(displayMix * 100)

  // --- Load ---
  async function load() {
    try {
      const [eps, effs] = await Promise.all([getEnergyProfiles(), getEffects()])
      setEnergyProfiles(eps)
      setEffects(effs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // --- Form helpers ---
  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function handleRangeChange([start, end]: number[]) {
    set('blend_start', String(Math.round(start * 100) / 100))
    set('blend_end',   String(Math.round(end   * 100) / 100))
  }

  // --- Editor lifecycle ---
  function openNew() {
    setEditingId('new')
    setForm(defaultForm())
    setSaveError(null)
    setSimulatedEnergy(0.5)
    setExpertMode(false)
  }

  function openEdit(ep: EnergyProfile) {
    setEditingId(ep.id)
    setForm(defaultForm(ep))
    setSaveError(null)
    setSimulatedEnergy(0.5)
    setExpertMode(false)
  }

  function closeEditor() {
    setEditingId(null)
    setSaveError(null)
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        high_energy_effect_id: form.high_energy_effect_id,
        low_energy_effect_id:  form.low_energy_effect_id,
        blend_start:    parseFloat(form.blend_start),
        blend_end:      parseFloat(form.blend_end),
        blend_response: parseFloat(form.blend_response),
      }
      if (editingId === 'new') {
        await createEnergyProfile(body)
      } else if (editingId) {
        await updateEnergyProfile(editingId, body)
      }
      setEditingId(null)
      await load()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    await deleteEnergyProfile(id)
    await load()
  }

  // --- Loading / error ---
  if (loading || error) {
    return (
      <div className="flex items-center justify-center h-full text-sm">
        {loading
          ? <span className="text-muted-foreground">Loading…</span>
          : <span className="text-destructive">{error}</span>}
      </div>
    )
  }

  const effectName = (id: string) =>
    effects.find((e) => e.id === id)?.name ?? (id ? id.slice(0, 8) + '…' : '—')

  // =========================================================================
  // WORKSPACE VIEW
  // =========================================================================

  if (editingId !== null) {
    return (
      <div className="flex flex-col h-full bg-background">

        <EditorPageHeader
          aria-label="Energy Profile name"
          name={form.name}
          placeholder="Energy Profile name…"
          onNameChange={(v) => set('name', v)}
          isLive={isLive}
          error={saveError}
          saving={saving}
          saveDisabled={!form.high_energy_effect_id}
          onCancel={closeEditor}
          onSave={handleSave}
          additionalControls={
            <ModeToggle expertMode={expertMode} onToggle={setExpertMode} />
          }
        />

        {/* Body: main workspace + inspector */}
        <div className="flex flex-1 overflow-hidden">

          {/* Main workspace */}
          <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8">

            {/* ── Effect cards ── */}
            <section>
              <SectionLabel className="mb-3">Effects</SectionLabel>
              <div className="grid grid-cols-2 gap-4">
                <EffectCard
                  role="low"
                  effectId={form.low_energy_effect_id}
                  allEffects={effects}
                  onChange={(id) => set('low_energy_effect_id', id)}
                />
                <EffectCard
                  role="high"
                  effectId={form.high_energy_effect_id}
                  allEffects={effects}
                  onChange={(id) => set('high_energy_effect_id', id)}
                />
              </div>
            </section>

            {/* ── Energy blend zone ── */}
            <SectionWithReset
              title="Energy blend"
              isAtDefault={blendAtDefault}
              onReset={() => { set('blend_start', '0.3'); set('blend_end', '0.7') }}
              resetTestId="reset-blend"
            >
              {/* Three-zone color bar: LOW (cyan) | TRANSITION (gradient) | HIGH (coral) */}
              <div
                data-testid="blend-zone-bar"
                className="relative h-2.5 rounded-full overflow-hidden mb-1"
              >
                <div
                  className="absolute inset-y-0 left-0"
                  style={{ right: `${100 - startPct}%`, background: 'rgba(34,211,238,0.40)' }}
                />
                <div
                  className="absolute inset-y-0"
                  style={{
                    left: `${startPct}%`,
                    right: `${100 - endPct}%`,
                    background: 'linear-gradient(to right, rgba(34,211,238,0.40), rgba(139,92,246,0.40), rgba(251,113,133,0.40))',
                  }}
                />
                <div
                  className="absolute inset-y-0 right-0"
                  style={{ left: `${endPct}%`, background: 'rgba(251,113,133,0.40)' }}
                />
              </div>

              {/* Dual-handle slider */}
              <Slider
                aria-label="Blend range"
                min={0}
                max={1}
                step={0.01}
                value={[blendStart, blendEnd]}
                onValueChange={handleRangeChange}
              />

              {/* Zone labels */}
              <div className="relative h-5 mt-1 text-xs font-mono select-none">
                <span className="absolute left-0" style={{ color: E_LOW + 'B3' }}>LOW 100%</span>
                <span
                  className="absolute -translate-x-1/2"
                  style={{ left: `${(startPct + endPct) / 2}%`, color: E_TRANS + 'B3' }}
                >
                  AUTO BLEND
                </span>
                <span className="absolute right-0" style={{ color: E_HIGH + 'B3' }}>HIGH 100%</span>
              </div>

              {/* Percent ticks */}
              <div className="relative h-4 mt-0.5 text-[10px] font-mono text-muted-foreground/40 select-none">
                <span className="absolute left-0">0%</span>
                {startPct > 8 && startPct < 92 && (
                  <span
                    className="absolute -translate-x-1/2"
                    style={{ left: `${startPct}%`, color: E_LOW + '99' }}
                  >
                    {Math.round(startPct)}%
                  </span>
                )}
                {endPct > 8 && endPct < 92 && Math.abs(endPct - startPct) > 6 && (
                  <span
                    className="absolute -translate-x-1/2"
                    style={{ left: `${endPct}%`, color: E_HIGH + '99' }}
                  >
                    {Math.round(endPct)}%
                  </span>
                )}
                <span className="absolute right-0">100%</span>
              </div>

              {/* Energy marker */}
              <div className="relative h-7 mt-1 select-none">
                <div
                  className="absolute flex flex-col items-center -translate-x-1/2"
                  style={{ left: `${displayEnergy * 100}%` }}
                >
                  <div className={cn('w-px h-4', isLive ? 'bg-green-400' : 'bg-muted-foreground/35')} />
                  <span
                    className={cn(
                      'text-[9px] whitespace-nowrap font-mono',
                      isLive ? 'text-green-400' : 'text-muted-foreground/50',
                    )}
                  >
                    {isLive ? `● live ${Math.round(displayEnergy * 100)}%` : `${Math.round(displayEnergy * 100)}%`}
                  </span>
                </div>
              </div>

              {/* Simulate slider — neutral track, position-colored marker, hidden when live */}
              {!isLive && (
                <div className="mt-3 space-y-1.5">
                  <p className="text-xs text-muted-foreground/60">Drag to simulate music energy</p>
                  <EnergyPositionSlider
                    value={simulatedEnergy}
                    onChange={setSimulatedEnergy}
                  />
                </div>
              )}
            </SectionWithReset>

            {/* ── Current blend ── */}
            <section>
              <SectionLabel className="mb-3">Current blend</SectionLabel>
              <div className="flex items-center gap-3">
                <span className="text-xs w-32 text-right truncate shrink-0" style={{ color: E_LOW }}>
                  {(lowEffect ?? highEffect)?.name ?? 'Low energy'}&nbsp;{lowPct}%
                </span>
                <div
                  className="flex-1 h-2.5 rounded-full overflow-hidden bg-secondary relative"
                  data-testid="current-blend-bar"
                >
                  {/* Low segment — restrained fill; strong color lives in the label */}
                  <div
                    className="absolute inset-y-0 left-0 transition-all duration-100"
                    style={{ right: `${100 - lowPct}%`, background: 'rgba(34,211,238,0.28)' }}
                  />
                  {/* High segment */}
                  <div
                    className="absolute inset-y-0 right-0 transition-all duration-100"
                    style={{ left: `${lowPct}%`, background: 'rgba(251,113,133,0.28)' }}
                  />
                  {/* Proportion divider */}
                  {lowPct > 2 && lowPct < 98 && (
                    <div
                      className="absolute inset-y-0 w-px bg-white/20 transition-all duration-100"
                      style={{ left: `${lowPct}%` }}
                    />
                  )}
                </div>
                <span className="text-xs w-32 truncate shrink-0" style={{ color: E_HIGH }}>
                  {highPct}%&nbsp;{highEffect?.name ?? 'High energy'}
                </span>
              </div>
            </section>

            {/* ── Output preview ── */}
            <section data-testid="output-preview-section">
              <div className="flex items-center justify-between mb-2">
                <SectionLabel>Output preview</SectionLabel>
                <span
                  data-testid={isLive ? 'label-live-output' : 'label-preview'}
                  className={cn(
                    'text-[9px] font-mono uppercase tracking-wide',
                    isLive ? 'text-green-400' : 'text-muted-foreground/40',
                  )}
                >
                  {isLive ? '● Live output' : 'Preview'}
                </span>
              </div>
              <div className="rounded-xl bg-black/25 border border-border/40 overflow-hidden">
                {isLive && preview.channel_colours.length > 0 ? (
                  <LiveChannelPreview channels={preview.channel_colours} />
                ) : (
                  <ConceptualPreview
                    lowEffectType={lowEffectType}
                    highEffectType={highEffectType}
                    mix={displayMix}
                    energy={displayEnergy}
                  />
                )}
              </div>
            </section>

          </div>

          {/* Inspector */}
          <aside className="w-80 border-l border-border shrink-0 overflow-y-auto">
            <div className="p-5 space-y-5">

              {/* Response */}
              <SectionWithReset
                title="Response"
                isAtDefault={responseAtDefault}
                onReset={() => set('blend_response', String(ENERGY_PROFILE_DEFAULTS.blend_response))}
                resetTestId="reset-response"
              >
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-12 shrink-0">Smooth</span>
                    <Slider
                      aria-label="Blend response"
                      min={0}
                      max={1}
                      step={0.001}
                      value={[responseToSlider(blendResponse)]}
                      onValueChange={(v) => set('blend_response', String(sliderToResponse(v[0])))}
                      className="flex-1"
                    />
                    <span className="text-xs text-muted-foreground w-8 text-right shrink-0">Fast</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Blend settles over {settleTime(blendResponse)} after a sustained energy change
                  </p>
                  {expertMode && (
                    <div className="pt-1 space-y-1">
                      <Label className="text-xs text-muted-foreground">Response (exact)</Label>
                      <Input
                        data-testid="field-blend-response"
                        type="number"
                        step={0.01}
                        min={0.01}
                        max={0.5}
                        value={form.blend_response}
                        onChange={(e) => set('blend_response', e.target.value)}
                        className="h-7 text-xs"
                      />
                    </div>
                  )}
                </div>
              </SectionWithReset>

              {expertMode && (
                <>
                  <Separator />
                  <div className="space-y-3">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                      Blend exact values
                    </span>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Blend start</Label>
                      <Input
                        data-testid="field-blend-start"
                        type="number"
                        step={0.05}
                        min={0}
                        max={1}
                        value={form.blend_start}
                        onChange={(e) => set('blend_start', e.target.value)}
                        className="h-7 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Blend end</Label>
                      <Input
                        data-testid="field-blend-end"
                        type="number"
                        step={0.05}
                        min={0}
                        max={1}
                        value={form.blend_end}
                        onChange={(e) => set('blend_end', e.target.value)}
                        className="h-7 text-xs"
                      />
                    </div>
                  </div>
                </>
              )}

            </div>
          </aside>

        </div>
      </div>
    )
  }

  // =========================================================================
  // LIST VIEW
  // =========================================================================

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Energy Profiles</h2>
          <Button size="sm" onClick={openNew}>New energy profile</Button>
        </div>

        {energyProfiles.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No energy profiles yet. Energy profiles are created automatically when a coupling is
            made, or create one here to share across couplings.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>High-energy effect</TableHead>
                <TableHead>Low-energy effect</TableHead>
                <TableHead>Blend range</TableHead>
                <TableHead>Blend response</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {energyProfiles.map((ep) => (
                <TableRow key={ep.id}>
                  <TableCell className="font-medium">{ep.name}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {effectName(ep.high_energy_effect_id)}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {ep.low_energy_effect_id
                      ? effectName(ep.low_energy_effect_id)
                      : '(same as high-energy)'}
                  </TableCell>
                  <TableCell className="text-sm font-mono">
                    {ep.blend_start}–{ep.blend_end}
                  </TableCell>
                  <TableCell className="text-sm font-mono">{ep.blend_response}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => openEdit(ep)}>
                        Edit
                      </Button>
                      <ConfirmDialog
                        trigger={
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                          >
                            Delete
                          </Button>
                        }
                        title="Delete energy profile"
                        description={`Delete "${ep.name}"? Any couplings referencing it will lose their effect settings.`}
                        onConfirm={() => handleDelete(ep.id)}
                      />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
