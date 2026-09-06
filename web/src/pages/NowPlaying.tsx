import { useEffect, useRef, useState } from 'react'
import { ColourSwatch } from '@/components/ColourSwatch'
import { SpectrumBars } from '@/components/SpectrumBars'
import { SliderField } from '@/components/SliderField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import type { PreviewState, SocketStatus } from '@/hooks/usePreviewSocket'
import {
  type Coupling,
  activateCoupling,
  deactivateCoupling,
  getCouplings,
  restartCava,
} from '@/lib/api'

const LOG_MIN = Math.log10(20)
const LOG_MAX = Math.log10(20000)

const DEFAULT_LOW  = 50
const DEFAULT_HIGH = 12000
const DEFAULT_BASS = 250
const DEFAULT_MID  = 2000

function hzToSlider(hz: number): number {
  return (Math.log10(Math.max(hz, 20)) - LOG_MIN) / (LOG_MAX - LOG_MIN) * 100
}

function sliderToHz(v: number): number {
  return Math.round(10 ** (LOG_MIN + v / 100 * (LOG_MAX - LOG_MIN)))
}

function ProcessBadge({ running }: { running: boolean }) {
  return (
    <Badge variant={running ? 'default' : 'destructive'} className="text-xs">
      {running ? 'Running' : 'Stopped'}
    </Badge>
  )
}

function StatusRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </>
  )
}

function StatusGrid({ status }: { status: SocketStatus | null }) {
  if (!status) {
    return <p className="text-sm text-muted-foreground">Waiting for data…</p>
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-2 text-sm items-start">
      <StatusRow label="Active">
        {status.active_profile_name ? (
          <span className="font-medium">{status.active_profile_name}</span>
        ) : (
          <span className="text-muted-foreground">None</span>
        )}
      </StatusRow>

      {status.color_mode && (
        <StatusRow label="Colour mode">
          <code className="text-xs font-mono">{status.color_mode}</code>
        </StatusRow>
      )}

      {status.onset_method && (
        <StatusRow label="Onset method">
          <code className="text-xs font-mono">{status.onset_method}</code>
        </StatusRow>
      )}

      <StatusRow label="Sync master">
        {status.sync_master ? (
          <div>
            {status.sync_master_name && (
              <div className="font-medium">{status.sync_master_name}</div>
            )}
            <code className="text-xs font-mono text-muted-foreground">
              {status.sync_master}
            </code>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </StatusRow>

      <StatusRow label="Delay">
        {status.applied_delay_ms} ms
      </StatusRow>

      <StatusRow label="Bridge">
        <Badge variant={status.bridge_connected ? 'default' : 'secondary'} className="text-xs">
          {status.bridge_connected ? 'Connected' : 'Disconnected'}
        </Badge>
      </StatusRow>

      <StatusRow label="squeezelite">
        <ProcessBadge running={status.processes.squeezelite} />
      </StatusRow>

      <StatusRow label="cava">
        <ProcessBadge running={status.processes.cava} />
      </StatusRow>

      {status.latency_warning && (
        <StatusRow label="Warning">
          <span className="text-destructive text-xs">{status.latency_warning}</span>
        </StatusRow>
      )}
    </dl>
  )
}

function CouplingSelector({
  status,
  onChanged,
}: {
  status: SocketStatus | null
  onChanged: () => void
}) {
  const [couplings, setCouplings] = useState<Coupling[]>([])
  const [selected, setSelected] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getCouplings().then(setCouplings).catch(() => {})
  }, [])

  // When couplings load and one is already active, pre-select it.
  useEffect(() => {
    if (!selected && status?.active_coupling_id) {
      setSelected(status.active_coupling_id)
    }
  }, [status?.active_coupling_id, couplings, selected])

  const isActive = !!status?.active_coupling_id
  const selectedIsActive = status?.active_coupling_id === selected

  async function handleActivate() {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      await activateCoupling(selected)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    setBusy(true)
    setError(null)
    try {
      await deactivateCoupling()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
          Active coupling
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <select
            className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={busy}
          >
            <option value="">— select a coupling —</option>
            {couplings.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>

          <Button
            size="sm"
            onClick={handleActivate}
            disabled={busy || !selected || selectedIsActive}
          >
            {busy && !isActive ? 'Activating…' : 'Activate'}
          </Button>

          {isActive && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleStop}
              disabled={busy}
            >
              {busy && isActive ? 'Stopping…' : 'Stop'}
            </Button>
          )}
        </div>

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  )
}

type Props = Pick<PreviewState, 'colour' | 'onset' | 'onset_bass' | 'onset_mid' | 'onset_treble' | 'bars' | 'status'>

