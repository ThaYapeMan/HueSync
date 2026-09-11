# HueSync

Spectrum-reactive Philips Hue Entertainment lighting that follows your music in real time.

Connect HueSync to your audio source — [Lyrion Music Server](https://lyrion.org/) (LMS)
or AirPlay 2 — and your Hue Entertainment lights react live to the music's spectrum and
dynamics. No pre-computed BPM tags, no extra microphone hardware.

AirPlay always analyses audio through `PcmAudioPipeline`, built on the `PcmSource`
interface. LMS defaults to an external cava binary (which bypasses `PcmSource`), but
can switch per-Analyser to the same pipeline via `bars_source = "pcm_pipeline"` — both
paths confirm the interface in production. A new source type (Roon, JACK, …) needs only
a `PcmSource` adapter; the analysis and rendering layers stay unchanged.

## How it works

```
── LMS path (squeezelite) ────────────────────────────────────────────────────
LMS (audio orchestration)
   │  slimproto
squeezelite -v  →  /dev/shm/squeezelite-<mac>   ← virtual player; snd-dummy for pacing
   │
   ├─ bars_source = "cava" (default):
   │    ├─ cava (shmem input → FIFO)     ←  FFT + log-spaced spectrum bars, ~30 Hz
   │    │      │  30-bar spectrum frames
   │    │  BandNormaliser (per-band AGC)
   │    │      │  normalised bars
   │    └─ PcmStft (100 Hz STFT tap)    ←  onset detection + optional HPSS
   │             │  magnitude frames per 10 ms hop
   │             ├─ MultibandStftPipeline / SuperfluxStftPipeline / StftOnsetPipeline
   │             │        │  onset flags (bass / mid / treble)
   │             └─ PcmHpss (when use_hpss_separation = true)
   │                      │  percussive_energy, harmonic_energy per frame
   │
   └─ bars_source = "pcm_pipeline":
        PcmAudioPipeline (SHM source, ~100 Hz STFT; same pipeline as AirPlay)
             │  bars + onset flags → AudioFeatures

── AirPlay path (shairport-sync) ─────────────────────────────────────────────
shairport-sync  →  /run/huesync/airplay.pcm  (named pipe)
   │
AirPlayPipeSource  →  PcmAudioPipeline (~100 Hz STFT, source-agnostic)
   │  bars + onset flags → AudioFeatures

── Rendering (both paths) ────────────────────────────────────────────────────
SyncEngine (30 Hz)
   │
   ├─ SustainedEnergyTracker  ←  raw PCM RMS, dual-timescale log-ratio
   │       │  τ_short ≈ 300 ms / τ_long ≈ 30 s, ±6 dB mapped to [0, 1]
   │       │  AudioFeatures.sustained_energy  (None when no PCM source)
   │
   ├─ AudioPipeline  →  AudioFeatures  →  LayerMixer
   │                                          │  EnergyProfile blend:
   │                                          │  sustained_energy drives crossfade
   │                                          │  (fallback: features.full when None)
   │                                          ↓
   └─────────────────────────────────────  Scene (Protocol)
                                              │
HueDriver  →  Hue Bridge  →  Entertainment Area
```

Three components worth understanding:

- **squeezelite's `-v` flag** exposes a live audio buffer in shared memory
  (`/dev/shm/squeezelite-<mac>`), originally for on-device spectrum displays
  (jivelite). HueSync uses it for both cava input and the PCM tap.
- **[cava](https://github.com/karlstav/cava)** has a `shmem` input module and
  a `raw` output mode for FIFOs. It handles the FFT and log-spaced bar
  computation — HueSync does not do its own DSP for colour.
- **[hue-entertainment](https://github.com/music-assistant/hue-entertainment)**
  handles the DTLS-PSK handshake and HueStream protocol.

### LMS integration: follow, not sync

HueSync does **not** join an LMS sync group. When a Coupling is activated,
HueSync's virtual player registers with LMS as a **standalone** player. After a
5-second startup delay `LmsFollower` connects to LMS's `listen 1` CLI
push-notification feed and mirrors every `playlist newsong` event from the
configured **Follow player** — issuing a `playlist play` command to HueSync's
own player. After each play command, HueSync immediately sends `sync -` to
both itself and the follow player to prevent any LMS plugin from automatically
re-creating a sync group.

**Why not join the sync group?** LMS drift correction keeps sync-group members
aligned by pausing or skipping frames. HueSync's virtual player has a much
shorter buffer than Sonos or AirPlay players, so LMS's correction frequently
hits HueSync — causing audible stutters on the real speakers in the room, not
just HueSync's silent virtual player. Running standalone eliminates this
interference entirely.

---

## Architecture — six-entity model

HueSync organises configuration as six types of entities. A **Coupling** links
one of each sub-entity and is the thing you activate.

```
Controller ──── Zone
                 │
VirtualPlayer ──── Coupling ──── EnergyProfile ──── Effect (high-energy)
     │                                           └── Effect (low-energy)
     └── Analyser
```

| Entity | Responsibility |
|---|---|
| **Controller** | A Hue Bridge: IP, app key, client key. Used for pairing and Entertainment Area discovery. |
| **Virtual Player** | A virtual squeezelite instance: LMS host, player name, MAC address, ALSA device, follow player MAC. |
| **Zone** | Links a Controller to one of its Entertainment Areas. Shared across Couplings. |
| **Analyser** | cava parameters (bars, cutoff freqs), onset detection settings (method, delta, alpha), and optional harmonic/percussive separation (`use_hpss_separation`). |
| **Effect** | Visual output parameters: effect type, sensitivity, brightness floor, band boundaries. |
| **EnergyProfile** | An Energy Profile automatically blends between a low-energy and high-energy Effect according to music energy, using configurable blend thresholds and EMA response. |
| **Coupling** | Binds exactly one Virtual Player + Zone + Analyser + EnergyProfile. Activate a Coupling to start the light show. |

**Why "Zone" and not "Group"?** An LMS sync group is also called a "group".
Using "Zone" for the Hue Entertainment Area avoids confusion — a Zone is a
physical room definition, a sync group is an audio routing concept.

A Coupling can be **cloned** (Clone button on the Couplings page) — the new
Coupling is a **shallow copy**: it shares the same Virtual Player, Zone,
Analyser, and EnergyProfile references as the original. No child entities are
duplicated. To build a genuinely independent A/B pair:

1. Clone the Coupling (Clone button on the Couplings page).
2. Clone the specific Analyser you want to vary (Clone button on the Analysers
   page). This creates an independent copy with its own row in the list.
   The API also supports cloning Effects and EnergyProfiles individually
   (`POST /api/effects/{id}/clone`, `POST /api/energy-profiles/{id}/clone`),
   though the UI does not expose those buttons yet.
3. In the cloned Coupling editor, point the Analyser (or EnergyProfile)
   dropdown at the newly cloned entity.

### Relationship rules

- One Virtual Player → many Couplings (same squeezelite process, different analysis
  or visual settings).
- One Controller → many Zones (one per Entertainment Area).
- Analysers, Effects, and Energy Profiles can be shared across Couplings — or kept
  exclusive per Coupling (as created by Clone).
- **One Entertainment Area can stream per bridge at a time** (Hue Bridge
  hardware limit). Activating a Coupling automatically stops whatever was running before.

---

## Effects catalogue

The **effect_type** field on an Effect controls how the audio spectrum maps to light
colour and motion.

| Effect | Style | Description |
|---|---|---|
| `spectrum_rgb` | Spectral colour | Bass → red, mid → green, treble → blue. Hue and brightness track the live spectrum in real time. |
| `spectrum_rgb_spatial` | Spatial spectral colour | Same band mapping as `spectrum_rgb`, but spread across the entertainment area: bass on the left, mid in the centre, treble on the right, cross-faded between neighbours instead of blended into one colour. |
| `mono_pulse` | Intensity-based | Single hue (configurable); overall brightness follows loudness. Clean look for uniform rooms. |
| `pulses` | Transient bursts | Short brightness pulses radiate outward from onset triggers. Fast and punchy. |
| `flashes` | Onset-driven | Hard flash on beat/onset with a brief cooldown; very dark between hits. High contrast. |
| `splotches` | Spatial colour | Random-hue blobs appear on onset and fade. Colourful and organic. |
| `fireworks` | Spatial burst | Particles burst outward from onset points and fade, like fireworks. |
| `swirl` | Rotating hue | Hue rotates continuously across the room; speed driven by spectral energy. |
| `wave` | Travelling wave | Brightness wave travels across the entertainment area; hue drifts with spectral centroid. |
| `solid` | Static | Fixed colour, no motion. Useful as a mellow-layer fallback during quiet passages. |
| `none` | Off | Lights off / no output. |

---

## Onset detection

HueSync runs up to two parallel onset-detection paths:

| Path | Rate | Source |
|---|---|---|
| cava-based (`OnsetDetector` in `CavaPipeline`) | ~30 Hz | Normalised bar spectrum |
| PCM tap (`StftOnsetPipeline` / `MultibandStftPipeline` / `SuperfluxStftPipeline`) | 100 Hz | 2048-sample Hamming-windowed STFT on raw PCM from shared memory |

The **`onset_method`** field in an Analyser selects the PCM-tap algorithm:

| Method | Behaviour |
|---|---|
| `combined` | StftOnsetPipeline on full-spectrum STFT flux. Runs as a parallel comparison; the cava-based onset still drives the `onset` flag seen by the UI. |
| `multiband` | Three independent `OnsetDetector` instances on bass / mid / treble STFT slices. Overrides `features.onset` and sets separate `onset_bass` / `onset_mid` / `onset_treble` flags for the UI preview. |
| `superflux` | Böck & Widmer (2013) max-filter vibrato suppression before spectral flux, then Dixon peak-picking. Overrides `features.onset`. Reduces false positives on vibrato-heavy material. |

Switching `onset_method` live rebuilds the PCM pipeline in place — no process restart,
BandNormaliser EMA preserved.

---

## Harmonic/percussive separation (HPSS)

Setting `use_hpss_separation: true` on an Analyser activates `PcmHpss`
as an additional layer on top of the STFT. It applies a 2D median filter
(Fitzgerald 2010) to the rolling magnitude spectrogram and produces two
per-frame energy values:

- **`percussive_energy`** (0.0–1.0): proportion of the frame dominated by
  transients — kick drums, snares, rhythmic hits.
- **`harmonic_energy`** (0.0–1.0): proportion dominated by tonal content —
  vocals, chords, sustained notes.

Both sum to 1.0 via Wiener soft masks.

**Effect routing when HPSS is active:**

| Effect type | HPSS source |
|---|---|
| Transient effects: Pulses, Flashes, Fireworks | Scale with `percussive_energy` |
| Tonal effects: Swirl, Wave, Solid | Scale with `harmonic_energy` |
| Spectral / intensity effects: Spectrum RGB, Mono Pulse | Unaffected (no HPSS coupling) |

**Implementation:** pure numpy — no librosa or scipy dependency. Time-axis
median window: 17 frames (~170 ms at 100 Hz). Frequency-axis median window:
31 bins. Each window is the minimum that cleanly separates kick drums from
bass guitar in practice.

**CPU cost (measured on the production LXC):** ~405 µs per frame — 25× headroom
against the 10 ms per-frame budget. Suitable for always-on use in production.

---

## Hardware/software requirements

- A Hue Bridge (V2 "square" or Pro) with at least one
  [Entertainment Area](https://www.philips-hue.com/en-us/explore-hue/propositions/entertainment)
  configured in the Hue app.
- `squeezelite` and `cava` installed and on `PATH`.
- Python 3.11+.
- A Lyrion Music Server instance on the same network.
- **The `snd-dummy` kernel module loaded** — see below.

### Why snd-dummy is required

ALSA's `null` plugin discards samples immediately with no hardware clock, so
squeezelite decodes as fast as the CPU allows — ~100 % of a core, and LMS
stutters for every other player. `snd-dummy` is a real, timer-driven ALSA
card; squeezelite paces at actual playback speed (~0.2 % CPU).

On a bare-metal host or VM:

```bash
modprobe snd-dummy
echo "snd-dummy" > /etc/modules-load.d/snd-dummy.conf   # persist across reboots
```

In an **LXC container** the module must be loaded on the *host* (containers
share the host kernel), then the device nodes passed in. On Proxmox:

```bash
# On the host:
modprobe snd-dummy
echo "snd-dummy" > /etc/modules-load.d/snd-dummy.conf
cat /proc/asound/cards          # note the Dummy card's number, e.g. 1
ls -la /dev/snd/                # find controlCN and pcmCND0p for that number

pct set <CTID> -dev0 /dev/snd/controlC1,gid=29
pct set <CTID> -dev1 /dev/snd/pcmC1D0p,gid=29
pct reboot <CTID>
```

Verify inside the container with `squeezelite -l` — the Dummy card should appear.

---

## Installation

```bash
apt install -y squeezelite cava python3-venv   # Debian/Ubuntu
git clone https://github.com/ThaYapeMan/HueSync.git
cd HueSync
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

`pip install .` embeds the current git commit hash into the package so
`GET /api/status` reports the exact build running on the target.

Run it:

```bash
huesync
```

The web UI and API listen on `http://<host>:8420`. Config is stored as JSON at
`/etc/huesync/config.json` (override with `HUESYNC_CONFIG`).

### Running as a service

See [`systemd/huesync.service`](systemd/huesync.service). Copy to
`/etc/systemd/system/`, then:

```bash
systemctl daemon-reload
systemctl enable --now huesync
```

### AirPlay 2 support (optional)

To accept AirPlay 2 audio as an input source, build shairport-sync and nqptp
from source on the LXC. Run once after the initial setup:

```bash
sudo bash scripts/setup-airplay.sh
```

This compiles shairport-sync with `--with-airplay-2`, installs nqptp (precision
timing daemon), configures a pipe output at `/run/huesync/airplay.pcm`, and
registers both as systemd services running as the `huesync` user. Re-running is
safe — existing source trees are updated via `git pull`, not re-cloned.

After the script completes, verify in iOS Control Center that "HueSync" appears
alongside your Sonos speakers and can be selected simultaneously (AirPlay 2
multi-room co-existence). HueSync reads the raw PCM stream silently — no audio
plays from the LXC.

### Deployment updates

```bash
# On the LXC / target machine, as root:
cd /opt/huesync
git pull && .venv/bin/pip install . && systemctl restart huesync
```

`pip install .` is required (not just `systemctl restart`) because the web UI
bundle is embedded in the Python package — a restart without reinstalling
serves the old bundle.

---

## Usage

### Quick start

1. **Pair a Controller**: go to *Controllers*, press the physical link button
   on the bridge, submit *Pair* within ~30 s.
2. **Create a Zone**: go to *Zones*, select the Controller and Entertainment Area.
3. **Create a Virtual Player**: go to *Virtual Players* → *New virtual player*.
   Enter the LMS host (use *Discover*), a player name, and set **Follow player**
   to the real LMS player you listen on (use *Discover* to see available players).
4. **Create a Coupling**: go to *Couplings* → *New coupling*. Link the Virtual
   Player, Zone, and an Energy Profile (which references your Effects). Defaults work
   out of the box.
5. Press **Go** (▶) on the Coupling. HueSync registers the virtual player with
   LMS as a standalone player and starts following your real player via LMS's
   push-notification feed — no sync group, no drift correction interference.
6. Play music. The lights react to the live spectrum.

The **Now Playing** tab shows the live preview and lets you activate/stop
Couplings directly without switching tabs.

### Configuration UI

> **Work in progress.** The configuration pages are being progressively rebuilt
> from modal dialogs into full-page desktop workspaces. The following pages have
> already been redesigned:
>
> - **Couplings** — signal-flow routing graph; live/blind edit badges.
> - **Analysers** — two-column workspace with Standard / Expert progressive disclosure.
> - **Effects** — card gallery + per-effect workspace editor with Standard / Expert mode.
> - **Energy Profiles** — EnergyBlendEditor workspace with animated blend preview.
>
> The following pages still use the earlier modal-dialog style and will be
> redesigned in a future iteration:
>
> - Virtual Players, Zones, Latency, Now Playing.

The rebuilt pages expose all fields with plain-language labels and in-place
descriptions — no prior knowledge of DSP terminology is required. Analyser and
Effect editors use a Standard / Expert toggle: Standard mode shows the handful
of settings that most affect perceived output; Expert mode reveals the full set
including exact numeric inputs, frequency-band boundaries, and exertion clip.
SuperFlux parameters are shown only when SuperFlux is the selected onset method.

### Live vs. Blind edits

Editing an active Coupling shows a **● Live** badge in the editor header.
HueSync applies the minimum necessary action depending on which fields changed:

| Changed field(s) | Action |
|---|---|
| `player_id`, `zone_id`, `lms_host`, `lms_port`, `player_name`, `alsa_device`, `follow_player_mac` | Full deactivate — squeezelite, cava, and the Hue DTLS session are torn down. Reactivate manually (blind: lights off briefly). |
| `analyser_id` | cava restart + PCM pipeline rebuild — new Analyser applied live, Hue session stays up. |
| `energy_profile_id` | Render update only — new EnergyProfile applied immediately, nothing restarts. |
| `bars`, `lower_cutoff_freq`, `higher_cutoff_freq` | cava-only restart — squeezelite and the Hue session stay up. |
| `onset_method`, `onset_delta`, `onset_alpha`, `superflux_mu`, `superflux_lag`, `bass_hz`, `mid_hz`, `use_hpss_separation` | PCM pipeline rebuilt live — no process restart, BandNormaliser EMA preserved. |
| `sensitivity`, `brightness_floor`, `exertion_clip`, `onset_flash_intensity` | Render update only — applied immediately, nothing restarts. |
| `name`, `enabled` | Metadata only — no session action. |

Only the most disruptive category in a given edit triggers an action.

Editing an **Effect** or **EnergyProfile** that is currently referenced by the
active Coupling also applies live — no Coupling restart needed.

### Key Analyser fields

| Field | Default | Notes |
|---|---|---|
| `bars` | 30 | Frequency bins cava analyses. Changing requires cava restart. |
| `lower_cutoff_freq` | 50 Hz | Low end of the analysed range. |
| `higher_cutoff_freq` | 12000 Hz | High end. Music has almost no energy above ~12 kHz. |
| `onset_method` | `combined` | `combined` / `multiband` / `superflux` — see Onset detection above. |
| `onset_delta` | 0.1 | Margin above local mean for Dixon peak-picking. Higher = fewer, more confident onsets. |
| `onset_alpha` | 0.9 | Per-frame decay of the suppression threshold. Higher = longer suppression window. |
| `superflux_mu` | 3 | SuperFlux only: max-filter half-width in FFT bins. |
| `superflux_lag` | 2 | SuperFlux only: compare with frame `lag` steps ago. |
| `use_hpss_separation` | `false` | Enable harmonic/percussive separation. See HPSS section. Rebuilds PCM pipeline live. |

### Key Effect fields

| Field | Default | Notes |
|---|---|---|
| `effect_type` | `spectrum_rgb` | Which effect algorithm renders to light. See effects catalogue. |
| `sensitivity` | 1.0 | Scales bar values after AGC normalisation. |
| `brightness_floor` | 0.15 | Minimum brightness; prevents lights going fully dark during quiet passages. |
| `exertion_clip` | 3.0 | Sets "maximally loud" in relative terms. A band at `exertion_clip`× its rolling average clips to full output. |
| `bass_hz` | 250 | Bass/mid boundary (Hz) for band splitting in the renderer. |
| `mid_hz` | 2000 | Mid/treble boundary (Hz). |

### Key EnergyProfile fields

| Field | Default | Notes |
|---|---|---|
| `high_energy_effect_id` | — | Effect used during loud passages. |
| `low_energy_effect_id` | — | Effect used during quiet passages. Empty = same as high-energy effect. |
| `blend_start` | 0.3 | `sustained_energy` below which the low-energy effect is used at full weight. `sustained_energy` is section-level loudness from `SustainedEnergyTracker` (dual-timescale PCM RMS log-ratio, ±6 dB → [0, 1]). Falls back to `features.full` (relative exertion) when no PCM source is attached. |
| `blend_end` | 0.7 | `sustained_energy` above which the high-energy effect is used at full weight. |
| `blend_response` | 0.1 | EMA smoothing coefficient — how quickly `LayerMixer` tracks the target blend position. Larger = faster crossfade. |

### Player latency

When HueSync's virtual player follows a Sonos or AirPlay room, audio arrives
at the speakers with a device-specific buffer delay (typically 1–2 s for
AirPlay, 2+ s for Sonos). HueSync's virtual player has no such delay, so the
lights lead the audio.

Add an entry in the *Player latency* section of the UI to delay HueSync's
frames and bring the lights into sync with the speakers:

- **Strategy → Fixed**: enter the known buffer delay in ms.
- **Strategy → None**: no delay compensation.

The entry takes effect immediately — no reactivation needed.

---

## JSON API

A REST API is available at `/api/*`. Interactive documentation (OpenAPI /
Swagger) is at **`/docs`**.

### Endpoints

```
POST             /api/controllers/pair           pair a Hue Bridge (press link button first)
GET/POST         /api/controllers
GET/PATCH/DELETE /api/controllers/{id}
GET              /api/controllers/{id}/areas      list Entertainment Areas

GET/POST         /api/virtual-players
GET/PATCH/DELETE /api/virtual-players/{id}

GET/POST         /api/zones
GET/PATCH/DELETE /api/zones/{id}

GET/POST         /api/analysers
GET/PATCH/DELETE /api/analysers/{id}

GET/POST         /api/effects
GET/PATCH/DELETE /api/effects/{id}
(GET/POST        /api/scenes             deprecated alias for /api/effects)
(GET/PATCH/DELETE /api/scenes/{id}       deprecated alias for /api/effects/{id})

GET/POST         /api/energy-profiles
GET/PATCH/DELETE /api/energy-profiles/{id}
(GET/POST        /api/crossfaders        deprecated alias for /api/energy-profiles)
(GET/PATCH/DELETE /api/crossfaders/{id}  deprecated alias for /api/energy-profiles/{id})

GET/POST         /api/couplings
GET/PATCH/DELETE /api/couplings/{id}
POST             /api/couplings/{id}/activate
POST             /api/couplings/{id}/clone        shallow clone — new Coupling shares all entity references with the original
POST             /api/couplings/{id}/restart-cava restart cava for the active coupling
POST             /api/couplings/deactivate
```

### Status and utilities

```
GET              /api/status        version, active coupling, effect, onset_method,
                                    delay, process status
GET              /api/player-latencies
POST/PATCH/DELETE /api/player-latencies/{mac}
GET              /api/lms/discover  broadcast-discover LMS on the local network
GET              /api/lms/players   list players registered with a given LMS host
                                    (?host=<ip>)
```

### WebSocket

The WebSocket at `/ws/preview` sends typed JSON messages at up to 20 Hz:

```jsonc
{ "type": "frame",
  "colour": {"r": 0, "g": 0, "b": 0},   // 16-bit (0–65535)
  "onset": false,
  "onset_bass": false, "onset_mid": false, "onset_treble": false,
  "pcm_onset": false }

{ "type": "spectrum", "bars": [0.1, 0.4, …] }   // normalised 0.0–1.0

{ "type": "status",
  "version": "0.2.0+abc1234",
  "active_coupling_id": "…",
  "active_coupling_name": "Zitkamer",
  "color_mode": "spectrum_rgb",
  "onset_method": "combined",
  "sync_master": "aa:bb:…",
  "sync_master_name": "SONOS::Study",
  "bridge_connected": true,
  "processes": {"squeezelite": true, "cava": true},
  … }   // sent only when values change
```

---

## Architecture — effect pipeline

```
AudioPipeline  →  AudioFeatures  →  Effect  →  Scene (Protocol)  →  Output
```

- **AudioPipeline** (`CavaPipeline` or `PcmAudioPipeline`): produces `AudioFeatures`
  — normalised spectrum bars, onset flags, and (when HPSS is active)
  `percussive_energy` / `harmonic_energy`. `CavaPipeline` reads the cava FIFO;
  `PcmAudioPipeline` runs its own STFT directly on a PCM source (squeezelite SHM
  or AirPlay pipe) and is fully source-agnostic. `AudioFeatures` also carries
  two additional energy signals populated by `SyncEngine` each tick:
  - **`sustained_energy`** (`float | None`): section-level loudness from
    `SustainedEnergyTracker` — dual-timescale log-ratio of short-term EMA
    (τ ≈ 300 ms) to long-term EMA (τ ≈ 30 s) of PCM RMS, ±6 dB → [0, 1].
    `None` when no PCM source is attached (cava-only path).
  - **`relative_exertion`**: alias for `features.full` — BandNormaliser exertion
    after AGC, a transient detector rather than a section-level signal.
- **Effect** (`LayerMixer`): maps `AudioFeatures` to a `Scene` (Protocol),
  crossfading a mellow and an active `ColourModeEffect` by energy. The blend
  input is `sustained_energy` (primary) or `features.full` (degraded fallback
  when no PCM source).
- **Scene**: `color_at(position, t) → Colour` — effects never touch Hue
  protocol types or channel IDs.
- **Output** (`HueDriver`): samples the `Scene` at each light's `(x, y, z)`
  position and sends commands over DTLS/UDP.

All protocol types live in `src/huesync/types.py`. `LightColorCommand` and
any future driver-specific types must not appear outside their own driver
module (`hue_output.py`, etc.).

---

## Development

### Python backend

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

### React frontend

Source lives in `web/`. The build output (`src/huesync/webui/`) is part of
the Python package and committed to the repo — **no Node is needed on the
target machine**, only for local development.

```bash
cd web
npm install
npm run dev        # dev server on :5173 with proxy to FastAPI on :8420
```

After any source change:

```bash
npm run build      # writes to src/huesync/webui/
git add src/huesync/webui/
```

Then deploy as usual (`git pull && pip install . && systemctl restart huesync`).

### Diagnostic scripts

Run on the LXC with the project venv (`.venv/bin/python scripts/<name>.py`).

| Script | Purpose |
|---|---|
| `compare_bars.py` | Side-by-side comparison of cava bar output versus `PcmAudioPipeline` bar output from the same squeezelite SHM source — useful for verifying the two analysis paths agree. |
| `verify_pcm_source.py` | Polls `SqueezeliteShmSource.read_new()` for five seconds against a live squeezelite instance and prints per-poll statistics including torn-read discard counts. |
| `capture_energy.py` | Streams live energy and blend-weight data from a running HueSync instance to a CSV file. Columns include `relative_exertion`, `sustained_energy`, and `mix`. Use to capture reference data for offline analysis. |
| `analyse_energy.py` | Offline evaluation of alternative `blend_start` / `blend_end` / `blend_response` settings against a CSV produced by `capture_energy.py`, without replaying audio or touching runtime state. Supports windowed analysis. |
| `calibrate_energy.py` | Decodes an audio file, reconstructs `sustained_energy` at production tick rate, and aligns it with a HueSync capture CSV via cross-correlation. Use to evaluate EnergyProfile thresholds against a specific track. |

---

## Known limitations

- One active Entertainment stream per bridge (Hue Bridge hardware limit;
  enforced in `HueDriver`, not in shared code).
- Spatial effects (`fireworks`, `wave`, `swirl`, `splotches`,
  `spectrum_rgb_spatial`) distribute colour across the entertainment area by
  position. The remaining effects (`spectrum_rgb`, `mono_pulse`, `pulses`,
  `flashes`) currently send the same colour to every light. Per-light spatial
  mapping for those effects is a planned milestone.
- No authentication on the web UI — intended for a trusted home LAN only.
- LMS discovery uses UDP broadcast and does not cross subnets.
- **Configuration UI is partially redesigned.** Couplings, Analysers, Effects,
  and Energy Profiles have been rebuilt as full-page workspace editors; Virtual
  Players, Zones, Latency, and Now Playing still use the older modal-dialog
  style. See the Configuration UI section above for the current state.

---

## Roadmap

See [`docs/HueSync_ROADMAP.md`](docs/HueSync_ROADMAP.md) for planned work.
The effect engine spec is in [`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — non-commercial use only.

**Attribution is required for all use**, including non-commercial use. Anyone
who receives a copy of this software (fork, download, redistribution) must
preserve and pass on the following notice:

> Required Notice: Copyright (c) 2026 Jaap van Vliet

This is not optional — the license explicitly mandates it in the Notices
section.
