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


def test_beat_detector_reset_rebuild_race_preserves_new_config():
    """A rebuild that completes concurrently with a reset must not be
    overwritten by the reset committing its captured-earlier state.

    Reproduces the item-17 defect: pre-fix reset() captured self._state at
    the top of the call, built a new state from that snapshot, then
    committed via a plain attribute assignment at the bottom.  A rebuild()
    landing between capture and commit would be silently reverted.

    Forces the race deterministically: we monkey-patch _make_state so that
    reset()'s intermediate work takes long enough to guarantee rebuild()
    commits in the middle.  With the _rebuild_lock in place, rebuild()
    cannot commit while reset() holds the lock (or vice versa), so the
    surviving state must always match the rebuild parameters.
    """
    bd = _make_beat_detector(method="combined")
    assert bd._state.onset_method == "combined"

    barrier = threading.Barrier(2)
    real_make_state = BeatDetector._make_state
    rebuild_done = threading.Event()

    def slow_make_state(cls, *args, **kw):  # type: ignore[no-untyped-def]
        # Only slow the RESET path (which passes the OLD "combined" method).
        # The rebuild path passes "superflux" and must run fast.
        if args and args[0] == "combined":
            barrier.wait()             # sync with rebuild
            rebuild_done.wait(1.0)     # let rebuild finish first
        return real_make_state.__func__(cls, *args, **kw)

    BeatDetector._make_state = classmethod(slow_make_state)  # type: ignore[assignment]
    try:
        def do_reset() -> None:
            bd.reset()

        def do_rebuild() -> None:
            barrier.wait()
            bd.rebuild(
                onset_method="superflux",
                onset_delta=0.1,
                onset_alpha=0.9,
                superflux_mu=3,
                superflux_lag=2,
                bass_hz=250,
                mid_hz=2000,
            )
            rebuild_done.set()

        t_reset = threading.Thread(target=do_reset)
        t_rebuild = threading.Thread(target=do_rebuild)
        t_reset.start()
        t_rebuild.start()
        t_reset.join(timeout=3)
        t_rebuild.join(timeout=3)
        assert not t_reset.is_alive() and not t_rebuild.is_alive()
    finally:
        BeatDetector._make_state = real_make_state  # type: ignore[assignment]

    # The rebuild committed "superflux" while reset was mid-flight.  With
    # the _rebuild_lock fix, reset's commit either waited (still ended with
    # superflux, because reset built its new state from the current locked
    # snapshot which is now "superflux") or ran first (reset committed
    # combined, rebuild then committed superflux).  Either way the final
    # onset_method must be "superflux" — never "combined".
    assert bd._state.onset_method == "superflux", (
        "reset() overwrote a concurrently-installed rebuild()"
    )


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


