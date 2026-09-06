# HueSync — analysis and player architecture

*Documents the session lifecycle, field-update routing, and the phased
roadmap toward a full Player/LightProvider/Preset data model.*

---

## Current state: Profile as single entity

A `Profile` in `models.py` combines three conceptually distinct concerns:

| Concern | Fields |
|---|---|
| **Player** (squeezelite/LMS) | `lms_host`, `lms_port`, `player_name`, `player_mac`, `alsa_device` |
| **Light target** (Hue area) | `bridge_id`, `entertainment_area_id`, `entertainment_area_name`, `light_count` |
| **Rendering** (analysis + colour) | `color_mode`, `sensitivity`, `brightness_floor`, `bars`, frequency cutoffs, band boundaries, `onset_method`, onset params, `exertion_clip`, `enabled` |

This is a deliberate simplification for the current one-player, one-bridge-area
use case.  The rendering fields can be updated live without touching the audio or
lighting infrastructure; the player and light-target fields require a partial or
full restart.

---

## Phase 1 — layered restart logic (implemented)

`PATCH /api/profiles/{id}` in `api.py` categorises changed fields and applies
the minimum necessary action on the active session:

```
_PLAYER_FIELDS   → full deactivate + manual reactivate
_CAVA_FIELDS     → restart_cava() only (squeezelite + Hue DTLS stay up)
_PCM_FIELDS      → update_onset_pipeline() live (no process restart)
_RENDER_FIELDS   → update_render() live (no process restart)
metadata (name)  → save to storage; no session action
```

Priority: if multiple categories change in one PATCH, the most disruptive
action runs.  `_PLAYER_FIELDS` always wins; within the other three, all
applicable non-deactivating actions execute (e.g. cava restart + PCM rebuild
when both `bars` and `onset_method` change simultaneously).

### New SyncEngine methods

**`update_onset_pipeline(profile)`** — tears down `_pcm_onset` /
`_pcm_multiband` / `_pcm_superflux` and rebuilds the correct one from
`profile.onset_method`, leaving squeezelite, cava, and the Hue DTLS session
untouched.

Side effect: onset warmup state resets (about 30 STFT frames ≈ 0.3 s at
100 Hz).  `BandNormaliser` EMA is NOT reset — the dynamic baseline carries
over.  When measuring A/B differences between onset methods, allow at least
5 s after switching before comparing `bars_mean` values.

**`update_render(profile)`** — replaces `ColourModeEffect` with a new
instance from the updated profile and calls
`BandNormaliser.update_exertion_clip(profile.exertion_clip)` so the clip
ratio takes effect without rebuilding the EMA.

### Why Phase 1 is not throwaway work

The field boundaries established here (`_PLAYER_FIELDS`, `_CAVA_FIELDS`,
`_PCM_FIELDS`, `_RENDER_FIELDS`) are exactly the same lines that will exist at
the data-model level in Phase 2.  In Phase 2, player fields live on a `Player`
entity, rendering fields live on a `Preset` entity, and cava/PCM fields become
part of the preset's analysis configuration.  The restart decision logic is the
same; only the entity that holds the fields changes.

---

## Phase 2 — Player / LightProvider / Preset split (future)

*Not yet implemented.  Design is recorded here for continuity.*

### Entities

```
Player
  id, name
  lms_host, lms_port, player_name, player_mac, alsa_device

LightProvider
  id, bridge_id, entertainment_area_id, entertainment_area_name, light_count

Preset   (= the player → light-provider coupling)
  id, name
  player_id, light_provider_id
  [all rendering fields from current Profile]
```

### Key decisions from the design session

- **Multiple players** are supported by the model (e.g. two LMS zones, each
  with its own squeezelite instance and Hue area).
- **One cava process per Preset** — no sharing, even when settings are
  identical.  cava overhead is negligible; deduplication logic would create
  coupling and complicate lifecycle management.
- **Bridge constraint becomes per-bridge**, not global: two Presets on the same
  bridge cannot both be active simultaneously; two Presets on different bridges
  can.
- **Migration** from the current flat Profile is automatic: group by
  `player_mac` to create Player entities, extract `(bridge_id, area_id)` pairs
  as LightProvider entities, keep the remaining rendering fields as Presets.

### Trigger for Phase 2

Phase 2 becomes necessary when any of the following is true:
- A user wants two Hue areas reacting to two independent LMS zones.
- A WLED or Nanoleaf output driver is added (each needs its own LightProvider
  record alongside the existing Hue entries).
- The UI needs to show "which squeezelite instances are running" separately
  from "which rendering presets are active".

Until then, the current Profile model with Phase 1 routing is sufficient.

---

## Session lifecycle (current)

```
activate(profile)
  ├── _start_squeezelite()   → /dev/shm/squeezelite-<mac>
  ├── _create_fifo()
  ├── engine.start()         → FifoReader thread opens FIFO read end
  ├── _start_cava()          → writes to FIFO; waits for SHM segment
  ├── shm_source.open()      → SqueezeliteShmSource maps SHM
  ├── engine.attach_shm_source()  → builds PCM pipeline from onset_method
  ├── HueDriver.start()      → DTLS handshake (8+ s)
  └── engine.run(hue_driver) → 30 Hz render loop

deactivate()
  ├── cancel engine.run() task
  ├── HueDriver.stop()
  ├── squeezelite.terminate()
  ├── cava.terminate()
  ├── shm_source.close()
  └── /dev/shm/squeezelite-<mac> unlinked

restart_cava()   [cava fields only]
  ├── reload profile from storage
  ├── engine.update_profile(profile)   [ColourModeEffect rebuild]
  ├── cava.terminate()
  └── _start_cava()   [fresh cava process; squeezelite + Hue stay up]

update_onset_pipeline(profile)   [PCM fields only]
  ├── null out _pcm_onset / _pcm_multiband / _pcm_superflux
  ├── reset onset state flags
  └── rebuild correct pipeline from profile.onset_method

update_render(profile)   [render fields only]
  ├── ColourModeEffect replaced
  └── BandNormaliser.exertion_clip updated (EMA preserved)
```

### Startup order rationale

The FIFO reader must open its read end **before** cava starts writing.  cava
is a FIFO writer; a writer with no reader receives SIGPIPE and exits silently.
During the DTLS handshake (8+ seconds) cava runs and BandNormaliser's EMA
warms up on real audio, so the first rendered frame is not artificially dark.

The SHM wait (`_wait_for_shm()`) polls at 0.1 s with a 10 s timeout because
squeezelite creates `/dev/shm/squeezelite-<mac>` a moment *after* starting.
cava reads the SHM path once on startup and exits if it is missing; every new
profile has a fresh MAC, so the segment is never pre-existing.
