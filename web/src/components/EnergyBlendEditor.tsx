import { useState } from 'react'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface Props {
  blendStart: number
  blendEnd: number
  blendResponse: number
  onChangeBlendStart: (v: number) => void
  onChangeBlendEnd: (v: number) => void
  onChangeBlendResponse: (v: number) => void
  liveEnergy?: number
}

function settleTime(response: number): string {
  const secs = 3 / (response * 30)
  return secs >= 10 ? `~${Math.round(secs)} s` : `~${secs.toFixed(1)} s`
}

export function EnergyBlendEditor({
  blendStart,
  blendEnd,
  blendResponse,
  onChangeBlendStart,
  onChangeBlendEnd,
  onChangeBlendResponse,
  liveEnergy,
}: Props) {
  const [previewEnergy, setPreviewEnergy] = useState(0.5)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const displayEnergy = liveEnergy !== undefined ? liveEnergy : previewEnergy

  function handleRangeChange(values: number[]) {
    const [start, end] = values
    if (start !== blendStart) onChangeBlendStart(Math.round(start * 100) / 100)
    if (end !== blendEnd) onChangeBlendEnd(Math.round(end * 100) / 100)
  }

  const startPct = blendStart * 100
  const endPct = blendEnd * 100
  const markerPct = displayEnergy * 100

  // EMA response slider: map blend_response [0.01..0.5] → slider [0..1]
  const responseSliderValue = (blendResponse - 0.01) / (0.5 - 0.01)

  function handleResponseSlider(values: number[]) {
    const mapped = 0.01 + values[0] * (0.5 - 0.01)
    onChangeBlendResponse(Math.round(mapped * 1000) / 1000)
  }

  return (
    <div className="space-y-4">
      {/* Blend zone visualisation + dual-handle slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground mb-1">
          <span>LOW ENERGY</span>
          <span>HIGH ENERGY</span>
        </div>

        {/* Colour zones behind the slider */}
        <div className="relative h-2 rounded-full overflow-hidden mb-3">
          {/* Low energy zone */}
          <div
            className="absolute inset-y-0 left-0 bg-blue-500/40"
            style={{ right: `${100 - startPct}%` }}
          />
          {/* Blend zone */}
          <div
            className="absolute inset-y-0 bg-amber-400/40"
            style={{ left: `${startPct}%`, right: `${100 - endPct}%` }}
          />
          {/* High energy zone */}
          <div
            className="absolute inset-y-0 right-0 bg-orange-500/40"
            style={{ left: `${endPct}%` }}
          />
        </div>

        <Slider
          min={0}
          max={1}
          step={0.01}
          value={[blendStart, blendEnd]}
          onValueChange={handleRangeChange}
        />

        {/* Zone labels */}
        <div className="relative h-4 text-xs text-muted-foreground font-mono mt-0.5">
          <span className="absolute left-0">LOW 100%</span>
          <span
            className="absolute -translate-x-1/2 text-amber-500"
            style={{ left: `${(startPct + endPct) / 2}%` }}
          >
            AUTO BLEND
          </span>
          <span className="absolute right-0">HIGH 100%</span>
        </div>

        {/* Energy marker */}
        <div className="relative h-5 mt-1">
          <div
            className="absolute flex flex-col items-center -translate-x-1/2"
            style={{ left: `${markerPct}%` }}
          >
            <div className={`w-0.5 h-3 ${liveEnergy !== undefined ? 'bg-green-400' : 'bg-muted-foreground/50'}`} />
            <span className={`text-[9px] whitespace-nowrap ${liveEnergy !== undefined ? 'text-green-400' : 'text-muted-foreground/60'}`}>
              {liveEnergy !== undefined ? `live ${Math.round(markerPct)}%` : `preview ${Math.round(markerPct)}%`}
            </span>
          </div>
        </div>
      </div>

      {/* Response slider */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs">
          <span className="text-muted-foreground">Response</span>
          <span className="text-muted-foreground font-mono">{settleTime(blendResponse)}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-12">Smooth</span>
          <Slider
            min={0}
            max={1}
            step={0.001}
            value={[responseSliderValue]}
            onValueChange={handleResponseSlider}
            className="flex-1"
          />
          <span className="text-xs text-muted-foreground w-8 text-right">Fast</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Blend settles over {settleTime(blendResponse)} after a sustained energy change
        </p>
      </div>

      {/* Preview simulation (only when not live) */}
      {liveEnergy === undefined && (
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Drag to simulate music energy</Label>
          <Slider
            min={0}
            max={1}
            step={0.01}
            value={[previewEnergy]}
            onValueChange={(v) => setPreviewEnergy(v[0])}
          />
          <div className="flex justify-between text-xs text-muted-foreground font-mono">
            <span>0%</span>
            <span>{Math.round(previewEnergy * 100)}%</span>
            <span>100%</span>
          </div>
        </div>
      )}

      {/* Advanced (numeric inputs) */}
      <div>
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setAdvancedOpen((o) => !o)}
        >
          <span>{advancedOpen ? '▾' : '▸'}</span>
          <span>Advanced</span>
        </button>

        {advancedOpen && (
          <div className="mt-2 space-y-2 pl-3 border-l border-border">
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">Blend start</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={blendStart}
                  onChange={(e) => onChangeBlendStart(parseFloat(e.target.value) || 0)}
                  className="h-7 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Blend end</Label>
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={blendEnd}
                  onChange={(e) => onChangeBlendEnd(parseFloat(e.target.value) || 0)}
                  className="h-7 text-xs"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Response</Label>
                <Input
                  type="number"
                  step={0.01}
                  min={0.01}
                  max={0.5}
                  value={blendResponse}
                  onChange={(e) => onChangeBlendResponse(parseFloat(e.target.value) || 0.1)}
                  className="h-7 text-xs"
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
