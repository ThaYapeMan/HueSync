"""Tests for capture_energy.py diagnostic calculations.

Covers _smoothstep, _percentile, and the blend-input selection logic
(_report uses sustained_energy when available, relative_exertion otherwise).
"""

import io
import sys

import pytest
from capture_energy import _pct_above_eq, _pct_below, _percentile, _report, _smoothstep

# ---------------------------------------------------------------------------
# _smoothstep
# ---------------------------------------------------------------------------

def test_smoothstep_below_lo_returns_zero():
    assert _smoothstep(0.0, 0.3, 0.7) == 0.0


def test_smoothstep_above_hi_returns_one():
    assert _smoothstep(1.0, 0.3, 0.7) == 1.0


def test_smoothstep_at_lo_returns_zero():
    assert _smoothstep(0.3, 0.3, 0.7) == 0.0


def test_smoothstep_at_hi_returns_one():
    assert _smoothstep(0.7, 0.3, 0.7) == 1.0


def test_smoothstep_midpoint_returns_half():
    # midpoint t=0.5 → 0.5*0.5*(3-2*0.5) = 0.25*2 = 0.5
    assert _smoothstep(0.5, 0.3, 0.7) == pytest.approx(0.5)


def test_smoothstep_degenerate_lo_eq_hi():
    # lo == hi: guard divides by 1e-9. (x-lo)=0 → t=0 → 0.0; x>lo → t=1 → 1.0.
    assert _smoothstep(0.5, 0.5, 0.5) == 0.0   # x == lo: numerator 0
    assert _smoothstep(0.6, 0.5, 0.5) == 1.0   # x > lo: numerator huge, clamped to 1
    assert _smoothstep(0.4, 0.5, 0.5) == 0.0   # x < lo: clamped to 0


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------

def test_percentile_empty_returns_zero():
    assert _percentile([], 50) == 0.0


def test_percentile_single_value():
    assert _percentile([0.4], 0) == 0.4
    assert _percentile([0.4], 50) == 0.4
    assert _percentile([0.4], 100) == 0.4


def test_percentile_known_values():
    vals = sorted([0.1, 0.3, 0.5, 0.7, 0.9])
    assert _percentile(vals, 0) == pytest.approx(0.1)
    assert _percentile(vals, 100) == pytest.approx(0.9)
    assert _percentile(vals, 50) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _pct_below / _pct_above_eq
# ---------------------------------------------------------------------------

def test_pct_below_all_below():
    assert _pct_below([0.1, 0.2], 0.5, 2) == pytest.approx(100.0)


def test_pct_below_none_below():
    assert _pct_below([0.6, 0.8], 0.5, 2) == pytest.approx(0.0)


def test_pct_above_eq_all_above():
    assert _pct_above_eq([0.6, 0.8], 0.5, 2) == pytest.approx(100.0)


def test_pct_above_eq_boundary_included():
    # value exactly at threshold counts as above_eq
    assert _pct_above_eq([0.5], 0.5, 1) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# _report: blend-input selection (SE vs relative_exertion)
# ---------------------------------------------------------------------------

def _make_samples(
    rel_ex: float,
    mix: float,
    se: float | None,
    n: int = 10,
) -> list[tuple[float, float, float, float | None]]:
    return [(float(i), rel_ex, mix, se) for i in range(n)]


def _capture_stderr(
    samples: list[tuple[float, float, float, float | None]],
    blend_start: float = 0.3,
    blend_end: float = 0.7,
) -> str:
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        _report(samples, blend_start, blend_end, ep_id="test-ep", out_path=None)
        return sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr


def test_report_with_se_uses_se_for_blend_input():
    """When sustained_energy is available, section 3 must say blend_input = sustained_energy."""
    # SE=0.8 → smoothstep(0.8, 0.3, 0.7) = 1.0 (above blend_end)
    samples = _make_samples(rel_ex=0.1, mix=0.9, se=0.8)
    out = _capture_stderr(samples)
    assert "blend_input = sustained_energy" in out
    # Section 3 p99 should be 1.0 (SE=0.8 ≥ blend_end=0.7 → smoothstep=1.0)
    assert "LayerMixer does NOT use this for blend" in out


def test_report_without_se_uses_relative_exertion_fallback():
    """Section 3 labels blend_input as relative_exertion (degraded) when SE is None."""
    samples = _make_samples(rel_ex=0.1, mix=0.05, se=None)
    out = _capture_stderr(samples)
    assert "blend_input = relative_exertion" in out
    assert "degraded" in out
    # Section 2 must say not available
    assert "not available" in out


def test_report_ep_target_weight_uses_se_not_rel_ex():
    """EP target weight must be computed from SE, not from relative_exertion.

    SE=0.8 (above blend_end=0.7) → smoothstep=1.0.
    relative_exertion=0.1 (below blend_start=0.3) → smoothstep=0.0.
    If the section shows high target (>90% time target >90%), blend is from SE.
    If the section shows low target (all below 10%), blend is from relative_exertion.
    """
    samples = _make_samples(rel_ex=0.1, mix=0.9, se=0.8)
    out = _capture_stderr(samples)
    # With SE=0.8 (> blend_end=0.7), ep_target = 1.0 → 100% time target >90%
    assert "Time target >90%   : 100.0%" in out


def test_report_ep_target_weight_fallback_matches_rel_ex():
    """Without SE, EP target uses relative_exertion.

    relative_exertion=0.1 (< blend_start=0.3) → smoothstep=0.0.
    → 100% time target <10%.
    """
    samples = _make_samples(rel_ex=0.1, mix=0.05, se=None)
    out = _capture_stderr(samples)
    assert "Time target <10%   : 100.0%" in out


def test_report_labels_not_ambiguous():
    """The report must not describe relative_exertion-derived weights as what LayerMixer targets
    when sustained_energy IS available."""
    samples = _make_samples(rel_ex=0.1, mix=0.9, se=0.8)
    out = _capture_stderr(samples)
    # The old misleading label must not appear
    assert "This is what LayerMixer targets" not in out
    # The correct sections must be present
    assert "1. Relative exertion" in out
    assert "2. Sustained energy" in out
    assert "3. EnergyProfile instantaneous target" in out
    assert "4. EnergyProfile backend smoothed mix" in out
