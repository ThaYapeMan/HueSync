import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EditorPageHeader } from '@/components/editor/EditorPageHeader'
import { SectionLabel } from '@/components/editor/SectionLabel'
import { AdvancedSection } from '@/components/editor/AdvancedSection'
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

// ── slider helpers (all internal) ────────────────────────────────────────────
const sens2s  = (v: number) => Math.max(0, Math.min(1, (v - 0.1) / 2.9))
const s2sens  = (v: number) => Math.round((0.1 + v * 2.9) * 10) / 10
const spd2s   = (v: number) => Math.max(0, Math.min(1, (v - 0.1) / 4.9))
const s2spd   = (v: number) => Math.round((0.1 + v * 4.9) * 10) / 10
const dec2s   = (v: number) => Math.max(0, Math.min(1, (v - 0.01) / 0.98))
const s2dec   = (v: number) => Math.round((0.01 + v * 0.98) * 100) / 100
const flr2s   = (v: number) => v
const s2flr   = (v: number) => Math.round(v * 100) / 100
const fsh2s   = (v: number) => v
const s2fsh   = (v: number) => Math.round(v * 100) / 100

// ── form ─────────────────────────────────────────────────────────────────────

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
    name:                   cfg?.name                   ?? '',
    effect_type:            cfg?.effect_type            ?? 'spectrum_rgb',
    effect_speed:           String(cfg?.effect_speed    ?? 1.0),
    effect_decay:           String(cfg?.effect_decay    ?? 0.3),
    sensitivity:            String(cfg?.sensitivity     ?? 1.0),
    brightness_floor:       String(cfg?.brightness_floor ?? 0.15),
    bass_hz:                String(cfg?.bass_hz          ?? 250),
    mid_hz:                 String(cfg?.mid_hz           ?? 2000),
    exertion_clip:          String(cfg?.exertion_clip   ?? 3.0),
    onset_flash_intensity:  String(cfg?.onset_flash_intensity ?? 0.0),
  }
}

// ── gallery card ─────────────────────────────────────────────────────────────

type PreviewInput = 'calm' | 'groove' | 'beat-heavy' | 'live'

const PREVIEW_ENERGY: Record<PreviewInput, number> = {
  calm:       0.2,
  groove:     0.55,
  'beat-heavy': 0.82,
  live:       0,  // overridden at render time
}

