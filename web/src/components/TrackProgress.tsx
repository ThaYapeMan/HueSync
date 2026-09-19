import { useEffect, useRef, useState } from 'react'
import { BackwardIcon, ForwardIcon, PauseIcon, PlayIcon } from './MediaIcons'
import { controlCouplingTransport, type TransportAction } from '@/lib/api'
import type { TrackPosition } from '@/hooks/usePreviewSocket'

function clock(seconds: number | null): string {
  if (seconds == null) return '—'
  const whole = Math.floor(Math.max(0, seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Generic delivery-time anchor; transport is an optional, separate capability. */
export function TrackProgress({ track, connected, transport, showControls = !!transport }: {
  track: TrackPosition | null
  connected: boolean
  transport?: { couplingId: string; targetName: string; targetMac?: string }
  showControls?: boolean
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
  const pending = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const disabled = !connected || !track || !transport
  const gesture = useRef<{
    pointerId: number; held: boolean; tap: TransportAction
    timer: ReturnType<typeof setTimeout>; repeat?: ReturnType<typeof setInterval>
  } | null>(null)
  function cancelHold() {
    const current = gesture.current
    if (!current) return
    clearTimeout(current.timer)
    clearInterval(current.repeat)
    gesture.current = null
  }
  useEffect(() => {
    setError(null)
    // Never carry a held command across disconnect, target replacement or unmount.
    cancelHold()
    window.addEventListener('blur', cancelHold)
    return () => { cancelHold(); window.removeEventListener('blur', cancelHold) }
  }, [disabled, showControls, transport?.couplingId, transport?.targetName, transport?.targetMac])
  async function send(action: TransportAction) {
    if (!transport || pending.current || disabled) return
    // At most one request in flight: slow networks cannot build a seek backlog.
    pending.current = true
    setError(null)
    try { await controlCouplingTransport(transport.couplingId, action) }
    catch (e) {
      cancelHold()
      setError(e instanceof Error ? e.message : 'Transport failed')
    }
    finally { pending.current = false }
  }
  function beginHold(pointerId: number, tap: TransportAction, seek: TransportAction) {
    if (disabled || gesture.current) return
    const current = {
      pointerId, held: false, tap,
      timer: setTimeout(() => {
        current.held = true
        void send(seek)
        current.repeat = setInterval(() => void send(seek), 250)
      }, 400),
      repeat: undefined as ReturnType<typeof setInterval> | undefined,
    }
    gesture.current = current
  }
  function release(pointerId: number) {
    const current = gesture.current
    if (!current || current.pointerId !== pointerId) return
    cancelHold()
    if (!current.held) void send(current.tap)
  }
  const duration = track?.duration_s ?? null
  const hasProgress = duration != null && duration > 0 && elapsed != null
  const buttonClass = 'flex shrink-0 touch-none select-none items-center justify-center rounded text-foreground focus-visible:outline focus-visible:outline-2 disabled:opacity-40'
  return (
    <div data-testid="track-block" className="w-full space-y-2.5 rounded-xl border border-border bg-card px-4 pt-3 pb-2.5 backdrop-blur-sm">
      <div data-testid="track-title" className="flex min-w-0 items-baseline gap-1 text-sm"
        title={[track?.title || 'Track information unavailable', track?.artist].filter(Boolean).join(' — ')}>
        <span className="min-w-0 truncate font-medium text-foreground">{track?.title || 'Track information unavailable'}</span>
        {track?.artist && <span className="shrink-0 max-w-[45%] truncate text-muted-foreground"> — <span>{track.artist}</span></span>}
      </div>
      <div data-testid="track-progress-row" className="flex items-center gap-3 text-xs tabular-nums text-muted-foreground">
        <span className="w-12 shrink-0 text-right" data-testid="track-elapsed">{clock(elapsed)}</span>
        {/* Display only; pointer seeking is a separate follow-up. */}
        <div role="progressbar" aria-label="Track position" aria-valuemin={0}
          aria-valuemax={hasProgress ? duration : undefined}
          aria-valuenow={hasProgress ? elapsed : undefined}
          className="relative h-1 min-w-0 flex-1 rounded-full bg-muted">
          <div className="h-full rounded-full bg-foreground" style={{ width: `${hasProgress ? 100 * elapsed / duration : 0}%` }} />
          {hasProgress && <span className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground"
            style={{ left: `${100 * elapsed / duration}%` }} />}
        </div>
        <span className="w-12 shrink-0" data-testid="track-total">{clock(duration)}</span>
      </div>
      {showControls && (
        <div className="flex items-center justify-center gap-7" role="group" data-testid="track-controls"
          aria-label={`Controls the followed player (${transport?.targetName || 'unavailable'})`}
          title={`Controls the followed player (${transport?.targetName || 'unavailable'})`}>
          {(['previous', 'toggle', 'next'] as const).map(action => {
            const middle = action === 'toggle'
            const Icon = middle ? (track?.playing ? PauseIcon : PlayIcon) : action === 'previous' ? BackwardIcon : ForwardIcon
            const label = middle ? (track?.playing ? 'Pause' : 'Play') : action === 'previous' ? 'Previous' : 'Next'
            return (
              <button key={action} type="button" aria-label={label} title={label}
                data-testid={`transport-${action}`} disabled={disabled} className={`${buttonClass} ${middle ? 'h-9 w-9' : 'h-7 w-7'}`}
                onContextMenu={e => e.preventDefault()}
                onPointerDown={e => {
                  if (!middle && e.button === 0 && e.isPrimary) {
                    beginHold(e.pointerId, action, action === 'next' ? 'seek_forward' : 'seek_backward')
                  }
                }}
                onPointerUp={e => release(e.pointerId)}
                onPointerCancel={cancelHold} onPointerLeave={cancelHold}
                onClick={e => {
                  // Pointer taps dispatch on release; keyboard/AT clicks have detail=0.
                  if (middle || e.detail === 0) void send(action)
                }}>
                <span data-testid={`glyph-${action}`} className={middle ? 'block h-9 w-9' : 'block h-7 w-7'}>
                  <Icon aria-hidden="true" className={`h-full w-full ${middle && !track?.playing ? 'translate-x-[6%]' : ''}`} />
                </span>
              </button>
            )
          })}
        </div>
      )}
      {error && <div role="alert" className="truncate text-xs text-destructive" title={error}>{error}</div>}
    </div>
  )
}
