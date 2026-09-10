#!/usr/bin/env python3
"""Capture live energy and blend-weight data from a running HueSync instance.

Run on the LXC using the project venv:

    .venv/bin/python scripts/capture_energy.py --duration 120
    .venv/bin/python scripts/capture_energy.py --host 192.168.1.x --duration 120 --out energy.csv

Summary and histogram are written to stderr; CSV rows to stdout (or --out file).
If no coupling is active, energy will be 0.0 for every sample — start a session
before running this.

Columns in CSV:
    timestamp_s   — seconds since capture start
    energy        — features.full from the backend (0.0–1.0)
    mix           — EMA-smoothed LayerMixer crossfade weight (0.0–1.0)
    high_weight   — smoothstep(energy, blend_start, blend_end), computed locally
                    (matches LayerMixer target before the blend_response EMA)
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


# ---------------------------------------------------------------------------
# WebSocket capture
# ---------------------------------------------------------------------------


async def _capture(
    host: str,
    port: int,
    duration: float,
) -> tuple[list[tuple[float, float, float]], float, float, str | None]:
    """Connect, collect frames for *duration* seconds.

    Returns (samples, blend_start, blend_end, ep_id).
    """
    url = f"ws://{host}:{port}/ws/preview"
    api_base = f"http://{host}:{port}/api"

    samples: list[tuple[float, float, float]] = []  # (timestamp_s, energy, mix)
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
                    energy = float(msg.get("energy", 0.0))
                    mix = float(msg.get("mix", 0.0))
                    samples.append((time.monotonic() - start_t, energy, mix))
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
# Statistics and output
# ---------------------------------------------------------------------------


def _report(
    samples: list[tuple[float, float, float]],
    blend_start: float,
    blend_end: float,
    ep_id: str | None,
    out_path: str | None,
) -> None:
    if not samples:
        print("No samples collected. Was a coupling active?", file=sys.stderr)
        return

    energies = [e for _, e, _ in samples]
    mixes = [m for _, _, m in samples]
    weights = [_smoothstep(e, blend_start, blend_end) for e in energies]
    n = len(energies)

    # CSV
    rows = [["timestamp_s", "energy", "mix", "high_weight"]] + [
        [f"{ts:.3f}", f"{energy:.4f}", f"{mix:.4f}", f"{hw:.4f}"]
        for (ts, energy, mix), hw in zip(samples, weights, strict=True)
    ]
    if out_path:
        with open(out_path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"CSV written → {out_path}", file=sys.stderr)
    else:
        w = csv.writer(sys.stdout)
        for row in rows:
            w.writerow(row)

    e_s = sorted(energies)
    w_s = sorted(weights)
    m_s = sorted(mixes)
    mean_e = sum(energies) / n

    def pct(vals: list[float], threshold: float, above: bool = False) -> float:
        if above:
            return 100.0 * sum(1 for v in vals if v >= threshold) / n
        return 100.0 * sum(1 for v in vals if v < threshold) / n

    sep = sys.stderr
    print("\n── Energy capture summary ──────────────────────────────────────────", file=sep)
    print(f"  Samples   : {n:,}  ({samples[-1][0]:.1f} s)", file=sep)
    print(f"  EP ID     : {ep_id or '(none / not active)' }", file=sep)
    print(f"  blend_start={blend_start}  blend_end={blend_end}", file=sep)

    print("\n── Energy statistics ───────────────────────────────────────────────", file=sep)
    for label, p in [("min", 0), ("p50", 50), ("p75", 75), ("p90", 90),
                     ("p95", 95), ("p99", 99), ("max", 100)]:
        print(f"  {label:<4}: {_percentile(e_s, p):.4f}", file=sep)
    print(f"  mean: {mean_e:.4f}", file=sep)

    print("\n── Energy histogram (0.1-wide bins) ────────────────────────────────", file=sep)
    bins = [0] * 10
    for e in energies:
        bins[min(int(e * 10), 9)] += 1
    for i, cnt in enumerate(bins):
        lo_b, hi_b = i * 0.1, (i + 1) * 0.1
        bar_pct = 100.0 * cnt / n
        bar_chr = "█" * int(bar_pct / 2)
        print(f"  [{lo_b:.1f}–{hi_b:.1f}) {cnt:6d}  ({bar_pct:5.1f}%)  {bar_chr}", file=sep)

    print("\n── Zone occupancy ──────────────────────────────────────────────────", file=sep)
    n_low = sum(1 for e in energies if e < blend_start)
    n_high = sum(1 for e in energies if e >= blend_end)
    n_blend = n - n_low - n_high
    print(f"  LOW   (energy < {blend_start:.2f})                  : {100.0*n_low/n:.1f}%", file=sep)
    blend_pct = 100.0 * n_blend / n
    print(f"  BLEND ({blend_start:.2f} ≤ energy < {blend_end:.2f})  : {blend_pct:.1f}%", file=sep)
    print(f"  HIGH  (energy ≥ {blend_end:.2f})                  : {100.0*n_high/n:.1f}%", file=sep)

    print("\n── High-effect weight — instantaneous smoothstep (no EMA) ─────────", file=sep)
    print(
        "  (This is what LayerMixer targets each frame before blend_response smoothing)",
        file=sep,
    )
    for label, p in [("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)]:
        print(f"  {label}: {_percentile(w_s, p):.4f}", file=sep)
    print(f"  Time weight <10%   : {pct(weights, 0.10):.1f}%", file=sep)
    w_mid_lo = 100.0 * sum(1 for w in weights if 0.10 <= w < 0.50) / n
    w_mid_hi = 100.0 * sum(1 for w in weights if 0.50 <= w < 0.90) / n
    print(f"  Time weight 10–50% : {w_mid_lo:.1f}%", file=sep)
    print(f"  Time weight 50–90% : {w_mid_hi:.1f}%", file=sep)
    print(f"  Time weight >90%   : {pct(weights, 0.90, above=True):.1f}%", file=sep)

    print("\n── Backend mix — EMA-smoothed (what actually drives LayerMixer) ────", file=sep)
    print("  (blend_response EMA further suppresses brief spikes)", file=sep)
    for label, p in [("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)]:
        print(f"  {label}: {_percentile(m_s, p):.4f}", file=sep)
    print(f"  Time mix <10%  : {pct(mixes, 0.10):.1f}%", file=sep)
    print(f"  Time mix 10–50%: {100.0*sum(1 for m in mixes if 0.10 <= m < 0.50)/n:.1f}%", file=sep)
    print(f"  Time mix 50–90%: {100.0*sum(1 for m in mixes if 0.50 <= m < 0.90)/n:.1f}%", file=sep)
    print(f"  Time mix >90%  : {pct(mixes, 0.90, above=True):.1f}%", file=sep)


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
