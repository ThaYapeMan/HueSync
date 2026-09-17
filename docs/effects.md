# Effects reference

This is the current selectable catalog, verified against `EFFECT_IDS` in
`src/huesync/models.py`, `_make_renderer()` in `src/huesync/sync_engine.py`, and
`EFFECTS` in `web/src/lib/api.ts`. Configure an Effect in the **Effects** tab, then
select it as the high- or low-energy Effect of an EnergyProfile used by a Coupling.

Effects consume generic `AudioFeatures`; they do not select a player, Spectrum
engine or Beat algorithm. EnergyProfile owns blending between two Effects, not
another effect implementation. See [output boundaries](EFFECT_ENGINE.md) and
[configuration](configuration.md).

## Selectable catalog

Parameters below use their persisted names. “Onset” means the generic `onset`
flag, not a promised BPM/beat grid. Spatial effects use light x positions from the
Zone's Hue Entertainment Area; lights at the same x position see the same spatial
sample. Time-driven motion is evaluated at the lighting render cadence.

| `effect_type` | Visual behavior | Important parameters | Features meaningfully consumed / limitations |
|---|---|---|---|
| `spectrum_rgb` | Uniform bass-red, mid-green, treble-blue mixture | `sensitivity`, `bass_hz`, `mid_hz`, `onset_flash_intensity` | `bars`, `onset`; deliberately ignores `brightness_floor`, so silent channels remain dark |
| `spectrum_rgb_spatial` | Bass red at left, mid green at centre, treble blue at right, with linear crossfades | Same as `spectrum_rgb` | `bars`, `onset`; uses x positions clamped to −1…1; no brightness floor |
| `mono_pulse` | Uniform white brightness following average spectrum energy, optionally boosted on onset | `sensitivity`, `brightness_floor`, `onset_flash_intensity` | `bars`, `onset`; no selectable fixed hue |
| `pulses` | Onset-triggered envelope with spectrum color captured at the trigger | `effect_decay`, `brightness_floor`, `bass_hz`, `mid_hz`, `sensitivity` | `bars`, `onset`; retains previous hue when bars are silent; sensitivity scales bands before hue normalization, not the pulse envelope |
| `flashes` | White onset flash with a four-render-tick cooldown and rapid decay | `effect_decay`, `brightness_floor` | `onset`; floor is scaled to 30%; does not use spectrum hue or sensitivity |
| `splotches` | Changing spatial patches flare on onset and fade | `effect_decay`, `brightness_floor`, `sensitivity` | `onset`; deterministic changing seed and x-position mask, not independently random lamps |
| `fireworks` | Uniform onset flash plus four colored trails spreading from a changing origin | `effect_speed`, `effect_decay`, `bass_hz`, `mid_hz`, `sensitivity` | `bars`, `onset`; captures spectrum hue; black before the first onset, ignores brightness floor; each onset replaces the previous burst |
| `swirl` | Moving rainbow gradient across the Zone | `effect_speed`, `sensitivity`, `brightness_floor` | `full`; motion follows render time, not beat timing |
| `wave` | Traveling brightness wave, hue slowly follows spectral centroid | `effect_speed`, `sensitivity`, `brightness_floor` | `full`, `centroid`; `effect_decay` is not used (the UI hides it) |
| `solid` | Uniform color slowly following spectral centroid and energy | `sensitivity`, `brightness_floor` | `full`, `centroid`; not a fixed-color picker and not constant brightness |
| `none` | Black/off layer | None | No audio features; may still be blended with another Effect by EnergyProfile |

## Shared settings and defaults

The Effect model stores `effect_type=spectrum_rgb`, `effect_speed=1.0`,
`effect_decay=0.3`, `sensitivity=1.0`, `brightness_floor=0.15`, `bass_hz=250`,
`mid_hz=2000`, `exertion_clip=3.0`, and `onset_flash_intensity=0.0`, plus ID/name.
Not every renderer consumes every field; use the table above.

- `bass_hz` and `mid_hz` split the log-frequency bars using the Analyser cutoffs.
  Runtime also propagates the selected Effect's boundaries into feature analysis.
- `sensitivity` scales the particular renderer's energy input; it is not a universal
  post-render brightness multiplier. Spectrum channels clamp at 1.
- `effect_speed` controls spatial motion for fireworks, swirl and wave.
- Higher `effect_decay` shortens pulses/flashes/splotches in render ticks; fireworks
  uses elapsed time for exponential decay. These are not universal millisecond values.
- `exertion_clip` configures the external-bars `BandNormaliser`; it is not extra V2
  conditioning applied after CAVA Core, nor a per-renderer parameter.
- `onset_flash_intensity` lifts spectrum RGB channels or mono brightness toward white
  when an onset is observed. The dedicated onset effects use their own envelopes.

When `hpss_active` is actually supplied, pulses/flashes use `percussive_energy` for
intensity, fireworks uses it for speed, and swirl/wave/solid use `harmonic_energy`
to weight brightness. These are conditional consumers of the existing legacy PCM-tap
features. The canonical processor stack does not currently publish HPSS separation;
a checkbox does not make HPSS a canonical processor. Loudness and Chroma remain
extension protocols, not selectable shipped effects/analyzers.

## Publication and validation limits

Live Effects poll the freshest-by-audio-interval `latest()` snapshot. The separate
publication queue preserves delayed processor records in delivery order; a renderer
is not a guaranteed consumer of every queued onset event. Onset strength and per-band
onset fields are not directly used by these renderers.

This catalog describes **CODE-VERIFIED** behavior, not target visual quality or timing.
Realtime performance, light placement, network latency and visual V2/CAVA comparison
still **REQUIRE LXC VALIDATION**. No particular music genre or beat accuracy is promised.
