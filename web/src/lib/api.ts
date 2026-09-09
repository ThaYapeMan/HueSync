// Canonical list of onset detection methods accepted by the backend.
// sync_engine.py switches on these exact string values; any value not in this
// list silently falls through to cava-based onset (combined behaviour).
// Virtual-player source types.  Keep in sync with VIRTUAL_PLAYER_TYPES in models.py.
export const PLAYER_TYPES = [
  { value: 'LMS',     label: 'LMS (squeezelite)' },
  { value: 'AirPlay', label: 'AirPlay (shairport-sync)' },
] as const

export type PlayerType = typeof PLAYER_TYPES[number]['value']

export const ONSET_METHODS = [
  { value: 'combined',  label: 'Combined',  description: 'Default — beat detection across the full spectrum. Good for most music.' },
  { value: 'multiband', label: 'Multiband', description: 'Detects beats separately in bass, mid, and treble. More sensitive; can feel busier.' },
  { value: 'superflux', label: 'SuperFlux', description: 'Suppresses vibrato and false triggers. More precise on vocals and sustained notes.' },
] as const

export type OnsetMethod = typeof ONSET_METHODS[number]['value']

// Canonical list of colour modes — kept as deprecated alias.
// New code should use EFFECTS instead.
export const COLOUR_MODES = [
  { value: 'spectrum_rgb', label: 'Spectrum RGB' },
  { value: 'mono_pulse',   label: 'Mono Pulse' },
] as const

export type ColourMode = typeof COLOUR_MODES[number]['value']

// Canonical list of effects.  Keep in sync with EFFECT_IDS in models.py.
export const EFFECTS = [
  { id: 'spectrum_rgb', label: 'Spectrum RGB', description: 'Bass drives red, mid drives green, treble drives blue',       hasSpeed: false, hasDecay: false },
  { id: 'mono_pulse',   label: 'Mono Pulse',   description: 'All lights dim and brighten with the overall energy',         hasSpeed: false, hasDecay: false },
  { id: 'pulses',       label: 'Pulses',       description: 'Lights flash on each beat and fade out smoothly',             hasSpeed: false, hasDecay: true  },
  { id: 'flashes',      label: 'Flashes',      description: 'Hard white flash on each beat, dark between beats',           hasSpeed: false, hasDecay: true  },
  { id: 'splotches',    label: 'Splotches',    description: 'Random individual lights flare up on each beat',              hasSpeed: false, hasDecay: true  },
  { id: 'fireworks',    label: 'Fireworks',    description: 'Colour burst expands outward from a point on each beat',      hasSpeed: true,  hasDecay: true  },
  { id: 'swirl',        label: 'Swirl',        description: 'Colour gradient rotates continuously across the lights',      hasSpeed: true,  hasDecay: false },
  { id: 'wave',         label: 'Wave',         description: 'Colour wave sweeps across the room in time with the beat',    hasSpeed: true,  hasDecay: false },
  { id: 'solid',        label: 'Solid',        description: 'Single steady colour that shifts slowly with the music',      hasSpeed: false, hasDecay: false },
  { id: 'none',         label: 'None',         description: 'Lights off — no output sent to this zone',                   hasSpeed: false, hasDecay: false },
] as const

export type EffectId = typeof EFFECTS[number]['id']

export interface EntertainmentArea {
  id: string
  name: string
  light_count: number
}

export interface PlayerLatency {
  player_mac: string
  name: string | null
  strategy: string
  fixed_delay_ms: number
  speaker_ip: string | null
}

export interface LmsServer {
  host: string
  name: string
  port: number
}

export interface ApiStatus {
  version: string
  active_coupling_id: string | null
  active_coupling_name: string | null
  active_player_type: string | null
  sync_master: string | null
  sync_master_name: string | null
  applied_delay_ms: number
  latency_warning: string | null
  processes: { squeezelite: boolean; cava: boolean }
  bridge_connected: boolean
  airplay_receiving: boolean | null
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let message = `${res.status} ${res.statusText}`
    try {
      const parsed = JSON.parse(text)
      if (parsed.detail) message = String(parsed.detail)
    } catch { /* keep default */ }
    const err = new Error(message)
    ;(err as any).status = res.status
    throw err
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function json(method: string, body: unknown, options?: RequestInit): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...options,
  }
}

// Controllers
export const pairController = (host: string, name?: string) =>
  request<Controller>('/api/controllers/pair', json('POST', { host, name }))
export const getControllerAreas = (controllerId: string) =>
  request<EntertainmentArea[]>(`/api/controllers/${controllerId}/areas`)

export const restartCouplingCava = (
  id: string,
  body: { lower_cutoff_freq?: number; higher_cutoff_freq?: number; bass_hz?: number; mid_hz?: number } = {}
) => request<{ ok: true }>(`/api/couplings/${id}/restart-cava`, json('POST', body))

// Player latencies
export const getPlayerLatencies = () => request<PlayerLatency[]>('/api/player-latencies')
export const createPlayerLatency = (body: {
  player_mac: string
  name?: string
  strategy?: string
  fixed_delay_ms?: number
}) => request<PlayerLatency>('/api/player-latencies', json('POST', body))
export const updatePlayerLatency = (
  mac: string,
  body: Partial<{ name: string; strategy: string; fixed_delay_ms: number }>
) => request<PlayerLatency>(`/api/player-latencies/${mac}`, json('PATCH', body))
export const deletePlayerLatency = (mac: string) =>
  request<void>(`/api/player-latencies/${mac}`, { method: 'DELETE' })

// LMS
export const discoverLms = () => request<LmsServer[]>('/api/lms/discover')

// Status
export const getStatus = () => request<ApiStatus>('/api/status')

