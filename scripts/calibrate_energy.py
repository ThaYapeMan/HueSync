#!/usr/bin/env python3
"""Offline energy calibration tool for HueSync EnergyProfile tuning.

M1: Decode audio file and reconstruct SustainedEnergy at production tick rate.
M2: Automatically align audio SE with a HueSync capture CSV via cross-correlation.

Usage:
    python scripts/calibrate_energy.py \\
        --audio "/path/to/Long Jacket.flac" \\
        --capture "/path/to/chris_lake.csv"

    python scripts/calibrate_energy.py \\
        --audio track.flac \\
        --capture capture.csv \\
        --search-range 90

Requirements for audio decoding:
    pip install soundfile        (FLAC/WAV via libsndfile)
    pip install 'huesync[calibration]'   (same, via project extras)

MP3 is not supported.  Transcode to FLAC first:
    ffmpeg -i track.mp3 track.flac

This tool is DIAGNOSTIC ONLY.  It does not modify EnergyProfiles,
production defaults, SustainedEnergyTracker, or any runtime state.

Alignment convention:
    track_time = capture_time + offset_s

If the reported quality is "poor" (r < 0.50), the offset is unreliable.
Ensure the audio file matches the capture session and that sustained_energy
was available during the capture (i.e., a PCM source was active).
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPTS_DIR), "src")
for _p in (_SCRIPTS_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analyse_energy import load_csv  # noqa: E402
from calib_alignment import MIN_ACCEPTABLE_CORRELATION, align  # noqa: E402
from calib_audio import PRODUCTION_TICK_S, load_and_reconstruct, se_stats  # noqa: E402

_DIV = "─" * 70


def _capture_tick_s(rows) -> float:
    """Median inter-sample dt from capture timestamps.

    The SyncEngine calls se_tracker.push(samples, dt) where dt is the actual
    wall-clock elapsed time between ticks.  asyncio.sleep(SEND_INTERVAL_S)
    schedules a 33 ms wakeup, but DTLS streaming overhead makes the actual
    tick period longer (typically ~50 ms on a loaded event loop).

    Using the median dt from the capture timestamps reproduces the exact EMA
    alpha values that the production run used, without hardcoding any rate.

    Falls back to PRODUCTION_TICK_S (1/30) when fewer than 2 rows are present.
    """
    if len(rows) < 2:
        return PRODUCTION_TICK_S
    dts = np.diff([r.t for r in rows])
    return float(np.median(dts))


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation coefficient between two equal-length arrays."""
    a = a - a.mean()
    b = b - b.mean()
    denom = math.sqrt(float(np.sum(a ** 2)) * float(np.sum(b ** 2)))
    return float(np.dot(a, b) / denom) if denom > 0.0 else 0.0


def _compute_error_metrics(recon, rows, offset_s: float) -> dict | None:
    """Interpolate audio SE onto capture timestamps; compute pointwise metrics.

    Returns a dict with:
      n_overlap       — frames in the aligned overlap
      mae, rmse       — mean absolute error, root mean squared error
      bias            — mean(audio − capture); positive = audio reads higher
      r_by_excl       — {0: r_full, 10: r_excl10s, 20: r_excl20s, 30: r_excl30s}
                        Pearson r over the overlap, excluding the first N seconds
                        of the capture (tracker warmup exclusion).
    Returns None if there are fewer than 10 overlapping frames.
    """
    # Build audio SE lookup from non-None points.
    valid = [
        (t, v)
        for t, v in zip(recon.timestamps_s, recon.se_values, strict=True)
        if v is not None
    ]
    if not valid:
        return None
    at, av = zip(*valid, strict=False)
    a_min, a_max = at[0], at[-1]

    # Capture rows mapped into audio time.
    cap_in = [
        (r.t, r.t + offset_s, r.se)
        for r in rows
        if r.se is not None and a_min <= r.t + offset_s <= a_max
    ]
    if len(cap_in) < 10:
        return None

    cap_t_capture = np.array([c[0] for c in cap_in])
    cap_t_audio   = np.array([c[1] for c in cap_in])
    cap_se        = np.array([c[2] for c in cap_in])
    audio_interp  = np.interp(cap_t_audio, at, av)

    diff = audio_interp - cap_se
    mae  = float(np.mean(np.abs(diff)))
    rmse = float(math.sqrt(float(np.mean(diff ** 2))))
    bias = float(np.mean(diff))

    r_by_excl: dict[int, float | None] = {}
    for excl_s in (0, 10, 20, 30):
        mask = cap_t_capture >= excl_s
        if mask.sum() < 10:
            r_by_excl[excl_s] = None
        else:
            r_by_excl[excl_s] = _pearson(audio_interp[mask], cap_se[mask])

    return {
        "n_overlap": len(cap_in),
        "mae": mae,
        "rmse": rmse,
        "bias": bias,
        "r_by_excl": r_by_excl,
    }


