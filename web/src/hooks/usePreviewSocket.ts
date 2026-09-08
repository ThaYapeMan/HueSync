import { useEffect, useRef, useState } from 'react'

export interface SocketStatus {
  version: string | null
  active_coupling_id: string | null
  active_coupling_name: string | null
  active_player_type: string | null
  active_zone_id: string | null
  sync_master: string | null
  sync_master_name: string | null
  applied_delay_ms: number
  latency_warning: string | null
  processes: { squeezelite: boolean; cava: boolean }
  bridge_connected: boolean
  color_mode: string | null
  follower_warning: string | null
  onset_method: string | null
  lower_cutoff_freq: number | null
  higher_cutoff_freq: number | null
  bass_hz: number | null
  mid_hz: number | null
  airplay_receiving: boolean | null
}

export interface PreviewState {
  colour: { r: number; g: number; b: number }
  channel_colours: Array<{ r: number; g: number; b: number }>
  onset: boolean
  onset_bass: boolean
  onset_mid: boolean
  onset_treble: boolean
  mix: number
  bars: number[]
  status: SocketStatus | null
  connected: boolean
  reconnectAttempt: number
}

const INITIAL_STATE: PreviewState = {
  colour: { r: 0, g: 0, b: 0 },
  channel_colours: [],
  onset: false,
  onset_bass: false,
  onset_mid: false,
  onset_treble: false,
  mix: 0,
  bars: [],
  status: null,
  connected: false,
  reconnectAttempt: 0,
}

export function usePreviewSocket(): PreviewState {
  const [state, setState] = useState<PreviewState>(INITIAL_STATE)
  const onsetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bassTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const midTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const trebleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let stopped = false

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/ws/preview`)

      ws.onopen = () => {
        attempt = 0
        setState((s) => ({ ...s, connected: true, reconnectAttempt: 0 }))
      }

      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false, reconnectAttempt: attempt }))
        if (!stopped) {
          // Exponential backoff: 250 ms → 500 → 1 s → 2 s → 4 s, cap at 5 s.
          const delay = Math.min(250 * Math.pow(2, attempt), 5000)
          attempt++
          reconnectTimer = setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        ws?.close()
      }

      ws.onmessage = (event: MessageEvent) => {
        const msg = JSON.parse(event.data as string) as Record<string, unknown>

        if (msg.type === 'frame') {
          const c = msg.colour as { r: number; g: number; b: number }
          // Values arrive as 16-bit (0–65535); normalise to 0–1 for CSS.
          const colour = { r: c.r / 65535, g: c.g / 65535, b: c.b / 65535 }
          const raw_channels = (msg.channel_colours as Array<{ r: number; g: number; b: number }>) ?? []
          const channel_colours = raw_channels.map((ch) => ({
            r: ch.r / 65535,
            g: ch.g / 65535,
            b: ch.b / 65535,
          }))
          const onset = msg.onset as boolean
          const onset_bass = (msg.onset_bass as boolean) ?? false
          const onset_mid = (msg.onset_mid as boolean) ?? false
          const onset_treble = (msg.onset_treble as boolean) ?? false
          const mix = (msg.mix as number) ?? 0

          setState((s) => ({
            ...s,
            colour,
            channel_colours,
            onset: onset || s.onset,
            onset_bass: onset_bass || s.onset_bass,
            onset_mid: onset_mid || s.onset_mid,
            onset_treble: onset_treble || s.onset_treble,
            mix,
          }))

          if (onset) {
            if (onsetTimer.current) clearTimeout(onsetTimer.current)
            onsetTimer.current = setTimeout(
              () => setState((s) => ({ ...s, onset: false })),
              80
            )
          }
          if (onset_bass) {
            if (bassTimer.current) clearTimeout(bassTimer.current)
            bassTimer.current = setTimeout(
              () => setState((s) => ({ ...s, onset_bass: false })),
              80
            )
          }
          if (onset_mid) {
            if (midTimer.current) clearTimeout(midTimer.current)
            midTimer.current = setTimeout(
              () => setState((s) => ({ ...s, onset_mid: false })),
              80
            )
          }
          if (onset_treble) {
            if (trebleTimer.current) clearTimeout(trebleTimer.current)
            trebleTimer.current = setTimeout(
              () => setState((s) => ({ ...s, onset_treble: false })),
              80
            )
          }
        } else if (msg.type === 'spectrum') {
          setState((s) => ({ ...s, bars: msg.bars as number[] }))
        } else if (msg.type === 'status') {
          const { type: _ignored, ...fields } = msg
          setState((s) => ({ ...s, status: fields as unknown as SocketStatus }))
        }
      }
    }

    connect()

    return () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (onsetTimer.current) clearTimeout(onsetTimer.current)
      if (bassTimer.current) clearTimeout(bassTimer.current)
      if (midTimer.current) clearTimeout(midTimer.current)
      if (trebleTimer.current) clearTimeout(trebleTimer.current)
      ws?.close()
    }
  }, [])

  return state
}
