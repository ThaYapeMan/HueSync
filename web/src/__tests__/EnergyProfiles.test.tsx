import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EnergyProfiles } from '../pages/EnergyProfiles'

// ── Preview socket mock ───────────────────────────────────────────────────────

vi.mock('../hooks/usePreviewSocket', () => ({
  usePreviewSocket: vi.fn(),
}))

import * as previewHooks from '../hooks/usePreviewSocket'

const mockUsePreviewSocket = previewHooks.usePreviewSocket as unknown as ReturnType<typeof vi.fn>

const DEFAULT_PREVIEW = {
  colour: { r: 0, g: 0, b: 0 },
  channel_colours: [] as Array<{ r: number; g: number; b: number }>,
  onset: false,
  onset_bass: false,
  onset_mid: false,
  onset_treble: false,
  mix: 0,
  energy: 0,
  bars: [],
  status: null,
  connected: false,
  reconnectAttempt: 0,
}

// ── API mock ──────────────────────────────────────────────────────────────────

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    getEnergyProfiles: vi.fn(),
    getEffects: vi.fn(),
    createEnergyProfile: vi.fn(),
    updateEnergyProfile: vi.fn(),
    deleteEnergyProfile: vi.fn(),
  }
})

import * as api from '../lib/api'

const mapi = api as {
  getEnergyProfiles: ReturnType<typeof vi.fn>
  getEffects: ReturnType<typeof vi.fn>
  createEnergyProfile: ReturnType<typeof vi.fn>
  updateEnergyProfile: ReturnType<typeof vi.fn>
  deleteEnergyProfile: ReturnType<typeof vi.fn>
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const BASE_EFFECT = {
  id: 'fx1',
  name: 'Test Effect',
  effect_type: 'spectrum_rgb',
  effect_speed: 1.0,
  effect_decay: 0.3,
  sensitivity: 1.0,
  brightness_floor: 0.05,
  bass_hz: 250,
  mid_hz: 2000,
  exertion_clip: 3.0,
  onset_flash_intensity: 0.0,
}

const BASE_EP = {
  id: 'ep1',
  name: 'Test Profile',
  high_energy_effect_id: 'fx1',
  low_energy_effect_id: '',
  blend_start: 0.3,
  blend_end: 0.7,
  blend_response: 0.1,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function renderAndOpen(ep = BASE_EP) {
  mockUsePreviewSocket.mockReturnValue(DEFAULT_PREVIEW)
  mapi.getEnergyProfiles.mockResolvedValue([ep])
  mapi.getEffects.mockResolvedValue([BASE_EFFECT])
  mapi.updateEnergyProfile.mockResolvedValue({ ...ep })

  const user = userEvent.setup()
  render(<EnergyProfiles />)
  const editBtn = await screen.findByRole('button', { name: /Edit/i })
  await user.click(editBtn)
  return user
}

async function renderAndNew() {
  mockUsePreviewSocket.mockReturnValue(DEFAULT_PREVIEW)
  mapi.getEnergyProfiles.mockResolvedValue([])
  mapi.getEffects.mockResolvedValue([BASE_EFFECT])
  mapi.createEnergyProfile.mockResolvedValue({ id: 'new1', ...BASE_EP, name: 'New Profile' })

  const user = userEvent.setup()
  render(<EnergyProfiles />)
  const newBtn = await screen.findByRole('button', { name: /New energy profile/i })
  await user.click(newBtn)
  return user
}

// ── Suite 1: Standard mode defaults ──────────────────────────────────────────

describe('Standard mode defaults', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('Standard mode is default after opening editor', async () => {
    await renderAndOpen()
    const stdBtn = screen.getByTestId('mode-standard-btn')
    expect(stdBtn.className).toMatch(/font-semibold/)
  })

  it('mode-standard-btn shows active style; mode-expert-btn does not', async () => {
    await renderAndOpen()
    expect(screen.getByTestId('mode-standard-btn').className).toMatch(/font-semibold/)
    expect(screen.getByTestId('mode-expert-btn').className).not.toMatch(/font-semibold/)
  })

  it('expert fields NOT visible in standard mode', async () => {
    await renderAndOpen()
    expect(screen.queryByTestId('field-blend-start')).toBeNull()
    expect(screen.queryByTestId('field-blend-end')).toBeNull()
    expect(screen.queryByTestId('field-blend-response')).toBeNull()
  })

  it('blend-zone-bar renders in standard mode', async () => {
    await renderAndOpen()
    expect(screen.getByTestId('blend-zone-bar')).toBeDefined()
  })

  it('current-blend-bar renders in standard mode', async () => {
    await renderAndOpen()
    expect(screen.getByTestId('current-blend-bar')).toBeDefined()
  })
})

// ── Suite 2: Expert mode ──────────────────────────────────────────────────────

describe('Expert mode', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('clicking mode-expert-btn reveals exact blend inputs', async () => {
    const user = await renderAndOpen()
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.getByTestId('field-blend-start')).toBeDefined()
    expect(screen.getByTestId('field-blend-end')).toBeDefined()
    expect(screen.getByTestId('field-blend-response')).toBeDefined()
  })

  it('expert mode toggle activates expert button styling', async () => {
    const user = await renderAndOpen()
    await user.click(screen.getByTestId('mode-expert-btn'))
    expect(screen.getByTestId('mode-expert-btn').className).toMatch(/font-semibold/)
    expect(screen.getByTestId('mode-standard-btn').className).not.toMatch(/font-semibold/)
  })

  it('clicking mode-standard-btn hides expert fields again', async () => {
    const user = await renderAndOpen()
    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('mode-standard-btn'))
    expect(screen.queryByTestId('field-blend-start')).toBeNull()
    expect(screen.queryByTestId('field-blend-end')).toBeNull()
    expect(screen.queryByTestId('field-blend-response')).toBeNull()
  })

  it('field-blend-start shows stored value', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_start: 0.25 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const input = screen.getByTestId('field-blend-start') as HTMLInputElement
    expect(input.value).toBe('0.25')
  })

  it('field-blend-end shows stored value', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_end: 0.85 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const input = screen.getByTestId('field-blend-end') as HTMLInputElement
    expect(input.value).toBe('0.85')
  })

  it('field-blend-response shows stored value', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_response: 0.25 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const input = screen.getByTestId('field-blend-response') as HTMLInputElement
    expect(input.value).toBe('0.25')
  })
})

