# HueSync — project context for Claude Code

## Conventions

- All code comments, docstrings, commit messages and documentation are written
  in **English**. Conversational replies to the user may be in Dutch.
- No AI attribution in commit messages (`includeCoAuthoredBy: false`).
- Always run `ruff check .` and the tests yourself before reporting work as
  finished.

## Documentation layout

| Path | Meaning |
|---|---|
| `docs/` | Current, actionable specs. Work from these. |
| `docs/future/` | Parked ideas. **Do not read or implement unless explicitly asked** — they cost context and are not current. |
| `docs/archive/` | Superseded specs, kept only so the reasoning behind past decisions is recoverable. |

`docs/EFFECT_ENGINE.md` is the consolidated, leading spec for the effect engine
work.

---

## What this project does

HueSync makes Philips Hue Entertainment lights react to music playing through
Lyrion Music Server (LMS).

```
LMS  →  squeezelite -v (virtual player)  →  POSIX shared memory (raw PCM)
     →  cava (spectrum analysis)         →  FIFO (named pipe)
     →  Analyser → Effect → Scene        →  Output driver
     →  Hue Entertainment API (DTLS/UDP) →  bridge → Entertainment Area
```

- **squeezelite** registers as a virtual player in LMS, decodes the stream to
  PCM, and (with `-v`) exposes it at `/dev/shm/squeezelite-<mac>`.
- **cava** reads that segment (`method = shmem`), computes a bar spectrum, and
  writes raw 8-bit frames to a FIFO.
- **HueSync** reads the FIFO, normalises, maps to colour, and streams to the
  bridge at a fixed 30 Hz.

---

## Architecture — the layer boundaries matter

The code was deliberately restructured so that effects know nothing about Hue,
and the analysis backend can be swapped. **Do not collapse these layers.**

All protocol types live in `types.py`:

| Type | Responsibility |
|---|---|
| `Colour` | RGB floats 0.0–1.0. Converted to protocol format *in the driver only*. |
| `Position` | A point in normalised room space (−1..1 per axis). |
| `Scene` | Colour as a function of position: `color_at(position, t)`. |
| `Effect` | Holds state, renders a `Scene` per frame from `AudioFeatures`. |
| `Analyser` | Produces `AudioFeatures`. Swappable backend. |
| `Output` | Samples a `Scene` at its own lights' positions and speaks its protocol. |

**Hard rule: `LightColorCommand` must not appear anywhere outside
`hue_output.py`.** Same principle applies to any future driver — WLED and
Nanoleaf specifics stay inside their own modules.

Effects return a `Scene` rather than a pixel array precisely because outputs
have different geometry: Hue lights sit at 3D positions, a WLED strip is
linear, Nanoleaf panels are 2D. Each driver samples the same Scene at its own
coordinates.

---

## Non-obvious design decisions — do not "clean up"

Each of these fixed a real, hard-to-diagnose bug. Removing them reintroduces it.

### snd-dummy instead of ALSA null (`player_manager.py`)

squeezelite's output goes to `hw:CARD=Dummy,DEV=0` (the `snd-dummy` kernel
module), **not** ALSA's `null` plugin. `null` has no clock, so squeezelite
decodes as fast as the CPU allows: ~100 % of a core, *and* LMS (single-threaded
Perl) stutters for every other player because it is being hammered with stream
requests. snd-dummy is a real timer-driven card — squeezelite paces at playback
speed, ~0.2 % CPU.

Requires `snd-dummy` loaded on the **Proxmox host** and the `/dev/snd` nodes
passed into the container. This is a host-level dependency: a host rebuild
without it silently breaks HueSync.

### FIFO reader starts before cava (`player_manager.activate_coupling()`)

A FIFO writer with no reader dies with SIGPIPE. Activation order is therefore:

1. `_create_fifo()` — create the FIFO
2. `engine.start()` — open the read end
3. `_start_cava()` — spawn the writer

Starting cava earlier meant it died during the 8-second Hue DTLS handshake,
silently, every time.

### Wait for squeezelite's SHM segment (`_wait_for_shm()`)

squeezelite creates `/dev/shm/squeezelite-<mac>` a moment *after* starting.
cava opens that path once and exits with "Could not open source" if it is not
there. Every new VirtualPlayer has a fresh MAC, so it never was — existing
players only appeared to work because an earlier run had left a segment behind.

Polls at 0.1 s, 10 s timeout, before cava is spawned.

### 30 Hz sender cap (`sync_engine.py`, `SEND_INTERVAL_S`)

Hue accepts ~50 updates/s; cava produces frames far faster. Queueing every
frame floods the event loop and starves the sender, and with zero sends for
10 s the bridge's own idle timeout closes the stream — which looks like a
crash.

`FifoReader` therefore keeps only the **latest** frame in a lock-protected
slot; the sender polls it at a fixed rate. Old frames are superseded, never
queued.

### cava stderr kept, not discarded (`player_manager.py`)

cava's stderr goes to `<coupling-id>.cava.log`, not `/dev/null`. Discarding it
made a silent `<defunct>` cava process very hard to diagnose. Teardown
deliberately leaves the log in place.

### Two clipping ceilings, and how they interact (`sync_engine.py`)

1. **`BandNormaliser.exertion_clip` (default 3.0×)** — a *musical scale choice*.
   Exertion is clipped at `exertion_clip`× the bar's rolling average and
   encoded as 0–255. With the default 3.0, steady-state music (exertion ≈ 1×)
   maps to byte ≈ 85 (≈ 33 % output). `BandNormaliser.update_exertion_clip()`
   changes the ratio without resetting the EMA.
