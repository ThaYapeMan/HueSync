/**
 * E2e tests for the Reset / Restore defaults buttons on Now Playing.
 *
 * Covers:
 *  1. "Reset to saved" restores the applied coupling value (not factory default).
 *  2. "Restore defaults" restores factory defaults (not the applied coupling value).
 *  3. Both buttons are disabled when the pending value matches their target.
 *  4. After Apply, applied == pending → "Reset to saved" becomes disabled.
 *
 * Applied values are non-default; factory defaults differ from applied values;
 * pending values differ from both.  A faulty "reset to wrong value" is caught.
 */
import { test, expect, type Page } from '@playwright/test'

// Factory defaults (must match models.py and DEFAULT_* in NowPlaying.tsx).
const DEFAULT_LOW  = 50
const DEFAULT_HIGH = 12000
const DEFAULT_BASS = 250
const DEFAULT_MID  = 2000

// Applied (saved coupling configuration) values sent by WebSocket — all non-default.
const APPLIED_LOW  = 80    // ≠ DEFAULT_LOW
const APPLIED_HIGH = 10000 // ≠ DEFAULT_HIGH
const APPLIED_BASS = 320   // ≠ DEFAULT_BASS
const APPLIED_MID  = 3000  // ≠ DEFAULT_MID

// Pending values typed in tests — different from both applied and defaults.
const PENDING_LOW  = 200
const PENDING_HIGH = 8000
const PENDING_BASS = 600
const PENDING_MID  = 5000

const STATUS_BASE = {
  type: 'status',
  version: '0.0.0+test',
  active_coupling_id: 'test-coupling-1',
  active_coupling_name: 'Test Coupling',
  sync_master: null,
  sync_master_name: null,
  applied_delay_ms: 0,
  latency_warning: null,
  processes: { squeezelite: true, cava: true },
  bridge_connected: false,
  effect_type: 'spectrum_rgb',
}

// ── helpers ─────────────────────────────────────────────────────────────────

async function mockApiRoutes(page: Page) {
  await page.route('/api/couplings', (r) => r.fulfill({ json: [] }))
  await page.route('/api/player-latencies', (r) => r.fulfill({ json: [] }))
}

function setupStaticWebSocket(
  page: Page,
  applied = {
    lower_cutoff_freq: APPLIED_LOW,
    higher_cutoff_freq: APPLIED_HIGH,
    bass_hz: APPLIED_BASS,
    mid_hz: APPLIED_MID,
  }
) {
  return page.routeWebSocket('/ws/preview', (ws) => {
    ws.send(JSON.stringify({ ...STATUS_BASE, ...applied }))
    const iv = setInterval(() => {
      try {
        ws.send(JSON.stringify({ type: 'frame', colour: { r: 0, g: 0, b: 0 }, onset: false }))
      } catch {
        clearInterval(iv)
      }
    }, 100)
  })
}

function setupDynamicWebSocket(page: Page, applied: Record<string, number>) {
  return page.routeWebSocket('/ws/preview', (ws) => {
    const sendStatus = () => {
      try { ws.send(JSON.stringify({ ...STATUS_BASE, ...applied })) } catch { /* closed */ }
    }
    sendStatus()
    const iv = setInterval(() => {
      try {
        ws.send(JSON.stringify({ type: 'frame', colour: { r: 0, g: 0, b: 0 }, onset: false }))
        sendStatus()
      } catch { clearInterval(iv) }
    }, 100)
  })
}

async function waitForValue(page: Page, testId: string, value: number) {
  await expect(page.locator(`[data-testid="${testId}"]`)).toHaveValue(String(value), { timeout: 5000 })
}

async function typeAndCommit(page: Page, testId: string, value: number) {
  const input = page.locator(`[data-testid="${testId}"]`)
  await input.fill(String(value))
  await input.press('Enter')
}

// ── Suite 1: "Reset to saved" ────────────────────────────────────────────────

