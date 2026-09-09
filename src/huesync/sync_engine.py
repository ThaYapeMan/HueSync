"""The audio analysis and colour engine.

Signal path (one layer at a time):

    FifoReader      — reads cava's raw FIFO output in a background thread
    BandNormaliser  — AGC: normalises each bar against its own rolling average
    OnsetDetector   — spectral flux onset detection with EMA-based threshold
    CavaPipeline    — wraps the three above; produces AudioFeatures each frame
    ColourModeEffect— implements Renderer; maps AudioFeatures to a Scene using
                      one of the two active ColorMode strategies
    SyncEngine      — orchestrates AudioPipeline + Renderer + Output at 30 Hz,
                      with an optional ring-buffer delay on the output

Nothing in this module imports from hue_entertainment; all Hue-specific code
lives in hue_output.py.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from collections import deque

import numpy as np

from .latency import NoLatencyProbe
from .models import Profile
from .pcm_source import WINDOW_SIZE, PcmHpss, PcmSource, PcmStft
from .types import (
    AudioFeatures,
    AudioPipeline,
    Colour,
    LatencyProbe,
    Output,
    Position,
    Scene,
    UniformScene,
)

log = logging.getLogger(__name__)

# Hue Entertainment accepts up to ~50 updates/sec; cava can emit frames much
# faster than that (its rate isn't tied to real playback speed, especially
# with a timer-driven ALSA device like snd-dummy).  Rather than queueing every
# frame — which overwhelms the event loop with scheduled callbacks and starves
# the sender coroutine — the reader thread keeps the *latest* frame in a
# lock-protected slot, and the sender polls it at a fixed interval.  Old
# frames are simply superseded, never queued.
SEND_INTERVAL_S = 1 / 30


# ---------------------------------------------------------------------------
# FifoReader — background thread that tails cava's FIFO
# ---------------------------------------------------------------------------


class FifoReader:
    """Reads fixed-size frames from cava's raw-output FIFO in a background
    thread and keeps only the most recent one available for the sender."""

    def __init__(self, fifo_path: str, frame_size: int) -> None:
        self.fifo_path = fifo_path
        self.frame_size = frame_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_frame: bytes | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_frame

    def _run(self) -> None:
        fd = os.open(self.fifo_path, os.O_RDONLY)
        try:
            buf = b""
            while not self._stop.is_set():
                chunk = os.read(fd, 4096)
                if not chunk:
                    # Writer (cava) closed the pipe — back off briefly and retry.
                    self._stop.wait(0.2)
                    continue
                buf += chunk
                while len(buf) >= self.frame_size:
                    frame, buf = buf[: self.frame_size], buf[self.frame_size :]
                    with self._lock:
                        self._latest_frame = frame
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# BandNormaliser — per-band EMA AGC
# ---------------------------------------------------------------------------


class BandNormaliser:
    """Converts raw cava bar values into per-band exertion scores.

    Instead of comparing bands against each other in absolute terms (which
    lets bass dominate almost every frame because it's always energetic),
    each bar is compared against *its own recent average*:

        exertion(i) = raw[i] / rolling_average(i)

    A band that is always loud stops being interesting; it only lights up
    when it is louder than it usually is.  Mids and treble that were
    previously drowned out now get equal standing whenever they spike above
    their own baselines.

    The result is re-encoded as bytes (0-255) so it can be fed straight
    into the existing frame_to_commands() pipeline unchanged:

        exertion 0.0  → byte   0  (band well below its average)
        exertion 1.0  → byte  85  (band exactly at its average, clip=3.0)
        exertion 3.0  → byte 255  (band at three times its average, clipped)

    Three-layer loudness pipeline — how the settings interact:

    1. HERE (exertion_clip): sets what "maximally loud relative to normal"
       means in relative terms.  Default 3.0 gives headroom so constant-
       level music (exertion ≈ 1×) sits around byte 85 (≈ 33 % output)
       rather than saturating.  Lower values compress dynamic range;
       higher values give more headroom before saturation.

    2. profile.sensitivity (in ColourModeEffect.render()): a multiplier
       applied *after* normalisation.  With normalised input at ≈ 0.33,
       sens=1.0 is roughly one-third brightness steady-state; sens=2.0
       doubles that to two-thirds.  Fine-tune here.

    3. Clip at 1.0 (in ColourModeEffect.render()): safety ceiling just
       before RGB conversion.  Prevents individual channels from exceeding
       full brightness regardless of sensitivity.  Not a musical choice.
    """

    # Time-constant-based asymmetric (attack/release) envelope follower.
    # alpha = 1 - exp(-dt/tau) is recomputed per call so the EMA evolves at the
    # same real-time rate regardless of call frequency (30 Hz cava vs ~100 Hz PCM).
    #
    # Starting values follow PPM-style ballistics; empirical direction still TBD —
    # compare_bars.py can be used to test both fast-attack/slow-release and the
    # inverse before committing to final tau values (see project memory).
    #
    # Convention: alpha multiplies the CHANGE (state += alpha*(input-state)), which
    # corresponds to alpha = 1-exp(-dt/tau).  Do NOT mix with the other common DSP
    # convention (state = alpha*old + (1-alpha)*new, alpha = exp(-dt/tau)).
    DEFAULT_ATTACK_TAU_S: float = 0.005   # 5 ms — near-instant rise at any frame rate
    DEFAULT_RELEASE_TAU_S: float = 0.700  # 0.70 s exponential time constant for decay

    #: Mean raw bar value (0-255) below which the entire output frame is
    #: zeroed.  Prevents background noise from being amplified into wild
    #: colours when the track is paused or very quiet.  The EMA still
    #: updates during silence so the baseline decays naturally.
    DEFAULT_GATE: float = 5.0

    #: Default exertion clip ratio.  See class docstring.
    DEFAULT_EXERTION_CLIP: float = 3.0

    def __init__(
        self,
        attack_tau_s: float = DEFAULT_ATTACK_TAU_S,
        release_tau_s: float = DEFAULT_RELEASE_TAU_S,
        gate: float = DEFAULT_GATE,
        exertion_clip: float = DEFAULT_EXERTION_CLIP,
    ) -> None:
        self.attack_tau_s = attack_tau_s
        self.release_tau_s = release_tau_s
        self.gate = gate
        self.exertion_clip = exertion_clip
        # Lazily initialised on the first frame so frame size need not be
        # known at construction time.
        self._ema: list[float] | None = None

    def update_exertion_clip(self, clip: float) -> None:
        """Update exertion_clip without resetting the EMA.

        Changing the clip only affects how exertion ratios are encoded to bytes;
        the rolling average state remains valid and warmup is not lost.
        """
        self.exertion_clip = clip

    def normalise(self, frame: bytes, dt: float) -> bytes:
        """Return an exertion-normalised copy of *frame* as bytes (0-255).

        *dt* is the elapsed time in seconds since the last call.  alpha is
        computed as 1-exp(-dt/tau) so the time constant is caller-rate-independent
        (CavaPipeline at ~30 Hz and PcmAudioPipeline at ~100 Hz share the same
        real-time behaviour).

        Single branching envelope follower — ONE state variable per band, ONE
        alpha chosen per update (attack when input rises, release when it falls).
        Never apply both alphas sequentially: that cascades two filters and gives
        a composite time constant different from either tau.

        Always updates the EMA, even below the silence gate, so the
        baseline decays during pauses and recovers cleanly on resumption.
        """
        n = len(frame)

        if self._ema is None:
            # Seed the EMA with the first frame so the normaliser is not
            # blind for the first few seconds of a session.
            self._ema = [float(v) for v in frame]

        alpha_rise = 1.0 - math.exp(-dt / self.attack_tau_s)
        alpha_fall = 1.0 - math.exp(-dt / self.release_tau_s)

        new_ema: list[float] = []
        for ema, v in zip(self._ema, frame, strict=True):
            a = alpha_rise if v > ema else alpha_fall
            new_ema.append(ema + a * (v - ema))
        self._ema = new_ema

        # Silence gate: if the mean raw bar is negligible, keep the lights
        # dark rather than amplifying noise into meaningless colour flashes.
        if sum(frame) / n < self.gate:
            return bytes(n)

        result = bytearray(n)
        for i, (v, ema) in enumerate(zip(frame, self._ema, strict=True)):
            # Guard against a zero EMA (e.g. a bar that has been silent for
            # the entire session so far).
            exertion = v / max(ema, 1.0)
            result[i] = int(min(exertion, self.exertion_clip) * (255.0 / self.exertion_clip))
        return bytes(result)


# ---------------------------------------------------------------------------
# OnsetDetector — Dixon (2006) three-condition peak-picking
# ---------------------------------------------------------------------------


class OnsetDetector:
    """Detects musical onsets using the peak-picking algorithm from Dixon (2006).

    Spectral flux is normalised to mean 0, standard deviation 1 via EMA
    statistics, then a candidate frame is declared an onset only when all
    three conditions hold simultaneously:

        1. Local maximum: f(n) >= f(k) for all k in [n-w, n+w]  (w=3)
        2. Above asymmetric mean: f(n) >= mean(f(k), k in [n-m*w, n+w]) + delta
           (m=3, so the window looks 3× further back than forward)
        3. Above decaying threshold: f(n) >= g_alpha(n-1)
           where g_alpha(n) = max(f(n), alpha*g_alpha(n-1) + (1-alpha)*f(n))

    Condition 1 requires looking w frames ahead, so the detector is inherently
    w frames (~100 ms at 30 Hz) behind real time.  This is irrelevant for
    lighting.

    Condition 3 replaces the old fixed cooldown: it suppresses re-triggering
    adaptively — a loud onset raises the bar for longer than a quiet one.

    Source: Simon Dixon, "Onset Detection Revisited", DAFx-06.
    """

    _W: int = 3    # local-max half-window (frames)
    _M: int = 3    # asymmetry multiplier for condition 2
    #: EMA factor for running flux statistics (normalisation).
    _ALPHA_NORM: float = 0.1
    #: Frames to wait before reporting onsets (lets EMA statistics settle).
    _WARMUP_FRAMES: int = 30
    #: Ring-buffer size: m*w past frames + candidate + w future frames.
    _BUF_MAXLEN: int = _M * _W + 1 + _W   # = 13

    def __init__(self, delta: float = 0.1, alpha: float = 0.9) -> None:
        self._delta = delta   # condition 2 margin (in normalised-flux units)
        self._alpha = alpha   # condition 3 decay factor per frame

        self._prev_bars: list[float] | None = None
        self._flux_ema: float = 0.0
        self._flux_var: float = 0.0

        # Ring buffer of normalised flux values.  Candidate to evaluate is
        # always at index _M*_W (= 9) — i.e. _W frames behind the newest.
        self._buf: deque[float] = deque(maxlen=self._BUF_MAXLEN)

        # g_alpha history: maxlen = W+2 so that g_hist[0] at step C+W equals
        # g_alpha(C-1), which is what condition 3 requires.
        self._g_hist: deque[float] = deque(
            [0.0] * (self._W + 2), maxlen=self._W + 2
        )
        self._g: float = 0.0
        self._warmup: int = self._WARMUP_FRAMES

    def process(self, bars: list[float]) -> tuple[bool, float]:
        """Return *(onset, flux_strength)* for the current bar frame.

        *onset* is True on frames where a musical onset is detected.
        *flux_strength* is the raw (unnormalised) spectral flux for this frame.
        """
        if self._prev_bars is None:
            self._prev_bars = list(bars)
            return False, 0.0

        # Spectral flux: sum of positive differences only (rising energy).
        flux = sum(max(0.0, b - p) for b, p in zip(bars, self._prev_bars, strict=True))
        self._prev_bars = list(bars)

        return self._peak_pick(flux)

    def process_odf(self, odf: float) -> tuple[bool, float]:
        """Apply Dixon peak-picking to a pre-computed ODF value.

        Identical to process() but skips spectral flux computation — use when
        the caller has already computed the ODF (e.g. SuperfluxDetector, which
        applies max-filtering before summing).  Strength returned is the raw
        ODF value.
        """
        return self._peak_pick(odf)

    def _peak_pick(self, flux: float) -> tuple[bool, float]:
        """Normalise *flux* and apply the three Dixon peak-picking conditions."""
        # Running mean and variance for normalisation.  Use pre-update mean so
        # the residual is unbiased.
        old_ema = self._flux_ema
        self._flux_ema = old_ema + self._ALPHA_NORM * (flux - old_ema)
        self._flux_var = self._flux_var + self._ALPHA_NORM * (
            (flux - old_ema) ** 2 - self._flux_var
        )
        flux_std = math.sqrt(max(self._flux_var, 0.0))

        # Normalise to mean 0, std 1.
        f_norm = (flux - old_ema) / max(flux_std, 1e-6)

        # Read g_alpha(C-1) before updating, then advance the history.
        g_prev = self._g_hist[0]
        self._g = max(f_norm, self._alpha * self._g + (1.0 - self._alpha) * f_norm)
        self._g_hist.append(self._g)

        self._buf.append(f_norm)

        if self._warmup > 0:
            self._warmup -= 1
            return False, flux

        if len(self._buf) < self._BUF_MAXLEN:
            return False, flux

        buf = list(self._buf)
        ci = self._M * self._W   # candidate index = 9
        f_c = buf[ci]

        # Condition 1: local maximum within ±w.
        w = self._W
        if any(f_c < buf[ci + k] for k in range(-w, w + 1) if k != 0):
            return False, flux

        # Condition 2: above asymmetric local mean + delta (window = entire buf).
        if f_c < sum(buf) / len(buf) + self._delta:
            return False, flux

        # Condition 3: above decaying threshold from previous onset.
        if f_c < g_prev:
            return False, flux

        return True, flux


# ---------------------------------------------------------------------------
# StftOnsetPipeline — 'combined' onset detector on PcmStft magnitude frames
# ---------------------------------------------------------------------------


class StftOnsetPipeline:
    """'Combined' onset detector (Dixon 2006) running on STFT magnitude frames.

    Implements step 3 of docs/HueSync_pcm_tap_spec.md: spectral flux is summed
    across all 1025 FFT bins, then Dixon's three peak-picking conditions are
    applied.  This is a parallel path alongside cava; it does not affect colour.

    Usage::

        pipeline = StftOnsetPipeline(sample_rate=44100)
        results = pipeline.push(mono_float32_samples)
        # results: list of (onset: bool, strength: float) per STFT frame
    """

    def __init__(
        self, sample_rate: int, delta: float = 0.1, alpha: float = 0.9
    ) -> None:
        self._stft = PcmStft(sample_rate)
        self._onset = OnsetDetector(delta=delta, alpha=alpha)

    @property
    def hop(self) -> int:
        return self._stft.hop

    def push(self, samples: np.ndarray) -> list[tuple[bool, float]]:
        """Process PCM samples; return *(onset, strength)* per STFT frame."""
        return [self._onset.process(frame.tolist()) for frame in self._stft.push(samples)]


# ---------------------------------------------------------------------------
# SuperfluxDetector — Böck & Widmer (2013) SuperFlux on STFT magnitude frames
# ---------------------------------------------------------------------------


class SuperfluxDetector:
    """SuperFlux onset detection (Böck & Widmer, 2013) with Dixon peak-picking.

    Spectral flux is computed with a maximum-filter applied over mu neighboring
    bins of the previous frame before taking the half-wave rectified difference.
    This suppresses vibrato and pitch-shifting artefacts that trigger plain
    spectral flux with false positives.

    Algorithm (from the paper):
        X_max(n, k) = max( X(n, k-mu) … X(n, k+mu) )
        SuperFlux(n) = Σ_k  H( X(n, k) − X_max(n-lag, k) )
        H = half-wave rectifier: H(x) = max(0, x)

    Böck uses mu=3 bins on a mel filterbank (~84 bands) and lag=2 frames.
    On raw FFT bins (PcmStft: 1025 bins at 21.5 Hz/bin for 44100 Hz), mu=3
    covers only ±65 Hz — less relative effect than on mel because the bin
    resolution is much finer.  Make mu configurable so users can increase it
    if more vibrato suppression is needed.

    Dixon peak-picking is applied to the SuperFlux ODF via OnsetDetector.process_odf()
    so the three conditions (local max, asymmetric mean + delta, g_alpha) are
    identical to the combined and multiband methods.

    Source: Böck & Widmer, "Maximum Filter Vibrato Suppression for Onset
    Detection", DAFx-13.  The algorithm itself is patent-free; this is an
    independent implementation from the paper.
    """

    def __init__(
        self,
        mu: int = 3,
        lag: int = 2,
        delta: float = 0.1,
        alpha: float = 0.9,
    ) -> None:
        self._mu = mu
        self._lag = lag
        self._picker = OnsetDetector(delta=delta, alpha=alpha)
        # Ring buffer of the most recent lag+1 magnitude frames.  The oldest
        # frame in this buffer is exactly lag frames behind the current one.
        self._frame_history: deque[np.ndarray] = deque(maxlen=lag + 1)

    def process(self, frame: np.ndarray) -> tuple[bool, float]:
        """Compute SuperFlux ODF for *frame* and apply Dixon peak-picking."""
        self._frame_history.append(frame)

        if len(self._frame_history) <= self._lag:
            # Not enough history for the lag yet; feed zero to the picker.
            return self._picker.process_odf(0.0)

        # Frame from exactly lag steps ago.
        prev_frame = self._frame_history[0]  # oldest in the fixed-size deque

        # Maximum-filter the lagged frame over mu neighboring bins.
        n_bins = len(prev_frame)
        mu = self._mu
        x_max = np.empty(n_bins, dtype=np.float32)
        for k in range(n_bins):
            lo = max(0, k - mu)
            hi = min(n_bins, k + mu + 1)
            x_max[k] = prev_frame[lo:hi].max()

        # Half-wave rectified spectral difference → SuperFlux scalar.
        superflux = float(np.sum(np.maximum(0.0, frame - x_max)))

        return self._picker.process_odf(superflux)


class SuperfluxStftPipeline:
    """SuperFlux onset detector on 100 Hz STFT data.

    Wraps PcmStft + SuperfluxDetector.  push() returns a list of
    (onset, superflux_strength) pairs — one per STFT frame.

    Usage::

        pipeline = SuperfluxStftPipeline(sample_rate=44100)
        results = pipeline.push(mono_float32_samples)
        # results: list of (onset: bool, strength: float) per STFT frame
    """

    def __init__(
        self,
        sample_rate: int,
        mu: int = 3,
        lag: int = 2,
        delta: float = 0.1,
        alpha: float = 0.9,
    ) -> None:
        self._stft = PcmStft(sample_rate)
        self._detector = SuperfluxDetector(mu=mu, lag=lag, delta=delta, alpha=alpha)

    @property
    def hop(self) -> int:
        return self._stft.hop

    def push(self, samples: np.ndarray) -> list[tuple[bool, float]]:
        """Process PCM samples; return *(onset, strength)* per STFT frame."""
        return [self._detector.process(frame) for frame in self._stft.push(samples)]


# ---------------------------------------------------------------------------
# MultibandOnsetDetector — per-band Dixon onset on STFT magnitude frames
# ---------------------------------------------------------------------------


class MultibandOnsetDetector:
    """Three OnsetDetectors applied to bass/mid/treble slices of a magnitude frame.

    Band boundaries are defined by bass_hz and mid_hz (Hz), converted to FFT
    bin indices using bin = round(hz * WINDOW_SIZE / sample_rate).  The lower
    cutoff is always bin 0; the upper cutoff is the last bin (n_bins - 1).

    Each band gets independent OnsetDetector state so a loud bass transient
    does not suppress the mid or treble detector's decaying threshold.
    """

    def __init__(
        self,
        sample_rate: int,
        bass_hz: int,
        mid_hz: int,
        delta: float,
        alpha: float,
    ) -> None:
        self._bass_hi = max(1, round(bass_hz * WINDOW_SIZE / sample_rate))
        self._mid_hi = max(self._bass_hi + 1, round(mid_hz * WINDOW_SIZE / sample_rate))
        self._bass_det = OnsetDetector(delta=delta, alpha=alpha)
        self._mid_det = OnsetDetector(delta=delta, alpha=alpha)
        self._treble_det = OnsetDetector(delta=delta, alpha=alpha)

    def process(
        self, frame: np.ndarray
    ) -> tuple[tuple[bool, float], tuple[bool, float], tuple[bool, float]]:
        """Return (bass_result, mid_result, treble_result) where each is (onset, strength)."""
        bass_r = self._bass_det.process(frame[: self._bass_hi].tolist())
        mid_r = self._mid_det.process(frame[self._bass_hi : self._mid_hi].tolist())
        treble_r = self._treble_det.process(frame[self._mid_hi :].tolist())
        return bass_r, mid_r, treble_r


class MultibandStftPipeline:
    """Multiband onset detector on 100 Hz STFT data.

    Applies separate OnsetDetector instances to bass, mid, and treble slices
    of each PcmStft magnitude frame.  push() returns per-frame tuples of three
    (onset, strength) pairs — one per band.

    Usage::

        pipeline = MultibandStftPipeline(sample_rate=44100, bass_hz=250, mid_hz=2000)
        for frame_result in pipeline.push(samples):
            (b_on, b_str), (m_on, m_str), (t_on, t_str) = frame_result
    """

    def __init__(
        self,
        sample_rate: int,
        bass_hz: int,
        mid_hz: int,
        delta: float = 0.1,
        alpha: float = 0.9,
    ) -> None:
        self._stft = PcmStft(sample_rate)
        self._detector = MultibandOnsetDetector(sample_rate, bass_hz, mid_hz, delta, alpha)

    @property
    def hop(self) -> int:
        return self._stft.hop

    def push(
        self, samples: np.ndarray
    ) -> list[tuple[tuple[bool, float], tuple[bool, float], tuple[bool, float]]]:
        """Process PCM samples; return per-frame (bass, mid, treble) onset results."""
        return [self._detector.process(frame) for frame in self._stft.push(samples)]


# ---------------------------------------------------------------------------
# Helpers shared by CavaPipeline and ColourModeEffect
# ---------------------------------------------------------------------------


def _band_average(frame: bytes, start: float, end: float) -> float:
    """Average of bars in a fractional slice of *frame* (bytes, 0-255 → 0.0-1.0).

    Kept for internal use and for the unit tests that exercise it directly.
    New code should prefer _slice_avg() which works on the float bar lists
    produced by CavaPipeline.
    """
    n = len(frame)
    lo, hi = int(start * n), max(int(end * n), int(start * n) + 1)
    hi = min(hi, n)
    band = frame[lo:hi]
    return (sum(band) / len(band)) / 255.0 if band else 0.0


def _slice_avg(bars: list[float], start: float, end: float) -> float:
    """Average of a fractional slice of a float bar list (values already 0.0-1.0)."""
    n = len(bars)
    lo, hi = int(start * n), max(int(end * n), int(start * n) + 1)
    hi = min(hi, n)
    segment = bars[lo:hi]
    return sum(segment) / len(segment) if segment else 0.0


def _hz_to_frac(hz: float, lower: float, upper: float) -> float:
    """Bar-fraction for *hz* given cava's log-spaced range [lower, upper].

    Matches the logFraction() formula used in the frontend SpectrumBars component.
    Returns 0.0 when hz <= lower and 1.0 when hz >= upper.
    """
    log_min = math.log10(max(lower, 1.0))
    log_max = math.log10(max(upper, lower + 1.0))
    return max(0.0, min(1.0, (math.log10(max(hz, 1.0)) - log_min) / (log_max - log_min)))


def _band_avg(bars: list[float], lo: int, hi: int) -> float:
    """Average of bars[lo:hi]; 0.0 if the band has no bars."""
    segment = bars[lo:hi]
    return sum(segment) / len(segment) if segment else 0.0


# ---------------------------------------------------------------------------
# CavaPipeline — implements the AudioPipeline protocol
# ---------------------------------------------------------------------------


class CavaPipeline:
    """Reads cava bar frames from a FIFO, normalises them, and produces
    AudioFeatures including onset detection.

    Wraps FifoReader (raw bytes from FIFO), BandNormaliser (AGC), and
    OnsetDetector (spectral flux).  The AudioPipeline protocol is satisfied by
    start(), stop(), and latest().

    AudioFeatures produced here:
    - bars:           normalised bar values (0.0-1.0)
    - bass/mid/full:  cumulative mel-like band slices (see AudioFeatures docs)
    - centroid:       spectral centroid normalised 0.0-1.0
    - onset:          True on frames where a musical onset is detected
    - onset_strength: raw spectral flux value for that frame
    - beat/tempo:     not yet computed; always None
    """

    def __init__(
        self,
        fifo_path: str,
        bars: int,
        onset_delta: float = 0.1,
        onset_alpha: float = 0.9,
        exertion_clip: float = BandNormaliser.DEFAULT_EXERTION_CLIP,
    ) -> None:
        self._reader = FifoReader(fifo_path, frame_size=bars)
        self._normaliser = BandNormaliser(exertion_clip=exertion_clip)
        self._onset = OnsetDetector(delta=onset_delta, alpha=onset_alpha)
        self._last_normalise_t: float | None = None

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    @property
    def normaliser(self) -> BandNormaliser:
        return self._normaliser

    def latest(self) -> AudioFeatures | None:
        frame = self._reader.latest_frame()
        if frame is None:
            return None
        now = time.monotonic()
        dt = (now - self._last_normalise_t) if self._last_normalise_t is not None else 1.0 / 30
        self._last_normalise_t = now
        normed = self._normaliser.normalise(frame, dt)
        n = len(normed)
        bars = [v / 255.0 for v in normed]
        total = sum(bars)
        centroid = (
            sum(i * v for i, v in enumerate(bars)) / total / n if total > 1e-9 else 0.0
        )
        onset, onset_strength = self._onset.process(bars)
        return AudioFeatures(
            bars=bars,
            # Cumulative slices: each covers its range plus everything below it.
            # Proportions are mel-like given cava's log-spaced bars at 50-10000 Hz.
            bass=_slice_avg(bars, 0.0, 0.20),
            mid=_slice_avg(bars, 0.0, 0.55),
            full=_slice_avg(bars, 0.0, 1.0),
            centroid=centroid,
            onset=onset,
            onset_strength=onset_strength,
        )


# ---------------------------------------------------------------------------
# PcmAudioPipeline — source-agnostic AudioPipeline from a PcmSource
# ---------------------------------------------------------------------------


class PcmAudioPipeline:
    """AudioPipeline that derives AudioFeatures from any PcmSource.

    Produces AudioFeatures structurally identical to CavaPipeline so all
    effects (including spectrum_rgb and mono_pulse) work without modification.

    Source-agnostic: operates exclusively on the PcmSource Protocol.
    AirPlayPipeSource, SqueezeliteShmSource, and any future RoonPcmSource
    are interchangeable from this pipeline's perspective.  This class itself
    never changes when a new audio source is added — only the PcmSource
    adapter does.  See CLAUDE.md "Future player/source types" for the full
    extension pattern.

    Bar computation:
        STFT magnitude bins (0–Nyquist) are averaged into N log-spaced bars
        covering [lower_cutoff_freq, higher_cutoff_freq] Hz, matching the
        frequency layout that cava uses.  BandNormaliser then applies the same
        EMA-based AGC as CavaPipeline.

    Onset detection:
        All three methods are supported (combined / multiband / superflux),
        each running its own internal STFT pipeline (same hop as the bar
        STFT so frame counts stay aligned).
    """

    _POLL_S: float = 0.005  # seconds to wait between polls when pipe is empty

    def __init__(
        self,
        source: PcmSource,
        bars: int,
        lower_cutoff_freq: int,
        higher_cutoff_freq: int,
        onset_method: str,
        onset_delta: float,
        onset_alpha: float,
        superflux_mu: int,
        superflux_lag: int,
        bass_hz: int,
        mid_hz: int,
        exertion_clip: float = BandNormaliser.DEFAULT_EXERTION_CLIP,
    ) -> None:
        self._source = source
        self._n_bars = bars
        self._lower_hz = float(lower_cutoff_freq)
        self._upper_hz = float(higher_cutoff_freq)
        self._onset_method = onset_method
        self._onset_delta = onset_delta
        self._onset_alpha = onset_alpha
        self._superflux_mu = superflux_mu
        self._superflux_lag = superflux_lag
        self._bass_hz = bass_hz
        self._mid_hz = mid_hz
        self._normaliser = BandNormaliser(exertion_clip=exertion_clip)
        # Pipelines are initialised lazily on first sample (sample_rate needed).
        self._bar_stft: PcmStft | None = None
        self._normalise_dt: float | None = None  # set in _init_pipelines(); hop/sample_rate
        self._onset_pipeline: (
            StftOnsetPipeline | MultibandStftPipeline | SuperfluxStftPipeline | None
        ) = None
        self._sample_rate: int | None = None
        self._latest: AudioFeatures | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def normaliser(self) -> BandNormaliser:
        return self._normaliser

    def _init_pipelines(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._bar_stft = PcmStft(sample_rate)
        # dt per STFT frame = hop / sample_rate (≈ 10 ms at 44.1 kHz).
        self._normalise_dt = self._bar_stft.hop / sample_rate
        if self._onset_method == "multiband":
            self._onset_pipeline = MultibandStftPipeline(
                sample_rate,
                bass_hz=self._bass_hz,
                mid_hz=self._mid_hz,
                delta=self._onset_delta,
                alpha=self._onset_alpha,
            )
        elif self._onset_method == "superflux":
            self._onset_pipeline = SuperfluxStftPipeline(
                sample_rate,
                mu=self._superflux_mu,
                lag=self._superflux_lag,
                delta=self._onset_delta,
                alpha=self._onset_alpha,
            )
        else:
            self._onset_pipeline = StftOnsetPipeline(
                sample_rate,
                delta=self._onset_delta,
                alpha=self._onset_alpha,
            )

    def _mag_to_bar_bytes(self, mag: np.ndarray) -> bytes:
        """Average STFT magnitude bins into N log-spaced bars → bytes 0-255."""
        assert self._sample_rate is not None
        sr = self._sample_rate
        n_bins = len(mag)
        log_lo = math.log10(max(self._lower_hz, 1.0))
        log_hi = math.log10(max(self._upper_hz, self._lower_hz + 1.0))
        result = bytearray(self._n_bars)
        for i in range(self._n_bars):
            f_lo = 10.0 ** (log_lo + i / self._n_bars * (log_hi - log_lo))
            f_hi = 10.0 ** (log_lo + (i + 1) / self._n_bars * (log_hi - log_lo))
            bin_lo = max(0, round(f_lo * WINDOW_SIZE / sr))
            bin_hi = min(n_bins, max(bin_lo + 1, round(f_hi * WINDOW_SIZE / sr)))
            val = float(np.mean(mag[bin_lo:bin_hi])) if bin_hi > bin_lo else 0.0
            result[i] = min(255, int(val))
        return bytes(result)

    def _run(self) -> None:
        while not self._stop.is_set():
            samples = self._source.read_new()
            if len(samples) == 0:
                # When the source goes inactive (AirPlay disconnected / LMS
                # paused), clear stale features so the engine sees None and
                # the lights go dark instead of showing the last music frame.
                if not self._source.running:
                    with self._lock:
                        self._latest = None
                self._stop.wait(self._POLL_S)
                continue

            if self._bar_stft is None:
                self._init_pipelines(self._source.sample_rate)

            assert self._bar_stft is not None
            assert self._onset_pipeline is not None

            bar_frames = self._bar_stft.push(samples)
            onset_frames = self._onset_pipeline.push(samples)

            # Both STFTs share the same hop, so frame counts are always equal.
            for bar_frame, onset_result in zip(bar_frames, onset_frames, strict=False):
                bar_bytes = self._mag_to_bar_bytes(bar_frame)
                # normalise() runs here in _run() (~100 Hz), not in latest() (30 Hz).
                # Moving it to latest() was attempted (Optie B, commit e0c425d) and
                # caused two bugs:
                # 1. Freeze: latest() kept calling normalise() with the same stale
                #    _latest bytes when the source went quiet — the EMA converged to
                #    a constant, producing 17 s of identical output.
                # 2. Regression: latest() reads only whichever frame _run() last
                #    wrote; intermediate STFT frames (including transient peaks)
                #    never reached normalise(). Correlation dropped 0.60 → 0.50 and
                #    the PCM/cava amplitude ratio dropped 0.73 → 0.50.
                # dt = hop/sample_rate so the time constant is rate-independent (P0 fix).
                normed = self._normaliser.normalise(bar_bytes, self._normalise_dt or 0.01)
                bars = [v / 255.0 for v in normed]
                total = sum(bars)
                n = len(bars)
                centroid = (
                    sum(idx * v for idx, v in enumerate(bars)) / total / n
                    if total > 1e-9
                    else 0.0
                )

                onset = False
                onset_strength = 0.0
                onset_bass = onset_mid = onset_treble = False
                onset_bass_str = onset_mid_str = onset_treble_str = 0.0

                if self._onset_method == "multiband":
                    (b_on, b_str), (m_on, m_str), (t_on, t_str) = onset_result
                    onset_bass, onset_bass_str = b_on, b_str
                    onset_mid, onset_mid_str = m_on, m_str
                    onset_treble, onset_treble_str = t_on, t_str
                    onset = b_on or m_on or t_on
                    onset_strength = max(b_str, m_str, t_str)
                else:
                    onset, onset_strength = onset_result

                # Silence gate: onset detector runs on raw STFT magnitudes, so its
                # flux variance can collapse to ~0 during silence and amplify the
                # next noise spike into a false onset. Suppress onset when bars are silent.
                if total < 1e-9:
                    onset = onset_bass = onset_mid = onset_treble = False
                    onset_strength = onset_bass_str = onset_mid_str = onset_treble_str = 0.0

                with self._lock:
                    self._latest = AudioFeatures(
                        bars=bars,
                        bass=_slice_avg(bars, 0.0, 0.20),
                        mid=_slice_avg(bars, 0.0, 0.55),
                        full=_slice_avg(bars, 0.0, 1.0),
                        centroid=centroid,
                        onset=onset,
                        onset_strength=onset_strength,
                        onset_bass=onset_bass,
                        onset_mid=onset_mid,
                        onset_treble=onset_treble,
                        onset_bass_strength=onset_bass_str,
                        onset_mid_strength=onset_mid_str,
                        onset_treble_strength=onset_treble_str,
                    )

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def latest(self) -> AudioFeatures | None:
        with self._lock:
            return self._latest


# ---------------------------------------------------------------------------
# HSV helper
# ---------------------------------------------------------------------------


def _hsv_to_colour(h: float, s: float, v: float) -> Colour:
    """Convert HSV (each in 0.0–1.0) to a Colour."""
    if s == 0.0:
        return Colour(v, v, v)
    h6 = h * 6.0
    i = int(h6) % 6
    f = h6 - int(h6)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t_v = v * (1.0 - s * (1.0 - f))
    r, g, b = ((v, t_v, p), (q, v, p), (p, v, t_v), (p, q, v), (t_v, p, v), (v, p, q))[i]
    return Colour(r, g, b)


# ---------------------------------------------------------------------------
# _EffectRenderer protocol and individual renderer implementations
# ---------------------------------------------------------------------------


class _EffectRenderer:
    """Internal protocol: render one frame to a Scene."""

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:
        raise NotImplementedError


class _SpectrumRgbRenderer(_EffectRenderer):
    """Bass→R, mid→G, treble→B with optional onset flash."""

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        sens = profile.sensitivity
        floor = profile.brightness_floor
        bars = features.bars
        n = len(bars)
        lo = profile.lower_cutoff_freq
        hi = profile.higher_cutoff_freq
        bass_frac = _hz_to_frac(profile.bass_hz, lo, hi)
        mid_frac = _hz_to_frac(profile.mid_hz, lo, hi)
        bass_hi = int(bass_frac * n)
        mid_hi = int(mid_frac * n)
        r = min(_band_avg(bars, 0, bass_hi) * sens, 1.0)
        g = min(_band_avg(bars, bass_hi, mid_hi) * sens, 1.0)
        b = min(_band_avg(bars, mid_hi, n) * sens, 1.0)
        if features.onset:
            fi = profile.onset_flash_intensity
            if fi > 0.0:
                r = r + fi * (1.0 - r)
                g = g + fi * (1.0 - g)
                b = b + fi * (1.0 - b)
        # spectrum_rgb does not apply a brightness floor per-channel
        # (silent channels are intentionally dark)
        del floor  # unused for spectrum_rgb
        return UniformScene(Colour(r=r, g=g, b=b))


class _MonoPulseRenderer(_EffectRenderer):
    """Single colour; brightness follows overall energy."""

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        sens = profile.sensitivity
        floor = profile.brightness_floor
        overall = min(_slice_avg(features.bars, 0.0, 1.0) * sens, 1.0)
        brightness = max(overall, floor)
        if features.onset:
            fi = profile.onset_flash_intensity
            if fi > 0.0:
                brightness = brightness + fi * (1.0 - brightness)
        return UniformScene(Colour(brightness, brightness, brightness))


class _PulsesRenderer(_EffectRenderer):
    """Onset-driven brightness pulse with spectrum-derived colour.

    Snapshots the spectrum hue at each onset; applies an exponential attack
    toward 1.0 and decays exponentially between onsets.  The hue snapshot is
    only updated when bars carry real signal, preventing the near-silence white
    wash caused by normalising against a near-zero peak.
    """

    def __init__(self) -> None:
        self._envelope: float = 0.0
        # Stored normalised colour direction (max channel = 1.0).
        # Irrelevant before the first onset — envelope is 0.0 until then.
        self._r: float = 1.0
        self._g: float = 1.0
        self._b: float = 1.0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        if features.onset:
            bars = features.bars
            n = len(bars)
            lo, hi = profile.lower_cutoff_freq, profile.higher_cutoff_freq
            bass_hi = int(_hz_to_frac(profile.bass_hz, lo, hi) * n)
            mid_hi = int(_hz_to_frac(profile.mid_hz, lo, hi) * n)
            r_raw = _band_avg(bars, 0, bass_hi) * profile.sensitivity
            g_raw = _band_avg(bars, bass_hi, mid_hi) * profile.sensitivity
            b_raw = _band_avg(bars, mid_hi, n) * profile.sensitivity
            peak = max(r_raw, g_raw, b_raw)
            if peak > 1e-6:
                self._r = r_raw / peak
                self._g = g_raw / peak
                self._b = b_raw / peak
            # HPSS: scale pulse intensity by percussive content so drum hits
            # are brighter than harmonic note onsets.
            if features.hpss_active:
                target = 0.3 + 0.7 * features.percussive_energy
                self._envelope += 0.7 * (target - self._envelope)
            else:
                self._envelope += 0.7 * (1.0 - self._envelope)
        else:
            self._envelope *= (1.0 - min(profile.effect_decay, 0.99))

        brightness = max(self._envelope, profile.brightness_floor)
        return UniformScene(Colour(
            min(self._r * brightness, 1.0),
            min(self._g * brightness, 1.0),
            min(self._b * brightness, 1.0),
        ))


class _FlashesRenderer(_EffectRenderer):
    """Hard flash on onset with cooldown; very dark between flashes."""

    def __init__(self) -> None:
        self._envelope: float = 0.0
        self._cooldown: int = 0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        if features.onset and self._cooldown == 0:
            # HPSS: flash envelope proportional to percussive content; pure
            # drums flash to full white, harmonic-only onsets flash at 30%.
            if features.hpss_active:
                self._envelope = 0.3 + 0.7 * features.percussive_energy
            else:
                self._envelope = 1.0
            self._cooldown = 4
        self._envelope *= (1.0 - min(profile.effect_decay * 2.0, 0.99))
        self._cooldown = max(0, self._cooldown - 1)
        brightness = max(self._envelope, profile.brightness_floor * 0.3)
        return UniformScene(Colour(brightness, brightness, brightness))


class _SplotchScene:
    """Spatial scene: lights vary by x-position based on onset seed."""

    __slots__ = ("_envelope", "_seed", "_floor", "_sens")

    def __init__(self, envelope: float, seed: int, floor: float, sens: float) -> None:
        self._envelope = envelope
        self._seed = seed
        self._floor = floor
        self._sens = sens

    def color_at(self, position: Position, t: float) -> Colour:  # noqa: ARG002
        v = math.sin(position.x * 3.7 + self._seed * 1.1)
        brightness = (self._envelope * self._sens) if v > 0 else self._floor
        brightness = max(brightness, self._floor)
        return Colour(brightness, brightness, brightness)


class _SplotchesRenderer(_EffectRenderer):
    """Random splotch of light flares on onset."""

    def __init__(self) -> None:
        self._envelope: float = 0.0
        self._seed: int = 0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        if features.onset:
            self._seed += 1
            self._envelope = 1.0
        else:
            self._envelope *= (1.0 - min(profile.effect_decay * 0.5, 0.99))
        return _SplotchScene(
            self._envelope, self._seed, profile.brightness_floor, profile.sensitivity
        )


class _Particle:
    """One particle in a fireworks burst: moves at constant velocity from its origin."""

    __slots__ = ("ox", "vel", "birth_t", "r", "g", "b")

    def __init__(
        self, ox: float, vel: float, birth_t: float, r: float, g: float, b: float
    ) -> None:
        self.ox = ox
        self.vel = vel
        self.birth_t = birth_t
        self.r = r
        self.g = g
        self.b = b


class _FireworkScene:
    """Spatial scene: uniform onset flash + particle trails radiating from origin.

    Two additive layers:
    1. Uniform flash — position-independent; decays quickly so even a single
       light shows a dramatic burst at onset regardless of where particles start.
    2. Spatial trails — four particles travel from origin at different speeds,
       illuminating lights they pass close to as they fan out.

    With many lights the spatial spread is visible; with one light the flash
    ensures a strong impact.  Returns black before any onset.
    """

    __slots__ = ("_particles", "_decay_rate", "_flash_rate", "_birth_t", "_r", "_g", "_b")

    def __init__(
        self,
        particles: list[_Particle],
        decay_rate: float,
        flash_rate: float,
        birth_t: float,
        r: float,
        g: float,
        b: float,
    ) -> None:
        self._particles = particles
        self._decay_rate = decay_rate
        self._flash_rate = flash_rate
        self._birth_t = birth_t
        self._r = r
        self._g = g
        self._b = b

    def color_at(self, position: Position, t: float) -> Colour:
        if not self._particles:
            return Colour.BLACK
        r = g = b = 0.0
        # Uniform flash: same brightness for every light at onset.
        flash_age = t - self._birth_t
        if 0.0 <= flash_age <= 5.0:
            flash = math.exp(-flash_age * self._flash_rate)
            r += flash * self._r
            g += flash * self._g
            b += flash * self._b
        # Spatial trail: each particle illuminates lights near its current position.
        for p in self._particles:
            age = t - p.birth_t
            if age < 0.0 or age > 5.0:
                continue
            x_now = p.ox + p.vel * age
            dist = abs(position.x - x_now)
            proximity = max(0.0, 1.0 - dist * 4.0)
            envelope = math.exp(-age * self._decay_rate)
            burst = proximity * envelope
            r += burst * p.r
            g += burst * p.g
            b += burst * p.b
        return Colour(min(r, 1.0), min(g, 1.0), min(b, 1.0))


class _FireworksRenderer(_EffectRenderer):
    """Four particles burst from a pseudo-random origin on each onset.

    Particles fan out at four distinct speeds so they sweep across all
    lights sequentially rather than all at once.  A position-independent
    flash component fires at every onset so single-light setups also show
    a dramatic burst.  The burst colour is snapshotted from the spectrum at
    the onset moment; the snapshot is only updated when bars carry real signal.
    effect_speed scales particle velocities; effect_decay controls fade duration.
    """

    def __init__(self) -> None:
        self._particles: list[_Particle] = []
        self._birth_t: float = -999.0
        self._r: float = 1.0
        self._g: float = 1.0
        self._b: float = 1.0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:
        if features.onset:
            origin_x = math.sin(t * 127.0) * 0.9
            bars = features.bars
            n = len(bars)
            lo, hi = profile.lower_cutoff_freq, profile.higher_cutoff_freq
            bass_hi = int(_hz_to_frac(profile.bass_hz, lo, hi) * n)
            mid_hi = int(_hz_to_frac(profile.mid_hz, lo, hi) * n)
            r_raw = _band_avg(bars, 0, bass_hi) * profile.sensitivity
            g_raw = _band_avg(bars, bass_hi, mid_hi) * profile.sensitivity
            b_raw = _band_avg(bars, mid_hi, n) * profile.sensitivity
            peak = max(r_raw, g_raw, b_raw)
            if peak > 1e-6:
                self._r = r_raw / peak
                self._g = g_raw / peak
                self._b = b_raw / peak
            # HPSS: scale particle speed by percussive content — drum hits
            # launch fast wide bursts; harmonic onsets launch slower short bursts.
            perc_scale = (0.4 + 0.6 * features.percussive_energy) if features.hpss_active else 1.0
            speed = profile.effect_speed * perc_scale
            self._particles = [
                _Particle(ox=origin_x, vel=v * speed, birth_t=t,
                          r=self._r, g=self._g, b=self._b)
                for v in (-1.2, -0.4, 0.4, 1.2)
            ]
            self._birth_t = t
        # Trail: effect_decay=0.3 → decay_rate=0.9 → half-life ≈ 770 ms.
        # Flash: 2× faster, so the initial BOOM fades while particle trails are still active.
        decay_rate = profile.effect_decay * 3.0
        flash_rate = decay_rate * 2.0
        return _FireworkScene(
            list(self._particles), decay_rate, flash_rate,
            self._birth_t, self._r, self._g, self._b,
        )


class _SwirlScene:
    """Spatial scene: rotating colour gradient."""

    __slots__ = ("_speed", "_brightness")

    def __init__(self, speed: float, brightness: float) -> None:
        self._speed = speed
        self._brightness = brightness

    def color_at(self, position: Position, t: float) -> Colour:
        hue = (position.x * 0.4 + t * self._speed * 0.05) % 1.0
        return _hsv_to_colour(hue, 0.8, self._brightness)


class _SwirlRenderer(_EffectRenderer):
    """Rotating colour gradient across position."""

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        # HPSS: use harmonic-weighted energy for smoother brightness — swirl
        # stays calm during drum hits (percussive share suppresses the full energy).
        effective = (
            features.harmonic_energy * features.full if features.hpss_active else features.full
        )
        brightness = max(effective * profile.sensitivity, profile.brightness_floor)
        return _SwirlScene(profile.effect_speed, brightness)


class _WaveScene:
    """Spatial scene: colour wave across positions."""

    __slots__ = ("_hue", "_brightness", "_speed", "_floor")

    def __init__(self, hue: float, brightness: float, speed: float, floor: float) -> None:
        self._hue = hue
        self._brightness = brightness
        self._speed = speed
        self._floor = floor

    def color_at(self, position: Position, t: float) -> Colour:
        phase = position.x * 2.0 - t * self._speed * 0.15
        wave_factor = (math.sin(phase * math.pi) + 1.0) / 2.0
        actual_brightness = self._floor + (self._brightness - self._floor) * wave_factor
        return _hsv_to_colour(self._hue, 0.7, actual_brightness)


class _WaveRenderer(_EffectRenderer):
    """Colour wave across positions, hue drifts with spectral centroid."""

    def __init__(self) -> None:
        self._hue: float = 0.0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        self._hue = self._hue * 0.98 + features.centroid * 0.7 * 0.02
        # HPSS: harmonic-weighted energy gives a smoother brightness signal.
        effective = (
            features.harmonic_energy * features.full if features.hpss_active else features.full
        )
        brightness = max(effective * profile.sensitivity, profile.brightness_floor)
        return _WaveScene(self._hue, brightness, profile.effect_speed, profile.brightness_floor)


class _SolidRenderer(_EffectRenderer):
    """Steady colour that drifts slowly with spectral centroid."""

    def __init__(self) -> None:
        self._hue: float = 0.0

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        self._hue = self._hue * 0.99 + features.centroid * 0.7 * 0.01
        # HPSS: harmonic-weighted energy keeps solid colour calm during drum hits.
        effective = (
            features.harmonic_energy * features.full if features.hpss_active else features.full
        )
        brightness = max(effective * profile.sensitivity, profile.brightness_floor)
        return UniformScene(_hsv_to_colour(self._hue, 0.7, brightness))


class _NoneRenderer(_EffectRenderer):
    """Layer off — always black."""

    def render(self, profile: Profile, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        return UniformScene(Colour.BLACK)


def _make_renderer(effect: str) -> _EffectRenderer:
    """Factory: map an effect ID string to an _EffectRenderer instance."""
    match effect:
        case "spectrum_rgb":
            return _SpectrumRgbRenderer()
        case "mono_pulse":
            return _MonoPulseRenderer()
        case "pulses":
            return _PulsesRenderer()
        case "flashes":
            return _FlashesRenderer()
        case "splotches":
            return _SplotchesRenderer()
        case "fireworks":
            return _FireworksRenderer()
        case "swirl":
            return _SwirlRenderer()
        case "wave":
            return _WaveRenderer()
        case "solid":
            return _SolidRenderer()
        case "none":
            return _NoneRenderer()
        case _:
            log.warning("Unknown effect %r; falling back to spectrum_rgb", effect)
            return _SpectrumRgbRenderer()


# ---------------------------------------------------------------------------
# ColourModeEffect — implements the Renderer protocol
# ---------------------------------------------------------------------------


class ColourModeEffect:
    """Dispatches to one of the _EffectRenderer implementations based on profile.effect_type.

    Clipping: each band value is multiplied by profile.sensitivity and clipped
    to 1.0.  This is the *second* ceiling in the pipeline (the first is
    BandNormaliser's exertion clip).  With normalised input, sens ≈ 1.0 keeps
    steady-state music at roughly half-brightness with brief peaks at full;
    raising sensitivity above ~2.0 pushes the steady state into saturation.
    See BandNormaliser for the full picture.
    """

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self._renderer = _make_renderer(profile.effect_type)

    def render(self, features: AudioFeatures, t: float) -> Scene:
        return self._renderer.render(self.profile, features, t)


# ---------------------------------------------------------------------------
# LerpScene, _smoothstep, LayerMixer — two-layer crossfade
# ---------------------------------------------------------------------------


class LerpScene:
    """Linearly interpolates between two Scenes per light position."""

    __slots__ = ("_a", "_b", "_t")

    def __init__(self, a: Scene, b: Scene, t: float) -> None:
        self._a = a
        self._b = b
        self._t = t

    def color_at(self, position: Position, t: float) -> Colour:
        ca = self._a.color_at(position, t)
        cb = self._b.color_at(position, t)
        return ca.lerp(cb, self._t)


def _smoothstep(x: float, lo: float, hi: float) -> float:
    """Clamp x into [lo, hi], normalise, then apply cubic smoothstep."""
    t = max(0.0, min(1.0, (x - lo) / max(hi - lo, 1e-9)))
    return t * t * (3.0 - 2.0 * t)


class LayerMixer:
    """Crossfades a Mellow and an Active ColourModeEffect by energy.

    mix = smoothstep(features.full, low_threshold, high_threshold),
    EMA-smoothed to avoid flickering between bass hits.

    mix=0.0 → pure mellow layer (mellow_profile.color_mode).
    mix=1.0 → pure active layer (active_profile.color_mode).

    Blend thresholds come from active_profile.blend_start / blend_end / blend_response,
    read from the EnergyProfile via the active Profile.
    """

    def __init__(self, active_profile: Profile, mellow_profile: Profile) -> None:
        self._mellow = ColourModeEffect(mellow_profile)
        self._active = ColourModeEffect(active_profile)
        self._mix: float = 0.0
        self._ema_alpha: float = active_profile.blend_response
        self._low: float = active_profile.blend_start
        self._high: float = active_profile.blend_end

    @property
    def mix(self) -> float:
        """Current crossfade value: 0.0 = pure mellow, 1.0 = pure active."""
        return self._mix

    def render(self, features: AudioFeatures, t: float) -> Scene:
        target = _smoothstep(features.full, self._low, self._high)
        self._mix += self._ema_alpha * (target - self._mix)
        mellow_scene = self._mellow.render(features, t)
        active_scene = self._active.render(features, t)
        if self._mix < 1e-6:
            return mellow_scene
        if self._mix > 1.0 - 1e-6:
            return active_scene
        return LerpScene(mellow_scene, active_scene, self._mix)


# ---------------------------------------------------------------------------
# SyncEngine — orchestrates AudioPipeline + Renderer + Output at 30 Hz
# ---------------------------------------------------------------------------


class SyncEngine:
    """Owns a CavaPipeline and a ColourModeEffect; drives them at a fixed rate
    into whatever Output is passed to run().

    Delay buffer
    ------------
    A LatencyProbe is queried each tick for the current delay in milliseconds.
    Every tick appends one slot (a rendered Scene or None for silent/absent
    frames) and pops the oldest slot(s) to keep the buffer at the probe's
    target depth.  This keeps the delay time-consistent: silent gaps advance
    the buffer rather than compressing it.

    The probe defaults to NoLatencyProbe (0 ms, zero overhead).
    PlayerManager constructs the appropriate probe from the PlayerLatency config
    and can install a new one live via update_probe() — for example when the
    LMS sync master changes between polling cycles.

    last_onset
    ----------
    Reflects the onset flag on the most recently analysed AudioFeatures,
    *without* any output delay applied.  This is intentional: the GUI
    preview uses it to let the user judge detection timing directly against
    what they hear, not against the delayed light output.
    """

    def __init__(
        self,
        fifo_path: str | None,
        profile: Profile,
        probe: LatencyProbe | None = None,
        mellow_profile: Profile | None = None,
        analyser: AudioPipeline | None = None,
    ) -> None:
        self.profile = profile
        if analyser is not None:
            self._analyser: AudioPipeline = analyser
        elif fifo_path is not None:
            self._analyser = CavaPipeline(
                fifo_path,
                bars=profile.bars,
                onset_delta=profile.onset_delta,
                onset_alpha=profile.onset_alpha,
                exertion_clip=profile.exertion_clip,
            )
        else:
            raise ValueError("Either fifo_path or analyser must be provided")
        effective_mellow = mellow_profile if mellow_profile is not None else profile
        self._effect: LayerMixer = LayerMixer(profile, effective_mellow)
        self._probe: LatencyProbe = probe if probe is not None else NoLatencyProbe()
        self._delay_buffer: deque[Scene | None] = deque()
        self._last_onset: bool = False
        self._last_mix: float = 0.0
        self._last_bars: list[float] = []
        self._shm_source: PcmSource | None = None
        self._pcm_onset: StftOnsetPipeline | None = None
        self._pcm_multiband: MultibandStftPipeline | None = None
        self._pcm_superflux: SuperfluxStftPipeline | None = None
        self._pcm_hpss: PcmHpss | None = None
        self._last_pcm_onset: bool = False
        self._last_onset_bass: bool = False
        self._last_onset_mid: bool = False
        self._last_onset_treble: bool = False
        self._diag_frame: int = 0

    def attach_shm_source(self, source: PcmSource) -> None:
        """Connect a PCM source for the PCM-tap onset pipeline.

        Selects the appropriate pipeline based on profile.onset_method:
        - "combined"  → StftOnsetPipeline (comparison only, no colour effect)
        - "multiband" → MultibandStftPipeline (drives onset_bass/mid/treble)
        - "superflux" → SuperfluxStftPipeline (max-filter vibrato suppression)

        Call after the squeezelite SHM segment is confirmed ready and before
        run() is started.
        """
        self._shm_source = source
        method = self.profile.onset_method
        if method == "multiband":
            self._pcm_multiband = MultibandStftPipeline(
                source.sample_rate,
                bass_hz=self.profile.bass_hz,
                mid_hz=self.profile.mid_hz,
                delta=self.profile.onset_delta,
                alpha=self.profile.onset_alpha,
            )
        elif method == "superflux":
            self._pcm_superflux = SuperfluxStftPipeline(
                source.sample_rate,
                mu=self.profile.superflux_mu,
                lag=self.profile.superflux_lag,
                delta=self.profile.onset_delta,
                alpha=self.profile.onset_alpha,
            )
        else:
            self._pcm_onset = StftOnsetPipeline(
                source.sample_rate,
                delta=self.profile.onset_delta,
                alpha=self.profile.onset_alpha,
            )
        if self.profile.use_hpss_separation:
            self._pcm_hpss = PcmHpss(source.sample_rate)
            log.info("HPSS separation enabled for this session")

    def update_probe(self, probe: LatencyProbe) -> None:
        """Swap the latency probe live. Safe to call from the asyncio event loop."""
        self._probe = probe

    def update_profile(self, profile: Profile, mellow_profile: Profile | None = None) -> None:
        """Rebuild the effect with a new profile. Call after saving band/cutoff changes."""
        self.profile = profile
        effective_mellow = mellow_profile if mellow_profile is not None else profile
        self._effect = LayerMixer(profile, effective_mellow)

    def update_onset_pipeline(self, profile: Profile) -> None:
        """Switch the PCM-tap onset detection method without restarting any process.

        Tears down the current onset pipeline and builds a new one from
        profile.onset_method.  squeezelite, cava, and the Hue Entertainment
        session continue running without interruption.

        Side effect: onset warmup state (_last_pcm_onset, _last_onset_bass/mid/
        treble) resets to False.  The new pipeline's OnsetDetector needs ~30
        frames (~0.3 s at 100 Hz) to accumulate enough history for reliable
        detections.  This is a much smaller disturbance than a full
        deactivate/reactivate cycle (which resets BandNormaliser EMA and the
        Hue DTLS session too), but it is not zero — when measuring A/B
        differences between onset methods, wait at least 5 s after switching
        before comparing bars_mean values.
        """
        # Reset state before rebuilding.  All assignments are atomic under the
        # GIL; run() is a coroutine in the same event-loop thread, so there is
        # no concurrent access to these attributes.
        self._pcm_onset = None
        self._pcm_multiband = None
        self._pcm_superflux = None
        self._pcm_hpss = None
        self._last_pcm_onset = False
        self._last_onset_bass = False
        self._last_onset_mid = False
        self._last_onset_treble = False
        self.profile = profile

        if self._shm_source is not None:
            method = profile.onset_method
            if method == "multiband":
                self._pcm_multiband = MultibandStftPipeline(
                    self._shm_source.sample_rate,
                    bass_hz=profile.bass_hz,
                    mid_hz=profile.mid_hz,
                    delta=profile.onset_delta,
                    alpha=profile.onset_alpha,
                )
            elif method == "superflux":
                self._pcm_superflux = SuperfluxStftPipeline(
                    self._shm_source.sample_rate,
                    mu=profile.superflux_mu,
                    lag=profile.superflux_lag,
                    delta=profile.onset_delta,
                    alpha=profile.onset_alpha,
                )
            else:
                self._pcm_onset = StftOnsetPipeline(
                    self._shm_source.sample_rate,
                    delta=profile.onset_delta,
                    alpha=profile.onset_alpha,
                )
            if profile.use_hpss_separation:
                self._pcm_hpss = PcmHpss(self._shm_source.sample_rate)
                log.info("HPSS separation enabled (pipeline rebuild)")
        log.info(
            "[diag] update_onset_pipeline: method=%s (BandNormaliser EMA preserved, "
            "frame counter=%d)",
            profile.onset_method,
            self._diag_frame,
        )

    def update_render(self, profile: Profile, mellow_profile: Profile | None = None) -> None:
        """Apply render-only profile changes without restarting any process.

        Rebuilds LayerMixer and updates BandNormaliser.exertion_clip live.
        Safe to call while run() is active.
        """
        self.profile = profile
        effective_mellow = mellow_profile if mellow_profile is not None else profile
        self._effect = LayerMixer(profile, effective_mellow)
        normaliser = getattr(self._analyser, "normaliser", None)
        if normaliser is not None:
            normaliser.update_exertion_clip(profile.exertion_clip)

    @property
    def last_onset(self) -> bool:
        return self._last_onset

    @property
    def last_mix(self) -> float:
        """Current LayerMixer crossfade value (0.0 = mellow, 1.0 = active)."""
        return self._last_mix

    @property
    def last_pcm_onset(self) -> bool:
        return self._last_pcm_onset

    @property
    def last_onset_bass(self) -> bool:
        return self._last_onset_bass

    @property
    def last_onset_mid(self) -> bool:
        return self._last_onset_mid

    @property
    def last_onset_treble(self) -> bool:
        return self._last_onset_treble

    @property
    def last_bars(self) -> list[float]:
        return self._last_bars

    def start(self) -> None:
        self._analyser.start()

    def stop(self) -> None:
        self._analyser.stop()

    async def run(self, output: Output) -> None:
        """Send the latest available frame at a fixed rate until cancelled.

        Deliberately does NOT try to send every frame cava produces — see the
        SEND_INTERVAL_S comment above for why that overwhelmed the event loop
        and starved this very coroutine, silently killing the Entertainment
        stream via its own idle timeout.

        Each tick, one slot is appended to the delay buffer (a rendered Scene
        or None for absent/silent frames) and the oldest slot(s) are popped
        to keep the buffer at the probe's current target depth.  The output
        timestamp is taken at send time so spatial effects that use t for
        animation stay consistent with the real display moment.
        """
        while True:
            features = self._analyser.latest()
            t = time.monotonic()

            # PCM-tap onset path runs BEFORE the effect render so that
            # multiband overwrites features.onset* before _last_onset and the
            # Scene are captured.
            if self._shm_source is not None:
                samples = self._shm_source.read_new()
                if len(samples) > 0:
                    if self._pcm_multiband is not None:
                        band_results = self._pcm_multiband.push(samples)
                        if band_results:
                            (b_on, b_str), (m_on, m_str), (t_on, t_str) = band_results[-1]
                            self._last_onset_bass = b_on
                            self._last_onset_mid = m_on
                            self._last_onset_treble = t_on
                            self._last_pcm_onset = b_on or m_on or t_on
                            if features is not None:
                                features.onset_bass = b_on
                                features.onset_bass_strength = b_str
                                features.onset_mid = m_on
                                features.onset_mid_strength = m_str
                                features.onset_treble = t_on
                                features.onset_treble_strength = t_str
                                features.onset = self._last_pcm_onset
                    elif self._pcm_superflux is not None:
                        sf_results = self._pcm_superflux.push(samples)
                        if sf_results:
                            onset, _ = sf_results[-1]
                            self._last_pcm_onset = onset
                            if features is not None:
                                features.onset = onset
                    elif self._pcm_onset is not None:
                        # "combined": parallel comparison only, colour unchanged.
                        results = self._pcm_onset.push(samples)
                        if results:
                            self._last_pcm_onset = any(onset for onset, _ in results)

                    # HPSS runs in parallel with whichever onset method is active.
                    if self._pcm_hpss is not None and features is not None:
                        hpss_results = self._pcm_hpss.push(samples)
                        if hpss_results:
                            p_energy, h_energy = hpss_results[-1]
                            features.hpss_active = True
                            features.percussive_energy = p_energy
                            features.harmonic_energy = h_energy

            if features is not None:
                self._last_onset = features.onset
                self._last_bars = features.bars
                scene: Scene = self._effect.render(features, t)
                self._last_mix = self._effect.mix
                self._delay_buffer.append(scene)
                self._diag_frame += 1
                if self._diag_frame % 60 == 0:
                    bars = features.bars
                    n = len(bars)
                    bars_mean = sum(bars) / n if bars else 0.0
                    bars_max = max(bars) if bars else 0.0
                    bass_frac = _hz_to_frac(
                        self.profile.bass_hz,
                        self.profile.lower_cutoff_freq,
                        self.profile.higher_cutoff_freq,
                    )
                    mid_frac = _hz_to_frac(
                        self.profile.mid_hz,
                        self.profile.lower_cutoff_freq,
                        self.profile.higher_cutoff_freq,
                    )
                    bass_hi = int(bass_frac * n)
                    mid_hi = int(mid_frac * n)
                    log.info(
                        "[diag] method=%s frame=%d "
                        "bars_mean=%.3f bars_max=%.3f "
                        "bass_mean=%.3f mid_mean=%.3f treble_mean=%.3f",
                        self.profile.onset_method,
                        self._diag_frame,
                        bars_mean,
                        bars_max,
                        _band_avg(bars, 0, bass_hi),
                        _band_avg(bars, bass_hi, mid_hi),
                        _band_avg(bars, mid_hi, n),
                    )
            else:
                # None slot: advances the buffer in time without sending,
                # so the delay stays consistent even during silent passages.
                self._delay_buffer.append(None)

            delay_frames = max(
                0, round(self._probe.current_delay_ms() / 1000.0 / SEND_INTERVAL_S)
            )
            while len(self._delay_buffer) > delay_frames:
                entry = self._delay_buffer.popleft()
                if entry is not None:
                    output.send(entry, time.monotonic())

            await asyncio.sleep(SEND_INTERVAL_S)
