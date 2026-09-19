import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { LiveEnergySource } from '../components/LiveEnergySource'
import { EnergySourceControls } from '../components/EnergyBlendEditor'
import { updateEnergyProfile, type EnergyProfile } from '../lib/api'
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), updateEnergyProfile: vi.fn() }))
const initial = { id:'e', name:'Spectrum RGB', energy_source:'sustained', lufs_floor:-30, lufs_ceiling:-8, adaptation_tau_s:60 } as EnergyProfile
function Harness() {
  const [profile, setProfile] = useState(initial)
  return <LiveEnergySource profile={profile} active onUpdated={setProfile} />
}
afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  let persisted = initial
  vi.mocked(updateEnergyProfile).mockImplementation(async (_id, body) => { persisted = {...persisted, ...body}; return persisted })
})
it('applies modes immediately, commits validated numbers only on blur/Enter, and avoids double PATCH', async () => {
  const user = userEvent.setup()
  render(<Harness />)
  const group = screen.getByRole('radiogroup', { name:'Energy source' })
  expect(within(group).getAllByRole('radio')).toHaveLength(3)
  await user.click(screen.getByRole('radio', {name:'Fixed loudness'}))
  await waitFor(() => expect(updateEnergyProfile).toHaveBeenCalledWith('e', {energy_source:'loudness_fixed'}))
  const floor = screen.getByLabelText(/Floor/)
  await user.clear(floor); await user.type(floor, '-25')
  expect(updateEnergyProfile).toHaveBeenCalledTimes(1)
  await user.keyboard('{Enter}')
  await waitFor(() => expect(updateEnergyProfile).toHaveBeenCalledTimes(2))
  expect(updateEnergyProfile).toHaveBeenLastCalledWith('e', {lufs_floor:-25})
  await user.click(floor); await user.clear(floor); await user.type(floor, '-5'); await user.tab()
  expect(screen.getByRole('alert')).toHaveTextContent('lufs_floor must be below lufs_ceiling')
  expect(updateEnergyProfile).toHaveBeenCalledTimes(2)
  await user.click(screen.getByRole('radio', {name:'Adaptive loudness'}))
  await screen.findByLabelText(/Adaptation/)
  expect(screen.queryByLabelText(/Floor/)).not.toBeInTheDocument()
  await user.click(screen.getByRole('radio', {name:'Sustained'}))
  await waitFor(() => expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument())
})
it('uses the same validation messages and labels as the profile editor', () => {
  const props = { source:'loudness_fixed', floor:'-5', ceiling:'-8', tau:'60', onChange:vi.fn() }
  const { rerender } = render(<EnergySourceControls {...props} />)
  const message = screen.getByRole('alert').textContent
  expect(screen.getByRole('button', {name:/Fixed loudness/})).toHaveAttribute('aria-pressed','true')
  rerender(<EnergySourceControls {...props} compact />)
  expect(screen.getByRole('alert').textContent).toBe(message)
  expect(screen.getByRole('radio', {name:'Fixed loudness'})).toHaveAttribute('aria-checked','true')
  rerender(<EnergySourceControls {...props} floor="" compact />)
  expect(screen.getByRole('alert')).toHaveTextContent('Energy source settings must be finite')
})
it('keeps all segments visible but disabled without an active coupling', () => {
  render(<LiveEnergySource active={false} onUpdated={vi.fn()} />)
  expect(screen.getByText('No active coupling')).toBeInTheDocument()
  for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled()
  expect(screen.queryByText('Open profile')).not.toBeInTheDocument()
})
it('supports arrow-key selection and shows server failure without changing persisted selection', async () => {
  const user = userEvent.setup()
  vi.mocked(updateEnergyProfile).mockRejectedValue(new Error('Connection failed'))
  render(<Harness />)
  screen.getByRole('radio', {name:'Sustained'}).focus()
  await user.keyboard('{ArrowRight}')
  expect(await screen.findByRole('alert')).toHaveTextContent('Connection failed')
  expect(updateEnergyProfile).toHaveBeenCalledWith('e', {energy_source:'loudness_fixed'})
  expect(screen.getByRole('radio', {name:'Sustained'})).toHaveAttribute('aria-checked','true')
})
