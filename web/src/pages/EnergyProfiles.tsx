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

function FormRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </div>
  )
}

export function EnergyProfiles() {
  const [energyProfiles, setEnergyProfiles] = useState<EnergyProfile[]>([])
  const [effects, setEffects] = useState<Effect[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingEp, setEditingEp] = useState<EnergyProfile | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

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

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function openNew() {
    setEditingEp(undefined)
    setForm(defaultForm())
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(ep: EnergyProfile) {
    setEditingEp(ep)
    setForm(defaultForm(ep))
    setSaveError(null)
    setEditorOpen(true)
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
      if (editingEp) {
        await updateEnergyProfile(editingEp.id, body)
      } else {
        await createEnergyProfile(body)
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
    await deleteEnergyProfile(id)
    await load()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (error) return <p className="text-destructive text-sm">{error}</p>

  const effectName = (id: string) => effects.find((e) => e.id === id)?.name ?? (id ? id.slice(0, 8) + '…' : '—')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Energy Profiles</h2>
        <Button size="sm" onClick={openNew}>New energy profile</Button>
      </div>

      {energyProfiles.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No energy profiles yet. Energy profiles are created automatically when a coupling is made,
          or create one here to share across couplings.
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
                <TableCell className="text-sm text-muted-foreground">{effectName(ep.high_energy_effect_id)}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {ep.low_energy_effect_id ? effectName(ep.low_energy_effect_id) : '(same as high-energy)'}
                </TableCell>
                <TableCell className="text-sm font-mono">{ep.blend_start}–{ep.blend_end}</TableCell>
                <TableCell className="text-sm font-mono">{ep.blend_response}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(ep)}>Edit</Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
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

      <Dialog open={editorOpen} onOpenChange={(o) => { if (!o) setEditorOpen(false) }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{editingEp ? 'Edit energy profile' : 'New energy profile'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-3">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My energy profile"
              />
            </FormRow>

            <FormRow label="High-energy effect (loud passages)">
              <Select value={form.high_energy_effect_id} onValueChange={(v) => set('high_energy_effect_id', v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select effect" />
                </SelectTrigger>
                <SelectContent>
                  {effects.map((e) => (
                    <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>

            <FormRow label="Low-energy effect (quiet passages, optional)">
              <Select value={form.low_energy_effect_id} onValueChange={(v) => set('low_energy_effect_id', v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Same as high-energy" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Same as high-energy</SelectItem>
                  {effects.map((e) => (
                    <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>

            <div className="border-t pt-3 space-y-3">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Blend thresholds</p>
                <p className="text-xs text-muted-foreground mt-1">
                  e.g. 0.3 / 0.7 → quiet passages use the low-energy effect, loud refrains use the high-energy effect, with a gradual blend in between.
                </p>
              </div>
              <FormRow label="Blend start — energy below this → pure low-energy effect">
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={form.blend_start}
                  onChange={(e) => set('blend_start', e.target.value)}
                />
              </FormRow>
              <FormRow label="Blend end — energy above this → pure high-energy effect">
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={form.blend_end}
                  onChange={(e) => set('blend_end', e.target.value)}
                />
              </FormRow>
              <FormRow
                label="Blend response — EMA smoothing (0.01 slow · 0.5 fast)"
                hint="How quickly the blend tracks the music energy level."
              >
                <Input
                  type="number"
                  step={0.01}
                  min={0.01}
                  max={0.5}
                  value={form.blend_response}
                  onChange={(e) => set('blend_response', e.target.value)}
                />
              </FormRow>
            </div>
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !form.high_energy_effect_id}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
