import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import {
  type Player,
  getPlayers,
  createPlayer,
  updatePlayer,
  deletePlayer,
} from '@/lib/api'

interface FormState {
  name: string
  lms_host: string
  lms_port: string
  player_name: string
  alsa_device: string
}

function defaultForm(player?: Player): FormState {
  return {
    name: player?.name ?? '',
    lms_host: player?.lms_host ?? '',
    lms_port: String(player?.lms_port ?? 9000),
    player_name: player?.player_name ?? 'HueSync',
    alsa_device: player?.alsa_device ?? '',
  }
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      {children}
    </div>
  )
}

export function Players() {
  const [players, setPlayers] = useState<Player[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingPlayer, setEditingPlayer] = useState<Player | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getPlayers()
      setPlayers(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function openNew() {
    setEditingPlayer(undefined)
    setForm(defaultForm())
    setSaveError(null)
    setEditorOpen(true)
  }

  function openEdit(player: Player) {
    setEditingPlayer(player)
    setForm(defaultForm(player))
    setSaveError(null)
    setEditorOpen(true)
  }

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        lms_host: form.lms_host,
        lms_port: parseInt(form.lms_port, 10),
        player_name: form.player_name,
        alsa_device: form.alsa_device,
      }
      if (editingPlayer) {
        await updatePlayer(editingPlayer.id, body)
      } else {
        await createPlayer(body)
      }
      setEditorOpen(false)
      await load()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    await deletePlayer(id)
    await load()
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  if (error) {
    return <p className="text-destructive text-sm">{error}</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Players</h2>
        <Button size="sm" onClick={openNew}>
          New player
        </Button>
      </div>

      {players.length === 0 ? (
        <p className="text-sm text-muted-foreground">No players yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>LMS Host</TableHead>
              <TableHead>Player Name</TableHead>
              <TableHead>MAC</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {players.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-medium">{p.name}</TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">{p.lms_host}</TableCell>
                <TableCell className="text-sm">{p.player_name}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{p.player_mac || '—'}</TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(p)}>
                      Edit
                    </Button>
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Delete player"
                      description={`Delete "${p.name}"? This cannot be undone.`}
                      onConfirm={() => handleDelete(p.id)}
                    />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={editorOpen} onOpenChange={(o) => { if (!o) setEditorOpen(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{editingPlayer ? 'Edit player' : 'New player'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <FormRow label="Name">
              <Input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="My player"
              />
            </FormRow>
            <FormRow label="LMS host">
              <Input
                value={form.lms_host}
                onChange={(e) => set('lms_host', e.target.value)}
                placeholder="192.168.x.x"
              />
            </FormRow>
            <FormRow label="LMS port">
              <Input
                type="number"
                value={form.lms_port}
                onChange={(e) => set('lms_port', e.target.value)}
              />
            </FormRow>
            <FormRow label="Player name">
              <Input
                value={form.player_name}
                onChange={(e) => set('player_name', e.target.value)}
              />
            </FormRow>
            <FormRow label="ALSA device">
              <Input
                value={form.alsa_device}
                onChange={(e) => set('alsa_device', e.target.value)}
                placeholder="hw:CARD=Dummy,DEV=0"
              />
            </FormRow>

            {editingPlayer && (
              <div className="space-y-1">
                <Label className="text-sm">Player MAC</Label>
                <p className="text-sm font-mono text-muted-foreground py-1">{editingPlayer.player_mac || '—'}</p>
                <p className="text-xs text-muted-foreground">MAC is assigned by squeezelite and cannot be changed.</p>
              </div>
            )}
          </div>

          {saveError && <p className="text-destructive text-sm mt-2">{saveError}</p>}

          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
