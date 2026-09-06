import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { SliderField } from '@/components/SliderField'
import {
  ONSET_METHODS,
  type Bridge,
  type EntertainmentArea,
  type LmsServer,
  type Profile,
  getBridgeAreas,
  createProfile,
  updateProfile,
  discoverLms,
} from '@/lib/api'

interface Props {
  profile?: Profile
  bridges: Bridge[]
  activeProfileId: string | null
  onSave: () => void
  onClose: () => void
  open: boolean
}

interface FormState {
  name: string
  lms_host: string
  lms_port: string
  player_name: string
  player_mac: string
  alsa_device: string
  bridge_id: string
  entertainment_area_id: string
  color_mode: string
  bars: string
  sensitivity: number
  brightness_floor: number
  exertion_clip: number
  onset_delta: number
  onset_alpha: number
  onset_method: string
  superflux_mu: number
  superflux_lag: number
  lower_cutoff_freq: string
  higher_cutoff_freq: string
  bass_hz: string
  mid_hz: string
}

function defaultForm(profile?: Profile): FormState {
  return {
    name: profile?.name ?? '',
    lms_host: profile?.lms_host ?? '',
    lms_port: String(profile?.lms_port ?? 9000),
    player_name: profile?.player_name ?? 'HueSync',
    player_mac: profile?.player_mac ?? '',
    alsa_device: profile?.alsa_device ?? '',
    bridge_id: profile?.bridge_id ?? '',
    entertainment_area_id: profile?.entertainment_area_id ?? '',
    color_mode: profile?.color_mode ?? 'spectrum_rgb',
    bars: String(profile?.bars ?? 30),
    sensitivity: profile?.sensitivity ?? 1.0,
    brightness_floor: profile?.brightness_floor ?? 0.05,
    exertion_clip: profile?.exertion_clip ?? 2.0,
    onset_delta: profile?.onset_delta ?? 0.07,
    onset_alpha: profile?.onset_alpha ?? 0.9,
    onset_method: profile?.onset_method ?? 'combined',
    superflux_mu: profile?.superflux_mu ?? 3,
    superflux_lag: profile?.superflux_lag ?? 2,
    lower_cutoff_freq: String(profile?.lower_cutoff_freq ?? 50),
    higher_cutoff_freq: String(profile?.higher_cutoff_freq ?? 12000),
    bass_hz: String(profile?.bass_hz ?? 250),
    mid_hz: String(profile?.mid_hz ?? 2000),
  }
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground pt-2 pb-1 border-b border-border mb-3">
      {children}
    </p>
  )
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      {children}
    </div>
  )
}