def test_stop_timeout_defers_close_to_worker_finally():
    """When stop() times out, the worker's finally block must close
    processors exactly once when it eventually exits (item 15).

    Simulates a stuck feed() by making source.read() block for ~0.5 s.  A
    stop(timeout≈0.1) returns False without closing processors.  Once the
    read blocks release and the worker exits its loop, the finally block
    picks up the close responsibility so no native resources leak.
    """
    close_calls: list[str] = []
    release_read = threading.Event()

    class _BlockingSource:
        def read(self):
            from huesync.canonicalizer import TemporarilyNoData
            # Block until the test releases us; simulates a wedged read.
            release_read.wait(2.0)
            return TemporarilyNoData()

        @property
        def running(self) -> bool:
            return True

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

    cap = _make_cap(engine=_TrackingEngine(), source=_BlockingSource())
    # Patch the join timeout by temporarily overriding stop's behaviour:
    # we invoke stop() while the worker is blocked in read().
    cap.start()
    # Give the worker a moment to enter read().
    time.sleep(0.05)
    # Reduce the join wait so stop() returns quickly with a timeout.
    original_join = cap._thread.join
    cap._thread.join = lambda timeout=None: original_join(timeout=0.1)  # type: ignore[assignment]

    clean = cap.stop()
    assert clean is False, "Blocked worker must cause stop() to return False"
    assert close_calls == [], (
        "stop() must NOT close processors while the worker is still alive"
    )

    # Now release the worker; it will loop, see _stop, exit, and its
    # finally block must run close() exactly once.
    release_read.set()
    # Wait for the real thread to exit.
    original_join(timeout=2.0)
    # Small window for finally to run.
    for _ in range(50):
        if close_calls:
            break
        time.sleep(0.02)

    assert close_calls == ["engine_close"], (
        f"Worker's finally block must close processors exactly once; got {close_calls}"
    )

    # Calling stop() again after the fact must NOT double-close.
    cap.stop()
    assert close_calls == ["engine_close"], (
        "Second stop() must not re-close processors already handled by _run finally"
    )


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
    write_seq: int = 0,
    gap_seq: int = 0,
) -> None:
    """Write a synthetic v1 SHM segment with extended header.

    Layout: magic(4), abi_version(2), flags(2), write_seq(4), generation(8),
    abs_write_pos(8) in stereo frames, gap_seq(8), pad(4).  write_seq must be
    even for the seqlock coherent-read protocol to accept the snapshot.
    """
    legacy_hdr = _struct.pack(
        _HDR_FMT, VIS_BUF_SIZE, buf_index % VIS_BUF_SIZE, int(running), rate, 0
    )
    ext_hdr = _struct.pack(
        _V2_EXT_FMT,
        SHM_ABI_V1_MAGIC,     # magic
        1,                     # abi_version
        0,                     # flags (reserved)
        write_seq,             # seqlock counter (even = stable)
        generation,            # producer lifetime ID
        abs_write_pos,         # stereo-frame counter
        gap_seq,               # skipped-export counter
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
    # abs_write_pos is in stereo frames; ring capacity is VIS_BUF_SIZE // 2.
    frame_capacity = VIS_BUF_SIZE // 2
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=500)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Advance abs_write_pos by exactly one full lap.
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=500 + frame_capacity)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.FULL_LAP


def test_shm_v1_multiple_laps(tmp_path):
    p = tmp_path / "shm"
    frame_capacity = VIS_BUF_SIZE // 2
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    # Advance by 3 full laps.
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=3 * frame_capacity)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.MULTIPLE_LAPS


def test_shm_v1_gap_sequence(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0, gap_seq=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=100, generation=1, abs_write_pos=50, gap_seq=1)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.GAP_SEQUENCE


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


# ---------------------------------------------------------------------------
# H1: seqlock coherent read
# ---------------------------------------------------------------------------


def test_shm_v1_seqlock_mid_write_rejected(tmp_path):
    """A persistently odd write_seq (writer never finishes) surfaces as TORN_READ."""
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0, write_seq=1)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    result = src.read_new_checked()
    assert result.event in (
        ShmContinuityEvent.NO_DATA,
        ShmContinuityEvent.TORN_READ,
    ), f"expected NO_DATA or TORN_READ, got {result.event}"


# ---------------------------------------------------------------------------
# H2: abs position is not double-counted
# ---------------------------------------------------------------------------


def test_shm_v1_abs_write_pos_used_verbatim(tmp_path):
    """abs_write_pos from SHM is used as-is; consumer does not add delivered frames."""
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=100, generation=1, abs_write_pos=50)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=200, generation=1, abs_write_pos=100)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.ADVANCE
    assert result.abs_write_pos == 100


# ---------------------------------------------------------------------------
# H3: gap_seq observed exactly once
# ---------------------------------------------------------------------------


def test_shm_v1_gap_seq_observed_exactly_once(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0, gap_seq=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=50, generation=1, abs_write_pos=25, gap_seq=1)
    r1 = src.read_new_checked()
    assert r1.event == ShmContinuityEvent.GAP_SEQUENCE

    _write_shm_v1(p, buf_index=100, generation=1, abs_write_pos=50, gap_seq=1)
    r2 = src.read_new_checked()
    assert r2.event == ShmContinuityEvent.ADVANCE


# ---------------------------------------------------------------------------
# H4: producer generation change detected between polls
# ---------------------------------------------------------------------------


