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

## Backup and restore

**Full backups are sensitive.** They include Hue Bridge pairing credentials and
must not be published, logged or stored in a public location. Ordinary Controller
API responses still hide credentials; the explicit full-backup endpoint includes them.
HueSync currently has no authentication. Restrict UI/API access to a trusted network;
use a protected connection or tunnel when transferring a backup across networks.

### Configuration inventory

Every field of every persisted entity is exported and restored, including fields
not currently exposed by a UI control. The same current model/schema validation is
used for file and HTTP operations.

| Configurable item | Persisted collection | Export / restore |
|---|---|---|
| Controller identity, type, host, app_key, client_key | `controllers` | All fields, credentials unredacted |
| LMS and AirPlay type, identity, connection, display, ALSA and follower settings | `virtual_players` | All fields |
| Lighting group, Controller and Entertainment Area references | `zones` | All fields |
| Spectrum backend, bands/cutoffs, onset and analysis settings | `analysers` | All fields |
| Visual algorithm and rendering settings | `effects` | All fields |
| High/low Effect references and blend behavior | `energy_profiles` | All fields |
| Entity bindings and enabled selection | `couplings` | All fields |
| Per-listening-player latency strategy, delay, name and optional speaker address | `player_latencies` | All fields |

Selected references must resolve. Empty references allowed by the current editable
schema remain unbound; backup does not invent missing entities. Invalid nonempty
references prevent export and import. Complete field sets are exported and required
on import, so an incomplete backup cannot silently reset settings to defaults.

Excluded: the active session selection (`active_coupling_id` is always null), playback,
audio/features/publication queues, SHM/FFT state, processes, connections, logs, caches,
binaries, venv and frontend artifacts. Runtime Profile objects are reconstructed from
entities. Generated CAVA/receiver files are recreated at activation. OS environment,
network/device mappings, custom systemd overrides and manually edited receiver settings
are deployment administration, not portable HueSync configuration. The installer owns
the standard deployment defaults. There is no additional persisted browser setting store.

### Format and validation

JSON envelope: `format="huesync-config-backup"`, `backup_version=1`,
`schema_version=1`, UTC `created_at`, `huesync_version`, `huesync_commit`,
`contains_secrets=true`, and `configuration` (current persisted schema).
Backup format version and configuration schema version are distinct and both checked.
Unsupported versions, unknown fields/collections, partial entities, duplicate JSON keys,
malformed types and dangling references are rejected. Upload/file input is limited to
4 MiB. Historical schema aliases are not accepted; future version conversion must be
explicit. Installed builds include the deployed commit; source-only development may
report `unknown`.

### UI / API

Open **Backup and restore**. Export downloads a sensitive JSON file. For restore,
deactivate the Coupling first, select a backup file, check the replacement confirmation,
then click **Restore configuration**. Selecting a file alone never uploads it.

- `GET /api/config/export`: JSON attachment, timestamp filename, `Cache-Control: no-store`.
- `POST /api/config/import`: JSON body (`Content-Type: application/json`), not multipart.
  Returns restored/inactive status with `restart_required=false`. Errors have a fixed
  `detail.code` and safe `detail.message`; uploaded configuration is never echoed.
- Invalid backup: 422; oversized input: 413; wrong media type: 415; active/retiring
  runtime or failed commit: 409. Backup responses use `Cache-Control: no-store`.

### Runtime and failure policy

Restore requires a fully inactive PlayerManager, including completion of retiring-worker
teardown. An active/retiring session is rejected **before mutation** and remains owned;
restore does not attempt a potentially destructive stop/restart rollback. API configuration
writes are serialized so a request begun before restore cannot write stale settings after it.
Storage readers see the old complete file or the new complete file, never part of each.

Before replacement, restore writes an exact-byte safety copy:

```text
/etc/huesync/config.json.pre-restore.<SHA256-of-original>.<unique>.bak
```

It validates and prepares the new file before atomic rename. Validation, safety-backup,
preparation or rename failure leaves the original configuration intact; safety backups
are retained. After commit there is no runtime reload step that can fail: no session is
active, and Storage reads the new file directly. Activation of a restored Coupling is
an explicit subsequent operation. This is atomic file visibility, not a transaction
against power failure or external lighting hardware.

### CLI / disaster recovery

All CLI and API operations use `huesync.backup`, not separate serializers.

```sh
# Export may run while HueSync is running. Existing output files are never overwritten.
sudo /opt/huesync/.venv/bin/python -m huesync.backup export /secure/huesync-backup.json

# Validation only: no configuration, lock or runtime mutation.
sudo /opt/huesync/.venv/bin/python -m huesync.backup import --check /secure/huesync-backup.json

# Offline restore: stop service first; the runtime ownership lease enforces this.
sudo systemctl stop huesync
sudo /opt/huesync/.venv/bin/python -m huesync.backup import /secure/huesync-backup.json
sudo systemctl start huesync
```

For a custom file, place `--config /path/config.json` before `export` or `import`.
New CLI export and restore-safety files are mode 0600, owned by the invoking user.
Every restore creates a distinct safety copy, so root CLI and service-account API
operations never need to read or overwrite each other's private backup files.
The restored config retains its owner/group and is mode 0600. Browsers control download
file permissions; protect downloads yourself. CLI success/error text never prints
credential values. A fixed adjacent `.runtime.lock` file is intentionally retained;
do not delete it while runtime/restore is running.

Do not run the installer/migration or edit configuration files concurrently with
CLI restore; those external administration tools are not API transactions.
Offline restore can also replace a corrupt existing configuration file: the original
bytes are still saved first, while the incoming backup must pass full validation.
Repeated import is safe; it does not duplicate entities or activate playback. A new
installation needs the repository installer first, then this import. Hue pairing data
survives, provided the same Bridge remains reachable and its credentials have not been
revoked. Live lighting, LMS/AirPlay connectivity and cross-host recovery remain
**REQUIRES LXC RUNTIME VALIDATION**.

Migration backups (`pre-v1...bak`) and restore-safety backups are raw rollback files.
Portable `huesync-config-backup` JSON is the user-created transfer/disaster-recovery
format. They are not interchangeable, and the installer does not automatically export
secret portable backups.
