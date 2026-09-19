import { expect } from '@playwright/test'
import type { Page, WebSocketRoute } from '@playwright/test'

export async function setupPreview(page: Page) {
  await page.setViewportSize({ width: 1400, height: 1100 })
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const data: Record<string, unknown> = {
      '/api/couplings': [{ id: 'c', name: 'Room', analyser_id: 'a', player_id: 'p', energy_profile_id: 'e' }],
      '/api/analysers': [{ id: 'a', bars_source: 'pcm_pipeline', spectrum_backend: 'v2', onset_method: 'combined', band_normalise: true }],
      '/api/virtual-players': [{ id: 'p', type: 'LMS' }],
      '/api/energy-profiles': [{ id: 'e', name: 'Spectrum RGB', energy_source: 'sustained', lufs_floor: -30, lufs_ceiling: -8, adaptation_tau_s: 60 }],
      '/api/zones/z/channels': [{ channel_id: 1, x: .5, y: 0, z: .5 }],
    }
    return route.fulfill({ json: data[path] ?? [] })
  })
  let socket: WebSocketRoute
  await page.routeWebSocket('**/ws/preview', ws => {
    socket = ws
    ws.send(JSON.stringify({ type: 'status', active_coupling_id: 'c', active_energy_profile_id: 'e',
      active_player_type: 'LMS', active_zone_id: 'z', active_bars_source: 'pcm_pipeline',
      effect_type: 'spectrum_rgb', processes: { squeezelite: true, cava: false }, applied_delay_ms: 1100,
      follow_target_mac: 'aa:bb:cc:dd:ee:ff', follow_target_name: 'Room',
      track: { title: 'Jealous (Extended Mix)', artist: 'Mochakk', position_s: 26, duration_s: 345, playing: true } }))
    ws.send(JSON.stringify({ type: 'spectrum', bars: [.6,.6,.2,.2,.2,.2,.4,.4,.4,.4], normalised_bars: Array(10).fill(.33) }))
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeVisible()
  return () => socket
}

export async function geometry(page: Page) {
  const result: Record<string, unknown> = {}
  for (const id of ['track-block', 'colour-preview-size', 'floorplan-preview-size', 'spectrum-panel']) {
    result[id] = await page.getByTestId(id).boundingBox()
  }
  result.status = await page.getByLabel('Session status').boundingBox()
  return result
}