// ── Suite 3: Semantic card colors ─────────────────────────────────────────────

describe('EffectCard semantic colors', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('low effect card has cyan border class', async () => {
    await renderAndOpen()
    const cards = document.querySelectorAll('[class*="border-cyan"]')
    expect(cards.length).toBeGreaterThan(0)
  })

  it('high effect card has rose border class', async () => {
    await renderAndOpen()
    const cards = document.querySelectorAll('[class*="border-rose"]')
    expect(cards.length).toBeGreaterThan(0)
  })
})

// ── Suite 4: Reset behavior ───────────────────────────────────────────────────

describe('Reset behavior', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('reset-blend is muted when blend values are at default (0.3 / 0.7)', async () => {
    await renderAndOpen(BASE_EP)
    const btn = screen.getByTestId('reset-blend')
    expect(btn.className).toMatch(/pointer-events-none/)
  })

  it('reset-blend is active when blend values deviate from default', async () => {
    await renderAndOpen({ ...BASE_EP, blend_start: 0.2, blend_end: 0.8 })
    const btn = screen.getByTestId('reset-blend')
    expect(btn.className).not.toMatch(/pointer-events-none/)
    expect(btn.className).toMatch(/text-foreground/)
  })

  it('reset-response is muted when response is at default (0.1)', async () => {
    await renderAndOpen(BASE_EP)
    const btn = screen.getByTestId('reset-response')
    expect(btn.className).toMatch(/pointer-events-none/)
  })

  it('reset-response is active when response deviates from default', async () => {
    await renderAndOpen({ ...BASE_EP, blend_response: 0.3 })
    const btn = screen.getByTestId('reset-response')
    expect(btn.className).not.toMatch(/pointer-events-none/)
    expect(btn.className).toMatch(/text-foreground/)
  })

  it('reset-blend resets both blend_start and blend_end to defaults', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_start: 0.2, blend_end: 0.8 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const startInput = screen.getByTestId('field-blend-start') as HTMLInputElement
    const endInput   = screen.getByTestId('field-blend-end')   as HTMLInputElement
    expect(startInput.value).toBe('0.2')
    expect(endInput.value).toBe('0.8')
    await user.click(screen.getByTestId('reset-blend'))
    expect(startInput.value).toBe('0.3')
    expect(endInput.value).toBe('0.7')
  })

  it('reset-response resets blend_response to default (0.1)', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_response: 0.3 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const input = screen.getByTestId('field-blend-response') as HTMLInputElement
    expect(input.value).toBe('0.3')
    await user.click(screen.getByTestId('reset-response'))
    expect(input.value).toBe('0.1')
  })

  it('reset-blend resets to muted state after reset', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_start: 0.2, blend_end: 0.8 })
    const btn = screen.getByTestId('reset-blend')
    await user.click(btn)
    expect(btn.className).toMatch(/pointer-events-none/)
  })
})

