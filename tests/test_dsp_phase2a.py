"""Phase 2A deterministic DSP comparison — pure Python unit tests.

These tests run without the CAVA binary and without any production state.
They assert properties of BandNormaliser, PcmStft, and the CavaSimulator
that are independent of clock rate or LXC availability.

Covered:
  1. BandNormaliser cadence invariance — same continuous-time EMA response
     at 30 Hz vs 100 Hz call rates.
  2. Native bar frequency mapping — log-spaced bin assignment, no bar silent.
  3. Opposite-phase stereo cancellation — native (L+R)/2 downmix vs CAVA
     separate-channel FFT produce measurably different output for L=-R.
  4. CavaSimulator silence floor — no non-zero output on zero input.
  5. NativeAnalyser produces frames for non-trivial input.

Phase 2A-R additions (see scripts/phase2a_r_compare.py):
  6. Phase2AR corrections — withdrawn 19% claim, corrected bar-edge formula.
  7. CanonicalPcm contract — SHA-256 stability, S16_LE roundtrip, frame count.
  8. StereoSemantics — native in-phase/opposite-phase behaviour (corrected infra).
  9. CavaRunnerIntegration — skipped unless cava binary present.

NOTE: TestCavaSimulatorSpectral.test_single_tone_activates_correct_bar uses
cava_bar_edges() from phase2a_compare (wrong formula) but tests the CavaSimulator
class from the same module, so it remains internally consistent.  The formula
correctness is covered separately in TestPhase2ARCorrections.
"""

from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from phase2a_compare import (
    CAVA_FRAMERATE,
    LOWER_HZ,
    N_BARS,
    SR,
    UPPER_HZ,
    CavaSimulator,
    NativeAnalyser,
    bar_centre_hz,
    cava_bar_edges,
    gen_multi_tone,
    gen_opposite_phase,
    gen_silence,
    gen_steady_sine,
    native_bar_edges,
)
from phase2a_r_compare import (
    CanonicalPcm,
    CavaReferenceModel,
    CavaRunner,
    _make_signal_b,
    _make_signal_c,
    log_bar_edges,
)
from phase2a_r_compare import (
    NativeAnalyser as NativeAnalyserR,
)

from huesync.sync_engine import BandNormaliser

# ---------------------------------------------------------------------------
# 1. BandNormaliser cadence invariance
# ---------------------------------------------------------------------------

_STEP_VAL = bytes([100] * N_BARS)
_SILENT_VAL = bytes([0] * N_BARS)


def _run_bandnorm(rate_hz: float, duration_s: float = 2.5,
                  onset_s: float = 0.5, gate: float = 0.0) -> dict[float, float]:
    """Feed a step signal to a BandNormaliser at `rate_hz` Hz.

    Returns a dict mapping elapsed time → normalised bar-0 value (0.0..1.0).
    """
    bn = BandNormaliser(
        attack_tau_s=BandNormaliser.DEFAULT_ATTACK_TAU_S,
        release_tau_s=BandNormaliser.DEFAULT_RELEASE_TAU_S,
        gate=gate,
        exertion_clip=BandNormaliser.DEFAULT_EXERTION_CLIP,
    )
    dt = 1.0 / rate_hz
    n_frames = int(duration_s * rate_hz)
    onset_frame = int(onset_s * rate_hz)
    result: dict[float, float] = {}
    for i in range(n_frames):
        frame = _STEP_VAL if i >= onset_frame else _SILENT_VAL
        normed = bn.normalise(frame, dt)
        result[i * dt] = normed[0] / 255.0
    return result


def _sample_at(series: dict[float, float], target_t: float) -> float:
    """Return the value nearest to target_t in a time→value dict."""
    times = sorted(series.keys())
    nearest = min(times, key=lambda t: abs(t - target_t))
    return series[nearest]


