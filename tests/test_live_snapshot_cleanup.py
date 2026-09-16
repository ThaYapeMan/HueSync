"""Delivery order and live freshness differ; teardown owns follower cleanup."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_analysis_architecture import _make_cap, _make_frame
from test_audit_blockers_v3 import _make_engine_and_start
from test_player_manager import _make_full_storage

from huesync.canonicalizer import TemporarilyNoData
from huesync.lms_follower import LmsFollower
from huesync.models import Profile
from huesync.player_manager import ActiveSession, PlayerManager
from huesync.spectrum_engine import ProcessorUpdate
from huesync.sync_engine import SyncEngine


def _deliver(cap, update, epoch='e'):
    return cap._publish_by_interval(
        epoch_id=epoch,
        spectrum_updates=[update] if update.bars is not None else [],
        other_updates=[] if update.bars is not None else [update],
        clamp_end=None,
    )[0]


def test_delayed_record_kept_but_live_effects_spectrum_does_not_regress():
    cap = _make_cap()
    engine = SyncEngine(None, Profile(), analyser=cap)
    try:
        historical = _deliver(cap, ProcessorUpdate('v2', 50, 60, bars=[0.2]))
        cap.drain_publications()
        fresh = _deliver(cap, ProcessorUpdate('v2', 100, 110, bars=[0.9]))
        delayed = _deliver(cap, ProcessorUpdate('beat_detector', 50, 60, onset=True))
        assert cap.drain_publications() == [fresh, delayed]
        assert delayed.sequence > fresh.sequence
        assert delayed.features.onset
        assert delayed.features.bars == historical.features.bars
        assert delayed.carried_spectrum_interval == (50, 60)
        assert delayed.effective_processor_ids == ('beat_detector',)
        assert cap._latest_pub is fresh
        # The same latest() call is used by SyncEngine.run for live Effects.
        assert engine._analyser.latest() is fresh.features
        assert engine._analyser.latest().bars == [0.9]
        assert cap.pub_seq == delayed.sequence
    finally:
        engine.stop()


def test_live_ties_same_interval_and_new_epoch():
    cap = _make_cap()
    try:
        first = _deliver(cap, ProcessorUpdate('v2', 100, 110, bars=[0.9]))
        wider = _deliver(cap, ProcessorUpdate('beat_detector', 50, 110, onset=True))
        assert cap._latest_pub is first  # equal end: later start is fresher
        tied = _deliver(cap, ProcessorUpdate('beat_detector', 100, 110, onset=True))
        assert cap._latest_pub is tied  # exact tie: later delivery wins
        assert tied.features.bars == [0.9]
        assert tied.carried_spectrum_interval == (100, 110)
        assert tied.effective_processor_ids == ('beat_detector',)
        assert cap.drain_publications() == [first, wider, tied]
        # Production epoch transitions clear the old snapshot before publishing.
        old_epoch = _deliver(cap, ProcessorUpdate('v2', 10000, 10010, bars=[0.8]))
        records = cap.feed(_make_frame(n=3000, epoch_id='a-new-epoch', sample_pos=0))
        assert records
        assert cap._latest_pub.epoch == 'a-new-epoch'
        assert cap._latest_pub.sample_end < old_epoch.sample_end
        assert cap.latest() is cap._latest_pub.features
    finally:
        cap.stop()


def test_delayed_eos_does_not_replace_terminal_live_snapshot():
    cap = _make_cap()

    class Processor:
        def feed(self, frame):
            return [ProcessorUpdate('v2', 100, 110, bars=[0.9])]

        def flush(self):
            return [ProcessorUpdate('beat_detector', 50, 60, onset=True)]

        def reset(self):
            pass

        def close(self):
            pass

    cap._processors = (Processor(),)
    try:
        fresh = cap.feed(_make_frame())[0]
        eos = cap.end_of_stream()
        assert [(r.sample_pos, r.sample_end) for r in eos] == [(50, 60)]
        assert eos[0].features.onset
        assert eos[0].features.bars == []  # no eligible history, never future bars
        assert cap._latest_pub is fresh
        assert cap.latest().bars == [0.9]
        assert cap.drain_publications() == [fresh, *eos]
        polled = threading.Event()
        reads = 0

        def no_data():
            nonlocal reads
            reads += 1
            if reads >= 2:
                polled.set()
            return TemporarilyNoData()

        cap._source = SimpleNamespace(read=no_data, running=False)
        cap.start()
        assert polled.wait(2)
        assert cap._latest_pub is fresh
    finally:
        cap.stop()


def test_obsolete_late_drop_counter_removed():
    cap = _make_cap()
    try:
        assert not hasattr(cap, 'pub_late_dropped_count')
    finally:
        cap.stop()


def test_follower_cleaned_once_across_blocked_teardown_retries(tmp_path, monkeypatch, caplog):
    class Source:
        running = True
        total_reads = 0
        closed = 0

        def __init__(self):
            self.release = threading.Event()

        def read(self):
            self.total_reads += 1
            self.release.wait(20)
            return TemporarilyNoData()

        def close(self):
            assert self.release.is_set()
            self.closed += 1

    async def run():
        source = Source()
        engine, cap = _make_engine_and_start(source)
        join = cap._thread.join
        monkeypatch.setattr(cap._thread, 'join', lambda timeout=None: join(0.02))
        storage, coupling = _make_full_storage(tmp_path)
        manager = PlayerManager(storage)
        session = ActiveSession(Profile())
        session.sync_engine, session.shm_source = engine, source
        session.fifo_path, session.cava_conf_path = tmp_path / 'fifo', tmp_path / 'conf'
        manager._active = session
        follower = LmsFollower('unused', 'aa', 'bb')
        closed = AsyncMock()
        entered = asyncio.Event()

        async def connection():
            try:
                entered.set()
                await asyncio.Future()
            finally:
                await closed()

        monkeypatch.setattr(follower, '_connect_and_listen', connection)
        monkeypatch.setattr(follower, 'stop', MagicMock(wraps=follower.stop))
        session.follower = follower
        task = session.follower_task = follower.start()
        await entered.wait()
        try:
            for _ in range(3):
                with pytest.raises(RuntimeError, match='retiring'):
                    await manager.deactivate()
                assert session.follower is None
                assert session.follower_task is None
                assert task.done()
                assert source.closed == 0
                follower.stop.assert_called_once()
                closed.assert_awaited_once()
            source.release.set()
            join(2)
            await manager.deactivate()
            assert source.closed == 1
            assert not engine.retirement_pending
            monkeypatch.setattr('huesync.player_manager.list_entertainment_areas',
                                AsyncMock(return_value=[SimpleNamespace(id='ae-001', name='AE')]))
            monkeypatch.setattr('huesync.player_manager.get_channel_infos',
                                AsyncMock(return_value=[]))

            async def activate(session, *args):
                session.profile.player_mac = ''
                session.fifo_path = tmp_path / 'new-fifo'
                session.cava_conf_path = tmp_path / 'new-conf'

            monkeypatch.setattr(manager, '_activate_lms', activate)
            await manager.activate_coupling(coupling)
            assert manager.active_coupling_id == coupling.id
            await manager.deactivate()
            follower.stop.assert_called_once()
            closed.assert_awaited_once()
            assert not [r for r in caplog.records if r.levelno >= 30]
        finally:
            source.release.set()
            join(2)
            engine.stop()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_follower_cleanup_failure_is_not_swallowed(tmp_path):
    async def run():
        manager = PlayerManager(_make_full_storage(tmp_path)[0])
        session = ActiveSession(Profile())
        session.follower = MagicMock()
        entered = asyncio.Event()

        async def follower_task():
            try:
                entered.set()
                await asyncio.Future()
            finally:
                raise OSError('cleanup failed')

        task = session.follower_task = asyncio.create_task(follower_task())
        await entered.wait()
        try:
            with pytest.raises(OSError, match='cleanup failed'):
                await manager._teardown_session(session)
        finally:
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
