# Target LXC deployment and validation

Status: **REQUIRES LXC VALIDATION**. Local source checks and stubbed native boundaries
cannot establish live deployment behavior.

## Before activation

```sh
git clone https://github.com/ThaYapeMan/HueSync.git
cd HueSync
sudo ./scripts/install-huesync.sh
sudo ./scripts/install-huesync.sh --check
```

The repository installer is the authoritative standard deployment path. It owns
packages, full native builds, frontend/wheel installation, schema migration and
services. Do not reproduce separate manual dependency or producer-patching steps.
`--check` is read-only verification of an existing Debian 13/trixie x86_64
installation with systemd running (`/run/systemd/system`). It does not install,
migrate or modify the host. For development-host checks, see [testing](testing.md).
Record its commit/binary hashes and run it twice to verify target idempotency.

The host must expose paced snd-dummy/audio devices and appropriate permissions for
LMS; the guest installer does not alter the host. Select canonical PCM + V2/CAVA Core
or the intentional external FIFO route in HueSync. Inspect `journalctl -u huesync`.

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
