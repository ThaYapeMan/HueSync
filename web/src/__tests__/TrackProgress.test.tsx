import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TrackProgress } from '@/components/TrackProgress'
import { controlCouplingTransport } from '@/lib/api'
import type { TrackPosition } from '@/hooks/usePreviewSocket'
vi.mock('@/lib/api', () => ({ controlCouplingTransport: vi.fn() }))
const track: TrackPosition = { title: 'One song', artist: 'One artist', position_s: 83, duration_s: 225, playing: true }
const transport = { couplingId: 'active', targetName: 'Room', targetMac: 'room' }
beforeEach(() => { vi.mocked(controlCouplingTransport).mockReset().mockResolvedValue({ ok: true, target_mac: 'room' }) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

it('renders exactly three ordered rows, elapsed/total, solid fixed-size controls and no state label', () => {
  vi.spyOn(performance, 'now').mockReturnValue(1000)
  const view = render(<TrackProgress track={track} connected transport={transport} />)
  expect([...screen.getByTestId('track-block').children].map(el => el.getAttribute('data-testid'))).toEqual([
    'track-title', 'track-progress-row', 'track-controls',
  ])
  expect(screen.getByText('One song')).toHaveClass('truncate', 'font-medium')
  expect(screen.getByText('One artist')).toBeInTheDocument()
  expect(screen.getByTestId('track-elapsed')).toHaveTextContent('1:23')
  expect(screen.getByTestId('track-total')).toHaveTextContent('3:45')
  expect(screen.getByRole('progressbar').firstElementChild).toHaveStyle({ width: `${100 * 83 / 225}%` })
  expect(screen.getAllByRole('button')).toHaveLength(3)
  expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
  const middle = screen.getByTestId('transport-toggle')
  expect(middle).toHaveClass('h-9', 'w-9')
  expect(middle.querySelector('svg')).toHaveAttribute('fill', 'currentColor')
  view.rerender(<TrackProgress track={{ ...track, playing: false }} connected transport={transport} />)
  expect(screen.getByRole('button', { name: 'Play' })).toBe(middle)
  expect(screen.getByTestId('glyph-toggle')).toHaveClass('h-9', 'w-9')
  expect(screen.queryByText(/Paused|stopped|Disconnected/)).not.toBeInTheDocument()
})

it('interpolates, freezes on pause/disconnect, clamps at duration and retains disabled slots', () => {
  vi.useFakeTimers()
  let now = 1000
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  const view = render(<TrackProgress track={track} connected transport={transport} />)
  now += 2000
  act(() => vi.advanceTimersByTime(2000))
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '85')
  view.rerender(<TrackProgress track={{ ...track, position_s: 50, playing: false }} connected transport={transport} />)
  now += 10000
  act(() => vi.advanceTimersByTime(10000))
  expect(screen.getByTestId('track-elapsed')).toHaveTextContent('0:50')
  view.rerender(<TrackProgress track={track} connected={false} transport={transport} />)
  act(() => vi.advanceTimersByTime(10000))
  expect(screen.getByTestId('track-elapsed')).toHaveTextContent('0:50')
  for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  view.rerender(<TrackProgress track={{ ...track, position_s: 224 }} connected transport={transport} />)
  now += 10000
  act(() => vi.advanceTimersByTime(10000))
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '225')
})

it('keeps no-track slots disabled and hides only row three for AirPlay', () => {
  const view = render(<TrackProgress track={null} connected showControls />)
  expect(screen.getByText('Track information unavailable')).toBeInTheDocument()
  expect(screen.getByTestId('track-total')).toHaveTextContent('—')
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
  for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
  view.rerender(<TrackProgress track={{ ...track, duration_s: null }} connected showControls={false} />)
  expect(screen.getByTestId('track-title')).toBeInTheDocument()
  expect(screen.getByTestId('track-progress-row')).toBeInTheDocument()
  expect(screen.queryByRole('group')).not.toBeInTheDocument()
})

it('toggles on either middle icon and reports errors without optimistic state changes', async () => {
  const view = render(<TrackProgress track={track} connected transport={transport} />)
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Pause' })))
  expect(controlCouplingTransport).toHaveBeenLastCalledWith('active', 'toggle')
  expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  view.rerender(<TrackProgress track={{ ...track, playing: false }} connected transport={transport} />)
  vi.mocked(controlCouplingTransport).mockRejectedValue(new Error('Transport failed'))
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Play' })))
  expect(screen.getByRole('alert')).toHaveTextContent('Transport failed')
})

it.each([['Next', 'next', 'seek_forward'], ['Previous', 'previous', 'seek_backward']])(
  'distinguishes short tap from repeated hold for %s', async (label, tap, seek) => {
    vi.useFakeTimers()
    render(<TrackProgress track={track} connected transport={transport} />)
    const button = screen.getByRole('button', { name: label })
    const pointer = { pointerId: 1, isPrimary: true, button: 0 }
    fireEvent.pointerDown(button, pointer)
    await act(async () => vi.advanceTimersByTime(399))
    expect(controlCouplingTransport).not.toHaveBeenCalled()
    await act(async () => fireEvent.pointerUp(button, pointer))
    expect(controlCouplingTransport).toHaveBeenCalledExactlyOnceWith('active', tap)
    vi.mocked(controlCouplingTransport).mockClear()
    fireEvent.pointerDown(button, pointer)
    await act(async () => vi.advanceTimersByTime(400))
    expect(controlCouplingTransport).toHaveBeenCalledExactlyOnceWith('active', seek)
    await act(async () => vi.advanceTimersByTime(250))
    expect(controlCouplingTransport).toHaveBeenCalledTimes(2)
    await act(async () => fireEvent.pointerUp(button, pointer))
    await act(async () => vi.advanceTimersByTime(1000))
    expect(controlCouplingTransport).toHaveBeenCalledTimes(2)
  },
)

it.each(['pointerCancel', 'pointerLeave', 'disconnect', 'unmount', 'target'])(
  'cancels a hold on %s without a trailing tap', async reason => {
    vi.useFakeTimers()
    const view = render(<TrackProgress track={track} connected transport={transport} />)
    const button = screen.getByRole('button', { name: 'Next' })
    fireEvent.pointerDown(button, { pointerId: 1, isPrimary: true, button: 0 })
    if (reason === 'disconnect') view.rerender(<TrackProgress track={track} connected={false} transport={transport} />)
    else if (reason === 'unmount') view.unmount()
    else if (reason === 'target') view.rerender(<TrackProgress track={track} connected transport={{ ...transport, targetMac: 'other' }} />)
    else fireEvent[reason as 'pointerCancel' | 'pointerLeave'](button)
    await act(async () => vi.advanceTimersByTime(1000))
    expect(controlCouplingTransport).not.toHaveBeenCalled()
  },
)
