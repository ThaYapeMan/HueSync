"""Tests for analyse_energy.py — offline EnergyProfile tuning tool.

Covers simulate(), blend-input selection (SE vs rel_ex), _first_cross(),
_band_pcts(), and load_csv() column-name fallback.
"""

from __future__ import annotations

import io

import pytest
from analyse_energy import (
    ALL_CANDIDATES,
    Candidate,
    Row,
    _band_pcts,
    _first_down_cross,
    _first_up_cross,
    _interp_cross,
    _minmeanmax,
    _parse_csv,
    simulate,
)

# ---------------------------------------------------------------------------
# Row.blend_input — SE prioritised over rel_ex
# ---------------------------------------------------------------------------

def test_blend_input_uses_se_when_available():
    r = Row(t=0.0, rel_ex=0.1, captured_mix=0.5, se=0.8)
    assert r.blend_input == pytest.approx(0.8)


def test_blend_input_falls_back_to_rel_ex_when_se_none():
    r = Row(t=0.0, rel_ex=0.3, captured_mix=0.5, se=None)
    assert r.blend_input == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# simulate() — mirrors LayerMixer.render() exactly
# ---------------------------------------------------------------------------

_C = Candidate("test", blend_start=0.3, blend_end=0.7, blend_response=0.10, speed_label="current")


def _rows_constant(blend_val: float, n: int = 30) -> list[Row]:
    return [Row(t=float(i), rel_ex=blend_val, captured_mix=0.0, se=None) for i in range(n)]


def test_simulate_starts_from_zero():
    rows = _rows_constant(1.0, n=1)
    result = simulate(rows, _C)
    t0, m0 = result[0]
    assert t0 == pytest.approx(1.0)        # target = smoothstep(1.0, 0.3, 0.7) = 1.0
    assert m0 == pytest.approx(0.10)       # mix += 0.10 * (1.0 - 0.0) = 0.10


def test_simulate_converges_toward_target():
    rows = _rows_constant(1.0, n=100)
    result = simulate(rows, _C)
    targets = [r[0] for r in result]
    mixes   = [r[1] for r in result]
    assert all(t == pytest.approx(1.0) for t in targets)
    # After 100 frames at alpha=0.10, mix should be very close to 1.0
    assert mixes[-1] == pytest.approx(1.0, abs=0.0001)


def test_simulate_below_blend_start_gives_zero_target():
    rows = _rows_constant(0.1, n=5)   # 0.1 < blend_start=0.3
    result = simulate(rows, _C)
    for tgt, _ in result:
        assert tgt == pytest.approx(0.0)


def test_simulate_above_blend_end_gives_one_target():
    rows = _rows_constant(0.9, n=5)   # 0.9 > blend_end=0.7
    result = simulate(rows, _C)
    for tgt, _ in result:
        assert tgt == pytest.approx(1.0)


def test_simulate_uses_se_as_blend_input():
    """If Row has se != None, simulate must use se, not rel_ex."""
    # se=0.9 (> blend_end) → target 1.0; rel_ex=0.1 (< blend_start) → target 0.0
    rows = [Row(t=float(i), rel_ex=0.1, captured_mix=0.0, se=0.9) for i in range(5)]
    result = simulate(rows, _C)
    for tgt, _ in result:
        assert tgt == pytest.approx(1.0)


def test_simulate_uses_rel_ex_fallback_when_se_none():
    rows = [Row(t=float(i), rel_ex=0.1, captured_mix=0.0, se=None) for i in range(5)]
    result = simulate(rows, _C)
    for tgt, _ in result:
        assert tgt == pytest.approx(0.0)


def test_simulate_faster_blend_response_converges_sooner():
    c_slow = Candidate("slow", 0.3, 0.7, 0.05, "slow")
    c_fast = Candidate("fast", 0.3, 0.7, 0.20, "fast")
    rows = _rows_constant(1.0, n=20)
    slow_mix = [m for _, m in simulate(rows, c_slow)]
    fast_mix = [m for _, m in simulate(rows, c_fast)]
    # At every frame past frame 0, faster alpha should yield a higher mix
    for s, f in zip(slow_mix[1:], fast_mix[1:], strict=True):
        assert f > s


def test_simulate_returns_one_tuple_per_row():
    rows = _rows_constant(0.5, n=17)
    result = simulate(rows, _C)
    assert len(result) == 17


# ---------------------------------------------------------------------------
# _interp_cross()
# ---------------------------------------------------------------------------

def _ts(n: int) -> list[float]:
    return [float(i) for i in range(n)]


def test_interp_cross_midpoint():
    # v_prev=0.0, v_curr=1.0, threshold=0.5, t_prev=0.0, t_curr=1.0 → 0.5
    assert _interp_cross(0.0, 0.0, 1.0, 1.0, 0.5) == pytest.approx(0.5)


def test_interp_cross_at_boundary():
    # threshold == v_prev → fraction = 0 → t_prev
    assert _interp_cross(0.0, 0.5, 1.0, 0.8, 0.5) == pytest.approx(0.0)


