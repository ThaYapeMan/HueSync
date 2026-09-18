import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrackProgress } from '@/components/TrackProgress'
import type { TrackPosition } from '@/hooks/usePreviewSocket'

import { controlCouplingTransport } from '@/lib/api'
vi.mock('@/lib/api', () => ({ controlCouplingTransport: vi.fn() }))

const track: TrackPosition = {
  title: 'One song', artist: 'One artist', position_s: 83, duration_s: 225, playing: true,
}
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

describe('player-independent track display', () => {
  it('renders either source and interpolates the delivery anchor', () => {
    vi.useFakeTimers()
    let now = 1000
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    const view = render(<TrackProgress track={track} connected />)
    expect(screen.getByText('One song')).toBeInTheDocument()
    expect(screen.getByText('One artist')).toBeInTheDocument()
    expect(screen.getByTitle('One song — One artist')).toHaveTextContent('One song — One artist')
    expect(screen.queryByText('Track', { exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar').firstElementChild).toHaveStyle({ width: `${100 * 83 / 225}%` })
    expect(screen.getByText('1:23 / 3:45')).toBeInTheDocument()
    now += 2000
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '85')
    view.rerender(<TrackProgress track={{ ...track, position_s: 50, playing: false }} connected />)
    now += 10000
    act(() => vi.advanceTimersByTime(10000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByText('0:50 / 3:45 · Paused / stopped')).toBeInTheDocument()
    view.rerender(<TrackProgress track={{ ...track, position_s: 224 }} connected />)
    now += 10000
    act(() => vi.advanceTimersByTime(10000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '225')
  })
  it('does not invent a duration or progress when metadata is unavailable', () => {
    const view = render(<TrackProgress track={null} connected />)
    expect(screen.getByText('Track information unavailable')).toBeInTheDocument()
    expect(screen.getByTitle('Track information unavailable')).toHaveTextContent(/^Track information unavailable$/)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    view.rerender(<TrackProgress track={{ ...track, duration_s: null }} connected />)
    expect(screen.getByText('1:23 / —')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
  it('keeps long metadata on one truncating line and leaves the time visible', () => {
    const title = 'Long song title '.repeat(20).trim()
    const view = render(<TrackProgress track={{ ...track, title }} connected />)
    const metadata = screen.getByTitle(`${title} — One artist`)
    expect(metadata).toHaveClass('min-w-0', 'truncate', 'flex-1')
    expect(metadata.parentElement).toHaveClass('flex', 'items-center')
    expect(screen.getByText('1:23 / 3:45')).toHaveClass('shrink-0', 'whitespace-nowrap')
    expect(screen.getByRole('progressbar')).toHaveClass('absolute', 'bottom-0')
    view.rerender(<TrackProgress track={{ ...track, artist: null }} connected />)
    expect(screen.getByTitle('One song')).toHaveTextContent(/^One song$/)
  })
  it('shows disconnected state without advancing an uncorrected clock', () => {
    vi.useFakeTimers()
    render(<TrackProgress track={track} connected={false} />)
    act(() => vi.advanceTimersByTime(5000))
    expect(screen.getByText('1:23 / 3:45 · Disconnected')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '83')
  })
})


describe('followed-player controls', () => {
  const transport = { couplingId: 'active', targetName: 'Living room' }
  it('sends all four actions without optimistically changing playback state', async () => {
    vi.mocked(controlCouplingTransport).mockResolvedValue({ ok: true, target_mac: 'room' })
    const view = render(<TrackProgress track={track} connected transport={transport} />)
    expect(screen.getByRole('group')).toHaveAttribute('title', 'Controls the followed player (Living room)')
    for (const [label, action] of [['Previous', 'previous'], ['Pause', 'pause'], ['Next', 'next'], ['Stop', 'stop']]) {
      fireEvent.click(screen.getByRole('button', { name: label }))
      await waitFor(() => expect(controlCouplingTransport).toHaveBeenLastCalledWith('active', action))
      await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled())
    }
    expect(screen.queryByRole('button', { name: 'Play' })).not.toBeInTheDocument()
    view.rerender(<TrackProgress track={{ ...track, playing: false }} connected transport={transport} />)
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    await waitFor(() => expect(controlCouplingTransport).toHaveBeenLastCalledWith('active', 'play'))
  })
  it('shows command failures and disables controls while disconnected', async () => {
    vi.mocked(controlCouplingTransport).mockRejectedValue(new Error('No controllable followed LMS player'))
    const view = render(<TrackProgress track={track} connected transport={transport} />)
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No controllable followed LMS player')
    view.rerender(<TrackProgress track={track} connected={false} transport={transport} />)
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
    view.rerender(<TrackProgress track={track} connected />)
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
  })
})
