#!/usr/bin/env python3
"""HueSync DSP Phase 2A — Deterministic Same-PCM Comparison.

Compares HueSync native PCM spectrum analysis vs CAVA 0.10.4 algorithm
using six identical deterministic test signals.

Usage (Python simulation, runs locally):
    python3 scripts/phase2a_compare.py [--output-dir /tmp/phase2a]

Usage (actual CAVA binary, run on LXC):
    python3 scripts/phase2a_compare.py --cava [--cava-binary /usr/bin/cava]

CAVA simulation sources:
- cavacore.c @ tag 0.10.4 (github.com/karlstav/cava)
- config.c @ tag 0.10.4 — default values
- CAVACORE.md: "autosens=1 gives dynamically adjusted output 0 to 1"
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from huesync.pcm_source import WINDOW_SIZE, PcmStft
from huesync.sync_engine import BandNormaliser

# ---------------------------------------------------------------------------
# Global experiment parameters
# ---------------------------------------------------------------------------

SR = 44100       # sample rate
N_BARS = 30
LOWER_HZ = 50.0
UPPER_HZ = 12000.0

# Native STFT
NATIVE_HOP = round(SR * 0.010)          # 441 samples ≈ 10 ms
NATIVE_HOP_DT = NATIVE_HOP / SR         # exact audio-time dt

# BandNormaliser production defaults
ATTACK_TAU = BandNormaliser.DEFAULT_ATTACK_TAU_S    # 5 ms
RELEASE_TAU = BandNormaliser.DEFAULT_RELEASE_TAU_S  # 700 ms
GATE = BandNormaliser.DEFAULT_GATE                  # 5.0
EXERTION_CLIP = BandNormaliser.DEFAULT_EXERTION_CLIP  # 3.0

# CAVA 0.10.4 defaults (from config.c)
CAVA_FRAMERATE = 60
CAVA_NOISE_REDUCTION = 0.77   # config default 77, /100 in load_config
CAVA_AUTOSENS = True          # autosens = 1
CAVA_SENSITIVITY = 1.0        # sensitivity = 100, /100 in main
CAVA_GRAVITY = 100
CAVA_MONO_OPT = "average"


# ---------------------------------------------------------------------------
# Bar frequency mapping
# ---------------------------------------------------------------------------

def native_bar_edges(n_bars: int = N_BARS, lower: float = LOWER_HZ,
                     upper: float = UPPER_HZ) -> list[float]:
    """Native _mag_to_bar_bytes bar edges (n_bars+1 values).

    Bar i spans [edges[i], edges[i+1]].
    Formula: 10^(log10(lower) + i/n_bars * (log10(upper)-log10(lower)))
    """
    log_lo = math.log10(max(lower, 1.0))
    log_hi = math.log10(max(upper, lower + 1.0))
    return [10.0 ** (log_lo + i / n_bars * (log_hi - log_lo)) for i in range(n_bars + 1)]


def cava_bar_edges(n_bars: int = N_BARS, lower: float = LOWER_HZ,
                   upper: float = UPPER_HZ) -> list[float]:
    """CAVA cavacore.c bar edges (n_bars+1 values including lower_cutoff).

    cut_off_frequency[n] = upper * 10^(log10(upper/lower) * ((n+1)/(n_bars+1) - 1))
                          = lower * (upper/lower)^((n+1)/(n_bars+1))
    for n = 0..n_bars.  n=n_bars yields exactly upper.
    Bar i spans [edges[i], edges[i+1]].  edges[0] = lower_cutoff (hardcoded).
    """
    freq_const = math.log10(upper / lower)
    edges = [lower]
    for n in range(n_bars):
        bar_dist = freq_const * ((n + 1) / (n_bars + 1) - 1)
        edges.append(upper * 10 ** bar_dist)
    return edges  # length n_bars+1; last entry ≈ lower*(upper/lower)^(n_bars/(n_bars+1))


def bar_centre_hz(edges: list[float]) -> list[float]:
    """Geometric mean of adjacent edge pairs as bar centre frequencies."""
    return [math.sqrt(edges[i] * edges[i + 1]) for i in range(len(edges) - 1)]


# ---------------------------------------------------------------------------
# Test signal generators  (stereo float32, shape (N, 2))
# ---------------------------------------------------------------------------

def _stereo(mono: np.ndarray) -> np.ndarray:
    s = mono[:, np.newaxis]
    return np.concatenate([s, s], axis=1)


def _opp(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, -mono], axis=1)


def _sine(freq: float, amp: float, n: int) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / SR
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def gen_silence(duration_s: float = 2.0) -> np.ndarray:
    n = int(duration_s * SR)
    return np.zeros((n, 2), dtype=np.float32)


def gen_steady_sine(freq: float = 440.0, amp: float = 0.5,
                    silence_pre: float = 1.0, duration: float = 3.0,
                    silence_post: float = 2.0) -> np.ndarray:
    pre = gen_silence(silence_pre)
    tone = _stereo(_sine(freq, amp, int(duration * SR)))
    post = gen_silence(silence_post)
    return np.concatenate([pre, tone, post])


def gen_amplitude_step(freq: float = 440.0,
                       amp_lo: float = 0.10, amp_hi: float = 0.70,
                       t_sil: float = 1.0, t_lo: float = 2.0,
                       t_hi: float = 2.0, t_lo2: float = 2.0,
                       t_end: float = 2.0) -> np.ndarray:
    segments = [
        gen_silence(t_sil),
        _stereo(_sine(freq, amp_lo, int(t_lo * SR))),
        _stereo(_sine(freq, amp_hi, int(t_hi * SR))),
        _stereo(_sine(freq, amp_lo, int(t_lo2 * SR))),
        gen_silence(t_end),
    ]
    return np.concatenate(segments)


def gen_multi_tone(freqs: tuple[float, ...] = (100.0, 1000.0, 8000.0),
                   amp: float = 0.25, duration: float = 5.0) -> np.ndarray:
    n = int(duration * SR)
    sig = sum(_sine(f, amp, n) for f in freqs).astype(np.float32)
    sig = np.clip(sig, -1.0, 1.0)
    return _stereo(sig)


def gen_transients(freq: float = 440.0, amp: float = 0.7,
                   burst_ms: float = 10.0, gap_ms: float = 190.0,
                   n_bursts: int = 10) -> np.ndarray:
    burst_n = int(burst_ms / 1000 * SR)
    chunks = []
    for _ in range(n_bursts):
        chunks.append(_stereo(_sine(freq, amp, burst_n)))
        chunks.append(gen_silence(gap_ms / 1000))
    return np.concatenate(chunks)


def gen_opposite_phase(freq: float = 440.0, amp: float = 0.5,
                       silence_pre: float = 0.5, duration: float = 3.0,
                       silence_post: float = 0.5) -> np.ndarray:
    pre = gen_silence(silence_pre)
    tone = _opp(_sine(freq, amp, int(duration * SR)))
    post = gen_silence(silence_post)
    return np.concatenate([pre, tone, post])


TEST_SIGNALS: dict[str, np.ndarray] = {}  # populated in main()


# ---------------------------------------------------------------------------
# Native analyser
# ---------------------------------------------------------------------------

def _mag_to_bar_bytes(mag: np.ndarray, n_bars: int, lower_hz: float,
                      upper_hz: float, sample_rate: int) -> bytes:
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

    Processes mono float32 samples derived from stereo (L+R)/2 downmix.
    """

    def __init__(self, n_bars: int = N_BARS, lower_hz: float = LOWER_HZ,
                 upper_hz: float = UPPER_HZ, sample_rate: int = SR) -> None:
        self._stft = PcmStft(sample_rate)
        self._normaliser = BandNormaliser(
            attack_tau_s=ATTACK_TAU,
            release_tau_s=RELEASE_TAU,
            gate=GATE,
            exertion_clip=EXERTION_CLIP,
        )
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz
        self._sample_rate = sample_rate
        self._dt = self._stft.hop / sample_rate  # exact audio time per frame

    @property
    def hop(self) -> int:
        return self._stft.hop

    def reset(self) -> None:
        self._stft = PcmStft(self._sample_rate)
        self._normaliser = BandNormaliser(
            attack_tau_s=ATTACK_TAU,
            release_tau_s=RELEASE_TAU,
            gate=GATE,
            exertion_clip=EXERTION_CLIP,
        )

    def push(self, stereo: np.ndarray) -> list[dict]:
        """Push stereo float32 (N,2). Returns list of frame dicts."""
        mono = (stereo[:, 0] + stereo[:, 1]) / 2.0  # (L+R)/2
        results = []
        for mag in self._stft.push(mono):
            raw = _mag_to_bar_bytes(mag, self._n_bars, self._lower_hz,
                                    self._upper_hz, self._sample_rate)
            normed = self._normaliser.normalise(raw, self._dt)
            results.append({
                "raw": np.frombuffer(raw, dtype=np.uint8).copy(),
                "normed": np.frombuffer(normed, dtype=np.uint8).copy(),
            })
        return results


