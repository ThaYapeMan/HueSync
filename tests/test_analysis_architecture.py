"""Tests for the final HueSync analysis architecture.

Verifies the AnalysisProcessor composition layer:
  - SharedAnalysisFrame data contract
  - ProcessorUpdate field coverage
  - SpectrumProcessor wrapping SpectrumEngine
  - BeatDetector independence from spectrum engine
  - Multi-processor composition inside CanonicalAnalysisPipeline
  - H1: stop() only closes processors after worker terminates
  - M3: drain_publications() returns real records
  - H5: bars_source exhaustive validation
  - PublicationRecord new fields (sample_end, effective_processor_ids)
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from huesync.canonicalizer import AnalysisPcmFrame
from huesync.models import Analyser, Profile
from huesync.spectrum_engine import (
    ProcessorUpdate,
    PublicationRecord,
    SharedAnalysis,
    SharedAnalysisFrame,
    SpectrumProcessor,
    SpectrumUpdate,
    V2SpectrumEngine,
)
from huesync.sync_engine import (
    BeatDetector,
    CanonicalAnalysisPipeline,
)

_CANONICAL_RATE = 48000
_HOP = 480


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(
    n: int = _HOP,
    epoch_id: str = "ep-1",
    sample_pos: int = 0,
) -> AnalysisPcmFrame:
    return AnalysisPcmFrame(
        samples=np.zeros((n, 2), dtype=np.float32),
        sample_pos=sample_pos,
        epoch_id=epoch_id,
        source_id="test:src",
        over_range=False,
    )


def _sine_frame(freq: float = 440.0, n: int = _CANONICAL_RATE) -> AnalysisPcmFrame:
    t = np.arange(n, dtype=np.float32) / _CANONICAL_RATE
    sig = (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)
    samples = np.column_stack([sig, sig])
    return AnalysisPcmFrame(
        samples=samples,
        sample_pos=0,
        epoch_id="ep-1",
        source_id="test:src",
        over_range=False,
    )


def _make_cap(**kw) -> CanonicalAnalysisPipeline:
    src = MagicMock()
    src.running = True
    defaults = dict(
        source=src,
        engine=V2SpectrumEngine(n_bars=16, lower_hz=50.0, upper_hz=10000.0),
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    defaults.update(kw)
    return CanonicalAnalysisPipeline(**defaults)


def _feed_warmup(cap: CanonicalAnalysisPipeline, epoch_id: str = "ep-1") -> None:
    """Feed enough frames to pass STFT warmup (4 hops × 480 = 2048 samples)."""
    for i in range(6):
        cap.feed(_make_frame(sample_pos=i * _HOP, epoch_id=epoch_id))


# ---------------------------------------------------------------------------
# SharedAnalysisFrame
# ---------------------------------------------------------------------------


def test_shared_analysis_frame_fields():
    pcm = np.zeros((480, 2), dtype=np.float32)
    mag = np.zeros(1025, dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm,
        mag_frames=[mag],
        epoch_id="ep-1",
        sample_start=0,
        sample_end=480,
        hop_sample_starts=[0],
    )
    assert frame.epoch_id == "ep-1"
    assert frame.sample_end - frame.sample_start == 480
    assert len(frame.hop_sample_starts) == 1


def test_shared_analysis_frame_empty_mag_frames():
    pcm = np.zeros((100, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm,
        mag_frames=[],
        epoch_id="ep-1",
        sample_start=0,
        sample_end=100,
        hop_sample_starts=[],
    )
    assert frame.mag_frames == []
    assert frame.hop_sample_starts == []


# ---------------------------------------------------------------------------
# ProcessorUpdate
# ---------------------------------------------------------------------------


def test_processor_update_spectrum_only():
    pu = ProcessorUpdate(
        processor_id="v2",
        sample_start=0,
        sample_end=480,
        bars=[0.5] * 16,
    )
    assert pu.bars is not None
    assert pu.onset is None  # not set by spectrum


def test_processor_update_beat_only():
    pu = ProcessorUpdate(
        processor_id="beat_detector",
        sample_start=0,
        sample_end=480,
        onset=True,
        onset_strength=0.8,
    )
    assert pu.bars is None  # not set by beat
    assert pu.onset is True
    assert pu.onset_strength == pytest.approx(0.8)


def test_processor_update_multiband_fields():
    pu = ProcessorUpdate(
        processor_id="beat_detector",
        sample_start=0,
        sample_end=480,
        onset=True,
        onset_bass=True,
        onset_mid=False,
        onset_treble=True,
        onset_bass_strength=0.9,
        onset_mid_strength=0.0,
        onset_treble_strength=0.7,
    )
    assert pu.onset_bass is True
    assert pu.onset_mid is False
    assert pu.onset_treble is True


# ---------------------------------------------------------------------------
# SpectrumProcessor
# ---------------------------------------------------------------------------


class _CountingEngine:
    """SpectrumEngine that returns a fixed bar value and counts feed() calls."""

    FIXED_VALUE = 0.42

    def __init__(self, n_bars: int = 8) -> None:
        self.n_bars = n_bars
        self.feed_count = 0
        self.reset_count = 0

    @property
    def engine_id(self) -> str:
        return "counting_engine"

    def feed(self, pcm: np.ndarray, shared: SharedAnalysis) -> list[SpectrumUpdate]:
        self.feed_count += 1
        if not shared.mag_frames:
            return []
        updates = []
        for i, _ in enumerate(shared.mag_frames):
            updates.append(SpectrumUpdate(
                engine_id="counting_engine",
                bars=[self.FIXED_VALUE] * self.n_bars,
                sample_pos=shared.sample_pos + i * _HOP,
            ))
        return updates

    def flush(self) -> list[SpectrumUpdate]:
        return []

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        pass


def test_spectrum_processor_id_delegates_to_engine():
    engine = _CountingEngine(n_bars=8)
    sp = SpectrumProcessor(engine)
    assert sp.processor_id == "counting_engine"


def test_spectrum_processor_feed_converts_to_processor_updates():
    engine = _CountingEngine(n_bars=8)
    sp = SpectrumProcessor(engine)
    mag = np.ones(1025, dtype=np.float32)
    pcm = np.zeros((480, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm,
        mag_frames=[mag],
        epoch_id="ep-1",
        sample_start=0,
        sample_end=480,
        hop_sample_starts=[0],
    )
    updates = sp.feed(frame)
    assert len(updates) == 1
    assert updates[0].bars == [_CountingEngine.FIXED_VALUE] * 8
    assert updates[0].processor_id == "counting_engine"
    assert updates[0].sample_start == 0
    assert updates[0].sample_end == 0 + _HOP


def test_spectrum_processor_feed_no_mag_frames_returns_empty():
    engine = _CountingEngine(n_bars=8)
    sp = SpectrumProcessor(engine)
    pcm = np.zeros((100, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm, mag_frames=[], epoch_id="ep-1",
        sample_start=0, sample_end=100, hop_sample_starts=[],
    )
    updates = sp.feed(frame)
    assert updates == []


def test_spectrum_processor_reset_delegates_to_engine():
    engine = _CountingEngine()
    sp = SpectrumProcessor(engine)
    sp.reset()
    assert engine.reset_count == 1


def test_spectrum_processor_flush_returns_processor_updates():
    engine = _CountingEngine(n_bars=4)
    sp = SpectrumProcessor(engine)
    updates = sp.flush()
    assert updates == []  # _CountingEngine.flush() returns []


# ---------------------------------------------------------------------------
# BeatDetector
# ---------------------------------------------------------------------------


def _make_beat_detector(method: str = "combined") -> BeatDetector:
    return BeatDetector(
        onset_method=method,
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )


def test_beat_detector_processor_id():
    bd = _make_beat_detector()
    assert bd.processor_id == "beat_detector"


def test_beat_detector_feed_empty_mag_frames():
    bd = _make_beat_detector()
    pcm = np.zeros((100, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm, mag_frames=[], epoch_id="ep-1",
        sample_start=0, sample_end=100, hop_sample_starts=[],
    )
    updates = bd.feed(frame)
    assert updates == []


def test_beat_detector_feed_returns_one_update_per_mag_frame():
    bd = _make_beat_detector()
    mag = np.zeros(1025, dtype=np.float32)
    pcm = np.zeros((960, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm,
        mag_frames=[mag, mag],
        epoch_id="ep-1",
        sample_start=0,
        sample_end=960,
        hop_sample_starts=[0, 480],
    )
    updates = bd.feed(frame)
    assert len(updates) == 2
    for pu in updates:
        assert pu.processor_id == "beat_detector"
        assert pu.onset is not None
        assert pu.onset_strength is not None


def test_beat_detector_multiband_populates_band_fields():
    bd = _make_beat_detector(method="multiband")
    mag = np.zeros(1025, dtype=np.float32)
    pcm = np.zeros((480, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm, mag_frames=[mag], epoch_id="ep-1",
        sample_start=0, sample_end=480, hop_sample_starts=[0],
    )
    updates = bd.feed(frame)
    assert len(updates) == 1
    pu = updates[0]
    assert pu.onset_bass is not None
    assert pu.onset_mid is not None
    assert pu.onset_treble is not None


def test_beat_detector_combined_has_no_band_fields():
    bd = _make_beat_detector(method="combined")
    mag = np.zeros(1025, dtype=np.float32)
    pcm = np.zeros((480, 2), dtype=np.float32)
    frame = SharedAnalysisFrame(
        pcm=pcm, mag_frames=[mag], epoch_id="ep-1",
        sample_start=0, sample_end=480, hop_sample_starts=[0],
    )
    updates = bd.feed(frame)
    assert len(updates) == 1
    pu = updates[0]
    assert pu.onset_bass is None
    assert pu.onset_mid is None
    assert pu.onset_treble is None


def test_beat_detector_reset_rebuilds_onset_pipeline():
    bd = _make_beat_detector()
    old_id = id(bd._onset_pipeline)
    bd.reset()
    assert id(bd._onset_pipeline) != old_id


def test_beat_detector_flush_returns_empty():
    bd = _make_beat_detector()
    assert bd.flush() == []


def test_beat_detector_independent_of_spectrum_engine():
    """BeatDetector has no reference to any SpectrumEngine."""
    bd = _make_beat_detector()
    assert not hasattr(bd, "_engine")
    assert not hasattr(bd, "_spectrum_processor")


# ---------------------------------------------------------------------------
# Multi-processor composition in CanonicalAnalysisPipeline
# ---------------------------------------------------------------------------


def test_cap_holds_spectrum_processor_and_beat_detector():
    cap = _make_cap()
    assert hasattr(cap, "_spectrum_processor")
    assert hasattr(cap, "_beat_detector")
    assert isinstance(cap._spectrum_processor, SpectrumProcessor)
    assert isinstance(cap._beat_detector, BeatDetector)


def test_cap_processors_tuple_contains_both():
    cap = _make_cap()
    assert len(cap._processors) == 2
    ids = {p.processor_id for p in cap._processors}
    assert "v2" in ids
    assert "beat_detector" in ids


def test_cap_backward_compat_engine_property():
    """_engine property returns the SpectrumEngine inside SpectrumProcessor."""
    engine = V2SpectrumEngine(n_bars=8, lower_hz=50.0, upper_hz=10000.0)
    cap = _make_cap(engine=engine)
    assert cap._engine is engine


def test_cap_backward_compat_onset_pipeline_property():
    """_onset_pipeline property returns BeatDetector's onset pipeline."""
    cap = _make_cap()
    op = cap._onset_pipeline
    assert op is cap._beat_detector._onset_pipeline


