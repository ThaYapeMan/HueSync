# Configuration

The web UI and API persist configuration through Storage. `HUESYNC_CONFIG` defaults
to `/etc/huesync/config.json`. Keep a backup before migration or target experiments.
The installer explicitly migrates supported historical formats before startup.
Current startup rejects non-current schema and clears stale active-coupling state;
it does not restore a previous process merely because a stored ID was active.

## Entities

- VirtualPlayer: audio ingress identity and LMS/AirPlay settings.
- Controller: output-controller credentials and endpoint.
- Zone: controller/Entertainment area selection.
- Analyser: Spectrum and onset settings shared by Couplings.
- Effect: rendering behavior/settings.
- EnergyProfile: high/low-energy Effects and blend behavior.
- Coupling: links the player, zone, Analyser and EnergyProfile.

Profile is an internal propagated runtime configuration, not a second authoritative
backend selector. Runtime builders copy Analyser settings, including bars_source and
spectrum_backend. Unknown IDs and incompatible combinations raise explicit errors.

## Analysis defaults

| Field | Default | Meaning |
|---|---|---|
| `bars_source` | `cava` | LMS external FIFO; `pcm_pipeline` selects canonical LMS |
| `spectrum_backend` | `v2` | Canonical Spectrum engine; currently `v2` / `cavacore` |
| `bars` | 30 | Spectrum bar count |
| `lower_cutoff_freq` | 50 | Hz |
| `higher_cutoff_freq` | 12000 | Hz |
| `onset_method` | `combined` | Also `multiband` / `superflux` |
| `onset_delta` | 0.1 | Onset threshold margin |
| `onset_alpha` | 0.9 | Adaptive suppression decay |
| `superflux_mu` | 3 | Superflux setting |
| `superflux_lag` | 2 | Superflux lag |
| `use_hpss_separation` | false | Legacy PCM-tap option |

For embedded CAVA, set **both** `bars_source=pcm_pipeline` and
`spectrum_backend=cavacore`. The external FIFO combination is rejected. AirPlay
always uses canonical analysis; choose pcm_pipeline explicitly for clarity.

Allowed engine IDs derive from the static registry. Saving/deserializing a valid
engine ID does not load native code. Activation may still fail if unavailable.
No explicit CAVA request silently falls back to V2.

## Live changes

Canonical band/cutoff/backend changes replace canonical analysis. Beat settings
rebuild the active BeatDetector safely. External FIFO changes use its process restart
path. Analyser-ID changes crossing canonical/FIFO modes require full session teardown
and activation. Rendering settings remain owned by Effects/runtime rendering.

Retirement is incomplete teardown, not successful activation. `analysis_stopping`
reports retained ownership; retry deactivation once the worker exits. See [API](api.md).
The implementation does not promise crash-atomic transactions across JSON storage,
process creation and every possible operational failure.

## Persisted schema boundary

Current schema version: `schema_version: 1`. Collections: `controllers`,
`virtual_players`, `zones`, `analysers`, `effects`, `energy_profiles`, `couplings`,
`player_latencies`, plus `active_coupling_id`. Coupling has only the current
`player_id`, `zone_id`, `analyser_id`, `energy_profile_id` references.
Normal model/storage deserialization rejects unknown keys; it does not translate
historical names. Only the explicit installer migration knows those names.
See [migration and backups](installation.md#migration).