# ---------------------------------------------------------------------------
# CAVA simulator (implements cavacore.c 0.10.4 algorithm)
# ---------------------------------------------------------------------------

class CavaSimulator:
    """Python implementation of CAVA 0.10.4 cavacore algorithm.

    Sources:
    - cavacore.c @0.10.4: gravity, noise_reduction, autosensitivity
    - config.c @0.10.4: framerate=60, noise_reduction=0.77, autosens=1
    - CAVACORE.md: output range [0,1] with autosens

    Stereo processing: L and R are FFT'd independently then bar-averaged
    (AVERAGE mono_option). Input stereo float32; int16-scale internally
    (multiply by 32767) to match CAVA's raw int16 input expectation.

    Multi-resolution windows (at 44100 Hz, treble_buffer_size*8 = 1024*8):
      bass  = 8192 samples (5.4 Hz/bin)  — used for low-frequency bars
      mid   = 4096 samples (10.8 Hz/bin)
      treble = 1024 samples (43.1 Hz/bin) — used for high-frequency bars

    Each bar is assigned the smallest FFT that gives at least 1 bin.
    """

    def __init__(
        self,
        n_bars: int = N_BARS,
        sample_rate: int = SR,
        lower_hz: float = LOWER_HZ,
        upper_hz: float = UPPER_HZ,
        framerate: int = CAVA_FRAMERATE,
        noise_reduction: float = CAVA_NOISE_REDUCTION,
        autosens: bool = CAVA_AUTOSENS,
        sensitivity: float = CAVA_SENSITIVITY,
    ) -> None:
        self.n_bars = n_bars
        self.sample_rate = sample_rate
        self.lower_hz = lower_hz
        self.upper_hz = upper_hz
        self.framerate = framerate
        self.noise_reduction = noise_reduction
        self.autosens = autosens
        self.sens = sensitivity
        self.sens_init = True  # True until first non-silence overshoot check

        # Frame size in samples
        self.frame_samples = sample_rate // framerate  # 735 at 44100/60

        # FFT sizes (from cavacore.c — treble_buffer_size * scale(rate))
        treble_base = 128
        if sample_rate > 32500:
            treble_base *= 8   # → 1024 at 44100 Hz
        self._fft_bass = treble_base * 8    # 8192
        self._fft_mid = treble_base * 4     # 4096
        self._fft_treble = treble_base      # 1024

        # Hann windows (from cavacore.c: 0.5*(1-cos(2πi/(N-1))))
        self._win = {
            sz: 0.5 * (1 - np.cos(2 * math.pi * np.arange(sz) / (sz - 1)))
            for sz in (self._fft_bass, self._fft_mid, self._fft_treble)
        }

        # CAVA bar edges (n_bars+1 values; bar n spans [edges[n], edges[n+1]])
        # Lower bound of bar 0 = lower_hz (hardcoded).
        freq_const = math.log10(upper_hz / lower_hz)
        self._edges = [lower_hz]
        for n in range(n_bars):
            bd = freq_const * ((n + 1) / (n_bars + 1) - 1)
            self._edges.append(upper_hz * 10 ** bd)

        # Assign each bar to the smallest FFT giving ≥1 bin
        self._bar_assign: list[tuple[int, int, int]] = []  # (fft_sz, bin_lo, bin_hi)
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

        # Rolling PCM buffers (L and R separately), float64 in int16 scale
        self._buf_l = np.zeros(self._fft_bass, dtype=np.float64)
        self._buf_r = np.zeros(self._fft_bass, dtype=np.float64)
        self._pending = 0  # samples since last output frame

        # Gravity state (cavacore.c: cava_peak, cava_fall)
        self._peak = np.zeros(n_bars, dtype=np.float64)
        self._fall = np.zeros(n_bars, dtype=np.float64)

        # Noise-reduction IIR state (cavacore.c: cava_mem)
        self._mem = np.zeros(n_bars, dtype=np.float64)

        # gravity_mod = pow(60/framerate, 2.5) * 1.54 / noise_reduction
        self._gravity_mod = (60.0 / framerate) ** 2.5 * 1.54 / noise_reduction

        # Output-amplitude normalization factor (see _fft_mag)
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
        self.sens = CAVA_SENSITIVITY
        self.sens_init = True

    def _fft_mag(self, buf: np.ndarray, fft_sz: int) -> np.ndarray:
        """Hann-windowed FFT magnitude, normalised so full-scale sine ≈ 1.0."""
        seg = buf[-fft_sz:] * self._win[fft_sz]
        mag = np.abs(np.fft.rfft(seg))
        return mag / self._norm[fft_sz]

    def _bar_from_mags(self, mag_l: dict[int, np.ndarray],
                       mag_r: dict[int, np.ndarray], bar_idx: int) -> float:
        fft_sz, b_lo, b_hi = self._bar_assign[bar_idx]
        ml = float(np.mean(mag_l[fft_sz][b_lo:b_hi]))
        mr = float(np.mean(mag_r[fft_sz][b_lo:b_hi]))
        return (ml + mr) / 2.0  # AVERAGE mono_option

    def _compute_frame(self) -> tuple[np.ndarray, np.ndarray]:
        """One output frame from current rolling buffers.

        Returns (raw_before_gravity, conditioned_0_255).
        raw_before_gravity: bar magnitudes after normalisation, before
        gravity/noise_reduction/autosens — useful for shape comparison.
        """
        fft_sizes = {self._fft_bass, self._fft_mid, self._fft_treble}
        mag_l = {sz: self._fft_mag(self._buf_l, sz) for sz in fft_sizes}
        mag_r = {sz: self._fft_mag(self._buf_r, sz) for sz in fft_sizes}

        raw_bars = np.array(
            [self._bar_from_mags(mag_l, mag_r, n) for n in range(self.n_bars)]
        )

        # --- Gravity (peak-hold + quadratic decay) — cavacore.c ---
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

        # --- Noise-reduction IIR: out = mem * nr + out — cavacore.c ---
        out = self._mem * self.noise_reduction + out
        self._mem = out.copy()

        # --- Autosensitivity (from cava.c) ---
        silence = float(np.max(out)) < 1e-8
        if self.autosens:
            if np.any(out * self.sens > 1.0):
                self.sens *= 0.98
                self.sens_init = False
            elif not silence:
                self.sens *= 1.002
                if self.sens_init:
                    self.sens *= 1.1

        # Scale: sens keeps output ≤ 1.0; then ×255 for 8-bit
        conditioned = np.clip(out * self.sens * 255.0, 0.0, 255.0).astype(np.uint8)

        return raw_bars, conditioned

    def push(self, stereo: np.ndarray) -> list[dict]:
        """Push stereo float32 (N,2). Returns frame dicts at CAVA_FRAMERATE."""
        # Scale to int16 range — CAVA reads int16 PCM
        pcm = (stereo * 32767.0).astype(np.float64)
        n_samples = pcm.shape[0]
        frames = []
        pos = 0
        while pos < n_samples:
            space = self.frame_samples - self._pending
            take = min(space, n_samples - pos)
            chunk = pcm[pos : pos + take]
            # Roll buffers and append new samples
            roll = len(chunk)
            self._buf_l = np.roll(self._buf_l, -roll)
            self._buf_l[-roll:] = chunk[:, 0]
            self._buf_r = np.roll(self._buf_r, -roll)
            self._buf_r[-roll:] = chunk[:, 1]
            pos += take
            self._pending += take
            if self._pending >= self.frame_samples:
                self._pending = 0
                raw_bars, cond = self._compute_frame()
                frames.append({"raw": raw_bars, "normed": cond})
        return frames