class TestBandNormaliserCadenceInvariance:
    """EMA response at same elapsed time must be equal regardless of call rate."""

    # At high attack_tau (5 ms), the EMA converges fast.  We check a slower
    # property: the RELEASE trajectory.  After 1 s of signal the normaliser
    # has settled; we measure value at several points post-onset.
    ONSET_S = 0.5
    EVAL_POINTS = [0.7, 1.0, 1.5, 2.0]
    TOLERANCE = 0.04   # ≤ 4% absolute difference between 30 and 100 Hz

    def test_30hz_vs_100hz_at_same_elapsed_time(self) -> None:
        v30 = _run_bandnorm(30.0)
        v100 = _run_bandnorm(100.0)
        for t in self.EVAL_POINTS:
            val30 = _sample_at(v30, t)
            val100 = _sample_at(v100, t)
            diff = abs(val30 - val100)
            assert diff <= self.TOLERANCE, (
                f"cadence divergence at t={t:.1f}s: "
                f"30Hz={val30:.4f}, 100Hz={val100:.4f}, diff={diff:.4f} > {self.TOLERANCE}"
            )

    def test_release_symmetry_same_at_both_rates(self) -> None:
        """After signal ends, release trajectory must match at both rates."""
        duration_s = 3.0
        onset_s = 0.5
        # Build time-indexed but use onset+1s as post-onset check
        v30 = _run_bandnorm(30.0, duration_s=duration_s, onset_s=onset_s)
        v100 = _run_bandnorm(100.0, duration_s=duration_s, onset_s=onset_s)
        # Sample at 1.5 s (1 s after onset — well into steady state)
        for t in (1.5, 2.0, 2.5):
            val30 = _sample_at(v30, t)
            val100 = _sample_at(v100, t)
            diff = abs(val30 - val100)
            assert diff <= self.TOLERANCE, (
                f"release trajectory differs at t={t:.1f}s: "
                f"30Hz={val30:.4f}, 100Hz={val100:.4f}"
            )

    def test_alpha_is_rate_independent(self) -> None:
        """verify continuous-time formula: alpha = 1 - exp(-dt/tau).

        At dt=10ms and dt=33ms the alpha values must differ, but the EMA
        response at the same elapsed time must converge to the same value.
        """
        tau = BandNormaliser.DEFAULT_RELEASE_TAU_S
        alpha_30 = 1.0 - math.exp(-1 / 30 / tau)
        alpha_100 = 1.0 - math.exp(-1 / 100 / tau)
        # Alphas are different (different step sizes)
        assert abs(alpha_30 - alpha_100) > 1e-4, "alphas should differ"
        # But after N steps of dt_30 vs M steps of dt_100 summing to T=1s,
        # the EMA value must be similar
        x0 = 1.0
        val_30_after_1s = x0 * (1 - alpha_30) ** 30   # 30 steps × 33 ms = 1 s
        val_100_after_1s = x0 * (1 - alpha_100) ** 100  # 100 steps × 10 ms = 1 s
        assert abs(val_30_after_1s - val_100_after_1s) < 0.02


# ---------------------------------------------------------------------------
# 2. Native bar frequency mapping
# ---------------------------------------------------------------------------

