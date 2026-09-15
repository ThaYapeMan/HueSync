"""CavaCoreAudioPipeline — AudioPipeline implementation backed by upstream cavacore.

Architecture
------------
canonical PCM ──┬── StereoMagStft (Hamming, 2048-pt) ──► onset / other features
                └── CavaCoreBackend (CAVA Hann, 4096/8192-pt) ──► spectrum bars

cavacore is invoked directly from canonical PCM.  It manages its own FFT,
band mapping, equalisation, gravity falloff, integral EMA, and autosensitivity.
None of the V2 post-processing (peak EMA, per-bar normalisation, per-bar falloff)
is applied on top of cavacore output.

Differences from PcmAudioPipelineV2 that are structural, not tuning choices
-----------------------------------------------------------------------------
Window:       Hamming (V2) vs. Hann (cavacore)
FFT size:     2048 (V2) vs. 4096/8192 dual (cavacore)
Bin aggr.:    np.max (V2) vs. bandwidth-normalised mean + freq. compensation (cavacore)
Bar smooth:   HueSync per-bar falloff τ=0.3s (V2) vs. CAVA gravity+integral (cavacore)
AGC:          HueSync global peak EMA (V2) vs. CAVA autosens scalar (cavacore)

cavacore configuration (upstream defaults — not tuned to match V2 appearance)
-----------------------------------------------------------------------------
n_bars=30, rate=48000, channels=2, autosens=1, noise_reduction=0.77,
low_cut_off=50, high_cut_off=10000, scaling_mode=SCALING_LINEAR

Upstream provenance
-------------------
github.com/karlstav/cava  commit 6d43df3b2c7882122585c02c064b20009842a6f8  (2026-08-18)
License: MIT — Copyright (c) 2022 Karl Stavestrand <karl@stavestrand.no>
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import numpy as np

from .canonicalizer import (
    AudioCanonicalizer,
    CanonicalData,
    EndOfStream,
    StreamInvalidated,
    TemporarilyNoData,
)
from .cavacore import SCALING_LINEAR, CavaCoreBackend
from .sync_engine import (
    BandNormaliser,
    MultibandStftPipeline,
    StereoMagStft,
    StftOnsetPipeline,
    SuperfluxStftPipeline,
)
from .types import AudioFeatures

if TYPE_CHECKING:
    from .models import Profile

log = logging.getLogger(__name__)

_POLL_S: float = 0.005
_SAMPLE_RATE: int = AudioCanonicalizer.TARGET_RATE  # 48000


def _slice_avg(bars: list[float], start: float, end: float) -> float:
    """Average of a fractional slice of a float bar list (values in 0.0-1.0)."""
    n = len(bars)
    lo = int(start * n)
    hi = max(int(end * n), lo + 1)
    hi = min(hi, n)
    segment = bars[lo:hi]
    return sum(segment) / len(segment) if segment else 0.0


class CavaCoreAudioPipeline:
    """AudioPipeline that feeds canonical stereo PCM to upstream cavacore for spectrum bars.

    Implements the same AudioPipeline protocol as PcmAudioPipelineV2 so it can
    be passed to SyncEngine as a drop-in replacement for A/B comparison.

    Onset detection uses the same StereoMagStft path as V2 (shared STFT,
    push_mag reuse).  Spectrum bars come exclusively from cavacore — no
    HueSync post-processing is stacked on top.

    Not thread-safe: start()/stop()/latest() must be called from a single owner.
    """

    def __init__(
        self,
        source: object,
        bars: int,
        lower_cutoff_freq: int,
        higher_cutoff_freq: int,
        onset_method: str,
        onset_delta: float,
        onset_alpha: float,
        superflux_mu: int,
        superflux_lag: int,
        bass_hz: int,
        mid_hz: int,
        exertion_clip: float = BandNormaliser.DEFAULT_EXERTION_CLIP,
    ) -> None:
        self._source = source
        self._n_bars = bars
        self._onset_method = onset_method
        self._onset_delta = onset_delta
        self._onset_alpha = onset_alpha
        self._superflux_mu = superflux_mu
        self._superflux_lag = superflux_lag
        self._bass_hz = bass_hz
        self._mid_hz = mid_hz

        # cavacore configuration — stored so _reset_dsp() can recreate the backend.
        # CAVA defaults; not tuned to resemble V2.
        self._cava_config = dict(
            n_bars=bars,
            rate=_SAMPLE_RATE,
            channels=2,
            autosens=1,
            noise_reduction=0.77,
            low_cut_off=lower_cutoff_freq,
            high_cut_off=higher_cutoff_freq,
            scaling_mode=SCALING_LINEAR,
        )
        self._cava = CavaCoreBackend(**self._cava_config)

        # StereoMagStft for onset detection only (does NOT feed bar generation).
        self._bar_stft = StereoMagStft(_SAMPLE_RATE)
        self._onset_pipeline: (
            StftOnsetPipeline | MultibandStftPipeline | SuperfluxStftPipeline
        ) = self._build_onset_pipeline()
        # Onset results accumulated between cavacore executions.  Onset STFT runs
        # at the canonical frame rate; cavacore executes every 480 frames.  When
        # canonical chunks are < 480 frames, onset frames may accumulate here before
        # cavacore produces bars.  All are drained at each cavacore publication.
        self._pending_onset: list = []

        self._canonicalizer = AudioCanonicalizer()
        self._current_epoch_id: str | None = None
        self._latest: AudioFeatures | None = None
        self._pub_seq: int = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _build_onset_pipeline(
        self,
    ) -> StftOnsetPipeline | MultibandStftPipeline | SuperfluxStftPipeline:
        sr = _SAMPLE_RATE
        if self._onset_method == "multiband":
            return MultibandStftPipeline(
                sr,
                bass_hz=self._bass_hz,
                mid_hz=self._mid_hz,
                delta=self._onset_delta,
                alpha=self._onset_alpha,
            )
        if self._onset_method == "superflux":
            return SuperfluxStftPipeline(
                sr,
                mu=self._superflux_mu,
                lag=self._superflux_lag,
                delta=self._onset_delta,
                alpha=self._onset_alpha,
            )
        return StftOnsetPipeline(sr, delta=self._onset_delta, alpha=self._onset_alpha)

    def _reset_dsp(self) -> None:
        self._bar_stft.reset()
        self._onset_pipeline = self._build_onset_pipeline()
        self._current_epoch_id = None
        self._pending_onset.clear()
        # Destroy and recreate cavacore backend to flush its rolling buffer,
        # autosens state, gravity, integral, and all internal peak tracking.
        self._cava.close()
        self._cava = CavaCoreBackend(**self._cava_config)

    def _publish_features_from_cava(self, cava_bars: list[float]) -> None:
        """Drain _pending_onset and publish AudioFeatures for the given cava bars."""
        onset_results = list(self._pending_onset)
        self._pending_onset.clear()

        onset, onset_strength = False, 0.0
        onset_bass = onset_mid = onset_treble = False
        onset_bass_str = onset_mid_str = onset_treble_str = 0.0

        if self._onset_method == "multiband":
            for of in onset_results:
                (b_on, b_str), (m_on, m_str), (t_on, t_str) = of
                onset_bass = onset_bass or b_on
                onset_mid = onset_mid or m_on
                onset_treble = onset_treble or t_on
                onset_bass_str = max(onset_bass_str, b_str)
                onset_mid_str = max(onset_mid_str, m_str)
                onset_treble_str = max(onset_treble_str, t_str)
            onset = onset_bass or onset_mid or onset_treble
            onset_strength = max(onset_bass_str, onset_mid_str, onset_treble_str)
        else:
            for on, st in onset_results:
                onset = onset or on
                onset_strength = max(onset_strength, st)

        total = sum(cava_bars)
        full = _slice_avg(cava_bars, 0.0, 1.0)
        with self._lock:
            self._latest = AudioFeatures(
                bars=cava_bars,
                bass=_slice_avg(cava_bars, 0.0, 0.20),
                mid=_slice_avg(cava_bars, 0.0, 0.55),
                full=full,
                centroid=(
                    sum(idx * v for idx, v in enumerate(cava_bars)) / total / len(cava_bars)
                    if total > 1e-9 else 0.0
                ),
                onset=onset,
                onset_strength=onset_strength,
                onset_bass=onset_bass,
                onset_mid=onset_mid,
                onset_treble=onset_treble,
                onset_bass_strength=onset_bass_str,
                onset_mid_strength=onset_mid_str,
                onset_treble_strength=onset_treble_str,
                sustained_energy=None,
                hpss_active=False,
                relative_exertion=full,
            )
            self._pub_seq += 1

    def _flush_eos_tail(self) -> None:
        """Process pending cavacore frames at clean EOS by zero-padding to block boundary.

        Clean EOS (source disconnected normally): pending frames in the cavacore
        carry-buffer are valid audio that must not be silently discarded.
        Zero-padding to 480 frames forces one final cava_execute() call.

        Invalidated / broken stream: call _reset_dsp() directly without flushing.
        Pending samples from a broken stream are intentionally discarded — flush()
        must not be called on a carry buffer that may contain corrupted audio.
        """
        if self._current_epoch_id is None:
            return
        cava_bars_np = self._cava.flush()
        if cava_bars_np is None:
            return  # carry buffer was already empty
        self._publish_features_from_cava(cava_bars_np.tolist())

    def _process_canonical_frame(self, frame: object) -> None:
        """Analyse one AnalysisPcmFrame: feed cavacore + onset pipeline, publish features."""
        epoch_id: str = frame.epoch_id  # type: ignore[union-attr]
        samples: np.ndarray = frame.samples  # type: ignore[union-attr]
        # samples: shape (n, 2), float32, range [-1, 1], columns [L, R]

        if epoch_id != self._current_epoch_id:
            if self._current_epoch_id is not None:
                self._reset_dsp()
            with self._lock:
                self._latest = None
            self._current_epoch_id = epoch_id

        # 1. Onset STFT — always, regardless of cavacore block readiness.
        #    All canonical PCM must reach onset analysis even when the cavacore
        #    carry-buffer has not yet accumulated a complete 480-frame execution block.
        mag_frames = self._bar_stft.push(samples)
        if mag_frames:
            onset_frames = self._onset_pipeline.push_mag(mag_frames)
            self._pending_onset.extend(onset_frames)

        # 2. cavacore spectrum bars — carry-buffered scheduler; returns None until
        #    a complete 480-frame execution block is ready.
        cava_bars_np = self._cava.execute(samples)
        if cava_bars_np is None:
            return  # onset state updated above; defer publication

        # 3. Publish features: drain accumulated onset + current cavacore bars.
        self._publish_features_from_cava(cava_bars_np.tolist())

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                source_result = self._source.read()  # type: ignore[union-attr]
                canonical_results = self._canonicalizer.push(source_result)

                if not canonical_results:
                    continue

                for cresult in canonical_results:
                    if isinstance(cresult, CanonicalData):
                        self._process_canonical_frame(cresult.frame)
                    elif isinstance(cresult, TemporarilyNoData):
                        if not self._source.running:  # type: ignore[union-attr]
                            with self._lock:
                                self._latest = None
                        self._stop.wait(_POLL_S)
                    elif isinstance(cresult, StreamInvalidated):
                        self._reset_dsp()
                        with self._lock:
                            self._latest = None
                    elif isinstance(cresult, EndOfStream):
                        # Clean EOS: flush pending cavacore carry buffer before reset.
                        # StreamInvalidated above calls _reset_dsp() directly (no flush).
                        # Do NOT clear _latest here — the EOS tail features must remain
                        # visible to consumers until the next epoch resets them.
                        self._flush_eos_tail()
                        self._reset_dsp()
                        self._canonicalizer.reset()
                        self._stop.wait(_POLL_S)
        except Exception:
            log.exception("CavaCoreAudioPipeline worker crashed; clearing stale features")
            with self._lock:
                self._latest = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                # Worker did not exit cleanly; leak the cavacore plan rather than
                # calling cava_destroy() while native code may still be executing.
                log.warning(
                    "CavaCoreAudioPipeline worker did not stop within 2 s; "
                    "cavacore plan leaked to avoid use-after-free"
                )
                return
        self._cava.close()

    def latest(self) -> AudioFeatures | None:
        with self._lock:
            return self._latest

    @property
    def pub_seq(self) -> int:
        """Monotonically increasing counter; incremented on each features publication."""
        with self._lock:
            return self._pub_seq

    @property
    def effective_spectrum_backend(self) -> str:
        """The spectrum backend actually running in this pipeline instance."""
        return "cavacore"


def make_cavacore_pipeline(source: object, profile: Profile) -> CavaCoreAudioPipeline:
    """Construct a CavaCoreAudioPipeline from a source and engine Profile.

    Mirrors the PcmAudioPipelineV2 construction in player_manager.py.
    """
    return CavaCoreAudioPipeline(
        source=source,
        bars=profile.bars,
        lower_cutoff_freq=profile.lower_cutoff_freq,
        higher_cutoff_freq=profile.higher_cutoff_freq,
        onset_method=profile.onset_method,
        onset_delta=profile.onset_delta,
        onset_alpha=profile.onset_alpha,
        superflux_mu=profile.superflux_mu,
        superflux_lag=profile.superflux_lag,
        bass_hz=profile.bass_hz,
        mid_hz=profile.mid_hz,
        exertion_clip=profile.exertion_clip,
    )
