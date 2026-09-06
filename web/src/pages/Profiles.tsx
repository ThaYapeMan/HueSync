import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ProfileEditor } from '@/components/ProfileEditor'
import {
  type Bridge,
  type Profile,
  getBridges,
  getProfiles,
  activateProfile,
  deactivateProfile,
  deleteProfile,
} from '@/lib/api'

interface Props {
  activeProfileId: string | null
  onActivationChange: () => void
}

export function Profiles({ activeProfileId, onActivationChange }: Props) {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [bridges, setBridges] = useState<Bridge[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingProfile, setEditingProfile] = useState<Profile | undefined>(undefined)
  const [actionError, setActionError] = useState<string | null>(null)

  async function load() {
    try {
      const [p, b] = await Promise.all([getProfiles(), getBridges()])
      setProfiles(p)
      setBridges(b)
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

  async function handleActivate(id: string) {
    setActionError(null)
    try {
      await activateProfile(id)
      onActivationChange()
      await load()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Activation failed')
    }
  }

  async function handleDeactivate() {
    setActionError(null)
    try {
      await deactivateProfile()
      onActivationChange()
      await load()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Deactivation failed')
    }
  }

  async function handleDelete(id: string) {
    await deleteProfile(id)
    await load()
  }

  function openNew() {
    setEditingProfile(undefined)
    setEditorOpen(true)
  }

  function openEdit(profile: Profile) {
    setEditingProfile(profile)
    setEditorOpen(true)
  }

  async function handleSave() {
    setEditorOpen(false)
    await load()
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading profiles…</p>
  }

  if (error) {
    return <p className="text-destructive text-sm">{error}</p>
  }

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-400">
        <strong>Legacy tab.</strong> Profiles are replaced by the new Couplings system. Use the{' '}
        <strong>Couplings</strong> tab instead. Activating a profile here and a coupling simultaneously
        will cause them to overwrite each other's data.
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Profiles</h2>
        <Button size="sm" onClick={openNew}>
          New profile
        </Button>
      </div>

      {actionError && <p className="text-destructive text-sm">{actionError}</p>}

      {profiles.length === 0 ? (
        <p className="text-sm text-muted-foreground">No profiles yet. Create one to get started.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Area</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {profiles.map((p) => {
              const isActive = p.id === activeProfileId
              return (
                <TableRow key={p.id}>
                  <TableCell className="font-medium">{p.name}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {p.entertainment_area_name || '—'}
                  </TableCell>
                  <TableCell className="text-sm font-mono">{p.color_mode}</TableCell>
                  <TableCell>
                    <Badge variant={isActive ? 'default' : 'secondary'}>
                      {isActive ? 'Active' : 'Inactive'}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {!isActive && (
                        <Button size="sm" variant="outline" onClick={() => handleActivate(p.id)}>
                          Activate
                        </Button>
                      )}
                      {isActive && (
                        <Button size="sm" variant="outline" onClick={handleDeactivate}>
                          Stop
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => openEdit(p)}>
                        Edit
                      </Button>
                      <ConfirmDialog
                        trigger={
                          <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                            Delete
                          </Button>
                        }
                        title="Delete profile"
                        description={`Delete "${p.name}"? This cannot be undone.`}
                        onConfirm={() => handleDelete(p.id)}
                      />
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      <ProfileEditor
        profile={editingProfile}
        bridges={bridges}
        activeProfileId={activeProfileId}
        onSave={handleSave}
        onClose={() => setEditorOpen(false)}
        open={editorOpen}
      />
    </div>
  )
}