export function ProfileEditor({ profile, bridges, onSave, onClose, open }: Props) {
  const isEditing = !!profile
  const [form, setForm] = useState<FormState>(() => defaultForm(profile))
  const [areas, setAreas] = useState<EntertainmentArea[]>([])
  const [lmsServers, setLmsServers] = useState<LmsServer[]>([])
  const [discoveringLms, setDiscoveringLms] = useState(false)
  const [showLmsPicker, setShowLmsPicker] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm(defaultForm(profile))
      setError(null)
      setShowLmsPicker(false)
      setLmsServers([])
    }
  }, [open, profile])

  useEffect(() => {
    if (!form.bridge_id) {
      setAreas([])
      return
    }
    getBridgeAreas(form.bridge_id).then(setAreas).catch(() => setAreas([]))
  }, [form.bridge_id])

  function set(key: keyof FormState, value: string | number) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function handleBridgeChange(bridgeId: string) {
    setForm((f) => ({ ...f, bridge_id: bridgeId, entertainment_area_id: '' }))
  }

  async function handleDiscoverLms() {
    setDiscoveringLms(true)
    try {
      const servers = await discoverLms()
      setLmsServers(servers)
      setShowLmsPicker(true)
    } catch {
      setError('LMS discovery failed')
    } finally {
      setDiscoveringLms(false)
    }
  }

  function handlePickLms(server: LmsServer) {
    setForm((f) => ({ ...f, lms_host: server.host, lms_port: String(server.port) }))
    setShowLmsPicker(false)
  }

  async function handleSave() {
    if (!form.lms_host.trim()) {
      setError('LMS host is required. Enter the IP address of your LMS server or use Discover.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        lms_host: form.lms_host,
        lms_port: parseInt(form.lms_port, 10),
        player_name: form.player_name,
        alsa_device: form.alsa_device,
        bridge_id: form.bridge_id,
        entertainment_area_id: form.entertainment_area_id,
        entertainment_area_name:
          areas.find((a) => a.id === form.entertainment_area_id)?.name ?? '',
        light_count: areas.find((a) => a.id === form.entertainment_area_id)?.light_count ?? 0,
        color_mode: form.color_mode,
        bars: parseInt(form.bars, 10),
        sensitivity: form.sensitivity,
        brightness_floor: form.brightness_floor,
        exertion_clip: form.exertion_clip,
        onset_delta: form.onset_delta,
        onset_alpha: form.onset_alpha,
        onset_method: form.onset_method,
        superflux_mu: form.superflux_mu,
        superflux_lag: form.superflux_lag,
        lower_cutoff_freq: parseInt(form.lower_cutoff_freq, 10),
        higher_cutoff_freq: parseInt(form.higher_cutoff_freq, 10),
        bass_hz: parseInt(form.bass_hz, 10),
        mid_hz: parseInt(form.mid_hz, 10),
        enabled: true,
      }
      if (isEditing) {
        await updateProfile(profile.id, body)
      } else {
        await createProfile(body as Parameters<typeof createProfile>[0])
      }
      onSave()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-w-lg flex flex-col max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>{isEditing ? 'Edit profile' : 'New profile'}</DialogTitle>
        </DialogHeader>

        <div className="overflow-y-auto flex-1 pr-1 space-y-4">
          <SectionLabel>Identity</SectionLabel>
          <FormRow label="Name">
            <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="My profile" />
          </FormRow>

          <SectionLabel>LMS</SectionLabel>
          <div className="space-y-3">
            <FormRow label="LMS host">
              <div className="flex gap-2">
                <Input
                  value={form.lms_host}
                  onChange={(e) => set('lms_host', e.target.value)}
                  placeholder="192.168.1.x"
                  className="flex-1"
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDiscoverLms}
                  disabled={discoveringLms}
                  type="button"
                >
                  {discoveringLms ? 'Scanning…' : 'Discover'}
                </Button>
              </div>
              {showLmsPicker && lmsServers.length > 0 && (
                <div className="mt-1 border border-border rounded-md divide-y divide-border">
                  {lmsServers.map((s) => (
                    <button
                      key={s.host}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-muted"
                      onClick={() => handlePickLms(s)}
                      type="button"
                    >
                      <span className="font-medium">{s.name}</span>{' '}
                      <span className="text-muted-foreground font-mono text-xs">{s.host}:{s.port}</span>
                    </button>
                  ))}
                </div>
              )}
              {showLmsPicker && lmsServers.length === 0 && (
                <p className="text-sm text-muted-foreground mt-1">No servers found.</p>
              )}
            </FormRow>
            <FormRow label="LMS port">
              <Input
                type="number"
                value={form.lms_port}
                onChange={(e) => set('lms_port', e.target.value)}
              />
            </FormRow>
            <FormRow label="Player name">
              <Input value={form.player_name} onChange={(e) => set('player_name', e.target.value)} />
            </FormRow>
            <FormRow label="Player MAC">
              {isEditing ? (
                <div>
                  <p className="text-sm font-mono text-muted-foreground py-2">{form.player_mac}</p>
                  <p className="text-xs text-muted-foreground">MAC cannot change after creation</p>
                </div>
              ) : (
                <Input
                  value={form.player_mac}
                  onChange={(e) => set('player_mac', e.target.value)}
                  placeholder="aa:bb:cc:dd:ee:ff (leave empty for random)"
                />
              )}
            </FormRow>
            <FormRow label="ALSA device">
              <Input
                value={form.alsa_device}
                onChange={(e) => set('alsa_device', e.target.value)}
                placeholder="leave empty for default"
              />
            </FormRow>
          </div>

          <SectionLabel>Hue</SectionLabel>
          <div className="space-y-3">
            <FormRow label="Bridge">
              <Select value={form.bridge_id} onValueChange={handleBridgeChange}>
                <SelectTrigger>
                  <SelectValue placeholder="Select bridge" />
                </SelectTrigger>
                <SelectContent>
                  {bridges.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name} — {b.host}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="Entertainment area">
              <Select
                value={form.entertainment_area_id}
                onValueChange={(v) => set('entertainment_area_id', v)}
                disabled={!form.bridge_id}
              >
                <SelectTrigger>
                  <SelectValue placeholder={form.bridge_id ? 'Select area' : 'Select bridge first'} />
                </SelectTrigger>
                <SelectContent>
                  {areas.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name} ({a.light_count} lights)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
          </div>

          <SectionLabel>Colour mapping</SectionLabel>
          <div className="space-y-3">
            <FormRow label="Colour mode">
              <Select value={form.color_mode} onValueChange={(v) => set('color_mode', v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="spectrum_rgb">Spectrum RGB</SelectItem>
                  <SelectItem value="mono_pulse">Mono pulse</SelectItem>
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="Bars">
              <Input
                type="number"
                min={10}
                max={100}
                value={form.bars}
                onChange={(e) => set('bars', e.target.value)}
              />
            </FormRow>
            <SliderField
              label="Sensitivity"
              value={form.sensitivity}
              min={0.1}
              max={3.0}
              step={0.1}
              format={(v) => v.toFixed(1)}
              onChange={(v) => set('sensitivity', v)}
            />
            <SliderField
              label="Brightness floor"
              value={form.brightness_floor}
              min={0.0}
              max={0.5}
              step={0.01}
              format={(v) => v.toFixed(2)}
              onChange={(v) => set('brightness_floor', v)}
            />
            <SliderField
              label="Exertion clip"
              value={form.exertion_clip}
              min={1.0}
              max={6.0}
              step={0.1}
              format={(v) => v.toFixed(1)}
              onChange={(v) => set('exertion_clip', v)}
            />
          </div>

          <SectionLabel>Onset detection</SectionLabel>
          <div className="space-y-3">
            <FormRow label="Method">
              <Select value={form.onset_method} onValueChange={(v) => set('onset_method', v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ONSET_METHODS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                {form.onset_method === 'multiband' && 'Per-band onset: bass/mid/treble detected independently at 100 Hz.'}
                {form.onset_method === 'superflux' && 'SuperFlux: max-filter suppresses vibrato false triggers (Böck & Widmer 2013).'}
                {form.onset_method === 'combined' && 'Full-spectrum spectral flux on cava bars at 30 Hz.'}
              </p>
            </FormRow>
            <SliderField
              label="Onset delta"
              value={form.onset_delta}
              min={0.01}
              max={0.5}
              step={0.01}
              format={(v) => v.toFixed(2)}
              onChange={(v) => set('onset_delta', v)}
            />
            <SliderField
              label="Onset alpha"
              value={form.onset_alpha}
              min={0.5}
              max={0.99}
              step={0.01}
              format={(v) => v.toFixed(2)}
              onChange={(v) => set('onset_alpha', v)}
            />
            {form.onset_method === 'superflux' && (
              <>
                <SliderField
                  label="SuperFlux mu (bins)"
                  value={form.superflux_mu}
                  min={1}
                  max={10}
                  step={1}
                  format={(v) => String(v)}
                  onChange={(v) => set('superflux_mu', v)}
                />
                <p className="text-xs text-muted-foreground -mt-1">
                  Max-filter half-width in FFT bins (±{form.superflux_mu} bins = ±{Math.round(form.superflux_mu * 44100 / 2048)} Hz).
                  Increase if vibrato still triggers false onsets.
                </p>
                <SliderField
                  label="SuperFlux lag (frames)"
                  value={form.superflux_lag}
                  min={1}
                  max={5}
                  step={1}
                  format={(v) => String(v)}
                  onChange={(v) => set('superflux_lag', v)}
                />
                <p className="text-xs text-muted-foreground -mt-1">
                  Compare frame n with frame n−{form.superflux_lag} ({Math.round(form.superflux_lag * 10)} ms look-back).
                </p>
              </>
            )}
          </div>

          <SectionLabel>Frequency cutoffs</SectionLabel>
          <div className="space-y-3">
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
            <p className="text-xs text-muted-foreground italic">
              Apply in Now Playing tab while music plays to hear the effect.
            </p>
          </div>

          <SectionLabel>Band boundaries (Hz)</SectionLabel>
          <div className="space-y-3">
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
            <p className="text-xs text-muted-foreground italic">
              Used by Spectrum RGB mode. Must be within the low/high cut range.
            </p>
          </div>
        </div>

        {error && <p className="text-destructive text-sm mt-2">{error}</p>}

        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
