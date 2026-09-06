/**
 * Component-level tests for the Reset / Restore defaults buttons on Now Playing.
 *
 * Uses Vitest + @testing-library/react so the full React event model runs in
 * jsdom.  Slider dragging is not testable here, but all text-input + state
 * paths are.
 *
 * Non-default applied values are used so that a faulty "reset to hardcoded
 * default" produces the wrong assertion value and catches the regression.
 * Factory defaults must match DEFAULT_* constants in NowPlaying.tsx and models.py.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NowPlaying } from '../pages/NowPlaying'
import type { SocketStatus } from '../hooks/usePreviewSocket'

// Factory defaults — must match models.py and DEFAULT_* in NowPlaying.tsx.
const DEFAULT_LOW  = 50
const DEFAULT_HIGH = 12000
const DEFAULT_BASS = 250
const DEFAULT_MID  = 2000

// Applied (saved coupling) values — all non-default so any mix-up is caught.
const APPLIED_LOW  = 120   // ≠ DEFAULT_LOW
const APPLIED_HIGH = 14000 // ≠ DEFAULT_HIGH
const APPLIED_BASS = 400   // ≠ DEFAULT_BASS
const APPLIED_MID  = 2500  // ≠ DEFAULT_MID

// Pending values typed in tests — different from both applied and defaults.
const PENDING_LOW  = 300
const PENDING_HIGH = 8000
const PENDING_BASS = 800
const PENDING_MID  = 5000

const MOCK_STATUS: SocketStatus = {
  version: '0.0.0+test',
  active_coupling_id: 'test-coupling-1',
  active_coupling_name: 'Test Coupling',
  sync_master: null,
  sync_master_name: null,
  applied_delay_ms: 0,
  latency_warning: null,
  processes: { squeezelite: true, cava: true },
  bridge_connected: false,
  color_mode: 'spectrum_rgb',
  onset_method: 'combined',
  lower_cutoff_freq: APPLIED_LOW,
  higher_cutoff_freq: APPLIED_HIGH,
  bass_hz: APPLIED_BASS,
  mid_hz: APPLIED_MID,
}

vi.mock('../lib/api', () => ({
  restartCouplingCava: vi.fn().mockResolvedValue({ ok: true }),
  getCouplings: vi.fn().mockResolvedValue([]),
  activateCoupling: vi.fn().mockResolvedValue({ active_id: 'test', warnings: [] }),
  deactivateCoupling: vi.fn().mockResolvedValue({ active_id: null }),
}))

function renderNowPlaying() {
  return render(
    <NowPlaying
      colour={{ r: 0, g: 0, b: 0 }}
      onset={false}
      bars={[]}
      status={MOCK_STATUS}
    />
  )
}

// ─────────────────────────────────────────────────────────────────────────────

describe('NowPlaying — Reset to saved & Restore factory defaults', () => {
  const user = userEvent.setup()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Initialisation ────────────────────────────────────────────────────────

  describe('Initialisation', () => {
    it('all four fields initialise from saved coupling values, not from factory defaults', () => {
      renderNowPlaying()
      expect(screen.getByTestId('low-cut-hz')).toHaveValue(APPLIED_LOW)
      expect(screen.getByTestId('high-cut-hz')).toHaveValue(APPLIED_HIGH)
      expect(screen.getByTestId('bass-hz')).toHaveValue(APPLIED_BASS)
      expect(screen.getByTestId('mid-hz')).toHaveValue(APPLIED_MID)
    })

    it('"Reset to saved" is disabled on init (pending equals applied)', () => {
      renderNowPlaying()
      expect(screen.getByTestId('reset-cutoffs')).toBeDisabled()
      expect(screen.getByTestId('reset-bands')).toBeDisabled()
    })

    it('"Restore defaults" is enabled on init (applied values differ from factory defaults)', () => {
      renderNowPlaying()
      // APPLIED_* ≠ DEFAULT_* so Restore defaults should be available.
      expect(screen.getByTestId('restore-defaults-cutoffs')).toBeEnabled()
      expect(screen.getByTestId('restore-defaults-bands')).toBeEnabled()
    })
  })

  // ── Cutoffs: Reset to saved ───────────────────────────────────────────────

  describe('Cutoffs — Reset to saved', () => {
    it('Low cut: Reset goes to APPLIED value, not factory default', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('low-cut-hz')

      await user.clear(input)
      await user.type(input, String(PENDING_LOW))
      await user.keyboard('{Enter}')
      expect(screen.getByTestId('reset-cutoffs')).toBeEnabled()

      await user.click(screen.getByTestId('reset-cutoffs'))

      // Must equal APPLIED_LOW (120), not DEFAULT_LOW (50) or PENDING_LOW (300).
      expect(input).toHaveValue(APPLIED_LOW)
      expect(input).not.toHaveValue(DEFAULT_LOW)
      expect(screen.getByTestId('reset-cutoffs')).toBeDisabled()
    })

    it('High cut: Reset goes to APPLIED value, not factory default', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('high-cut-hz')

      await user.clear(input)
      await user.type(input, String(PENDING_HIGH))
      await user.keyboard('{Enter}')

      await user.click(screen.getByTestId('reset-cutoffs'))

      // Must equal APPLIED_HIGH (14000), not DEFAULT_HIGH (12000).
      expect(input).toHaveValue(APPLIED_HIGH)
      expect(input).not.toHaveValue(DEFAULT_HIGH)
    })
  })

  // ── Cutoffs: Restore factory defaults ────────────────────────────────────

  describe('Cutoffs — Restore factory defaults', () => {
    it('Low cut: Restore defaults goes to DEFAULT value, not applied coupling value', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('low-cut-hz')

      await user.click(screen.getByTestId('restore-defaults-cutoffs'))

      // Must equal DEFAULT_LOW (50), not APPLIED_LOW (120).
      expect(input).toHaveValue(DEFAULT_LOW)
      expect(input).not.toHaveValue(APPLIED_LOW)
    })

    it('High cut: Restore defaults goes to DEFAULT value, not applied coupling value', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('high-cut-hz')

      await user.click(screen.getByTestId('restore-defaults-cutoffs'))

      // Must equal DEFAULT_HIGH (12000), not APPLIED_HIGH (14000).
      expect(input).toHaveValue(DEFAULT_HIGH)
      expect(input).not.toHaveValue(APPLIED_HIGH)
    })

    it('Restore defaults enables "Reset to saved" (pending now differs from applied)', async () => {
      renderNowPlaying()

      await user.click(screen.getByTestId('restore-defaults-cutoffs'))

      // Pending is now DEFAULT_*, applied is still APPLIED_* → Reset enabled.
      expect(screen.getByTestId('reset-cutoffs')).toBeEnabled()
    })

    it('"Restore defaults" is disabled when already at factory defaults', async () => {
      renderNowPlaying()

      // Set sliders to default values via Restore defaults.
      await user.click(screen.getByTestId('restore-defaults-cutoffs'))

      // Now pending == defaults → button should be disabled.
      expect(screen.getByTestId('restore-defaults-cutoffs')).toBeDisabled()
    })
  })

  // ── Bands: Reset to saved ─────────────────────────────────────────────────

  describe('Bands — Reset to saved', () => {
    it('Bass/mid: Reset goes to APPLIED value, not factory default', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('bass-hz')

      await user.clear(input)
      await user.type(input, String(PENDING_BASS))
      await user.keyboard('{Enter}')
      expect(screen.getByTestId('reset-bands')).toBeEnabled()

      await user.click(screen.getByTestId('reset-bands'))

      // Must equal APPLIED_BASS (400), not DEFAULT_BASS (250).
      expect(input).toHaveValue(APPLIED_BASS)
      expect(input).not.toHaveValue(DEFAULT_BASS)
      expect(screen.getByTestId('reset-bands')).toBeDisabled()
    })

    it('Mid/treble: Reset goes to APPLIED value, not factory default', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('mid-hz')

      await user.clear(input)
      await user.type(input, String(PENDING_MID))
      await user.keyboard('{Enter}')

      await user.click(screen.getByTestId('reset-bands'))

      // Must equal APPLIED_MID (2500), not DEFAULT_MID (2000).
      expect(input).toHaveValue(APPLIED_MID)
      expect(input).not.toHaveValue(DEFAULT_MID)
    })
  })

  // ── Bands: Restore factory defaults ──────────────────────────────────────

  describe('Bands — Restore factory defaults', () => {
    it('Bass/mid: Restore defaults goes to DEFAULT value, not applied coupling value', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('bass-hz')

      await user.click(screen.getByTestId('restore-defaults-bands'))

      // Must equal DEFAULT_BASS (250), not APPLIED_BASS (400).
      expect(input).toHaveValue(DEFAULT_BASS)
      expect(input).not.toHaveValue(APPLIED_BASS)
    })

    it('Mid/treble: Restore defaults goes to DEFAULT value, not applied coupling value', async () => {
      renderNowPlaying()
      const input = screen.getByTestId('mid-hz')

      await user.click(screen.getByTestId('restore-defaults-bands'))

      // Must equal DEFAULT_MID (2000), not APPLIED_MID (2500).
      expect(input).toHaveValue(DEFAULT_MID)
      expect(input).not.toHaveValue(APPLIED_MID)
    })

    it('Restore defaults enables "Reset to saved" for bands', async () => {
      renderNowPlaying()

      await user.click(screen.getByTestId('restore-defaults-bands'))

      expect(screen.getByTestId('reset-bands')).toBeEnabled()
    })

    it('"Restore defaults" for bands is disabled when already at factory defaults', async () => {
      renderNowPlaying()

      await user.click(screen.getByTestId('restore-defaults-bands'))

      expect(screen.getByTestId('restore-defaults-bands')).toBeDisabled()
    })
  })
})
