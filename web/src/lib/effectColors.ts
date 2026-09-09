export const EFFECT_SWATCH_CLASS: Record<string, string> = {
  spectrum_rgb:         'bg-gradient-to-r from-red-500 via-green-400 to-blue-500',
  spectrum_rgb_spatial: 'bg-gradient-to-r from-red-400 via-green-400 to-blue-400',
  mono_pulse:           'bg-slate-200',
  pulses:               'bg-sky-400',
  flashes:              'bg-amber-200',
  splotches:            'bg-violet-500',
  fireworks:            'bg-gradient-to-r from-orange-500 to-amber-300',
  swirl:                'bg-gradient-to-r from-emerald-400 to-teal-500',
  wave:                 'bg-gradient-to-r from-cyan-400 to-blue-500',
  solid:                'bg-slate-400',
  none:                 'bg-slate-800',
}

export const EFFECT_RGB: Record<string, [number, number, number]> = {
  spectrum_rgb:         [96,  165, 250],
  spectrum_rgb_spatial: [192, 132, 252],
  mono_pulse:           [210, 214, 220],
  pulses:               [56,  189, 248],
  flashes:              [253, 224, 132],
  splotches:            [167, 139, 250],
  fireworks:            [251, 146,  60],
  swirl:                [52,  211, 153],
  wave:                 [34,  211, 238],
  solid:                [148, 163, 184],
  none:                 [20,   20,  20],
}

export function getEffectRgb(effectType: string): [number, number, number] {
  return EFFECT_RGB[effectType] ?? [100, 100, 100]
}

export function getEffectSwatchClass(effectType: string): string {
  return EFFECT_SWATCH_CLASS[effectType] ?? 'bg-muted'
}
