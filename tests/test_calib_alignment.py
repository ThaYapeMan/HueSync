"""Tests for calib_alignment.py — cross-correlation alignment (M2).

All tests use synthetic SE series (no audio files, no soundfile needed).
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from calib_alignment import (
    MIN_ACCEPTABLE_CORRELATION,
    AlignmentResult,
    _quality_label,
    _to_uniform_grid,
    align,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICK = 1.0 / 30.0   # production tick


def _smooth_se(n: int, seed: int = 42) -> list[float]:
    """Generate a deterministic smooth SE series in [0.1, 0.9].

    Uses two non-harmonic sinusoidal periods (11.3 s and 4.7 s at 30 Hz) so
    that typical alignment offsets (60–300 frames) are not integer multiples
    of either period, making the cross-correlation peak unambiguous.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float) * _TICK
    sig = (
        0.5
        + 0.20 * np.sin(2 * math.pi * t / 11.3)
        + 0.12 * np.sin(2 * math.pi * t / 4.7 + 0.8)
        + 0.03 * rng.standard_normal(n)
    )
    return list(float(v) for v in np.clip(sig, 0.0, 1.0))


def _ts(n: int, tick: float = _TICK) -> list[float]:
    return [i * tick for i in range(n)]


# ---------------------------------------------------------------------------
# _quality_label
# ---------------------------------------------------------------------------

def test_quality_excellent():
    assert _quality_label(0.90) == "excellent"


def test_quality_good():
    assert _quality_label(0.75) == "good"


def test_quality_fair():
    assert _quality_label(0.60) == "fair"


def test_quality_poor_below_min():
    assert _quality_label(0.30) == "poor"
    assert _quality_label(MIN_ACCEPTABLE_CORRELATION - 0.01) == "poor"


def test_quality_at_boundaries():
    assert _quality_label(0.85) == "excellent"
    assert _quality_label(0.70) == "good"
    assert _quality_label(0.50) == "fair"


# ---------------------------------------------------------------------------
# _to_uniform_grid
# ---------------------------------------------------------------------------

def test_to_uniform_grid_all_valid():
    ts = [0.0, 1.0, 2.0]
    vs = [0.0, 0.5, 1.0]
    grid = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    out = _to_uniform_grid(ts, vs, grid)
    assert out == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])


def test_to_uniform_grid_with_none():
    # None values are skipped — only non-None (t, v) pairs are used.
    # Grid points before the first valid t get left-extrapolated.
    ts = [0.0, 1.0, 2.0]
    vs = [None, 0.5, 1.0]
    grid = np.array([0.0, 1.0, 1.5, 2.0])
    out = _to_uniform_grid(ts, vs, grid)
    assert out[0] == pytest.approx(0.5)    # t=0.0: left extrapolation to first valid (0.5)
    assert out[1] == pytest.approx(0.5)    # t=1.0: exactly at first valid point
    assert out[2] == pytest.approx(0.75)   # t=1.5: interpolated between (1.0,0.5) and (2.0,1.0)
    assert out[3] == pytest.approx(1.0)    # t=2.0: at second valid point


def test_to_uniform_grid_all_none():
    out = _to_uniform_grid([0.0, 1.0], [None, None], np.array([0.0, 0.5, 1.0]))
    assert list(out) == pytest.approx([0.0, 0.0, 0.0])


def test_to_uniform_grid_extrapolation_right():
    ts = [0.0, 1.0]
    vs = [0.2, 0.8]
    grid = np.array([0.0, 1.0, 2.0, 3.0])
    out = _to_uniform_grid(ts, vs, grid)
    # Beyond t=1.0: right-extrapolate to 0.8
    assert out[2] == pytest.approx(0.8)
    assert out[3] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# align — known offset recovery
# ---------------------------------------------------------------------------

def test_align_zero_offset():
    """When capture SE == audio SE (zero offset), recovered offset should be ≈ 0."""
    n = 400
    se = _smooth_se(n)
    ts = _ts(n)
    result = align(ts, se, ts, se, tick_s=_TICK, search_range_s=30.0)
    assert abs(result.offset_s) < 2 * _TICK   # within one frame
    assert result.correlation > 0.95


def test_align_positive_offset():
    """Capture starts some seconds into the audio — offset should be recovered."""
    n_audio = 600   # 20 s at 30 Hz
    offset_frames = 90   # 3 s
    se = _smooth_se(n_audio)
    audio_ts = _ts(n_audio)
    audio_se = se

    # Capture: sub-series starting at frame 90 → capture_ts starts at 0
    capture_ts = _ts(n_audio - offset_frames)
    capture_se = se[offset_frames:]

    expected_offset_s = offset_frames * _TICK
    result = align(audio_ts, audio_se, capture_ts, capture_se,
                   tick_s=_TICK, search_range_s=15.0)
    assert abs(result.offset_s - expected_offset_s) < 2 * _TICK, (
        f"expected offset ≈{expected_offset_s:.3f}s, got {result.offset_s:.3f}s"
    )
    assert result.correlation > 0.85


