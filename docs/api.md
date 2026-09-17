# API operations

HueSync serves the web application/API on port 8420. The running application exposes
FastAPI's `/docs` and `/openapi.json`; use those generated schemas for complete bodies
and routes. This document describes lifecycle semantics rather than duplicating schemas.

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
