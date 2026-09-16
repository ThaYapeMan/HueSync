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
required — to clone the pinned upstream commit, apply the patch, build, and
install to `/usr/local/bin/squeezelite`.  The manual procedure below is
retained only for developers maintaining a fork.

## Pinned upstream revision

Upstream (ralph-irving/squeezelite) does not publish git tags.  The current
patch is generated against, and verified to apply cleanly to, the following
exact commit:

```
c7c4248ddd70e47dbfeba0bf4a8a7ec08d8a995c   ralph-irving/squeezelite master
```

`scripts/build-squeezelite.sh` records this hash in the `SQUEEZELITE_COMMIT`
variable and refuses to proceed if the checked-out revision does not match.
When bumping upstream, regenerate `output_vis_v1.patch` (see the notes in
that file) and update the hash here and in the build script in a single
commit so they stay synchronised.

## Applying to squeezelite

The recommended path is to run `scripts/build-squeezelite.sh` from the
repository root — it downloads a pinned upstream squeezelite commit,
applies the HueSync producer patch (`output_vis_v1.patch`), builds with
`-DVISEXPORT` (always enabled — the build refuses to install a binary
that omits the visualiser producer objects), and installs the resulting
binary.  No manual copying or patching is required.

If you need to integrate manually (for example when maintaining a fork), the
required steps are:

1. Copy `vis_shm_v1.h` and `output_vis_v1.c` into the squeezelite source
   directory (typically `squeezelite/`).
2. Add `output_vis_v1.c` to `SOURCES_VIS` in the Makefile and ensure
   `-DVISEXPORT` is present in `OPTS` — without it the upstream Makefile
   omits `output_vis.c` (and therefore the HueSync producer) entirely.
3. In `output_vis.c`:
   * `#include "vis_shm_v1.h"` alongside the existing headers.
   * Extend the mmap size to include the 40-byte extension block:
     `size = 80 + 40 + buf_size_bytes` (32888 for the default 16384-scalar
     ring).
   * Add a `vis_shm_v1_ext_t huesync_v1_ext` field immediately after the
     legacy `vis_t` header fields (before the ring `buffer`).
   * In `output_vis_init()` — the SHM initialisation path — publish
     `write_seq` **odd** BEFORE mutating any snapshot field.  Because
     `shm_open(O_CREAT | O_RDWR)` may reuse an existing segment, `mmap`
     does **not** guarantee `write_seq` is zero; a naive `fetch_add(1)`
     could flip an already-odd value to even and publish an
     in-progress init as "stable".  Call `vis_shm_v1_begin_init(ext)`
     (reads the current `write_seq` and stores the smallest odd value
     strictly greater than it) before the legacy `buf_size / running /
     rate` writes, then call `vis_shm_v1_finish_init(ext)` after them
     to populate the extension block and flip `write_seq` even.
     Both return `int`: `finish_init` returns `-1` when both
     `getrandom(2)` and `/dev/urandom` are unavailable — that return
     MUST abort SHM setup, not fall back to a PID- or time-derived
     generation.  `vis_shm_v1_init(ext)` is a convenience wrapper for
     callers that have no legacy writes to interleave.
   * Replace direct writes to `vis->buffer` with the
     `vis_shm_v1_begin_write` / `vis_shm_v1_end_write` pair (or the
     convenience helper `vis_shm_v1_write_samples`).  This applies to
     the normal PCM export path AND to every transition that changes
     the `running` flag: the silence branch inside `_vis_export`
     **and** `vis_stop()` itself must both be wrapped.
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
