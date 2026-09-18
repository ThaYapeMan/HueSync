"""Synthetic acceptance checks against the production V2 engine, not just the reference."""
import math
from unittest.mock import patch

import numpy as np
import pytest

from huesync import spectrum_engine
from huesync import v2_bars_pink as pink
from huesync.pcm_source import WINDOW_SIZE
from huesync.spectrum_engine import SharedAnalysis, V2SpectrumEngine


def run(engine, frames):
    pcm = np.zeros((480, 2), dtype=np.float32)
    return np.asarray([update.bars for update in engine.feed(
        pcm, SharedAnalysis(mag_frames=frames, pcm=pcm, sample_pos=0, n_samples=480),
    )])


def test_production_v2_pink_noise_spread_before_and_after():
    rng = np.random.default_rng(7)
    frames = [pink.synthetic_pink_magnitude(rng) for _ in range(600)]
    before = V2SpectrumEngine(34, 50, 12000)
    before._pink_compensation = np.ones(34)  # controlled baseline: remove only the new gain
    after = V2SpectrumEngine(34, 50, 12000)
    raw_mean = run(before, frames)[-300:].mean(axis=0)
    corrected_mean = run(after, frames)[-300:].mean(axis=0)
    raw_spread = 20 * math.log10(raw_mean.max() / raw_mean.min())
    corrected_spread = 20 * math.log10(corrected_mean.max() / corrected_mean.min())
    assert raw_spread > 10
    assert corrected_spread < 2
    assert corrected_mean.mean() > 0.5
    print(f'Production V2 pink spread: {raw_spread:.1f} dB -> {corrected_spread:.1f} dB')


def test_production_v2_bass_and_treble_remain_separate():
    engine = V2SpectrumEngine(34, 50, 12000)
    tone = np.zeros(pink.N_BINS)
    tone[round(80 * WINDOW_SIZE / 48000)] = 1
    bass = run(engine, [tone] * 50)[-1]
    assert bass[:3].max() > 0.9
    assert np.all(bass[17:] == 0)
    engine.reset()
    tone = np.zeros(pink.N_BINS)
    tone[round(8000 * WINDOW_SIZE / 48000)] = 1
    treble = run(engine, [tone] * 50)[-1]
    assert treble[-4:].max() > 0.9
    assert np.all(treble[:17] == 0)


def test_production_v2_table_limits_determinism_and_reset():
    engine = V2SpectrumEngine(34, 50, 12000)
    gain = engine._pink_compensation
    assert gain[0] == 1.0
    assert gain.min() >= 1.0
    assert gain.max() <= 12
    assert gain[-1] > gain[0]
    np.testing.assert_array_equal(gain, pink.derive_pink_compensation(34, 50, 12000))
    with patch('huesync.spectrum_engine.derive_pink_compensation') as derive:
        engine.reset()
        assert engine._pink_compensation is gain
        derive.assert_not_called()


@pytest.mark.parametrize('layout', [(10, 50, 10000), (34, 50, 12000), (60, 20, 20000),
                                    (10, 200, 8000), (34, 100, 16000), (60, 50, 12000)])
def test_production_and_derivation_use_identical_bin_ranges(layout):
    assert WINDOW_SIZE == pink.WINDOW_SIZE
    assert spectrum_engine._SAMPLE_RATE == pink.SAMPLE_RATE
    assert spectrum_engine._V2_NOISE_FLOOR == pink.NOISE_FLOOR
    assert spectrum_engine.bar_edges is pink.bar_edges
    with patch.object(pink, 'bar_edges', wraps=pink.bar_edges) as derive_edges:
        engine = V2SpectrumEngine(*layout)
        derive_edges.assert_called_once_with(*layout)
    expected = pink.bar_edges(*layout)
    # Probe the engine's actual mapping with every individual FFT bin. Recover
    # each bar's entire bin range, including its inclusive/exclusive boundaries.
    actual = [[] for _ in expected]
    for index in range(pink.N_BINS):
        magnitude = np.zeros(pink.N_BINS)
        magnitude[index] = 1
        for bar, value in enumerate(engine._mag_to_bar_floats(magnitude)):
            if value:
                actual[bar].append(index)
    assert actual == [list(range(lo, hi)) for lo, hi in expected]


def test_production_matches_verified_reference_chain():
    engine = V2SpectrumEngine(34, 50, 12000)
    reference = pink.V2Bars(34, 50, 12000)
    rng = np.random.default_rng(42)
    frames = [pink.synthetic_pink_magnitude(rng) for _ in range(100)]
    expected = [reference.feed(frame, 0.01).copy() for frame in frames]
    np.testing.assert_allclose(run(engine, frames), expected, rtol=1e-14, atol=1e-14)


def test_compensation_is_after_squelch():
    engine = V2SpectrumEngine(34, 50, 12000)
    frame = np.full(pink.N_BINS, pink.NOISE_FLOOR)
    assert np.all(run(engine, [frame]) == 0)
