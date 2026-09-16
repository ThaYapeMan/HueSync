# Frozen analysis architecture

This is the current structural contract. Detailed runtime semantics live in
[audio-pipeline.md](audio-pipeline.md). Historical design prompts do not override it.

```text
Audio ingress → CanonicalAnalysisPipeline → SharedAnalysisFrame
    → AnalysisProcessor[]
        ├─ SpectrumProcessor → SpectrumEngine → V2 / CAVA Core / future
        ├─ BeatDetector → combined / multiband / Superflux onset algorithm
        ├─ LoudnessAnalyzer → future algorithm (extension protocol only)
        └─ ChromaAnalyzer → future algorithm (extension protocol only)
    → PublicationRecord → AudioFeatures → Effects → Scene → output driver
```

## Ownership

| Component | Owns |
|---|---|
| Source adapter | Transport, complete decoded frames, continuity detection and source events |
| PlayerManager | Source open/close, subprocesses and session lifecycle |
| CanonicalAnalysisPipeline | Canonicalizer, shared STFT, processor lifecycle, epoch/sample clocks, publication and worker |
| SharedAnalysisFrame | Read-only canonical PCM plus shared magnitudes and their hop positions |
| AnalysisProcessor | One feature family; zero or more exact-interval contributions |
| SpectrumProcessor | SpectrumEngine integration and block adaptation |
| SpectrumEngine | Spectrum algorithm, state, availability/effective identity |
| BeatDetector | Independent onset algorithm and its synchronized rebuild/reset |
| PublicationRecord | Atomic delivery identity, event interval, features and provenance |
| Effects | Generic AudioFeatures-to-Scene behavior |

Multiple processors can coexist. Dispatch is deterministic and sequential on the
pipeline worker, not concurrent calls to a single processor. `feed`, `flush`,
`reset` and `close` iterate the processor collection. Loudness/Chroma protocols
provide extension points; production algorithms for those families are not shipped.

## Shared analysis and Spectrum DSP

The shared StereoMagStft is 2048 samples, Hamming, with a 480-sample hop at 48 kHz.
V2 and Beat reuse its output. There is no second V2 FFT.

Embedded CAVA consumes canonical PCM directly. Its native path preserves upstream
4096 normal / 8192 bass FFTs, Hann windows, independent channels, band mapping,
compensation, autosensitivity, integral, gravity/fall and noise reduction. HueSync
schedules 480 stereo frames per execution: **100 Hz source-audio cadence**.
Boundary conversion is float64 interleaved PCM ×32768. Conditioned channel bars
are averaged; no second V2 Spectrum conditioning follows CAVA.

## Registry and extension

`ENGINES`, `VALID_ENGINE_IDS` and `make_spectrum_engine()` in `spectrum_engine.py`
are the production engine source of truth. Validation checks IDs without requiring
native loading; activation checks availability. A new Spectrum implementation
requires its implementation, registry entry and tests, not new ingress/Effects
branches. The compatibility wrappers delegate to the shared pipeline.

This is a static internal registry. There is no generic graph, node scheduler,
dynamic discovery or plugin framework. The architecture is frozen.

## Publication and latency

Publication **sequence orders delivery**. Sample intervals describe audio, and
may go backwards in delivery order when a slower processor produces its result.
Valid delayed contributions are never discarded merely to sort timestamps.

Within one dispatch, contributions group only by exact `(sample_start, sample_end)`.
Spectrum intervals are emitted first, then other first-seen intervals; equal keys
merge. Already published records are not mutated. No positional pairing or bounding
union is used. Fresh contributor IDs belong to that record only.

A bounded history of 1000 Spectrum records can provide bars from the exact same
interval, or from a completed interval ending no later than the contribution start.
`carried_spectrum_interval` identifies those historical bars; Spectrum is not added
to fresh contributor IDs. Missing history yields empty bars, not future bars or
lost Beat/Loudness/Chroma updates. No processor waits indefinitely for another.

`latest()` is a polling snapshot, not guaranteed event delivery. Consumers needing
all records use the bounded publication queue and check overflow. See
[audio-pipeline.md](audio-pipeline.md) for EOS, bounds and sequence semantics.

## Retirement and physical source safety

A stop timeout leaves ownership with SyncEngine. The old worker remains in
`_retiring`; unused replacement candidates are closed, and no second reader starts.
The worker closes its processors in `finally` once it exits.

SyncEngine.stop returns False if any owned analyser has not stopped. Manager teardown
checks that result and retirement state **before source close**. It retains the
session, reports a retriable error and marks `analysis_stopping`. A later deactivate
joins completed workers and releases the source; activation begins with this same
teardown gate. No forced source close is used to manufacture a successful join.

Ordinary candidate-start failure rebuilds the old configuration before committing
runtime replacement. Storage/session rollback and operational errors remain separate
from native availability checks. See [api.md](api.md).

## Ingress separation

AirPlay and LMS PCM use the common canonical factory. LMS SHM continuity is a
producer/source contract, not an analysis scheduler responsibility. Exact v1 is
required for production stereo PCM. See [Squeezelite ABI](../squeezelite/README.md).

External CAVA/FIFO supplies derived bars and remains outside the processor/engine
registry. Its normalization and legacy PCM tap must not be copied into CAVA Core.

## Evidence boundary

These are implemented code contracts tested locally. Full target native execution,
SHM deployment, wheel installation, resource stress and visual comparison
**REQUIRE LXC VALIDATION**. This document does not claim those checks have passed.
