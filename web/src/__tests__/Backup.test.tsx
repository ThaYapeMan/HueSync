import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Backup } from '@/pages/Backup'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
})
afterEach(() => vi.unstubAllGlobals())

function chooseFile(name = 'backup.json') {
  const file = new File(['{"format":"huesync-config-backup"}'], name, { type: 'application/json' })
  fireEvent.change(screen.getByLabelText(/Backup JSON file/), { target: { files: [file] } })
  return file
}

describe('Backup and restore', () => {
  it('warns about secrets and never uploads merely selected files', () => {
    render(<Backup />)
    expect(screen.getByText(/Keep this file secure/)).toBeInTheDocument()
    expect(screen.getByText(/HueSync has no authentication/)).toBeInTheDocument()
    chooseFile()
    expect(screen.getByText('Selected file: backup.json')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Restore configuration' })).toBeDisabled()
  })

  it('requires confirmation, uploads the file and reports inactive success', async () => {
    fetchMock.mockResolvedValue({ ok: true })
    render(<Backup />)
    const file = chooseFile()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Restore configuration' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Configuration restored'))
    expect(fetchMock).toHaveBeenCalledWith('/api/config/import', expect.objectContaining({
      method: 'POST', body: file, headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
    }))
    expect(screen.getByRole('status')).toHaveTextContent('No service restart is required')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('resets confirmation when the selected file changes', () => {
    render(<Backup />)
    chooseFile()
    fireEvent.click(screen.getByRole('checkbox'))
    chooseFile('another.json')
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Restore configuration' })).toBeDisabled()
  })

  it('shows server validation or active-session errors without backup contents', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409,
      json: async () => ({ detail: { code: 'runtime_active', message: 'Deactivate the current Coupling first' } }),
    })
    render(<Backup />)
    chooseFile()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Restore configuration' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Deactivate'))
  })

  it('rejects oversize files before upload', async () => {
    render(<Backup />)
    const file = new File(['x'], 'huge.json', { type: 'application/json' })
    Object.defineProperty(file, 'size', { value: 4 * 1024 * 1024 + 1 })
    fireEvent.change(screen.getByLabelText(/Backup JSON file/), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Restore configuration' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('4 MiB'))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('downloads a blob without putting credentials in the URL or filename', async () => {
    const create = vi.fn(() => 'blob:private-download')
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const blob = new Blob(['sensitive-data'], { type: 'application/json' })
    fetchMock.mockResolvedValue({ ok: true, blob: async () => blob })
    render(<Backup />)
    fireEvent.click(screen.getByRole('button', { name: 'Export HueSync configuration' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Backup downloaded'))
    expect(fetchMock).toHaveBeenCalledWith('/api/config/export', { cache: 'no-store' })
    expect(create).toHaveBeenCalledWith(blob)
    expect(click).toHaveBeenCalledOnce()
    click.mockRestore()
  })
})
