import { useEffect, useRef, useState } from 'react'
import { EnergySourceControls, validateEnergySettings, type EnergySetting } from './EnergyBlendEditor'
import { updateEnergyProfile, type EnergyProfile } from '@/lib/api'

export function LiveEnergySource({ profile, active, onUpdated, onOpen }: {
  profile?: EnergyProfile; active: boolean
  onUpdated: (profile: EnergyProfile) => void; onOpen?: (id: string) => void
}) {
  const values = () => ({ floor: String(profile?.lufs_floor ?? -30), ceiling: String(profile?.lufs_ceiling ?? -8), tau: String(profile?.adaptation_tau_s ?? 60) })
  const [draft, setDraft] = useState(values)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  useEffect(() => { setDraft(values()); setError(null) }, [profile])

  async function patch(body: Partial<EnergyProfile>) {
    if (!active || !profile || pending.current) return
    pending.current = true; setBusy(true); setError(null)
    try { onUpdated(await updateEnergyProfile(profile.id, body)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to update energy profile') }
    finally { pending.current = false; setBusy(false) }
  }
  function change(field: EnergySetting, value: string) {
    if (field === 'energy_source') {
      if (value !== (profile?.energy_source ?? 'sustained')) void patch({ energy_source: value })
    } else {
      const key = field === 'lufs_floor' ? 'floor' : field === 'lufs_ceiling' ? 'ceiling' : 'tau'
      setDraft(d => ({ ...d, [key]: value }))
    }
  }
  function commit() {
    if (!profile || validateEnergySettings(draft.floor, draft.ceiling, draft.tau)) return
    const body: Partial<EnergyProfile> = {}
    if (profile.energy_source === 'loudness_fixed') {
      if (Number(draft.floor) !== (profile.lufs_floor ?? -30)) body.lufs_floor = Number(draft.floor)
      if (Number(draft.ceiling) !== (profile.lufs_ceiling ?? -8)) body.lufs_ceiling = Number(draft.ceiling)
    } else if (profile.energy_source === 'loudness_adaptive' && Number(draft.tau) !== (profile.adaptation_tau_s ?? 60)) body.adaptation_tau_s = Number(draft.tau)
    if (Object.keys(body).length) void patch(body)
  }
  return <div className="mt-3 space-y-2" data-testid="live-energy-source">
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span>{!active ? 'No active coupling' : profile ? `Energy profile: ${profile.name}` : 'Energy profile: —'}</span>
      {active && profile && <a href={`#energy-profiles/${encodeURIComponent(profile.id)}`}
        onClick={e => { if (onOpen) { e.preventDefault(); onOpen(profile.id) } }}
        className="underline underline-offset-2">Open profile</a>}
    </div>
    <EnergySourceControls compact source={profile?.energy_source ?? 'sustained'}
      floor={draft.floor} ceiling={draft.ceiling} tau={draft.tau} onChange={change}
      onCommit={commit} disabled={!active || !profile} pending={busy} />
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </div>
}
