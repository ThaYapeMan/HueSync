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
   |  slimproto
squeezelite -n "HueSync" -o hw:CARD=Dummy,DEV=0 -v   ← virtual player; snd-dummy for pacing
   |  shared memory (live PCM, /dev/shm/squeezelite-<mac>)
cava (shmem input, raw output → FIFO)                 ← FFT + log-spaced spectrum bars
   |  spectrum bars (30 Hz)
huesync / SyncEngine                                  ← analysis, normalisation, effect render
   |  Hue Entertainment API, up to 50 Hz (DTLS/UDP)
Hue Bridge → Entertainment Area (your chosen room)
```

Three points worth understanding:

- **squeezelite's `-v` flag** exposes a live audio buffer in shared memory,
  originally for on-device spectrum displays (jivelite). HueSync repurposes it.
- **[cava](https://github.com/karlstav/cava)** has a `shmem` input module built
  for squeezelite's shared memory format and a `raw` output mode for FIFOs.
  HueSync doesn't do its own FFT — cava handles the DSP.
- **[hue-entertainment](https://github.com/music-assistant/hue-entertainment)**
  handles the DTLS-PSK handshake and HueStream protocol.

## Hardware/software requirements

- A Hue Bridge (V2 "square" or Pro) with at least one
  [Entertainment Area](https://www.philips-hue.com/en-us/explore-hue/propositions/entertainment)
  configured in the Hue app (HueSync cannot create areas — only the app can).
- `squeezelite` and `cava` installed and on `PATH`.
- Python 3.11+.
- A Lyrion Music Server instance on the same network.
- **The `snd-dummy` kernel module loaded** — see below.

### Why snd-dummy is required

ALSA's `null` plugin discards samples immediately with no hardware clock, so
squeezelite decodes as fast as the CPU allows — ~100 % of a core, and LMS
stutters for every other player. `snd-dummy` is a real, timer-driven ALSA card;
squeezelite paces at actual playback speed (~0.2 % CPU).

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

### Bridge limitation

**A single Hue Bridge can stream to only one Entertainment Area at a time.**
Activating a profile automatically deactivates whichever was running before.
Multiple bridges run independently.

## Installation

```bash
apt install -y squeezelite cava python3-venv   # Debian/Ubuntu
git clone https://github.com/ThaYapeMan/SqueezeHue.git
cd SqueezeHue
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

The build step captures the current git commit hash and embeds it in the
package so `GET /api/status` can report the exact build running on the target.

Run it:

```bash
huesync
```

The web UI and API listen on `http://<host>:8420`. Config is stored as JSON at
`/etc/huesync/config.json` by default (override with `HUESYNC_CONFIG`).

### Running as a service

See [`systemd/huesync.service`](systemd/huesync.service). Copy to
`/etc/systemd/system/`, then:

```bash
systemctl daemon-reload
systemctl enable --now huesync
```

## Usage

1. **Pair a bridge**: press the physical link button, then submit the "Pair"
   form within ~30 seconds.
2. **Create a profile**: point it at your LMS server (use **Discover** to find
   it), name the virtual player, pick a bridge + Entertainment Area.
3. **Activate** the profile. In LMS, sync the new virtual player to your real
   listening player, exactly like multi-room playback.
4. Play music. The lights react to the live spectrum.

Editing a profile applies the minimum necessary action based on which fields
changed:

| Changed field(s) | Action |
|---|---|
| `lms_host`, `lms_port`, `player_name`, `player_mac`, `alsa_device`, `bridge_id`, `entertainment_area_id` | Full deactivate — squeezelite, cava, and the Hue DTLS session are torn down. Reactivate manually. |
| `bars`, `lower_cutoff_freq`, `higher_cutoff_freq` | cava-only restart — squeezelite and the Hue DTLS session stay up. |
| `onset_method`, `onset_delta`, `onset_alpha`, `superflux_mu`, `superflux_lag`, `bass_hz`, `mid_hz` | PCM pipeline rebuilt live — no process restart. |
| `color_mode`, `sensitivity`, `brightness_floor`, `exertion_clip`, `enabled`, `entertainment_area_name`, `light_count` | Render update only — applied immediately, nothing restarts. |
| `name` (profile name only) | Metadata only — no session action. |

Only the most disruptive category in a given PATCH triggers a restart (player
wins over cava, cava wins over PCM, etc.).

### Colour modes

| Mode | Behaviour |
|---|---|
| `spectrum_rgb` | Bass → red, mid → green, treble → blue. |
| `mono_pulse` | Single colour, brightness follows overall loudness. |

### Profile fields