test.describe('NowPlaying — "Reset to saved" restores applied coupling value', () => {
  test.beforeEach(async ({ page }) => {
    await mockApiRoutes(page)
    await setupStaticWebSocket(page)
    await page.goto('/')
  })

  test('Low cut: Reset to saved → APPLIED value, not factory default', async ({ page }) => {
    await waitForValue(page, 'low-cut-hz', APPLIED_LOW)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()

    await typeAndCommit(page, 'low-cut-hz', PENDING_LOW)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeEnabled()

    await page.locator('[data-testid="reset-cutoffs"]').click()
    await expect(page.locator('[data-testid="low-cut-hz"]')).toHaveValue(String(APPLIED_LOW))
    // Verify it did NOT fall back to factory default.
    await expect(page.locator('[data-testid="low-cut-hz"]')).not.toHaveValue(String(DEFAULT_LOW))
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()
  })

  test('High cut: Reset to saved → APPLIED value, not factory default', async ({ page }) => {
    await waitForValue(page, 'high-cut-hz', APPLIED_HIGH)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()

    await typeAndCommit(page, 'high-cut-hz', PENDING_HIGH)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeEnabled()

    await page.locator('[data-testid="reset-cutoffs"]').click()
    await expect(page.locator('[data-testid="high-cut-hz"]')).toHaveValue(String(APPLIED_HIGH))
    await expect(page.locator('[data-testid="high-cut-hz"]')).not.toHaveValue(String(DEFAULT_HIGH))
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()
  })

  test('Bass/mid: Reset to saved → APPLIED value, not factory default', async ({ page }) => {
    await waitForValue(page, 'bass-hz', APPLIED_BASS)
    await expect(page.locator('[data-testid="reset-bands"]')).toBeDisabled()

    await typeAndCommit(page, 'bass-hz', PENDING_BASS)
    await expect(page.locator('[data-testid="reset-bands"]')).toBeEnabled()

    await page.locator('[data-testid="reset-bands"]').click()
    await expect(page.locator('[data-testid="bass-hz"]')).toHaveValue(String(APPLIED_BASS))
    await expect(page.locator('[data-testid="bass-hz"]')).not.toHaveValue(String(DEFAULT_BASS))
    await expect(page.locator('[data-testid="reset-bands"]')).toBeDisabled()
  })

  test('Mid/treble: Reset to saved → APPLIED value, not factory default', async ({ page }) => {
    await waitForValue(page, 'mid-hz', APPLIED_MID)
    await expect(page.locator('[data-testid="reset-bands"]')).toBeDisabled()

    await typeAndCommit(page, 'mid-hz', PENDING_MID)
    await expect(page.locator('[data-testid="reset-bands"]')).toBeEnabled()

    await page.locator('[data-testid="reset-bands"]').click()
    await expect(page.locator('[data-testid="mid-hz"]')).toHaveValue(String(APPLIED_MID))
    await expect(page.locator('[data-testid="mid-hz"]')).not.toHaveValue(String(DEFAULT_MID))
    await expect(page.locator('[data-testid="reset-bands"]')).toBeDisabled()
  })
})

// ── Suite 2: "Restore factory defaults" ─────────────────────────────────────

