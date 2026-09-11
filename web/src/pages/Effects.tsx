import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EditorPageHeader } from '@/components/editor/EditorPageHeader'
import { SectionLabel } from '@/components/editor/SectionLabel'
import { EffectPreview } from '@/components/EffectPreview'
import { EffectTypeSelector } from '@/components/EffectTypeSelector'
import { getEffectSwatchClass } from '@/lib/effectColors'
import { usePreviewSocket } from '@/hooks/usePreviewSocket'
import { cn } from '@/lib/utils'
import {
  EFFECTS,
  type Effect,
  type EnergyProfile,
  getEffects,
  createEffect,
  updateEffect,
  deleteEffect,
  getEnergyProfiles,
} from '@/lib/api'

// ── Effect defaults (from models.py) ─────────────────────────────────────────

export const EFFECT_DEFAULTS = {
  sensitivity:           1.0,
  brightness_floor:      0.15,
  onset_flash_intensity: 0.0,
  effect_speed:          1.0,
  effect_decay:          0.3,
  bass_hz:               250,
  mid_hz:                2000,
  exertion_clip:         3.0,
} as const

// ── Slider scale helpers ──────────────────────────────────────────────────────
const sens2s = (v: number) => Math.max(0, Math.min(1, (v - 0.1) / 2.9))
const s2sens = (v: number) => Math.round((0.1 + v * 2.9) * 10) / 10
const spd2s  = (v: number) => Math.max(0, Math.min(1, (v - 0.1) / 4.9))
const s2spd  = (v: number) => Math.round((0.1 + v * 4.9) * 10) / 10
const dec2s  = (v: number) => Math.max(0, Math.min(1, (v - 0.01) / 0.98))
const s2dec  = (v: number) => Math.round((0.01 + v * 0.98) * 100) / 100

// ── Form state ────────────────────────────────────────────────────────────────

interface FormState {
  name: string
  effect_type: string
  effect_speed: string
  effect_decay: string
  sensitivity: string
  brightness_floor: string
  bass_hz: string
  mid_hz: string
  exertion_clip: string
  onset_flash_intensity: string
}

function defaultForm(cfg?: Effect): FormState {
  return {
    name:                  cfg?.name                    ?? '',
    effect_type:           cfg?.effect_type             ?? 'spectrum_rgb',
    effect_speed:          String(cfg?.effect_speed         ?? EFFECT_DEFAULTS.effect_speed),
    effect_decay:          String(cfg?.effect_decay         ?? EFFECT_DEFAULTS.effect_decay),
    sensitivity:           String(cfg?.sensitivity          ?? EFFECT_DEFAULTS.sensitivity),
    brightness_floor:      String(cfg?.brightness_floor     ?? EFFECT_DEFAULTS.brightness_floor),
    bass_hz:               String(cfg?.bass_hz              ?? EFFECT_DEFAULTS.bass_hz),
    mid_hz:                String(cfg?.mid_hz               ?? EFFECT_DEFAULTS.mid_hz),
    exertion_clip:         String(cfg?.exertion_clip        ?? EFFECT_DEFAULTS.exertion_clip),
    onset_flash_intensity: String(cfg?.onset_flash_intensity ?? EFFECT_DEFAULTS.onset_flash_intensity),
  }
}

// ── Per-field row with Reset ──────────────────────────────────────────────────

interface FieldRowProps {
  label: string
  value?: string  // current numeric value to display
  isAtDefault: boolean
  onReset: () => void
  resetTestId?: string
  children: React.ReactNode
}

function FieldRow({ label, value, isAtDefault, onReset, resetTestId, children }: FieldRowProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{label}</span>
        <div className="flex items-center gap-2">
          {value !== undefined && (
            <span className="text-xs font-mono tabular-nums text-muted-foreground">{value}</span>
          )}
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
      </div>
      {children}
    </div>
  )
}

// ── Expert section with group Reset ──────────────────────────────────────────

