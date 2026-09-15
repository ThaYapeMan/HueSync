# HueSync v1 squeezelite producer

This directory contains the producer side of the HueSync v1 visualiser SHM
ABI.  It is the counterpart to `src/huesync/pcm_source.py` which implements
the consumer.  Together they replace the ambiguous legacy v0 layout with a
coherent, monotonic, seqlock-protected view of squeezelite's ring buffer.

## Files

| File | Role |
|---|---|
| `vis_shm_v1.h` | ABI header — layout, constants, and static-asserted offsets. |
| `output_vis_v1.c` | Reference C implementation of the producer half. |
| `output_vis_v1.patch` | Documented diff hooking the producer into upstream `output_vis.c` and the Makefile. |

The producer sources target upstream squeezelite's `output_vis.c`.  Run
`scripts/build-squeezelite.sh` from the repository root — no manual patching
required — to clone a pinned upstream tag, apply the patch, build, and
install to `/usr/local/bin/squeezelite`.  The manual procedure below is
retained only for developers maintaining a fork.

## Applying to squeezelite

The recommended path is to run `scripts/build-squeezelite.sh` from the
repository root — it downloads a pinned upstream squeezelite tag, applies the
HueSync producer patch (`output_vis_v1.patch`), builds, and installs the
resulting binary.  No manual copying or patching is required.

If you need to integrate manually (for example when maintaining a fork), the
required steps are:

1. Copy `vis_shm_v1.h` and `output_vis_v1.c` into the squeezelite source
   directory (typically `squeezelite/`).
2. Add `output_vis_v1.c` to the source list in the Makefile (or its
   equivalent build script).
3. In `output_vis.c`:
   * `#include "vis_shm_v1.h"` alongside the existing headers.
   * Extend the mmap size to include the 40-byte extension block:
     `size = 80 + 40 + buf_size_bytes` (32888 for the default 16384-scalar
     ring).
   * Add a `vis_shm_v1_ext_t *ext` pointer immediately after the legacy
     `vis_t` header.
   * Call `vis_shm_v1_init(ext)` once after the SHM segment is mapped.
     **`vis_shm_v1_init()` returns `int`: 0 on success, -1 when both
     `getrandom(2)` and `/dev/urandom` are unavailable.  A -1 return MUST
     abort SHM setup — do not fall back to a PID- or time-derived
     generation, or restart detection on the consumer will break.**
   * Replace direct writes to `vis->buffer` with the
     `vis_shm_v1_begin_write` / `vis_shm_v1_end_write` pair (or the
     convenience helper `vis_shm_v1_write_samples`).
   * When `pthread_rwlock_trywrlock` fails and the export block is skipped,
     call `vis_shm_v1_record_gap(ext)` — the counter is flushed to SHM at
     the next successful write.

## Compatibility with HueSync

`SqueezeliteShmStereoSource.open()` in the consumer defaults to
`require_v1=True`.  The production canonical LMS PCM path
(`bars_source='pcm_pipeline'`) refuses to activate against a stock
squeezelite that only exposes the v0 layout:

```
RuntimeError: Squeezelite SHM at /dev/shm/squeezelite-<mac> does not provide
the v1 ABI (magic 0x48555345 not found at offset 80).  Rebuild squeezelite
with the HueSync v1 producer patch from squeezelite/output_vis_v1.c and
redeploy.
```

Legacy code paths and tests that still exercise the v0 fall-through pass
`require_v1=False` explicitly.

## Verifying the ABI on a running player

```
xxd /dev/shm/squeezelite-<mac> | head -8
```

The 32-bit magic `0x48555345` is stored little-endian, so the actual byte
sequence in memory at offset 80 (decimal) is `45 53 55 48` — ASCII `E S U H`
(the 'HUSE' characters appear reversed because the least significant byte
sits first).  A `hexdump -C` reader will therefore see:

```
00000050  45 53 55 48 01 00 00 00  ...
          ^^^^^^^^^^^ magic (LE)  ^^^ abi_version=1
```

If those bytes are zero, the patched producer is not active — check that the
rebuilt squeezelite binary is running rather than the packaged one.
