import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { deleteVirtualPlayer } from '../lib/api'

describe('referenced entity deletion', () => {
  it('shows API 409 blockers, keeps the dialog open and permits a retry', async () => {
    const user = userEvent.setup()
    const detail = 'Cannot delete: referenced by couplings: Living room (coupling-1) via player_id.'
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail }), { status: 409 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    try {
      render(<ConfirmDialog trigger={<button>Delete player</button>} title="Delete player"
        description="Delete this player?" onConfirm={() => deleteVirtualPlayer('player-1')} />)
      await user.click(screen.getByRole('button', { name: 'Delete player' }))
      await user.click(screen.getByRole('button', { name: 'Confirm' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(detail)
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Confirm' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(fetch).toHaveBeenCalledTimes(2)
    } finally { fetch.mockRestore() }
  })
})