| Field | Default | Notes |
|---|---|---|
| `sensitivity` | 1.0 | Scales bar values after AGC normalisation. Fine-tune brightness; see `exertion_clip`. |
| `brightness_floor` | 0.15 | Minimum brightness; keeps lights from going fully dark during quiet passages. |
| `exertion_clip` | 3.0 | Sets "maximally loud" in relative terms. A band at `exertion_clip`× its rolling average clips to full output. Raise for more dynamic headroom before saturation. |
| `bars` | 30 | Number of frequency bins cava analyses. |
| `lower_cutoff_freq` | 50 Hz | Low end of the analysed range. |
| `higher_cutoff_freq` | 12000 Hz | High end. Music has almost no energy above ~12 kHz; cava's default 22 kHz Nyquist leaves the top third of the bar frame near zero. |
| `onset_method` | `combined` | Onset detection algorithm. `combined` = full-spectrum spectral flux on cava bars (30 Hz). `multiband` = per-band flux on STFT data (100 Hz), giving separate bass/mid/treble onset flags. `superflux` = Böck & Widmer (2013) max-filter vibrato suppression on STFT data (100 Hz). Switching this field live-rebuilds the PCM pipeline without restarting cava or the Hue session. |
| `onset_delta` | 0.1 | Margin above the local mean for onset detection (Dixon 2006). Higher = fewer, more confident onsets. |
| `onset_alpha` | 0.9 | Per-frame decay of the suppression threshold after an onset. Higher = longer suppression before a second onset can fire. |
| `superflux_mu` | 3 | SuperFlux only: max-filter half-width in FFT bins. Wider = more vibrato suppression. |
| `superflux_lag` | 2 | SuperFlux only: compare current frame with the frame `lag` steps ago. |

### Player latency

When HueSync's virtual player is synced to an AirPlay or Sonos room, audio
arrives at the speakers with a device-specific buffer delay (typically 1–2 s
for AirPlay, 2+ s for Sonos). HueSync detects the LMS sync master automatically
every 15 s and applies the matching **Player latency** entry.

Add an entry in the *Player latency* section of the UI:

- **Strategy → Fixed**: enter the known buffer delay in ms. Right for AirPlay
  (~2000 ms) and other players with a stable, negotiated latency.
- **Strategy → None**: no delay compensation (direct/wired players).

The entry takes effect immediately on a running session — no reactivation needed.

## JSON API

A REST API is available at `/api/*`. Interactive documentation (OpenAPI/Swagger)
is at **`/docs`**.

Key endpoints:

```
GET  /api/status              version, active profile, sync master, delay, process status
GET  /api/profiles            list profiles
POST /api/profiles            create profile
PATCH /api/profiles/{id}      partial update (live parameter changes)
POST /api/profiles/{id}/activate
GET  /api/bridges             list paired bridges
GET  /api/player-latencies    list latency entries
```

The WebSocket at `/ws/preview` sends typed JSON messages:

```jsonc
{ "type": "frame",    "colour": {"r": 0, "g": 0, "b": 0}, "onset": false }
{ "type": "spectrum", "bars": [0.1, 0.4, …] }   // 10 Hz
{ "type": "status",   "version": "0.2.0+abc1234",
                       "active_profile_id": "…", "active_profile_name": "…",
                       "sync_master": "aa:bb:…", "sync_master_name": "SONOS::Study",
                       … }  // on change only
```

`sync_master_name` carries the raw LMS player name. The UI formats it for
display (e.g. `"SONOS::Study"` → `"Study (Sonos)"`).

## Architecture — effect pipeline

```
Analyser  →  AudioFeatures  →  Effect  →  Scene  →  Output
```

- **Analyser** (`CavaAnalyser`): reads cava bars, applies per-band AGC
  (`BandNormaliser`), runs Dixon onset detection (`OnsetDetector`).
- **Effect** (`ColourModeEffect`): maps `AudioFeatures` to a `Scene`.
- **Scene**: `color_at(position, t) → Colour` — effects never touch
  `LightColorCommand` or channel IDs.
- **Output** (`HueDriver`): samples the `Scene` at each light's `(x, y, z)`
  position and sends commands over DTLS/UDP.

The effect roadmap (layered Mellow/Active engine, palettes, spatial waves) is
in [`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).

## Development

### Python backend

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

### React frontend

Source lives in `web/`. The build output (`src/huesync/webui/`) is part of the
Python package and committed to the repo, so **no Node is needed on the target
machine** — only for local development.

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

Deploy as usual (`git pull && pip install . && systemctl restart huesync`).
The `pip install .` step re-embeds the git commit hash into the package.
The built UI is served at `/`.

## Known limitations / roadmap

- One active stream per bridge (Hue Bridge hardware limit).
- Colour mapping currently uses the same colour for every light in the area.
  Per-light spatial effects are planned — see
  [`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).
- No authentication on the web UI — intended for a trusted home LAN only.

## License

MIT, see [LICENSE](LICENSE).
