# HueSync Analysis Architecture — Frozen Design

**Committed:** 2026-09-15
**Revised:** 2026-09-16 (batches 1-3 final correction round)
**Status:** FROZEN — do not redesign without explicit review
**Primary commit:** analysis-arch-freeze

## Pipeline

```
CanonicalAnalysisPipeline (CAP)
  │
  ├─ source (PcmSource) ──── AudioCanonicalizer
  │                                │
  │                         AnalysisPcmFrame (48 kHz stereo float32)
  │                                │
  ├─ StereoMagStft ─── shared mag_frames (one STFT pass)
  │                                │
  │                         SharedAnalysisFrame
  │                                │
  ├─ _processors: tuple[AnalysisProcessor, ...]
  │     │
  │     ├─ SpectrumProcessor ── SpectrumEngine ── ProcessorUpdate(bars)
  │     ├─ BeatDetector ──────── onset pipelines ─ ProcessorUpdate(onset)
  │     ├─ (future) LoudnessAnalyzer stub
  │     └─ (future) ChromaAnalyzer stub
  │                                │
  │                         list[ProcessorUpdate] per processor
  │                                │
  ├─ aggregation: bars → AudioFeatures; onset → AudioFeatures
  │                                │
  ├─ PublicationRecord (sequence, epoch, sample_pos, sample_end, features,
  │                     effective_engine_id, effective_processor_ids)
  │                                │
  └─ SyncEngine ── BandNormaliser ── LayerMixer ── Scene ── Output
```

## Invariants

### Feed/reset/flush/close orchestration is GENERIC

`_process_canonical_frame()` and `_flush_engine()` iterate `self._processors` generically.
Every processor in the tuple receives `feed()`, `flush()`, `reset()`, and `close()` without
special-casing.

Processor-specific aggregation (bars vs. onset) is determined by `ProcessorUpdate.bars is not None`.

`PublicationRecord.effective_processor_ids` lists ONLY the processors that produced at
least one `ProcessorUpdate` on the current frame.  A processor whose carry buffer is
still warming up (no output yet) does not appear in the effective ids — this makes the
metadata a truthful audit of what actually contributed to the record, not a static
enumeration of what could contribute in principle.

### Publication is NOT gated on Spectrum bars

`_process_canonical_frame()` emits a `PublicationRecord` whenever ANY processor
produced output — Beat-only and future Loudness-only publications are first-class.
When the `SpectrumProcessor` produced no updates in this call but the record still
publishes, the pipeline reuses the last known bars via the private `_last_bars`
carry-forward field.  `SpectrumProcessor`'s ID is NOT in `effective_processor_ids`
for a carry-forward publication — `_last_bars` is a payload-completeness helper,
not a contribution.

### Interval merging is simple, not a DAG

Every `ProcessorUpdate` returned in one `_process_canonical_frame()` call
describes the same canonical interval `[frame.sample_pos, frame.sample_pos + N)`.
Multiple SpectrumProcessor updates (V2: N mag_frames → N updates) create N
publications; onset updates accompany them 1:1 with a drain on the last one.
A single carry-forward publication is emitted for the entire canonical interval
when Spectrum produced no updates but another processor did.  Out-of-order
`sample_start` values from a processor produce a warning (not a crash) so a
future engine cannot tear the pipeline down through misbookkeeping.

### One STFT pass, shared

`StereoMagStft` runs once per `_process_canonical_frame()` call. The resulting
`mag_frames` are embedded in `SharedAnalysisFrame` and passed to all processors.
No processor re-computes the FFT.

### Processor families

| Family | Current implementation | Extension point |
|---|---|---|
| Spectrum | `SpectrumProcessor` wrapping `V2SpectrumEngine` or `CavaCoreSpectrumEngine` | Replace engine via `make_spectrum_engine()` |
| Beat | `BeatDetector` with `StftOnsetPipeline`, `MultibandStftPipeline`, `SuperfluxStftPipeline` | Call `BeatDetector.rebuild()` live |
| Loudness | Protocol stub only | Add `LoudnessAnalyzer` to `_processors` |
| Chroma | Protocol stub only | Add `ChromaAnalyzer` to `_processors` |

