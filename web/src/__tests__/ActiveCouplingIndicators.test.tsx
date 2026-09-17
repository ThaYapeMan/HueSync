import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Analysers } from '../pages/Analysers'
import { Players } from '../pages/Players'
import { Zones } from '../pages/Zones'
import { EnergyProfiles } from '../pages/EnergyProfiles'
import { Effects } from '../pages/Effects'

vi.mock('../hooks/usePreviewSocket', () => ({
  usePreviewSocket: () => ({ status: null, energy: 0, bars: [], channel_colours: [] }),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getCouplings: vi.fn().mockResolvedValue([
    { id: 'c1', player_id: 'p1', zone_id: 'z1', analyser_id: 'a1', energy_profile_id: 'ep1' },
    { id: 'c2', player_id: 'p2', zone_id: 'z2', analyser_id: 'a2', energy_profile_id: 'ep2' },
    { id: 'missing', energy_profile_id: 'absent' },
  ]),
  getVirtualPlayers: vi.fn().mockResolvedValue([1, 2].map(i => ({
    id: `p${i}`, player_name: `Player ${i}`, type: 'LMS', lms_host: 'localhost',
  }))),
  getZones: vi.fn().mockResolvedValue([1, 2].map(i => ({
    id: `z${i}`, name: `Zone ${i}`, type: 'hue', controller_id: 'ctrl',
  }))),
  getControllers: vi.fn().mockResolvedValue([]),
  getAnalysers: vi.fn().mockResolvedValue([1, 2].map(i => ({
    id: `a${i}`, name: `Analyser ${i}`, onset_method: 'multiband', bars: 30, bars_source: 'pcm_pipeline',
  }))),
  getEnergyProfiles: vi.fn().mockResolvedValue([
    { id: 'ep1', name: 'Energy 1', high_energy_effect_id: 'e1', low_energy_effect_id: 'e2', blend_start: 0.3, blend_end: 0.7, blend_response: 0.1 },
    { id: 'ep2', name: 'Energy 2', high_energy_effect_id: 'e3', low_energy_effect_id: '', blend_start: 0.3, blend_end: 0.7, blend_response: 0.1 },
  ]),
  getEffects: vi.fn().mockResolvedValue([1, 2, 3].map(i => ({
    id: `e${i}`, name: `Effect ${i}`, effect_type: 'spectrum_rgb',
  }))),
}))

const label = 'In use by active coupling'

describe('active Coupling references on list pages', () => {
  it.each([
    [Analysers, 'Analyser', '[data-testid^="analyser-item-"]'],
    [Players, 'Player', 'tr'],
    [Zones, 'Zone', 'tr'],
    [EnergyProfiles, 'Energy', 'tr'],
  ] as const)('%s follows activation changes and clears on deactivation', async (Page, name, rowSelector) => {
    const { rerender } = render(<Page activeCouplingId="c1" />)
    await screen.findAllByText(`${name} 1`)
    const first = screen.getAllByText(`${name} 1`)[0].closest(rowSelector)!
    const second = screen.getAllByText(`${name} 2`)[0].closest(rowSelector)!
    expect(first.querySelector('[aria-label="' + label + '"]')).toHaveClass('text-green-400')
    expect(second.querySelector('[aria-label="' + label + '"]')).toBeNull()
    rerender(<Page activeCouplingId="c2" />)
    expect(first.querySelector('[aria-label="' + label + '"]')).toBeNull()
    expect(second.querySelector('[aria-label="' + label + '"]')).not.toBeNull()
    rerender(<Page activeCouplingId={null} />)
    expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    rerender(<Page activeCouplingId="unknown" />)
    expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
  })

  it('marks both configured Effects, then only high when low is unset', async () => {
    const { rerender } = render(<Effects activeCouplingId="c1" />)
    await screen.findByTestId('effect-card-e1')
    expect(screen.getAllByLabelText(label)).toHaveLength(2)
    for (const id of ['e1', 'e2']) {
      expect(screen.getByTestId(`effect-card-${id}`).querySelector('[aria-label="' + label + '"]')).not.toBeNull()
    }
    rerender(<Effects activeCouplingId="c2" />)
    expect(screen.getAllByLabelText(label)).toHaveLength(1)
    expect(screen.getByTestId('effect-card-e3')).toContainElement(screen.getByLabelText(label))
    for (const id of ['missing', 'unknown', null]) {
      rerender(<Effects activeCouplingId={id} />)
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    }
  })
})
