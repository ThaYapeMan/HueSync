import { test, expect } from '@playwright/test'
import type { WebSocketRoute } from '@playwright/test'

test('player bar geometry and tap/hold controls at 1400px', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1400, height: 1100 })
  await page.clock.install()
  const actions: string[] = []
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/transport')) {
      actions.push(route.request().postDataJSON().action)
      return route.fulfill({ json: { ok: true, target_mac: 'aa:bb:cc:dd:ee:ff' } })
    }
    const data: Record<string, unknown> = {
      '/api/couplings': [{ id: 'c', name: 'Room', analyser_id: 'a', player_id: 'p' }],
      '/api/analysers': [{ id: 'a', bars_source: 'pcm_pipeline', spectrum_backend: 'v2', onset_method: 'combined' }],
      '/api/virtual-players': [{ id: 'p', type: 'LMS' }],
      '/api/zones/z/channels': [{ channel_id: 1, x: .5, y: 0, z: .5 }],
    }
    return route.fulfill({ json: data[path] ?? [] })
  })
  let socket: WebSocketRoute
  const status = (playing: boolean, extra = {}) => ({ type: 'status',
    active_coupling_id: 'c', active_player_type: 'LMS', active_zone_id: 'z',
    active_bars_source: 'pcm_pipeline', effect_type: 'spectrum_rgb',
    follow_target_mac: 'aa:bb:cc:dd:ee:ff', follow_target_name: 'Room',
    processes: { squeezelite: true, cava: false }, applied_delay_ms: 1100,
    sync_master: 'aa:bb:cc:dd:ee:ff', sync_master_name: 'Room',
    track: { title: 'Jealous (Extended Mix)', artist: 'Mochakk', position_s: 26, duration_s: 345, playing },
    ...extra,
  })
  await page.routeWebSocket('**/ws/preview', ws => { socket = ws; ws.send(JSON.stringify(status(true))) })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeVisible()
  await page.clock.pauseAt(new Date(Date.now() + 1000))
  socket!.send(JSON.stringify(status(true)))
  const bar = page.getByTestId('track-block')
  const box = async (id: string) => (await page.getByTestId(id).boundingBox())!
  const measure = async () => ({
    bar: (await bar.boundingBox())!,
    controls: await Promise.all(['previous', 'toggle', 'next'].map(action => box(`transport-${action}`))),
    glyph: await box('glyph-toggle'),
  })
  const playing = await measure()
  const active = await page.getByRole('heading', { name: 'Active coupling', exact: true })
    .locator('..').locator('..').boundingBox()
  const live = await page.getByRole('heading', { name: 'Live preview', exact: true })
    .locator('..').locator('..').boundingBox()
  expect(Math.abs(playing.bar.width - active!.width)).toBeLessThanOrEqual(1)
  expect(Math.abs(playing.bar.width - live!.width)).toBeLessThanOrEqual(1)
  expect(playing.bar.height).toBeLessThan(130)
  await page.screenshot({ path: testInfo.outputPath('player-bar-playing-1400.png'), fullPage: true })
  await bar.screenshot({ path: testInfo.outputPath('player-bar-crop.png') })
  socket!.send(JSON.stringify(status(false)))
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeVisible()
  const paused = await measure()
  for (let i = 0; i < 3; i++) {
    expect(paused.controls[i].x).toBe(playing.controls[i].x)
    expect(paused.controls[i].width).toBe(playing.controls[i].width)
  }
  expect(paused.glyph.width).toBe(playing.glyph.width)
  expect(paused.glyph.height).toBe(playing.glyph.height)
  expect(Math.abs(paused.controls[1].x + paused.controls[1].width / 2 -
    (paused.bar.x + paused.bar.width / 2))).toBeLessThanOrEqual(1)
  await page.screenshot({ path: testInfo.outputPath('player-bar-paused-1400.png'), fullPage: true })
  console.log('PLAYER_BAR_MEASUREMENTS', JSON.stringify({ playing, paused, active, live }))

  const next = page.getByRole('button', { name: 'Next', exact: true })
  await next.hover()
  await page.mouse.down()
  await page.clock.runFor(399)
  expect(actions).toEqual([])
  await page.clock.runFor(1)
  await expect.poll(() => actions).toEqual(['seek_forward'])
  await page.clock.runFor(250)
  await expect.poll(() => actions).toEqual(['seek_forward', 'seek_forward'])
  await page.clock.runFor(250)
  await expect.poll(() => actions).toEqual(['seek_forward', 'seek_forward', 'seek_forward'])
  await page.mouse.up()
  await page.clock.runFor(1000)
  expect(actions).toEqual(['seek_forward', 'seek_forward', 'seek_forward'])
  actions.length = 0
  await page.mouse.down()
  await page.clock.runFor(100)
  await page.mouse.up()
  await expect.poll(() => actions).toEqual(['next'])
  await page.clock.runFor(500)
  expect(actions).toEqual(['next'])

  socket!.send(JSON.stringify(status(false, { track: null })))
  await expect(next).toBeDisabled()
  const noTrack = await measure()
  expect(noTrack.controls).toEqual(paused.controls)
  socket!.send(JSON.stringify(status(false, { active_player_type: 'AirPlay' })))
  await expect(page.getByTestId('track-controls')).toHaveCount(0)
  expect((await bar.boundingBox())!.width).toBe(playing.bar.width)
})
