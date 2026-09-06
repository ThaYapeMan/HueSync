"""The audio analysis and colour engine.

Signal path (one layer at a time):

    FifoReader      — reads cava's raw FIFO output in a background thread
    BandNormaliser  — AGC: normalises each bar against its own rolling average
    OnsetDetector   — spectral flux onset detection with EMA-based threshold
    CavaAnalyser    — wraps the three above; produces AudioFeatures each frame
    ColourModeEffect— implements Effect; maps AudioFeatures to a Scene using
                      one of the two active ColorMode strategies
    SyncEngine      — orchestrates Analyser + Effect + Output at 30 Hz,
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
from .models import ColorMode, Profile
from .pcm_source import WINDOW_SIZE, PcmStft, SqueezeliteShmSource
from .types import (
    Analyser,
    AudioFeatures,
    Colour,
    Effect,
    LatencyProbe,
    Output,
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

    #: EMA smoothing factor.  At 30 Hz, α = 0.02 gives a window of ≈ 1.7 s.
    #: Raise (e.g. 0.05) to react faster; lower (e.g. 0.01) for a longer
    #: memory that smooths over brief dynamic shifts.
    DEFAULT_ALPHA: float = 0.02

    #: Mean raw bar value (0-255) below which the entire output frame is
    #: zeroed.  Prevents background noise from being amplified into wild
    #: colours when the track is paused or very quiet.  The EMA still
    #: updates during silence so the baseline decays naturally.
    DEFAULT_GATE: float = 5.0

    #: Default exertion clip ratio.  See class docstring.
    DEFAULT_EXERTION_CLIP: float = 3.0

    def __init__(
        self,
        alpha: float = DEFAULT_ALPHA,
        gate: float = DEFAULT_GATE,
        exertion_clip: float = DEFAULT_EXERTION_CLIP,
    ) -> None:
        self.alpha = alpha
        self.gate = gate
        self.exertion_clip = exertion_clip
        # Lazily initialised on the first frame so frame size need not be
        # known at construction time.
        self._ema: list[float] | None = None

    def normalise(self, frame: bytes) -> bytes:
        """Return an exertion-normalised copy of *frame* as bytes (0-255).

        Always updates the EMA, even below the silence gate, so the
        baseline decays during pauses and recovers cleanly on resumption.
        """
        n = len(frame)

        if self._ema is None:
            # Seed the EMA with the first frame so the normaliser is not
            # blind for the first few seconds of a session.
            self._ema = [float(v) for v in frame]

        a = self.alpha
        self._ema = [ema * (1.0 - a) + v * a for ema, v in zip(self._ema, frame, strict=True)]

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
# Helpers shared by CavaAnalyser and ColourModeEffect
# ---------------------------------------------------------------------------


def _band_average(frame: bytes, start: float, end: float) -> float:
    """Average of bars in a fractional slice of *frame* (bytes, 0-255 → 0.0-1.0).

    Kept for internal use and for the unit tests that exercise it directly.
    New code should prefer _slice_avg() which works on the float bar lists
    produced by CavaAnalyser.
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
# CavaAnalyser — implements the Analyser protocol
# ---------------------------------------------------------------------------