# ---------------------------------------------------------------------------
# CAVA binary runner (for LXC with actual CAVA installed)
# ---------------------------------------------------------------------------

class CavaBinaryRunner:
    """Runs actual CAVA binary and collects bar output for a PCM signal.

    Writes PCM as stereo S16_LE to a FIFO that CAVA reads (method=pipe).
    Reads CAVA's 8-bit raw output from a second FIFO.
    """

    def __init__(self, cava_binary: str = "cava", n_bars: int = N_BARS,
                 lower_hz: float = LOWER_HZ, upper_hz: float = UPPER_HZ) -> None:
        self._binary = cava_binary
        self._n_bars = n_bars
        self._lower_hz = lower_hz
        self._upper_hz = upper_hz

    def run(self, stereo: np.ndarray, warmup_s: float = 2.0) -> list[dict]:
        """Feed stereo float32 signal through actual CAVA; return frame dicts."""
        with tempfile.TemporaryDirectory() as tmp:
            in_fifo = os.path.join(tmp, "cava_in.pcm")
            out_fifo = os.path.join(tmp, "cava_out.raw")
            conf_path = os.path.join(tmp, "cava.conf")
            os.mkfifo(in_fifo)
            os.mkfifo(out_fifo)

            conf = f"""[general]
bars = {self._n_bars}
lower_cutoff_freq = {int(self._lower_hz)}
higher_cutoff_freq = {int(self._upper_hz)}
framerate = {CAVA_FRAMERATE}
autosens = 1
sensitivity = 100

[input]
method = pipe
source = {in_fifo}
sample_rate = {SR}
sample_bits = 16
channels = 2

[output]
method = raw
raw_target = {out_fifo}
data_format = binary
bit_format = 8bit
channels = mono
"""
            Path(conf_path).write_text(conf)

            # Convert float32 → S16_LE bytes
            s16 = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)
            # interleaved stereo
            raw_pcm = s16.tobytes()

            frames: list[dict] = []
            stop = threading.Event()

            def read_cava() -> None:
                fd = os.open(out_fifo, os.O_RDONLY)
                buf = b""
                try:
                    while not stop.is_set():
                        try:
                            chunk = os.read(fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        buf += chunk
                        while len(buf) >= self._n_bars:
                            frame_bytes = buf[: self._n_bars]
                            buf = buf[self._n_bars :]
                            arr = np.frombuffer(frame_bytes, dtype=np.uint8).copy()
                            frames.append({"raw": arr.astype(float) / 255.0,
                                           "normed": arr})
                finally:
                    os.close(fd)

            proc = subprocess.Popen(
                [self._binary, "-p", conf_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            reader = threading.Thread(target=read_cava, daemon=True)
            reader.start()

            # Write PCM at roughly real-time to let CAVA process it properly
            chunk_sz = SR * 2 * 2  # 1 second of S16_LE stereo
            pos = 0
            fd_in = os.open(in_fifo, os.O_WRONLY)
            try:
                while pos < len(raw_pcm):
                    end = min(pos + chunk_sz, len(raw_pcm))
                    os.write(fd_in, raw_pcm[pos:end])
                    pos = end
                    time.sleep(1.0)
            finally:
                os.close(fd_in)

            time.sleep(0.5)
            stop.set()
            proc.terminate()
            proc.wait()
            reader.join(timeout=2.0)

        return frames


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _dominant_bar(frames_normed: np.ndarray) -> int:
    """Index of bar with highest mean value across all frames."""
    return int(np.argmax(np.mean(frames_normed, axis=0)))


def attack_release_metrics(values: np.ndarray, times: np.ndarray,
                           t_on: float, t_off: float) -> dict:
    """Compute 10→90% attack and 90→10% release times.

    values: 1D array of bar amplitude values.
    times:  corresponding timestamps in seconds.
    t_on:   approximate time of signal onset.
    t_off:  approximate time of signal offset.
    """
    mask_on = (times >= t_on) & (times < t_off)
    mask_off = times >= t_off

    if not np.any(mask_on) or not np.any(mask_off):
        return {}

    ss_mean = float(np.mean(values[mask_on]))
    ss_std = float(np.std(values[mask_on]))
    floor = float(np.mean(values[times < t_on])) if np.any(times < t_on) else 0.0

    # Attack: 10→90% of (ss_mean - floor)
    thr_lo = floor + 0.10 * (ss_mean - floor)
    thr_hi = floor + 0.90 * (ss_mean - floor)

    attack_frames = np.where(mask_on)[0]
    attack_start = None
    attack_end = None
    for idx in attack_frames:
        if attack_start is None and values[idx] >= thr_lo:
            attack_start = times[idx]
        if values[idx] >= thr_hi:
            attack_end = times[idx]
            break
    attack_s = (attack_end - attack_start) if (attack_start and attack_end) else None

    # Release: 90→10%
    release_frames = np.where(mask_off)[0]
    rel_start = None
    rel_end = None
    for idx in release_frames:
        if rel_start is None and values[idx] >= thr_hi:
            rel_start = times[idx]
        if rel_start is not None and values[idx] <= thr_lo:
            rel_end = times[idx]
            break
    release_s = (rel_end - rel_start) if (rel_start and rel_end) else None

    # First-response latency after t_on
    latency = None
    for idx in attack_frames:
        if values[idx] > floor + 1e-4:
            latency = times[idx] - t_on
            break

    return {
        "floor": floor,
        "ss_mean": ss_mean,
        "ss_std": ss_std,
        "attack_10_90_s": attack_s,
        "release_90_10_s": release_s,
        "latency_s": latency,
    }


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def _normed_series(frames: list[dict], bar_idx: int | None = None,
                   key: str = "normed") -> tuple[np.ndarray, np.ndarray]:
    """Extract (times, values) for a specific bar or all bars."""
    arr = np.stack([f[key] for f in frames])  # (n_frames, n_bars)
    if bar_idx is not None:
        vals = arr[:, bar_idx].astype(float) / 255.0
    else:
        vals = arr.astype(float) / 255.0
    return arr, vals


class TestResult:
    def __init__(self, name: str, native_frames: list[dict],
                 cava_frames: list[dict], signal_duration_s: float) -> None:
        self.name = name
        self.native = native_frames
        self.cava = cava_frames
        self.signal_duration_s = signal_duration_s

        # Build time axes
        n_native = len(native_frames)
        n_cava = len(cava_frames)
        self.native_times = np.arange(n_native) * NATIVE_HOP_DT
        self.cava_times = np.arange(n_cava) / CAVA_FRAMERATE

        # Normed arrays (0-1)
        self.native_normed = (
            np.stack([f["normed"] for f in native_frames]).astype(float) / 255.0
            if native_frames else np.zeros((0, N_BARS))
        )
        self.cava_normed = (
            np.stack([f["normed"] for f in cava_frames]).astype(float) / 255.0
            if cava_frames else np.zeros((0, N_BARS))
        )


def run_test(name: str, signal: np.ndarray,
             native: NativeAnalyser, cava: CavaSimulator) -> TestResult:
    native.reset()
    cava.reset()
    native_frames = native.push(signal)
    cava_frames = cava.push(signal)
    dur = signal.shape[0] / SR
    return TestResult(name, native_frames, cava_frames, dur)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_csv(result: TestResult, outdir: Path) -> None:
    path = outdir / f"{result.name}.csv"
    with path.open("w", newline="") as fh:
        bar_cols = [f"bar_{i:02d}" for i in range(N_BARS)]
        writer = csv.writer(fh)
        writer.writerow(["backend", "frame", "time_s"] + bar_cols)
        for fi, (t, frame) in enumerate(
            zip(result.native_times, result.native, strict=False)
        ):
            row = ["native", fi, f"{t:.6f}"]
            row += [f"{v:.6f}" for v in frame["normed"].astype(float) / 255.0]
            writer.writerow(row)
        for fi, (t, frame) in enumerate(zip(result.cava_times, result.cava, strict=False)):
            row = ["cava_sim", fi, f"{t:.6f}"]
            normed = frame["normed"].astype(float) / 255.0
            row += [f"{v:.6f}" for v in normed]
            writer.writerow(row)
    print(f"  CSV → {path}")


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _fig(title: str) -> tuple:
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle(title, fontsize=11)
    return fig, ax


def plot_bar_spectrum(result: TestResult, outdir: Path,
                      n_edges: list[float], c_edges: list[float]) -> None:
    """Steady-state mean bar spectrum (shape comparison)."""
    if not result.native or not result.cava:
        return
    fig, ax = _fig(f"{result.name} — steady-state bar spectrum (shape-normalised)")

    n_arr = result.native_normed
    c_arr = result.cava_normed

    n_mean = np.mean(n_arr, axis=0)
    c_mean = np.mean(c_arr, axis=0)

    # Shape-normalise
    n_peak = max(n_mean.max(), 1e-8)
    c_peak = max(c_mean.max(), 1e-8)

    x = np.arange(N_BARS)
    ax.bar(x - 0.2, n_mean / n_peak, 0.35, label="Native (shape-norm)", color="steelblue")
    ax.bar(x + 0.2, c_mean / c_peak, 0.35, label="CAVA-sim (shape-norm)", color="orange")
    ax.set_xlabel("Bar index")
    ax.set_ylabel("Normalised amplitude")
    ax.legend()
    ax.set_xticks(x[::5])

    # Secondary: absolute comparison
    ax2 = ax.twinx()
    ax2.plot(x, n_mean, "b--", alpha=0.4, label="Native abs")
    ax2.plot(x, c_mean, "r--", alpha=0.4, label="CAVA abs")
    ax2.set_ylabel("Absolute (0–1)", color="gray")
    ax2.tick_params(axis="y", colors="gray")

    fig.tight_layout()
    path = outdir / f"{result.name}_spectrum.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")


def plot_time_response(result: TestResult, outdir: Path,
                       bar_idx: int, t_on: float, t_off: float,
                       title_suffix: str = "") -> dict:
    """Time response of dominant bar for native vs CAVA-sim."""
    if not result.native or not result.cava:
        return {}
    fig, ax = _fig(f"{result.name} — bar {bar_idx} time response{title_suffix}")

    n_vals = result.native_normed[:, bar_idx]
    c_vals = result.cava_normed[:, bar_idx]

    ax.plot(result.native_times, n_vals, color="steelblue", lw=1.2,
            label=f"Native (bar {bar_idx})")
    ax.plot(result.cava_times, c_vals, color="orange", lw=1.2,
            label=f"CAVA-sim (bar {bar_idx})")
    ax.axvline(t_on, color="green", ls="--", lw=0.8, label="signal on")
    ax.axvline(t_off, color="red", ls="--", lw=0.8, label="signal off")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Normalised bar value (0–1)")
    ax.legend(fontsize=9)

    fig.tight_layout()
    path = outdir / f"{result.name}_time_response.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")

    # Metrics
    n_metrics = attack_release_metrics(n_vals, result.native_times, t_on, t_off)
    c_metrics = attack_release_metrics(c_vals, result.cava_times, t_on, t_off)
    return {"native": n_metrics, "cava_sim": c_metrics}


def plot_opposite_phase(result_in_phase: TestResult, result_op: TestResult,
                        outdir: Path) -> None:
    """Compare in-phase vs opposite-phase for bar energy."""
    if not result_in_phase.native or not result_op.native:
        return
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Test F — Opposite-phase stereo (L=+sine, R=−sine)", fontsize=11)

    for ax, backend, nt_ip, ct_ip, nt_op, ct_op in (
        (axes[0], "Native",
         result_in_phase.native_times, result_in_phase.native_normed[:, :].mean(axis=1),
         result_op.native_times, result_op.native_normed[:, :].mean(axis=1)),
        (axes[1], "CAVA-sim",
         result_in_phase.cava_times, result_in_phase.cava_normed[:, :].mean(axis=1),
         result_op.cava_times, result_op.cava_normed[:, :].mean(axis=1)),
    ):
        ax.plot(nt_ip, ct_ip, label="In-phase L=R")
        ax.plot(nt_op, ct_op, label="Opposite-phase L=−R", ls="--")
        ax.set_title(backend)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Mean bar (all bars)")
        ax.legend(fontsize=9)

    fig.tight_layout()
    path = outdir / "test_F_opposite_phase.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")


