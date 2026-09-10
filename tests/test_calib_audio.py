"""Tests for calib_audio.py — audio SE reconstruction (M1).

reconstruct_se() is tested with synthetic numpy arrays (no soundfile needed).
decode_audio_mono() tests are skipped if soundfile is not installed.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from calib_audio import reconstruct_se, se_stats

from huesync.sync_engine import SustainedEnergyTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sine_pcm(freq_hz: float, duration_s: float, sr: int = 44100, amp: float = 0.5) -> np.ndarray:
    """Generate a mono float32 sine wave."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * math.pi * freq_hz * t)).astype(np.float32)


def _silence_pcm(duration_s: float, sr: int = 44100) -> np.ndarray:
    return np.zeros(int(sr * duration_s), dtype=np.float32)


def _constant_pcm(amplitude: float, duration_s: float, sr: int = 44100) -> np.ndarray:
    return np.full(int(sr * duration_s), amplitude, dtype=np.float32)


# ---------------------------------------------------------------------------
# reconstruct_se — tick count and structure
# ---------------------------------------------------------------------------

def test_reconstruct_se_tick_count():
    """Number of ticks = floor(n_samples / chunk_size)."""
    sr = 44100
    tick_s = 1.0 / 30.0
    duration_s = 5.0
    pcm = _sine_pcm(440.0, duration_s, sr)
    ts, se = reconstruct_se(pcm, sr, tick_s)
    expected = int(len(pcm) // int(sr * tick_s))
    assert len(ts) == expected
    assert len(se) == expected


def test_reconstruct_se_timestamps_uniform():
    """Timestamps must be uniformly spaced at tick_s."""
    sr = 44100
    tick_s = 1.0 / 30.0
    pcm = _sine_pcm(440.0, 3.0, sr)
    ts, _ = reconstruct_se(pcm, sr, tick_s)
    diffs = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    for d in diffs:
        assert d == pytest.approx(tick_s)
    assert ts[0] == pytest.approx(0.0)


def test_reconstruct_se_empty_pcm():
    """Empty PCM → empty outputs, no crash."""
    ts, se = reconstruct_se(np.array([], dtype=np.float32), 44100)
    assert ts == []
    assert se == []


def test_reconstruct_se_pure_silence_returns_none():
    """Silence before any non-silent frame → all None."""
    pcm = _silence_pcm(3.0)
    ts, se = reconstruct_se(pcm, 44100)
    assert all(v is None for v in se)
    assert len(se) > 0


def test_reconstruct_se_first_nonsilent_frame_is_not_none():
    """First non-silent frame yields a value (SE = 0.5 by design)."""
    sr = 44100
    tick_s = 1.0 / 30.0
    pcm = np.concatenate([
        _silence_pcm(0.2, sr),
        _constant_pcm(0.3, 5.0, sr),
    ])
    ts, se = reconstruct_se(pcm, sr, tick_s)
    non_none = [v for v in se if v is not None]
    assert len(non_none) > 0
    # First non-None value must be 0.5 (tracker seeds both EMAs to RMS → ratio=1 → SE=0.5)
    assert non_none[0] == pytest.approx(0.5)


def test_reconstruct_se_constant_loud_settles_above_half():
    """Loud constant signal eventually pushes SE above 0.5 (short > long EMA)."""
    sr = 44100
    tick_s = 1.0 / 30.0
    # Short τ=300ms → short EMA reaches amplitude quickly.
    # Long τ=30s → long EMA lags far behind.
    # After a few seconds, short_ema >> long_ema → SE > 0.5.
    pcm = _constant_pcm(0.3, 10.0, sr)
    _, se = reconstruct_se(pcm, sr, tick_s)
    final = [v for v in se[-30:] if v is not None]
    assert final, "expected non-None SE values in last 1 s"
    assert all(v > 0.5 for v in final), f"expected SE > 0.5, got {final}"


def test_reconstruct_se_uses_production_tracker_constants():
    """reconstruct_se defaults must match SustainedEnergyTracker defaults."""
    sr = 44100
    tick_s = 1.0 / 30.0
    pcm = _sine_pcm(440.0, 2.0, sr)

    # Explicit defaults should produce same output as implicit defaults.
    _, se_default = reconstruct_se(pcm, sr, tick_s)
    _, se_explicit = reconstruct_se(
        pcm, sr, tick_s,
        tau_short_s=SustainedEnergyTracker.DEFAULT_TAU_SHORT_S,
        tau_long_s=SustainedEnergyTracker.DEFAULT_TAU_LONG_S,
        range_db=SustainedEnergyTracker.DEFAULT_RANGE_DB,
    )
    for a, b in zip(se_default, se_explicit, strict=True):
        if a is None and b is None:
            continue
        assert a is not None and b is not None
        assert a == pytest.approx(b)


def test_reconstruct_se_scale_invariant():
    """SE output should be (nearly) the same at different amplitude scales.

    SustainedEnergyTracker is scale-invariant because it tracks
    short/long RMS *ratio*, not absolute level.  After the long EMA
    has tracked the programme mean for a while, the ratio—and thus
    SE—should be nearly independent of amplitude.

    We compare the tail (last second) of two identical-trajectory
    signals at different amplitudes, well after the warmup period.
    """
    sr = 44100
    tick_s = 1.0 / 30.0
    # 30 s gives the long EMA (τ=30s) a chance to track the programme mean.
    pcm_lo = _sine_pcm(110.0, 30.0, sr, amp=0.1)
    pcm_hi = _sine_pcm(110.0, 30.0, sr, amp=0.8)

    _, se_lo = reconstruct_se(pcm_lo, sr, tick_s)
    _, se_hi = reconstruct_se(pcm_hi, sr, tick_s)

    # Take the last 30 ticks (1 s) where both are settled.
    tail_lo = [v for v in se_lo[-30:] if v is not None]
    tail_hi = [v for v in se_hi[-30:] if v is not None]
    assert tail_lo and tail_hi, "expected non-None SE in tail"
    mean_lo = sum(tail_lo) / len(tail_lo)
    mean_hi = sum(tail_hi) / len(tail_hi)
    assert abs(mean_hi - mean_lo) < 0.05, (
        f"SE should be scale-invariant after warmup; got {mean_lo:.3f} vs {mean_hi:.3f}"
    )


def test_reconstruct_se_silence_safety():
    """Silence after loud section should NOT drain long EMA to 0.

    The tracker's silence guard prevents the long EMA from decaying during
    silence, so SE value is preserved (not pushed toward 1.0 on resume).
    """
    sr = 44100
    tick_s = 1.0 / 30.0
    loud = _constant_pcm(0.3, 5.0, sr)
    quiet = _silence_pcm(5.0, sr)
    pcm = np.concatenate([loud, quiet])
    _, se = reconstruct_se(pcm, sr, tick_s)
    # SE value during silence should remain roughly equal to the value at
    # the transition (silence does not update either EMA).
    loud_ticks = int(5.0 / tick_s)
    se_at_transition = next((v for v in reversed(se[:loud_ticks]) if v is not None), None)
    se_during_silence = [v for v in se[loud_ticks:] if v is not None]
    # During silence the tracker returns the last computed value (no update)
    if se_at_transition is not None and se_during_silence:
        # All values during silence must equal the value at the transition.
        for v in se_during_silence:
            assert v == pytest.approx(se_at_transition, abs=1e-9)


def test_reconstruct_se_rate_independent():
    """Same audio produces consistent SE regardless of tick_s (within tolerance).

    tick_s affects when EMA alphas are computed (via dt). Two different tick
    rates on identical audio should give similar SE values at matched times.
    """
    sr = 44100
    pcm = _sine_pcm(440.0, 10.0, sr)
    ts_30, se_30 = reconstruct_se(pcm, sr, 1.0 / 30.0)
    ts_15, se_15 = reconstruct_se(pcm, sr, 1.0 / 15.0)

    # Find matching times (ts_15 ≈ every other ts_30).
    for i, t15 in enumerate(ts_15):
        # Find closest ts_30 index.
        j = min(range(len(ts_30)), key=lambda k: abs(ts_30[k] - t15))
        v30 = se_30[j]
        v15 = se_15[i]
        if v30 is None or v15 is None:
            continue
        # Allow generous tolerance — not exact equality, just same ballpark.
        assert abs(v30 - v15) < 0.10, (
            f"SE mismatch at t≈{t15:.2f}s: 30Hz={v30:.4f}, 15Hz={v15:.4f}"
        )


# ---------------------------------------------------------------------------
# se_stats
# ---------------------------------------------------------------------------

def test_se_stats_all_none():
    result = se_stats([None, None])
    assert result["n_valid"] == 0
    assert result["mean"] is None


def test_se_stats_known_values():
    result = se_stats([None, 0.2, 0.4, 0.6, None])
    assert result["n_valid"] == 3
    assert result["mean"] == pytest.approx(0.4)
    assert result["min"] == pytest.approx(0.2)
    assert result["max"] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# decode_audio_mono — skipped if soundfile is not installed
# ---------------------------------------------------------------------------

def test_decode_audio_requires_soundfile_gracefully():
    """decode_audio_mono raises ImportError with helpful message if soundfile missing."""
    try:
        import soundfile  # noqa: F401
        pytest.skip("soundfile IS installed — this test only runs without it")
    except ImportError:
        pass
    from calib_audio import decode_audio_mono
    with pytest.raises(ImportError, match="soundfile is required"):
        decode_audio_mono("/tmp/nonexistent.flac")


@pytest.mark.skipif(
    pytest.importorskip("soundfile", reason="soundfile not installed") is None,
    reason="soundfile not available",
)
def test_decode_audio_mono_synthetic_wav(tmp_path):
    """Decode a synthetic WAV file and verify shape + dtype."""
    pytest.importorskip("soundfile")
    import wave as _wave  # stdlib, always available

    # Write a simple 1-second stereo WAV using stdlib.
    wav_path = tmp_path / "test.wav"
    sr = 16000
    n_samples = sr
    data = np.zeros((n_samples, 2), dtype=np.int16)
    # Left channel: sine, right channel: cos
    t = np.linspace(0, 1.0, n_samples, endpoint=False)
    data[:, 0] = (0.3 * np.sin(2 * math.pi * 440 * t) * 32767).astype(np.int16)
    data[:, 1] = (0.3 * np.cos(2 * math.pi * 440 * t) * 32767).astype(np.int16)
    with _wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())

    from calib_audio import decode_audio_mono
    pcm, out_sr, info = decode_audio_mono(str(wav_path))
    assert pcm.ndim == 1
    assert pcm.dtype == np.float32
    assert out_sr == sr
    assert info["n_channels"] == 2
    assert abs(info["duration_s"] - 1.0) < 0.01
    # Mono mix of sin+cos at same amp → amplitude = 0.3 * sqrt(2)/2 ≈ 0.212
    assert pcm.max() > 0.1
