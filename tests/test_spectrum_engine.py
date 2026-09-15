"""Unit tests for spectrum_engine.py: registry, V2 engine, and engine-agnostic CAP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from huesync.canonicalizer import AnalysisPcmFrame
from huesync.models import Profile
from huesync.pcm_source import WINDOW_SIZE
from huesync.spectrum_engine import (
    ENGINES,
    VALID_ENGINE_IDS,
    SharedAnalysis,
    SpectrumUpdate,
    V2SpectrumEngine,
    make_spectrum_engine,
)
from huesync.sync_engine import CanonicalAnalysisPipeline, SyncEngine

# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_valid_engine_ids_contains_v2_and_cavacore():
    assert "v2" in VALID_ENGINE_IDS
    assert "cavacore" in VALID_ENGINE_IDS


def test_engines_dict_keys_match_valid_ids():
    assert frozenset(ENGINES.keys()) == VALID_ENGINE_IDS


def test_make_spectrum_engine_v2_returns_correct_id():
    engine = make_spectrum_engine("v2", n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    assert engine.engine_id == "v2"


def test_make_spectrum_engine_unknown_raises_key_error():
    with pytest.raises(KeyError):
        make_spectrum_engine("nonexistent_engine", n_bars=10, lower_hz=50.0, upper_hz=10000.0)


def test_v2_check_available_returns_true():
    assert ENGINES["v2"].check_available() is True


# ---------------------------------------------------------------------------
# V2SpectrumEngine unit tests
# ---------------------------------------------------------------------------


def _make_mag(n_bins: int, energy: float = 0.1) -> np.ndarray:
    mag = np.zeros(n_bins, dtype=np.float32)
    mag[n_bins // 4] = energy   # single tone in the middle of the spectrum
    return mag


N_BINS = WINDOW_SIZE // 2 + 1


def test_v2_engine_feed_returns_one_update_per_mag_frame():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    mag1 = _make_mag(N_BINS)
    mag2 = _make_mag(N_BINS)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(
        mag_frames=[mag1, mag2],
        pcm=pcm,
        sample_pos=0,
        n_samples=480,
    )
    updates = engine.feed(pcm, shared)
    assert len(updates) == 2


def test_v2_engine_update_has_correct_engine_id_and_bar_count():
    engine = V2SpectrumEngine(n_bars=15, lower_hz=50.0, upper_hz=10000.0)
    mag = _make_mag(N_BINS)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(mag_frames=[mag], pcm=pcm, sample_pos=0, n_samples=480)
    updates = engine.feed(pcm, shared)
    assert updates[0].engine_id == "v2"
    assert len(updates[0].bars) == 15


def test_v2_engine_bars_in_unit_range():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    mag = _make_mag(N_BINS, energy=1.0)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(mag_frames=[mag], pcm=pcm, sample_pos=0, n_samples=480)
    updates = engine.feed(pcm, shared)
    for bar in updates[0].bars:
        assert 0.0 <= bar <= 1.0, f"bar out of range: {bar}"


def test_v2_engine_peak_ema_initialised_after_first_frame():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    assert engine.v2_peak_ema is None
    mag = _make_mag(N_BINS, energy=0.5)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(mag_frames=[mag], pcm=pcm, sample_pos=0, n_samples=480)
    engine.feed(pcm, shared)
    assert engine.v2_peak_ema is not None
    assert engine.v2_peak_ema > 0.0


def test_v2_engine_reset_clears_state():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    mag = _make_mag(N_BINS)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(mag_frames=[mag], pcm=pcm, sample_pos=0, n_samples=480)
    engine.feed(pcm, shared)
    assert engine.v2_peak_ema is not None
    engine.reset()
    assert engine.v2_peak_ema is None
    assert engine.v2_bar_smooth is None


def test_v2_engine_flush_returns_empty():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    assert engine.flush() == []


def test_v2_engine_silence_yields_zero_bars():
    engine = V2SpectrumEngine(n_bars=10, lower_hz=50.0, upper_hz=10000.0)
    mag = np.zeros(N_BINS, dtype=np.float32)
    pcm = np.zeros((480, 2), dtype=np.float32)
    shared = SharedAnalysis(mag_frames=[mag], pcm=pcm, sample_pos=0, n_samples=480)
    updates = engine.feed(pcm, shared)
    assert all(b == 0.0 for b in updates[0].bars)


# ---------------------------------------------------------------------------
# Engine-agnostic CanonicalAnalysisPipeline test with a dummy third engine
# ---------------------------------------------------------------------------


class _DummySpectrumEngine:
    """Minimal third engine that always returns flat bars at 0.5."""

    FIXED_VALUE: float = 0.5

    def __init__(self, n_bars: int) -> None:
        self._n_bars = n_bars
        self._call_count = 0

    @property
    def engine_id(self) -> str:
        return "dummy_test_engine"

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        self._call_count += 1
        return [SpectrumUpdate(
            engine_id=self.engine_id,
            bars=[self.FIXED_VALUE] * self._n_bars,
            sample_pos=shared.sample_pos,
        )]

    def flush(self) -> list[SpectrumUpdate]:
        return []

    def reset(self) -> None:
        self._call_count = 0

    def close(self) -> None:
        pass


class _NullSource:
    """Stub source for synchronous (no-thread) use."""

    @property
    def running(self) -> bool:
        return False

    def read(self) -> None:
        raise RuntimeError("_NullSource.read() must not be called in synchronous mode")


def _make_hop_frame(epoch_id: str = "epoch-1", sample_pos: int = 0) -> AnalysisPcmFrame:
    return AnalysisPcmFrame(
        samples=np.zeros((480, 2), dtype=np.float32),
        sample_pos=sample_pos,
        epoch_id=epoch_id,
        source_id="test",
        over_range=False,
    )


def test_dummy_engine_can_be_used_with_canonical_pipeline():
    """A third engine satisfying the SpectrumEngine protocol works without CAP changes."""
    n_bars = 8
    engine = _DummySpectrumEngine(n_bars=n_bars)
    pipeline = CanonicalAnalysisPipeline(
        source=_NullSource(),
        engine=engine,
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )

    # Feed enough frames to warm up the STFT (hop = 480, window = 2048 → warmup ≈ 4 frames)
    recs = []
    for i in range(6):
        recs.extend(pipeline.feed(_make_hop_frame(sample_pos=i * 480)))

    assert len(recs) > 0, "expected at least one publication after warmup"
    for rec in recs:
        assert rec.effective_engine_id == "dummy_test_engine"
        assert len(rec.features.bars) == n_bars
        assert all(abs(b - _DummySpectrumEngine.FIXED_VALUE) < 1e-9 for b in rec.features.bars)


def test_dummy_engine_epoch_transition_resets_engine():
    engine = _DummySpectrumEngine(n_bars=5)
    pipeline = CanonicalAnalysisPipeline(
        source=_NullSource(),
        engine=engine,
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    # Feed one epoch.
    for i in range(5):
        pipeline.feed(_make_hop_frame(epoch_id="epoch-A", sample_pos=i * 480))

    # Feed a new epoch — triggers reset (call_count goes to 0) then new calls.
    for i in range(3):
        pipeline.feed(_make_hop_frame(epoch_id="epoch-B", sample_pos=i * 480))

    # After reset, call_count restarted at 0 on the first epoch-B frame.
    assert engine._call_count == 3


def test_sync_engine_replace_analyser():
    """SyncEngine.replace_analyser() swaps the analyser without raising."""
    profile = Profile(id="p1", name="test", effect_type="spectrum_rgb", bars=10,
                      bars_source="pcm_pipeline", spectrum_backend="v2")

    # Build a minimal SyncEngine with a mock analyser.
    old_analyser = MagicMock()
    old_analyser.latest.return_value = None

    new_analyser = MagicMock()
    new_analyser.latest.return_value = None

    with patch("huesync.sync_engine.CavaPipeline"):
        engine = SyncEngine(fifo_path=None, profile=profile, analyser=old_analyser)

    engine.replace_analyser(new_analyser)

    old_analyser.stop.assert_called_once()
    new_analyser.start.assert_called_once()
    assert engine._analyser is new_analyser
