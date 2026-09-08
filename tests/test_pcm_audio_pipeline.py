"""Tests for PcmAudioPipeline — source-agnostic AudioPipeline implementation.

Uses a stub PcmSource that feeds pre-defined samples so no real pipe or
shared-memory segment is needed.
"""

from __future__ import annotations

import time

import numpy as np

from huesync.pcm_source import WINDOW_SIZE, PcmSource
from huesync.sync_engine import PcmAudioPipeline
from huesync.types import AudioFeatures

# ---------------------------------------------------------------------------
# Stub PcmSource
# ---------------------------------------------------------------------------


class _StubSource:
    """Feeds a fixed sample array once, then returns empty arrays.

    Satisfies the PcmSource Protocol at runtime.
    """

    def __init__(self, samples: np.ndarray, sample_rate: int = 44100) -> None:
        self._samples = samples
        self._sample_rate = sample_rate
        self._consumed = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read_new(self) -> np.ndarray:
        if not self._consumed:
            self._consumed = True
            return self._samples
        return np.empty(0, dtype=np.float32)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def running(self) -> bool:
        return True


assert isinstance(_StubSource(np.array([], dtype=np.float32)), PcmSource)


def _make_pipeline(
    source: _StubSource,
    bars: int = 30,
    onset_method: str = "combined",
) -> PcmAudioPipeline:
    return PcmAudioPipeline(
        source=source,
        bars=bars,
        lower_cutoff_freq=50,
        higher_cutoff_freq=10000,
        onset_method=onset_method,
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )


def _feed_and_collect(
    pipeline: PcmAudioPipeline,
    timeout: float = 2.0,
) -> AudioFeatures | None:
    """Start pipeline, wait for the first non-None latest(), then stop."""
    pipeline.start()
    deadline = time.monotonic() + timeout
    result: AudioFeatures | None = None
    while time.monotonic() < deadline:
        f = pipeline.latest()
        if f is not None:
            result = f
            break
        time.sleep(0.005)
    pipeline.stop()
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pipeline_produces_audio_features() -> None:
    """Pipeline must return an AudioFeatures after processing enough samples."""
    silence = np.zeros(WINDOW_SIZE * 2, dtype=np.float32)
    src = _StubSource(silence)
    features = _feed_and_collect(_make_pipeline(src))
    assert features is not None


def test_bars_length_matches_n_bars() -> None:
    silence = np.zeros(WINDOW_SIZE * 2, dtype=np.float32)
    for n in (10, 20, 30):
        src = _StubSource(silence)
        features = _feed_and_collect(_make_pipeline(src, bars=n))
        assert features is not None
        assert len(features.bars) == n, f"expected {n} bars, got {len(features.bars)}"


def test_bars_values_in_range() -> None:
    """All bar values must be in [0.0, 1.0]."""
    rng = np.random.default_rng(0)
    signal = rng.standard_normal(WINDOW_SIZE * 4).astype(np.float32)
    src = _StubSource(signal)
    features = _feed_and_collect(_make_pipeline(src))
    assert features is not None
    for v in features.bars:
        assert 0.0 <= v <= 1.0, f"bar value {v} out of range"


def test_silence_gives_zero_bars() -> None:
    """Complete silence must produce all-zero bars (BandNormaliser silence gate)."""
    silence = np.zeros(WINDOW_SIZE * 2, dtype=np.float32)
    src = _StubSource(silence)
    features = _feed_and_collect(_make_pipeline(src))
    assert features is not None
    assert all(v == 0.0 for v in features.bars), "silence should produce all-zero bars"


def test_band_aggregates_computed() -> None:
    """bass, mid, full, centroid must all be present and in [0, 1]."""
    rng = np.random.default_rng(1)
    signal = rng.standard_normal(WINDOW_SIZE * 4).astype(np.float32) * 0.1
    src = _StubSource(signal)
    features = _feed_and_collect(_make_pipeline(src))
    assert features is not None
    for attr in ("bass", "mid", "full", "centroid"):
        v = getattr(features, attr)
        assert 0.0 <= v <= 1.0, f"{attr}={v} out of range"
    assert features.bass <= features.mid <= features.full


def test_multiband_onset_fields_populated() -> None:
    """Multiband method must populate onset_bass/mid/treble fields."""
    rng = np.random.default_rng(2)
    signal = rng.standard_normal(WINDOW_SIZE * 6).astype(np.float32)
    src = _StubSource(signal)
    pipeline = _make_pipeline(src, onset_method="multiband")
    features = _feed_and_collect(pipeline)
    assert features is not None
    # Fields exist and are bool — value depends on signal, not tested here.
    assert isinstance(features.onset_bass, bool)
    assert isinstance(features.onset_mid, bool)
    assert isinstance(features.onset_treble, bool)


def test_onset_consistent_with_multiband() -> None:
    """With multiband, features.onset == onset_bass OR onset_mid OR onset_treble."""
    rng = np.random.default_rng(3)
    signal = rng.standard_normal(WINDOW_SIZE * 6).astype(np.float32)

    # Run multiple times to catch frames with varying onset state.
    collected: list[AudioFeatures] = []
    src = _StubSource(signal)
    pipeline = _make_pipeline(src, onset_method="multiband")
    pipeline.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        f = pipeline.latest()
        if f is not None and f not in collected:
            collected.append(f)
        if len(collected) >= 3:
            break
        time.sleep(0.01)
    pipeline.stop()

    for f in collected:
        expected = f.onset_bass or f.onset_mid or f.onset_treble
        assert f.onset == expected


def test_superflux_method_produces_features() -> None:
    rng = np.random.default_rng(4)
    signal = rng.standard_normal(WINDOW_SIZE * 6).astype(np.float32)
    src = _StubSource(signal)
    features = _feed_and_collect(_make_pipeline(src, onset_method="superflux"))
    assert features is not None
    assert isinstance(features.onset, bool)


def test_stop_is_clean() -> None:
    """stop() must return within 3 seconds even when the source is exhausted."""
    silence = np.zeros(WINDOW_SIZE * 2, dtype=np.float32)
    src = _StubSource(silence)
    pipeline = _make_pipeline(src)
    pipeline.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    pipeline.stop()
    assert time.monotonic() - t0 < 3.0