def _print_audio_info(recon, sr: int) -> None:
    info = recon.audio_info
    stats = se_stats(recon.se_values)
    rms_window_samples = int(sr * recon.tick_s)
    print(f"\n{_DIV}")
    print("  Audio")
    print(f"  File       : {info.path}")
    print(f"  Format     : {info.format}  {info.n_channels}-ch  {info.sample_rate:,} Hz")
    dur_s = info.duration_s
    print(f"  Duration   : {dur_s:.2f} s  ({int(dur_s // 60)}m {dur_s % 60:.1f}s)")
    print(f"  SE ticks   : {stats['n_valid']:,} valid / {stats['n_total']:,} total")
    if stats["mean"] is not None:
        print(
            f"  SE range   : {stats['min']:.3f} – {stats['max']:.3f}"
            f"  (mean {stats['mean']:.3f}  p50 {stats['p50']:.3f})"
        )
    print(
        f"  Recon rate : {1.0 / recon.tick_s:.1f} Hz"
        f"  (tick_s={recon.tick_s:.4f} s,  RMS window={rms_window_samples:,} samples)"
    )
    print("  Resample   : np.interp linear (independent grids, no padding)")


def _print_capture_info(rows, tick_s: float) -> None:
    se_n = sum(1 for r in rows if r.se is not None)
    t0 = rows[0].t
    t1 = rows[-1].t
    se_vals = [r.se for r in rows if r.se is not None]
    dts = np.diff([r.t for r in rows])
    dt_min, dt_max = float(dts.min()), float(dts.max())
    print(f"\n{_DIV}")
    print("  Capture")
    print(f"  Rows       : {len(rows):,}  ({t0:.2f} – {t1:.2f} s)")
    print(f"  SE frames  : {se_n:,} / {len(rows):,}")
    if se_vals:
        print(
            f"  SE range   : {min(se_vals):.3f} – {max(se_vals):.3f}"
            f"  (mean {sum(se_vals)/len(se_vals):.3f})"
        )
    elif se_n == 0:
        print("  WARNING: no sustained_energy in capture — alignment will fail.")
        print("  Ensure the capture was made with a PCM source (AirPlay/squeezelite) active.")
    print(
        f"  Cadence    : {1.0 / tick_s:.1f} Hz"
        f"  (median dt={tick_s:.4f} s,  range {dt_min:.3f}–{dt_max:.3f} s)"
    )


def _print_cadence_warning(recon_tick_s: float, capture_tick_s: float) -> None:
    ratio = recon_tick_s / capture_tick_s
    if abs(ratio - 1.0) > 0.05:
        print(
            f"\n  *** CADENCE MISMATCH: recon={1/recon_tick_s:.1f} Hz  "
            f"capture={1/capture_tick_s:.1f} Hz — correlation will be reduced ***",
            file=sys.stderr,
        )


