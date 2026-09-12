#!/usr/bin/env python3
"""HueSync DSP Phase 2A-R — Recovery / Real-CAVA Validation.

Phase 2A-R corrects the bar-mapping formula error from Phase 2A and provides
a repaired CavaRunner for deployment on the LXC with actual CAVA 0.10.4.

WITHDRAWN FINDING (Phase 2A):
    "~19% logarithmic bar-mapping divergence between native and CAVA at high
    frequencies" — caused by transcription error in CavaSimulator.  CAVA
    cavacore.c and native HueSync use the same ideal log-spaced edge formula.
    Effective bin-coverage differs only due to FFT size and bin discretisation.

Usage — native analysis only (default):
    python3 scripts/phase2a_r_compare.py [--output-dir artifacts/dsp_phase2a_r]

Usage — with actual CAVA binary (run on LXC as huesync user):
    python3 scripts/phase2a_r_compare.py --cava [--cava-binary /usr/bin/cava]
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from huesync.pcm_source import WINDOW_SIZE, PcmStft
from huesync.sync_engine import BandNormaliser

# ---------------------------------------------------------------------------
# Global experiment parameters
# ---------------------------------------------------------------------------

SR = 44100
N_BARS = 30
LOWER_HZ = 50.0
UPPER_HZ = 12000.0
CAVA_FRAMERATE = 60
CAVA_NOISE_REDUCTION = 77  # integer percent, per config.c default
NATIVE_HOP_DT = round(SR * 0.010) / SR  # ≈ 0.010 s

# BandNormaliser production defaults
_ATTACK_TAU = BandNormaliser.DEFAULT_ATTACK_TAU_S
_RELEASE_TAU = BandNormaliser.DEFAULT_RELEASE_TAU_S
_GATE = BandNormaliser.DEFAULT_GATE
_EXERTION_CLIP = BandNormaliser.DEFAULT_EXERTION_CLIP


# ---------------------------------------------------------------------------
# CanonicalPcm
# ---------------------------------------------------------------------------


class CanonicalPcm:
    """Canonical test PCM: stereo S16_LE, 44100 Hz, SHA-256 verified."""

    SAMPLE_RATE = 44100
    CHANNELS = 2
    SAMPLE_WIDTH = 2  # bytes, S16_LE

    def __init__(self, label: str, stereo_float32: np.ndarray) -> None:
        # quantise float32 → S16_LE (little-endian int16)
        clipped = np.clip(stereo_float32, -1.0, 1.0)
        s16 = (clipped * 32767.0).astype("<i2")
        self.label = label
        self.bytes_data: bytes = s16.tobytes()
        self.sha256 = hashlib.sha256(self.bytes_data).hexdigest()
        self.n_frames = len(self.bytes_data) // (self.CHANNELS * self.SAMPLE_WIDTH)
        self.duration_s = self.n_frames / self.SAMPLE_RATE

    def to_float32_stereo(self) -> np.ndarray:
        """Decode S16_LE → (N, 2) float32 on [-1, 1].  Common decode path."""
        s = np.frombuffer(self.bytes_data, dtype="<i2").astype(np.float32)
        s /= 32768.0
        return s.reshape(-1, 2)

    def to_float32_mono(self) -> np.ndarray:
        """(L+R)/2 downmix — native pre-FFT path."""
        stereo = self.to_float32_stereo()
        return (stereo[:, 0] + stereo[:, 1]) / 2.0

    def info(self) -> dict:
        return {
            "label": self.label,
            "sample_rate": self.SAMPLE_RATE,
            "channels": self.CHANNELS,
            "format": "S16_LE",
            "byte_length": len(self.bytes_data),
            "n_frames": self.n_frames,
            "duration_s": round(self.duration_s, 6),
            "sha256": self.sha256,
        }


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------


def _stereo_inphase(mono: np.ndarray) -> np.ndarray:
    s = mono[:, np.newaxis]
    return np.concatenate([s, s], axis=1)


def _stereo_opposite(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, -mono], axis=1)


def _sine(freq: float, amp: float, n: int) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / SR
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def _silence_stereo(duration_s: float) -> np.ndarray:
    n = int(duration_s * SR)
    return np.zeros((n, 2), dtype=np.float32)


def _make_signal_a() -> CanonicalPcm:
    """A — 440 Hz amplitude step: sil 1s, 0.10 2s, 0.70 2s, 0.10 2s, sil 2s."""
    parts = [
        _silence_stereo(1.0),
        _stereo_inphase(_sine(440.0, 0.10, int(2.0 * SR))),
        _stereo_inphase(_sine(440.0, 0.70, int(2.0 * SR))),
        _stereo_inphase(_sine(440.0, 0.10, int(2.0 * SR))),
        _silence_stereo(2.0),
    ]
    return CanonicalPcm("A_amplitude_step", np.concatenate(parts))


def _make_signal_b() -> CanonicalPcm:
    """B — 440 Hz in-phase stereo: sil 1s, amp=0.50 3s, sil 1s."""
    parts = [
        _silence_stereo(1.0),
        _stereo_inphase(_sine(440.0, 0.50, int(3.0 * SR))),
        _silence_stereo(1.0),
    ]
    return CanonicalPcm("B_inphase_440", np.concatenate(parts))


def _make_signal_c() -> CanonicalPcm:
    """C — 440 Hz opposite-phase stereo: same timing as B, L=+sine, R=-sine."""
    parts = [
        _silence_stereo(1.0),
        _stereo_opposite(_sine(440.0, 0.50, int(3.0 * SR))),
        _silence_stereo(1.0),
    ]
    return CanonicalPcm("C_opposite_440", np.concatenate(parts))


def _make_signal_d() -> CanonicalPcm:
    """D — 80 Hz low-frequency tone: sil 0.5s, amp=0.50 3s, sil 0.5s."""
    parts = [
        _silence_stereo(0.5),
        _stereo_inphase(_sine(80.0, 0.50, int(3.0 * SR))),
        _silence_stereo(0.5),
    ]
    return CanonicalPcm("D_low_80hz", np.concatenate(parts))


def _make_signal_e() -> CanonicalPcm:
    """E — 11025 Hz high-frequency tone: sil 0.5s, amp=0.50 3s, sil 0.5s."""
    parts = [
        _silence_stereo(0.5),
        _stereo_inphase(_sine(11025.0, 0.50, int(3.0 * SR))),
        _silence_stereo(0.5),
    ]
    return CanonicalPcm("E_high_11025hz", np.concatenate(parts))


def make_all_signals() -> dict[str, CanonicalPcm]:
    return {
        "A": _make_signal_a(),
        "B": _make_signal_b(),
        "C": _make_signal_c(),
        "D": _make_signal_d(),
        "E": _make_signal_e(),
    }


# ---------------------------------------------------------------------------
# Bar frequency mapping
# ---------------------------------------------------------------------------


def log_bar_edges(
    n_bars: int = N_BARS,
    lower: float = LOWER_HZ,
    upper: float = UPPER_HZ,
) -> list[float]:
    """Corrected log-spaced bar edges (n_bars+1 values, lower..upper inclusive).

    edge[n] = lower * (upper/lower)^(n/n_bars) for n=0..n_bars.

    Matches:
    - Native HueSync _mag_to_bar_bytes:
          10^(log_lo + i/n_bars * (log_hi - log_lo))  [algebraically identical]
    - CAVA cavacore.c cut_off_frequency formula
          [hardcoded [0]=lower; [n]=upper*10^(log10(u/l)*(n/n_bars-1)) for n>=1]

    WITHDRAWN: Phase 2A cava_bar_edges() used (n+1)/(n_bars+1) — wrong formula,
    caused spurious ~19% divergence claim.  Actual ideal divergence: zero.
    """
    return [lower * (upper / lower) ** (n / n_bars) for n in range(n_bars + 1)]


def bar_mapping_table(
    n_bars: int = N_BARS,
    lower: float = LOWER_HZ,
    upper: float = UPPER_HZ,
    sample_rate: int = SR,
) -> list[dict]:
    """Per-bar mapping: ideal edges, native FFT bins, CAVA reference model bins.

    CAVA reference model FFT sizes at 44100 Hz: bass=8192, mid=4096, treble=1024.
    Assignment: smallest FFT giving >=1 bin (b_hi > b_lo using integer truncation).
    """
    edges = log_bar_edges(n_bars, lower, upper)
    # Native FFT parameters (WINDOW_SIZE=2048, production formula uses round())
    native_fft = WINDOW_SIZE  # 2048
    # CAVA reference model FFT sizes
    treble_base = 128
    if sample_rate > 32500:
        treble_base *= 8  # 1024 at 44100 Hz
    cava_fft_sizes = (treble_base, treble_base * 4, treble_base * 8)  # 1024, 4096, 8192

    rows = []
    for i in range(n_bars):
        f_lo = edges[i]
        f_hi = edges[i + 1]

        # Native bins (round())
        n_bin_lo = max(0, round(f_lo * native_fft / sample_rate))
        n_bin_hi = min(
            native_fft // 2 + 1,
            max(n_bin_lo + 1, round(f_hi * native_fft / sample_rate)),
        )
        n_eff_lo = n_bin_lo * sample_rate / native_fft
        n_eff_hi = (n_bin_hi - 1) * sample_rate / native_fft

        # CAVA reference model bins (int truncation, smallest FFT giving >=1 bin)
        cava_fft_sz = cava_fft_sizes[-1]  # default to bass
        c_bin_lo_final = 0
        c_bin_hi_final = 1
        for fft_sz in cava_fft_sizes:
            b_lo = max(0, int(f_lo * fft_sz / sample_rate))
            b_hi = min(fft_sz // 2, int(f_hi * fft_sz / sample_rate) + 1)
            if b_hi > b_lo:
                cava_fft_sz = fft_sz
                c_bin_lo_final = b_lo
                c_bin_hi_final = b_hi
                break

        c_eff_lo = c_bin_lo_final * sample_rate / cava_fft_sz
        c_eff_hi = (c_bin_hi_final - 1) * sample_rate / cava_fft_sz

        rows.append(
            {
                "bar": i,
                "ideal_lo_hz": round(f_lo, 4),
                "ideal_hi_hz": round(f_hi, 4),
                "native_fft_size": native_fft,
                "native_bin_lo": n_bin_lo,
                "native_bin_hi": n_bin_hi,
                "native_eff_lo_hz": round(n_eff_lo, 4),
                "native_eff_hi_hz": round(n_eff_hi, 4),
                "cava_ref_fft_size": cava_fft_sz,
                "cava_ref_bin_lo": c_bin_lo_final,
                "cava_ref_bin_hi": c_bin_hi_final,
                "cava_ref_eff_lo_hz": round(c_eff_lo, 4),
                "cava_ref_eff_hi_hz": round(c_eff_hi, 4),
                "note": "cava_ref: unvalidated assignment",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# NativeAnalyser
# ---------------------------------------------------------------------------


def _mag_to_bar_bytes(
    mag: np.ndarray,
    n_bars: int,
    lower_hz: float,
    upper_hz: float,
    sample_rate: int,
) -> bytes:
    """Replicate PcmAudioPipeline._mag_to_bar_bytes exactly."""
    n_bins = len(mag)
    log_lo = math.log10(max(lower_hz, 1.0))
    log_hi = math.log10(max(upper_hz, lower_hz + 1.0))
    result = bytearray(n_bars)
    for i in range(n_bars):
        f_lo = 10.0 ** (log_lo + i / n_bars * (log_hi - log_lo))
        f_hi = 10.0 ** (log_lo + (i + 1) / n_bars * (log_hi - log_lo))
        b_lo = max(0, round(f_lo * WINDOW_SIZE / sample_rate))
        b_hi = min(n_bins, max(b_lo + 1, round(f_hi * WINDOW_SIZE / sample_rate)))
        val = float(np.mean(mag[b_lo:b_hi])) if b_hi > b_lo else 0.0
        result[i] = min(255, int(val))
    return bytes(result)


class NativeAnalyser:
    """HueSync native PCM path: PcmStft + _mag_to_bar_bytes + BandNormaliser.

    Processes CanonicalPcm.to_float32_stereo() through the production pipeline.
    Downmixes to mono via (L+R)/2 before STFT (matches production).

    Returns frames with sample-derived timing only (no wall clock).
    """

    def __init__(
        self,
        n_bars: int = N_BARS,
        lower_hz: float = LOWER_HZ,
        upper_hz: float = UPPER_HZ,
        sample_rate: int = SR,
    ) -> None:
        self._stft = PcmStft(sample_rate)
        self._normaliser = BandNormaliser(
            attack_tau_s=_ATTACK_TAU,
            release_tau_s=_RELEASE_TAU,
            gate=_GATE,
            exertion_clip=_EXERTION_CLIP,
        )
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz
        self._sample_rate = sample_rate
        self._hop = self._stft.hop
        self._dt = self._hop / sample_rate

    @property
    def hop(self) -> int:
        return self._hop

    def reset(self) -> None:
        self._stft = PcmStft(self._sample_rate)
        self._normaliser = BandNormaliser(
            attack_tau_s=_ATTACK_TAU,
            release_tau_s=_RELEASE_TAU,
            gate=_GATE,
            exertion_clip=_EXERTION_CLIP,
        )

    def run(self, pcm: CanonicalPcm) -> list[dict]:
        """Process a CanonicalPcm.  Returns list of frame dicts with timing."""
        stereo = pcm.to_float32_stereo()
        mono = (stereo[:, 0] + stereo[:, 1]) / 2.0  # (L+R)/2
        results = []
        sample_pos = 0
        for mag in self._stft.push(mono):
            t_start = sample_pos / self._sample_rate
            t_centre = (sample_pos + WINDOW_SIZE / 2) / self._sample_rate
            raw = _mag_to_bar_bytes(
                mag, self._n_bars, self._lower_hz, self._upper_hz, self._sample_rate
            )
            normed = self._normaliser.normalise(raw, self._dt)
            results.append(
                {
                    "raw": np.frombuffer(raw, dtype=np.uint8).copy(),
                    "normed": np.frombuffer(normed, dtype=np.uint8).copy(),
                    "t_sample_start": t_start,
                    "t_sample_centre": t_centre,
                }
            )
            sample_pos += self._hop
        return results


# ---------------------------------------------------------------------------
# CavaReferenceModel
# ---------------------------------------------------------------------------


class CavaReferenceModel:
    """Corrected source-derived CAVA 0.10.4 model.

    STATUS: SOURCE-DERIVED (cavacore.c 0.10.4) — NOT BINARY-VALIDATED
    Gravity, noise-reduction, autosensitivity implemented per cavacore.c source.
    Bar assignment uses reference model — actual CAVA may differ at transition
    frequencies and with collision-detection logic.

    Key fix vs Phase 2A CavaSimulator: bar edges use n/n_bars (not (n+1)/(n_bars+1)).
    """

    def __init__(
        self,
        n_bars: int = N_BARS,
        sample_rate: int = SR,
        lower_hz: float = LOWER_HZ,
        upper_hz: float = UPPER_HZ,
        framerate: int = CAVA_FRAMERATE,
        noise_reduction_pct: int = CAVA_NOISE_REDUCTION,
        autosens: bool = True,
        sensitivity: float = 1.0,
    ) -> None:
        self.n_bars = n_bars
        self.sample_rate = sample_rate
        self.lower_hz = lower_hz
        self.upper_hz = upper_hz
        self.framerate = framerate
        self._noise_reduction = noise_reduction_pct / 100.0  # e.g. 0.77
        self.autosens = autosens
        self.sens = sensitivity
        self.sens_init = True

        self.frame_samples = sample_rate // framerate  # 735 at 44100/60

        # FFT sizes (cavacore.c: treble_buffer_size * scale(rate))
        treble_base = 128
        if sample_rate > 32500:
            treble_base *= 8  # 1024 at 44100 Hz
        self._fft_bass = treble_base * 8    # 8192
        self._fft_mid = treble_base * 4     # 4096
        self._fft_treble = treble_base      # 1024

        # Hann windows
        self._win = {
            sz: 0.5 * (1 - np.cos(2 * math.pi * np.arange(sz) / (sz - 1)))
            for sz in (self._fft_bass, self._fft_mid, self._fft_treble)
        }

        # Corrected bar edges: n/n_bars (Phase 2A used (n+1)/(n_bars+1) — WRONG)
        self._edges = log_bar_edges(n_bars, lower_hz, upper_hz)

        # Assign each bar to smallest FFT giving >=1 bin
        self._bar_assign: list[tuple[int, int, int]] = []
        for i in range(n_bars):
            f_lo = self._edges[i]
            f_hi = self._edges[i + 1]
            assigned = False
            for fft_sz in (self._fft_treble, self._fft_mid, self._fft_bass):
                b_lo = max(0, int(f_lo * fft_sz / sample_rate))
                b_hi = min(fft_sz // 2, int(f_hi * fft_sz / sample_rate) + 1)
                if b_hi > b_lo:
                    self._bar_assign.append((fft_sz, b_lo, b_hi))
                    assigned = True
                    break
            if not assigned:
                self._bar_assign.append((self._fft_bass, 0, 1))

        # Rolling PCM buffers (L and R), float64 in int16 scale
        self._buf_l = np.zeros(self._fft_bass, dtype=np.float64)
        self._buf_r = np.zeros(self._fft_bass, dtype=np.float64)
        self._pending = 0

        # Gravity state
        self._peak = np.zeros(n_bars, dtype=np.float64)
        self._fall = np.zeros(n_bars, dtype=np.float64)

        # Noise-reduction IIR state
        self._mem = np.zeros(n_bars, dtype=np.float64)

        # gravity_mod = pow(60/framerate, 2.5) * 1.54 / noise_reduction
        self._gravity_mod = (60.0 / framerate) ** 2.5 * 1.54 / self._noise_reduction

        self._norm = {
            sz: float(sz) / 2.0
            for sz in (self._fft_bass, self._fft_mid, self._fft_treble)
        }

    def reset(self) -> None:
        self._buf_l = np.zeros(self._fft_bass, dtype=np.float64)
        self._buf_r = np.zeros(self._fft_bass, dtype=np.float64)
        self._pending = 0
        self._peak[:] = 0
        self._fall[:] = 0
        self._mem[:] = 0
        self.sens = 1.0
        self.sens_init = True

    def _fft_mag(self, buf: np.ndarray, fft_sz: int) -> np.ndarray:
        seg = buf[-fft_sz:] * self._win[fft_sz]
        mag = np.abs(np.fft.rfft(seg))
        return mag / self._norm[fft_sz]

    def _bar_from_mags(
        self,
        mag_l: dict[int, np.ndarray],
        mag_r: dict[int, np.ndarray],
        bar_idx: int,
    ) -> float:
        fft_sz, b_lo, b_hi = self._bar_assign[bar_idx]
        ml = float(np.mean(mag_l[fft_sz][b_lo:b_hi]))
        mr = float(np.mean(mag_r[fft_sz][b_lo:b_hi]))
        return (ml + mr) / 2.0

    def _compute_frame(self, sample_pos: int) -> dict:
        fft_sizes = {self._fft_bass, self._fft_mid, self._fft_treble}
        mag_l = {sz: self._fft_mag(self._buf_l, sz) for sz in fft_sizes}
        mag_r = {sz: self._fft_mag(self._buf_r, sz) for sz in fft_sizes}

        raw_bars = np.array(
            [self._bar_from_mags(mag_l, mag_r, n) for n in range(self.n_bars)]
        )

        # Gravity (peak-hold + quadratic decay) — cavacore.c
        out = raw_bars.copy()
        for n in range(self.n_bars):
            if out[n] > self._peak[n]:
                self._peak[n] = out[n]
                self._fall[n] = 0.0
            decay = 1.0 - self._fall[n] ** 2 * self._gravity_mod
            if decay < 0.0:
                decay = 0.0
                self._peak[n] = 0.0
            out[n] = self._peak[n] * decay
            self._fall[n] += 0.028

        # Noise-reduction IIR: out = mem * nr + out — cavacore.c
        out = self._mem * self._noise_reduction + out
        self._mem = out.copy()

        # Autosensitivity (cava.c)
        silence = float(np.max(out)) < 1e-8
        if self.autosens:
            if np.any(out * self.sens > 1.0):
                self.sens *= 0.98
                self.sens_init = False
            elif not silence:
                self.sens *= 1.002
                if self.sens_init:
                    self.sens *= 1.1

        bars = np.clip(out * self.sens * 255.0, 0.0, 255.0).astype(np.uint8)

        return {
            "raw_pre_gravity": raw_bars,
            "bars": bars,
            "t_sample_start": sample_pos / self.sample_rate,
        }

    def run(self, pcm: CanonicalPcm) -> list[dict]:
        """Process a CanonicalPcm.  Returns frame dicts at CAVA_FRAMERATE."""
        stereo = pcm.to_float32_stereo()
        # Scale to int16 range
        pcm_scaled = (stereo * 32767.0).astype(np.float64)
        n_samples = pcm_scaled.shape[0]
        frames = []
        pos = 0
        sample_pos = 0  # track sample position for timing
        while pos < n_samples:
            space = self.frame_samples - self._pending
            take = min(space, n_samples - pos)
            chunk = pcm_scaled[pos : pos + take]
            roll = len(chunk)
            self._buf_l = np.roll(self._buf_l, -roll)
            self._buf_l[-roll:] = chunk[:, 0]
            self._buf_r = np.roll(self._buf_r, -roll)
            self._buf_r[-roll:] = chunk[:, 1]
            pos += take
            self._pending += take
            if self._pending >= self.frame_samples:
                frames.append(self._compute_frame(sample_pos))
                sample_pos += self.frame_samples
                self._pending = 0
        return frames


# ---------------------------------------------------------------------------
# CavaRunner — fixed real-CAVA binary runner
# ---------------------------------------------------------------------------


class CavaRunner:
    """Fixed real-CAVA binary runner.

    Key fixes vs Phase 2A CavaBinaryRunner:
    - method = fifo (NOT pipe) — CAVA 0.10.x uses fifo
    - Explicit framerate/noise_reduction/autosens/sensitivity matching defaults
    - Partial-write handling (os.write in a loop)
    - CAVA stderr surfaced, not discarded
    - select.select() with timeout for non-blocking output reads
    - fcntl O_NONBLOCK on output FIFO
    - Timestamps from wall clock (CAVA provides no sample-accurate timing)
    """

    def __init__(
        self,
        cava_binary: str = "cava",
        n_bars: int = N_BARS,
        lower_hz: float = LOWER_HZ,
        upper_hz: float = UPPER_HZ,
    ) -> None:
        self._binary = cava_binary
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz

    def run(self, pcm: CanonicalPcm, timeout_s: float = 60.0) -> tuple[list[dict], str]:
        """Run actual CAVA binary.  Returns (frames, stderr_text).

        Raises RuntimeError if binary not found.
        frame dict: {"bars": np.ndarray[uint8], "t_wall": float}
        """
        import shutil

        if not shutil.which(self._binary) and not os.path.isfile(self._binary):
            raise RuntimeError(f"CAVA binary not found: {self._binary!r}")

        with tempfile.TemporaryDirectory() as tmp:
            in_fifo = os.path.join(tmp, "cava_in.pcm")
            out_fifo = os.path.join(tmp, "cava_out.raw")
            conf_path = os.path.join(tmp, "cava.conf")
            os.mkfifo(in_fifo)
            os.mkfifo(out_fifo)

            conf = (
                f"[general]\n"
                f"bars = {self._n_bars}\n"
                f"lower_cutoff_freq = {int(self._lower_hz)}\n"
                f"higher_cutoff_freq = {int(self._upper_hz)}\n"
                f"framerate = {CAVA_FRAMERATE}\n"
                f"noise_reduction = {CAVA_NOISE_REDUCTION}\n"
                f"autosens = 1\n"
                f"sensitivity = 100\n"
                f"\n"
                f"[input]\n"
                f"method = fifo\n"
                f"source = {in_fifo}\n"
                f"sample_rate = {SR}\n"
                f"sample_bits = 16\n"
                f"channels = 2\n"
                f"\n"
                f"[output]\n"
                f"method = raw\n"
                f"raw_target = {out_fifo}\n"
                f"data_format = binary\n"
                f"bit_format = 8bit\n"
                f"channels = mono\n"
            )
            Path(conf_path).write_text(conf)

            frames: list[dict] = []
            stderr_lines: list[str] = []

            # Open output FIFO in a thread to avoid blocking main thread
            out_fd_holder: list[int] = []

            def open_out_fifo() -> None:
                fd = os.open(out_fifo, os.O_RDONLY)
                fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
                out_fd_holder.append(fd)

            out_opener = threading.Thread(target=open_out_fifo, daemon=True)
            out_opener.start()

            proc = subprocess.Popen(
                [self._binary, "-p", conf_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            # Write PCM to input FIFO in a separate thread
            raw_pcm = pcm.bytes_data

            def write_pcm() -> None:
                try:
                    fd = os.open(in_fifo, os.O_WRONLY)
                    try:
                        pos = 0
                        while pos < len(raw_pcm):
                            n = os.write(fd, raw_pcm[pos:])
                            pos += n
                    finally:
                        os.close(fd)
                except OSError:
                    pass

            writer = threading.Thread(target=write_pcm, daemon=True)
            writer.start()

            # Wait for output FIFO to open (or timeout)
            out_opener.join(timeout=10.0)
            if not out_fd_holder:
                proc.terminate()
                proc.wait()
                writer.join(timeout=2.0)
                return [], "Timed out waiting for CAVA to open output FIFO"

            out_fd = out_fd_holder[0]
            buf = b""
            t_start = time.monotonic()

            try:
                while True:
                    elapsed = time.monotonic() - t_start
                    if elapsed > timeout_s:
                        break
                    remaining = timeout_s - elapsed
                    ready, _, _ = select.select([out_fd], [], [], min(remaining, 0.1))
                    if ready:
                        try:
                            chunk = os.read(out_fd, 4096)
                            if chunk:
                                buf += chunk
                                while len(buf) >= self._n_bars:
                                    frame_bytes = buf[: self._n_bars]
                                    buf = buf[self._n_bars :]
                                    arr = np.frombuffer(frame_bytes, dtype=np.uint8).copy()
                                    frames.append(
                                        {
                                            "bars": arr,
                                            "t_wall": time.monotonic() - t_start,
                                        }
                                    )
                        except BlockingIOError:
                            pass
                    # Check if writer is done and CAVA has had time to flush
                    if not writer.is_alive() and elapsed > pcm.duration_s + 1.0:
                        # Give CAVA a short extra window to flush remaining frames
                        extra_deadline = time.monotonic() + 0.5
                        while time.monotonic() < extra_deadline:
                            ready2, _, _ = select.select([out_fd], [], [], 0.05)
                            if ready2:
                                try:
                                    chunk2 = os.read(out_fd, 4096)
                                    if chunk2:
                                        buf += chunk2
                                        while len(buf) >= self._n_bars:
                                            fb = buf[: self._n_bars]
                                            buf = buf[self._n_bars :]
                                            arr2 = np.frombuffer(fb, dtype=np.uint8).copy()
                                            frames.append(
                                                {
                                                    "bars": arr2,
                                                    "t_wall": time.monotonic() - t_start,
                                                }
                                            )
                                except BlockingIOError:
                                    pass
                        break
            finally:
                os.close(out_fd)

            proc.terminate()
            proc.wait()
            writer.join(timeout=2.0)

            # Collect stderr
            if proc.stderr:
                try:
                    stderr_text = proc.stderr.read().decode(errors="replace")
                except OSError:
                    stderr_text = ""
                stderr_lines.append(stderr_text)

        return frames, "".join(stderr_lines)


# ---------------------------------------------------------------------------
# Stereo comparison
# ---------------------------------------------------------------------------


def run_stereo_comparison(
    pcm_in_phase: CanonicalPcm,
    pcm_opposite: CanonicalPcm,
    backend: str,
    native_analyser: NativeAnalyser | None = None,
    cava_ref: CavaReferenceModel | None = None,
    cava_runner: CavaRunner | None = None,
) -> dict:
    """Run both in-phase and opposite-phase signals through one backend.

    backend: "native" | "cava_ref" | "cava_binary"

    Returns:
    {
        "backend": str,
        "in_phase_mean_raw": float,
        "opp_phase_mean_raw": float,
        "ratio_raw": float,          # opp / in_phase (key metric)
        "in_phase_mean_normed": float,
        "opp_phase_mean_normed": float,
        "ratio_normed": float,
    }
    """
    def _frame_mean(frames: list[dict], key: str) -> float:
        if not frames:
            return 0.0
        return float(np.mean([f[key].astype(float).mean() for f in frames]))

    if backend == "native":
        assert native_analyser is not None
        native_analyser.reset()
        ip_frames = native_analyser.run(pcm_in_phase)
        native_analyser.reset()
        op_frames = native_analyser.run(pcm_opposite)
        ip_raw = _frame_mean(ip_frames, "raw")
        op_raw = _frame_mean(op_frames, "raw")
        ip_normed = _frame_mean(ip_frames, "normed")
        op_normed = _frame_mean(op_frames, "normed")
    elif backend == "cava_ref":
        assert cava_ref is not None
        cava_ref.reset()
        ip_frames_r = cava_ref.run(pcm_in_phase)
        cava_ref.reset()
        op_frames_r = cava_ref.run(pcm_opposite)
        ip_raw = _frame_mean(ip_frames_r, "raw_pre_gravity")
        op_raw = _frame_mean(op_frames_r, "raw_pre_gravity")
        ip_normed = _frame_mean(ip_frames_r, "bars")
        op_normed = _frame_mean(op_frames_r, "bars")
    elif backend == "cava_binary":
        assert cava_runner is not None
        ip_bin, _ = cava_runner.run(pcm_in_phase)
        op_bin, _ = cava_runner.run(pcm_opposite)
        ip_raw = _frame_mean(ip_bin, "bars")
        op_raw = _frame_mean(op_bin, "bars")
        ip_normed = ip_raw
        op_normed = op_raw
    else:
        raise ValueError(f"Unknown backend: {backend!r}")

    return {
        "backend": backend,
        "in_phase_mean_raw": ip_raw,
        "opp_phase_mean_raw": op_raw,
        "ratio_raw": op_raw / (ip_raw + 1e-9),
        "in_phase_mean_normed": ip_normed,
        "opp_phase_mean_normed": op_normed,
        "ratio_normed": op_normed / (ip_normed + 1e-9),
    }


# ---------------------------------------------------------------------------
# Stale-frame analysis
# ---------------------------------------------------------------------------


def stale_frame_analysis() -> dict:
    """Characterise BandNormaliser output when same CAVA frame is normalized repeatedly.

    Scenario: producer publishes frame N (value=128 for all bars).
    Consumer calls normalise(frame_N, dt) 3 times before producer publishes frame N+1.

    Compare against: one normalise() call per unique frame.

    Key question: what measurable effect does repeated normalisation of an
    unchanged CAVA frame have?

    NOT a simulator test — tests actual CavaPipeline / BandNormaliser integration.
    """
    n_bars = N_BARS
    cava_frame = bytes([128] * n_bars)
    silence = bytes([0] * n_bars)

    rate = 30
    dt = 1.0 / rate
    duration = 4.0
    n = int(duration * rate)
    silence_frames = int(1.0 * rate)

    bn_single = BandNormaliser(
        attack_tau_s=_ATTACK_TAU, release_tau_s=_RELEASE_TAU, gate=0.0, exertion_clip=_EXERTION_CLIP
    )
    bn_triple = BandNormaliser(
        attack_tau_s=_ATTACK_TAU, release_tau_s=_RELEASE_TAU, gate=0.0, exertion_clip=_EXERTION_CLIP
    )

    times = np.arange(n) * dt
    vals_single = np.zeros(n)
    vals_triple = np.zeros(n)

    for i in range(n):
        f = silence if i < silence_frames else cava_frame
        vals_single[i] = bn_single.normalise(f, dt)[0] / 255.0
        # Repeated: 3 calls per tick, each with dt/3
        bn_triple.normalise(f, dt / 3)
        bn_triple.normalise(f, dt / 3)
        vals_triple[i] = bn_triple.normalise(f, dt / 3)[0] / 255.0

    max_diff = float(np.max(np.abs(vals_single - vals_triple)))
    return {
        "times": times.tolist(),
        "vals_single_call": vals_single.tolist(),
        "vals_triple_call": vals_triple.tolist(),
        "max_diff": max_diff,
        "cadence_invariant": max_diff < 0.02,
    }


# ---------------------------------------------------------------------------
# BandNormaliser cadence control
# ---------------------------------------------------------------------------


def bandnorm_cadence_control() -> dict:
    """Mathematical property test: same tau, different call rates.

    Returns {"at_Xs": {"30Hz": float, "100Hz": float, "diff": float}, ...}
    for elapsed times 1.0, 1.5, 2.0, 2.5 s after step onset.
    """
    n_bars = N_BARS
    frame_val = bytes([128] * n_bars)
    silent = bytes([0] * n_bars)

    duration_s = 3.0
    rates = {30: 1.0 / 30, 100: 1.0 / 100}
    series: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    for rate, dt in rates.items():
        bn = BandNormaliser(
            attack_tau_s=_ATTACK_TAU,
            release_tau_s=_RELEASE_TAU,
            gate=0.0,
            exertion_clip=_EXERTION_CLIP,
        )
        n_frames = int(duration_s * rate)
        silence_frames = int(1.0 * rate)
        times = np.arange(n_frames) * dt
        vals = np.zeros(n_frames)
        for i in range(n_frames):
            f = silent if i < silence_frames else frame_val
            normed = bn.normalise(f, dt)
            vals[i] = normed[0] / 255.0
        series[rate] = (times, vals)

    checks: dict[str, dict] = {}
    for elapsed in (1.0, 1.5, 2.0, 2.5):
        t30, v30 = series[30]
        t100, v100 = series[100]
        i30 = int(min(np.searchsorted(t30, elapsed), len(v30) - 1))
        i100 = int(min(np.searchsorted(t100, elapsed), len(v100) - 1))
        checks[f"at_{elapsed:.1f}s"] = {
            "30Hz": float(v30[i30]),
            "100Hz": float(v100[i100]),
            "diff": float(abs(v30[i30] - v100[i100])),
        }
    return checks


# ---------------------------------------------------------------------------
# Artifact generation — plots and CSVs
# ---------------------------------------------------------------------------


def _save_bar_mapping_plot(rows: list[dict], outdir: Path) -> None:
    bar_idx = [r["bar"] for r in rows]
    ideal_lo = [r["ideal_lo_hz"] for r in rows]
    ideal_hi = [r["ideal_hi_hz"] for r in rows]
    n_eff_lo = [r["native_eff_lo_hz"] for r in rows]
    n_eff_hi = [r["native_eff_hi_hz"] for r in rows]

    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle("Phase 2A-R — Bar mapping: ideal vs native effective edges", fontsize=11)
    for i in bar_idx:
        ax.plot([i, i], [ideal_lo[i], ideal_hi[i]], color="steelblue", lw=2, alpha=0.7)
        ax.plot([i + 0.2, i + 0.2], [n_eff_lo[i], n_eff_hi[i]], color="orange", lw=2, alpha=0.7)
    from matplotlib.patches import Patch
    ax.legend(
        handles=[
            Patch(color="steelblue", label="Ideal (corrected formula)"),
            Patch(color="orange", label="Native effective (FFT bins)"),
        ]
    )
    ax.set_xlabel("Bar index")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_yscale("log")
    fig.tight_layout()
    path = outdir / "bar_mapping_native_vs_ref.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")


def _save_bandnorm_cadence_plot(checks: dict, outdir: Path) -> None:
    labels = list(checks.keys())
    v30 = [checks[k]["30Hz"] for k in labels]
    v100 = [checks[k]["100Hz"] for k in labels]
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.suptitle("BandNormaliser cadence control — 30 Hz vs 100 Hz", fontsize=11)
    ax.plot(x, v30, "o-", label="30 Hz")
    ax.plot(x, v100, "s--", label="100 Hz")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20)
    ax.set_ylabel("Normalised bar 0 (0–1)")
    ax.legend()
    fig.tight_layout()
    path = outdir / "bandnorm_cadence.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")


def _save_stale_frame_plot(stale: dict, outdir: Path) -> None:
    times = np.array(stale["times"])
    v_single = np.array(stale["vals_single_call"])
    v_triple = np.array(stale["vals_triple_call"])

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle("Phase 2A-R — Stale-frame: single vs repeated normalise() calls", fontsize=11)
    ax.plot(times, v_single, label="Single call per tick (dt=33ms)")
    ax.plot(times, v_triple, ls="--", label="Triple calls per tick (dt=11ms × 3)")
    ax.axvline(1.0, color="green", ls="--", lw=0.8, label="signal on")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Normalised bar 0 (0–1)")
    ax.legend()
    fig.tight_layout()
    path = outdir / "stale_frame.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")


def _write_stereo_csv(
    signal_label: str,
    frames: list[dict],
    backend: str,
    outdir: Path,
    *,
    raw_key: str = "raw",
    normed_key: str = "normed",
    time_key: str = "t_sample_start",
) -> None:
    path = outdir / f"stereo_{signal_label}_{backend}.csv"
    with path.open("w", newline="") as fh:
        bar_cols = [f"bar_{i:02d}" for i in range(N_BARS)]
        writer = csv.writer(fh)
        writer.writerow(["frame", "t_s"] + bar_cols)
        for fi, frame in enumerate(frames):
            t = frame.get(time_key, fi / CAVA_FRAMERATE)
            row_data = frame.get(normed_key, frame.get("bars", np.zeros(N_BARS, dtype=np.uint8)))
            if isinstance(row_data, np.ndarray):
                vals = row_data.astype(float) / 255.0
            else:
                vals = np.array(row_data, dtype=float) / 255.0
            row = [fi, f"{t:.6f}"] + [f"{v:.6f}" for v in vals]
            writer.writerow(row)
    print(f"  CSV → {path}")


def _write_bar_mapping_csv(rows: list[dict], outdir: Path) -> None:
    path = outdir / "bar_mapping.csv"
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  CSV → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HueSync DSP Phase 2A-R — Recovery / Real-CAVA Validation"
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/dsp_phase2a_r",
        help="Directory for output artifacts",
    )
    parser.add_argument(
        "--cava",
        action="store_true",
        help="Run actual CAVA binary (requires LXC with CAVA installed)",
    )
    parser.add_argument("--cava-binary", default="cava", help="Path to CAVA binary")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {outdir}")

    # --- Generate signals ---
    print("\n=== Generating canonical test signals ===")
    signals = make_all_signals()
    for label, pcm in signals.items():
        info = pcm.info()
        print(f"  {label}: {info['duration_s']:.2f}s  sha256={info['sha256'][:16]}...")

    # --- Bar mapping ---
    print("\n=== Bar frequency mapping (corrected formula) ===")
    mapping_rows = bar_mapping_table()
    for row in mapping_rows:
        print(
            f"  bar {row['bar']:2d}: "
            f"ideal {row['ideal_lo_hz']:7.1f}–{row['ideal_hi_hz']:7.1f} Hz  "
            f"native_bins [{row['native_bin_lo']}:{row['native_bin_hi']}]"
        )
    _write_bar_mapping_csv(mapping_rows, outdir)
    _save_bar_mapping_plot(mapping_rows, outdir)

    # --- Native analysis ---
    print("\n=== Native analysis ===")
    native = NativeAnalyser()
    native_frames: dict[str, list[dict]] = {}
    for label, pcm in signals.items():
        native.reset()
        frames = native.run(pcm)
        native_frames[label] = frames
        print(f"  {label}: {len(frames)} native frames")

    # Stereo comparison: B (in-phase) vs C (opposite-phase) — native
    print("\n=== Stereo comparison (native) ===")
    stereo_native = run_stereo_comparison(
        signals["B"], signals["C"], "native", native_analyser=native
    )
    print(
        f"  in-phase mean raw={stereo_native['in_phase_mean_raw']:.4f}, "
        f"opp-phase mean raw={stereo_native['opp_phase_mean_raw']:.4f}, "
        f"ratio={stereo_native['ratio_raw']:.4f}"
    )
    _write_stereo_csv("B_inphase", native_frames["B"], "native", outdir)
    _write_stereo_csv("C_opposite", native_frames["C"], "native", outdir)

    # --- CAVA reference model ---
    print("\n=== CAVA reference model (source-derived, not binary-validated) ===")
    cava_ref = CavaReferenceModel()
    cava_ref_frames: dict[str, list[dict]] = {}
    for label, pcm in signals.items():
        cava_ref.reset()
        frames = cava_ref.run(pcm)
        cava_ref_frames[label] = frames
        print(f"  {label}: {len(frames)} cava_ref frames")

    # Stereo comparison: B vs C — cava_ref
    print("\n=== Stereo comparison (cava_ref) ===")
    stereo_cava_ref = run_stereo_comparison(
        signals["B"], signals["C"], "cava_ref", cava_ref=cava_ref
    )
    print(
        f"  in-phase mean raw={stereo_cava_ref['in_phase_mean_raw']:.4f}, "
        f"opp-phase mean raw={stereo_cava_ref['opp_phase_mean_raw']:.4f}, "
        f"ratio={stereo_cava_ref['ratio_raw']:.4f}"
    )
    _write_stereo_csv(
        "B_inphase", cava_ref_frames["B"], "cava_ref", outdir,
        raw_key="raw_pre_gravity", normed_key="bars", time_key="t_sample_start"
    )
    _write_stereo_csv(
        "C_opposite", cava_ref_frames["C"], "cava_ref", outdir,
        raw_key="raw_pre_gravity", normed_key="bars", time_key="t_sample_start"
    )

    # --- Optional: real CAVA binary ---
    cava_binary_stereo: dict | None = None
    if args.cava:
        print("\n=== Real CAVA binary run ===")
        runner = CavaRunner(cava_binary=args.cava_binary)
        try:
            ip_frames_bin, ip_stderr = runner.run(signals["B"])
            op_frames_bin, op_stderr = runner.run(signals["C"])
            print(f"  B (in-phase): {len(ip_frames_bin)} frames")
            print(f"  C (opposite): {len(op_frames_bin)} frames")
            if ip_stderr.strip():
                print(f"  stderr (B): {ip_stderr[:200]}")
            cava_binary_stereo = run_stereo_comparison(
                signals["B"], signals["C"], "cava_binary", cava_runner=runner
            )
            print(
                f"  in-phase mean={cava_binary_stereo['in_phase_mean_raw']:.4f}, "
                f"opp-phase mean={cava_binary_stereo['opp_phase_mean_raw']:.4f}, "
                f"ratio={cava_binary_stereo['ratio_raw']:.4f}"
            )
        except RuntimeError as exc:
            print(f"  CAVA binary error: {exc}")

    # --- Cadence control ---
    print("\n=== BandNormaliser cadence control ===")
    cadence = bandnorm_cadence_control()
    for elapsed, vals in cadence.items():
        print(
            f"  {elapsed}: 30Hz={vals['30Hz']:.4f}, 100Hz={vals['100Hz']:.4f}, "
            f"diff={vals['diff']:.5f}"
        )
    _save_bandnorm_cadence_plot(cadence, outdir)

    # --- Stale frame ---
    print("\n=== Stale-frame analysis ===")
    stale = stale_frame_analysis()
    print(
        f"  max_diff={stale['max_diff']:.5f}, "
        f"cadence_invariant={stale['cadence_invariant']}"
    )
    _save_stale_frame_plot(stale, outdir)

    # --- Manifest ---
    manifest = {
        "phase": "2A-R",
        "withdrawn_finding": (
            "~19% bar-mapping divergence — caused by (n+1)/(n_bars+1) formula error; WITHDRAWN"
        ),
        "corrected_formula": "edge[n] = lower * (upper/lower)^(n/n_bars) for n=0..n_bars",
        "signals": {k: v.info() for k, v in signals.items()},
        "config": {
            "SR": SR,
            "N_BARS": N_BARS,
            "LOWER_HZ": LOWER_HZ,
            "UPPER_HZ": UPPER_HZ,
            "CAVA_FRAMERATE": CAVA_FRAMERATE,
            "CAVA_NOISE_REDUCTION": CAVA_NOISE_REDUCTION,
            "WINDOW_SIZE": WINDOW_SIZE,
        },
        "numpy_version": np.__version__,
    }
    manifest_path = outdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  manifest → {manifest_path}")

    # --- Metrics ---
    metrics = {
        "stereo_comparison_native": stereo_native,
        "stereo_comparison_cava_ref": stereo_cava_ref,
        "bandnorm_cadence": cadence,
        "stale_frame": {
            k: v for k, v in stale.items()
            if k not in ("times", "vals_single_call", "vals_triple_call")
        },
    }
    if cava_binary_stereo is not None:
        metrics["stereo_comparison_cava_binary"] = cava_binary_stereo
    metrics_path = outdir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2))
    print(f"  metrics → {metrics_path}")

    print(f"\nDone. All outputs in {outdir}")


if __name__ == "__main__":
    main()
