"""Regressions reproduced against 841ca873, using real DSP orchestration."""

import asyncio
import ctypes
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi import HTTPException
from test_analysis_architecture import _make_cap, _make_frame
from test_audit_blockers_v3 import _make_engine_and_start

from huesync.api import deactivate_coupling
from huesync.canonicalizer import (
    AnalysisPcmFrame,
    DataResult,
    DecodedSourceFrame,
    TemporarilyNoData,
)
from huesync.cavacore import CavaCoreBackend
from huesync.player_manager import ActiveSession, PlayerManager
from huesync.spectrum_engine import CavaCoreSpectrumEngine, ProcessorUpdate


def _real_cava_scheduler():
    class Native:
        def cava_execute(self, pcm, count, out, plan):
            out[0], out[1] = 0.1, 0.3

        def cavacore_close_plan(self, plan):
            pass

    backend = CavaCoreBackend.__new__(CavaCoreBackend)
    backend._plan = 1
    backend._channels = 2
    backend._n_bars = 1
    backend._out_buf = (ctypes.c_double * 2)()
    backend._carry_flat = None
    backend._exec_block_flat = 960
    backend._lib = Native()
    engine = CavaCoreSpectrumEngine.__new__(CavaCoreSpectrumEngine)
    engine._cava = backend
    engine._epoch_samples = 0
    engine._next_exec_start = 0
    return engine


def test_real_cava_and_beat_publish_every_detected_onset():
    cap = _make_cap(engine=_real_cava_scheduler())
    actual_feed = cap._beat_detector.feed
    detected = []

    def observe(frame):
        updates = actual_feed(frame)
        detected.extend((u.sample_start, u.sample_end) for u in updates if u.onset)
        return updates

    cap._beat_detector.feed = observe
    rng = np.random.default_rng(33)
    records = []
    visible_onsets = 0
    try:
        for i in range(400):
            pcm = (rng.normal(size=(480, 2)) * (0.5 if i % 30 < 2 else 0.002)).astype('float32')
            records.extend(cap.feed(AnalysisPcmFrame(
                samples=pcm, sample_pos=i * 480, epoch_id='e', source_id='s', over_range=False,
            )))
            visible_onsets += bool(cap.latest().onset)
        published = [(r.sample_pos, r.sample_end) for r in records
                     if 'beat_detector' in r.effective_processor_ids and r.features.onset]
        assert detected, 'fixture must produce actual positive onsets'
        assert published == detected
        assert visible_onsets > 0, 'polling consumers must also see Beat output'
        assert cap.pub_late_dropped_count == 0
        for r in records:
            carry = r.carried_spectrum_interval
            assert (carry is None or carry == (r.sample_pos, r.sample_end)
                    or carry[1] <= r.sample_pos)
    finally:
        cap.stop()


@pytest.mark.parametrize('eos', [False, True])
def test_delayed_distinct_intervals_and_provenance(eos):
    cap = _make_cap()

    class Processor:
        processor_id = 'loudness'

        def feed(self, frame):
            if frame.sample_start == 0:
                return [ProcessorUpdate('spectrum', 0, 10, bars=[0.25])]
            if not eos:
                return self.flush()
            return [ProcessorUpdate('spectrum', 480, 960, bars=[0.9])]

        def flush(self):
            return [ProcessorUpdate('loudness', 10, 20, onset_strength=0.4),
                    ProcessorUpdate('chroma', 300, 320, onset=True)]

        def reset(self):
            pass

        def close(self):
            pass

    cap._processors = (Processor(),)
    try:
        cap.feed(_make_frame(sample_pos=0))
        if eos:
            cap.feed(_make_frame(sample_pos=480))
            records = cap.end_of_stream()
        else:
            # Establish a newer publication before the delayed contributions arrive.
            cap._publish_by_interval(epoch_id='ep-1', spectrum_updates=[
                ProcessorUpdate('spectrum', 480, 960, bars=[0.9])],
                other_updates=[], clamp_end=None)
            records = cap.feed(_make_frame(sample_pos=480))
        assert [(r.sample_pos, r.sample_end) for r in records] == [(10, 20), (300, 320)]
        assert [r.effective_processor_ids for r in records] == [('loudness',), ('chroma',)]
        assert all(r.features.bars == [0.25] for r in records)
        assert all(r.carried_spectrum_interval == (0, 10) for r in records)
        assert records[1].sequence > records[0].sequence
    finally:
        cap.stop()