def test_interp_cross_zero_delta_returns_t_curr():
    # degenerate: v_prev == v_curr → returns t_curr
    assert _interp_cross(0.0, 0.5, 1.0, 0.5, 0.5) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _first_up_cross()
# ---------------------------------------------------------------------------

def test_up_cross_starts_below_crosses_upward():
    # [0.0, 0.3, 0.8] — crosses 0.5 between index 1 (0.3) and 2 (0.8)
    vals = [0.0, 0.3, 0.8]
    ts   = _ts(3)
    # interp: t=1 + (0.5-0.3)/(0.8-0.3) * 1 = 1 + 0.4 = 1.4
    assert _first_up_cross(vals, ts, 0.5) == pytest.approx(1.4)


def test_up_cross_starts_above_no_crossing_until_dip_and_rise():
    # [0.9, 0.3, 0.8] — starts above, dips below, rises above again
    # first upward crossing is at i=2 (vals[1]=0.3 < 0.5 <= vals[2]=0.8)
    vals = [0.9, 0.3, 0.8]
    ts   = _ts(3)
    assert _first_up_cross(vals, ts, 0.5) == pytest.approx(1.4)


def test_up_cross_starts_above_never_falls():
    # [0.9, 0.8, 0.7] — always above 0.5; no upward crossing
    vals = [0.9, 0.8, 0.7]
    assert _first_up_cross(vals, _ts(3), 0.5) is None


def test_up_cross_starts_above_only_one_sample():
    vals = [0.99]
    assert _first_up_cross(vals, _ts(1), 0.5) is None


def test_up_cross_never_reaches_threshold():
    vals = [0.1, 0.2, 0.3]
    assert _first_up_cross(vals, _ts(3), 0.5) is None


def test_up_cross_exact_threshold_value():
    # vals[1] == threshold exactly → counts as crossing (>= threshold)
    vals = [0.0, 0.5, 0.8]
    ts   = _ts(3)
    # interp: t=0 + (0.5-0.0)/(0.5-0.0)*1 = 1.0
    assert _first_up_cross(vals, ts, 0.5) == pytest.approx(1.0)


def test_up_cross_oscillation_reports_first():
    # oscillates above and below: [0.0, 0.8, 0.2, 0.9]
    # first upward crossing at i=1 (0.0 → 0.8)
    vals = [0.0, 0.8, 0.2, 0.9]
    ts   = _ts(4)
    result = _first_up_cross(vals, ts, 0.5)
    # interp between ts[0]=0, v=0.0 and ts[1]=1, v=0.8: t=0 + 0.5/0.8 = 0.625
    assert result == pytest.approx(0.5 / 0.8)


def test_up_cross_missing_se_uses_rel_ex():
    """Up-cross works on mix sequences derived from rel_ex fallback (se=None).

    With alpha=0.10, 10 silent frames then 10 loud frames: mix crosses 0.50
    around frame 16 (1 - 0.9^6 ≈ 0.47 → 1 - 0.9^7 ≈ 0.52).
    """
    rows = [Row(t=float(i), rel_ex=0.9 if i >= 10 else 0.1, captured_mix=0.0, se=None)
            for i in range(20)]
    c = Candidate("t", 0.3, 0.7, 0.10, "current")
    sim = simulate(rows, c)
    mix_w = [m for _, m in sim]
    ts_w  = [float(i) for i in range(20)]
    result = _first_up_cross(mix_w, ts_w, 0.50)
    assert result is not None
    assert result > 10.0   # can only rise after signal goes high at frame 10


# ---------------------------------------------------------------------------
# _first_down_cross()
# ---------------------------------------------------------------------------

def test_down_cross_starts_above_falls_below():
    # [0.9, 0.3, 0.1] — falls below 0.5 between index 0 (0.9) and 1 (0.3)
    vals = [0.9, 0.3, 0.1]
    ts   = _ts(3)
    # interp: t=0 + (0.5-0.9)/(0.3-0.9)*1 = 0 + (-0.4/-0.6) = 0.667
    assert _first_down_cross(vals, ts, 0.5) == pytest.approx((-0.4) / (-0.6))


def test_down_cross_starts_below_never_fires_immediately():
    # starts below 0.5; no downward crossing unless it first goes above
    vals = [0.3, 0.8, 0.2]
    ts   = _ts(3)
    # i=1: vals[0]=0.3 < 0.5, vals[1]=0.8 → NOT a down crossing
    # i=2: vals[1]=0.8 >= 0.5, vals[2]=0.2 → down crossing between i=1 and i=2
    result = _first_down_cross(vals, ts, 0.5)
    assert result == pytest.approx(1 + (0.5 - 0.8) / (0.2 - 0.8))


def test_down_cross_never_reaches_threshold():
    vals = [0.1, 0.2, 0.3]
    assert _first_down_cross(vals, _ts(3), 0.5) is None


def test_down_cross_starts_above_stays_above():
    vals = [0.9, 0.8, 0.7]
    assert _first_down_cross(vals, _ts(3), 0.5) is None


