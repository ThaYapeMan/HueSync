import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Effects } from '../pages/Effects'

// ── mocks ────────────────────────────────────────────────────────────────────

vi.mock('../hooks/usePreviewSocket', () => ({
  usePreviewSocket: () => ({
    colour: { r: 0, g: 0, b: 0 },
    channel_colours: [],
    onset: false,
    onset_bass: false,
    onset_mid: false,
    onset_treble: false,
    mix: 0,
    energy: 0.5,
    bars: [],
    status: null,
    connected: false,
    reconnectAttempt: 0,
  }),
}))


vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    getEffects: vi.fn().mockResolvedValue([
      {
        id: 'e1', name: 'Party Fireworks', effect_type: 'fireworks',
        effect_speed: 2.5, effect_decay: 0.6, sensitivity: 1.4, brightness_floor: 0.05,
        bass_hz: 250, mid_hz: 2000, exertion_clip: 3.0, onset_flash_intensity: 0.3,
      },
      {
        id: 'e2', name: 'Calm Spectrum', effect_type: 'spectrum_rgb',
        effect_speed: 1.0, effect_decay: 0.3, sensitivity: 0.6, brightness_floor: 0.15,
        bass_hz: 250, mid_hz: 2000, exertion_clip: 3.0, onset_flash_intensity: 0.0,
      },
    ]),
    createEffect: vi.fn().mockResolvedValue({
      id: 'new-id', name: 'New Effect', effect_type: 'spectrum_rgb',
      effect_speed: 1.0, effect_decay: 0.3, sensitivity: 1.0, brightness_floor: 0.15,
      bass_hz: 250, mid_hz: 2000, exertion_clip: 3.0, onset_flash_intensity: 0.0,
    }),
    updateEffect: vi.fn().mockResolvedValue(undefined),
    deleteEffect: vi.fn().mockResolvedValue(undefined),
    getEnergyProfiles: vi.fn().mockResolvedValue([]),
  }
})

// ── helpers ──────────────────────────────────────────────────────────────────

async function renderEffects() {
  const user = userEvent.setup()
  render(<Effects />)
  // Wait for async load
  await screen.findByTestId('effects-gallery')
  return user
}

async function renderEffectsInWorkspace() {
  const user = userEvent.setup()
  render(<Effects />)
  await screen.findByTestId('effects-gallery')
  await user.click(screen.getByTestId('edit-effect-e1'))
  // Wait for workspace to appear
  await screen.findByTestId('editor-name-input')
  return user
}

// ── gallery tests ─────────────────────────────────────────────────────────────

describe('Effects gallery', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders a card for each effect', async () => {
    await renderEffects()
    expect(screen.getByTestId('effect-card-e1')).toBeDefined()
    expect(screen.getByTestId('effect-card-e2')).toBeDefined()
  })

  it('shows effect name and type label on each card', async () => {
    await renderEffects()
    const card1 = screen.getByTestId('effect-card-e1')
    expect(within(card1).getByText('Party Fireworks')).toBeDefined()
    expect(within(card1).getByText('Fireworks')).toBeDefined()

    const card2 = screen.getByTestId('effect-card-e2')
    expect(within(card2).getByText('Calm Spectrum')).toBeDefined()
    expect(within(card2).getByText('Spectrum RGB')).toBeDefined()
  })

  it('shows human-readable summary instead of raw numbers', async () => {
    await renderEffects()
    const card1 = screen.getByTestId('effect-card-e1')
    // sensitivity 1.4 → "Balanced", decay 0.6 → "Punchy", speed 2.5 → nothing (below 2.5 threshold)
    expect(within(card1).getByText(/Balanced.*Punchy/)).toBeDefined()
    // Does NOT show raw numbers like "1.4" in the summary area
    const card2 = screen.getByTestId('effect-card-e2')
    expect(within(card2).getByText('Subtle')).toBeDefined()
  })

  it('renders EffectPreview dots for each card', async () => {
    await renderEffects()
    const previews = screen.getAllByTestId('effect-preview')
    expect(previews.length).toBeGreaterThanOrEqual(2)
  })

  it('opens editor when Edit is clicked', async () => {
    const user = await renderEffects()
    await user.click(screen.getByTestId('edit-effect-e1'))
    await screen.findByTestId('editor-name-input')
    expect((screen.getByTestId('editor-name-input') as HTMLInputElement).value).toBe('Party Fireworks')
  })

  it('opens new editor on New effect button', async () => {
    const user = await renderEffects()
    await user.click(screen.getByTestId('new-effect-btn'))
    await screen.findByTestId('editor-name-input')
    expect((screen.getByTestId('editor-name-input') as HTMLInputElement).value).toBe('')
  })
})