def plot_bandnorm_cadence(outdir: Path) -> dict:
    """BandNormaliser cadence-invariance test.

    Feeds the same step signal to two BandNormaliser instances at 30 Hz
    and 100 Hz (different dt).  Both should produce approximately the same
    value at the same elapsed real time.
    """
    # Step signal: 30 bars, value 0 → 128 at t=0
    n_bars = 30
    frame_val = bytes([128] * n_bars)
    silent = bytes([0] * n_bars)

    duration_s = 3.0
    rates = {30: 1.0 / 30, 100: 1.0 / 100}
    results = {}

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("BandNormaliser cadence control — same tau at 30 Hz vs 100 Hz", fontsize=11)

    for ax, (rate, dt) in zip(axes, rates.items(), strict=False):
        bn = BandNormaliser(
            attack_tau_s=ATTACK_TAU,
            release_tau_s=RELEASE_TAU,
            gate=0.0,        # disable gate for this test
            exertion_clip=EXERTION_CLIP,
        )
        n_frames = int(duration_s * rate)
        # 1 second silence then step
        silence_frames = int(1.0 * rate)
        times = np.arange(n_frames) * dt
        vals = np.zeros(n_frames)
        for i in range(n_frames):
            f = silent if i < silence_frames else frame_val
            normed = bn.normalise(f, dt)
            vals[i] = normed[0] / 255.0

        results[rate] = {"times": times.tolist(), "vals": vals.tolist()}
        ax.plot(times, vals, label=f"{rate} Hz (dt={dt*1000:.1f} ms)")
        ax.axvline(1.0, color="green", ls="--", lw=0.8, label="step on")
        ax.set_title(f"{rate} Hz update rate")
        ax.set_xlabel("Elapsed time (s)")
        ax.set_ylabel("Normalised bar 0 (0–1)")
        ax.legend(fontsize=9)

    # Quantify difference at 1 s and 2 s
    v30 = np.array(results[30]["vals"])
    t30 = np.array(results[30]["times"])
    v100 = np.array(results[100]["vals"])
    t100 = np.array(results[100]["times"])

    checks = {}
    for elapsed in (1.0, 1.5, 2.0, 2.5):
        i30 = int(min(np.searchsorted(t30, elapsed), len(v30) - 1))
        i100 = int(min(np.searchsorted(t100, elapsed), len(v100) - 1))
        checks[f"at_{elapsed:.1f}s"] = {
            "30Hz": float(v30[i30]),
            "100Hz": float(v100[i100]),
            "diff": float(abs(v30[i30] - v100[i100])),
        }

    fig.tight_layout()
    path = outdir / "bandnorm_cadence.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")

    return checks


