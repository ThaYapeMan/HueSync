import { toCssRgb } from '@/lib/colorUtils'
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

// Dot radius scales with light count.
// max(3, min(8, round(16 / √n)))  →  8 px for 2 lights, 3 px for 30+.
function dotRadius(n: number): number {
  return Math.max(3, Math.min(8, Math.round(16 / Math.sqrt(Math.max(n, 1)))))
}

// Map Hue position to SVG coordinate; shrinks usable area by the dot
// radius on each side so circles never clip the room outline.
function svgX(hx: number, radius: number): number {
  return PAD + radius + ((hx + 1) / 2) * (INNER_W - 2 * radius)
}

function svgY(hz: number, radius: number): number {
  // z=-1 (front of room) → bottom of SVG; z=1 (back) → top.
  return PAD + radius + ((1 - hz) / 2) * (INNER_H - 2 * radius)
}

export function FloorplanPreview({ channels, colours, onset }: Props) {
  if (channels.length === 0) return null

  const allAtOrigin = channels.every((ch) => ch.x === 0 && ch.z === 0)
  const r = dotRadius(channels.length)

  return (
    <div>
      {/* No width/height attributes: SVG is fully responsive via viewBox + CSS. */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto block"
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
          ? channels.map((ch, i) => {
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
                  fill={toCssRgb(col.r, col.g, col.b)}
                  stroke={onset ? 'white' : 'transparent'}
                  strokeWidth={onset ? 1 : 0}
                />
              )
            })
          : channels.map((ch, i) => {
              const col = colours[i] ?? { r: 0, g: 0, b: 0 }
              return (
                <circle
                  key={ch.channel_id}
                  cx={svgX(ch.x, r)}
                  cy={svgY(ch.z, r)}
                  r={r}
                  fill={toCssRgb(col.r, col.g, col.b)}
                  stroke={onset ? 'white' : 'transparent'}
                  strokeWidth={onset ? 1 : 0}
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