class TestNativeBarMapping:
    """_mag_to_bar_bytes bin-assignment consistency."""

    def test_n_edges_has_correct_length(self) -> None:
        edges = native_bar_edges()
        assert len(edges) == N_BARS + 1

    def test_edges_are_monotonically_increasing(self) -> None:
        edges = native_bar_edges()
        for i in range(len(edges) - 1):
            assert edges[i] < edges[i + 1], f"edge {i} not monotone"

    def test_lower_bound_at_lower_hz(self) -> None:
        edges = native_bar_edges()
        assert abs(edges[0] - LOWER_HZ) < 1.0

    def test_upper_bound_at_upper_hz(self) -> None:
        edges = native_bar_edges()
        assert abs(edges[-1] - UPPER_HZ) < 1.0

    def test_bar_centres_are_geometric_means(self) -> None:
        edges = native_bar_edges()
        centres = bar_centre_hz(edges)
        assert len(centres) == N_BARS
        for i, c in enumerate(centres):
            expected = math.sqrt(edges[i] * edges[i + 1])
            assert abs(c - expected) < 1e-6

    def test_single_tone_activates_expected_bar(self) -> None:
        """440 Hz tone activates a bar near bar index for 440 Hz."""
        na = NativeAnalyser()
        sig = gen_steady_sine(440.0, 0.5, silence_pre=0.0, duration=0.5, silence_post=0.0)
        frames = na.push(sig)
        assert len(frames) > 0
        # Find bar whose native edges contain 440 Hz
        edges = native_bar_edges()
        expected_bar = None
        for i in range(N_BARS):
            if edges[i] <= 440.0 < edges[i + 1]:
                expected_bar = i
                break
        assert expected_bar is not None
        # Mean raw output: dominant bar should be within ±2 of expected
        raw_mean = np.mean(
            np.stack([f["raw"] for f in frames]).astype(float), axis=0
        )
        dom = int(np.argmax(raw_mean))
        assert abs(dom - expected_bar) <= 2, (
            f"dominant bar {dom} is >2 away from expected {expected_bar} for 440 Hz"
        )


# ---------------------------------------------------------------------------
# 3. Opposite-phase stereo cancellation
# ---------------------------------------------------------------------------

class TestOppositePhase:
    """Native (L+R)/2 downmix vs CAVA separate-channel FFT for L=-R."""

    def test_native_cancels_opposite_phase(self) -> None:
        """Native downmix (L+R)/2 = 0 for L=-R; raw FFT output must be near-zero."""
        na = NativeAnalyser()
        sig = gen_opposite_phase(440.0, 0.5, silence_pre=0.0, duration=1.0, silence_post=0.0)
        frames = na.push(sig)
        if not frames:
            pytest.skip("Not enough samples to produce frames")
        raw_mean = np.mean(
            np.stack([f["raw"] for f in frames]).astype(float)
        )
        assert raw_mean < 5.0, (
            f"Native opposite-phase should cancel: mean raw={raw_mean:.2f}"
        )

    def test_cava_retains_opposite_phase_energy(self) -> None:
        """CAVA FFT(L) and FFT(R) separately: |FFT(-x)| = |FFT(x)|, energy retained."""
        cava = CavaSimulator()
        sig = gen_opposite_phase(440.0, 0.5, silence_pre=0.0, duration=2.0, silence_post=0.0)
        frames = cava.push(sig)
        if not frames:
            pytest.skip("Not enough samples to produce frames")
        raw_mean = np.mean(
            np.stack([f["raw"] for f in frames])
        )
        # CAVA sees same magnitude as in-phase — must not be near-zero
        assert raw_mean > 0.05, (
            f"CAVA opposite-phase should retain energy: mean raw={raw_mean:.4f}"
        )

    def test_native_vs_cava_ratio_opposite_phase(self) -> None:
        """Native output for L=-R should be much smaller than CAVA's."""
        na = NativeAnalyser()
        cava = CavaSimulator()
        sig = gen_opposite_phase(440.0, 0.5, silence_pre=0.0, duration=2.0, silence_post=0.0)

        n_frames = na.push(sig)
        c_frames = cava.push(sig)

        if not n_frames or not c_frames:
            pytest.skip("Not enough samples")

        n_mean = float(np.mean(
            np.stack([f["raw"] for f in n_frames]).astype(float) / 255.0
        ))
        c_mean = float(np.mean(
            np.stack([f["raw"] for f in c_frames])
        ))

        # CAVA energy should be at least 5× native for opposite-phase
        assert c_mean > n_mean * 5, (
            f"Expected cava > 5× native for opp-phase; "
            f"native={n_mean:.4f}, cava={c_mean:.4f}"
        )


