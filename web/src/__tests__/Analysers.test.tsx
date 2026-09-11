import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Analysers, hzToPercent } from '../pages/Analysers'

// ── API mock ──────────────────────────────────────────────────────────────────

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    getAnalysers: vi.fn(),
    getCouplings: vi.fn(),
    createAnalyser: vi.fn(),
    updateAnalyser: vi.fn(),
    deleteAnalyser: vi.fn(),
    cloneAnalyser: vi.fn(),
  }
})

import * as api from '../lib/api'

const mapi = api as {
  getAnalysers: ReturnType<typeof vi.fn>
  getCouplings: ReturnType<typeof vi.fn>
  createAnalyser: ReturnType<typeof vi.fn>
  updateAnalyser: ReturnType<typeof vi.fn>
  deleteAnalyser: ReturnType<typeof vi.fn>
  cloneAnalyser: ReturnType<typeof vi.fn>
}

// ── Fixture ───────────────────────────────────────────────────────────────────

const BASE = {
  id: 'a1',
  name: 'Multiband',
  onset_method: 'multiband',
  onset_delta: 0.1,
  onset_alpha: 0.9,
  superflux_mu: 3,
  superflux_lag: 2,
  bars: 30,
  lower_cutoff_freq: 50,
  higher_cutoff_freq: 12000,
  use_hpss_separation: false,
  bars_source: 'cava',
}

// ── Helper ────────────────────────────────────────────────────────────────────

async function renderAndSelect(analyser = BASE) {
  mapi.getAnalysers.mockResolvedValue([analyser])
  mapi.getCouplings.mockResolvedValue([])

  const user = userEvent.setup()
  render(<Analysers />)
  const item = await screen.findByTestId(`analyser-item-${analyser.id}`)
  await user.click(item)
  return user
}

// ── Suite 1: Standard mode defaults ──────────────────────────────────────────

describe('Standard mode defaults', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('Standard mode is default after render + select', async () => {
    await renderAndSelect()
    const stdBtn = screen.getByTestId('mode-standard-btn')
    expect(stdBtn.className).toMatch(/font-semibold/)
  })

  it('mode-standard-btn shows active style; mode-expert-btn does not', async () => {
    await renderAndSelect()
    const stdBtn = screen.getByTestId('mode-standard-btn')
    const expBtn = screen.getByTestId('mode-expert-btn')
    expect(stdBtn.className).toMatch(/font-semibold/)
    expect(expBtn.className).not.toMatch(/font-semibold/)
  })

  it('all standard sections render', async () => {
    await renderAndSelect()
    expect(screen.getByTestId('section-audio-source')).toBeDefined()
    expect(screen.getByTestId('section-beat-detection')).toBeDefined()
    expect(screen.getByTestId('section-freq-range')).toBeDefined()
    expect(screen.getByTestId('section-spectrum')).toBeDefined()
    expect(screen.getByTestId('section-beat-sensitivity')).toBeDefined()
    expect(screen.getByTestId('section-advanced-processing')).toBeDefined()
  })

  it('section-onset-tuning does NOT render in standard mode', async () => {
    await renderAndSelect()
    expect(screen.queryByTestId('section-onset-tuning')).toBeNull()
  })

  it('field-onset-alpha does NOT render in standard mode', async () => {
    await renderAndSelect()
    expect(screen.queryByTestId('field-onset-alpha')).toBeNull()
  })
})

// ── Suite 2: Expert mode ──────────────────────────────────────────────────────

describe('Expert mode', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('clicking mode-expert-btn shows section-onset-tuning', async () => {
    const user = await renderAndSelect()
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.getByTestId('section-onset-tuning')).toBeDefined()
  })

  it('field-onset-alpha renders in expert mode', async () => {
    const user = await renderAndSelect()
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.getByTestId('field-onset-alpha')).toBeDefined()
  })

  it('clicking mode-expert-btn shows current onset_alpha value', async () => {
    const user = await renderAndSelect({ ...BASE, onset_alpha: 0.9 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.9')
  })

  it('expert mode toggle activates expert button styling', async () => {
    const user = await renderAndSelect()
    await user.click(screen.getByTestId('mode-expert-btn'))
    const expBtn = screen.getByTestId('mode-expert-btn')
    const stdBtn = screen.getByTestId('mode-standard-btn')
    expect(expBtn.className).toMatch(/font-semibold/)
    expect(stdBtn.className).not.toMatch(/font-semibold/)
  })

  it('expert fields do NOT appear in standard mode', async () => {
    await renderAndSelect()
    expect(screen.queryByTestId('field-onset-alpha')).toBeNull()
    expect(screen.queryByTestId('field-superflux-mu')).toBeNull()
    expect(screen.queryByTestId('field-superflux-lag')).toBeNull()
  })
})

