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
`latest()` is the freshest live snapshot by `(sample_end, sample_start)` within
an epoch. An exact interval tie prefers the later delivery, so same-interval Beat
can update the live snapshot. A new epoch clears the old snapshot before publishing.
The queue still includes every delivered record, even when a delayed record does
not replace the live snapshot. Sequence/latest/queue are updated under the same
lock, but latest's sequence can be less than the delivery counter. Live Effects
therefore do not replay older intervals merely because they arrived late.

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
- The obsolete `pub_late_dropped_count` API has been removed. Late delivery is
  valid, not a drop; only bounded-queue overflow has drop telemetry.
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

Follower teardown is separately owned: stop the follower once, cancel and await its
task (including connection cleanup), then clear its references. Analysis retirement
retries do not repeat follower cleanup. Errors other than the expected task
cancellation propagate; they are not converted into successful cleanup.

## Independent track metadata channel

Track metadata does not enter PCM, analysis processors, or `AudioFeatures`.
`ActiveSession` owns a separate `TrackPositionSource` (`open`, asynchronous
`close`, nonblocking `read`, and `running`). `PlayerManager` selects the adapter;
WebSocket delivery and the track/progress UI consume the same immutable
`TrackPosition` regardless of player type. Session teardown awaits the metadata
reader before releasing it, including failed activation and retried teardown.

- **LMS:** a dedicated read-only `status - 1 tags:ad subscribe:10` connection
  receives changes and ten-second corrections. `a` requests artist, `d` duration,
  and title is a standard field ([CLI tags](https://lyrion.org/reference/cli/database/#songinfo),
  [subscriptions](https://lyrion.org/reference/cli/compoundqueries/#status)).
  The target comes from the existing follower's `target_mac`: pinned manual
  target or dynamically selected sync-group peer. With no manual follower,
  it queries HueSync's own player. Unsynced auto mode returns no track. The
  adapter checks the local target each second without querying LMS again and
  discards old-target snapshots immediately. It sends no playback/group commands.
- **AirPlay:** the pinned Shairport build enables metadata and writes a separate
  `/run/huesync/airplay.metadata` FIFO, owned by `huesync` with mode `0600`.
  `core/minm` and `core/asar` supply title and artist. `ssnc/prgr` gives
  start/current/end RTP timestamps; differences use unsigned 32-bit wraparound
  and 44,100 frames/second. Configured ten-second `phbt` updates correct position;
  pause/resume/end events control interpolation. See the upstream
  [metadata protocol](https://github.com/mikebrady/shairport-sync-metadata-reader).

Snapshots retain a monotonic position anchor. WebSocket delivery rebases the
position when sending a changed status, so browsers do not need a synchronized
server clock. The UI interpolates locally only while playing and connected,
clamps at known duration, and shows unknown values as `—`. This is track progress,
not a measurement of lighting/audio latency. Metadata availability depends on
what the sender provides; live streams may have no duration. Activating midway
through an AirPlay track may require a subsequent sender metadata update.

Local tests cover CLI subscriptions, dynamic targets, real FIFO reads, RTP wrap,
pause/seek/end, bounded parsing, teardown, and generic UI rendering. Live LMS,
iOS sender behavior, and the rebuilt target Shairport service still require LXC
validation.