### Algorithms inside a family are replaceable

`SpectrumEngine` is a protocol; `make_spectrum_engine("v2")` or `("cavacore")` selects
the implementation. Adding a new engine requires only:
1. Implement `SpectrumEngine` protocol
2. Register in `ENGINES` dict in `spectrum_engine.py`
3. `make_spectrum_engine()` is the sole factory — no other code changes

`BeatDetector.rebuild()` replaces the active onset pipeline live without restarting
the analysis thread or the Hue session.

### V2 and CAVA share StereoMagStft

Both `V2SpectrumEngine` and the `BeatDetector` consume `mag_frames` from the
shared `StereoMagStft`. CavaCore has its own internal FFT (different window size)
but still receives `mag_frames` via `SharedAnalysisFrame` for future use.

### CAVA sample positions are independent of the shared STFT

`SpectrumProcessor` maintains its own `_cava_carry_sample_start` and
`_cava_carry_len` fields for the CAVA engine.  Every 480-frame execution block
is timestamped at `carry_sample_start + block_index * 480`.  This is the
authoritative CAVA position source — `hop_sample_starts` from the shared STFT
is NOT consulted for CAVA blocks, so a shared-STFT warmup or reset can never
corrupt CAVA's block timestamps.  V2, in contrast, consumes shared mag_frames
directly and uses `hop_sample_starts` for its update positions.

At CAVA EOS (`flush()`), the emitted `sample_end` is clamped to
`carry_sample_start + carry_len` — the real source extent, not the zero-padded
480-frame extent.

### Legacy CAVA FIFO is separate

The CAVA FIFO path (`CavaPipeline`) remains as-is for `bars_source='cava'` sessions.
It is independent of `CanonicalAnalysisPipeline` and does not share code or state.

### No generic DSP DAG or plugin framework

This architecture is not a generic plugin system. Feed dispatch iterates a fixed
tuple of processors. Processors are added at construction time or by replacing
the `_processors` tuple at the instance level. No dynamic discovery, no reflection,
no dependency graph.

## Extension rules

### Adding a new spectrum engine

1. Implement `SpectrumEngine` in `spectrum_engine.py`
2. Add entry to `ENGINES` in `spectrum_engine.py`
3. No changes to `CanonicalAnalysisPipeline`

### Adding a new processor (e.g. real LoudnessAnalyzer)

1. Implement `AnalysisProcessor` protocol
2. In `player_manager.py`, add the new processor to the `_processors` tuple when constructing `CanonicalAnalysisPipeline`
3. Update `_build_features()` in `sync_engine.py` to aggregate the new processor's `ProcessorUpdate` fields into `AudioFeatures`
4. Note: `_process_canonical_frame()` automatically dispatches `feed()` to all processors in the tuple

### Adding a new audio source

Implement `PcmSource` protocol in `pcm_source.py`. No changes to `CanonicalAnalysisPipeline`.

## SHM continuity contract

`SqueezeliteShmSource` supports two ABI modes:

- **v0 (legacy)**: `buf_index` wraps at 16384; `updated` is second-resolution. Full-lap aliasing possible (undetectable without producer changes). Overrun detection: `n_new > 8192`.
- **v1 (extended)**: Magic `0x48555345` at offset 80 signals extended header. Provides
  `generation` (random per-process ID), `abs_write_pos` (monotonic stereo-frame counter),
  `gap_seq` (monotonic count of skipped exports) and `write_seq` (seqlock counter for
  coherent snapshotting).  Requires squeezelite built with the HueSync producer patch —
  see `squeezelite/vis_shm_v1.h` and `squeezelite/output_vis_v1.c`.