export function NowPlaying({ colour, onset, onset_bass, onset_mid, onset_treble, bars, status }: Props) {
  const profileId = status?.active_profile_id ?? null

  const initializedForRef = useRef<string | null>(null)

  const [lowSlider, setLowSlider] = useState<number>(hzToSlider(50))
  const [highSlider, setHighSlider] = useState<number>(hzToSlider(12000))
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<string | null>(null)
  const [applyError, setApplyError] = useState(false)

  const [bassSlider, setBassSlider] = useState<number>(hzToSlider(250))
  const [midSlider, setMidSlider] = useState<number>(hzToSlider(2000))
  const [applyingBands, setApplyingBands] = useState(false)
  const [bandResult, setBandResult] = useState<string | null>(null)
  const [bandError, setBandError] = useState(false)

  // Reload coupling selector when activation changes.
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!profileId || profileId === initializedForRef.current) return
    if (!status) return
    initializedForRef.current = profileId
    if (status.lower_cutoff_freq != null) setLowSlider(hzToSlider(status.lower_cutoff_freq))
    if (status.higher_cutoff_freq != null) setHighSlider(hzToSlider(status.higher_cutoff_freq))
    if (status.bass_hz != null) setBassSlider(hzToSlider(status.bass_hz))
    if (status.mid_hz != null) setMidSlider(hzToSlider(status.mid_hz))
    setApplyResult(null)
    setBandResult(null)
  }, [profileId, status])

  const appliedLower  = status?.lower_cutoff_freq  ?? 50
  const appliedHigher = status?.higher_cutoff_freq ?? 12000
  const appliedBass   = status?.bass_hz            ?? 250
  const appliedMid    = status?.mid_hz             ?? 2000

  const pendingLower  = sliderToHz(lowSlider)
  const pendingHigher = sliderToHz(highSlider)
  const pendingBass   = sliderToHz(bassSlider)
  const pendingMid    = sliderToHz(midSlider)

  const hasChanges            = pendingLower !== appliedLower || pendingHigher !== appliedHigher
  const hasBandChanges        = pendingBass  !== appliedBass  || pendingMid    !== appliedMid
  const hasDefaultChanges     = pendingLower !== DEFAULT_LOW  || pendingHigher !== DEFAULT_HIGH
  const hasDefaultBandChanges = pendingBass  !== DEFAULT_BASS || pendingMid    !== DEFAULT_MID

  function handleBassChange(v: number) {
    const hz = sliderToHz(v)
    if (hz >= pendingMid) return
    if (hz <= appliedLower) return
    setBassSlider(v)
  }

  function handleMidChange(v: number) {
    const hz = sliderToHz(v)
    if (hz <= pendingBass) return
    if (hz >= appliedHigher) return
    setMidSlider(v)
  }

  function handleLowCommit(hz: number) {
    setLowSlider(hzToSlider(Math.max(20, Math.min(pendingHigher - 1, hz))))
  }

  function handleHighCommit(hz: number) {
    setHighSlider(hzToSlider(Math.max(pendingLower + 1, Math.min(20000, hz))))
  }

  function handleBassCommit(hz: number) {
    const constrained = Math.max(appliedLower + 1, Math.min(pendingMid - 1, hz))
    setBassSlider(hzToSlider(constrained))
  }

  function handleMidCommit(hz: number) {
    const constrained = Math.max(pendingBass + 1, Math.min(appliedHigher - 1, hz))
    setMidSlider(hzToSlider(constrained))
  }

  async function handleApply() {
    if (!profileId) return
    setApplying(true)
    setApplyResult(null)
    setApplyError(false)
    try {
      await restartCava(profileId, { lower_cutoff_freq: pendingLower, higher_cutoff_freq: pendingHigher })
      setApplyResult('Applied.')
    } catch (e) {
      setApplyResult(e instanceof Error ? e.message : 'Failed')
      setApplyError(true)
    } finally {
      setApplying(false)
    }
  }

  function handleReset() {
    setLowSlider(hzToSlider(appliedLower))
    setHighSlider(hzToSlider(appliedHigher))
  }

  function handleRestoreDefaults() {
    setLowSlider(hzToSlider(DEFAULT_LOW))
    setHighSlider(hzToSlider(DEFAULT_HIGH))
  }

  async function handleApplyBands() {
    if (!profileId) return
    setApplyingBands(true)
    setBandResult(null)
    setBandError(false)
    try {
      await restartCava(profileId, { bass_hz: pendingBass, mid_hz: pendingMid })
      setBandResult('Applied.')
    } catch (e) {
      setBandResult(e instanceof Error ? e.message : 'Failed')
      setBandError(true)
    } finally {
      setApplyingBands(false)
    }
  }

  function handleResetBands() {
    setBassSlider(hzToSlider(appliedBass))
    setMidSlider(hzToSlider(appliedMid))
  }

  function handleRestoreDefaultsBands() {
    setBassSlider(hzToSlider(DEFAULT_BASS))
    setMidSlider(hzToSlider(DEFAULT_MID))
  }

  return (
    <div className="space-y-4">
      <CouplingSelector
        key={reloadKey}
        status={status}
        onChanged={() => setReloadKey((k) => k + 1)}
      />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Live preview
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ColourSwatch r={colour.r} g={colour.g} b={colour.b} onset={onset} />
          <p className="text-xs text-muted-foreground mt-2">
            Colour of the first light channel. White outline&nbsp;= onset detected.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Spectrum
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <SpectrumBars
            bars={bars}
            colorMode={status?.color_mode ?? null}
            lowerCutoffHz={appliedLower}
            higherCutoffHz={appliedHigher}
            bassHz={appliedBass}
            midHz={appliedMid}
            onsetBass={onset_bass}
            onsetMid={onset_mid}
            onsetTreble={onset_treble}
          />

          {profileId && (
            <>
              <Separator />
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
                Band boundaries
              </p>
              <div className="space-y-3">
                <SliderField
                  label="Bass / mid"
                  value={bassSlider}
                  min={0}
                  max={100}
                  step={1}
                  format={() => `${pendingBass} Hz`}
                  onChange={handleBassChange}
                  disabled={applyingBands}
                  inputHz={pendingBass}
                  inputMin={20}
                  inputMax={20000}
                  onInputCommit={handleBassCommit}
                  inputTestId="bass-hz"
                />
                <SliderField
                  label="Mid / treble"
                  value={midSlider}
                  min={0}
                  max={100}
                  step={1}
                  format={() => `${pendingMid} Hz`}
                  onChange={handleMidChange}
                  disabled={applyingBands}
                  inputHz={pendingMid}
                  inputMin={20}
                  inputMax={20000}
                  onInputCommit={handleMidCommit}
                  inputTestId="mid-hz"
                />
                <div className="flex items-center gap-3">
                  <Button size="sm" onClick={handleApplyBands} disabled={applyingBands || !hasBandChanges} data-testid="apply-bands">
                    {applyingBands ? 'Applying…' : 'Apply'}
                  </Button>
                  <Button
                    size="sm" variant="outline"
                    onClick={handleResetBands}
                    disabled={applyingBands || !hasBandChanges}
                    title="Reset to saved profile value"
                    data-testid="reset-bands"
                  >
                    Reset to saved
                  </Button>
                  <Button
                    size="sm" variant="ghost"
                    onClick={handleRestoreDefaultsBands}
                    disabled={applyingBands || !hasDefaultBandChanges}
                    title="Restore factory defaults"
                    data-testid="restore-defaults-bands"
                  >
                    Restore defaults
                  </Button>
                  {bandResult && (
                    <span className={bandError ? 'text-destructive text-sm' : 'text-sm text-muted-foreground'}>
                      {bandResult}
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground italic ml-auto">
                    cava restarts briefly
                  </span>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {profileId && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
              Frequency cutoffs
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <SliderField
              label="Low cut"
              value={lowSlider}
              min={0}
              max={100}
              step={1}
              format={() => `${pendingLower} Hz`}
              onChange={setLowSlider}
              disabled={applying}
              inputHz={pendingLower}
              inputMin={20}
              inputMax={20000}
              onInputCommit={handleLowCommit}
              inputTestId="low-cut-hz"
            />
            <SliderField
              label="High cut"
              value={highSlider}
              min={0}
              max={100}
              step={1}
              format={() => `${pendingHigher} Hz`}
              onChange={setHighSlider}
              disabled={applying}
              inputHz={pendingHigher}
              inputMin={20}
              inputMax={20000}
              onInputCommit={handleHighCommit}
              inputTestId="high-cut-hz"
            />
            <div className="flex items-center gap-3">
              <Button size="sm" onClick={handleApply} disabled={applying || !hasChanges} data-testid="apply-cutoffs">
                {applying ? 'Applying…' : 'Apply'}
              </Button>
              <Button
                size="sm" variant="outline"
                onClick={handleReset}
                disabled={applying || !hasChanges}
                title="Reset to saved profile value"
                data-testid="reset-cutoffs"
              >
                Reset to saved
              </Button>
              <Button
                size="sm" variant="ghost"
                onClick={handleRestoreDefaults}
                disabled={applying || !hasDefaultChanges}
                title="Restore factory defaults"
                data-testid="restore-defaults-cutoffs"
              >
                Restore defaults
              </Button>
              {applyResult && (
                <span className={applyError ? 'text-destructive text-sm' : 'text-sm text-muted-foreground'}>
                  {applyResult}
                </span>
              )}
              <span className="text-xs text-muted-foreground italic ml-auto">
                cava restarts briefly
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          <StatusGrid status={status} />
        </CardContent>
      </Card>
    </div>
  )
}
