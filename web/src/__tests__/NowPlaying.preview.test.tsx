import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NowPlaying } from '../pages/NowPlaying'
import type { SocketStatus } from '../hooks/usePreviewSocket'
import { getAnalysers, getCouplings, getVirtualPlayers } from '../lib/api'

vi.mock('../lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getCouplings: vi.fn(), getAnalysers: vi.fn(), getVirtualPlayers: vi.fn(),
  getZoneChannels: vi.fn().mockResolvedValue([{ channel_id: 1, x: .5, y: 0, z: .5 }]),
  activateCoupling: vi.fn(), deactivateCoupling: vi.fn(), restartCouplingCava: vi.fn(),
}))

const status = {
  active_coupling_id: 'c', active_zone_id: 'z', active_energy_profile_id: 'e',
  active_bars_source: 'pcm_pipeline', active_player_type: null,
  sync_master_name: 'Living room', sync_master: 'aa:bb:cc:dd:ee:ff', applied_delay_ms: 1100,
  processes: { squeezelite: true, cava: false }, bridge_connected: true,
  effect_type: 'spectrum_rgb', onset_method: 'combined',
} as SocketStatus
const props = { colour: { r: 1, g: 0, b: 0 }, channel_colours: [], onset: false, bars: [] }

afterEach(cleanup)
beforeEach(() => {
  vi.mocked(getCouplings).mockResolvedValue([{ id: 'c', name: 'Room', analyser_id: 'a', player_id: 'p' }] as never)
  vi.mocked(getAnalysers).mockResolvedValue([{ id: 'a', name: 'Canonical analyser', bars_source: 'pcm_pipeline', spectrum_backend: 'cavacore', onset_method: 'combined' }] as never)
  vi.mocked(getVirtualPlayers).mockResolvedValue([{ id: 'p', type: 'LMS' }] as never)
})

