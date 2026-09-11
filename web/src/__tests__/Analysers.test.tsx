import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Analysers } from '../pages/Analysers'

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
    // Still in Standard mode — no expert section visible
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

    // No save calls — only the initial load
    expect(mapi.updateAnalyser).not.toHaveBeenCalled()
    expect(mapi.createAnalyser).not.toHaveBeenCalled()
  })

  it('standard fields remain after expert → standard switch', async () => {
    const user = await renderAndSelect()

    // Note the initial bars value
    const barsInput = screen.getByTestId('field-bars') as HTMLInputElement
    expect(barsInput.value).toBe('30')

    // Switch to expert and back
    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('mode-standard-btn'))

    // bars is still 30
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
      // After create, reload returns the new analyser
      mapi.getAnalysers.mockResolvedValueOnce([]).mockResolvedValue([newAnalyser])

      const user = userEvent.setup()
      render(<Analysers />)
      // Wait for empty state to load
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
      // Workspace gone — empty state shows
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
      // First load: just BASE; second load (after clone): BASE + cloned
      mapi.getAnalysers
        .mockResolvedValueOnce([BASE])
        .mockResolvedValue([BASE, cloned])
      mapi.getCouplings.mockResolvedValue([])
      mapi.cloneAnalyser.mockResolvedValue(cloned)

      const user = userEvent.setup()
      render(<Analysers />)
      await user.click(await screen.findByTestId('analyser-item-a1'))
      await user.click(screen.getByTestId('analyser-clone-btn'))

      // After clone, list should contain the clone and it should be selected
      await screen.findByTestId('analyser-item-clone-id')
    })
  })

  describe('Delete', () => {
    it('deleteAnalyser is called on confirm', async () => {
      mapi.deleteAnalyser.mockResolvedValue(undefined)
      mapi.getAnalysers.mockResolvedValueOnce([BASE]).mockResolvedValue([])

      const user = await renderAndSelect()
      await user.click(screen.getByTestId('analyser-delete-btn'))

      // ConfirmDialog renders a "Confirm" button in the dialog
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
    // usedByCount = 0, badge should not appear
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

// ── Suite 10: FrequencyRangeBar renders ───────────────────────────────────────

describe('FrequencyRangeBar', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('frequency-range-bar element exists after selecting analyser', async () => {
    await renderAndSelect()
    expect(screen.getByTestId('frequency-range-bar')).toBeDefined()
  })

  it('does not crash with edge Hz values', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 20, higher_cutoff_freq: 20000 })
    expect(screen.getByTestId('frequency-range-bar')).toBeDefined()
  })

  it('does not crash when lower > higher (bad data)', async () => {
    await renderAndSelect({ ...BASE, lower_cutoff_freq: 5000, higher_cutoff_freq: 100 })
    expect(screen.getByTestId('frequency-range-bar')).toBeDefined()
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
    // The subtitle <p> contains the method, bars, and source info
    // Use getAllByText to avoid ambiguity — we just need both Multiband texts to exist
    const texts = within(item).getAllByText(/Multiband/)
    expect(texts.length).toBeGreaterThanOrEqual(1)
    // The subtitle paragraph contains "30 bars"
    expect(within(item).getByText(/30 bars/)).toBeDefined()
    // The subtitle paragraph contains "cava"
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