2. **The clip at 1.0 before RGB conversion** — a *safety ceiling*, applied
   after multiplying by the active session's sensitivity setting.

Post-normalisation, `sensitivity = 1.0` is the correct default: steady-state
music sits around one-third brightness with brief peaks at full. Above ~3.0
the steady state saturates.

### Coupling PATCH routes to the minimum necessary action (`api.py`, `player_manager.py`)

`PATCH /api/couplings/{id}` categorises changed fields before deciding what to
restart. **Do not collapse these categories back into "always deactivate"** —
that was the original source of warmup-state interference in A/B comparisons.

| Category | Fields | Action |
|---|---|---|
| deactivate | `player_id`, `light_provider_id`, `lms_host`, `lms_port`, `player_name`, `alsa_device` | Full deactivate |
| live FK | `analysis_config_id` | `restart_cava()` + `update_onset_pipeline()` |
| live FK | `render_config_id` | `update_render()` only |
| cava | `bars`, `lower_cutoff_freq`, `higher_cutoff_freq` | `restart_cava()` |
| pcm | `onset_method`, `onset_delta`, `onset_alpha`, `superflux_mu`, `superflux_lag`, `bass_hz`, `mid_hz` | `SyncEngine.update_onset_pipeline()` |
| render | `color_mode`, `sensitivity`, `brightness_floor`, `exertion_clip`, `onset_flash_intensity`, `enabled`, `entertainment_area_name`, `light_count` | `SyncEngine.update_render()` |

Priority: deactivate > cava > pcm > render. `update_onset_pipeline()` resets
onset warmup state but NOT the BandNormaliser EMA.

### Swapping AnalysisConfig on an active Coupling is a live operation (`api.py`, `player_manager.py`)

`PATCH /api/couplings/{id}` with `analysis_config_id` changed to a different AC
does **not** require a full session restart. It is intentionally live-updateable:

1. `_apply_coupling_action()` routes `analysis_config_id` to `restart_cava()` +
   `update_onset_pipeline()` — the same path as changing `bars` or `onset_method`
   inline. The Hue DTLS session and squeezelite stay running.
2. `restart_cava()` reloads `session.coupling` from storage before calling
   `_build_engine_profile()` — so the new `analysis_config_id` (already
   written to storage by `patch_coupling()`) is picked up, not the stale
   in-memory reference.

**Do not move `analysis_config_id` back into `_C_DEACTIVATE_FIELDS`.** It was
there previously and caused lights to freeze (deactivate called, no re-activate).
The fix is tested by `test_ac_swap_session_remains_active` in `tests/test_api.py`.

Similarly, `render_config_id` routes to `update_render()` only (no cava restart,
no deactivate).

### Timing: lights run AHEAD of Sonos

`sonos-squeezebox` throttles its encoder to ~2 seconds of lookahead and Sonos
buffers further on top. HueSync's virtual player has no such delay, so the
lights lead the audio in Sonos rooms.

The latency setting therefore has to **delay** frames, not render ahead — the
opposite of what Music Assistant's plugin does. Implemented as a ring buffer in
the output layer.

---

## Deployment

Runtime is a dedicated **LXC container** (Debian 13) on a Proxmox host. There
is no Python runtime, venv, pytest or pip on the development machine, and
**Claude Code has no SSH access to the container** — deployment is manual:

```bash
# on the LXC, as root
cd /opt/huesync
git pull && .venv/bin/pip install . && systemctl restart huesync
```

Runs as user `huesync` under systemd (`systemd/huesync.service`). Config at
`/etc/huesync/config.json` (override with `HUESYNC_CONFIG`).

Note: `git config --global --add safe.directory /opt/huesync` was needed once,
because the directory is owned by `huesync` while git runs as root.

---

## Key files

| File | Role |
|---|---|
| `src/huesync/types.py` | Protocol types: `Colour`, `Position`, `Scene`, `Effect`, `Analyser`, `Output`, `AudioFeatures` |
| `src/huesync/sync_engine.py` | `FifoReader`, `BandNormaliser`, `CavaAnalyser`, `ColourModeEffect`, `SyncEngine` |
| `src/huesync/hue_output.py` | **Only** file importing `hue_entertainment` for streaming: `HueDriver`, `ChannelInfo`, `get_channel_infos()` |
| `src/huesync/hue_bridge.py` | Controller pairing and Entertainment Area discovery |
| `src/huesync/player_manager.py` | Process lifecycle: squeezelite + cava + output driver; `activate_coupling()`, `update_onset_pipeline()`, `update_render()` |
| `src/huesync/models.py` | Five-entity model: `Controller`, `VirtualPlayer`, `LightProvider`, `AnalysisConfig`, `RenderConfig`, `Coupling`; also `Profile` and `BridgeConfig` as internal engine types |
| `src/huesync/lms_discovery.py` | UDP broadcast discovery of the LMS server |
| `src/huesync/app.py` | FastAPI web UI |
| `src/huesync/storage.py` | JSON config persistence |
| `tests/` | Unit tests — keep green |

---

## Known constraints

- **One Entertainment Area can stream per bridge at a time.** A hard Hue Bridge
  limitation, including on the Bridge Pro. Enforce it in the Hue driver, not in
  shared code — other outputs have no such limit.
- **LMS discovery is UDP broadcast** and does not cross subnets. LXC 112 sits
  on `vmbr1` rather than `vmbr0`; harmless on a flat network, relevant once
  VLANs exist.
- **VirtualPlayer edits must preserve `player_mac`.** A new MAC means a new player
  in LMS and a new shared-memory segment, so the user has to re-sync. The MAC is
  auto-generated on first activation and stored on the VirtualPlayer entity.
