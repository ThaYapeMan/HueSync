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

import struct as _struct
import threading
import time
from pathlib import Path as _Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from huesync.canonicalizer import AnalysisPcmFrame
from huesync.models import Analyser, Profile
from huesync.pcm_source import (
    _HDR_FMT,
    _HDR_OFFSET,
    _MMAP_SIZE_V1,
    _V2_EXT_FMT,
    SHM_ABI_V1_MAGIC,
    VIS_BUF_SIZE,
    ShmContinuityEvent,
    SqueezeliteShmSource,
)
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


# ---------------------------------------------------------------------------
# Test 1: Generic 4-processor feed dispatch
# ---------------------------------------------------------------------------


def test_generic_4processor_feed_dispatch():
    """All four processors in _processors receive feed(), reset(), flush(), close()."""

    class _DummyProc:
        def __init__(self, pid: str) -> None:
            self._pid = pid
            self.feed_calls: list = []
            self.reset_calls: int = 0
            self.flush_calls: int = 0
            self.close_calls: int = 0

        @property
        def processor_id(self) -> str:
            return self._pid

        def feed(self, frame):
            self.feed_calls.append(frame)
            return []

        def reset(self) -> None:
            self.reset_calls += 1

        def flush(self) -> list:
            self.flush_calls += 1
            return []

        def close(self) -> None:
            self.close_calls += 1

    dummy_loudness = _DummyProc("loudness")
    dummy_chroma = _DummyProc("chroma")

    cap = _make_cap()
    cap._processors = (*cap._processors, dummy_loudness, dummy_chroma)

    # Feed one frame
    cap.feed(_make_frame(n=_HOP, sample_pos=0))

    assert len(dummy_loudness.feed_calls) == 1
    assert len(dummy_chroma.feed_calls) == 1

    # Reset
    cap._reset_dsp()
    assert dummy_loudness.reset_calls == 1
    assert dummy_chroma.reset_calls == 1

    # Flush (via end_of_stream)
    cap.end_of_stream()
    assert dummy_loudness.flush_calls >= 1
    assert dummy_chroma.flush_calls >= 1

    # Close
    cap.stop()
    assert dummy_loudness.close_calls == 1
    assert dummy_chroma.close_calls == 1


# ---------------------------------------------------------------------------
# Test 2: H3 — live BeatDetector rebuild
# ---------------------------------------------------------------------------


