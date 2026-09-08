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
  type Crossfader,
  type Scene,
  getCrossfaders,
  createCrossfader,
  updateCrossfader,
  deleteCrossfader,
  getScenes,
} from '@/lib/api'

interface FormState {
  name: string
  active_scene_id: string
  mellow_scene_id: string
  low_threshold: string
  high_threshold: string
  fade_speed: string
}

function defaultForm(cf?: Crossfader): FormState {
  return {
    name: cf?.name ?? '',
    active_scene_id: cf?.active_scene_id ?? '',
    mellow_scene_id: cf?.mellow_scene_id ?? '',
    low_threshold: String(cf?.low_threshold ?? 0.3),
    high_threshold: String(cf?.high_threshold ?? 0.7),
    fade_speed: String(cf?.fade_speed ?? 0.1),
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

export function Crossfaders() {
  const [crossfaders, setCrossfaders] = useState<Crossfader[]>([])
  const [scenes, setScenes] = useState<Scene[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingCf, setEditingCf] = useState<Crossfader | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const [cfs, scs] = await Promise.all([getCrossfaders(), getScenes()])
      setCrossfaders(cfs)
      setScenes(scs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function openNew() {
    setEditingCf(undefined)
    setForm(defaultForm())
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(cf: Crossfader) {
    setEditingCf(cf)
    setForm(defaultForm(cf))
    setSaveError(null)
    setEditorOpen(true)
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        active_scene_id: form.active_scene_id,
        mellow_scene_id: form.mellow_scene_id,
        low_threshold: parseFloat(form.low_threshold),
        high_threshold: parseFloat(form.high_threshold),
        fade_speed: parseFloat(form.fade_speed),
      }
      if (editingCf) {
        await updateCrossfader(editingCf.id, body)
      } else {
        await createCrossfader(body)
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
    await deleteCrossfader(id)
    await load()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (error) return <p className="text-destructive text-sm">{error}</p>

  const sceneName = (id: string) => scenes.find((s) => s.id === id)?.name ?? (id ? id.slice(0, 8) + '…' : '—')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Crossfaders</h2>
        <Button size="sm" onClick={openNew}>New crossfader</Button>
      </div>

      {crossfaders.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No crossfaders yet. Crossfaders are created automatically when a coupling is made,
          or create one here to share across couplings.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Active scene</TableHead>
              <TableHead>Mellow scene</TableHead>
              <TableHead>Thresholds</TableHead>
              <TableHead>Fade speed</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {crossfaders.map((cf) => (
              <TableRow key={cf.id}>
                <TableCell className="font-medium">{cf.name}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{sceneName(cf.active_scene_id)}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {cf.mellow_scene_id ? sceneName(cf.mellow_scene_id) : '(same as active)'}
                </TableCell>
                <TableCell className="text-sm font-mono">{cf.low_threshold}–{cf.high_threshold}</TableCell>
                <TableCell className="text-sm font-mono">{cf.fade_speed}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(cf)}>Edit</Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Delete crossfader"
                      description={`Delete "${cf.name}"? Any couplings referencing it will lose their scene settings.`}
                      onConfirm={() => handleDelete(cf.id)}
                    />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={editorOpen} onOpenChange={(o) => { if (!o) setEditorOpen(false) }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{editingCf ? 'Edit crossfader' : 'New crossfader'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-3">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My crossfader"
              />
            </FormRow>

            <FormRow label="Active scene (loud passages)">
              <Select value={form.active_scene_id} onValueChange={(v) => set('active_scene_id', v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select scene" />
                </SelectTrigger>
                <SelectContent>
                  {scenes.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>

            <FormRow label="Mellow scene (quiet passages, optional)">
              <Select value={form.mellow_scene_id} onValueChange={(v) => set('mellow_scene_id', v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Same as active" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Same as active</SelectItem>
                  {scenes.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>

            <div className="border-t pt-3 space-y-3">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Crossfade thresholds</p>
                <p className="text-xs text-muted-foreground mt-1">
                  e.g. 0.3 / 0.7 → soft passages use Mellow, loud refrains use Active, with a gradual blend in between.
                </p>
              </div>
              <FormRow label="Low threshold — energy below this → pure mellow">
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={form.low_threshold}
                  onChange={(e) => set('low_threshold', e.target.value)}
                />
              </FormRow>
              <FormRow label="High threshold — energy above this → pure active">
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={form.high_threshold}
                  onChange={(e) => set('high_threshold', e.target.value)}
                />
              </FormRow>
              <FormRow label="Fade speed — EMA smoothing (0.01 slow · 0.5 fast)">
                <Input
                  type="number"
                  step={0.01}
                  min={0.01}
                  max={0.5}
                  value={form.fade_speed}
                  onChange={(e) => set('fade_speed', e.target.value)}
                />
              </FormRow>
            </div>
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.active_scene_id}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