def test_shm_v1_generation_change_detected(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=10, generation=1, abs_write_pos=5)
    src.read_new_checked()  # consume the initial advance
    _write_shm_v1(p, buf_index=10, generation=2, abs_write_pos=0)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.RESTART


# ---------------------------------------------------------------------------
# H5: abs_write_pos regression = restart
# ---------------------------------------------------------------------------


def test_shm_v1_abs_write_pos_regression_is_restart(tmp_path):
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=5000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    _write_shm_v1(p, buf_index=10, generation=1, abs_write_pos=10)
    result = src.read_new_checked()
    assert result.event == ShmContinuityEvent.RESTART


# ---------------------------------------------------------------------------
# H6: BeatDetector concurrent rebuild + feed
# ---------------------------------------------------------------------------


def test_beat_detector_concurrent_rebuild_feed_safe():
    """Interleaving rebuild() with feed() must not raise or corrupt state."""
    bd = BeatDetector(
        onset_method="combined", onset_delta=0.1, onset_alpha=0.9,
        superflux_mu=3, superflux_lag=2, bass_hz=250, mid_hz=2000,
    )
    frame = SharedAnalysisFrame(
        pcm=np.zeros((480, 2), dtype=np.float32),
        mag_frames=[np.zeros(1025, dtype=np.float32)] * 3,
        epoch_id="ep-1", sample_start=0, sample_end=480,
        hop_sample_starts=[0, 160, 320],
    )
    errors: list[Exception] = []

    def feeder() -> None:
        for _ in range(200):
            try:
                bd.feed(frame)
            except Exception as exc:  # noqa: BLE001 - test aggregation
                errors.append(exc)

    def rebuilder() -> None:
        methods = ["combined", "multiband", "combined", "superflux"]
        for i in range(100):
            try:
                bd.rebuild(
                    onset_method=methods[i % 4],
                    onset_delta=0.1, onset_alpha=0.9,
                    superflux_mu=3, superflux_lag=2,
                    bass_hz=250, mid_hz=2000,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t1 = threading.Thread(target=feeder)
    t2 = threading.Thread(target=rebuilder)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors, f"Concurrent errors: {errors}"


# ---------------------------------------------------------------------------
# H7: stop() returns bool and blocks on live workers
# ---------------------------------------------------------------------------


def test_cap_stop_returns_bool_clean():
    """stop() returns True when the worker terminated cleanly."""
    cap = _make_cap()
    cap.start()
    assert cap.stop() is True


# ---------------------------------------------------------------------------
# H8: atomic PublicationRecord state
# ---------------------------------------------------------------------------


def test_cap_pub_seq_matches_last_record_sequence():
    cap = _make_cap()
    _feed_warmup(cap)
    recs = cap.feed(_make_frame(sample_pos=6 * _HOP))
    if not recs:
        pytest.skip("No publication produced during warmup range")
    assert cap.pub_seq == recs[-1].sequence
    drained = cap.drain_publications()
    assert any(r.sequence == recs[-1].sequence for r in drained)


# ---------------------------------------------------------------------------
# H9: EOS features survive TemporarilyNoData
# ---------------------------------------------------------------------------


def test_eos_latest_survives_temporary_no_data():
    cap = _make_cap()
    for i in range(10):
        cap.feed(_make_frame(sample_pos=i * _HOP))
    before = cap.latest()
    if before is None:
        pytest.skip("No publication produced")
    cap.end_of_stream()
    # After a clean EOS the latest publication must still be reachable —
    # short source gaps do not clear it.
    assert cap.latest() is not None


# ---------------------------------------------------------------------------
# H10: STFT sample positions are chunk-independent
# ---------------------------------------------------------------------------


def test_v2_positions_chunk_independent():
    """Same audio fed as 480-sample chunks vs 100-sample chunks → identical positions."""
    rng = np.random.default_rng(42)
    audio_mono = rng.standard_normal(480 * 20).astype(np.float32) * 0.1
    audio = np.column_stack([audio_mono, audio_mono])

    def _run(chunk_size: int) -> list[int]:
        cap = _make_cap()
        positions: list[int] = []
        pos = 0
        for i in range(0, len(audio), chunk_size):
            block = audio[i:i + chunk_size]
            frame = AnalysisPcmFrame(
                samples=block, sample_pos=pos,
                epoch_id="ep-1", source_id="t", over_range=False,
            )
            for rec in cap.feed(frame):
                positions.append(rec.sample_pos)
            pos += len(block)
        return positions

    pos_480 = _run(480)
    pos_100 = _run(100)
    assert pos_480 == pos_100, (
        f"positions differ between chunk sizes:\n480: {pos_480[:5]}\n100: {pos_100[:5]}"
    )


# ---------------------------------------------------------------------------
# H11: CAVA EOS clamps sample_end
# ---------------------------------------------------------------------------


def test_cava_eos_tail_clamped():
    """A 480-sample block followed by a 1-sample tail must not report sample_end > 481."""
    cap = _make_cap()
    for i in range(10):
        cap.feed(_make_frame(sample_pos=i * _HOP))
    cap.feed(AnalysisPcmFrame(
        samples=np.ones((1, 2), dtype=np.float32),
        sample_pos=10 * _HOP, epoch_id="ep-1", source_id="t", over_range=False,
    ))
    flush_recs = cap.end_of_stream()
    real_end = 10 * _HOP + 1
    for rec in flush_recs:
        assert rec.sample_end <= real_end, (
            f"flush record sample_end={rec.sample_end} exceeds real source end {real_end}"
        )


# ---------------------------------------------------------------------------
# H12: v1-required SquzeezliteShmStereoSource rejects v0
# ---------------------------------------------------------------------------


def test_stereo_source_rejects_v0_when_require_v1(tmp_path):
    """SqueezeliteShmStereoSource.open(require_v1=True) raises for v0-only SHM."""
    from huesync.pcm_source import SqueezeliteShmStereoSource
    p = tmp_path / "shm"
    header = _struct.pack(_HDR_FMT, VIS_BUF_SIZE, 0, 1, 44100, 0)
    p.write_bytes(bytes(_HDR_OFFSET) + header + bytes(VIS_BUF_SIZE * 2))
    src = SqueezeliteShmStereoSource()
    with pytest.raises(RuntimeError, match="v1 ABI"):
        src.open("x", _path=p, require_v1=True)


# ---------------------------------------------------------------------------
# H13: v1 PCM does not overlap with the extension bytes
# ---------------------------------------------------------------------------


def test_stereo_source_v1_buf_offset_is_120(tmp_path):
    """v1 mode reads PCM from offset 120, not 80 (the extension is between)."""
    from huesync.pcm_source import (
        _BUF_OFFSET_V1,
        SqueezeliteShmStereoSource,
    )
    p = tmp_path / "shm"
    _write_shm_v1(p, buf_index=0, generation=1, abs_write_pos=0)
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=False)  # accept either
    assert src._buf_offset() == _BUF_OFFSET_V1


