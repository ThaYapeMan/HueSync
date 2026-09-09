import { useEffect, useState } from 'react'
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
import { LightPreview } from '@/components/LightPreview'
import { EditorPageHeader } from '@/components/editor/EditorPageHeader'
import { SectionLabel } from '@/components/editor/SectionLabel'
import { AdvancedSection } from '@/components/editor/AdvancedSection'
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
    low_energy_effect_id: ep?.low_energy_effect_id ?? '',
    blend_start: String(ep?.blend_start ?? 0.3),
    blend_end: String(ep?.blend_end ?? 0.7),
    blend_response: String(ep?.blend_response ?? 0.1),
  }
}

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

  const preview = usePreviewSocket()

  // --- Derived values for workspace ---
  const blendStart = parseFloat(form.blend_start) || 0
  const blendEnd = parseFloat(form.blend_end) || 1
  const blendResponse = parseFloat(form.blend_response) || 0.1
  const startPct = blendStart * 100
  const endPct = blendEnd * 100

  const isLive = editingId !== null && editingId !== 'new'
    && preview.status?.active_energy_profile_id === editingId

  const displayEnergy = isLive ? preview.energy : simulatedEnergy
  const instantMix = calcBlendMix(displayEnergy, blendStart, blendEnd)
  const displayMix = isLive ? preview.mix : instantMix

  const highEffect = effects.find((e) => e.id === form.high_energy_effect_id)
  const lowEffect = effects.find((e) => e.id === form.low_energy_effect_id)
  const lowEffectType = (lowEffect ?? highEffect)?.effect_type ?? 'mono_pulse'
  const highEffectType = highEffect?.effect_type ?? 'mono_pulse'

  const lowPct = Math.round((1 - displayMix) * 100)
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
    set('blend_end', String(Math.round(end * 100) / 100))
  }

  // --- Editor lifecycle ---

  function openNew() {
    setEditingId('new')
    setForm(defaultForm())
    setSaveError(null)
    setSimulatedEnergy(0.5)
  }

  function openEdit(ep: EnergyProfile) {
    setEditingId(ep.id)
    setForm(defaultForm(ep))
    setSaveError(null)
    setSimulatedEnergy(0.5)
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
        low_energy_effect_id: form.low_energy_effect_id,
        blend_start: parseFloat(form.blend_start),
        blend_end: parseFloat(form.blend_end),
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
        />

        {/* Body: main + inspector */}
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
            <section>
              <SectionLabel className="mb-4">Energy blend</SectionLabel>

              {/* Zone color bar */}
              <div className="relative h-2.5 rounded-full overflow-hidden mb-4">
                <div
                  className="absolute inset-y-0 left-0 bg-blue-500/35"
                  style={{ right: `${100 - startPct}%` }}
                />
                <div
                  className="absolute inset-y-0 bg-amber-400/35"
                  style={{ left: `${startPct}%`, right: `${100 - endPct}%` }}
                />
                <div
                  className="absolute inset-y-0 right-0 bg-orange-500/35"
                  style={{ left: `${endPct}%` }}
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
              <div className="relative h-5 mt-1 text-xs font-mono text-muted-foreground/70 select-none">
                <span className="absolute left-0">LOW 100%</span>
                <span
                  className="absolute -translate-x-1/2 text-amber-400/75"
                  style={{ left: `${(startPct + endPct) / 2}%` }}
                >
                  AUTO BLEND
                </span>
                <span className="absolute right-0">HIGH 100%</span>
              </div>

              {/* Percent ticks */}
              <div className="relative h-4 mt-0.5 text-[10px] font-mono text-muted-foreground/40 select-none">
                <span className="absolute left-0">0%</span>
                {startPct > 8 && startPct < 92 && (
                  <span
                    className="absolute -translate-x-1/2 text-blue-400/60"
                    style={{ left: `${startPct}%` }}
                  >
                    {Math.round(startPct)}%
                  </span>
                )}
                {endPct > 8 && endPct < 92 && Math.abs(endPct - startPct) > 6 && (
                  <span
                    className="absolute -translate-x-1/2 text-orange-400/60"
                    style={{ left: `${endPct}%` }}
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
                  <div
                    className={cn('w-px h-4', isLive ? 'bg-green-400' : 'bg-muted-foreground/35')}
                  />
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

              {/* Simulate slider (hidden when live) */}
              {!isLive && (
                <div className="mt-3 space-y-1.5">
                  <p className="text-xs text-muted-foreground/60">Drag to simulate music energy</p>
                  <Slider
                    aria-label="Simulated energy"
                    min={0}
                    max={1}
                    step={0.01}
                    value={[simulatedEnergy]}
                    onValueChange={(v) => setSimulatedEnergy(v[0])}
                  />
                </div>
              )}
            </section>

            {/* ── Blend weights ── */}
            <section>
              <SectionLabel className="mb-3">Current blend</SectionLabel>
              <div className="flex items-center gap-3">
                <span className="text-xs text-blue-300 w-32 text-right truncate shrink-0">
                  {(lowEffect ?? highEffect)?.name ?? 'Low energy'}&nbsp;{lowPct}%
                </span>
                <div className="flex-1 h-2.5 rounded-full overflow-hidden bg-secondary">
                  <div
                    className="h-full rounded-full transition-all duration-100"
                    style={{
                      background: `linear-gradient(to right,
                        rgb(96, 165, 250) 0%,
                        rgb(96, 165, 250) ${lowPct}%,
                        rgb(251, 146, 60) ${lowPct}%,
                        rgb(251, 146, 60) 100%)`,
                    }}
                  />
                </div>
                <span className="text-xs text-orange-300 w-32 truncate shrink-0">
                  {highPct}%&nbsp;{highEffect?.name ?? 'High energy'}
                </span>
              </div>
            </section>

            {/* ── Output preview ── */}
            <section>
              <SectionLabel className="mb-2">Output preview</SectionLabel>
              <div className="rounded-xl bg-black/25 border border-border/40">
                <LightPreview
                  lowEffectType={lowEffectType}
                  highEffectType={highEffectType}
                  mix={displayMix}
                  energy={displayEnergy}
                />
              </div>
            </section>

          </div>

          {/* Inspector */}
          <aside className="w-80 border-l border-border shrink-0 overflow-y-auto">
            <div className="p-5 space-y-5">

              {/* Response */}
              <div>
                <SectionLabel className="mb-3">Response</SectionLabel>
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
                </div>
              </div>

              <Separator />

              {/* Advanced */}
              <AdvancedSection>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">Blend start</Label>
                  <Input
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
                    type="number"
                    step={0.05}
                    min={0}
                    max={1}
                    value={form.blend_end}
                    onChange={(e) => set('blend_end', e.target.value)}
                    className="h-7 text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">Response (exact)</Label>
                  <Input
                    type="number"
                    step={0.01}
                    min={0.01}
                    max={0.5}
                    value={form.blend_response}
                    onChange={(e) => set('blend_response', e.target.value)}
                    className="h-7 text-xs"
                  />
                </div>
              </AdvancedSection>

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