// ── Suite 3: Superflux conditional fields ─────────────────────────────────────

describe('Superflux conditional fields', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('superflux fields visible in expert mode with superflux method', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux' })
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.getByTestId('field-superflux-mu')).toBeDefined()
    expect(screen.getByTestId('field-superflux-lag')).toBeDefined()
  })

  it('superflux fields NOT visible in expert mode with combined method', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'combined' })
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.queryByTestId('field-superflux-mu')).toBeNull()
    expect(screen.queryByTestId('field-superflux-lag')).toBeNull()
  })

  it('superflux fields NOT visible in standard mode even with superflux method', async () => {
    await renderAndSelect({ ...BASE, onset_method: 'superflux' })
    expect(screen.queryByTestId('field-superflux-mu')).toBeNull()
    expect(screen.queryByTestId('field-superflux-lag')).toBeNull()
  })
})

// ── Suite 4: Mode switch safety — hidden Expert values survive ────────────────

describe('Mode switch safety', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('Standard save preserves hidden Expert onset_alpha value', async () => {
    const specialAnalyser = { ...BASE, onset_alpha: 0.347 }
    mapi.getAnalysers.mockResolvedValue([specialAnalyser])
    mapi.getCouplings.mockResolvedValue([])
    mapi.updateAnalyser.mockResolvedValue(specialAnalyser)

    const user = userEvent.setup()
    render(<Analysers />)
    await user.click(await screen.findByTestId('analyser-item-a1'))

    // Standard mode — onset_alpha not visible
    expect(screen.queryByTestId('field-onset-alpha')).toBeNull()

    // Change onset_delta
    const deltaInput = screen.getByTestId('field-onset-delta') as HTMLInputElement
    await user.clear(deltaInput)
    await user.type(deltaInput, '0.2')

    // Save
    await user.click(screen.getByTestId('analyser-save-btn'))

    // updateAnalyser must have been called with onset_alpha: 0.347 (preserved)
    expect(mapi.updateAnalyser).toHaveBeenCalledWith('a1', expect.objectContaining({
      onset_alpha: 0.347,
      onset_delta: 0.2,
    }))
  })

  it('all expert fields are included in save even in standard mode', async () => {
    mapi.getAnalysers.mockResolvedValue([{ ...BASE, superflux_mu: 7, superflux_lag: 5 }])
    mapi.getCouplings.mockResolvedValue([])
    mapi.updateAnalyser.mockResolvedValue(BASE)

    const user = userEvent.setup()
    render(<Analysers />)
    await user.click(await screen.findByTestId('analyser-item-a1'))

    await user.click(screen.getByTestId('analyser-save-btn'))

    expect(mapi.updateAnalyser).toHaveBeenCalledWith('a1', expect.objectContaining({
      superflux_mu: 7,
      superflux_lag: 5,
    }))
  })
})

// ── Suite 5: Expert → Standard switch doesn't mutate ─────────────────────────

describe('Expert → Standard switch does not mutate state', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('no API call fires when switching between modes', async () => {
    const user = await renderAndSelect()

    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('mode-standard-btn'))

    expect(mapi.updateAnalyser).not.toHaveBeenCalled()
    expect(mapi.createAnalyser).not.toHaveBeenCalled()
  })

  it('standard fields remain after expert → standard switch', async () => {
    const user = await renderAndSelect()

    const barsInput = screen.getByTestId('field-bars') as HTMLInputElement
    expect(barsInput.value).toBe('30')

    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('mode-standard-btn'))

    const barsInput2 = screen.getByTestId('field-bars') as HTMLInputElement
    expect(barsInput2.value).toBe('30')
  })
})