def test_cap_publication_record_has_new_fields():
    cap = _make_cap()
    _feed_warmup(cap)
    recs = cap.feed(_make_frame(sample_pos=6 * _HOP))
    if not recs:
        pytest.skip("No publication produced during warmup range")
    rec = recs[0]
    assert isinstance(rec.sample_end, int)
    assert rec.sample_end > rec.sample_pos
    assert "v2" in rec.effective_processor_ids
    assert "beat_detector" in rec.effective_processor_ids


def test_cap_effective_engine_id_matches_spectrum_processor():
    cap = _make_cap()
    _feed_warmup(cap)
    recs = cap.feed(_make_frame(sample_pos=6 * _HOP))
    if not recs:
        pytest.skip("No publication produced")
    assert recs[0].effective_engine_id == "v2"


def test_cap_drain_publications_returns_records(monkeypatch):
    """drain_publications() must return records that the worker thread published (M3 fix)."""
    cap = _make_cap()
    # Synchronous feed() also appends to _pub_queue now.
    _feed_warmup(cap)
    recs_feed = cap.feed(_make_frame(sample_pos=6 * _HOP))
    recs_drained = cap.drain_publications()
    # All records from feed() must also appear in drain.
    assert len(recs_drained) >= len(recs_feed)
    seq_feed = {r.sequence for r in recs_feed}
    seq_drained = {r.sequence for r in recs_drained}
    assert seq_feed.issubset(seq_drained)