test.describe('NowPlaying — "Restore defaults" restores factory defaults', () => {
  test.beforeEach(async ({ page }) => {
    await mockApiRoutes(page)
    await setupStaticWebSocket(page)
    await page.goto('/')
  })

  test('Low cut: Restore defaults → DEFAULT value, not applied coupling value', async ({ page }) => {
    await waitForValue(page, 'low-cut-hz', APPLIED_LOW)
    // Button enabled because APPLIED_LOW ≠ DEFAULT_LOW.
    await expect(page.locator('[data-testid="restore-defaults-cutoffs"]')).toBeEnabled()

    await page.locator('[data-testid="restore-defaults-cutoffs"]').click()
    await expect(page.locator('[data-testid="low-cut-hz"]')).toHaveValue(String(DEFAULT_LOW))
    await expect(page.locator('[data-testid="low-cut-hz"]')).not.toHaveValue(String(APPLIED_LOW))
  })

  test('High cut: Restore defaults → DEFAULT value, not applied coupling value', async ({ page }) => {
    await waitForValue(page, 'high-cut-hz', APPLIED_HIGH)

    await page.locator('[data-testid="restore-defaults-cutoffs"]').click()
    await expect(page.locator('[data-testid="high-cut-hz"]')).toHaveValue(String(DEFAULT_HIGH))
    await expect(page.locator('[data-testid="high-cut-hz"]')).not.toHaveValue(String(APPLIED_HIGH))
  })

  test('Bass/mid: Restore defaults → DEFAULT value, not applied coupling value', async ({ page }) => {
    await waitForValue(page, 'bass-hz', APPLIED_BASS)

    await page.locator('[data-testid="restore-defaults-bands"]').click()
    await expect(page.locator('[data-testid="bass-hz"]')).toHaveValue(String(DEFAULT_BASS))
    await expect(page.locator('[data-testid="bass-hz"]')).not.toHaveValue(String(APPLIED_BASS))
  })

  test('Mid/treble: Restore defaults → DEFAULT value, not applied coupling value', async ({ page }) => {
    await waitForValue(page, 'mid-hz', APPLIED_MID)

    await page.locator('[data-testid="restore-defaults-bands"]').click()
    await expect(page.locator('[data-testid="mid-hz"]')).toHaveValue(String(DEFAULT_MID))
    await expect(page.locator('[data-testid="mid-hz"]')).not.toHaveValue(String(APPLIED_MID))
  })

  test('Restore defaults enables "Reset to saved" (pending now differs from applied)', async ({ page }) => {
    await waitForValue(page, 'high-cut-hz', APPLIED_HIGH)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()

    await page.locator('[data-testid="restore-defaults-cutoffs"]').click()

    // Pending is now DEFAULT_HIGH; applied is still APPLIED_HIGH → Reset enabled.
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeEnabled()
    // Restore defaults is now disabled (pending == defaults).
    await expect(page.locator('[data-testid="restore-defaults-cutoffs"]')).toBeDisabled()
  })
})

// ── Suite 3: Apply makes "Reset to saved" disabled ──────────────────────────

test.describe('NowPlaying — Apply makes "Reset to saved" disabled', () => {
  test('After Apply, applied == pending → "Reset to saved" becomes disabled', async ({ page }) => {
    await mockApiRoutes(page)

    const applied: Record<string, number> = {
      lower_cutoff_freq: APPLIED_LOW,
      higher_cutoff_freq: APPLIED_HIGH,
      bass_hz: APPLIED_BASS,
      mid_hz: APPLIED_MID,
    }

    await page.route('**/api/couplings/*/restart-cava', async (route) => {
      const body = route.request().postDataJSON() as Record<string, number | undefined>
      if (body.lower_cutoff_freq  != null) applied.lower_cutoff_freq  = body.lower_cutoff_freq
      if (body.higher_cutoff_freq != null) applied.higher_cutoff_freq = body.higher_cutoff_freq
      if (body.bass_hz            != null) applied.bass_hz            = body.bass_hz
      if (body.mid_hz             != null) applied.mid_hz             = body.mid_hz
      await route.fulfill({ json: { ok: true } })
    })

    await setupDynamicWebSocket(page, applied)
    await page.goto('/')

    await waitForValue(page, 'high-cut-hz', APPLIED_HIGH)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled()

    await typeAndCommit(page, 'high-cut-hz', PENDING_HIGH)
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeEnabled()

    await page.locator('[data-testid="apply-cutoffs"]').click()
    await expect(page.locator('[data-testid="apply-cutoffs"]')).toBeEnabled({ timeout: 5000 })

    // WebSocket sends updated applied = PENDING_HIGH → pending == applied → Reset disabled.
    await expect(page.locator('[data-testid="reset-cutoffs"]')).toBeDisabled({ timeout: 3000 })
    await expect(page.locator('[data-testid="high-cut-hz"]')).toHaveValue(String(PENDING_HIGH))
  })
})