def test_down_cross_oscillation_reports_first():
    # [0.0, 0.8, 0.2, 0.9] — first down crossing at i=2 (0.8 → 0.2)
    vals = [0.0, 0.8, 0.2, 0.9]
    ts   = _ts(4)
    result = _first_down_cross(vals, ts, 0.5)
    # interp between ts[1]=1 v=0.8 and ts[2]=2 v=0.2: t=1 + (0.5-0.8)/(0.2-0.8)*1
    expected = 1 + (0.5 - 0.8) / (0.2 - 0.8)
    assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _band_pcts()
# ---------------------------------------------------------------------------

def test_band_pcts_empty():
    assert _band_pcts([]) == (0.0, 0.0, 0.0, 0.0)


def test_band_pcts_all_below_10():
    vals = [0.05, 0.05, 0.05]
    lo, mid_lo, mid_hi, hi = _band_pcts(vals)
    assert lo == pytest.approx(100.0)
    assert mid_lo == pytest.approx(0.0)
    assert mid_hi == pytest.approx(0.0)
    assert hi == pytest.approx(0.0)


def test_band_pcts_all_above_90():
    vals = [0.95, 0.95]
    lo, mid_lo, mid_hi, hi = _band_pcts(vals)
    assert lo == pytest.approx(0.0)
    assert hi == pytest.approx(100.0)


def test_band_pcts_sums_to_100():
    import random
    random.seed(42)
    vals = [random.random() for _ in range(200)]
    lo, m1, m2, hi = _band_pcts(vals)
    assert lo + m1 + m2 + hi == pytest.approx(100.0, abs=0.001)


def test_band_pcts_boundary_10_in_lower_mid():
    # 0.10 should be in mid_lo (10–50%), NOT in lo (<10%)
    lo, mid_lo, _, _ = _band_pcts([0.10])
    assert lo == pytest.approx(0.0)
    assert mid_lo == pytest.approx(100.0)


def test_band_pcts_boundary_90_in_hi():
    # 0.90 should count as hi (≥90%)
    _, _, _, hi = _band_pcts([0.90])
    assert hi == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# _minmeanmax()
# ---------------------------------------------------------------------------

def test_minmeanmax_empty():
    assert _minmeanmax([]) == "—"


def test_minmeanmax_known():
    result = _minmeanmax([0.0, 0.5, 1.0])
    assert result == "0.000/0.500/1.000"


# ---------------------------------------------------------------------------
# _parse_csv() — column-name compatibility
# ---------------------------------------------------------------------------

_CSV_CURRENT = """\
timestamp_s,relative_exertion,mix,sustained_energy
0.0,0.10,0.05,0.60
1.0,0.20,0.10,0.70
"""

_CSV_LEGACY = """\
timestamp_s,energy,mix
0.0,0.10,0.05
1.0,0.20,0.10
"""

_CSV_NO_SE_COLUMN = """\
timestamp_s,relative_exertion,mix
0.0,0.15,0.08
"""

_CSV_SE_BLANK = """\
timestamp_s,relative_exertion,mix,sustained_energy
0.0,0.15,0.08,
"""


def test_parse_csv_current_columns():
    rows = _parse_csv(io.StringIO(_CSV_CURRENT))
    assert len(rows) == 2
    assert rows[0].t == pytest.approx(0.0)
    assert rows[0].rel_ex == pytest.approx(0.10)
    assert rows[0].captured_mix == pytest.approx(0.05)
    assert rows[0].se == pytest.approx(0.60)


def test_parse_csv_legacy_energy_column():
    rows = _parse_csv(io.StringIO(_CSV_LEGACY))
    assert len(rows) == 2
    assert rows[0].rel_ex == pytest.approx(0.10)
    assert rows[0].se is None   # no SE column


def test_parse_csv_no_se_column_gives_none():
    rows = _parse_csv(io.StringIO(_CSV_NO_SE_COLUMN))
    assert rows[0].se is None


def test_parse_csv_blank_se_gives_none():
    rows = _parse_csv(io.StringIO(_CSV_SE_BLANK))
    assert rows[0].se is None


def test_parse_csv_missing_required_columns_raises():
    bad = "timestamp_s,mix\n0.0,0.5\n"
    with pytest.raises(ValueError, match="missing required columns"):
        _parse_csv(io.StringIO(bad))


def test_parse_csv_empty_raises():
    with pytest.raises(ValueError, match="Empty or header-less"):
        _parse_csv(io.StringIO(""))


# ---------------------------------------------------------------------------
# ALL_CANDIDATES — structural sanity
# ---------------------------------------------------------------------------

def test_all_candidates_contains_current_thresholds():
    labels = [c.label for c in ALL_CANDIDATES]
    assert "current" in labels


def test_all_candidates_blend_response_values_ordered():
    """Slower candidate should have lower blend_response than faster."""
    brs = {c.label: c.blend_response for c in ALL_CANDIDATES}
    assert brs["slower"] < brs["current"] < brs["faster"]
