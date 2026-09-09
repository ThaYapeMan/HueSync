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
  type Analyser,
  type Effect,
  type EnergyProfile,
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
  getAnalysers,
  createAnalyser,
  getEffects,
  createEffect,
  getEnergyProfiles,
  createEnergyProfile,
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
        display_name: '',
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
  const [nameError, setNameError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSelectedControllerId('')
      setAreas([])
      setSelectedAreaId('')
      setName('')
      setError(null)
      setNameError(null)
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
    setNameError(null)
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
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) {
        setNameError(msg)
      } else {
        setError(msg)
      }
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
              <Input value={name} onChange={(e) => { setName(e.target.value); setNameError(null) }} />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
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

// ---- Inline mini-dialog: New Analyser ----

interface NewAnalyserDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (cfg: Analyser) => void
}

function NewAnalyserDialog({ open, onClose, onCreated }: NewAnalyserDialogProps) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setError(null)
      setNameError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    setNameError(null)
    try {
      const cfg = await createAnalyser({
        name,
        onset_method: 'combined',
        bars: 30,
        lower_cutoff_freq: 50,
        higher_cutoff_freq: 12000,
        onset_delta: 0.1,
        onset_alpha: 0.9,
        superflux_mu: 3,
        superflux_lag: 2,
        use_hpss_separation: false,
      })
      onCreated(cfg)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) {
        setNameError(msg)
      } else {
        setError(msg)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New analyser</DialogTitle>
          <DialogDescription>Uses sensible defaults — edit in Analysers tab to adjust.</DialogDescription>
        </DialogHeader>
        <div className="space-y-1">
          <Label className="text-sm">Name</Label>
          <Input value={name} onChange={(e) => { setName(e.target.value); setNameError(null) }} placeholder="My analyser" />
          {nameError && <p className="text-xs text-destructive">{nameError}</p>}
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

// ---- Inline mini-dialog: New Effect ----

interface NewEffectDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (cfg: Effect) => void
}