interface ExpertSectionProps {
  title: string
  isAtDefault: boolean
  onReset: () => void
  resetTestId?: string
  children: React.ReactNode
}

function ExpertSection({ title, isAtDefault, onReset, resetTestId, children }: ExpertSectionProps) {
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

// ── Preview input ─────────────────────────────────────────────────────────────

type PreviewInput = 'calm' | 'groove' | 'beat-heavy' | 'live'

const PREVIEW_ENERGY: Record<PreviewInput, number> = {
  calm:         0.2,
  groove:       0.55,
  'beat-heavy': 0.82,
  live:         0,   // overridden at render time with liveEnergy
}

// ── Gallery card ──────────────────────────────────────────────────────────────

function effectSummary(effect: Effect): string {
  const parts: string[] = []
  if (effect.sensitivity < 0.7)        parts.push('Subtle')
  else if (effect.sensitivity > 1.5)   parts.push('Reactive')
  else                                  parts.push('Balanced')
  const meta = EFFECTS.find((e) => e.id === effect.effect_type)
  if (meta?.hasDecay) {
    if (effect.effect_decay > 0.5)     parts.push('Punchy')
    else if (effect.effect_decay < 0.2) parts.push('Slow fade')
    else                               parts.push('Smooth')
  }
  if (meta?.hasSpeed) {
    if (effect.effect_speed > 2.5)     parts.push('Fast')
    else if (effect.effect_speed < 0.8) parts.push('Slow')
  }
  return parts.join(' · ')
}

interface GalleryCardProps {
  effect: Effect
  onEdit: () => void
  onDelete: () => void
}

function GalleryCard({ effect, onEdit, onDelete }: GalleryCardProps) {
  const meta = EFFECTS.find((e) => e.id === effect.effect_type)
  const swatchClass = getEffectSwatchClass(effect.effect_type)

  return (
    <div
      className="border border-border rounded-xl flex flex-col overflow-hidden bg-card hover:border-muted-foreground/30 transition-colors"
      data-testid={`effect-card-${effect.id}`}
    >
      <div className="bg-black/20 py-5 px-4 flex flex-col items-center justify-center gap-3 min-h-[96px]">
        <EffectPreview effectType={effect.effect_type} energy={0.80} count={6} size="md" />
        <div className={cn('h-1 w-14 rounded-full opacity-60', swatchClass)} />
      </div>
      <div className="p-4 flex flex-col gap-2 flex-1">
        <div>
          <div className="font-semibold text-sm leading-tight">{effect.name}</div>
          <div className="text-xs text-muted-foreground mt-0.5">{meta?.label ?? effect.effect_type}</div>
        </div>
        <div className="text-xs text-muted-foreground/70 leading-snug flex-1">
          {effectSummary(effect)}
        </div>
        <div className="flex items-center gap-1 pt-1">
          <Button size="sm" variant="outline" className="text-xs h-7 flex-1" onClick={onEdit}
            data-testid={`edit-effect-${effect.id}`}>
            Edit
          </Button>
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="ghost" className="text-xs h-7 text-destructive hover:text-destructive">
                Delete
              </Button>
            }
            title="Delete effect"
            description={`Delete "${effect.name}"? This cannot be undone.`}
            onConfirm={onDelete}
          />
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function Effects() {
  const [effects, setEffects] = useState<Effect[]>([])
  const [energyProfiles, setEnergyProfiles] = useState<EnergyProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [previewInput, setPreviewInput] = useState<PreviewInput>('groove')
  const [expertMode, setExpertMode] = useState(false)

  const preview = usePreviewSocket()

  // ── derived values ──
  const selectedMeta = EFFECTS.find((e) => e.id === form.effect_type)
  const isNoneType   = form.effect_type === 'none'
  const sensitivity  = parseFloat(form.sensitivity)          || 1
  const speed        = parseFloat(form.effect_speed)         || 1
  const decay        = parseFloat(form.effect_decay)         || 0.3
  const floor        = parseFloat(form.brightness_floor)     || 0
  const flash        = parseFloat(form.onset_flash_intensity) || 0
  const bassHz       = parseInt(form.bass_hz, 10)            || EFFECT_DEFAULTS.bass_hz
  const midHz        = parseInt(form.mid_hz, 10)             || EFFECT_DEFAULTS.mid_hz
  const exertionClip = parseFloat(form.exertion_clip)        || EFFECT_DEFAULTS.exertion_clip

  // ── at-default checks ──
  const sensDef    = Math.abs(sensitivity  - EFFECT_DEFAULTS.sensitivity)           < 0.001
  const floorDef   = Math.abs(floor        - EFFECT_DEFAULTS.brightness_floor)      < 0.001
  const flashDef   = Math.abs(flash        - EFFECT_DEFAULTS.onset_flash_intensity) < 0.001
  const speedDef   = Math.abs(speed        - EFFECT_DEFAULTS.effect_speed)          < 0.001
  const decayDef   = Math.abs(decay        - EFFECT_DEFAULTS.effect_decay)          < 0.001
  const freqDef    = bassHz === EFFECT_DEFAULTS.bass_hz && midHz === EFFECT_DEFAULTS.mid_hz
  const exertDef   = Math.abs(exertionClip - EFFECT_DEFAULTS.exertion_clip)         < 0.001

  // ── preview energy computation ──
  // Preview Input supplies energy to EffectPreview only — never mutates form/Effect config.
  const liveEnergy    = preview.energy
  const baseEnergy    = previewInput === 'live' ? liveEnergy : PREVIEW_ENERGY[previewInput]
  const displayEnergy = Math.min(1, Math.max(0, baseEnergy * Math.min(sensitivity, 3) / 1.5))

  // ── live badge ──
  const activeEp = energyProfiles.find(
    (ep) => ep.id === preview.status?.active_energy_profile_id,
  )
  const isLive = editingId !== null && editingId !== 'new' && (
    activeEp?.high_energy_effect_id === editingId ||
    activeEp?.low_energy_effect_id  === editingId
  )

  // ── load ──
  async function load() {
    try {
      const [effs, eps] = await Promise.all([getEffects(), getEnergyProfiles()])
      setEffects(effs)
      setEnergyProfiles(eps)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // ── form helpers ──
  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function openNew() {
    setEditingId('new')
    setForm(defaultForm())
    setSaveError(null)
    setPreviewInput('groove')
    setExpertMode(false)
  }

  function openEdit(effect: Effect) {
    setEditingId(effect.id)
    setForm(defaultForm(effect))
    setSaveError(null)
    setPreviewInput('groove')
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
        name:                  form.name,
        effect_type:           form.effect_type,
        effect_speed:          parseFloat(form.effect_speed),
        effect_decay:          parseFloat(form.effect_decay),
        sensitivity:           parseFloat(form.sensitivity),
        brightness_floor:      parseFloat(form.brightness_floor),
        bass_hz:               parseInt(form.bass_hz, 10),
        mid_hz:                parseInt(form.mid_hz, 10),
        exertion_clip:         parseFloat(form.exertion_clip),
        onset_flash_intensity: parseFloat(form.onset_flash_intensity),
      }
      if (editingId === 'new') {
        await createEffect(body)
      } else if (editingId) {
        await updateEffect(editingId, body)
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
    await deleteEffect(id)
    await load()
  }

  // ── loading / error ──
  if (loading || error) {
    return (
      <div className="flex items-center justify-center h-full text-sm">
        {loading
          ? <span className="text-muted-foreground">Loading…</span>
          : <span className="text-destructive">{error}</span>}
      </div>
    )
  }

  // =========================================================================
  // WORKSPACE VIEW
  // =========================================================================

  if (editingId !== null) {
    return (
      <div className="flex flex-col h-full bg-background">

        <EditorPageHeader
          aria-label="Effect name"
          name={form.name}
          placeholder="Effect name…"
          onNameChange={(v) => set('name', v)}
          isLive={isLive}
          error={saveError}
          saving={saving}
          saveDisabled={!form.name}
          onCancel={closeEditor}
          onSave={handleSave}
          additionalControls={
            <ModeToggle expertMode={expertMode} onToggle={setExpertMode} />
          }
        />

        <div className="flex flex-1 overflow-hidden">

          {/* ── Main: preview workspace ── */}
          <div className="flex-1 overflow-y-auto px-6 py-6">

            {/* Preview card — EffectPreview and Preview Input are one visual unit */}
            <div
              className="rounded-xl bg-black/25 border border-border/40 overflow-hidden"
              data-testid="preview-workspace"
            >
              {/* Large effect preview */}
              <div className="py-10 px-6 flex flex-col items-center gap-4">
                <EffectPreview
                  effectType={form.effect_type}
                  energy={displayEnergy}
                  count={8}
                  size="lg"
                />
                <div className="text-center space-y-1">
                  <div className="text-sm font-medium">{selectedMeta?.label ?? form.effect_type}</div>
                  <div className="text-xs text-muted-foreground/60 max-w-xs leading-relaxed">
                    {selectedMeta?.description}
                  </div>
                </div>
              </div>

              {/* Preview Input — visually integrated with the preview surface */}
              <div
                className="border-t border-border/25 px-5 py-3 flex items-center gap-1.5"
                data-testid="preview-input-selector"
              >
                <span className="text-[10px] uppercase tracking-widest text-muted-foreground/40 mr-1 shrink-0">
                  Preview
                </span>
                {(['calm', 'groove', 'beat-heavy'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setPreviewInput(mode)}
                    data-testid={`preview-${mode}`}
                    className={cn(
                      'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                      previewInput === mode
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted/60 text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {mode === 'beat-heavy' ? 'Beat-heavy' : mode.charAt(0).toUpperCase() + mode.slice(1)}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setPreviewInput('live')}
                  disabled={!preview.connected}
                  data-testid="preview-live"
                  title={preview.connected ? 'Use live audio input' : 'Not connected'}
                  className={cn(
                    'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                    previewInput === 'live' && preview.connected
                      ? 'bg-green-600 text-white'
                      : preview.connected
                      ? 'bg-muted/60 text-muted-foreground hover:text-foreground'
                      : 'bg-muted/60 text-muted-foreground/40 cursor-not-allowed',
                  )}
                >
                  {previewInput === 'live' && preview.connected
                    ? `● ${Math.round(liveEnergy * 100)}%`
                    : 'Live'}
                </button>
              </div>
            </div>

          </div>

          {/* ── Inspector ── */}
          <aside className="w-80 border-l border-border shrink-0 overflow-y-auto">
            <div className="p-5 space-y-5">

              {/* Effect Type */}
              <div>
                <SectionLabel className="mb-3">Effect type</SectionLabel>
                <EffectTypeSelector
                  value={form.effect_type}
                  onChange={(t) => set('effect_type', t)}
                />
              </div>

              <Separator />

              {/* Behaviour — hidden entirely for 'none' type */}
              {isNoneType ? (
                <div data-testid="section-none-message">
                  <SectionLabel className="mb-2">Behaviour</SectionLabel>
                  <p className="text-xs text-muted-foreground/60">
                    No output is sent to this zone. No behaviour settings apply.
                  </p>
                </div>
              ) : (
                <div className="space-y-4" data-testid="section-behaviour">
                  <SectionLabel>Behaviour</SectionLabel>

                  {/* Sensitivity — all non-none types */}
                  <FieldRow
                    label="Sensitivity"
                    value={sensitivity.toFixed(1)}
                    isAtDefault={sensDef}
                    onReset={() => set('sensitivity', String(EFFECT_DEFAULTS.sensitivity))}
                    resetTestId="reset-sensitivity"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Subtle</span>
                      <Slider
                        aria-label="Sensitivity"
                        min={0} max={1} step={0.01}
                        value={[sens2s(sensitivity)]}
                        onValueChange={([v]) => set('sensitivity', String(s2sens(v)))}
                        className="flex-1"
                      />
                      <span className="text-[10px] text-muted-foreground/60 w-14 text-right shrink-0">Reactive</span>
                    </div>
                    {expertMode && (
                      <Input
                        data-testid="field-sensitivity-exact"
                        type="number" step={0.1} min={0.1} max={3.0}
                        value={form.sensitivity}
                        onChange={(e) => set('sensitivity', e.target.value)}
                        className="h-7 text-xs mt-1"
                      />
                    )}
                  </FieldRow>

                  {/* Speed — fireworks, swirl, wave only */}
                  {selectedMeta?.hasSpeed && (
                    <FieldRow
                      label="Speed"
                      value={speed.toFixed(1)}
                      isAtDefault={speedDef}
                      onReset={() => set('effect_speed', String(EFFECT_DEFAULTS.effect_speed))}
                      resetTestId="reset-speed"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Slow</span>
                        <Slider
                          aria-label="Speed"
                          min={0} max={1} step={0.01}
                          value={[spd2s(speed)]}
                          onValueChange={([v]) => set('effect_speed', String(s2spd(v)))}
                          className="flex-1"
                        />
                        <span className="text-[10px] text-muted-foreground/60 w-14 text-right shrink-0">Fast</span>
                      </div>
                      {expertMode && (
                        <Input
                          data-testid="field-speed-exact"
                          type="number" step={0.1} min={0.1} max={5.0}
                          value={form.effect_speed}
                          onChange={(e) => set('effect_speed', e.target.value)}
                          className="h-7 text-xs mt-1"
                        />
                      )}
                    </FieldRow>
                  )}

                  {/* Decay — pulses, flashes, splotches, fireworks only */}
                  {selectedMeta?.hasDecay && (
                    <FieldRow
                      label="Decay"
                      value={decay.toFixed(2)}
                      isAtDefault={decayDef}
                      onReset={() => set('effect_decay', String(EFFECT_DEFAULTS.effect_decay))}
                      resetTestId="reset-decay"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Long</span>
                        <Slider
                          aria-label="Decay"
                          min={0} max={1} step={0.01}
                          value={[dec2s(decay)]}
                          onValueChange={([v]) => set('effect_decay', String(s2dec(v)))}
                          className="flex-1"
                        />
                        <span className="text-[10px] text-muted-foreground/60 w-14 text-right shrink-0">Short</span>
                      </div>
                      {expertMode && (
                        <Input
                          data-testid="field-decay-exact"
                          type="number" step={0.01} min={0.01} max={0.99}
                          value={form.effect_decay}
                          onChange={(e) => set('effect_decay', e.target.value)}
                          className="h-7 text-xs mt-1"
                        />
                      )}
                    </FieldRow>
                  )}

                  {/* Brightness floor — all non-none types */}
                  <FieldRow
                    label="Brightness floor"
                    value={floor.toFixed(2)}
                    isAtDefault={floorDef}
                    onReset={() => set('brightness_floor', String(EFFECT_DEFAULTS.brightness_floor))}
                    resetTestId="reset-brightness-floor"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Dark</span>
                      <Slider
                        aria-label="Brightness floor"
                        min={0} max={1} step={0.01}
                        value={[floor]}
                        onValueChange={([v]) => set('brightness_floor', String(Math.round(v * 100) / 100))}
                        className="flex-1"
                      />
                      <span className="text-[10px] text-muted-foreground/60 w-14 text-right shrink-0">Bright</span>
                    </div>
                    {expertMode && (
                      <Input
                        data-testid="field-brightness-floor-exact"
                        type="number" step={0.01} min={0} max={1}
                        value={form.brightness_floor}
                        onChange={(e) => set('brightness_floor', e.target.value)}
                        className="h-7 text-xs mt-1"
                      />
                    )}
                  </FieldRow>

                  {/* Beat flash — all non-none types */}
                  <FieldRow
                    label="Beat flash"
                    value={flash.toFixed(2)}
                    isAtDefault={flashDef}
                    onReset={() => set('onset_flash_intensity', String(EFFECT_DEFAULTS.onset_flash_intensity))}
                    resetTestId="reset-beat-flash"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Off</span>
                      <Slider
                        aria-label="Beat flash intensity"
                        min={0} max={1} step={0.05}
                        value={[flash]}
                        onValueChange={([v]) => set('onset_flash_intensity', String(Math.round(v * 100) / 100))}
                        className="flex-1"
                      />
                      <span className="text-[10px] text-muted-foreground/60 w-14 text-right shrink-0">Full</span>
                    </div>
                    {expertMode && (
                      <Input
                        data-testid="field-beat-flash-exact"
                        type="number" step={0.05} min={0} max={1}
                        value={form.onset_flash_intensity}
                        onChange={(e) => set('onset_flash_intensity', e.target.value)}
                        className="h-7 text-xs mt-1"
                      />
                    )}
                  </FieldRow>
                </div>
              )}

              {/* ── Expert-only sections ── */}
              {expertMode && !isNoneType && (
                <>
                  <Separator />

                  {/* Frequency bands — bass/mid boundaries (grouped reset) */}
                  <ExpertSection
                    title="Frequency bands"
                    isAtDefault={freqDef}
                    onReset={() => {
                      set('bass_hz', String(EFFECT_DEFAULTS.bass_hz))
                      set('mid_hz',  String(EFFECT_DEFAULTS.mid_hz))
                    }}
                    resetTestId="reset-freq-bands"
                  >
                    <div className="space-y-2">
                      <div className="space-y-1">
                        <label className="text-xs text-muted-foreground">Bass / mid (Hz)</label>
                        <Input
                          data-testid="field-bass-hz"
                          type="number" min={50} max={2000}
                          value={form.bass_hz}
                          onChange={(e) => set('bass_hz', e.target.value)}
                          className="h-7 text-xs"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs text-muted-foreground">Mid / treble (Hz)</label>
                        <Input
                          data-testid="field-mid-hz"
                          type="number" min={200} max={10000}
                          value={form.mid_hz}
                          onChange={(e) => set('mid_hz', e.target.value)}
                          className="h-7 text-xs"
                        />
                      </div>
                    </div>
                  </ExpertSection>

                  <Separator />

                  {/* Exertion clip */}
                  <FieldRow
                    label="Exertion clip"
                    value={exertionClip.toFixed(1)}
                    isAtDefault={exertDef}
                    onReset={() => set('exertion_clip', String(EFFECT_DEFAULTS.exertion_clip))}
                    resetTestId="reset-exertion-clip"
                  >
                    <Input
                      data-testid="field-exertion-clip"
                      type="number" step={0.1} min={1} max={10}
                      value={form.exertion_clip}
                      onChange={(e) => set('exertion_clip', e.target.value)}
                      className="h-7 text-xs"
                    />
                  </FieldRow>
                </>
              )}

            </div>
          </aside>

        </div>
      </div>
    )
  }

  // =========================================================================
  // GALLERY VIEW
  // =========================================================================

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-sm font-semibold">Effects</h2>
          <Button size="sm" onClick={openNew} data-testid="new-effect-btn">New effect</Button>
        </div>
        <p className="text-xs text-muted-foreground mb-6">Create and tune reusable light behaviours</p>

        {effects.length === 0 ? (
          <p className="text-sm text-muted-foreground">No effects yet. Create one to get started.</p>
        ) : (
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
            data-testid="effects-gallery"
          >
            {effects.map((e) => (
              <GalleryCard
                key={e.id}
                effect={e}
                onEdit={() => openEdit(e)}
                onDelete={() => handleDelete(e.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
