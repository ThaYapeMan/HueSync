# Squeezelite producer build and SHM v1

Current producer/source contract. Canonical LMS PCM requires exact SHM v1;
legacy external CAVA/FIFO is separate. Native live deployment **REQUIRES LXC VALIDATION**.

## Automatic build

Pinned upstream: `ralph-irving/squeezelite` at
`c7c4248ddd70e47dbfeba0bf4a8a7ec08d8a995c`.

For a standard installation, from the repository:

```sh
sudo ./scripts/install-huesync.sh
```

The repository installer is the authoritative standard deployment path. It provisions
all build/runtime dependencies and invokes `scripts/build-squeezelite.sh` automatically.
The helper remains available for isolated developer builds with dependencies already
present; it is not a second standard deployment procedure.

The helper clones and verifies the pin, copies `vis_shm_v1.h` and `output_vis_v1.c`,
dry-runs/applies `output_vis_v1.patch`, forces VISEXPORT, checks both producer objects
in the make plan, compiles them and performs the full link. The installer copies the
result to `/usr/local/bin/squeezelite`, compares bytes and records SHA256 provenance.
The service PATH selects this binary ahead of any packaged Squeezelite.
The helper also builds `huesync-squeezelite-fifo` from unpatched pinned upstream
with VISEXPORT, specifically for external CAVA's byte-80 ring layout. That binary
is selected only by `bars_source=cava`; canonical PCM always uses the v1 binary.
Neither route falls back to a distro executable.

The standard build enables the default PCM/FLAC/Vorbis/MP3/AAC codecs and VISEXPORT;
optional Opus/FFmpeg/ALAC/resampler flags are not enabled. AirPlay dependencies and
CAVA Core's FFTW dependency are provisioned separately by the same top-level installer.
See [installation](../docs/installation.md) for stages and verification boundaries.

The standard installer stops the previous service before installing binaries;
newly activated players use the matching executable. For manual developer builds,
restart the actual running process and verify its executable path. A successful build plan is not an object compile; compiled producer objects
are not a full link, deployment or live continuity test.

## ABI layout

The supported target layout is little-endian, with the legacy pthread-lock/header
region preserved and a packed 40-byte extension before the PCM ring. C static
assertions and Python struct formats define the same offsets.

| Absolute byte offset | Field | Type / units |
|---:|---|---|
| 0–55 | Legacy lock region | Target ABI layout |
| 56 | buf_size | uint32, scalar int16 sample capacity |
| 60 | buf_index | uint32, scalar int16 write index |
| 64 | running | uint8; padding through 67 |
| 68 | rate | uint32, Hz |
| 72 | updated | int64 legacy timestamp; not continuity evidence |
| 80 | magic | uint32 `0x48555345` |
| 84 | abi_version | uint16, exactly 1 |
| 86 | flags | uint16 reserved |
| 88 | write_seq | uint32, odd while writing; even stable |
| 92 | generation | uint64 producer lifetime ID |
| 100 | abs_write_pos | uint64 exclusive **stereo-frame** position |
| 108 | gap_seq | uint64 export-gap counter |
| 116–119 | padding | 4 bytes |
| 120 | PCM ring | Interleaved signed int16 L/R |

Default ring capacity is 16384 scalar samples = 8192 stereo frames = 32768 bytes;
total mapping size is 32888 bytes. One stereo frame advances abs_write_pos by 1 and
buf_index by 2 modulo scalar capacity. Do not double-add absolute positions or treat
extension bytes at offset 80 as PCM.

## Snapshot, initialization and gaps

The reader does not take the producer pthread lock. The producer's sequence protects
legacy metadata, extension metadata and PCM changes, including stop/silence transitions.
The reader verifies sequence and generation after copying PCM, rejecting torn snapshots.

Initialization may reuse an old segment. `vis_shm_v1_begin_init()` returns void and
forces an odd uint32 successor: even +1, odd +2, modulo 2^32. It runs before legacy
metadata writes. `vis_shm_v1_finish_init()` fills extension state and publishes even;
it returns -1 if secure generation creation fails. `getrandom` or `/dev/urandom`
supplies generation; there is no PID/time fallback. A failed initialization remains
unavailable. Sequence wrap is intentional: 0xffffffff → 1 during init → 2 at completion.

Skipped exports retain producer-local pending gap state when the SHM lock is unavailable.
The next successful write publishes a changed gap_seq. The reader invalidates once for
that observed change, establishes a new baseline, then accepts subsequent clean data.
Absolute position exposes full/multiple laps and overrun; regression, generation change,
rate/restart and gap changes invalidate before new PCM enters the old DSP epoch.

## Production consumer and remap

`SqueezeliteShmStereoSource(require_v1=True)` is the production canonical reader.
Wrong magic, v0 and unsupported versions cannot fall back to legacy PCM. An initial
unsupported producer causes an actionable activation error: rebuild/upgrade it.

Backing-object device/inode change closes the old mmap and establishes pending remap.
Absent or incompletely initialized replacements remain unavailable and are retried.
A rejected ABI belongs to that mapping only: a later inode replacement by valid v1
is detected, revalidated and remapped. The new generation/counters establish a fresh
baseline; StreamInvalidated precedes delivery of its PCM. An unsupported unchanged
mapping remains rejected. No v0/header-as-PCM fallback is permitted under require_v1.

The manager retains this source while any reader is retiring. Do not close/remap it
from a second management path while that worker is active.

## Diagnostics

```sh
xxd /dev/shm/squeezelite-<mac> | head -8
```

At offset 0x50, little-endian magic bytes are `45 53 55 48`; version bytes are
`01 00`. Wrong bytes commonly mean the stock/old executable is still running.
Check process executable, permissions, rate, generation, sequence and gap changes.
Do not use the legacy updated timestamp to prove continuity.

Local tests establish source logic and compiler outputs. Live producer-to-consumer
behavior, target ABI/toolchain compatibility and realtime pacing still require the
LXC validation procedure in [deployment-lxc.md](../docs/deployment-lxc.md).
