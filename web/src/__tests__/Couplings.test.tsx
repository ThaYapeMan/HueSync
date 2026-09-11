import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Couplings } from '../pages/Couplings'

// ── API mock ──────────────────────────────────────────────────────────────────

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    getCouplings: vi.fn(),
    getVirtualPlayers: vi.fn(),
    getZones: vi.fn(),
    getAnalysers: vi.fn(),
    getEffects: vi.fn(),
    getEnergyProfiles: vi.fn(),
    getControllers: vi.fn(),
    getStatus: vi.fn(),
    listLmsPlayers: vi.fn(),
    activateCoupling: vi.fn(),
    deactivateCoupling: vi.fn(),
    createCoupling: vi.fn(),
    updateCoupling: vi.fn().mockResolvedValue({ id: 'c1', name: 'Test', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }),
    deleteCoupling: vi.fn(),
    cloneCoupling: vi.fn(),
  }
})

// ── fixture factory ───────────────────────────────────────────────────────────

import * as api from '../lib/api'
const mapi = api as {
  getCouplings: ReturnType<typeof vi.fn>
  getVirtualPlayers: ReturnType<typeof vi.fn>
  getZones: ReturnType<typeof vi.fn>
  getAnalysers: ReturnType<typeof vi.fn>
  getEffects: ReturnType<typeof vi.fn>
  getEnergyProfiles: ReturnType<typeof vi.fn>
  getControllers: ReturnType<typeof vi.fn>
  getStatus: ReturnType<typeof vi.fn>
  listLmsPlayers: ReturnType<typeof vi.fn>
}

const BASE_STATUS = { active_coupling_id: null, active_coupling_name: null, active_player_type: null, sync_master: null, sync_master_name: null, applied_delay_ms: 0, latency_warning: null, processes: { squeezelite: false, cava: false }, bridge_connected: false, airplay_receiving: null, version: '0.1' }
const BASE_ANALYSER = { id: 'a1', name: 'Multiband', onset_method: 'multiband', onset_delta: 0.1, onset_alpha: 0.1, superflux_mu: 1, superflux_lag: 2, bars: 30, lower_cutoff_freq: 20, higher_cutoff_freq: 20000, use_hpss_separation: false, bars_source: 'cava' }
const BASE_CONTROLLER = { id: 'c1', name: 'Living Room Bridge', type: 'hue', host: '192.168.1.50', app_key_configured: true, client_key_configured: true }
const BASE_ZONE = { id: 'z1', name: 'Zitkamer AE', controller_id: 'c1', entertainment_area_id: 'ea1', entertainment_area_name: 'Zitkamer AE', light_count: 2 }

function setupDefaultMocks({
  players = [] as any[],
  zones = [BASE_ZONE],
  analysers = [BASE_ANALYSER],
  effects = [] as any[],
  energyProfiles = [] as any[],
  controllers = [BASE_CONTROLLER],
  couplings = [] as any[],
  listLmsPlayersResult = [] as any[],
} = {}) {
  mapi.getCouplings.mockResolvedValue(couplings)
  mapi.getVirtualPlayers.mockResolvedValue(players)
  mapi.getZones.mockResolvedValue(zones)
  mapi.getAnalysers.mockResolvedValue(analysers)
  mapi.getEffects.mockResolvedValue(effects)
  mapi.getEnergyProfiles.mockResolvedValue(energyProfiles)
  mapi.getControllers.mockResolvedValue(controllers)
  mapi.getStatus.mockResolvedValue(BASE_STATUS)
  mapi.listLmsPlayers.mockResolvedValue(listLmsPlayersResult)
}

async function renderAndSelectCoupling(couplingId: string) {
  const user = userEvent.setup()
  render(<Couplings activeCouplingId={null} onActivationChange={vi.fn()} />)
  const item = await screen.findByTestId(`coupling-item-${couplingId}`)
  await user.click(item)
  return user
}

// ── Effect routing — different effects ───────────────────────────────────────