// ── Suite 6: Exact numeric values render correctly ────────────────────────────

describe('Exact numeric values in fields', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('onset_alpha 0.347 shows as "0.347" in expert mode', async () => {
    const user = await renderAndSelect({ ...BASE, onset_alpha: 0.347 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.347')
  })

  it('bars 42 shows as "42" in standard mode', async () => {
    await renderAndSelect({ ...BASE, bars: 42 })
    const barsInput = screen.getByTestId('field-bars') as HTMLInputElement
    expect(barsInput.value).toBe('42')
  })

  it('onset_delta shows as stored value', async () => {
    await renderAndSelect({ ...BASE, onset_delta: 0.15 })
    const deltaInput = screen.getByTestId('field-onset-delta') as HTMLInputElement
    expect(deltaInput.value).toBe('0.15')
  })
})

// ── Suite 7: Entity operations ────────────────────────────────────────────────

describe('Entity operations', () => {
  beforeEach(() => { vi.clearAllMocks() })

  describe('Create', () => {
    it('clicking New analyser shows workspace with empty form', async () => {
      mapi.getAnalysers.mockResolvedValue([BASE])
      mapi.getCouplings.mockResolvedValue([])
      const user = userEvent.setup()
      render(<Analysers />)
      await screen.findByTestId('analyser-item-a1')

      await user.click(screen.getByText('New analyser'))
      const nameInput = screen.getByTestId('analyser-name-input') as HTMLInputElement
      expect(nameInput.value).toBe('')
    })

    it('save on new calls createAnalyser with name', async () => {
      const newAnalyser = { ...BASE, id: 'new-id', name: 'My New Analyser' }
      mapi.getAnalysers.mockResolvedValue([])
      mapi.getCouplings.mockResolvedValue([])
      mapi.createAnalyser.mockResolvedValue(newAnalyser)
      mapi.getAnalysers.mockResolvedValueOnce([]).mockResolvedValue([newAnalyser])

      const user = userEvent.setup()
      render(<Analysers />)
      await screen.findByText('No analysers yet.')

      await user.click(screen.getByText('New analyser'))
      const nameInput = screen.getByTestId('analyser-name-input') as HTMLInputElement
      await user.type(nameInput, 'My New Analyser')

      await user.click(screen.getByTestId('analyser-save-btn'))

      expect(mapi.createAnalyser).toHaveBeenCalledWith(expect.objectContaining({
        name: 'My New Analyser',
      }))
    })

    it('cancel create button hides workspace', async () => {
      mapi.getAnalysers.mockResolvedValue([BASE])
      mapi.getCouplings.mockResolvedValue([])
      const user = userEvent.setup()
      render(<Analysers />)
      await screen.findByTestId('analyser-item-a1')

      await user.click(screen.getByText('New analyser'))
      expect(screen.getByTestId('analyser-cancel-btn')).toBeDefined()

      await user.click(screen.getByTestId('analyser-cancel-btn'))
      expect(screen.getByText(/Select an analyser/i)).toBeDefined()
    })
  })

  describe('Clone', () => {
    it('cloneAnalyser is called with correct id', async () => {
      const cloned = { ...BASE, id: 'clone-id', name: 'Multiband (copy)' }
      mapi.getAnalysers.mockResolvedValue([BASE])
      mapi.getCouplings.mockResolvedValue([])
      mapi.cloneAnalyser.mockResolvedValue(cloned)

      const user = userEvent.setup()
      render(<Analysers />)
      await user.click(await screen.findByTestId('analyser-item-a1'))
      await user.click(screen.getByTestId('analyser-clone-btn'))

      expect(mapi.cloneAnalyser).toHaveBeenCalledWith('a1')
    })

    it('clone selects the new analyser', async () => {
      const cloned = { ...BASE, id: 'clone-id', name: 'Multiband (copy)' }
      mapi.getAnalysers
        .mockResolvedValueOnce([BASE])
        .mockResolvedValue([BASE, cloned])
      mapi.getCouplings.mockResolvedValue([])
      mapi.cloneAnalyser.mockResolvedValue(cloned)

      const user = userEvent.setup()
      render(<Analysers />)
      await user.click(await screen.findByTestId('analyser-item-a1'))
      await user.click(screen.getByTestId('analyser-clone-btn'))

      await screen.findByTestId('analyser-item-clone-id')
    })
  })

  describe('Delete', () => {
    it('deleteAnalyser is called on confirm', async () => {
      mapi.deleteAnalyser.mockResolvedValue(undefined)
      mapi.getAnalysers.mockResolvedValueOnce([BASE]).mockResolvedValue([])

      const user = await renderAndSelect()
      await user.click(screen.getByTestId('analyser-delete-btn'))

      const confirmBtn = await screen.findByRole('button', { name: /confirm/i })
      await user.click(confirmBtn)

      expect(mapi.deleteAnalyser).toHaveBeenCalledWith('a1')
    })
  })

  describe('Rename', () => {
    it('updateAnalyser called with new name', async () => {
      mapi.updateAnalyser.mockResolvedValue({ ...BASE, name: 'Renamed Analyser' })
      mapi.getAnalysers.mockResolvedValue([BASE])

      const user = await renderAndSelect()
      const nameInput = screen.getByTestId('analyser-name-input') as HTMLInputElement
      await user.clear(nameInput)
      await user.type(nameInput, 'Renamed Analyser')

      await user.click(screen.getByTestId('analyser-save-btn'))

      expect(mapi.updateAnalyser).toHaveBeenCalledWith('a1', expect.objectContaining({
        name: 'Renamed Analyser',
      }))
    })
  })
})

// ── Suite 8: Missing/edge data ────────────────────────────────────────────────

describe('Missing/edge data', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('empty analyser list shows empty state, no crash', async () => {
    mapi.getAnalysers.mockResolvedValue([])
    mapi.getCouplings.mockResolvedValue([])
    render(<Analysers />)
    await screen.findByText('No analysers yet.')
  })

  it('analyser with no couplings — no coupling badge visible', async () => {
    await renderAndSelect()
    expect(screen.queryByText(/Used by/)).toBeNull()
  })

  it('analyser used by 2 couplings shows correct badge', async () => {
    const couplings = [
      { id: 'c1', name: 'C1', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1', enabled: true },
      { id: 'c2', name: 'C2', player_id: 'p2', zone_id: 'z2', analyser_id: 'a1', energy_profile_id: 'ep2', enabled: true },
    ]
    mapi.getAnalysers.mockResolvedValue([BASE])
    mapi.getCouplings.mockResolvedValue(couplings)

    const user = userEvent.setup()
    render(<Analysers />)
    const item = await screen.findByTestId('analyser-item-a1')
    await user.click(item)

    expect(screen.getByText('Used by 2 couplings')).toBeDefined()
  })
})

// ── Suite 9: Superflux fields in Expert mode with correct defaults ────────────

describe('Superflux Expert mode with defaults', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('superflux-mu and superflux-lag visible with superflux method in expert mode', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux', superflux_mu: 3, superflux_lag: 2 })
    await user.click(screen.getByTestId('mode-expert-btn'))

    expect(screen.getByTestId('field-superflux-mu')).toBeDefined()
    expect(screen.getByTestId('field-superflux-lag')).toBeDefined()
  })

  it('superflux-mu default value is 3', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux', superflux_mu: 3 })
    await user.click(screen.getByTestId('mode-expert-btn'))

    const muInput = screen.getByTestId('field-superflux-mu') as HTMLInputElement
    expect(muInput.value).toBe('3')
  })

  it('superflux-lag default value is 2', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux', superflux_lag: 2 })
    await user.click(screen.getByTestId('mode-expert-btn'))

    const lagInput = screen.getByTestId('field-superflux-lag') as HTMLInputElement
    expect(lagInput.value).toBe('2')
  })
})