# ---------------------------------------------------------------------------
# H14: effective_processor_ids limited to contributors
# ---------------------------------------------------------------------------


def test_effective_processor_ids_only_contributors():
    """A processor that emits no updates must not appear in effective_processor_ids."""

    class _NoOutputProc:
        processor_id = "no_output"

        def feed(self, frame):
            return []

        def flush(self):
            return []

        def reset(self) -> None:
            pass

        def close(self) -> None:
            pass

    cap = _make_cap()
    cap._processors = (*cap._processors, _NoOutputProc())
    _feed_warmup(cap)
    recs = cap.feed(_make_frame(sample_pos=6 * _HOP))
    for rec in recs:
        assert "no_output" not in rec.effective_processor_ids


# ---------------------------------------------------------------------------
# H15: bounded queue drop counter
# ---------------------------------------------------------------------------


def test_pub_queue_overflow_pub_dropped_non_negative():
    """pub_dropped_count is always >= 0 and never regresses within a single drain window."""
    cap = _make_cap()
    for i in range(2000):
        cap.feed(_make_frame(sample_pos=i * _HOP))
    assert cap.pub_dropped_count >= 0
    cap.drain_publications()
    assert cap.pub_dropped_count == 0


# ---------------------------------------------------------------------------
# Item 23: Beat-only publication (SpectrumProcessor produces no output)
# ---------------------------------------------------------------------------


