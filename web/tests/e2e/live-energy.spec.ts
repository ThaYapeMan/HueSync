import { test, expect } from '@playwright/test'
import { setupPreview, geometry } from './preview-fixture'
import { readFileSync } from 'node:fs'
const baseline = JSON.parse(readFileSync('tests/e2e/fixtures/spectrum-baseline.json', 'utf8')) as Record<string, {x:number; y:number; width:number; height:number}>

test('live energy source patching and unchanged preview geometry', async ({ page }, info) => {
  const socket = await setupPreview(page)
  let profile = { id:'e', name:'Spectrum RGB', energy_source:'sustained', lufs_floor:-30, lufs_ceiling:-8, adaptation_tau_s:60 }
  const patches: unknown[] = []
  await page.route('**/api/energy-profiles/e', async route => {
    const body = route.request().postDataJSON()
    patches.push(body); profile = { ...profile, ...body }
    await new Promise(resolve => setTimeout(resolve, 100))
    return route.fulfill({json:profile})
  })
  await expect(page.getByTestId('live-energy-source').getByText('Spectrum RGB', {exact:true})).toBeVisible()
  const controls = page.getByTestId('live-energy-source')
  const radios = page.getByRole('radiogroup', {name:'Energy source'}).getByRole('radio')
  const groupBox = (await page.getByRole('radiogroup', {name:'Energy source'}).boundingBox())!
  expect(groupBox.width).toBeLessThan(420)
  expect(groupBox.height).toBeLessThanOrEqual(28)
  const widths = await Promise.all([0,1,2].map(async i => (await radios.nth(i).boundingBox())!.width))
  expect(Math.max(...widths)-Math.min(...widths)).toBeLessThanOrEqual(.02)
  const boxes = await geometry(page) as typeof baseline
  for (const key of ['track-block', 'colour-preview-size', 'floorplan-preview-size', 'status'] as const) expect(boxes[key]).toEqual(baseline[key])
  // The added control adds height below the blend. The later spectrum card
  // necessarily moves down by exactly that inserted height; its own size is fixed.
  const controlBox = (await controls.boundingBox())!
  expect(controlBox.height).toBeLessThan(105)
  const spectrumY = [boxes['spectrum-panel'].y]
  expect((await page.getByTestId('energy-parameters').boundingBox())!.height).toBe(28)
  expect(boxes['spectrum-panel'].x).toBe(baseline['spectrum-panel'].x)
  expect(boxes['spectrum-panel'].width).toBe(baseline['spectrum-panel'].width)
  expect(boxes['spectrum-panel'].height).toBe(baseline['spectrum-panel'].height)
  expect(boxes['spectrum-panel'].y - baseline['spectrum-panel'].y).toBe(controlBox.height + 12)
  const blend = (await page.getByTestId('energy-blend').boundingBox())!
  const session = (await page.getByTestId('session-diagnostics').boundingBox())!
  expect(controlBox.y).toBeGreaterThanOrEqual(blend.y+blend.height)
  expect(controlBox.y+controlBox.height).toBeLessThanOrEqual(session.y)
  await page.screenshot({path:info.outputPath('energy-sustained-1400.png'), fullPage:true})
  await page.getByRole('radio', {name:'Fixed loudness'}).click()
  await expect(controls.getByRole('spinbutton', {name:'Floor', exact:true})).toBeVisible()
  await expect(controls.getByRole('spinbutton', {name:'Ceiling', exact:true})).toBeVisible()
  expect(patches).toEqual([{energy_source:'loudness_fixed'}])
  await expect(controls.getByRole('spinbutton')).toHaveCount(2)
  spectrumY.push((await page.getByTestId('spectrum-panel').boundingBox())!.y)
  await controls.getByRole('button', {name:'Increase Floor'}).click()
  await expect.poll(() => patches.length).toBe(2)
  expect(patches[1]).toEqual({lufs_floor:-29})
  const floor = controls.getByRole('spinbutton', {name:'Floor', exact:true})
  await expect(floor).toBeEnabled()
  await floor.press('ArrowUp')
  await expect(floor).toHaveValue('-28')
  expect(patches).toHaveLength(2)
  await floor.press('Enter')
  await expect.poll(() => patches.length).toBe(3)
  expect(patches[2]).toEqual({lufs_floor:-28})
  await expect(floor).toBeEnabled()
  await floor.press('Shift+ArrowUp')
  await floor.press('Enter')
  await expect.poll(() => patches.length).toBe(4)
  expect(patches[3]).toEqual({lufs_floor:-23})
  await expect(floor).toBeEnabled()
  socket().send(JSON.stringify({type:'frame', colour:{r:0,g:0,b:0}, last_energy_input:.86, mix:.7, loudness_momentary_lufs:-11}))
  await expect(page.getByTestId('energy-input-marker')).toHaveAttribute('title','Energy input: 86%')
  await page.screenshot({path:info.outputPath('energy-fixed-1400.png'), fullPage:true})
  await page.getByRole('radio', {name:'Adaptive loudness'}).click()
  await expect(controls.getByRole('spinbutton')).toHaveCount(1)
  await expect(controls.getByRole('spinbutton', {name:'Adaptation', exact:true})).toBeVisible()
  spectrumY.push((await page.getByTestId('spectrum-panel').boundingBox())!.y)
  await page.screenshot({path:info.outputPath('energy-adaptive-1400.png'), fullPage:true})
  await page.getByRole('radio', {name:'Sustained'}).click()
  await expect(controls.getByRole('spinbutton')).toHaveCount(0)
  spectrumY.push((await page.getByTestId('spectrum-panel').boundingBox())!.y)
  expect(new Set(spectrumY).size).toBe(1)
  await expect(controls.getByText('No parameters for this source')).toBeVisible()
  const after = await geometry(page) as typeof baseline
  for (const key of ['track-block','colour-preview-size','floorplan-preview-size','status'] as const) expect(after[key]).toEqual(boxes[key])
  console.log('ENERGY_MEASUREMENTS',JSON.stringify({groupBox, widths, spectrumY, boxes, controlBox, blend, session, patches}))
  await page.getByRole('radio', {name:'Sustained'}).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('radio', {name:'Fixed loudness'})).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('radio', {name:'Fixed loudness'})).toBeFocused()
  await page.getByText('Open profile', {exact:true}).click()
  await expect(page.getByLabel('Energy source settings')).toBeVisible()
})

test('inactive energy control reserves the same compact rows', async ({page}, info) => {
  const socket = await setupPreview(page)
  const block = page.getByTestId('live-energy-source')
  const before = (await block.boundingBox())!
  const spectrumY = (await page.getByTestId('spectrum-panel').boundingBox())!.y
  socket().send(JSON.stringify({type:'status', active_coupling_id:null, active_energy_profile_id:null}))
  await expect(block.getByText('No active coupling')).toBeVisible()
  for (const radio of await block.getByRole('radio').all()) await expect(radio).toBeDisabled()
  expect((await block.boundingBox())!.height).toBe(before.height)
  expect((await page.getByTestId('spectrum-panel').boundingBox())!.y).toBe(spectrumY)
  await page.screenshot({path:info.outputPath('energy-inactive-1400.png'), fullPage:true})
})
