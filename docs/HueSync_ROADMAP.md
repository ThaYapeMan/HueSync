# HueSync — work order

Agreed sequence. Each step is a checkpoint: test before moving on, so a
disappointing result can be traced to one change rather than three.

---

## 1. PCM tap — the foundation

**Spec:** `docs/HueSync_pcm_tap_spec.md`

Read squeezelite's shared memory directly and run our own STFT (2048 window,
10 ms hop, 100 Hz) instead of consuming cava's reduced bars for onset detection.
cava keeps running and keeps driving colour.

**Why first:** the onset methods in step 2 are designed for 100 Hz and raw FFT
bins. On cava's 30 Hz smoothed 24-60 bars they cannot perform as intended, so
evaluating them on that input tells you nothing about the methods themselves.

**Substeps:**
1. `SqueezeliteShmSource` — header parsing, circular buffer, wraparound
2. STFT layer producing magnitude frames at 100 Hz
3. Point the existing `combined` detector at it and verify onsets still land
   where expected

**Checkpoint:** if `combined` behaves *worse* on the new input than on cava's
bars, something is wrong in the plumbing. Find that before stacking two more
methods on top.

---

## 2. Onset methods — multiband and SuperFlux

**Spec:** `docs/HueSync_onset_modes_prompt.md`

Two extra options next to the existing `combined`, selectable per profile via
`onset_method`. Dixon's three-condition peak-picking stays in all three; only
the detection function differs.

- **`multiband`** — flux per band (bass/mid/treble), each with its own state.
  Gives `onset_bass` / `onset_mid` / `onset_treble` separately, which the four
  Light DJ effects need: a kick drives Pulses, a hi-hat drives Flashes.
- **`superflux`** — Böck's maximum filter over neighbouring bands, which stops
  vibrato from registering as a stream of onsets.

Per-band onset indicators in the Now Playing spectrum, so the distinction can be
judged before effects are built on it.

**Checkpoint:** does multiband actually separate a kick from a hi-hat by ear?
If not, no point building four effects that depend on the difference.

---

## 3. Two-layer mixer

**Spec:** `docs/EFFECT_ENGINE.md`

A mellow layer and an active layer running simultaneously, crossfaded on energy.
This is the structural answer to "it feels monotonous" — the music decides which
*kind of behaviour* is visible, rather than one fixed mode running throughout.

Start with **one** mellow and **one** active layer. That is the smallest version
in which the concept proves itself. The remaining effects are variations on
something that demonstrably works — or a warning not to build them.

**Do not forget:** the mixer must **publish** the computed `mix` value, not keep
it internal. Step 4 depends on it, and it is three lines while building versus
an annoying retrofit afterwards.

---

## 4. Mood outputs — the Tuya projector

**Spec:** `docs/future/HueSync_mood_outputs_spec.md`

The projector accepts a few commands per second at best, so it reacts to **mood
changes, not frames**. Hue keeps doing the fast work.

- `Mood` (calm / moderate / energetic) derived from the mixer's `mix` value
- **Hysteresis** — separate thresholds for entering and leaving a state, so it
  does not flip back and forth around a boundary
- **Minimum interval** of 30-60 s, so a change reads as atmosphere rather than
  flickering
- `MoodOutput` protocol, fire-and-forget with a timeout — a slow or unplugged
  device must never stall the Hue path
- `TuyaOutput` via tinytuya (local) or Home Assistant

**Before building any of this:** try the projector's own built-in music mode
first. It listens through a microphone, so it is inherently in sync with what
the room hears — the exact problem the Hue path struggles with. If that looks
good enough at a party, this whole step is optional.

---

## 7. Player-per-area data model — Phase 2 (future)

*Design completed; implementation deliberately deferred.*

Split the monolithic `Profile` into three entities: `Player` (squeezelite /
LMS registration), `LightProvider` (Hue Entertainment Area or future output
target), and `Preset` (the analysis + rendering config per player→provider
coupling).

**Phase 1 is already done** (`PATCH /api/profiles/{id}` routing by field
category — see `docs/HueSync_analysis_and_player_architecture.md`).  The field
boundaries established there (`_PLAYER_FIELDS`, `_CAVA_FIELDS`, `_PCM_FIELDS`,
`_RENDER_FIELDS`) are reused verbatim at the entity level in Phase 2, so Phase
1 is not throwaway work.

**When to implement Phase 2:** when a second output driver (WLED, Nanoleaf) is
added, or when a user needs two LMS zones each driving their own Hue area from
a single HueSync instance.  Until then the current flat Profile + Phase 1
routing is sufficient.

---

## Later, deliberately parked

- **Spatial effects** (Groove Wave, Fireworks) — light positions are captured
  but unused; every light still gets the same colour. Likely the largest visual
  gain for the smallest change, but it needs the effects from step 3 first.
- **Palettes** — curated colour sets that effects index into, rather than
  computing an arbitrary RGB from the spectrum.
- **`set_param` over the WebSocket** — live parameter changes without saving.
  Needed to tune the effects by ear once there is more than one to tune.
- **Chroma-based colour** (`docs/future/`) — pitch class to hue via the circle
  of fifths, with vector summation giving consonance as saturation. The most
  musically principled option, and the one requiring the most work.
- **WLED driver** — the real test of whether the `Scene`/`Output` abstraction
  generalises. Hardware is already on hand for the ambilight project.
- **Continuous latency measurement** (`docs/HueSync_latency_measurement_v2.md`)
  — UPnP `GetPositionInfo` polling for Sonos, whose buffering varies with WiFi
  conditions. Fixed per-player values already cover AirPlay and local players.

---

## Standing constraint

One Entertainment Area can stream per Hue Bridge at a time — including on the
Bridge Pro. Multiple rooms reacting simultaneously requires multiple bridges.