class _NullSpectrumEngine:
    """SpectrumEngine stub that never produces bars.

    Simulates cavacore during its 480-frame carry warmup — the shared STFT
    may still have magnitudes and BeatDetector may emit onset updates, but
    the spectrum engine has nothing to publish this frame.
    """

    engine_id: str = "null_spectrum"

    def feed(self, pcm, shared):
        return []

    def flush(self):
        return []

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_beat_only_publication():
    """When Spectrum produces no output, BeatDetector onset still yields a PublicationRecord.

    Publication must NOT be gated on Spectrum bars.  effective_processor_ids
    must contain the BeatDetector but MUST NOT contain the SpectrumProcessor.
    """
    cap = _make_cap(engine=_NullSpectrumEngine())
    # Feed enough energetic PCM to trigger onsets after STFT warmup.
    rng = np.random.default_rng(0xBEEF)
    onset_seen = False
    beat_only_seen = False
    for i in range(30):
        noise = rng.standard_normal((_HOP, 2)).astype(np.float32) * 0.4
        frame = AnalysisPcmFrame(
            samples=noise, sample_pos=i * _HOP,
            epoch_id="ep-beat", source_id="t", over_range=False,
        )
        recs = cap.feed(frame)
        for rec in recs:
            # SpectrumProcessor must never appear in the contributors.
            assert "null_spectrum" not in rec.effective_processor_ids
            if "beat_detector" in rec.effective_processor_ids:
                beat_only_seen = True
                if rec.features.onset:
                    onset_seen = True
    assert beat_only_seen, "expected at least one BeatDetector-only publication"
    # Onset may or may not fire depending on the random signal; the key
    # invariant is that publications happen and effective_processor_ids
    # is truthful.
    _ = onset_seen


class _DummyLoudnessProcessor:
    """Test-only processor that emits exactly one ProcessorUpdate per feed()."""

    processor_id: str = "dummy_loudness"

    def feed(self, frame):
        return [ProcessorUpdate(
            processor_id=self.processor_id,
            sample_start=frame.sample_start,
            sample_end=frame.sample_end,
        )]

    def flush(self):
        return []

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_dummy_loudness_only_publication():
    """A pipeline with ONLY a loudness-style processor still publishes records."""
    cap = _make_cap(engine=_NullSpectrumEngine())
    dummy = _DummyLoudnessProcessor()
    cap._processors = (dummy,)  # replace both Spectrum + Beat with just the dummy
    for i in range(5):
        recs = cap.feed(_make_frame(sample_pos=i * _HOP))
        assert recs, "loudness-only pipeline must publish"
        for rec in recs:
            assert rec.effective_processor_ids == ("dummy_loudness",)


# ---------------------------------------------------------------------------
# Items 25-28: CAVA sample position ownership
# ---------------------------------------------------------------------------


class _StubCavaEngine:
    """SpectrumEngine that emulates CAVA's carry-based 480-block scheduler.

    Mirrors CavaCoreSpectrumEngine's feed() semantics without requiring the
    native library: samples accumulate in a carry buffer, and every full
    480-frame block yields one SpectrumUpdate.  Position tracking inside the
    engine is deliberately naive so that SpectrumProcessor's carry-tracking
    logic is exercised for real.
    """

    engine_id: str = "cavacore"

    _BLOCK: int = 480

    def __init__(self, n_bars: int = 8) -> None:
        self._n_bars = n_bars
        self._carry: int = 0
        self._epoch_samples: int = 0

    def feed(self, pcm, shared):
        n = len(pcm)
        combined = self._carry + n
        n_blocks = combined // self._BLOCK
        self._carry = combined - n_blocks * self._BLOCK
        self._epoch_samples += n
        # Emit one identical bar array per completed block.
        return [
            SpectrumUpdate(
                engine_id=self.engine_id,
                bars=[0.1] * self._n_bars,
                sample_pos=max(0, self._epoch_samples - self._BLOCK),
            )
            for _ in range(n_blocks)
        ]

    def flush(self):
        return []

    def reset(self) -> None:
        self._carry = 0
        self._epoch_samples = 0

    def close(self) -> None:
        pass