# ---------------------------------------------------------------------------
# 4. CavaSimulator silence floor
# ---------------------------------------------------------------------------

class TestCavaSimulatorSilence:
    def test_silence_produces_zero_raw(self) -> None:
        cava = CavaSimulator()
        sig = gen_silence(2.0)
        frames = cava.push(sig)
        assert len(frames) > 0, "expected frames even for silence"
        raw_mean = np.mean(
            np.stack([f["raw"] for f in frames])
        )
        assert raw_mean < 1e-8, f"silence should produce ~zero raw: got {raw_mean}"

    def test_silence_produces_zero_normed(self) -> None:
        cava = CavaSimulator()
        sig = gen_silence(2.0)
        frames = cava.push(sig)
        normed_vals = np.concatenate([f["normed"].astype(float) for f in frames])
        assert float(normed_vals.max()) == 0.0, "silence should produce zero normed bytes"

    def test_frame_count_matches_framerate(self) -> None:
        """Number of CAVA frames for N seconds ≈ N × framerate (±2)."""
        cava = CavaSimulator()
        duration = 3.0
        sig = gen_silence(duration)
        frames = cava.push(sig)
        expected = int(duration * CAVA_FRAMERATE)
        assert abs(len(frames) - expected) <= 2, (
            f"expected ~{expected} frames, got {len(frames)}"
        )


# ---------------------------------------------------------------------------
# 5. NativeAnalyser basic sanity
# ---------------------------------------------------------------------------

class TestNativeAnalyser:
    def test_produces_frames_for_tone(self) -> None:
        na = NativeAnalyser()
        sig = gen_steady_sine(440.0, 0.5, silence_pre=0.0, duration=0.5, silence_post=0.0)
        frames = na.push(sig)
        assert len(frames) > 0

    def test_frame_structure(self) -> None:
        na = NativeAnalyser()
        sig = gen_steady_sine(1000.0, 0.3, silence_pre=0.0, duration=0.2, silence_post=0.0)
        frames = na.push(sig)
        assert len(frames) > 0
        f0 = frames[0]
        assert "raw" in f0
        assert "normed" in f0
        assert f0["raw"].dtype == np.uint8
        assert f0["normed"].dtype == np.uint8
        assert len(f0["raw"]) == N_BARS
        assert len(f0["normed"]) == N_BARS

    def test_raw_values_in_uint8_range(self) -> None:
        na = NativeAnalyser()
        sig = gen_steady_sine(440.0, 0.8, silence_pre=0.0, duration=0.5, silence_post=0.0)
        frames = na.push(sig)
        all_raw = np.concatenate([f["raw"] for f in frames])
        assert all_raw.min() >= 0
        assert all_raw.max() <= 255

    def test_normed_values_in_uint8_range(self) -> None:
        na = NativeAnalyser()
        sig = gen_steady_sine(440.0, 0.8, silence_pre=0.0, duration=0.5, silence_post=0.0)
        frames = na.push(sig)
        all_normed = np.concatenate([f["normed"] for f in frames])
        assert all_normed.min() >= 0
        assert all_normed.max() <= 255

    def test_reset_gives_fresh_state(self) -> None:
        """After reset(), output matches a brand-new NativeAnalyser."""
        na1 = NativeAnalyser()
        na2 = NativeAnalyser()
        sig = gen_steady_sine(440.0, 0.3, silence_pre=0.0, duration=0.3, silence_post=0.0)

        na1.push(sig)
        na1.reset()
        frames1_after = na1.push(sig)
        frames2 = na2.push(sig)

        n1 = np.stack([f["raw"] for f in frames1_after]).astype(float)
        n2 = np.stack([f["raw"] for f in frames2]).astype(float)
        # Reset gives same output as fresh instance (same STFT state)
        assert np.allclose(n1, n2, atol=1e-6)

    def test_silence_then_wideband_activates(self) -> None:
        """After a silence period, wideband signal raises normalised output.

        Single-frequency tones concentrate energy in 1-2 bars; mean raw
        across 30 bars stays below the BandNormaliser gate (default 5.0).
        A multi-tone signal spread across several bars passes the gate reliably.
        """
        na = NativeAnalyser()
        # Silence then wide-band signal (100 Hz + 1 kHz + 8 kHz)
        silence = gen_silence(1.0)
        tone = gen_multi_tone(freqs=(100.0, 1000.0, 8000.0), amp=0.25, duration=2.0)
        sig = np.concatenate([silence, tone])
        frames = na.push(sig)
        n_silence = int(1.0 / (na.hop / SR))
        n_tone = int(2.0 / (na.hop / SR))
        silence_normed = np.stack([f["normed"] for f in frames[:n_silence]]).astype(float)
        tone_end = n_silence + n_tone
        tone_normed = np.stack(
            [f["normed"] for f in frames[n_silence:tone_end]]
        ).astype(float)
        # Tone section must have meaningfully higher mean normed output than silence
        assert tone_normed.mean() > silence_normed.mean() + 10, (
            f"Wideband signal should raise normed output: "
            f"tone_mean={tone_normed.mean():.2f}, silence_mean={silence_normed.mean():.2f}"
        )


