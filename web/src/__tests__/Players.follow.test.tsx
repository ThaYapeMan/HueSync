import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Players } from '../pages/Players'
import { updateVirtualPlayer } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getVirtualPlayers: vi.fn().mockResolvedValue([{
    id: 'managed', type: 'LMS', player_name: 'HueSync', lms_host: 'lms.local',
    lms_port: 3483, player_mac: 'aa:bb:cc:dd:ee:01',
    follow_player_mac: 'aa:bb:cc:dd:ee:02',
  }]),
  updateVirtualPlayer: vi.fn().mockResolvedValue(undefined),
  getCouplings: vi.fn().mockResolvedValue([]),
  listLmsPlayers: vi.fn().mockResolvedValue([
    { playerid: '11:22:33:44:55:66', name: 'Sonos Living Room' },
  ]),
}))

describe('Follow player discovery', () => {
  const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
  beforeAll(() => {
    // Radix scrolls the focused option; jsdom has no layout/scroll implementation.
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  })
  afterAll(() => {
    if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll)
    else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
  })

  it('retains the saved target omitted by discovery while offering external targets', async () => {
    const user = userEvent.setup()
    render(<Players />)
    await user.click(await screen.findByRole('button', { name: 'Edit', exact: true }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Discover', exact: true }))
    const follow = within(dialog).getAllByRole('combobox').find(
      element => element.textContent === 'aa:bb:cc:dd:ee:02',
    )!
    expect(follow).toHaveTextContent('aa:bb:cc:dd:ee:02')
    // Open with the keyboard; jsdom does not implement pointer capture.
    fireEvent.keyDown(follow, { key: 'ArrowDown' })
    expect(await screen.findByRole('option', { name: 'Sonos Living Room (11:22:33:44:55:66)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'aa:bb:cc:dd:ee:02' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('option', { name: /aa:bb:cc:dd:ee:01/ })).not.toBeInTheDocument()
  })
  it('switches to passive sync-group mode and saves without losing the manual MAC', async () => {
    const user = userEvent.setup()
    render(<Players />)
    await user.click(await screen.findByRole('button', { name: 'Edit', exact: true }))
    const mode = screen.getByRole('combobox', { name: 'Follow mode' })
    expect(mode).toHaveTextContent('Manual')
    fireEvent.keyDown(mode, { key: 'ArrowDown' })
    await user.click(await screen.findByRole('option', { name: 'Automatic — LMS sync group' }))
    expect(screen.queryByRole('button', { name: 'Discover', exact: true })).not.toBeInTheDocument()
    expect(screen.getByText(/never changes its membership or sends playback commands/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    expect(updateVirtualPlayer).toHaveBeenCalledWith('managed', expect.objectContaining({
      follow_mode: 'sync_group', follow_player_mac: 'aa:bb:cc:dd:ee:02',
    }))
  })

})
