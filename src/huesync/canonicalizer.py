"""Audio source and canonicaliser contract types — Phase 1 of the PCM-first architecture.

Defines the type-level contracts for the decoded-source and canonical-PCM layers.
No existing production code imports from this module; all types are additive.

Layer contracts:

    Source adapter → DecodedSourceFrame  (via SourceReadResult)
    AudioCanonicalizer → AnalysisPcmFrame  (via CanonicalReadResult)
    Analyser → OnsetEvent
    Configuration → SpectrumLayout

See docs/audio-architecture-v1.md §§5–9, §11–14 for the authoritative spec.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

import numpy as np
import soxr

# ---------------------------------------------------------------------------
# Invalidation cause
# ---------------------------------------------------------------------------


class InvalidationCause(Enum):
    """Reason a source stream was invalidated."""

    RESTART = "restart"
    RECONNECT = "reconnect"
    SEEK = "seek"
    RATE_CHANGE = "rate_change"
    FORMAT_CHANGE = "format_change"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Decoded source frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecodedSourceFrame:
    """One batch of decoded float32 PCM from a source adapter.

    Source adapters own transport, byte handling, endian conversion, integer
    scaling, and complete-channel-frame construction.  The canonicaliser receives
    only complete DecodedSourceFrame objects — never raw bytes or integer PCM.

    Published samples are read-only (WRITEABLE=False).  Consumers may retain
    this object and access samples without copying (§8 ownership contract).
    """

    samples: np.ndarray  # shape (n_frames, channels), dtype=float32, WRITEABLE=False
    sample_rate: int  # native source rate, e.g. 44100 or 48000
    channels: int  # 1 (mono) or 2 (stereo); multichannel rejected explicitly
    source_id: str  # stable logical source identity, e.g. "lms:aa:bb:cc:dd:ee:ff"
    source_sample_pos: int | None  # source-native position; None when unavailable
    over_range: bool  # any |sample| >= 1.0; diagnostic only — not audible distortion
    wall_ns: int | None  # wall-clock ns at frame capture; informational only

    def __post_init__(self) -> None:
        arr = np.asarray(self.samples)
        if arr.dtype != np.float32:
            raise ValueError(f"samples dtype must be float32; got {arr.dtype}")
        if arr.ndim != 2:
            raise ValueError(f"samples must be 2-D (n_frames, channels); got shape {arr.shape}")
        if self.channels not in (1, 2):
            raise ValueError(f"channels must be 1 or 2; got {self.channels}")
        if arr.shape[1] != self.channels:
            raise ValueError(
                f"samples shape {arr.shape} inconsistent with channels={self.channels}"
            )
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive; got {self.sample_rate}")
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        owned = arr.copy()
        owned.flags.writeable = False
        object.__setattr__(self, "samples", owned)


# ---------------------------------------------------------------------------
# Source lifecycle result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataResult:
    """Successfully decoded PCM frame from the source."""

    frame: DecodedSourceFrame


@dataclass(frozen=True)
class TemporarilyNoData:
    """No audio arrived this poll interval; stream continuity is not broken.

    Do NOT invent silence, advance sample_pos, reset DSP state, or transition epoch.
    The latest AudioFeatures snapshot remains valid.
    """


@dataclass(frozen=True)
class StreamInvalidated:
    """Stream continuity is no longer trustworthy; the current epoch ends.

    DSP state must be reset exactly once per epoch transition.
    The next DataResult begins a new epoch with epoch_id changed and sample_pos=0.
    """

    cause: InvalidationCause
    known_lost_samples: int | None = None  # source-native samples; None = unknown duration

    def __post_init__(self) -> None:
        if self.known_lost_samples is not None and self.known_lost_samples < 0:
            raise ValueError(
                f"known_lost_samples must be non-negative; got {self.known_lost_samples}"
            )


@dataclass(frozen=True)
class EndOfStream:
    """Clean end of stream; no further data for the current epoch."""


SourceReadResult = DataResult | TemporarilyNoData | StreamInvalidated | EndOfStream


# ---------------------------------------------------------------------------
# Analysis PCM frame (canonical)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisPcmFrame:
    """Canonical 48 kHz stereo PCM frame for native analysis.

    Produced by AudioCanonicalizer from any source adapter.  All downstream
    DSP (STFT, BandNormaliser, onset detectors, HPSS, activity tracker) consumes
    this contract; it never sees source-specific formats.

    epoch_id is the primary mechanism for consumers to detect epoch changes and
    reset their DSP state.  sample_pos starts at 0 for every new epoch.

    Published samples are read-only.  Consumers may retain without copying (§8).
    """

    samples: np.ndarray  # shape (n_frames, 2), dtype=float32, WRITEABLE=False, columns=[L, R]
    sample_pos: int  # first canonical stereo-frame index within the current epoch
    epoch_id: str  # opaque identifier; changes on every epoch boundary
    source_id: str  # preserved from DecodedSourceFrame
    over_range: bool  # source over_range OR resampler overshoot produced |value| >= 1.0

    SAMPLE_RATE: ClassVar[int] = 48000
    CHANNELS: ClassVar[int] = 2

    def __post_init__(self) -> None:
        arr = np.asarray(self.samples)
        if arr.dtype != np.float32:
            raise ValueError(f"samples dtype must be float32; got {arr.dtype}")
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(f"samples must have shape (n_frames, 2); got {arr.shape}")
        if self.sample_pos < 0:
            raise ValueError(f"sample_pos must be >= 0; got {self.sample_pos}")
        if not self.epoch_id:
            raise ValueError("epoch_id must not be empty")
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        owned = arr.copy()
        owned.flags.writeable = False
        object.__setattr__(self, "samples", owned)


# ---------------------------------------------------------------------------
# Canonical result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalData:
    """Successfully canonicalised frame, ready for native audio analysis."""

    frame: AnalysisPcmFrame


CanonicalReadResult = CanonicalData | TemporarilyNoData | StreamInvalidated | EndOfStream


# ---------------------------------------------------------------------------
# Feature validity status
# ---------------------------------------------------------------------------


class FeatureStatus(Enum):
    """Validity status for optional audio feature families (activity, HPSS).

    Only feature families whose availability varies independently per path need
    an explicit FeatureStatus field.  Bars, onset, level, etc. are implied valid
    by the existence of the AudioFeatures snapshot.
    """

    VALID = "valid"
    WARMING_UP = "warming_up"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    INVALID = "invalid"


# ---------------------------------------------------------------------------
# Onset event (authoritative transient delivery)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OnsetEvent:
    """Authoritative transient event from the onset detector.

    The OnsetEvent queue is the primary delivery mechanism for transients.
    Snapshot onset fields on AudioFeatures (onset, onset_bass, …) are
    compatibility projections only.

    event_sample_pos is the estimated signal time within the epoch at 48 kHz.
    It may lag the physical transient by up to W hops (~30 ms) due to the
    Dixon peak-picker look-ahead window.
    """

    epoch_id: str
    event_sample_pos: int  # estimated signal time within epoch, at 48 kHz
    strength: float  # calibrated salience [0.0, 1.0]
    bands: frozenset[str]  # e.g. frozenset({"bass", "mid"}) for multiband onsets

    def __post_init__(self) -> None:
        if not self.epoch_id:
            raise ValueError("epoch_id must not be empty")
        if self.event_sample_pos < 0:
            raise ValueError(f"event_sample_pos must be >= 0; got {self.event_sample_pos}")
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError(f"strength must be in [0.0, 1.0]; got {self.strength}")


# ---------------------------------------------------------------------------
# Spectrum layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpectrumLayout:
    """Immutable spectrum configuration identity.

    Equivalent audio with equivalent SpectrumLayout produces equivalent spectrum
    features.  Different configurations may produce different bar values.

    Logarithmic bar mapping: bar i covers
        [10^(lo + i/N*(hi-lo)), 10^(lo + (i+1)/N*(hi-lo))]
    where lo = log10(lower_cutoff_hz), hi = log10(upper_cutoff_hz).

    Band indices are exclusive upper bounds: bass = [0, bass_bar_index()),
    mid = [bass_bar_index(), mid_bar_index()), high = [mid_bar_index(), bar_count).
    Empty bands are legal and evaluate to 0.0.
    """

    bar_count: int = 30
    lower_cutoff_hz: float = 50.0
    upper_cutoff_hz: float = 12000.0
    bass_boundary_hz: float = 250.0
    mid_boundary_hz: float = 2000.0

    def __post_init__(self) -> None:
        if self.bar_count <= 0:
            raise ValueError(f"bar_count must be > 0; got {self.bar_count}")
        if self.lower_cutoff_hz <= 0:
            raise ValueError(f"lower_cutoff_hz must be > 0; got {self.lower_cutoff_hz}")
        if self.upper_cutoff_hz <= self.lower_cutoff_hz:
            raise ValueError(
                f"upper_cutoff_hz ({self.upper_cutoff_hz}) must be > "
                f"lower_cutoff_hz ({self.lower_cutoff_hz})"
            )
        if not (self.lower_cutoff_hz <= self.bass_boundary_hz <= self.upper_cutoff_hz):
            raise ValueError(
                f"bass_boundary_hz ({self.bass_boundary_hz}) must be within "
                f"[{self.lower_cutoff_hz}, {self.upper_cutoff_hz}]"
            )
        if not (self.lower_cutoff_hz <= self.mid_boundary_hz <= self.upper_cutoff_hz):
            raise ValueError(
                f"mid_boundary_hz ({self.mid_boundary_hz}) must be within "
                f"[{self.lower_cutoff_hz}, {self.upper_cutoff_hz}]"
            )
        if self.bass_boundary_hz > self.mid_boundary_hz:
            raise ValueError(
                f"bass_boundary_hz ({self.bass_boundary_hz}) must be <= "
                f"mid_boundary_hz ({self.mid_boundary_hz})"
            )

    def hz_to_bar_frac(self, hz: float) -> float:
        """Log-fraction of hz within [lower_cutoff_hz, upper_cutoff_hz].

        Returns 0.0 at lower_cutoff_hz, 1.0 at upper_cutoff_hz.
        Not clamped: Hz outside layout range yields fractions outside [0, 1].
        """
        lo = math.log10(self.lower_cutoff_hz)
        hi = math.log10(self.upper_cutoff_hz)
        return (math.log10(hz) - lo) / (hi - lo)

    def bass_bar_index(self) -> int:
        """Exclusive upper bar index for the bass band.

        Uses round() as rounding rule.  Result clamped to [0, bar_count].
        """
        idx = round(self.hz_to_bar_frac(self.bass_boundary_hz) * self.bar_count)
        return max(0, min(idx, self.bar_count))

    def mid_bar_index(self) -> int:
        """Exclusive upper bar index for the mid band.

        Uses round() as rounding rule.  Result clamped to [0, bar_count].
        """
        idx = round(self.hz_to_bar_frac(self.mid_boundary_hz) * self.bar_count)
        return max(0, min(idx, self.bar_count))


# ---------------------------------------------------------------------------
# Stereo conversion helper
# ---------------------------------------------------------------------------


def _to_stereo(samples: np.ndarray) -> np.ndarray:
    """Return a (n, 2) float32 view/copy: stereo passes through; mono duplicates to L=R.

    Never performs (L+R)/2.  Mono input produces identical L and R channels.
    Input must have shape (n, 1) or (n, 2) and dtype float32.
    """
    if samples.shape[1] == 2:
        return samples
    col = samples[:, 0]
    return np.column_stack([col, col])


# ---------------------------------------------------------------------------
# AudioCanonicalizer — Phase 2 implementation
# ---------------------------------------------------------------------------


class AudioCanonicalizer:
    """Converts source PCM lifecycle results to canonical 48 kHz stereo frames.

    Receives ``SourceReadResult`` from a decoded-source adapter.
    Returns a list of ``CanonicalReadResult`` — zero or more items per call.
    An empty list means the resampler buffered the input but has not yet
    produced output; call again with the next source result.

    Responsibilities:
    - Mono → stereo duplication (L=R)
    - Stereo L/R preservation; never performs (L+R)/2
    - Other-rate → 48 kHz resampling via soxr (stateful, chunk-independent)
    - Epoch assignment and epoch_id generation (uuid4)
    - Epoch-local canonical sample_pos derived from actual emitted frames only
    - Canonical over_range aggregation: source flag OR resampler overshoot
    - Lifecycle propagation: exactly one DSP reset per epoch transition
    - Clean EOS drain of valid resampler tail before EndOfStream
    - Unexpected rate-change detection: synthetic StreamInvalidated

    Does NOT:
    - Decode bytes or integers
    - Perform AGC, loudness normalisation, or peak normalisation
    - Downmix stereo
    - Run any spectral analysis
    """

    TARGET_RATE: ClassVar[int] = 48000
    _SOXR_QUALITY: ClassVar[str] = "HQ"

    def __init__(self, quality: str = "HQ") -> None:
        self._quality = quality
        # Resampler (soxr.ResampleStream); None when no active epoch.
        self._stream: soxr.ResampleStream | None = None
        # Source rate of the current epoch's resampler.
        self._current_rate: int | None = None
        # source_id of the current epoch.
        self._current_source_id: str | None = None
        # Epoch identity assigned when the epoch starts; committed to _epoch_id
        # on the first canonical output frame.  Pending = epoch started but no
        # output emitted yet (resampler still accumulating).
        self._pending_epoch_id: str | None = None
        # Committed epoch id — None until the first CanonicalData is emitted.
        self._epoch_id: str | None = None
        # Canonical sample_pos for the next AnalysisPcmFrame.
        self._sample_pos: int = 0
        # Accumulated source over_range from DataResults since the last output.
        self._over_range_acc: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, result: SourceReadResult) -> list[CanonicalReadResult]:
        """Process one source result; return zero or more canonical results.

        Empty list: resampler buffered the input, no output yet.
        Caller must continue feeding source results to advance the epoch.
        """
        if isinstance(result, DataResult):
            return self._process_data(result.frame)
        if isinstance(result, TemporarilyNoData):
            return [TemporarilyNoData()]
        if isinstance(result, StreamInvalidated):
            # Terminate current epoch without draining resampler tail.
            self._invalidate()
            return [result]
        if isinstance(result, EndOfStream):
            return self._drain_and_end()
        return []  # unreachable; satisfies type checker

    def reset(self) -> None:
        """Reset all internal state: resampler history, epoch tracking."""
        self._invalidate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _invalidate(self) -> None:
        """Discard resampler state and epoch identity.  No tail drain."""
        if self._stream is not None:
            self._stream.clear()
            self._stream = None
        self._current_rate = None
        self._current_source_id = None
        self._pending_epoch_id = None
        self._epoch_id = None
        self._sample_pos = 0
        self._over_range_acc = False

    def _start_epoch(self, source_rate: int, source_id: str) -> None:
        """Initialise resampler and pending epoch identity for a new epoch."""
        self._current_rate = source_rate
        self._current_source_id = source_id
        self._pending_epoch_id = str(uuid.uuid4())
        self._epoch_id = None
        self._sample_pos = 0
        self._over_range_acc = False
        self._stream = soxr.ResampleStream(
            source_rate,
            self.TARGET_RATE,
            2,
            quality=self._quality,
            dtype="float32",
        )

    def _process_data(self, frame: DecodedSourceFrame) -> list[CanonicalReadResult]:
        results: list[CanonicalReadResult] = []

        # Unexpected rate change between consecutive DataResults: generate a
        # synthetic invalidation, then process the new frame as a new epoch.
        if self._current_rate is not None and self._current_rate != frame.sample_rate:
            self._invalidate()
            results.append(
                StreamInvalidated(cause=InvalidationCause.RATE_CHANGE, known_lost_samples=None)
            )

        # First DataResult (or first after reset/invalidation): start a new epoch.
        if self._stream is None:
            self._start_epoch(frame.sample_rate, frame.source_id)

        # Accumulate source over_range before the resampler produces output.
        self._over_range_acc = self._over_range_acc or frame.over_range

        # Expand mono → stereo (L=R); stereo preserved unchanged.
        stereo = _to_stereo(frame.samples)

        # Feed to soxr.  Output may be empty if the filter hasn't filled yet.
        canonical: np.ndarray = self._stream.resample_chunk(stereo, last=False)

        if len(canonical) == 0:
            # Epoch identity remains pending.  Return any synthetic invalidation
            # that was prepended, but no CanonicalData yet.
            return results

        # Safety: non-finite canonical samples must not contaminate DSP state.
        if not np.all(np.isfinite(canonical)):
            self._invalidate()
            results.append(
                StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)
            )
            return results

        # Commit epoch on first canonical output.
        if self._epoch_id is None:
            self._epoch_id = self._pending_epoch_id

        # Aggregate over_range: accumulated source flags + resampler overshoot.
        resampler_over = bool(np.any(np.abs(canonical) >= 1.0))
        over_range = self._over_range_acc or resampler_over
        self._over_range_acc = False

        af = AnalysisPcmFrame(
            samples=canonical,
            sample_pos=self._sample_pos,
            epoch_id=self._epoch_id,
            source_id=self._current_source_id,
            over_range=over_range,
        )
        self._sample_pos += len(canonical)
        results.append(CanonicalData(frame=af))
        return results

    def _drain_and_end(self) -> list[CanonicalReadResult]:
        """Flush valid resampler tail, then emit EndOfStream."""
        results: list[CanonicalReadResult] = []
        epoch_for_drain = self._epoch_id or self._pending_epoch_id

        if self._stream is not None and epoch_for_drain is not None:
            # Flush soxr internal filter buffer.
            tail: np.ndarray = self._stream.resample_chunk(
                np.zeros((0, 2), dtype=np.float32), last=True
            )

            if len(tail) > 0:
                if not np.all(np.isfinite(tail)):
                    # Discard non-finite drain rather than poisoning state.
                    tail = np.zeros_like(tail)

                # Commit pending epoch if this is the very first output.
                if self._epoch_id is None:
                    self._epoch_id = epoch_for_drain
                    self._sample_pos = 0

                over_range = self._over_range_acc or bool(np.any(np.abs(tail) >= 1.0))
                self._over_range_acc = False
                drain_f = AnalysisPcmFrame(
                    samples=tail,
                    sample_pos=self._sample_pos,
                    epoch_id=self._epoch_id,
                    source_id=self._current_source_id,
                    over_range=over_range,
                )
                self._sample_pos += len(tail)
                results.append(CanonicalData(frame=drain_f))

        self._invalidate()
        results.append(EndOfStream())
        return results
