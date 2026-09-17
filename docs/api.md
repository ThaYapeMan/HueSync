# API operations

HueSync serves the web application/API on port 8420. The running application exposes
FastAPI's `/docs` and `/openapi.json`; use those generated schemas for REST request
and response bodies. The [REST endpoint index](#rest-endpoint-index) below lists current
methods and paths without needing a running app. OpenAPI does not describe WebSocket
messages; their separate contract is documented in [WebSocket](#websocket).

## WebSocket

Connect to `ws://<host>:8420/ws/preview` (or `wss://` behind a TLS proxy).
The handler accepts the connection and pushes JSON objects identified by `type`.
There is no subscription message or client-command protocol in this handler.
As with the REST API, no authentication is implemented; use trusted-network access.

### Delivery and cadence

Each connection starts its own loop at tick 0:

1. `frame` is sent every iteration, followed by a 50 ms sleep after the other work
   in that iteration: approximately 20 Hz, not a hard realtime guarantee.
2. `spectrum` is sent when `tick % 3 == 0`, including the first iteration:
   approximately every 150 ms.
3. `status` is sent on the first iteration and thereafter only when its serialized
   contents differ from the last status sent on that connection. This is
   **change-triggered delivery of a complete object**, not a partial-field delta.

Within an iteration the order is frame, optional spectrum, optional status.
Reconnecting starts this sequence again. Messages sample current manager state;
they contain no publication sequence, epoch or sample interval and are not an atomic
cross-message snapshot or a lossless analysis event log. Cadence is independent of
Spectrum engine execution cadence. Clients should retain status between updates.

### `frame`

All fields below are sent on every frame message. RGB values are integers in
0–65535, not 8-bit RGB or normalized floats.

| Field | JSON type | Meaning |
|---|---|---|
| `type` | string | Always `"frame"` |
| `colour` | object `{r, g, b}` | First output channel's color; `{r: 0, g: 0, b: 0}` if there are no output colors |
| `channel_colours` | array of `{r, g, b}` | Output colors in driver channel order; empty when none are available |
| `onset` | boolean | Latest overall onset flag, before lighting output delay |
| `pcm_onset` | boolean | Manager's PCM-tap onset flag; not another guaranteed onset event stream |
| `onset_bass` | boolean | Latest bass onset flag |
| `onset_mid` | boolean | Latest mid onset flag |
| `onset_treble` | boolean | Latest treble onset flag |
| `mix` | number | LayerMixer blend: 0 = low-energy Effect, 1 = high-energy Effect |
| `energy` | number | Latest full-band relative energy/exertion value (`last_energy`); 0 when no session/SyncEngine is available |
| `sustained_energy` | number or null | Section-level energy tracker value when available; null otherwise |
| `relative_exertion` | number | The same `last_energy` value as `energy`, not a separate measurement |

These are latest observed values, not a guarantee of one message per detected onset.
Output colors can reflect lighting delay while onset flags reflect undelayed analysis.

### `spectrum`

| Field | JSON type | Meaning |
|---|---|---|
| `type` | string | Always `"spectrum"` |
| `bars` | array of numbers | Latest Spectrum bars in their analysis order; empty if unavailable; do not assume a fixed bar count |

### `status`

Every status update includes the following complete field set. Nullable fields
represent absent session/configuration or unavailable information; configured but
unbound references can also be empty strings.

| Field | JSON type | Meaning |
|---|---|---|
| `type` | string | Always `"status"` |
| `version` | string | HueSync version plus commit, formatted `version+commit` |
| `active_coupling_id` | string or null | Current owned Coupling ID |
| `active_coupling_name` | string or null | Current Coupling name |
| `active_zone_id` | string or null | Coupling's Zone reference |
| `active_energy_profile_id` | string or null | Coupling's EnergyProfile reference |
| `sync_master` | string or null | Detected LMS sync-master identity |
| `sync_master_name` | string or null | Detected sync-master display name |
| `applied_delay_ms` | integer | Current latency-probe delay in milliseconds; 0 without a session |
| `latency_warning` | string or null | Latency warning text, when present |
| `processes` | object | Exactly `squeezelite` and `cava` booleans indicating external process liveness; both false for AirPlay or no session. Embedded CAVA Core is not this `cava` process |
| `bridge_connected` | boolean | Whether the session has a Hue output driver; not an independent network-health probe |
| `effect_type` | string or null | Selected runtime visual algorithm ID |
| `follower_warning` | string or null | LMS follower warning, when present |
| `onset_method` | string or null | Active runtime onset method |
| `lower_cutoff_freq` | integer or null | Active lower analysis cutoff in Hz |
| `higher_cutoff_freq` | integer or null | Active upper analysis cutoff in Hz |
| `bass_hz` | integer or null | Active bass/mid boundary in Hz |
| `mid_hz` | integer or null | Active mid/treble boundary in Hz |

Do not substitute the `GET /api/status` response as this message schema: the REST
route has a different field set. For example, `analysis_stopping`,
`active_player_type`, `airplay_receiving` and `bars_stats` are REST fields, not
fields sent by `ws_preview`. A Coupling ID indicates session ownership, not proof
that analysis is still processing during teardown.

## Controller setup

Controller creation/pairing is currently API-only; the Zones page selects existing
Controllers. Use `POST /api/controllers/pair` with the bridge host and name after
pressing its link button, or `POST /api/controllers` with existing pairing data.
See `/docs` for the exact current request fields. `GET /api/controllers` redacts
credentials; `GET /api/controllers/{controller_id}/areas` lists Entertainment Areas
for Zone selection. Pairing is still application configuration, not an installer step.

## Analysis configuration

Analyser create/PATCH supports spectrum_backend and bars_source. Model/API validation
uses the engine registry and rejects unsupported values and FIFO+embedded-CAVA conflicts.
Passive validation does not require native availability. Activation does.

An active Analyser PATCH routes analysis-owned fields to the current owner. Canonical
Spectrum settings create a candidate pipeline; Beat settings target the active
BeatDetector. Failed runtime replacement restores stored/session configuration through
the existing rollback path and returns an explicit error (typically HTTP 409).

The historically named `restart-cava` coupling endpoint is owner-aware: it replaces
canonical analysis for PCM mode and restarts external CAVA for FIFO mode. Failed changes
roll back the persisted Analyser/effect/coupling snapshots. FIFO startup failure attempts
to restore the old process configuration; if recovery also fails, cava is explicitly
absent rather than represented by the terminated process.

## Stop and retirement

A stop timeout closes the unused candidate, retains the old worker and refuses a
second reader. Manager status includes `analysis_stopping`. Follower stop/task cleanup completes
once and its references are cleared; repeated analysis-retirement retries do not
repeat that cleanup. Unexpected cleanup errors propagate. Teardown cancels session
work but retains the source/session until the owned analysis worker terminates.
An unsuccessful teardown raises a retriable operational error; no new session starts.
`POST /api/couplings/deactivate` reports this as HTTP 409 with the retirement detail.
Retry deactivation/activation after retirement. A stored active coupling identifies
retained session ownership and is not proof that DSP is still processing.

The API's existing exception handling determines the status code for each route;
not every operational error is a validation error. Do not interpret a failed request
as successful activation. There is no claim of crash-atomic disk/process transactions.

## Feature consumers

Runtime Effects consume AudioFeatures. PublicationRecord is the synchronous/queued
analysis contract used by acceptance. Sequence orders delivery; sample intervals are
event time and may be older for delayed processors. Historical bars have explicit
carried_spectrum_interval provenance and are not fresh contributor IDs. `/ws/preview`
is a live preview, not a lossless analysis event log.

## Current terminology only

Use `/api/effects` and `/api/energy-profiles`. Retired entity-name routes are removed;
request schemas reject unknown fields rather than silently discarding them. The
WebSocket status payload uses `effect_type` (the same field consumed by the current
frontend). Publication records use `effective_spectrum_backend` for actual Spectrum
identity. Persisted history is migrated by the installer, never by HTTP aliases.

## Sensitive configuration transfer

`GET /api/config/export` downloads a full credential-preserving JSON attachment.
`POST /api/config/import` accepts that JSON as an application/json body (4 MiB limit),
validates it and restores only while the runtime is fully inactive. Both use no-store.
This is the explicit exception to ordinary Controller credential redaction. There is
no authentication; use trusted-network access only. See the authoritative
[backup/restore contract](configuration.md#backup-and-restore).

## REST endpoint index

Methods and paths below are taken from the router definitions in
[`src/huesync/api.py`](../src/huesync/api.py), including its `/api` prefix.
Request/response bodies, query parameters and validation remain in `/docs` and
`/openapi.json`. These are REST routes; `/ws/preview` is documented separately above.

```text
GET /api/player-latencies
POST /api/player-latencies
PATCH /api/player-latencies/{player_mac}
DELETE /api/player-latencies/{player_mac}
GET /api/lms/discover
GET /api/lms/players
GET /api/status
POST /api/controllers/pair
GET /api/controllers
POST /api/controllers
GET /api/controllers/{controller_id}
PATCH /api/controllers/{controller_id}
GET /api/controllers/{controller_id}/areas
DELETE /api/controllers/{controller_id}
GET /api/virtual-players
POST /api/virtual-players
GET /api/virtual-players/{player_id}
PATCH /api/virtual-players/{player_id}
DELETE /api/virtual-players/{player_id}
GET /api/zones
POST /api/zones
GET /api/zones/{zone_id}
PATCH /api/zones/{zone_id}
DELETE /api/zones/{zone_id}
GET /api/zones/{zone_id}/channels
GET /api/analysers
POST /api/analysers
GET /api/analysers/{ac_id}
PATCH /api/analysers/{ac_id}
DELETE /api/analysers/{ac_id}
POST /api/analysers/{ac_id}/clone
GET /api/effects
POST /api/effects
GET /api/effects/{effect_id}
PATCH /api/effects/{effect_id}
DELETE /api/effects/{effect_id}
POST /api/effects/{effect_id}/clone
GET /api/energy-profiles
POST /api/energy-profiles
GET /api/energy-profiles/{ep_id}
PATCH /api/energy-profiles/{ep_id}
DELETE /api/energy-profiles/{ep_id}
POST /api/energy-profiles/{ep_id}/clone
GET /api/couplings
POST /api/couplings
POST /api/couplings/deactivate
POST /api/couplings/{coupling_id}/restart-cava
GET /api/couplings/{coupling_id}
PATCH /api/couplings/{coupling_id}
DELETE /api/couplings/{coupling_id}
POST /api/couplings/{coupling_id}/clone
POST /api/couplings/{coupling_id}/activate
GET /api/config/export
POST /api/config/import
```