def test_align_larger_offset():
    """Offset of ~10 s (common case: startup delay) is recovered correctly."""
    n_audio = 900   # 30 s at 30 Hz
    offset_frames = 300  # 10 s
    se = _smooth_se(n_audio, seed=7)
    audio_ts = _ts(n_audio)

    capture_ts = _ts(n_audio - offset_frames)
    capture_se = se[offset_frames:]

    expected = offset_frames * _TICK
    result = align(audio_ts, se, capture_ts, capture_se,
                   tick_s=_TICK, search_range_s=20.0)
    assert abs(result.offset_s - expected) < 2 * _TICK, (
        f"expected {expected:.2f} s, got {result.offset_s:.2f} s"
    )
    assert result.correlation > 0.70


def test_align_offset_outside_search_range_not_found():
    """When true offset exceeds search_range_s, the result will be wrong but won't crash."""
    n_audio = 600
    offset_frames = 450   # 15 s
    se = _smooth_se(n_audio, seed=13)
    audio_ts = _ts(n_audio)

    capture_ts = _ts(n_audio - offset_frames)
    capture_se = se[offset_frames:]

    # Only search ±5 s — true offset (15 s) is outside.
    result = align(audio_ts, se, capture_ts, capture_se,
                   tick_s=_TICK, search_range_s=5.0)
    # Should not crash; offset should be within the search range.
    assert abs(result.offset_s) <= 5.0 + _TICK


def test_align_deterministic():
    """Same inputs always produce the same result (no randomness)."""
    n = 400
    offset_frames = 100
    se = _smooth_se(n)
    audio_ts = _ts(n)
    capture_ts = _ts(n - offset_frames)
    capture_se = se[offset_frames:]
    r1 = align(audio_ts, se, capture_ts, capture_se, tick_s=_TICK, search_range_s=15.0)
    r2 = align(audio_ts, se, capture_ts, capture_se, tick_s=_TICK, search_range_s=15.0)
    assert r1.offset_s == r2.offset_s
    assert r1.correlation == r2.correlation


# ---------------------------------------------------------------------------
# align — edge cases
# ---------------------------------------------------------------------------

def test_align_empty_audio_returns_poor():
    result = align([], [], _ts(10), _smooth_se(10), tick_s=_TICK)
    assert result.quality == "poor"
    assert result.correlation == pytest.approx(0.0)


def test_align_empty_capture_returns_poor():
    result = align(_ts(10), _smooth_se(10), [], [], tick_s=_TICK)
    assert result.quality == "poor"


def test_align_all_none_capture_returns_poor():
    """If capture has no SE values, correlation will be near zero → poor."""
    n = 300
    se = _smooth_se(n)
    result = align(
        _ts(n), se,
        _ts(n), [None] * n,
        tick_s=_TICK, search_range_s=20.0,
    )
    assert result.correlation < MIN_ACCEPTABLE_CORRELATION
    assert result.quality == "poor"


def test_align_random_noise_gives_low_correlation():
    """Uncorrelated noise → correlation near zero → quality poor or fair."""
    rng = np.random.default_rng(0)
    n = 400
    a = list(float(v) for v in rng.uniform(0, 1, n))
    b = list(float(v) for v in rng.uniform(0, 1, n))
    result = align(_ts(n), a, _ts(n), b, tick_s=_TICK, search_range_s=10.0)
    assert result.correlation < 0.5


def test_align_truncated_overlap():
    """Audio much shorter than capture — still returns a result, overlap noted."""
    n_audio = 150   # 5 s
    n_capture = 300  # 10 s
    se = _smooth_se(max(n_audio, n_capture) + 50, seed=3)
    audio_ts = _ts(n_audio)
    audio_se = se[:n_audio]
    capture_ts = _ts(n_capture)
    capture_se = se[30: 30 + n_capture]   # offset ≈ 1 s

    result = align(audio_ts, audio_se, capture_ts, capture_se,
                   tick_s=_TICK, search_range_s=10.0)
    # With limited overlap the correlation may be lower but the tool shouldn't crash.
    assert isinstance(result, AlignmentResult)
    assert result.n_overlap >= 0


def test_align_capture_with_leading_nones():
    """Capture SE with None values at the start (tracker warmup) is handled."""
    n = 400
    offset_frames = 60  # 2 s
    se = _smooth_se(n + offset_frames, seed=17)
    audio_ts = _ts(n + offset_frames)

    # Capture starts at offset_frames; first 5 frames are None (warmup).
    capture_ts = _ts(n)
    capture_se_raw = se[offset_frames:]
    capture_se = [None] * 5 + capture_se_raw[5:]

    expected = offset_frames * _TICK
    result = align(audio_ts, se, capture_ts, capture_se,
                   tick_s=_TICK, search_range_s=10.0)
    assert abs(result.offset_s - expected) < 3 * _TICK, (
        f"expected {expected:.2f} s, got {result.offset_s:.2f} s"
    )


# ---------------------------------------------------------------------------
# AlignmentResult quality — integration
# ---------------------------------------------------------------------------

def test_alignment_quality_label_matches_threshold():
    """Result.quality must be consistent with result.correlation."""
    n = 400
    se = _smooth_se(n)
    result = align(_ts(n), se, _ts(n), se, tick_s=_TICK, search_range_s=5.0)
    assert _quality_label(result.correlation) == result.quality


def test_alignment_n_overlap_positive_for_matching_series():
    n = 400
    offset_frames = 30
    se = _smooth_se(n)
    result = align(
        _ts(n), se,
        _ts(n - offset_frames), se[offset_frames:],
        tick_s=_TICK, search_range_s=5.0,
    )
    assert result.n_overlap > 0
