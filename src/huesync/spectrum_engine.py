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
# former whole-pipeline V2 class attributes (math preserved by extraction).
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
class SharedAnalysisFrame:
    """Enriched per-canonical-frame context passed to every AnalysisProcessor.

    Replaces the narrower SharedAnalysis that was passed to SpectrumEngine.
    SpectrumProcessor converts this to SharedAnalysis internally so the
    SpectrumEngine protocol remains unchanged.
    """

    pcm: np.ndarray               # shape (n, 2) float32 — canonical stereo
    mag_frames: list[np.ndarray]  # each (n_bins,) float32 — from StereoMagStft
    epoch_id: str                 # current epoch identifier
    sample_start: int             # canonical epoch position of pcm[0]
    sample_end: int               # canonical epoch position past pcm[-1]
    hop_sample_starts: list[int]  # canonical position of the first sample in each mag_frame


@dataclass
class ProcessorUpdate:
    """Partial AudioFeatures contribution from one AnalysisProcessor per hop.

    SpectrumProcessor populates ``bars``; BeatDetector populates onset fields.
    Fields not produced by a processor are left None.
    """

    processor_id: str
    sample_start: int
    sample_end: int
    bars: list[float] | None = None
    onset: bool | None = None
    onset_strength: float | None = None
    onset_bass: bool | None = None
    onset_mid: bool | None = None
    onset_treble: bool | None = None
    onset_bass_strength: float | None = None
    onset_mid_strength: float | None = None
    onset_treble_strength: float | None = None


@dataclass
class PublicationRecord:
    """Atomic publication snapshot from CanonicalAnalysisPipeline."""

    sequence: int
    epoch: str
    sample_pos: int
    features: object            # AudioFeatures — typed as object to avoid circular import
    effective_spectrum_backend: str   # actual Spectrum engine identity
    sample_end: int = 0        # canonical position past the last sample in this publication
    effective_processor_ids: tuple[str, ...] = ()  # IDs of all processors that contributed
    # Historical Spectrum payload, not a fresh contributor to this record.
    carried_spectrum_interval: tuple[int, int] | None = None


# ---------------------------------------------------------------------------
# AnalysisProcessor protocol
# ---------------------------------------------------------------------------


class AnalysisProcessor(Protocol):
    """Contract for a pluggable analysis processor.

    An AnalysisProcessor receives SharedAnalysisFrame per canonical PCM frame
    and returns zero or more ProcessorUpdates.  Multiple processors compose
    independently inside CanonicalAnalysisPipeline: SpectrumProcessor for bars,
    BeatDetector for onset, and extension points for loudness/chroma.

    Implementations must be stateful but NOT thread-safe; CAP calls feed()
    from a single worker thread.
    """

    @property
    def processor_id(self) -> str:
        """Stable string identifier (e.g. 'v2', 'cavacore', 'beat_detector')."""
        ...

    def feed(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]:
        """Process one canonical frame; return zero or more updates."""
        ...

    def flush(self) -> list[ProcessorUpdate]:
        """Flush carry buffer at clean EOS; return final updates."""
        ...

    def reset(self) -> None:
        """Reset all internal state (epoch transition or stream invalidation)."""
        ...

    def close(self) -> None:
        """Release native resources (e.g. cavacore plan allocation)."""
        ...


# ---------------------------------------------------------------------------
# Extension-point protocols (no production DSP; future composition points)
# ---------------------------------------------------------------------------


class LoudnessAnalyzer(Protocol):
    """Extension point for long-term loudness tracking.

    Not implemented in production; reserved for future RMS/LUFS analysis.
    Satisfies AnalysisProcessor structurally.
    """

    @property
    def processor_id(self) -> str: ...
    def feed(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]: ...
    def flush(self) -> list[ProcessorUpdate]: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...


