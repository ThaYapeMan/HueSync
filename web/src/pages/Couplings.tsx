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
  EFFECTS,
  type Coupling,
  type VirtualPlayer,
  type Zone,
  type AnalysisConfig,
  type Scene,
  type Crossfader,
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
  getZones,
  createZone,
  getAnalysisConfigs,
  createAnalysisConfig,
  getScenes,
  createScene,
  getCrossfaders,
  createCrossfader,
  updateCrossfader,
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
  const [lmsHost, setLmsHost] = useState('')
  const [playerName, setPlayerName] = useState('HueSync')
  const [alsaDevice, setAlsaDevice] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
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
        type: 'LMS',
        lms_host: lmsHost,
        lms_port: 9000,
        player_name: playerName,
        alsa_device: alsaDevice,
        follow_player_mac: '',
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
          <DialogTitle>New virtual player</DialogTitle>
          <DialogDescription>Create a new virtual player.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
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

// ---- Inline mini-dialog: New Zone ----

interface NewZoneDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (zone: Zone) => void
}

function NewZoneDialog({ open, onClose, onCreated }: NewZoneDialogProps) {
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
      const zone = await createZone({
        name,
        controller_id: selectedControllerId,
        entertainment_area_id: selectedAreaId,
        entertainment_area_name: area?.name ?? '',
        light_count: area?.light_count ?? 0,
      })
      onCreated(zone)
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
          <DialogTitle>New zone</DialogTitle>
          <DialogDescription>Link a Hue entertainment area as a zone.</DialogDescription>
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

// ---- Inline mini-dialog: New Scene ----

interface NewSceneDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (cfg: Scene) => void
}

