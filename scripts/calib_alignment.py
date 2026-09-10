"""M2: Cross-correlation alignment of audio-derived SE with captured SE.

Finds the time offset such that track_time = capture_time + offset_s,
by maximizing the normalized cross-correlation of the two SE series.

Algorithm:
  1. Interpolate both series to a common uniform grid at tick_s resolution.
  2. Zero-mean both series.
  3. np.correlate(capture, audio, 'full') → lag where capture best matches audio.
  4. Peak lag L (samples) → offset_s = L * dt.
  5. Normalize by sqrt(sum(a²)*sum(b²)) for Pearson-like quality score.

Convention throughout HueSync calibration:
    track_time = capture_time + offset_s

A positive offset means the capture started part-way into the track.

Quality thresholds:
    r ≥ 0.85  excellent
    r ≥ 0.70  good
    r ≥ 0.50  fair     (warn but proceed)
    r <  0.50  poor    (alignment is unreliable)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_QUALITY_THRESHOLDS: list[tuple[float, str]] = [
    (0.85, "excellent"),
    (0.70, "good"),
    (0.50, "fair"),
    (0.00, "poor"),
]
MIN_ACCEPTABLE_CORRELATION: float = 0.50


# ── Public types ───────────────────────────────────────────────────────────────


@dataclass
class AlignmentResult:
    offset_s: float          # track_time = capture_time + offset_s
    correlation: float       # normalized cross-correlation peak; in [−1, 1]
    quality: str             # "excellent" / "good" / "fair" / "poor"
    n_overlap: int           # frames in the overlapping region at found offset
    search_range_s: float    # ±this many seconds were searched


# ── Helpers ────────────────────────────────────────────────────────────────────


def _quality_label(r: float) -> str:
    for threshold, label in _QUALITY_THRESHOLDS:
        if r >= threshold:
            return label
    return "poor"


def _to_uniform_grid(
    src_ts: list[float],
    src_vals: list[float | None],
    grid: np.ndarray,
) -> np.ndarray:
    """Linearly interpolate src to the uniform grid.

    None values are skipped — only the non-None (t, v) pairs are used as
    control points.  Regions before the first or after the last valid point
    are filled with the nearest valid value (flat extrapolation).

    If there are no non-None values, returns a zero array.
    """
    valid_t = [t for t, v in zip(src_ts, src_vals, strict=True) if v is not None]
    valid_v = [float(v) for v in src_vals if v is not None]
    if not valid_t:
        return np.zeros(len(grid))
    return np.interp(
        grid, valid_t, valid_v,
        left=float(valid_v[0]),
        right=float(valid_v[-1]),
    )


def _overlap_frames(
    audio_ts: list[float],
    capture_ts: list[float],
    offset_s: float,
    dt: float,
) -> int:
    """Number of grid frames in the capture-audio overlap at *offset_s*."""
    if not audio_ts or not capture_ts:
        return 0
    cap_in_audio_start = offset_s
    cap_in_audio_end = capture_ts[-1] + offset_s
    audio_end = audio_ts[-1]
    overlap_start = max(0.0, cap_in_audio_start)
    overlap_end = min(audio_end, cap_in_audio_end)
    return max(0, int((overlap_end - overlap_start) / dt))


# ── Alignment ──────────────────────────────────────────────────────────────────


def _resample_to_uniform(
    ts: list[float],
    vals: list[float | None],
    dt: float,
) -> np.ndarray:
    """Resample an SE series to a uniform dt grid covering [ts[0], ts[-1]].

    None values are skipped; the valid (t, v) pairs are linearly interpolated
    onto the uniform grid.  If fewer than two valid points exist, returns a
    zero array of the natural length.
    """
    valid_t = [t for t, v in zip(ts, vals, strict=True) if v is not None]
    valid_v = [float(v) for v in vals if v is not None]
    if len(valid_t) < 2:
        n = max(1, int((ts[-1] - ts[0]) / dt) + 1) if len(ts) >= 2 else 1
        return np.zeros(n)
    t_start, t_end = ts[0], ts[-1]
    grid = np.arange(t_start, t_end + dt * 0.5, dt)
    return np.interp(grid, valid_t, valid_v,
                     left=float(valid_v[0]), right=float(valid_v[-1]))


def align(
    audio_ts: list[float],
    audio_se: list[float | None],
    capture_ts: list[float],
    capture_se: list[float | None],
    tick_s: float = 1.0 / 30.0,
    search_range_s: float = 60.0,
) -> AlignmentResult:
    """Find the offset such that track_time = capture_time + offset_s.

    Each series is resampled to a uniform *tick_s* grid at its own natural
    length (independent grids — avoids padding bias from a shared grid).
    After zero-meaning, np.correlate(capture, audio, 'full') gives the lag
    at which capture best matches audio.

    Peak at lag L (frames) → offset_s = L * tick_s.
    Positive offset_s: capture started offset_s seconds into the track.

    When both series have all-None values, correlation ≈ 0 → quality "poor".
    """
    if not audio_ts or not capture_ts:
        return AlignmentResult(
            offset_s=0.0, correlation=0.0, quality="poor",
            n_overlap=0, search_range_s=search_range_s,
        )

    dt = tick_s

    # Resample each series to its own uniform grid independently.
    # This avoids the constant-extrapolation tail that appears when both
    # series are forced onto a single shared grid.
    a = _resample_to_uniform(audio_ts, audio_se, dt)
    b = _resample_to_uniform(capture_ts, capture_se, dt)

    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return AlignmentResult(
            offset_s=0.0, correlation=0.0, quality="poor",
            n_overlap=0, search_range_s=search_range_s,
        )

    # Zero-mean before correlating (removes DC offset bias).
    a = a - a.mean()
    b = b - b.mean()

    # np.correlate(a, b, 'full') with "a"=audio, "b"=capture:
    #   c[k] = sum_n audio[n] * capture[n - k]
    #   Peaks at lag L where capture[n-L] ≈ audio[n] → capture[t] ≈ audio[t+L]
    #   → track_time = capture_time + L → offset_s = L * dt.
    # lags[idx] = idx - (nb-1), with nb = len(capture).
    xcorr = np.correlate(a, b, mode="full")
    lags = np.arange(len(xcorr)) - (nb - 1)

    # Restrict search to ±search_range_s.
    search_frames = int(math.ceil(search_range_s / dt))
    mask = np.abs(lags) <= search_frames
    if not mask.any():
        mask = np.ones(len(lags), dtype=bool)

    xcorr_f = xcorr.astype(float)
    xcorr_masked = xcorr_f.copy()
    xcorr_masked[~mask] = -np.inf

    norm = math.sqrt(float(np.sum(a**2)) * float(np.sum(b**2)))
    if norm > 0.0:
        xcorr_norm = xcorr_f / norm
        xcorr_masked_norm = xcorr_masked / norm
    else:
        xcorr_norm = np.zeros_like(xcorr_f)
        xcorr_masked_norm = np.zeros_like(xcorr_masked)

    peak_k = int(np.argmax(xcorr_masked_norm))
    peak_lag = int(lags[peak_k])
    offset_s = peak_lag * dt
    correlation = float(xcorr_norm[peak_k])

    return AlignmentResult(
        offset_s=offset_s,
        correlation=correlation,
        quality=_quality_label(correlation),
        n_overlap=_overlap_frames(audio_ts, capture_ts, offset_s, dt),
        search_range_s=search_range_s,
    )