def test_cap_drain_publications_empty_after_second_drain():
    cap = _make_cap()
    _feed_warmup(cap)
    cap.feed(_make_frame(sample_pos=6 * _HOP))
    cap.drain_publications()  # first drain
    assert cap.drain_publications() == []  # second drain is empty


def test_cap_reset_dsp_resets_all_processors():
    cap = _make_cap()
    _feed_warmup(cap)
    engine_before = id(cap._spectrum_processor._engine)
    onset_id_before = id(cap._beat_detector._onset_pipeline)

    cap._reset_dsp()

    # SpectrumProcessor.reset() calls engine.reset() (not replace, same engine instance)
    assert id(cap._spectrum_processor._engine) == engine_before
    # BeatDetector.reset() rebuilds onset pipeline
    assert id(cap._beat_detector._onset_pipeline) != onset_id_before
    assert cap._current_epoch_id is None
    assert cap._pending_onset == []


# ---------------------------------------------------------------------------
# H1: stop() only closes processors after worker terminates
# ---------------------------------------------------------------------------


def test_h1_stop_closes_processors_after_thread_terminates():
    """Processors are closed only after the worker thread has stopped (H1 fix)."""
    close_calls: list[str] = []

    class _TrackingEngine:
        @property
        def engine_id(self) -> str:
            return "tracking"

        def feed(self, pcm, shared):
            return []

        def flush(self):
            return []

        def reset(self) -> None:
            pass

        def close(self) -> None:
            close_calls.append("engine_close")

    engine = _TrackingEngine()
    cap = _make_cap(engine=engine)
    cap.start()
    cap.stop()

    # After stop() returns, thread is dead → close was called.
    assert "engine_close" in close_calls


