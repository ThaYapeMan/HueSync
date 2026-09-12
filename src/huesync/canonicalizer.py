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
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

import numpy as np

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
# AudioCanonicalizer — Phase 1 API boundary stub
# ---------------------------------------------------------------------------


class AudioCanonicalizer:
    """Converts source PCM lifecycle results to canonical 48 kHz stereo frames.

    Receives SourceReadResult from a decoded-source adapter.
    Emits CanonicalReadResult for consumption by PcmAudioPipeline.

    Canonicaliser responsibilities (Phase 2 implementation):
    - Mono → stereo duplication (L=R)
    - Stereo L/R preservation
    - Other-rate → 48 kHz resampling (library selected in Phase 2)
    - Epoch assignment and epoch_id generation
    - Canonical sample_pos tracking (accumulated across chunks; not rounded per chunk)
    - Canonical over_range aggregation (source flag OR resampler overshoot)
    - Lifecycle propagation with exactly one DSP reset per epoch transition

    Phase 1: API boundary stub only.  Implementation in Phase 2.
    """

    def push(self, result: SourceReadResult) -> CanonicalReadResult:
        """Process one source result and return the corresponding canonical result."""
        raise NotImplementedError

    def reset(self) -> None:
        """Reset all internal state (resampler history, epoch tracking)."""
        raise NotImplementedError
