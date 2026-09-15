"""CavaCoreAudioPipeline — thin wrapper around CanonicalAnalysisPipeline + CavaCoreSpectrumEngine.

Architecture
------------
canonical PCM ──┬── StereoMagStft (Hamming, 2048-pt) ──► onset / other features
                └── CavaCoreSpectrumEngine (CAVA Hann, 4096/8192-pt) ──► spectrum bars

All analysis logic lives in CanonicalAnalysisPipeline (sync_engine.py) and
CavaCoreSpectrumEngine (spectrum_engine.py).  This module is a thin compatibility
wrapper that keeps the same constructor signature as the previous monolithic
implementation, with no duplicated DSP.

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

from typing import TYPE_CHECKING

from .spectrum_engine import CavaCoreSpectrumEngine
from .sync_engine import BandNormaliser, CanonicalAnalysisPipeline
from .types import AudioFeatures

if TYPE_CHECKING:
    from .canonicalizer import AnalysisPcmFrame
    from .models import Profile
    from .spectrum_engine import PublicationRecord


class CavaCoreAudioPipeline:
    """Thin wrapper: CanonicalAnalysisPipeline with CavaCoreSpectrumEngine.

    Implements the same interface as PcmAudioPipelineV2 so it can be passed to
    SyncEngine as a drop-in replacement.  All DSP logic lives in
    CanonicalAnalysisPipeline + CavaCoreSpectrumEngine.
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
        engine = CavaCoreSpectrumEngine(
            n_bars=bars,
            lower_hz=float(lower_cutoff_freq),
            upper_hz=float(higher_cutoff_freq),
        )
        self._pipeline = CanonicalAnalysisPipeline(
            source=source,
            engine=engine,
            onset_method=onset_method,
            onset_delta=onset_delta,
            onset_alpha=onset_alpha,
            superflux_mu=superflux_mu,
            superflux_lag=superflux_lag,
            bass_hz=bass_hz,
            mid_hz=mid_hz,
        )

    @property
    def hop(self) -> int:
        return self._pipeline.hop

    @property
    def pub_seq(self) -> int:
        return self._pipeline.pub_seq

    @property
    def effective_spectrum_backend(self) -> str:
        return "cavacore"

    @property
    def v2_peak_ema(self) -> None:
        return None

    @property
    def v2_bar_smooth(self) -> None:
        return None

    def start(self) -> None:
        self._pipeline.start()

    def stop(self) -> None:
        self._pipeline.stop()

    def latest(self) -> AudioFeatures | None:
        return self._pipeline.latest()

    def feed(self, frame: AnalysisPcmFrame) -> list[PublicationRecord]:
        return self._pipeline.feed(frame)

    def end_of_stream(self) -> list[PublicationRecord]:
        return self._pipeline.end_of_stream()

    def drain_publications(self) -> list[PublicationRecord]:
        return self._pipeline.drain_publications()


def make_cavacore_pipeline(source: object, profile: Profile) -> CavaCoreAudioPipeline:
    """Construct a CavaCoreAudioPipeline from a source and engine Profile."""
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