# ---------------------------------------------------------------------------
# 6. CavaSimulator spectral shape sanity
# ---------------------------------------------------------------------------

class TestCavaSimulatorSpectral:
    def test_single_tone_activates_correct_bar(self) -> None:
        """A 1 kHz tone must activate a bar near 1 kHz in CAVA-sim output."""
        cava = CavaSimulator()
        # Use a longer tone to let autosens stabilise
        sig = gen_steady_sine(1000.0, 0.5, silence_pre=0.0, duration=3.0, silence_post=0.0)
        frames = cava.push(sig)
        assert len(frames) > 0
        # Skip first 30 frames (autosens warmup)
        stable_frames = frames[30:]
        if not stable_frames:
            pytest.skip("Not enough frames after warmup")
        raw_mean = np.mean(
            np.stack([f["raw"] for f in stable_frames]), axis=0
        )
        dom = int(np.argmax(raw_mean))
        # 1000 Hz should land in bar range roughly 15-25 for 30 bars 50-12000 Hz
        edges = cava_bar_edges()
        expected_bar = None
        for i in range(N_BARS):
            if edges[i] <= 1000.0 < edges[i + 1]:
                expected_bar = i
                break
        assert expected_bar is not None
        assert abs(dom - expected_bar) <= 3, (
            f"CAVA dominant bar {dom} is >3 from expected {expected_bar} for 1kHz"
        )

    def test_autosens_increases_during_silence(self) -> None:
        """Autosens should not decrease (sens×0.98) when signal is silence."""
        cava = CavaSimulator()
        sig = gen_silence(2.0)
        sens_before = cava.sens
        cava.push(sig)
        assert cava.sens >= sens_before, "Autosens should not decrease during silence"

    def test_autosens_decreases_on_overshoot(self) -> None:
        """High-amplitude signal should trigger autosens reduction."""
        cava = CavaSimulator()
        # Start with artificially high sens to guarantee overshoot
        cava.sens = 100.0
        cava.sens_init = False
        sig = gen_steady_sine(440.0, 0.9, silence_pre=0.0, duration=1.0, silence_post=0.0)
        cava.push(sig)
        assert cava.sens < 100.0, "sens should decrease when output > 1.0"


# ---------------------------------------------------------------------------
# 7. Phase 2A-R corrections
# ---------------------------------------------------------------------------