`abs_write_pos` is measured in STEREO FRAMES (one frame = 2 * int16 = 4 bytes) so it
maps directly onto `DecodedSourceFrame` positions downstream.

Use `read_new_checked()` on `SqueezeliteShmSource` (mono) or `read()` on
`SqueezeliteShmStereoSource` (stereo).  Both classify continuity via the enum
`ShmContinuityEvent`:

- `ADVANCE` — normal forward progress.
- `NO_DATA` — no advance since last poll.
- `TORN_READ` — seqlock coherent-snapshot retries exhausted or the PCM copy raced.
- `OVERRUN` — v0 heuristic: fell more than half the ring behind.
- `FULL_LAP` / `MULTIPLE_LAPS` — v1: `abs_write_pos` delta equals / exceeds one full lap.
- `RESTART` — v1 generation change, or a rate/running transition (v0 and v1).
- `SHM_REPLACED` — legacy `buf_size` field changed.
- `GAP_SEQUENCE` — v1: producer's `gap_seq` incremented (skipped export).  Reported
  once per unique sequence value.
- `UNSUPPORTED_ABI` — reserved for v0 encountered where v1 was required.

Consumers must map every non-`ADVANCE`, non-`NO_DATA` event to `StreamInvalidated`
before further PCM enters the same analysis epoch.

### v1 is mandatory for canonical LMS PCM

`SqueezeliteShmStereoSource.open(require_v1=True)` — the default — refuses to activate
against a stock squeezelite that only exposes v0.  This is the production canonical
LMS path (`bars_source='pcm_pipeline'`).  Legacy code paths that still need v0 access
must pass `require_v1=False` explicitly.

Build/deployment: run `scripts/build-squeezelite.sh` on the LXC to compile and
install the patched squeezelite binary (requires `libfftw3-dev`, not `libfftw3-3`).
The script is idempotent and safe to re-run.  Producer sources live under
`squeezelite/` and are versioned; there are no uncommitted required files.

### Coherent snapshot contract

A full v1 read requires a coherent view of:

1. `write_seq` (seqlock counter, even == stable)
2. `generation` (per-process random ID)
3. All extension fields (`abs_write_pos`, `gap_seq`, `write_seq2`, `gen2`)
4. PCM bytes at the current `buf_index`
5. Re-read `write_seq2` and `generation2`

If either the `write_seq` mismatches or `generation` != `generation2`, the
snapshot is torn and the reader retries.  When retries are exhausted the
event is `TORN_READ` and the epoch invalidates.

### Transactional live analyser replacement

`SyncEngine.replace_analyser(new_analyser)` is transactional in the ordinary case:

1. The old analyser is asked to stop.  `stop()` returns `False` if the worker did
   not terminate within its 2-second timeout.
2. **On timeout the old analyser is marked RETIRING**: it stays as
   `self._analyser`, is added to `self._retiring`, and the (unused) candidate is
   closed exactly once.  No rebuilt analyser is started — spawning one would
   create a second concurrent reader on the same PCM source, which has no
   concurrent-reader contract.  `retirement_pending` becomes `True` and the
   caller must deactivate so `SyncEngine.stop()` can join the retiring worker
   as part of session teardown (the retiring worker's `finally` block closes
   its processors exactly once, guarded by `_processors_closed`).
3. Old processors are closed only after the worker thread has genuinely
   terminated (H1 protocol).  Closing while a `feed()` is still in flight would
   invalidate native resources such as the cavacore FFT plan.
4. If `new_analyser.start()` raises during a clean-stop path,
   `replace_analyser` attempts to rebuild+restart the old analyser via the
   caller-supplied `rebuild_old` callback.  If that also fails,
   `self._analyser` is left pointing at the cleanly-stopped old pipeline
   and the caller must deactivate.  The pipeline does NOT silently install
   a deactivated sentinel — that would let manager/session drift out of sync
   with the runtime.

