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
  type Controller,
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
  getControllers,
  getStatus,
  listLmsPlayers,
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

// ── BranchConnector ───────────────────────────────────────────────────────────

function BranchConnector() {
  return (
    <div className="w-full py-1" aria-hidden>
      <svg viewBox="0 0 400 48" className="w-full h-10">
        <line x1="200" y1="0" x2="200" y2="18" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="100" y1="18" x2="300" y2="18" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="100" y1="18" x2="100" y2="42" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="300" y1="18" x2="300" y2="42" className="stroke-border/50" strokeWidth="1.5" />
        <polygon points="94,36 100,43 106,36" className="fill-border/50" />
        <polygon points="294,36 300,43 306,36" className="fill-border/50" />
      </svg>
    </div>
  )
}

// ── MergeConnector ────────────────────────────────────────────────────────────

function MergeConnector() {
  return (
    <div className="w-full py-1" aria-hidden>
      <svg viewBox="0 0 400 48" className="w-full h-10">
        <line x1="100" y1="0" x2="100" y2="24" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="300" y1="0" x2="300" y2="24" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="100" y1="24" x2="300" y2="24" className="stroke-border/50" strokeWidth="1.5" />
        <line x1="200" y1="24" x2="200" y2="48" className="stroke-border/50" strokeWidth="1.5" />
        <polygon points="194,41 200,48 206,41" className="fill-border/50" />
      </svg>
    </div>
  )
}

// ── RoutingNode (view mode — Player, Analyser, Zone) ──────────────────────────

