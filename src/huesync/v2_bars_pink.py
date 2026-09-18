"""V2 spectrum bars with per-band pink-noise compensation.

Standalone, self-verifying reference for the improvement to HueSync's
V2SpectrumEngine.  It reproduces V2's exact bin→bar mapping (log-spaced bars,
np.max within each bar, squelch gate) so the compensation is derived for the
*real* layout, then adds the one piece of the WLED AudioReactive chain that V2
omitted: the per-band pink-noise compensation (WLED's ``fftResultPink[]``).

Why this exists
---------------
V2 normalises every bar against ONE global peak EMA.  Music carries far more
raw magnitude in the bass than in the mids and treble (roughly a 1/f, "pink"
tilt), so that single reference is always set by the bass bar, and mid/treble
are squashed towards zero — visible on the live system as an almost entirely
red spectrum_rgb output the moment a beat dominates.

WLED (the stated inspiration for V2) has the same global AGC but applies a
static per-band multiplier table *before* it, flattening the systematic tilt
while leaving genuine cross-band dynamics intact.  The maintainers of WLED
AudioReactive (MoonModules) state the acceptance test plainly: play pink noise
and all bars must sit at a similar level.  That test is implemented below.

WLED's table is hand-tuned for its fixed 16-band, 10 kHz layout and cannot be
copied: V2 has 10–60 bars over a user-set frequency range.  So the table is
derived here for whatever layout is configured, by pushing synthetic pink
noise through the identical mapping and inverting the per-bar response.  This
also captures a second-order effect a formula would miss: np.max over a wide
high-frequency bar is biased upward by the number of bins it spans.

What is deliberately NOT changed: np.max per bar (V2's character, and the
reason its onset feel is good), the global peak AGC (so bass-heavy music still
reads bass-heavy — just not 10× so), and the 0..1 linear output range that
downstream effects assume.  WLED's optional logarithmic output scaling is a
separate decision and is not applied here.
"""

from __future__ import annotations

import math

import numpy as np

SAMPLE_RATE = 48000
WINDOW_SIZE = 2048  # HueSync StereoMagStft window → 1025 magnitude bins
N_BINS = WINDOW_SIZE // 2 + 1
NOISE_FLOOR = 1e-3  # _V2_NOISE_FLOOR

# Guard rails for the derived table.
MAX_COMPENSATION = 12.0  # WLED's steepest hand-tuned entry is 9.55×
_DERIVE_FRAMES = 400  # synthetic frames averaged when deriving (deterministic seed)


def bar_edges(
    n_bars: int,
    lower_hz: float,
    upper_hz: float,
    *,
    n_bins: int = N_BINS,
) -> list[tuple[int, int]]:
    """Exact V2 bin ranges: log-spaced bars, at least one bin each."""
    log_lo = math.log10(max(lower_hz, 1.0))
    log_hi = math.log10(max(upper_hz, lower_hz + 1.0))
    edges: list[tuple[int, int]] = []
    for i in range(n_bars):
        f_lo = 10.0 ** (log_lo + i / n_bars * (log_hi - log_lo))
        f_hi = 10.0 ** (log_lo + (i + 1) / n_bars * (log_hi - log_lo))
        bin_lo = max(0, round(f_lo * WINDOW_SIZE / SAMPLE_RATE))
        bin_hi = min(n_bins, max(bin_lo + 1, round(f_hi * WINDOW_SIZE / SAMPLE_RATE)))
        edges.append((bin_lo, bin_hi))
    return edges


def mag_to_bars_raw(mag: np.ndarray, edges: list[tuple[int, int]]) -> np.ndarray:
    """V2's mapping, unchanged: np.max within each bar, squelch-gated."""
    out = np.empty(len(edges))
    for i, (lo, hi) in enumerate(edges):
        v = float(np.max(mag[lo:hi])) if hi > lo else 0.0
        out[i] = v if v > NOISE_FLOOR else 0.0
    return out


def synthetic_pink_magnitude(rng: np.random.Generator) -> np.ndarray:
    """One STFT magnitude frame of pink noise (power ∝ 1/f → magnitude ∝ 1/√f).

    Rayleigh-distributed bin magnitudes, as a windowed FFT of Gaussian noise
    produces, so np.max sees realistic per-bin scatter rather than a smooth
    curve.  Bin 0 (DC) is zeroed; V2 never maps a bar there anyway.
    """
    f = np.arange(N_BINS) * (SAMPLE_RATE / WINDOW_SIZE)
    shape = np.zeros(N_BINS)
    shape[1:] = 1.0 / np.sqrt(f[1:])
    scatter = rng.rayleigh(scale=1.0, size=N_BINS)
    return shape * scatter


