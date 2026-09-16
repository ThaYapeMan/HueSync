# PCM, clocks, lifecycle and publications

## Source and canonical frames

Production stereo adapters return `SourceReadResult`: decoded data, temporarily
no data, clean EOS or invalidation. DecodedSourceFrame contains complete float32
mono/stereo frames at the source rate; the source performs integer decoding and
channel alignment. Published arrays are owned/read-only.

AudioCanonicalizer produces 48 kHz, two-channel float32 AnalysisPcmFrame objects,
nominally in [-1,1]. Mono is duplicated; rate conversion uses soxr. It maintains
stream epochs and canonical frame positions, not transport-byte positions or wall
clock timestamps. Invalidation discards uncertain carry. Clean EOS drains valid
resampler output. Source adapters must report continuity loss before post-gap PCM.

The manager opens and ultimately closes physical sources. The canonical worker
reads source events and owns canonical DSP lifecycle. A manager cannot close/reuse
that source until every owned worker terminates.

## SharedAnalysisFrame

Every canonical frame reaches shared STFT and all enabled processors exactly once,
independently of Spectrum readiness. SharedAnalysisFrame supplies PCM, magnitude
frames, epoch, canonical transport interval and `hop_sample_starts` for each STFT
output. These hop positions are chunk-independent. The transport interval is
**not** an instruction to overwrite a ProcessorUpdate's interval.

V2 consumes magnitudes. Beat consumes the same magnitudes with its own algorithm
state. CAVA consumes PCM and retains its own FFTs. Future PCM/magnitude consumers
can use the same frame; the current worker dispatches sequentially.

## Event time versus delivery order

A PublicationRecord binds sequence, epoch, sample_pos (interval start), sample_end,
AudioFeatures, effective engine ID, fresh processor IDs and optional carried
Spectrum interval. Sequence/latest/queue commit under one lock.

Sequence is monotonic delivery order. Audio intervals can arrive out of order:
CAVA [1920,2400) may be available before Beat [0,480). The later Beat record remains
[0,480); it is not discarded, shifted or unioned with a transport interval.
`latest()` is the last delivered record's features, not the greatest event timestamp.

Per dispatch, exact interval keys merge. Spectrum-bearing keys are delivered first,
then other first-seen keys in processor/update order. Equal keys merge regardless of
list position. A delayed result for a previously published interval creates a new
sequence; it does not rewrite earlier records. EOS follows the same policy.

Historical Spectrum bars may accompany a delayed contribution only when their
interval matches exactly, or ends at/before its start. The record explicitly reports
`carried_spectrum_interval`; fresh contributor IDs exclude historical Spectrum.
No future bars are substituted. If the bounded history cannot supply bars, the
record has empty bars but retains its real contribution and interval.

## Bounds and consumer responsibilities

- No pending map waits for slow/silent processors. Every valid returned update is
  handled in the current dispatch.
- Spectrum history retains at most 1000 entries. Eviction removes optional bars,
  not delayed processor results.
- The publication queue retains at most 1000 records. Overflow increments
  `pub_dropped_count`; 1006 undrained publications produce 6 evictions.
- `drain_publications()` returns queued records and resets drop counters. Inspect
  the count before draining; sequence gaps also reveal missed delivery. This is
  best-effort bounded history, not a durable event log.
- `pub_late_dropped_count` is retained for compatibility and remains zero.
- Polling `latest()` may miss intermediate events. Offline acceptance captures the
  lists returned from public feed/EOS calls; consumers must not infer new delivery
  from feature-value equality or assume CSV timestamps are globally sorted.

## Lifecycle

| Event | Behavior |
|---|---|
| Normal PCM | Canonicalize, shared analysis, generic feed, publish |
| TemporarilyNoData | No inserted silence, no reset, terminal/latest remains |
| StreamInvalidated | Discard uncertain carry; reset processors/STFT; clear latest |
| New epoch | Reset old analysis/history; establish new clock; clear old latest |
| Clean EOS | Drain canonical source; flush all processors; publish valid intervals; reset DSP; preserve terminal latest |
| Stop succeeds | Worker joined; processors closed exactly once |
| Stop times out | Return incomplete; worker/source ownership retained; worker closes processors on exit |

CAVA zero-pads a final short native block only. Its publication ends at the valid
source boundary: 481 source frames give [0,480) and [480,481). Canonical source
duration is independent of padding, STFT warmup and publication count.

Retirement status is observational: a completed worker remains tracked until an
explicit stop/replacement joins or reaps it. Merely reading status does not erase
the timeout before the manager handles it.

Manager deactivation can be retried. An incomplete stop raises an explicit error,
retains the session/source, and exposes `analysis_stopping=true`. A new activation
first completes that teardown. Source close and release happen only after termination.
Do not repeatedly call pipeline.start on a retiring analyser.