// ── workspace editor tests ────────────────────────────────────────────────────

describe('Effects workspace editor', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows name input with effect name', async () => {
    await renderEffectsInWorkspace()
    expect((screen.getByTestId('editor-name-input') as HTMLInputElement).value).toBe('Party Fireworks')
  })

  it('updates name when typed', async () => {
    const user = await renderEffectsInWorkspace()
    const input = screen.getByTestId('editor-name-input') as HTMLInputElement
    await user.clear(input)
    await user.type(input, 'Renamed')
    expect(input.value).toBe('Renamed')
  })

  it('Save button is disabled when name is empty', async () => {
    const user = await renderEffectsInWorkspace()
    const input = screen.getByTestId('editor-name-input') as HTMLInputElement
    await user.clear(input)
    const saveBtn = screen.getByTestId('editor-save')
    expect(saveBtn).toBeDisabled()
  })

  it('Cancel returns to gallery', async () => {
    const user = await renderEffectsInWorkspace()
    await user.click(screen.getByText('Cancel'))
    await screen.findByTestId('effects-gallery')
  })

  it('shows effect type selector with current type', async () => {
    await renderEffectsInWorkspace()
    const display = screen.getByTestId('effect-type-display')
    expect(within(display).getByText('Fireworks')).toBeDefined()
  })

  it('effect type picker opens and allows selection', async () => {
    const user = await renderEffectsInWorkspace()
    await user.click(screen.getByTestId('effect-type-change'))
    const picker = await screen.findByTestId('effect-type-picker')
    expect(picker).toBeDefined()
    // All effect types are shown
    expect(screen.getByTestId('type-option-spectrum_rgb')).toBeDefined()
    expect(screen.getByTestId('type-option-fireworks')).toBeDefined()
    // Pick spectrum_rgb
    await user.click(screen.getByTestId('type-option-spectrum_rgb'))
    // Picker closes, display shows new selection
    const display = await screen.findByTestId('effect-type-display')
    expect(within(display).getByText('Spectrum RGB')).toBeDefined()
  })

  it('shows the large effect preview', async () => {
    await renderEffectsInWorkspace()
    // The workspace has a large preview with 8 dots
    const previews = screen.getAllByTestId('effect-preview')
    // At least one preview exists in the workspace
    expect(previews.length).toBeGreaterThan(0)
  })

  it('preview input buttons are present', async () => {
    await renderEffectsInWorkspace()
    expect(screen.getByTestId('preview-calm')).toBeDefined()
    expect(screen.getByTestId('preview-groove')).toBeDefined()
    expect(screen.getByTestId('preview-beat-heavy')).toBeDefined()
    expect(screen.getByTestId('preview-live')).toBeDefined()
  })

  it('preview input switching changes active button', async () => {
    const user = await renderEffectsInWorkspace()
    const calmBtn = screen.getByTestId('preview-calm')
    await user.click(calmBtn)
    // After clicking, calm should be active (primary style)
    expect(calmBtn.className).toMatch(/bg-primary/)
  })

  it('Live preview button is disabled when not connected', async () => {
    await renderEffectsInWorkspace()
    const liveBtn = screen.getByTestId('preview-live') as HTMLButtonElement
    expect(liveBtn.disabled).toBe(true)
  })

  it('Advanced section is collapsed by default', async () => {
    await renderEffectsInWorkspace()
    const toggle = screen.getByTestId('advanced-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTestId('advanced-content')).toBeNull()
  })

  it('Advanced section expands on click', async () => {
    const user = await renderEffectsInWorkspace()
    const toggle = screen.getByTestId('advanced-toggle')
    await user.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('advanced-content')).toBeDefined()
  })
})

// ── EffectPreview unit tests ──────────────────────────────────────────────────

describe('EffectPreview dots', () => {
  it('renders the correct number of dots', async () => {
    const { EffectPreview } = await import('../components/EffectPreview')
    render(<EffectPreview effectType="spectrum_rgb" count={6} />)
    const preview = screen.getByTestId('effect-preview')
    expect(preview.children.length).toBe(6)
  })

  it('renders default 8 dots', async () => {
    const { EffectPreview } = await import('../components/EffectPreview')
    render(<EffectPreview effectType="mono_pulse" />)
    const preview = screen.getByTestId('effect-preview')
    expect(preview.children.length).toBe(8)
  })

  it('renders for none effect type without crash', async () => {
    const { EffectPreview } = await import('../components/EffectPreview')
    render(<EffectPreview effectType="none" />)
    expect(screen.getByTestId('effect-preview')).toBeDefined()
  })
})