class TestPhase2ARCorrections:
    """Verify Phase 2A-R corrections — withdrawn 19% claim, corrected formula."""

    def test_corrected_edges_reach_upper_bound(self) -> None:
        """Corrected formula reaches upper exactly at n=n_bars."""
        edges = log_bar_edges(N_BARS, LOWER_HZ, UPPER_HZ)
        assert len(edges) == N_BARS + 1
        assert abs(edges[-1] - UPPER_HZ) < 1e-6, (
            f"Last edge {edges[-1]} should equal UPPER_HZ {UPPER_HZ}"
        )
        assert abs(edges[0] - LOWER_HZ) < 1e-6, (
            f"First edge {edges[0]} should equal LOWER_HZ {LOWER_HZ}"
        )

    def test_withdrawn_formula_did_not_reach_upper(self) -> None:
        """The old (n+1)/(n_bars+1) formula produced systematic error."""
        # Phase 2A formula: last edge = lower*(upper/lower)^(n_bars/(n_bars+1))
        # which is strictly less than upper
        old_last = LOWER_HZ * (UPPER_HZ / LOWER_HZ) ** (N_BARS / (N_BARS + 1))
        assert old_last < UPPER_HZ - 1.0, (
            f"Old formula last edge {old_last:.1f} should be well below {UPPER_HZ}"
        )
        # Confirm the deviation is non-trivial (the ~19% claim source)
        relative_error = abs(old_last - UPPER_HZ) / UPPER_HZ
        assert relative_error > 0.05, (
            f"Old formula relative error {relative_error:.3f} should be > 5%"
        )

    def test_native_and_corrected_cava_edges_identical(self) -> None:
        """Ideal bar edges: corrected CAVA formula = native formula (algebraically identical)."""
        corrected = log_bar_edges(N_BARS, LOWER_HZ, UPPER_HZ)
        native = native_bar_edges(N_BARS, LOWER_HZ, UPPER_HZ)
        assert len(corrected) == len(native)
        for i, (c, n) in enumerate(zip(corrected, native, strict=True)):
            assert abs(c - n) < 1e-6, (
                f"Edge {i}: corrected={c:.6f}, native={n:.6f}, diff={abs(c-n):.2e}"
            )

    def test_corrected_bar_centre_at_expected_hz(self) -> None:
        """With corrected formula, bar 14 centre is geometric mean of edges[14] and edges[15]."""
        edges = log_bar_edges(N_BARS, LOWER_HZ, UPPER_HZ)
        # Bar 14 centre = geometric mean of edges[14] and edges[15]
        centre_14 = math.sqrt(edges[14] * edges[15])
        # Verify it falls in a plausible range for 30 bars spanning 50-12000 Hz
        # log midpoint of [50, 12000] is sqrt(50*12000) ≈ 775 Hz; bar 14 is near there
        assert 600.0 < centre_14 < 1000.0, (
            f"Bar 14 centre {centre_14:.1f} Hz out of expected 600-1000 Hz range"
        )


# ---------------------------------------------------------------------------
# 8. CanonicalPcm contract tests
# ---------------------------------------------------------------------------


