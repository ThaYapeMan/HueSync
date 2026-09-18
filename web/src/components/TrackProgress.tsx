import { useEffect, useRef, useState } from 'react'
import { Pause, Play, SkipBack, SkipForward, Square } from 'lucide-react'
import { controlCouplingTransport, type TransportAction } from '@/lib/api'
import type { TrackPosition } from '@/hooks/usePreviewSocket'

function clock(seconds: number | null): string {
  if (seconds == null) return '—'
  const whole = Math.floor(Math.max(0, seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Generic delivery-time anchor: neither player identity nor server clock is needed. */
export function TrackProgress({ track, connected, transport }: {
  track: TrackPosition | null
  connected: boolean
  transport?: { couplingId: string; targetName: string }
}) {
  const [elapsed, setElapsed] = useState<number | null>(track?.position_s ?? null)
  useEffect(() => {
    if (!connected) return // retain the last displayed position on disconnect
    const start = performance.now()
    const update = () => {
      if (track?.position_s == null) { setElapsed(null); return }
      const delta = track.playing && connected ? (performance.now() - start) / 1000 : 0
      const value = track.position_s + delta
      setElapsed(track.duration_s == null ? value : Math.min(value, track.duration_s))
    }
    update()
    const timer = setInterval(update, 250)
    return () => clearInterval(timer)
  }, [track, connected])
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { setError(null) }, [transport?.couplingId, transport?.targetName])
  async function send(action: TransportAction) {
    if (!transport || pending.current || !connected) return
    pending.current = true
    setBusy(true)
    setError(null)
    try { await controlCouplingTransport(transport.couplingId, action) }
    catch (e) { setError(e instanceof Error ? e.message : 'Transport failed') }
    finally { pending.current = false; setBusy(false) }
  }
  const duration = track?.duration_s ?? null
  return (
    <div className="relative flex items-center gap-3 overflow-hidden rounded-md border border-border bg-card px-3 py-2">
      <div className="min-w-0 flex-1 truncate text-sm"
        title={[track?.title || 'Track information unavailable', track?.artist].filter(Boolean).join(' — ')}>
        <span className="font-medium">{track?.title || 'Track information unavailable'}</span>
        {track?.artist && (
          <>
            <span className="text-muted-foreground"> — </span>
            <span className="text-muted-foreground">{track.artist}</span>
          </>
        )}
      </div>
      {transport && (
        <div className="flex shrink-0 items-center gap-1" role="group"
          aria-label={`Controls the followed player (${transport.targetName})`}
          title={`Controls the followed player (${transport.targetName})`}>
          {([
            ['previous', 'Previous', SkipBack],
            [track?.playing ? 'pause' : 'play', track?.playing ? 'Pause' : 'Play', track?.playing ? Pause : Play],
            ['next', 'Next', SkipForward], ['stop', 'Stop', Square],
          ] as const).map(([action, label, Icon]) => (
            <button key={label} type="button" aria-label={label} title={label}
              disabled={busy || !connected} onClick={() => void send(action)}
              className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline focus-visible:outline-2 disabled:opacity-40">
              <Icon className="h-4 w-4" aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
      {error && <span role="alert" className="max-w-32 truncate text-xs text-destructive" title={error}>{error}</span>}
      <div className="shrink-0 whitespace-nowrap text-xs text-muted-foreground font-mono">
        {clock(elapsed)} / {clock(duration)}
        {!connected && ' · Disconnected'}
        {connected && track && !track.playing && ' · Paused / stopped'}
      </div>
      {duration != null && duration > 0 && elapsed != null && (
        <div role="progressbar" aria-label="Track position" aria-valuemin={0}
          aria-valuemax={duration} aria-valuenow={elapsed}
          className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-muted">
          <div className="h-full bg-primary/60" style={{ width: `${100 * elapsed / duration}%` }} />
        </div>
      )}
    </div>
  )
}
