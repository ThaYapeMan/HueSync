import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { cn } from '@/lib/utils'
import {
  EFFECTS,
  type Effect,
  getEffects,
  createEffect,
  updateEffect,
  deleteEffect,
} from '@/lib/api'

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
    name: cfg?.name ?? '',
    effect_type: cfg?.effect_type ?? 'spectrum_rgb',
    effect_speed: String(cfg?.effect_speed ?? 1.0),
    effect_decay: String(cfg?.effect_decay ?? 0.3),
    sensitivity: String(cfg?.sensitivity ?? 1.0),
    brightness_floor: String(cfg?.brightness_floor ?? 0.15),
    bass_hz: String(cfg?.bass_hz ?? 250),
    mid_hz: String(cfg?.mid_hz ?? 2000),
    exertion_clip: String(cfg?.exertion_clip ?? 3.0),
    onset_flash_intensity: String(cfg?.onset_flash_intensity ?? 0.0),
  }
}

function FormRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </div>
  )
}

export function Effects() {
  const [effects, setEffects] = useState<Effect[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingEffect, setEditingEffect] = useState<Effect | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getEffects()
      setEffects(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function openNew() {
    setEditingEffect(undefined)
    setForm(defaultForm())
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(effect: Effect) {
    setEditingEffect(effect)
    setForm(defaultForm(effect))
    setSaveError(null)
    setEditorOpen(true)
  }

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        effect_type: form.effect_type,
        effect_speed: parseFloat(form.effect_speed),
        effect_decay: parseFloat(form.effect_decay),
        sensitivity: parseFloat(form.sensitivity),
        brightness_floor: parseFloat(form.brightness_floor),
        bass_hz: parseInt(form.bass_hz, 10),
        mid_hz: parseInt(form.mid_hz, 10),
        exertion_clip: parseFloat(form.exertion_clip),
        onset_flash_intensity: parseFloat(form.onset_flash_intensity),
      }
      if (editingEffect) {
        await updateEffect(editingEffect.id, body)
      } else {
        await createEffect(body)
      }
      setEditorOpen(false)
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

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  if (error) {
    return <p className="text-destructive text-sm">{error}</p>
  }

  const selectedEffect = EFFECTS.find((e) => e.id === form.effect_type)

  const speedLabel: Record<string, string> = {
    fireworks: 'Burst expansion speed (0.1 = slow, 5 = fast)',
    swirl:     'Rotation speed (0.1 = slow, 5 = fast)',
    wave:      'Wave sweep speed (0.1 = slow, 5 = fast)',
  }
  const decayLabel: Record<string, string> = {
    fireworks: 'Burst fade speed (0.1 = lingers, 0.9 = snappy)',
    pulses:    'Pulse fade speed (0.1 = slow fade, 0.9 = snappy)',
    flashes:   'Flash fade speed (0.1 = lingers, 0.9 = snappy)',
    splotches: 'Flare fade speed (0.1 = slow fade, 0.9 = snappy)',
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Effects</h2>
        <Button size="sm" onClick={openNew}>
          New effect
        </Button>
      </div>

      {effects.length === 0 ? (
        <p className="text-sm text-muted-foreground">No effects yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Effect Type</TableHead>
              <TableHead>Sensitivity</TableHead>
              <TableHead>Brightness Floor</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {effects.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="font-medium">{e.name}</TableCell>
                <TableCell className="text-sm font-mono">{e.effect_type}</TableCell>
                <TableCell className="text-sm">{e.sensitivity.toFixed(1)}</TableCell>
                <TableCell className="text-sm">{e.brightness_floor.toFixed(2)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(e)}>
                      Edit
                    </Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Delete effect"
                      description={`Delete "${e.name}"? This cannot be undone.`}
                      onConfirm={() => handleDelete(e.id)}
                    />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={editorOpen} onOpenChange={(o) => { if (!o) setEditorOpen(false) }}>
        <DialogContent className="max-w-md flex flex-col max-h-[90vh]">
          <DialogHeader>
            <DialogTitle>{editingEffect ? 'Edit effect' : 'New effect'}</DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My effect"
              />
            </FormRow>
            <FormRow label="Effect Type">
              <div className="grid grid-cols-2 gap-1.5">
                {EFFECTS.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => set('effect_type', e.id)}
                    className={cn(
                      'text-left rounded border p-2 text-sm transition-colors',
                      form.effect_type === e.id
                        ? 'border-primary bg-primary/10'
                        : 'border-border hover:border-muted-foreground/60'
                    )}
                  >
                    <div className="font-medium leading-tight">{e.label}</div>
                    <div className="text-xs text-muted-foreground leading-tight mt-0.5">{e.description}</div>
                  </button>
                ))}
              </div>
            </FormRow>
            {selectedEffect?.hasSpeed && (
              <FormRow label={speedLabel[form.effect_type] ?? 'Effect speed (0.1 = slow, 5 = fast)'}>
                <Input
                  type="number"
                  step={0.1}
                  min={0.1}
                  max={5}
                  value={form.effect_speed}
                  onChange={(e) => set('effect_speed', e.target.value)}
                />
              </FormRow>
            )}
            {selectedEffect?.hasDecay && (
              <FormRow label={decayLabel[form.effect_type] ?? 'Effect decay (0.1 = slow, 0.9 = fast)'}>
                <Input
                  type="number"
                  step={0.05}
                  min={0.01}
                  max={0.99}
                  value={form.effect_decay}
                  onChange={(e) => set('effect_decay', e.target.value)}
                />
              </FormRow>
            )}
            <FormRow
              label="Sensitivity"
              hint="Brightness multiplier for music energy. At 1.0, steady music sits at ~⅓ brightness with brief peaks at full. Above 3 the lights saturate."
            >
              <Input
                type="number"
                step={0.1}
                min={0.1}
                max={10}
                value={form.sensitivity}
                onChange={(e) => set('sensitivity', e.target.value)}
              />
            </FormRow>
            <FormRow
              label="Brightness floor"
              hint="Minimum brightness during silences or very quiet passages. 0 = lights can go fully dark."
            >
              <Input
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={form.brightness_floor}
                onChange={(e) => set('brightness_floor', e.target.value)}
              />
            </FormRow>
            <FormRow
              label="Bass / mid boundary (Hz)"
              hint="Frequencies below this count as bass (red in Spectrum RGB, low-end energy for other effects)."
            >
              <Input
                type="number"
                min={50}
                max={2000}
                value={form.bass_hz}
                onChange={(e) => set('bass_hz', e.target.value)}
              />
            </FormRow>
            <FormRow
              label="Mid / treble boundary (Hz)"
              hint="Frequencies above this count as treble (blue in Spectrum RGB). 2000 Hz is a neutral starting point."
            >
              <Input
                type="number"
                min={200}
                max={10000}
                value={form.mid_hz}
                onChange={(e) => set('mid_hz', e.target.value)}
              />
            </FormRow>
            <FormRow
              label="Exertion clip"
              hint="Sets when lights hit full brightness. At 3.0 (default), average energy maps to ~⅓ brightness; loud peaks reach full. Lower = more dramatic, higher = more subtle."
            >
              <Input
                type="number"
                step={0.1}
                min={1}
                max={10}
                value={form.exertion_clip}
                onChange={(e) => set('exertion_clip', e.target.value)}
              />
            </FormRow>
            <FormRow
              label="Beat flash intensity"
              hint="Extra brightness burst on each detected beat, on top of the normal colour. 0 = off, 1 = full white flash."
            >
              <Input
                type="number"
                step={0.05}
                min={0}
                max={1}
                value={form.onset_flash_intensity}
                onChange={(e) => set('onset_flash_intensity', e.target.value)}
              />
            </FormRow>
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