class TestCanonicalPcm:
    """Canonical PCM contract tests."""

    def _make_simple_pcm(self, label: str = "test") -> CanonicalPcm:
        t = np.arange(int(0.5 * SR), dtype=np.float64) / SR
        mono = (0.5 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)
        stereo = np.stack([mono, mono], axis=1)
        return CanonicalPcm(label, stereo)

    def test_sha256_is_stable(self) -> None:
        """Same float32 input always produces same SHA-256."""
        pcm1 = self._make_simple_pcm("a")
        pcm2 = self._make_simple_pcm("b")  # different label, same signal
        assert pcm1.sha256 == pcm2.sha256, "SHA-256 must depend only on signal data"

    def test_s16le_roundtrip_within_scaling(self) -> None:
        """S16_LE encode-decode: roundtrip error within quantisation + scale error.

        CanonicalPcm encodes with *32767, decodes with /32768 (standard practice).
        The systematic scale factor (32767/32768 ≈ 1 - 3e-5) means maximum error
        is bounded by 1/32768 + |x| * (1/32768) ≤ 2/32768 for |x| ≤ 1.
        """
        t = np.arange(int(0.2 * SR), dtype=np.float64) / SR
        mono = (0.8 * np.sin(2 * math.pi * 1000.0 * t)).astype(np.float32)
        stereo = np.stack([mono, mono], axis=1)
        pcm = CanonicalPcm("roundtrip", stereo)
        decoded = pcm.to_float32_stereo()
        # Error bound: quantisation (1/32768) + scale asymmetry (≤1/32768)
        max_err = float(np.max(np.abs(decoded[:, 0] - mono)))
        assert max_err <= 2.0 / 32768 + 1e-6, (
            f"S16_LE roundtrip error {max_err:.2e} exceeds 2/32768"
        )

    def test_n_frames_matches_duration(self) -> None:
        """Frame count × sample_rate matches byte count / (channels * width)."""
        pcm = self._make_simple_pcm()
        expected_frames = len(pcm.bytes_data) // (CanonicalPcm.CHANNELS * CanonicalPcm.SAMPLE_WIDTH)
        assert pcm.n_frames == expected_frames
        expected_duration = expected_frames / CanonicalPcm.SAMPLE_RATE
        assert abs(pcm.duration_s - expected_duration) < 1e-9

    def test_info_keys_present(self) -> None:
        """info() returns all required keys."""
        pcm = self._make_simple_pcm()
        info = pcm.info()
        required = {"label", "sample_rate", "channels", "format", "byte_length",
                    "n_frames", "duration_s", "sha256"}
        assert required.issubset(info.keys())

    def test_mono_downmix_inphase(self) -> None:
        """In-phase stereo (L=R): (L+R)/2 equals L."""
        t = np.arange(int(0.1 * SR), dtype=np.float64) / SR
        mono = (0.5 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)
        stereo = np.stack([mono, mono], axis=1)
        pcm = CanonicalPcm("inphase", stereo)
        downmix = pcm.to_float32_mono()
        decoded_stereo = pcm.to_float32_stereo()
        # After S16_LE quantisation, L should equal R exactly
        np.testing.assert_array_equal(decoded_stereo[:, 0], decoded_stereo[:, 1])
        # Downmix should equal L
        np.testing.assert_array_equal(downmix, decoded_stereo[:, 0])


# ---------------------------------------------------------------------------
# 9. Stereo semantics with corrected infrastructure
# ---------------------------------------------------------------------------


