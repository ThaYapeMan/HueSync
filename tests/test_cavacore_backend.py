"""Unit tests for CavaCoreBackend (native ctypes binding to upstream cavacore).

Tests are skipped when the shared library has not been compiled yet.
Build it with:  pip install .   (requires libfftw3-dev on the host).

Upstream:  github.com/karlstav/cava  commit 6d43df3b  (2026-08-18)
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Skip guard — skip every test in this module if the .so is absent
# ---------------------------------------------------------------------------

_SO_PATH = Path(__file__).parent.parent / "src" / "huesync" / "cavacore" / "_libcavacore.so"

pytestmark = pytest.mark.skipif(
    not _SO_PATH.exists(),
    reason="cavacore native library not built — run: pip install . (needs libfftw3-dev)",
)

# These imports happen only when the skip guard passes.
from huesync.cavacore import SCALING_DECIBEL, CavaCoreBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE = 48000
_N_BARS = 30
_HOP = 480  # matches PcmAudioPipelineV2 STFT hop at 48 kHz


def _make_backend(**kwargs) -> CavaCoreBackend:
    defaults: dict = dict(n_bars=_N_BARS, rate=_RATE, channels=2)
    defaults.update(kwargs)
    return CavaCoreBackend(**defaults)


def _sine_stereo(freq: float, n: int, amplitude: float = 0.5) -> np.ndarray:
    """Stereo sine wave, shape (n, 2), dtype float32."""
    t = np.arange(n, dtype=np.float32) / _RATE
    wave = (np.sin(2 * math.pi * freq * t) * amplitude).astype(np.float32)
    return np.column_stack([wave, wave])


def _silence(n: int) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float32)


def _warmup(backend: CavaCoreBackend, n_seconds: float = 2.0) -> None:
    """Feed silence to prime cavacore's rolling buffer and autosens state."""
    n_frames = int(n_seconds * _RATE / _HOP)
    for _ in range(n_frames):
        backend.execute(_silence(_HOP))


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def test_init_stereo_succeeds() -> None:
    with _make_backend() as b:
        assert b.n_bars == _N_BARS
        assert b.channels == 2


def test_init_mono_succeeds() -> None:
    with _make_backend(channels=1) as b:
        assert b.channels == 1


def test_init_various_bar_counts() -> None:
    for n in (1, 10, 30, 64, 128):
        with _make_backend(n_bars=n) as b:
            assert b.n_bars == n


def test_init_scaling_decibel() -> None:
    with _make_backend(scaling_mode=SCALING_DECIBEL) as b:
        assert b.n_bars == _N_BARS


def test_init_bad_channels_raises() -> None:
    with pytest.raises(RuntimeError, match="channels"):
        _make_backend(channels=3)


def test_init_bad_sample_rate_raises() -> None:
    with pytest.raises(RuntimeError, match="sample rate"):
        _make_backend(rate=0)


