"""M1: Audio file decoding and offline SustainedEnergy reconstruction.

Decodes FLAC/WAV audio to mono PCM float32 and replays the exact
SustainedEnergyTracker algorithm at production tick rate to reconstruct
the SE time series that HueSync would have produced for that audio.

Requires:  pip install soundfile   (FLAC and WAV via libsndfile)
           MP3 is not supported — use ffmpeg to transcode first.

Dependency direction:
    calib_audio → huesync.sync_engine (import only, no mutation)
    calibrate_energy.py → calib_audio (CLI layer)

Production runtime has no dependency on this module.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from huesync.sync_engine import SustainedEnergyTracker  # noqa: E402

PRODUCTION_TICK_S: float = 1.0 / 30.0   # matches SyncEngine SEND_INTERVAL_S


# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class AudioInfo:
    path: str
    duration_s: float
    sample_rate: int
    n_channels: int
    format: str
    n_ticks: int


@dataclass
class SEReconstruction:
    """Offline SE series derived from an audio file at production tick rate."""
    timestamps_s: list[float]         # one per tick, seconds from track start
    se_values: list[float | None]     # None until tracker sees first non-silent frame
    audio_info: AudioInfo
    tick_s: float


# ── Audio decoding ─────────────────────────────────────────────────────────────


def _require_soundfile():
    """Lazy import so that non-audio tests can import this module freely."""
    try:
        import soundfile
        return soundfile
    except ImportError:
        raise ImportError(
            "soundfile is required for audio decoding.\n"
            "Install it with:  pip install soundfile\n"
            "  or:  pip install 'huesync[calibration]'\n"
            "(requires libsndfile; supports FLAC and WAV; not MP3)"
        ) from None


def decode_audio_mono(path: str) -> tuple[np.ndarray, int, dict]:
    """Decode an audio file to mono float32 PCM in [−1.0, 1.0].

    Returns (pcm, sample_rate, info) where pcm.dtype == float32 and
    pcm.ndim == 1.  info contains duration_s, sample_rate, n_channels, format.

    Supported: FLAC, WAV (and anything libsndfile handles).
    Not supported: MP3 (transcode to FLAC/WAV first with ffmpeg).
    """
    sf = _require_soundfile()
    info = sf.info(path)
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono: np.ndarray = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return mono.astype(np.float32), sr, {
        "duration_s": float(info.duration),
        "sample_rate": int(sr),
        "n_channels": int(info.channels),
        "format": str(info.format),
    }


# ── SE reconstruction ──────────────────────────────────────────────────────────


def reconstruct_se(
    pcm: np.ndarray,
    sample_rate: int,
    tick_s: float = PRODUCTION_TICK_S,
    *,
    tau_short_s: float = SustainedEnergyTracker.DEFAULT_TAU_SHORT_S,
    tau_long_s: float = SustainedEnergyTracker.DEFAULT_TAU_LONG_S,
    range_db: float = SustainedEnergyTracker.DEFAULT_RANGE_DB,
) -> tuple[list[float], list[float | None]]:
    """Replay SustainedEnergyTracker over PCM at fixed tick_s intervals.

    Uses the exact production SustainedEnergyTracker with identical EMA
    constants, dB mapping, and silence handling.  The chunk fed to push()
    at each tick is exactly what the SyncEngine would have read from the
    PCM buffer at that moment.

    Returns (timestamps_s, se_values):
        timestamps_s[i] = i * tick_s   (seconds from track start)
        se_values[i]    = None until the tracker has seen a non-silent frame

    If pcm is empty, returns ([], []).
    """
    if len(pcm) == 0:
        return [], []
    chunk_size = int(sample_rate * tick_s)
    if chunk_size == 0:
        raise ValueError(f"tick_s={tick_s!r} too small for sample_rate={sample_rate}")

    tracker = SustainedEnergyTracker(
        tau_short_s=tau_short_s,
        tau_long_s=tau_long_s,
        range_db=range_db,
    )
    n_ticks = len(pcm) // chunk_size
    timestamps: list[float] = []
    se_values: list[float | None] = []

    for i in range(n_ticks):
        chunk = pcm[i * chunk_size : (i + 1) * chunk_size]
        se = tracker.push(chunk.astype(np.float32), tick_s)
        timestamps.append(i * tick_s)
        se_values.append(se)

    return timestamps, se_values


def load_and_reconstruct(path: str, tick_s: float = PRODUCTION_TICK_S) -> SEReconstruction:
    """Decode audio file and reconstruct SE series. Top-level convenience wrapper."""
    pcm, sr, info_dict = decode_audio_mono(path)
    timestamps, se_values = reconstruct_se(pcm, sr, tick_s)
    audio_info = AudioInfo(
        path=path,
        duration_s=info_dict["duration_s"],
        sample_rate=info_dict["sample_rate"],
        n_channels=info_dict["n_channels"],
        format=info_dict["format"],
        n_ticks=len(timestamps),
    )
    return SEReconstruction(
        timestamps_s=timestamps,
        se_values=se_values,
        audio_info=audio_info,
        tick_s=tick_s,
    )


# ── SE series statistics (shared with alignment/scoring) ──────────────────────


def se_stats(se_values: list[float | None]) -> dict:
    """Return basic statistics for an SE series, ignoring None entries."""
    vals = [v for v in se_values if v is not None]
    n_total = len(se_values)
    n_valid = len(vals)
    if not vals:
        return {
            "n_total": n_total, "n_valid": 0,
            "min": None, "mean": None, "max": None, "p50": None,
        }
    s = sorted(vals)
    mean = sum(vals) / n_valid
    p50_idx = (n_valid - 1) * 0.5
    lo = int(p50_idx)
    hi = min(lo + 1, n_valid - 1)
    p50 = s[lo] + (p50_idx - lo) * (s[hi] - s[lo])
    return {
        "n_total": n_total, "n_valid": n_valid,
        "min": s[0], "mean": mean, "max": s[-1], "p50": p50,
    }