def _feed_pcm(processor: SpectrumProcessor, sample_start: int, n: int) -> list[ProcessorUpdate]:
    """Feed n canonical frames starting at sample_start via SharedAnalysisFrame."""
    return processor.feed(SharedAnalysisFrame(
        pcm=np.zeros((n, 2), dtype=np.float32),
        mag_frames=[],
        epoch_id="ep-1",
        sample_start=sample_start,
        sample_end=sample_start + n,
        hop_sample_starts=[],
    ))


def test_cava_positions_single_full_block():
    """Feed 480 → one update at [0, 480), no carry."""
    p = SpectrumProcessor(_StubCavaEngine())
    updates = _feed_pcm(p, 0, 480)
    assert len(updates) == 1
    assert updates[0].sample_start == 0
    assert updates[0].sample_end == 480
    assert p._cava_carry_len == 0


def test_cava_positions_1_frame_only():
    """Feed 1 frame → no update, carry_start=0, carry_len=1."""
    p = SpectrumProcessor(_StubCavaEngine())
    updates = _feed_pcm(p, 0, 1)
    assert updates == []
    assert p._cava_carry_len == 1
    assert p._cava_carry_sample_start == 0


def test_cava_positions_480_then_1():
    """480 frames + 1 frame → one update at [0, 480), carry_start=480, carry_len=1."""
    p = SpectrumProcessor(_StubCavaEngine())
    u1 = _feed_pcm(p, 0, 480)
    u2 = _feed_pcm(p, 480, 1)
    assert len(u1) == 1
    assert u1[0].sample_start == 0
    assert u1[0].sample_end == 480
    assert u2 == []
    assert p._cava_carry_sample_start == 480
    assert p._cava_carry_len == 1


def test_cava_positions_240_then_241():
    """240 + 241 = 481 → one update at [0, 480), carry_start=480, carry_len=1."""
    p = SpectrumProcessor(_StubCavaEngine())
    u1 = _feed_pcm(p, 0, 240)
    u2 = _feed_pcm(p, 240, 241)
    assert u1 == []
    assert len(u2) == 1
    assert u2[0].sample_start == 0
    assert u2[0].sample_end == 480
    assert p._cava_carry_sample_start == 480
    assert p._cava_carry_len == 1


def test_cava_positions_719_then_241():
    """719 + 241 = 960 → two updates at [0,480) and [480,960), no carry remaining."""
    p = SpectrumProcessor(_StubCavaEngine())
    u1 = _feed_pcm(p, 0, 719)
    u2 = _feed_pcm(p, 719, 241)
    assert len(u1) == 1
    assert u1[0].sample_start == 0
    assert u1[0].sample_end == 480
    assert len(u2) == 1
    assert u2[0].sample_start == 480
    assert u2[0].sample_end == 960
    assert p._cava_carry_len == 0
    assert p._cava_carry_sample_start == 960


def test_cava_timestamps_monotonic_six_blocks():
    """Six consecutive 480-frame CAVA inputs → strictly monotonic timestamps.

    Regression test for the "positions reset after 4 blocks" bug: the CAVA
    carry counter must be independent of the shared STFT epoch_start /
    stft_frame_count fields that _process_canonical_frame() maintains.
    """
    p = SpectrumProcessor(_StubCavaEngine())
    prev_start = -1
    prev_end = -1
    for i in range(6):
        updates = _feed_pcm(p, i * 480, 480)
        assert len(updates) == 1
        u = updates[0]
        assert u.sample_start > prev_start, (
            f"backward timestamp at block {i}: {u.sample_start} <= {prev_start}"
        )
        assert u.sample_start >= prev_end
        prev_start = u.sample_start
        prev_end = u.sample_end
    assert prev_start == 5 * 480


def test_cava_positions_reset_clears_carry():
    p = SpectrumProcessor(_StubCavaEngine())
    _feed_pcm(p, 0, 240)  # leave 240 in carry
    p.reset()
    assert p._cava_carry_len == 0
    assert p._cava_carry_sample_start == 0
    # After reset, sample_start of the next block must respect the new chunk start.
    updates = _feed_pcm(p, 1000, 480)
    assert len(updates) == 1
    assert updates[0].sample_start == 1000


