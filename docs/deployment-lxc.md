# Target LXC deployment and validation

Status: **REQUIRES LXC VALIDATION**. Local source checks and stubbed native boundaries
cannot establish live deployment behavior.

## Before activation

1. Follow [installation](installation.md); record the HueSync commit and configuration.
2. Provision host snd-dummy/audio-device passthrough and permissions for LMS pacing.
3. Build the pinned Squeezelite producer with VISEXPORT. Record actual binary path and
   revision; restart the process so it maps a v1 segment.
4. Confirm the selected mode: canonical PCM + V2/CAVA Core, or external CAVA/FIFO.
5. Run `bash scripts/validate.sh`; inspect `journalctl -u huesync -n 100`.

Do not read a production audio FIFO from a diagnostic second consumer. For SHM,
inspect metadata or use isolated test fixtures. Unsupported canonical ABI is a
producer-upgrade error, never permission to use v0/header bytes as PCM.

## Required target evidence

- Full producer build/link with target ALSA/codec dependencies.
- Live source continuity across restart, gap, overrun and SHM replacement.
- Native CAVA loading/execution, repeated close/reset and allocation/leak stress.
- Wheel build and clean installation in a fresh environment.
- CPU/backlog behavior under both engines and multiple processors.
- Source-consistent acceptance and visual comparison; record engine identities,
  sample intervals and configuration, including CAVA's 100 Hz execution cadence.

On a stuck worker, source ownership is retained. Do not force a new session on the
same source. Inspect `analysis_stopping`, release/fix the source condition, then retry
teardown. A service process exit has OS cleanup semantics; it is not evidence that
an in-process timed-out teardown successfully joined its worker.

Archive results with commit, producer revision, dependencies, hardware/container limits,
commands and raw artifacts. Old production benchmarks apply only to their original
context and do not certify this implementation.
