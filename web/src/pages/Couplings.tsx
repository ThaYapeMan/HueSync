import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EffectPreview } from '@/components/EffectPreview'
import { cn } from '@/lib/utils'
import {
  EFFECTS,
  type Coupling,
  type VirtualPlayer,
  type Zone,
  type Analyser,
  type Effect,
  type EnergyProfile,
  getCouplings,
  createCoupling,
  updateCoupling,
  deleteCoupling,
  activateCoupling,
  cloneCoupling,
  deactivateCoupling,
  getVirtualPlayers,
  getZones,
  getAnalysers,
  getEffects,
  getEnergyProfiles,
  getStatus,
} from '@/lib/api'

interface Props {
  activeCouplingId: string | null
  onActivationChange: () => void
  onNavigate?: (tab: string) => void
}

// ── NodeConnector ─────────────────────────────────────────────────────────────

function NodeConnector() {
  return (
    <div className="flex justify-center py-2">
      <div className="flex flex-col items-center">
        <div className="w-px h-7 bg-border/50" />
        <div
          className="w-0 h-0"
          style={{
            borderLeft: '6px solid transparent',
            borderRight: '6px solid transparent',
            borderTop: '6px solid hsl(var(--border) / 0.5)',
          }}
        />
      </div>
    </div>
  )
}

// ── RoutingNode (view mode — Player, Analyser, Zone) ──────────────────────────

function RoutingNode({ label, name, context, onOpen }: {
  label: string
  name?: string
  context?: string
  onOpen?: () => void
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/40 border-b border-border/40">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </span>
        {name && onOpen && (
          <button
            className="text-[11px] text-muted-foreground/50 hover:text-primary transition-colors"
            onClick={onOpen}
          >
            Open →
          </button>
        )}
      </div>
      <div className="px-5 py-4">
        {name ? (
          <>
            <p className="font-semibold text-base">{name}</p>
            {context && <p className="text-xs text-muted-foreground mt-1">{context}</p>}
          </>
        ) : (
          <p className="text-base text-muted-foreground/40 italic">Not configured</p>
        )}
      </div>
    </div>
  )
}

// ── EnergyProfileRoutingNode ──────────────────────────────────────────────────