export interface Controller {
  id: string
  name: string
  host: string
  app_key: string
  client_key: string
}

export interface VirtualPlayer {
  id: string
  type: string
  lms_host: string
  lms_port: number
  player_name: string
  display_name: string
  player_mac: string
  alsa_device: string
  follow_player_mac: string
}

export interface LmsPlayer {
  playerid: string
  name: string
}

export interface Zone {
  id: string
  name: string
  controller_id: string
  entertainment_area_id: string
  entertainment_area_name: string
  light_count: number
}

export interface ChannelPosition {
  channel_id: number
  x: number
  y: number
  z: number
}

export interface Analyser {
  id: string
  name: string
  onset_method: string
  onset_delta: number
  onset_alpha: number
  superflux_mu: number
  superflux_lag: number
  bars: number
  lower_cutoff_freq: number
  higher_cutoff_freq: number
  use_hpss_separation: boolean
}

export interface Effect {
  id: string
  name: string
  effect_type: string
  effect_speed: number
  effect_decay: number
  sensitivity: number
  brightness_floor: number
  bass_hz: number
  mid_hz: number
  exertion_clip: number
  onset_flash_intensity: number
}

export interface EnergyProfile {
  id: string
  name: string
  high_energy_effect_id: string
  low_energy_effect_id: string
  blend_start: number
  blend_end: number
  blend_response: number
}

export interface Coupling {
  id: string
  name: string
  player_id: string
  analyser_id: string
  zone_id: string
  energy_profile_id: string
  enabled: boolean
}

// Controllers
export const getControllers = () => request<Controller[]>('/api/controllers')
export const deleteController = (id: string) => request<void>(`/api/controllers/${id}`, { method: 'DELETE' })

// VirtualPlayers
export const getVirtualPlayers = () => request<VirtualPlayer[]>('/api/virtual-players')
export const createVirtualPlayer = (body: Omit<VirtualPlayer, 'id' | 'player_mac'>) =>
  request<VirtualPlayer>('/api/virtual-players', json('POST', body))
export const updateVirtualPlayer = (id: string, body: Partial<Omit<VirtualPlayer, 'id'>>) =>
  request<VirtualPlayer>(`/api/virtual-players/${id}`, json('PATCH', body))
export const deleteVirtualPlayer = (id: string) => request<void>(`/api/virtual-players/${id}`, { method: 'DELETE' })
export const listLmsPlayers = (host: string) =>
  request<LmsPlayer[]>(`/api/lms/players?host=${encodeURIComponent(host)}`)

// Zones
export const getZones = () => request<Zone[]>('/api/zones')
export const createZone = (body: Omit<Zone, 'id'>) =>
  request<Zone>('/api/zones', json('POST', body))
export const updateZone = (id: string, body: Partial<Omit<Zone, 'id'>>) =>
  request<Zone>(`/api/zones/${id}`, json('PATCH', body))
export const deleteZone = (id: string) =>
  request<void>(`/api/zones/${id}`, { method: 'DELETE' })
export const getZoneChannels = (id: string) =>
  request<ChannelPosition[]>(`/api/zones/${id}/channels`)

// Analysers
export const getAnalysers = () => request<Analyser[]>('/api/analysers')
export const createAnalyser = (body: Omit<Analyser, 'id'>) =>
  request<Analyser>('/api/analysers', json('POST', body))
export const updateAnalyser = (id: string, body: Partial<Omit<Analyser, 'id'>>) =>
  request<Analyser>(`/api/analysers/${id}`, json('PATCH', body))
export const deleteAnalyser = (id: string) =>
  request<void>(`/api/analysers/${id}`, { method: 'DELETE' })

// Effects
export const getEffects = () => request<Effect[]>('/api/effects')
export const createEffect = (body: Omit<Effect, 'id'>) =>
  request<Effect>('/api/effects', json('POST', body))
export const updateEffect = (id: string, body: Partial<Omit<Effect, 'id'>>) =>
  request<Effect>(`/api/effects/${id}`, json('PATCH', body))
export const deleteEffect = (id: string) =>
  request<void>(`/api/effects/${id}`, { method: 'DELETE' })

// EnergyProfiles
export const getEnergyProfiles = () => request<EnergyProfile[]>('/api/energy-profiles')
export const createEnergyProfile = (body: Omit<EnergyProfile, 'id'>) =>
  request<EnergyProfile>('/api/energy-profiles', json('POST', body))
export const updateEnergyProfile = (id: string, body: Partial<Omit<EnergyProfile, 'id'>>) =>
  request<EnergyProfile>(`/api/energy-profiles/${id}`, json('PATCH', body))
export const deleteEnergyProfile = (id: string) =>
  request<void>(`/api/energy-profiles/${id}`, { method: 'DELETE' })

// Couplings
export const getCouplings = () => request<Coupling[]>('/api/couplings')
export const createCoupling = (body: Omit<Coupling, 'id'>) =>
  request<Coupling>('/api/couplings', json('POST', body))
export const updateCoupling = (id: string, body: Partial<Omit<Coupling, 'id'>>) =>
  request<Coupling>(`/api/couplings/${id}`, json('PATCH', body))
export const deleteCoupling = (id: string) =>
  request<void>(`/api/couplings/${id}`, { method: 'DELETE' })
export const activateCoupling = (id: string) =>
  request<{ active_id: string; warnings: string[] }>(`/api/couplings/${id}/activate`, { method: 'POST' })
export const deactivateCoupling = () =>
  request<{ active_id: null }>('/api/couplings/deactivate', { method: 'POST' })
export const cloneCoupling = (id: string) =>
  request<Coupling>(`/api/couplings/${id}/clone`, { method: 'POST' })
