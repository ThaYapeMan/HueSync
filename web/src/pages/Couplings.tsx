import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
import { SectionLabel } from '@/components/editor/SectionLabel'
import { LightPreview } from '@/components/LightPreview'
import { cn } from '@/lib/utils'
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
  onNavigate?: (tab: string) => void
}

// ---- Mini-dialog: New Player ----

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
    if (open) { setLmsHost(''); setPlayerName('HueSync'); setAlsaDevice(''); setError(null) }
  }, [open])

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const player = await createVirtualPlayer({
        type: 'LMS', lms_host: lmsHost, lms_port: 9000,
        player_name: playerName, display_name: '', alsa_device: alsaDevice, follow_player_mac: '',
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

// ---- Mini-dialog: New Zone ----

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
      setSelectedControllerId(''); setAreas([]); setSelectedAreaId(''); setName('')
      setError(null); setNameError(null); setLoadingControllers(true)
      getControllers().then(setControllers).catch(() => setControllers([])).finally(() => setLoadingControllers(false))
    }
  }, [open])

  useEffect(() => {
    if (!selectedControllerId) { setAreas([]); setSelectedAreaId(''); setName(''); return }
    setLoadingAreas(true)
    getControllerAreas(selectedControllerId).then(setAreas).catch(() => setAreas([])).finally(() => setLoadingAreas(false))
  }, [selectedControllerId])

  function handleAreaChange(areaId: string) {
    setSelectedAreaId(areaId)
    const area = areas.find((a) => a.id === areaId)
    if (area) setName(area.name)
  }

  async function handleSave() {
    setSaving(true); setError(null); setNameError(null)
    try {
      const area = areas.find((a) => a.id === selectedAreaId)
      const zone = await createZone({
        name, controller_id: selectedControllerId,
        entertainment_area_id: selectedAreaId,
        entertainment_area_name: area?.name ?? '',
        light_count: area?.light_count ?? 0,
      })
      onCreated(zone)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) setNameError(msg)
      else setError(msg)
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
            {loadingControllers ? <p className="text-sm text-muted-foreground">Loading…</p> : (
              <Select value={selectedControllerId} onValueChange={setSelectedControllerId}>
                <SelectTrigger><SelectValue placeholder="Select controller" /></SelectTrigger>
                <SelectContent>
                  {controllers.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </div>
          {selectedControllerId && (
            <div className="space-y-1">
              <Label className="text-sm">Entertainment area</Label>
              {loadingAreas ? <p className="text-sm text-muted-foreground">Loading areas…</p> : (
                <Select value={selectedAreaId} onValueChange={handleAreaChange}>
                  <SelectTrigger><SelectValue placeholder="Select area" /></SelectTrigger>
                  <SelectContent>
                    {areas.map((a) => <SelectItem key={a.id} value={a.id}>{a.name} ({a.light_count} lights)</SelectItem>)}
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

// ---- Mini-dialog: New Analyser ----

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

  useEffect(() => { if (open) { setName(''); setError(null); setNameError(null) } }, [open])

  async function handleSave() {
    setSaving(true); setError(null); setNameError(null)
    try {
      const cfg = await createAnalyser({
        name, bars_source: 'cava', onset_method: 'combined', bars: 30,
        lower_cutoff_freq: 50, higher_cutoff_freq: 12000,
        onset_delta: 0.1, onset_alpha: 0.9, superflux_mu: 3, superflux_lag: 2,
        use_hpss_separation: false,
      })
      onCreated(cfg)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) setNameError(msg)
      else setError(msg)
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

// ---- Mini-dialog: New Effect ----

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

  useEffect(() => { if (open) { setName(''); setEffectType('spectrum_rgb'); setError(null); setNameError(null) } }, [open])

  async function handleSave() {
    setSaving(true); setError(null); setNameError(null)
    try {
      const cfg = await createEffect({
        name, effect_type: effectType, effect_speed: 1.0, effect_decay: 0.3,
        sensitivity: 1.0, brightness_floor: 0.15, bass_hz: 250, mid_hz: 2000,
        exertion_clip: 3.0, onset_flash_intensity: 0.0,
      })
      onCreated(cfg)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) setNameError(msg)
      else setError(msg)
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
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {EFFECTS.map((e) => <SelectItem key={e.id} value={e.id}>{e.label}</SelectItem>)}
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

// ---- Mini-dialog: New Energy Profile ----

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
    if (open) { setName(''); setHighEnergyEffectId(''); setLowEnergyEffectId(''); setError(null); setNameError(null) }
  }, [open])

  async function handleEffectCreated(cfg: Effect) {
    setNewEffectOpen(false)
    const updated = await getEffects().catch(() => effects)
    onEffectsChanged(updated)
    if (newEffectTarget === 'low') setLowEnergyEffectId(cfg.id)
    else setHighEnergyEffectId(cfg.id)
  }

  async function handleSave() {
    if (!highEnergyEffectId) { setError('Select a high-energy effect'); return }
    setSaving(true); setError(null); setNameError(null)
    try {
      const ep = await createEnergyProfile({
        name: name || 'Energy Profile',
        high_energy_effect_id: highEnergyEffectId,
        low_energy_effect_id: lowEnergyEffectId,
        blend_start: 0.3, blend_end: 0.7, blend_response: 0.1,
      })
      onCreated(ep)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) setNameError(msg)
      else setError(msg)
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
            <DialogDescription>Adjust blend thresholds in the Energy Profiles tab.</DialogDescription>
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
                  <SelectTrigger className="flex-1"><SelectValue placeholder="Select effect…" /></SelectTrigger>
                  <SelectContent>
                    {effects.map((e) => <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button"
                  onClick={() => { setNewEffectTarget('high'); setNewEffectOpen(true) }}>
                  + New
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-sm">Low-energy effect (quiet passages, optional)</Label>
              <div className="flex gap-1.5">
                <Select value={lowEnergyEffectId} onValueChange={setLowEnergyEffectId}>
                  <SelectTrigger className="flex-1"><SelectValue placeholder="Same as high-energy" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">Same as high-energy</SelectItem>
                    {effects.map((e) => <SelectItem key={e.id} value={e.id}>{e.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" type="button"
                  onClick={() => { setNewEffectTarget('low'); setNewEffectOpen(true) }}>
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
      <NewEffectDialog open={newEffectOpen} onClose={() => setNewEffectOpen(false)} onCreated={handleEffectCreated} />
    </>
  )
}

// ---- Coupling Card ----

interface CardProps {
  coupling: Coupling
  player: VirtualPlayer | undefined
  zone: Zone | undefined
  ep: EnergyProfile | undefined
  effects: Effect[]
  isActive: boolean
  isSelected: boolean
  onSelect: () => void
  onActivate: () => void
  onDeactivate: () => void
}

function CouplingCard({ coupling, player, zone, ep, effects, isActive, isSelected, onSelect, onActivate, onDeactivate }: CardProps) {
  const highEffect = effects.find(e => e.id === ep?.high_energy_effect_id)
  const lowEffect = effects.find(e => e.id === ep?.low_energy_effect_id) ?? highEffect

  return (
    <div
      className={cn(
        'border rounded-lg cursor-pointer transition-colors overflow-hidden',
        isSelected
          ? 'border-primary/60 bg-muted/30'
          : 'border-border hover:border-muted-foreground/30 bg-card',
      )}
      onClick={onSelect}
    >
      <div className="bg-black/25">
        {highEffect ? (
          <LightPreview
            lowEffectType={lowEffect?.effect_type ?? highEffect.effect_type}
            highEffectType={highEffect.effect_type}
            mix={0.5}
            energy={isActive ? 0.65 : 0.28}
          />
        ) : (
          <div className="h-[60px] flex items-center justify-center">
            <span className="text-xs text-muted-foreground/40">No energy profile</span>
          </div>
        )}
      </div>

      <div className="px-3 py-2.5 space-y-1.5">
        <div className="flex items-start justify-between gap-2">
          <span className="font-medium text-sm leading-tight">{coupling.name}</span>
          <Badge
            variant={isActive ? 'default' : 'secondary'}
            className={cn('shrink-0 text-xs', !coupling.enabled && !isActive && 'opacity-50')}
          >
            {isActive ? '● Active' : coupling.enabled ? 'Ready' : 'Disabled'}
          </Badge>
        </div>

        <div className="text-xs text-muted-foreground flex items-center gap-1.5">
          <span className={cn(!player && 'text-destructive/60')}>
            {player ? (player.display_name || player.player_name || player.type) : '(no player)'}
          </span>
          <span className="opacity-40">→</span>
          <span className={cn(!zone && 'text-destructive/60')}>
            {zone ? zone.name : '(no zone)'}
          </span>
        </div>

        <div className="flex justify-end" onClick={e => e.stopPropagation()}>
          {isActive ? (
            <Button size="sm" variant="outline" className="h-6 px-2.5 text-xs" onClick={onDeactivate}>
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2.5 text-xs"
              disabled={!coupling.enabled}
              onClick={onActivate}
            >
              Go
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- Coupling Inspector ----

interface InspectorForm {
  name: string
  playerId: string
  zoneId: string
  analyserId: string
  energyProfileId: string
  enabled: boolean
}

function inspectorFormFrom(c: Coupling | null): InspectorForm {
  return {
    name: c?.name ?? '',
    playerId: c?.player_id ?? '',
    zoneId: c?.zone_id ?? '',
    analyserId: c?.analyser_id ?? '',
    energyProfileId: c?.energy_profile_id ?? '',
    enabled: c?.enabled ?? true,
  }
}

interface InspectorProps {
  coupling: Coupling | null
  players: VirtualPlayer[]
  zones: Zone[]
  analysers: Analyser[]
  effects: Effect[]
  energyProfiles: EnergyProfile[]
  activeCouplingId: string | null
  onSaved: (c: Coupling) => void
  onDeleted: (id: string) => void
  onCloned: (id: string) => void
  onCancelCreate: () => void
  onNavigate?: (tab: string) => void
  onPlayersChanged: (ps: VirtualPlayer[]) => void
  onZonesChanged: (zs: Zone[]) => void
  onAnalysersChanged: (as: Analyser[]) => void
  onEffectsChanged: (es: Effect[]) => void
  onEnergyProfilesChanged: (eps: EnergyProfile[]) => void
}

function CouplingInspector({
  coupling,
  players,
  zones,
  analysers,
  effects,
  energyProfiles,
  activeCouplingId,
  onSaved,
  onDeleted,
  onCloned,
  onCancelCreate,
  onNavigate,
  onPlayersChanged,
  onZonesChanged,
  onAnalysersChanged,
  onEffectsChanged,
  onEnergyProfilesChanged,
}: InspectorProps) {
  const isCreating = coupling === null
  const isLive = !isCreating && coupling?.id === activeCouplingId

  const [form, setForm] = useState<InspectorForm>(() => inspectorFormFrom(coupling))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  const [newPlayerOpen, setNewPlayerOpen] = useState(false)
  const [newZoneOpen, setNewZoneOpen] = useState(false)
  const [newAnalyserOpen, setNewAnalyserOpen] = useState(false)
  const [newEnergyProfileOpen, setNewEnergyProfileOpen] = useState(false)

  function patch(p: Partial<InspectorForm>) {
    setForm(f => ({ ...f, ...p }))
    if (p.name !== undefined) setNameError(null)
    setError(null)
  }

  async function handleSave() {
    setSaving(true); setError(null); setNameError(null)
    try {
      const body = {
        name: form.name,
        player_id: form.playerId,
        zone_id: form.zoneId,
        analyser_id: form.analyserId,
        energy_profile_id: form.energyProfileId,
        enabled: form.enabled,
      }
      const saved = isCreating
        ? await createCoupling(body)
        : await updateCoupling(coupling.id, body)
      onSaved(saved)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      if ((e as any).status === 409) setNameError(msg)
      else setError(msg)
    } finally {
      setSaving(false)
    }
  }

  async function handlePlayerCreated(player: VirtualPlayer) {
    setNewPlayerOpen(false)
    const updated = await getVirtualPlayers().catch(() => players)
    onPlayersChanged(updated)
    patch({ playerId: player.id })
  }

  async function handleZoneCreated(zone: Zone) {
    setNewZoneOpen(false)
    const updated = await getZones().catch(() => zones)
    onZonesChanged(updated)
    patch({ zoneId: zone.id })
  }

  async function handleAnalyserCreated(a: Analyser) {
    setNewAnalyserOpen(false)
    const updated = await getAnalysers().catch(() => analysers)
    onAnalysersChanged(updated)
    patch({ analyserId: a.id })
  }

  async function handleEnergyProfileCreated(ep: EnergyProfile) {
    setNewEnergyProfileOpen(false)
    const updated = await getEnergyProfiles().catch(() => energyProfiles)
    onEnergyProfilesChanged(updated)
    patch({ energyProfileId: ep.id })
  }

  const selectedEp = energyProfiles.find(e => e.id === form.energyProfileId)
  const highEffect = effects.find(e => e.id === selectedEp?.high_energy_effect_id)
  const lowEffect = effects.find(e => e.id === selectedEp?.low_energy_effect_id) ?? highEffect
  const selectedZone = zones.find(z => z.id === form.zoneId)

  return (
    <>
      <div className="flex flex-col h-full">
        {/* Header */}
        <div className="px-4 pt-4 pb-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2 mb-2">
            <SectionLabel className="flex-1">{isCreating ? 'New coupling' : 'Coupling'}</SectionLabel>
            {isLive && (
              <span className="flex items-center gap-1 text-xs text-green-400 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                Live
              </span>
            )}
          </div>
          <input
            value={form.name}
            onChange={e => patch({ name: e.target.value })}
            placeholder="Coupling name…"
            className="w-full bg-transparent text-base font-semibold outline-none placeholder:text-muted-foreground/40 border-b border-transparent focus:border-border pb-0.5 transition-colors"
          />
          {nameError && <p className="text-xs text-destructive mt-1">{nameError}</p>}
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
          {/* Enabled */}
          <div className="flex items-center gap-2">
            <input
              id="insp-enabled"
              type="checkbox"
              checked={form.enabled}
              onChange={e => patch({ enabled: e.target.checked })}
              className="h-4 w-4 cursor-pointer"
            />
            <Label htmlFor="insp-enabled" className="text-sm cursor-pointer">Enabled</Label>
          </div>

          {/* References */}
          <div className="space-y-3">
            <SectionLabel>References</SectionLabel>

            {/* Player */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Virtual Player</Label>
                {form.playerId && onNavigate && (
                  <button
                    className="text-xs text-primary/70 hover:text-primary transition-colors"
                    onClick={() => onNavigate('players')}
                  >
                    Open →
                  </button>
                )}
              </div>
              <div className="flex gap-1.5">
                <Select value={form.playerId} onValueChange={v => patch({ playerId: v })}>
                  <SelectTrigger className="flex-1 h-8 text-xs">
                    <SelectValue placeholder="Select player…" />
                  </SelectTrigger>
                  <SelectContent>
                    {players.map(p => (
                      <SelectItem key={p.id} value={p.id} className="text-xs">
                        {p.display_name || p.player_name || p.type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" className="h-8 px-2 text-xs shrink-0"
                  onClick={() => setNewPlayerOpen(true)}>+ New</Button>
              </div>
            </div>

            {/* Zone */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Zone</Label>
                {form.zoneId && onNavigate && (
                  <button
                    className="text-xs text-primary/70 hover:text-primary transition-colors"
                    onClick={() => onNavigate('zones')}
                  >
                    Open →
                  </button>
                )}
              </div>
              <div className="flex gap-1.5">
                <Select value={form.zoneId} onValueChange={v => patch({ zoneId: v })}>
                  <SelectTrigger className="flex-1 h-8 text-xs">
                    <SelectValue placeholder="Select zone…" />
                  </SelectTrigger>
                  <SelectContent>
                    {zones.map(z => (
                      <SelectItem key={z.id} value={z.id} className="text-xs">{z.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" className="h-8 px-2 text-xs shrink-0"
                  onClick={() => setNewZoneOpen(true)}>+ New</Button>
              </div>
              {selectedZone && (
                <p className="text-xs text-muted-foreground">
                  {selectedZone.entertainment_area_name} — {selectedZone.light_count} lights
                </p>
              )}
            </div>

            {/* Analyser */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Analyser</Label>
                {form.analyserId && onNavigate && (
                  <button
                    className="text-xs text-primary/70 hover:text-primary transition-colors"
                    onClick={() => onNavigate('analysers')}
                  >
                    Open →
                  </button>
                )}
              </div>
              <div className="flex gap-1.5">
                <Select value={form.analyserId} onValueChange={v => patch({ analyserId: v })}>
                  <SelectTrigger className="flex-1 h-8 text-xs">
                    <SelectValue placeholder="Select analyser…" />
                  </SelectTrigger>
                  <SelectContent>
                    {analysers.map(a => (
                      <SelectItem key={a.id} value={a.id} className="text-xs">{a.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" className="h-8 px-2 text-xs shrink-0"
                  onClick={() => setNewAnalyserOpen(true)}>+ New</Button>
              </div>
            </div>

            {/* Energy Profile */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs text-muted-foreground">Energy Profile</Label>
                {form.energyProfileId && onNavigate && (
                  <button
                    className="text-xs text-primary/70 hover:text-primary transition-colors"
                    onClick={() => onNavigate('energy-profiles')}
                  >
                    Open →
                  </button>
                )}
              </div>
              <div className="flex gap-1.5">
                <Select value={form.energyProfileId} onValueChange={v => patch({ energyProfileId: v })}>
                  <SelectTrigger className="flex-1 h-8 text-xs">
                    <SelectValue placeholder="Select energy profile…" />
                  </SelectTrigger>
                  <SelectContent>
                    {energyProfiles.map(ep => (
                      <SelectItem key={ep.id} value={ep.id} className="text-xs">{ep.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" className="h-8 px-2 text-xs shrink-0"
                  onClick={() => setNewEnergyProfileOpen(true)}>+ New</Button>
              </div>

              {highEffect && (
                <div className="rounded-md border border-border/50 bg-black/20 mt-2 overflow-hidden">
                  <LightPreview
                    lowEffectType={lowEffect?.effect_type ?? highEffect.effect_type}
                    highEffectType={highEffect.effect_type}
                    mix={0.5}
                    energy={0.60}
                  />
                  {selectedEp && (
                    <p className="text-xs text-muted-foreground text-center pb-2">{selectedEp.name}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-border shrink-0 space-y-2">
          {error && <p className="text-xs text-destructive">{error}</p>}
          <Button onClick={handleSave} disabled={saving} className="w-full" size="sm">
            {saving ? 'Saving…' : isCreating ? 'Create coupling' : 'Save changes'}
          </Button>
          {isCreating ? (
            <Button variant="outline" size="sm" className="w-full" onClick={onCancelCreate}>
              Cancel
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => onCloned(coupling!.id)}
              >
                Clone
              </Button>
              <ConfirmDialog
                trigger={
                  <Button variant="outline" size="sm" className="flex-1 text-destructive hover:text-destructive">
                    Delete
                  </Button>
                }
                title="Delete coupling"
                description={`Delete "${coupling!.name}"? This cannot be undone.`}
                onConfirm={() => onDeleted(coupling!.id)}
              />
            </div>
          )}
        </div>
      </div>

      <NewPlayerDialog open={newPlayerOpen} onClose={() => setNewPlayerOpen(false)} onCreated={handlePlayerCreated} />
      <NewZoneDialog open={newZoneOpen} onClose={() => setNewZoneOpen(false)} onCreated={handleZoneCreated} />
      <NewAnalyserDialog open={newAnalyserOpen} onClose={() => setNewAnalyserOpen(false)} onCreated={handleAnalyserCreated} />
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

// ---- Couplings (main) ----

export function Couplings({ activeCouplingId: activeCouplingIdProp, onActivationChange, onNavigate }: Props) {
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
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  async function loadAll() {
    try {
      const [cs, ps, zs, acs, eps, effs, st] = await Promise.all([
        getCouplings(), getVirtualPlayers(), getZones(), getAnalysers(),
        getEnergyProfiles(), getEffects(), getStatus(),
      ])
      setCouplings(cs); setPlayers(ps); setZones(zs); setAnalysers(acs)
      setEnergyProfiles(eps); setEffects(effs)
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

  async function handleClone(id: string) {
    setActionError(null)
    try {
      const cloned = await cloneCoupling(id)
      await loadAll()
      setSelectedId(cloned.id)
      setIsCreating(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Clone failed')
    }
  }

  async function handleDelete(id: string) {
    setActionError(null)
    try {
      await deleteCoupling(id)
      if (selectedId === id) { setSelectedId(null); setIsCreating(false) }
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  function handleSaved(saved: Coupling) {
    setSelectedId(saved.id)
    setIsCreating(false)
    loadAll()
  }

  const inspectorKey = isCreating ? '__new__' : (selectedId ?? '__none__')
  const selectedCoupling = isCreating ? null : (couplings.find(c => c.id === selectedId) ?? null)
  const showInspector = isCreating || selectedId !== null

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
        <SectionLabel>Couplings</SectionLabel>
        <Button size="sm" onClick={() => { setIsCreating(true); setSelectedId(null) }}>
          New coupling
        </Button>
      </div>

      {actionError && (
        <div className="px-5 py-2 text-xs text-destructive border-b border-border bg-destructive/5 shrink-0">
          {actionError}
        </div>
      )}

      {/* Main workspace: card list + inspector */}
      <div className="flex-1 overflow-hidden flex">
        {/* Card list */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : error ? (
            <p className="text-sm text-destructive">{error}</p>
          ) : couplings.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center gap-2">
              <p className="text-sm text-muted-foreground">No couplings yet.</p>
              <p className="text-xs text-muted-foreground/60">Create one to connect a player to a zone.</p>
            </div>
          ) : (
            <div className="max-w-xs space-y-3">
              {couplings.map(c => (
                <CouplingCard
                  key={c.id}
                  coupling={c}
                  player={players.find(p => p.id === c.player_id)}
                  zone={zones.find(z => z.id === c.zone_id)}
                  ep={energyProfiles.find(e => e.id === c.energy_profile_id)}
                  effects={effects}
                  isActive={c.id === activeCouplingId}
                  isSelected={!isCreating && c.id === selectedId}
                  onSelect={() => { setSelectedId(c.id); setIsCreating(false) }}
                  onActivate={() => handleActivate(c.id)}
                  onDeactivate={handleDeactivate}
                />
              ))}
            </div>
          )}
        </div>

        {/* Inspector panel */}
        <div className="w-80 border-l border-border flex flex-col shrink-0">
          {showInspector ? (
            <CouplingInspector
              key={inspectorKey}
              coupling={selectedCoupling}
              players={players}
              zones={zones}
              analysers={analysers}
              effects={effects}
              energyProfiles={energyProfiles}
              activeCouplingId={activeCouplingId}
              onSaved={handleSaved}
              onDeleted={handleDelete}
              onCloned={handleClone}
              onCancelCreate={() => { setIsCreating(false); setSelectedId(null) }}
              onNavigate={onNavigate}
              onPlayersChanged={setPlayers}
              onZonesChanged={setZones}
              onAnalysersChanged={setAnalysers}
              onEffectsChanged={setEffects}
              onEnergyProfilesChanged={setEnergyProfiles}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-xs text-muted-foreground/40 p-8 text-center">
              Select a coupling to edit, or create a new one.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