class ChromaAnalyzer(Protocol):
    """Extension point for chroma/key detection.

    Not implemented in production; reserved for future tonal analysis.
    Satisfies AnalysisProcessor structurally.
    """

    @property
    def processor_id(self) -> str: ...
    def feed(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]: ...
    def flush(self) -> list[ProcessorUpdate]: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...


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

    Per-block position ownership (BLOCKER 5)
    ----------------------------------------
    The engine owns ``_next_exec_start`` — the canonical sample-start of the
    next 480-frame block to execute.  ``feed_block(480_frames)`` executes one
    block and returns ``(bars, block_start, block_end)`` where block_start is
    the value of ``_next_exec_start`` before the call.  After the call the
    engine advances the counter by 480.

    The SpectrumEngine ``feed(pcm, shared)`` contract supports synchronous
    callers with arbitrary chunk sizes. The fixed-block production adapter
    should prefer ``feed_block`` so each executed block carries its own
    interval, independent of how many blocks were consumed in one call.
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
        # Canonical sample-start of the NEXT 480-frame block to execute.  The
        # engine advances this in feed_block(); SpectrumProcessor reads it via
        # ``next_exec_start`` when it needs to know the position of a
        # carry-buffered flush block.
        self._next_exec_start: int = 0

    @property
    def engine_id(self) -> str:
        return "cavacore"

    @property
    def block_size(self) -> int:
        """Number of canonical stereo frames per cavacore execution block."""
        return self._BLOCK_SIZE

    @property
    def next_exec_start(self) -> int:
        """Canonical sample-start of the next feed_block execution."""
        return self._next_exec_start

    def feed_block(self, pcm: np.ndarray) -> tuple[np.ndarray, int, int] | None:
        """Execute exactly one 480-frame cavacore block.

        Returns ``(bars, block_start, block_end)`` on success; ``None`` when
        the block did not produce output (e.g. plan/backend edge cases).  The
        caller must supply exactly ``_BLOCK_SIZE`` frames — enforced with an
        assertion so a chunking bug in the caller surfaces immediately.

        The engine tracks its own execution position via ``_next_exec_start``
        so a single call always produces exactly one interval, independent of
        how many blocks the caller previously fed as one big buffer.
        """
        if len(pcm) != self._BLOCK_SIZE:
            raise ValueError(
                f"CavaCoreSpectrumEngine.feed_block requires "
                f"{self._BLOCK_SIZE} frames, got {len(pcm)}"
            )
        result = self._cava.execute(pcm)
        self._epoch_samples += self._BLOCK_SIZE
        block_start = self._next_exec_start
        block_end = block_start + self._BLOCK_SIZE
        self._next_exec_start = block_end
        if result is None:
            return None
        return result, block_start, block_end

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        """SpectrumEngine feed contract with arbitrary transport-sized PCM.

        Prefer ``feed_block`` in new code; it returns per-block intervals so
        the SpectrumProcessor does not have to reconstruct positions from
        the length of the returned bar list (the underlying cavacore
        backend coalesces multiple executed blocks into a single returned
        bar array — the LAST block's bars).

        BLOCKER 7 fix: the returned ``SpectrumUpdate.sample_pos`` is the
        canonical block-start of the executed block, tracked through the
        same ``_next_exec_start`` counter as ``feed_block`` so callers get
        consistent positions across the two entry points.  A call that
        feeds ``M`` frames while the internal carry already held ``P``
        frames executes ``(P + M) // BLOCK_SIZE`` complete blocks; the
        returned update belongs to the LAST of those and its sample_pos is
        computed accordingly (not the pre-fix ``_epoch_samples -
        BLOCK_SIZE`` heuristic, which was off by up to ``BLOCK_SIZE - 1``
        whenever ``len(shared.pcm)`` was not a multiple of the block).
        """
        pending_before = self._cava.pending_frames
        n_in = len(shared.pcm)
        result = self._cava.execute(shared.pcm)
        pending_after = self._cava.pending_frames
        blocks_executed = (pending_before + n_in - pending_after) // self._BLOCK_SIZE
        self._epoch_samples += n_in
        if result is None or blocks_executed <= 0:
            return []
        last_block_start = (
            self._next_exec_start + (blocks_executed - 1) * self._BLOCK_SIZE
        )
        self._next_exec_start += blocks_executed * self._BLOCK_SIZE
        return [
            SpectrumUpdate(
                engine_id="cavacore",
                bars=result.tolist(),
                sample_pos=last_block_start,
            )
        ]

    def flush(self) -> list[SpectrumUpdate]:
        """Legacy flush entry point.

        BLOCKER 7 fix: the returned ``sample_pos`` is the canonical start
        of the block executed by cavacore's flush — which is exactly
        ``_next_exec_start`` (the next scheduled block, whose leading
        ``pending_frames`` samples are the real audio tail; the remainder
        is zero-padding).  The pre-fix heuristic reused
        ``_epoch_samples - BLOCK_SIZE`` and was off by the size of the
        real tail (e.g. 481 fed → tail at frame 480 reported as 1).
        """
        result = self._cava.flush()
        if result is None:
            return []
        block_start = self._next_exec_start
        self._next_exec_start += self._BLOCK_SIZE
        self._epoch_samples += self._BLOCK_SIZE
        return [
            SpectrumUpdate(
                engine_id="cavacore",
                bars=result.tolist(),
                sample_pos=block_start,
            )
        ]

    def flush_block(self, source_end: int | None = None) -> tuple[np.ndarray, int, int] | None:
        """Zero-pad-and-execute the carry buffer at clean EOS.

        Returns ``(bars, block_start, block_end)`` where block_start is the
        canonical position of the carry's first frame, block_end is clamped
        to the source's true end when ``source_end`` is provided (so the
        publication interval does not extend into zero-padding).

        ``None`` is returned when the carry buffer is empty.  This method
        advances ``_next_exec_start`` by exactly one block, so subsequent
        calls after a real epoch reset behave predictably.
        """
        pending = self._cava.pending_frames
        result = self._cava.flush()
        if result is None:
            return None
        block_start = self._next_exec_start
        block_end = block_start + pending if pending > 0 else block_start
        if source_end is not None:
            block_end = min(block_end, source_end)
        # Advance by the full block; the cavacore backend consumed a padded
        # 480-frame execution regardless of ``pending``.
        self._next_exec_start = block_start + self._BLOCK_SIZE
        self._epoch_samples += self._BLOCK_SIZE
        return result, block_start, block_end

    def reset_position(self) -> None:
        """Reset only the canonical position counter (leave DSP state intact)."""
        self._next_exec_start = 0

    def reset(self) -> None:
        self._cava.close()
        self._cava = _CavaCoreBackend(**self._cava_config)
        self._epoch_samples = 0
        self._next_exec_start = 0

    def close(self) -> None:
        self._cava.close()