def _print_alignment(result, metrics: dict | None) -> None:
    print(f"\n{_DIV}")
    print("  Alignment")
    print(
        f"  Offset     : {result.offset_s:+.2f} s  "
        f"(track_time = capture_time + offset)"
    )
    print(f"  Quality    : {result.quality}  (xcorr r={result.correlation:.4f})")
    print(f"  Overlap    : ~{result.n_overlap:,} ticks  (searched ±{result.search_range_s:.0f} s)")

    if metrics is None:
        return

    print(f"\n  Pointwise Pearson r over aligned overlap ({metrics['n_overlap']:,} frames):")
    rb = metrics["r_by_excl"]
    for excl_s, r_val in sorted(rb.items()):
        label = "full" if excl_s == 0 else f">+{excl_s:2d}s"
        if r_val is None:
            print(f"    excl {label:<8}: —  (< 10 frames)")
        else:
            print(f"    excl {label:<8}: {r_val:.4f}")
    print(
        f"\n  Error metrics (audio − capture):"
        f"  MAE={metrics['mae']:.4f}"
        f"  RMSE={metrics['rmse']:.4f}"
        f"  bias={metrics['bias']:+.4f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--audio", required=True, metavar="FILE",
                    help="Audio file (FLAC or WAV; requires soundfile)")
    ap.add_argument("--capture", required=True, metavar="CSV",
                    help="HueSync energy capture CSV (from capture_energy.py)")
    ap.add_argument(
        "--search-range", type=float, default=60.0, metavar="SECONDS",
        help="Maximum alignment offset to search in either direction (default: 60 s)",
    )
    args = ap.parse_args()

    # Load capture CSV first — we derive reconstruction tick_s from its cadence.
    try:
        rows = load_csv(args.capture)
    except (OSError, ValueError) as exc:
        print(f"ERROR reading capture: {exc}", file=sys.stderr)
        sys.exit(1)
    if not rows:
        print("ERROR: capture CSV has no data rows.", file=sys.stderr)
        sys.exit(1)

    # Derive tick_s from the actual capture cadence (not a hardcoded constant).
    # SyncEngine measures dt = wall-clock elapsed since last tick; asyncio overhead
    # makes it longer than SEND_INTERVAL_S (1/30).  The capture timestamps are the
    # exact times at which se_tracker.push(samples, dt) was called in production.
    tick_s = _capture_tick_s(rows)

    # M1 — audio decode + SE reconstruction at capture cadence
    try:
        recon = load_and_reconstruct(args.audio, tick_s=tick_s)
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR reading audio: {exc}", file=sys.stderr)
        sys.exit(1)

    sr = recon.audio_info.sample_rate
    _print_audio_info(recon, sr)
    _print_capture_info(rows, tick_s)

    # Warn if reconstruction and capture cadences don't match (should never happen
    # after the fix, but catches future regressions).
    _print_cadence_warning(recon.tick_s, tick_s)

    # M2 — alignment
    capture_ts = [r.t for r in rows]
    capture_se = [r.se for r in rows]
    result = align(
        audio_ts=recon.timestamps_s,
        audio_se=recon.se_values,
        capture_ts=capture_ts,
        capture_se=capture_se,
        tick_s=tick_s,
        search_range_s=args.search_range,
    )

    metrics = _compute_error_metrics(recon, rows, result.offset_s)
    _print_alignment(result, metrics)

    if result.correlation < MIN_ACCEPTABLE_CORRELATION:
        print(
            f"\n  *** ALIGNMENT WARNING ***\n"
            f"  Correlation {result.correlation:.3f} is below the minimum acceptable\n"
            f"  threshold ({MIN_ACCEPTABLE_CORRELATION:.2f}).  The reported offset is unreliable.\n"
            f"  Check that:\n"
            f"    - The audio file is the track that was playing during the capture.\n"
            f"    - sustained_energy was available (PCM source active).\n"
            f"    - There is enough musical content in the overlap region.\n"
            f"    - Try --search-range {int(args.search_range * 2)} if the offset may be larger.",
            file=sys.stderr,
        )
        sys.exit(2)

    print()


if __name__ == "__main__":
    main()