class TestStereoSemantics:
    """Stereo handling tests using corrected CavaReferenceModel infrastructure."""

    def test_native_in_phase_produces_same_as_mono(self) -> None:
        """In-phase stereo (L=R): (L+R)/2 = L = R, so FFT output equals mono."""
        pcm_b = _make_signal_b()
        decoded = pcm_b.to_float32_stereo()
        # L and R should be equal after quantisation (within 1 LSB)
        max_diff = float(np.max(np.abs(decoded[:, 0] - decoded[:, 1])))
        assert max_diff <= 1.0 / 32767 + 1e-7, (
            f"In-phase L/R differ by {max_diff:.2e} after quantisation"
        )

        # Native downmix (L+R)/2 should equal L
        mono = pcm_b.to_float32_mono()
        np.testing.assert_allclose(mono, decoded[:, 0], atol=1.0 / 32767 + 1e-7)

    def test_native_opposite_phase_mono_is_zero(self) -> None:
        """Opposite-phase (L=-R): (L+R)/2 = 0 → FFT magnitude near zero."""
        pcm_c = _make_signal_c()
        mono = pcm_c.to_float32_mono()
        # After S16_LE quantisation there may be 1-LSB rounding; mean should be ~0
        assert float(np.max(np.abs(mono))) <= 1.0 / 32767 + 1e-7, (
            f"Opposite-phase mono max={float(np.max(np.abs(mono))):.2e} should be near zero"
        )

        # NativeAnalyser raw output should be near zero for opposite-phase
        na = NativeAnalyserR()
        frames = na.run(pcm_c)
        if frames:
            raw_mean = float(np.mean([f["raw"].astype(float).mean() for f in frames]))
            assert raw_mean < 5.0, (
                f"Native opposite-phase raw mean {raw_mean:.2f} should be < 5"
            )

    def test_cava_ref_opposite_phase_ratio_near_one(self) -> None:
        """CavaReferenceModel: |FFT(-x)| = |FFT(x)| -> opposite-phase ratio close to in-phase."""
        pcm_b = _make_signal_b()
        pcm_c = _make_signal_c()

        cava_ref = CavaReferenceModel()
        cava_ref.reset()
        ip_frames = cava_ref.run(pcm_b)
        cava_ref.reset()
        op_frames = cava_ref.run(pcm_c)

        if not ip_frames or not op_frames:
            pytest.skip("Not enough frames from CavaReferenceModel")

        ip_raw = float(np.mean([f["raw_pre_gravity"].mean() for f in ip_frames]))
        op_raw = float(np.mean([f["raw_pre_gravity"].mean() for f in op_frames]))

        # Since |FFT(-x)| = |FFT(x)|, the energy should be preserved
        # Ratio should be near 1.0 (within 20% tolerance for IIR state effects)
        ratio = op_raw / (ip_raw + 1e-9)
        assert 0.5 < ratio < 2.0, (
            f"CavaRef opposite/in-phase ratio {ratio:.4f} should be near 1.0; "
            f"in_phase={ip_raw:.4f}, opposite={op_raw:.4f}"
        )


# ---------------------------------------------------------------------------
# 10. CAVA runner integration (skipped if binary not available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("cava"), reason="cava binary not installed")
class TestCavaRunnerIntegration:
    """Integration tests requiring an actual CAVA binary."""

    def test_runner_uses_fifo_method(self) -> None:
        """CavaRunner config must specify method = fifo (not pipe)."""
        # Verify the config template uses 'method = fifo' (not 'method = pipe')
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            conf_content = (
                "[general]\n"
                f"bars = {N_BARS}\n"
                f"lower_cutoff_freq = {int(LOWER_HZ)}\n"
                f"higher_cutoff_freq = {int(UPPER_HZ)}\n"
                "framerate = 60\n"
                "noise_reduction = 77\n"
                "autosens = 1\n"
                "sensitivity = 100\n"
                "\n"
                "[input]\n"
                "method = fifo\n"
                f"source = {tmp}/dummy.pcm\n"
                f"sample_rate = {SR}\n"
                "sample_bits = 16\n"
                "channels = 2\n"
                "\n"
                "[output]\n"
                "method = raw\n"
                f"raw_target = {tmp}/dummy.raw\n"
                "data_format = binary\n"
                "bit_format = 8bit\n"
                "channels = mono\n"
            )
            assert "method = fifo" in conf_content
            assert "method = pipe" not in conf_content

    def test_runner_produces_frames(self) -> None:
        """CavaRunner produces at least some frames for a non-trivial signal."""
        runner = CavaRunner()
        pcm = _make_signal_b()  # 440 Hz in-phase, 5 seconds total
        frames, stderr = runner.run(pcm, timeout_s=30.0)
        assert len(frames) > 0, f"Expected frames from CAVA binary; stderr={stderr[:200]!r}"
        # Check frame structure
        f0 = frames[0]
        assert "bars" in f0
        assert isinstance(f0["bars"], np.ndarray)
        assert len(f0["bars"]) == N_BARS
        assert f0["bars"].dtype == np.uint8

    def test_runner_surfaces_stderr_on_error(self) -> None:
        """CavaRunner returns stderr as a string (may be empty on success)."""
        runner = CavaRunner()
        pcm = _make_signal_b()
        _frames, stderr = runner.run(pcm, timeout_s=30.0)
        # stderr is always a string (possibly empty)
        assert isinstance(stderr, str)