def derive_pink_compensation(n_bars: int, lower_hz: float, upper_hz: float) -> np.ndarray:
    """Per-bar multipliers that flatten V2's *displayed* response to pink noise.

    Derived on the same peak-hold statistic the engine displays (step 4's
    falloff), not on the raw per-frame mean.  This matters: a 1-bin bass bar
    has far more frame-to-frame scatter than a 100-bin treble bar (np.max over
    many bins already sits near the top of its distribution), so equalising
    the *mean* still leaves the bass peak-hold visibly higher.  Equalising the
    peak-held envelope is what makes the on-screen bars actually come out
    level, which is the acceptance criterion.

    Deterministic (fixed seed) so the same layout always yields the same
    table.  Normalised so the lowest bar has gain 1.0 — compensation only ever
    lifts bands the pink tilt pushes down; it never attenuates the bass.
    """
    edges = bar_edges(n_bars, lower_hz, upper_hz)
    rng = np.random.default_rng(20260918)
    dt = 480 / SAMPLE_RATE
    fall = math.exp(-dt / V2Bars.BAR_FALL_TAU_S)
    held: np.ndarray | None = None
    acc = np.zeros(n_bars)
    settle = 60  # let the peak-hold reach steady state first
    for k in range(_DERIVE_FRAMES + settle):
        raw = mag_to_bars_raw(synthetic_pink_magnitude(rng), edges)
        held = raw.copy() if held is None else np.maximum(held * fall, raw)
        if k >= settle:
            acc += held
    resp = acc / _DERIVE_FRAMES
    ref = resp[0] if resp[0] > 0 else np.max(resp)
    with np.errstate(divide="ignore"):
        gain = np.where(resp > 0, ref / resp, 1.0)
    return np.clip(gain, 1.0, MAX_COMPENSATION)


class V2Bars:
    """Bar computation for the improved V2 engine (frame → 0..1 bars).

    Chain, per frame — identical to V2 with one insertion (step 2):
      1. bin→bar np.max, squelch gate                   (unchanged)
      2. per-bar pink compensation                      (NEW — the WLED piece)
      3. global peak EMA, fast attack / slow release    (unchanged)
      4. per-bar falloff                                (unchanged)
    """

    ATTACK_TAU_S = 0.005
    RELEASE_TAU_S = 1.5
    BAR_FALL_TAU_S = 0.3

    def __init__(
        self, n_bars: int, lower_hz: float, upper_hz: float, compensate: bool = True
    ) -> None:
        self._edges = bar_edges(n_bars, lower_hz, upper_hz)
        self.compensation = (
            derive_pink_compensation(n_bars, lower_hz, upper_hz) if compensate else np.ones(n_bars)
        )
        self._peak_ema: float | None = None
        self._smooth: np.ndarray | None = None

    def reset(self) -> None:
        self._peak_ema = None
        self._smooth = None

    def feed(self, mag: np.ndarray, dt: float) -> np.ndarray:
        raw = mag_to_bars_raw(mag, self._edges) * self.compensation  # steps 1+2
        peak = float(raw.max()) if raw.size else 0.0
        if self._peak_ema is None:  # step 3
            self._peak_ema = max(peak, NOISE_FLOOR)
        else:
            tau = self.ATTACK_TAU_S if peak > self._peak_ema else self.RELEASE_TAU_S
            self._peak_ema += (1.0 - math.exp(-dt / tau)) * (peak - self._peak_ema)
        bars = np.minimum(raw / max(self._peak_ema, NOISE_FLOOR), 1.0)
        fall = math.exp(-dt / self.BAR_FALL_TAU_S)  # step 4
        self._smooth = (
            bars.copy() if self._smooth is None else np.maximum(self._smooth * fall, bars)
        )
        return self._smooth


# ---------------------------------------------------------------------------
# Self-check: the MoonModules acceptance test, measured before vs after
# ---------------------------------------------------------------------------


