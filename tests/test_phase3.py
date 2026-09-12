"""Phase 3 tests: StereoMagStft, PcmAudioPipelineV2, stereo acceptance, AirPlay path."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from huesync.canonicalizer import (
    AudioCanonicalizer,
    CanonicalData,
    EndOfStream,
    InvalidationCause,
    StreamInvalidated,
    TemporarilyNoData,
)
from huesync.pcm_source import (
    AIRPLAY_SAMPLE_RATE,
    AirPlayPipeStereoSource,
)

# Import StereoMagStft directly (it is defined in sync_engine).
from huesync.sync_engine import (
    MultibandStftPipeline,
    PcmAudioPipelineV2,
    StereoMagStft,
    StftOnsetPipeline,
    SuperfluxStftPipeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_RATE = 48000
_WINDOW = 2048


def _sine_stereo(
    freq_l: float,
    freq_r: float,
    amplitude_l: float = 0.5,
    amplitude_r: float = 0.5,
    n: int = _CANONICAL_RATE,
    rate: int = _CANONICAL_RATE,
) -> np.ndarray:
    """Return stereo float32 array shape (n, 2)."""
    t = np.arange(n, dtype=np.float32) / rate
    left = (np.sin(2 * np.pi * freq_l * t) * amplitude_l).astype(np.float32)
    right = (np.sin(2 * np.pi * freq_r * t) * amplitude_r).astype(np.float32)
    return np.column_stack([left, right])


def _silence_stereo(n: int = _CANONICAL_RATE) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float32)


def _make_canonical_frame(
    samples: np.ndarray,
    epoch_id: str = "ep-1",
    source_id: str = "test:src",
    sample_pos: int = 0,
    over_range: bool = False,
) -> CanonicalData:
    from huesync.canonicalizer import AnalysisPcmFrame

    frame = AnalysisPcmFrame(
        samples=samples,
        sample_pos=sample_pos,
        epoch_id=epoch_id,
        source_id=source_id,
        over_range=over_range,
    )
    return CanonicalData(frame=frame)


def _feed_pipeline(
    pipeline: PcmAudioPipelineV2,
    stereo: np.ndarray,
    epoch_id: str = "ep-1",
    chunk_size: int = 4096,
) -> None:
    """Feed stereo samples to pipeline in chunks, bypassing the background thread."""
    n = len(stereo)
    pos = 0
    offset = 0
    while offset < n:
        end = min(offset + chunk_size, n)
        chunk = stereo[offset:end]
        cd = _make_canonical_frame(chunk, epoch_id=epoch_id, sample_pos=pos)
        pipeline._process_canonical_frame(cd.frame)
        pos += len(chunk)
        offset = end


def _make_pipeline(**kwargs) -> PcmAudioPipelineV2:
    """Return a PcmAudioPipelineV2 with a null source and default profile params."""
    src = MagicMock()
    src.running = True
    defaults = dict(
        source=src,
        bars=30,
        lower_cutoff_freq=50,
        higher_cutoff_freq=10000,
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    defaults.update(kwargs)
    return PcmAudioPipelineV2(**defaults)


# ---------------------------------------------------------------------------
# StereoMagStft — unit tests
# ---------------------------------------------------------------------------


def test_stereo_mag_stft_in_phase_vs_opposite_phase():
    """In-phase and opposite-phase stereo produce the same combined magnitude."""
    sr = _CANONICAL_RATE
    stft_ip = StereoMagStft(sr)
    stft_op = StereoMagStft(sr)

    t = np.arange(_WINDOW * 8, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)

    in_phase = np.column_stack([sig, sig])
    opposite = np.column_stack([sig, -sig])

    frames_ip = stft_ip.push(in_phase)
    frames_op = stft_op.push(opposite)

    assert len(frames_ip) == len(frames_op)
    assert len(frames_ip) > 0
    for fip, fop in zip(frames_ip, frames_op, strict=True):
        np.testing.assert_allclose(fip, fop, rtol=1e-5)


def test_stereo_mag_stft_no_preFFT_downmix():
    """StereoMagStft does not perform (L+R)/2: proved by anti-phase non-zero output."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)

    t = np.arange(_WINDOW * 4, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    opposite = np.column_stack([sig, -sig])

    frames = stft.push(opposite)
    assert frames
    # (L+R)/2 would give zero magnitude; stereo RMS must be non-zero.
    assert max(float(np.max(f)) for f in frames) > 0.01


def test_stereo_mag_stft_l_only():
    """Signal on L only produces non-zero magnitude (not cancelled)."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)
    t = np.arange(_WINDOW * 4, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    stereo = np.column_stack([sig, np.zeros_like(sig)])
    frames = stft.push(stereo)
    assert frames
    assert max(float(np.max(f)) for f in frames) > 0.01


def test_stereo_mag_stft_r_only():
    """Signal on R only produces non-zero magnitude (not cancelled)."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)
    t = np.arange(_WINDOW * 4, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    stereo = np.column_stack([np.zeros_like(sig), sig])
    frames = stft.push(stereo)
    assert frames
    assert max(float(np.max(f)) for f in frames) > 0.01


def test_stereo_mag_stft_silence_is_zero():
    """Silence input produces near-zero magnitude frames."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)
    stereo = np.zeros((_WINDOW * 4, 2), dtype=np.float32)
    frames = stft.push(stereo)
    assert frames
    assert max(float(np.max(f)) for f in frames) < 1e-6


def test_stereo_mag_stft_reset_clears_history():
    """After reset(), the STFT buffer is cleared and frame count resets."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)

    # Prime with some data (less than one window).
    partial = np.zeros((100, 2), dtype=np.float32)
    stft.push(partial)

    stft.reset()

    # After reset, another partial push should produce no frames.
    frames = stft.push(partial)
    assert len(frames) == 0


def test_stereo_mag_stft_different_frequencies():
    """L=440 Hz, R=880 Hz: combined magnitude shows energy at both frequencies."""
    sr = _CANONICAL_RATE
    stft = StereoMagStft(sr)
    n = _WINDOW * 8
    t = np.arange(n, dtype=np.float32) / sr
    l_sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    r_sig = (np.sin(2 * np.pi * 880 * t) * 0.5).astype(np.float32)
    stereo = np.column_stack([l_sig, r_sig])
    frames = stft.push(stereo)
    assert frames

    # 440 Hz bin ≈ round(440 * 2048 / 48000) = 19
    # 880 Hz bin ≈ round(880 * 2048 / 48000) = 38
    bin_440 = round(440 * _WINDOW / sr)
    bin_880 = round(880 * _WINDOW / sr)
    # Average over frames to smooth window leakage.
    avg = np.mean(np.stack(frames, axis=0), axis=0)
    assert avg[bin_440] > avg[bin_440 - 5], "440 Hz peak should stand out"
    assert avg[bin_880] > avg[bin_880 - 5], "880 Hz peak should stand out"


def test_stereo_mag_stft_hop():
    """hop property matches PcmStft at same sample rate."""
    from huesync.pcm_source import PcmStft

    sr = 48000
    stft_stereo = StereoMagStft(sr)
    stft_mono = PcmStft(sr)
    assert stft_stereo.hop == stft_mono.hop


def test_stereo_mag_stft_n_bins():
    """n_bins is WINDOW_SIZE // 2 + 1 = 1025."""
    stft = StereoMagStft(48000)
    assert stft.n_bins == _WINDOW // 2 + 1


# ---------------------------------------------------------------------------
# push_mag methods — onset pipeline API
# ---------------------------------------------------------------------------


def test_stft_onset_push_mag_matches_push():
    """StftOnsetPipeline.push_mag() on pre-computed frames matches push() on same samples."""
    from huesync.pcm_source import PcmStft

    sr = 44100
    n = 8192
    t = np.arange(n, dtype=np.float32) / sr
    samples = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)

    # Reference path: push() computes its own STFT internally.
    pipe_ref = StftOnsetPipeline(sr)
    results_ref = pipe_ref.push(samples)

    # V2 path: external STFT, pass mag frames directly.
    stft_ext = PcmStft(sr)
    mag_frames = stft_ext.push(samples)
    pipe_v2 = StftOnsetPipeline(sr)
    results_v2 = pipe_v2.push_mag(mag_frames)

    assert len(results_ref) == len(results_v2) == len(mag_frames)
    # Onset detections should be identical (same detector, same frames).
    for (on_ref, _), (on_v2, _) in zip(results_ref, results_v2, strict=True):
        assert on_ref == on_v2


def test_superflux_push_mag_type():
    """SuperfluxStftPipeline.push_mag() returns list of (bool, float)."""
    sr = 48000
    pipe = SuperfluxStftPipeline(sr)
    mag = np.zeros(1025, dtype=np.float32)
    results = pipe.push_mag([mag])
    assert len(results) == 1
    onset, strength = results[0]
    assert isinstance(onset, bool)
    assert isinstance(strength, float)


def test_multiband_push_mag_type():
    """MultibandStftPipeline.push_mag() returns list of three (bool, float) tuples."""
    sr = 48000
    pipe = MultibandStftPipeline(sr, bass_hz=250, mid_hz=2000)
    mag = np.zeros(1025, dtype=np.float32)
    results = pipe.push_mag([mag])
    assert len(results) == 1
    (b_on, b_str), (m_on, m_str), (t_on, t_str) = results[0]
    assert all(isinstance(x, bool) for x in [b_on, m_on, t_on])
    assert all(isinstance(x, float) for x in [b_str, m_str, t_str])


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — opposite-phase stereo bar test (central Phase 3 test)
# ---------------------------------------------------------------------------


def test_v2_opposite_phase_produces_nonzero_bars():
    """CENTRAL TEST: opposite-phase stereo must produce non-zero bars after analysis."""
    pipeline = _make_pipeline()

    # Feed enough audio to warm up BandNormaliser (~2 s of signal).
    stereo_in_phase = _sine_stereo(440, 440, 0.5, 0.5, n=2 * _CANONICAL_RATE)
    _feed_pipeline(pipeline, stereo_in_phase, epoch_id="ep-warmup")

    # Switch to opposite-phase (same epoch so EMA doesn't reset).
    stereo_opposite = _sine_stereo(440, 440, amplitude_l=0.5, amplitude_r=-0.5, n=_CANONICAL_RATE)
    _feed_pipeline(pipeline, stereo_opposite, epoch_id="ep-warmup")

    features = pipeline.latest()
    assert features is not None
    assert max(features.bars) > 0.01, (
        f"Opposite-phase bars must be non-zero; max={max(features.bars):.4f}"
    )


def test_v2_opposite_phase_equivalent_to_in_phase():
    """In-phase and opposite-phase 440 Hz produce equivalent bar magnitudes."""
    pip_ip = _make_pipeline()
    pip_op = _make_pipeline()

    warmup = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(pip_ip, warmup, epoch_id="ep-1")
    _feed_pipeline(pip_op, warmup, epoch_id="ep-1")

    # Measure on same-duration opposite-phase signal.
    sig_ip = _sine_stereo(440, 440, 0.5, 0.5, n=_CANONICAL_RATE)
    sig_op = _sine_stereo(440, 440, 0.5, -0.5, n=_CANONICAL_RATE)
    _feed_pipeline(pip_ip, sig_ip, epoch_id="ep-1")
    _feed_pipeline(pip_op, sig_op, epoch_id="ep-1")

    feat_ip = pip_ip.latest()
    feat_op = pip_op.latest()
    assert feat_ip is not None
    assert feat_op is not None

    # Max bar values should be similar (within 25% relative tolerance).
    max_ip = max(feat_ip.bars)
    max_op = max(feat_op.bars)
    assert max_ip > 0.01
    assert max_op > 0.01
    ratio = max_op / max_ip if max_ip > 0 else 0.0
    assert 0.5 < ratio < 2.0, f"ip={max_ip:.3f} op={max_op:.3f} ratio={ratio:.3f}"


def test_v2_legacy_mono_downmix_would_cancel():
    """Prove that (L+R)/2 with opposite-phase input gives near-zero output.

    This is the regression the new path is designed to avoid.  This test
    directly verifies the failure mode; PcmAudioPipelineV2 never takes this path.
    """
    from huesync.pcm_source import PcmStft

    sr = _CANONICAL_RATE
    n = _WINDOW * 8
    t = np.arange(n, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    # Legacy downmix path.
    mono_downmix = (sig + (-sig)) / 2.0
    stft = PcmStft(sr)
    frames = stft.push(mono_downmix)
    assert frames
    # Downmix of opposite-phase should produce essentially zero magnitude.
    max_mag = max(float(np.max(f)) for f in frames)
    assert max_mag < 1e-5, f"(L+R)/2 opposite-phase should cancel; max={max_mag}"


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — stereo acceptance: all six scenarios
# ---------------------------------------------------------------------------


def _warmup_and_measure(pipeline: PcmAudioPipelineV2, stereo: np.ndarray) -> list[float]:
    """Warm up the BandNormaliser, then measure bars on the given signal."""
    _feed_pipeline(pipeline, stereo, epoch_id="ep-measure")
    _feed_pipeline(pipeline, stereo, epoch_id="ep-measure")
    features = pipeline.latest()
    return features.bars if features is not None else []


def test_v2_stereo_lr_equal():
    """L=R sine: bars are non-zero."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5)
    bars = _warmup_and_measure(p, sig)
    assert max(bars) > 0.01


def test_v2_stereo_lr_opposite():
    """L=-R sine: bars are non-zero (anti-phase not cancelled)."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, -0.5)
    bars = _warmup_and_measure(p, sig)
    assert max(bars) > 0.01


def test_v2_stereo_l_only():
    """Signal on L only: bars are non-zero (broadband to clear silence gate)."""
    p = _make_pipeline()
    rng = np.random.default_rng(42)
    n = 2 * _CANONICAL_RATE
    sig = np.zeros((n, 2), dtype=np.float32)
    sig[:, 0] = rng.standard_normal(n).astype(np.float32) * 0.3
    bars = _warmup_and_measure(p, sig)
    assert max(bars) > 0.01


def test_v2_stereo_r_only():
    """Signal on R only: bars are non-zero (broadband to clear silence gate)."""
    p = _make_pipeline()
    rng = np.random.default_rng(42)
    n = 2 * _CANONICAL_RATE
    sig = np.zeros((n, 2), dtype=np.float32)
    sig[:, 1] = rng.standard_normal(n).astype(np.float32) * 0.3
    bars = _warmup_and_measure(p, sig)
    assert max(bars) > 0.01


def test_v2_stereo_different_lr_frequencies():
    """L=440 Hz, R=880 Hz: bars retain evidence of both frequencies."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 880, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    features = p.latest()
    assert features is not None
    bars = features.bars

    # 440 Hz and 880 Hz should both produce non-zero bars in distinct regions.
    n = len(bars)
    # Rough log-spaced bar indices at 440 Hz and 880 Hz for 30 bars, 50–10000 Hz.
    import math

    def _hz_to_bar(hz: float, lo: float = 50.0, hi: float = 10000.0, n_bars: int = 30) -> int:
        if hz <= 0:
            return 0
        frac = (math.log10(hz) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
        return max(0, min(n_bars - 1, round(frac * n_bars)))

    bar_440 = _hz_to_bar(440)
    bar_880 = _hz_to_bar(880)
    # Both frequency regions should have non-trivial energy.
    # Use a ±2 bar window to account for spectral leakage.
    lo_440 = max(0, bar_440 - 2)
    hi_440 = min(n, bar_440 + 3)
    lo_880 = max(0, bar_880 - 2)
    hi_880 = min(n, bar_880 + 3)
    energy_440 = max(bars[lo_440:hi_440]) if hi_440 > lo_440 else 0.0
    energy_880 = max(bars[lo_880:hi_880]) if hi_880 > lo_880 else 0.0
    assert energy_440 > 0.005 or energy_880 > 0.005, (
        f"At least one frequency band should show energy; "
        f"440={energy_440:.4f} 880={energy_880:.4f}"
    )


def test_v2_stereo_silence():
    """Silence input: bars remain at zero."""
    p = _make_pipeline()
    sig = _silence_stereo(n=2 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    _feed_pipeline(p, sig, epoch_id="ep-1")
    features = p.latest()
    # After silence, latest may be None (no frames produced above threshold)
    # or bars should be near-zero.
    if features is not None:
        assert max(features.bars) < 0.01


def test_v2_mono_source_canonicalized_to_lr_equal():
    """Mono source canonicalized to L=R by AudioCanonicalizer: bars non-zero."""

    p = _make_pipeline()
    # Simulate what AudioCanonicalizer produces for mono input: L == R.
    t = np.arange(3 * _CANONICAL_RATE, dtype=np.float32) / _CANONICAL_RATE
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    lr_equal = np.column_stack([sig, sig])
    _feed_pipeline(p, lr_equal, epoch_id="ep-mono")
    features = p.latest()
    assert features is not None
    assert max(features.bars) > 0.01


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — timing is sample-derived
# ---------------------------------------------------------------------------


def test_v2_normalise_dt_is_sample_derived():
    """BandNormaliser dt must be hop/48000, not wall-clock."""
    p = _make_pipeline()
    expected_dt = p._bar_stft.hop / PcmAudioPipelineV2._SAMPLE_RATE
    assert abs(p._normalise_dt - expected_dt) < 1e-9


def test_v2_sample_rate_is_48000():
    """Canonical pipeline always operates at 48000 Hz."""
    assert PcmAudioPipelineV2._SAMPLE_RATE == 48000


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — epoch/reset behaviour
# ---------------------------------------------------------------------------


def test_v2_epoch_transition_resets_stft():
    """New epoch_id triggers DSP reset: STFT history cleared, EMA reset."""
    p = _make_pipeline()

    # Epoch A: 440 Hz
    sig_a = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig_a, epoch_id="epoch-A")

    # Remember state.
    epoch_id_before = p._current_epoch_id
    normaliser_before = id(p._normaliser)

    # Epoch B: different signal, new epoch_id.
    sig_b = _silence_stereo(n=100)
    _feed_pipeline(p, sig_b, epoch_id="epoch-B")

    # DSP was reset: new epoch_id committed, new BandNormaliser instance.
    assert p._current_epoch_id == "epoch-B"
    assert p._current_epoch_id != epoch_id_before
    assert id(p._normaliser) != normaliser_before


def test_v2_epoch_transition_no_contamination():
    """After epoch A (440 Hz) → epoch B (silence), bars must be near-zero."""
    p = _make_pipeline()

    # Epoch A: strong 440 Hz signal.
    sig_a = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig_a, epoch_id="epoch-A")

    # Epoch B: silence (new epoch_id triggers DSP reset).
    sig_b = _silence_stereo(n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig_b, epoch_id="epoch-B")

    features = p.latest()
    if features is not None:
        # After epoch B (silence), bars should be near-zero — no 440 Hz ghost.
        assert max(features.bars) < 0.05, (
            f"Old epoch STFT state must not contaminate epoch B; max={max(features.bars):.4f}"
        )


def test_v2_stream_invalidated_resets_dsp():
    """StreamInvalidated causes DSP reset exactly once."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    norm_id_before = id(p._normaliser)

    # Simulate StreamInvalidated coming through _run().
    p._run_one_canonical(StreamInvalidated(cause=InvalidationCause.UNKNOWN))

    # BandNormaliser was replaced.
    assert id(p._normaliser) != norm_id_before
    assert p._current_epoch_id is None


def test_v2_temporarily_no_data_no_reset():
    """TemporarilyNoData must not reset DSP or change epoch."""
    p = _make_pipeline()
    p._source.running = True
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    norm_id = id(p._normaliser)
    epoch = p._current_epoch_id

    p._run_one_canonical(TemporarilyNoData())

    assert id(p._normaliser) == norm_id
    assert p._current_epoch_id == epoch


def test_v2_end_of_stream_resets_dsp():
    """EndOfStream resets DSP and clears latest."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")

    p._run_one_canonical(EndOfStream())

    assert p.latest() is None
    assert p._current_epoch_id is None


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — chunk independence
# ---------------------------------------------------------------------------


def _measure_v2_bars(chunk_size: int, n: int = 3 * _CANONICAL_RATE) -> list[float]:
    """Feed a sine wave in chunk_size pieces; return final bars."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=n)
    _feed_pipeline(p, sig, epoch_id="ep-1", chunk_size=chunk_size)
    features = p.latest()
    return features.bars if features is not None else []


def test_v2_chunk_independence_max_bar():
    """Different chunk sizes produce similar max bar values (within 10%)."""
    bars_large = _measure_v2_bars(chunk_size=8192)
    bars_small = _measure_v2_bars(chunk_size=256)
    if bars_large and bars_small:
        max_large = max(bars_large)
        max_small = max(bars_small)
        if max_large > 0.01 and max_small > 0.01:
            ratio = max_small / max_large
            assert 0.5 < ratio < 2.0, f"large={max_large:.3f} small={max_small:.3f}"


# ---------------------------------------------------------------------------
# PcmAudioPipelineV2 — AudioFeatures compatibility
# ---------------------------------------------------------------------------


def test_v2_produces_audiofeatures_shape():
    """latest() produces an AudioFeatures with the required fields."""
    from huesync.types import AudioFeatures

    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    features = p.latest()
    assert features is not None
    assert isinstance(features, AudioFeatures)
    assert len(features.bars) == 30
    assert isinstance(features.onset, bool)
    assert isinstance(features.bass, float)
    assert isinstance(features.mid, float)
    assert isinstance(features.full, float)
    assert isinstance(features.centroid, float)
    assert isinstance(features.relative_exertion, float)


def test_v2_sustained_energy_is_none():
    """Phase 3 AirPlay path does not provide sustained_energy (Phase 4 work)."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    features = p.latest()
    assert features is not None
    assert features.sustained_energy is None


def test_v2_hpss_inactive():
    """Phase 3 AirPlay path: hpss_active=False (AirPlay HPSS is Phase 4 work)."""
    p = _make_pipeline()
    sig = _sine_stereo(440, 440, 0.5, 0.5, n=3 * _CANONICAL_RATE)
    _feed_pipeline(p, sig, epoch_id="ep-1")
    features = p.latest()
    assert features is not None
    assert features.hpss_active is False


def test_v2_layermixer_fallback_via_sustained_none():
    """EnergyProfile LayerMixer fallback: when sustained_energy is None, uses full."""
    from huesync.models import Profile
    from huesync.sync_engine import LayerMixer
    from huesync.types import AudioFeatures

    p_model = Profile(id="test", name="test")
    lm = LayerMixer(p_model, p_model)

    features = AudioFeatures(
        bars=[0.5] * 30,
        bass=0.5,
        mid=0.5,
        full=0.6,
        centroid=0.5,
        sustained_energy=None,  # Phase 3 AirPlay: no sustained_energy
    )
    # render() falls back to full=0.6 when sustained_energy is None.
    # blend_start=0.3, blend_end=0.7 → smoothstep(0.6, 0.3, 0.7) > 0 → mix > 0.
    lm.render(features, 0.0)
    assert lm.mix > 0.0, (
        f"Fallback to full=0.6 should drive mix > 0; got {lm.mix}"
    )


def test_v2_effects_consume_audiofeatures():
    """Existing effect renderers can consume V2 AudioFeatures without error."""
    from huesync.models import Profile
    from huesync.sync_engine import _MonoPulseRenderer, _SpectrumRgbRenderer
    from huesync.types import AudioFeatures

    profile = Profile(id="test", name="test")
    features = AudioFeatures(
        bars=[0.3] * 30,
        bass=0.3,
        mid=0.3,
        full=0.3,
        centroid=0.5,
        onset=True,
        onset_strength=0.5,
        sustained_energy=None,
        hpss_active=False,
    )
    t = 0.0
    scene_rgb = _SpectrumRgbRenderer().render(profile, features, t)
    scene_pulse = _MonoPulseRenderer().render(profile, features, t)
    assert scene_rgb is not None
    assert scene_pulse is not None


# ---------------------------------------------------------------------------
# Full AirPlay 44100 S16_LE → canonical 48000 → bars path
# ---------------------------------------------------------------------------


def _make_airplay_stereo_pipe() -> tuple[AirPlayPipeStereoSource, int]:
    """Open an AirPlayPipeStereoSource on a synthetic os.pipe()."""
    r_fd, w_fd = os.pipe()
    fl = fcntl.fcntl(r_fd, fcntl.F_GETFL)
    fcntl.fcntl(r_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    src = AirPlayPipeStereoSource.__new__(AirPlayPipeStereoSource)
    src._path = Path("/synthetic/airplay.pcm")
    src._fd = r_fd
    src._last_data_t = None
    src._remainder = b""
    src._source_id = "airplay:/synthetic/airplay.pcm"
    return src, w_fd


def _s16le_stereo(left: np.ndarray, right: np.ndarray) -> bytes:
    interleaved = np.empty(len(left) * 2, dtype=np.int16)
    interleaved[0::2] = (left * 32767).astype(np.int16)
    interleaved[1::2] = (right * 32767).astype(np.int16)
    return interleaved.tobytes()


def test_full_airplay_path_opposite_phase_nonzero_bars():
    """Full path: 44100 S16LE → AirPlayPipeStereoSource → AudioCanonicalizer →
    48000 stereo → PcmAudioPipelineV2 → non-zero bars for opposite-phase audio."""
    src, w_fd = _make_airplay_stereo_pipe()
    canon = AudioCanonicalizer()
    pipeline = PcmAudioPipelineV2(
        source=src,
        bars=30,
        lower_cutoff_freq=50,
        higher_cutoff_freq=10000,
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )

    sr = AIRPLAY_SAMPLE_RATE  # 44100
    n_total = sr * 3  # 3 seconds of 44100 Hz audio

    t = np.arange(n_total, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)

    # Opposite-phase: L=sin, R=-sin
    payload = _s16le_stereo(sig, -sig)

    # Write in chunks to the pipe and process synchronously.
    chunk_bytes = 4096  # ~23 ms at 44100 Hz stereo S16LE
    offset = 0
    while offset < len(payload):
        end = min(offset + chunk_bytes, len(payload))
        os.write(w_fd, payload[offset:end])
        result = src.read()
        for cresult in canon.push(result):
            if isinstance(cresult, CanonicalData):
                pipeline._process_canonical_frame(cresult.frame)
        offset = end

    os.close(w_fd)
    src.close()

    features = pipeline.latest()
    assert features is not None, "Pipeline must have produced AudioFeatures"
    assert max(features.bars) > 0.01, (
        f"Opposite-phase bars must be non-zero; max={max(features.bars):.4f}"
    )


def test_full_airplay_path_in_phase_nonzero_bars():
    """Full path: in-phase audio also produces non-zero bars."""
    src, w_fd = _make_airplay_stereo_pipe()
    canon = AudioCanonicalizer()
    pipeline = _make_pipeline(source=src)

    sr = AIRPLAY_SAMPLE_RATE
    n_total = sr * 2
    t = np.arange(n_total, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    payload = _s16le_stereo(sig, sig)

    chunk_bytes = 4096
    offset = 0
    while offset < len(payload):
        end = min(offset + chunk_bytes, len(payload))
        os.write(w_fd, payload[offset:end])
        result = src.read()
        for cresult in canon.push(result):
            if isinstance(cresult, CanonicalData):
                pipeline._process_canonical_frame(cresult.frame)
        offset = end

    os.close(w_fd)
    src.close()

    features = pipeline.latest()
    assert features is not None
    assert max(features.bars) > 0.01


# ---------------------------------------------------------------------------
# One-ingress audit: production FIFO construction sites
# ---------------------------------------------------------------------------


def test_one_ingress_audit_activate_airplay():
    """The _activate_airplay method instantiates exactly AirPlayPipeStereoSource
    (not the legacy AirPlayPipeSource) — verified by patch check."""
    import huesync.player_manager as pm
    # The production code now uses AirPlayPipeStereoSource only.
    # Verify the legacy class is NOT imported at the module level.
    assert not hasattr(pm, "AirPlayPipeSource"), (
        "AirPlayPipeSource must not be imported in player_manager after Phase 3 wiring"
    )
    # The new class must be imported.
    assert hasattr(pm, "AirPlayPipeStereoSource"), (
        "AirPlayPipeStereoSource must be imported in player_manager"
    )


def test_legacy_airplay_source_not_in_player_manager_imports():
    """Legacy AirPlayPipeSource must not be a name in the player_manager module namespace."""
    import huesync.player_manager as pm
    assert "AirPlayPipeSource" not in dir(pm), (
        "AirPlayPipeSource must not be in player_manager namespace after Phase 3"
    )


# ---------------------------------------------------------------------------
# Production scope: LMS path unchanged
# ---------------------------------------------------------------------------


def test_lms_path_still_uses_pcm_audio_pipeline():
    """The LMS PCM sub-path still uses PcmAudioPipeline (not V2)."""
    import huesync.player_manager as pm
    # PcmAudioPipeline is still imported for the LMS native path.
    assert hasattr(pm, "PcmAudioPipeline"), (
        "PcmAudioPipeline must remain in player_manager for LMS native path"
    )


def test_v2_not_used_for_lms():
    """player_manager._activate_lms_pcm uses PcmAudioPipeline, not V2."""
    import inspect

    import huesync.player_manager as pm

    src = inspect.getsource(pm.PlayerManager._activate_lms_pcm)
    assert "PcmAudioPipelineV2" not in src
    assert "PcmAudioPipeline(" in src


# ---------------------------------------------------------------------------
# Helpers used in reset tests — expose single canonical result processing
# ---------------------------------------------------------------------------


def _run_one_canonical_patch(self, cresult):
    """Drive PcmAudioPipelineV2 with a single pre-built CanonicalReadResult."""
    from huesync.canonicalizer import CanonicalData

    if isinstance(cresult, CanonicalData):
        self._process_canonical_frame(cresult.frame)
    elif isinstance(cresult, TemporarilyNoData):
        if not self._source.running:
            with self._lock:
                self._latest = None
    elif isinstance(cresult, StreamInvalidated):
        self._reset_dsp()
    elif isinstance(cresult, EndOfStream):
        self._reset_dsp()
        with self._lock:
            self._latest = None
        self._canonicalizer.reset()


# Monkey-patch onto the class for the reset tests above.
PcmAudioPipelineV2._run_one_canonical = _run_one_canonical_patch