function NewSceneDialog({ open, onClose, onCreated }: NewSceneDialogProps) {
  const [name, setName] = useState('')
  const [effect, setEffect] = useState('spectrum_rgb')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setEffect('spectrum_rgb')
      setError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const cfg = await createScene({
        name,
        effect,
        effect_speed: 1.0,
        effect_decay: 0.3,
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

  const EFFECTS_BASIC = [
    { id: 'spectrum_rgb', label: 'Spectrum RGB' },
    { id: 'mono_pulse', label: 'Mono Pulse' },
    { id: 'pulses', label: 'Pulses' },
    { id: 'flashes', label: 'Flashes' },
    { id: 'splotches', label: 'Splotches' },
    { id: 'fireworks', label: 'Fireworks' },
    { id: 'swirl', label: 'Swirl' },
    { id: 'wave', label: 'Wave' },
    { id: 'solid', label: 'Solid' },
    { id: 'none', label: 'None' },
  ]

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New scene</DialogTitle>
          <DialogDescription>Uses sensible defaults — edit in Scenes tab to adjust.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-sm">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My scene" />
          </div>
          <div className="space-y-1">
            <Label className="text-sm">Effect</Label>
            <Select value={effect} onValueChange={setEffect}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EFFECTS_BASIC.map((e) => (
                  <SelectItem key={e.id} value={e.id}>{e.label}</SelectItem>
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
  zones: Zone[]
  analysisConfigs: AnalysisConfig[]
  scenes: Scene[]
  crossfaders: Crossfader[]
  activeCouplingId: string | null
  onSave: () => void
  onClose: () => void
  onPlayersChanged: (players: VirtualPlayer[]) => void
  onZonesChanged: (zones: Zone[]) => void
  onAnalysisConfigsChanged: (cfgs: AnalysisConfig[]) => void
  onScenesChanged: (scenes: Scene[]) => void
  onCrossfadersChanged: (crossfaders: Crossfader[]) => void
}

function CouplingEditor({
  open,
  coupling,
  players,
  zones,
  analysisConfigs,
  scenes,
  crossfaders,
  activeCouplingId,
  onSave,
  onClose,
  onPlayersChanged,
  onZonesChanged,
  onAnalysisConfigsChanged,
  onScenesChanged,
  onCrossfadersChanged,
}: CouplingEditorProps) {
  const isEditing = !!coupling
  const isLive = !!(coupling && activeCouplingId && coupling.id === activeCouplingId)

  const [name, setName] = useState('')
  const [playerId, setPlayerId] = useState('')
  const [zoneId, setZoneId] = useState('')
  const [analysisConfigId, setAnalysisConfigId] = useState('')
  const [activeSceneId, setActiveSceneId] = useState('')
  const [mellowSceneId, setMellowSceneId] = useState('')
  const [lowThreshold, setLowThreshold] = useState('0.3')
  const [highThreshold, setHighThreshold] = useState('0.7')
  const [fadeSpeed, setFadeSpeed] = useState('0.1')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Nested dialog states
  const [newPlayerOpen, setNewPlayerOpen] = useState(false)
  const [newZoneOpen, setNewZoneOpen] = useState(false)
  const [newAnalysisConfigOpen, setNewAnalysisConfigOpen] = useState(false)
  const [newSceneOpen, setNewSceneOpen] = useState(false)
  const [newSceneTarget, setNewSceneTarget] = useState<'active' | 'mellow'>('active')

  useEffect(() => {
    if (open) {
      setName(coupling?.name ?? '')
      setPlayerId(coupling?.player_id ?? '')
      setZoneId(coupling?.zone_id ?? '')
      setAnalysisConfigId(coupling?.analysis_config_id ?? '')
      setEnabled(coupling?.enabled ?? true)
      setSaveError(null)

      // Resolve crossfader settings
      if (coupling?.crossfader_id) {
        const cf = crossfaders.find((x) => x.id === coupling.crossfader_id)
        setActiveSceneId(cf?.active_scene_id ?? '')
        setMellowSceneId(cf?.mellow_scene_id ?? '')
        setLowThreshold(String(cf?.low_threshold ?? 0.3))
        setHighThreshold(String(cf?.high_threshold ?? 0.7))
        setFadeSpeed(String(cf?.fade_speed ?? 0.1))
      } else {
        setActiveSceneId('')
        setMellowSceneId('')
        setLowThreshold('0.3')
        setHighThreshold('0.7')
        setFadeSpeed('0.1')
      }
    }
  }, [open, coupling, crossfaders])

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      if (isEditing && coupling) {
        // Update crossfader with changed scene/threshold settings
        if (coupling.crossfader_id) {
          await updateCrossfader(coupling.crossfader_id, {
            active_scene_id: activeSceneId,
            mellow_scene_id: mellowSceneId,
            low_threshold: parseFloat(lowThreshold),
            high_threshold: parseFloat(highThreshold),
            fade_speed: parseFloat(fadeSpeed),
          })
        }
        // Update coupling (name, enabled, player_id, zone_id, analysis_config_id)
        await updateCoupling(coupling.id, {
          name,
          enabled,
          player_id: playerId,
          zone_id: zoneId,
          analysis_config_id: analysisConfigId,
        })
      } else {
        // Create crossfader first
        const cf = await createCrossfader({
          name: `${name} Crossfader`,
          active_scene_id: activeSceneId,
          mellow_scene_id: mellowSceneId,
          low_threshold: parseFloat(lowThreshold),
          high_threshold: parseFloat(highThreshold),
          fade_speed: parseFloat(fadeSpeed),
        })
        // Create coupling
        await createCoupling({
          name,
          player_id: playerId,
          zone_id: zoneId,
          analysis_config_id: analysisConfigId,
          crossfader_id: cf.id,
          enabled,
        })
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

  async function handleZoneCreated(zone: Zone) {
    setNewZoneOpen(false)
    const updated = await getZones().catch(() => zones)
    onZonesChanged(updated)
    setZoneId(zone.id)
  }

  async function handleAnalysisConfigCreated(cfg: AnalysisConfig) {
    setNewAnalysisConfigOpen(false)
    const updated = await getAnalysisConfigs().catch(() => analysisConfigs)
    onAnalysisConfigsChanged(updated)
    setAnalysisConfigId(cfg.id)
  }

  function openNewScene(target: 'active' | 'mellow') {
    setNewSceneTarget(target)
    setNewSceneOpen(true)
  }

  async function handleSceneCreated(cfg: Scene) {
    setNewSceneOpen(false)
    const updated = await getScenes().catch(() => scenes)
    onScenesChanged(updated)
    if (newSceneTarget === 'mellow') {
      setMellowSceneId(cfg.id)
    } else {
      setActiveSceneId(cfg.id)
    }
    // Refresh crossfaders list too
    const updatedCf = await getCrossfaders().catch(() => crossfaders)
    onCrossfadersChanged(updatedCf)
  }

  const selectedZone = zones.find((z) => z.id === zoneId)

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
        <DialogContent className="max-w-xl flex flex-col max-h-[90vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {isEditing ? 'Edit coupling' : 'New coupling'}
              {isLive && (
                <Badge variant="destructive" className="text-xs font-normal">● Live</Badge>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 pr-1 space-y-4">
            <div className="space-y-1">
              <Label className="text-sm">Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My coupling" />
            </div>

            {/* Player */}
            <div className="space-y-1">
              <Label className="text-sm">Virtual Player</Label>
              <div className="flex gap-2">
                <Select value={playerId} onValueChange={setPlayerId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select virtual player" />
                  </SelectTrigger>
                  <SelectContent>
                    {players.map((p) => (
                      <SelectItem key={p.id} value={p.id}>{p.player_name} ({p.lms_host})</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewPlayerOpen(true)}>
                  + New
                </Button>
              </div>
            </div>

            {/* Zone */}
            <div className="space-y-1">
              <Label className="text-sm">Zone</Label>
              <div className="flex gap-2">
                <Select value={zoneId} onValueChange={setZoneId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select zone" />
                  </SelectTrigger>
                  <SelectContent>
                    {zones.map((z) => (
                      <SelectItem key={z.id} value={z.id}>
                        {z.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewZoneOpen(true)}>
                  + New
                </Button>
              </div>
              {selectedZone && (
                <p className="text-xs text-muted-foreground">{selectedZone.entertainment_area_name} — {selectedZone.light_count} lights</p>
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

            {/* Two-column Active/Mellow scene selectors */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <div>
                  <div className="text-sm font-semibold">Active scene</div>
                  <div className="text-xs text-muted-foreground">Loud passages</div>
                </div>
                <div className="flex gap-1.5">
                  <Select value={activeSceneId} onValueChange={setActiveSceneId}>
                    <SelectTrigger className="flex-1 min-w-0">
                      <SelectValue placeholder="Select…" />
                    </SelectTrigger>
                    <SelectContent>
                      {scenes.map((c) => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="outline" type="button" onClick={() => openNewScene('active')}>
                    + New
                  </Button>
                </div>
              </div>
              <div className="space-y-1.5">
                <div>
                  <div className="text-sm font-semibold">Mellow scene</div>
                  <div className="text-xs text-muted-foreground">Quiet passages</div>
                </div>
                <div className="flex gap-1.5">
                  <Select value={mellowSceneId} onValueChange={setMellowSceneId}>
                    <SelectTrigger className="flex-1 min-w-0">
                      <SelectValue placeholder="Same as active" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">Same as active</SelectItem>
                      {scenes.map((c) => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="outline" type="button" onClick={() => openNewScene('mellow')}>
                    + New
                  </Button>
                </div>
              </div>
            </div>

            {/* Crossfade mix controls */}
            <div className="space-y-3 border-t pt-3">
              <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Crossfade</div>
              <div className="space-y-1">
                <Label className="text-sm text-muted-foreground">Low threshold (energy below → pure mellow)</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={lowThreshold}
                  onChange={(e) => setLowThreshold(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-sm text-muted-foreground">High threshold (energy above → pure active)</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={highThreshold}
                  onChange={(e) => setHighThreshold(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-sm text-muted-foreground">Fade speed / EMA alpha (0.01–0.5)</Label>
                <Input
                  type="number"
                  step={0.01}
                  min={0.01}
                  max={0.5}
                  value={fadeSpeed}
                  onChange={(e) => setFadeSpeed(e.target.value)}
                />
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
      <NewZoneDialog
        open={newZoneOpen}
        onClose={() => setNewZoneOpen(false)}
        onCreated={handleZoneCreated}
      />
      <NewAnalysisConfigDialog
        open={newAnalysisConfigOpen}
        onClose={() => setNewAnalysisConfigOpen(false)}
        onCreated={handleAnalysisConfigCreated}
      />
      <NewSceneDialog
        open={newSceneOpen}
        onClose={() => setNewSceneOpen(false)}
        onCreated={handleSceneCreated}
      />
    </>
  )
}

// ---- Main Couplings page ----

export function Couplings({ activeCouplingId: activeCouplingIdProp, onActivationChange }: Props) {
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [players, setPlayers] = useState<VirtualPlayer[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [analysisConfigs, setAnalysisConfigs] = useState<AnalysisConfig[]>([])
  const [scenes, setScenes] = useState<Scene[]>([])
  const [crossfaders, setCrossfaders] = useState<Crossfader[]>([])
  const [activeCouplingId, setActiveCouplingId] = useState<string | null>(activeCouplingIdProp)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingCoupling, setEditingCoupling] = useState<Coupling | undefined>(undefined)

  async function loadAll() {
    try {
      const [cs, ps, zs, acs, cfs, scs, st] = await Promise.all([
        getCouplings(),
        getVirtualPlayers(),
        getZones(),
        getAnalysisConfigs(),
        getCrossfaders(),
        getScenes(),
        getStatus(),
      ])
      setCouplings(cs)
      setPlayers(ps)
      setZones(zs)
      setAnalysisConfigs(acs)
      setCrossfaders(cfs)
      setScenes(scs)
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
    const p = players.find((p) => p.id === id)
    return p ? p.player_name : id
  }
  function areaName(zoneId: string) {
    const zone = zones.find((z) => z.id === zoneId)
    return zone ? zone.name : zoneId
  }
  function effectLabel(effectId: string) {
    return EFFECTS.find((e) => e.id === effectId)?.label ?? effectId
  }
  function effectDisplay(cfId: string) {
    const cf = crossfaders.find((x) => x.id === cfId)
    if (!cf) return cfId
    const activeEffect = scenes.find((s) => s.id === cf.active_scene_id)?.effect ?? '—'
    const active = effectLabel(activeEffect)
    if (!cf.mellow_scene_id || cf.mellow_scene_id === cf.active_scene_id) return active
    const mellowEffect = scenes.find((s) => s.id === cf.mellow_scene_id)?.effect ?? '—'
    return `${active} / ${effectLabel(mellowEffect)}`
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
              <TableHead>Zone</TableHead>
              <TableHead>Effect</TableHead>
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
                  <TableCell className="text-sm text-muted-foreground">{areaName(c.zone_id)}</TableCell>
                  <TableCell className="text-sm">{effectDisplay(c.crossfader_id)}</TableCell>
                  <TableCell>
                    <Badge variant={isActive ? 'default' : 'secondary'}>
                      {isActive ? 'Active' : c.enabled ? 'Inactive' : 'Disabled'}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {!isActive && (
                        <Button size="sm" variant="outline" onClick={() => handleActivate(c.id)}>
                          Go
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
        zones={zones}
        analysisConfigs={analysisConfigs}
        scenes={scenes}
        crossfaders={crossfaders}
        activeCouplingId={activeCouplingId}
        onSave={handleSave}
        onClose={() => setEditorOpen(false)}
        onPlayersChanged={setPlayers}
        onZonesChanged={setZones}
        onAnalysisConfigsChanged={setAnalysisConfigs}
        onScenesChanged={setScenes}
        onCrossfadersChanged={setCrossfaders}
      />
    </div>
  )
}