def test_real_manager_teardown_retains_blocked_reader(tmp_path, monkeypatch):
    class Source:
        running = True

        def __init__(self):
            self.release = threading.Event()
            self.total_reads = 0
            self.in_read = False
            self.closed = 0

        def read(self):
            self.in_read = True
            self.total_reads += 1
            try:
                self.release.wait(20)
                return TemporarilyNoData()
            finally:
                self.in_read = False

        def close(self):
            assert not self.in_read, 'source closed under active reader'
            self.closed += 1

    source = Source()
    engine, cap = _make_engine_and_start(source)
    original_join = cap._thread.join
    monkeypatch.setattr(cap._thread, 'join', lambda timeout=None: original_join(0.02))
    closes = [0]
    original_close = cap._spectrum_processor.close

    def count_close():
        closes[0] += 1
        original_close()

    cap._spectrum_processor.close = count_close
    manager = PlayerManager.__new__(PlayerManager)
    manager.storage = MagicMock()
    session = ActiveSession(engine.profile)
    session.profile.player_mac = ''
    session.fifo_path = tmp_path / 'fifo'
    session.cava_conf_path = tmp_path / 'conf'
    session.sync_engine = engine
    session.shm_source = source
    manager._active = session
    candidate = _make_cap()
    candidate_close = MagicMock(wraps=candidate._spectrum_processor.close)
    candidate._spectrum_processor.close = candidate_close
    try:
        with pytest.raises(RuntimeError):
            engine.replace_analyser(candidate)
        assert candidate._processors_closed
        candidate_close.assert_called_once()
        with pytest.raises(RuntimeError, match='retir|stop'):
            asyncio.run(manager.deactivate())
        assert manager._active is session
        assert manager.analysis_stopping
        with pytest.raises(RuntimeError, match='stopping|deactivation'):
            manager.replace_pcm_analyser(session.profile)
        assert engine.retirement_pending
        assert source.closed == closes[0] == 0
        request = MagicMock()
        request.app.state.player_manager = manager
        request.app.state.storage = manager.storage
        with pytest.raises(HTTPException) as error:
            asyncio.run(deactivate_coupling(request))
        assert error.value.status_code == 409
        assert 'retiring' in error.value.detail
        assert source.closed == 0
        with pytest.raises(RuntimeError, match='retir'):
            engine.start()
        with pytest.raises(RuntimeError, match='running|retiring'):
            cap.start()
        # Activation's first action is deactivate: it must not create another reader.
        with pytest.raises(RuntimeError, match='retir|stop'):
            asyncio.run(manager.activate_coupling(MagicMock()))
        source.release.set()
        original_join(2)
        assert not cap._thread.is_alive()
        assert closes == [1]
        with pytest.raises(RuntimeError, match='Closed'):
            cap.start()
        asyncio.run(manager.deactivate())
        assert manager._active is None
        assert source.closed == 1
        assert not engine.retirement_pending
        asyncio.run(manager.deactivate())
        assert source.closed == 1
        # The same engine may accept a fresh analyser after ownership is released.
        class FreshSource:
            running = True
            sent = False

            def read(self):
                if self.sent:
                    return TemporarilyNoData()
                self.sent = True
                return DataResult(DecodedSourceFrame(
                    samples=np.ones((5000, 2), dtype='float32') * 0.1,
                    sample_rate=48000, channels=2, source_id='fresh',
                    source_sample_pos=0, over_range=False, wall_ns=None,
                ))

        fresh = _make_cap(source=FreshSource())
        engine.replace_analyser(fresh)
        deadline = time.monotonic() + 2
        while fresh.latest() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert fresh.latest() is not None
        assert engine.stop() is True
    finally:
        source.release.set()
        original_join(2)
        engine.stop()
        candidate.stop()


def test_evicted_bar_history_does_not_drop_delayed_feature():
    cap = _make_cap()
    try:
        for i in range(1006):
            cap._publish_by_interval(epoch_id='e', spectrum_updates=[
                ProcessorUpdate('spectrum', i * 10, (i + 1) * 10, bars=[0.5])],
                other_updates=[], clamp_end=None)
        records = cap._publish_by_interval(epoch_id='e', spectrum_updates=[], other_updates=[
            ProcessorUpdate('chroma', 0, 10, onset_strength=0.5)], clamp_end=None)
        assert len(records) == 1
        assert records[0].effective_processor_ids == ('chroma',)
        assert records[0].features.bars == []
        assert records[0].features.onset_strength == 0.5
        assert records[0].carried_spectrum_interval is None
        assert len(cap._bar_history) == 1000
        assert not cap._pending_onset
    finally:
        cap.stop()
