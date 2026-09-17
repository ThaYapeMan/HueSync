import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrackProgress } from '@/components/TrackProgress'
import type { TrackPosition } from '@/hooks/usePreviewSocket'

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
    expect(screen.getByText('1:23 / 3:45')).toBeInTheDocument()
    now += 2000
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '85')
    view.rerender(<TrackProgress track={{ ...track, position_s: 50, playing: false }} connected />)
    now += 10000
    act(() => vi.advanceTimersByTime(10000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    view.rerender(<TrackProgress track={{ ...track, position_s: 224 }} connected />)
    now += 10000
    act(() => vi.advanceTimersByTime(10000))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '225')
  })
  it('does not invent a duration or progress when metadata is unavailable', () => {
    const view = render(<TrackProgress track={null} connected />)
    expect(screen.getByText('Track information unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    view.rerender(<TrackProgress track={{ ...track, duration_s: null }} connected />)
    expect(screen.getByText('1:23 / —')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
  it('shows disconnected state without advancing an uncorrected clock', () => {
    render(<TrackProgress track={track} connected={false} />)
    expect(screen.getByText(/Disconnected/)).toBeInTheDocument()
  })
})
