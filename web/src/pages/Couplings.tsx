import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
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
  COLOUR_MODES,
  type Coupling,
  type VirtualPlayer,
  type LightProvider,
  type AnalysisConfig,
  type RenderConfig,
  type Controller,
  type EntertainmentArea,
  getCouplings,
  createCoupling,
  updateCoupling,
  deleteCoupling,
  activateCoupling,
  cloneCoupling,
  deactivateCoupling,
  getVirtualPlayers,
  createVirtualPlayer,
  getLightProviders,
  createLightProvider,
  getAnalysisConfigs,
  createAnalysisConfig,
  getRenderConfigs,
  createRenderConfig,
  getControllers,
  getControllerAreas,
  getStatus,
} from '@/lib/api'

interface Props {
  activeCouplingId: string | null
  onActivationChange: () => void
}

// ---- Inline mini-dialog: New Player ----

interface NewPlayerDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (player: VirtualPlayer) => void
}

function NewPlayerDialog({ open, onClose, onCreated }: NewPlayerDialogProps) {
  const [name, setName] = useState('')
  const [lmsHost, setLmsHost] = useState('')
  const [playerName, setPlayerName] = useState('HueSync')
  const [alsaDevice, setAlsaDevice] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setLmsHost('')
      setPlayerName('HueSync')
      setAlsaDevice('')
      setError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const player = await createVirtualPlayer({
        name,
        lms_host: lmsHost,
        lms_port: 9000,
        player_name: playerName,
        alsa_device: alsaDevice,
      })
      onCreated(player)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New player</DialogTitle>
          <DialogDescription>Create a new squeezelite player.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-sm">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My player" />
          </div>
          <div className="space-y-1">
            <Label className="text-sm">LMS host</Label>
            <Input value={lmsHost} onChange={(e) => setLmsHost(e.target.value)} placeholder="192.168.x.x" />
          </div>
          <div className="space-y-1">
            <Label className="text-sm">Player name</Label>
            <Input value={playerName} onChange={(e) => setPlayerName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label className="text-sm">ALSA device</Label>
            <Input value={alsaDevice} onChange={(e) => setAlsaDevice(e.target.value)} placeholder="hw:CARD=Dummy,DEV=0" />
          </div>
        </div>
        {error && <p className="text-destructive text-sm mt-2">{error}</p>}
        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Create'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Inline mini-dialog: New LightProvider ----

interface NewLightProviderDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (lp: LightProvider) => void
}

function NewLightProviderDialog({ open, onClose, onCreated }: NewLightProviderDialogProps) {
  const [controllers, setControllers] = useState<Controller[]>([])
  const [selectedControllerId, setSelectedControllerId] = useState('')
  const [areas, setAreas] = useState<EntertainmentArea[]>([])
  const [selectedAreaId, setSelectedAreaId] = useState('')
  const [name, setName] = useState('')
  const [loadingControllers, setLoadingControllers] = useState(false)
  const [loadingAreas, setLoadingAreas] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSelectedControllerId('')
      setAreas([])
      setSelectedAreaId('')
      setName('')
      setError(null)
      setLoadingControllers(true)
      getControllers()
        .then(setControllers)
        .catch(() => setControllers([]))
        .finally(() => setLoadingControllers(false))
    }
  }, [open])

  useEffect(() => {
    if (!selectedControllerId) {
      setAreas([])
      setSelectedAreaId('')
      setName('')
      return
    }
    setLoadingAreas(true)
    getControllerAreas(selectedControllerId)
      .then(setAreas)
      .catch(() => setAreas([]))
      .finally(() => setLoadingAreas(false))
  }, [selectedControllerId])

  function handleAreaChange(areaId: string) {
    setSelectedAreaId(areaId)
    const area = areas.find((a) => a.id === areaId)
    if (area) setName(area.name)
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const area = areas.find((a) => a.id === selectedAreaId)
      const lp = await createLightProvider({
        name,
        controller_id: selectedControllerId,
        entertainment_area_id: selectedAreaId,
        entertainment_area_name: area?.name ?? '',
        light_count: area?.light_count ?? 0,
      })
      onCreated(lp)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New light provider</DialogTitle>
          <DialogDescription>Link a Hue entertainment area as a light provider.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-sm">Controller</Label>
            {loadingControllers ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Select value={selectedControllerId} onValueChange={setSelectedControllerId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select controller" />
                </SelectTrigger>
                <SelectContent>
                  {controllers.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          {selectedControllerId && (
            <div className="space-y-1">
              <Label className="text-sm">Entertainment area</Label>
              {loadingAreas ? (
                <p className="text-sm text-muted-foreground">Loading areas…</p>
              ) : (
                <Select value={selectedAreaId} onValueChange={handleAreaChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select area" />
                  </SelectTrigger>
                  <SelectContent>
                    {areas.map((a) => (
                      <SelectItem key={a.id} value={a.id}>{a.name} ({a.light_count} lights)</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
          {selectedAreaId && (
            <div className="space-y-1">
              <Label className="text-sm">Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          )}
        </div>
        {error && <p className="text-destructive text-sm mt-2">{error}</p>}
        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || !selectedAreaId}>{saving ? 'Saving…' : 'Create'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Inline mini-dialog: New AnalysisConfig ----

interface NewAnalysisConfigDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (cfg: AnalysisConfig) => void
}

function NewAnalysisConfigDialog({ open, onClose, onCreated }: NewAnalysisConfigDialogProps) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const cfg = await createAnalysisConfig({
        name,
        onset_method: 'combined',
        bars: 30,
        lower_cutoff_freq: 50,
        higher_cutoff_freq: 12000,
        onset_delta: 0.1,
        onset_alpha: 0.9,
        superflux_mu: 3,
        superflux_lag: 2,
      })
      onCreated(cfg)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New analysis config</DialogTitle>
          <DialogDescription>Uses sensible defaults — edit in Analysis tab to adjust.</DialogDescription>
        </DialogHeader>
        <div className="space-y-1">
          <Label className="text-sm">Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My analysis config" />
        </div>
        {error && <p className="text-destructive text-sm mt-2">{error}</p>}
        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Create'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- Inline mini-dialog: New RenderConfig ----

interface NewRenderConfigDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (cfg: RenderConfig) => void
}

function NewRenderConfigDialog({ open, onClose, onCreated }: NewRenderConfigDialogProps) {
  const [name, setName] = useState('')
  const [colorMode, setColorMode] = useState('spectrum_rgb')
  const [mellowMode, setMellowMode] = useState('spectrum_rgb')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setColorMode('spectrum_rgb')
      setMellowMode('spectrum_rgb')
      setError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const cfg = await createRenderConfig({
        name,
        color_mode: colorMode,
        mellow_colour_mode: mellowMode,
        mix_low_threshold: 0.3,
        mix_high_threshold: 0.7,
        mix_ema_alpha: 0.1,
        sensitivity: 1.0,
        brightness_floor: 0.15,
        bass_hz: 250,
        mid_hz: 2000,
        exertion_clip: 3.0,
        onset_flash_intensity: 0.0,
      })
      onCreated(cfg)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New render config</DialogTitle>
          <DialogDescription>Uses sensible defaults — edit in Rendering tab to adjust.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-sm">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My render config" />
          </div>
          <div className="space-y-1">
            <Label className="text-sm">Active layer (loud passages)</Label>
            <Select value={colorMode} onValueChange={setColorMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COLOUR_MODES.map((m) => (
                  <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-sm">Mellow layer (quiet passages)</Label>
            <Select value={mellowMode} onValueChange={setMellowMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COLOUR_MODES.map((m) => (
                  <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {error && <p className="text-destructive text-sm mt-2">{error}</p>}
        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Create'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ---- CouplingEditor dialog ----

interface CouplingEditorProps {
  open: boolean
  coupling?: Coupling
  players: VirtualPlayer[]
  lightProviders: LightProvider[]
  analysisConfigs: AnalysisConfig[]
  renderConfigs: RenderConfig[]
  onSave: () => void
  onClose: () => void
  onPlayersChanged: (players: VirtualPlayer[]) => void
  onLightProvidersChanged: (lps: LightProvider[]) => void
  onAnalysisConfigsChanged: (cfgs: AnalysisConfig[]) => void
  onRenderConfigsChanged: (cfgs: RenderConfig[]) => void
}

function CouplingEditor({
  open,
  coupling,
  players,
  lightProviders,
  analysisConfigs,
  renderConfigs,
  onSave,
  onClose,
  onPlayersChanged,
  onLightProvidersChanged,
  onAnalysisConfigsChanged,
  onRenderConfigsChanged,
}: CouplingEditorProps) {
  const isEditing = !!coupling

  const [name, setName] = useState('')
  const [playerId, setPlayerId] = useState('')
  const [lightProviderId, setLightProviderId] = useState('')
  const [analysisConfigId, setAnalysisConfigId] = useState('')
  const [renderConfigId, setRenderConfigId] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Nested dialog states
  const [newPlayerOpen, setNewPlayerOpen] = useState(false)
  const [newLightProviderOpen, setNewLightProviderOpen] = useState(false)
  const [newAnalysisConfigOpen, setNewAnalysisConfigOpen] = useState(false)
  const [newRenderConfigOpen, setNewRenderConfigOpen] = useState(false)

  useEffect(() => {
    if (open) {
      setName(coupling?.name ?? '')
      setPlayerId(coupling?.player_id ?? '')
      setLightProviderId(coupling?.light_provider_id ?? '')
      setAnalysisConfigId(coupling?.analysis_config_id ?? '')
      setRenderConfigId(coupling?.render_config_id ?? '')
      setEnabled(coupling?.enabled ?? true)
      setSaveError(null)
    }
  }, [open, coupling])

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name,
        player_id: playerId,
        light_provider_id: lightProviderId,
        analysis_config_id: analysisConfigId,
        render_config_id: renderConfigId,
        enabled,
      }
      if (isEditing) {
        await updateCoupling(coupling.id, body)
      } else {
        await createCoupling(body)
      }
      onSave()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handlePlayerCreated(player: VirtualPlayer) {
    setNewPlayerOpen(false)
    const updated = await getVirtualPlayers().catch(() => players)
    onPlayersChanged(updated)
    setPlayerId(player.id)
  }

  async function handleLightProviderCreated(lp: LightProvider) {
    setNewLightProviderOpen(false)
    const updated = await getLightProviders().catch(() => lightProviders)
    onLightProvidersChanged(updated)
    setLightProviderId(lp.id)
  }

  async function handleAnalysisConfigCreated(cfg: AnalysisConfig) {
    setNewAnalysisConfigOpen(false)
    const updated = await getAnalysisConfigs().catch(() => analysisConfigs)
    onAnalysisConfigsChanged(updated)
    setAnalysisConfigId(cfg.id)
  }

  async function handleRenderConfigCreated(cfg: RenderConfig) {
    setNewRenderConfigOpen(false)
    const updated = await getRenderConfigs().catch(() => renderConfigs)
    onRenderConfigsChanged(updated)
    setRenderConfigId(cfg.id)
  }

  const selectedLP = lightProviders.find((lp) => lp.id === lightProviderId)

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
        <DialogContent className="max-w-md flex flex-col max-h-[90vh]">
          <DialogHeader>
            <DialogTitle>{isEditing ? 'Edit coupling' : 'New coupling'}</DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <div className="space-y-1">
              <Label className="text-sm">Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My coupling" />
            </div>

            {/* Player */}
            <div className="space-y-1">
              <Label className="text-sm">Player</Label>
              <div className="flex gap-2">
                <Select value={playerId} onValueChange={setPlayerId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select player" />
                  </SelectTrigger>
                  <SelectContent>
                    {players.map((p) => (
                      <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewPlayerOpen(true)}>
                  + New
                </Button>
              </div>
            </div>

            {/* Light provider */}
            <div className="space-y-1">
              <Label className="text-sm">Light provider (area)</Label>
              <div className="flex gap-2">
                <Select value={lightProviderId} onValueChange={setLightProviderId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select area" />
                  </SelectTrigger>
                  <SelectContent>
                    {lightProviders.map((lp) => (
                      <SelectItem key={lp.id} value={lp.id}>
                        {lp.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewLightProviderOpen(true)}>
                  + New
                </Button>
              </div>
              {selectedLP && (
                <p className="text-xs text-muted-foreground">{selectedLP.entertainment_area_name} — {selectedLP.light_count} lights</p>
              )}
            </div>

            {/* Analysis config */}
            <div className="space-y-1">
              <Label className="text-sm">Analysis config</Label>
              <div className="flex gap-2">
                <Select value={analysisConfigId} onValueChange={setAnalysisConfigId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select analysis config" />
                  </SelectTrigger>
                  <SelectContent>
                    {analysisConfigs.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewAnalysisConfigOpen(true)}>
                  + New
                </Button>
              </div>
            </div>

            {/* Render config */}
            <div className="space-y-1">
              <Label className="text-sm">Render config</Label>
              <div className="flex gap-2">
                <Select value={renderConfigId} onValueChange={setRenderConfigId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select render config" />
                  </SelectTrigger>
                  <SelectContent>
                    {renderConfigs.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewRenderConfigOpen(true)}>
                  + New
                </Button>
              </div>
            </div>

            {/* Enabled */}
            <div className="flex items-center gap-2">
              <input
                id="coupling-enabled"
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="h-4 w-4"
              />
              <Label htmlFor="coupling-enabled" className="text-sm cursor-pointer">
                Enabled
              </Label>
            </div>
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <NewPlayerDialog
        open={newPlayerOpen}
        onClose={() => setNewPlayerOpen(false)}
        onCreated={handlePlayerCreated}
      />
      <NewLightProviderDialog
        open={newLightProviderOpen}
        onClose={() => setNewLightProviderOpen(false)}
        onCreated={handleLightProviderCreated}
      />
      <NewAnalysisConfigDialog
        open={newAnalysisConfigOpen}
        onClose={() => setNewAnalysisConfigOpen(false)}
        onCreated={handleAnalysisConfigCreated}
      />
      <NewRenderConfigDialog
        open={newRenderConfigOpen}
        onClose={() => setNewRenderConfigOpen(false)}
        onCreated={handleRenderConfigCreated}
      />
    </>
  )
}

// ---- Main Couplings page ----

export function Couplings({ activeCouplingId: activeCouplingIdProp, onActivationChange }: Props) {
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [players, setPlayers] = useState<VirtualPlayer[]>([])
  const [lightProviders, setLightProviders] = useState<LightProvider[]>([])
  const [analysisConfigs, setAnalysisConfigs] = useState<AnalysisConfig[]>([])
  const [renderConfigs, setRenderConfigs] = useState<RenderConfig[]>([])
  const [activeCouplingId, setActiveCouplingId] = useState<string | null>(activeCouplingIdProp)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingCoupling, setEditingCoupling] = useState<Coupling | undefined>(undefined)

  async function loadAll() {
    try {
      const [cs, ps, lps, acs, rcs, st] = await Promise.all([
        getCouplings(),
        getVirtualPlayers(),
        getLightProviders(),
        getAnalysisConfigs(),
        getRenderConfigs(),
        getStatus(),
      ])
      setCouplings(cs)
      setPlayers(ps)
      setLightProviders(lps)
      setAnalysisConfigs(acs)
      setRenderConfigs(rcs)
      setActiveCouplingId(st.active_coupling_id ?? activeCouplingIdProp)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleActivate(id: string) {
    setActionError(null)
    try {
      await activateCoupling(id)
      onActivationChange()
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Activation failed')
    }
  }

  async function handleDeactivate() {
    setActionError(null)
    try {
      await deactivateCoupling()
      onActivationChange()
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Deactivation failed')
    }
  }

  async function handleDelete(id: string) {
    await deleteCoupling(id)
    await loadAll()
  }

  async function handleClone(id: string) {
    setActionError(null)
    try {
      await cloneCoupling(id)
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Clone failed')
    }
  }

  function openNew() {
    setEditingCoupling(undefined)
    setEditorOpen(true)
  }

  function openEdit(coupling: Coupling) {
    setEditingCoupling(coupling)
    setEditorOpen(true)
  }

  async function handleSave() {
    setEditorOpen(false)
    await loadAll()
  }

  // Lookup helpers
  function playerName(id: string) {
    return players.find((p) => p.id === id)?.name ?? id
  }
  function areaName(lpId: string) {
    const lp = lightProviders.find((l) => l.id === lpId)
    return lp ? lp.name : lpId
  }
  function modeName(rcId: string) {
    return renderConfigs.find((r) => r.id === rcId)?.color_mode ?? rcId
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
        <h2 className="text-sm font-semibold">Couplings</h2>
        <Button size="sm" onClick={openNew}>
          New coupling
        </Button>
      </div>

      {actionError && <p className="text-destructive text-sm">{actionError}</p>}

      {couplings.length === 0 ? (
        <p className="text-sm text-muted-foreground">No couplings yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Player</TableHead>
              <TableHead>Area</TableHead>
              <TableHead>Color Mode</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {couplings.map((c) => {
              const isActive = c.id === activeCouplingId
              return (
                <TableRow key={c.id}>
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{playerName(c.player_id)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{areaName(c.light_provider_id)}</TableCell>
                  <TableCell className="text-sm font-mono">{modeName(c.render_config_id)}</TableCell>
                  <TableCell>
                    <Badge variant={isActive ? 'default' : 'secondary'}>
                      {isActive ? 'Active' : c.enabled ? 'Inactive' : 'Disabled'}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {!isActive && (
                        <Button size="sm" variant="outline" onClick={() => handleActivate(c.id)}>
                          Activate
                        </Button>
                      )}
                      {isActive && (
                        <Button size="sm" variant="outline" onClick={handleDeactivate}>
                          Stop
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => openEdit(c)}>
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleClone(c.id)}>
                        Clone
                      </Button>
                      <ConfirmDialog
                        trigger={
                          <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                            Delete
                          </Button>
                        }
                        title="Delete coupling"
                        description={`Delete "${c.name}"? This cannot be undone.`}
                        onConfirm={() => handleDelete(c.id)}
                      />
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      <CouplingEditor
        open={editorOpen}
        coupling={editingCoupling}
        players={players}
        lightProviders={lightProviders}
        analysisConfigs={analysisConfigs}
        renderConfigs={renderConfigs}
        onSave={handleSave}
        onClose={() => setEditorOpen(false)}
        onPlayersChanged={setPlayers}
        onLightProvidersChanged={setLightProviders}
        onAnalysisConfigsChanged={setAnalysisConfigs}
        onRenderConfigsChanged={setRenderConfigs}
      />
    </div>
  )
}
