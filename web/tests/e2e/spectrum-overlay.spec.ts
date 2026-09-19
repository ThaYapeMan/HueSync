import { test, expect } from '@playwright/test'
import { setupPreview, geometry } from './preview-fixture'
import { writeFileSync } from 'node:fs'

for (const theme of ['dark', 'light']) {
  test(`spectrum overlay adjacent contrast and geometry ${theme}`, async ({ page }, info) => {
    // The app currently ships dark tokens only. Exercise a light card surface
    // explicitly, rather than claiming there is an existing light-theme switch.
    if (theme === 'light') await page.addInitScript(() => {
      document.addEventListener('DOMContentLoaded', () => {
        for (const [key, value] of Object.entries({ card: '0 0% 100%', background: '0 0% 96%', foreground: '222 13% 10%', 'card-foreground': '222 13% 10%', 'muted-foreground': '220 10% 40%' })) document.documentElement.style.setProperty(`--${key}`, value)
      })
    })
    const socket = await setupPreview(page)
    await expect(page.getByTestId('spectrum-legend')).toBeVisible()
    const raw = page.getByTestId('spectrum-raw'), outline = page.getByTestId('spectrum-outline')
    for (const [index, higher] of [[0, false], [2, true]] as const) {
      const a = (await raw.nth(index).boundingBox())!, b = (await outline.nth(index).boundingBox())!
      expect(b.x).toBe(a.x); expect(b.width).toBe(a.width)
      expect(b.height > a.height).toBe(higher)
      await expect(outline.nth(index)).toBeVisible()
    }
    const ratios = await outline.evaluateAll(elements => {
      const rgb = (s: string) => s.match(/[\d.]+/g)!.slice(0, 3).map(Number)
      const lum = (c: number[]) => c.map(v => v/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4).reduce((a,v,i) => a+v*[.2126,.7152,.0722][i],0)
      const ratio = (a:number[],b:number[]) => (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05)
      return elements.map(el => {
        const s = getComputedStyle(el.children[1]), haloStyle = getComputedStyle(el.children[0]), fillStyle = getComputedStyle(el.previousElementSibling!)
        const surface = rgb(getComputedStyle(el.closest('[role="img"]')!).backgroundColor)
        const opacity = Number(fillStyle.opacity), band = rgb(fillStyle.backgroundColor)
        const fill = band.map((v,i) => v*opacity + surface[i]*(1-opacity))
        const halo = rgb(haloStyle.stroke)
        return { strokeHalo: ratio(rgb(s.stroke),halo), haloFill: ratio(halo,fill), haloWidth: (parseFloat(haloStyle.strokeWidth) - parseFloat(s.strokeWidth)) / 2, strokeWidth: s.strokeWidth }
      })
    })
    for (const r of ratios) { expect(r.strokeHalo).toBeGreaterThanOrEqual(3); expect(r.haloFill).toBeGreaterThanOrEqual(3); expect(r.haloWidth).toBe(1.5); expect(r.strokeWidth).toBe('1.5px') }
    await expect(page.getByRole('img', { name: /Spectrum bass 0.60.*normalised 0.33, 0.33, 0.33/ })).toBeVisible()
    await expect(page.getByText('→ 0.33')).toHaveCount(3)
    const boxes = await geometry(page)
    writeFileSync(info.outputPath('geometry.json'), JSON.stringify(boxes, null, 2))
    await page.screenshot({ path: info.outputPath(`spectrum-${theme}-1400.png`), fullPage: true })
    console.log('SPECTRUM_MEASUREMENTS', theme, JSON.stringify({ boxes, strokeHalo: Math.min(...ratios.map(r=>r.strokeHalo)), haloFill: Math.min(...ratios.map(r=>r.haloFill)) }))
    socket().send(JSON.stringify({ type:'spectrum', bars:Array(10).fill(.2) }))
    await expect(page.getByTestId('spectrum-legend')).toHaveCount(0)
    await expect(outline).toHaveCount(0)
  })
}
