# HueSync effect engine

*Single authoritative spec. The five source documents are preserved in
`docs/archive/` for historical context; this file supersedes all of them.*

---

## Goals and current shortcomings

HueSync currently has one colour mode active at a time (`spectrum_rgb`,
`bass_brightness`, or `mono_pulse`), derived directly from absolute bar values.
Two structural problems make it unsatisfying regardless of which mode is active.

**The yellow problem.** cava's default upper frequency limit is 22 kHz. Music
has almost no energy above 10-12 kHz, so the top half of the bar frame sits
near zero permanently. In `spectrum_rgb` the "treble" channel never fires;
output is always yellow (red + green).
*Status: fixed* — explicit cutoffs (`lower_cutoff_freq = 50`,
`higher_cutoff_freq = 10000`) written to the generated cava config.

**The monotony problem.** A single mode running at fixed behaviour ignores what
the music is doing. Quiet passages and intense passages look identical. There is
no layering, no colour event on musical boundaries, and every light shows the
same colour regardless of position in the room.
*Status: open* — addressed by the architecture below.

---

## Architecture

### Signal chain

```
LMS
 └── squeezelite (virtual player, one per Entertainment Area)
      └── /dev/shm/squeezelite-<mac>  (PCM shared memory)
           ├── cava (FFT + log-spaced bars, 30+ fps)
           │    └── FIFO → AnalysisEngine
           └── [optional] PCM tap → LibrosaAnalyser / AubioAnalyser
```

```
AnalysisEngine
 ├── BandNormaliser      AGC: exertion per bar (done)
 ├── peak_isolation      non-linear contrast boost
 ├── cumulative_bands    bass / mid / full slices (mel-like proportions)
 └── OnsetDetector       spectral flux + threshold + cooldown
 → AudioFeatures
```

```
EffectEngine
 ├── MellowLayer.render(features, t)  → Scene
 ├── ActiveLayer.render(features, t)  → Scene
 └── LayerMixer(energy)               → Scene  (crossfade)
```

```
OutputDriver
 └── HueDriver: Scene × LightChannel.position → LightColorCommand per channel
      └── hue-entertainment → bridge → Entertainment Area
```

### Layer boundaries

The key architectural principle is that **each layer knows nothing about the
layer below it**:

- `AudioFeatures` is the contract between analysis and effects. An effect
  never reads cava bars directly.
- `Scene` is the contract between effects and the output driver. An effect
  never constructs a `LightColorCommand`. A `Scene` is a function:
  `Color = scene(position: Vec3, t: float)`. The driver samples it at each
  light's registered position.
- `OutputDriver` is the only thing that knows about the Hue API. Replacing the
  transport (e.g. a future DMX driver) requires only a new driver, not touching
  any effect.

This makes every layer independently testable: supply a `NullAnalyser` with
scripted `AudioFeatures`, check the `Scene` values at fixed positions, never
touch real hardware.

---

## Analysis chain

### AudioFeatures dataclass

```python
@dataclass
class AudioFeatures:
    # Per-bar exertion values (0.0-1.0), output of BandNormaliser
    bars: list[float]

    # Cumulative mel-like band energies (overlapping, not exclusive)
    # bass:  lower ~20% of bars  (~20-350 Hz at 30 bars, 50-10000 Hz range)
    # mid:   lower ~55% of bars  (~20-2000 Hz)
    # full:  all bars
    bass: float
    mid: float
    full: float

    # Spectral centroid, normalised 0.0-1.0 across the bar frame
    centroid: float

    # Onset detection
    onset: bool
    onset_strength: float          # spectral flux value, unnormalised

    # Optional — only filled by LibrosaAnalyser or AubioAnalyser
    beat: bool | None = None       # True on beat frames
    tempo: float | None = None     # BPM estimate
```

Effects must degrade gracefully when `beat` or `tempo` is `None`. If an effect
requires `beat` and none is available it falls back to onset-triggered behaviour.

### Band boundaries

cava's bars are log-spaced, approximating the mel scale. With
`lower_cutoff_freq = 50`, `higher_cutoff_freq = 10000`, and 30 bars, the
cumulative slices are:

| Field  | Bar range | Approx Hz   | Content                     |
|--------|-----------|-------------|----------------------------|
| `bass` | 0-5       | 50-350 Hz   | sub-bass, kick, bass guitar |
| `mid`  | 0-16      | 50-2000 Hz  | bass through upper mid      |
| `full` | 0-29      | 50-10000 Hz | full audible range          |