describe('Effect routing — different effects', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders two separate Effect nodes when low != high', async () => {
    const lowFx = { id: 'e1', name: 'Swirl', effect_type: 'swirl', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const highFx = { id: 'e2', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const ep = { id: 'ep1', name: 'Test Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e2', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }

    setupDefaultMocks({ players: [player], effects: [lowFx, highFx], energyProfiles: [ep], couplings: [coupling] })

    await renderAndSelectCoupling('c1')

    const lowNode = await screen.findByTestId('node-effect-low')
    const highNode = screen.getByTestId('node-effect-high')

    expect(lowNode).toBeDefined()
    expect(highNode).toBeDefined()
    expect(within(lowNode).getByText(/low energy effect/i)).toBeDefined()
    expect(within(highNode).getByText(/high energy effect/i)).toBeDefined()
    expect(within(lowNode).getByText('Swirl')).toBeDefined()
    expect(within(highNode).getByText('Spectrum RGB')).toBeDefined()
    expect(screen.queryByTestId('node-effect')).toBeNull()
  })

  it('both Effect nodes have EffectPreview', async () => {
    const lowFx = { id: 'e1', name: 'Swirl', effect_type: 'swirl', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const highFx = { id: 'e2', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const ep = { id: 'ep1', name: 'Test Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e2', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }

    setupDefaultMocks({ players: [player], effects: [lowFx, highFx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    const previews = await screen.findAllByTestId('effect-preview')
    expect(previews.length).toBeGreaterThanOrEqual(2)
  })
})

// ── Effect routing — same effect ──────────────────────────────────────────────

describe('Effect routing — same effect (low == high by ID)', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders exactly one Effect node when low_id === high_id', async () => {
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const ep = { id: 'ep1', name: 'Same Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }

    setupDefaultMocks({ players: [player], effects: [fx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    const singleNode = await screen.findByTestId('node-effect')
    expect(singleNode).toBeDefined()
    expect(screen.queryByTestId('node-effect-low')).toBeNull()
    expect(screen.queryByTestId('node-effect-high')).toBeNull()
  })

  it('Effect not duplicated when same ID', async () => {
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const ep = { id: 'ep1', name: 'Same Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }

    setupDefaultMocks({ players: [player], effects: [fx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    await screen.findByTestId('node-effect')
    // Only one occurrence of the effect name
    const _nameMatches = screen.getAllByText('Spectrum RGB')
    // The EP name "Same Profile" and the effect name should each appear once (effect name appears once in the node)
    // There should not be two effect node containers
    expect(screen.queryAllByTestId('node-effect').length).toBe(1)
  })
})

// ── Effect equality by ID not name ────────────────────────────────────────────

describe('Effect equality — by ID, not name', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders two nodes when IDs differ even if names are identical', async () => {
    const fx1 = { id: 'e1', name: 'Same Name', effect_type: 'swirl', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const fx2 = { id: 'e2', name: 'Same Name', effect_type: 'swirl', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }
    const ep = { id: 'ep1', name: 'Name-Equal Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e2', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }

    setupDefaultMocks({ players: [player], effects: [fx1, fx2], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    await screen.findByTestId('node-effect-low')
    expect(screen.getByTestId('node-effect-high')).toBeDefined()
    expect(screen.queryByTestId('node-effect')).toBeNull()
  })
})

// ── VirtualPlayer type semantics — LMS ───────────────────────────────────────

describe('VirtualPlayer — LMS type', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows LMS and Follow Player label with resolved name', async () => {
    const player = { id: 'p1', type: 'LMS', lms_host: '192.168.1.100', lms_port: 9000, player_name: 'HueSync LMS', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: 'aa:bb:cc:dd:ee:ff' }
    const coupling = { id: 'c1', name: 'LMS Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({
      players: [player],
      effects: [fx],
      energyProfiles: [ep],
      couplings: [coupling],
      listLmsPlayersResult: [{ playerid: 'aa:bb:cc:dd:ee:ff', name: 'Study (Sonos)' }],
    })

    await renderAndSelectCoupling('c1')
    const playerNode = await screen.findByTestId('node-virtual-player')

    await waitFor(() => {
      expect(within(playerNode).getByText(/Follow Player.*Study \(Sonos\)/i)).toBeDefined()
    })
    expect(within(playerNode).queryByText(/Advertised Player/i)).toBeNull()
  })

  it('does not show Advertised Player for LMS', async () => {
    const player = { id: 'p1', type: 'LMS', lms_host: '192.168.1.100', lms_port: 9000, player_name: 'HueSync LMS', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: 'aa:bb:cc:dd:ee:ff' }
    const coupling = { id: 'c1', name: 'LMS Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({
      players: [player],
      effects: [fx],
      energyProfiles: [ep],
      couplings: [coupling],
      listLmsPlayersResult: [{ playerid: 'aa:bb:cc:dd:ee:ff', name: 'Study (Sonos)' }],
    })

    await renderAndSelectCoupling('c1')
    const playerNode = await screen.findByTestId('node-virtual-player')
    expect(within(playerNode).queryByText(/Advertised Player/i)).toBeNull()
  })
})

// ── VirtualPlayer type semantics — AirPlay ────────────────────────────────────

describe('VirtualPlayer — AirPlay type', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows AirPlay and Advertised Player label', async () => {
    const player = { id: 'p1', type: 'AirPlay', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: 'HueSync', player_mac: '', alsa_device: '', follow_player_mac: '' }
    const coupling = { id: 'c1', name: 'AirPlay Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({ players: [player], effects: [fx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    const playerNode = await screen.findByTestId('node-virtual-player')
    expect(within(playerNode).getByText(/Advertised Player.*HueSync/i)).toBeDefined()
    expect(within(playerNode).queryByText(/Follow Player/i)).toBeNull()
  })

  it('does not show Follow Player for AirPlay', async () => {
    const player = { id: 'p1', type: 'AirPlay', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: 'HueSync', player_mac: '', alsa_device: '', follow_player_mac: '' }
    const coupling = { id: 'c1', name: 'AirPlay Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({ players: [player], effects: [fx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    const playerNode = await screen.findByTestId('node-virtual-player')
    expect(within(playerNode).queryByText(/Follow Player/i)).toBeNull()
  })
})

// ── Zone — controller type ────────────────────────────────────────────────────

describe('Zone — controller type', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows Hue · 2 lights for a Hue-backed zone', async () => {
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({ players: [player], effects: [fx], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    const zoneNode = await screen.findByTestId('node-zone')
    expect(within(zoneNode).getByText('Zitkamer AE')).toBeDefined()
    expect(within(zoneNode).getByText(/Hue · 2 lights/i)).toBeDefined()
  })

  it('uses resolved controller type not hardcoded Hue', async () => {
    // If a controller with type 'wled' existed, it should show 'Wled'
    const altController = { id: 'c2', name: 'WLED Strip', type: 'wled', host: '10.0.0.2', app_key_configured: false, client_key_configured: false }
    const altZone = { id: 'z2', name: 'Kitchen Strip', controller_id: 'c2', entertainment_area_id: 'ea2', entertainment_area_name: 'Kitchen Strip', light_count: 5 }
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z2', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({ players: [player], zones: [altZone], effects: [fx], energyProfiles: [ep], couplings: [coupling], controllers: [altController] })
    await renderAndSelectCoupling('c1')

    const zoneNode = await screen.findByTestId('node-zone')
    expect(within(zoneNode).getByText(/Wled · 5 lights/i)).toBeDefined()
  })
})

// ── Missing references — no crash ────────────────────────────────────────────

describe('Missing references — graceful fallback', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders without crash when effects are missing', async () => {
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'missing1', high_energy_effect_id: 'missing2', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: '', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }

    setupDefaultMocks({ effects: [], energyProfiles: [ep], couplings: [coupling] })
    await renderAndSelectCoupling('c1')

    // Should not throw — "Not configured" placeholders appear
    await screen.findByTestId('node-effect-low')
    const lowNode = screen.getByTestId('node-effect-low')
    expect(within(lowNode).getByText(/Not configured/i)).toBeDefined()
  })

  it('renders without crash when zone controller is missing', async () => {
    const player = { id: 'p1', type: 'LMS', lms_host: '', lms_port: 9000, player_name: 'HueSync', display_name: '', player_mac: '', alsa_device: '', follow_player_mac: '' }
    const zoneNoCtrl = { id: 'z1', name: 'Orphan Zone', controller_id: 'nonexistent', entertainment_area_id: 'ea1', entertainment_area_name: 'Orphan Zone', light_count: 3 }
    const coupling = { id: 'c1', name: 'Test Coupling', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true }
    const ep = { id: 'ep1', name: 'Profile', low_energy_effect_id: 'e1', high_energy_effect_id: 'e1', blend_start: 0.3, blend_end: 0.7, blend_response: 1 }
    const fx = { id: 'e1', name: 'Spectrum RGB', effect_type: 'spectrum_rgb', effect_speed: 1, effect_decay: 0.3, sensitivity: 1, brightness_floor: 0, bass_hz: 250, mid_hz: 2000, exertion_clip: 3, onset_flash_intensity: 0 }

    setupDefaultMocks({ players: [player], zones: [zoneNoCtrl], effects: [fx], energyProfiles: [ep], couplings: [coupling], controllers: [] })
    await renderAndSelectCoupling('c1')

    const zoneNode = await screen.findByTestId('node-zone')
    // Falls back to just showing light count without controller type prefix
    expect(within(zoneNode).getByText('Orphan Zone')).toBeDefined()
    expect(within(zoneNode).getByText(/3 lights/i)).toBeDefined()
  })
})
