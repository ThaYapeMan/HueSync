import { useEffect, useState } from 'react'
import type { TrackPosition } from '@/hooks/usePreviewSocket'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

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
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
          Track
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="text-sm font-medium truncate">{track?.title || 'Track information unavailable'}</div>
        {track?.artist && <div className="text-xs text-muted-foreground truncate">{track.artist}</div>}
        {duration != null && duration > 0 && elapsed != null && (
          <div role="progressbar" aria-label="Track position" aria-valuemin={0}
            aria-valuemax={duration} aria-valuenow={elapsed}
            className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary" style={{ width: `${100 * elapsed / duration}%` }} />
          </div>
        )}
        <div className="text-xs text-muted-foreground font-mono">
          {clock(elapsed)} / {clock(duration)}
          {!connected && ' · Disconnected'}
          {connected && track && !track.playing && ' · Paused / stopped'}
        </div>
      </CardContent>
    </Card>
  )
}