These are **cumulative** (like LedFx's melbanks), not exclusive bands. Effects
that react to bass receive a signal already containing it; they pick the
smallest slice that covers their range.

The current code uses exclusive bands (0-15%, 15-50%, 50-100%) with the wrong
proportions. Replace with the cumulative model above when adding new effects.
Keep the old exclusive bands for backwards compatibility with existing
`ColorMode` values.

### In-frame analysis (no external library)

All per-frame work requires no dependencies beyond the standard library:

```python
# Spectral flux onset detection
flux = sum(max(0, bars[i] - prev_bars[i]) for i in range(n))
onset = flux > flux_mean + k * flux_stddev  # k ≈ 1.5-2.0

# Spectral centroid (position of "centre of mass")
centroid = sum(i * bars[i] for i in range(n)) / max(sum(bars), 1e-6) / n

# Peak isolation (after BandNormaliser)
value = value ** (1.0 - peak_isolation)   # peak_isolation 0.0-1.0, default 0.4
```

Cooldown after each onset: ~120 ms (≈ 4 frames at 30 Hz). Without a cooldown
the onset fires on every hi-hat transient.

### Analysis backends

The `Analyser` protocol keeps the backend decision reversible:

```python
class Analyser(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def latest(self) -> AudioFeatures: ...
```

Three backends, in order of increasing cost:

**CavaPipeline** (implement first)
Reads cava bars from the FIFO. Adds spectral flux onset detection, spectral
centroid, and peak isolation in pure Python. Fills all `AudioFeatures` fields
except `beat` and `tempo`. Zero new dependencies, negligible CPU.

**LibrosaAnalyser** (add if tempo is needed)
Reads PCM directly from `/dev/shm/squeezelite-<mac>` alongside cava. Runs
`librosa.beat.beat_track()` on a rolling buffer (last 5-10 s) every few
seconds — bounded cost, not per-frame. Fills `beat` and `tempo`. Licence: ISC
(permissive, compatible with MIT). Pulls in NumPy, SciPy, numba — verify
cold-start time on the LXC before committing.

**AubioAnalyser** (subprocess, only if 1 and 2 fall short)
Runs `aubiotrack` / `aubioonset` from the PCM tap and parses output line by
line. Same subprocess pattern as cava and squeezelite — aubio itself is GPL-3
but spawning it as a subprocess does not make HueSync a derivative work. Only
worth the complexity if onset/beat quality proves to be the limiting factor.

**NullAnalyser**
Scripted `AudioFeatures` for unit tests. Never touches hardware.

---

## Effect catalogue

### Scene model

An effect's `render()` returns a `Scene`, not RGB values:

```python
class Scene(Protocol):
    def color_at(self, position: Vec3, t: float) -> Color: ...
```

The `HueDriver` calls `scene.color_at(channel.position, t)` for each registered
`LightChannel`. Spatial effects work naturally; uniform effects return the same
colour regardless of position.

### Palettes

Effects never compute an arbitrary RGB value. They index into a palette.

- A palette is an ordered list of 2-4 colours.
- Ship a small set: `warm` (amber/orange/red), `cool` (blue/cyan/white),
  `neon` (magenta/green/yellow), `monochrome` (white shades), `rainbow`.
- A profile picks one palette; effects index into it by a float position.
- Palette position is driven by spectral centroid (bass-heavy → one end,
  bright/airy → the other) or dominant band index, not by a raw RGB mapping.
- **Palette changes are events, not drifts.** Change on a sustained energy
  shift or every N beats. Continuous colour drift produces the same muddy
  result as `spectrum_rgb` with extra steps.

### Mellow layer

Runs when energy is low. Slow, drifting, low contrast.

**Drift gradient** — palette position driven by centroid, advancing slowly
over time. Brightness follows overall energy gently. No hard transitions.

**Ambient** — saturation reacts to bass; hue drifts slowly; brightness stays
constant. Based on Music Assistant's Ambient mode description.

### Active layer

Triggers during loud or dense passages. All four from Light DJ:

**Pulses** — all lights breathe together with the beat: fast attack on onset,
exponential decay. Closest to the existing `mono_pulse`. Simplest to implement.

**Splotches** — random subsets of lights take a palette colour on each onset;
others stay dim. Feels like patches of colour appearing around the room. Can
cluster spatially (lights near each other get the same assignment) once
positions are available.

**Fireworks** — on an onset, one light flashes bright then decays; neighbouring
lights get a weaker, delayed version, radiating outward. Requires
`LightChannel.position`.

**Flashes** — hard, short brightness spikes on onsets, near-black between
them. Most aggressive. Hard cooldown (~120 ms) required or it fires on every
hi-hat transient.

Each effect receives the current `Palette`; it never picks colours freely.

### Layer mixer

```
energy = AudioFeatures.full  (normalised overall loudness)
mix = smoothstep(energy, low_threshold=0.3, high_threshold=0.7)  # 0.0-1.0

mellow_scene = mellow_layer.render(features, t)
active_scene  = active_layer.render(features, t)
final_scene   = lerp(mellow_scene, active_scene, mix)
```

`mix` needs its own smoothing (separate EMA, slower than the audio EMA) so
the layers crossfade rather than flicker. During intense passages increase
colour inertia to prevent flickering between bass hits and hi-hat transients.

### Spatial rendering

`LightChannel` from the `hue-entertainment` library carries a `position`
(x, y, z) per light, set when the Entertainment Area was configured in the Hue
app. Capture and store these at session start.

Without positions every light is identical. With them:

- **Waves** — `brightness = envelope((t × speed) − (position.x × wavelength))`
- **Fireworks** — decay strength as function of distance from origin light
- **Splotches** — spatial clustering of colour assignments
- **Spectrum RGB Spatial** — bass/mid/treble bands placed at `position.x`
  = -1/0/+1, cross-faded linearly between neighbours (EffectType
  `spectrum_rgb_spatial`)

Position capture is the prerequisite for everything spatial and should be one
of the first things added.

### Latency compensation

The Hue bridge, network, and the squeezelite→cava→FIFO chain introduce fixed
delay. Add `light_latency_ms` as a profile field (default 20, range 0-3000).
Implementation: keep a short ring buffer of recent rendered frames and output
from `now − latency_ms`. Without it the lights are perpetually behind the
audio.

---

## Order of work

Layer boundaries first — establish the contracts before filling them in. Each
step is independently deployable and testable by ear.

| # | Step | Unlocks |
|---|------|---------|
| 1 | ✅ **cava frequency cutoffs** (done) | Blue channel active, fixes yellow |
| 2 | **`LightChannel.position` capture** — store at session start | All spatial effects |
| 3 | **`AudioFeatures` dataclass + `CavaPipeline`** — spectral flux onset, centroid, cumulative bands, peak isolation; replaces bare frame passing | Layer-independent testing |
| 4 | **`Scene` model + `HueDriver`** — effects return `Scene`, driver samples at positions | Effect/transport separation |
| 5 | **Palettes** — named colour sets, profile field | Effects that look intentional |
| 6 | **Mellow layer** (drift gradient) + **Active layer** (Pulses) + **LayerMixer** | End-to-end two-layer engine |
| 7 | **Remaining active effects**: Splotches, Flashes, Fireworks | Full active catalogue |
| 8 | **Mellow Ambient** | Full mellow catalogue |
| 9 | **LibrosaAnalyser** — rolling PCM buffer, tempo, beat — only if step 3 onset quality is insufficient | Beat-locked effects |
| 10 | **Player-per-area model** — one squeezelite per Entertainment Area, LMS sync as control surface | Architectural restructure; worth doing only once effects are worth driving |
| 11 | **`light_latency_ms`** setting | Compensates bridge/network delay |

Steps 1-8 are incremental and individually testable. Step 10 is a restructure
and should be its own piece of work.

---

## Licence notes

**Algorithms are not copyrightable. Implementations are.** Spectral flux,
mel filterbanks, spectral centroid, autocorrelation tempo estimation — all
published research. Reimplementing them from the papers in MIT-licensed code
is entirely legitimate. LedFx did exactly this.

| Library | Licence | Use |
|---------|---------|-----|
| librosa | ISC | Permissive. Compatible with MIT. Safe to import. |
| NumPy / SciPy | BSD | Permissive. No restrictions. |
| aubio | GPL-3 | Cannot be imported. Safe to spawn as subprocess (same as cava/squeezelite). |
| BTrack | GPL-3 | Same as aubio. Subprocess only; primarily a library, so less natural. |
| Essentia | AGPL-3 | Strictest copyleft. Avoid. |
| madmom | mixed | Neural-network batch processing; too heavy regardless of licence. |
| LedFx | GPL-3 | Read for ideas and documented algorithms. Do not copy code into this project. |

The subprocess boundary is the established pattern for GPL tools in this
codebase — squeezelite (GPL-2+) and cava (MIT, but noting the precedent)
are already managed this way.

---

## Sources

**LedFx** — analysis architecture, melbank constants, peak isolation, AGC,
frequency bin table, gradient extraction:
- https://docs.ledfx.app/en/latest/developer/melbanks.html
- https://docs.ledfx.app/en/latest/developer/gradient_extraction.html
- https://github.com/LedFx/LedFx

**Light DJ** — two-layer (Mellow/Active) architecture, Splotches/Fireworks/
Pulses/Flashes effects, Groove Wave, tempo controls, palette selection:
- https://lightdjapp.com/android
- https://lightdjapp.com/ios

**Music Assistant** — player-per-area model, Smooth/Ambient modes, light
latency setting, `hue-entertainment` library:
- https://www.music-assistant.io/plugins/hue-entertainment/
- https://github.com/music-assistant/hue-entertainment

**Onset detection:**
- Spectral flux / beat tracking pipeline:
  https://blog.paperspace.com/audio-analysis-processing-maching-learning/
- Superflux (Böck & Widmer 2013) as implemented in librosa:
  https://librosa.org/doc/latest/auto_tutorials/03-advanced/plot_superflux.html
- Exertion scoring, circular hue mapping, colour inertia, strobe blanking:
  https://github.com/CanYuzbey/music-reactive-lighting

**librosa** — ISC licence, onset_strength/onset_detect/beat_track:
- https://cloudsmith.com/navigator/pypi/librosa
- https://proceedings.scipy.org/articles/Majora-7b98e3ed-003.pdf

**Spectral centroid as perceived brightness:**
- https://www.runcomfy.com/comfyui-nodes/deforum-comfy-nodes/SpectralCentroid

**BTrack** (GPL-3, causal real-time beat tracking, subprocess only):
- https://pypi.org/project/btrack-beat-tracker/