// ── Suite 5: Mode switch safety ───────────────────────────────────────────────

describe('Mode switch safety', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('no API call fires when switching between modes', async () => {
    const user = await renderAndOpen()
    await user.click(screen.getByTestId('mode-expert-btn'))
    await user.click(screen.getByTestId('mode-standard-btn'))
    expect(mapi.updateEnergyProfile).not.toHaveBeenCalled()
  })

  it('blend values preserved after expert → standard switch', async () => {
    const user = await renderAndOpen({ ...BASE_EP, blend_start: 0.25, blend_end: 0.75 })
    await user.click(screen.getByTestId('mode-expert-btn'))
    const startBefore = (screen.getByTestId('field-blend-start') as HTMLInputElement).value
    await user.click(screen.getByTestId('mode-standard-btn'))
    // Re-enter expert to verify value persisted
    await user.click(screen.getByTestId('mode-expert-btn'))
    const startAfter = (screen.getByTestId('field-blend-start') as HTMLInputElement).value
    expect(startBefore).toBe(startAfter)
  })
})

// ── Suite 6: Output preview ───────────────────────────────────────────────────

describe('Output preview', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows label-preview when not live', async () => {
    await renderAndOpen()
    expect(screen.getByTestId('label-preview')).toBeDefined()
    expect(screen.queryByTestId('label-live-output')).toBeNull()
  })

  it('shows label-live-output when active_energy_profile_id matches', async () => {
    mockUsePreviewSocket.mockReturnValue({
      ...DEFAULT_PREVIEW,
      energy: 0.6,
      mix: 0.7,
      channel_colours: [
        { r: 0.8, g: 0.1, b: 0.1 },
        { r: 0.1, g: 0.8, b: 0.1 },
      ],
      status: { active_energy_profile_id: 'ep1' },
    })
    mapi.getEnergyProfiles.mockResolvedValue([BASE_EP])
    mapi.getEffects.mockResolvedValue([BASE_EFFECT])
    mapi.updateEnergyProfile.mockResolvedValue({ ...BASE_EP })

    const user = userEvent.setup()
    render(<EnergyProfiles />)
    await user.click(await screen.findByRole('button', { name: /Edit/i }))

    expect(screen.getByTestId('label-live-output')).toBeDefined()
    expect(screen.queryByTestId('label-preview')).toBeNull()
  })

  it('shows live-channel-dots when live with channel_colours', async () => {
    mockUsePreviewSocket.mockReturnValue({
      ...DEFAULT_PREVIEW,
      energy: 0.6,
      channel_colours: [
        { r: 0.8, g: 0.1, b: 0.1 },
        { r: 0.1, g: 0.8, b: 0.1 },
        { r: 0.1, g: 0.1, b: 0.8 },
      ],
      status: { active_energy_profile_id: 'ep1' },
    })
    mapi.getEnergyProfiles.mockResolvedValue([BASE_EP])
    mapi.getEffects.mockResolvedValue([BASE_EFFECT])
    mapi.updateEnergyProfile.mockResolvedValue({ ...BASE_EP })

    const user = userEvent.setup()
    render(<EnergyProfiles />)
    await user.click(await screen.findByRole('button', { name: /Edit/i }))

    expect(screen.getByTestId('live-channel-dots')).toBeDefined()
    expect(screen.getByTestId('channel-dot-0')).toBeDefined()
    expect(screen.getByTestId('channel-dot-1')).toBeDefined()
    expect(screen.getByTestId('channel-dot-2')).toBeDefined()
  })

  it('output-preview-section always renders', async () => {
    await renderAndOpen()
    expect(screen.getByTestId('output-preview-section')).toBeDefined()
  })
})