function effectSummary(effect: Effect): string {
  const parts: string[] = []
  if (effect.sensitivity < 0.7)       parts.push('Subtle')
  else if (effect.sensitivity > 1.5)  parts.push('Reactive')
  else                                 parts.push('Balanced')
  const meta = EFFECTS.find((e) => e.id === effect.effect_type)
  if (meta?.hasDecay) {
    if (effect.effect_decay > 0.5)    parts.push('Punchy')
    else if (effect.effect_decay < 0.2) parts.push('Slow fade')
    else                              parts.push('Smooth')
  }
  if (meta?.hasSpeed) {
    if (effect.effect_speed > 2.5)    parts.push('Fast')
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
      {/* Visual preview area */}
      <div className="bg-black/20 py-5 px-4 flex flex-col items-center justify-center gap-3 min-h-[96px]">
        <EffectPreview effectType={effect.effect_type} energy={0.80} count={6} size="md" />
        <div className={cn('h-1 w-14 rounded-full opacity-60', swatchClass)} />
      </div>

      {/* Info */}
      <div className="p-4 flex flex-col gap-2 flex-1">
        <div>
          <div className="font-semibold text-sm leading-tight">{effect.name}</div>
          <div className="text-xs text-muted-foreground mt-0.5">{meta?.label ?? effect.effect_type}</div>
        </div>
        <div className="text-xs text-muted-foreground/70 leading-snug flex-1">
          {effectSummary(effect)}
        </div>

        {/* Actions */}
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

// ── main component ────────────────────────────────────────────────────────────

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

  const preview = usePreviewSocket()

  // ── derived ──
  const selectedMeta = EFFECTS.find((e) => e.id === form.effect_type)
  const sensitivity   = parseFloat(form.sensitivity)        || 1
  const speed         = parseFloat(form.effect_speed)       || 1
  const decay         = parseFloat(form.effect_decay)       || 0.3
  const floor         = parseFloat(form.brightness_floor)   || 0
  const flash         = parseFloat(form.onset_flash_intensity) || 0

  // Preview energy: either from socket (Live) or from preset
  const liveEnergy  = preview.energy
  const baseEnergy  = previewInput === 'live' ? liveEnergy : PREVIEW_ENERGY[previewInput]
  // Apply sensitivity so slider movements visually affect brightness
  const displayEnergy = Math.min(1, Math.max(0, baseEnergy * Math.min(sensitivity, 3) / 1.5))

  // Live badge: the effect is in use by the active coupling's energy profile
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
  }

  function openEdit(effect: Effect) {
    setEditingId(effect.id)
    setForm(defaultForm(effect))
    setSaveError(null)
    setPreviewInput('groove')
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
        />

        <div className="flex flex-1 overflow-hidden">

          {/* ── Main: preview + input selector ── */}
          <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8">

            {/* Large effect preview */}
            <section>
              <SectionLabel className="mb-4">Effect preview</SectionLabel>
              <div className="rounded-xl bg-black/25 border border-border/40 py-8 px-4 flex flex-col items-center gap-4">
                <EffectPreview
                  effectType={form.effect_type}
                  energy={displayEnergy}
                  count={8}
                  size="lg"
                />
                <div className="text-center space-y-0.5">
                  <div className="text-sm font-medium">{selectedMeta?.label ?? form.effect_type}</div>
                  <div className="text-xs text-muted-foreground/70">{selectedMeta?.description}</div>
                </div>
              </div>
            </section>

            {/* Preview input selector */}
            <section>
              <SectionLabel className="mb-3">Preview input</SectionLabel>
              <div className="flex items-center gap-2" data-testid="preview-input-selector">
                {(['calm', 'groove', 'beat-heavy'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setPreviewInput(mode)}
                    data-testid={`preview-${mode}`}
                    className={cn(
                      'px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize',
                      previewInput === mode
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground hover:text-foreground',
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
                      ? 'bg-muted text-muted-foreground hover:text-foreground'
                      : 'bg-muted text-muted-foreground/40 cursor-not-allowed',
                  )}
                >
                  Live
                </button>
              </div>
              {previewInput === 'live' && preview.connected && (
                <p className="text-xs text-green-400 mt-2">
                  ● Using live audio — {Math.round(liveEnergy * 100)}% energy
                </p>
              )}
            </section>

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

              {/* Behaviour sliders */}
              <div className="space-y-4">
                <SectionLabel>Behaviour</SectionLabel>

                {/* Sensitivity */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Sensitivity</span>
                    <span className="text-xs font-mono tabular-nums text-muted-foreground">{sensitivity.toFixed(1)}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Subtle</span>
                    <Slider
                      aria-label="Sensitivity"
                      min={0} max={1} step={0.01}
                      value={[sens2s(sensitivity)]}
                      onValueChange={([v]) => set('sensitivity', String(s2sens(v)))}
                      className="flex-1"
                    />
                    <span className="text-[10px] text-muted-foreground/60 w-12 text-right shrink-0">Reactive</span>
                  </div>
                </div>

                {/* Speed (conditional) */}
                {selectedMeta?.hasSpeed && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">Speed</span>
                      <span className="text-xs font-mono tabular-nums text-muted-foreground">{speed.toFixed(1)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Slow</span>
                      <Slider
                        aria-label="Speed"
                        min={0} max={1} step={0.01}
                        value={[spd2s(speed)]}
                        onValueChange={([v]) => set('effect_speed', String(s2spd(v)))}
                        className="flex-1"
                      />
                      <span className="text-[10px] text-muted-foreground/60 w-12 text-right shrink-0">Fast</span>
                    </div>
                  </div>
                )}

                {/* Decay (conditional) */}
                {selectedMeta?.hasDecay && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">Decay</span>
                      <span className="text-xs font-mono tabular-nums text-muted-foreground">{decay.toFixed(2)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Long</span>
                      <Slider
                        aria-label="Decay"
                        min={0} max={1} step={0.01}
                        value={[dec2s(decay)]}
                        onValueChange={([v]) => set('effect_decay', String(s2dec(v)))}
                        className="flex-1"
                      />
                      <span className="text-[10px] text-muted-foreground/60 w-12 text-right shrink-0">Short</span>
                    </div>
                  </div>
                )}

                {/* Brightness floor */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Brightness floor</span>
                    <span className="text-xs font-mono tabular-nums text-muted-foreground">{floor.toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Dark</span>
                    <Slider
                      aria-label="Brightness floor"
                      min={0} max={1} step={0.01}
                      value={[flr2s(floor)]}
                      onValueChange={([v]) => set('brightness_floor', String(s2flr(v)))}
                      className="flex-1"
                    />
                    <span className="text-[10px] text-muted-foreground/60 w-12 text-right shrink-0">Bright</span>
                  </div>
                </div>

                {/* Beat flash */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Beat flash</span>
                    <span className="text-xs font-mono tabular-nums text-muted-foreground">{flash.toFixed(2)}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground/60 w-10 shrink-0">Off</span>
                    <Slider
                      aria-label="Beat flash intensity"
                      min={0} max={1} step={0.05}
                      value={[fsh2s(flash)]}
                      onValueChange={([v]) => set('onset_flash_intensity', String(s2fsh(v)))}
                      className="flex-1"
                    />
                    <span className="text-[10px] text-muted-foreground/60 w-12 text-right shrink-0">Full</span>
                  </div>
                </div>
              </div>

              <Separator />

              <AdvancedSection>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">Bass / mid boundary (Hz)</Label>
                  <Input type="number" min={50} max={2000}
                    value={form.bass_hz} onChange={(e) => set('bass_hz', e.target.value)}
                    className="h-7 text-xs" />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">Mid / treble boundary (Hz)</Label>
                  <Input type="number" min={200} max={10000}
                    value={form.mid_hz} onChange={(e) => set('mid_hz', e.target.value)}
                    className="h-7 text-xs" />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">Exertion clip</Label>
                  <Input type="number" step={0.1} min={1} max={10}
                    value={form.exertion_clip} onChange={(e) => set('exertion_clip', e.target.value)}
                    className="h-7 text-xs" />
                </div>
                {selectedMeta?.hasSpeed && (
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Speed (exact)</Label>
                    <Input type="number" step={0.1} min={0.1} max={5}
                      value={form.effect_speed} onChange={(e) => set('effect_speed', e.target.value)}
                      className="h-7 text-xs" />
                  </div>
                )}
                {selectedMeta?.hasDecay && (
                  <div className="space-y-1">
                    <Label className="text-xs text-muted-foreground">Decay (exact)</Label>
                    <Input type="number" step={0.01} min={0.01} max={0.99}
                      value={form.effect_decay} onChange={(e) => set('effect_decay', e.target.value)}
                      className="h-7 text-xs" />
                  </div>
                )}
              </AdvancedSection>

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
