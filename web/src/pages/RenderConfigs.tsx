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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import {
  COLOUR_MODES,
  type RenderConfig,
  getRenderConfigs,
  createRenderConfig,
  updateRenderConfig,
  deleteRenderConfig,
} from '@/lib/api'

interface FormState {
  name: string
  color_mode: string
  sensitivity: string
  brightness_floor: string
  bass_hz: string
  mid_hz: string
  exertion_clip: string
  onset_flash_intensity: string
}

function defaultForm(cfg?: RenderConfig): FormState {
  return {
    name: cfg?.name ?? '',
    color_mode: cfg?.color_mode ?? 'spectrum_rgb',
    sensitivity: String(cfg?.sensitivity ?? 1.0),
    brightness_floor: String(cfg?.brightness_floor ?? 0.15),
    bass_hz: String(cfg?.bass_hz ?? 250),
    mid_hz: String(cfg?.mid_hz ?? 2000),
    exertion_clip: String(cfg?.exertion_clip ?? 3.0),
    onset_flash_intensity: String(cfg?.onset_flash_intensity ?? 0.0),
  }
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      {children}
    </div>
  )
}

export function RenderConfigs() {
  const [configs, setConfigs] = useState<RenderConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingConfig, setEditingConfig] = useState<RenderConfig | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getRenderConfigs()
      setConfigs(data)
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
    setEditingConfig(undefined)
    setForm(defaultForm())
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(cfg: RenderConfig) {
    setEditingConfig(cfg)
    setForm(defaultForm(cfg))
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
        color_mode: form.color_mode,
        sensitivity: parseFloat(form.sensitivity),
        brightness_floor: parseFloat(form.brightness_floor),
        bass_hz: parseInt(form.bass_hz, 10),
        mid_hz: parseInt(form.mid_hz, 10),
        exertion_clip: parseFloat(form.exertion_clip),
        onset_flash_intensity: parseFloat(form.onset_flash_intensity),
      }
      if (editingConfig) {
        await updateRenderConfig(editingConfig.id, body)
      } else {
        await createRenderConfig(body)
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
    await deleteRenderConfig(id)
    await load()
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  if (error) {
    return <p className="text-destructive text-sm">{error}</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Render configs</h2>
        <Button size="sm" onClick={openNew}>
          New config
        </Button>
      </div>

      {configs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No render configs yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Color Mode</TableHead>
              <TableHead>Sensitivity</TableHead>
              <TableHead>Brightness Floor</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {configs.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="text-sm font-mono">{c.color_mode}</TableCell>
                <TableCell className="text-sm">{c.sensitivity.toFixed(1)}</TableCell>
                <TableCell className="text-sm">{c.brightness_floor.toFixed(2)}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(c)}>
                      Edit
                    </Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Delete render config"
                      description={`Delete "${c.name}"? This cannot be undone.`}
                      onConfirm={() => handleDelete(c.id)}
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
            <DialogTitle>{editingConfig ? 'Edit render config' : 'New render config'}</DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My render config"
              />
            </FormRow>
            <FormRow label="Color mode">
              <Select value={form.color_mode} onValueChange={(v) => set('color_mode', v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COLOUR_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="Sensitivity">
              <Input
                type="number"
                step={0.1}
                min={0.1}
                max={10}
                value={form.sensitivity}
                onChange={(e) => set('sensitivity', e.target.value)}
              />
            </FormRow>
            <FormRow label="Brightness floor">
              <Input
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={form.brightness_floor}
                onChange={(e) => set('brightness_floor', e.target.value)}
              />
            </FormRow>
            <FormRow label="Bass / mid boundary (Hz)">
              <Input
                type="number"
                min={50}
                max={2000}
                value={form.bass_hz}
                onChange={(e) => set('bass_hz', e.target.value)}
              />
            </FormRow>
            <FormRow label="Mid / treble boundary (Hz)">
              <Input
                type="number"
                min={200}
                max={10000}
                value={form.mid_hz}
                onChange={(e) => set('mid_hz', e.target.value)}
              />
            </FormRow>
            <FormRow label="Exertion clip">
              <Input
                type="number"
                step={0.1}
                min={1}
                max={10}
                value={form.exertion_clip}
                onChange={(e) => set('exertion_clip', e.target.value)}
              />
            </FormRow>
            <FormRow label="Onset flash intensity (0 = off, 1 = full white)">
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
