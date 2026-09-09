const PLACEHOLDER_COUNT = 30

const BAND_COLORS = {
  bass:   'rgb(239, 68, 68)',
  mid:    'rgb(34, 197, 94)',
  treble: 'rgb(96, 165, 250)',
}

function logFraction(hz: number, lower: number, upper: number): number {
  const logMin = Math.log10(Math.max(lower, 1))
  const logMax = Math.log10(Math.max(upper, lower + 1))
  return Math.max(0, Math.min(1, (Math.log10(Math.max(hz, 1)) - logMin) / (logMax - logMin)))
}

function fmtHz(hz: number): string {
  return hz >= 1000 ? `${Math.round(hz / 100) / 10} kHz` : `${hz} Hz`
}

function bandAvg(bars: number[], lo: number, hi: number): number {
  const slice = bars.slice(lo, hi)
  return slice.length ? slice.reduce((a, b) => a + b, 0) / slice.length : 0
}

interface Props {
  bars: number[]
  colorMode: string | null
  lowerCutoffHz?: number
  higherCutoffHz?: number
  bassHz?: number
  midHz?: number
  onsetBass?: boolean
  onsetMid?: boolean
  onsetTreble?: boolean
}

export function SpectrumBars({
  bars,
  colorMode,
  lowerCutoffHz = 50,
  higherCutoffHz = 12000,
  bassHz = 250,
  midHz = 2000,
  onsetBass = false,
  onsetMid = false,
  onsetTreble = false,
}: Props) {
  const data = bars.length > 0 ? bars : Array(PLACEHOLDER_COUNT).fill(0)
  const n = data.length
  const isRgb = colorMode === 'spectrum_rgb'

  // Single source of truth for both bar colouring and divider lines.
  // Uses the same log-scale formula as the backend _hz_to_frac().
  const bassHi = Math.floor(logFraction(bassHz, lowerCutoffHz, higherCutoffHz) * n)
  const midHi  = Math.floor(logFraction(midHz,  lowerCutoffHz, higherCutoffHz) * n)

  const bassAvg   = isRgb ? bandAvg(data, 0,      bassHi) : 0
  const midAvg    = isRgb ? bandAvg(data, bassHi, midHi)  : 0
  const trebleAvg = isRgb ? bandAvg(data, midHi,  n)      : 0

  // Detect empty bands: warn the user instead of silently showing nothing.
  const bassEmpty   = isRgb && bassHi === 0
  const midEmpty    = isRgb && midHi <= bassHi
  const trebleEmpty = isRgb && midHi >= n

  // Axis tick marks: low cutoff, bass/mid boundary, mid/treble boundary, high cutoff.
  type Align = 'left' | 'center' | 'right'
  const ticks: { pct: number; label: string; color?: string; align: Align }[] = []
  if (isRgb) {
    ticks.push({ pct: 0,   label: fmtHz(lowerCutoffHz), align: 'left' })
    const bassPct = (bassHi / n) * 100
    const midPct  = (midHi  / n) * 100
    if (bassPct > 5 && bassPct < 95)
      ticks.push({ pct: bassPct, label: fmtHz(bassHz), color: BAND_COLORS.bass, align: 'center' })
    if (midPct > 5 && midPct < 95 && Math.abs(midPct - bassPct) > 8)
      ticks.push({ pct: midPct, label: fmtHz(midHz), color: BAND_COLORS.mid, align: 'center' })
    ticks.push({ pct: 100, label: fmtHz(higherCutoffHz), align: 'right' })
  }

  return (
    <div>
      <div className="relative h-20 flex items-end gap-px">
        {data.map((v, i) => {
          let bg: string
          if (isRgb) {
            if (i < bassHi) bg = BAND_COLORS.bass
            else if (i < midHi) bg = BAND_COLORS.mid
            else bg = BAND_COLORS.treble
          } else {
            bg = 'hsl(var(--accent))'
          }
          return (
            <div
              key={i}
              className="flex-1 rounded-sm"
              style={{
                height: `${Math.max(v * 100, 2)}%`,
                backgroundColor: bg,
                opacity: Math.max(v, 0.18),
                transition: 'height 33ms linear, opacity 33ms linear',
              }}
            />
          )
        })}

        {isRgb && [bassHi, midHi].map((hi) => (
          <div
            key={hi}
            className="absolute inset-y-0 w-px bg-white/20 pointer-events-none"
            style={{ left: `${(hi / n) * 100}%` }}
          />
        ))}
      </div>

      {!isRgb && colorMode !== null && (
        <p className="mt-1 text-xs text-muted-foreground italic">
          Bass/mid/treble colour-coding only applies when the active Effect is Spectrum RGB.
        </p>
      )}

      {isRgb && (
        <>
          <div className="relative h-4 mt-0.5 text-xs font-mono text-muted-foreground">
            {ticks.map(({ pct, label, color, align }) => (
              <span
                key={pct}
                className="absolute whitespace-nowrap"
                style={{
                  left: `${pct}%`,
                  color: color ?? undefined,
                  transform:
                    align === 'center' ? 'translateX(-50%)' :
                    align === 'right'  ? 'translateX(-100%)' :
                    undefined,
                }}
              >
                {label}
              </span>
            ))}
          </div>

          <div className="flex justify-between mt-1 text-xs font-mono">
            <span style={{ color: BAND_COLORS.bass }} className="flex items-center gap-1">
              {onsetBass && <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />}
              {bassEmpty ? <em className="not-italic opacity-50">Bass (empty)</em> : `Bass ${bassAvg.toFixed(2)}`}
            </span>
            <span style={{ color: BAND_COLORS.mid }} className="flex items-center gap-1">
              {onsetMid && <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />}
              {midEmpty ? <em className="not-italic opacity-50">Mid (empty)</em> : `Mid ${midAvg.toFixed(2)}`}
            </span>
            <span style={{ color: BAND_COLORS.treble }} className="flex items-center gap-1">
              {onsetTreble && <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />}
              {trebleEmpty ? <em className="not-italic opacity-50">Treble (empty)</em> : `Treble ${trebleAvg.toFixed(2)}`}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
