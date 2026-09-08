import type { ChannelPosition } from '@/lib/api'

interface Props {
  channels: ChannelPosition[]
  colours: Array<{ r: number; g: number; b: number }>
  onset: boolean
}

const W = 200
const H = 200
const PAD = 14
const INNER_W = W - 2 * PAD
const INNER_H = H - 2 * PAD

// Scale dot radius with light count: large for few lights, shrinks for many.
// Formula keeps dots from overlapping regardless of count.
function dotRadius(n: number): number {
  return Math.max(6, Math.min(20, Math.round(40 / Math.sqrt(Math.max(n, 1)))))
}

function rgb(r: number, g: number, b: number): string {
  return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`
}

// Hue: x ∈ [-1,1] left→right, z ∈ [-1,1] front→back
// SVG: x grows right, y grows down; front of room (z=-1) at bottom.
function svgX(hx: number): number {
  return PAD + ((hx + 1) / 2) * INNER_W
}

function svgY(hz: number): number {
  return PAD + ((1 - hz) / 2) * INNER_H
}

export function FloorplanPreview({ channels, colours, onset }: Props) {
  if (channels.length === 0) return null

  const allAtOrigin = channels.every((ch) => ch.x === 0 && ch.z === 0)
  const r = dotRadius(channels.length)

  return (
    <div>
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full block"
        aria-label="Light floorplan"
      >
        {/* Room outline */}
        <rect
          x={PAD}
          y={PAD}
          width={INNER_W}
          height={INNER_H}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.15}
          strokeWidth={1}
          rx={4}
        />

        {/* "Front" label */}
        <text
          x={W / 2}
          y={H - 2}
          textAnchor="middle"
          fontSize={8}
          fill="currentColor"
          fillOpacity={0.3}
        >
          front
        </text>

        {allAtOrigin
          ? /* Circular fallback when bridge positions are all at origin */
            channels.map((ch, i) => {
              const angle = (i / channels.length) * 2 * Math.PI - Math.PI / 2
              const rr = Math.min(INNER_W, INNER_H) * 0.32
              const cx = W / 2 + rr * Math.cos(angle)
              const cy = H / 2 + rr * Math.sin(angle)
              const col = colours[i] ?? { r: 0, g: 0, b: 0 }
              return (
                <circle
                  key={ch.channel_id}
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill={rgb(col.r, col.g, col.b)}
                  stroke={onset ? 'white' : 'transparent'}
                  strokeWidth={onset ? 1.5 : 0}
                />
              )
            })
          : channels.map((ch, i) => {
              const col = colours[i] ?? { r: 0, g: 0, b: 0 }
              return (
                <circle
                  key={ch.channel_id}
                  cx={svgX(ch.x)}
                  cy={svgY(ch.z)}
                  r={r}
                  fill={rgb(col.r, col.g, col.b)}
                  stroke={onset ? 'white' : 'transparent'}
                  strokeWidth={onset ? 1.5 : 0}
                />
              )
            })}
      </svg>
      {allAtOrigin && (
        <p className="text-[10px] text-muted-foreground text-center mt-1">
          Positions not set in Hue app — circular layout
        </p>
      )}
    </div>
  )
}
