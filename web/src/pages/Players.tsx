import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
  type VirtualPlayer,
  type LmsPlayer,
  getVirtualPlayers,
  createVirtualPlayer,
  updateVirtualPlayer,
  deleteVirtualPlayer,
  listLmsPlayers,
} from '@/lib/api'

interface FormState {
  lms_host: string
  lms_port: string
  player_name: string
  alsa_device: string
  follow_player_mac: string
}

function defaultForm(player?: VirtualPlayer): FormState {
  return {
    lms_host: player?.lms_host ?? '',
    lms_port: String(player?.lms_port ?? 9000),
    player_name: player?.player_name ?? 'HueSync',
    alsa_device: player?.alsa_device ?? '',
    follow_player_mac: player?.follow_player_mac ?? '',
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
  const [players, setPlayers] = useState<VirtualPlayer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingPlayer, setEditingPlayer] = useState<VirtualPlayer | undefined>(undefined)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [lmsPlayers, setLmsPlayers] = useState<LmsPlayer[]>([])
  const [discovering, setDiscovering] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)

  async function load() {
    try {
      const data = await getVirtualPlayers()
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
    setLmsPlayers([])
    setSaveError(null)
    setDiscoverError(null)
    setEditorOpen(true)
  }

  function openEdit(player: VirtualPlayer) {
    setEditingPlayer(player)
    setForm(defaultForm(player))
    setLmsPlayers([])
    setSaveError(null)
    setDiscoverError(null)
    setEditorOpen(true)
  }

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function discoverPlayers() {
    if (!form.lms_host) {
      setDiscoverError('Enter an LMS host first')
      return
    }
    setDiscovering(true)
    setDiscoverError(null)
    try {
      const result = await listLmsPlayers(form.lms_host)
      setLmsPlayers(result)
      if (result.length === 0) setDiscoverError('No players found on this LMS server')
    } catch (e) {
      setDiscoverError(e instanceof Error ? e.message : 'Discovery failed')
    } finally {
      setDiscovering(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    try {
      if (editingPlayer) {
        await updateVirtualPlayer(editingPlayer.id, {
          lms_host: form.lms_host,
          lms_port: parseInt(form.lms_port, 10),
          player_name: form.player_name,
          alsa_device: form.alsa_device,
          follow_player_mac: form.follow_player_mac,
        })
      } else {
        await createVirtualPlayer({
          type: 'LMS',
          lms_host: form.lms_host,
          lms_port: parseInt(form.lms_port, 10),
          player_name: form.player_name,
          alsa_device: form.alsa_device,
          follow_player_mac: form.follow_player_mac,
        })
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
    await deleteVirtualPlayer(id)
    await load()
  }

  // Build options for the Follow player dropdown. If the current follow_player_mac
  // is not in the discovered list (e.g. discovery hasn't run yet), inject it as a
  // bare MAC so the selection is not lost.
  const followOptions: LmsPlayer[] = [...lmsPlayers]
  if (
    form.follow_player_mac &&
    !lmsPlayers.some((p) => p.playerid === form.follow_player_mac)
  ) {
    followOptions.unshift({ playerid: form.follow_player_mac, name: form.follow_player_mac })
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
        <h2 className="text-sm font-semibold">Virtual Players</h2>
        <Button size="sm" onClick={openNew}>
          New virtual player
        </Button>
      </div>

      {players.length === 0 ? (
        <p className="text-sm text-muted-foreground">No virtual players yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>LMS Host</TableHead>
              <TableHead>Player Name</TableHead>
              <TableHead>MAC</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {players.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-medium">{p.type}</TableCell>
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
                      title="Delete virtual player"
                      description={`Delete virtual player "${p.player_name}" (${p.lms_host})? This cannot be undone.`}
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
            <DialogTitle>{editingPlayer ? 'Edit virtual player' : 'New virtual player'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <FormRow label="Type">
              <p className="text-sm font-mono py-1 text-muted-foreground">LMS (squeezelite)</p>
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

            <div className="space-y-1">
              <Label className="text-sm">Follow player</Label>
              <p className="text-xs text-muted-foreground">
                HueSync mirrors track changes from this LMS player without joining its sync group.
              </p>
              <div className="flex gap-2">
                <Select
                  value={form.follow_player_mac || '__none__'}
                  onValueChange={(v) => set('follow_player_mac', v === '__none__' ? '' : v)}
                >
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="None — track mirroring disabled" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">None — track mirroring disabled</SelectItem>
                    {followOptions.map((p) => (
                      <SelectItem key={p.playerid} value={p.playerid}>
                        {p.name !== p.playerid ? `${p.name} (${p.playerid})` : p.playerid}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={discoverPlayers}
                  disabled={discovering}
                >
                  {discovering ? '…' : 'Discover'}
                </Button>
              </div>
              {discoverError && (
                <p className="text-xs text-destructive">{discoverError}</p>
              )}
            </div>

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