// ── Suite 10: FrequencyRangeSlider renders ────────────────────────────────────

describe('FrequencyRangeSlider', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('frequency-range-slider element exists after selecting analyser', async () => {
    await renderAndSelect()
    expect(screen.getByTestId('frequency-range-slider')).toBeDefined()
  })

  it('does not crash with edge Hz values', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 20, higher_cutoff_freq: 20000 })
    expect(screen.getByTestId('frequency-range-slider')).toBeDefined()
  })

  it('does not crash when lower > higher (bad data)', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 5000, higher_cutoff_freq: 100 })
    expect(screen.getByTestId('frequency-range-slider')).toBeDefined()
  })
})

// ── Suite 11: List item subtitle ─────────────────────────────────────────────

describe('Analyser list item', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows method + bars + source in subtitle', async () => {
    mapi.getAnalysers.mockResolvedValue([BASE])
    mapi.getCouplings.mockResolvedValue([])
    render(<Analysers />)
    const item = await screen.findByTestId('analyser-item-a1')
    const texts = within(item).getAllByText(/Multiband/)
    expect(texts.length).toBeGreaterThanOrEqual(1)
    expect(within(item).getByText(/30 bars/)).toBeDefined()
    expect(within(item).getByText(/cava/)).toBeDefined()
  })

  it('shows PCM label for pcm_pipeline source', async () => {
    mapi.getAnalysers.mockResolvedValue([{ ...BASE, bars_source: 'pcm_pipeline' }])
    mapi.getCouplings.mockResolvedValue([])
    render(<Analysers />)
    const item = await screen.findByTestId('analyser-item-a1')
    expect(within(item).getByText(/PCM/)).toBeDefined()
  })
})