### Cross-call publication ordering

Within one `feed()` call, ProcessorUpdates are grouped by exact
`(sample_start, sample_end)` and emitted in ascending `(start, end)` order.
Across `feed()` calls the publication stream is strictly monotonic on
`sample_start`: any ProcessorUpdate whose `sample_start` is less than the
`sample_start` of the most recently emitted record is dropped and counted in
`pub_late_dropped_count`.  This applies uniformly at EOS.

A publication may only carry `_last_bars` if the recorded `_last_bars_end`
is `<=` the publication's own `sample_start`.  Historical/carry-forward bars
are permitted; **future bars from a later spectrum interval never leak into
an earlier feature record.**

### Atomic publication state

`CanonicalAnalysisPipeline._latest_pub` is the sole authoritative publication record.
Both `latest()` and `pub_seq` are derived from it under `_pub_lock`, so consumers
always observe a consistent `(sequence, features, sample_pos)` triple.

The single `_pub_lock` covers all three publication fields:
`_latest_pub`, `_pub_queue`, and `_pub_seq`.  A publication is committed inside
one critical section — a reader can never observe `pub_seq` incremented
without the corresponding record already being in `_latest_pub` and appended
to `_pub_queue`.

The publication queue is bounded (`maxlen=1000`) as a best-effort stream: slow
consumers can lose old records, and `pub_dropped_count` exposes the loss so
downstream can detect overflow instead of silently missing frames.
`drain_publications()` empties the queue and resets `pub_dropped_count` atomically
under `_pub_lock`, so a caller draining the stream sees an exact drop count for
the window it just consumed.

### Terminal publication lifetime

- `StreamInvalidated`: reset all DSP state and clear `_latest_pub`.
- `TemporarilyNoData`: DO NOT clear `_latest_pub`.  Short source gaps preserve the
  last-known features so effects keep rendering rather than snapping dark.
- `EndOfStream`: flush processor carry buffers and reset DSP.  `_latest_pub`
  persists until the next epoch or a `StreamInvalidated`.
- Epoch transition: reset DSP and clear `_latest_pub` exactly once at the boundary.

### Acceptance duration reporting

`scripts/run_acceptance.py` exports TWO distinct duration fields to
`<csv>.meta.json`:

- `source_duration_s`: total canonical frames fed to the analyser divided by
  the canonical rate.  Source-authoritative — independent of engine warmup
  and publication cadence.  Never affected by V2 warmup or CAVA EOS padding.
- `analysis_publication_extent_s`: `max(PublicationRecord.sample_end) /
  canonical_rate`.  Reflects what was actually published.  Shortened by V2
  warmup; the CAVA carry-length clamp at EOS keeps it from being inflated by
  zero-padding.

The two values are equal for a pipeline that publishes every fed sample and
diverge exactly by the engine warmup.  Downstream tools use the difference to
detect warmup-related gaps.

### BeatDetector concurrency

`BeatDetector` stores its parameters + pipeline in an immutable `_BeatState`.  `feed()`
captures the state once at the top and reads all subsequent fields from that local —
`rebuild()` may replace the whole state via a single atomic assignment (GIL-safe) at
any point without corrupting the running frame.

## Modification policy

**Do NOT:**
- Collapse these layers back into a monolithic class
- Add engine-specific logic to `CanonicalAnalysisPipeline`
- Make `feed()` dispatch non-generic again
- Move `LightColorCommand` outside `hue_output.py`
- Introduce a generic DSP DAG, plugin framework, or dynamic plugin discovery
- Add a new spectrum engine without registering it in `ENGINES`

**Do:**
- Add new processors to the `_processors` tuple in `player_manager.py`
- Use `make_spectrum_engine()` as the sole engine factory everywhere
- Use `ShmReadResult.event` to classify continuity before passing PCM to the canonicalizer
