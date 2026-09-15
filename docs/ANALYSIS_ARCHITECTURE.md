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
- **v1 (extended)**: Magic `0x48555345` at offset 80 signals extended header. Provides `generation` (producer restart counter), `abs_write_pos` (monotonic 64-bit sample counter), and `gap_flag` (export skipped due to lock failure). Requires squeezelite built with patched vis.c.

Use `read_new_checked()` to obtain `ShmReadResult` with `ShmContinuityEvent` classification.
Map `OVERRUN`, `FULL_LAP`, `MULTIPLE_LAPS`, `RESTART`, `SHM_REPLACED`, `GAP_EXPORT` to `StreamInvalidated` before further PCM enters the same analysis epoch.

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