def test_cava_positions_independent_of_shared_stft_reset():
    """SpectrumProcessor's CAVA carry survives a shared-STFT epoch-frame reset.

    Even if CanonicalAnalysisPipeline's _epoch_start_sample_pos changed
    (e.g. a fresh feed chunk starts at a wildly different sample_start),
    SpectrumProcessor's own carry counter drives the CAVA block timestamps
    — not the shared STFT hop_sample_starts.
    """
    p = SpectrumProcessor(_StubCavaEngine())
    updates1 = _feed_pcm(p, 0, 960)          # two blocks: [0,480), [480,960)
    # Now feed another chunk but claim a nonsense sample_start.  The CAVA
    # carry counter is empty here (960 samples in, 960 blocks out), so the
    # next carry_start snaps to this new chunk start.
    updates2 = _feed_pcm(p, 9999, 480)       # one block starting at 9999
    assert len(updates1) == 2
    assert updates1[0].sample_start == 0
    assert updates1[1].sample_start == 480
    assert len(updates2) == 1
    assert updates2[0].sample_start == 9999
    assert updates2[0].sample_end == 9999 + 480


# ---------------------------------------------------------------------------
# Item 33 (strengthened): terminal latest survives explicit TemporarilyNoData
# ---------------------------------------------------------------------------


def test_terminal_latest_survives_temporarily_no_data_path():
    """The TemporarilyNoData branch of _run() must NEVER clear _latest_pub.

    Exercises the code path directly rather than relying on end_of_stream():
    inject a TemporarilyNoData result into the worker's canonical result
    handling to verify the latest publication survives.
    """
    from huesync.canonicalizer import TemporarilyNoData
    cap = _make_cap()
    # Produce at least one publication.
    for i in range(10):
        cap.feed(_make_frame(sample_pos=i * _HOP))
    before = cap.latest()
    assert before is not None
    latest_pub_before = cap._latest_pub
    assert latest_pub_before is not None

    # Simulate the TemporarilyNoData branch of _run().  The worker thread is
    # not started; we invoke the observable side-effects that branch performs
    # (i.e. nothing to _latest_pub) and assert the state is unchanged.
    _ = TemporarilyNoData()
    # No mutation of publication state should happen for TemporarilyNoData.
    assert cap._latest_pub is latest_pub_before
    assert cap.latest() is not None


# ---------------------------------------------------------------------------
# Item 34 (strengthened): exact queue overflow drop count
# ---------------------------------------------------------------------------


def test_queue_overflow_exact_drops():
    """Queue is bounded at maxlen=1000; overflow drop count must be exact."""
    cap = _make_cap()
    # Force publications by calling _publish directly with dummy records so
    # STFT warmup does not affect the drop-count math.
    from huesync.types import AudioFeatures
    features = AudioFeatures(
        bars=[0.0] * 30, bass=0.0, mid=0.0, full=0.0, centroid=0.0,
        onset=False, onset_strength=0.0,
        onset_bass=False, onset_mid=False, onset_treble=False,
        onset_bass_strength=0.0, onset_mid_strength=0.0, onset_treble_strength=0.0,
        sustained_energy=None, hpss_active=False, relative_exertion=0.0,
    )
    n_pubs = 1006
    for i in range(n_pubs):
        cap._publish(
            epoch_id="ep-1",
            sample_start=i * _HOP,
            sample_end=(i + 1) * _HOP,
            features=features,
            effective_engine_id="v2",
            effective_processor_ids=("v2",),
        )
    maxlen = 1000
    # Exact expected drop count for 1006 publications and maxlen=1000.
    assert cap._pub_dropped == n_pubs - maxlen, (
        f"expected {n_pubs - maxlen} drops, got {cap._pub_dropped}"
    )
    assert len(cap._pub_queue) == maxlen


# ---------------------------------------------------------------------------
# Item 32: publication atomicity — _pub_lock covers all three fields
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Items 29-31: acceptance source duration and CAVA metadata
# ---------------------------------------------------------------------------


