import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'

vi.mock('../lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('../lib/api')>(),
  getCouplings: vi.fn().mockResolvedValue([]),
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('delivers WebSocket loudness through App, clears silence and disconnected readings', () => {
  let socket: FakeSocket
  class FakeSocket {
    onopen = () => {}
    onclose = () => {}
    onmessage = (_event: { data: string }) => {}
    constructor() { socket = this }
    close() {}
  }
  vi.stubGlobal('WebSocket', FakeSocket)
  render(<App />)
  act(() => socket.onopen())
  function frame(momentary: number | null) {
    act(() => socket.onmessage({ data: JSON.stringify({
      type: 'frame', colour: { r: 0, g: 0, b: 0 }, onset: false, mix: .35,
      loudness_momentary_lufs: momentary, loudness_short_term_lufs: -20,
    }) }))
  }
  frame(-18.4)
  expect(screen.getByTestId('momentary-loudness')).toHaveTextContent('-18.4 LUFS')
  expect(screen.getByText('35%')).toBeInTheDocument()
  frame(null)
  expect(screen.getByTestId('momentary-loudness')).toHaveTextContent('— LUFS')
  frame(-21)
  act(() => socket.onclose())
  expect(screen.getByTestId('momentary-loudness')).toHaveTextContent('— LUFS')
})
