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
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPTS_DIR), "src")
for _p in (_SCRIPTS_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analyse_energy import load_csv  # noqa: E402
from calib_alignment import MIN_ACCEPTABLE_CORRELATION, align  # noqa: E402
from calib_audio import load_and_reconstruct, se_stats  # noqa: E402

_DIV = "─" * 70


def _print_audio_info(recon) -> None:
    info = recon.audio_info
    stats = se_stats(recon.se_values)
    print(f"\n{_DIV}")
    print("  Audio")
    print(f"  File       : {info.path}")
    print(f"  Format     : {info.format}  {info.n_channels}-ch  {info.sample_rate:,} Hz")
    dur_s = info.duration_s
    print(f"  Duration   : {dur_s:.2f} s  ({int(dur_s//60)}m {dur_s%60:.1f}s)")
    print(f"  SE ticks   : {stats['n_valid']:,} valid / {stats['n_total']:,} total")
    if stats["mean"] is not None:
        print(
            f"  SE range   : {stats['min']:.3f} – {stats['max']:.3f}"
            f"  (mean {stats['mean']:.3f}  p50 {stats['p50']:.3f})"
        )


def _print_capture_info(rows) -> None:
    se_n = sum(1 for r in rows if r.se is not None)
    t0 = rows[0].t
    t1 = rows[-1].t
    se_vals = [r.se for r in rows if r.se is not None]
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


def _print_alignment(result) -> None:
    print(f"\n{_DIV}")
    print("  Alignment")
    print(f"  Offset     : {result.offset_s:+.2f} s  "
          f"(track_time = capture_time + offset)")
    print(f"  Correlation: {result.correlation:.4f}")
    print(f"  Quality    : {result.quality}")
    print(f"  Overlap    : ~{result.n_overlap:,} ticks")
    print(f"  Searched   : ±{result.search_range_s:.0f} s")


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

    # M1 — audio decode + SE reconstruction
    try:
        recon = load_and_reconstruct(args.audio)
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR reading audio: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load capture CSV
    try:
        rows = load_csv(args.capture)
    except (OSError, ValueError) as exc:
        print(f"ERROR reading capture: {exc}", file=sys.stderr)
        sys.exit(1)
    if not rows:
        print("ERROR: capture CSV has no data rows.", file=sys.stderr)
        sys.exit(1)

    _print_audio_info(recon)
    _print_capture_info(rows)

    # M2 — alignment
    result = align(
        audio_ts=recon.timestamps_s,
        audio_se=recon.se_values,
        capture_ts=[r.t for r in rows],
        capture_se=[r.se for r in rows],
        tick_s=recon.tick_s,
        search_range_s=args.search_range,
    )
    _print_alignment(result)

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
