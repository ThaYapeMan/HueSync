# HueSync Analysis Architecture — Frozen Design

**Committed:** 2026-09-15
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

### Transactional live analyser replacement

`SyncEngine.replace_analyser(new_analyser)` is transactional:

1. The old analyser is asked to stop; `stop()` returns `False` if the worker did not
   terminate within the timeout.  In that case the replacement is aborted with a
   `RuntimeError` — the old pipeline remains owner and no runtime state is lost.
2. Old processors are closed only after the worker thread has genuinely terminated
   (H1 protocol).  Closing while a `feed()` is in flight would invalidate native
   resources such as the cavacore FFT plan.
3. If `new_analyser.start()` raises, the runtime is left in a degraded state and the
   exception propagates to the caller — the standard recovery path is a full coupling
   deactivate.

### Atomic publication state

`CanonicalAnalysisPipeline._latest_pub` is the sole authoritative publication record.
Both `latest()` and `pub_seq` are derived from it under `_pub_lock`, so consumers
always observe a consistent `(sequence, features, sample_pos)` triple.

The publication queue is bounded (`maxlen=1000`) as a best-effort stream: slow
consumers can lose old records, and `pub_dropped_count` exposes the loss so
downstream can detect overflow instead of silently missing frames.

### Terminal publication lifetime

- `StreamInvalidated`: reset all DSP state and clear `_latest_pub`.
- `TemporarilyNoData`: DO NOT clear `_latest_pub`.  Short source gaps preserve the
  last-known features so effects keep rendering rather than snapping dark.
- `EndOfStream`: flush processor carry buffers and reset DSP.  `_latest_pub`
  persists until the next epoch or a `StreamInvalidated`.
- Epoch transition: reset DSP and clear `_latest_pub` exactly once at the boundary.

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
