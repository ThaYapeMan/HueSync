#!/usr/bin/env python3
"""Offline analysis of HueSync energy captures produced by capture_energy.py.

Evaluates alternative EnergyProfile settings (blend_start, blend_end,
blend_response) against a captured CSV without replaying audio or touching
runtime state.

Usage:
    python scripts/analyse_energy.py /path/to/energy.csv
    python scripts/analyse_energy.py /tmp/energy.csv --window 41.16:56.16
    python scripts/analyse_energy.py /tmp/energy.csv \\
        --window 41.16:56.16 --window 91.16:109.16

The CSV must have been produced by capture_energy.py (current column names:
relative_exertion, mix, sustained_energy; legacy name "energy" also accepted).

blend_response semantics:
    LayerMixer applies:  mix += blend_response * (target - mix)
    Larger blend_response → faster convergence.
    Approximate convergence time to 63% of step: ~1/blend_response frames.
      0.05 → ~20 frames ≈ 0.67 s  (slower smoothing)
      0.10 → ~10 frames ≈ 0.33 s  (current default)
      0.20 →  ~5 frames ≈ 0.17 s  (faster smoothing)
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from dataclasses import dataclass

# _smoothstep, _percentile, _pct_below, _pct_above_eq imported from
# capture_energy so this script shares the exact same formulas as the backend.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from capture_energy import _pct_above_eq, _pct_below, _percentile, _smoothstep  # noqa: E402

# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class Row:
    t: float
    rel_ex: float           # relative_exertion (features.full)
    captured_mix: float     # actual backend mix from the capture session
    se: float | None        # sustained_energy; None = unavailable that frame

    @property
    def blend_input(self) -> float:
        """Signal LayerMixer uses: sustained_energy when available, else rel_ex.

        Must mirror LayerMixer.render() exactly — do not change independently.
        """
        return self.se if self.se is not None else self.rel_ex


@dataclass(frozen=True)
class Candidate:
    label: str
    blend_start: float
    blend_end: float
    blend_response: float   # EMA alpha; larger → faster convergence
    speed_label: str        # "current" | "slower smoothing" | "faster smoothing"


# ── Candidate definitions ──────────────────────────────────────────────────────

# blend_response default (EnergyProfile model): 0.10
_BR_CURRENT: float = 0.10
_BR_SLOWER:  float = 0.05   # ≈ 0.5× effective speed (2× time to converge)
_BR_FASTER:  float = 0.20   # ≈ 2× effective speed (0.5× time to converge)

THRESHOLD_CANDIDATES: list[Candidate] = [
    Candidate("current", 0.30, 0.70, _BR_CURRENT, "current"),
    Candidate("A",       0.35, 0.75, _BR_CURRENT, "current"),
    Candidate("B",       0.40, 0.80, _BR_CURRENT, "current"),
    Candidate("C",       0.45, 0.85, _BR_CURRENT, "current"),
]

RESPONSE_CANDIDATES: list[Candidate] = [
    Candidate("slower",  0.30, 0.70, _BR_SLOWER,  "slower smoothing"),
    Candidate("current", 0.30, 0.70, _BR_CURRENT, "current"),
    Candidate("faster",  0.30, 0.70, _BR_FASTER,  "faster smoothing"),
]

# All unique candidates (window detail uses this)
ALL_CANDIDATES: list[Candidate] = [
    *THRESHOLD_CANDIDATES,
    Candidate("slower", 0.30, 0.70, _BR_SLOWER, "slower smoothing"),
    Candidate("faster", 0.30, 0.70, _BR_FASTER, "faster smoothing"),
]


# ── CSV loading ────────────────────────────────────────────────────────────────


def load_csv(path: str) -> list[Row]:
    """Load a CSV produced by capture_energy.py.

    Handles both current column names (relative_exertion, ep_target_weight)
    and the pre-migration names (energy, high_weight).  sustained_energy may
    be absent or blank — treated as None.
    """
    with open(path, newline="") as fh:
        return _parse_csv(fh)


def _parse_csv(fh: io.TextIOBase) -> list[Row]:  # type: ignore[type-arg]
    reader = csv.DictReader(fh)
    if not reader.fieldnames:
        raise ValueError("Empty or header-less CSV.")
    headers = set(reader.fieldnames)

    col_t   = next((h for h in ("timestamp_s",)                      if h in headers), None)
    col_re  = next((h for h in ("relative_exertion", "energy")       if h in headers), None)
    col_mix = next((h for h in ("mix",)                              if h in headers), None)
    col_se  = next((h for h in ("sustained_energy",)                 if h in headers), None)

    if not col_t or not col_re or not col_mix:
        raise ValueError(
            "CSV missing required columns. "
            "Need: timestamp_s, relative_exertion (or energy), mix. "
            f"Found: {sorted(headers)}"
        )

    rows: list[Row] = []
    for raw in reader:
        se_str = (raw.get(col_se) or "") if col_se else ""
        se: float | None = float(se_str) if se_str.strip() else None
        rows.append(Row(
            t=float(raw[col_t]),
            rel_ex=float(raw[col_re]),
            captured_mix=float(raw[col_mix]),
            se=se,
        ))
    return rows


# ── Simulation ─────────────────────────────────────────────────────────────────


def simulate(rows: list[Row], cand: Candidate) -> list[tuple[float, float]]:
    """Simulate LayerMixer for a sequence of rows; return (ep_target, mix) per row.

    Replicates LayerMixer.render() exactly:
        target = smoothstep(blend_input, blend_start, blend_end)
        mix   += blend_response * (target - mix)

    Starts from mix = 0.0 (fresh session).  blend_input = sustained_energy
    when available, relative_exertion otherwise — same as LayerMixer.
    """
    mix = 0.0
    out: list[tuple[float, float]] = []
    for row in rows:
        target = _smoothstep(row.blend_input, cand.blend_start, cand.blend_end)
        mix += cand.blend_response * (target - mix)
        out.append((target, mix))
    return out


# ── Statistics ─────────────────────────────────────────────────────────────────


def _band_pcts(vals: list[float]) -> tuple[float, float, float, float]:
    """Return (<10%, 10–50%, 50–90%, >90%) as floats 0–100."""
    n = len(vals)
    if not n:
        return 0.0, 0.0, 0.0, 0.0
    return (
        _pct_below(vals, 0.10, n),
        100.0 * sum(1 for v in vals if 0.10 <= v < 0.50) / n,
        100.0 * sum(1 for v in vals if 0.50 <= v < 0.90) / n,
        _pct_above_eq(vals, 0.90, n),
    )


def _first_cross(vals: list[float], ts: list[float], threshold: float) -> float | None:
    """Return first timestamp where val >= threshold, or None if never reached."""
    for t, v in zip(ts, vals, strict=True):
        if v >= threshold:
            return t
    return None


def _fmt_rel(t_abs: float | None, t0: float) -> str:
    return f"+{t_abs - t0:.1f}s" if t_abs is not None else "  —  "


def _minmeanmax(vals: list[float]) -> str:
    if not vals:
        return "—"
    return f"{min(vals):.3f}/{sum(vals)/len(vals):.3f}/{max(vals):.3f}"


# ── Report: comparison table ───────────────────────────────────────────────────

_DIV = "─" * 114


def _print_comparison_table(
    full_rows: list[Row],
    candidates: list[Candidate],
    title: str,
    window: tuple[float, float] | None = None,
) -> None:
    """Print a candidate comparison table.

    Simulates the full sequence per candidate (so EMA state is correct at
    any window start), then reports statistics on the window subset.
    """
    if window:
        rep_idx = [i for i, r in enumerate(full_rows) if window[0] <= r.t <= window[1]]
    else:
        rep_idx = list(range(len(full_rows)))

    if not rep_idx:
        print(f"\n  {title}: no samples in range.")
        return

    t_lo = full_rows[rep_idx[0]].t
    t_hi = full_rows[rep_idx[-1]].t
    se_n = sum(1 for i in rep_idx if full_rows[i].se is not None)

    print(f"\n{_DIV}")
    print(f"  {title}")
    print(f"  {len(rep_idx):,} samples  ({t_lo:.1f}–{t_hi:.1f} s)  SE: {se_n}/{len(rep_idx)}")

    print()
    print(
        f"  {'Label':<8} {'BS':>4} {'BE':>4} {'BR':>4}  {'Speed':<18}"
        f"  {'─── EP target ─────────────────':31}"
        f"  {'─── Smoothed mix ─────────────':30}"
    )
    print(
        f"  {'':8} {'':4} {'':4} {'':4}  {'':18}"
        f"  {'<10%':>5} {'10-50':>6} {'50-90':>6} {'>90%':>5} {'  p50':>6}"
        f"  {'<10%':>5} {'10-50':>6} {'50-90':>6} {'>90%':>5} {'  p50':>6}"
    )
    print(f"  {'─'*112}")

    for c in candidates:
        sim = simulate(full_rows, c)
        tgts = [sim[i][0] for i in rep_idx]
        mxs  = [sim[i][1] for i in rep_idx]
        tl, t1050, t5090, tg = _band_pcts(tgts)
        ml, m1050, m5090, mg = _band_pcts(mxs)
        tp50 = _percentile(sorted(tgts), 50)
        mp50 = _percentile(sorted(mxs), 50)
        print(
            f"  {c.label:<8} {c.blend_start:>4.2f} {c.blend_end:>4.2f}"
            f" {c.blend_response:>4.2f}  {c.speed_label:<18}"
            f"  {tl:>5.1f} {t1050:>6.1f} {t5090:>6.1f} {tg:>5.1f} {tp50:>6.3f}"
            f"  {ml:>5.1f} {m1050:>6.1f} {m5090:>6.1f} {mg:>5.1f} {mp50:>6.3f}"
        )

    print(f"  {'─'*112}")


# ── Report: window detail ──────────────────────────────────────────────────────


def _print_window_detail(
    full_rows: list[Row],
    candidates: list[Candidate],
    window: tuple[float, float],
) -> None:
    """Print crossing-time detail for one window.

    Simulates the full sequence (correct EMA state at window start), then
    reports SE/target/mix statistics and first-crossing timestamps relative
    to window start.
    """
    w_idx = [i for i, r in enumerate(full_rows) if window[0] <= r.t <= window[1]]
    if not w_idx:
        print(f"\n  Window {window[0]:.2f}–{window[1]:.2f} s: no samples.")
        return

    ts = [full_rows[i].t for i in w_idx]
    t0 = ts[0]
    se_w = [full_rows[i].se for i in w_idx if full_rows[i].se is not None]
    bi_w = [full_rows[i].blend_input for i in w_idx]

    print(f"\n{_DIV}")
    print(f"  Window detail: {window[0]:.2f}–{window[1]:.2f} s  ({len(w_idx)} samples)")
    print(f"  Sustained energy:  {_minmeanmax(se_w) if se_w else '(not available)'}")
    print(f"  Blend input:       {_minmeanmax(bi_w)}")

    print()
    print(
        f"  {'Label':<8} {'Speed':<18}"
        f"  {'Target (min/mean/max)':<22}"
        f"  {'Mix (min/mean/max)':<22}"
        f"  {'→t≥50%':>7} {'→t≥90%':>7} {'→m≥50%':>7} {'→m≥90%':>7}"
    )
    print(f"  {'─'*106}")

    for c in candidates:
        sim = simulate(full_rows, c)
        tgt_w = [sim[i][0] for i in w_idx]
        mix_w = [sim[i][1] for i in w_idx]
        print(
            f"  {c.label:<8} {c.speed_label:<18}"
            f"  {_minmeanmax(tgt_w):<22}"
            f"  {_minmeanmax(mix_w):<22}"
            f"  {_fmt_rel(_first_cross(tgt_w, ts, 0.50), t0):>7}"
            f" {_fmt_rel(_first_cross(tgt_w, ts, 0.90), t0):>7}"
            f" {_fmt_rel(_first_cross(mix_w, ts, 0.50), t0):>7}"
            f" {_fmt_rel(_first_cross(mix_w, ts, 0.90), t0):>7}"
        )

    print(f"  {'─'*106}")


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("csv", metavar="CSV", help="CSV file from capture_energy.py")
    ap.add_argument(
        "--window", action="append", metavar="START:END",
        help="Analyse a time window in seconds (may be repeated, e.g. --window 41.16:56.16)",
    )
    args = ap.parse_args()

    rows = load_csv(args.csv)
    if not rows:
        print("ERROR: no data rows in CSV.", file=sys.stderr)
        sys.exit(1)

    se_cnt = sum(1 for r in rows if r.se is not None)
    print(f"\n── CSV: {args.csv}")
    print(f"   Samples : {len(rows):,}  duration: {rows[-1].t:.1f} s")
    print(f"   SE available: {se_cnt} / {len(rows)} frames")
    if se_cnt == 0:
        print(
            "   WARNING: no sustained_energy values — all blend inputs use relative_exertion.\n"
            "   Simulation still runs but reflects degraded (cava-only) behaviour."
        )

    # ── Threshold comparison (full capture)
    _print_comparison_table(
        rows, THRESHOLD_CANDIDATES,
        f"Threshold candidates — full {rows[-1].t:.1f} s capture",
    )

    # ── blend_response comparison (full capture, current thresholds)
    _print_comparison_table(
        rows, RESPONSE_CANDIDATES,
        "blend_response candidates — full capture  (BS=0.30, BE=0.70)",
    )

    # ── Per-window detail
    if args.window:
        for w_str in args.window:
            parts = w_str.split(":")
            if len(parts) != 2:
                print(f"ERROR: bad window '{w_str}' — use START:END in seconds.", file=sys.stderr)
                sys.exit(1)
            try:
                window: tuple[float, float] = (float(parts[0]), float(parts[1]))
            except ValueError:
                print(f"ERROR: non-numeric window values in '{w_str}'.", file=sys.stderr)
                sys.exit(1)
            _print_window_detail(rows, ALL_CANDIDATES, window)

    print()


if __name__ == "__main__":
    main()
