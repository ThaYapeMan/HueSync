#!/usr/bin/env python3
"""Capture live energy and blend-weight data from a running HueSync instance.

Run on the LXC using the project venv:

    .venv/bin/python scripts/capture_energy.py --duration 120
    .venv/bin/python scripts/capture_energy.py --host 192.168.1.x --duration 120 --out energy.csv

Summary and histogram are written to stderr; CSV rows to stdout (or --out file).
If no coupling is active, energy will be 0.0 for every sample — start a session
before running this.

Columns in CSV:
    timestamp_s       — seconds since capture start
    relative_exertion — features.full: BandNormaliser exertion (0.0–1.0)
                        This is a transient detector, not section-level loudness.
    mix               — EMA-smoothed LayerMixer crossfade weight (0.0–1.0)
    ep_target_weight  — smoothstep(blend_input, blend_start, blend_end), where
                        blend_input = sustained_energy when available,
                        else relative_exertion (degraded fallback).
                        Matches LayerMixer's instantaneous target before EMA.
    sustained_energy  — section-level energy from SustainedEnergyTracker
                        (0.0–1.0, or blank when PCM source unavailable)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
import urllib.request

try:
    import websockets
except ImportError:
    print(
        "ERROR: 'websockets' not found. Run with .venv/bin/python, or:\n"
        "  .venv/bin/pip install websockets",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _smoothstep(x: float, lo: float, hi: float) -> float:
    """Exact copy of sync_engine._smoothstep — must stay in sync."""
    t = max(0.0, min(1.0, (x - lo) / max(hi - lo, 1e-9)))
    return t * t * (3.0 - 2.0 * t)


def _fetch_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"Warning: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _pct_below(vals: list[float], threshold: float, count: int) -> float:
    return 100.0 * sum(1 for v in vals if v < threshold) / count


def _pct_above_eq(vals: list[float], threshold: float, count: int) -> float:
    return 100.0 * sum(1 for v in vals if v >= threshold) / count


# ---------------------------------------------------------------------------
# WebSocket capture
# ---------------------------------------------------------------------------


async def _capture(
    host: str,
    port: int,
    duration: float,
) -> tuple[list[tuple[float, float, float, float | None]], float, float, str | None]:
    """Connect, collect frames for *duration* seconds.

    Returns (samples, blend_start, blend_end, ep_id).
    Each sample: (timestamp_s, relative_exertion, mix, sustained_energy|None).
    """
    url = f"ws://{host}:{port}/ws/preview"
    api_base = f"http://{host}:{port}/api"

    samples: list[tuple[float, float, float, float | None]] = []
    blend_start = 0.3
    blend_end = 0.7
    ep_fetched = False
    active_ep_id: str | None = None
    start_t: float | None = None

    print(f"Connecting to {url} …", file=sys.stderr)
    try:
        async with websockets.connect(url) as ws:
            print(
                f"Connected. Capturing for {duration:.0f} s "
                f"(play music on an active coupling now) …",
                file=sys.stderr,
            )
            start_t = time.monotonic()
            deadline = start_t + duration

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining + 0.1, 2.0))
                except TimeoutError:
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                if mtype == "frame":
                    rel_ex = float(msg.get("energy", 0.0))
                    mix = float(msg.get("mix", 0.0))
                    se_raw = msg.get("sustained_energy")
                    se: float | None = float(se_raw) if se_raw is not None else None
                    samples.append((time.monotonic() - start_t, rel_ex, mix, se))
                elif mtype == "status" and not ep_fetched:
                    ep_id = msg.get("active_energy_profile_id")
                    if ep_id:
                        active_ep_id = ep_id
                        ep_fetched = True
                        profile = _fetch_json(f"{api_base}/energy-profiles/{ep_id}")
                        if profile:
                            blend_start = float(profile.get("blend_start", 0.3))
                            blend_end = float(profile.get("blend_end", 0.7))
                            print(
                                f"Active energy profile: {profile.get('name', ep_id)!r}\n"
                                f"  blend_start={blend_start}  blend_end={blend_end}",
                                file=sys.stderr,
                            )
    except OSError as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        if not samples:
            sys.exit(1)

    return samples, blend_start, blend_end, active_ep_id


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _report(
    samples: list[tuple[float, float, float, float | None]],
    blend_start: float,
    blend_end: float,
    ep_id: str | None,
    out_path: str | None,
) -> None:
    if not samples:
        print("No samples collected. Was a coupling active?", file=sys.stderr)
        return

    rel_exs: list[float] = [e for _, e, _, _ in samples]
    mixes:   list[float] = [m for _, _, m, _ in samples]
    se_vals: list[float | None] = [se for _, _, _, se in samples]
    n = len(rel_exs)

    # EnergyProfile instantaneous target — mirrors LayerMixer.render() exactly:
    #   blend_input = sustained_energy when available, else relative_exertion.
    se_present_count = sum(1 for se in se_vals if se is not None)
    se_available = se_present_count > 0
    ep_targets: list[float] = [
        _smoothstep(
            se if se is not None else re,
            blend_start,
            blend_end,
        )
        for re, se in zip(rel_exs, se_vals, strict=True)
    ]

    # CSV
    rows = [
        ["timestamp_s", "relative_exertion", "mix", "ep_target_weight", "sustained_energy"]
    ] + [
        [
            f"{ts:.3f}",
            f"{re:.4f}",
            f"{m:.4f}",
            f"{w:.4f}",
            f"{se:.4f}" if se is not None else "",
        ]
        for (ts, re, m, se), w in zip(samples, ep_targets, strict=True)
    ]
    if out_path:
        with open(out_path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"CSV written → {out_path}", file=sys.stderr)
    else:
        w = csv.writer(sys.stdout)
        for row in rows:
            w.writerow(row)

    sep = sys.stderr

    # ── Capture summary ──────────────────────────────────────────────────────
    print("\n── Capture summary ─────────────────────────────────────────────────", file=sep)
    print(f"  Samples     : {n:,}  ({samples[-1][0]:.1f} s)", file=sep)
    print(f"  EP ID       : {ep_id or '(none / not active)'}", file=sep)
    print(f"  blend_start = {blend_start}   blend_end = {blend_end}", file=sep)
    blend_src = "sustained_energy" if se_available else "relative_exertion (degraded — no PCM)"
    print(f"  LayerMixer blend input: {blend_src}", file=sep)

    # ── 1. Relative exertion ─────────────────────────────────────────────────
    re_s = sorted(rel_exs)
    mean_re = sum(rel_exs) / n
    print(
        "\n── 1. Relative exertion  "
        "(features.full — BandNormaliser transient detector) ─────",
        file=sep,
    )
    if se_available:
        print(
            "  Note: LayerMixer does NOT use this for blend; it uses sustained_energy.",
            file=sep,
        )
    else:
        print(
            "  Note: LayerMixer is using this as a degraded fallback (no PCM source).",
            file=sep,
        )
    for label, p in [("min", 0), ("p50", 50), ("p75", 75), ("p90", 90),
                     ("p95", 95), ("p99", 99), ("max", 100)]:
        print(f"  {label:<4}: {_percentile(re_s, p):.4f}", file=sep)
    print(f"  mean: {mean_re:.4f}", file=sep)

    print("\n  Histogram (0.1-wide bins):", file=sep)
    bins = [0] * 10
    for e in rel_exs:
        bins[min(int(e * 10), 9)] += 1
    for i, cnt in enumerate(bins):
        lo_b, hi_b = i * 0.1, (i + 1) * 0.1
        bar_pct = 100.0 * cnt / n
        bar_chr = "█" * int(bar_pct / 2)
        print(f"  [{lo_b:.1f}–{hi_b:.1f}) {cnt:6d}  ({bar_pct:5.1f}%)  {bar_chr}", file=sep)

    print("\n  Zone occupancy (against blend thresholds):", file=sep)
    n_re_low  = sum(1 for e in rel_exs if e < blend_start)
    n_re_high = sum(1 for e in rel_exs if e >= blend_end)
    n_re_blend = n - n_re_low - n_re_high
    print(f"  LOW   (< {blend_start:.2f}) : {100.0*n_re_low/n:.1f}%", file=sep)
    print(f"  BLEND ({blend_start:.2f}–{blend_end:.2f}): {100.0*n_re_blend/n:.1f}%", file=sep)
    print(f"  HIGH  (≥ {blend_end:.2f}) : {100.0*n_re_high/n:.1f}%", file=sep)

    # ── 2. Sustained energy ──────────────────────────────────────────────────
    se_present: list[float] = [v for v in se_vals if v is not None]
    if se_present:
        se_s = sorted(se_present)
        se_n = len(se_present)
        print(
            "\n── 2. Sustained energy  "
            "(SustainedEnergyTracker — section-level loudness) ────────",
            file=sep,
        )
        print("  Note: this IS what LayerMixer uses as its blend input.", file=sep)
        print(f"  Samples with SE: {se_n} / {n}", file=sep)
        for label, p in [("min", 0), ("p50", 50), ("p75", 75), ("p90", 90),
                         ("p95", 95), ("p99", 99), ("max", 100)]:
            print(f"  {label:<4}: {_percentile(se_s, p):.4f}", file=sep)

        n_se_low  = sum(1 for v in se_present if v < blend_start)
        n_se_high = sum(1 for v in se_present if v >= blend_end)
        n_se_blend = se_n - n_se_low - n_se_high
        print("\n  Zone occupancy (against blend thresholds):", file=sep)
        print(f"  LOW   (< {blend_start:.2f}) : {100.0*n_se_low/se_n:.1f}%", file=sep)
        print(
            f"  BLEND ({blend_start:.2f}–{blend_end:.2f}): {100.0*n_se_blend/se_n:.1f}%",
            file=sep,
        )
        print(f"  HIGH  (≥ {blend_end:.2f}) : {100.0*n_se_high/se_n:.1f}%", file=sep)
    else:
        print(
            "\n── 2. Sustained energy: not available ──────────────────────────────────",
            file=sep,
        )
        print(
            "  No PCM source attached. LayerMixer is using relative_exertion as a\n"
            "  degraded fallback — blend will react to transients, not song sections.",
            file=sep,
        )

    # ── 3. EnergyProfile instantaneous target ────────────────────────────────
    ep_s = sorted(ep_targets)
    ep_n = len(ep_targets)
    print(
        "\n── 3. EnergyProfile instantaneous target  "
        "(smoothstep of LayerMixer's blend input) ──",
        file=sep,
    )
    if se_available:
        n_se_none = ep_n - se_present_count
        print("  blend_input = sustained_energy", file=sep)
        if n_se_none > 0:
            print(
                f"  ({n_se_none} frames used relative_exertion fallback while SE was warming up)",
                file=sep,
            )
    else:
        print(
            "  blend_input = relative_exertion  (degraded — no PCM source)",
            file=sep,
        )
    for label, p in [("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)]:
        print(f"  {label}: {_percentile(ep_s, p):.4f}", file=sep)
    print(f"  Time target <10%   : {_pct_below(ep_targets, 0.10, ep_n):.1f}%", file=sep)
    ep_mid_lo = 100.0 * sum(1 for w in ep_targets if 0.10 <= w < 0.50) / ep_n
    ep_mid_hi = 100.0 * sum(1 for w in ep_targets if 0.50 <= w < 0.90) / ep_n
    print(f"  Time target 10–50% : {ep_mid_lo:.1f}%", file=sep)
    print(f"  Time target 50–90% : {ep_mid_hi:.1f}%", file=sep)
    print(f"  Time target >90%   : {_pct_above_eq(ep_targets, 0.90, ep_n):.1f}%", file=sep)

    # ── 4. EnergyProfile backend smoothed mix ────────────────────────────────
    m_s = sorted(mixes)
    print(
        "\n── 4. EnergyProfile backend smoothed mix  "
        "(blend_response EMA of instantaneous target) ─",
        file=sep,
    )
    print("  (EMA further suppresses brief spikes; this is what drives the crossfade)", file=sep)
    for label, p in [("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)]:
        print(f"  {label}: {_percentile(m_s, p):.4f}", file=sep)
    print(f"  Time mix <10%  : {_pct_below(mixes, 0.10, n):.1f}%", file=sep)
    m_mid_lo = 100.0 * sum(1 for m in mixes if 0.10 <= m < 0.50) / n
    m_mid_hi = 100.0 * sum(1 for m in mixes if 0.50 <= m < 0.90) / n
    print(f"  Time mix 10–50%: {m_mid_lo:.1f}%", file=sep)
    print(f"  Time mix 50–90%: {m_mid_hi:.1f}%", file=sep)
    print(f"  Time mix >90%  : {_pct_above_eq(mixes, 0.90, n):.1f}%", file=sep)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", default="localhost", metavar="HOST",
                    help="HueSync host (default: localhost)")
    ap.add_argument("--port", type=int, default=8420, metavar="PORT",
                    help="HueSync port (default: 8420)")
    ap.add_argument("--duration", type=float, default=120.0, metavar="SECONDS",
                    help="Capture duration in seconds (default: 120)")
    ap.add_argument("--out", default=None, metavar="PATH",
                    help="Write CSV to this file instead of stdout")
    args = ap.parse_args()

    samples, blend_start, blend_end, ep_id = asyncio.run(
        _capture(args.host, args.port, args.duration)
    )
    _report(samples, blend_start, blend_end, ep_id, args.out)


if __name__ == "__main__":
    main()