it('resolves the six technical status rows in order, preserving the preview layout', async () => {
  render(<NowPlaying {...props} status={status} />)
  const dl = screen.getByLabelText('Session status')
  await within(dl).findByText('PCM Pipeline')
  expect(within(dl).getByText('CAVA Core')).toBeInTheDocument()
  expect(within(dl).getByText('Combined')).toBeInTheDocument()
  expect(within(dl).queryByText('Analyser')).not.toBeInTheDocument()
  expect(within(dl).queryByText('Energy Profile')).not.toBeInTheDocument()
  expect(within(dl).queryByText('Canonical analyser')).not.toBeInTheDocument()
  expect([...dl.querySelectorAll('dt')].map(el => el.textContent)).toEqual([
    'Audio source', 'Spectrum engine', 'Beat detection', 'Effect', 'Sync master', 'Delay',
  ])
  expect(within(dl).getByText('Living room')).toBeInTheDocument()
  expect(within(dl).getByText('aa:bb:cc:dd:ee:ff')).toBeInTheDocument()
  expect(within(dl).getByText('1100 ms')).toBeInTheDocument()
  expect(screen.queryByText('Onset method')).not.toBeInTheDocument()
  for (const id of ['colour-preview-size', 'floorplan-preview-size']) {
    expect(screen.getByTestId(id)).toHaveClass('aspect-square', 'w-full')
  }
  expect(screen.getByTestId('live-preview-grid')).toHaveClass('grid-cols-1', 'lg:grid-cols-3')
  expect(screen.getByLabelText('Light floorplan')).toHaveAttribute('viewBox', '0 0 200 200')
  expect(screen.getByText('front')).toBeInTheDocument()
  expect(screen.getByText('Bridge API')).toBeInTheDocument()
  expect(screen.queryByText('Bridge', { exact: true })).not.toBeInTheDocument()
  expect(within(dl).queryByText('Bridge API')).not.toBeInTheDocument()
  expect(screen.getByText('Energy blend').compareDocumentPosition(screen.getByText('Bridge API'))
    & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('shows muted placeholders while the analyser is unavailable', async () => {
  vi.mocked(getAnalysers).mockReturnValue(new Promise(() => {}))
  render(<NowPlaying {...props} status={status} />)
  await screen.findByText('squeezelite')
  const names = screen.getByLabelText('Session status').querySelectorAll('dd')
  for (const index of [0, 1, 2]) {
    expect(names[index]).toHaveTextContent('—')
    expect(names[index].firstElementChild).toHaveClass('text-muted-foreground')
  }
})

it('only shows cava on the LMS FIFO path, and never squeezelite on AirPlay', async () => {
  const view = render(<NowPlaying {...props} status={status} />)
  await screen.findByText('squeezelite') // resolves player type from the entity list
  expect(screen.queryByText('cava', { exact: true })).not.toBeInTheDocument()
  view.rerender(<NowPlaying {...props} status={{ ...status, active_bars_source: 'cava' }} />)
  expect(screen.getByText('cava', { exact: true })).toBeInTheDocument()
  view.rerender(<NowPlaying {...props} status={{ ...status, active_player_type: 'AirPlay' }} />)
  expect(screen.queryByText('squeezelite')).not.toBeInTheDocument()
  expect(screen.queryByText('cava', { exact: true })).not.toBeInTheDocument()
  expect(screen.getByText('AirPlay', { exact: true })).toBeInTheDocument()
})

it('resolves an AirPlay player from configuration when the websocket omits its type', async () => {
  vi.mocked(getVirtualPlayers).mockResolvedValue([{ id: 'p', type: 'AirPlay' }] as never)
  render(<NowPlaying {...props} status={{ ...status, active_bars_source: 'cava' }} />)
  await screen.findByText('AirPlay', { exact: true })
  expect(screen.queryByText('squeezelite')).not.toBeInTheDocument()
  expect(screen.queryByText('cava', { exact: true })).not.toBeInTheDocument()
})

it('keeps follower and latency warnings visible outside the six status rows', async () => {
  render(<NowPlaying {...props} status={{ ...status, follower_warning: 'Not synced', latency_warning: 'No latency data' }} />)
  await waitFor(() => expect(screen.getByText('Not synced')).toBeInTheDocument())
  expect(screen.getByText('No latency data')).toBeInTheDocument()
  expect(screen.getByLabelText('Session status').querySelectorAll('dt')).toHaveLength(6)
})


it('shows the external cava engine and takes beat detection from the analyser, not status', async () => {
  vi.mocked(getAnalysers).mockResolvedValue([{
    id: 'a', name: 'Custom label', bars_source: 'cava', spectrum_backend: 'v2', onset_method: 'superflux',
  }] as never)
  render(<NowPlaying {...props} status={status} />)
  const dl = screen.getByLabelText('Session status')
  await within(dl).findByText('cava (external)')
  expect(within(dl).getByText('Cava')).toBeInTheDocument()
  expect(within(dl).getByText('SuperFlux')).toBeInTheDocument()
  expect(within(dl).queryByText('Combined')).not.toBeInTheDocument()
  expect(within(dl).queryByText('V2')).not.toBeInTheDocument()
})


it('only exposes transport for LMS with a live follow target; AirPlay sync is n/a', async () => {
  const view = render(<NowPlaying {...props} status={{ ...status,
    active_player_type: 'LMS', follow_target_mac: 'aa:bb:cc:dd:ee:ff',
    follow_target_name: 'Living room',
  }} />)
  expect(screen.getByRole('group', { name: 'Controls the followed player (Living room)' })).toBeInTheDocument()
  view.rerender(<NowPlaying {...props} status={{ ...status, active_player_type: 'LMS', follow_target_mac: null }} />)
  expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  view.rerender(<NowPlaying {...props} status={{ ...status,
    active_player_type: 'AirPlay', follow_target_mac: 'stale',
  }} />)
  expect(screen.queryByRole('button', { name: 'Previous' })).not.toBeInTheDocument()
  const dl = screen.getByLabelText('Session status')
  expect(within(dl).getByText('n/a')).toHaveClass('text-muted-foreground')
  expect(within(dl).getByText('1100 ms')).toBeInTheDocument()
  await within(dl).findByText('PCM Pipeline')
})