# ---------------------------------------------------------------------------
# SpectrumProcessor — AnalysisProcessor adapter for SpectrumEngine
# ---------------------------------------------------------------------------


class SpectrumProcessor:
    """Wraps a SpectrumEngine to satisfy the AnalysisProcessor protocol.

    Projects SharedAnalysisFrame into the SpectrumEngine input contract
    with SpectrumEngine.feed(), then converts SpectrumUpdates → ProcessorUpdates.

    This is the canonical spectrum composition point: adding a new SpectrumEngine
    (engine #3) only requires updating the ENGINES registry; SpectrumProcessor
    and CanonicalAnalysisPipeline require no changes.

    Sample-position ownership (items 25-28)
    ---------------------------------------
    The two engine families use different position sources:

    - V2SpectrumEngine consumes the shared STFT ``mag_frames`` at 480-sample
      hop.  Its output positions are the canonical ``hop_sample_starts`` from
      CanonicalAnalysisPipeline (chunk-independent, tied to the shared STFT
      epoch counter).
    - CavaCoreSpectrumEngine has its own internal carry buffer that is
      completely independent of the shared STFT.  Position tracking therefore
      cannot use ``hop_sample_starts`` — we maintain a private
      ``_cava_carry_sample_start`` and ``_cava_carry_len`` inside the
      processor.  Each 480-frame execution block is timestamped at
      ``carry_start + block_offset_in_combined_buffer``.  The shared STFT
      warming up (or being reset) cannot corrupt CAVA positions because
      SpectrumProcessor holds these fields itself.
    """

    _CAVA_BLOCK: int = 480  # matches CavaCoreSpectrumEngine._BLOCK_SIZE

    def __init__(self, engine: SpectrumEngine) -> None:
        self._engine = engine
        self._is_cava = getattr(engine, "engine_id", None) == "cavacore"
        # CAVA-only bookkeeping.  With per-block feed_block(), the canonical
        # block-start is owned by the engine (``_next_exec_start``).  The
        # SpectrumProcessor now only carries the PCM samples that did not
        # fill a full block; the engine's own carry keeps the DSP context.
        # ``_cava_carry`` is the raw canonical PCM prefix that must ride
        # along with the next incoming frame before another 480-frame block
        # is available.  ``_cava_carry_sample_start`` records the canonical
        # position of the first sample in ``_cava_carry`` — used ONLY when
        # feeding EOS zero-padded blocks so the flush interval starts at the
        # real carry-start rather than an interpolated approximation.
        self._cava_carry: np.ndarray | None = None
        self._cava_carry_sample_start: int = 0
        # Track total samples fed since the last reset — used for
        # sanity/warning checks.  Not required by the position math.
        self._cava_total_fed: int = 0

    @property
    def processor_id(self) -> str:
        return self._engine.engine_id

    def _feed_cava(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]:
        """CAVA-specific position tracking (BLOCKER 5 fix).

        The engine owns the canonical execution-start counter and returns one
        interval per 480-frame block via ``feed_block``.  We do the PCM
        blocking here — collect (carry + new) samples, hand them to the
        engine in 480-frame slices, and record each returned interval as an
        independent ProcessorUpdate.  This is invariant under arbitrary
        chunking of the caller's PCM.
        """
        n_new = len(frame.pcm)
        # Combine any prior PCM tail with the new samples.
        if self._cava_carry is not None and len(self._cava_carry) > 0:
            combined = np.concatenate([self._cava_carry, frame.pcm], axis=0)
            combined_start = self._cava_carry_sample_start
        else:
            combined = frame.pcm
            combined_start = frame.sample_start

        result: list[ProcessorUpdate] = []
        pos = 0
        block = self._CAVA_BLOCK
        while pos + block <= len(combined):
            chunk = np.ascontiguousarray(combined[pos : pos + block])
            outcome = self._engine.feed_block(chunk)  # type: ignore[attr-defined]
            if outcome is not None:
                bars_arr, blk_start, blk_end = outcome
                bars_list = (
                    bars_arr.tolist()
                    if hasattr(bars_arr, "tolist")
                    else list(bars_arr)
                )
                result.append(ProcessorUpdate(
                    processor_id=self._engine.engine_id,
                    sample_start=blk_start,
                    sample_end=blk_end,
                    bars=bars_list,
                ))
            pos += block

        # Save the trailing partial block as carry for the next feed().
        if pos < len(combined):
            self._cava_carry = np.ascontiguousarray(combined[pos:])
            self._cava_carry_sample_start = combined_start + pos
        else:
            self._cava_carry = None
            # Carry-start is only meaningful when carry is non-empty; keep
            # the previously-advanced position so a subsequent empty-carry
            # feed uses the incoming frame's start.
            self._cava_carry_sample_start = combined_start + pos

        self._cava_total_fed += n_new
        return result

    def _feed_v2(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]:
        """V2 path: uses shared STFT ``hop_sample_starts`` (chunk-independent)."""
        shared = SharedAnalysis(
            mag_frames=frame.mag_frames,
            pcm=frame.pcm,
            sample_pos=frame.sample_start,
            n_samples=len(frame.pcm),
        )
        updates = self._engine.feed(frame.pcm, shared)
        hop_starts = frame.hop_sample_starts
        result: list[ProcessorUpdate] = []
        for i, u in enumerate(updates):
            if i < len(hop_starts):
                start = hop_starts[i]
            else:
                start = u.sample_pos
            result.append(ProcessorUpdate(
                processor_id=self._engine.engine_id,
                sample_start=start,
                sample_end=start + _STFT_HOP,
                bars=u.bars,
            ))
        return result

    def feed(self, frame: SharedAnalysisFrame) -> list[ProcessorUpdate]:
        if self._is_cava:
            return self._feed_cava(frame)
        return self._feed_v2(frame)

    def flush(self) -> list[ProcessorUpdate]:
        result: list[ProcessorUpdate] = []
        if self._is_cava:
            # Flush the SpectrumProcessor's PCM carry into the engine, then
            # let the engine zero-pad+execute its own remaining carry.  The
            # engine's flush_block returns a per-block interval clamped to
            # the real source end (BLOCKER 5).
            if self._cava_carry is not None and len(self._cava_carry) > 0:
                pad = np.zeros(
                    (self._CAVA_BLOCK - len(self._cava_carry), 2),
                    dtype=self._cava_carry.dtype,
                )
                padded = np.concatenate([self._cava_carry, pad], axis=0)
                outcome = self._engine.feed_block(padded)  # type: ignore[attr-defined]
                if outcome is not None:
                    bars_arr, _blk_start, _blk_end = outcome
                    bars_list = (
                        bars_arr.tolist()
                        if hasattr(bars_arr, "tolist")
                        else list(bars_arr)
                    )
                    # The zero-padded block covers only ``len(carry)`` real
                    # source frames; clamp sample_end accordingly.
                    real_start = self._cava_carry_sample_start
                    real_end = real_start + len(self._cava_carry)
                    result.append(ProcessorUpdate(
                        processor_id=self._engine.engine_id,
                        sample_start=real_start,
                        sample_end=real_end,
                        bars=bars_list,
                    ))
                self._cava_carry = None
                return result
            # No SpectrumProcessor-level carry — engine may still have its
            # own partial carry (SpectrumEngine feed contract).  Delegate to flush().
            updates = self._engine.flush()
            for u in updates:
                start = u.sample_pos
                result.append(ProcessorUpdate(
                    processor_id=self._engine.engine_id,
                    sample_start=start,
                    sample_end=start,
                    bars=u.bars,
                ))
            return result
        updates = self._engine.flush()
        return [
            ProcessorUpdate(
                processor_id=self._engine.engine_id,
                sample_start=u.sample_pos,
                sample_end=u.sample_pos + _STFT_HOP,
                bars=u.bars,
            )
            for u in updates
        ]

    def reset(self) -> None:
        self._engine.reset()
        self._cava_carry = None
        self._cava_carry_sample_start = 0
        self._cava_total_fed = 0

    def close(self) -> None:
        self._engine.close()


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