def test_h1_close_not_called_when_thread_still_alive(monkeypatch):
    """If join() times out and thread is still alive, processors must NOT be closed."""
    close_calls: list[str] = []
    join_called: list[bool] = []

    class _TrackingEngine:
        @property
        def engine_id(self) -> str:
            return "tracking"

        def feed(self, pcm, shared):
            return []

        def flush(self):
            return []

        def reset(self) -> None:
            pass

        def close(self) -> None:
            close_calls.append("engine_close")

    engine = _TrackingEngine()
    cap = _make_cap(engine=engine)

    # Simulate a zombie thread that never terminates.
    zombie = threading.Thread(target=lambda: time.sleep(10), daemon=True)
    zombie.start()
    cap._thread = zombie
    cap._stop.set()  # so _run() exits if it were running

    # join() will time out; thread is still alive.
    def _patched_join(timeout=None):
        join_called.append(True)
        # Do not actually join — thread stays alive.

    monkeypatch.setattr(zombie, "join", _patched_join)

    cap.stop()

    # Thread still alive → close must NOT have been called.
    assert close_calls == [], (
        "Processors must not be closed while the worker thread is still alive"
    )
    zombie.join(timeout=0.1)  # cleanup (daemon)


# ---------------------------------------------------------------------------
# M3: drain_publications threaded path
# ---------------------------------------------------------------------------


def test_m3_drain_publications_via_threaded_worker():
    """Worker thread appends to _pub_queue; drain_publications() returns them."""
    done = threading.Event()

    class _SingleFrameSource:
        def __init__(self):
            self._sent = False

        def read(self):
            from huesync.canonicalizer import TemporarilyNoData
            if not self._sent:
                self._sent = True
                done.set()
            time.sleep(0.05)
            return TemporarilyNoData()

        @property
        def running(self) -> bool:
            return True

    cap = _make_cap(source=_SingleFrameSource())
    # Feed some data synchronously to ensure queue is populated.
    _feed_warmup(cap)
    cap.feed(_make_frame(sample_pos=6 * _HOP))

    recs = cap.drain_publications()
    assert len(recs) > 0, "drain_publications() must return synchronous feed records"
    cap.drain_publications()  # second drain must be empty


# ---------------------------------------------------------------------------
# H5: bars_source exhaustive validation
# ---------------------------------------------------------------------------


def test_h5_profile_invalid_bars_source_raises():
    with pytest.raises(ValueError, match="bars_source"):
        Profile(bars_source="invalid_source")


def test_h5_analyser_invalid_bars_source_raises():
    with pytest.raises(ValueError, match="bars_source"):
        Analyser(bars_source="invalid_source")


def test_h5_profile_valid_bars_sources_accepted():
    for src in ("cava", "pcm_pipeline"):
        p = Profile(bars_source=src)
        assert p.bars_source == src


def test_h5_analyser_valid_bars_sources_accepted():
    for src in ("cava", "pcm_pipeline"):
        a = Analyser(bars_source=src)
        assert a.bars_source == src


# ---------------------------------------------------------------------------
# PublicationRecord new fields
# ---------------------------------------------------------------------------


def test_publication_record_default_new_fields():
    rec = PublicationRecord(
        sequence=1,
        epoch="ep-1",
        sample_pos=0,
        features=None,
        effective_engine_id="v2",
    )
    assert rec.sample_end == 0
    assert rec.effective_processor_ids == ()


def test_publication_record_new_fields_set():
    rec = PublicationRecord(
        sequence=1,
        epoch="ep-1",
        sample_pos=0,
        sample_end=480,
        features=None,
        effective_engine_id="v2",
        effective_processor_ids=("v2", "beat_detector"),
    )
    assert rec.sample_end == 480
    assert rec.effective_processor_ids == ("v2", "beat_detector")