def _tilt_db(bars: np.ndarray) -> float:
    """Spread between the loudest and quietest bar, in dB (0 = perfectly flat)."""
    b = bars[bars > 0]
    return 20.0 * math.log10(b.max() / b.min()) if b.size >= 2 else 0.0


def _run(engine: V2Bars, frames: list[np.ndarray], dt: float, average_last: int = 0) -> np.ndarray:
    """Feed frames; return the last output, or the mean of the last ``average_last`` outputs.

    For noise input the per-frame output jitters (a 1-bin bass bar scatters far
    more than a 70-bin treble bar), so "bars at a similar level" — the
    MoonModules acceptance criterion, which itself says "some jitter expected"
    — is a statement about the time-averaged level, not a single frame.
    """
    outs: list[np.ndarray] = []
    for m in frames:
        outs.append(engine.feed(m, dt).copy())
    if average_last:
        return np.mean(outs[-average_last:], axis=0)
    return outs[-1]


def _self_check() -> None:
    n_bars, lo_hz, hi_hz = 34, 50.0, 12000.0  # the person's live "Combined PCM Pipeline" layout
    dt = 480 / SAMPLE_RATE  # 10 ms hop
    rng = np.random.default_rng(7)
    pink = [synthetic_pink_magnitude(rng) for _ in range(600)]

    # 1. Pink noise: bars must come out similar (MoonModules acceptance test),
    #    judged on the time-averaged level over the last 300 frames (3 s).
    before = _run(V2Bars(n_bars, lo_hz, hi_hz, compensate=False), pink, dt, average_last=300)
    after = _run(V2Bars(n_bars, lo_hz, hi_hz, compensate=True), pink, dt, average_last=300)
    t_before, t_after = _tilt_db(before), _tilt_db(after)
    assert t_before > 10.0, f"expected strong bass tilt without compensation, got {t_before:.1f} dB"
    assert t_after < 2.0, f"compensated pink noise should be near-flat, got {t_after:.1f} dB spread"
    assert after.mean() > 0.5, f"compensated bars should sit high, mean {after.mean():.2f}"

    # 2. A pure bass tone must still read as bass: compensation flattens the
    #    systematic tilt, it must not invent treble.  A single bin at ~80 Hz.
    eng = V2Bars(n_bars, lo_hz, hi_hz)
    tone = np.zeros(N_BINS)
    tone[round(80.0 * WINDOW_SIZE / SAMPLE_RATE)] = 1.0
    bass = _run(eng, [tone] * 50, dt)
    assert bass[:3].max() > 0.9, "bass bar should be at full scale for a bass tone"
    assert bass[n_bars // 2 :].max() < 0.05, (
        "no energy should appear in the upper half for a bass tone"
    )

    # 3. The same for a treble tone — and it must now reach full scale, which
    #    is exactly what the uncompensated engine cannot do under a bass reference.
    eng.reset()
    tone = np.zeros(N_BINS)
    tone[round(8000.0 * WINDOW_SIZE / SAMPLE_RATE)] = 1.0
    treble = _run(eng, [tone] * 50, dt)
    assert treble[-4:].max() > 0.9, "treble tone should reach full scale"
    assert treble[: n_bars // 2].max() < 0.05, (
        "no energy should appear in the lower half for a treble tone"
    )

    # 4. Table sanity: starts at 1.0, never attenuates, capped, rises overall.
    comp = eng.compensation
    assert comp[0] == 1.0 and comp.min() >= 1.0 and comp.max() <= MAX_COMPENSATION
    assert comp[-1] > comp[0], "highest bar must be lifted relative to the lowest"

    print(
        f"OK  pink-noise spread: {t_before:.1f} dB before → {t_after:.1f} dB after   "
        f"(bars {n_bars}, {lo_hz:.0f}–{hi_hz:.0f} Hz)"
    )
    print(
        f"    compensation table: first={comp[0]:.2f} mid={comp[n_bars // 2]:.2f} "
        f"last={comp[-1]:.2f}"
    )
    print(
        f"    bass tone → bar0..2 max {bass[:3].max():.2f}, "
        f"upper half max {bass[n_bars // 2 :].max():.3f}"
    )
    print(
        f"    treble tone → last4 max {treble[-4:].max():.2f}, "
        f"lower half max {treble[: n_bars // 2].max():.3f}"
    )


if __name__ == "__main__":
    _self_check()
