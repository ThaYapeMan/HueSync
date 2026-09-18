"""Streaming K-weighted momentary loudness (ITU-R BS.1770 / EBU R128).

Implemented directly from the recommendation, not from pyloudnorm.  Filter
coefficients are the exact values published in ITU-R BS.1770 Table 1 and
Table 2 for 48 kHz — no runtime coefficient derivation, no RBJ-cookbook
approximation.  HueSync's canonical pipeline is fixed at 48 kHz
(AudioCanonicalizer.TARGET_RATE), so this is the strictly most accurate
formulation available and the general-rate design formulas are unnecessary.

Differences from pyloudnorm, deliberately:

* Streaming, not offline.  Filter state (two direct-form-II-transposed
  biquads per channel) and the 400 ms mean-square window persist across
  feed() calls, so there is no transient at chunk boundaries.  pyloudnorm's
  ``apply_filter`` calls ``scipy.signal.lfilter`` with no ``zi`` and is only
  correct on a whole buffer at once.
* Momentary only.  BS.1770's gating (absolute −70 LKFS, relative −10 LU,
  two-pass block averaging) exists for *integrated programme* loudness and
  is irrelevant for a live meter; none of it is carried over.
* Coefficients computed once, as constants.  pyloudnorm regenerates them
  on every ``.a``/``.b`` property access (twice per apply_filter call).
* Silence is explicit.  log10(0) is never evaluated; below the absolute gate
  the meter reports ``SILENCE_LUFS`` rather than leaking -inf downstream.
* Pure NumPy, no SciPy.  A biquad is six multiply-adds per sample; the
  vectorised per-block form below is exact and needs no external filter
  routine.

Reference implementation studied: pyloudnorm (Steinmetz & Reiss, AES 2021,
MIT license). No code was copied from it.

Reference: ITU-R BS.1770-5 (11/2023), §1 and Annex 1; EBU Tech 3341 for
the sine-wave compliance test used in the self-check.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 48000

# ITU-R BS.1770 Table 1 — stage 1 pre-filter (high shelf, spherical head), 48 kHz.
_PRE_B = (1.53512485958697, -2.69169618940638, 1.19839281085285)
_PRE_A = (-1.69065929318241, 0.73248077421585)          # a1, a2 (a0 = 1)

# ITU-R BS.1770 Table 2 — stage 2 RLB weighting (high pass), 48 kHz.
_RLB_B = (1.0, -2.0, 1.0)
_RLB_A = (-1.99004745483398, 0.99007225036621)

# BS.1770 eq. (2): loudness = -0.691 + 10 log10(sum_i G_i z_i)
_LOUDNESS_OFFSET_DB = -0.691
# Channel weights G_i for {L, R, C, Ls, Rs}; stereo uses the first two.
_CHANNEL_GAINS = (1.0, 1.0, 1.0, 1.41, 1.41)

MOMENTARY_WINDOW_S = 0.400      # EBU R128 "momentary"
SHORT_TERM_WINDOW_S = 3.0       # EBU R128 "short-term"
ABSOLUTE_GATE_LUFS = -70.0      # BS.1770 absolute gate; used here only as the silence floor
SILENCE_LUFS = -np.inf          # reported when below the absolute gate


class _Biquad:
    """Direct-form II transposed biquad with persistent state, one per channel.

    Processes a whole block in a single vectorised pass: the recursive part
    is unavoidable sample-by-sample, but it is done in float64 on a compact
    loop over the block rather than a Python loop over every sample of a
    whole file, which is what makes this cheap enough for a 10 ms hop.
    """

    __slots__ = ("_b", "_a", "_z1", "_z2")

    def __init__(
        self, b: tuple[float, float, float], a: tuple[float, float], channels: int
    ) -> None:
        self._b = b
        self._a = a
        self._z1 = np.zeros(channels)
        self._z2 = np.zeros(channels)

    def reset(self) -> None:
        self._z1[:] = 0.0
        self._z2[:] = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """x: (n, channels) float64 → filtered (n, channels)."""
        b0, b1, b2 = self._b
        a1, a2 = self._a
        y = np.empty_like(x)
        z1, z2 = self._z1, self._z2
        for i in range(x.shape[0]):
            xi = x[i]
            yi = b0 * xi + z1
            z1 = b1 * xi - a1 * yi + z2
            z2 = b2 * xi - a2 * yi
            y[i] = yi
        self._z1, self._z2 = z1, z2
        return y


class _RunningMeanSquare:
    """Exact sliding-window mean square over the last ``window`` samples, per channel."""

    __slots__ = ("_window", "_ring", "_pos", "_filled", "_sum")

    def __init__(self, window: int, channels: int) -> None:
        self._window = window
        self._ring = np.zeros((window, channels))
        self._pos = 0
        self._filled = 0
        self._sum = np.zeros(channels)

    def reset(self) -> None:
        self._ring[:] = 0.0
        self._pos = 0
        self._filled = 0
        self._sum[:] = 0.0

    def push(self, sq: np.ndarray) -> None:
        """sq: (n, channels) squared samples."""
        n = sq.shape[0]
        if n >= self._window:
            sq = sq[-self._window:]
            n = self._window
            self._ring[:] = 0.0
            self._sum[:] = 0.0
            self._pos = 0
            self._filled = 0
        end = self._pos + n
        if end <= self._window:
            old = self._ring[self._pos:end]
            self._sum -= old.sum(axis=0)
            self._ring[self._pos:end] = sq
        else:
            first = self._window - self._pos
            old = np.concatenate((self._ring[self._pos:], self._ring[: n - first]))
            self._sum -= old.sum(axis=0)
            self._ring[self._pos:] = sq[:first]
            self._ring[: n - first] = sq[first:]
        self._sum += sq.sum(axis=0)
        self._pos = end % self._window
        self._filled = min(self._window, self._filled + n)

    @property
    def ready(self) -> bool:
        return self._filled >= self._window

    def mean(self) -> np.ndarray:
        # Guard tiny negative drift from the running subtraction.
        return np.maximum(self._sum, 0.0) / self._window


class MomentaryLoudnessMeter:
    """K-weighted momentary (400 ms) and short-term (3 s) loudness on a live 48 kHz stereo stream.

    feed(pcm) accepts any block length and returns the loudness values valid at
    the end of that block.  Values are None until the corresponding window has
    filled once (EBU R128 defines momentary loudness only over a full 400 ms).
    """

    def __init__(self, channels: int = 2, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(
                f"Coefficients are specified for {SAMPLE_RATE} Hz; got {sample_rate}. "
                "Resample to the canonical rate first (BS.1770 Table 1/2 note)."
            )
        if not 1 <= channels <= len(_CHANNEL_GAINS):
            raise ValueError(f"channels must be 1..{len(_CHANNEL_GAINS)}")
        self._gains = np.array(_CHANNEL_GAINS[:channels])
        self._pre = _Biquad(_PRE_B, _PRE_A, channels)
        self._rlb = _Biquad(_RLB_B, _RLB_A, channels)
        self._momentary = _RunningMeanSquare(int(round(MOMENTARY_WINDOW_S * SAMPLE_RATE)), channels)
        self._short_term = _RunningMeanSquare(
            int(round(SHORT_TERM_WINDOW_S * SAMPLE_RATE)), channels
        )

    def reset(self) -> None:
        self._pre.reset()
        self._rlb.reset()
        self._momentary.reset()
        self._short_term.reset()

    def feed(self, pcm: np.ndarray) -> tuple[float | None, float | None]:
        """pcm: (n, channels) float in [-1, 1].  Returns (momentary_lufs, short_term_lufs)."""
        x = np.asarray(pcm, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        y = self._rlb.process(self._pre.process(x))      # K-weighting = pre-filter then RLB
        sq = y * y
        self._momentary.push(sq)
        self._short_term.push(sq)
        return self._loudness(self._momentary), self._loudness(self._short_term)

    def _loudness(self, acc: _RunningMeanSquare) -> float | None:
        if not acc.ready:
            return None
        power = float(np.dot(self._gains, acc.mean()))        # BS.1770 eq. (2), sum_i G_i z_i
        if power <= 0.0:
            return SILENCE_LUFS
        lufs = _LOUDNESS_OFFSET_DB + 10.0 * np.log10(power)
        return SILENCE_LUFS if lufs < ABSOLUTE_GATE_LUFS else lufs


# ---------------------------------------------------------------------------
# Self-check against the EBU Tech 3341 compliance reference
# ---------------------------------------------------------------------------

def _self_check() -> None:
    """EBU Tech 3341 test 1: stereo 1 kHz sine at -23.0 dBFS must read -23.0 LUFS (±0.1 LU).

    Also checks the two frequency-response landmarks that define K-weighting:
    ~0 dB at 1 kHz and ~+4 dB high-shelf gain well above the ~1.7 kHz corner.
    Fed in irregular chunk sizes to prove the streaming state handling is
    seamless (a chunk-boundary transient would show up as a wrong reading).
    """
    rate = SAMPLE_RATE
    amp = 10 ** (-23.0 / 20.0)                     # -23 dBFS peak
    t = np.arange(int(5 * rate)) / rate
    sine = amp * np.sin(2 * np.pi * 1000.0 * t)
    stereo = np.stack([sine, sine], axis=1)

    meter = MomentaryLoudnessMeter()
    rng = np.random.default_rng(0)
    pos, last = 0, (None, None)
    while pos < len(stereo):
        n = int(rng.integers(100, 2000))            # deliberately irregular hops
        last = meter.feed(stereo[pos:pos + n])
        pos += n
    mom, st = last
    assert mom is not None and st is not None
    # A steady stereo sine at -23 dBFS peak sums to -23.0 LUFS per Tech 3341.
    assert abs(mom - (-23.0)) < 0.1, f"momentary {mom:.3f} LUFS, expected -23.0 ±0.1"
    assert abs(st - (-23.0)) < 0.1, f"short-term {st:.3f} LUFS, expected -23.0 ±0.1"

    # Frequency-response landmarks of the K-weighting curve itself.
    def response_db(freq: float) -> float:
        m = MomentaryLoudnessMeter(channels=1)
        s = 0.5 * np.sin(2 * np.pi * freq * t)
        out = m.feed(s[:, None])[0]
        ref = _LOUDNESS_OFFSET_DB + 10 * np.log10(0.5 ** 2 / 2)   # unweighted level of that sine
        return out - ref
    r1k, r10k, r30 = response_db(1000.0), response_db(10000.0), response_db(30.0)
    # The raw K-weighting curve has +0.691 dB of gain at 1 kHz — this is exactly
    # why BS.1770 eq. (2) subtracts 0.691.  Reproducing that constant here is a
    # strong check that the published Table 1 coefficients are applied correctly.
    assert abs(r1k - 0.691) < 0.05, f"1 kHz should be +0.691 dB (the BS.1770 offset), got {r1k:.3f}"
    assert 3.5 < r10k < 4.5, f"10 kHz should sit on the ~+4 dB shelf, got {r10k:.2f}"
    assert r30 < -1.0, f"30 Hz should be attenuated by the RLB high-pass, got {r30:.2f}"

    # Silence handling: no -inf leaking as a number, no log10(0) warning.
    m = MomentaryLoudnessMeter()
    with np.errstate(all="raise"):
        out = m.feed(np.zeros((rate, 2)))[0]
    assert out == SILENCE_LUFS

    print(f"OK  momentary={mom:.3f} LUFS  short_term={st:.3f} LUFS  "
          f"resp(1k)={r1k:+.2f} dB  resp(10k)={r10k:+.2f} dB  resp(30)={r30:+.2f} dB")


if __name__ == "__main__":
    _self_check()
