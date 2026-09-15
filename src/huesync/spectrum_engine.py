"""Spectrum engine abstraction — registry and concrete implementations.

Defines the SpectrumEngine protocol, concrete V2 and cavacore implementations,
and the static registry that is the single source of truth for valid engine IDs.

Design contract
---------------
CanonicalAnalysisPipeline owns the source loop, canonicalizer, epoch tracking,
StereoMagStft, and onset detectors.  It delegates spectrum bar computation to a
SpectrumEngine and assembles the resulting SpectrumUpdates into AudioFeatures.

SpectrumEngine implementations must be thread-safe from the caller's perspective:
CanonicalAnalysisPipeline calls feed() from its worker thread; no concurrent
calls are made to the same engine instance.

Adding engine #3
----------------
1. Implement SpectrumEngine (no other interface changes).
2. Add an EngineSpec entry to ENGINES.
3. VALID_ENGINE_IDS updates automatically (frozenset(ENGINES.keys())).
4. models.py, api.py, and CanonicalAnalysisPipeline require no edits.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .pcm_source import WINDOW_SIZE

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V2 engine constants — defined locally to avoid importing from sync_engine.py.
# Values must stay in sync with BandNormaliser.DEFAULT_ATTACK_TAU_S and the
# _V2_* class attributes on PcmAudioPipelineV2.
# ---------------------------------------------------------------------------
_SAMPLE_RATE: int = 48000
_STFT_HOP: int = round(_SAMPLE_RATE * 0.010)    # 480 samples at 48 kHz = 10 ms hop
_V2_NOISE_FLOOR: float = 1e-3
_V2_ATTACK_TAU_S: float = 0.005   # fast attack — matches BandNormaliser.DEFAULT_ATTACK_TAU_S
_V2_RELEASE_TAU_S: float = 1.5    # slow release
_V2_BAR_FALL_TAU_S: float = 0.3   # per-bar falloff time constant


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SpectrumUpdate:
    """One spectrum output from a SpectrumEngine execution."""

    engine_id: str
    bars: list[float]           # normalised bar values in [0.0, 1.0]
    sample_pos: int             # canonical epoch position of the first sample in this update


@dataclass
class SharedAnalysis:
    """Pre-computed analysis shared between CanonicalAnalysisPipeline and the engine.

    V2 engine uses mag_frames (from StereoMagStft; no second FFT).
    Cavacore engine uses pcm directly (its own internal FFT; ignores mag_frames).
    """

    mag_frames: list[np.ndarray]  # each (n_bins,) float32 — from StereoMagStft
    pcm: np.ndarray               # shape (n, 2) float32 — canonical stereo
    sample_pos: int               # canonical epoch position of pcm[0]
    n_samples: int                # == len(pcm)


@dataclass
class PublicationRecord:
    """Atomic publication snapshot from CanonicalAnalysisPipeline."""

    sequence: int
    epoch: str
    sample_pos: int
    features: object            # AudioFeatures — typed as object to avoid circular import
    effective_engine_id: str


# ---------------------------------------------------------------------------
# SpectrumEngine protocol
# ---------------------------------------------------------------------------


class SpectrumEngine(Protocol):
    """Contract for a pluggable spectrum bar engine.

    An engine receives SharedAnalysis per canonical PCM frame and returns
    zero or more SpectrumUpdates.

    V2:        returns one update per STFT mag frame (~100 Hz).
    Cavacore:  returns one update per 480-frame execution block (~100 Hz,
               carry-buffered; may return zero until block is complete).
    """

    @property
    def engine_id(self) -> str:
        """Stable string identifier: 'v2' or 'cavacore'."""
        ...

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        """Process one canonical PCM frame; return zero or more updates.

        Empty list: engine buffered input but is not yet ready to publish.
        The pcm argument equals shared.pcm; provided as a convenience.
        """
        ...

    def flush(self) -> list[SpectrumUpdate]:
        """Flush any buffered state at clean end-of-stream; return final updates."""
        ...

    def reset(self) -> None:
        """Reset all internal state (epoch transition or stream invalidation)."""
        ...

    def close(self) -> None:
        """Release native resources (e.g. cavacore plan allocation)."""
        ...


# ---------------------------------------------------------------------------
# V2SpectrumEngine
# ---------------------------------------------------------------------------


class V2SpectrumEngine:
    """HueSync V2 spectrum engine.

    Receives pre-computed StereoMagStft magnitude frames from SharedAnalysis
    and applies the V2 AGC chain:
      1. Per-bar squelch gate (_V2_NOISE_FLOOR).
      2. Global peak EMA: fast attack (_V2_ATTACK_TAU_S) / slow release
         (_V2_RELEASE_TAU_S) — WLED-style adaptive reference.
      3. Per-bar fast-attack / slow-release falloff (_V2_BAR_FALL_TAU_S).

    No second FFT: mag_frames were computed once by StereoMagStft in
    CanonicalAnalysisPipeline and shared here directly.  This preserves the
    exact V2 DSP without duplication.
    """

    def __init__(
        self,
        n_bars: int,
        lower_hz: float,
        upper_hz: float,
    ) -> None:
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz
        self._v2_peak_ema: float | None = None
        self._bar_smooth: list[float] | None = None
        self._diag_frame: int = 0

    @property
    def engine_id(self) -> str:
        return "v2"

    @property
    def v2_peak_ema(self) -> float | None:
        """Current global peak EMA (diagnostic; None before first frame)."""
        return self._v2_peak_ema

    @property
    def v2_bar_smooth(self) -> list[float] | None:
        """Current per-bar falloff state (diagnostic; None before first frame)."""
        return self._bar_smooth

    def _mag_to_bar_floats(self, mag: np.ndarray) -> list[float]:
        """Map STFT magnitude bins into N log-spaced bars (linear, squelch-gated).

        Uses np.max within each bin range — matches hardware analysers and WLED
        AudioReactive.  np.mean would introduce systematic n_bins× attenuation
        for high-frequency bars where a single tonal bin dominates.
        """
        sr = _SAMPLE_RATE
        n_bins = len(mag)
        log_lo = math.log10(max(self._lower_hz, 1.0))
        log_hi = math.log10(max(self._upper_hz, self._lower_hz + 1.0))
        result: list[float] = []
        for i in range(self._n_bars):
            f_lo = 10.0 ** (log_lo + i / self._n_bars * (log_hi - log_lo))
            f_hi = 10.0 ** (log_lo + (i + 1) / self._n_bars * (log_hi - log_lo))
            bin_lo = max(0, round(f_lo * WINDOW_SIZE / sr))
            bin_hi = min(n_bins, max(bin_lo + 1, round(f_hi * WINDOW_SIZE / sr)))
            val = float(np.max(mag[bin_lo:bin_hi])) if bin_hi > bin_lo else 0.0
            result.append(val if val > _V2_NOISE_FLOOR else 0.0)
        return result

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        updates: list[SpectrumUpdate] = []
        dt = _STFT_HOP / _SAMPLE_RATE
        for i, mag_frame in enumerate(shared.mag_frames):
            bar_mags = self._mag_to_bar_floats(mag_frame)
            peak = max(bar_mags) if bar_mags else 0.0
            if self._v2_peak_ema is None:
                self._v2_peak_ema = max(peak, _V2_NOISE_FLOOR)
            else:
                a = 1.0 - math.exp(
                    -dt / (_V2_ATTACK_TAU_S if peak > self._v2_peak_ema else _V2_RELEASE_TAU_S)
                )
                self._v2_peak_ema += a * (peak - self._v2_peak_ema)
            ref = max(self._v2_peak_ema, _V2_NOISE_FLOOR)
            bars = [min(m / ref, 1.0) for m in bar_mags]
            fall_factor = math.exp(-dt / _V2_BAR_FALL_TAU_S)
            if self._bar_smooth is None:
                self._bar_smooth = list(bars)
            else:
                self._bar_smooth = [
                    max(s * fall_factor, b)
                    for s, b in zip(self._bar_smooth, bars, strict=True)
                ]
            bars = self._bar_smooth

            # TEMPORARY DIAGNOSTIC: log every 50 V2 frames.
            # Remove once mushy-output defect is confirmed fixed on LXC.
            self._diag_frame += 1
            if self._diag_frame % 50 == 0:
                raw_mag_mean = float(np.mean(mag_frame))
                bar_float_max = max(bar_mags) if bar_mags else 0.0
                bars_mean_v = sum(bars) / len(bars) if bars else 0.0
                bars_max_v = max(bars) if bars else 0.0
                log.info(
                    "[v2-engine diag] frame=%d raw_mag_mean=%.4f bar_float_max=%.4f "
                    "peak_ema=%.4f bars_smooth_mean=%.3f bars_smooth_max=%.3f",
                    self._diag_frame, raw_mag_mean, bar_float_max,
                    self._v2_peak_ema, bars_mean_v, bars_max_v,
                )

            updates.append(SpectrumUpdate(
                engine_id="v2",
                bars=list(bars),
                sample_pos=shared.sample_pos + i * _STFT_HOP,
            ))
        return updates

    def flush(self) -> list[SpectrumUpdate]:
        return []  # V2 has no carry buffer; all frames publish immediately

    def reset(self) -> None:
        self._v2_peak_ema = None
        self._bar_smooth = None

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# CavaCoreSpectrumEngine (optional — requires native library)
# ---------------------------------------------------------------------------

try:
    from .cavacore import SCALING_LINEAR as _SCALING_LINEAR
    from .cavacore import CavaCoreBackend as _CavaCoreBackend
    _CAVACORE_IMPORT_OK: bool = True
except Exception:
    _CAVACORE_IMPORT_OK = False


def _check_cavacore_available() -> bool:
    if not _CAVACORE_IMPORT_OK:
        return False
    try:
        from .cavacore import is_cavacore_available
        return is_cavacore_available()
    except Exception:
        return False


class CavaCoreSpectrumEngine:
    """Spectrum engine backed by upstream cavacore.

    Consumes SharedAnalysis.pcm directly (ignores mag_frames).
    Carries a 480-frame buffer internally — cavacore executes once per block.
    Preserves all upstream cavacore defaults without V2 post-processing.

    Only constructable when the native cavacore library is available.
    Use check_available() before constructing.
    """

    _BLOCK_SIZE: int = 480  # cavacore execution block: 480 frames at 48 kHz = 10 ms

    def __init__(
        self,
        n_bars: int,
        lower_hz: float,
        upper_hz: float,
    ) -> None:
        if not _CAVACORE_IMPORT_OK:
            raise RuntimeError("cavacore native library not available")
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz
        self._cava_config: dict = dict(
            n_bars=n_bars,
            rate=_SAMPLE_RATE,
            channels=2,
            autosens=1,
            noise_reduction=0.77,
            low_cut_off=round(lower_hz),
            high_cut_off=round(upper_hz),
            scaling_mode=_SCALING_LINEAR,
        )
        self._cava = _CavaCoreBackend(**self._cava_config)
        self._epoch_samples: int = 0

    @property
    def engine_id(self) -> str:
        return "cavacore"

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        result = self._cava.execute(shared.pcm)
        self._epoch_samples += len(shared.pcm)
        if result is None:
            return []
        pos = max(0, self._epoch_samples - self._BLOCK_SIZE)
        return [SpectrumUpdate(engine_id="cavacore", bars=result.tolist(), sample_pos=pos)]

    def flush(self) -> list[SpectrumUpdate]:
        result = self._cava.flush()
        if result is None:
            return []
        pos = max(0, self._epoch_samples - self._BLOCK_SIZE)
        return [SpectrumUpdate(engine_id="cavacore", bars=result.tolist(), sample_pos=pos)]

    def reset(self) -> None:
        self._cava.close()
        self._cava = _CavaCoreBackend(**self._cava_config)
        self._epoch_samples = 0

    def close(self) -> None:
        self._cava.close()


# ---------------------------------------------------------------------------
# Engine registry
# ---------------------------------------------------------------------------


@dataclass
class EngineSpec:
    """Metadata and factory for one spectrum engine."""

    id: str
    display_name: str
    create: Callable[..., object]           # (n_bars, lower_hz, upper_hz) -> SpectrumEngine
    check_available: Callable[[], bool]


def _check_v2_available() -> bool:
    return True  # pure Python, always available


ENGINES: dict[str, EngineSpec] = {
    "v2": EngineSpec(
        id="v2",
        display_name="HueSync V2",
        create=lambda n, lo, hi: V2SpectrumEngine(n_bars=n, lower_hz=lo, upper_hz=hi),
        check_available=_check_v2_available,
    ),
    "cavacore": EngineSpec(
        id="cavacore",
        display_name="CAVA Core",
        create=lambda n, lo, hi: CavaCoreSpectrumEngine(n_bars=n, lower_hz=lo, upper_hz=hi),
        check_available=_check_cavacore_available,
    ),
}

# Single source of truth for valid engine IDs.  models.py and api.py import
# these rather than maintaining their own copies.
VALID_ENGINE_IDS: frozenset[str] = frozenset(ENGINES.keys())
VALID_BARS_SOURCES: frozenset[str] = frozenset({"pcm_pipeline", "cava"})


def make_spectrum_engine(
    engine_id: str,
    *,
    n_bars: int,
    lower_hz: float,
    upper_hz: float,
) -> SpectrumEngine:
    """Construct a SpectrumEngine by registry ID.

    Raises KeyError for unknown engine_id.
    Raises RuntimeError if the engine is not available (e.g. missing native library).
    """
    if engine_id not in ENGINES:
        raise KeyError(f"Unknown spectrum engine {engine_id!r}; valid: {sorted(VALID_ENGINE_IDS)}")
    spec = ENGINES[engine_id]
    if not spec.check_available():
        raise RuntimeError(
            f"Spectrum engine {engine_id!r} is not available on this system. "
            f"Build the native library first: pip install .  (requires libfftw3-dev)"
        )
    return spec.create(n_bars, lower_hz, upper_hz)  # type: ignore[return-value]
