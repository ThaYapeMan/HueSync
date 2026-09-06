# HueSync

Spectrum-reactive Philips Hue Entertainment lighting for [Lyrion Music
Server](https://lyrion.org/) (formerly Logitech Media Server / Squeezebox).

HueSync registers a **virtual player** with your LMS server. Sync it to
whatever real player you're actually listening on, and the room's Hue lights
react live to the music's spectrum and dynamics — no pre-computed BPM tags,
no extra microphone hardware.

## How it works

```
LMS (audio orchestration)
   │  slimproto
squeezelite -v  →  /dev/shm/squeezelite-<mac>   ← virtual player; snd-dummy for pacing
   │
   ├─ cava (shmem input → FIFO)         ←  FFT + log-spaced spectrum bars, ~30 Hz
   │      │  30-bar spectrum frames
   │  BandNormaliser (per-band AGC)
   │      │  normalised bars → AudioFeatures
   │
   └─ PcmStft (100 Hz STFT tap)         ←  onset detection only (multiband / superflux)
          │  magnitude frames per 10 ms hop
   MultibandStftPipeline / SuperfluxStftPipeline / StftOnsetPipeline
          │  onset flags (bass / mid / treble)

SyncEngine  →  ColourModeEffect  →  Scene
   │  30 Hz send loop (DTLS/UDP)
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

---

## Architecture — five-entity model

HueSync organises configuration as six types of entities. A **Coupling** links
one of each sub-entity and is the thing you activate.

```
Controller  ←── LightProvider ──┐
                                 │
Player  ──── AnalysisConfig ────── Coupling ──── RenderConfig
```

| Entity | Responsibility |
|---|---|
| **Controller** | A Hue Bridge: IP, app key, client key. Used for pairing and Entertainment Area queries. |
| **Player** | A virtual squeezelite instance: LMS host, player name, MAC address, ALSA device. |
| **LightProvider** | Links a Controller to one of its Entertainment Areas. |
| **AnalysisConfig** | cava parameters (bars, cutoff freqs) and onset detection settings (method, delta, alpha). |
| **RenderConfig** | Visual output parameters: colour mode, sensitivity, brightness floor, band boundaries. |
| **Coupling** | Binds exactly one Player + AnalysisConfig + LightProvider + RenderConfig. Activate a Coupling to start the light show. |

A Coupling can be **cloned** (Clone button in the UI) — the new Coupling gets
independent copies of its AnalysisConfig and RenderConfig (fresh IDs, same
values), while sharing the same Player and LightProvider. This is the
recommended way to set up A/B comparisons: clone, change one field on the
copy, switch between them.

### Relationship rules

- One Player → many Couplings (same squeezelite process, different analysis
  or render settings).
- One Controller → many LightProviders (one per Entertainment Area).
- AnalysisConfig and RenderConfig can be shared across Couplings — or kept
  exclusive per Coupling (as created by Clone).
- **One Entertainment Area can stream per bridge at a time** (Hue Bridge
  hardware limit). Activating a Coupling automatically stops whatever was
  running before.

### Backward-compatibility layer (Profiles + Bridges)

The original **Profile** and **Bridge** flat-model is still present as a
backward-compatibility layer. The old *Profiles* tab and `/api/profiles`
endpoints continue to work; the *Bridges* tab and `/api/bridges` endpoints
are still the pairing entry point. During the transition the active-profile
and active-coupling state are kept in sync — activating via either tab updates
both.

This layer will be removed in a planned cutover commit once the five-entity
model has been fully validated in production. Until then, the *Profiles* tab
shows a legacy warning and the five-entity tabs (Virtual Players, Analysis
Configs, Render Configs, Couplings) are the primary interface.

---

## Onset detection

HueSync runs up to two parallel onset-detection paths:

| Path | Rate | Source |
|---|---|---|
| cava-based (`OnsetDetector` in `CavaAnalyser`) | ~30 Hz | Normalised bar spectrum |
| PCM tap (`StftOnsetPipeline` / `MultibandStftPipeline` / `SuperfluxStftPipeline`) | 100 Hz | 2048-sample Hamming-windowed STFT on raw PCM from shared memory |

The **`onset_method`** field in AnalysisConfig selects the PCM-tap algorithm:

| Method | Behaviour |
|---|---|
| `combined` | StftOnsetPipeline on full-spectrum STFT flux. Runs as a parallel comparison; the cava-based onset still drives the `onset` flag seen by the UI. |
| `multiband` | Three independent `OnsetDetector` instances on bass / mid / treble STFT slices. Overrides `features.onset` and sets separate `onset_bass` / `onset_mid` / `onset_treble` flags for the UI preview. |
| `superflux` | Böck & Widmer (2013) max-filter vibrato suppression before spectral flux, then Dixon peak-picking. Overrides `features.onset`. Reduces false positives on vibrato-heavy material. |

**Onset detection does not affect light colour or brightness** — that is
driven entirely by the cava spectrum bars through `BandNormaliser`. Onset
flags are used by the UI preview (onset flash indicator) and are available for
future effects that need transient triggers.

Switching `onset_method` live (by editing the active Coupling's field) rebuilds
the PCM pipeline in place — no process restart, BandNormaliser EMA preserved.

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
git clone https://github.com/ThaYapeMan/SqueezeHue.git
cd SqueezeHue
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

### Quick start with Couplings

1. **Pair a Controller**: go to *Controllers*, press the physical link button
   on the bridge, submit *Pair* within ~30 s.
2. **Create a Virtual Player**: go to *Virtual Players* → *New virtual player*.
   Enter the LMS host (use *Discover*) and a player name.
3. **Create a LightProvider**: go to *Couplings* → *New coupling* → *+ New
   light provider*. Select the Controller and Entertainment Area.
4. **Create a Coupling**: link the Player, a LightProvider, an AnalysisConfig,
   and a RenderConfig. Defaults work out of the box.
5. **Activate** the Coupling. In LMS, sync the new virtual player to your real
   listening player, exactly like multi-room playback.
6. Play music. The lights react to the live spectrum.

The **Now Playing** tab shows the live preview and lets you activate/stop
Couplings directly without switching tabs.

### Layered update routing

Editing a Coupling applies the minimum necessary action based on which fields
changed:

| Changed field(s) | Action |
|---|---|
| `player_id`, `analysis_config_id`, `light_provider_id`, `render_config_id`, `lms_host`, `lms_port`, `player_name`, `alsa_device` | Full deactivate — squeezelite, cava, and the Hue DTLS session are torn down. Reactivate manually. |
| `bars`, `lower_cutoff_freq`, `higher_cutoff_freq` | cava-only restart — squeezelite and the Hue session stay up. |
| `onset_method`, `onset_delta`, `onset_alpha`, `superflux_mu`, `superflux_lag`, `bass_hz`, `mid_hz` | PCM pipeline rebuilt live — no process restart, BandNormaliser EMA preserved. |
| `color_mode`, `sensitivity`, `brightness_floor`, `exertion_clip`, `entertainment_area_name`, `light_count` | Render update only — applied immediately, nothing restarts. |
| `name`, `enabled` | Metadata only — no session action. |

Only the most disruptive category in a given PATCH triggers a restart.

### Colour modes

| Mode | Behaviour |
|---|---|
| `spectrum_rgb` | Bass → red, mid → green, treble → blue. |
| `mono_pulse` | Single colour; brightness follows overall loudness. |

### Key AnalysisConfig fields

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

### Key RenderConfig fields

| Field | Default | Notes |
|---|---|---|
| `sensitivity` | 1.0 | Scales bar values after AGC normalisation. |
| `brightness_floor` | 0.15 | Minimum brightness; prevents lights going fully dark during quiet passages. |
| `exertion_clip` | 3.0 | Sets "maximally loud" in relative terms. A band at `exertion_clip`× its rolling average clips to full output. |
| `bass_hz` | 250 | Bass/mid boundary (Hz) for band splitting in the renderer. |
| `mid_hz` | 2000 | Mid/treble boundary (Hz). |

### Player latency

When HueSync's virtual player is synced to an AirPlay or Sonos room, audio
arrives at the speakers with a device-specific buffer delay (typically 1–2 s
for AirPlay, 2+ s for Sonos). HueSync detects the LMS sync master
automatically every 15 s and applies the matching **Player latency** entry.

Add an entry in the *Player latency* section of the UI:

- **Strategy → Fixed**: enter the known buffer delay in ms. Right for AirPlay
  (~2000 ms) and other players with a stable, negotiated latency.
- **Strategy → None**: no delay compensation.

The entry takes effect immediately — no reactivation needed.

---

## JSON API

A REST API is available at `/api/*`. Interactive documentation (OpenAPI /
Swagger) is at **`/docs`**.

### Five-entity endpoints

```
GET/POST        /api/controllers
GET/PATCH/DELETE /api/controllers/{id}
GET             /api/controllers/{id}/areas    list Entertainment Areas

GET/POST        /api/players
GET/PATCH/DELETE /api/players/{id}

GET/POST        /api/light-providers
GET/PATCH/DELETE /api/light-providers/{id}

GET/POST        /api/analysis-configs
GET/PATCH/DELETE /api/analysis-configs/{id}

GET/POST        /api/render-configs
GET/PATCH/DELETE /api/render-configs/{id}

GET/POST        /api/couplings
GET/PATCH/DELETE /api/couplings/{id}
POST            /api/couplings/{id}/activate
POST            /api/couplings/{id}/clone      deep-clone with fresh AC + RC copies
POST            /api/couplings/deactivate
```

### Legacy endpoints (backward compatibility)

```
GET/POST        /api/bridges
GET             /api/bridges/{id}/areas
GET/POST        /api/profiles
GET/PATCH/DELETE /api/profiles/{id}
POST            /api/profiles/{id}/activate
POST            /api/profiles/{id}/restart-cava
POST            /api/profiles/deactivate
```

### Status and utilities

```
GET             /api/status      version, active coupling/profile, sync master,
                                 onset_method, color_mode, delay, process status
GET             /api/player-latencies
POST/PATCH/DELETE /api/player-latencies/{mac}
GET             /api/lms/discover
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
  "active_coupling_id": "…", "active_profile_id": "…",
  "active_profile_name": "Zitkamer",
  "color_mode": "spectrum_rgb", "onset_method": "combined",
  "sync_master": "aa:bb:…", "sync_master_name": "SONOS::Study",
  "bridge_connected": true,
  "processes": {"squeezelite": true, "cava": true},
  … }   // sent only when values change
```

---

## Architecture — effect pipeline

```
Analyser  →  AudioFeatures  →  Effect  →  Scene  →  Output
```

- **Analyser** (`CavaAnalyser`): reads cava bars, applies per-band EMA AGC
  (`BandNormaliser`), runs Dixon onset detection.
- **Effect** (`ColourModeEffect`): maps `AudioFeatures.bars` to a `Scene`.
  Onset flags are available in `AudioFeatures` but not currently used for
  colour — reserved for future effects.
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

---

## Roadmap

See [`docs/HueSync_ROADMAP.md`](docs/HueSync_ROADMAP.md) for what is planned:
two-layer Mellow/Active mixer, mood outputs (Tuya projector), spatial effects,
and more. The effect engine spec is in
[`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).

## Known limitations

- One active Entertainment stream per bridge (Hue Bridge hardware limit;
  enforced in `HueDriver`, not in shared code).
- Colour mapping currently sends the same colour to every light in the area.
  Per-light spatial effects are the next planned effect-engine milestone.
- No authentication on the web UI — intended for a trusted home LAN only.
- The old Profiles/Bridges layer and the new five-entity layer coexist during
  the transition period. They share `active_profile_id` state; activating
  via either tab affects the same running session.

## License

MIT, see [LICENSE](LICENSE).