def plot_cava_stale_frame(outdir: Path) -> dict:
    """Quantify CAVA stale-frame repeated BandNormaliser issue.

    CavaPipeline.latest() calls normalise() with wall-clock dt ≈ 1/30 s.
    CAVA writes at 60 Hz.  FifoReader keeps only latest → every other frame
    is discarded.  BandNormaliser runs at 30 Hz (render rate) not 60 Hz (CAVA rate).
    This is correct (dt-based EMA is call-rate-independent) UNLESS the render
    loop stalls: then dt accumulates and a larger alpha is applied.

    This test shows what happens when normalise() is called repeatedly with
    the SAME stale frame (no new CAVA output) at twice the expected rate.
    """
    n_bars = 30
    # Simulate: CAVA frame holds steady at 128 for 1 second
    cava_frame = bytes([128] * n_bars)
    silence = bytes([0] * n_bars)

    bn_correct = BandNormaliser(attack_tau_s=ATTACK_TAU, release_tau_s=RELEASE_TAU,
                                gate=0.0, exertion_clip=EXERTION_CLIP)
    bn_stale = BandNormaliser(attack_tau_s=ATTACK_TAU, release_tau_s=RELEASE_TAU,
                              gate=0.0, exertion_clip=EXERTION_CLIP)

    rate = 30
    dt = 1.0 / rate
    duration = 4.0
    n = int(duration * rate)
    silence_frames = int(1.0 * rate)

    times = np.arange(n) * dt
    vals_correct = np.zeros(n)
    vals_stale = np.zeros(n)

    for i in range(n):
        f = silence if i < silence_frames else cava_frame
        vals_correct[i] = bn_correct.normalise(f, dt)[0] / 255.0
        # Stale: same frame called twice per tick with dt/2
        bn_stale.normalise(f, dt / 2)
        vals_stale[i] = bn_stale.normalise(f, dt / 2)[0] / 255.0

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle("CAVA stale-frame test — dt split vs correct dt", fontsize=11)
    ax.plot(times, vals_correct, label="Correct (dt=33 ms × 1 per tick)")
    ax.plot(times, vals_stale, ls="--", label="Stale (dt=16 ms × 2 per tick)")
    ax.axvline(1.0, color="green", ls="--", lw=0.8, label="signal on")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Normalised bar 0 (0–1)")
    ax.legend(fontsize=9)
    fig.tight_layout()
    path = outdir / "cava_stale_frame.png"
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(f"  plot → {path}")

    # Measure difference
    diff = float(np.max(np.abs(vals_correct - vals_stale)))
    return {"max_diff": diff, "cadence_invariant": diff < 0.02}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="HueSync DSP Phase 2A comparison")
    parser.add_argument("--output-dir", default="/tmp/phase2a")
    parser.add_argument("--cava", action="store_true",
                        help="Use real CAVA binary (requires LXC)")
    parser.add_argument("--cava-binary", default="cava")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {outdir}")

    native = NativeAnalyser()
    cava = CavaSimulator()

    n_edges = native_bar_edges()
    c_edges = cava_bar_edges()
    n_centres = bar_centre_hz(n_edges)
    c_centres = bar_centre_hz(c_edges)

    # --- Band mapping report ---
    print("\n=== Bar frequency mapping (bar index : native centre Hz : CAVA centre Hz) ===")
    for i in range(N_BARS):
        print(f"  bar {i:2d}: native {n_centres[i]:7.1f} Hz  |  cava {c_centres[i]:7.1f} Hz  "
              f"(delta: {abs(n_centres[i]-c_centres[i]):.1f} Hz)")

    # --- Generate signals ---
    sig_A = gen_silence(2.0)
    sig_B = gen_steady_sine(440.0, 0.5, 1.0, 3.0, 2.0)
    sig_C = gen_amplitude_step(440.0, 0.10, 0.70, 1.0, 2.0, 2.0, 2.0, 2.0)
    sig_D = gen_multi_tone((100.0, 1000.0, 8000.0), 0.25, 5.0)
    sig_E = gen_transients(440.0, 0.7, 10.0, 190.0, 10)
    sig_F = gen_opposite_phase(440.0, 0.5, 0.5, 3.0, 0.5)
    # In-phase version of F for direct comparison
    sig_F_in = gen_steady_sine(440.0, 0.5, 0.5, 3.0, 0.5)

    print("\n=== Running experiments ===")

    results = {}
    for name, sig in [("A_silence", sig_A), ("B_steady_sine", sig_B),
                       ("C_amplitude_step", sig_C), ("D_multi_tone", sig_D),
                       ("E_transients", sig_E), ("F_opposite_phase", sig_F),
                       ("F_in_phase", sig_F_in)]:
        print(f"  {name} ({sig.shape[0]/SR:.1f}s)...", end=" ", flush=True)
        r = run_test(name, sig, native, cava)
        results[name] = r
        write_csv(r, outdir)
        print(f"native={len(r.native)} frames, cava={len(r.cava)} frames")

    # --- BandNormaliser cadence test ---
    print("\n=== BandNormaliser cadence control ===")
    cadence_checks = plot_bandnorm_cadence(outdir)
    for elapsed, vals in cadence_checks.items():
        print(f"  {elapsed}: 30Hz={vals['30Hz']:.4f}, 100Hz={vals['100Hz']:.4f}, "
              f"diff={vals['diff']:.5f}")

    # --- Stale-frame test ---
    print("\n=== CAVA stale-frame analysis ===")
    stale = plot_cava_stale_frame(outdir)
    print(f"  max_diff={stale['max_diff']:.5f}, "
          f"cadence_invariant={stale['cadence_invariant']}")

    # --- Plots ---
    print("\n=== Generating plots ===")

    # A — Silence
    plot_bar_spectrum(results["A_silence"], outdir, n_edges, c_edges)

    # B — Steady sine
    plot_bar_spectrum(results["B_steady_sine"], outdir, n_edges, c_edges)
    dom_native = _dominant_bar(results["B_steady_sine"].native_normed)
    dom_cava = _dominant_bar(results["B_steady_sine"].cava_normed)
    print(f"  B dominant bar: native={dom_native} (~{n_centres[dom_native]:.0f} Hz), "
          f"cava={dom_cava} (~{c_centres[dom_cava]:.0f} Hz)")
    b_metrics = plot_time_response(results["B_steady_sine"], outdir,
                                   dom_native, 1.0, 4.0)
    if b_metrics:
        for backend, m in b_metrics.items():
            print(f"  B {backend}: ss_mean={m.get('ss_mean', 0):.3f}, "
                  f"attack={m.get('attack_10_90_s')}, release={m.get('release_90_10_s')}")

    # C — Amplitude step
    plot_bar_spectrum(results["C_amplitude_step"], outdir, n_edges, c_edges)
    c_metrics = plot_time_response(results["C_amplitude_step"], outdir,
                                   dom_native, 1.0, 7.0,
                                   " (amplitude step, lo→hi→lo)")
    if c_metrics:
        for backend, m in c_metrics.items():
            print(f"  C {backend}: ss_mean={m.get('ss_mean', 0):.3f}")

    # D — Multi-tone
    plot_bar_spectrum(results["D_multi_tone"], outdir, n_edges, c_edges)

    # E — Transients
    plot_bar_spectrum(results["E_transients"], outdir, n_edges, c_edges)
    plot_time_response(results["E_transients"], outdir, dom_native, 0.0,
                       results["E_transients"].signal_duration_s, " (transients)")

    # F — Opposite phase
    plot_opposite_phase(results["F_in_phase"], results["F_opposite_phase"], outdir)
    if results["F_opposite_phase"].native_normed.shape[0]:
        op_native_mean = float(results["F_opposite_phase"].native_normed.mean())
        ip_native_mean = float(results["F_in_phase"].native_normed.mean())
        op_cava_mean = float(results["F_opposite_phase"].cava_normed.mean())
        ip_cava_mean = float(results["F_in_phase"].cava_normed.mean())
        print(f"  F native  in-phase mean={ip_native_mean:.4f}, "
              f"opposite-phase mean={op_native_mean:.4f}, "
              f"ratio={op_native_mean/(ip_native_mean+1e-9):.4f}")
        print(f"  F cava    in-phase mean={ip_cava_mean:.4f}, "
              f"opposite-phase mean={op_cava_mean:.4f}, "
              f"ratio={op_cava_mean/(ip_cava_mean+1e-9):.4f}")

    # --- Silence metrics ---
    if results["A_silence"].native_normed.shape[0]:
        n_floor = float(results["A_silence"].native_normed.mean())
        c_floor = float(results["A_silence"].cava_normed.mean())
        print(f"\n  A silence floor: native={n_floor:.4f}, cava_sim={c_floor:.4f}")

    print(f"\nDone. All outputs in {outdir}")


if __name__ == "__main__":
    main()