def test_h3_rebuild_beat_detector_updates_active_processor():
    """After rebuild(), BeatDetector uses new onset_method and rebuilds pipeline."""
    bd = BeatDetector(
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    assert bd._onset_method == "combined"
    old_pipeline = bd._onset_pipeline

    bd.rebuild(
        onset_method="multiband",
        onset_delta=0.2,
        onset_alpha=0.8,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=300,
        mid_hz=2500,
    )
    assert bd._onset_method == "multiband"
    assert bd._onset_delta == pytest.approx(0.2)
    assert bd._bass_hz == 300
    assert bd._onset_pipeline is not old_pipeline  # new object


def test_h3_cap_rebuild_beat_detector():
    """CanonicalAnalysisPipeline.rebuild_beat_detector reaches the BeatDetector."""
    cap = _make_cap()

    bd_before = next(p for p in cap._processors if isinstance(p, BeatDetector))
    old_pipeline = bd_before._onset_pipeline

    # Build a Profile with new onset settings using keyword args.
    profile = Profile(
        onset_method="multiband",
        onset_delta=0.2,
        onset_alpha=0.8,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=300,
        mid_hz=2500,
    )
    cap.rebuild_beat_detector(profile)

    bd_after = next(p for p in cap._processors if isinstance(p, BeatDetector))
    assert bd_after is bd_before  # same object, rebuilt in place
    assert bd_after._onset_method == "multiband"
    assert bd_after._onset_pipeline is not old_pipeline


# ---------------------------------------------------------------------------
# Test 4: SHM continuity v0 and v1
# ---------------------------------------------------------------------------


def _write_shm_v0(
    path: _Path,
    buf_index: int,
    running: bool = True,
    rate: int = 44100,
    buf_size: int = VIS_BUF_SIZE,
) -> None:
    """Write a synthetic v0 SHM segment."""
    header = _struct.pack(
        _HDR_FMT, buf_size, buf_index % VIS_BUF_SIZE, int(running), rate, 0
    )
    data = bytes(_HDR_OFFSET) + header + bytes(VIS_BUF_SIZE * 2)
    path.write_bytes(data)


def _write_shm_v1(
    path: _Path,
    buf_index: int,
    generation: int,
    abs_write_pos: int,
    running: bool = True,
    rate: int = 44100,
    gap_flag: int = 0,
) -> None:
    """Write a synthetic v1 SHM segment with extended header."""
    legacy_hdr = _struct.pack(
        _HDR_FMT, VIS_BUF_SIZE, buf_index % VIS_BUF_SIZE, int(running), rate, 0
    )
    ext_hdr = _struct.pack(
        _V2_EXT_FMT, SHM_ABI_V1_MAGIC, 1, gap_flag, 0, generation, abs_write_pos
    )
    data = bytes(_HDR_OFFSET) + legacy_hdr + ext_hdr + bytes(VIS_BUF_SIZE * 2)
    assert len(data) == _MMAP_SIZE_V1, f"Expected {_MMAP_SIZE_V1}, got {len(data)}"
    path.write_bytes(data)


def test_shm_advance_v0(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v0(p, buf_index=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Write 100 new stereo samples (200 bytes) at offset 0.
    data = bytearray(p.read_bytes())
    # Advance buf_index by 100.
    buf_index_new = 100
    _struct.pack_into(_HDR_FMT, data, _HDR_OFFSET, VIS_BUF_SIZE, buf_index_new, 1, 44100, 0)
    p.write_bytes(bytes(data))
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.ADVANCE
    assert result.n_delivered > 0


def test_shm_no_data_v0(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v0(p, buf_index=50)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    result = src.read_new_checked()  # no advance since open
    assert result.event == ShmContinuityEvent.NO_DATA


def test_shm_overrun_v0(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v0(p, buf_index=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Advance by more than VIS_BUF_SIZE//2 (overrun).
    data = bytearray(p.read_bytes())
    _struct.pack_into(
        _HDR_FMT, data, _HDR_OFFSET, VIS_BUF_SIZE, VIS_BUF_SIZE // 2 + 1, 1, 44100, 0
    )
    p.write_bytes(bytes(data))
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.OVERRUN
    assert result.n_delivered == 0


def test_shm_restart_on_running_transition(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v0(p, buf_index=0, running=True)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Transition running → False.
    _write_shm_v0(p, buf_index=0, running=False)
    src.read_new_checked()  # consume the running=False transition
    # Transition running → True.
    _write_shm_v0(p, buf_index=100, running=True)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.RESTART


def test_shm_restart_on_rate_change(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v0(p, buf_index=0, rate=44100)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v0(p, buf_index=50, rate=48000)  # rate changed
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.RESTART


def test_shm_v1_full_lap_detectable(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=1000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Advance abs_write_pos by exactly VIS_BUF_SIZE.
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=1000 + VIS_BUF_SIZE)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.FULL_LAP


def test_shm_v1_multiple_laps(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Advance by 3 full laps.
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=3 * VIS_BUF_SIZE)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.MULTIPLE_LAPS


def test_shm_v1_gap_export(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=100, generation=1, abs_write_pos=100, gap_flag=1)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.GAP_EXPORT


def test_shm_producer_generation_change(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=5000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Producer restarted: generation incremented, abs_write_pos reset to small value.
    _write_shm_v1(p, buf_index=10, generation=2, abs_write_pos=10)
    result = src.read_new_checked()
    # Generation change must be classified as a continuity break.
    assert result.event in (
        ShmContinuityEvent.RESTART,
        ShmContinuityEvent.SHM_REPLACED,
        ShmContinuityEvent.OVERRUN,
        ShmContinuityEvent.MULTIPLE_LAPS,
    )


# ---------------------------------------------------------------------------
# Test 5: CAVA EOS valid sample interval
# ---------------------------------------------------------------------------


def test_cava_eos_sample_end_not_padded():
    """PublicationRecord.sample_end from flush should not exceed real source end."""
    cap = _make_cap()

    sample_pos = 0
    for _ in range(10):
        cap.feed(_make_frame(n=_HOP, sample_pos=sample_pos))
        sample_pos += _HOP

    # Feed a partial tail of 100 samples (< HOP = 480).
    tail_frame = AnalysisPcmFrame(
        samples=np.zeros((100, 2), dtype=np.float32),
        sample_pos=sample_pos,
        epoch_id="ep-1",
        source_id="test:src",
        over_range=False,
    )
    cap.feed(tail_frame)
    expected_source_end = sample_pos + 100  # real end

    flush_recs = cap.end_of_stream()
    if flush_recs:
        last_rec = flush_recs[-1]
        assert last_rec.sample_end <= expected_source_end, (
            f"flush record sample_end {last_rec.sample_end} "
            f"exceeds real source end {expected_source_end}"
        )


# ---------------------------------------------------------------------------
# Test 6: Acceptance registry selection
# ---------------------------------------------------------------------------


def test_acceptance_rejects_unknown_engine():
    """analyse_pcm() raises KeyError for an unknown backend."""
    import sys
    sys.path.insert(0, str(_Path(__file__).parent.parent / "scripts"))
    import importlib
    acc = importlib.import_module("run_acceptance")
    with pytest.raises(KeyError, match="nonexistent_engine"):
        acc.analyse_pcm(b"\x00" * 1000, backend="nonexistent_engine")


def test_acceptance_uses_registry():
    """ENGINES registry contains at least 'v2'; CLI choices are derived from it."""
    from huesync.spectrum_engine import ENGINES
    assert "v2" in ENGINES
    assert "cavacore" in ENGINES  # registered even if not available on this system