// ── Suite 7: Entity operations ────────────────────────────────────────────────

describe('Entity operations', () => {
  beforeEach(() => { vi.clearAllMocks() })

  describe('Create', () => {
    it('clicking New energy profile shows workspace with empty name', async () => {
      await renderAndNew()
      const nameInput = screen.getByTestId('editor-name-input') as HTMLInputElement
      expect(nameInput.value).toBe('')
    })

    it('cancel create button hides workspace', async () => {
      const user = await renderAndNew()
      await user.click(screen.getByRole('button', { name: /Cancel/i }))
      expect(screen.queryByTestId('editor-name-input')).toBeNull()
    })
  })

  describe('Edit', () => {
    it('edit opens workspace with profile name', async () => {
      await renderAndOpen()
      const nameInput = screen.getByTestId('editor-name-input') as HTMLInputElement
      expect(nameInput.value).toBe('Test Profile')
    })

    it('save calls updateEnergyProfile with correct id', async () => {
      const user = await renderAndOpen()
      mapi.getEnergyProfiles.mockResolvedValue([BASE_EP])
      await user.click(screen.getByTestId('editor-save'))
      await screen.findByRole('button', { name: /Edit/i })
      expect(mapi.updateEnergyProfile).toHaveBeenCalledWith('ep1', expect.objectContaining({
        name: 'Test Profile',
      }))
    })
  })

  describe('Delete', () => {
    it('deleteEnergyProfile is called on confirm', async () => {
      mockUsePreviewSocket.mockReturnValue(DEFAULT_PREVIEW)
      mapi.getEnergyProfiles.mockResolvedValue([BASE_EP])
      mapi.getEffects.mockResolvedValue([BASE_EFFECT])
      mapi.deleteEnergyProfile.mockResolvedValue(undefined)
      mapi.getEnergyProfiles.mockResolvedValueOnce([BASE_EP]).mockResolvedValue([])

      const user = userEvent.setup()
      render(<EnergyProfiles />)
      await screen.findByText('Test Profile')

      await user.click(screen.getByRole('button', { name: /Delete/i }))
      const confirmBtn = await screen.findByRole('button', { name: /Confirm/i })
      await user.click(confirmBtn)
      expect(mapi.deleteEnergyProfile).toHaveBeenCalledWith('ep1')
    })
  })
})

// ── Suite 8: Missing / edge data ──────────────────────────────────────────────

describe('Missing/edge data', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('empty profile list shows empty state without crash', async () => {
    mockUsePreviewSocket.mockReturnValue(DEFAULT_PREVIEW)
    mapi.getEnergyProfiles.mockResolvedValue([])
    mapi.getEffects.mockResolvedValue([])
    render(<EnergyProfiles />)
    await screen.findByText(/No energy profiles yet/)
  })

  it('new profile editor renders without crash', async () => {
    await renderAndNew()
    expect(screen.getByTestId('blend-zone-bar')).toBeDefined()
    expect(screen.getByTestId('current-blend-bar')).toBeDefined()
  })

  it('expert mode inputs default to correct values for new profile', async () => {
    const user = await renderAndNew()
    await user.click(screen.getByTestId('mode-expert-btn'))
    const startInput    = screen.getByTestId('field-blend-start')    as HTMLInputElement
    const endInput      = screen.getByTestId('field-blend-end')      as HTMLInputElement
    const responseInput = screen.getByTestId('field-blend-response') as HTMLInputElement
    expect(startInput.value).toBe('0.3')
    expect(endInput.value).toBe('0.7')
    expect(responseInput.value).toBe('0.1')
  })
})