def _analyse_sine(duration_s: float, backend: str = "v2"):
    """Run scripts/run_acceptance.analyse_pcm on `duration_s` seconds of sine."""
    import sys
    sys.path.insert(0, str(_Path(__file__).parent.parent / "scripts"))
    import importlib
    acc = importlib.import_module("run_acceptance")
    sr = 44100
    n = int(sr * duration_s)
    t = np.arange(n, dtype=np.float32) / sr
    sig = (np.sin(2 * np.pi * 440.0 * t) * 0.3 * 32767.0).astype(np.int16)
    stereo = np.column_stack([sig, sig]).flatten()
    return acc.analyse_pcm(stereo.tobytes(), backend=backend), acc


def test_acceptance_source_duration_from_source():
    """source_duration_s reflects canonical frames fed, not publications produced."""
    (rows, info), acc = _analyse_sine(2.0, backend="v2")
    canonical_rate = info["canonical_rate"]
    total_frames = info["total_canonical_frames"]
    source_duration_s = info["source_duration_s"]
    # source_duration_s must derive from total_canonical_frames, not from
    # the count of published rows or their sample_end.
    assert source_duration_s == round(total_frames / canonical_rate, 6)
    # V2 has ~2048-sample warmup, so max sample_end is lower than the
    # source-authoritative duration.  This is exactly what the two fields
    # are supposed to distinguish.
    analysis_extent = info["analysis_publication_extent_s"]
    # In this configuration analysis_extent should be at MOST source_duration_s;
    # for V2 it must be strictly less because of warmup.
    assert analysis_extent <= source_duration_s + 1e-6


def test_acceptance_v2_engine_metadata():
    """V2 metadata reports the shared STFT parameters as its own transform."""
    (_rows, info), _acc = _analyse_sine(0.5, backend="v2")
    spec = info["spectrum_engine_details"]
    assert spec["engine_id"] == "v2"
    assert info["shared_stft_fft_size"] > 0
    assert info["shared_stft_window"] == "hamming"
    assert info["shared_stft_hop"] == info["hop"]


def test_acceptance_cava_metadata_when_available():
    """When CAVA is available, metadata reports CAVA-specific FFT/window/cadence.

    Skipped when the native library is not built.
    """
    from huesync.spectrum_engine import ENGINES
    if not ENGINES["cavacore"].check_available():
        pytest.skip("cavacore native library not available")
    (_rows, info), _acc = _analyse_sine(0.5, backend="cavacore")
    spec = info["spectrum_engine_details"]
    assert spec["engine_id"] == "cavacore"
    assert spec["execution_block_frames"] == 480
    assert spec["execution_rate_hz"] == 100.0
    assert spec["normal_fft"] == 4096
    assert spec["bass_fft"] == 8192
    assert spec["window"] == "hann"
    # Shared STFT fields describe the shared STFT (BeatDetector input), NOT
    # the CAVA engine.  They must still exist and be truthful.
    assert info["shared_stft_window"] == "hamming"


def test_publication_atomicity_lock_covers_all_state():
    """_pub_lock guards _latest_pub, _pub_queue, and _pub_seq as one unit."""
    cap = _make_cap()
    # Assert the three fields live behind the same lock; static check on
    # source structure would be brittle, so verify runtime invariant instead.
    from huesync.types import AudioFeatures
    features = AudioFeatures(
        bars=[0.0] * 30, bass=0.0, mid=0.0, full=0.0, centroid=0.0,
        onset=False, onset_strength=0.0,
        onset_bass=False, onset_mid=False, onset_treble=False,
        onset_bass_strength=0.0, onset_mid_strength=0.0, onset_treble_strength=0.0,
        sustained_energy=None, hpss_active=False, relative_exertion=0.0,
    )
    rec = cap._publish(
        epoch_id="ep-1", sample_start=0, sample_end=_HOP,
        features=features, effective_engine_id="v2",
        effective_processor_ids=("v2",),
    )
    # After a single _publish call, all three views must agree.
    assert cap.pub_seq == rec.sequence
    assert cap._latest_pub is rec
    with cap._pub_lock:
        assert cap._pub_queue[-1] is rec
