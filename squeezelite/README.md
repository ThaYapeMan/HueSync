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

Both files target upstream squeezelite's `output_vis.c` — copy them into the
squeezelite source tree and adjust the build system to compile
`output_vis_v1.c` alongside the existing vis code.

## Applying to squeezelite

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

The bytes at offset 80 (decimal) should read `48 55 53 45` (`'HUSE'`) in
little-endian order.  If those bytes are zero, the patched producer is not
active — check that the rebuilt squeezelite binary is running rather than
the packaged one.