function EnergyProfileRoutingNode({ ep, effects, onOpen }: {
  ep: EnergyProfile | undefined
  effects: Effect[]
  onOpen?: () => void
}) {
  const highEffect = ep ? effects.find(e => e.id === ep.high_energy_effect_id) : undefined
  const lowEffect = ep
    ? (ep.low_energy_effect_id
        ? effects.find(e => e.id === ep.low_energy_effect_id)
        : highEffect)
    : undefined

  // Equality is by ID — empty low_energy_effect_id means same as high
  const singleEffect = !ep?.low_energy_effect_id || ep.low_energy_effect_id === ep.high_energy_effect_id

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/40 border-b border-border/40">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          Energy Profile
        </span>
        {ep && onOpen && (
          <button
            className="text-[11px] text-muted-foreground/50 hover:text-primary transition-colors"
            onClick={onOpen}
          >
            Open →
          </button>
        )}
      </div>
      <div className="px-5 py-4">
        {!ep ? (
          <p className="text-base text-muted-foreground/40 italic">Not configured</p>
        ) : (
          <>
            <p className="font-semibold text-base">{ep.name}</p>

            {highEffect ? (
              singleEffect ? (
                /* ─── Single effect ─── */
                <div className="mt-4 pt-3 border-t border-border/30">
                  <p className="text-[10px] uppercase tracking-widest text-muted-foreground/50 font-semibold mb-2">
                    Effect
                  </p>
                  <div className="bg-black/25 rounded-md">
                    <EffectPreview effectType={highEffect.effect_type} energy={0.80} count={6} size="md" />
                  </div>
                  <p className="text-xs text-muted-foreground mt-2 text-center">
                    {EFFECTS.find(e => e.id === highEffect.effect_type)?.label ?? highEffect.effect_type}
                  </p>
                  <p className="text-sm font-medium text-center mt-0.5">{highEffect.name}</p>
                </div>
              ) : (
                /* ─── Two different effects ─── */
                <div className="mt-4 pt-3 border-t border-border/30">
                  <div className="grid grid-cols-2 gap-3">
                    {/* Low energy */}
                    <div>
                      <p className="text-[10px] uppercase tracking-widest text-muted-foreground/50 font-semibold mb-2">
                        Low energy
                      </p>
                      <div className="bg-black/25 rounded-md">
                        <EffectPreview
                          effectType={lowEffect?.effect_type ?? 'none'}
                          energy={0.80}
                          count={5}
                          size="md"
                        />
                      </div>
                      <p className="text-xs text-muted-foreground mt-2 text-center">
                        {EFFECTS.find(e => e.id === lowEffect?.effect_type)?.label ?? lowEffect?.effect_type ?? '—'}
                      </p>
                      <p className="text-sm font-medium text-center mt-0.5">{lowEffect?.name ?? '—'}</p>
                    </div>
                    {/* High energy */}
                    <div>
                      <p className="text-[10px] uppercase tracking-widest text-muted-foreground/50 font-semibold mb-2">
                        High energy
                      </p>
                      <div className="bg-black/25 rounded-md">
                        <EffectPreview
                          effectType={highEffect.effect_type}
                          energy={0.80}
                          count={5}
                          size="md"
                        />
                      </div>
                      <p className="text-xs text-muted-foreground mt-2 text-center">
                        {EFFECTS.find(e => e.id === highEffect.effect_type)?.label ?? highEffect.effect_type}
                      </p>
                      <p className="text-sm font-medium text-center mt-0.5">{highEffect.name}</p>
                    </div>
                  </div>
                </div>
              )
            ) : (
              <p className="text-xs text-muted-foreground mt-2 italic">Effects not found</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── RoutingEditorNode (edit mode) ─────────────────────────────────────────────

function RoutingEditorNode({ label, options, value, onChange, placeholder, onNavigateCreate }: {
  label: string
  options: { id: string; label: string }[]
  value: string
  onChange: (v: string) => void
  placeholder: string
  onNavigateCreate?: () => void
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card">
      <div className="px-4 py-2 bg-muted/40 border-b border-border/40">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </span>
      </div>
      <div className="px-5 py-3">
        {options.length === 0 ? (
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground/50 italic">No {label.toLowerCase()}s available</p>
            {onNavigateCreate && (
              <button
                className="text-xs text-primary/70 hover:text-primary transition-colors shrink-0"
                onClick={onNavigateCreate}
              >
                Create →
              </button>
            )}
          </div>
        ) : (
          <Select value={value} onValueChange={onChange}>
            <SelectTrigger className="h-8 text-sm">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              {options.map(o => (
                <SelectItem key={o.id} value={o.id} className="text-sm">
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  )
}

// ── CouplingListItem ──────────────────────────────────────────────────────────

function CouplingListItem({ coupling, playerLabel, zoneLabel, isActive, isSelected, onSelect }: {
  coupling: Coupling
  playerLabel: string
  zoneLabel: string
  isActive: boolean
  isSelected: boolean
  onSelect: () => void
}) {
  return (
    <button
      className={cn(
        'w-full text-left px-3 py-2.5 rounded-md transition-colors',
        isSelected
          ? 'bg-muted text-foreground'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
      )}
      onClick={onSelect}
    >
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-sm font-medium truncate flex-1">{coupling.name}</span>
        {isActive && <span className="shrink-0 text-[10px] text-green-400">●</span>}
        {!isActive && !coupling.enabled && (
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-muted-foreground/30" />
        )}
      </div>
      <p className="text-xs text-muted-foreground/60 truncate">
        {playerLabel} → {zoneLabel}
      </p>
    </button>
  )
}

// ── CouplingWorkspace ─────────────────────────────────────────────────────────

interface WorkspaceProps {
  coupling: Coupling | null
  players: VirtualPlayer[]
  zones: Zone[]
  analysers: Analyser[]
  effects: Effect[]
  energyProfiles: EnergyProfile[]
  activeCouplingId: string | null
  onSaved: (c: Coupling) => void
  onDeleted: (id: string) => void
  onCloned: (id: string) => void
  onCancelCreate: () => void
  onActivate: (id: string) => void
  onDeactivate: () => void
  onNavigate?: (tab: string) => void
}

function CouplingWorkspace({
  coupling,
  players, zones, analysers, effects, energyProfiles,
  activeCouplingId,
  onSaved, onDeleted, onCloned, onCancelCreate,
  onActivate, onDeactivate, onNavigate,
}: WorkspaceProps) {
  const isCreating = coupling === null
  const isActive = !isCreating && coupling?.id === activeCouplingId

  const [name, setName] = useState(coupling?.name ?? '')
  const [nameError, setNameError] = useState<string | null>(null)
  const [enabled, setEnabled] = useState(coupling?.enabled ?? true)

  const [isEditingRouting, setIsEditingRouting] = useState(isCreating)
  const [draft, setDraft] = useState({
    playerId: coupling?.player_id ?? '',
    zoneId: coupling?.zone_id ?? '',
    analyserId: coupling?.analyser_id ?? '',
    energyProfileId: coupling?.energy_profile_id ?? '',
  })
  const [savingRouting, setSavingRouting] = useState(false)
  const [routingError, setRoutingError] = useState<string | null>(null)

  async function handleSaveName() {
    if (!coupling || name.trim() === coupling.name) return
    setNameError(null)
    try {
      const saved = await updateCoupling(coupling.id, { name: name.trim() })
      onSaved(saved)
    } catch (e) {
      setNameError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleToggleEnabled(v: boolean) {
    setEnabled(v)
    if (coupling) {
      try {
        const saved = await updateCoupling(coupling.id, { enabled: v })
        onSaved(saved)
      } catch {
        setEnabled(!v)
      }
    }
  }

  async function handleSaveRouting() {
    setSavingRouting(true)
    setRoutingError(null)
    try {
      const body = {
        player_id: draft.playerId,
        zone_id: draft.zoneId,
        analyser_id: draft.analyserId,
        energy_profile_id: draft.energyProfileId,
      }
      const saved = isCreating
        ? await createCoupling({ name: name.trim() || 'New coupling', enabled, ...body })
        : await updateCoupling(coupling!.id, body)
      setIsEditingRouting(false)
      onSaved(saved)
    } catch (e) {
      setRoutingError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSavingRouting(false)
    }
  }

  function handleCancelRouting() {
    if (isCreating) {
      onCancelCreate()
      return
    }
    setDraft({
      playerId: coupling!.player_id,
      zoneId: coupling!.zone_id,
      analyserId: coupling!.analyser_id,
      energyProfileId: coupling!.energy_profile_id,
    })
    setRoutingError(null)
    setIsEditingRouting(false)
  }

  // View-mode entity lookups
  const player = players.find(p => p.id === coupling?.player_id)
  const zone = zones.find(z => z.id === coupling?.zone_id)
  const analyser = analysers.find(a => a.id === coupling?.analyser_id)
  const ep = energyProfiles.find(e => e.id === coupling?.energy_profile_id)

  const playerName = player
    ? (player.display_name || player.player_name || player.type)
    : undefined
  const playerContext = player
    ? (player.type === 'LMS' && player.lms_host ? `LMS · ${player.lms_host}` : player.type)
    : undefined
  const analyserContext = analyser
    ? `${analyser.bars} bars · ${analyser.onset_method} · ${analyser.bars_source === 'pcm_pipeline' ? 'PCM' : 'cava'}`
    : undefined
  const zoneContext = zone
    ? `${zone.light_count} light${zone.light_count === 1 ? '' : 's'}`
    : undefined

  // Edit-mode option lists
  const playerOptions = players.map(p => ({
    id: p.id,
    label: p.display_name || p.player_name || p.type,
  }))
  const analyserOptions = analysers.map(a => ({
    id: a.id,
    label: `${a.name} — ${a.bars} bars`,
  }))
  const epOptions = energyProfiles.map(e => ({ id: e.id, label: e.name }))
  const zoneOptions = zones.map(z => ({
    id: z.id,
    label: `${z.name} — ${z.light_count} lights`,
  }))

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Header: name + status */}
      <div className="px-5 py-3.5 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <input
            value={name}
            onChange={e => { setName(e.target.value); setNameError(null) }}
            onBlur={handleSaveName}
            placeholder={isCreating ? 'Coupling name…' : 'Name…'}
            className="flex-1 min-w-0 bg-transparent text-lg font-semibold outline-none placeholder:text-muted-foreground/40 border-b border-transparent focus:border-border pb-0.5 transition-colors"
          />
          {!isCreating && (
            <Badge
              variant={isActive ? 'default' : 'secondary'}
              className={cn('shrink-0 text-xs', !enabled && !isActive && 'opacity-50')}
            >
              {isActive ? '● Active' : enabled ? 'Ready' : 'Disabled'}
            </Badge>
          )}
        </div>
        {nameError && <p className="text-xs text-destructive mt-1">{nameError}</p>}
      </div>

      {/* Routing graph */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-md mx-auto px-4 py-8">
          {isEditingRouting ? (
            <>
              <RoutingEditorNode
                label="Virtual Player"
                options={playerOptions}
                value={draft.playerId}
                onChange={v => setDraft(d => ({ ...d, playerId: v }))}
                placeholder="Select virtual player…"
                onNavigateCreate={onNavigate ? () => onNavigate('players') : undefined}
              />
              <NodeConnector />
              <RoutingEditorNode
                label="Analyser"
                options={analyserOptions}
                value={draft.analyserId}
                onChange={v => setDraft(d => ({ ...d, analyserId: v }))}
                placeholder="Select analyser…"
                onNavigateCreate={onNavigate ? () => onNavigate('analysers') : undefined}
              />
              <NodeConnector />
              <RoutingEditorNode
                label="Energy Profile"
                options={epOptions}
                value={draft.energyProfileId}
                onChange={v => setDraft(d => ({ ...d, energyProfileId: v }))}
                placeholder="Select energy profile…"
                onNavigateCreate={onNavigate ? () => onNavigate('energy-profiles') : undefined}
              />
              <NodeConnector />
              <RoutingEditorNode
                label="Zone"
                options={zoneOptions}
                value={draft.zoneId}
                onChange={v => setDraft(d => ({ ...d, zoneId: v }))}
                placeholder="Select zone…"
                onNavigateCreate={onNavigate ? () => onNavigate('zones') : undefined}
              />
              {routingError && (
                <p className="text-sm text-destructive mt-4">{routingError}</p>
              )}
              <div className="flex gap-2 mt-5">
                <Button size="sm" onClick={handleSaveRouting} disabled={savingRouting} className="flex-1">
                  {savingRouting ? 'Saving…' : isCreating ? 'Create coupling' : 'Save routing'}
                </Button>
                <Button size="sm" variant="outline" onClick={handleCancelRouting} disabled={savingRouting}>
                  Cancel
                </Button>
              </div>
            </>
          ) : (
            <>
              <RoutingNode
                label="Virtual Player"
                name={playerName}
                context={playerContext}
                onOpen={onNavigate ? () => onNavigate('players') : undefined}
              />
              <NodeConnector />
              <RoutingNode
                label="Analyser"
                name={analyser?.name}
                context={analyserContext}
                onOpen={onNavigate ? () => onNavigate('analysers') : undefined}
              />
              <NodeConnector />
              <EnergyProfileRoutingNode
                ep={ep}
                effects={effects}
                onOpen={onNavigate ? () => onNavigate('energy-profiles') : undefined}
              />
              <NodeConnector />
              <RoutingNode
                label="Zone"
                name={zone?.name}
                context={zoneContext}
                onOpen={onNavigate ? () => onNavigate('zones') : undefined}
              />
            </>
          )}
        </div>
      </div>

      {/* Action bar — view mode only */}
      {!isEditingRouting && !isCreating && (
        <div className="px-4 py-2.5 border-t border-border shrink-0 flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs cursor-pointer select-none text-muted-foreground hover:text-foreground transition-colors">
            <input
              type="checkbox"
              checked={enabled}
              onChange={e => handleToggleEnabled(e.target.checked)}
              className="h-3.5 w-3.5 cursor-pointer"
            />
            Enabled
          </label>
          <span className="h-3.5 w-px bg-border mx-0.5" />
          {isActive ? (
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onDeactivate}>
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={!enabled}
              onClick={() => onActivate(coupling!.id)}
            >
              Go
            </Button>
          )}
          <span className="flex-1" />
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setIsEditingRouting(true)}
          >
            Edit routing
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => onCloned(coupling!.id)}
          >
            Clone
          </Button>
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="ghost" className="h-7 text-xs text-destructive/70 hover:text-destructive">
                Delete
              </Button>
            }
            title="Delete coupling"
            description={`Delete "${coupling!.name}"? This cannot be undone.`}
            onConfirm={() => onDeleted(coupling!.id)}
          />
        </div>
      )}
    </div>
  )
}

// ── Couplings (main) ──────────────────────────────────────────────────────────

export function Couplings({ activeCouplingId: activeCouplingIdProp, onActivationChange, onNavigate }: Props) {
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [players, setPlayers] = useState<VirtualPlayer[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [analysers, setAnalysers] = useState<Analyser[]>([])
  const [effects, setEffects] = useState<Effect[]>([])
  const [energyProfiles, setEnergyProfiles] = useState<EnergyProfile[]>([])
  const [activeCouplingId, setActiveCouplingId] = useState<string | null>(activeCouplingIdProp)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  async function loadAll() {
    try {
      const [cs, ps, zs, acs, eps, effs, st] = await Promise.all([
        getCouplings(), getVirtualPlayers(), getZones(), getAnalysers(),
        getEnergyProfiles(), getEffects(), getStatus(),
      ])
      setCouplings(cs); setPlayers(ps); setZones(zs); setAnalysers(acs)
      setEnergyProfiles(eps); setEffects(effs)
      setActiveCouplingId(st.active_coupling_id ?? activeCouplingIdProp)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleActivate(id: string) {
    setActionError(null)
    try {
      await activateCoupling(id)
      onActivationChange()
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Activation failed')
    }
  }

  async function handleDeactivate() {
    setActionError(null)
    try {
      await deactivateCoupling()
      onActivationChange()
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Deactivation failed')
    }
  }

  async function handleClone(id: string) {
    setActionError(null)
    try {
      const cloned = await cloneCoupling(id)
      await loadAll()
      setSelectedId(cloned.id)
      setIsCreating(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Clone failed')
    }
  }

  async function handleDelete(id: string) {
    setActionError(null)
    try {
      await deleteCoupling(id)
      if (selectedId === id) { setSelectedId(null); setIsCreating(false) }
      await loadAll()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  function handleSaved(saved: Coupling) {
    setSelectedId(saved.id)
    setIsCreating(false)
    loadAll()
  }

  const workspaceKey = isCreating ? '__new__' : (selectedId ?? '__none__')
  const selectedCoupling = isCreating
    ? null
    : (couplings.find(c => c.id === selectedId) ?? null)
  const showWorkspace = isCreating || selectedId !== null

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          Couplings
        </span>
        <Button size="sm" onClick={() => { setIsCreating(true); setSelectedId(null) }}>
          New coupling
        </Button>
      </div>

      {actionError && (
        <div className="px-4 py-1.5 text-xs text-destructive border-b border-border bg-destructive/5 shrink-0">
          {actionError}
        </div>
      )}

      <div className="flex-1 overflow-hidden flex">
        {/* Compact coupling list */}
        <div className="w-52 shrink-0 border-r border-border overflow-y-auto py-2 px-2">
          {loading ? (
            <p className="text-xs text-muted-foreground px-1 py-2">Loading…</p>
          ) : error ? (
            <p className="text-xs text-destructive px-1 py-2">{error}</p>
          ) : couplings.length === 0 ? (
            <p className="text-xs text-muted-foreground/50 px-1 py-2 leading-relaxed">
              No couplings yet.
            </p>
          ) : (
            couplings.map(c => {
              const p = players.find(pl => pl.id === c.player_id)
              const z = zones.find(zn => zn.id === c.zone_id)
              return (
                <CouplingListItem
                  key={c.id}
                  coupling={c}
                  playerLabel={p ? (p.display_name || p.player_name || p.type) : '—'}
                  zoneLabel={z?.name ?? '—'}
                  isActive={c.id === activeCouplingId}
                  isSelected={!isCreating && c.id === selectedId}
                  onSelect={() => { setSelectedId(c.id); setIsCreating(false) }}
                />
              )
            })
          )}
        </div>

        {/* Workspace */}
        <div className="flex-1 overflow-hidden flex">
          {showWorkspace ? (
            <CouplingWorkspace
              key={workspaceKey}
              coupling={selectedCoupling}
              players={players}
              zones={zones}
              analysers={analysers}
              effects={effects}
              energyProfiles={energyProfiles}
              activeCouplingId={activeCouplingId}
              onSaved={handleSaved}
              onDeleted={handleDelete}
              onCloned={handleClone}
              onCancelCreate={() => { setIsCreating(false); setSelectedId(null) }}
              onActivate={handleActivate}
              onDeactivate={handleDeactivate}
              onNavigate={onNavigate}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-muted-foreground/35">
                Select a coupling to view its routing
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