class CavaAnalyser:
    """Reads cava bar frames from a FIFO, normalises them, and produces
    AudioFeatures including onset detection.

    Wraps FifoReader (raw bytes from FIFO), BandNormaliser (AGC), and
    OnsetDetector (spectral flux).  The Analyser protocol is satisfied by
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

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    def latest(self) -> AudioFeatures | None:
        frame = self._reader.latest_frame()
        if frame is None:
            return None
        normed = self._normaliser.normalise(frame)
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
# ColourModeEffect — implements the Effect protocol
# ---------------------------------------------------------------------------


class ColourModeEffect:
    """The two remaining ColorMode strategies wrapped as a stateful Effect.

    bass_brightness has been removed.  The two surviving modes are:
    - SPECTRUM_RGB:  bass/mid/treble bands mapped to R/G/B channels.
    - MONO_PULSE:    single colour; brightness follows overall loudness.

    This is stateless per-frame (rendered colour depends only on the current
    AudioFeatures), but implemented as a class so future stateful Effects
    (onset cooldowns, decay envelopes, palette drift) follow the same pattern.

    Clipping: each band value is multiplied by profile.sensitivity and clipped
    to 1.0.  This is the *second* ceiling in the pipeline (the first is
    BandNormaliser's exertion clip).  With normalised input, sens ≈ 1.0 keeps
    steady-state music at roughly half-brightness with brief peaks at full;
    raising sensitivity above ~2.0 pushes the steady state into saturation.
    See BandNormaliser for the full picture.

    Band proportions: the legacy exclusive splits (0-15 %, 15-50 %, 50-100 %)
    are preserved here for backwards compatibility with existing profiles.
    New effects should use the cumulative fields on AudioFeatures instead.
    """

    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def render(self, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        sens = self.profile.sensitivity
        floor = self.profile.brightness_floor
        bars = features.bars

        n = len(bars)
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
        bass = min(_band_avg(bars, 0, bass_hi) * sens, 1.0)
        mid = min(_band_avg(bars, bass_hi, mid_hi) * sens, 1.0)
        treble = min(_band_avg(bars, mid_hi, n) * sens, 1.0)
        overall = min(_slice_avg(bars, 0.0, 1.0) * sens, 1.0)

        mode = self.profile.color_mode
        if mode == ColorMode.SPECTRUM_RGB:
            r, g, b = bass, mid, treble
        else:  # MONO_PULSE
            brightness = max(overall, floor)
            r = g = b = brightness

        if mode != ColorMode.SPECTRUM_RGB:
            r = max(r, floor)
            g = max(g, floor)
            b = max(b, floor)

        return UniformScene(Colour(r=r, g=g, b=b))


# ---------------------------------------------------------------------------
# SyncEngine — orchestrates Analyser + Effect + Output at 30 Hz
# ---------------------------------------------------------------------------


class SyncEngine:
    """Owns a CavaAnalyser and a ColourModeEffect; drives them at a fixed rate
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
        fifo_path: str,
        profile: Profile,
        probe: LatencyProbe | None = None,
    ) -> None:
        self.profile = profile
        self._analyser: Analyser = CavaAnalyser(
            fifo_path,
            bars=profile.bars,
            onset_delta=profile.onset_delta,
            onset_alpha=profile.onset_alpha,
            exertion_clip=profile.exertion_clip,
        )
        self._effect: Effect = ColourModeEffect(profile)
        self._probe: LatencyProbe = probe if probe is not None else NoLatencyProbe()
        self._delay_buffer: deque[Scene | None] = deque()
        self._last_onset: bool = False
        self._last_bars: list[float] = []
        self._shm_source: SqueezeliteShmSource | None = None
        self._pcm_onset: StftOnsetPipeline | None = None
        self._pcm_multiband: MultibandStftPipeline | None = None
        self._pcm_superflux: SuperfluxStftPipeline | None = None
        self._last_pcm_onset: bool = False
        self._last_onset_bass: bool = False
        self._last_onset_mid: bool = False
        self._last_onset_treble: bool = False
        self._diag_frame: int = 0

    def attach_shm_source(self, source: SqueezeliteShmSource) -> None:
        """Connect a SHM source for the PCM-tap onset pipeline.

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

    def update_probe(self, probe: LatencyProbe) -> None:
        """Swap the latency probe live. Safe to call from the asyncio event loop."""
        self._probe = probe

    def update_profile(self, profile: Profile) -> None:
        """Rebuild the effect with a new profile. Call after saving band/cutoff changes."""
        self.profile = profile
        self._effect = ColourModeEffect(profile)

    @property
    def last_onset(self) -> bool:
        return self._last_onset

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

            if features is not None:
                self._last_onset = features.onset
                self._last_bars = features.bars
                scene: Scene = self._effect.render(features, t)
                self._delay_buffer.append(scene)
                self._diag_frame += 1
                if self._diag_frame % 300 == 0:
                    bars = features.bars
                    log.info(
                        "[diag] onset_method=%s bars_mean=%.3f bars_max=%.3f",
                        self.profile.onset_method,
                        sum(bars) / len(bars) if bars else 0.0,
                        max(bars) if bars else 0.0,
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
