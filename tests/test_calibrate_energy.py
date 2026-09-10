"""Tests for calibrate_energy.py — CLI helpers.

Focuses on the cadence-derivation fix: _capture_tick_s() must extract median dt
from capture timestamps rather than relying on a hardcoded constant.
"""
from __future__ import annotations

import math
import types

import numpy as np
import pytest
from calib_audio import PRODUCTION_TICK_S
from calibrate_energy import _capture_tick_s, _compute_error_metrics, _pearson

# ---------------------------------------------------------------------------
# Minimal Row stub (captures the .t and .se attributes used by the helpers)
# ---------------------------------------------------------------------------

def _rows(ts: list[float], se_vals: list[float | None] | None = None):
    """Return a list of minimal Row-like objects."""
    if se_vals is None:
        se_vals = [0.5] * len(ts)
    return [types.SimpleNamespace(t=t, se=se) for t, se in zip(ts, se_vals, strict=True)]


# ---------------------------------------------------------------------------
# _capture_tick_s
# ---------------------------------------------------------------------------

def test_capture_tick_s_uniform_20hz():
    """Uniform 20 Hz timestamps → tick_s ≈ 0.05."""
    rows = _rows([i * 0.05 for i in range(100)])
    assert _capture_tick_s(rows) == pytest.approx(0.05, abs=1e-9)


def test_capture_tick_s_uniform_30hz():
    """Uniform 30 Hz timestamps → tick_s ≈ 1/30."""
    rows = _rows([i / 30.0 for i in range(60)])
    assert _capture_tick_s(rows) == pytest.approx(1.0 / 30.0, abs=1e-9)


def test_capture_tick_s_noisy_timestamps():
    """Median is robust to a few outlier dt values."""
    rng = np.random.default_rng(42)
    base_dt = 0.0503
    ts = np.cumsum(np.full(200, base_dt) + rng.uniform(-0.002, 0.002, 200))
    rows = _rows(list(ts))
    assert _capture_tick_s(rows) == pytest.approx(base_dt, abs=0.005)


def test_capture_tick_s_single_row_fallback():
    """Single row → fall back to PRODUCTION_TICK_S (can't compute dt)."""
    rows = _rows([0.0])
    assert _capture_tick_s(rows) == pytest.approx(PRODUCTION_TICK_S)


def test_capture_tick_s_two_rows():
    """Two rows → median of one dt value."""
    rows = _rows([0.0, 0.0503])
    assert _capture_tick_s(rows) == pytest.approx(0.0503, abs=1e-9)


def test_capture_tick_s_differs_from_production_constant():
    """Captures at ~20 Hz give tick_s that differs meaningfully from 1/30."""
    rows = _rows([i * 0.0503 for i in range(200)])
    tick_s = _capture_tick_s(rows)
    assert abs(tick_s - PRODUCTION_TICK_S) > 0.01, (
        "Expected capture cadence to differ from PRODUCTION_TICK_S for ~20 Hz captures"
    )


# ---------------------------------------------------------------------------
# _pearson
# ---------------------------------------------------------------------------

def test_pearson_identical():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert _pearson(a, a.copy()) == pytest.approx(1.0)


def test_pearson_anticorrelated():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert _pearson(a, -a) == pytest.approx(-1.0)


def test_pearson_constant_returns_zero():
    a = np.array([0.5, 0.5, 0.5])
    b = np.array([0.1, 0.2, 0.3])
    assert _pearson(a, b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _compute_error_metrics
# ---------------------------------------------------------------------------

def _make_recon(ts: list[float], se: list[float | None], tick_s: float = 1.0 / 30.0):
    """Minimal SEReconstruction-like object."""
    import types
    info = types.SimpleNamespace(
        path="", duration_s=ts[-1] if ts else 0.0,
        sample_rate=44100, n_channels=1, format="WAV", n_ticks=len(ts),
    )
    return types.SimpleNamespace(
        timestamps_s=ts,
        se_values=se,
        audio_info=info,
        tick_s=tick_s,
    )


def test_compute_error_metrics_perfect_alignment():
    """Audio SE == capture SE, zero offset → MAE=0, RMSE=0, r=1."""
    n = 60
    tick_s = 1.0 / 30.0
    ts = [i * tick_s for i in range(n)]
    se = [0.3 + 0.1 * math.sin(2 * math.pi * i / 20) for i in range(n)]
    recon = _make_recon(ts, se, tick_s)
    rows = _rows(ts, se)
    m = _compute_error_metrics(recon, rows, offset_s=0.0)
    assert m is not None
    assert m["mae"] == pytest.approx(0.0, abs=1e-9)
    assert m["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert m["bias"] == pytest.approx(0.0, abs=1e-9)
    assert m["r_by_excl"][0] == pytest.approx(1.0, abs=1e-6)


def test_compute_error_metrics_returns_none_for_short_overlap():
    """When < 10 frames overlap, return None rather than unreliable metrics."""
    n = 5
    ts = [i * 0.05 for i in range(n)]
    se = [0.5] * n
    recon = _make_recon(ts, se)
    rows = _rows(ts, se)
    assert _compute_error_metrics(recon, rows, offset_s=0.0) is None


def test_compute_error_metrics_constant_bias():
    """Audio uniformly higher than capture → positive bias = actual difference."""
    n = 30
    tick_s = 1.0 / 30.0
    ts = [i * tick_s for i in range(n)]
    se_audio = [0.6] * n
    se_cap   = [0.4] * n
    recon = _make_recon(ts, se_audio, tick_s)
    rows = _rows(ts, se_cap)
    m = _compute_error_metrics(recon, rows, offset_s=0.0)
    assert m is not None
    assert m["bias"] == pytest.approx(0.2, abs=1e-6)
    assert m["mae"]  == pytest.approx(0.2, abs=1e-6)


def test_compute_error_metrics_startup_exclusion():
    """r_by_excl[0] includes all frames; r_by_excl[10] excludes the first 10 s."""
    tick_s = 0.05   # 20 Hz
    n = 400          # 20 s
    ts = [i * tick_s for i in range(n)]
    # Varying base signal so Pearson r is well-defined throughout.
    base = [0.5 + 0.2 * math.sin(2 * math.pi * i / 50) for i in range(n)]
    se_audio = base[:]
    # Capture has a large additive error in the first 10 s, then matches audio.
    se_cap = [v + (0.3 if t < 10.0 else 0.0) for v, t in zip(base, ts, strict=True)]
    recon = _make_recon(ts, se_audio, tick_s)
    rows = _rows(ts, se_cap)
    m = _compute_error_metrics(recon, rows, offset_s=0.0)
    assert m is not None
    # Full: r includes the first-10s offset → noticeably lower than perfect
    assert m["r_by_excl"][0] < 0.99
    # excl 10s: audio and cap are identical → r ≈ 1
    assert m["r_by_excl"][10] == pytest.approx(1.0, abs=1e-6)


def test_compute_error_metrics_none_when_no_audio_se():
    """No valid audio SE → None."""
    n = 20
    ts = [i * 0.05 for i in range(n)]
    recon = _make_recon(ts, [None] * n)
    rows = _rows(ts)
    assert _compute_error_metrics(recon, rows, offset_s=0.0) is None
