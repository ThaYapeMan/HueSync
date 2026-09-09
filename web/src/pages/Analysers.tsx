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
  ONSET_METHODS,
  BARS_SOURCE_OPTIONS,
  type Analyser,
  getAnalysers,
  createAnalyser,
  updateAnalyser,
  deleteAnalyser,
} from '@/lib/api'

interface FormState {
  name: string
  bars_source: string
  onset_method: string
  bars: string
  lower_cutoff_freq: string
  higher_cutoff_freq: string
  onset_delta: string
  onset_alpha: string
  superflux_mu: string
  superflux_lag: string
  use_hpss_separation: boolean
}

function defaultForm(cfg?: Analyser): FormState {
  return {
    name: cfg?.name ?? '',
    bars_source: cfg?.bars_source ?? 'cava',
    onset_method: cfg?.onset_method ?? 'combined',
    bars: String(cfg?.bars ?? 30),
    lower_cutoff_freq: String(cfg?.lower_cutoff_freq ?? 50),
    higher_cutoff_freq: String(cfg?.higher_cutoff_freq ?? 12000),
    onset_delta: String(cfg?.onset_delta ?? 0.1),
    onset_alpha: String(cfg?.onset_alpha ?? 0.9),
    superflux_mu: String(cfg?.superflux_mu ?? 3),
    superflux_lag: String(cfg?.superflux_lag ?? 2),
    use_hpss_separation: cfg?.use_hpss_separation ?? false,
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

export function Analysers() {
  const [configs, setConfigs] = useState<Analyser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingConfig, setEditingConfig] = useState<Analyser | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getAnalysers()
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

  function openEdit(cfg: Analyser) {
    setEditingConfig(cfg)
    setForm(defaultForm(cfg))
    setSaveError(null)
    setEditorOpen(true)
  }

  function set(key: keyof Omit<FormState, 'use_hpss_separation'>, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        bars_source: form.bars_source,
        onset_method: form.onset_method,
        bars: parseInt(form.bars, 10),
        lower_cutoff_freq: parseInt(form.lower_cutoff_freq, 10),
        higher_cutoff_freq: parseInt(form.higher_cutoff_freq, 10),
        onset_delta: parseFloat(form.onset_delta),
        onset_alpha: parseFloat(form.onset_alpha),
        superflux_mu: parseInt(form.superflux_mu, 10),
        superflux_lag: parseInt(form.superflux_lag, 10),
        use_hpss_separation: form.use_hpss_separation,
      }
      if (editingConfig) {
        await updateAnalyser(editingConfig.id, body)
      } else {
        await createAnalyser(body)
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
    await deleteAnalyser(id)
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
        <h2 className="text-sm font-semibold">Analysers</h2>
        <Button size="sm" onClick={openNew}>
          New analyser
        </Button>
      </div>

      {configs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No analysers yet. Create one to get started.</p>
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
                      title="Delete analyser"
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
            <DialogTitle>{editingConfig ? 'Edit analyser' : 'New analyser'}</DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My analyser"
              />
            </FormRow>

            <FormRow label="Bars source">
              <div className="grid grid-cols-1 gap-1.5">
                {BARS_SOURCE_OPTIONS.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => set('bars_source', m.value)}
                    className={cn(
                      'text-left rounded border p-2 text-sm transition-colors',
                      form.bars_source === m.value
                        ? 'border-primary bg-primary/10'
                        : 'border-border hover:border-muted-foreground/60'
                    )}
                  >
                    <div className="font-medium leading-tight">{m.label}</div>
                    <div className="text-xs text-muted-foreground leading-tight mt-0.5">{m.description}</div>
                  </button>
                ))}
              </div>
            </FormRow>

            <FormRow label="Onset method">
              <div className="grid grid-cols-1 gap-1.5">
                {ONSET_METHODS.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => set('onset_method', m.value)}
                    className={cn(
                      'text-left rounded border p-2 text-sm transition-colors',
                      form.onset_method === m.value
                        ? 'border-primary bg-primary/10'
                        : 'border-border hover:border-muted-foreground/60'
                    )}
                  >
                    <div className="font-medium leading-tight">{m.label}</div>
                    <div className="text-xs text-muted-foreground leading-tight mt-0.5">{m.description}</div>
                  </button>
                ))}
              </div>
            </FormRow>

            <div className="flex items-start gap-2.5 rounded border p-2.5">
              <input
                id="hpss-sep"
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer"
                checked={form.use_hpss_separation}
                onChange={(e) => setForm((f) => ({ ...f, use_hpss_separation: e.target.checked }))}
              />
              <div>
                <Label htmlFor="hpss-sep" className="text-sm font-medium cursor-pointer leading-tight">
                  Harmonic / percussive separation
                </Label>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Splits the music into a percussive layer (drums, rhythm) and a harmonic
                  layer (vocals, chords) for more targeted effects. High-energy effects react to
                  rhythm; low-energy effects react to melody. Costs ~0.4 ms per frame — well
                  within the 10 ms budget.
                </p>
              </div>
            </div>

            <FormRow
              label="Bars"
              hint="Number of frequency bands. 20–30 works well for most rooms."
            >
              <Input
                type="number"
                min={10}
                max={60}
                value={form.bars}
                onChange={(e) => set('bars', e.target.value)}
              />
            </FormRow>

            <FormRow
              label="Low cut (Hz)"
              hint="Lowest frequency analysed. 50 Hz filters out sub-bass rumble."
            >
              <Input
                type="number"
                min={20}
                max={500}
                value={form.lower_cutoff_freq}
                onChange={(e) => set('lower_cutoff_freq', e.target.value)}
              />
            </FormRow>

            <FormRow
              label="High cut (Hz)"
              hint="Highest frequency analysed. 12 000 Hz covers most music; lower values exclude high treble."
            >
              <Input
                type="number"
                min={1000}
                max={20000}
                value={form.higher_cutoff_freq}
                onChange={(e) => set('higher_cutoff_freq', e.target.value)}
              />
            </FormRow>

            <FormRow
              label="Onset delta"
              hint="Beat sensitivity — lower catches softer beats, higher requires stronger hits to trigger."
            >
              <Input
                type="number"
                step={0.01}
                min={0.01}
                max={1}
                value={form.onset_delta}
                onChange={(e) => set('onset_delta', e.target.value)}
              />
            </FormRow>

            <FormRow
              label="Onset alpha"
              hint="How quickly the beat threshold adapts to changes in volume. 0.9 is a good default for dynamic music."
            >
              <Input
                type="number"
                step={0.01}
                min={0}
                max={1}
                value={form.onset_alpha}
                onChange={(e) => set('onset_alpha', e.target.value)}
              />
            </FormRow>

            {form.onset_method === 'superflux' && (
              <>
                <FormRow
                  label="SuperFlux mu"
                  hint="Vibrato suppression strength — higher values prevent false beats on sustained notes and vocal runs."
                >
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={form.superflux_mu}
                    onChange={(e) => set('superflux_mu', e.target.value)}
                  />
                </FormRow>

                <FormRow
                  label="SuperFlux lag"
                  hint="Look-back window for vibrato detection. Higher = more context but slightly slower reaction."
                >
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={form.superflux_lag}
                    onChange={(e) => set('superflux_lag', e.target.value)}
                  />
                </FormRow>
              </>
            )}
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