// ── Suite 12: Default / Reset behavior ───────────────────────────────────────

describe('Default / Reset behavior', () => {
  beforeEach(() => { vi.clearAllMocks() })

  // ── Audio Source ───────────────────────────────────────────────────────────

  it('reset-audio-source is disabled when bars_source is already cava (default)', async () => {
    await renderAndSelect({ ...BASE, bars_source: 'cava' })
    const resetBtn = screen.getByTestId('reset-audio-source')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('reset-audio-source is active after switching to PCM Pipeline', async () => {
    const user = await renderAndSelect({ ...BASE, bars_source: 'cava' })
    await user.click(screen.getByTestId('opt-bars-source-pcm_pipeline'))
    const resetBtn = screen.getByTestId('reset-audio-source')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('false')
  })

  it('PCM Pipeline → Reset → Cava', async () => {
    const user = await renderAndSelect({ ...BASE, bars_source: 'pcm_pipeline' })
    await user.click(screen.getByTestId('reset-audio-source'))
    // After reset, cava option card should be selected
    const cavaCard = screen.getByTestId('opt-bars-source-cava')
    expect(cavaCard.className).toMatch(/border-primary/)
  })

  // ── Beat Detection ─────────────────────────────────────────────────────────

  it('reset-onset-method is disabled when onset_method is already combined (default)', async () => {
    await renderAndSelect({ ...BASE, onset_method: 'combined' })
    const resetBtn = screen.getByTestId('reset-onset-method')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('reset-onset-method is active after switching to Multiband', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'combined' })
    await user.click(screen.getByTestId('opt-onset-method-multiband'))
    const resetBtn = screen.getByTestId('reset-onset-method')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('false')
  })

  it('Multiband → Reset → Combined', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'multiband' })
    await user.click(screen.getByTestId('reset-onset-method'))
    const combinedCard = screen.getByTestId('opt-onset-method-combined')
    expect(combinedCard.className).toMatch(/border-primary/)
  })

  it('SuperFlux → Reset → Combined', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux' })
    await user.click(screen.getByTestId('reset-onset-method'))
    const combinedCard = screen.getByTestId('opt-onset-method-combined')
    expect(combinedCard.className).toMatch(/border-primary/)
  })

  // ── Frequency Range ────────────────────────────────────────────────────────

  it('reset-freq-range is disabled when at default (50 / 12000)', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const resetBtn = screen.getByTestId('reset-freq-range')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('reset-freq-range is active after changing lower cutoff', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 80, higher_cutoff_freq: 12000 })
    // Lower is already non-default (80)
    const resetBtn = screen.getByTestId('reset-freq-range')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('false')
  })

  it('non-default freq range → Reset → 50 / 12000', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 80, higher_cutoff_freq: 9000 })
    await user.click(screen.getByTestId('reset-freq-range'))
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(lowerInput.value).toBe('50')
    expect(higherInput.value).toBe('12000')
  })

  it('freq range reset updates slider handle positions', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 80, higher_cutoff_freq: 9000 })
    await user.click(screen.getByTestId('reset-freq-range'))
    const lowHandle = screen.getByTestId('slider-handle-low')
    const highHandle = screen.getByTestId('slider-handle-high')
    expect(lowHandle.getAttribute('aria-valuenow')).toBe('50')
    expect(highHandle.getAttribute('aria-valuenow')).toBe('12000')
  })

  // ── Spectrum Resolution ────────────────────────────────────────────────────

  it('reset-bars is disabled when bars = 30 (default)', async () => {
    await renderAndSelect({ ...BASE, bars: 30 })
    const resetBtn = screen.getByTestId('reset-bars')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('non-default bars → Reset → 30', async () => {
    const user = await renderAndSelect({ ...BASE, bars: 45 })
    await user.click(screen.getByTestId('reset-bars'))
    const barsInput = screen.getByTestId('field-bars') as HTMLInputElement
    expect(barsInput.value).toBe('30')
  })

  // ── Reset active-state visual styling ─────────────────────────────────────

  it('reset button at default has muted styling (not text-foreground)', async () => {
    await renderAndSelect({ ...BASE, onset_delta: 0.1 })
    const resetBtn = screen.getByTestId('reset-onset-delta')
    expect(resetBtn.className).not.toMatch(/\btext-foreground\b/)
    expect(resetBtn.className).toMatch(/pointer-events-none/)
  })

  it('reset button at non-default has white/foreground active styling', async () => {
    await renderAndSelect({ ...BASE, onset_delta: 0.07 })
    const resetBtn = screen.getByTestId('reset-onset-delta')
    expect(resetBtn.className).toMatch(/\btext-foreground\b/)
    expect(resetBtn.className).not.toMatch(/pointer-events-none/)
  })

  // ── Beat Sensitivity ───────────────────────────────────────────────────────

  it('reset-onset-delta is disabled when onset_delta = 0.1 (default)', async () => {
    await renderAndSelect({ ...BASE, onset_delta: 0.1 })
    const resetBtn = screen.getByTestId('reset-onset-delta')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('reset-onset-delta is active when onset_delta is changed', async () => {
    const user = await renderAndSelect({ ...BASE, onset_delta: 0.07 })
    const resetBtn = screen.getByTestId('reset-onset-delta')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('false')
  })

  it('non-default onset_delta → Reset → 0.1', async () => {
    const user = await renderAndSelect({ ...BASE, onset_delta: 0.07 })
    await user.click(screen.getByTestId('reset-onset-delta'))
    const deltaInput = screen.getByTestId('field-onset-delta') as HTMLInputElement
    expect(deltaInput.value).toBe('0.1')
  })

  // ── HPSS ──────────────────────────────────────────────────────────────────

  it('reset-hpss is disabled when use_hpss_separation = false (default)', async () => {
    await renderAndSelect({ ...BASE, use_hpss_separation: false })
    const resetBtn = screen.getByTestId('reset-hpss')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('enabling HPSS makes reset available', async () => {
    const user = await renderAndSelect({ ...BASE, use_hpss_separation: false })
    await user.click(screen.getByTestId('field-use-hpss'))
    const resetBtn = screen.getByTestId('reset-hpss')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('false')
  })

  it('HPSS enabled → Reset → false', async () => {
    const user = await renderAndSelect({ ...BASE, use_hpss_separation: true })
    await user.click(screen.getByTestId('reset-hpss'))
    const hpssCheck = screen.getByTestId('field-use-hpss') as HTMLInputElement
    expect(hpssCheck.checked).toBe(false)
  })

  // ── Onset Tuning (Expert) ──────────────────────────────────────────────────

  it('reset-onset-tuning is disabled when onset_alpha = 0.9 (default)', async () => {
    const user = await renderAndSelect({ ...BASE, onset_alpha: 0.9 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const resetBtn = screen.getByTestId('reset-onset-tuning')
    expect(resetBtn.getAttribute('aria-disabled')).toBe('true')
  })

  it('non-default onset_alpha → Reset → 0.9', async () => {
    const user = await renderAndSelect({ ...BASE, onset_alpha: 0.5 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('reset-onset-tuning'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.9')
  })

  it('superflux: non-default mu/lag → Reset → 3 / 2', async () => {
    const user = await renderAndSelect({ ...BASE, onset_method: 'superflux', superflux_mu: 8, superflux_lag: 5 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('reset-onset-tuning'))
    const muInput = screen.getByTestId('field-superflux-mu') as HTMLInputElement
    const lagInput = screen.getByTestId('field-superflux-lag') as HTMLInputElement
    expect(muInput.value).toBe('3')
    expect(lagInput.value).toBe('2')
  })

  // ── Hidden value safety ────────────────────────────────────────────────────

  it('resetting audio source does not modify expert fields', async () => {
    const user = await renderAndSelect({ ...BASE, bars_source: 'pcm_pipeline', onset_alpha: 0.42 })
    await user.click(screen.getByTestId('reset-audio-source'))
    // Go to expert mode to verify onset_alpha was untouched
    await user.click(screen.getByTestId('mode-expert-btn'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.42')
  })

  it('resetting freq range does not modify expert onset_alpha', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 80, onset_alpha: 0.42 })
    await user.click(screen.getByTestId('reset-freq-range'))
    await user.click(screen.getByTestId('mode-expert-btn'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.42')
  })

  it('resetting onset_delta does not modify onset_alpha', async () => {
    const user = await renderAndSelect({ ...BASE, onset_delta: 0.5, onset_alpha: 0.42 })
    await user.click(screen.getByTestId('reset-onset-delta'))
    await user.click(screen.getByTestId('mode-expert-btn'))
    const alphaInput = screen.getByTestId('field-onset-alpha') as HTMLInputElement
    expect(alphaInput.value).toBe('0.42')
  })

  // ── Reset participates in save flow ───────────────────────────────────────

  it('reset draft values are persisted through save', async () => {
    mapi.updateAnalyser.mockResolvedValue(BASE)
    const user = await renderAndSelect({ ...BASE, onset_delta: 0.5 })

    // Reset onset_delta to default (0.1)
    await user.click(screen.getByTestId('reset-onset-delta'))

    // Save — should send default value
    await user.click(screen.getByTestId('analyser-save-btn'))
    expect(mapi.updateAnalyser).toHaveBeenCalledWith('a1', expect.objectContaining({
      onset_delta: 0.1,
    }))
  })
})

// ── Suite 13: Dual range frequency slider ─────────────────────────────────────

describe('Dual range frequency slider', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('slider handles exist with correct initial aria attributes', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    const highHandle = screen.getByTestId('slider-handle-high')
    expect(lowHandle.getAttribute('aria-valuenow')).toBe('50')
    expect(highHandle.getAttribute('aria-valuenow')).toBe('12000')
  })

  it('low handle ArrowRight increases lower_cutoff_freq', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    lowHandle.focus()
    await user.keyboard('{ArrowRight}')
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    expect(parseInt(lowerInput.value)).toBeGreaterThan(50)
  })

  it('low handle ArrowLeft decreases lower_cutoff_freq', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 100, higher_cutoff_freq: 12000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    lowHandle.focus()
    await user.keyboard('{ArrowLeft}')
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    expect(parseInt(lowerInput.value)).toBeLessThan(100)
  })

  it('high handle ArrowLeft decreases higher_cutoff_freq', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const highHandle = screen.getByTestId('slider-handle-high')
    highHandle.focus()
    await user.keyboard('{ArrowLeft}')
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(parseInt(higherInput.value)).toBeLessThan(12000)
  })

  it('high handle ArrowRight increases higher_cutoff_freq', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const highHandle = screen.getByTestId('slider-handle-high')
    highHandle.focus()
    await user.keyboard('{ArrowRight}')
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(parseInt(higherInput.value)).toBeGreaterThan(12000)
  })

  it('low handle is clamped to 500 Hz maximum', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 450, higher_cutoff_freq: 12000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    lowHandle.focus()
    // Press many times — should never exceed 500
    for (let i = 0; i < 15; i++) await user.keyboard('{ArrowRight}')
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    expect(parseInt(lowerInput.value)).toBeLessThanOrEqual(500)
  })

  it('low handle does not go below 20 Hz minimum', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 25, higher_cutoff_freq: 12000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    lowHandle.focus()
    for (let i = 0; i < 10; i++) await user.keyboard('{ArrowLeft}')
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    expect(parseInt(lowerInput.value)).toBeGreaterThanOrEqual(20)
  })

  it('high handle is clamped to 1000 Hz minimum', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 1200 })
    const highHandle = screen.getByTestId('slider-handle-high')
    highHandle.focus()
    for (let i = 0; i < 15; i++) await user.keyboard('{ArrowLeft}')
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(parseInt(higherInput.value)).toBeGreaterThanOrEqual(1000)
  })

  it('high handle does not exceed 20000 Hz maximum', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 18000 })
    const highHandle = screen.getByTestId('slider-handle-high')
    highHandle.focus()
    for (let i = 0; i < 15; i++) await user.keyboard('{ArrowRight}')
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(parseInt(higherInput.value)).toBeLessThanOrEqual(20000)
  })

  it('handles maintain order: low Hz always < high Hz', async () => {
    // With lowHz near max (490) and highHz at min valid (1000), pressing
    // ArrowRight on low handle must not allow it to reach highHz.
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 490, higher_cutoff_freq: 1000 })
    const lowHandle = screen.getByTestId('slider-handle-low')
    lowHandle.focus()
    for (let i = 0; i < 10; i++) await user.keyboard('{ArrowRight}')
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    expect(parseInt(lowerInput.value)).toBeLessThan(parseInt(higherInput.value))
  })

  it('numeric lower input change updates slider-handle-low aria-valuenow', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const lowerInput = screen.getByTestId('field-lower-cutoff') as HTMLInputElement
    await user.clear(lowerInput)
    await user.type(lowerInput, '100')
    const lowHandle = screen.getByTestId('slider-handle-low')
    expect(lowHandle.getAttribute('aria-valuenow')).toBe('100')
  })

  it('numeric higher input change updates slider-handle-high aria-valuenow', async () => {
    const user = await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 12000 })
    const higherInput = screen.getByTestId('field-higher-cutoff') as HTMLInputElement
    await user.clear(higherInput)
    await user.type(higherInput, '8000')
    const highHandle = screen.getByTestId('slider-handle-high')
    expect(highHandle.getAttribute('aria-valuenow')).toBe('8000')
  })

  it('logarithmic mapping: 1000 Hz handle is near ~57% not ~5% (linear)', async () => {
    // Geometric mean of 20–20000 Hz on a log scale: sqrt(20*20000) ≈ 632 Hz → 50%
    // 1000 Hz is log-mid: (log10(1000)-log10(20))/(log10(20000)-log10(20))*100 ≈ 56.6%
    // Linear mapping would give 1000/20000*100 = 5% — very different.
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 50, higher_cutoff_freq: 1000 })
    const highHandle = screen.getByTestId('slider-handle-high')
    const left = parseFloat(highHandle.style.left ?? '0')
    // Logarithmic: ~56.6%; linear: ~5%. Verify we're clearly not linear.
    expect(left).toBeGreaterThan(50)
    expect(left).toBeLessThan(70)
  })

  it('hzToPercent export: 20 Hz maps to 0%', () => {
    expect(hzToPercent(20)).toBeCloseTo(0, 5)
  })

  it('hzToPercent export: 20000 Hz maps to 100%', () => {
    expect(hzToPercent(20000)).toBeCloseTo(100, 5)
  })

  it('hzToPercent export: 1000 Hz maps to ~56.6% (logarithmic midpoint)', () => {
    const pct = hzToPercent(1000)
    expect(pct).toBeGreaterThan(55)
    expect(pct).toBeLessThan(58)
  })
})