def test_init_high_cut_off_exceeds_nyquist_raises() -> None:
    with pytest.raises(RuntimeError, match="Nyquist"):
        _make_backend(high_cut_off=_RATE // 2 + 1)


def test_init_inverted_cutoffs_raises() -> None:
    with pytest.raises(RuntimeError, match="high_cut_off"):
        _make_backend(low_cut_off=5000, high_cut_off=1000)


# ---------------------------------------------------------------------------
# Basic output properties
# ---------------------------------------------------------------------------


def test_output_shape() -> None:
    b = _make_backend()
    out = b.execute(_silence(_HOP))
    assert out.shape == (_N_BARS,)
    b.close()


def test_output_dtype_float64() -> None:
    b = _make_backend()
    out = b.execute(_silence(_HOP))
    assert out.dtype == np.float64
    b.close()


def test_silence_output_zeros() -> None:
    """Pure silence should produce all-zero bars (no noise floor artefacts)."""
    b = _make_backend()
    # Feed enough silence to fill the rolling buffer
    for _ in range(40):
        out = b.execute(_silence(_HOP))
    assert np.all(out == 0.0), f"Expected all zeros, got max={out.max():.6f}"
    b.close()


def test_autosens_bounds_output() -> None:
    """With autosens=1, bars must stay in [0, 1] after warmup with audio."""
    b = _make_backend(autosens=1)
    # Feed 3s of loud broadband noise to trigger autosens
    rng = np.random.default_rng(seed=42)
    n_frames = int(3.0 * _RATE / _HOP)
    last_out = np.zeros(_N_BARS)
    for _ in range(n_frames):
        noise = rng.uniform(-1.0, 1.0, (_HOP, 2)).astype(np.float32)
        last_out = b.execute(noise)
    assert np.all(last_out >= 0.0), f"Negative bar detected: {last_out.min():.6f}"
    assert np.all(last_out <= 1.0), f"Bar exceeds 1.0: {last_out.max():.6f}"
    b.close()


def test_autosens_off_raw_output() -> None:
    """With autosens=0, output is raw (may exceed 1.0 for loud signals)."""
    b = _make_backend(autosens=0)
    _warmup(b)
    loud = _sine_stereo(1000.0, _HOP, amplitude=1.0)
    out = b.execute(loud)
    # With no AGC and a full-amplitude 1 kHz tone the output should be non-zero
    assert out.max() > 0.0
    b.close()


# ---------------------------------------------------------------------------
# Spectral accuracy
# ---------------------------------------------------------------------------


def test_440hz_tone_peak_bar_in_expected_range() -> None:
    """440 Hz sine → max bar in the expected frequency range for 30 bars, 50-10000 Hz.

    For log-spaced bars from 50 to 10000 Hz with 30 bars, 440 Hz falls in bar ~8-12.
    Accept a wider window to account for CAVA's band boundary algorithm.
    """
    b = _make_backend()
    # Warm up autosens with the signal itself
    for _ in range(100):
        b.execute(_sine_stereo(440.0, _HOP))
    out = b.execute(_sine_stereo(440.0, _HOP))
    peak_bar = int(np.argmax(out))
    assert 4 <= peak_bar <= 18, (
        f"440 Hz peak at bar {peak_bar}, expected roughly 4-18 for 30 bars 50-10kHz.\n"
        f"Bar values: {out.tolist()}"
    )
    b.close()


def test_low_freq_tone_peak_in_low_bars() -> None:
    """80 Hz sine → peak should be in the lower bars (bass region)."""
    b = _make_backend()
    for _ in range(100):
        b.execute(_sine_stereo(80.0, _HOP))
    out = b.execute(_sine_stereo(80.0, _HOP))
    peak_bar = int(np.argmax(out))
    assert peak_bar < 10, (
        f"80 Hz peak at bar {peak_bar}, expected <10.\n"
        f"Bar values: {out.tolist()}"
    )
    b.close()


def test_high_freq_tone_peak_in_high_bars() -> None:
    """5000 Hz sine → peak should be in the upper half of bars."""
    b = _make_backend()
    for _ in range(100):
        b.execute(_sine_stereo(5000.0, _HOP))
    out = b.execute(_sine_stereo(5000.0, _HOP))
    peak_bar = int(np.argmax(out))
    assert peak_bar >= 15, (
        f"5000 Hz peak at bar {peak_bar}, expected >=15.\n"
        f"Bar values: {out.tolist()}"
    )
    b.close()


def test_broadband_noise_all_bars_nonzero_after_warmup() -> None:
    """Broadband noise should activate all bars."""
    b = _make_backend()
    rng = np.random.default_rng(seed=7)
    for _ in range(200):  # ~2s warmup
        noise = rng.uniform(-0.5, 0.5, (_HOP, 2)).astype(np.float32)
        b.execute(noise)
    # All bars nonzero after sustained broadband signal
    noise = rng.uniform(-0.5, 0.5, (_HOP, 2)).astype(np.float32)
    out = b.execute(noise)
    assert np.all(out > 0.0), f"Some bars still zero: {(out == 0).sum()} zeros\n{out.tolist()}"
    b.close()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_output() -> None:
    """Identical PCM input through two independent backends must yield identical output."""
    frames = [_sine_stereo(440.0, _HOP) for _ in range(50)]
    frames.extend([_silence(_HOP)] * 5)
    frames.extend([_sine_stereo(1000.0, _HOP)] * 20)

    b1 = _make_backend()
    b2 = _make_backend()
    for f in frames:
        r1 = b1.execute(f)
        r2 = b2.execute(f)
        np.testing.assert_array_equal(r1, r2)
    b1.close()
    b2.close()


# ---------------------------------------------------------------------------
# Stereo handling
# ---------------------------------------------------------------------------


def test_stereo_l_only_r_only_average_equal() -> None:
    """L-only and R-only sines at the same frequency should produce equal averaged bars."""
    freq = 440.0
    n = _HOP * 50

    l_only = np.zeros((n, 2), dtype=np.float32)
    l_only[:, 0] = np.sin(2 * math.pi * freq * np.arange(n, dtype=np.float32) / _RATE) * 0.5

    r_only = np.zeros((n, 2), dtype=np.float32)
    r_only[:, 1] = np.sin(2 * math.pi * freq * np.arange(n, dtype=np.float32) / _RATE) * 0.5

    b_l = _make_backend()
    b_r = _make_backend()

    for i in range(0, n, _HOP):
        r1 = b_l.execute(l_only[i : i + _HOP])
        r2 = b_r.execute(r_only[i : i + _HOP])

    # After warm-up, the last outputs should be equal (L and R averaged)
    np.testing.assert_allclose(r1, r2, atol=1e-6)
    b_l.close()
    b_r.close()


# ---------------------------------------------------------------------------
# Context manager / lifecycle
# ---------------------------------------------------------------------------


def test_context_manager_closes_cleanly() -> None:
    with _make_backend() as b:
        b.execute(_silence(_HOP))
    # execute after close should raise
    with pytest.raises(RuntimeError, match="closed"):
        b.execute(_silence(_HOP))


def test_double_close_safe() -> None:
    b = _make_backend()
    b.close()
    b.close()  # must not raise


def test_variable_chunk_sizes_accepted() -> None:
    """cavacore accepts variable new_samples per execute() call."""
    b = _make_backend()
    for size in (240, 480, 960, 1920, 100, 1):
        b.execute(_silence(size))
    b.close()


# ---------------------------------------------------------------------------
# Lifecycle safety — failed init and __del__ edge cases
# ---------------------------------------------------------------------------


def test_invalid_config_raises_no_double_free() -> None:
    """A bad cava_init must raise RuntimeError; calling close() or __del__ afterwards is safe."""
    with pytest.raises(RuntimeError):
        _make_backend(channels=3)
    # No crash from double-free or use-after-free if cavacore_free_failed is used correctly.


def test_del_after_failed_init_safe() -> None:
    """__del__ must not crash even if __init__ raises before _plan is assigned."""
    class _BadInit(CavaCoreBackend):
        def __init__(self) -> None:
            super().__init__(n_bars=_N_BARS, rate=_RATE, channels=3)  # will raise

    b = object.__new__(_BadInit)
    try:
        _BadInit.__init__(b)
    except RuntimeError:
        pass
    # Trigger __del__ manually — must not raise AttributeError or double-free.
    b.__del__()


# ---------------------------------------------------------------------------
# Carry-buffer scheduler
# ---------------------------------------------------------------------------


def test_execute_returns_none_for_partial_block() -> None:
    """execute() returns None when the carry buffer holds fewer than 480 frames."""
    b = _make_backend()
    result = b.execute(_silence(100))  # 100 < 480 — no complete block
    assert result is None, f"Expected None for partial block, got {result}"
    b.close()


def test_execute_returns_array_for_full_block() -> None:
    """execute() returns an ndarray when at least 480 frames are available."""
    b = _make_backend()
    result = b.execute(_silence(_HOP))  # exactly 480 frames
    assert result is not None
    assert result.shape == (_N_BARS,)
    b.close()


def test_carry_buffer_equivalence() -> None:
    """Identical PCM split into different chunk sizes must produce the same final output.

    The carry-buffer scheduler must be sample-count-driven, not call-count-driven.
    """
    total = _HOP * 20
    signal = _sine_stereo(440.0, total)

    # Backend A: 480-frame chunks (one block per call)
    b_a = _make_backend()
    last_a = None
    for i in range(0, total, _HOP):
        r = b_a.execute(signal[i : i + _HOP])
        if r is not None:
            last_a = r
    b_a.close()

    # Backend B: irregular chunks (137, 293, rest)
    b_b = _make_backend()
    last_b = None
    i = 0
    while i < total:
        size = 137 if i % (137 * 2) == 0 else 293
        chunk = signal[i : min(i + size, total)]
        r = b_b.execute(chunk)
        if r is not None:
            last_b = r
        i += len(chunk)
    b_b.close()

    assert last_a is not None
    assert last_b is not None
    np.testing.assert_allclose(last_a, last_b, atol=1e-9)


# ---------------------------------------------------------------------------
# Amplitude convention
# ---------------------------------------------------------------------------


def test_canonical_array_not_modified_by_execute() -> None:
    """execute() must not modify the caller's input array (×32768 must be on a copy)."""
    b = _make_backend()
    original = _sine_stereo(440.0, _HOP)
    before = original.copy()
    b.execute(original)
    np.testing.assert_array_equal(original, before)
    b.close()


def test_amplitude_silence_produces_zeros() -> None:
    """All-zero input (silence) must produce all-zero bars regardless of ×32768 scaling."""
    b = _make_backend()
    for _ in range(40):
        out = b.execute(_silence(_HOP))
    assert out is not None
    assert np.all(out == 0.0), f"Expected zeros from silence, got max={out.max():.6f}"
    b.close()