function RoutingNode({ label, name, context, onOpen, testId }: {
  label: string
  name?: string
  context?: string
  onOpen?: () => void
  testId?: string
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card" data-testid={testId}>
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

// ── VirtualPlayerRoutingNode ──────────────────────────────────────────────────

function VirtualPlayerRoutingNode({ player, followPlayerName, onOpen }: {
  player: VirtualPlayer | undefined
  followPlayerName: string | undefined
  onOpen?: () => void
}) {
  const displayName = player
    ? (player.display_name || player.player_name || player.type)
    : undefined

  let context: string | undefined
  if (player?.type === 'AirPlay') {
    const advertisedName = player.display_name || player.player_name
    context = `AirPlay · Advertised Player · ${advertisedName}`
  } else if (player?.type === 'LMS') {
    const followName = followPlayerName ?? (player.follow_player_mac || undefined)
    context = followName
      ? `LMS · Follow Player · ${followName}`
      : 'LMS'
  } else if (player?.type) {
    context = player.type
  }

  return (
    <RoutingNode
      label="Virtual Player"
      name={displayName}
      context={context}
      onOpen={onOpen}
      testId="node-virtual-player"
    />
  )
}

// ── EffectRoutingNode ─────────────────────────────────────────────────────────

function EffectRoutingNode({ role, effect, onOpen, testId }: {
  role: string
  effect: Effect | undefined
  onOpen?: () => void
  testId?: string
}) {
  const effectMeta = effect ? EFFECTS.find(e => e.id === effect.effect_type) : undefined

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-card" data-testid={testId}>
      <div className="flex items-center justify-between px-4 py-2 bg-muted/40 border-b border-border/40">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {role}
        </span>
        {effect && onOpen && (
          <button
            className="text-[11px] text-muted-foreground/50 hover:text-primary transition-colors"
            onClick={onOpen}
          >
            Open →
          </button>
        )}
      </div>
      <div className="px-5 py-4">
        {!effect ? (
          <p className="text-base text-muted-foreground/40 italic">Not configured</p>
        ) : (
          <>
            <div className="bg-black/25 rounded-md mb-3">
              <EffectPreview effectType={effect.effect_type} energy={0.75} count={6} size="md" />
            </div>
            <p className="text-sm font-medium text-center">{effect.name}</p>
            {effectMeta && effectMeta.label !== effect.name && (
              <p className="text-xs text-muted-foreground text-center mt-0.5">{effectMeta.label}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── EnergyProfileRoutingNode ──────────────────────────────────────────────────

function EnergyProfileRoutingNode({ ep, onOpen }: {
  ep: EnergyProfile | undefined
  onOpen?: () => void
}) {
  return (
    <RoutingNode
      label="Energy Profile"
      name={ep?.name}
      onOpen={ep ? onOpen : undefined}
      testId="node-energy-profile"
    />
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
      data-testid={`coupling-item-${coupling.id}`}
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
  controllers: Controller[]
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
  players, zones, analysers, effects, energyProfiles, controllers,
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

  const [followPlayerName, setFollowPlayerName] = useState<string | undefined>(undefined)

  // Resolve LMS follow player name
  useEffect(() => {
    const player = players.find(p => p.id === coupling?.player_id)
    if (!player || player.type !== 'LMS' || !player.follow_player_mac || !player.lms_host) {
      setFollowPlayerName(undefined)
      return
    }
    listLmsPlayers(player.lms_host)
      .then(lmsPlayers => {
        const found = lmsPlayers.find(lp => lp.playerid === player.follow_player_mac)
        setFollowPlayerName(found?.name)
      })
      .catch(() => setFollowPlayerName(undefined))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

  // Effect routing
  const highEffect = ep ? effects.find(e => e.id === ep.high_energy_effect_id) : undefined
  const lowEffectId = ep?.low_energy_effect_id || null
  const twoEffects = !!lowEffectId && lowEffectId !== ep?.high_energy_effect_id
  const lowEffect = twoEffects ? effects.find(e => e.id === lowEffectId!) : undefined
  const singleEffect = twoEffects ? undefined : highEffect

  // Analyser context
  const analyserContext = analyser
    ? `${analyser.bars} bars · ${analyser.onset_method} · ${analyser.bars_source === 'pcm_pipeline' ? 'PCM' : 'cava'}`
    : undefined

  // Zone controller type (resolved from controllers list, not hardcoded)
  const zoneController = controllers.find(c => c.id === zone?.controller_id)
  const controllerLabel = zoneController?.type
    ? zoneController.type.charAt(0).toUpperCase() + zoneController.type.slice(1)
    : undefined
  const zoneContext = zone
    ? (controllerLabel
        ? `${controllerLabel} · ${zone.light_count} light${zone.light_count === 1 ? '' : 's'}`
        : `${zone.light_count} light${zone.light_count === 1 ? '' : 's'}`)
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
        <div className={cn('max-w-md mx-auto px-4 py-8', twoEffects && 'max-w-2xl')}>
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
              <VirtualPlayerRoutingNode
                player={player}
                followPlayerName={followPlayerName}
                onOpen={onNavigate ? () => onNavigate('players') : undefined}
              />
              <NodeConnector />
              <RoutingNode
                label="Analyser"
                name={analyser?.name}
                context={analyserContext}
                onOpen={onNavigate ? () => onNavigate('analysers') : undefined}
                testId="node-analyser"
              />
              <NodeConnector />
              <EnergyProfileRoutingNode
                ep={ep}
                onOpen={onNavigate ? () => onNavigate('energy-profiles') : undefined}
              />
              {twoEffects ? (
                <>
                  <BranchConnector />
                  <div className="grid grid-cols-2 gap-4">
                    <EffectRoutingNode
                      role="Low Energy Effect"
                      effect={lowEffect}
                      onOpen={onNavigate ? () => onNavigate('effects') : undefined}
                      testId="node-effect-low"
                    />
                    <EffectRoutingNode
                      role="High Energy Effect"
                      effect={highEffect}
                      onOpen={onNavigate ? () => onNavigate('effects') : undefined}
                      testId="node-effect-high"
                    />
                  </div>
                  <MergeConnector />
                </>
              ) : (
                <>
                  <NodeConnector />
                  <EffectRoutingNode
                    role="Effect"
                    effect={singleEffect}
                    onOpen={onNavigate ? () => onNavigate('effects') : undefined}
                    testId="node-effect"
                  />
                  <NodeConnector />
                </>
              )}
              <RoutingNode
                label="Zone"
                name={zone?.name}
                context={zoneContext}
                onOpen={onNavigate ? () => onNavigate('zones') : undefined}
                testId="node-zone"
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
  const [controllers, setControllers] = useState<Controller[]>([])
  const [activeCouplingId, setActiveCouplingId] = useState<string | null>(activeCouplingIdProp)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  async function loadAll() {
    try {
      const [cs, ps, zs, acs, eps, effs, ctrls, st] = await Promise.all([
        getCouplings(), getVirtualPlayers(), getZones(), getAnalysers(),
        getEnergyProfiles(), getEffects(), getControllers(), getStatus(),
      ])
      setCouplings(cs); setPlayers(ps); setZones(zs); setAnalysers(acs)
      setEnergyProfiles(eps); setEffects(effs); setControllers(ctrls)
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
              controllers={controllers}
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
