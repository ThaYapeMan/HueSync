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
  type AnalysisConfig,
  getAnalysisConfigs,
  createAnalysisConfig,
  updateAnalysisConfig,
  deleteAnalysisConfig,
} from '@/lib/api'

interface FormState {
  name: string
  onset_method: string
  bars: string
  lower_cutoff_freq: string
  higher_cutoff_freq: string
  onset_delta: string
  onset_alpha: string
  superflux_mu: string
  superflux_lag: string
}

function defaultForm(cfg?: AnalysisConfig): FormState {
  return {
    name: cfg?.name ?? '',
    onset_method: cfg?.onset_method ?? 'combined',
    bars: String(cfg?.bars ?? 30),
    lower_cutoff_freq: String(cfg?.lower_cutoff_freq ?? 50),
    higher_cutoff_freq: String(cfg?.higher_cutoff_freq ?? 12000),
    onset_delta: String(cfg?.onset_delta ?? 0.1),
    onset_alpha: String(cfg?.onset_alpha ?? 0.9),
    superflux_mu: String(cfg?.superflux_mu ?? 3),
    superflux_lag: String(cfg?.superflux_lag ?? 2),
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

export function AnalysisConfigs() {
  const [configs, setConfigs] = useState<AnalysisConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingConfig, setEditingConfig] = useState<AnalysisConfig | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getAnalysisConfigs()
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

  function openEdit(cfg: AnalysisConfig) {
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
        onset_method: form.onset_method,
        bars: parseInt(form.bars, 10),
        lower_cutoff_freq: parseInt(form.lower_cutoff_freq, 10),
        higher_cutoff_freq: parseInt(form.higher_cutoff_freq, 10),
        onset_delta: parseFloat(form.onset_delta),
        onset_alpha: parseFloat(form.onset_alpha),
        superflux_mu: parseInt(form.superflux_mu, 10),
        superflux_lag: parseInt(form.superflux_lag, 10),
      }
      if (editingConfig) {
        await updateAnalysisConfig(editingConfig.id, body)
      } else {
        await createAnalysisConfig(body)
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
    await deleteAnalysisConfig(id)
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
        <h2 className="text-sm font-semibold">Analysis configs</h2>
        <Button size="sm" onClick={openNew}>
          New config
        </Button>
      </div>

      {configs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No analysis configs yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Method</TableHead>
              <TableHead>Bars</TableHead>
              <TableHead>Freq Range</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {configs.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="text-sm font-mono">{c.onset_method}</TableCell>
                <TableCell className="text-sm">{c.bars}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {c.lower_cutoff_freq}–{c.higher_cutoff_freq} Hz
                </TableCell>
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
                      title="Delete analysis config"
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
            <DialogTitle>{editingConfig ? 'Edit analysis config' : 'New analysis config'}</DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My analysis config"
              />
            </FormRow>
            <FormRow label="Onset method">
              <Select value={form.onset_method} onValueChange={(v) => set('onset_method', v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="combined">Combined</SelectItem>
                  <SelectItem value="superflux">SuperFlux</SelectItem>
                  <SelectItem value="hfc">HFC</SelectItem>
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="Bars">
              <Input
                type="number"
                min={10}
                max={60}
                value={form.bars}
                onChange={(e) => set('bars', e.target.value)}
              />
            </FormRow>
            <FormRow label="Low cut (Hz)">
              <Input
                type="number"
                min={20}
                max={500}
                value={form.lower_cutoff_freq}
                onChange={(e) => set('lower_cutoff_freq', e.target.value)}
              />
            </FormRow>
            <FormRow label="High cut (Hz)">
              <Input
                type="number"
                min={1000}
                max={20000}
                value={form.higher_cutoff_freq}
                onChange={(e) => set('higher_cutoff_freq', e.target.value)}
              />
            </FormRow>
            <FormRow label="Onset delta">
              <Input
                type="number"
                step={0.01}
                min={0.01}
                max={1}
                value={form.onset_delta}
                onChange={(e) => set('onset_delta', e.target.value)}
              />
            </FormRow>
            <FormRow label="Onset alpha">
              <Input
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={form.onset_alpha}
                onChange={(e) => set('onset_alpha', e.target.value)}
              />
            </FormRow>
            <FormRow label="SuperFlux mu">
              <Input
                type="number"
                min={1}
                max={20}
                value={form.superflux_mu}
                onChange={(e) => set('superflux_mu', e.target.value)}
              />
            </FormRow>
            <FormRow label="SuperFlux lag">
              <Input
                type="number"
                min={1}
                max={10}
                value={form.superflux_lag}
                onChange={(e) => set('superflux_lag', e.target.value)}
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