function NewEffectDialog({ open, onClose, onCreated }: NewEffectDialogProps) {
  const [name, setName] = useState('')
  const [effectType, setEffectType] = useState('spectrum_rgb')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setEffectType('spectrum_rgb')
      setError(null)
      setNameError(null)
    }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    setNameError(null)
    try {
      const cfg = await createEffect({
        name,
        effect_type: effectType,
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
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) {
        setNameError(msg)
      } else {
        setError(msg)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New effect</DialogTitle>
          <DialogDescription>Uses sensible defaults — edit in Effects tab to adjust.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-sm">Name</Label>
            <Input value={name} onChange={(e) => { setName(e.target.value); setNameError(null) }} placeholder="My effect" />
            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
          </div>
          <div className="space-y-1">
            <Label className="text-sm">Effect type</Label>
            <Select value={effectType} onValueChange={setEffectType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EFFECTS.map((e) => (
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

// ---- Inline mini-dialog: New Energy Profile ----

interface NewEnergyProfileDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (ep: EnergyProfile) => void
  effects: Effect[]
  onEffectsChanged: (effects: Effect[]) => void
}

function NewEnergyProfileDialog({ open, onClose, onCreated, effects, onEffectsChanged }: NewEnergyProfileDialogProps) {
  const [name, setName] = useState('')
  const [highEnergyEffectId, setHighEnergyEffectId] = useState('')
  const [lowEnergyEffectId, setLowEnergyEffectId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [newEffectOpen, setNewEffectOpen] = useState(false)
  const [newEffectTarget, setNewEffectTarget] = useState<'high' | 'low'>('high')

  useEffect(() => {
    if (open) {
      setName('')
      setHighEnergyEffectId('')
      setLowEnergyEffectId('')
      setError(null)
      setNameError(null)
    }
  }, [open])

  async function handleEffectCreated(cfg: Effect) {
    setNewEffectOpen(false)
    const updated = await getEffects().catch(() => effects)
    onEffectsChanged(updated)
    if (newEffectTarget === 'low') {
      setLowEnergyEffectId(cfg.id)
    } else {
      setHighEnergyEffectId(cfg.id)
    }
  }

  async function handleSave() {
    if (!highEnergyEffectId) {
      setError('Select a high-energy effect')
      return
    }
    setSaving(true)
    setError(null)
    setNameError(null)
    try {
      const ep = await createEnergyProfile({
        name: name || 'Energy Profile',
        high_energy_effect_id: highEnergyEffectId,
        low_energy_effect_id: lowEnergyEffectId,
        blend_start: 0.3,
        blend_end: 0.7,
        blend_response: 0.1,
      })
      onCreated(ep)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) {
        setNameError(msg)
      } else {
        setError(msg)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>New energy profile</DialogTitle>
            <DialogDescription>Adjust blend thresholds and response in the Energy Profiles tab.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-sm">Name</Label>
              <Input value={name} onChange={(e) => { setName(e.target.value); setNameError(null) }} placeholder="My energy profile" />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
            </div>
            <div className="space-y-1">
              <Label className="text-sm">High-energy effect (loud passages)</Label>
              <div className="flex gap-1.5">
                <Select value={highEnergyEffectId} onValueChange={setHighEnergyEffectId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select effect…" />
                  </SelectTrigger>
                  <SelectContent>
                    {effects.map((e) => (
                      <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => { setNewEffectTarget('high'); setNewEffectOpen(true) }}>
                  + New
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-sm">Low-energy effect (quiet passages, optional)</Label>
              <div className="flex gap-1.5">
                <Select value={lowEnergyEffectId} onValueChange={setLowEnergyEffectId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Same as high-energy" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">Same as high-energy</SelectItem>
                    {effects.map((e) => (
                      <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => { setNewEffectTarget('low'); setNewEffectOpen(true) }}>
                  + New
                </Button>
              </div>
            </div>
          </div>
          {error && <p className="text-destructive text-sm mt-2">{error}</p>}
          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={onClose} disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={saving || !highEnergyEffectId}>{saving ? 'Saving…' : 'Create'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <NewEffectDialog
        open={newEffectOpen}
        onClose={() => setNewEffectOpen(false)}
        onCreated={handleEffectCreated}
      />
    </>
  )
}

// ---- CouplingEditor dialog ----

interface CouplingEditorProps {
  open: boolean
  coupling?: Coupling
  players: VirtualPlayer[]
  zones: Zone[]
  analysers: Analyser[]
  effects: Effect[]
  energyProfiles: EnergyProfile[]
  activeCouplingId: string | null
  onSave: () => void
  onClose: () => void
  onPlayersChanged: (players: VirtualPlayer[]) => void
  onZonesChanged: (zones: Zone[]) => void
  onAnalysersChanged: (cfgs: Analyser[]) => void
  onEffectsChanged: (effects: Effect[]) => void
  onEnergyProfilesChanged: (eps: EnergyProfile[]) => void
}

function CouplingEditor({
  open,
  coupling,
  players,
  zones,
  analysers,
  effects,
  energyProfiles,
  activeCouplingId,
  onSave,
  onClose,
  onPlayersChanged,
  onZonesChanged,
  onAnalysersChanged,
  onEffectsChanged,
  onEnergyProfilesChanged,
}: CouplingEditorProps) {
  const isEditing = !!coupling
  const isLive = !!(coupling && activeCouplingId && coupling.id === activeCouplingId)

  const [name, setName] = useState('')
  const [playerId, setPlayerId] = useState('')
  const [zoneId, setZoneId] = useState('')
  const [analyserId, setAnalyserId] = useState('')
  const [energyProfileId, setEnergyProfileId] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  // Nested dialog states
  const [newPlayerOpen, setNewPlayerOpen] = useState(false)
  const [newZoneOpen, setNewZoneOpen] = useState(false)
  const [newAnalyserOpen, setNewAnalyserOpen] = useState(false)
  const [newEnergyProfileOpen, setNewEnergyProfileOpen] = useState(false)

  useEffect(() => {
    if (open) {
      setName(coupling?.name ?? '')
      setPlayerId(coupling?.player_id ?? '')
      setZoneId(coupling?.zone_id ?? '')
      setAnalyserId(coupling?.analyser_id ?? '')
      setEnergyProfileId(coupling?.energy_profile_id ?? '')
      setEnabled(coupling?.enabled ?? true)
      setSaveError(null)
      setNameError(null)
    }
  }, [open, coupling])

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    setNameError(null)
    try {
      if (isEditing && coupling) {
        await updateCoupling(coupling.id, {
          name,
          enabled,
          player_id: playerId,
          zone_id: zoneId,
          analyser_id: analyserId,
          energy_profile_id: energyProfileId,
        })
      } else {
        await createCoupling({
          name,
          player_id: playerId,
          zone_id: zoneId,
          analyser_id: analyserId,
          energy_profile_id: energyProfileId,
          enabled,
        })
      }
      onSave()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) {
        setNameError(msg)
      } else {
        setSaveError(msg)
      }
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

  async function handleAnalyserCreated(cfg: Analyser) {
    setNewAnalyserOpen(false)
    const updated = await getAnalysers().catch(() => analysers)
    onAnalysersChanged(updated)
    setAnalyserId(cfg.id)
  }

  async function handleEnergyProfileCreated(ep: EnergyProfile) {
    setNewEnergyProfileOpen(false)
    const updated = await getEnergyProfiles().catch(() => energyProfiles)
    onEnergyProfilesChanged(updated)
    setEnergyProfileId(ep.id)
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
              <Input value={name} onChange={(e) => { setName(e.target.value); setNameError(null) }} placeholder="My coupling" />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
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
                      <SelectItem key={p.id} value={p.id}>{p.type}</SelectItem>
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

            {/* Analyser */}
            <div className="space-y-1">
              <Label className="text-sm">Analyser</Label>
              <div className="flex gap-2">
                <Select value={analyserId} onValueChange={setAnalyserId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select analyser" />
                  </SelectTrigger>
                  <SelectContent>
                    {analysers.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewAnalyserOpen(true)}>
                  + New
                </Button>
              </div>
            </div>

            {/* Energy Profile */}
            <div className="space-y-1">
              <Label className="text-sm">Energy Profile</Label>
              <p className="text-xs text-muted-foreground">
                Controls which effects play and how they blend with music energy. Edit details in the Energy Profiles tab.
              </p>
              <div className="flex gap-2">
                <Select value={energyProfileId} onValueChange={setEnergyProfileId}>
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Select energy profile" />
                  </SelectTrigger>
                  <SelectContent>
                    {energyProfiles.map((ep) => (
                      <SelectItem key={ep.id} value={ep.id}>{ep.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button" onClick={() => setNewEnergyProfileOpen(true)}>
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
      <NewZoneDialog
        open={newZoneOpen}
        onClose={() => setNewZoneOpen(false)}
        onCreated={handleZoneCreated}
      />
      <NewAnalyserDialog
        open={newAnalyserOpen}
        onClose={() => setNewAnalyserOpen(false)}
        onCreated={handleAnalyserCreated}
      />
      <NewEnergyProfileDialog
        open={newEnergyProfileOpen}
        onClose={() => setNewEnergyProfileOpen(false)}
        onCreated={handleEnergyProfileCreated}
        effects={effects}
        onEffectsChanged={onEffectsChanged}
      />
    </>
  )
}

// ---- Main Couplings page ----

export function Couplings({ activeCouplingId: activeCouplingIdProp, onActivationChange }: Props) {
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [players, setPlayers] = useState<VirtualPlayer[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [analysers, setAnalysers] = useState<Analyser[]>([])
  const [effects, setEffects] = useState<Effect[]>([])
  const [energyProfiles, setEnergyProfiles] = useState<EnergyProfile[]>([])
  const [activeCouplingId, setActiveCouplingId] = useState<string | null>(activeCouplingIdProp)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingCoupling, setEditingCoupling] = useState<Coupling | undefined>(undefined)

  async function loadAll() {
    try {
      const [cs, ps, zs, acs, eps, effs, st] = await Promise.all([
        getCouplings(),
        getVirtualPlayers(),
        getZones(),
        getAnalysers(),
        getEnergyProfiles(),
        getEffects(),
        getStatus(),
      ])
      setCouplings(cs)
      setPlayers(ps)
      setZones(zs)
      setAnalysers(acs)
      setEnergyProfiles(eps)
      setEffects(effs)
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
    return p ? p.type : id
  }
  function areaName(zoneId: string) {
    const zone = zones.find((z) => z.id === zoneId)
    return zone ? zone.name : zoneId
  }
  function effectTypeLabel(effectTypeId: string) {
    return EFFECTS.find((e) => e.id === effectTypeId)?.label ?? effectTypeId
  }
  function effectDisplay(epId: string) {
    const ep = energyProfiles.find((x) => x.id === epId)
    if (!ep) return epId
    const highEffect = effects.find((e) => e.id === ep.high_energy_effect_id)
    const highLabel = effectTypeLabel(highEffect?.effect_type ?? '—')
    if (!ep.low_energy_effect_id || ep.low_energy_effect_id === ep.high_energy_effect_id) return highLabel
    const lowEffect = effects.find((e) => e.id === ep.low_energy_effect_id)
    return `${highLabel} / ${effectTypeLabel(lowEffect?.effect_type ?? '—')}`
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
                  <TableCell className="text-sm">{effectDisplay(c.energy_profile_id)}</TableCell>
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
        analysers={analysers}
        effects={effects}
        energyProfiles={energyProfiles}
        activeCouplingId={activeCouplingId}
        onSave={handleSave}
        onClose={() => setEditorOpen(false)}
        onPlayersChanged={setPlayers}
        onZonesChanged={setZones}
        onAnalysersChanged={setAnalysers}
        onEffectsChanged={setEffects}
        onEnergyProfilesChanged={setEnergyProfiles}
      />
    </div>
  )
}
