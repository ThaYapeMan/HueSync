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
  DialogDescription,
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
  type Zone,
  type Controller,
  type EntertainmentArea,
  getZones,
  createZone,
  updateZone,
  deleteZone,
  getControllers,
  getControllerAreas,
} from '@/lib/api'

interface FormState {
  name: string
  controller_id: string
  entertainment_area_id: string
  entertainment_area_name: string
  light_count: string
}

function defaultForm(z?: Zone): FormState {
  return {
    name: z?.name ?? '',
    controller_id: z?.controller_id ?? '',
    entertainment_area_id: z?.entertainment_area_id ?? '',
    entertainment_area_name: z?.entertainment_area_name ?? '',
    light_count: String(z?.light_count ?? 0),
  }
}

export function Zones() {
  const [zones, setZones] = useState<Zone[]>([])
  const [controllers, setControllers] = useState<Controller[]>([])
  const [areas, setAreas] = useState<EntertainmentArea[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingZone, setEditingZone] = useState<Zone | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [loadingAreas, setLoadingAreas] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const [zs, cs] = await Promise.all([getZones(), getControllers()])
      setZones(zs)
      setControllers(cs)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!form.controller_id) {
      setAreas([])
      return
    }
    if (editingZone && form.controller_id === editingZone.controller_id) {
      // Keep areas empty when editing — area is fixed on creation
      return
    }
    setLoadingAreas(true)
    getControllerAreas(form.controller_id)
      .then(setAreas)
      .catch(() => setAreas([]))
      .finally(() => setLoadingAreas(false))
  }, [form.controller_id])

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function handleAreaChange(areaId: string) {
    const area = areas.find((a) => a.id === areaId)
    setForm((f) => ({
      ...f,
      entertainment_area_id: areaId,
      entertainment_area_name: area?.name ?? '',
      light_count: String(area?.light_count ?? 0),
      name: f.name || (area?.name ?? ''),
    }))
  }

  function openNew() {
    setEditingZone(undefined)
    setForm(defaultForm())
    setAreas([])
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(z: Zone) {
    setEditingZone(z)
    setForm(defaultForm(z))
    setAreas([])
    setSaveError(null)
    setEditorOpen(true)
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      if (editingZone) {
        await updateZone(editingZone.id, {
          name: form.name,
          entertainment_area_name: form.entertainment_area_name,
          light_count: parseInt(form.light_count, 10),
        })
      } else {
        await createZone({
          name: form.name,
          controller_id: form.controller_id,
          entertainment_area_id: form.entertainment_area_id,
          entertainment_area_name: form.entertainment_area_name,
          light_count: parseInt(form.light_count, 10),
        })
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
    await deleteZone(id)
    await load()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (error) return <p className="text-destructive text-sm">{error}</p>

  const controllerName = (id: string) => controllers.find((c) => c.id === id)?.name ?? id

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Zones</h2>
        <Button size="sm" onClick={openNew}>New zone</Button>
      </div>

      {zones.length === 0 ? (
        <p className="text-sm text-muted-foreground">No zones yet. Create one to link a Hue Entertainment Area.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Controller</TableHead>
              <TableHead>Entertainment Area</TableHead>
              <TableHead>Lights</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {zones.map((z) => (
              <TableRow key={z.id}>
                <TableCell className="font-medium">{z.name}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{controllerName(z.controller_id)}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{z.entertainment_area_name || z.entertainment_area_id}</TableCell>
                <TableCell className="text-sm">{z.light_count}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(z)}>Edit</Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Delete zone"
                      description={`Delete "${z.name}"? This cannot be undone.`}
                      onConfirm={() => handleDelete(z.id)}
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
            <DialogTitle>{editingZone ? 'Edit zone' : 'New zone'}</DialogTitle>
            {!editingZone && (
              <DialogDescription>Link a Hue Entertainment Area as a zone.</DialogDescription>
            )}
          </DialogHeader>

          <div className="space-y-3">
            {!editingZone && (
              <>
                <div className="space-y-1">
                  <Label className="text-sm">Controller</Label>
                  <Select value={form.controller_id} onValueChange={(v) => set('controller_id', v)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select controller" />
                    </SelectTrigger>
                    <SelectContent>
                      {controllers.map((c) => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1">
                  <Label className="text-sm">Entertainment Area</Label>
                  <Select
                    value={form.entertainment_area_id}
                    onValueChange={handleAreaChange}
                    disabled={!form.controller_id || loadingAreas}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={loadingAreas ? 'Loading…' : 'Select area'} />
                    </SelectTrigger>
                    <SelectContent>
                      {areas.map((a) => (
                        <SelectItem key={a.id} value={a.id}>{a.name} ({a.light_count} lights)</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

            <div className="space-y-1">
              <Label className="text-sm">Name</Label>
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="Living Room"
              />
            </div>

            {editingZone && (
              <>
                <div className="space-y-1">
                  <Label className="text-sm">Entertainment Area name</Label>
                  <Input
                    value={form.entertainment_area_name}
                    onChange={(e) => set('entertainment_area_name', e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-sm">Light count</Label>
                  <Input
                    type="number"
                    min={0}
                    value={form.light_count}
                    onChange={(e) => set('light_count', e.target.value)}
                  />
                </div>
              </>
            )}
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || (!editingZone && !form.entertainment_area_id)}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
