import { useEffect, useState } from 'react'
import type { TrackPosition } from '@/hooks/usePreviewSocket'

function clock(seconds: number | null): string {
  if (seconds == null) return '—'
  const whole = Math.floor(Math.max(0, seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Generic delivery-time anchor: neither player identity nor server clock is needed. */
export function TrackProgress({ track, connected }: {
  track: TrackPosition | null
  connected: boolean
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
