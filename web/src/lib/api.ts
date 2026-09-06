// Canonical list of onset detection methods accepted by the backend.
// sync_engine.py switches on these exact string values; any value not in this
// list silently falls through to cava-based onset (combined behaviour).
export const ONSET_METHODS = [
  { value: 'combined',  label: 'Combined (cava, 30 Hz)' },
  { value: 'multiband', label: 'Multiband (PCM tap, 100 Hz)' },
  { value: 'superflux', label: 'SuperFlux (PCM tap, 100 Hz)' },
] as const

export type OnsetMethod = typeof ONSET_METHODS[number]['value']

// Canonical list of colour modes.  Derived from models.ColorMode in the backend;
// any value not in this list raises 422 on POST and 500 on PATCH (until the
// PATCH guard was added).  Keep in sync with COLOUR_MODES in models.py.
export const COLOUR_MODES = [
  { value: 'spectrum_rgb', label: 'Spectrum RGB' },
  { value: 'mono_pulse',   label: 'Mono Pulse' },
] as const

export type ColourMode = typeof COLOUR_MODES[number]['value']

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
  sync_master: string | null
  sync_master_name: string | null
  applied_delay_ms: number
  latency_warning: string | null
  processes: { squeezelite: boolean; cava: boolean }
  bridge_connected: boolean
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ': ' + body : ''}`)
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
  name: string
  lms_host: string
  lms_port: number
  player_name: string
  player_mac: string
  alsa_device: string
}

export interface LightProvider {
  id: string
  name: string
  controller_id: string
  entertainment_area_id: string
  entertainment_area_name: string
  light_count: number
}

export interface AnalysisConfig {
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
}

export interface RenderConfig {
  id: string
  name: string
  color_mode: string
  sensitivity: number
  brightness_floor: number
  bass_hz: number
  mid_hz: number
  exertion_clip: number
  onset_flash_intensity: number
}

export interface Coupling {
  id: string
  name: string
  player_id: string
  analysis_config_id: string
  light_provider_id: string
  render_config_id: string
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

// LightProviders
export const getLightProviders = () => request<LightProvider[]>('/api/light-providers')
export const createLightProvider = (body: Omit<LightProvider, 'id'>) =>
  request<LightProvider>('/api/light-providers', json('POST', body))
export const deleteLightProvider = (id: string) =>
  request<void>(`/api/light-providers/${id}`, { method: 'DELETE' })

// AnalysisConfigs
export const getAnalysisConfigs = () => request<AnalysisConfig[]>('/api/analysis-configs')
export const createAnalysisConfig = (body: Omit<AnalysisConfig, 'id'>) =>
  request<AnalysisConfig>('/api/analysis-configs', json('POST', body))
export const updateAnalysisConfig = (id: string, body: Partial<Omit<AnalysisConfig, 'id'>>) =>
  request<AnalysisConfig>(`/api/analysis-configs/${id}`, json('PATCH', body))
export const deleteAnalysisConfig = (id: string) =>
  request<void>(`/api/analysis-configs/${id}`, { method: 'DELETE' })

// RenderConfigs
export const getRenderConfigs = () => request<RenderConfig[]>('/api/render-configs')
export const createRenderConfig = (body: Omit<RenderConfig, 'id'>) =>
  request<RenderConfig>('/api/render-configs', json('POST', body))
export const updateRenderConfig = (id: string, body: Partial<Omit<RenderConfig, 'id'>>) =>
  request<RenderConfig>(`/api/render-configs/${id}`, json('PATCH', body))
export const deleteRenderConfig = (id: string) =>
  request<void>(`/api/render-configs/${id}`, { method: 'DELETE' })

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
